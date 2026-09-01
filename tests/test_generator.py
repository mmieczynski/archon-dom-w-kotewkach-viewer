"""T11's own tests: the 3D generator produces the solid the spec describes.

This module is deliberately *not* the invariants suite. T09 asserts the model against
Archon's published figures; what is checked here is that the generator did what it was
asked to do -- that the ridge is an output rather than an input, that the booleans cut,
that the same spec gives the same bytes, and that the elements the schema cannot express
(roof windows, chimney stacks, slab openings) came out the way this module documents.

The single most important test in the file is
:func:`test_ridge_ignores_the_printed_ridge_height`. Every other roof assertion in the
project -- here, in T09, in T12 -- is only meaningful if the ridge cannot be reached from
``section_elevations.ridge``. That test proves the wire is cut.
"""

from __future__ import annotations

import copy
import hashlib
import math
import os
import pathlib
import subprocess
import sys

import numpy as np
import pytest
import trimesh

from kotewki.generator import (
    CHIMNEY_ABOVE_RIDGE_MM,
    ROOF_WINDOWS,
    GeneratorError,
    build,
    build_scene,
    roof_geometry,
)

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

#: Tolerance for "these two elevations are the same number", metres. The spec is integer
#: millimetres and the arithmetic is exact, so anything above float noise is a real
#: disagreement; 1e-6 m is one micrometre.
EXACT_M = 1e-6

#: manifold3d works in float32, so any elevation read back off a mesh that has been
#: through a boolean carries ~1e-6 m of quantisation. Used only for mesh-derived values.
MESH_M = 1e-4


@pytest.fixture(scope="module")
def scene(spec):
    return build_scene(spec)


@pytest.fixture(scope="module")
def roof(spec):
    return roof_geometry(spec)


def _raw(spec, **blocks):
    """A plain-dict copy of the spec with some top-level blocks replaced.

    Deep-copied, because the generator must be free of side effects on its input and
    these tests would otherwise poison the session-scoped ``spec`` fixture.
    """
    document = copy.deepcopy(spec.to_dict() if hasattr(spec, "to_dict") else dict(spec))
    document.update(blocks)
    return document


def _replace(spec, block, **fields):
    changed = copy.deepcopy(spec[block])
    changed.update(fields)
    return _raw(spec, **{block: changed})


# ======================================================================================
# The ridge is an OUTPUT
# ======================================================================================


def test_ridge_is_the_documented_chain(spec, roof):
    """ridge = attic floor + knee wall + build-up + (span / 2) * tan(pitch).

    Recomputed here from the spec fields directly rather than from the RoofGeometry, so
    that the test fails if the generator ever quietly re-defines any term of the chain.
    """
    levels = {level["id"]: level for level in spec["levels"]}
    expected = (
        levels["attic"]["elevation"]
        + spec["construction"]["knee_wall_height"]
        + spec["roof"]["roof_buildup_vertical"]
        + (roof.span / 2.0) * math.tan(math.radians(spec["roof"]["pitch_deg"]))
    )
    assert roof.springing_elevation == pytest.approx(3610.0, abs=1e-9)
    assert roof.ridge_elevation_mm == pytest.approx(expected, abs=1e-9)


def test_ridge_ignores_the_printed_ridge_height(spec):
    """Corrupting ``section_elevations.ridge`` must not move the roof by one micron.

    This is the anti-tautology test. T09 compares the computed ridge with the printed
    +6.77 m and the published 7.09 m building height, and both comparisons are worth
    exactly nothing if the printed figure can reach the constructor. Here the printed
    value is replaced with a nonsense 99.999 m and the roof is rebuilt: if the ridge
    moves, some code path is reading the answer instead of deriving it.
    """
    honest = build_scene(spec).metadata["ridge_height_m"]
    corrupted = build_scene(
        _replace(spec, "section_elevations", ridge=99999)
    ).metadata["ridge_height_m"]
    assert corrupted == honest


@pytest.mark.parametrize("pitch", [25.0, 30.0, 35.0, 40.0, 45.0])
def test_pitch_moves_the_ridge(spec, pitch):
    """Changing ``pitch_deg`` changes the computed ridge, by exactly the right amount.

    The regression this guards against is a generator that computes a ridge, then snaps
    it to the printed value: such a generator passes T09 and fails here.
    """
    scene = build_scene(_replace(spec, "roof", pitch_deg=pitch))
    expected = 3.610 + 4.5 * math.tan(math.radians(pitch))
    assert scene.metadata["ridge_height_m"] == pytest.approx(expected, abs=EXACT_M)


def test_ridge_is_monotonic_in_pitch(spec):
    ridges = [
        build_scene(_replace(spec, "roof", pitch_deg=pitch)).metadata["ridge_height_m"]
        for pitch in (30.0, 35.0, 40.0)
    ]
    assert ridges[0] < ridges[1] < ridges[2]


def test_ridge_lands_near_the_printed_mark(spec, scene):
    """The check the derivation exists to make: -9 mm against a printed +6.77 m.

    Loose enough to be a check rather than a tautology (``roof_buildup_vertical`` carries
    a measured +-30 mm), tight enough that a wrong springing plane fails it: springing
    from the 3330 ceiling plane instead of the 3610 roof plane misses by 280 mm.
    """
    printed = spec["section_elevations"]["ridge"] / 1000.0
    assert scene.metadata["ridge_height_m"] == pytest.approx(printed, abs=0.030)
    assert scene.metadata["ridge_height_m"] < printed, "expected the derived ridge to sit low"


def test_building_height_is_ridge_above_terrain(spec, scene):
    terrain = spec["section_elevations"]["terrain"] / 1000.0
    assert scene.metadata["building_height_m"] == pytest.approx(
        scene.metadata["ridge_height_m"] - terrain, abs=EXACT_M
    )
    assert scene.metadata["building_height_m"] == pytest.approx(7.09, abs=0.030)


def test_eave_lands_on_the_printed_fascia_mark(spec, roof):
    """springing - eaves_overhang * tan(pitch) - fascia_depth = the printed +2.88 m.

    The third of the three parallel planes, and the one that proves ``fascia_depth`` and
    ``roof_buildup_vertical`` are being used as the different quantities they are: using
    280 mm here instead of 310 mm misses the printed mark by 30 mm.
    """
    printed = spec["section_elevations"]["eave_fascia_underside"]
    assert roof.eave_fascia_underside_mm == pytest.approx(printed, abs=1.0)
    assert roof.fascia_depth != roof.roof_buildup_vertical


def test_three_planes_are_not_confused(spec, roof):
    """3330 banding / 3610 roof / 2880 eave stay three different numbers."""
    knee_top = spec["levels"][1]["elevation"] + spec["construction"]["knee_wall_height"]
    assert knee_top == 3330
    assert roof.springing_elevation == knee_top + spec["roof"]["roof_buildup_vertical"]
    assert roof.springing_elevation != knee_top
    assert roof.top_at(roof.cross_min) == pytest.approx(roof.springing_elevation, abs=1e-9)


def test_roof_area_is_the_plan_over_cos_pitch(roof):
    plan = (roof.eaves_max - roof.eaves_min) * (roof.verge_max - roof.verge_min) / 1e6
    assert roof.area_m2 == pytest.approx(plan / math.cos(math.radians(roof.pitch_deg)))
    # T09 asserts this against the published 216.8 m2 as a +-6 % band; here we only pin
    # the arithmetic, so that a failure there is about the overhangs and not about this.
    assert 200.0 < roof.area_m2 < 250.0


# ======================================================================================
# Determinism -- T13's golden-image diff depends on it
# ======================================================================================


def test_two_builds_are_byte_identical(spec):
    """Same spec in, byte-identical mesh out, down to the exported GLB.

    Vertices and faces are compared exactly (not approximately) because approximate
    equality would hide exactly the kind of drift -- a set iteration, a dict ordering --
    that makes T13's overlay flaky and gets it disabled.
    """
    first, second = build_scene(spec), build_scene(spec)
    assert list(first.geometry) == list(second.geometry)
    for name, mesh in first.geometry.items():
        other = second.geometry[name]
        assert np.array_equal(mesh.vertices, other.vertices), name
        assert np.array_equal(mesh.faces, other.faces), name
    digest = [
        hashlib.sha256(scene.export(file_type="glb")).hexdigest() for scene in (first, second)
    ]
    assert digest[0] == digest[1]


def test_a_fresh_interpreter_builds_the_same_bytes():
    """Two builds in *separate processes*, under different hash seeds.

    :func:`test_two_builds_are_byte_identical` cannot see the failure mode this one is
    for: ``PYTHONHASHSEED`` is fixed for the life of a process, so a generator that
    iterates a ``set`` -- of wall ids, of room names, of materials -- produces the same
    order twice in a row and looks perfectly deterministic, then reorders on the next CI
    run and turns T13's golden-image diff into a flaky test. A flaky test gets disabled,
    and disabling T13 removes the project's only guard against building a mirror image of
    the right house. So the seed is varied deliberately.
    """
    script = (
        "import hashlib, json, sys;"
        "from kotewki.spec import load_spec;"
        "from kotewki.generator import build_scene;"
        "s = build_scene(load_spec());"
        "print(hashlib.sha256(s.export(file_type='glb')).hexdigest());"
        "print(json.dumps(s.metadata, sort_keys=True, default=str))"
    )
    outputs = []
    for seed in ("0", "1", "424242"):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(pathlib.Path(__file__).resolve().parents[1]),
            env=environment,
        )
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    assert len(outputs[0].splitlines()[0]) == 64


def test_build_does_not_mutate_the_spec(spec):
    before = copy.deepcopy(spec.to_dict() if hasattr(spec, "to_dict") else dict(spec))
    build_scene(spec)
    after = spec.to_dict() if hasattr(spec, "to_dict") else dict(spec)
    assert after == before


def test_build_alias_is_build_scene():
    assert build is build_scene


# ======================================================================================
# Solids
# ======================================================================================


def test_scene_is_a_scene_in_metres(scene):
    assert isinstance(scene, trimesh.Scene)
    assert scene.metadata["units"] == "m"
    assert len(scene.geometry) > 50
    assert np.allclose(scene.graph.get(scene.graph.nodes_geometry[0])[0], np.eye(4))


def test_every_solid_is_a_closed_volume(scene):
    """T12 asserts watertightness on the export; failing here first localises it."""
    broken = [
        name
        for name, mesh in scene.geometry.items()
        if not mesh.is_volume or mesh.volume <= 0.0
    ]
    assert broken == []


def test_no_degenerate_or_nan_triangles(scene):
    for name, mesh in scene.geometry.items():
        assert np.isfinite(mesh.vertices).all(), name
        assert (mesh.area_faces > 0.0).all(), name


def test_scene_bounds_are_the_roofed_building(scene, roof):
    """XY is the roof outline including overhangs; Z runs terrain -> chimney top.

    Recorded here so T12 does not have to rediscover it: the top of the bounding box is
    **not** the ridge, because the two stacks stand above it, exactly as they are drawn on
    both side elevations.
    """
    low, high = scene.bounds
    assert low[0] == pytest.approx(roof.verge_min / 1000.0, abs=MESH_M)
    assert high[0] == pytest.approx(roof.verge_max / 1000.0, abs=MESH_M)
    assert low[1] == pytest.approx(roof.eaves_min / 1000.0, abs=MESH_M)
    assert high[1] == pytest.approx(roof.eaves_max / 1000.0, abs=MESH_M)
    assert low[2] == pytest.approx(scene.metadata["terrain_m"], abs=MESH_M)
    assert high[2] == pytest.approx(
        scene.metadata["ridge_height_m"] + CHIMNEY_ABOVE_RIDGE_MM / 1000.0, abs=MESH_M
    )


# ======================================================================================
# Node naming -- T14 toggles on these, T12 asserts they survive glTF
# ======================================================================================


def test_node_names_are_level_category_id(scene):
    for name in scene.geometry:
        parts = name.split("/")
        assert 2 <= len(parts) <= 4, name
        assert parts[0] in {"ground", "attic", "roof"}, name
        assert all(part for part in parts), name


@pytest.mark.parametrize(
    "prefix",
    [
        "ground/walls/",
        "ground/slabs/",
        "ground/openings/windows/",
        "ground/openings/doors/",
        "ground/rooms/",
        "ground/chimneys/",
        "attic/walls/",
        "attic/slabs/",
        "attic/openings/windows/",
        "attic/openings/doors/",
        "attic/rooms/",
        "attic/chimneys/",
        "roof/slab",
        "roof/windows/",
    ],
)
def test_every_toggleable_group_is_populated(scene, prefix):
    assert [name for name in scene.geometry if name.startswith(prefix)]


def test_room_names_ride_on_the_nodes(spec, scene):
    """Every room in the spec is named by some room node's metadata.

    Not every room gets a node of its own: four ground-floor rooms share one open-plan
    face and one plate names all four, and Schody has no plate because it *is* the
    stairwell opening. Both are stated in the metadata rather than silently dropped.
    """
    named = set()
    for name, mesh in scene.geometry.items():
        if "/rooms/" in name:
            assert mesh.metadata["room_names"]
            named.update(mesh.metadata["room_ids"])
    voids = {opening["id"] for opening in scene.metadata["floor_voids"]}
    assert voids  # the stairwell is why A_R4 may legitimately be absent
    missing = {room["id"] for room in spec["rooms"]} - named
    assert missing == {"A_R4"}


def test_open_plan_rooms_share_one_plate(scene):
    """Hol + Salon + Hol + Kuchnia are one space, so they get one plate, not four.

    Four coincident plates would z-fight and would imply walls the plan does not draw.
    """
    shared = [
        mesh for name, mesh in scene.geometry.items()
        if "/rooms/" in name and mesh.metadata["shared_face"]
    ]
    assert len(shared) == 1
    assert len(shared[0].metadata["room_ids"]) == 4
    assert shared[0].metadata["computed_area_m2"] == pytest.approx(49.679, abs=0.01)


# ======================================================================================
# Walls, layers and openings
# ======================================================================================


def test_exterior_walls_are_layered(scene):
    """Porotherm + EPS + render, each addressable.

    A window reveal in a single 450 mm slab shows one blank face; the real reveal steps
    through three materials, and that step is the most visible tell of a model without
    thickness.
    """
    layers = [name for name in scene.geometry if name.startswith("ground/walls/G_W1/")]
    assert len(layers) == 3
    thicknesses = sorted(
        round(scene.geometry[name].metadata["thickness_m"], 4) for name in layers
    )
    assert thicknesses == [0.45, 0.45, 0.45]  # each layer belongs to the same 450 mm wall
    materials = [scene.geometry[name].metadata["material"].lower() for name in layers]
    assert any("porotherm" in item for item in materials)
    assert any("eps" in item or "styropian" in item for item in materials)
    assert any("tynk" in item for item in materials)


def test_render_is_outboard_and_moves_no_structure(spec):
    """The 10 mm render sits outside the dimensioned outline and shifts nothing.

    T04 solved the render against the published *pow. zabudowy*, which PN-ISO 9836
    measures on the finished building; the structural centreline thickness stays 450 mm.
    Dropping the render layer must therefore leave every structural solid bit-identical.
    """
    with_render = build_scene(spec)
    without = build_scene(spec, include_render=False)
    structural = [name for name in without.geometry if "tynk" not in name]
    exterior = {item["id"] for item in spec["walls"] if item["type"] == "exterior"}
    assert len(with_render.geometry) - len(without.geometry) == len(exterior)
    for name in structural:
        assert np.array_equal(
            with_render.geometry[name].vertices, without.geometry[name].vertices
        ), name
    # The render grows the *walls* by its own thickness on each face and nothing else --
    # the scene bounds cannot show it, being set by the roof overhangs either way.
    def wall_extent(scene):
        walls = [mesh for name, mesh in scene.geometry.items() if "/walls/" in name]
        return max(mesh.bounds[1][0] for mesh in walls)

    render = spec["construction"]["exterior_wall"]["thickness"] - 450
    assert wall_extent(with_render) - wall_extent(without) == pytest.approx(
        render / 1000.0, abs=MESH_M
    )
    assert wall_extent(without) == pytest.approx(17.100, abs=MESH_M)
    # Nothing *derived* from the outline may move either. The roof span is measured off
    # the storey envelopes, so a render that leaked into the structural footprint would
    # lengthen the span, raise the ridge and quietly re-tune the project's only roof check
    # by 10 mm of plaster -- while every one of the assertions above still passed.
    assert with_render.metadata["ridge_height_m"] == without.metadata["ridge_height_m"]
    assert with_render.metadata["roof"]["span_m"] == pytest.approx(9.000, abs=EXACT_M)
    for level in ("ground", "attic"):
        assert (
            with_render.metadata["levels"][level]["envelope_area_m2"]
            == without.metadata["levels"][level]["envelope_area_m2"]
        )
    assert with_render.metadata["levels"]["attic"]["envelope_area_m2"] == pytest.approx(
        17.100 * 9.000, abs=1e-6
    )


def test_openings_are_actually_cut(scene):
    """The garden facade loses exactly its three openings' worth of Porotherm.

    G_W7 runs the full 17.10 m of the north wall and carries 180/230 + 420/230 + 180/230.
    Checking the volume rather than the hole count is what catches a boolean that ran but
    subtracted the wrong box.
    """
    mesh = scene.geometry["ground/walls/G_W7/porotherm_25"]
    solid = 17.100 * 0.250 * 3.040
    cut = (1.800 + 4.200 + 1.800) * 2.300 * 0.250
    assert mesh.volume == pytest.approx(solid - cut, abs=1e-3)


def test_openings_do_not_pierce_the_wrong_wall(scene):
    """A cutter reaches past its host's faces, so it must never leave that host.

    G_W13 is a 120 mm partition crossed by the 450 mm exterior walls; if an opening cutter
    were subtracted from the level instead of from its own wall, this volume would drop.
    """
    mesh = scene.geometry["ground/walls/G_W12/structure"]
    assert mesh.volume == pytest.approx((1.520 + 0.06 + 0.06) * 0.120 * 2.700 - 0.9 * 2.0 * 0.12,
                                        abs=5e-3)


def test_every_opening_has_a_pane(spec, scene):
    panes = {
        name.rsplit("/", 1)[1] for name in scene.geometry if "/openings/" in name
    }
    assert panes == {opening["id"] for opening in spec["openings"]}


def test_balustrade_stops_at_guard_height(spec, scene):
    """A_W7 is a 60 mm guard along the Antresola void edge, not a wall to the ceiling.

    Its 1100 mm height is in the spec but flagged ``derived`` there, and the node has to
    say so: a guard height nobody printed must not read as a transcribed dimension in the
    artifact. The cross-check that it is the *right* guard is that it runs along A_SO1's
    own boundary -- two independently transcribed entities agreeing on one edge.
    """
    wall = next(item for item in spec["walls"] if item["id"] == "A_W7")
    assert wall["thickness"] == 60
    mesh = scene.geometry["attic/walls/A_W7/structure"]
    floor = spec["levels"][1]["elevation"] / 1000.0
    assert mesh.bounds[0][2] == pytest.approx(floor, abs=MESH_M)
    assert mesh.bounds[1][2] == pytest.approx(floor + wall["height"] / 1000.0, abs=MESH_M)
    assert mesh.bounds[1][2] < scene.metadata["ridge_height_m"]
    assert mesh.metadata["height_source"] == "wall"
    assert mesh.metadata["height_derived"] is True
    assert mesh.metadata["trimmed_to_roof"] is False
    # It runs along the void it guards: the balustrade's far face and A_SO1's near edge.
    void = next(item for item in spec["slab_openings"] if item["id"] == "A_SO1")
    assert mesh.bounds[1][1] == pytest.approx(void["bounds"][1] / 1000.0, abs=MESH_M)
    # ...while an ordinary attic partition is trimmed to the roof instead.
    assert scene.geometry["attic/walls/A_W5/structure"].metadata["height_source"] == "roof"
    assert scene.geometry["ground/walls/G_W9/structure"].metadata["height_source"] == "storey"


def test_gable_walls_are_trimmed_to_the_roof(scene, roof):
    """The gable triangle is cut by the roof, so its apex is the roof underside.

    A gable extruded to a single height either pokes through the roof or leaves a slot
    under it; both are immediately visible on an elevation and neither shows up in any
    area check.
    """
    for name in ("attic/walls/A_W2/porotherm_25", "attic/walls/A_W4/porotherm_25"):
        mesh = scene.geometry[name]
        apex = roof.underside_at(roof.ridge_coord) / 1000.0
        assert mesh.bounds[1][2] == pytest.approx(apex, abs=MESH_M), name
        # ...and every vertex is at or below the roof underside above its own position.
        cross = mesh.vertices[:, 1] * 1000.0
        limit = np.array([roof.underside_at(value) for value in cross]) / 1000.0
        assert (mesh.vertices[:, 2] <= limit + MESH_M).all(), name


def test_gable_window_head_is_raked_to_the_roof(scene, roof):
    """A_O1/A_O2 are stored as 100/273 rectangles; 2730 mm is a maximum, not the head.

    elevation_side_2.png draws a raking head where the roof crosses the opening, and
    intersecting the pane with the volume under the roof applies exactly that. The rake is
    *small* -- 19 mm deep over the outer 28 mm of the 1000 mm width -- because the roof
    plane this project builds clears the 2730 mm head over almost the whole opening. It is
    asserted as a strict inequality rather than a figure so that it stays a statement about
    the roof rather than about a number someone measured off this model.
    """
    for name in ("attic/openings/windows/A_O1", "attic/openings/windows/A_O2"):
        mesh = scene.geometry[name]
        rectangle = 1.000 * 2.730 * 0.040
        assert mesh.volume < rectangle, name
        assert mesh.volume > 0.99 * rectangle, name
        cross = mesh.vertices[:, 1] * 1000.0
        limit = np.array([roof.underside_at(value) for value in cross]) / 1000.0
        assert (mesh.vertices[:, 2] <= limit + MESH_M).all(), name


# ======================================================================================
# Slabs and the floor voids
# ======================================================================================


def test_plinth_sits_between_terrain_and_the_datum(spec, scene):
    mesh = scene.geometry["ground/slabs/plinth"]
    assert mesh.bounds[0][2] == pytest.approx(spec["section_elevations"]["terrain"] / 1000.0)
    assert mesh.bounds[1][2] == pytest.approx(0.0, abs=EXACT_M)


def test_floor_slab_is_the_spec_thickness_under_the_attic(spec, scene):
    mesh = scene.geometry["attic/slabs/floor"]
    thickness = spec["construction"]["ceiling"]["thickness"] / 1000.0
    top = spec["levels"][1]["elevation"] / 1000.0
    assert mesh.bounds[1][2] == pytest.approx(top, abs=MESH_M)
    assert mesh.bounds[0][2] == pytest.approx(top - thickness, abs=MESH_M)


def test_slab_openings_are_cut_from_the_floor(spec, scene):
    """No floor is poured over the Pustka or the stairwell.

    Slabbing either is invisible to every dimensional check in the suite -- all room areas
    stay exactly right -- while the double-height living space silently disappears. So it
    is checked geometrically, by asserting that no triangle of anything in the scene lies
    inside either opening within the slab's own elevation band.
    """
    declared = [item for item in spec["slab_openings"] if item["level"] == "attic"]
    assert {item["kind"] for item in declared} == {"void", "stairwell"}
    top = spec["levels"][1]["elevation"] / 1000.0
    bottom = top - spec["construction"]["ceiling"]["thickness"] / 1000.0
    inset = 0.002
    for opening in declared:
        x0, y0, x1, y1 = (value / 1000.0 for value in opening["bounds"])
        for name, mesh in scene.geometry.items():
            centres = mesh.triangles_center
            hits = (
                (centres[:, 0] > x0 + inset) & (centres[:, 0] < x1 - inset)
                & (centres[:, 1] > y0 + inset) & (centres[:, 1] < y1 - inset)
                & (centres[:, 2] > bottom + inset) & (centres[:, 2] < top - inset)
            )
            assert not hits.any(), f"{name} has material inside {opening['id']}"


def test_floor_slab_area_accounts_for_every_hole(spec, scene):
    """Slab volume = (envelope - openings - chimneys) * thickness, to the cubic mm."""
    thickness = spec["construction"]["ceiling"]["thickness"] / 1000.0
    envelope = scene.metadata["levels"]["attic"]["envelope_area_m2"]
    voids = sum(item["area_m2"] for item in scene.metadata["floor_voids"])
    stacks = 1.0656 + 0.24255  # G_W27 and G_W28 pass through this band
    expected = (envelope - voids - stacks) * thickness
    assert scene.geometry["attic/slabs/floor"].volume == pytest.approx(expected, abs=1e-3)


def test_slab_openings_are_read_generically_not_hardcoded(spec):
    """A fabricated opening, on the *other* level, at coordinates nobody transcribed.

    The two real openings could be reproduced by a generator that special-cased ``A_SO1``
    or its literal bounds, and every dimensional check in the suite would still pass. This
    invents a third one at 12.0-13.0 x 2.0-3.0 m on the **ground** floor -- a level that
    has no openings at all today -- and asserts the plinth is cut there. Only a generator
    that reads ``spec["slab_openings"]`` as data can pass it.
    """
    invented = {
        "id": "G_SO_TEST",
        "level": "ground",
        "bounds": [12000, 2000, 13000, 3000],
        "kind": "void",
    }
    openings = copy.deepcopy(spec["slab_openings"]) + [invented]
    built = build_scene(_raw(spec, slab_openings=openings))
    plinth = built.geometry["ground/slabs/plinth"]
    intact = build_scene(spec).geometry["ground/slabs/plinth"]
    terrain = -spec["section_elevations"]["terrain"] / 1000.0
    assert intact.volume - plinth.volume == pytest.approx(1.0 * 1.0 * terrain, abs=1e-3)
    assert "G_SO_TEST" in {item["id"] for item in built.metadata["floor_voids"]}
    centres = plinth.triangles_center
    inside = (
        (centres[:, 0] > 12.002) & (centres[:, 0] < 12.998)
        & (centres[:, 1] > 2.002) & (centres[:, 1] < 2.998)
        & (centres[:, 2] > -0.318) & (centres[:, 2] < -0.002)
    )
    assert not inside.any()


def test_no_enclosed_face_is_left_undeclared(scene):
    """Every enclosed face is either a room or a declared slab opening.

    A face that is neither would be floored over silently, which is how the Pustka was
    nearly lost in the first place. Reported by the generator rather than inferred by it.
    """
    assert scene.metadata["undeclared_floor_faces"] == []


# ======================================================================================
# Roof windows and chimneys -- the elements the schema cannot host
# ======================================================================================


def test_roof_windows_are_three_in_the_south_slope(scene, roof):
    windows = {name for name in scene.geometry if name.startswith("roof/windows/")}
    assert len(windows) == 3
    for name in windows:
        mesh = scene.geometry[name]
        assert mesh.metadata["slope"] == "south"
        assert mesh.bounds[1][1] < roof.ridge_coord / 1000.0


def test_roof_window_plan_depth_matches_its_on_slope_height(scene, roof):
    """1297 mm projected against 1600 mm on a 35 deg slope. The one cross-check there is.

    These three windows live outside the spec (see the module docstring of
    kotewki.generator), so this identity is the only thing keeping their transcription
    honest. It holds to **1.1 %**: 1600 * cos 35 = 1310.6 mm against a measured
    1296 +- 12 mm (1297 as the integer bounds round). Asserted at 5 %, which is the band
    the +-12 mm supports.

    T19 DELETED the companion assertion that the identity does *not* hold at 1 %. It was
    written when the depth was recorded as 1271 mm and the disagreement looked like 3.1 %;
    T18 showed 1271 was a one-pixel edge-convention error -- measured to the inner faces
    of the two dashed lines rather than their centres -- and the real residual is 0.6 px on
    a raster whose pixel is 24.46 mm. It is deleted rather than re-tuned. It would still
    evaluate true (16.6 mm against a 16.0 mm threshold), and that is exactly the problem:
    the claim "these two disagree by more than 1 %" now rests on 0.6 mm of rounding while
    the measurement itself carries +-12 mm, over which the residual runs from 0.2 % to
    2.0 %. Keeping it green would be asserting noise, and an assertion that has stopped
    being *true of the drawing* is deleted, never loosened.
    """
    assert roof.pitch_deg == 35.0
    cos = math.cos(math.radians(roof.pitch_deg))
    for window, reported in zip(ROOF_WINDOWS, scene.metadata["roof_windows"], strict=True):
        projected = window.plan_depth(roof.ridge_axis)
        assert projected / cos == pytest.approx(window.height_on_slope, rel=0.05)
        assert reported["implied_height_on_slope_m"] == pytest.approx(projected / cos / 1000.0)


def test_roof_windows_come_from_the_spec_when_it_declares_them(spec):
    """``spec["roof_openings"]`` wins over the generator's fallback constant.

    The three windows are generator-side literals only because the schema has nowhere to
    put a roof-hosted opening. This asserts the recommended schema entity is already a
    drop-in: declare the block and the geometry follows it -- at fabricated coordinates
    that appear nowhere in this repository, so a generator that quietly kept using its own
    constant, or that special-cased the real ids, fails here.
    """
    declared = [
        {
            "id": "SPEC_RW",
            "kind": "roof_window",
            "slope": "south",
            "bounds": [2000, 1500, 3400, 2600],
            "width": 1400,
            "height_on_slope": 1343,
        }
    ]
    built = build_scene(_raw(spec, roof_openings=declared))
    assert [name for name in built.geometry if name.startswith("roof/windows/")] == [
        "roof/windows/SPEC_RW"
    ]
    reported = built.metadata["roof_windows"]
    assert [item["id"] for item in reported] == ["SPEC_RW"]
    assert reported[0]["from_spec"] is True
    assert reported[0]["bounds_m"] == [2.0, 1.5, 3.4, 2.6]
    mesh = built.geometry["roof/windows/SPEC_RW"]
    assert mesh.bounds[0][0] == pytest.approx(2.0, abs=MESH_M)
    assert mesh.bounds[1][0] == pytest.approx(3.4, abs=MESH_M)
    # ...and the schema gap it stands in for is no longer reported as open.
    assert not any("roof_openings" in gap for gap in built.metadata["schema_gaps"])

    # The real spec now DECLARES roof_openings (R_RW1..3), so it takes the spec path too.
    # This assertion previously read `is False`, which was correct only while the schema
    # gap was open; closing the gap legitimately flipped it. Keep it pinned either way so
    # a silent regression to the module constant would fail.
    assert build_scene(spec).metadata["roof_windows"][0]["from_spec"] is True
    assert [item["id"] for item in build_scene(spec).metadata["roof_windows"]] == [
        "R_RW1",
        "R_RW2",
        "R_RW3",
    ]

    # The fallback path still works for a spec that does not declare them.
    without = _raw(spec)
    without.pop("roof_openings", None)
    assert build_scene(without).metadata["roof_windows"][0]["from_spec"] is False


def test_a_roof_window_spanning_the_ridge_is_refused(spec):
    """Two planes need a plane-and-slope representation; a plan rectangle is not enough.

    The vertical-prism cut is exact only within one slope. Rather than quietly cutting a
    window through the apex, the generator says what it cannot model -- which is also the
    answer to "does the schema entity need a plane and a slope?": only for this case.
    """
    straddling = [
        {
            "id": "BAD_RW",
            "kind": "roof_window",
            "slope": "south",
            "bounds": [2000, 4000, 3400, 5000],
            "width": 1400,
            "height_on_slope": 1220,
        }
    ]
    with pytest.raises(GeneratorError, match="spans the ridge"):
        build_scene(_raw(spec, roof_openings=straddling))


def test_a_mislabelled_slope_is_refused(spec):
    """A window put on the wrong slope changes an elevation and no area check sees it."""
    mislabelled = [
        {
            "id": "BAD_RW",
            "kind": "roof_window",
            "slope": "north",
            "bounds": [2000, 1500, 3400, 2600],
            "width": 1400,
            "height_on_slope": 1343,
        }
    ]
    with pytest.raises(GeneratorError, match="south side of the ridge"):
        build_scene(_raw(spec, roof_openings=mislabelled))


def test_roof_window_panes_lie_in_the_roof_plane(scene, roof):
    """Every pane vertex sits on or just under the outer roof plane above it."""
    for name in (item.id for item in ROOF_WINDOWS):
        mesh = scene.geometry[f"roof/windows/{name}"]
        cross = mesh.vertices[:, 1] * 1000.0
        top = np.array([roof.top_at(value) for value in cross]) / 1000.0
        assert (mesh.vertices[:, 2] <= top + MESH_M).all(), name
        assert (mesh.vertices[:, 2] >= top - 0.041).all(), name


def test_the_roof_is_holed_where_the_windows_are(scene, roof):
    """The slab lost the three windows' worth of material, and no more."""
    solid = (roof.eaves_max - roof.eaves_min) * (roof.verge_max - roof.verge_min) / 1e6
    intact = solid * roof.fascia_depth / 1000.0
    assert scene.geometry["roof/slab"].volume < intact
    assert scene.geometry["roof/slab"].volume > 0.9 * intact


def test_chimneys_pass_through_the_roof(scene):
    """Both stacks stand above the ridge, as drawn on elevation_side_1 and _2."""
    stacks = {name: mesh for name, mesh in scene.geometry.items() if "/chimneys/" in name}
    assert len(stacks) == 4  # two stacks, each traced once per storey plan
    ridge = scene.metadata["ridge_height_m"]
    for name, mesh in stacks.items():
        assert mesh.bounds[1][2] == pytest.approx(
            ridge + CHIMNEY_ABOVE_RIDGE_MM / 1000.0, abs=MESH_M
        ), name
        assert mesh.bounds[0][2] < ridge, name


def test_chimneys_merge_into_two_silhouettes(scene):
    """The ground and attic pairs are the same two stacks read off different plans.

    So above the roof they must overlap in plan, not stand as four separate stacks: each
    pair's footprints are nested. Checked as overlap rather than equality because the two
    tracings legitimately differ by a few centimetres.
    """
    stacks = [mesh for name, mesh in scene.geometry.items() if "/chimneys/" in name]
    pairs = [(stacks[0], stacks[2]), (stacks[1], stacks[3])]
    for lower, upper in pairs:
        for axis in (0, 1):
            assert lower.bounds[0][axis] < upper.bounds[1][axis]
            assert upper.bounds[0][axis] < lower.bounds[1][axis]


def test_entrance_recess_is_not_slabbed_over(spec, scene):
    """The ground outline is not a rectangle; the attic above it is.

    800 x 2240 mm of the south facade is recessed, so the ground envelope is 152.1 m2
    against the attic's full 153.9 m2 -- and the attic slab still spans the whole
    rectangle, because the storey above is built over the recess.
    """
    ground = scene.metadata["levels"]["ground"]["envelope_area_m2"]
    attic = scene.metadata["levels"]["attic"]["envelope_area_m2"]
    assert attic - ground == pytest.approx(0.800 * 2.240, abs=0.01)
    assert scene.geometry["attic/slabs/floor"].bounds[0][1] == pytest.approx(0.0, abs=MESH_M)


# ======================================================================================
# Failure modes
# ======================================================================================


def test_missing_roof_input_is_a_clear_error(spec):
    roof = copy.deepcopy(spec["roof"])
    del roof["roof_buildup_vertical"]
    with pytest.raises(GeneratorError, match="roof_buildup_vertical"):
        build_scene(_raw(spec, roof=roof))


def test_unknown_springing_rule_is_refused(spec):
    with pytest.raises(GeneratorError, match="knee_wall_top"):
        build_scene(_replace(spec, "roof", springing="wall_plate"))


def test_stale_chimney_list_is_caught(spec):
    """The chimney ids are a stand-in for a schema field, so they are guarded.

    If the spec ever renumbers its walls, extruding the wrong wall through the roof would
    put a phantom stack on the elevation and nothing else would notice.
    """
    walls = copy.deepcopy(spec["walls"])
    for wall in walls:
        if wall["id"] == "G_W27":
            wall["type"] = "partition"
    with pytest.raises(GeneratorError, match="CHIMNEY_WALL_IDS"):
        build_scene(_raw(spec, walls=walls))

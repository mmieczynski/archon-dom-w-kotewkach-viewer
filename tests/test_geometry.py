"""Unit tests for the 2D geometry kernel (T07).

**Every test here builds its own synthetic building and compares against a hand-computed
answer.** Nothing in this module reads `spec/ground.json` or `spec/attic.json`: the kernel
has to be testable before the transcription lands (T04/T05 are writing those files right
now), and a geometry test that depends on the transcription cannot tell a kernel bug from
a typo in a dimension.

The synthetic specs are still written to disk and pushed through `kotewki.spec.load_spec`,
so every fixture is a *schema-valid* spec. A fixture that quietly drifted away from the
contract would test the kernel against geometry the real spec can never produce.

Convention in the numbers below: exterior walls are 500 mm and partitions 250/200/120 mm
rather than the real 465/115, purely so that half-thicknesses land on whole millimetres
and the expected areas can be written down exactly by hand.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from shapely.geometry import Point, Polygon, box

from kotewki.geometry import (
    DEFAULT_FINISH_ALLOWANCE_MM,
    MM_PER_M,
    GeometryError,
    SlopedCeiling,
    build_model,
    polygonise,
    room_polygon,
    to_m,
)
from kotewki.quantities import (
    DEFAULT_HEIGHT_BANDS,
    area_in_group,
    banded_polygons,
    footprint_area,
    footprint_polygon,
    net_area,
    room_area,
    sloped_band_areas,
    usable_area,
    usable_area_sloped,
)
from kotewki.spec import SpecValidationError, load_spec

# --------------------------------------------------------------------------------------
# Synthetic spec construction
# --------------------------------------------------------------------------------------

EXTERIOR_LAYERS = [
    {"material": "Porotherm 25", "thickness": 250},
    {"material": "EPS", "thickness": 200},
    {"material": "tynk", "thickness": 15},
]


def wall(
    wall_id: str,
    start: tuple[int, int],
    end: tuple[int, int],
    thickness: int,
    *,
    level: str = "ground",
    kind: str = "exterior",
    layers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out = {
        "id": wall_id,
        "level": level,
        "start": list(start),
        "end": list(end),
        "thickness": thickness,
        "type": kind,
    }
    if layers is not None:
        out["layers"] = layers
    return out


def room(
    room_id: str,
    published_id: int,
    name: str,
    boundary: list[str],
    published_area: float,
    *,
    level: str = "ground",
    groups: list[str] | None = None,
    seed: tuple[int, int] | None = None,
    measure_to: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "id": room_id,
        "published_id": published_id,
        "name": name,
        "level": level,
        "boundary": boundary,
        "published_area": published_area,
        "area_groups": groups or ["usable", "net"],
    }
    if seed is not None:
        out["seed"] = list(seed)
    if measure_to is not None:
        out["measure_to"] = measure_to
    return out


def meta_document(*, finish_allowance: int = 20, pitch_deg: float = 35.0) -> dict[str, Any]:
    """A minimal but schema-valid ``spec/meta.json`` for the synthetic fixtures."""
    return {
        "meta": {
            "schema_version": "1.0.0",
            "source_url": "https://example.invalid/synthetic",
            "variant": "TEST",
            "transcribed_by": "tests/test_geometry.py",
            "date": "2026-08-30",
        },
        "levels": [
            {"id": "ground", "name": "PARTER", "elevation": 0, "ceiling_height": 2700},
            {"id": "attic", "name": "PODDASZE", "elevation": 3040, "ceiling_height": 2730},
        ],
        "construction": {
            "exterior_wall": {"thickness": 465, "layers": EXTERIOR_LAYERS},
            "ceiling": {
                "thickness": 340,
                "layers": [{"material": "zelbet", "thickness": 340}],
            },
            "knee_wall_height": 290,
            "finish_allowance": finish_allowance,
        },
        "section_elevations": {
            "terrain": -320,
            "ground_floor": 0,
            "attic_floor": 3040,
            "ridge": 6770,
            "eave_fascia_underside": 2880,
            "source_image": "data/source/section.png",
        },
        "roof": {
            "type": "gable",
            "pitch_deg": pitch_deg,
            "eaves_overhang": 600,
            "verge_overhang": 590,
            "ridge_axis": "x",
            "springing": "knee_wall_top",
            "roof_buildup_vertical": 280,
            "fascia_depth": 310,
        },
    }


def make_spec(
    tmp_path,
    *,
    walls: list[dict[str, Any]] | None = None,
    rooms: list[dict[str, Any]] | None = None,
    attic_walls: list[dict[str, Any]] | None = None,
    attic_rooms: list[dict[str, Any]] | None = None,
    finish_allowance: int = 20,
    pitch_deg: float = 35.0,
):
    """Write a synthetic three-file spec to ``tmp_path`` and load it through the loader."""
    import json

    (tmp_path / "meta.json").write_text(
        json.dumps(meta_document(finish_allowance=finish_allowance, pitch_deg=pitch_deg)),
        encoding="utf-8",
    )
    (tmp_path / "ground.json").write_text(
        json.dumps(
            {
                "walls": walls or [],
                "openings": [],
                "rooms": rooms or [],
                "dimension_chains": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "attic.json").write_text(
        json.dumps(
            {
                "walls": attic_walls or [],
                "openings": [],
                "rooms": attic_rooms or [],
                "dimension_chains": [],
            }
        ),
        encoding="utf-8",
    )
    return load_spec(tmp_path)


# --------------------------------------------------------------------------------------
# Fixture 1 -- one rectangular room
#
#   outer envelope   6000 x 4000     walls 500 thick, centrelines inset 250
#   clear structure  5000 x 3000  =  15.0000 m2
#   clear finished   4960 x 2960  =  14.6816 m2   (20 mm per face)
# --------------------------------------------------------------------------------------

BOX_WALLS = [
    wall("G_S", (250, 250), (5750, 250), 500),
    wall("G_N", (250, 3750), (5750, 3750), 500),
    wall("G_W", (250, 250), (250, 3750), 500),
    wall("G_E", (5750, 250), (5750, 3750), 500),
]
BOX_ROOMS = [room("G_R1", 1, "Pokoj", ["G_S", "G_N", "G_W", "G_E"], 15.0)]


@pytest.fixture
def box_spec(tmp_path):
    return make_spec(tmp_path, walls=BOX_WALLS, rooms=BOX_ROOMS)


@pytest.fixture
def box_model(box_spec):
    return build_model(box_spec)


# --------------------------------------------------------------------------------------
# Fixture 2 -- an L-shaped room and a small room, bounded by walls of three thicknesses
#
#   outer envelope 6000 x 5000, exterior walls 500
#   interior       5000 x 4000 = 20.0000 m2
#   partition P1 vertical  at x=3500, t=250 -> faces at 3375 / 3625
#   partition P2 horizontal at y=2500, t=120 -> faces at 2440 / 2560
#
#   small room  x 3625..5500, y 2560..4500  = 1875 x 1940 =  3.6375 m2
#   L room      interior minus 2125 x 2060  = 20.0 - 4.3775 = 15.6225 m2
#
#   finished (20 mm per face):
#   small room  1835 x 1900                            =  3.4865 m2
#   L room      4960 x 3960 minus 2125 x 2060          = 15.2641 m2
# --------------------------------------------------------------------------------------

L_WALLS = [
    wall("G_S", (250, 250), (5750, 250), 500),
    wall("G_N", (250, 4750), (5750, 4750), 500),
    wall("G_W", (250, 250), (250, 4750), 500),
    wall("G_E", (5750, 250), (5750, 4750), 500),
    wall("G_P1", (3500, 2500), (3500, 4750), 250, kind="partition"),
    wall("G_P2", (3500, 2500), (5750, 2500), 120, kind="partition"),
]
L_ROOMS = [
    room("G_L", 1, "Salon", ["G_S", "G_N", "G_W", "G_E", "G_P1", "G_P2"], 15.62),
    room("G_SMALL", 2, "Spizarnia", ["G_N", "G_E", "G_P1", "G_P2"], 3.64),
]

L_ROOM_STRUCTURE_M2 = 15.6225
L_ROOM_FINISH_M2 = 15.2641
SMALL_ROOM_STRUCTURE_M2 = 3.6375
SMALL_ROOM_FINISH_M2 = 3.4865


@pytest.fixture
def l_spec(tmp_path):
    return make_spec(tmp_path, walls=L_WALLS, rooms=L_ROOMS)


@pytest.fixture
def l_model(l_spec):
    return build_model(l_spec)


def l_room_polygon_by_hand(inset_mm: float = 0.0) -> Polygon:
    """The L-shaped room written out corner by corner, independent of the kernel."""
    i = inset_mm
    west, east = to_m(500 + i), to_m(5500 - i)
    south, north = to_m(500 + i), to_m(4500 - i)
    notch_x, notch_y = to_m(3375 - i), to_m(2440 - i)
    return Polygon(
        [
            (west, south),
            (east, south),
            (east, notch_y),
            (notch_x, notch_y),
            (notch_x, north),
            (west, north),
        ]
    )


# --------------------------------------------------------------------------------------
# Fixture 3 -- three rooms in a row, one of each area group
#
#   outer 7000 x 3000, exterior 500, two 200 partitions at x=2500 and x=4500
#   Pokoj    1900 x 2000 = 3.8   usable + net
#   Kotlownia 1800 x 2000 = 3.6  usable + boiler
#   Schody   1900 x 2000 = 3.8   net only
# --------------------------------------------------------------------------------------

ROW_WALLS = [
    wall("G_S", (250, 250), (6750, 250), 500),
    wall("G_N", (250, 2750), (6750, 2750), 500),
    wall("G_W", (250, 250), (250, 2750), 500),
    wall("G_E", (6750, 250), (6750, 2750), 500),
    wall("G_P1", (2500, 250), (2500, 2750), 200, kind="partition"),
    wall("G_P2", (4500, 250), (4500, 2750), 200, kind="partition"),
]
ROW_ROOMS = [
    room("G_A", 1, "Pokoj", ["G_S", "G_N", "G_W", "G_P1"], 3.8, groups=["usable", "net"]),
    room("G_B", 2, "Kotlownia", ["G_S", "G_N", "G_P1", "G_P2"], 3.6, groups=["usable", "boiler"]),
    room("G_C", 3, "Schody", ["G_S", "G_N", "G_P2", "G_E"], 3.8, groups=["net"]),
]


@pytest.fixture
def row_model(tmp_path):
    return build_model(make_spec(tmp_path, walls=ROW_WALLS, rooms=ROW_ROOMS))


# --------------------------------------------------------------------------------------
# Units and precision
# --------------------------------------------------------------------------------------


def test_millimetres_convert_to_metres_once():
    assert MM_PER_M == 1000.0
    assert to_m(17100) == 17.1
    assert to_m(465) == 0.465


def test_wall_length_is_in_metres(box_model):
    walls = box_model.level("ground").network.wall_by_id
    assert walls["G_S"].length_m == pytest.approx(5.5, rel=1e-12)
    assert walls["G_S"].thickness_m == 0.5


def test_no_intermediate_rounding(tmp_path):
    """A room whose clear dimensions are not round numbers keeps full float precision.

    3457 x 2113 mm is 7.304641 m2 exactly. If anything in the kernel rounded to whole
    millimetres, centimetres, or two decimal places, this would not hold.
    """
    walls = [
        wall("G_S", (250, 250), (4207, 250), 500),
        wall("G_N", (250, 2863), (4207, 2863), 500),
        wall("G_W", (250, 250), (250, 2863), 500),
        wall("G_E", (4207, 250), (4207, 2863), 500),
    ]
    rooms = [room("G_R", 1, "Pokoj", ["G_S", "G_N", "G_W", "G_E"], 7.30)]
    model = build_model(make_spec(tmp_path, walls=walls, rooms=rooms))
    assert model.room("G_R").area("structure") == pytest.approx(3.457 * 2.113, rel=1e-12)


# --------------------------------------------------------------------------------------
# Wall solids, corners and T-junctions
# --------------------------------------------------------------------------------------


def test_wall_footprint_is_a_flat_capped_rectangle(box_model):
    """A lone wall footprint is exactly length x thickness -- no round or square caps."""
    solid = box_model.level("ground").network.wall_by_id["G_S"]
    bare = solid.centreline.buffer(solid.half_thickness_m, cap_style="flat", join_style="mitre")
    assert bare.area == pytest.approx(5.5 * 0.5, rel=1e-12)
    assert len(bare.exterior.coords) == 5


def test_corners_are_mitred_with_no_notch(box_model):
    """The envelope is exactly the 6.0 x 4.0 outer rectangle.

    Flat caps alone would leave a 0.25 x 0.25 m square missing at each of the four
    corners -- 0.25 m2 of the 24 m2 footprint, which would quietly break the 154.42 m2
    check. The junction extensions are what close them.
    """
    envelope = box_model.level("ground").network.envelope()
    expected = box(0.0, 0.0, 6.0, 4.0)
    assert envelope.symmetric_difference(expected).area == pytest.approx(0.0, abs=1e-12)


def test_walls_never_overshoot_the_envelope(l_model):
    """No wall solid pokes outside the building outline -- the no-overshoot guarantee."""
    network = l_model.level("ground").network
    assert network.solid().difference(network.envelope()).area == pytest.approx(0.0, abs=1e-12)


def test_t_junction_does_not_overshoot_into_the_room(l_model):
    """The 120 mm partition stops at the 250 mm partition's face, not past it.

    An overshoot here would eat a 0.125 x 0.12 m bite out of the L-shaped room, which is
    the classic symptom of buffering wall centrelines without thinking about junctions.
    """
    network = l_model.level("ground").network
    p2 = network.wall_by_id["G_P2"].footprint()
    minx, _, _, _ = p2.bounds
    assert minx == pytest.approx(to_m(3375), rel=1e-12)


def test_walls_are_axis_aligned(l_model):
    """The junction-extension rule is exact only at right angles; assert we are there."""
    assert l_model.level("ground").network.oblique_wall_ids == ()


# --------------------------------------------------------------------------------------
# Polygonisation
# --------------------------------------------------------------------------------------


def test_polygonise_finds_exactly_the_enclosed_faces(l_model):
    faces = l_model.level("ground").network.faces()
    assert len(faces) == 2
    assert sorted(round(face.area, 6) for face in faces) == [
        SMALL_ROOM_STRUCTURE_M2,
        L_ROOM_STRUCTURE_M2,
    ]


def test_polygonise_keeps_the_room_of_a_single_room_house(box_model):
    """The envelope face and the room face contain each other's representative point.

    A containment filter that is not area-aware discards both and reports zero rooms.
    """
    faces = box_model.level("ground").network.faces()
    assert len(faces) == 1
    assert faces[0].area == pytest.approx(15.0, rel=1e-12)


def test_polygonise_on_empty_geometry_is_empty():
    assert polygonise(Polygon()) == ()


# --------------------------------------------------------------------------------------
# Room polygons -- rectangular
# --------------------------------------------------------------------------------------


def test_rectangular_room_structure_area(box_model):
    assert box_model.room("G_R1").area("structure") == pytest.approx(15.0, rel=1e-12)


def test_rectangular_room_finish_area(box_model):
    assert box_model.room("G_R1").area("finish") == pytest.approx(14.6816, rel=1e-12)


def test_room_polygon_is_the_clear_structural_rectangle(box_model):
    polygon = box_model.room("G_R1").polygon("structure")
    assert polygon.symmetric_difference(box(0.5, 0.5, 5.5, 3.5)).area == pytest.approx(
        0.0, abs=1e-12
    )


# --------------------------------------------------------------------------------------
# The measurement-face parameter
# --------------------------------------------------------------------------------------


def test_measure_to_shrinks_the_room_by_exactly_the_allowance(box_model):
    """Direction and magnitude, both checked against closed-form arithmetic.

    For a w x h room and an allowance a per face the loss is 2a(w + h) - 4a^2, which for
    5.0 x 3.0 and 20 mm is 0.3184 m2, i.e. -2.12 %.
    """
    structure = box_model.room("G_R1").area("structure")
    finish = box_model.room("G_R1").area("finish")
    allowance = to_m(DEFAULT_FINISH_ALLOWANCE_MM)
    expected_loss = 2 * allowance * (5.0 + 3.0) - 4 * allowance**2

    assert finish < structure
    assert structure - finish == pytest.approx(expected_loss, rel=1e-12)
    assert structure - finish == pytest.approx(0.3184, rel=1e-12)
    assert (structure - finish) / structure == pytest.approx(0.021227, rel=1e-4)


def test_measure_to_is_not_hardcoded_at_the_model_level(box_spec):
    """The same spec built both ways gives both answers -- this is T15's sweep."""
    structural = build_model(box_spec, measure_to="structure")
    finished = structural.with_measure_to("finish")
    assert structural.room("G_R1").area() == pytest.approx(15.0, rel=1e-12)
    assert finished.room("G_R1").area() == pytest.approx(14.6816, rel=1e-12)


def test_room_polygon_function_requires_an_explicit_face(box_spec):
    assert room_polygon(box_spec, "G_R1", measure_to="structure").area == pytest.approx(
        15.0, rel=1e-12
    )
    with pytest.raises(TypeError):
        room_polygon(box_spec, "G_R1")  # type: ignore[call-arg]


def test_unknown_measurement_face_is_rejected(box_model):
    with pytest.raises(GeometryError, match="measurement face"):
        box_model.room("G_R1").polygon("plaster")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("allowance_mm", "expected"),
    [
        (0, 5.0 * 3.0),
        (10, 4.98 * 2.98),
        (20, 4.96 * 2.96),
        (50, 4.90 * 2.90),
    ],
)
def test_finish_allowance_is_a_parameter(box_spec, allowance_mm, expected):
    """20 mm is a default, not a constant: two samples is not proof (README)."""
    model = build_model(box_spec, finish_allowance_mm=allowance_mm)
    assert model.room("G_R1").area("finish") == pytest.approx(expected, rel=1e-12)


# --------------------------------------------------------------------------------------
# The per-room override -- rooms[].measure_to (T19, from docs/T18-findings.md item 1)
#
# The real instance is A_R4 Schody: a slab opening, not a room w swietle scian, so it
# takes no finish allowance and its plan projection runs under the guard at its head.
# Exercised here on the synthetic row fixture, whose third room is also called Schody:
#
#   G_C  x 4600..6500, y 500..2500 = 1.9 x 2.0 = 3.80 m2 at structure
#   extended under the 200 mm partition G_P2  -> x 4400..6500 = 2.1 x 2.0 = 4.20 m2
# --------------------------------------------------------------------------------------

SCHODY_STRUCTURE_M2 = 3.8
SCHODY_EXTENDED_M2 = 4.2


def row_spec_with_override(tmp_path, measure_to):
    """The row fixture with the override applied to its Schody, G_C."""
    rooms = [
        room(
            "G_C",
            3,
            "Schody",
            ["G_S", "G_N", "G_P2", "G_E"],
            3.8,
            groups=["net"],
            measure_to=measure_to,
        )
        if item["id"] == "G_C"
        else item
        for item in ROW_ROOMS
    ]
    return make_spec(tmp_path, walls=ROW_WALLS, rooms=rooms)


def test_a_room_may_declare_the_face_it_is_measured_to(tmp_path):
    """The declared face beats the model-wide default, and only for that room.

    This is the whole mechanism: the model is built to ``finish`` exactly as the area
    checks build it, and the one room that says ``structure`` gets structure while its
    neighbours are unaffected.
    """
    spec = row_spec_with_override(tmp_path, {"face": "structure"})
    model = build_model(spec, measure_to="finish")

    assert model.room("G_C").measured_to() == "structure"
    assert model.room("G_C").area() == pytest.approx(SCHODY_STRUCTURE_M2, rel=1e-12)
    assert model.room("G_A").measured_to() == "finish"
    assert model.room("G_A").area() == pytest.approx(1.86 * 1.96, rel=1e-12)


def test_a_declared_face_also_beats_an_explicit_request(tmp_path):
    """Asking for ``finish`` on a room with no finish face does not invent one.

    A room that declares its face is stating a fact about its boundaries, not a
    preference. Schody's edges are a slab edge and two guards; there is no plaster on
    them to deduct, so there is no finish face to return. This is what makes the T15
    allowance sweep and the T08 structure-vs-finish diagnosis hold it at structure
    without either of them naming it.
    """
    spec = row_spec_with_override(tmp_path, {"face": "structure"})
    model = build_model(spec, measure_to="structure")

    assert model.room("G_C").measured_to("finish") == "structure"
    assert model.room("G_C").area("finish") == pytest.approx(SCHODY_STRUCTURE_M2, rel=1e-12)
    # ...and the mechanism is not a blanket "ignore the argument": G_A still moves.
    assert model.room("G_A").area("finish") == pytest.approx(1.86 * 1.96, rel=1e-12)


def test_a_room_with_no_override_is_untouched_by_the_mechanism(tmp_path):
    """17 of the 18 real rooms carry no ``measure_to``; nothing may change for them."""
    plain = build_model(make_spec(tmp_path, walls=ROW_WALLS, rooms=ROW_ROOMS))
    assert plain.room("G_C").measured_to() == "structure"
    assert plain.room("G_C").measured_to("finish") == "finish"
    assert plain.room("G_C").area("finish") == pytest.approx(1.86 * 1.96, rel=1e-12)


def test_extends_under_grows_the_room_across_the_named_wall(tmp_path):
    """The guard's footprint is handed to the room it stands in, exactly and only it.

    A 200 mm partition between two rooms takes its own footprint out of both. Where the
    "wall" is a balustrade standing at the head of a slab opening, the opening runs to the
    slab edge underneath it, and polygonisation cannot say so -- the spec does.
    """
    spec = row_spec_with_override(
        tmp_path, {"face": "structure", "extends_under": ["G_P2"]}
    )
    model = build_model(spec)
    polygon = model.room("G_C").polygon()

    assert polygon.area == pytest.approx(SCHODY_EXTENDED_M2, rel=1e-12)
    assert polygon.symmetric_difference(box(4.4, 0.5, 6.5, 2.5)).area == pytest.approx(
        0.0, abs=1e-12
    )
    assert polygon.area - SCHODY_STRUCTURE_M2 == pytest.approx(0.2 * 2.0, rel=1e-12)


def test_extends_under_takes_the_strip_across_this_room_and_no_more(tmp_path):
    """The band is clipped to the room, not to the wall: G_P2 also bounds G_B.

    G_P2 runs the full 2500 mm depth of the plan and is 200 mm thick, so its footprint is
    0.5 m2. Only the 0.4 m2 lying across G_C's own edge may move, and the neighbour it is
    taken from keeps every square metre it had.
    """
    rooms_before = build_model(make_spec(tmp_path, walls=ROW_WALLS, rooms=ROW_ROOMS))
    spec = row_spec_with_override(
        tmp_path, {"face": "structure", "extends_under": ["G_P2"]}
    )
    after = build_model(spec)

    assert after.room("G_B").polygon().area == pytest.approx(
        rooms_before.room("G_B").polygon().area, rel=1e-12
    )
    assert after.room("G_A").polygon().area == pytest.approx(
        rooms_before.room("G_A").polygon().area, rel=1e-12
    )
    # The two rooms now share the y = 4400..4600 boundary strip: they abut, and abutting
    # is not overlapping. T10's room-overlap check depends on this staying true.
    overlap = after.room("G_B").polygon().intersection(after.room("G_C").polygon())
    assert overlap.area == pytest.approx(0.0, abs=1e-12)


def test_extends_under_must_name_a_wall_that_bounds_the_room(tmp_path):
    """A room may only annex a wall it is actually bounded by -- the loader says so.

    Without this, ``extends_under`` would be a licence to grow a room across any wall on
    the level, i.e. to take floor area from a room that never agreed to give it up.
    """
    with pytest.raises(SpecValidationError, match="extends_under"):
        row_spec_with_override(tmp_path, {"face": "structure", "extends_under": ["G_W"]})


def test_finish_allowance_comes_from_the_spec(tmp_path):
    spec = make_spec(tmp_path, walls=BOX_WALLS, rooms=BOX_ROOMS, finish_allowance=15)
    model = build_model(spec)
    assert model.finish_allowance_mm == 15
    assert model.room("G_R1").area("finish") == pytest.approx(4.97 * 2.97, rel=1e-12)


@pytest.mark.parametrize(
    ("clear_x", "clear_y", "published"),
    [
        (3800, 3230, 11.99),  # Pokoj, README "The finish allowance"
        (2600, 1400, 3.48),  # Lazienka
    ],
)
def test_finish_face_reproduces_the_two_sampled_published_rooms(
    tmp_path, clear_x, clear_y, published
):
    """The reconciliation from README.md, reproduced from geometry rather than arithmetic.

    Raw structure overshoots the published area by 2.4 % / 4.6 %; the finished face lands
    inside 0.05 %. The rooms are synthetic (a single room in its own little box) -- the
    published numbers are the real ones, which is the whole point of the check.
    """
    walls = [
        wall("G_S", (250, 250), (clear_x + 750, 250), 500),
        wall("G_N", (250, clear_y + 750), (clear_x + 750, clear_y + 750), 500),
        wall("G_W", (250, 250), (250, clear_y + 750), 500),
        wall("G_E", (clear_x + 750, 250), (clear_x + 750, clear_y + 750), 500),
    ]
    rooms = [room("G_R", 1, "Pokoj", ["G_S", "G_N", "G_W", "G_E"], published)]
    model = build_model(make_spec(tmp_path, walls=walls, rooms=rooms))

    structure = model.room("G_R").area("structure")
    finish = model.room("G_R").area("finish")

    assert structure == pytest.approx(to_m(clear_x) * to_m(clear_y), rel=1e-12)
    assert abs(structure - published) / published > 0.02
    assert abs(finish - published) / published < 0.0005


# --------------------------------------------------------------------------------------
# Room polygons -- non-convex
# --------------------------------------------------------------------------------------


def test_l_shaped_room_area_structure(l_model):
    assert l_model.room("G_L").area("structure") == pytest.approx(L_ROOM_STRUCTURE_M2, rel=1e-12)


def test_l_shaped_room_area_finish(l_model):
    assert l_model.room("G_L").area("finish") == pytest.approx(L_ROOM_FINISH_M2, rel=1e-12)


def test_l_shaped_room_polygon_matches_hand_construction(l_model):
    polygon = l_model.room("G_L").polygon("structure")
    expected = l_room_polygon_by_hand()
    assert polygon.symmetric_difference(expected).area == pytest.approx(0.0, abs=1e-12)


def test_l_shaped_finish_polygon_matches_hand_construction(l_model):
    polygon = l_model.room("G_L").polygon("finish")
    expected = l_room_polygon_by_hand(inset_mm=20)
    assert polygon.symmetric_difference(expected).area == pytest.approx(0.0, abs=1e-12)


def test_l_shaped_room_really_is_non_convex(l_model):
    """Guards the fixture itself: a convex 'L' would make this whole file vacuous."""
    polygon = l_model.room("G_L").polygon("structure")
    assert polygon.is_valid
    # simplify(0) drops the collinear vertices polygonisation leaves at wall junctions.
    assert len(polygon.simplify(0).exterior.coords) == 7  # six corners, first repeated
    # The hull cuts the 2125 x 2060 notch diagonally, so it recovers exactly half of it.
    assert polygon.convex_hull.area - polygon.area == pytest.approx(
        to_m(2125) * to_m(2060) / 2, rel=1e-9
    )


def test_rooms_do_not_share_a_face(l_model):
    """Two rooms resolving to the same face would make every area check meaningless."""
    first = l_model.room("G_L").polygon("structure")
    second = l_model.room("G_SMALL").polygon("structure")
    assert first.intersection(second).area == pytest.approx(0.0, abs=1e-12)


def test_room_bounded_by_walls_of_differing_thickness(l_model):
    """The small room is bounded by 500, 500, 250 and 120 mm walls, one per side."""
    thicknesses = {
        l_model.level("ground").network.wall_by_id[wall_id].thickness_m
        for wall_id in ("G_N", "G_E", "G_P1", "G_P2")
    }
    assert thicknesses == {0.5, 0.25, 0.12}
    assert l_model.room("G_SMALL").area("structure") == pytest.approx(
        SMALL_ROOM_STRUCTURE_M2, rel=1e-12
    )
    assert l_model.room("G_SMALL").area("finish") == pytest.approx(
        SMALL_ROOM_FINISH_M2, rel=1e-12
    )


# --------------------------------------------------------------------------------------
# Seeds
# --------------------------------------------------------------------------------------


def test_seed_is_derived_when_the_spec_omits_it(l_model):
    """Both fixture rooms omit `seed`; both resolve from their boundary wall sets."""
    for room_id in ("G_L", "G_SMALL"):
        seed = l_model.room(room_id).seed
        assert l_model.room(room_id).polygon("structure").contains(seed)


def test_explicit_seed_and_derived_seed_select_the_same_face(tmp_path, l_model):
    rooms = [
        room(
            "G_L",
            1,
            "Salon",
            ["G_S", "G_N", "G_W", "G_E", "G_P1", "G_P2"],
            15.62,
            seed=(1000, 1000),
        ),
        room("G_SMALL", 2, "Spizarnia", ["G_N", "G_E", "G_P1", "G_P2"], 3.64, seed=(5000, 4000)),
    ]
    seeded = build_model(make_spec(tmp_path, walls=L_WALLS, rooms=rooms))
    for room_id in ("G_L", "G_SMALL"):
        assert seeded.room(room_id).polygon("structure").symmetric_difference(
            l_model.room(room_id).polygon("structure")
        ).area == pytest.approx(0.0, abs=1e-12)


def test_seed_inside_a_wall_is_a_diagnosable_error(tmp_path):
    rooms = [room("G_R1", 1, "Pokoj", ["G_S", "G_N", "G_W", "G_E"], 15.0, seed=(100, 100))]
    model = build_model(make_spec(tmp_path, walls=BOX_WALLS, rooms=rooms))
    with pytest.raises(GeometryError, match="falls in none of the"):
        model.room("G_R1").polygon("structure")


def test_unclosed_wall_network_is_reported_rather_than_guessed(tmp_path):
    """Drop one wall: there is no enclosed face, and the error says so."""
    walls = [w for w in BOX_WALLS if w["id"] != "G_E"]
    rooms = [room("G_R1", 1, "Pokoj", ["G_S", "G_N", "G_W"], 15.0, seed=(3000, 2000))]
    model = build_model(make_spec(tmp_path, walls=walls, rooms=rooms))
    with pytest.raises(GeometryError, match="closed loop"):
        model.room("G_R1").polygon("structure")


# --------------------------------------------------------------------------------------
# Layered walls
# --------------------------------------------------------------------------------------


@pytest.fixture
def layered_model(tmp_path):
    """A 465 mm layered exterior wall: Porotherm 250 + EPS 200 + render 15."""
    walls = [
        wall("G_S", (1000, 1000), (9000, 1000), 465, layers=EXTERIOR_LAYERS),
        wall("G_N", (1000, 6000), (9000, 6000), 465, layers=EXTERIOR_LAYERS),
        wall("G_W", (1000, 1000), (1000, 6000), 465, layers=EXTERIOR_LAYERS),
        wall("G_E", (9000, 1000), (9000, 6000), 465, layers=EXTERIOR_LAYERS),
    ]
    rooms = [room("G_R", 1, "Pokoj", ["G_S", "G_N", "G_W", "G_E"], 39.0)]
    return build_model(make_spec(tmp_path, walls=walls, rooms=rooms))


def test_exterior_wall_is_layered_not_a_single_slab(layered_model):
    bands = layered_model.level("ground").network.layer_bands("G_W")
    assert [layer.material for layer, _ in bands] == ["Porotherm 25", "EPS", "tynk"]
    assert [round(layer.thickness_m, 6) for layer, _ in bands] == [0.25, 0.2, 0.015]


def test_layer_bands_tile_the_wall_without_overlap(layered_model):
    network = layered_model.level("ground").network
    bands = network.layer_bands("G_W")
    footprint = network.wall_by_id["G_W"].footprint()
    total = sum(polygon.area for _, polygon in bands)
    assert total == pytest.approx(footprint.area, rel=1e-9)
    assert bands[0][1].intersection(bands[1][1]).area == pytest.approx(0.0, abs=1e-12)


def test_innermost_layer_face_is_the_structure_measurement_face(layered_model):
    """The Porotherm's inner face and the room's `structure` polygon are the same line.

    This is what makes `measure_to="structure"` mean something physical rather than being
    a synonym for 'half the wall thickness'.
    """
    network = layered_model.level("ground").network
    porotherm = network.layer_bands("G_W")[0][1]
    room_polygon_ = layered_model.room("G_R").polygon("structure")
    assert max(x for x, _ in porotherm.exterior.coords) == pytest.approx(
        room_polygon_.bounds[0], rel=1e-12
    )
    assert network.interior_side("G_W") == -1


def test_face_line_moves_with_the_measurement_face(layered_model):
    solid = layered_model.level("ground").network.wall_by_id["G_W"]
    structure = solid.face_line(-1, "structure", to_m(20))
    finish = solid.face_line(-1, "finish", to_m(20))
    assert structure.coords[0][0] == pytest.approx(to_m(1232.5), rel=1e-12)
    assert finish.coords[0][0] == pytest.approx(to_m(1252.5), rel=1e-12)


# --------------------------------------------------------------------------------------
# Quantities -- footprint and aggregates
# --------------------------------------------------------------------------------------


def test_footprint_polygon_is_the_outer_envelope(box_model):
    level = box_model.level("ground")
    assert footprint_polygon(level).symmetric_difference(box(0, 0, 6, 4)).area == pytest.approx(
        0.0, abs=1e-12
    )
    assert footprint_area(level) == pytest.approx(24.0, rel=1e-12)


def test_footprint_includes_the_render_layer(layered_model):
    """465 mm walls include the 15 mm render, so pow. zabudowy is to the finished face."""
    assert footprint_area(layered_model.level("ground")) == pytest.approx(
        to_m(8465) * to_m(5465), rel=1e-12
    )


def test_room_area_helper_matches_the_polygon(box_model):
    room_geometry = box_model.room("G_R1")
    assert room_area(room_geometry, "structure") == pytest.approx(15.0, rel=1e-12)
    assert room_area(room_geometry.polygon("finish")) == pytest.approx(14.6816, rel=1e-12)


def test_usable_area_excludes_the_stairs(row_model):
    assert usable_area(row_model.level("ground")) == pytest.approx(3.8 + 3.6, rel=1e-12)


def test_usable_area_can_include_the_stairs(row_model):
    assert usable_area(row_model.level("ground"), include_stairs=True) == pytest.approx(
        3.8 + 3.6 + 3.8, rel=1e-12
    )


def test_net_area_includes_stairs_and_excludes_the_boiler_room(row_model):
    assert net_area(row_model.level("ground")) == pytest.approx(3.8 + 3.8, rel=1e-12)


def test_area_groups_are_read_not_inferred(row_model):
    level = row_model.level("ground")
    assert area_in_group(level, "boiler") == pytest.approx(3.6, rel=1e-12)
    assert area_in_group(level, "usable") == pytest.approx(7.4, rel=1e-12)
    assert area_in_group(level, "attic") == pytest.approx(0.0, abs=1e-12)


def test_aggregates_respect_the_measurement_face(row_model):
    structure = usable_area(row_model.level("ground"), measure_to="structure")
    finish = usable_area(row_model.level("ground"), measure_to="finish")
    assert finish < structure
    assert structure - finish == pytest.approx(
        (2 * 0.02 * (1.9 + 2.0) - 4 * 0.02**2) + (2 * 0.02 * (1.8 + 2.0) - 4 * 0.02**2),
        rel=1e-9,
    )


def test_model_level_aggregate_covers_every_storey(row_model):
    assert usable_area(row_model) == pytest.approx(usable_area(row_model.level("ground")))


# --------------------------------------------------------------------------------------
# The attic height banding
# --------------------------------------------------------------------------------------

#: A 35 deg ceiling springing from the top of a 290 mm knee wall on the 3.04 m attic
#: floor, ridge centred on an 8.10 m clear span. This is the real house's geometry.
ATTIC_CEILING = SlopedCeiling(
    ridge_axis="x",
    ridge_coord_m=0.0,
    springing_offset_m=4.05,
    springing_elevation_m=3.33,
    pitch_deg=35.0,
)
ATTIC_FLOOR_M = 3.04


def test_bands_are_1_4_and_2_2_not_1_9():
    assert DEFAULT_HEIGHT_BANDS == ((2.2, 1.0), (1.4, 0.5))


def test_ceiling_springs_from_the_knee_wall_top(tmp_path):
    """`SlopedCeiling.from_spec` uses 3040 + 290, not the 3610 outer-plane figure.

    3610 is the roof's *outer* plane at the wall face -- 280 mm of build-up above the
    ceiling. Banding is measured to the ceiling. Using 3610 would move the 1.4 m contour
    from 1.585 m to 1.185 m, a 0.4 m error against contours T17 measured to 4 mm.
    """
    spec = make_spec(tmp_path, walls=BOX_WALLS, rooms=BOX_ROOMS)
    ceiling = SlopedCeiling.from_spec(spec, ridge_coord_mm=0, springing_offset_mm=4050)
    assert ceiling.springing_elevation_m == pytest.approx(3.33, rel=1e-12)
    assert ceiling.pitch_deg == 35.0
    assert ceiling.ridge_axis == "x"
    assert ceiling.ridge_elevation_m == pytest.approx(3.33 + 4.05 * math.tan(math.radians(35)))


def test_contours_reproduce_the_t17_measurements():
    """d140 = 1.589 m and d220 = 2.726 m, measured off plan_attic.png at 40.9 px/m.

    T17 called this the decisive evidence for the 35 deg pitch. If the banding geometry
    here is right, it has to land on the same two numbers.
    """
    d140 = ATTIC_CEILING.contour_distance_from_springing(1.4, ATTIC_FLOOR_M)
    d220 = ATTIC_CEILING.contour_distance_from_springing(2.2, ATTIC_FLOOR_M)
    assert d140 == pytest.approx(1.585, abs=0.001)
    assert d220 == pytest.approx(2.728, abs=0.001)
    assert abs(d140 - 1.589) < 0.005
    assert abs(d220 - 2.726) < 0.005


def test_usable_area_sloped_hand_computed_at_45_degrees():
    """A case whose arithmetic can be done on paper, because tan 45 = 1.

    Ceiling springs at +3.33 over a floor at +3.04, so it is 0.29 m clear at the wall and
    rises 1 m per metre. Over a half span of 4.05 m the ridge is 4.34 m clear.
      2.2 m contour: 4.34 - 2.2 = 2.14 m from the ridge
      1.4 m contour: 4.34 - 1.4 = 2.94 m from the ridge
    Counted depth = 2(2.14) + 0.5 * 2(2.94 - 2.14) = 4.28 + 0.80 = 5.08 m
    Over a 5 m long room: 25.4 m2, out of a 40.5 m2 floor.
    """
    ceiling = SlopedCeiling(
        ridge_axis="x",
        ridge_coord_m=0.0,
        springing_offset_m=4.05,
        springing_elevation_m=3.33,
        pitch_deg=45.0,
    )
    polygon = box(0.0, -4.05, 5.0, 4.05)
    assert polygon.area == pytest.approx(40.5, rel=1e-12)
    assert usable_area_sloped(polygon, ceiling, ATTIC_FLOOR_M) == pytest.approx(25.4, abs=1e-9)


def test_usable_area_sloped_matches_t17_strych_prediction():
    """T17 section 5: counted depth 3.785 m, and 14.87 m2 for the 3.93 m long Strych.

    The expected value is recomputed here from first principles (contour distances from
    tan 35), not taken from the kernel, so this is an independent check rather than a
    restatement.
    """
    length = 3.93
    slope = math.tan(math.radians(35.0))
    clear_at_ridge = 3.33 + 4.05 * slope - ATTIC_FLOOR_M
    u220 = (clear_at_ridge - 2.2) / slope
    u140 = (clear_at_ridge - 1.4) / slope
    counted_depth = 2 * u220 + 0.5 * 2 * (u140 - u220)
    expected = counted_depth * length

    polygon = box(0.0, -4.05, length, 4.05)
    computed = usable_area_sloped(polygon, ATTIC_CEILING, ATTIC_FLOOR_M)

    assert counted_depth == pytest.approx(3.787, abs=0.002)
    assert computed == pytest.approx(expected, rel=1e-12)
    assert abs(computed - 14.87) / 14.87 < 0.002


def test_bands_tile_the_room_exactly():
    polygon = box(0.0, -4.05, 3.93, 4.05)
    bands = banded_polygons(polygon, ATTIC_CEILING, ATTIC_FLOOR_M)
    assert [band.factor for band in bands] == [1.0, 0.5, 0.0]
    assert sum(band.area for band in bands) == pytest.approx(polygon.area, rel=1e-12)


def test_band_breakdown_is_diagnostic():
    polygon = box(0.0, -4.05, 3.93, 4.05)
    breakdown = sloped_band_areas(polygon, ATTIC_CEILING, ATTIC_FLOOR_M)
    assert breakdown["floor"] == pytest.approx(polygon.area, rel=1e-12)
    assert breakdown["counted"] < breakdown["floor"]
    assert breakdown["band_2.2"] > breakdown["band_1.4"] > breakdown["band_0"] * 0.0


def test_room_entirely_above_2_2_counts_in_full():
    """A strip either side of the ridge, all of it well over 2.2 m clear."""
    polygon = box(0.0, -0.5, 4.0, 0.5)
    assert usable_area_sloped(polygon, ATTIC_CEILING, ATTIC_FLOOR_M) == pytest.approx(
        polygon.area, rel=1e-12
    )


def test_room_entirely_below_1_4_counts_for_nothing():
    """Against the knee wall, where the ceiling is 0.29 m clear."""
    polygon = box(0.0, 3.8, 4.0, 4.05)
    assert usable_area_sloped(polygon, ATTIC_CEILING, ATTIC_FLOOR_M) == pytest.approx(
        0.0, abs=1e-12
    )


def test_banding_works_with_the_ridge_on_the_other_axis():
    """Same room rotated 90 deg: the answer must not depend on the axis convention."""
    along_x = SlopedCeiling(
        ridge_axis="x",
        ridge_coord_m=0.0,
        springing_offset_m=4.05,
        springing_elevation_m=3.33,
        pitch_deg=35.0,
    )
    along_y = SlopedCeiling(
        ridge_axis="y",
        ridge_coord_m=0.0,
        springing_offset_m=4.05,
        springing_elevation_m=3.33,
        pitch_deg=35.0,
    )
    assert usable_area_sloped(box(0.0, -4.05, 3.93, 4.05), along_x, ATTIC_FLOOR_M) == (
        pytest.approx(usable_area_sloped(box(-4.05, 0.0, 4.05, 3.93), along_y, ATTIC_FLOOR_M))
    )


def test_banding_is_offset_by_the_ridge_position():
    """A ridge that is not at the origin shifts every contour with it."""
    shifted = SlopedCeiling(
        ridge_axis="x",
        ridge_coord_m=7.5,
        springing_offset_m=4.05,
        springing_elevation_m=3.33,
        pitch_deg=35.0,
    )
    centred = usable_area_sloped(box(0.0, -4.05, 3.93, 4.05), ATTIC_CEILING, ATTIC_FLOOR_M)
    moved = usable_area_sloped(box(0.0, 3.45, 3.93, 11.55), shifted, ATTIC_FLOOR_M)
    assert moved == pytest.approx(centred, rel=1e-12)


def test_height_at_a_point_is_the_ceiling_minus_the_floor():
    assert ATTIC_CEILING.height_at((0.0, 4.05), ATTIC_FLOOR_M) == pytest.approx(0.29, rel=1e-12)
    assert ATTIC_CEILING.height_at((0.0, 0.0), ATTIC_FLOOR_M) == pytest.approx(
        0.29 + 4.05 * math.tan(math.radians(35.0)), rel=1e-12
    )


def test_flat_ceiling_has_no_contours_to_place():
    flat = SlopedCeiling(
        ridge_axis="x",
        ridge_coord_m=0.0,
        springing_offset_m=4.05,
        springing_elevation_m=3.33,
        pitch_deg=0.0,
    )
    with pytest.raises(GeometryError, match="flat ceiling"):
        flat.offset_for_height(1.4, ATTIC_FLOOR_M)


def test_attic_rooms_can_be_banded_through_the_aggregate(tmp_path):
    """End to end: an attic level whose usable area is banded, not raw floor area."""
    attic_walls = [
        wall("A_S", (250, 250), (5750, 250), 500, level="attic"),
        wall("A_N", (250, 8850), (5750, 8850), 500, level="attic"),
        wall("A_W", (250, 250), (250, 8850), 500, level="attic"),
        wall("A_E", (5750, 250), (5750, 8850), 500, level="attic"),
    ]
    attic_rooms = [
        room(
            "A_STRYCH",
            2,
            "Strych ocieplony",
            ["A_S", "A_N", "A_W", "A_E"],
            14.67,
            level="attic",
            groups=["usable", "attic"],
        )
    ]
    model = build_model(make_spec(tmp_path, attic_walls=attic_walls, attic_rooms=attic_rooms))
    level = model.level("attic")
    floor_polygon = model.room("A_STRYCH").polygon("structure")

    # Clear span 8.10 m in y (0.50 .. 8.60), ridge on its centreline, room 5.00 m long.
    ceiling = SlopedCeiling(
        ridge_axis="x",
        ridge_coord_m=to_m(4550),
        springing_offset_m=4.05,
        springing_elevation_m=3.33,
        pitch_deg=35.0,
    )
    raw = usable_area(level)
    banded = usable_area(level, sloped_ceiling=ceiling)

    assert raw == pytest.approx(5.0 * 8.1, rel=1e-12)
    assert banded < raw
    assert banded == pytest.approx(
        usable_area_sloped(floor_polygon, ceiling, level.elevation_m), rel=1e-12
    )


# --------------------------------------------------------------------------------------
# Behaviour before the transcription lands
# --------------------------------------------------------------------------------------


def test_model_builds_from_a_spec_with_no_walls_or_rooms(tmp_path):
    """T04/T05 have not landed. An empty level is a state, not an error.

    tests/conftest.py's `model` fixture calls build() on the real spec, so this must not
    raise while ground.json and attic.json are still stubs.
    """
    model = build_model(make_spec(tmp_path))
    assert sorted(model.levels) == ["attic", "ground"]
    assert model.rooms == ()
    assert model.level("ground").network.faces() == ()
    assert footprint_area(model.level("ground")) == 0.0
    assert usable_area(model.level("ground")) == 0.0


def test_missing_room_and_level_lookups_say_what_exists(box_model):
    with pytest.raises(GeometryError, match="no room"):
        box_model.room("G_NOPE")
    with pytest.raises(GeometryError, match="no level"):
        box_model.level("basement")


def test_kernel_accepts_the_raw_merged_mapping_too(box_spec):
    """Downstream tasks may hold the merged dict rather than the typed Spec."""
    model = build_model(box_spec.to_dict())
    assert model.room("G_R1").area("structure") == pytest.approx(15.0, rel=1e-12)


def test_seed_point_helper_is_a_point(box_model):
    assert isinstance(box_model.room("G_R1").seed, Point)

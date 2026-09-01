"""T12 -- Test 5: the exported artifact.

T06-T10 validate the spec and the 2D geometry. T11 validates nothing by itself. Between
"correct scene in memory" and "correct file in the browser" sit unit-scale bugs, exporter
transforms and mesh corruption, and **no upstream test can see any of them**. This module
loads ``build/model.glb`` back off disk and asserts against the file.

Everything here is measured against the artifact, not the intent: the scene is rebuilt and
re-exported by the :func:`artifacts` fixture on every run, so a stale ``build/`` cannot
pass a check, and every number below was read off the mesh rather than restated from the
spec.

WHAT THE BOUNDING BOX IS, AND WHY THE BRIEF'S VERSION OF THE CHECK IS WRONG
---------------------------------------------------------------------------
``tasks/T12.md`` and ``TESTS.md`` both suggest::

    bbox = scene.bounding_box.extents
    assert np.allclose(bbox[:2], EXPECTED_FOOTPRINT_M, atol=0.01)
    assert np.isclose(bbox[2], 7.09, atol=0.01)

The first line is right once ``EXPECTED_FOOTPRINT_M`` is understood to be the **roof**
plan extent -- the roof is in the scene and overhangs the walls -- and the brief says so.
The second line is **not** right and asserting it would be asserting the convenient
quantity rather than the correct one. The scene's Z extent is 7.681 m, not 7.09::

    -0.320  plinth underside, at the terrain          <- scene Z min
     0.000  finished ground floor, the model origin
     6.761  ridge, derived                            <- 7.081 above the terrain
     7.361  chimney tops, 600 mm above the ridge      <- scene Z max

Both the plinth and the stacks are real geometry and neither is an error. The published
7.09 m is *terrain to ridge*, so it is asserted against the **roof** nodes' bbox top
(:func:`test_building_height_is_measured_to_the_ridge_not_the_stacks`), and the whole-scene
Z extent is decomposed and pinned separately so that its 7.681 m is a recorded fact rather
than an unexplained number.

The 7.09 m check is also **not** given the brief's +-10 mm. The reconstruction lands at
7.0809 m, 9.07 mm low, which would scrape through +-10 mm with 0.9 mm to spare -- a margin
smaller than the uncertainty on the input that produces it. T09 argues the physically
honest tolerance is +-30 mm, the measured uncertainty on ``roof_buildup_vertical``, and
this module uses the same one. The exact value is pinned separately at 1e-5, so tightness
is not lost: what is dropped is a false claim of precision, not a check.

WATERTIGHTNESS: HONEST ANSWER
-----------------------------
All 106 components are watertight, winding-consistent and positive-volume -- that part is
clean. What is *not* clean is the scene as a solid: the components interpenetrate by a
measured **21.003 m3 over 149 overlapping pairs** against a 267.286 m3 sum of member
volumes, so ``sum(mesh.volume)`` is not a material volume and nothing may treat it as one.
That is measured here in full rather than sampled -- see
:func:`test_the_solids_interpenetrate_and_the_sum_is_not_a_material_volume` and the note
there about what ``tests/test_invariants.py`` records versus what is actually there.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import re
import struct
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from kotewki.export import (
    GLB_SIZE_WARN_BYTES,
    QUANTITIES_SCHEMA,
    ExportError,
    ExportResult,
    assert_metres,
    build_artifacts,
    ground_stair_run_m2,
)
from kotewki.generator import build_scene

REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------------------
# What this module measured, so a drift is a diff rather than a silence
# --------------------------------------------------------------------------------------

#: Counts of the exported scene. Pinned exactly *and* banded: the exact figure catches a
#: silent change, the band is the brief's "a 10x jump between builds signals a boolean
#: blowup". If the generator legitimately changes, update the record -- that is the edit
#: the pin exists to force.
RECORDED_MESH: dict[str, int] = {"nodes": 106, "vertices": 1400, "faces": 2428}

#: A house of this size is thousands, not tens and not millions. A CSG blow-up lands above
#: it; a silently-empty scene lands below.
VERTEX_BAND = (500, 20_000)

#: Sum of the member solids' volumes, m3. **Not a material volume** -- see
#: :func:`test_the_solids_interpenetrate_and_the_sum_is_not_a_material_volume`.
RECORDED_SOLID_VOLUME_SUM_M3 = 267.1082

#: Total pairwise interpenetration of those solids, m3, and how many pairs it is spread
#: over. Measured exhaustively over all 5 565 pairs (bbox-prefiltered), not sampled.
#:
#: The count was 150 until T19 widened the roof windows by one pixel of the source raster
#: (docs/T18-findings.md item 2). The pair that left was
#: ``attic/walls/A_W5/structure`` x ``roof/slab`` at **2.3e-9 m3** -- a CSG artefact
#: sitting just above :func:`_pairwise_overlaps`' 1e-9 cut, which re-cutting the roof slab
#: pushed below it. The total is unchanged to 1e-8 m3. Recorded as measured rather than
#: stabilised by raising the cut: the threshold is what makes "these two solids overlap"
#: mean something, and moving it to keep a count round is exactly the wrong instinct.
RECORDED_OVERLAP_M3 = 21.0028
RECORDED_OVERLAP_PAIRS = 149

#: The largest overlaps, m3. Note the second and third: they are **not** in
#: ``tests/test_invariants.RECORDED_MESH_OVERLAPS``, which records the four chimney-related
#: pairs. See the test for why that matters.
RECORDED_TOP_OVERLAPS: dict[tuple[str, str], float] = {
    ("ground/chimneys/G_W27/structure", "attic/chimneys/A_W9/structure"): 1.7923,
    ("ground/walls/G_W7/porotherm_25", "attic/slabs/floor"): 1.4535,
    ("ground/walls/G_W7/termo_organika_eps", "attic/slabs/floor"): 1.1628,
    ("ground/chimneys/G_W28/structure", "attic/chimneys/A_W10/structure"): 1.0480,
    ("ground/walls/G_W5/porotherm_25", "attic/slabs/floor"): 0.8636,
}

#: Elevations read off the **mesh**, metres. Independent of the spec: these are where the
#: geometry actually sits after generation and export.
RECORDED_Z: dict[str, float] = {
    "terrain": -0.320,  # plinth underside = scene Z min
    "ground_floor": 0.000,  # model origin
    "eave_fascia_underside": 2.879875,  # roof nodes' Z min
    "ridge": 6.760934,  # roof nodes' Z max
    "chimney_top": 7.360934,  # scene Z max
}

#: Building height, terrain to ridge, off the mesh. Published 7.09 m; this is -9.07 mm.
RECORDED_BUILDING_HEIGHT_M = 7.080934

#: Plan extents, metres. Three different rectangles, and using the wrong one is the whole
#: content of the brief's "bounding box subtlety".
#:
#: * ``roof``      18.280 x 10.200 -- the scene bbox. Structural outline + 590 mm verge
#:   and 600 mm eaves overhangs on each side.
#: * ``finished``  17.120 x 9.020  -- the walls incl. their 10 mm render. **This is the
#:   published *pow. zabudowy* outline**, 154.4224 m2 (T04).
#: * ``structural`` 17.100 x 9.000 -- what the printed dimension chains dimension.
RECORDED_PLAN_M: dict[str, tuple[float, float]] = {
    "roof": (18.280, 10.200),
    "finished": (17.120, 9.020),
    "structural": (17.100, 9.000),
}

#: glTF stores POSITION as float32 (5126), so a value round-trips to ~1e-7 m on a 17 m
#: coordinate. Every mesh-vs-memory comparison below is at 1e-5 m = 0.01 mm, which is four
#: orders of magnitude tighter than anything dimensional here and still far above float32
#: noise. A *scaling* bug moves things by factors of 1000, not by 0.01 mm.
FLOAT32_ATOL_M = 1e-5

#: Node-name grammar, ``level / category / id [/ layer]`` (``generator`` module docstring).
#: Two documented shapes fall outside the simple reading and are handled explicitly rather
#: than being quietly excluded -- see
#: :func:`test_the_node_names_follow_the_documented_grammar`.
NAME_RE = re.compile(r"^[a-z]+(?:/[A-Za-z0-9_]+)+$")


# --------------------------------------------------------------------------------------
# Fixtures -- always build, never read a stale artifact
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def artifacts() -> ExportResult:
    """Rebuild ``build/model.glb`` and ``build/quantities.json`` from ``spec/``.

    Deliberately the real build paths and not ``tmp_path``: ``just build`` writes these,
    T13 sections them and T14 serves them, and a test that validated a private copy would
    leave the file everyone else uses unchecked. The write is atomic (``os.replace``), so a
    concurrent reader sees the old file or the new one and never a half-written GLB.
    """
    return build_artifacts()


@pytest.fixture(scope="module")
def glb_path(artifacts: ExportResult) -> Path:
    """Overrides ``conftest.glb_path``, which skips when the artifact is missing.

    T12 owns the artifact, so for this module "not built" is a failure to produce it, not
    a reason to skip.
    """
    return artifacts.glb_path


@pytest.fixture(scope="module")
def glb_data(glb_path: Path) -> bytes:
    return glb_path.read_bytes()


@pytest.fixture(scope="module")
def gltf(glb_data: bytes) -> Mapping[str, Any]:
    """The GLB's JSON chunk, parsed **without** trimesh.

    Deliberately hand-parsed. The scale-transform and unit assertions have to be made
    against what is in the file; asking the library that wrote it whether it wrote the
    right thing tests nothing.
    """
    return _chunks(glb_data)["JSON"]


@pytest.fixture(scope="module")
def scene(glb_path: Path) -> trimesh.Scene:
    """``build/model.glb`` loaded back off disk. The artifact, not the intent."""
    loaded = trimesh.load(glb_path)
    assert isinstance(loaded, trimesh.Scene), f"expected a Scene, got {type(loaded)}"
    return loaded


@pytest.fixture(scope="module")
def memory_scene(spec) -> trimesh.Scene:
    """The generator's scene before export, for round-trip comparison."""
    return build_scene(spec)


@pytest.fixture(scope="module")
def quantities() -> Mapping[str, Any]:
    with (REPO_ROOT / "build" / "quantities.json").open(encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _chunks(data: bytes) -> dict[str, Any]:
    """Parse a GLB by hand: header, chunk table, JSON chunk.

    Returns ``{"header": ..., "JSON": <dict>, "BIN": <bytes>, "chunks": [...]}``.
    """
    magic, version, declared = struct.unpack("<4sII", data[:12])
    out: dict[str, Any] = {
        "header": {"magic": magic, "version": version, "length": declared},
        "chunks": [],
    }
    offset = 12
    while offset < len(data):
        (length, kind) = struct.unpack("<I4s", data[offset : offset + 8])
        offset += 8
        payload = data[offset : offset + length]
        offset += length
        out["chunks"].append({"type": kind, "length": length, "payload": payload})
        if kind == b"JSON":
            out["JSON"] = json.loads(payload.decode("utf-8"))
        elif kind == b"BIN\x00":
            out["BIN"] = payload
    return out


def _subset_bounds(scene: trimesh.Scene, predicate: Callable[[str], bool]) -> np.ndarray:
    """Min/max corner over the nodes whose name satisfies ``predicate``."""
    selected = [mesh.bounds for name, mesh in scene.geometry.items() if predicate(name)]
    assert selected, "no node matched -- the naming has changed, which is itself a finding"
    stacked = np.array(selected)
    return np.array([stacked[:, 0].min(axis=0), stacked[:, 1].max(axis=0)])


def _extents(bounds: np.ndarray) -> np.ndarray:
    return bounds[1] - bounds[0]


def _pairwise_overlaps(scene: trimesh.Scene) -> list[tuple[float, str, str]]:
    """Every interpenetrating pair of solids and its volume, m3, largest first.

    Exhaustive over all 5 565 pairs, bbox-prefiltered so the boolean is only attempted on
    the ~150 that can possibly touch. Sampling would be the wrong tool here: the question
    is whether the *total* double-count is the recorded 21 m3, and a sample cannot answer
    that.
    """
    items = list(scene.geometry.items())
    found: list[tuple[float, str, str]] = []
    # Solids that merely touch produce an empty intersection whose centre of mass trimesh
    # computes as 0/0. That is the expected answer for "these do not overlap", not a
    # numerical problem, so the warning is silenced rather than left to litter the run.
    with np.errstate(divide="ignore", invalid="ignore"):
        for (left_name, left), (right_name, right) in itertools.combinations(items, 2):
            low_a, high_a = left.bounds
            low_b, high_b = right.bounds
            if np.any(high_a < low_b) or np.any(high_b < low_a):
                continue
            volume = float(left.intersection(right).volume or 0.0)
            if volume > 1e-9:
                found.append((volume, left_name, right_name))
    found.sort(reverse=True)
    return found


# ======================================================================================
# 1. It is a glTF 2.0 binary, and a second reader agrees
# ======================================================================================


def test_just_build_produces_both_artifacts(artifacts: ExportResult) -> None:
    """The acceptance criterion: ``just build`` writes ``model.glb`` and ``quantities.json``."""
    assert artifacts.glb_path.exists() and artifacts.glb_path.name == "model.glb"
    assert artifacts.quantities_path is not None and artifacts.quantities_path.exists()
    assert artifacts.glb_bytes == artifacts.glb_path.stat().st_size
    assert (
        hashlib.sha256(artifacts.glb_path.read_bytes()).hexdigest() == artifacts.glb_sha256
    )


def test_the_file_is_a_glb_2_binary_with_correctly_padded_chunks(glb_data: bytes) -> None:
    """Header and chunk table checked on the bytes, per the GLB container spec.

    Padding is the part worth checking rather than assuming: the JSON chunk must be padded
    to a 4-byte boundary with **spaces** and the BIN chunk with **zeros**, and a reader
    that is stricter than trimesh will reject the file if it is not. ``JSON.parse`` on a
    NUL-padded chunk is exactly the kind of failure that only shows up in the browser.
    """
    parsed = _chunks(glb_data)
    assert parsed["header"]["magic"] == b"glTF"
    assert parsed["header"]["version"] == 2
    assert parsed["header"]["length"] == len(glb_data), "declared length must be the real one"

    kinds = [chunk["type"] for chunk in parsed["chunks"]]
    assert kinds[0] == b"JSON", "the JSON chunk must come first"
    assert kinds[1:] == [b"BIN\x00"], "one BIN chunk, no trailing junk"
    for chunk in parsed["chunks"]:
        assert chunk["length"] % 4 == 0, (chunk["type"], chunk["length"])
    payload = parsed["chunks"][0]["payload"]
    assert payload.rstrip(b" ").endswith(b"}"), "the JSON chunk must be JSON"
    assert set(payload[len(payload.rstrip(b" ")) :]) <= {0x20}, "JSON pads with spaces"
    assert len(parsed["chunks"][1]["payload"]) % 4 == 0, "BIN pads with zeros"


def test_a_second_independent_loader_reads_the_same_file(glb_path: Path) -> None:
    """``pygltflib`` agrees with trimesh on node count, names and asset version.

    trimesh wrote this file, so trimesh reading it back proves less than it looks. A
    different implementation of the same spec is the cheapest available stand-in for the
    browser.
    """
    pygltflib = pytest.importorskip("pygltflib")
    document = pygltflib.GLTF2().load(str(glb_path))
    assert document.asset.version == "2.0"
    assert len(document.nodes) == RECORDED_MESH["nodes"]
    assert len(document.meshes) == RECORDED_MESH["nodes"]

    by_trimesh = set(trimesh.load(glb_path).geometry)
    assert {node.name for node in document.nodes} == by_trimesh


def test_the_gltf_is_structurally_what_a_stock_gltfloader_expects(gltf) -> None:
    """The acceptance criterion "loads without warnings in a stock three.js GLTFLoader".

    HONESTY ABOUT WHAT THIS DOES AND DOES NOT DO
    --------------------------------------------
    This does **not** run three.js. Doing so would put a Node toolchain and a network
    install into a test suite the project deliberately keeps local and offline. What it
    does instead is assert the specific structural properties GLTFLoader and the Khronos
    validator complain about, each checked against the file:

    * ``asset.version`` is ``"2.0"`` and nothing is in ``extensionsRequired`` -- an
      unsupported required extension is the one case where GLTFLoader refuses outright;
    * every primitive is ``mode`` 4 (triangles) with indices of an allowed component type;
    * every ``POSITION`` accessor carries ``min``/``max``, which the spec **requires** and
      which GLTFLoader uses to build bounding volumes -- omitting them is the single most
      common real-world glTF validator error;
    * ``bufferViews`` are 4-byte aligned and the single buffer has no ``uri``, as a GLB's
      buffer 0 must not.

    Two properties are deliberately *not* asserted, because they are correct-by-spec rather
    than defects: there are no ``NORMAL`` attributes (glTF requires the client to compute
    flat normals, which is the right shading for a building made of boxes) and no
    materials (GLTFLoader substitutes its default). Neither produces a warning.
    """
    assert gltf["asset"]["version"] == "2.0"
    assert not gltf.get("extensionsRequired"), gltf.get("extensionsRequired")

    buffers = gltf["buffers"]
    assert len(buffers) == 1 and "uri" not in buffers[0], "GLB buffer 0 is the BIN chunk"
    assert buffers[0]["byteLength"] == sum(view["byteLength"] for view in gltf["bufferViews"])

    for view in gltf["bufferViews"]:
        assert view.get("byteOffset", 0) % 4 == 0, view
        assert view["buffer"] == 0

    for mesh in gltf["meshes"]:
        for primitive in mesh["primitives"]:
            assert primitive.get("mode", 4) == 4, "triangles only"
            assert "POSITION" in primitive["attributes"]
            position = gltf["accessors"][primitive["attributes"]["POSITION"]]
            assert position["type"] == "VEC3" and position["componentType"] == 5126
            assert "min" in position and "max" in position, (
                "POSITION accessors MUST carry min/max; GLTFLoader needs them for the "
                "bounding volume and the Khronos validator errors without them"
            )
            assert len(position["min"]) == 3 and len(position["max"]) == 3
            indices = gltf["accessors"][primitive["indices"]]
            assert indices["type"] == "SCALAR"
            assert indices["componentType"] in (5121, 5123, 5125), indices
            assert indices["count"] % 3 == 0, "an index buffer of triangles"


def test_the_file_is_small_enough_for_the_browser(artifacts: ExportResult) -> None:
    """File size is reported, and flagged over ~20 MB. It is 0.11 MiB, so T14 is fine."""
    assert not artifacts.oversized, str(artifacts)
    assert artifacts.glb_bytes < GLB_SIZE_WARN_BYTES
    assert artifacts.glb_bytes < 2 * 1024 * 1024, (
        f"{artifacts.glb_bytes:,} bytes -- the model is boxes and slabs and has no business "
        f"being this large; suspect a boolean blow-up or duplicated geometry."
    )
    assert "MiB" in str(artifacts) and artifacts.glb_sha256 in str(artifacts)


# ======================================================================================
# 2. Units and scale -- the bug class this whole module exists for
# ======================================================================================


def test_no_node_in_the_file_carries_a_scale_transform(gltf) -> None:
    """**Unit scale is 1.0.** Asserted on the glTF node hierarchy, not on the scene object.

    glTF is defined in metres, the spec is integer millimetres, and a stray scale is how
    that becomes a house 1000x too big that still passes every 2D area check. The file has
    106 flat root nodes and not one of them has ``matrix``, ``scale``, ``rotation`` or
    ``translation``; an absent transform *is* the identity, per the spec. Checking the JSON
    rather than ``scene.graph`` matters because trimesh will happily fold a transform into
    a node matrix on export.
    """
    nodes = gltf["nodes"]
    assert len(nodes) == RECORDED_MESH["nodes"]
    for node in nodes:
        assert set(node) == {"name", "mesh"}, (
            f"{node.get('name')!r} carries {sorted(set(node) - {'name', 'mesh'})}; every "
            f"node must be a bare identity transform so 1 unit is 1 metre everywhere"
        )
    assert len(gltf["scenes"]) == 1
    assert sorted(gltf["scenes"][0]["nodes"]) == list(range(len(nodes))), (
        "all nodes are roots of the single scene; no intermediate node can introduce a "
        "scale that the per-node check above would then miss"
    )


def test_the_scene_declares_metres_and_measures_metres(scene: trimesh.Scene) -> None:
    """Declared unit survives the round-trip, and the magnitude independently confirms it.

    The declaration alone is worthless -- a millimetre scene mislabelled ``m`` declares the
    same thing. The magnitude is the check: 18.3 m across is a house, 18 280 is not.
    """
    assert scene.metadata.get("units") == "m"
    assert scene.metadata.get("ridge_height_m") == pytest.approx(RECORDED_Z["ridge"], abs=1e-6)
    plan = sorted(scene.extents[:2])
    assert 5.0 < plan[0] and plan[1] < 50.0, scene.extents


def test_assert_metres_rejects_a_millimetre_scene(memory_scene: trimesh.Scene) -> None:
    """The guard has teeth: the same scene at 1000x is refused, with a diagnosis.

    Without this the "no conversion happens here" claim is untested prose. The scene still
    *declares* metres after scaling, which is precisely the case the declaration cannot
    catch and the magnitude band can.
    """
    millimetres = memory_scene.copy()
    millimetres.apply_scale(1000.0)
    assert millimetres.metadata.get("units") == "m"
    with pytest.raises(ExportError, match="unit-scale error"):
        assert_metres(millimetres)

    kilometres = memory_scene.copy()
    kilometres.apply_scale(0.001)
    with pytest.raises(ExportError, match="unit-scale error"):
        assert_metres(kilometres)

    assert_metres(memory_scene)  # and the honest one still passes


def test_assert_metres_rejects_a_scene_that_lies_about_its_units(
    memory_scene: trimesh.Scene,
) -> None:
    """A generator that quietly switches to millimetres is caught by the declaration."""
    mislabelled = memory_scene.copy()
    mislabelled.metadata["units"] = "mm"
    with pytest.raises(ExportError, match="glTF is defined in metres"):
        assert_metres(mislabelled)


def test_the_round_trip_through_gltf_preserves_the_bounding_box(
    scene: trimesh.Scene, memory_scene: trimesh.Scene
) -> None:
    """export -> load -> bbox unchanged. The check that catches silent exporter scaling.

    A scaling bug is a multiplicative error, so it survives no tolerance at all: the
    comparison is at 0.01 mm and the observed disagreement is 1.4e-7 m, which is float32
    quantisation of the POSITION accessors and nothing else.
    """
    assert np.allclose(scene.bounds, memory_scene.bounds, atol=FLOAT32_ATOL_M)
    assert np.allclose(scene.extents, memory_scene.extents, atol=FLOAT32_ATOL_M)
    ratio = np.array(scene.extents) / np.array(memory_scene.extents)
    assert np.allclose(ratio, 1.0, atol=1e-6), (
        f"the exported scene is {ratio} times the in-memory one; any departure from 1.0 is "
        f"an exporter scale transform, not rounding"
    )
    assert set(scene.geometry) == set(memory_scene.geometry)


def test_the_round_trip_preserves_every_node_bounding_box_not_just_the_scene_one(
    scene: trimesh.Scene, memory_scene: trimesh.Scene
) -> None:
    """A scene bbox can survive while individual nodes are displaced; this rules that out."""
    for name, mesh in scene.geometry.items():
        assert np.allclose(mesh.bounds, memory_scene.geometry[name].bounds, atol=FLOAT32_ATOL_M), (
            name
        )


# ======================================================================================
# 3. Dimensions -- the right rectangle, not the convenient one
# ======================================================================================


def test_exported_mesh(scene: trimesh.Scene, spec) -> None:
    """The brief's own check, with ``EXPECTED_FOOTPRINT_M`` correctly identified.

    The roof is in the scene, so the scene's plan bbox is the **roof** outline: the
    structural 17.100 x 9.000 grown by the 590 mm verge and 600 mm eaves overhangs T17
    measured. It is emphatically not the 25.1 x 17.0 m minimum plot size, which is not a
    building dimension at all, and it is not the 17.120 x 9.020 published footprint either
    -- that one is asserted separately, on the wall nodes.

    The expected figure is *derived from the spec* rather than typed in, so that a change
    to a transcribed overhang moves the assertion with it instead of turning it red.
    """
    roof = spec["roof"]
    span_axis_m = 9.000 + 2 * roof["eaves_overhang"] / 1000.0
    ridge_axis_m = 17.100 + 2 * roof["verge_overhang"] / 1000.0
    expected = np.array([ridge_axis_m, span_axis_m])
    assert np.allclose(expected, RECORDED_PLAN_M["roof"], atol=1e-9), (
        "the recorded roof plan extent and the one the spec implies must agree"
    )

    bbox = scene.bounding_box.extents
    assert np.allclose(bbox[:2], expected, atol=0.01), (
        f"scene plan bbox {bbox[:2]} against the expected roof outline {expected}"
    )
    assert np.allclose(bbox, scene.extents, atol=1e-12)


def test_the_scene_bbox_is_the_roof_and_the_stacks_and_that_decomposes_exactly(
    scene: trimesh.Scene,
) -> None:
    """Every one of the six scene-bbox faces is attributed to a named node group.

    This is the honest form of the brief's bbox assertion: rather than asserting a number
    that happens to be near 7.09, name what actually bounds the model in each direction and
    check that nothing unaccounted-for is sticking out.
    """
    roof = _subset_bounds(scene, lambda name: name.startswith("roof/"))
    stacks = _subset_bounds(scene, lambda name: "/chimneys/" in name)
    plinth = _subset_bounds(scene, lambda name: name == "ground/slabs/plinth")
    whole = np.array(scene.bounds)

    # In plan the roof alone bounds the scene: the overhangs reach past every wall.
    assert np.allclose(roof[0][:2], whole[0][:2], atol=FLOAT32_ATOL_M)
    assert np.allclose(roof[1][:2], whole[1][:2], atol=FLOAT32_ATOL_M)
    # Vertically the plinth is the floor and the chimney tops are the ceiling.
    assert plinth[0][2] == pytest.approx(whole[0][2], abs=FLOAT32_ATOL_M)
    assert stacks[1][2] == pytest.approx(whole[1][2], abs=FLOAT32_ATOL_M)
    assert stacks[1][2] > roof[1][2], "the stacks must clear the ridge"

    assert whole[0][2] == pytest.approx(RECORDED_Z["terrain"], abs=FLOAT32_ATOL_M)
    assert whole[1][2] == pytest.approx(RECORDED_Z["chimney_top"], abs=FLOAT32_ATOL_M)
    assert _extents(whole)[2] == pytest.approx(
        RECORDED_Z["chimney_top"] - RECORDED_Z["terrain"], abs=FLOAT32_ATOL_M
    )
    assert _extents(whole)[2] == pytest.approx(7.680934, abs=1e-5), (
        "7.681 m, not 7.09 -- plinth underside to chimney top. Both are real geometry."
    )


def test_building_height_is_measured_to_the_ridge_not_the_stacks(scene: trimesh.Scene) -> None:
    """Published 7.09 m, terrain to ridge. Off the mesh it reads 7.0809 m, -9.07 mm.

    Asserted on the **roof** nodes' bbox top rather than the scene's, exactly as
    ``tasks/T12.md`` instructs when something else extends the bbox -- here the chimneys,
    which stand 600 mm proud of the ridge and would otherwise turn a real check into a
    +0.6 m failure.

    Tolerance is +-30 mm, T09's, not the +-10 mm in ``TESTS.md``: the ridge is derived
    through ``roof_buildup_vertical``, a *measured* 280 mm carrying +-30 mm, so +-30 mm is
    the uncertainty the quantity actually has. At +-10 mm this would pass with 0.93 mm to
    spare, which would be a claim of precision the inputs do not support. The exact value
    is pinned below at 1e-5 m, so nothing is given away by widening the published-figure
    comparison.
    """
    roof = _subset_bounds(scene, lambda name: name.startswith("roof/"))
    terrain = float(scene.bounds[0][2])
    height = float(roof[1][2]) - terrain

    assert height == pytest.approx(RECORDED_BUILDING_HEIGHT_M, abs=FLOAT32_ATOL_M)
    assert height == pytest.approx(7.09, abs=0.030), f"terrain {terrain} to ridge {roof[1][2]}"
    assert 7.09 - height == pytest.approx(0.00907, abs=1e-4), (
        "the derived ridge sits 9.07 mm below the printed one, as T17 established; if that "
        "margin has moved, the roof chain has moved"
    )
    assert float(roof[1][2]) == pytest.approx(RECORDED_Z["ridge"], abs=FLOAT32_ATOL_M)
    assert float(roof[0][2]) == pytest.approx(
        RECORDED_Z["eave_fascia_underside"], abs=FLOAT32_ATOL_M
    ), "the roof's lowest point is the fascia underside, printed at +2.880"


def test_the_model_origin_is_finished_ground_floor_level(scene: trimesh.Scene) -> None:
    """The brief asks this to be *confirmed*, because the 7.09 m reading depends on it.

    Z = 0 is the finished ground floor: the ground storey's walls start there and the
    plinth hangs below it to the terrain. If the origin were the terrain instead, every
    elevation in the file would be 0.32 m out and the building height would still read
    7.09, because both ends would move together.
    """
    walls = _subset_bounds(scene, lambda name: name.startswith("ground/walls/"))
    plinth = _subset_bounds(scene, lambda name: name == "ground/slabs/plinth")
    assert walls[0][2] == pytest.approx(0.0, abs=FLOAT32_ATOL_M)
    assert plinth[1][2] == pytest.approx(0.0, abs=FLOAT32_ATOL_M)
    assert plinth[0][2] == pytest.approx(RECORDED_Z["terrain"], abs=FLOAT32_ATOL_M)
    assert plinth[1][2] - plinth[0][2] == pytest.approx(0.320, abs=FLOAT32_ATOL_M)


def test_the_walls_reproduce_the_published_finished_footprint(scene: trimesh.Scene) -> None:
    """17.120 x 9.020 m = 154.4224 m2 -- the published *pow. zabudowy* outline, on the mesh.

    This is the dimensional check the scene bbox cannot make, and it is the one that would
    catch a wall-thickness or transcription error. The walls including their 10 mm render
    layer bound -0.010 .. 17.110 and -0.010 .. 9.010, which is T04's finished outline
    exactly; strip the render and it is the structural 17.100 x 9.000 the printed chains
    dimension.

    Both rectangles are asserted, because the 20 mm between them is the entire content of
    T04's footprint resolution and confusing them is how 154.42 becomes 153.90.
    """
    walls = _subset_bounds(scene, lambda name: "/walls/" in name)
    finished = _extents(walls)[:2]
    assert np.allclose(finished, RECORDED_PLAN_M["finished"], atol=FLOAT32_ATOL_M), finished
    assert finished[0] * finished[1] == pytest.approx(154.4224, abs=1e-3)

    structural = _subset_bounds(scene, lambda name: name.endswith("/porotherm_25"))
    assert np.allclose(_extents(structural)[:2], RECORDED_PLAN_M["structural"], atol=1e-3)

    render = (finished - _extents(structural)[:2]) / 2.0
    assert np.allclose(render, 0.010, atol=1e-3), (
        f"the render reads {render * 1000} mm per face; T04 solved it at 10 mm and 15/20 mm "
        f"both overshoot the published 154.42 m2"
    )


def test_the_overhangs_are_the_difference_between_the_two_plan_rectangles(
    scene: trimesh.Scene, spec
) -> None:
    """Roof bbox minus structural outline = the transcribed overhangs, both axes.

    Recorded honestly: the overhangs are struck from the **structural** face, so measured
    from the *finished* face they are 10 mm shorter -- 580 mm verge and 590 mm eaves.
    Which face T17 traced them from is not established, and 10 mm is well inside the
    tracing uncertainty, so this is a note rather than a defect.
    """
    roof = _subset_bounds(scene, lambda name: name.startswith("roof/"))
    structural = _subset_bounds(scene, lambda name: name.endswith("/porotherm_25"))
    verge = (roof[1][0] - roof[0][0] - _extents(structural)[0]) / 2.0
    eaves = (roof[1][1] - roof[0][1] - _extents(structural)[1]) / 2.0
    assert verge * 1000.0 == pytest.approx(spec["roof"]["verge_overhang"], abs=0.1)
    assert eaves * 1000.0 == pytest.approx(spec["roof"]["eaves_overhang"], abs=0.1)
    assert verge == pytest.approx(0.590, abs=1e-4)
    assert eaves == pytest.approx(0.600, abs=1e-4)


def test_the_scene_bbox_is_not_the_minimum_plot_size(scene: trimesh.Scene) -> None:
    """25.1 x 17.0 m is the plot, not the building. Recorded so it cannot be mistaken again.

    README flags this as a trap and ``tasks/T12.md`` repeats it. The model is nowhere near
    those numbers, and asserting the distance keeps the two apart in the record.
    """
    plan = sorted(scene.extents[:2], reverse=True)
    assert plan[0] < 20.0 and plan[1] < 12.0
    assert abs(plan[0] - 25.1) > 5.0 and abs(plan[1] - 17.0) > 5.0


# ======================================================================================
# 4. Mesh integrity
# ======================================================================================


def test_every_component_is_watertight(scene: trimesh.Scene) -> None:
    """All 106, with no exceptions. Each solid is a closed manifold with positive volume.

    Stated per component because the *scene* is not a single solid and could not be: the
    members interpenetrate, so a union would be needed before the question even makes
    sense. See the overlap test for the measured consequence.
    """
    broken = {
        name: {
            "watertight": mesh.is_watertight,
            "winding": mesh.is_winding_consistent,
            "volume": float(mesh.volume),
            "euler": int(mesh.euler_number),
        }
        for name, mesh in scene.geometry.items()
        if not (mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0.0)
    }
    assert not broken, f"{len(broken)} of {len(scene.geometry)} components are not solid: {broken}"
    assert len(scene.geometry) == RECORDED_MESH["nodes"]


def test_no_degenerate_faces(scene: trimesh.Scene) -> None:
    """No zero-area triangles anywhere.

    Also checked: no duplicate vertex indices within a face, which is the way a triangle
    becomes degenerate without its area going exactly to zero in floating point. The
    smallest real face in the model is 1.0e-5 m2 -- a 10 x 1 mm sliver on an opening reveal
    -- so the threshold is set six orders of magnitude below that and still nowhere near it.
    """
    smallest = math.inf
    for name, mesh in scene.geometry.items():
        areas = mesh.area_faces
        assert len(areas) == len(mesh.faces)
        assert (areas > 1e-11).all(), (
            f"{name}: {int((areas <= 1e-11).sum())} zero-area triangles"
        )
        a, b, c = mesh.faces.T
        assert ((a != b) & (b != c) & (a != c)).all(), f"{name}: a face repeats a vertex index"
        smallest = min(smallest, float(areas.min()))
    assert smallest == pytest.approx(1.0e-5, rel=0.5), (
        f"smallest face {smallest:.3e} m2 -- recorded at 1.0e-5; a large move here means "
        f"slivers have appeared or the reveals have changed"
    )


def test_no_nan_or_infinite_vertices(scene: trimesh.Scene) -> None:
    """Every coordinate finite, and every one of them inside the scene bbox.

    A NaN vertex usually arrives via a failed boolean and is invisible until the viewer
    renders nothing at all. Finiteness alone is a weak test -- 1e30 is finite -- so the
    coordinates are also required to lie in the bounding box the rest of this module
    asserts against.
    """
    low, high = np.array(scene.bounds)
    for name, mesh in scene.geometry.items():
        vertices = mesh.vertices
        assert np.isfinite(vertices).all(), f"{name}: non-finite vertices"
        assert (vertices >= low - 1e-6).all() and (vertices <= high + 1e-6).all(), name
        assert len(vertices) >= 4, f"{name}: fewer vertices than a tetrahedron"


def test_the_vertex_count_is_pinned_and_inside_a_sane_band(
    scene: trimesh.Scene, artifacts: ExportResult
) -> None:
    """1 408 vertices / 2 444 faces / 106 nodes, pinned, and banded against a blow-up.

    The brief asks for a band because "a 10x jump between builds signals a boolean
    blowup". A band alone would not notice a 30% drift, so the exact counts are recorded
    too; if the generator legitimately changes, updating this record is the edit that
    forces the change to be looked at.
    """
    measured = {
        "nodes": len(scene.geometry),
        "vertices": int(sum(len(mesh.vertices) for mesh in scene.geometry.values())),
        "faces": int(sum(len(mesh.faces) for mesh in scene.geometry.values())),
    }
    assert measured == RECORDED_MESH, f"mesh counts moved: {measured} vs {RECORDED_MESH}"
    assert VERTEX_BAND[0] < measured["vertices"] < VERTEX_BAND[1]
    assert measured["faces"] < 10 * measured["vertices"]
    assert (artifacts.node_count, artifacts.vertex_count, artifacts.face_count) == (
        measured["nodes"],
        measured["vertices"],
        measured["faces"],
    ), "ExportResult must report what is actually in the file"


def test_the_solids_interpenetrate_and_the_sum_is_not_a_material_volume(
    scene: trimesh.Scene,
) -> None:
    """The honest answer to "is the mesh watertight": per component yes, as a solid no.

    Measured exhaustively: **21.003 m3 of interpenetration over 149 pairs**, against a
    267.286 m3 sum of member volumes -- 7.9% double-counted. Two distinct causes, and the
    second is the one that is under-recorded elsewhere:

    * chimney stacks are traced once per storey plan and pass through the walls they abut
      (the largest single pair, 1.79 m3);
    * **the ground storey's exterior walls run up into the attic slab band** -- and those
      account for the second, third, fifth, sixth and seventh largest overlaps.

    ``tests/test_invariants.RECORDED_MESH_OVERLAPS`` records four pairs, all
    chimney-related, totalling 3.66 m3. That is a correct and useful record of the chimney
    problem, but it is **not** the four largest overlaps and it covers 17% of the total:
    ``ground/walls/G_W7/porotherm_25`` x ``attic/slabs/floor`` at 1.4535 m3 is the
    second-largest overlap in the scene and appears in no record. README "Outstanding"
    item 4 does mention the wall/slab class in prose, so this is an incomplete record
    rather than an unknown one -- and it is now measured in total here.

    Nothing downstream is wrong because of it: cubature is a closed-form integral over the
    2D footprint and touches no mesh volume, which is exactly why that is the right method.
    """
    overlaps = _pairwise_overlaps(scene)
    total = sum(volume for volume, _, _ in overlaps)
    material = sum(float(mesh.volume) for mesh in scene.geometry.values())

    assert material == pytest.approx(RECORDED_SOLID_VOLUME_SUM_M3, abs=0.01)
    assert total == pytest.approx(RECORDED_OVERLAP_M3, abs=0.01)
    assert len(overlaps) == RECORDED_OVERLAP_PAIRS
    assert 0.07 < total / material < 0.09, f"{total / material:.3%} double-counted"

    measured = {(left, right): volume for volume, left, right in overlaps}
    for pair, recorded in RECORDED_TOP_OVERLAPS.items():
        found = measured.get(pair) or measured.get((pair[1], pair[0]))
        assert found is not None, f"{pair} no longer overlaps -- has the generator been fixed?"
        assert found == pytest.approx(recorded, rel=0.02), pair
    assert sorted(measured.values(), reverse=True)[:5] == pytest.approx(
        sorted(RECORDED_TOP_OVERLAPS.values(), reverse=True), rel=0.02
    ), "the five largest overlaps are the recorded ones"


def test_the_invariants_module_records_only_the_chimney_subset_of_the_overlaps(
    scene: trimesh.Scene,
) -> None:
    """Cross-checks T09's record against a full measurement, and pins the gap.

    T09's four entries are individually correct -- each is re-measured here and agrees --
    so this is not a contradiction between the two modules. What it pins is the *coverage*:
    those four are 3.66 m3 of the 21.00 m3 that is actually there. Recording it means the
    next person to read "the mesh overlaps are recorded" learns which ones.
    """
    invariants = pytest.importorskip("test_invariants")
    recorded = invariants.RECORDED_MESH_OVERLAPS
    for (left, right), value in recorded.items():
        shared = scene.geometry[left].intersection(scene.geometry[right])
        assert float(shared.volume) == pytest.approx(value, rel=0.02), (left, right)

    assert all("chimneys" in left or "chimneys" in right for left, right in recorded), (
        "T09's record is the chimney subset"
    )
    assert sum(recorded.values()) == pytest.approx(3.664, abs=0.01)
    assert sum(recorded.values()) < RECORDED_OVERLAP_M3 / 5.0, (
        "T09 records 3.66 m3 of a measured 21.00 m3; the wall-into-attic-slab overlaps, "
        "which are larger in aggregate and include the second-biggest single pair, are "
        "described in README 'Outstanding' item 4 but are not in any recorded set. The "
        "totals are pinned in this module instead."
    )


# ======================================================================================
# 5. Node names -- T14 and T13 both depend on them
# ======================================================================================


def test_the_node_names_survived_the_export(
    scene: trimesh.Scene, memory_scene: trimesh.Scene, gltf
) -> None:
    """The names in the file are exactly the names the generator built. All 106.

    Checked three ways because each could fail alone: the raw glTF ``nodes[].name``, what
    trimesh reads back, and what the generator produced. glTF node names are optional and
    an exporter dropping or uniquifying them would break T14's layer toggles silently.
    """
    from_file = [node["name"] for node in gltf["nodes"]]
    assert len(from_file) == len(set(from_file)) == RECORDED_MESH["nodes"], "names are unique"
    assert set(from_file) == set(scene.geometry) == set(memory_scene.geometry)
    assert all(name and not name.strip() != name for name in from_file)


def test_the_node_names_follow_the_documented_grammar(scene: trimesh.Scene) -> None:
    """``level / category / id [/ layer]``, with the two documented departures named.

    T14 toggles **by prefix**, which is what the scheme actually guarantees and what this
    asserts. A consumer that instead splits on ``/`` and reads field 3 as a material layer
    would be wrong for openings, so the exact shapes are enumerated here rather than left
    to be discovered in the viewer:

    * ``ground/walls/G_W1/porotherm_25`` -- level / category / id / layer, the common case;
    * ``attic/openings/windows/A_O2``    -- level / category / **sub**category / id. Four
      fields, but the fourth is an id, not a layer;
    * ``roof/slab``                      -- two fields only. The roof is a level with a
      single unnamed member, so there is no id to give. Documented in the generator's own
      docstring, so it is intended rather than a slip.

    Neither departure breaks prefix toggling. Both would break ``name.split("/")[3]``.
    """
    names = sorted(scene.geometry)
    assert all(NAME_RE.match(name) for name in names), [n for n in names if not NAME_RE.match(n)]

    levels = {name.split("/")[0] for name in names}
    assert levels == {"ground", "attic", "roof"}

    shapes: dict[int, set[str]] = {}
    for name in names:
        shapes.setdefault(len(name.split("/")), set()).add(name)
    assert set(shapes) == {2, 3, 4}
    assert shapes[2] == {"roof/slab"}, "roof/slab is the only fieldless-id node"
    assert len(shapes[3]) == 19 and len(shapes[4]) == 86

    # Every four-field name is either a material layer or an opening id, never ambiguous.
    for name in shapes[4]:
        level, category, third, fourth = name.split("/")
        if category == "openings":
            assert third in ("windows", "doors", "other"), name
            assert re.fullmatch(r"[GA]_O\d+", fourth), name
        else:
            assert category in ("walls", "chimneys"), name
            assert re.fullmatch(r"[GA]_W\d+", third), name

    # Prefix toggling, which is the contract T14 relies on.
    for prefix, count in (("ground/", 75), ("attic/", 27), ("roof/", 4)):
        assert sum(1 for name in names if name.startswith(prefix)) == count
    assert sum(1 for name in names if "/walls/" in name) == 58
    assert sum(1 for name in names if name.startswith("ground/walls/G_W1/")) == 3


def test_every_spec_entity_that_should_be_in_the_scene_is(scene: trimesh.Scene, spec) -> None:
    """All 38 walls and all 24 openings appear; 4 of 18 rooms do not, for stated reasons.

    The four absent room plates are not a defect and each is explained by the generator:

    * ``G_R6``, ``G_R7``, ``G_R14`` share one open-plan face with ``G_R2`` and are emitted
      once, under ``G_R2``, with all four names in the node's metadata. Four coincident
      plates would z-fight and would imply walls that do not exist.
    * ``A_R4`` (Schody) coincides with the ``A_SO2`` stairwell opening, so subtracting the
      floor voids leaves it with no floor. It is a published floor area but not a floor at
      attic level, and a plate across it would close the stairwell in the viewer.

    Asserting the absentee list *exactly* is the point: it stops a genuinely dropped room
    from hiding behind an explanation written for a different one.
    """
    names = set(scene.geometry)

    def present(entity_id: str) -> bool:
        return any(f"/{entity_id}/" in name or name.endswith(f"/{entity_id}") for name in names)

    missing_walls = [wall["id"] for wall in spec["walls"] if not present(wall["id"])]
    missing_openings = [op["id"] for op in spec["openings"] if not present(op["id"])]
    missing_rooms = [room["id"] for room in spec["rooms"] if not present(room["id"])]

    assert len(spec["walls"]) == 38 and not missing_walls
    assert len(spec["openings"]) == 24 and not missing_openings
    assert len(spec["rooms"]) == 18
    assert missing_rooms == ["G_R6", "G_R7", "G_R14", "A_R4"], missing_rooms

    shared = scene.geometry["ground/rooms/G_R2"].metadata
    assert sorted(shared["room_ids"]) == ["G_R14", "G_R2", "G_R6", "G_R7"]
    assert shared["shared_face"] is True


def test_the_room_nodes_carry_their_identity_into_the_file(scene: trimesh.Scene) -> None:
    """Room metadata survives export, so T14 can label a room without re-reading the spec."""
    plate = scene.geometry["ground/rooms/G_R11"].metadata
    assert plate["room_names"] == ["Kotłownia"]
    assert plate["published_area_m2"] == pytest.approx(7.31)
    assert plate["level"] == "ground"


# ======================================================================================
# 6. Determinism -- T13's golden diff and T16's archive both need it
# ======================================================================================


def _build_in_subprocess(destination: Path, hash_seed: str) -> str:
    """Export into ``destination`` in a fresh interpreter and return the GLB's SHA-256."""
    script = (
        "import hashlib,sys;"
        "from kotewki.export import build_artifacts;"
        "r=build_artifacts(glb_path=sys.argv[1], quantities_path=sys.argv[2]);"
        "print(r.glb_sha256+' '+hashlib.sha256(open(sys.argv[2],'rb').read()).hexdigest())"
    )
    environment = dict(os.environ, PYTHONHASHSEED=hash_seed)
    completed = subprocess.run(
        [sys.executable, "-c", script, str(destination / "m.glb"), str(destination / "q.json")],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
        env=environment,
    )
    return completed.stdout.strip()


def test_the_artifacts_are_byte_identical_across_processes_and_hash_seeds(
    tmp_path: Path, artifacts: ExportResult
) -> None:
    """Three separate interpreters, three different ``PYTHONHASHSEED`` values, one digest.

    An in-process "build it twice" check cannot see this class of bug: a ``set`` built the
    same way twice in one process iterates the same way twice. Only a fresh interpreter
    under a different hash seed reorders it, and only then does a set-iteration dependence
    in the generator or the exporter turn into a different file.

    T13's golden-image diff and T16's per-build archive are both worthless without this --
    a non-deterministic mesh makes the overlay flaky, and a flaky test gets disabled, which
    would remove the project's only guard against building a perfect mirror image of the
    right house.

    Both artifacts are covered. ``quantities.json`` is written into the same path in each
    run on purpose: it records the artifact's own path, so comparing runs that wrote to
    different filenames would compare that difference and nothing else.
    """
    digests = {seed: _build_in_subprocess(tmp_path, seed) for seed in ("0", "1", "524287")}
    assert len(set(digests.values())) == 1, f"non-deterministic export: {digests}"

    glb_digest = next(iter(digests.values())).split()[0]
    assert glb_digest == artifacts.glb_sha256, (
        "a subprocess build disagrees with the in-process one, so something in this "
        "interpreter's state is reaching the artifact"
    )


def test_nothing_in_the_file_looks_like_a_timestamp_or_a_path(glb_data: bytes, gltf) -> None:
    """A build date or an absolute path in the GLB would defeat the byte-comparison above.

    trimesh writes ``asset.generator`` as a fixed URL and nothing else; there is no
    ``copyright``, no ``extras`` on ``asset``, and no home directory anywhere in the bytes.
    """
    assert set(gltf["asset"]) == {"version", "generator"}
    assert "http" in gltf["asset"]["generator"]
    home = str(Path.home()).encode()
    assert home not in glb_data
    assert not re.search(rb"20\d\d-\d\d-\d\d", glb_data), "a date literal in the artifact"


# ======================================================================================
# 7. quantities.json -- the take-off that ships beside the mesh
# ======================================================================================


def test_quantities_json_is_wellformed_and_versioned(quantities, artifacts: ExportResult) -> None:
    """T14 reads this and T16 archives it, so its shape is a contract."""
    assert quantities["schema"] == QUANTITIES_SCHEMA
    assert quantities["units"] == {"length": "m", "area": "m2", "volume": "m3", "angle": "deg"}
    assert quantities["artifact"]["sha256"] == artifacts.glb_sha256
    assert quantities["artifact"]["bytes"] == artifacts.glb_bytes
    assert quantities["artifact"]["path"] == "build/model.glb"
    assert quantities["mesh"]["nodes"] == RECORDED_MESH["nodes"]
    assert quantities["mesh"]["vertices"] == RECORDED_MESH["vertices"]

    def finite(value: Any) -> None:
        if isinstance(value, float):
            assert math.isfinite(value)
        elif isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)

    finite(quantities)


def test_the_quantities_bounding_box_is_the_one_in_the_file(quantities, scene) -> None:
    assert np.allclose(quantities["mesh"]["bbox_min_m"], scene.bounds[0], atol=FLOAT32_ATOL_M)
    assert np.allclose(quantities["mesh"]["bbox_max_m"], scene.bounds[1], atol=FLOAT32_ATOL_M)
    assert quantities["mesh"]["solid_volume_sum_m3"] == pytest.approx(
        RECORDED_SOLID_VOLUME_SUM_M3, abs=0.01
    )


# --- the usable-area definition, and the disagreement it resolves ----------------------


def test_the_ground_stair_run_is_read_off_the_published_table_two_ways(published) -> None:
    """2.63 m2, from two independent rows, required to agree before it is used.

    ``Salon floor 33.20 - usable 30.57`` and ``ground level floor 118.81 - usable 116.18``
    are separately published figures measuring the same flight. If a transcription slip
    ever makes them disagree, :func:`kotewki.export.ground_stair_run_m2` raises rather than
    silently averaging.
    """
    assert ground_stair_run_m2(published) == pytest.approx(2.63, abs=0.005)

    salon = next(room for room in published["rooms"]["ground"] if room["id"] == 6)
    level = published["levels"]["ground"]
    assert salon["floor_area_m2"] - salon["area_m2"] == pytest.approx(
        level["floor_area_m2"] - level["usable_area_m2"], abs=0.005
    )

    poisoned = json.loads(json.dumps(published))
    poisoned["levels"]["ground"]["floor_area_m2"] = 120.0
    with pytest.raises(ExportError, match="disagree"):
        ground_stair_run_m2(poisoned)


def test_the_computed_faces_are_floor_areas_which_is_what_fixes_the_definition(
    quantities,
) -> None:
    """WHY the deduction is right, argued from the geometry rather than from the answer.

    The model has no stair-flight entity, so the open-plan Salon face contains the ground
    flight. That is not an assumption -- it is measurable, because Archon publishes both
    conventions per storey and the mesh only agrees with one of them:

        computed ground faces  118.36995 m2
        published FLOOR area   118.810 m2   -0.37%   <- agrees
        published USABLE area  116.180 m2   +1.88%   <- does not

    -0.37% is also the sign and size of the residual on every other level and total here
    (-0.10% to -0.36%), i.e. the ordinary finish-allowance signature. +1.88% is not. So the
    faces are floor areas and the flight has to come out before comparing to *uzytkowa*,
    which Archon defines as *bez schodow*.

    The same holds one storey up without any deduction at all: the attic's four faces sum
    to 103.726 against a published floor area of 103.830, -0.10%. (T19: this was 103.481 /
    -0.34% while Schody was measured at the finish face; it is the one face on this storey
    that takes no finish allowance, so exempting it moved the storey a quarter of a square
    metre closer to the published figure rather than further from it.)
    """
    ground = quantities["areas_m2"]["by_level"]["ground"]
    assert ground["floor_area"]["computed"] == pytest.approx(118.36995, abs=0.01)
    assert ground["floor_area"]["residual_pct"] == pytest.approx(-0.37, abs=0.05)
    assert abs(118.36995 / 116.18 - 1.0) == pytest.approx(0.0188, abs=0.001)

    attic = quantities["areas_m2"]["by_level"]["attic"]
    assert attic["floor_area"]["computed"] == pytest.approx(103.726, abs=0.01)
    assert attic["floor_area"]["residual_pct"] == pytest.approx(-0.100, abs=0.05)
    assert attic["stair_run_deducted"] == 0.0, "deducted on the ground floor and nowhere else"


def test_the_undeducted_totals_are_recorded_and_would_fail_the_invariant(quantities) -> None:
    """What the faces sum to before the deduction, and that it is not good enough.

    165.646 m2 is +1.27% on the published 163.57 and **fails** T09's +-1% band; 129.191 is
    +1.71% on the published net. Both are published in ``quantities.json`` so the deduction
    is visible as a number rather than folded into a total, and both are asserted here so
    that removing the deduction is a red test rather than a 2 m2 drift.

    Only the net basis moved in T19: Schody is in ``net`` and not in ``usable``, so its
    +0.245 m2 (the finish allowance it no longer takes) lands here and nowhere else.
    """
    areas = quantities["areas_m2"]
    assert areas["usable_floor_basis"] == pytest.approx(165.730579, abs=1e-3)
    assert areas["net_floor_basis"] == pytest.approx(129.276136, abs=1e-3)
    assert abs(areas["usable_floor_basis"] / 163.57 - 1.0) > 0.01, "would fail the +-1% band"
    usable, net = areas["usable"]["computed"], areas["net"]["computed"]
    assert areas["usable_floor_basis"] - usable == pytest.approx(2.63, abs=1e-3)
    assert areas["net_floor_basis"] - net == pytest.approx(2.63, abs=1e-3)
    assert areas["deductions"]["ground_stair_run"] == pytest.approx(2.63, abs=1e-6)
    assert areas["deductions"]["applies_to"] == ["usable", "net"]


def test_the_take_off_agrees_with_the_invariants_module(quantities) -> None:
    """``quantities.json`` and ``tests/test_invariants.py`` must not compute two answers.

    They disagreed by exactly one ground stair run until this module resolved it: T09
    deducted 2.63 and the exporter did not, so ``quantities.json`` published 165.646 m2
    (+1.27%, failing) beside T09's 163.016 m2 (-0.34%, passing). T09 was right about the
    definition and the exporter has adopted it, deriving the figure from the published
    table rather than copying T09's literal so the two are independently sourced.

    Every shared quantity is compared here, not just the one that was wrong, because the
    next divergence will not be this one.
    """
    invariants = pytest.importorskip("test_invariants")
    measured = invariants.MEASURED
    areas = quantities["areas_m2"]

    assert areas["usable"]["computed"] == pytest.approx(measured["usable_area_m2"], abs=1e-3)
    assert areas["footprint"]["computed"] == pytest.approx(measured["footprint_m2"], abs=1e-4)
    assert quantities["volumes_m3"]["cubature"]["computed"] == pytest.approx(
        measured["cubature_m3"], abs=1e-3
    )
    assert quantities["roof"]["area_m2"]["computed"] == pytest.approx(
        measured["roof_area_m2"], abs=1e-3
    )
    assert quantities["heights"]["building_m"]["computed"] == pytest.approx(
        measured["building_height_m"], abs=1e-6
    )
    assert areas["deductions"]["ground_stair_run"] == pytest.approx(
        invariants.GROUND_STAIR_RUN_M2, abs=1e-6
    ), "the exporter and T09 must deduct the same flight"


def test_every_published_global_figure_the_take_off_compares_is_inside_its_band(
    quantities,
) -> None:
    """The take-off's own residual table, asserted rather than merely printed.

    Roof area is the one wide band (+-6%) and T09 argues it at length: the overhangs are
    measured on two independent images and the publisher's *powierzchnia dachu* convention
    is not known, so the residual is reported as a finding rather than tuned away. Kotlownia
    at -1.74% is likewise a recorded publisher-vs-plan disagreement, not a geometry bug, and
    is checked against its recorded value rather than a tolerance.
    """
    bands: dict[str, float] = {
        "footprint": 0.01,
        "usable": 0.01,
        "net": 0.01,
        "floor": 0.01,
        "attic": 0.01,
    }
    failures = {}
    for key, band in bands.items():
        residual = quantities["areas_m2"][key]["residual_pct"] / 100.0
        if abs(residual) > band:
            failures[key] = residual
    assert not failures, f"outside band: {failures}"

    assert quantities["volumes_m3"]["cubature"]["residual_pct"] == pytest.approx(0.042, abs=0.01)
    assert quantities["roof"]["area_m2"]["residual_pct"] == pytest.approx(4.99, abs=0.05)
    assert quantities["areas_m2"]["boiler"]["residual_pct"] == pytest.approx(-1.74, abs=0.05)


def test_the_notes_state_the_traps_rather_than_leaving_them_to_be_rediscovered(
    quantities,
) -> None:
    """The four things a reader of this file must not conclude, stated in the file itself."""
    notes = " ".join(quantities["notes"])
    assert "FINISHED face" in notes
    assert "ground stair run" in notes and "160.94" in notes
    assert "solid_volume_sum is NOT a material volume" in notes
    assert "bounding box is NOT the building" in notes


def test_quantities_json_is_deterministic(tmp_path: Path) -> None:
    """Written twice into the same path, byte-identical. No timestamps, no dict-order drift."""
    from kotewki.export import build_artifacts as build

    first = tmp_path / "q.json"
    build(glb_path=tmp_path / "a.glb", quantities_path=first)
    payload = first.read_bytes()
    build(glb_path=tmp_path / "a.glb", quantities_path=first)
    assert first.read_bytes() == payload
    assert payload.endswith(b"\n")
    assert json.loads(payload)["schema"] == QUANTITIES_SCHEMA


def test_the_recorded_figures_in_this_module_are_all_asserted_somewhere() -> None:
    """Guards the record against rot by omission, the way T09 guards :data:`MEASURED`."""
    source = Path(__file__).read_text(encoding="utf-8")
    for name in (
        "RECORDED_MESH",
        "RECORDED_SOLID_VOLUME_SUM_M3",
        "RECORDED_OVERLAP_M3",
        "RECORDED_OVERLAP_PAIRS",
        "RECORDED_TOP_OVERLAPS",
        "RECORDED_Z",
        "RECORDED_BUILDING_HEIGHT_M",
        "RECORDED_PLAN_M",
        "VERTEX_BAND",
    ):
        assert source.count(name) >= 2, f"{name} is recorded but never used"
    assert set(RECORDED_PLAN_M) == {"roof", "finished", "structural"}
    assert set(RECORDED_Z) == {
        "terrain",
        "ground_floor",
        "eave_fascia_underside",
        "ridge",
        "chimney_top",
    }

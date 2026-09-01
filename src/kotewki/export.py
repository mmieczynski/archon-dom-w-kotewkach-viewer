"""export.py -- glTF/GLB export and the computed take-off (T12).

Turns T11's :class:`trimesh.Scene` into two build artifacts::

    build/model.glb          the mesh, glTF 2.0 binary, metres
    build/quantities.json    the computed areas, volumes and elevations

Neither is ever hand-edited. ``just build`` regenerates both from ``spec/``; so does the
test suite, so a stale artifact cannot silently pass a check.

UNITS -- WHERE THE BOUNDARY ACTUALLY IS
---------------------------------------
**glTF is defined in metres, 1 unit = 1 m.** The spec is integer millimetres, so exactly
one multiplication by 1/1000 stands between them, and the whole "the model is 1000x too
big" bug class lives in whether that multiplication happens once, twice or not at all.

The project docs (README "Units", TASKS rule 2, tasks/T12.md) all say the conversion
happens *here*. It does not, and pretending otherwise would be the second conversion:
:func:`kotewki.geometry.to_m` already converts at the kernel boundary, and T11 hands this
module a scene whose coordinates are **already metres**. So this module's job is not to
convert but to *hold the boundary shut*:

* :func:`assert_metres` refuses to export a scene that is not plausibly in metres --
  ``metadata['units']`` must say so, and the building's plan extent must fall in
  :data:`PLAUSIBLE_EXTENT_M`, which is T06's magnitude-sanity assertion applied to the
  mesh instead of to the spec. A missed or doubled /1000 fails it instantly.
* no scale is ever applied and no scale transform is ever written. Every node in the
  exported hierarchy carries the identity transform; T12's tests assert that on the file.

If the conversion is ever moved down here, this is the module that has to lose the
assertion -- which is the point of putting it here rather than in a comment.

FLOOR AREA vs USABLE AREA -- THE ONE PUBLISHED NUMBER THAT GOES IN
------------------------------------------------------------------
The polygonised faces are **floor** areas. Nothing in ``spec/ground.json`` models the
ground-floor stair flight, so the open-plan Salon face contains it, and the ground faces
sum to 118.285 m2 against a published *floor* area of 118.81 (-0.44%) and a published
*usable* area of 116.18 (+1.81%). The geometry sides with floor area, which is what
settles the definition: Archon's *powierzchnia uzytkowa* is explicitly *bez schodow*, so
the flight has to come out before any comparison against 163.57 or 127.02.

:func:`ground_stair_run_m2` reads that 2.63 m2 off the published table two independent
ways and requires them to agree. It is the only published figure this module feeds into a
computed one, and the cost is stated rather than hidden: the usable-area comparison
independently constrains 160.94 m2 of the 163.57. ``areas_m2.by_level[*].floor_area``
costs nothing and is the check to read when you want one with no published input at all.

DETERMINISM
-----------
Same spec in, byte-identical ``model.glb`` out, across processes and across
``PYTHONHASHSEED`` values. T13's golden-image diff and T16's per-build archive both depend
on it. Nothing in this module hashes, seeds, timestamps or iterates a ``set``; the GLB
carries no creation date, and ``quantities.json`` is written with sorted-by-construction
insertion order and fixed rounding. ``tests/test_export.py`` verifies this by exporting in
three separate subprocesses under different hash seeds and comparing SHA-256 -- an
in-process build-twice check cannot see set-iteration order, because a set built the same
way twice in one process iterates the same way twice.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import trimesh
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from kotewki import quantities as takeoff
from kotewki.generator import RoofGeometry, build_scene, roof_geometry
from kotewki.geometry import MM_PER_M, Model, RoomGeometry, SlopedCeiling, build_model, to_m

__all__ = [
    "BUILD_DIR",
    "GLB_PATH",
    "GLB_SIZE_WARN_BYTES",
    "PLAUSIBLE_EXTENT_M",
    "QUANTITIES_PATH",
    "QUANTITIES_SCHEMA",
    "ROUND_DP",
    "ExportError",
    "ExportResult",
    "assert_metres",
    "build_artifacts",
    "compute_quantities",
    "export",
    "export_scene",
    "glb_bytes",
    "ground_stair_run_m2",
]


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "build"
GLB_PATH = BUILD_DIR / "model.glb"
QUANTITIES_PATH = BUILD_DIR / "quantities.json"

#: Bumped whenever the shape of ``quantities.json`` changes, so T14 and T16 can tell a new
#: layout from a changed number.
QUANTITIES_SCHEMA = "kotewki/quantities/1"

#: Decimal places every float in ``quantities.json`` is rounded to. Six is a micrometre on
#: a length and a square millimetre on an area -- far below anything measurable here, and
#: far above the float noise that would otherwise make two identical builds diff.
ROUND_DP = 6

#: T14 has to load this over HTTP into a browser; flag anything that would hurt.
GLB_SIZE_WARN_BYTES = 20 * 1024 * 1024

#: The unit-scale guard. A house is between 5 m and 50 m across in *any* plan direction;
#: the same band T06 asserts on the spec's overall dimensions. In millimetres the mesh
#: would read 18 280, in centimetres 1 828, and both are excluded. This is the assertion
#: that makes "no scale transform" mean something rather than being a claim.
PLAUSIBLE_EXTENT_M = (5.0, 50.0)

#: Ditto vertically: ground to ridge, plus a plinth and a chimney, is metres not miles.
PLAUSIBLE_HEIGHT_M = (2.0, 30.0)

#: glTF magic and version, checked on the bytes this module produces rather than assumed.
GLB_MAGIC = b"glTF"
GLB_VERSION = 2


class ExportError(Exception):
    """The scene could not be exported. Always says what was expected and what was seen."""


# --------------------------------------------------------------------------------------
# The unit boundary
# --------------------------------------------------------------------------------------


def assert_metres(scene: trimesh.Scene) -> None:
    """Refuse to export a scene that is not in metres. Raises :class:`ExportError`.

    Two independent tests, because either alone is weak:

    * the **declared** unit -- ``scene.metadata['units']``, which T11 sets. Catches a
      generator that changed its mind without telling anyone.
    * the **measured** magnitude -- the plan extent of the mesh against
      :data:`PLAUSIBLE_EXTENT_M`. Catches the case the declaration cannot: a scene
      correctly labelled metres whose numbers are millimetres.

    The magnitude test is the one that matters. It is deliberately a wide band and not a
    comparison against the transcribed 17.10 x 9.00 m: a *dimensional* check belongs in
    ``tests/test_export.py`` where a failure is diagnostic, whereas this is a tripwire on
    the one error that would otherwise produce a perfectly valid, perfectly wrong file.
    """
    declared = scene.metadata.get("units")
    if declared not in ("m", "meter", "meters", "metre", "metres"):
        raise ExportError(
            f"the scene declares units={declared!r}; glTF is defined in metres (1 unit = "
            f"1 m) and this exporter applies no conversion. kotewki.geometry.to_m is the "
            f"project's single millimetre->metre boundary -- if a second one has appeared, "
            f"remove it rather than scaling here."
        )
    if scene.is_empty:
        raise ExportError("the scene has no geometry; there is nothing to export.")

    extents = scene.extents
    plan = sorted(float(value) for value in extents[:2])
    low, high = PLAUSIBLE_EXTENT_M
    if not (low <= plan[0] and plan[1] <= high):
        raise ExportError(
            f"the scene's plan extent is {plan[0]:.3f} x {plan[1]:.3f} units, outside the "
            f"{low}-{high} m band a house has to fall in. This is a unit-scale error: at "
            f"1000x it would read millimetres, at 1/1000 kilometres. Fix the conversion, "
            f"never the band."
        )
    height = float(extents[2])
    low, high = PLAUSIBLE_HEIGHT_M
    if not low <= height <= high:
        raise ExportError(
            f"the scene is {height:.3f} units tall, outside the {low}-{high} m band. "
            f"See the plan-extent message above: this is a unit-scale error."
        )


def glb_bytes(scene: trimesh.Scene) -> bytes:
    """The scene as glTF 2.0 binary, in metres, with no scale transform anywhere.

    ``trimesh`` never rescales on export -- ``units`` is written as an ``extras`` tag and
    nothing else -- so the identity of every node transform is a property of the scene T11
    built, not something applied here. It is asserted on the file in
    ``tests/test_export.py`` rather than trusted.
    """
    assert_metres(scene)
    data = trimesh.exchange.gltf.export_glb(scene)
    if data[:4] != GLB_MAGIC:
        raise ExportError(
            f"the exporter produced {data[:4]!r} where a GLB header must read {GLB_MAGIC!r}."
        )
    return data


def _write_atomic(path: Path, payload: bytes) -> Path:
    """Write via a sibling temp file and ``os.replace``.

    ``build/`` is read concurrently -- T13 sections the mesh, T14 serves it, T16 archives
    it -- and a half-written GLB is indistinguishable from a corrupt one. ``os.replace``
    is atomic within a filesystem, so a reader sees either the old file or the new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)
    return path


# --------------------------------------------------------------------------------------
# The take-off
# --------------------------------------------------------------------------------------


def _spec_mapping(spec: Any, key: str) -> Mapping[str, Any]:
    try:
        return spec[key]
    except (TypeError, KeyError):
        value = getattr(spec, key, None)
        if value is None:
            raise ExportError(f"the spec has no {key!r} block.") from None
        return value


def render_thickness_m(spec: Any) -> float:
    """The exterior render, metres: finished wall thickness minus the structural layers.

    T04 solved the published *pow. zabudowy* against the **finished** outline (17.120 x
    9.020 m = 154.4224 m2) while the dimension chains and therefore the wall centrelines
    are the **structural** 450 mm. The difference -- 10 mm per face -- sits outboard of
    every dimensioned surface, which is why the kernel's envelope reads 17.100 x 9.000 and
    the exported mesh reads 17.120 x 9.020. Computed the same way T11 computes it, from
    the spec, so a change to the layer table moves both together.
    """
    wall = _spec_mapping(_spec_mapping(spec, "construction"), "exterior_wall")
    finished = wall["thickness"]
    structural = sum(layer["thickness"] for layer in wall.get("layers", ())[:-1])
    return max(to_m(finished - structural), 0.0)


def structural_envelope(model: Model) -> BaseGeometry:
    """Union of every storey's structural outline.

    The union, not the ground floor: the ground floor carries an 800 mm entrance recess
    that makes its outline non-rectangular (152.108 m2), while the attic above is built
    over it as a full rectangle. *Pow. zabudowy* is the area the building occupies, so the
    recess does not reduce it -- README, "RESOLVED: the footprint residual".
    """
    return unary_union([level.network.envelope() for level in model.levels.values()])


def finished_envelope(spec: Any, model: Model) -> BaseGeometry:
    """:func:`structural_envelope` offset outward by the render. The published outline.

    A mitred (``join_style=2``) offset, so the corners stay square and the result is the
    exact rectangle the offset of a rectangle is -- a rounded join would shave the corners
    and lose ~0.3 cm2 of a figure that is being compared at four significant figures.
    """
    return structural_envelope(model).buffer(render_thickness_m(spec), join_style=2)


def attic_ceiling(spec: Any, model: Model) -> SlopedCeiling:
    """The attic **ceiling** plane, for the PN-ISO 9836 1.4 m / 2.2 m banding.

    Springs from the top of the knee wall (attic floor 3040 + knee wall 290 = 3330), which
    is **not** the 3610 mm roof outer plane the ridge is built from. README, "Two different
    planes": banding from 3610 puts the contours ~0.4 m out and over-reads the attic by
    ~20 %. Same construction as ``tests/test_room_areas.py``, deliberately -- if the two
    ever disagree, the published attic figure moves in one place and not the other.

    Ridge position and half-span come off the attic's own envelope rather than being typed
    in, so a change to the transcribed building depth propagates instead of drifting.
    """
    attic = model.level("attic")
    axis = _spec_mapping(spec, "roof")["ridge_axis"]
    minx, miny, maxx, maxy = attic.network.envelope().bounds
    lo, hi = (miny, maxy) if axis == "x" else (minx, maxx)
    exterior = [wall for wall in attic.network.walls if wall.type == "exterior"]
    if not exterior:
        raise ExportError("the attic has no exterior walls; the springing line is undefined.")
    thickness = max(wall.thickness_m for wall in exterior)
    return SlopedCeiling.from_spec(
        spec,
        ridge_coord_mm=(lo + hi) / 2 * MM_PER_M,
        springing_offset_mm=((hi - lo) / 2 - thickness) * MM_PER_M,
    )


@dataclass(frozen=True)
class _Face:
    """One polygonised floor face and every room the publisher measures on it.

    Four ground-floor rooms -- Hol, Salon, Hol, Kuchnia -- are a single open-plan space:
    the wall network cannot separate them and the publisher's splits between them are
    virtual measuring lines, not walls. Summing their computed areas room by room counts
    that one face four times, which is a +150 m2 error, so faces are the unit of account
    here and rooms ride on them. README, "Known area-check limitations".
    """

    rooms: tuple[RoomGeometry, ...]
    area_m2: float
    counted_m2: float

    @property
    def published_m2(self) -> float:
        return sum(room.published_area for room in self.rooms)

    @property
    def shared(self) -> bool:
        return len(self.rooms) > 1

    def in_group(self, group: str) -> bool:
        return any(group in room.area_groups for room in self.rooms)


def _faces(spec: Any, model: Model) -> tuple[_Face, ...]:
    """Every distinct floor face, in spec order, with its counted area.

    ``counted`` is the area that goes into an aggregate: plain floor area everywhere
    except the attic's sloping-ceiling rooms, which are banded, and ``Schody``, which is
    published as plain floor area and must not be banded (banding it yields ~1.55 m2
    against a published 3.64 and looks exactly like a 2 m2 geometry bug). TESTS.md, "Room
    areas".
    """
    ceiling = attic_ceiling(spec, model)
    grouped: dict[tuple[Any, ...], list[RoomGeometry]] = {}
    for room in model.rooms:
        polygon = room.polygon()
        key = (
            room.room.level,
            round(polygon.area, 9),
            tuple(round(value, 9) for value in polygon.bounds),
        )
        grouped.setdefault(key, []).append(room)

    faces = []
    for rooms in grouped.values():
        polygon = rooms[0].polygon()
        banded = rooms[0].room.level == "attic" and all(room.name != "Schody" for room in rooms)
        counted = (
            takeoff.usable_area_sloped(polygon, ceiling, rooms[0].floor_elevation_m)
            if banded
            else polygon.area
        )
        faces.append(_Face(rooms=tuple(rooms), area_m2=polygon.area, counted_m2=counted))
    return tuple(faces)


def _group_total(faces: Iterable[_Face], group: str) -> float:
    return sum(face.counted_m2 for face in faces if face.in_group(group))


def _by_level(
    model: Model,
    faces: Sequence[_Face],
    published_levels: Mapping[str, Any],
    stair_run: float,
) -> dict[str, Any]:
    """Per-storey totals against the published per-storey table.

    Two comparisons per level, and the first of them is the one that pays for itself:

    * ``floor_area`` -- the plain sum of the face polygons, against Archon's published
      *floor* area for that storey (118.81 / 103.83). **No deduction of any kind is applied
      to it**, so it is a fully independent check on the storey: nothing published feeds it.
    * ``counted_area`` -- what the level contributes to the usable total: banded in the
      attic, and with the ground-floor stair flight taken out on the ground floor, against
      the published *usable* figures (116.18 / 51.03).

    Having both is what localises a failure. If ``floor_area`` holds on both storeys and
    ``counted_area`` does not, the geometry is right and a *convention* has moved -- the
    banding plane, or the stair deduction. If ``floor_area`` moves, the layout moved.
    """
    out: dict[str, Any] = {}
    for level_id, level in model.levels.items():
        level_faces = [face for face in faces if face.rooms[0].room.level == level_id]
        deduction = stair_run if level_id == "ground" else 0.0
        reference = published_levels.get(level_id, {})
        out[level_id] = {
            "envelope": level.network.envelope().area,
            "rooms": len(level.rooms),
            "faces": len(level_faces),
            "floor_area": _residual(
                sum(face.area_m2 for face in level_faces), reference.get("floor_area_m2")
            ),
            "counted_area": _residual(
                sum(face.counted_m2 for face in level_faces) - deduction,
                reference.get("usable_area_m2"),
            ),
            "stair_run_deducted": deduction,
        }
    return out


def cubature_m3(spec: Any, model: Model, roof: RoofGeometry) -> float:
    """*Kubatura*: gross built volume, terrain to the roof's outer plane, m3.

    A prism on the **finished** outline -- the same face *pow. zabudowy* is measured to --
    from the terrain at -0.32 m up to the roof plane where it crosses that outline, plus
    the gable wedge above it::

        A * (roof_plane_at_outline - terrain)  +  1/2 * span * rise * length

    Nothing is fitted to the published 849.27 m3. Every input is either transcribed
    (terrain, the outline) or derived from the pitch through the ridge, so the comparison
    is a real check on the roof: at 40.7 deg the wedge alone would be 60 m3 larger. It
    lands at +0.04 %, which is the closest agreement any figure in this project reaches.
    """
    outline = finished_envelope(spec, model)
    minx, miny, maxx, maxy = outline.bounds
    across = (maxy - miny) if roof.ridge_axis == "x" else (maxx - minx)
    along = (maxx - minx) if roof.ridge_axis == "x" else (maxy - miny)
    ridge_m = to_m(roof.ridge_elevation_mm)
    terrain_m = to_m(_spec_mapping(spec, "section_elevations")["terrain"])
    # Where the roof's outer plane crosses the finished outline -- not the springing at the
    # structural face, which is 10 mm further in and 7 mm higher.
    plane_at_outline_m = ridge_m - (across / 2.0) * roof.slope
    prism = outline.area * (plane_at_outline_m - terrain_m)
    wedge = 0.5 * across * (ridge_m - plane_at_outline_m) * along
    return prism + wedge


def _mesh_stats(scene: trimesh.Scene) -> dict[str, Any]:
    """Counts and summed volume of the exported solids.

    ``solid_volume_sum_m3`` is **not** the building's material volume and must never be
    used as one. The solids interpenetrate by design in several places -- wall footprints
    run into each other at junctions, the ground storey's exterior walls pass through the
    attic slab band, and each chimney is traced once per storey plan -- so the sum
    double-counts. ``tests/test_export.py`` measures the overlap (it is ~8 %) and pins it
    against a recorded breakdown. The figure is published only so that a boolean blow-up
    between two builds is visible.
    """
    geometries = list(scene.geometry.values())
    return {
        "nodes": len(scene.geometry),
        "vertices": int(sum(len(mesh.vertices) for mesh in geometries)),
        "faces": int(sum(len(mesh.faces) for mesh in geometries)),
        "solid_volume_sum_m3": float(sum(mesh.volume for mesh in geometries)),
        "bbox_min_m": [float(value) for value in scene.bounds[0]],
        "bbox_max_m": [float(value) for value in scene.bounds[1]],
        "bbox_extents_m": [float(value) for value in scene.extents],
    }


def _published_document() -> Mapping[str, Any]:
    """``data/published.json``, whole. Read-only evidence, never written.

    A missing file degrades to ``{}`` rather than raising: the take-off is computed from
    the spec and is meaningful on its own, and ``data/published.json`` is another task's
    artifact. A *comparison* being absent is a much smaller problem than a build that will
    not run without it.
    """
    path = REPO_ROOT / "data" / "published.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _published() -> Mapping[str, Any]:
    """The ``global`` block of :func:`_published_document`."""
    return _published_document().get("global", {})


def ground_stair_run_m2(published: Mapping[str, Any] | None = None) -> float:
    """The ground-floor stair flight, m2. **The one deduction that consumes published data.**

    WHY IT EXISTS
    -------------
    Archon publishes two different figures per storey and they are not the same
    convention: *powierzchnia zabudowy podlogi* (floor area) counts the whole enclosed
    floor, while *powierzchnia uzytkowa* is explicitly *bez schodow* -- stairs deducted.
    On the ground floor the difference is the stair flight rising out of the Salon, and
    the model's polygonised faces contain it: the wall network has no entity for a stair
    run, so the open-plan face is a *floor* area, not a usable one. Summing the faces
    therefore yields the published **118.81**, not the published **116.18**.

    That is measurable rather than asserted, and it is what settles the definition:
    the ground faces sum to 118.285 m2, which is -0.44% against the published floor area
    and +1.81% against the published usable area. The geometry sides with floor area, so
    the stair run is inside the mesh and has to come out of any usable-area comparison.
    Same for *net* area, which the publisher derives from usable and which therefore also
    excludes this flight -- the 3.64 m2 it adds back is the attic's ``Schody`` room, one
    storey up and a different quantity entirely (README, "Known area-check limitations";
    ``spec/ground.json`` says the same thing on ``G_R6``).

    HONESTY
    -------
    This hands 2.63 m2 of published information to a comparison against a published
    figure, so the usable-area check independently constrains 160.94 m2 of the 163.57, not
    all of it. Declared here rather than buried, exactly as ``tests/test_invariants.py``
    declares it -- and the two must agree, which ``tests/test_export.py`` asserts.

    The value is *derived from the table two independent ways* rather than typed in::

        Salon        floor 33.20  - usable  30.57 = 2.63
        ground level floor 118.81 - usable 116.18 = 2.63

    and the readings are required to agree, so a transcription slip in either row is a
    loud failure instead of a quiet 2.63.
    """
    document = published if published is not None else _published_document()
    level = (document.get("levels") or {}).get("ground") or {}
    rows = [room for room in (document.get("rooms") or {}).get("ground", ()) if room.get("id") == 6]

    readings: dict[str, float] = {}
    if "floor_area_m2" in level and "usable_area_m2" in level:
        readings["level"] = level["floor_area_m2"] - level["usable_area_m2"]
    if rows and "floor_area_m2" in rows[0] and "area_m2" in rows[0]:
        readings["salon"] = rows[0]["floor_area_m2"] - rows[0]["area_m2"]

    if not readings:
        raise ExportError(
            "data/published.json carries neither levels.ground nor the Salon row, so the "
            "ground-floor stair run cannot be read off the published table. It is the one "
            "deduction that turns computed FLOOR area into published USABLE area and it "
            "must not be guessed -- restore the file rather than hardcoding 2.63 here."
        )
    spread = max(readings.values()) - min(readings.values())
    if spread > 0.005:
        raise ExportError(
            f"the ground-floor stair run reads {readings} off the published table; the two "
            f"rows disagree by {spread:.4f} m2. They are independent transcriptions of the "
            f"same quantity and must agree -- one of them is mistyped."
        )
    return round(sum(readings.values()) / len(readings), 6)


def _residual(computed: float, published: float | None) -> dict[str, Any]:
    if published in (None, 0):
        return {"computed": computed, "published": published, "residual_pct": None}
    return {
        "computed": computed,
        "published": published,
        "residual_pct": (computed / published - 1.0) * 100.0,
    }


def compute_quantities(
    spec: Any,
    scene: trimesh.Scene,
    *,
    model: Model | None = None,
    artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The computed take-off that ships next to the mesh, as a JSON-ready dict.

    Every number here is derived from ``spec/`` through the kernel or the generator; the
    published figures ride alongside purely so a reader (and T14) sees the comparison
    without having to hold two files open. Areas are measured to the **finished** face,
    which is the convention Archon publishes (PN-ISO 9836, *w swietle scian*).
    """
    if model is None:
        model = build_model(spec, measure_to="finish")
    if model.measure_to != "finish":
        model = model.with_measure_to("finish")
    roof = roof_geometry(spec, build_model(spec))
    document = _published_document()
    published = document.get("global", {})
    published_levels = document.get("levels", {})
    sections = _spec_mapping(spec, "section_elevations")
    construction = _spec_mapping(spec, "construction")

    faces = _faces(spec, model)
    stair_run = ground_stair_run_m2(document)
    # The faces are FLOOR areas -- the mesh contains the ground stair flight because no
    # spec entity models it. Published usable and net areas are both *bez schodow*, so the
    # flight comes out of each exactly once, on the ground floor and nowhere else.
    usable_floor_basis = _group_total(faces, "usable")
    net_floor_basis = _group_total(faces, "net")
    usable = usable_floor_basis - stair_run
    net = net_floor_basis - stair_run
    shared = [face for face in faces if face.shared]
    open_plan_residual = sum(face.counted_m2 - face.published_m2 for face in shared)

    terrain_m = to_m(sections["terrain"])
    ridge_m = to_m(roof.ridge_elevation_mm)
    attic_level = model.level("attic")

    quantities: dict[str, Any] = {
        "schema": QUANTITIES_SCHEMA,
        "units": {"length": "m", "area": "m2", "volume": "m3", "angle": "deg"},
        "source": {
            "spec": "spec/*.json",
            "generator": "kotewki.generator",
            "exporter": "kotewki.export",
        },
        "artifact": dict(artifact or {}),
        "elevations_m": {
            "terrain": terrain_m,
            "ground_floor": model.level("ground").elevation_m,
            "attic_floor": attic_level.elevation_m,
            "knee_wall_top": attic_level.elevation_m + to_m(construction["knee_wall_height"]),
            "roof_springing": to_m(roof.springing_elevation),
            "ridge": ridge_m,
            "eave_fascia_underside": to_m(roof.eave_fascia_underside_mm),
        },
        "heights": {
            "building_m": _residual(
                ridge_m - terrain_m, published.get("building_height_m")
            ),
            "ridge_above_ground_m": _residual(ridge_m, to_m(sections.get("ridge", 0)) or None),
            "eave_above_ground_m": _residual(
                to_m(roof.eave_fascia_underside_mm),
                to_m(sections.get("eave_fascia_underside", 0)) or None,
            ),
        },
        "roof": {
            "pitch_deg": float(roof.pitch_deg),
            "ridge_axis": roof.ridge_axis,
            "span_m": to_m(roof.span),
            "eaves_overhang_m": to_m(roof.eaves_overhang),
            "verge_overhang_m": to_m(roof.verge_overhang),
            "roof_buildup_vertical_m": to_m(roof.roof_buildup_vertical),
            "fascia_depth_m": to_m(roof.fascia_depth),
            "area_m2": _residual(roof.area_m2, published.get("roof_area_m2")),
        },
        "areas_m2": {
            "footprint": _residual(
                finished_envelope(spec, model).area, published.get("footprint_m2")
            ),
            "footprint_structural": structural_envelope(model).area,
            "usable": _residual(usable, published.get("usable_area_m2")),
            "net": _residual(net, published.get("net_area_m2")),
            "floor": _residual(
                sum(face.area_m2 for face in faces), published.get("floor_area_m2")
            ),
            "attic": _residual(_group_total(faces, "attic"), published.get("attic_area_m2")),
            "boiler": _residual(_group_total(faces, "boiler"), published.get("boiler_room_m2")),
            # Before the stair deduction, i.e. the quantity the polygonised faces actually
            # measure. Published alongside so the deduction is visible as a number rather
            # than folded into the total -- see areas_m2.deductions.
            "usable_floor_basis": usable_floor_basis,
            "net_floor_basis": net_floor_basis,
            "deductions": {
                "ground_stair_run": stair_run,
                "applies_to": ["usable", "net"],
                "source": (
                    "data/published.json, read two ways: levels.ground floor 118.81 - "
                    "usable 116.18, and the Salon row floor 33.20 - usable 30.57. The "
                    "readings must agree or the export fails."
                ),
            },
            "by_level": _by_level(model, faces, published_levels, stair_run),
        },
        "volumes_m3": {
            "cubature": _residual(
                cubature_m3(spec, model, roof), published.get("cubature_m3")
            ),
            "solid_volume_sum": _mesh_stats(scene)["solid_volume_sum_m3"],
        },
        "mesh": _mesh_stats(scene),
        "rooms": [
            {
                "ids": [room.id for room in face.rooms],
                "names": [room.name for room in face.rooms],
                "level": face.rooms[0].room.level,
                "shared_face": face.shared,
                "floor_area_m2": face.area_m2,
                "counted_area_m2": face.counted_m2,
                "published_area_m2": face.published_m2,
                "residual_pct": (
                    (face.counted_m2 / face.published_m2 - 1.0) * 100.0
                    if face.published_m2
                    else None
                ),
            }
            for face in faces
        ],
        "notes": [
            "Areas are measured to the FINISHED face (PN-ISO 9836, w swietle scian), which "
            "is the convention Archon publishes. The dimension chains are printed to raw "
            "structure; the 20 mm/face finish allowance is solved-for, not published.",
            "The four open-plan ground-floor rooms (Hol, Salon, Hol, Kuchnia) share one "
            "polygonised face and are reported as one row. The publisher's splits between "
            "them are virtual measuring lines, not walls.",
            f"That face reads {open_plan_residual:+.3f} m2 against the sum of its four "
            f"published USABLE figures, because it contains the ground-floor stair run "
            f"which the publisher excludes from usable area and which no spec entity "
            f"models. Against the four published FLOOR figures (49.35 m2) the same face "
            f"reads -0.84 %, which is what identifies it as a floor area.",
            f"areas_m2.usable and areas_m2.net are therefore the face sums LESS the "
            f"{stair_run:.2f} m2 ground stair run (areas_m2.deductions), once each and on "
            f"the ground floor only. Undeducted they read {usable_floor_basis:.4f} and "
            f"{net_floor_basis:.4f}, i.e. +1.27 % and +1.52 %, and the usable figure would "
            f"fail the +-1 % invariant. This deduction consumes 2.63 m2 of published "
            f"information: the usable check independently constrains 160.94 m2 of 163.57. "
            f"areas_m2.by_level[*].floor_area consumes none and is the honest check.",
            "The attic's Schody 3.64 m2 is a DIFFERENT quantity from the ground stair run: "
            "different storey, and it is added back into net area rather than deducted.",
            "solid_volume_sum is NOT a material volume. The exported solids interpenetrate "
            "by design (wall junction extensions, ground walls through the attic slab band, "
            "each chimney traced once per storey plan); the sum double-counts by ~8 %. "
            "Cubature is computed from the envelope, never by summing mesh volumes.",
            "The scene bounding box is NOT the building: in plan it includes the 600 mm "
            "eaves and 590 mm verge overhangs, and vertically it runs from the plinth "
            "underside at the terrain to the chimney tops 600 mm above the ridge.",
        ],
        "open_plan_face_residual_m2": open_plan_residual,
    }
    return _rounded(quantities)


def _rounded(value: Any) -> Any:
    """Round every float in a nested structure to :data:`ROUND_DP`.

    Fixed rounding is what makes two builds of the same spec diff to nothing at all in
    T16's archive, instead of to a trail of last-bit changes that hide a real move.
    """
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, ROUND_DP)
    if isinstance(value, bool) or isinstance(value, int) or value is None:
        return value
    if isinstance(value, Mapping):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    return value


def quantities_bytes(quantities: Mapping[str, Any]) -> bytes:
    """``quantities.json`` as bytes: UTF-8, two-space indent, trailing newline."""
    return (
        json.dumps(quantities, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


# --------------------------------------------------------------------------------------
# The public entry points
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportResult:
    """What an export produced. Printable, because ``just build`` prints it."""

    glb_path: Path
    glb_bytes: int
    glb_sha256: str
    quantities_path: Path | None
    node_count: int
    vertex_count: int
    face_count: int
    extents_m: tuple[float, float, float]

    @property
    def oversized(self) -> bool:
        """Would this be uncomfortable to load in a browser? T14 has to care."""
        return self.glb_bytes > GLB_SIZE_WARN_BYTES

    def __str__(self) -> str:
        megabytes = self.glb_bytes / (1024 * 1024)
        lines = [
            f"{self.glb_path}  {self.glb_bytes:,} bytes ({megabytes:.2f} MiB)",
            f"  sha256   {self.glb_sha256}",
            f"  nodes    {self.node_count}  vertices {self.vertex_count:,}  "
            f"faces {self.face_count:,}",
            f"  bbox     {self.extents_m[0]:.3f} x {self.extents_m[1]:.3f} x "
            f"{self.extents_m[2]:.3f} m  (incl. eaves/verge overhangs, plinth and stacks)",
        ]
        if self.quantities_path is not None:
            lines.append(f"{self.quantities_path}")
        if self.oversized:
            lines.append(
                f"  WARNING: over {GLB_SIZE_WARN_BYTES / (1024 * 1024):.0f} MiB -- T14's "
                f"viewer will feel it."
            )
        return "\n".join(lines)


def export_scene(
    scene: trimesh.Scene,
    *,
    spec: Any = None,
    glb_path: Path | str = GLB_PATH,
    quantities_path: Path | str | None = QUANTITIES_PATH,
) -> ExportResult:
    """Write ``model.glb`` (and, unless suppressed, ``quantities.json``) from a scene.

    ``spec`` is only needed for the take-off; it is loaded on demand so that
    ``kotewki.generator.main`` can call this with a scene alone.
    """
    payload = glb_bytes(scene)
    glb = _write_atomic(Path(glb_path), payload)
    digest = hashlib.sha256(payload).hexdigest()
    stats = _mesh_stats(scene)

    written: Path | None = None
    if quantities_path is not None:
        if spec is None:
            from kotewki.spec import load_spec

            spec = load_spec()
        artifact = {
            "path": _relative(glb),
            "bytes": len(payload),
            "sha256": digest,
            "nodes": stats["nodes"],
            "vertices": stats["vertices"],
            "faces": stats["faces"],
        }
        written = _write_atomic(
            Path(quantities_path),
            quantities_bytes(compute_quantities(spec, scene, artifact=artifact)),
        )

    return ExportResult(
        glb_path=glb,
        glb_bytes=len(payload),
        glb_sha256=digest,
        quantities_path=written,
        node_count=stats["nodes"],
        vertex_count=stats["vertices"],
        face_count=stats["faces"],
        extents_m=tuple(stats["bbox_extents_m"]),  # type: ignore[arg-type]
    )


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def build_artifacts(
    spec: Any = None,
    *,
    glb_path: Path | str = GLB_PATH,
    quantities_path: Path | str | None = QUANTITIES_PATH,
) -> ExportResult:
    """Load the spec if needed, build the scene, write both artifacts. ``just build``."""
    if spec is None:
        from kotewki.spec import load_spec

        spec = load_spec()
    return export_scene(
        build_scene(spec),
        spec=spec,
        glb_path=glb_path,
        quantities_path=quantities_path,
    )


#: Alias kept because ``kotewki.generator.main`` looks for ``export_scene`` then ``export``.
export = export_scene


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI convenience
    """``python -m kotewki.export``: build the spec into ``build/`` and report."""
    result = build_artifacts()
    print(result)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

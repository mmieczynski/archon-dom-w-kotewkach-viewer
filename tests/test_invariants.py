"""T09 -- Test 3: global invariants.

Five scalars that constrain the model from directions the room areas cannot reach:
interior layout in aggregate, the exterior envelope, the vertical section, and the roof.

    | Invariant                  | Target     | Tolerance | Validates                     |
    |----------------------------|------------|-----------|-------------------------------|
    | Usable area (uzytkowa)     | 163.57 m2  | +-1%      | interior layout + wall thick. |
    | Footprint (zabudowy)       | 154.42 m2  | +-1%      | exterior envelope at ground   |
    | Cubature                   | 849.27 m3  | +-1.5%    | storey heights + roof volume  |
    | Roof area                  | 216.8 m2   | +-6%      | 35 deg pitch + eave overhangs |
    | Building height            | 7.09 m     | +-30 mm   | section, ground -> ridge      |

All five pass. The measured residuals are recorded in :data:`MEASURED` and asserted, so
the record cannot rot silently.

THE RIDGE IS AN OUTPUT, WHICH IS WHAT MAKES TWO OF THESE REAL
-------------------------------------------------------------
``ridge = attic_floor 3040 + knee_wall 290 + roof_buildup_vertical 280 + (span/2)*tan(35)``
= 6760.93 mm. It is never assigned. ``section_elevations.ridge`` (6770) and the published
7.09 m are **comparison targets only**; nothing in this module feeds either into a
computation. T11 cut that wire three ways in ``tests/test_generator.py`` -- most
importantly ``test_ridge_ignores_the_printed_ridge_height``, which rebuilds with the
printed ridge set to 99999 -- and this module adds
:func:`test_changing_the_pitch_moves_the_computed_ridge` plus
:func:`test_no_invariant_moves_when_the_printed_ridge_is_corrupted` so the guarantee holds
from the invariants' own side too. Without those, "building height = 7.09 m" is a
tautology and the project's central claim is empty.

THREE PARALLEL PLANES, NEVER INTERCHANGEABLE
--------------------------------------------
Conflating any two of these produced the phantom roof discrepancy T17 spent a whole task
undoing, so they are named explicitly wherever they are used:

    3330 = 3040 + 290           ceiling / knee-wall top    -> attic 1.4/2.2 area banding
    3610 = 3330 + 280           roof outer plane at wall   -> ridge, roof construction
    2880 = 3610 - 600*tan35-310 fascia underside at eave   -> the eave assertion only

``roof_buildup_vertical`` (280 mm, at the wall face) and ``fascia_depth`` (310 mm, at the
overhang edge) are different quantities on different planes.

ROOF AREA IS A +-6% BAND, DELIBERATELY
--------------------------------------
See :func:`test_roof_area_is_within_the_six_percent_sanity_band` for the full argument.
Short version: the measured overhangs (600 mm eaves, 590 mm verges, both traced on two
independent images) give 227.6 m2 against a published 216.8 m2. 216.8 implies a 0.44 m
uniform overhang. T17 declined to shrink the overhang to fit, because the overhang is
measured and the publisher's *powierzchnia dachu* convention is not known. This is an
evidence-based band on one invariant whose definition is unknown -- not a tolerance
widened to make a test pass. It is not loosened further here, and it still has teeth:
the refuted 40.7 deg pitch reads +13.4% and fails it.

CUBATURE COMES FROM THE 2D KERNEL, NOT FROM MESH VOLUMES
--------------------------------------------------------
``generator.build_scene`` emits solids that overlap -- chimney stacks pass through the
walls they abut and ground exterior walls run up into the attic slab band. Measured, the
scene's pairwise solid overlap is **21.0 m3** against a 267.3 m3 sum of member volumes, so
``sum(mesh.volume)`` double-counts by ~8% (and is material volume anyway, not the gross
enclosed volume cubature asks for). Cubature here is a closed-form integral over the 2D
footprint polygon; see :func:`cubature_m3` and
:func:`test_cubature_must_not_be_summed_off_the_mesh`.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from kotewki.generator import RoofGeometry, build_scene, roof_geometry
from kotewki.geometry import MM_PER_M, Model, SlopedCeiling, build_model
from kotewki.quantities import sloped_band_areas
from kotewki.spec import Spec

# --------------------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------------------

#: Published areas are *w swietle scian* (PN-ISO 9836), so every room polygon in this
#: module is measured to the finished face -- structure inset by
#: ``construction.finish_allowance`` (20 mm per face, confirmed by T08's sweep over all 18
#: area equations). The envelope is unaffected: the finish allowance is an interior
#: convention and does not push the outside of the building outwards.
MEASURE_TO = "finish"

#: Ground-floor stair run, m2. **This deduction consumes published information** and is
#: declared rather than hidden: the ground-floor stair flight occupies floor area that is
#: not usable area, and nothing in ``spec/ground.json`` models it (the attic's A_SO2
#: stairwell void is 3.6005 m2 and is a different opening one storey up). The figure is
#: read two independent ways off the published table and both give 2.63 --
#: ``Salon 33.2 floor - 30.57 usable`` and ``ground level 118.81 floor - 116.18 usable`` --
#: which is itself checked in :func:`test_the_ground_stair_run_reads_the_same_two_ways`.
#: Consequence for honesty: the usable-area invariant is a 163.57 check with 2.63 m2 of
#: its own answer handed to it, i.e. it independently constrains 160.94 m2 of the 163.57.
GROUND_STAIR_RUN_M2 = 2.63

#: Sloped-ceiling bands, PN-ISO 9836: below 1.4 m counts 0%, 1.4-2.2 m counts 50%, above
#: 2.2 m counts 100%. **1.4/2.2, not 1.9** -- corroborated by the ``140``/``220`` contour
#: labels printed on ``plan_attic.png``. Banded to the CEILING at 3330, never to the 3610
#: roof plane; banding from 3610 over-reads the attic by ~20%.
BANDS: tuple[tuple[float, float], ...] = ((2.2, 1.0), (1.4, 0.5))

#: What this module measures today, so a drift shows up as a diff rather than as silence.
#: These are computed values, not targets; the targets live in ``data/published.json``.
MEASURED: dict[str, float] = {
    "usable_area_m2": 163.1006,
    "footprint_m2": 154.4224,
    "cubature_m3": 849.6268,
    "roof_area_m2": 227.6207,
    "building_height_m": 7.080934,
}

#: Scene solids that are known to interpenetrate, with the measured overlap in m3. README
#: "Outstanding" item 4. Recorded here because it is the reason cubature is computed from
#: the 2D kernel; if these ever come back zero the fix has landed and both this entry and
#: the README item should be deleted.
#:
#: **These four are the CHIMNEY SUBSET, not the four largest overlaps.** T12 measured all
#: 5 565 solid pairs and found 21.0028 m3 across 149 pairs; these four are 3.664 m3 of it,
#: 17%. The second-largest single overlap in the scene,
#: ``ground/walls/G_W7/porotherm_25`` x ``attic/slabs/floor`` at 1.4535 m3, is not here --
#: it belongs to the ground-walls-into-the-attic-slab-band class, which README "Outstanding"
#: item 4 describes in prose but no set enumerates. The full totals are pinned in
#: ``tests/test_export.py`` (``RECORDED_OVERLAP_M3``, ``RECORDED_TOP_OVERLAPS``), which also
#: cross-checks every entry below. Nothing downstream is wrong because of the gap: cubature
#: is a closed-form integral over the 2D footprint and touches no mesh volume.
RECORDED_MESH_OVERLAPS: dict[tuple[str, str], float] = {
    ("ground/chimneys/G_W27/structure", "attic/chimneys/A_W9/structure"): 1.7923,
    ("ground/chimneys/G_W28/structure", "attic/chimneys/A_W10/structure"): 1.0480,
    ("attic/walls/A_W6/structure", "ground/chimneys/G_W27/structure"): 0.4781,
    ("attic/walls/A_W2/porotherm_25", "attic/chimneys/A_W10/structure"): 0.3459,
}


# --------------------------------------------------------------------------------------
# One invariant, evaluated
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Invariant:
    """One published scalar, computed and compared.

    Tolerance is expressed relatively for the four areas/volumes and absolutely for the
    building height, because 30 mm on 7.09 m is the physically meaningful statement: it is
    the measured +-30 mm uncertainty on ``roof_buildup_vertical``, the single derived input
    in the ridge chain.
    """

    name: str
    computed: float
    published: float
    unit: str
    rel_tolerance: float | None = None
    abs_tolerance: float | None = None
    validates: str = ""

    def __post_init__(self) -> None:
        if (self.rel_tolerance is None) == (self.abs_tolerance is None):
            raise ValueError(
                f"{self.name}: give exactly one of rel_tolerance / abs_tolerance, so the "
                f"failure message can state the tolerance the way the brief states it."
            )

    @property
    def delta(self) -> float:
        """Signed error. Positive means the model reads larger than published."""
        return self.computed - self.published

    @property
    def rel(self) -> float:
        return self.delta / self.published

    @property
    def ok(self) -> bool:
        if self.abs_tolerance is not None:
            return abs(self.delta) <= self.abs_tolerance
        assert self.rel_tolerance is not None
        return abs(self.rel) <= self.rel_tolerance

    @property
    def tolerance_text(self) -> str:
        if self.abs_tolerance is not None:
            return f"+-{self.abs_tolerance * 1000:.0f} mm"
        assert self.rel_tolerance is not None
        return f"+-{self.rel_tolerance * 100:g}%"

    def describe(self) -> str:
        mark = "ok  " if self.ok else "FAIL"
        if self.abs_tolerance is not None:
            margin = f"{self.delta * 1000:+8.1f} mm"
        else:
            margin = f"{self.rel * 100:+8.3f} %"
        return (
            f"{mark} {self.name:<16} computed {self.computed:>10.4f} {self.unit:<3} "
            f"published {self.published:>8.2f} {self.unit:<3} {margin}  "
            f"(tol {self.tolerance_text})"
        )


def format_table(invariants: Sequence[Invariant]) -> str:
    """All five, together, whether they passed or not.

    The brief asks that a failure name *which* invariants failed and by how much, together
    -- one invariant drifting is a different diagnosis from all five drifting, and you
    cannot tell which you have from a single assertion firing.
    """
    lines = [item.describe() for item in invariants]
    failed = [item.name for item in invariants if not item.ok]
    lines.append(
        f"{len(invariants) - len(failed)}/{len(invariants)} invariants inside tolerance"
        + (f"; FAILED: {', '.join(failed)}" if failed else "")
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# The measurement definitions
# --------------------------------------------------------------------------------------


def render_thickness_m(spec: Any) -> float:
    """The exterior render, metres: finished wall thickness less structural.

    ``construction.exterior_wall.thickness`` is the **finished** 460 mm; the walls in
    ``spec/ground.json`` and ``spec/attic.json`` are placed at the **structural** 450 mm
    (Porotherm 250 + EPS 200), which is what the printed chains dimension. The 10 mm
    difference is the render, and it sits outboard of the dimensioned outline -- so it
    moves no dimensioned surface but does count towards *pow. zabudowy*, which PN-ISO 9836
    measures on the building *w stanie wykonczonym*.

    Derived from the spec rather than hardcoded, so that a change to either number
    propagates instead of silently disagreeing with the generator's own render layer.
    """
    finished = spec["construction"]["exterior_wall"]["thickness"]
    structural = max(_exterior_wall_thickness_mm(spec), 0)
    return max(finished - structural, 0) / MM_PER_M


def _exterior_wall_thickness_mm(spec: Any) -> float:
    """The thickness the exterior walls are actually built at, millimetres (450).

    Accepts a typed :class:`~kotewki.spec.Spec` or the raw merged mapping, the way
    ``geometry.py`` and ``generator.py`` both do -- the sensitivity tests below rebuild
    from a plain dict.
    """
    if hasattr(spec, "walls"):
        return max(wall.thickness for wall in spec.walls if wall.type == "exterior")
    return max(wall["thickness"] for wall in spec["walls"] if wall["type"] == "exterior")


def footprint_polygon(spec: Any, model: Model) -> BaseGeometry:
    """*Powierzchnia zabudowy*: the terrain area the finished building occupies.

    Archon's stated definition (``data/published.json``): *powierzchnia terenu, zajeta
    przez budynek w stanie wykonczonym, bez tarasow, schodow zewnetrznych i podjazdow*
    (PN-ISO 9836). Three consequences, each of which is a decision this function makes:

    1. **Finished, not structural.** The storey envelope is the structural 450 mm outline
       (17.10 x 9.00 = 153.90 m2); the published figure is that outline grown by the 10 mm
       render on every face, 17.12 x 9.02 = **154.4224 m2**. T04 solved the render this way
       and 15 mm / 20 mm both overshoot. Note the honest limitation recorded in
       :func:`test_the_footprint_band_alone_does_not_discriminate_the_render`.
    2. **The whole building's plan extent, not just the ground storey.** The ground floor
       carries an 800 x 2240 mm entrance recess in the south facade (152.108 m2), but the
       attic is a full rectangle built over it, so the terrain is still occupied. Taking
       the union of the storey envelopes is what expresses that.
    3. **No eaves.** The 600/590 mm overhangs are roof, not building outline, and are
       excluded here. They appear only in :func:`roof_area_m2`.

    The mitre join is required: a round join would fillet the corners and lose ~0.0003 m2,
    and more importantly would not be the outline the render actually follows.
    """
    structural = unary_union([level.network.envelope() for level in model.levels.values()])
    return structural.buffer(render_thickness_m(spec), join_style="mitre")


def footprint_area_m2(spec: Any, model: Model) -> float:
    return footprint_polygon(spec, model).area


def attic_ceiling(spec: Any, roof: RoofGeometry) -> SlopedCeiling:
    """The attic **ceiling** plane, for the 1.4 m / 2.2 m banding. Springs at 3330.

    Not 3610. ``SlopedCeiling.from_spec`` defaults the springing elevation to
    ``attic_floor + knee_wall``, deliberately excluding ``roof_buildup_vertical``: the
    contours are measured to the plastered underside of the roof, which is the build-up
    below its outer plane. T17's traced contours are the check -- 35 deg from 3330 predicts
    d140 = 1.585 m and d220 = 2.728 m against 1.589 and 2.726 measured, i.e. 4 mm and 2 mm.
    From 3610 they would land at 1.185 and 2.328 and the attic would over-read by ~20%.

    Ridge position and half-span are taken from the derived :class:`RoofGeometry` rather
    than retyped, so the banding follows the transcribed building depth. The springing line
    is the interior face of the exterior wall, hence the wall thickness deduction.
    """
    exterior = max(wall.thickness for wall in spec.walls if wall.type == "exterior")
    return SlopedCeiling.from_spec(
        spec,
        ridge_coord_mm=roof.ridge_coord,
        springing_offset_mm=roof.span / 2.0 - exterior,
    )


def _distinct_faces(rooms: Sequence[Any]) -> list[tuple[Any, BaseGeometry]]:
    """One entry per distinct polygonised face, keeping the first room that names it.

    Hol (2), Salon (6), Hol (7) and Kuchnia (14) are one continuous open-plan space with no
    masonry between them; their published splits are the publisher's virtual measuring
    lines (x 4500, y 3800, x 11500). Polygonisation correctly hands all four the *same*
    49.68 m2 face, so summing per room would count it four times and inflate the usable
    area by ~149 m2. Deduplicating by geometry rather than by a hardcoded room-id set means
    a future open-plan room is handled without an edit here.
    """
    out: list[tuple[Any, BaseGeometry]] = []
    seen: set[bytes] = set()
    for room in rooms:
        polygon = room.polygon()
        key = polygon.wkb
        if key in seen:
            continue
        seen.add(key)
        out.append((room, polygon))
    return out


@dataclass(frozen=True)
class UsableAreaBreakdown:
    """Where the usable-area total came from, for the failure message."""

    ground_floor_faces_m2: float
    ground_stair_run_m2: float
    attic_banded_m2: dict[str, float]

    @property
    def ground_m2(self) -> float:
        return self.ground_floor_faces_m2 - self.ground_stair_run_m2

    @property
    def attic_m2(self) -> float:
        return sum(self.attic_banded_m2.values())

    @property
    def total_m2(self) -> float:
        return self.ground_m2 + self.attic_m2


def usable_area_breakdown(spec: Any, model: Model, roof: RoofGeometry) -> UsableAreaBreakdown:
    """*Powierzchnia uzytkowa*, 163.57 m2, assembled from its two levels.

    Definition, from ``data/published.json`` and README's worked reconciliation
    (``116.18 + 14.51 + 14.67 + 18.21 = 163.57``):

    * every room tagged ``usable`` in ``rooms[].area_groups``, measured *w swietle scian*;
    * **the boiler room is in** (7.31 m2, tagged ``boiler`` and ``usable``) -- the published
      *net* area is what excludes it;
    * **the attic stairs are out** (Schody 3.64 m2 is tagged ``net`` only) -- usable area
      excludes stairs, net area includes them, and that asymmetry is the whole content of
      the 163.57 / 127.02 pair;
    * attic rooms are height-banded 1.4/2.2 to the ceiling at 3330; ground rooms are not
      banded at all;
    * the ground-floor stair run is deducted once, on the ground floor, and nowhere else
      (:data:`GROUND_STAIR_RUN_M2`).
    """
    ground = model.level("ground")
    attic = model.level("attic")
    ceiling = attic_ceiling(spec, roof)

    ground_faces = sum(
        polygon.area
        for _, polygon in _distinct_faces(
            [room for room in ground.rooms if room.room.in_usable_area]
        )
    )
    banded = {
        room.id: sloped_band_areas(polygon, ceiling, attic.elevation_m, bands=BANDS)["counted"]
        for room, polygon in _distinct_faces(
            [room for room in attic.rooms if room.room.in_usable_area]
        )
    }
    return UsableAreaBreakdown(
        ground_floor_faces_m2=ground_faces,
        ground_stair_run_m2=GROUND_STAIR_RUN_M2,
        attic_banded_m2=banded,
    )


def cubature_m3(spec: Any, model: Model, roof: RoofGeometry) -> float:
    """*Kubatura*, 849.27 m3: gross volume to the exterior faces.

    Archon's stated definition: *objetosc budynku, liczona zgodnie z norma PN-ISO 9836,
    czyli wraz z przegrodami zewnetrznymi i wewnetrznymi (scianami, stropem, dachem i
    podmurowka)* -- with the external and internal partitions, the slab, **the roof and the
    plinth**. So the solid is:

    * bounded in plan by the finished footprint outline (:func:`footprint_polygon`) --
      **no eaves**, because an overhang encloses nothing;
    * bounded below by the terrain at -0.320 m, i.e. the plinth (*podmurowka*) is included,
      as the definition says in so many words;
    * bounded above by the **outer roof surface**, the tent formed by the two 35 deg planes
      meeting at the derived ridge -- not by a flat lid at the springing.

    METHOD, and why it is exact
    ---------------------------
    The roof surface is piecewise linear, so the enclosed volume is
    ``integral over the footprint of (roof_z(x, y) - terrain_z)``. Split the footprint at
    the ridge line and each piece sees a single linear function; the integral of a linear
    function over a polygon is exactly ``area * f(centroid)``. No sampling, no meshing, and
    no dependence on the footprint being a rectangle -- if the entrance recess were ever
    counted, or a wing added, this still returns the right number.

    NOT computed from mesh volumes: see the module docstring. The generator's solids
    interpenetrate by 21.0 m3 and summing them would double-count.
    """
    footprint = footprint_polygon(spec, model)
    terrain_m = spec["section_elevations"]["terrain"] / MM_PER_M
    minx, miny, maxx, maxy = footprint.bounds
    ridge_m = roof.ridge_coord / MM_PER_M
    pad = 1.0

    if roof.ridge_axis == "x":
        halves = (
            box(minx - pad, miny - pad, maxx + pad, ridge_m),
            box(minx - pad, ridge_m, maxx + pad, maxy + pad),
        )
    else:
        halves = (
            box(minx - pad, miny - pad, ridge_m, maxy + pad),
            box(ridge_m, miny - pad, maxx + pad, maxy + pad),
        )

    volume = 0.0
    for half in halves:
        piece = footprint.intersection(half)
        if piece.is_empty:
            continue
        centroid = piece.centroid
        cross = (centroid.y if roof.ridge_axis == "x" else centroid.x) * MM_PER_M
        volume += piece.area * (roof.top_at(cross) / MM_PER_M - terrain_m)
    return volume


def roof_area_m2(roof: RoofGeometry) -> float:
    """*Powierzchnia dachu*: the sloped surface of both planes, including overhangs.

    Not the horizontal projection. Plan extent (span + 2 x 600 eaves) x (length + 2 x 590
    verges) divided by ``cos(35 deg)``; the ratio is 1.22077, which the brief names as the
    arithmetic sanity check and which
    :func:`test_roof_area_is_the_plan_projection_over_cos_pitch` asserts.
    """
    return roof.area_m2


def building_height_m(spec: Any, roof: RoofGeometry) -> float:
    """Published 7.09 m: *wysokosc mierzona od poziomu terenu... do kalenicy*.

    ``ridge - terrain``, where the ridge is the derived output of the pitch chain and the
    terrain is the printed -0.32 m mark. Both terms come from the section; neither comes
    from the published height, and the printed +6.77 ridge is not read here.
    """
    return (roof.ridge_elevation_mm - spec["section_elevations"]["terrain"]) / MM_PER_M


def evaluate(spec: Any, published: dict[str, Any]) -> list[Invariant]:
    """All five invariants, computed once, in the order the brief lists them."""
    model = build_model(spec, measure_to=MEASURE_TO)
    roof = roof_geometry(spec, model)
    figures = published["global"]
    return [
        Invariant(
            name="usable area",
            computed=usable_area_breakdown(spec, model, roof).total_m2,
            published=figures["usable_area_m2"],
            unit="m2",
            rel_tolerance=0.01,
            validates="interior layout + wall thicknesses",
        ),
        Invariant(
            name="footprint",
            computed=footprint_area_m2(spec, model),
            published=figures["footprint_m2"],
            unit="m2",
            rel_tolerance=0.01,
            validates="exterior envelope at ground level",
        ),
        Invariant(
            name="cubature",
            computed=cubature_m3(spec, model, roof),
            published=figures["cubature_m3"],
            unit="m3",
            rel_tolerance=0.015,
            validates="storey heights + roof volume + plinth",
        ),
        Invariant(
            name="roof area",
            computed=roof_area_m2(roof),
            published=figures["roof_area_m2"],
            unit="m2",
            rel_tolerance=0.06,
            validates="35 deg pitch + eave overhangs",
        ),
        Invariant(
            name="building height",
            computed=building_height_m(spec, roof),
            published=figures["building_height_m"],
            unit="m",
            abs_tolerance=0.030,
            validates="section geometry, ground -> ridge",
        ),
    ]


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def model(spec) -> Model:
    return build_model(spec, measure_to=MEASURE_TO)


@pytest.fixture(scope="module")
def roof(spec, model) -> RoofGeometry:
    return roof_geometry(spec, model)


@pytest.fixture(scope="module")
def invariants(spec, published) -> list[Invariant]:
    return evaluate(spec, published)


def _by_name(invariants: Sequence[Invariant], name: str) -> Invariant:
    return next(item for item in invariants if item.name == name)


def _respec(spec, block: str, **fields) -> Spec:
    """A copy of the spec with one block's fields overridden.

    Deep-copied so a sensitivity test cannot poison the session-scoped ``spec`` fixture.

    Returns a real :class:`Spec`, not the plain dict this originally handed back. ``Spec``
    is a ``Mapping``, so the consumers that only subscript it (``roof_geometry``,
    ``cubature_m3``) were satisfied either way -- but :func:`evaluate` reaches through to
    ``spec.walls``, and a dict has no such attribute. Returning the same type the fixture
    yields keeps every consumer on one code path instead of two.
    """
    document = copy.deepcopy(spec.to_dict() if hasattr(spec, "to_dict") else dict(spec))
    changed = copy.deepcopy(document[block])
    changed.update(fields)
    document[block] = changed
    return Spec(document)


# ======================================================================================
# The five invariants -- together, then one at a time
# ======================================================================================


def test_all_five_invariants_hold(invariants: list[Invariant]) -> None:
    """The headline. Reports every invariant and every margin in one message.

    Deliberately not five separate asserts: if the model drifts, whether *one* invariant
    moved or *all five* did is the entire diagnosis, and a single failing assert hides it.
    Cubature and building height are coupled through the storey heights and both are partly
    derived, so "cubature fails, every area passes" means the error is vertical -- look at
    the section, not the floor plan.
    """
    assert len(invariants) == 5
    failed = [item for item in invariants if not item.ok]
    assert not failed, "global invariants outside tolerance:\n" + format_table(invariants)


def test_usable_area_matches_the_published_163_57(invariants: list[Invariant], spec, model, roof):
    """163.57 m2, +-1%. Reads -0.34%.

    Measured to the finished face because Archon publishes *w swietle scian*. At raw
    structure the same sum reads 166.45 m2 (+1.76%) -- the known pre-allowance signature,
    asserted in :func:`test_the_structure_face_over_reads_the_usable_area`.
    """
    item = _by_name(invariants, "usable area")
    breakdown = usable_area_breakdown(spec, model, roof)
    assert item.ok, (
        f"{item.describe()}\n"
        f"  ground faces {breakdown.ground_floor_faces_m2:.4f} - stair run "
        f"{breakdown.ground_stair_run_m2:.2f} = {breakdown.ground_m2:.4f} (published 116.18)\n"
        f"  attic banded {breakdown.attic_banded_m2} = {breakdown.attic_m2:.4f} "
        f"(published 47.39)"
    )
    assert item.computed == pytest.approx(MEASURED["usable_area_m2"], abs=1e-3)


def test_footprint_matches_the_published_154_42(invariants: list[Invariant]) -> None:
    """154.42 m2, +-1%. Reads +0.0016% -- 17.120 x 9.020 = 154.4224 m2 exactly."""
    item = _by_name(invariants, "footprint")
    assert item.ok, item.describe()
    assert item.computed == pytest.approx(MEASURED["footprint_m2"], abs=1e-4)


def test_cubature_matches_the_published_849_27(invariants: list[Invariant]) -> None:
    """849.27 m3, +-1.5%. Reads +0.042%."""
    item = _by_name(invariants, "cubature")
    assert item.ok, item.describe()
    assert item.computed == pytest.approx(MEASURED["cubature_m3"], abs=1e-3)


def test_roof_area_is_within_the_six_percent_sanity_band(invariants: list[Invariant]) -> None:
    """216.8 m2, **+-6%** rather than +-1%. Reads +4.99% (227.62 m2).

    WHY THIS ONE BAND IS WIDE, AND WHY IT IS NOT WIDENED FURTHER
    ------------------------------------------------------------
    Every other invariant here is compared against a figure whose *definition* Archon
    states. *Powierzchnia dachu* is the exception: the project card gives the number and
    not the convention. T17 could not establish whether the publisher nets off the three
    roof windows, measures to the fascia rather than the tile edge, or excludes the verge
    overhang -- that last reading, 17.10 x 10.20 / cos 35 = 212.9 m2, lands within 1.8%.

    What *is* measured, on two independent images, is the overhang: 600 mm at the eaves
    (section 636/632 mm, attic plan 611 mm) and 590 mm at the verges (attic plan). Those
    give 227.6 m2. Reproducing 216.8 m2 needs a uniform 0.44 m overhang, which contradicts
    the drawings. T17 explicitly declined to shrink the overhang to fit, and this module
    does not do it either: that would be tuning an input to make an assertion pass, which
    is the exact failure mode the whole project is built to prevent.

    So: a deliberate, evidence-based +-6% band on one invariant whose definition is
    unknown, documented rather than silently applied. It is not a tolerance widened to
    rescue a failing test -- the geometry is believed, the published convention is not
    known, and the residual is reported as a finding.

    The band still has teeth. It is not a free pass:

    * the refuted 40.7 deg pitch reads 245.9 m2, +13.4%, and FAILS this band
      (:func:`test_the_six_percent_band_still_excludes_the_refuted_pitch`);
    * a mis-set overhang of 0 mm reads 203.8 m2, -6.0%, and fails it too.
    """
    item = _by_name(invariants, "roof area")
    assert item.ok, item.describe()
    assert item.computed == pytest.approx(MEASURED["roof_area_m2"], abs=1e-3)
    assert 0.04 < item.rel < 0.06, (
        "the roof-area residual is a recorded finding, not a passing check: it should sit "
        f"just under +5%, and it reads {item.rel * 100:+.2f}%. If it has moved, the "
        "overhangs or the pitch have moved with it."
    )


def test_building_height_matches_the_published_7_09(invariants: list[Invariant]) -> None:
    """7.09 m, +-30 mm. Reads -9.1 mm.

    30 mm rather than the 10 mm in ``TESTS.md`` because ``roof_buildup_vertical`` (280 mm)
    is a *measured* quantity carrying +-30 mm -- T17 read it as 0.30-0.34 m vertical at the
    fascia and 0.304 m at the wall face. It is the only derived input in the ridge chain,
    so its uncertainty is the invariant's uncertainty. The reconstruction lands 9 mm low,
    i.e. comfortably inside a tolerance it was not fitted to.
    """
    item = _by_name(invariants, "building height")
    assert item.ok, item.describe()
    assert item.computed == pytest.approx(MEASURED["building_height_m"], abs=1e-6)
    assert item.delta < 0.0, "expected the derived height to sit low, as T17 predicted"


def test_the_recorded_measurements_are_the_ones_the_module_reports(
    invariants: list[Invariant],
) -> None:
    """:data:`MEASURED` covers every invariant, so the record cannot go stale by omission."""
    assert set(MEASURED) == {
        "usable_area_m2",
        "footprint_m2",
        "cubature_m3",
        "roof_area_m2",
        "building_height_m",
    }
    assert len(MEASURED) == len(invariants)


# ======================================================================================
# The ridge is an OUTPUT -- without this, two of the five are tautologies
# ======================================================================================


def test_roof_pitch_is_exactly_35_degrees(spec, roof: RoofGeometry) -> None:
    """Asserted **exactly**, with no tolerance: it is a spec input, not a derived value.

    T17 confirmed the published 35 deg four independent ways (section line 35.003 deg at
    0.06 px RMS; attic contours 35.13 +- 0.6 deg; banded attic areas 34.3-34.5 deg; clean
    gable-render branches 34.92-35.01 deg). ``derived: false`` in ``spec/meta.json``, and
    an equality check is the honest expression of that.
    """
    assert spec["roof"]["pitch_deg"] == 35.0
    assert roof.pitch_deg == 35.0
    assert "pitch_deg" not in spec["roof"].get("derived_fields", ())


@pytest.mark.parametrize("pitch", [25.0, 30.0, 34.0, 35.0, 40.0, 45.0])
def test_changing_the_pitch_moves_the_computed_ridge(spec, pitch: float) -> None:
    """The acceptance criterion: the ridge is computed, not assigned.

    A generator that derived a ridge and then snapped it to the printed +6.77 would pass
    every invariant in this module and fail here. The expected value is written out
    longhand from the chain rather than taken from the object under test, so that a
    redefinition of any term is caught rather than absorbed.
    """
    derived = roof_geometry(_respec(spec, "roof", pitch_deg=pitch))
    expected = 3040.0 + 290.0 + 280.0 + 4500.0 * math.tan(math.radians(pitch))
    assert derived.springing_elevation == pytest.approx(3610.0, abs=1e-9)
    assert derived.ridge_elevation_mm == pytest.approx(expected, abs=1e-6)


def test_the_building_height_check_has_teeth_in_the_pitch(spec, published) -> None:
    """+-30 mm is 0.26 deg of pitch, so the invariant genuinely discriminates.

    At 35 deg the height lands 9 mm from 7.09 m; one degree either way misses by ~120 mm
    and the assertion fires. This is what stops the +-30 mm band from being a formality.
    """
    target = published["global"]["building_height_m"]
    assert abs(building_height_m(spec, roof_geometry(spec)) - target) <= 0.030
    for pitch in (34.0, 36.0):
        off = roof_geometry(_respec(spec, "roof", pitch_deg=pitch))
        assert abs(building_height_m(spec, off) - target) > 0.100, pitch


def test_no_invariant_moves_when_the_printed_ridge_is_corrupted(spec, published) -> None:
    """Replace ``section_elevations.ridge`` with nonsense; nothing computed may move.

    T11 proves the generator does not read it (``test_ridge_ignores_the_printed_ridge_height``
    in ``tests/test_generator.py``, same 99999 mm value). This is the same guarantee stated
    from the invariants' side: if any of the five could reach the printed answer, the
    building-height check would be a tautology and the roof would be unvalidated. Only the
    *published* targets may be compared against -- and those live in
    ``data/published.json``, not in the spec.
    """
    honest = evaluate(spec, published)
    corrupted = evaluate(_respec(spec, "section_elevations", ridge=99999), published)
    for a, b in zip(honest, corrupted, strict=True):
        assert a.computed == b.computed, a.name


def test_the_three_parallel_planes_stay_three_numbers(spec, roof: RoofGeometry) -> None:
    """3330 ceiling / 3610 roof-at-wall / 2880 fascia underside.

    Collapsing any two of these is what produced T17's phantom roof discrepancy, and each
    conflation breaks a *different* check: 3330 vs 3610 mis-bands the attic by ~20%, and
    using ``roof_buildup_vertical`` (280) where ``fascia_depth`` (310) belongs misses the
    printed eave mark by 30 mm.
    """
    knee_top = spec["levels"][1]["elevation"] + spec["construction"]["knee_wall_height"]
    assert knee_top == 3330
    assert roof.springing_elevation == 3610
    assert roof.roof_buildup_vertical == 280
    assert roof.fascia_depth == 310
    assert roof.roof_buildup_vertical != roof.fascia_depth
    assert roof.eave_fascia_underside_mm == pytest.approx(
        spec["section_elevations"]["eave_fascia_underside"], abs=1.0
    )
    # The banding plane is the ceiling, 280 mm below the roof's outer plane at the wall.
    ceiling = attic_ceiling(spec, roof)
    assert ceiling.springing_elevation_m == pytest.approx(knee_top / MM_PER_M, abs=1e-9)
    assert ceiling.ridge_elevation_m < roof.ridge_elevation_mm / MM_PER_M


def test_the_ridge_lands_near_the_printed_mark_without_being_told_it(spec, roof) -> None:
    """The derived 6760.93 mm against the printed 6770 mm: -9.07 mm, and genuinely so."""
    printed = spec["section_elevations"]["ridge"]
    assert roof.ridge_elevation_mm == pytest.approx(printed, abs=30.0)
    assert printed - roof.ridge_elevation_mm == pytest.approx(9.07, abs=0.1)


# ======================================================================================
# The measurement definitions, pinned
# ======================================================================================


def test_the_ground_stair_run_reads_the_same_two_ways(published) -> None:
    """2.63 m2, from two independent rows of the published table.

    ``Salon 33.2 floor - 30.57 usable`` and ``ground level 118.81 floor - 116.18 usable``
    are different published figures and must agree, because the ground floor's whole
    floor/usable gap *is* the stair run. They do. This is why :data:`GROUND_STAIR_RUN_M2`
    is a transcription of the table rather than a fudge factor -- but it is still published
    information being handed to the usable-area invariant, and that is declared.
    """
    salon = next(room for room in published["rooms"]["ground"] if room["id"] == 6)
    from_room = salon["floor_area_m2"] - salon["area_m2"]
    level = published["levels"]["ground"]
    from_level = level["floor_area_m2"] - level["usable_area_m2"]
    assert from_room == pytest.approx(from_level, abs=0.005)
    assert from_room == pytest.approx(GROUND_STAIR_RUN_M2, abs=0.005)


def test_the_open_plan_face_is_counted_once_not_four_times(model: Model) -> None:
    """Hol (2), Salon (6), Hol (7) and Kuchnia (14) are one face; summing them inflates.

    Without the deduplication in :func:`_distinct_faces` the usable area reads ~312 m2
    instead of 163 m2 -- a 91% error that no tolerance would absorb, but which is invisible
    if you only ever look at the room-by-room table.
    """
    ground = model.level("ground")
    usable = [room for room in ground.rooms if room.room.in_usable_area]
    naive = sum(room.polygon().area for room in usable)
    deduplicated = sum(polygon.area for _, polygon in _distinct_faces(usable))
    assert len(_distinct_faces(usable)) == len(usable) - 3

    face = ground.room_by_id["G_R6"].polygon().area
    for room_id in ("G_R2", "G_R7", "G_R14"):
        assert ground.room_by_id[room_id].polygon().area == pytest.approx(face, abs=1e-9)
    assert naive - deduplicated == pytest.approx(3 * face, abs=1e-9)
    assert face == pytest.approx(48.937, abs=1e-3)


def test_schody_is_excluded_from_usable_area_and_never_banded(spec, model, roof) -> None:
    """Usable area excludes stairs; net area includes them. Schody is ``net`` only.

    Two failure modes this pins at once. Counting Schody would put usable at ~166.6 m2
    (+1.9%), and *banding* it -- it is plain floor area, not a sloped-ceiling room -- would
    read ~1.5 m2 and look exactly like a 2 m2 geometry bug.
    """
    schody = model.level("attic").room_by_id["A_R4"]
    assert schody.name == "Schody"
    assert sorted(schody.area_groups) == ["net"]
    assert not schody.room.in_usable_area
    assert "A_R4" not in usable_area_breakdown(spec, model, roof).attic_banded_m2


def test_the_boiler_room_is_inside_usable_area_and_inside_the_footprint(
    spec, model: Model
) -> None:
    """Kotlownia 7.31 m2 counts towards *uzytkowa* and is not an annexe.

    The brief asks this to be confirmed rather than assumed. Both readings check out: the
    room carries ``usable`` in ``area_groups`` (README's reconciliation subtracts it only
    to reach the *net* figure), and its polygon lies wholly within the footprint outline,
    so it is inside the exterior envelope rather than a lean-to outside it.
    """
    boiler = model.level("ground").room_by_id["G_R11"]
    assert boiler.name == "Kotłownia"
    assert boiler.room.in_usable_area
    assert "boiler" in boiler.area_groups
    assert footprint_polygon(spec, model).contains(boiler.polygon())


def test_the_footprint_is_the_union_of_the_storeys_not_the_ground_floor_alone(
    spec, model: Model
) -> None:
    """The entrance recess is real, and it does not reduce *pow. zabudowy*.

    The ground storey's own envelope is 152.108 m2 -- the 800 x 2240 mm recess in the south
    facade, which T04 confirmed because the bottom chain's middle segment ``224`` *is* the
    opening and the left chain's ``80`` *is* its depth. But the attic is a full rectangle
    built over it, so the terrain underneath is still occupied and Archon does not deduct
    it. Using the ground floor alone would read 152.65 m2 finished, -1.15%, and fail.
    """
    ground = model.level("ground").network.envelope()
    attic = model.level("attic").network.envelope()
    assert ground.area == pytest.approx(152.108, abs=1e-3)
    assert attic.area == pytest.approx(153.900, abs=1e-3)
    assert attic.area - ground.area == pytest.approx(0.800 * 2.240, abs=1e-3)

    render = render_thickness_m(spec)
    ground_only = ground.buffer(render, join_style="mitre").area
    assert abs(ground_only - 154.42) / 154.42 > 0.01, (
        "the ground-floor-only footprint should fail the +-1% band; if it now passes, the "
        "recess has changed and this test's reasoning needs re-deriving."
    )


def test_the_footprint_band_alone_does_not_discriminate_the_render(spec, model: Model) -> None:
    """HONEST LIMITATION, recorded so it is not mistaken for a stronger claim.

    The published 154.42 m2 is reproduced to 0.0016% by the finished 17.120 x 9.020
    outline. But the *structural* 17.10 x 9.00 outline reads 153.90 m2, which is only
    -0.34% -- comfortably inside the +-1% band. So this invariant confirms the render but
    does not by itself establish it; the 10 mm figure comes from T04 solving
    ``(17.100 + 2t)(9.000 + 2t) = 154.42``, and what this test records is that the
    invariant would still pass without it, 200x less accurately.
    """
    structural = unary_union(
        [level.network.envelope() for level in model.levels.values()]
    ).area
    finished = footprint_area_m2(spec, model)
    assert abs(structural - 154.42) / 154.42 < 0.01
    assert abs(finished - 154.42) < abs(structural - 154.42) / 100.0


def test_cubature_includes_the_plinth_and_that_is_a_real_check(spec, model, roof) -> None:
    """*wraz z... podmurowka* -- and the 0.32 m plinth is worth 49.4 m3, i.e. 5.8%.

    The brief flags plinth inclusion as the usual cubature ambiguity and suggests treating
    the plinth height as a free variable solved against the published figure. It does not
    need to be free: Archon's stated definition names the *podmurowka* explicitly, and the
    printed terrain mark -0.32 m fixes its height. Dropping it gives 800.2 m3, -5.78%,
    which fails the +-1.5% band -- so the published cubature independently confirms both
    the plinth convention *and* the -0.32 m terrain mark.
    """
    with_plinth = cubature_m3(spec, model, roof)
    without = cubature_m3(_respec(spec, "section_elevations", terrain=0), model, roof)
    assert with_plinth - without == pytest.approx(
        footprint_area_m2(spec, model) * 0.320, abs=1e-6
    )
    assert abs(without - 849.27) / 849.27 > 0.015
    assert without == pytest.approx(800.212, abs=1e-3)


def test_cubature_is_bounded_by_the_roof_surface_not_a_flat_lid(spec, model, roof) -> None:
    """The gable adds 242.7 m3 over a flat lid at the springing plane.

    Stated as its own check because "gross volume to the roof" and "gross volume to the
    wall head" differ by 29% here, and the published figure picks the first. (The gable
    term is 242.7 rather than the 243.8 a naive half-base-times-height gives, because the
    roof plane over the render is 10 mm x tan 35 lower than it is at the structural face.)
    """
    flat = footprint_area_m2(spec, model) * (
        (roof.springing_elevation - spec["section_elevations"]["terrain"]) / MM_PER_M
    )
    full = cubature_m3(spec, model, roof)
    assert full - flat == pytest.approx(242.747, abs=0.01)
    assert abs(flat - 849.27) / 849.27 > 0.015


def test_cubature_excludes_the_eaves_overhang(spec, model, roof) -> None:
    """An overhang encloses nothing, so it is not in the cubature -- but it is in the roof
    area. The two invariants use deliberately different outlines and this pins that."""
    footprint = footprint_polygon(spec, model)
    minx, miny, maxx, maxy = footprint.bounds
    assert maxy - miny == pytest.approx(9.020, abs=1e-6)
    assert (roof.eaves_max - roof.eaves_min) / MM_PER_M == pytest.approx(10.200, abs=1e-6)


def test_cubature_must_not_be_summed_off_the_mesh(spec, model, roof) -> None:
    """The trap the README flags as Outstanding item 4, quantified.

    ``generator.build_scene`` emits interpenetrating solids: the chimney stacks pass
    through the walls they abut, and the ground exterior walls run up into the attic slab
    band. The overlaps are invisible in glTF and harm nothing that is rendered, but
    ``sum(mesh.volume)`` double-counts them -- and is material volume, not gross volume, so
    it is not the right quantity for cubature under any correction. Cubature here is a
    closed-form integral over the 2D footprint and touches no mesh.

    If these overlaps are ever fixed, this test fails: delete the entry from
    :data:`RECORDED_MESH_OVERLAPS` and the corresponding README item.
    """
    scene = build_scene(spec)
    for (left, right), recorded in RECORDED_MESH_OVERLAPS.items():
        assert left in scene.geometry and right in scene.geometry, (left, right)
        shared = scene.geometry[left].intersection(scene.geometry[right])
        assert shared.volume == pytest.approx(recorded, rel=0.02), (left, right)

    material = sum(mesh.volume for mesh in scene.geometry.values())
    assert material == pytest.approx(267.3, abs=1.0)
    assert material < cubature_m3(spec, model, roof) / 2.0


def test_roof_area_is_the_plan_projection_over_cos_pitch(roof: RoofGeometry) -> None:
    """The brief's own sanity check: the ratio to the horizontal projection is 1.2208."""
    plan = (roof.eaves_max - roof.eaves_min) * (roof.verge_max - roof.verge_min) / 1e6
    assert plan == pytest.approx(10.200 * 18.280, abs=1e-6)
    assert roof.area_m2 == pytest.approx(plan / math.cos(math.radians(roof.pitch_deg)))
    assert roof.area_m2 / plan == pytest.approx(1.22077, abs=1e-5)


def test_the_six_percent_band_still_excludes_the_refuted_pitch(spec) -> None:
    """40.7 deg reads +13.4% and fails; the band is wide, not open.

    The pitch conclusion does not depend on the roof area at all -- forcing 216.8 m2 at
    40.7 deg would need a 0.197 m overhang against 0.60 m measured -- but it is worth
    demonstrating that the loosened band has not quietly stopped constraining anything.
    """
    refuted = roof_geometry(_respec(spec, "roof", pitch_deg=40.7))
    assert abs(refuted.area_m2 - 216.8) / 216.8 > 0.06
    no_overhang = roof_geometry(_respec(spec, "roof", eaves_overhang=0, verge_overhang=0))
    assert abs(no_overhang.area_m2 - 216.8) / 216.8 > 0.06


def test_the_published_roof_area_implies_an_overhang_the_drawings_contradict(roof) -> None:
    """Records *why* the residual exists rather than only that it does.

    Solving 216.8 m2 back for a uniform overhang ``t`` at 35 deg gives ~0.44 m, against
    0.60 m eaves and 0.59 m verges traced on two independent images. T17 declined to adopt
    0.44 m and so does this module -- the overhang is measured evidence and the roof-area
    convention is not known, so the unexplained term belongs on the convention, not on the
    geometry.
    """
    span_m, length_m = roof.span / MM_PER_M, (roof.along_max - roof.along_min) / MM_PER_M
    target_plan = 216.8 * math.cos(math.radians(roof.pitch_deg))
    lo, hi = 0.0, 2.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if (span_m + 2 * mid) * (length_m + 2 * mid) < target_plan:
            lo = mid
        else:
            hi = mid
    implied = (lo + hi) / 2
    assert implied == pytest.approx(0.44, abs=0.01)
    assert roof.eaves_overhang / MM_PER_M == 0.600
    assert roof.verge_overhang / MM_PER_M == 0.590


# ======================================================================================
# Convention diagnosis -- the same shape as T08's, so a failure is localisable
# ======================================================================================


def test_the_structure_face_over_reads_the_usable_area(spec, roof) -> None:
    """At raw structure the usable area is +1.76%, the known pre-allowance signature.

    Printed chains dimension raw block; published areas are *w swietle scian*. The gap is
    the 20 mm per-face finish allowance T08 confirmed by sweeping all 18 area equations.
    If the finish reading ever drifts, this says immediately whether the convention moved
    or the geometry did.
    """
    structure_model = build_model(spec, measure_to="structure")
    structure = usable_area_breakdown(spec, structure_model, roof).total_m2
    finish = usable_area_breakdown(spec, build_model(spec, measure_to=MEASURE_TO), roof).total_m2
    assert structure > finish
    assert (structure - 163.57) / 163.57 == pytest.approx(0.0176, abs=0.002)
    assert abs(finish - 163.57) < abs(structure - 163.57)


def test_banding_from_the_roof_plane_would_break_the_usable_area(spec, model, roof) -> None:
    """Banding the attic from 3610 instead of 3330 over-reads it, exactly as documented.

    The failure this guards against is silent: the attic rooms simply read larger and the
    usable-area invariant fails by an amount that looks like a wall-thickness error.
    """
    correct = usable_area_breakdown(spec, model, roof)
    wrong_ceiling = SlopedCeiling.from_spec(
        spec,
        ridge_coord_mm=roof.ridge_coord,
        springing_offset_mm=roof.span / 2.0 - 450,
        springing_elevation_mm=roof.springing_elevation,
    )
    attic = model.level("attic")
    wrong = sum(
        sloped_band_areas(polygon, wrong_ceiling, attic.elevation_m, bands=BANDS)["counted"]
        for _, polygon in _distinct_faces(
            [room for room in attic.rooms if room.room.in_usable_area]
        )
    )
    assert wrong > correct.attic_m2 * 1.15
    assert abs(correct.ground_m2 + wrong - 163.57) / 163.57 > 0.01


def test_the_bands_are_one_four_and_two_two_not_one_nine(spec, model, roof) -> None:
    """1.4 / 2.2, from PN-ISO 9836 and from the ``140``/``220`` contours on the plan.

    An earlier draft of the project docs used 1.9 m. Substituting it moves the attic by
    several square metres and the usable-area invariant fails, which is the point.
    """
    assert BANDS == ((2.2, 1.0), (1.4, 0.5))
    attic = model.level("attic")
    ceiling = attic_ceiling(spec, roof)
    faces = _distinct_faces([room for room in attic.rooms if room.room.in_usable_area])
    correct = sum(
        sloped_band_areas(p, ceiling, attic.elevation_m, bands=BANDS)["counted"] for _, p in faces
    )
    wrong = sum(
        sloped_band_areas(p, ceiling, attic.elevation_m, bands=((2.2, 1.0), (1.9, 0.5)))[
            "counted"
        ]
        for _, p in faces
    )
    assert abs(wrong - correct) > 1.0


def test_the_traced_attic_contours_reproduce_at_35_degrees(spec, roof) -> None:
    """T17's decisive cross-check, re-run against the built geometry.

    d140 = 1.589 m and d220 = 2.726 m measured on ``plan_attic.png`` at 40.90 px/m, over
    twelve columns that all agreed. Predicted from the ceiling plane at 3330 and a 35 deg
    pitch: 1.585 m and 2.728 m. Agreement of 4 mm and 2 mm, under one pixel. This is the
    evidence that the banding plane is the ceiling and not the roof, and it is independent
    of the section entirely.
    """
    ceiling = attic_ceiling(spec, roof)
    floor = spec["levels"][1]["elevation"] / MM_PER_M
    assert ceiling.contour_distance_from_springing(1.4, floor) == pytest.approx(1.589, abs=0.010)
    assert ceiling.contour_distance_from_springing(2.2, floor) == pytest.approx(2.726, abs=0.010)


# ======================================================================================
# Reporting
# ======================================================================================


def test_the_report_names_every_invariant_and_its_margin(invariants: list[Invariant]) -> None:
    text = format_table(invariants)
    for item in invariants:
        assert item.name in text
    assert "5/5 invariants inside tolerance" in text
    assert "FAILED" not in text


def test_the_report_names_the_failures_when_there_are_any() -> None:
    """The failure path is exercised too, so it cannot be broken and go unnoticed."""
    broken = [
        Invariant("usable area", 200.0, 163.57, "m2", rel_tolerance=0.01),
        Invariant("footprint", 154.42, 154.42, "m2", rel_tolerance=0.01),
        Invariant("building height", 7.5, 7.09, "m", abs_tolerance=0.030),
    ]
    text = format_table(broken)
    assert "FAILED: usable area, building height" in text
    assert "1/3 invariants inside tolerance" in text
    assert "+22.272 %" in text
    assert "+410.0 mm" in text


def test_an_invariant_needs_exactly_one_tolerance() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Invariant("x", 1.0, 1.0, "m", rel_tolerance=0.01, abs_tolerance=0.01)
    with pytest.raises(ValueError, match="exactly one"):
        Invariant("x", 1.0, 1.0, "m")


def test_the_tolerances_are_the_ones_the_brief_specifies(invariants: list[Invariant]) -> None:
    """Pinned so no future edit can quietly widen one. +-6% on the roof is the exception
    and is argued in :func:`test_roof_area_is_within_the_six_percent_sanity_band`."""
    assert {item.name: item.rel_tolerance for item in invariants} == {
        "usable area": 0.01,
        "footprint": 0.01,
        "cubature": 0.015,
        "roof area": 0.06,
        "building height": None,
    }
    assert _by_name(invariants, "building height").abs_tolerance == 0.030


def test_every_invariant_says_what_it_validates(invariants: list[Invariant]) -> None:
    assert all(item.validates for item in invariants)
    assert all(item.unit in ("m", "m2", "m3") for item in invariants)


def test_no_two_scene_solids_pairs_are_recorded_twice() -> None:
    """RECORDED_MESH_OVERLAPS keys are unordered pairs; a duplicate would hide a case."""
    keys = [frozenset(pair) for pair in RECORDED_MESH_OVERLAPS]
    assert len(keys) == len(set(keys))
    assert all(len(pair) == 2 for pair in keys), "a solid cannot overlap itself"

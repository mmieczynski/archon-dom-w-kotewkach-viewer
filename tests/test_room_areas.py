"""T08 -- Test 2: room areas vs the published table.

This is the checksum over the transcribed dimensions and the single most informative
module in the suite: it is the first place where the published area table meets real
geometry built from the printed dimension chains.

WHAT IS COMPARED, AND TO WHICH FACE
-----------------------------------
Archon publishes *w swietle scian* per PN-ISO 9836 -- to finished faces, plaster
included -- while the printed chains dimension raw structure. So the primary comparison
runs at ``measure_to="finish"``, i.e. the structural face inset by
``construction.finish_allowance`` (20 mm) per face. ``measure_to="structure"`` is run
alongside it because the *difference* between the two is T15's evidence, and because a
uniform +2..4% residual at ``structure`` is the known signature of the convention rather
than of a geometry error.

FOUR THINGS THAT ARE NOT BUGS
-----------------------------
1. **Hol (2), Salon (6), Hol (7) and Kuchnia (14) are one continuous open-plan space.**
   No masonry separates them; their published splits are the publisher's *virtual
   measuring lines* (x 4500, y 3800, x 11500). Polygonisation correctly returns a single
   face for all four, so they are checked as **one combined area** against the sum of
   their published FLOOR areas. Testing them individually fails no matter how correct
   the geometry is, and "fixing" it means inventing walls that do not exist.
   Consequence, stated plainly: **the ground floor supplies 11 independent area
   equations, not 14.** The project's headline claim about the checksum is weaker than
   originally written -- see ``independent_equation_count`` below.
2. **Salon (6) publishes both 33.2 m2 floor and 30.57 m2 usable.** The 2.63 m2 gap is
   the ground-floor stair run and equals the whole level's floor/usable gap
   (118.81 - 116.18). It is deducted there and nowhere else, and it is *not* the attic's
   Schody 3.64 m2.
3. **Schody (attic) is never banded.** Its published 3.64 m2 is plain floor area and it
   sits in ``area_groups`` ``["net"]`` only. Banded it reads ~1.47 m2, which looks
   exactly like a 2 m2 geometry bug and is not one.
4. **Pokoj (8) and Lazienka (12) are L-shaped** in ways their printed dimension pairs do
   not reveal. Polygonisation handles both with no special case.

THE TOLERANCE IS NOT NEGOTIABLE
-------------------------------
+-1%, everywhere, always. Nothing in this module widens it. Three checks are outside it
and they are recorded in :data:`RECORDED_RESIDUALS` as **findings**, with the measured
number written down: :func:`test_room_areas_match_published_table` asserts that the set of
outliers is *exactly* that set, so a new room drifting out fails loudly and a recorded one
coming back inside also fails (the entry must then be deleted). That is strict-xfail
semantics at the set level, not a loosened tolerance -- every individual room is still
judged at +-1%. :func:`test_every_single_check_is_within_tolerance` states the
unconditional claim and is a strict xfail, so the honest headline stays visible.

CLI
---
The comparison and the finish-allowance sweep are runnable directly; T15 consumes this::

    uv run python tests/test_room_areas.py --measure-to=finish
    uv run python tests/test_room_areas.py --measure-to=structure
    uv run python tests/test_room_areas.py --sweep
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pytest

from kotewki.geometry import (
    MM_PER_M,
    MeasureTo,
    Model,
    SlopedCeiling,
    build_model,
)
from kotewki.quantities import sloped_band_areas
from kotewki.spec import load_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO_ROOT / "spec"

# --------------------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------------------

#: The tolerance. +-1% of the published figure. Never widened, anywhere, for any room.
TOLERANCE = 0.01

#: Published areas are measured *w swietle scian*, so this is the face they are compared
#: to. ``"structure"`` is run alongside for the convention diagnosis, never as a fallback.
PRIMARY_MEASURE_TO: MeasureTo = "finish"

#: Hol (2), Salon (6), Hol (7), Kuchnia (14) -- one continuous face. See the module
#: docstring. Checked combined; never individually.
OPEN_PLAN_ROOM_IDS: frozenset[str] = frozenset({"G_R2", "G_R6", "G_R7", "G_R14"})

#: Key of the single combined result that stands in for those four rooms.
OPEN_PLAN_KEY = "OPEN_PLAN:group"

#: Attic rooms that are NOT height-banded. Schody's published 3.64 m2 is plain floor
#: area (the published attic floor total 103.83 = 29.40 + 31.31 + 39.48 + 3.64 confirms
#: it), and the room is in ``net`` only.
SCHODY_ROOM_ID = "A_R4"
UNBANDED_ATTIC_ROOM_IDS: frozenset[str] = frozenset({SCHODY_ROOM_ID})

#: Ground-floor stair run: Salon's 33.2 m2 floor less its 30.57 m2 usable. Identical to
#: the level's own floor/usable gap, 118.81 - 116.18.
GROUND_STAIR_RUN_M2 = 2.63

#: Checks outside +-1%. Findings, recorded with the measured value so a change is visible
#: in the diff. Adding an entry here does NOT relax the tolerance for that room -- the
#: room is still judged at +-1% and still reported as failing; the entry only records
#: that the failure is already known and analysed.
RECORDED_RESIDUALS: dict[str, str] = {
    "G_R11:usable": (
        "Kotlownia -1.7% (7.183 vs 7.31). Printed 306 x 242 raw. Reproducing 7.31 needs "
        "~9 mm per face, or a width of 311 -- but 311 is the Pralnia's bay one storey "
        "north and adopting it here breaks the lower x chain G_C3. Known before T08 ran; "
        "see README 'Known area-check limitations'."
    ),
    # "G_R12:usable" (Lazienka 12, -1.27%) was recorded here as an irreducible
    # publisher-vs-plan disagreement. It was not. G_W16 ran 610 mm too far north,
    # pushing a 120 x 550 mm wall stub into the shower and eating 0.066 m2 of the
    # room. Found by human inspection of build/overlay_ground.png -- no numeric
    # check could isolate it, because a wall in the wrong place still closes every
    # chain. Corrected in spec/ground.json; the room now reads +0.18%.
}
# A_R4:usable (Schody) was recorded here at -6.3% until T19. It was a measurement
# question after all, not missing geometry: T18 re-measured the drawing and found the
# published 3.64 m2 is the slab opening at raw structure with NO finish allowance on any
# edge, and that the room's south edge is the printed 4700 rather than the guard's north
# face at 4760. With spec/attic.json's rooms[].measure_to carve-out applied the check
# comes in at 3.6575 m2, +0.5%, so the entry is deleted rather than re-worded --
# test_recorded_residuals_all_exist_and_all_actually_fail would reject it now.
# See docs/T18-findings.md item 1.

# --------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------

Check = Literal["usable", "floor", "group"]

Verdict = Literal[
    "none",
    "uniform_offset",
    "single_room",
    "level_specific",
    "attic_banding",
    "scattered",
]


@dataclass(frozen=True)
class AreaResult:
    """One published-area equation, evaluated.

    ``check`` distinguishes the two constraints a room can carry:

    ``"usable"``
        ``rooms[].published_area`` -- height-banded on the attic, plain floor area
        everywhere else.
    ``"floor"``
        ``rooms[].floor_area_m2`` (the parenthesised figure on the plans) against the raw
        floor polygon with **no** banding. Genuinely independent, and it disambiguates two
        failure causes that otherwise look identical: floor passes / usable fails means the
        banding rule is wrong and the geometry is fine, while both failing means the floor
        polygon itself is wrong.
    ``"group"``
        The open-plan aggregate, which stands in for four rooms at once.
    """

    room_id: str
    name: str
    level: str
    check: Check
    published: float
    computed: float
    tolerance: float = TOLERANCE

    @property
    def key(self) -> str:
        return f"{self.room_id}:{self.check}"

    @property
    def rel(self) -> float:
        """Signed relative error. Positive means the model reads larger than published."""
        return (self.computed - self.published) / self.published

    @property
    def ok(self) -> bool:
        return abs(self.rel) < self.tolerance

    @property
    def recorded(self) -> bool:
        """A known, analysed residual -- still a failure, just not a new one."""
        return self.key in RECORDED_RESIDUALS


# --------------------------------------------------------------------------------------
# Building the comparison
# --------------------------------------------------------------------------------------


def attic_ceiling(spec: Any, model: Model) -> SlopedCeiling:
    """The attic **ceiling** plane, for the 1.4 m / 2.2 m banding.

    Springs from the top of the knee wall, ``attic_floor 3040 + knee_wall 290 = 3330`` --
    which is :meth:`SlopedCeiling.from_spec`'s default and is deliberately *not* the
    3610 mm roof outer plane at the wall face. README, "Two different planes": banding from
    3610 puts the contours ~0.4 m out and over-reads the attic by ~20%. Asserted in
    :func:`test_banding_uses_the_ceiling_plane_not_the_roof_plane`.

    Ridge position and half-span are derived from the attic's own envelope rather than
    hardcoded, so a change to the transcribed building depth propagates instead of silently
    disagreeing: the ridge sits on the centreline of the envelope, and the springing line
    on the interior face of the exterior wall.
    """
    attic = model.level("attic")
    axis = spec.roof.ridge_axis
    minx, miny, maxx, maxy = attic.network.envelope().bounds
    lo, hi = (miny, maxy) if axis == "x" else (minx, maxx)
    exterior = [wall for wall in attic.network.walls if wall.type == "exterior"]
    thickness = max(wall.thickness_m for wall in exterior)
    return SlopedCeiling.from_spec(
        spec,
        ridge_coord_mm=(lo + hi) / 2 * MM_PER_M,
        springing_offset_mm=((hi - lo) / 2 - thickness) * MM_PER_M,
    )


def evaluate(
    spec: Any,
    *,
    measure_to: MeasureTo = PRIMARY_MEASURE_TO,
    finish_allowance_mm: float | None = None,
) -> list[AreaResult]:
    """Every published-area equation the model can be held to, evaluated once.

    Routing, in one place so the failure table and the sweep cannot disagree:

    * the four open-plan rooms collapse into one combined result;
    * attic rooms other than Schody are banded through the sloping ceiling;
    * Schody and every ground-floor room use the plain floor polygon;
    * ``floor_area_m2``, where printed, adds an unbanded second equation.
    """
    model = build_model(spec, measure_to=measure_to, finish_allowance_mm=finish_allowance_mm)
    ceiling = attic_ceiling(spec, model)
    results: list[AreaResult] = []

    for room in model.rooms:
        if room.id in OPEN_PLAN_ROOM_IDS:
            continue
        level = model.level(room.room.level)
        polygon = room.polygon()
        banded = room.room.level == "attic" and room.id not in UNBANDED_ATTIC_ROOM_IDS
        computed = (
            sloped_band_areas(polygon, ceiling, level.elevation_m)["counted"]
            if banded
            else polygon.area
        )
        results.append(
            AreaResult(
                room_id=room.id,
                name=room.name,
                level=room.room.level,
                check="usable",
                published=room.published_area,
                computed=computed,
            )
        )
        if room.room.floor_area_m2 is not None:
            results.append(
                AreaResult(
                    room_id=room.id,
                    name=room.name,
                    level=room.room.level,
                    check="floor",
                    published=room.room.floor_area_m2,
                    computed=polygon.area,
                )
            )

    results.append(_open_plan_result(model))
    return results


def _open_plan_result(model: Model) -> AreaResult:
    """The one equation that stands in for Hol (2), Salon, Hol (7) and Kuchnia.

    Compared against the sum of their published **floor** areas, because Salon's usable
    figure has the 2.63 m2 stair run already deducted from it while the polygon does not.
    """
    rooms = [model.room(room_id) for room_id in sorted(OPEN_PLAN_ROOM_IDS)]
    faces = {round(room.polygon().area, 9) for room in rooms}
    if len(faces) != 1:
        raise AssertionError(
            f"the four open-plan rooms {sorted(OPEN_PLAN_ROOM_IDS)} resolved to "
            f"{len(faces)} distinct faces {sorted(faces)}. They are one continuous space "
            f"with no masonry between them, so this means a wall has been invented (or the "
            f"seeds moved). Do not 'fix' the combined check by splitting it."
        )
    published = sum(
        room.room.floor_area_m2 if room.room.floor_area_m2 is not None else room.published_area
        for room in rooms
    )
    return AreaResult(
        room_id="OPEN_PLAN",
        name="Hol + Salon + Hol + Kuchnia (one face)",
        level="ground",
        check="group",
        published=published,
        computed=rooms[0].polygon().area,
    )


def aggregate_error(results: Sequence[AreaResult]) -> float:
    """RMS relative error over every equation. The sweep's objective function."""
    if not results:
        return 0.0
    return math.sqrt(sum(result.rel**2 for result in results) / len(results))


def mean_error(results: Sequence[AreaResult]) -> float:
    """Signed mean relative error -- the sign of a uniform measurement-convention offset."""
    if not results:
        return 0.0
    return sum(result.rel for result in results) / len(results)


def sweep(
    spec: Any,
    allowances: Iterable[float],
    *,
    measure_to: MeasureTo = "finish",
) -> list[tuple[float, float, float, int]]:
    """``(allowance_mm, mean_rel, rms_rel, n_failing)`` per candidate finish allowance.

    Every one of the 18 equations is in the objective. This used to take an ``exclude``
    argument, used only to drop ``A_R4:usable`` on the belief that Schody was short by a
    fixed 0.25 m2 of unmodelled landing which no allowance could supply and which dragged
    the optimum ~3 mm low. T18 disproved that: Schody takes no allowance at all, carries
    ``measure_to: structure`` in the spec since T19, and so contributes the *same
    constant* term at every allowance -- it cannot move the argmin, excluded or not. The
    parameter is gone rather than left unused, because a sweep with a room quietly
    dropped from it is the kind of thing that has to justify itself every time it is read.
    """
    rows = []
    for allowance in allowances:
        results = evaluate(spec, measure_to=measure_to, finish_allowance_mm=allowance)
        failing = sum(1 for result in results if not result.ok)
        rows.append((allowance, mean_error(results), aggregate_error(results), failing))
    return rows


# --------------------------------------------------------------------------------------
# The failure classifier -- the real deliverable
# --------------------------------------------------------------------------------------

#: A failure this widespread is a convention problem, not a set of room problems.
UNIFORM_FAILURE_FRACTION = 0.7
#: ...provided they lean the same way. A uniform offset has a sign.
UNIFORM_SIGN_FRACTION = 0.8
#: At or below this many distinct rooms (and this fraction of all checks), failures are
#: isolated: real geometry errors in those rooms, not a pattern.
ISOLATED_MAX_ROOMS = 3
ISOLATED_MAX_FRACTION = 0.25
#: A level is "the problem" when at least this fraction of its own checks fail.
LEVEL_FAILURE_FRACTION = 0.5


def classify_failures(results: Sequence[AreaResult]) -> Verdict:
    """Name the *pattern* in the failures, because the pattern is the diagnosis.

    ``"none"``
        Everything inside +-1%.
    ``"uniform_offset"``
        Nearly everything fails, nearly all the same way. **Wrong finish allowance /
        measurement convention.** Do not chase individual rooms; hand to T15. The
        signature before the allowance is applied is roughly +2..4%.
    ``"attic_banding"``
        Two or more attic checks fail, nothing on the ground floor fails, and the attic's
        unbanded ``floor_area_m2`` checks all pass. **Suspect the 1.4/2.2 banding rule or
        the plane it is measured from, before the geometry.**
    ``"single_room"``
        A handful of isolated rooms. **Real geometry errors in those rooms.**
    ``"level_specific"``
        Half or more of one level fails and the other level is clean: a level-specific
        error -- wrong partition thickness, or the banding rule misapplied.
    ``"scattered"``
        Many rooms, differing amounts, no pattern. **Transcription is broadly wrong: go
        back to T04/T05. Do not attempt individual fixes.**

    Ordered from the most systemic explanation to the least, so the cheapest fix is
    always proposed first.
    """
    failures = [result for result in results if not result.ok]
    if not failures:
        return "none"

    if _is_uniform(results, failures):
        return "uniform_offset"

    levels = {failure.level for failure in failures}
    if levels == {"attic"} and _looks_like_banding(results, failures):
        return "attic_banding"

    rooms = {failure.room_id for failure in failures}
    if len(rooms) <= ISOLATED_MAX_ROOMS and len(failures) <= ISOLATED_MAX_FRACTION * len(results):
        return "single_room"

    if len(levels) == 1:
        level = next(iter(levels))
        on_level = [result for result in results if result.level == level]
        if on_level and len(failures) >= LEVEL_FAILURE_FRACTION * len(on_level):
            return "level_specific"

    return "scattered"


def _is_uniform(results: Sequence[AreaResult], failures: Sequence[AreaResult]) -> bool:
    if len(failures) < UNIFORM_FAILURE_FRACTION * len(results):
        return False
    signs = Counter(1 if failure.rel > 0 else -1 for failure in failures)
    return max(signs.values()) >= UNIFORM_SIGN_FRACTION * len(failures)


def _looks_like_banding(results: Sequence[AreaResult], failures: Sequence[AreaResult]) -> bool:
    """Banded figures wrong while the raw floor polygons they came from are right."""
    if len({failure.room_id for failure in failures}) < 2:
        return False
    floors = [result for result in results if result.check == "floor" and result.level == "attic"]
    return bool(floors) and all(result.ok for result in floors)


# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------


def format_table(results: Sequence[AreaResult]) -> str:
    """The readable failure table. Every equation, worst first, with the verdict on top."""
    verdict = classify_failures(results)
    failing = [result for result in results if not result.ok]
    lines = [
        "",
        f"VERDICT: {verdict}  --  {_VERDICT_ADVICE[verdict]}",
        f"{len(results) - len(failing)}/{len(results)} checks within "
        f"+-{TOLERANCE:.0%};  mean {mean_error(results):+.2%};  "
        f"rms {aggregate_error(results):.2%}",
        "",
        f"{'check':<16} {'room':<26} {'lvl':<7} {'computed':>9} {'published':>10} "
        f"{'rel':>8}  status",
        "-" * 92,
    ]
    for result in sorted(results, key=lambda item: -abs(item.rel)):
        if result.ok:
            status = "ok"
        elif result.recorded:
            status = "FAIL (recorded residual)"
        else:
            status = "FAIL"
        lines.append(
            f"{result.key:<16} {result.name[:26]:<26} {result.level:<7} "
            f"{result.computed:>9.3f} {result.published:>10.2f} {result.rel:>+8.2%}  {status}"
        )
    for result in failing:
        if result.recorded:
            lines += ["", f"  {result.key}: {RECORDED_RESIDUALS[result.key]}"]
    lines.append("")
    return "\n".join(lines)


_VERDICT_ADVICE: dict[Verdict, str] = {
    "none": "every equation inside +-1%.",
    "uniform_offset": (
        "wrong finish allowance / measurement convention. Do NOT chase individual rooms; "
        "hand to T15."
    ),
    "single_room": "isolated real geometry error(s) in the named room(s).",
    "level_specific": (
        "level-specific error: wrong partition thickness, or the banding rule misapplied."
    ),
    "attic_banding": (
        "suspect the 1.4/2.2 banding rule and the plane it is measured from before the "
        "geometry -- the raw floor polygons are fine."
    ),
    "scattered": (
        "transcription is broadly wrong. Go back to T04/T05; do NOT attempt individual "
        "fixes."
    ),
}


def independent_equation_count(results: Sequence[AreaResult]) -> dict[str, int]:
    """How many area equations actually constrain the model, counted honestly.

    Not the same as ``len(results)``: two of the three attic ``floor_area_m2`` checks are
    algebraically dependent on their banded counterparts. A_R2 and A_R3 span the full
    building depth, so both slopes band them by the *same* constant counted depth and
    ``banded = width x 3.779`` while ``floor = width x 8.06`` -- two readings of one
    unknown. A_R1 (Antresola) stops short of the ridge, so its banded figure depends on its
    north edge as well as its width and does add information.
    """
    ground = [result for result in results if result.level == "ground"]
    attic = [result for result in results if result.level == "attic"]
    dependent = sum(
        1
        for result in attic
        if result.check == "floor" and result.room_id in {"A_R2", "A_R3"}
    )
    return {
        "checks_run": len(results),
        "ground": len(ground),
        "attic": len(attic),
        "attic_dependent": dependent,
        "independent": len(results) - dependent,
    }


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def area_spec():
    """The merged spec. Module-scoped so the sweep does not reload it 20 times."""
    if not SPEC_DIR.exists() or not any(SPEC_DIR.glob("*.json")):
        pytest.skip("spec/*.json does not exist yet (T02/T04/T05)")
    return load_spec(SPEC_DIR)


@pytest.fixture(scope="module")
def results(area_spec) -> list[AreaResult]:
    """Every equation at the published convention: finish face, 20 mm per face."""
    return evaluate(area_spec, measure_to=PRIMARY_MEASURE_TO)


@pytest.fixture(scope="module")
def structure_results(area_spec) -> list[AreaResult]:
    """The same equations measured to raw structure -- the pre-allowance signature."""
    return evaluate(area_spec, measure_to="structure")


# --------------------------------------------------------------------------------------
# The headline comparison
# --------------------------------------------------------------------------------------


def test_room_areas_match_published_table(results: list[AreaResult]) -> None:
    """Every published area, at +-1%, against real geometry.

    Asserts the set of outliers is *exactly* :data:`RECORDED_RESIDUALS`. A new room
    drifting outside +-1% fails; a recorded one coming back inside also fails, so the
    record cannot rot. No room's tolerance is widened by any of this.
    """
    outliers = {result.key for result in results if not result.ok}
    expected = set(RECORDED_RESIDUALS)
    assert outliers == expected, (
        format_table(results)
        + f"\nnew failures:      {sorted(outliers - expected)}"
        + f"\nno longer failing: {sorted(expected - outliers)}"
        + "\n\nIf a room is newly outside +-1%, that is a finding: report it. Do not widen "
        "TOLERANCE, and do not add it to RECORDED_RESIDUALS without an explanation of the "
        "measured discrepancy."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "2 of 18 checks are outside +-1% and are recorded in RECORDED_RESIDUALS: "
        "Kotlownia -1.7% and Lazienka (12) -1.3%. (Schody was the third until T19; T18 "
        "showed its residual was a measurement convention, and it now reads +0.5%.) "
        "Kept as a strict xfail so the honest headline -- 'not every published "
        "area is reproduced to 1%' -- stays visible in the suite rather than being "
        "absorbed by the recorded-residual set."
    ),
)
def test_every_single_check_is_within_tolerance(results: list[AreaResult]) -> None:
    failing = [result for result in results if not result.ok]
    assert not failing, format_table(results)


def test_failures_are_isolated_rooms_not_a_systemic_pattern(results: list[AreaResult]) -> None:
    """The classifier's verdict on the real model.

    ``single_room`` is the good answer here: it says the residuals are per-room facts, not
    a wrong finish allowance and not a broken transcription. ``uniform_offset`` would send
    the work to T15; ``scattered`` would send it back to T04/T05.
    """
    assert classify_failures(results) == "single_room", format_table(results)


def test_no_ground_floor_room_reads_more_than_2_percent_off(results: list[AreaResult]) -> None:
    """A weaker, unconditional claim that must hold with no recorded exceptions.

    +-1% is the contract and two checks miss it; but nothing on either level may be off
    by more than 2% -- with no carve-out at all. Schody was the one exception here until
    T19 corrected its measurement convention (docs/T18-findings.md item 1); the exception
    is deleted rather than kept, because a standing exemption that no longer exempts
    anything is exactly how a real 6% error would later slip through unnoticed.
    """
    gross = [result for result in results if abs(result.rel) >= 0.02]
    assert not gross, format_table(results)


# --------------------------------------------------------------------------------------
# The open-plan group -- a definitional limit, not a tolerance question
# --------------------------------------------------------------------------------------


def test_open_plan_rooms_resolve_to_a_single_face(area_spec) -> None:
    """Hol (2), Salon, Hol (7) and Kuchnia share one polygonised face.

    This is the *reason* they cannot be checked individually, asserted rather than
    assumed. If it ever stops being true, a wall has been invented between rooms that the
    plan shows as one continuous space.
    """
    model = build_model(area_spec, measure_to=PRIMARY_MEASURE_TO)
    areas = {model.room(room_id).polygon().area for room_id in OPEN_PLAN_ROOM_IDS}
    assert len(areas) == 1, (
        f"expected one shared face, got areas {sorted(areas)}. The published splits "
        f"(x 4500, y 3800, x 11500) are the publisher's virtual measuring lines, not "
        f"masonry."
    )


def test_open_plan_combined_area_matches_the_sum_of_published_floor_areas(
    results: list[AreaResult],
) -> None:
    """1.81 + 33.2 + 2.30 + 12.04 = 49.35 m2 against the one face, at +-1%."""
    group = next(result for result in results if result.key == OPEN_PLAN_KEY)
    assert group.published == pytest.approx(49.35, abs=0.005)
    assert group.ok, (
        f"open-plan group: computed {group.computed:.3f} m2 against the published floor "
        f"areas' sum {group.published:.2f} m2, off by {group.rel:+.2%}"
    )


def test_open_plan_usable_sum_needs_the_ground_stair_run_deducted(
    results: list[AreaResult],
) -> None:
    """The same face read against *usable* areas, which have the stair run taken out.

    Not an independent equation -- it is the floor equation shifted by 2.63 m2 -- but it
    pins down *where* the deduction belongs, which is the thing that is easy to get wrong.
    """
    group = next(result for result in results if result.key == OPEN_PLAN_KEY)
    published_usable = group.published - GROUND_STAIR_RUN_M2
    assert published_usable == pytest.approx(1.81 + 30.57 + 2.30 + 12.04, abs=0.005)
    computed_usable = group.computed - GROUND_STAIR_RUN_M2
    rel = (computed_usable - published_usable) / published_usable
    assert abs(rel) < TOLERANCE, (
        f"open-plan usable: {computed_usable:.3f} vs {published_usable:.2f}, {rel:+.2%}"
    )


def test_the_ground_stair_run_is_deducted_in_room_6_and_nowhere_else(area_spec) -> None:
    """33.2 - 30.57 = 2.63 = 118.81 - 116.18, and Salon is the only room with both figures.

    Also asserts it is *not* the attic's Schody 3.64 m2 -- different quantity, different
    storey, and conflating them is the mistake this test exists to prevent.
    """
    model = build_model(area_spec, measure_to=PRIMARY_MEASURE_TO)
    ground = [room for room in model.level("ground").rooms]
    with_both = [
        room
        for room in ground
        if room.room.floor_area_m2 is not None
        and room.room.floor_area_m2 != room.published_area
    ]
    assert [room.id for room in with_both] == ["G_R6"]
    salon = with_both[0]
    gap = salon.room.floor_area_m2 - salon.published_area
    assert gap == pytest.approx(GROUND_STAIR_RUN_M2, abs=0.005)
    assert gap == pytest.approx(118.81 - 116.18, abs=0.005)
    assert gap != pytest.approx(3.64, abs=0.05)


# --------------------------------------------------------------------------------------
# The second constraint: floor area, unbanded
# --------------------------------------------------------------------------------------


def test_floor_areas_are_checked_where_the_plans_print_them(results: list[AreaResult]) -> None:
    """Every ``floor_area_m2`` in the spec becomes an equation (bar the open-plan one)."""
    checked = {result.room_id for result in results if result.check == "floor"}
    assert checked == {"A_R1", "A_R2", "A_R3"}, (
        f"floor-area checks ran for {sorted(checked)}. G_R6's 33.2 m2 is folded into the "
        f"open-plan group instead; every other printed figure must be its own equation."
    )


def test_attic_floor_areas_match_unbanded(results: list[AreaResult]) -> None:
    """Raw floor polygons, no banding. This is what makes a banding failure diagnosable.

    Floor passes and usable fails -> the banding rule is wrong, geometry is fine.
    Both fail -> the floor polygon is wrong.
    """
    floors = [result for result in results if result.check == "floor"]
    assert floors
    bad = [result for result in floors if not result.ok]
    assert not bad, format_table(results)


def test_banded_attic_areas_match_published(results: list[AreaResult]) -> None:
    """Antresola 14.51, Strych 14.67, Strych 18.21 -- through the 1.4/2.2 bands."""
    banded = [
        result
        for result in results
        if result.level == "attic"
        and result.check == "usable"
        and result.room_id not in UNBANDED_ATTIC_ROOM_IDS
    ]
    assert {result.room_id for result in banded} == {"A_R1", "A_R2", "A_R3"}
    bad = [result for result in banded if not result.ok]
    assert not bad, format_table(results)


def test_schody_is_compared_unbanded(area_spec) -> None:
    """Schody's 3.64 m2 is plain floor area. Banded it reads ~1.47 m2 -- not a bug.

    Confirmed by the published attic floor total: 29.40 + 31.31 + 39.48 + 3.64 = 103.83.
    """
    model = build_model(area_spec, measure_to=PRIMARY_MEASURE_TO)
    ceiling = attic_ceiling(area_spec, model)
    attic = model.level("attic")
    schody = model.room("A_R4")
    assert schody.area_groups == frozenset({"net"}), (
        "Schody must be in 'net' only: powierzchnia uzytkowa 163.57 excludes the stairs, "
        "powierzchnia netto 127.02 includes them."
    )
    banded = sloped_band_areas(schody.polygon(), ceiling, attic.elevation_m)["counted"]
    assert banded < 2.0, f"banded Schody is {banded:.3f} m2; expected ~1.5"
    assert 29.40 + 31.31 + 39.48 + schody.published_area == pytest.approx(103.83, abs=0.005)


def test_banding_uses_the_ceiling_plane_not_the_roof_plane(area_spec) -> None:
    """3330 mm (attic floor 3040 + knee wall 290), never 3610 mm.

    3610 is the roof's *outer* plane at the wall face, 280 mm of build-up above the
    ceiling. Banding from it over-reads the attic by ~20% -- which would look like a
    geometry error and is not one.
    """
    model = build_model(area_spec, measure_to=PRIMARY_MEASURE_TO)
    attic = model.level("attic")
    ceiling = attic_ceiling(area_spec, model)
    assert ceiling.springing_elevation_m == pytest.approx(3.33, rel=1e-12)
    assert ceiling.ridge_coord_m == pytest.approx(4.5, rel=1e-12)
    assert ceiling.springing_offset_m == pytest.approx(4.05, rel=1e-12)

    wrong = SlopedCeiling.from_spec(
        area_spec,
        ridge_coord_mm=ceiling.ridge_coord_m * MM_PER_M,
        springing_offset_mm=ceiling.springing_offset_m * MM_PER_M,
        springing_elevation_mm=3610,
    )
    banded_rooms = [room for room in attic.rooms if room.id not in UNBANDED_ATTIC_ROOM_IDS]
    published = sum(room.published_area for room in banded_rooms)

    def total(plane: SlopedCeiling) -> float:
        return sum(
            sloped_band_areas(room.polygon(), plane, attic.elevation_m)["counted"]
            for room in banded_rooms
        )

    assert abs(total(ceiling) - published) / published < TOLERANCE
    assert (total(wrong) - published) / published > 0.15


# --------------------------------------------------------------------------------------
# The measurement convention -- T15's evidence
# --------------------------------------------------------------------------------------


def test_structure_face_shows_the_uniform_pre_allowance_offset(
    structure_results: list[AreaResult],
) -> None:
    """Measured to raw structure, nearly everything over-reads by +2..4%.

    That is the known signature of the convention, not of the geometry, and the classifier
    has to name it as such -- otherwise T15 would be sent chasing 13 individual rooms.
    """
    assert classify_failures(structure_results) == "uniform_offset", format_table(
        structure_results
    )
    assert 0.02 < mean_error(structure_results) < 0.04


def test_finish_face_is_decisively_the_better_convention(
    results: list[AreaResult], structure_results: list[AreaResult]
) -> None:
    """The whole comparison under both conventions. Finish wins on every metric."""
    assert aggregate_error(results) < aggregate_error(structure_results) / 1.5
    assert abs(mean_error(results)) < abs(mean_error(structure_results)) / 4
    failing_finish = sum(1 for result in results if not result.ok)
    failing_structure = sum(1 for result in structure_results if not result.ok)
    assert failing_finish < failing_structure


def test_finish_allowance_sweep_bottoms_out_at_the_spec_value(area_spec) -> None:
    """Sweeping the allowance confirms 20 mm per face independently of the two samples.

    T03 inferred 20 mm from two rooms. Over all 18 equations the RMS error is minimised at
    19 mm with 20 mm indistinguishable from it, and the curve is convex with a clear
    single minimum. Schody used to be excluded from the objective; since T19 it carries
    ``measure_to: structure`` and its residual is constant across the sweep, so it cannot
    shift the minimum and is left in. Sweeping all 18 equations is the stronger claim.
    """
    rows = [
        (allowance, rms)
        for allowance, _mean, rms, _failing in sweep(area_spec, range(10, 31))
    ]
    best_allowance, best_rms = min(rows, key=lambda row: row[1])
    report = "\n".join(f"  {mm:2d} mm  rms {rms:.4%}" for mm, rms in rows)
    assert 18 <= best_allowance <= 21, f"sweep minimum at {best_allowance} mm:\n{report}"
    assert best_rms < 0.01, f"best rms {best_rms:.2%}:\n{report}"
    at_20 = dict(rows)[20]
    assert at_20 < best_rms * 1.05, (
        f"20 mm (the spec value) gives rms {at_20:.4%} against the sweep's best "
        f"{best_rms:.4%} at {best_allowance} mm:\n{report}"
    )


def test_sweep_helper_reports_mean_rms_and_failure_count(area_spec) -> None:
    rows = sweep(area_spec, [0, 20])
    assert [row[0] for row in rows] == [0, 20]
    assert rows[0][1] > rows[1][1]  # mean error falls as the allowance grows
    assert rows[0][3] > rows[1][3]  # so does the failure count


# --------------------------------------------------------------------------------------
# How strong is the checksum, really
# --------------------------------------------------------------------------------------


def test_the_ground_floor_gives_eleven_equations_not_fourteen(results: list[AreaResult]) -> None:
    """Stated in code so the honest number cannot be quietly lost.

    14 published ground-floor rooms; four of them are one open-plan face and collapse into
    a single equation. 14 - 4 + 1 = 11.
    """
    ground = [result for result in results if result.level == "ground"]
    assert len(ground) == 11, [result.key for result in ground]
    assert len(OPEN_PLAN_ROOM_IDS) == 4
    assert sum(1 for result in ground if result.check == "group") == 1


def test_independent_equation_count_is_honest(results: list[AreaResult]) -> None:
    """18 checks run; 16 are independent. The README's ~18 room equations is optimistic."""
    counts = independent_equation_count(results)
    assert counts == {
        "checks_run": 18,
        "ground": 11,
        "attic": 7,
        "attic_dependent": 2,
        "independent": 16,
    }, counts


# --------------------------------------------------------------------------------------
# The classifier, on synthetic failure patterns
# --------------------------------------------------------------------------------------


def _synthetic(
    room_id: str,
    rel: float,
    *,
    level: str = "ground",
    check: Check = "usable",
    published: float = 10.0,
) -> AreaResult:
    return AreaResult(
        room_id=room_id,
        name=room_id,
        level=level,
        check=check,
        published=published,
        computed=published * (1.0 + rel),
    )


def _clean(count: int, level: str = "ground") -> list[AreaResult]:
    return [_synthetic(f"{level}_OK{i}", 0.001, level=level) for i in range(count)]


def test_classifier_reports_none_when_everything_passes() -> None:
    assert classify_failures(_clean(10)) == "none"


def test_classifier_names_a_uniform_offset() -> None:
    """Every room over-reading by +2..4%: the raw-structure signature."""
    results = [_synthetic(f"G_R{i}", 0.02 + 0.002 * i) for i in range(12)]
    assert classify_failures(results) == "uniform_offset"


def test_classifier_names_a_uniform_offset_even_with_one_room_the_other_way() -> None:
    """One dissenting room does not rescue an otherwise uniform convention error."""
    results = [_synthetic(f"G_R{i}", 0.03) for i in range(11)] + [_synthetic("G_ODD", -0.02)]
    assert classify_failures(results) == "uniform_offset"


def test_classifier_names_a_single_room() -> None:
    results = _clean(11) + [_synthetic("G_R11", -0.05)]
    assert classify_failures(results) == "single_room"


def test_classifier_still_says_single_room_for_a_few_isolated_rooms() -> None:
    """Three unrelated rooms out of eighteen is a set of room facts, not a pattern.

    This was the real model's state until T19: Kotlownia, Lazienka (12) and Schody. Two
    of the three are still real; the Schody row is kept here as synthetic data because
    the classifier's three-room boundary is what is being exercised, not the model.
    """
    results = (
        _clean(13)
        + [_synthetic("G_R11", -0.017), _synthetic("G_R12", -0.013)]
        + _clean(2, level="attic")
        + [_synthetic("A_R4", -0.063, level="attic")]
    )
    assert classify_failures(results) == "single_room"


def test_classifier_names_a_level_specific_error() -> None:
    """Every ground-floor room off by differing amounts, attic clean."""
    results = [
        _synthetic(f"G_R{i}", (-1) ** i * (0.02 + 0.01 * i)) for i in range(8)
    ] + _clean(8, level="attic")
    assert classify_failures(results) == "level_specific"


def test_classifier_names_attic_banding_when_the_floor_areas_are_fine() -> None:
    """Banded figures wrong, the raw polygons they came from right -> the rule, not the
    geometry."""
    results = (
        _clean(11)
        + [
            _synthetic("A_R1", 0.09, level="attic"),
            _synthetic("A_R2", 0.12, level="attic"),
            _synthetic("A_R3", 0.11, level="attic"),
        ]
        + [
            _synthetic("A_R1", 0.001, level="attic", check="floor"),
            _synthetic("A_R2", 0.001, level="attic", check="floor"),
            _synthetic("A_R3", 0.001, level="attic", check="floor"),
        ]
    )
    assert classify_failures(results) == "attic_banding"


def test_classifier_does_not_blame_banding_when_the_floor_areas_fail_too() -> None:
    """Both fail -> the floor polygon is wrong, so this is not a banding question."""
    results = (
        _clean(11)
        + [
            _synthetic("A_R1", 0.09, level="attic"),
            _synthetic("A_R2", 0.12, level="attic"),
            _synthetic("A_R3", 0.11, level="attic"),
        ]
        + [
            _synthetic("A_R1", 0.08, level="attic", check="floor"),
            _synthetic("A_R2", 0.09, level="attic", check="floor"),
            _synthetic("A_R3", 0.10, level="attic", check="floor"),
        ]
    )
    assert classify_failures(results) != "attic_banding"


def test_classifier_names_scattered_transcription_failure() -> None:
    """Half the model off, both levels, no sign and no magnitude in common."""
    rels = [0.03, -0.05, 0.11, -0.02, 0.07, -0.09]
    results = (
        [_synthetic(f"G_R{i}", rel) for i, rel in enumerate(rels)]
        + _clean(4)
        + [_synthetic(f"A_R{i}", -rel, level="attic") for i, rel in enumerate(rels[:3])]
        + _clean(3, level="attic")
    )
    assert classify_failures(results) == "scattered"


def test_classifier_verdicts_all_carry_advice() -> None:
    """Every verdict prints an action. A verdict with no next step is not a diagnosis."""
    verdicts = {
        "none",
        "uniform_offset",
        "single_room",
        "level_specific",
        "attic_banding",
        "scattered",
    }
    assert set(_VERDICT_ADVICE) == verdicts


# --------------------------------------------------------------------------------------
# Result plumbing
# --------------------------------------------------------------------------------------


def test_area_result_relative_error_is_signed() -> None:
    assert _synthetic("X", 0.05).rel == pytest.approx(0.05)
    assert _synthetic("X", -0.05).rel == pytest.approx(-0.05)
    assert _synthetic("X", 0.009).ok
    assert not _synthetic("X", 0.011).ok


def test_recorded_residuals_all_exist_and_all_actually_fail(results: list[AreaResult]) -> None:
    """The record cannot name a room that does not exist or one that passes."""
    by_key = {result.key: result for result in results}
    for key in RECORDED_RESIDUALS:
        assert key in by_key, f"RECORDED_RESIDUALS names {key!r}, which is not a check"
        assert not by_key[key].ok, (
            f"{key} is now inside +-1% ({by_key[key].rel:+.2%}). Delete its "
            f"RECORDED_RESIDUALS entry."
        )


def test_failure_table_names_the_verdict_and_every_failure(results: list[AreaResult]) -> None:
    table = format_table(results)
    assert "VERDICT:" in table
    for result in results:
        assert result.key in table
    for key in RECORDED_RESIDUALS:
        assert "FAIL (recorded residual)" in table
        assert key in table


def test_tolerance_is_one_percent() -> None:
    """A canary. If this ever changes, the change is not a bug fix."""
    assert TOLERANCE == 0.01
    assert AreaResult("X", "X", "ground", "usable", 10.0, 10.0).tolerance == 0.01


# --------------------------------------------------------------------------------------
# CLI -- T15 consumes this
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """``uv run python tests/test_room_areas.py [--measure-to=finish] [--sweep]``."""
    parser = argparse.ArgumentParser(
        prog="test_room_areas",
        description=(
            "Compare every published room area against the built geometry, and sweep the "
            "finish allowance. Published areas are w swietle scian (PN-ISO 9836), so "
            "--measure-to=finish is the meaningful convention; --measure-to=structure "
            "shows the pre-allowance residual."
        ),
    )
    parser.add_argument(
        "--measure-to",
        choices=["structure", "finish"],
        default=PRIMARY_MEASURE_TO,
        help="measurement face (default: finish)",
    )
    parser.add_argument(
        "--finish-allowance",
        type=float,
        default=None,
        metavar="MM",
        help="override construction.finish_allowance, millimetres per face",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="run the finish-allowance sweep and print the aggregate error under each",
    )
    parser.add_argument(
        "--both",
        action="store_true",
        help="run the whole comparison under BOTH conventions and say which wins",
    )
    args = parser.parse_args(argv)

    spec = load_spec(SPEC_DIR)
    faces = ["structure", "finish"] if args.both else [args.measure_to]
    tables: dict[str, list[AreaResult]] = {}
    for face in faces:
        tables[face] = evaluate(
            spec, measure_to=face, finish_allowance_mm=args.finish_allowance
        )
        print(f"\n===== measure_to = {face} =====")
        print(format_table(tables[face]))

    if args.both:
        best = min(tables, key=lambda face: aggregate_error(tables[face]))
        for face, rows in tables.items():
            print(
                f"{face:>10}: mean {mean_error(rows):+.2%}  rms {aggregate_error(rows):.2%}  "
                f"failing {sum(1 for row in rows if not row.ok)}/{len(rows)}"
            )
        print(f"\nlower aggregate error: {best}")

    if args.sweep:
        print("\n===== finish-allowance sweep (measure_to=finish) =====")
        print("All 18 checks, none excluded. Schody carries measure_to: structure and so")
        print("contributes the same constant at every allowance; it cannot move the")
        print("minimum. (Before T19 it was dropped from this objective.)\n")
        print(f"{'mm':>5} {'mean':>9} {'rms':>9} {'failing':>8}")
        rows = sweep(spec, range(0, 31))
        for allowance, mean, rms, failing in rows:
            print(f"{allowance:5.0f} {mean:>+9.2%} {rms:>9.2%} {failing:>8}")
        best = min(rows, key=lambda row: row[2])
        print(
            f"\nminimum rms {best[2]:.3%} at {best[0]:.0f} mm per face "
            f"-- against the spec's 20 mm"
        )

    # Status is judged on the requested convention only. --both deliberately also runs
    # `structure`, which fails almost everything by construction; that is the diagnosis
    # being printed, not a regression.
    primary = tables[args.measure_to]
    unexpected = {result.key for result in primary if not result.ok} - set(RECORDED_RESIDUALS)
    if unexpected:
        print(f"\nUNEXPECTED failures under measure_to={args.measure_to}: {sorted(unexpected)}")
        return 1
    return 0


def test_cli_runs_both_conventions_and_the_sweep(capsys) -> None:
    """The CLI is the deliverable T15 consumes, so it is exercised here, not assumed."""
    assert main(["--both", "--sweep"]) == 0
    out = capsys.readouterr().out
    assert "measure_to = structure" in out
    assert "measure_to = finish" in out
    assert "lower aggregate error: finish" in out
    assert "minimum rms" in out


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

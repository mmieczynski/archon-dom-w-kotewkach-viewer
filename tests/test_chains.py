"""T06 — dimension chain closure, magnitude sanity, cross-chain consistency, and
chain-to-geometry agreement.

This is the cheapest and highest-value check in the suite (see TESTS.md §1): it runs on
the raw spec, needs no geometry, and fires during active transcription of
``spec/ground.json`` / ``spec/attic.json`` (T04/T05). Failure-message quality is a primary
deliverable here, not a nicety — an agent fixing a typo should be able to act on the
message alone, without reopening the plan image.

Four checks, in order of what they can catch:

1. **Closure** — every chain's ``segments_cm`` must sum exactly to its ``total_cm``.
   Integer centimetres, no tolerance. See README.md "Units".
2. **Magnitude sanity** — the one check closure structurally cannot do. Closure is
   invariant to scale: a spec transcribed in centimetres but *interpreted* as millimetres
   closes perfectly and describes a house one tenth the size. See README.md "Units" and
   ``kotewki.spec.CM_TO_MM``.
3. **Cross-chain consistency** — chains sharing ``(level, axis, extent)`` are redundant
   transcriptions of the same physical run and must agree.
4. **Chain-to-geometry agreement** — once walls exist, wall centrelines derived along a
   chain's axis must reproduce that chain's segment lengths. Skips gracefully until T04/T05
   land walls.

Real-spec-facing tests (``test_chain_closure`` etc.) consume the session-scoped ``spec``
fixture from ``tests/conftest.py`` and skip cleanly while ``spec/ground.json`` and
``spec/attic.json`` are still empty stubs. Everything else in this file is a synthetic
fixture built directly against plain dicts (or, for a couple of integration tests, against
the real ``kotewki.spec.load_spec`` loader) so the four checks can be exercised and proven
correct *before* real transcription lands.
"""

from __future__ import annotations

import json
import warnings
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from kotewki.spec import CM_TO_MM, load_spec

REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------------------
# Magnitude sanity bands (millimetres). See README.md "Units" and tasks/T06.md.
#
# WHERE THE x10 PROTECTION ACTUALLY LIVES — read before touching any number below.
#
# The primary guard against a spec transcribed in centimetres but read as millimetres is
# the pair of assertions on the OVERALL run: BUILDING_DIM_*, and above all the exact
# GROUND_OVERALL_MM equality. Under a x10 slip the building becomes 1710 x 900 mm and both
# fail hard and unambiguously. Those two are untouched and must stay untouched.
#
# The wall-thickness and per-segment bands below are a SECONDARY net. They were originally
# set tight enough to fire on legitimate geometry — a 60 mm balustrade, 610/620 mm chimney
# stacks, and the 450/120 mm wall thicknesses that appear as segments in any chain that
# decomposes a span into walls and spaces. Widening a secondary net whose lower bound is
# producing false positives costs nothing that the primary guard was not already covering;
# see each band's note for exactly what it still catches and what it no longer can.
# --------------------------------------------------------------------------------------

BUILDING_DIM_MIN_MM = 5_000
BUILDING_DIM_MAX_MM = 50_000
STOREY_HEIGHT_MIN_MM = 2_000
STOREY_HEIGHT_MAX_MM = 4_000

#: Any single segment of any chain. NOT a "room dimension" band: a chain that decomposes a
#: span into walls and spaces (spec/attic.json's A_C1 = 45 · 393 · 12 · 700 · 12 · 503 · 45
#: cm) carries wall thicknesses as segments by construction, so the floor has to clear the
#: thinnest dimensioned element on the plans — a 12 cm partition. 5 cm is the floor.
#:
#: STILL CATCHES: a chain scaled by 1/10, whenever any of its segments is under 50 cm,
#: which is true of every wall-decomposing chain (A_C1 ÷ 10 puts four segments under 5 cm);
#: a dropped digit on a small segment (45 -> 4); a segment longer than the building.
#: NO LONGER CATCHES: a dropped digit on a large segment landing between 5 and 50 cm, e.g.
#: 393 -> 39 (390 mm), which now reads as a plausible wall thickness. That case is covered
#: by closure instead — the chain no longer sums to its printed total.
SEGMENT_MIN_MM = 50
SEGMENT_MAX_MM = 20_000

#: Wall thickness, for elements that are actual wall RUNS. Lower bound admits the 60 mm
#: balustrade along the Antresola void (spec/attic.json A_W7) and the 100 mm stair guard
#: (A_W8); upper bound admits the 465 mm exterior build-up with margin.
#:
#: STILL CATCHES: a thickness typed in centimetres (45 -> below 50) or metres (0 is
#: rejected by the schema); an added digit (450 -> 4500, 115 -> 1150).
#: NO LONGER CATCHES: a x10 slip on an element thicker than 500 mm, e.g. the 620 mm chimney
#: typed as 62 — that lands inside the widened band. Nothing in this file catches that;
#: T07's geometry kernel and T08's area checks do, because a chimney an order of magnitude
#: too small stops netting 0.78 m2 off Strych ocieplony (3).
WALL_THICKNESS_MIN_MM = 50
WALL_THICKNESS_MAX_MM = 700

#: Chimney stacks and piers are carried in `walls` for want of anywhere better to put them,
#: and they are legitimately thicker than any wall: 610 and 620 mm in spec/attic.json.
#: `type` cannot separate them — they are typed "structural", and so are load-bearing walls.
#: Their geometry can: a stack is a STUB, roughly as long as it is thick, where a wall run
#: is an order of magnitude longer than it is thick. See _is_pier().
PIER_THICKNESS_MAX_MM = 1_200
PIER_MAX_ASPECT = 2.0

#: T03's cross-reference (README.md "Units"): confirmed from two independent chains plus
#: the site plan. The ground-floor "overall" chain per axis must equal this exactly. THIS
#: IS THE x10 GUARD — see the note at the top of this block.
GROUND_OVERALL_MM = {"x": 17_100, "y": 9_000}


# --------------------------------------------------------------------------------------
# Small helpers shared by the checks below. These operate on plain Mapping access
# (``chain["segments_cm"]``, not ``chain.segments_cm``) so they work identically against a
# raw dict fixture and against the real kotewki.spec.Spec (which is itself a Mapping).
# --------------------------------------------------------------------------------------


def _segments_mm(chain: Mapping[str, Any]) -> list[int]:
    return [value * CM_TO_MM for value in chain["segments_cm"]]


def _total_mm(chain: Mapping[str, Any]) -> int:
    return chain["total_cm"] * CM_TO_MM


def _wall_length_mm(wall: Mapping[str, Any]) -> int | None:
    """Centreline length of an axis-aligned wall, or None if it has no coordinates."""
    start, end = wall.get("start"), wall.get("end")
    if start is None or end is None:
        return None
    return max(abs(end[0] - start[0]), abs(end[1] - start[1]))


def _is_pier(wall: Mapping[str, Any]) -> bool:
    """True for a chimney stack or pier rather than a wall run.

    A wall run is much longer than it is thick (the attic's thinnest partition is 120 mm
    thick and 8550 mm long, a factor of 71). A stack is roughly square on plan: 610 x 680
    and 620 x 875 for the two chimneys in spec/attic.json, factors of 1.1 and 1.4. The
    aspect ratio separates them cleanly with two orders of magnitude of headroom, which
    `type` cannot do — both stacks are typed "structural", as a load-bearing wall would be.

    A wall with no coordinates (synthetic fixtures below) is treated as a run, i.e. it gets
    the tighter band.
    """
    length = _wall_length_mm(wall)
    if length is None:
        return False
    return length < PIER_MAX_ASPECT * wall["thickness"]


# --------------------------------------------------------------------------------------
# 1. Closure
# --------------------------------------------------------------------------------------


def _closure_failures(spec: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    for chain in spec["dimension_chains"]:
        total = sum(chain["segments_cm"])
        printed = chain["total_cm"]
        if total != printed:
            delta = total - printed
            failures.append(
                f"chain {chain['id']!r} ({chain['source_image']}): segments "
                f"{list(chain['segments_cm'])} cm sum to {total} cm, but the printed total "
                f"is {printed} cm — delta {delta:+d} cm. (A delta near a whole segment's "
                f"length usually means a dropped/extra segment; a delta of ~10-100 usually "
                f"means a misread digit.)"
            )
    return failures


def test_chain_closure(spec) -> None:
    """Every printed chain on the real spec must close exactly. Integers, no tolerance."""
    chains = spec["dimension_chains"]
    if not chains:
        pytest.skip(
            "spec/ground.json and spec/attic.json have no dimension_chains yet "
            "(T04/T05 have not transcribed)"
        )
    failures = _closure_failures(spec)
    assert not failures, "\n".join(failures)


# --------------------------------------------------------------------------------------
# 2. Magnitude sanity
# --------------------------------------------------------------------------------------


def _select_overall(group: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Pick the chain in a (level, axis) group that represents the outer envelope.

    Prefers an explicit ``extent`` tag naming it as an overall run (schema:
    ``dimension_chains[].extent``, e.g. ``"overall_width"``); falls back to the chain with
    the largest total, since by construction inner chains nest inside the overall one.
    """
    tagged = [c for c in group if c.get("extent") and "overall" in c["extent"].lower()]
    if tagged:
        return tagged[0]
    return max(group, key=lambda c: c["total_cm"])


def _magnitude_failures(spec: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []

    # Storey clear heights — from levels[], transcribed independently of any chain, so
    # this fires even before a single wall or dimension_chain has been typed.
    for level in spec.get("levels", []):
        height = level["ceiling_height"]
        if not (STOREY_HEIGHT_MIN_MM <= height <= STOREY_HEIGHT_MAX_MM):
            nearest_delta = min(
                abs(height - STOREY_HEIGHT_MIN_MM), abs(height - STOREY_HEIGHT_MAX_MM)
            )
            failures.append(
                f"level {level['id']!r} ({level.get('name', '?')}): ceiling_height "
                f"{height} mm is outside the storey clear-height sanity band "
                f"[{STOREY_HEIGHT_MIN_MM}, {STOREY_HEIGHT_MAX_MM}] mm — looks like a scale "
                f"error (delta to nearest bound {nearest_delta} mm)."
            )

    # Wall thicknesses — the properly-typed field for this (wall.thickness), not chain
    # segments: a chain segment can legitimately be a room span, a reveal or a thickness,
    # and the schema gives wall.thickness its own home so this check doesn't have to guess.
    # Piers/chimney stacks get their own band; see _is_pier() and PIER_THICKNESS_MAX_MM.
    for wall in spec.get("walls", []):
        thickness = wall["thickness"]
        pier = _is_pier(wall)
        upper = PIER_THICKNESS_MAX_MM if pier else WALL_THICKNESS_MAX_MM
        kind = "pier/chimney-stack" if pier else "wall-thickness"
        if not (WALL_THICKNESS_MIN_MM <= thickness <= upper):
            length = _wall_length_mm(wall)
            plan = f", {length} mm long on plan" if length is not None else ""
            failures.append(
                f"wall {wall['id']!r}: thickness {thickness} mm{plan} is outside the "
                f"{kind} sanity band [{WALL_THICKNESS_MIN_MM}, {upper}] mm."
            )

    chains = list(spec.get("dimension_chains", []))

    # Individual segments — every segment of every chain, in millimetres. Deliberately a
    # loose floor: chains legitimately carry wall thicknesses as segments. See SEGMENT_MIN_MM.
    for chain in chains:
        for seg_cm, seg_mm in zip(chain["segments_cm"], _segments_mm(chain)):
            if not (SEGMENT_MIN_MM <= seg_mm <= SEGMENT_MAX_MM):
                failures.append(
                    f"chain {chain['id']!r} ({chain['source_image']}): segment {seg_cm} cm "
                    f"= {seg_mm} mm is outside the individual-segment sanity band "
                    f"[{SEGMENT_MIN_MM}, {SEGMENT_MAX_MM}] mm — check for a misread digit "
                    f"or a x10 scale slip."
                )

    # Overall building dimensions — the outer-envelope chain per (level, axis). This is
    # the one check that catches a spec transcribed in cm but read as mm: closure is
    # scale-invariant, this is not.
    by_level_axis: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for chain in chains:
        by_level_axis[(chain["level"], chain["axis"])].append(chain)

    for (level, axis), group in by_level_axis.items():
        overall = _select_overall(group)
        overall_mm = _total_mm(overall)
        if not (BUILDING_DIM_MIN_MM <= overall_mm <= BUILDING_DIM_MAX_MM):
            failures.append(
                f"chain {overall['id']!r} ({overall['source_image']}), level {level!r} "
                f"axis {axis!r}: overall total {overall['total_cm']} cm = {overall_mm} mm "
                f"is outside the building-envelope sanity band "
                f"[{BUILDING_DIM_MIN_MM}, {BUILDING_DIM_MAX_MM}] mm. This is exactly the "
                f"x10 scale error the rest of the suite cannot catch — see README.md "
                f"'Units'."
            )
        if level == "ground" and axis in GROUND_OVERALL_MM:
            expected = GROUND_OVERALL_MM[axis]
            if overall_mm != expected:
                delta = overall_mm - expected
                failures.append(
                    f"chain {overall['id']!r} ({overall['source_image']}): ground-floor "
                    f"overall {axis}-extent is {overall['total_cm']} cm = {overall_mm} mm, "
                    f"expected exactly {expected} mm (T03 cross-reference: building is "
                    f"17.10 x 9.00 m) — delta {delta:+d} mm."
                )

    return failures


def test_magnitude_sanity(spec) -> None:
    """Absolute-magnitude tripwire. Runs today against real levels[]; chain- and
    wall-derived sub-checks quietly have nothing to check until T04/T05 land, and
    activate automatically as soon as they do."""
    failures = _magnitude_failures(spec)
    assert not failures, "\n".join(failures)


# --------------------------------------------------------------------------------------
# 3. Cross-chain consistency
# --------------------------------------------------------------------------------------


def _cross_chain_groups(
    chains: Sequence[Mapping[str, Any]],
) -> tuple[dict[tuple[str, str, str], list[Mapping[str, Any]]], int]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    untagged = 0
    for chain in chains:
        extent = chain.get("extent")
        if not extent:
            untagged += 1
            continue
        groups[(chain["level"], chain["axis"], extent)].append(chain)
    return groups, untagged


def _cross_chain_failures(
    spec: Mapping[str, Any],
) -> tuple[list[str], int, int, int]:
    """Returns (failures, checked, total, unpartnered).

    ``checked`` counts chains that had at least one partner sharing (level, axis, extent)
    and were therefore actually cross-verified. ``unpartnered`` counts chains with no
    ``extent`` tag, or whose extent group has only one member — these are NOT verified by
    this check, and that fact must not be silently invisible (tasks/T06.md).
    """
    chains = list(spec.get("dimension_chains", []))
    groups, unpartnered = _cross_chain_groups(chains)
    failures: list[str] = []
    checked = 0
    for (level, axis, extent), group in groups.items():
        if len(group) < 2:
            unpartnered += 1
            continue
        checked += len(group)
        totals = {c["total_cm"] for c in group}
        if len(totals) > 1:
            described = ", ".join(
                f"{c['id']!r} ({c['total_cm']} cm, {c['source_image']})" for c in group
            )
            delta_cm = max(totals) - min(totals)
            failures.append(
                f"cross-chain mismatch on level {level!r} axis {axis!r} extent {extent!r}: "
                f"{described} — totals disagree, delta {delta_cm} cm ({delta_cm * CM_TO_MM} mm)."
            )
    return failures, checked, len(chains), unpartnered


def test_cross_chain_consistency(spec) -> None:
    """Chains sharing (level, axis, extent) are redundant by construction and must agree.

    Chains with no partner are not verifiable by this test at all; that count is reported
    via a warning rather than being invisible (tasks/T06.md).
    """
    chains = spec["dimension_chains"]
    if not chains:
        pytest.skip(
            "spec/ground.json and spec/attic.json have no dimension_chains yet "
            "(T04/T05 have not transcribed)"
        )
    failures, checked, total, unpartnered = _cross_chain_failures(spec)
    warnings.warn(
        f"cross-chain consistency: verified {checked}/{total} chain(s) against a partner "
        f"sharing (level, axis, extent); {unpartnered} chain(s) have no partner (missing "
        f"or unique 'extent' tag) and are NOT cross-checked by this test.",
        UserWarning,
        stacklevel=1,
    )
    assert not failures, "\n".join(failures)


# --------------------------------------------------------------------------------------
# 4. Chain-to-geometry agreement
# --------------------------------------------------------------------------------------


def _derive_axis_spans(walls: Sequence[Mapping[str, Any]], axis: str) -> tuple[int, int] | None:
    """The built OUTER and INNER spans along ``axis``, in HALF-millimetres.

    A chain running along ``axis`` is bounded by the walls that run PERPENDICULAR to it: an
    x-axis chain is divided up by vertical walls, i.e. walls whose start and end share an
    x-coordinate. Of those, the two extreme ones are the exterior walls, and:

        outer span = outermost face to outermost face   (what a printed overall chain
                     measures: 17100 x 9000 for this building)
        inner span = their two INNER faces              (what a printed interior chain
                     measures: 16200 x 8100)

    Returned doubled so the arithmetic stays in exact integers for an odd thickness such as
    the 465 mm exterior build-up; a half-millimetre of silent rounding is exactly the sort
    of thing this file exists to refuse. Returns None when the axis has no bounding walls.

    WHY SPANS AND NOT PER-SEGMENT LENGTHS. This function used to return the consecutive
    differences between perpendicular wall centrelines and compare that list to the chain's
    segments. That comparison cannot be made correct in general, for two reasons found
    against T05's real attic transcription:

    1. Centre-to-centre vs face-to-face. Centreline differences measure from the middle of
       the first wall to the middle of the last, so every chain came out short by exactly
       one exterior wall thickness — a uniform -450 mm across all six chains, the signature
       of a modelling error rather than a data error.
    2. The wall list contains elements no printed chain decomposes. The attic's `walls`
       carry a stair balustrade (A_W8) and two chimney stacks; A_W8 alone injects a
       boundary at x = 5500 that appears in no chain. And chains differ in what they even
       decompose: A_C1 walks walls and rooms alternately, A_C2 = 470 · 224 · 1016 is a
       facade decomposition, A_C6 traces ceiling-height contours that are not walls at all.

    A narrower check that is correct beats a broader one that always fires, so the
    per-segment comparison is gone and the span comparison replaces it. What is lost: a
    single interior partition placed at the wrong coordinate no longer breaks this test if
    the envelope still closes. What is kept, and it is not nothing: the spans are checked
    to EXACT integer equality on both axes and against both the overall and the interior
    chains — four independent equalities on the attic — so any error in an exterior wall's
    position or thickness, and any x10 slip in either, fails here. T07's polygonisation and
    T08's per-room areas are what catch a misplaced interior partition.
    """
    idx = 0 if axis == "x" else 1
    perpendicular = [
        wall
        for wall in walls
        if wall.get("start") is not None
        and wall.get("end") is not None
        and wall["start"][idx] == wall["end"][idx]
    ]
    if len(perpendicular) < 2:
        return None

    def lower_face2(wall: Mapping[str, Any]) -> int:
        return 2 * wall["start"][idx] - wall["thickness"]

    def upper_face2(wall: Mapping[str, Any]) -> int:
        return 2 * wall["start"][idx] + wall["thickness"]

    first = min(perpendicular, key=lower_face2)
    last = max(perpendicular, key=upper_face2)
    outer2 = upper_face2(last) - lower_face2(first)
    inner2 = lower_face2(last) - upper_face2(first)
    return outer2, inner2


def _fmt_halves(value2: int, *, signed: bool = False) -> str:
    """Render a half-millimetre integer as millimetres without inventing precision."""
    if value2 % 2 == 0:
        return f"{value2 // 2:+d}" if signed else str(value2 // 2)
    return f"{value2 / 2:+.1f}" if signed else f"{value2 / 2:.1f}"


#: Which built span a chain's ``extent`` tag says it measures. A chain whose extent is
#: missing or matches neither is not span-checkable and is reported, never assumed correct.
_EXTENT_SPANS = {"overall": "outer", "interior": "inner"}


def _span_kind(chain: Mapping[str, Any]) -> str | None:
    extent = (chain.get("extent") or "").lower()
    for keyword, kind in _EXTENT_SPANS.items():
        if keyword in extent:
            return kind
    return None


def _geometry_failures(spec: Mapping[str, Any]) -> tuple[list[str], int, int]:
    """Returns (failures, checked, unchecked). See :func:`_derive_axis_spans`."""
    failures: list[str] = []
    walls_by_level: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for wall in spec.get("walls", []):
        walls_by_level[wall["level"]].append(wall)

    checked = 0
    unchecked = 0
    for chain in spec.get("dimension_chains", []):
        level_walls = walls_by_level.get(chain["level"], [])
        kind = _span_kind(chain)
        spans = _derive_axis_spans(level_walls, chain["axis"]) if level_walls else None
        if kind is None or spans is None:
            unchecked += 1
            continue

        checked += 1
        built2 = spans[0] if kind == "outer" else spans[1]
        printed2 = 2 * _total_mm(chain)
        if built2 != printed2:
            delta2 = built2 - printed2
            failures.append(
                f"chain {chain['id']!r} ({chain['source_image']}) level {chain['level']!r} "
                f"axis {chain['axis']!r} extent {chain.get('extent')!r}: the walls give an "
                f"{kind} span of {_fmt_halves(built2)} mm ({kind} face to {kind} face of "
                f"the two bounding walls), but the chain's printed total is "
                f"{chain['total_cm']} cm = {_total_mm(chain)} mm — delta "
                f"{_fmt_halves(delta2, signed=True)} mm. A delta equal to one exterior wall "
                f"thickness "
                f"usually means the chain and the walls disagree about faces vs "
                f"centrelines; a delta of ~10x means a scale slip."
            )
    return failures, checked, unchecked


def test_chain_to_geometry_agreement(spec) -> None:
    """The built wall envelope must reproduce the printed overall and interior spans.

    Skips gracefully until T04/T05 land walls, so this test can exist before geometry does.
    Chains that are not span-checkable are counted in a warning rather than being silently
    treated as verified — see :func:`_derive_axis_spans` for what this check gave up and why.
    """
    if not spec["walls"]:
        pytest.skip("spec has dimension_chains but no walls yet (T04/T05)")
    if not spec["dimension_chains"]:
        pytest.skip("spec has no dimension_chains yet (T04/T05)")
    failures, checked, unchecked = _geometry_failures(spec)
    warnings.warn(
        f"chain-to-geometry: span-checked {checked} chain(s) against the built walls; "
        f"{unchecked} chain(s) carry no 'overall'/'interior' extent tag (or their level has "
        f"no walls on that axis yet) and are NOT checked against geometry by this test.",
        UserWarning,
        stacklevel=1,
    )
    assert not failures, "\n".join(failures)


# ========================================================================================
# Synthetic-fixture unit tests. These build their own spec fragments (plain dicts, or a
# full document through the real kotewki.spec.load_spec loader) so the four checks above
# are proven correct NOW, independent of whether spec/ground.json and spec/attic.json have
# been transcribed yet.
# ========================================================================================

# T03's known-good chains (README.md "Units" / tasks/T06.md), tagged with an explicit
# 'overall' extent so _select_overall doesn't have to fall back to the max-total heuristic.
KNOWN_GOOD_BOTTOM: dict[str, Any] = {
    "id": "G_C_BOTTOM",
    "level": "ground",
    "axis": "x",
    "extent": "overall_width",
    "segments_cm": [470, 224, 1016],
    "total_cm": 1710,
    "source_image": "data/source/plan_ground.png",
}
KNOWN_GOOD_LEFT: dict[str, Any] = {
    "id": "G_C_LEFT",
    "level": "ground",
    "axis": "y",
    "extent": "overall_depth",
    "segments_cm": [820, 80],
    "total_cm": 900,
    "source_image": "data/source/plan_ground.png",
}


# ---- 1. closure ------------------------------------------------------------------------


def test_closure_helper_passes_on_known_good_chains() -> None:
    spec = {"dimension_chains": [KNOWN_GOOD_BOTTOM, KNOWN_GOOD_LEFT]}
    assert _closure_failures(spec) == []


def test_closure_helper_flags_off_by_100_with_id_source_and_delta() -> None:
    """Off-by-100 chain: segments still sum to 1710, printed total wrongly reads 1810."""
    bad = dict(KNOWN_GOOD_BOTTOM)
    bad["total_cm"] = 1810
    spec = {"dimension_chains": [bad]}

    failures = _closure_failures(spec)

    assert len(failures) == 1
    message = failures[0]
    assert "G_C_BOTTOM" in message, "must name the chain id"
    assert "data/source/plan_ground.png" in message, "must name the source image"
    assert "1710" in message and "1810" in message, "must name both numbers"
    assert "-100" in message, "must name the delta explicitly"


# --------------------------------------------------------------------------------------
# 2. magnitude sanity — unit tests
# --------------------------------------------------------------------------------------


def test_magnitude_helper_known_good_chains_pass_and_hit_exact_envelope() -> None:
    spec = {"levels": [], "walls": [], "dimension_chains": [KNOWN_GOOD_BOTTOM, KNOWN_GOOD_LEFT]}
    assert _magnitude_failures(spec) == []


def test_magnitude_helper_flags_storey_height_out_of_band() -> None:
    spec = {
        "levels": [{"id": "ground", "name": "PARTER", "ceiling_height": 270}],
        "walls": [],
        "dimension_chains": [],
    }
    failures = _magnitude_failures(spec)
    assert any("ceiling_height" in f and "270" in f for f in failures)


def test_magnitude_helper_flags_wall_thickness_out_of_band() -> None:
    spec = {
        "levels": [],
        "walls": [{"id": "G_W1", "thickness": 30}],
        "dimension_chains": [],
    }
    failures = _magnitude_failures(spec)
    assert any("G_W1" in f and "thickness" in f for f in failures)


def _wall(wall_id: str, thickness: int, length: int, kind: str = "partition") -> dict[str, Any]:
    return {
        "id": wall_id,
        "level": "attic",
        "start": [0, 0],
        "end": [length, 0],
        "thickness": thickness,
        "type": kind,
    }


def test_magnitude_helper_admits_a_balustrade_and_a_chimney_stack() -> None:
    """The three real elements the original [80, 600] band rejected (spec/attic.json).

    A_W7 is a 60 mm guard along the Antresola void; A_W9/A_W10 are 610/620 mm chimney
    stacks. All three are legitimate geometry, corroborated in T05's transcription notes.
    """
    spec = {
        "levels": [],
        "walls": [
            _wall("A_W7", 60, 7120),  # balustrade — thin, but a genuine run
            _wall("A_W9", 610, 680, "structural"),  # chimney stack — a stub
            _wall("A_W10", 620, 875, "structural"),
            _wall("A_W1", 450, 16650, "exterior"),
        ],
        "dimension_chains": [],
    }
    assert _magnitude_failures(spec) == []


def test_magnitude_helper_still_rejects_a_thick_wall_run() -> None:
    """The pier allowance is earned by the aspect ratio, not handed to every wall.

    The same 900 mm thickness passes as a 1000 mm stub (a stack) and fails as an 8550 mm
    run (a wall twice as thick as any exterior build-up).
    """
    run = {"levels": [], "walls": [_wall("A_WX", 900, 8550)], "dimension_chains": []}
    stub = {"levels": [], "walls": [_wall("A_WY", 900, 1000)], "dimension_chains": []}

    failures = _magnitude_failures(run)
    assert any("A_WX" in f and "wall-thickness sanity band" in f for f in failures)
    assert _magnitude_failures(stub) == []


def test_magnitude_helper_rejects_a_pier_ten_times_too_thick() -> None:
    """Even inside the widened pier band, a x10 slip on a stack is still caught."""
    spec = {"levels": [], "walls": [_wall("A_W9", 6100, 680, "structural")], "dimension_chains": []}
    failures = _magnitude_failures(spec)
    assert any("A_W9" in f and "pier/chimney-stack" in f for f in failures)


def test_magnitude_helper_admits_wall_thicknesses_as_chain_segments() -> None:
    """spec/attic.json's A_C1 decomposes the span into walls and rooms alternately.

    45 cm and 12 cm are the exterior wall and the partition. The original [500, 20000] mm
    segment band rejected both, which is a false positive on the chain's whole reason for
    existing — it is what pins the derived 120 mm partition thickness.
    """
    chain = {
        "id": "A_C1",
        "level": "attic",
        "axis": "x",
        "extent": "overall_x",
        "segments_cm": [45, 393, 12, 700, 12, 503, 45],
        "total_cm": 1710,
        "source_image": "data/source/plan_attic.png",
    }
    spec = {"levels": [], "walls": [], "dimension_chains": [chain]}
    assert _closure_failures(spec) == []
    assert _magnitude_failures(spec) == []


def test_magnitude_helper_flags_tiny_segment() -> None:
    """The floor still fires below 5 cm — nothing on these plans is dimensioned thinner."""
    chain = {
        "id": "G_C_TINY",
        "level": "ground",
        "axis": "x",
        "segments_cm": [4, 1706],
        "total_cm": 1710,
        "source_image": "data/source/plan_ground.png",
    }
    spec = {"levels": [], "walls": [], "dimension_chains": [chain]}
    failures = _magnitude_failures(spec)
    assert any("segment 4 cm" in f for f in failures)


def test_magnitude_helper_catches_a_wall_decomposing_chain_scaled_by_a_tenth() -> None:
    """The widened segment floor still catches the x10 case it was widened around.

    A_C1's shape — wall · room · wall · room · wall · room · wall — with the numbers
    rounded so that a tenth stays integral: 50 · 390 · 10 · 700 · 10 · 500 · 50 = 1710 cm.
    Scaled by 1/10 it still closes perfectly (170 + 1 = 171), and the two partition
    segments drop to 1 cm, under the 5 cm floor. So the secondary net still fires on
    precisely the kind of chain that forced the band to be widened in the first place: any
    chain that decomposes a span into walls and spaces carries small segments, and a tenth
    of a small segment is smaller still.
    """
    scaled = {
        "id": "A_C1",
        "level": "attic",
        "axis": "x",
        "extent": "overall_x",
        "segments_cm": [5, 39, 1, 70, 1, 50, 5],
        "total_cm": 171,
        "source_image": "data/source/plan_attic.png",
    }
    spec = {"levels": [], "walls": [], "dimension_chains": [scaled]}
    assert _closure_failures(spec) == [], "closure is scale-invariant by construction"
    failures = _magnitude_failures(spec)
    assert sum("segment 1 cm" in f for f in failures) == 2
    assert any("building-envelope sanity band" in f for f in failures)


def test_magnitude_helper_scaled_by_tenth_still_closes_but_fails_magnitude() -> None:
    """The scale error chain closure structurally cannot catch (README.md 'Units').

    50 + 20 + 100 == 170 closes exactly, but at CM_TO_MM=10 this is a 1700 mm run —
    the same numbers a correctly-scaled 500+200+1000=1700 cm chain would produce if
    every digit were shifted one place, describing a building a tenth the size.
    """
    scaled = {
        "id": "G_C_SCALED",
        "level": "ground",
        "axis": "x",
        "segments_cm": [50, 20, 100],
        "total_cm": 170,
        "source_image": "data/source/plan_ground.png",
    }
    spec = {"levels": [], "walls": [], "dimension_chains": [scaled]}

    assert _closure_failures(spec) == [], "closure is scale-invariant by construction"

    failures = _magnitude_failures(spec)
    assert failures, "a chain scaled 1/10 must be caught by magnitude sanity"
    assert any("G_C_SCALED" in f for f in failures)


def test_full_spec_scaled_by_tenth_fails_magnitude_via_real_loader(tmp_path: Path) -> None:
    """End-to-end: build a real spec through kotewki.spec.load_spec with every chain
    divided by 10, and confirm the magnitude check (not closure) is what catches it."""
    good_bottom = {
        "id": "G_C1",
        "level": "ground",
        "axis": "x",
        "segments_cm": [500, 300, 900],
        "total_cm": 1700,
        "source_image": "data/source/plan_ground.png",
    }
    good_left = {
        "id": "G_C2",
        "level": "ground",
        "axis": "y",
        "segments_cm": [800, 100],
        "total_cm": 900,
        "source_image": "data/source/plan_ground.png",
    }
    scaled_bottom = dict(good_bottom, id="G_C3", segments_cm=[50, 30, 90], total_cm=170)
    scaled_left = dict(good_left, id="G_C4", segments_cm=[80, 10], total_cm=90)

    write_spec(
        tmp_path,
        ground={
            "walls": [],
            "openings": [],
            "rooms": [],
            "dimension_chains": [scaled_bottom, scaled_left],
        },
        attic=minimal_attic(),
    )
    loaded = load_spec(tmp_path)

    assert _closure_failures(loaded) == [], "the scaled spec still closes"
    failures = _magnitude_failures(loaded)
    assert failures, "the whole-spec 1/10 scale error must fail magnitude sanity"


# --------------------------------------------------------------------------------------
# 3. cross-chain consistency — unit tests
# --------------------------------------------------------------------------------------


def test_cross_chain_helper_passes_when_totals_agree() -> None:
    partner = dict(KNOWN_GOOD_BOTTOM, id="G_C_BOTTOM_INNER")
    spec = {"dimension_chains": [KNOWN_GOOD_BOTTOM, partner]}
    failures, checked, total, unpartnered = _cross_chain_failures(spec)
    assert failures == []
    assert checked == 2
    assert total == 2
    assert unpartnered == 0


def test_cross_chain_helper_flags_disagreement() -> None:
    disagreeing = dict(
        KNOWN_GOOD_BOTTOM,
        id="G_C_BOTTOM_2",
        segments_cm=[470, 224, 1006],  # internally closes to 1700, not 1710
        total_cm=1700,
    )
    spec = {"dimension_chains": [KNOWN_GOOD_BOTTOM, disagreeing]}

    failures, checked, total, unpartnered = _cross_chain_failures(spec)

    assert len(failures) == 1
    assert "G_C_BOTTOM" in failures[0] and "G_C_BOTTOM_2" in failures[0]
    assert "1710" in failures[0] and "1700" in failures[0]
    assert checked == 2
    assert unpartnered == 0


def test_cross_chain_helper_reports_unpartnered_chains() -> None:
    """A chain with no extent tag, or a unique extent, is not cross-checked at all —
    and that must be visible in the reported counts, not silently assumed verified."""
    lone = dict(KNOWN_GOOD_LEFT, id="G_C_LONE", extent=None)
    spec = {"dimension_chains": [KNOWN_GOOD_BOTTOM, lone]}

    failures, checked, total, unpartnered = _cross_chain_failures(spec)

    assert failures == []
    assert total == 2
    assert checked == 0, "neither chain has a partner sharing (level, axis, extent)"
    assert unpartnered == 2


def test_cross_chain_consistency_warns_with_the_verified_count() -> None:
    """The real test function must surface how many chains it actually verified."""
    partner = dict(KNOWN_GOOD_BOTTOM, id="G_C_BOTTOM_INNER")
    fake_spec = {"dimension_chains": [KNOWN_GOOD_BOTTOM, partner, KNOWN_GOOD_LEFT]}

    with pytest.warns(UserWarning, match=r"verified 2/3"):
        test_cross_chain_consistency(fake_spec)


# --------------------------------------------------------------------------------------
# 4. chain-to-geometry agreement — unit tests
# --------------------------------------------------------------------------------------


def _rect_walls_for_chain(x_positions: list[int]) -> list[dict[str, Any]]:
    """Vertical walls at the given CENTRELINE x coordinates, outermost two 250 mm thick."""
    return [
        {
            "id": f"G_W{i}",
            "level": "ground",
            "start": [x, 0],
            "end": [x, 3000],
            "thickness": 250 if i in (0, len(x_positions) - 1) else 115,
            "type": "exterior" if i in (0, len(x_positions) - 1) else "partition",
        }
        for i, x in enumerate(x_positions)
    ]


#: 250 mm exterior walls whose OUTER faces are 0 and 7000, i.e. centrelines at 125 and 6875,
#: with a 115 mm partition between them. A printed overall chain measures 7000; a printed
#: interior chain measures 7000 - 2 x 250 = 6500.
FACE_CONSISTENT_X = [125, 3000, 6875]


def test_geometry_helper_matches_the_printed_overall_span() -> None:
    chain = {
        "id": "G_C1",
        "level": "ground",
        "axis": "x",
        "extent": "overall_width",
        "segments_cm": [300, 400],
        "total_cm": 700,
        "source_image": "data/source/plan_ground.png",
    }
    spec = {"walls": _rect_walls_for_chain(FACE_CONSISTENT_X), "dimension_chains": [chain]}
    failures, checked, unchecked = _geometry_failures(spec)
    assert failures == []
    assert (checked, unchecked) == (1, 0)


def test_geometry_helper_matches_the_printed_interior_span() -> None:
    """The same walls also have to reproduce a printed INTERIOR run, 500 mm narrower.

    This is what makes the attic's A_C5/A_C6 (total 810 cm, the printed interior clear
    depth) checkable at all: they measure a different pair of faces from A_C3/A_C4's 900.
    """
    chain = {
        "id": "G_C2",
        "level": "ground",
        "axis": "x",
        "extent": "interior_width",
        "segments_cm": [650],
        "total_cm": 650,
        "source_image": "data/source/plan_ground.png",
    }
    spec = {"walls": _rect_walls_for_chain(FACE_CONSISTENT_X), "dimension_chains": [chain]}
    failures, checked, _ = _geometry_failures(spec)
    assert failures == []
    assert checked == 1


def test_geometry_helper_flags_a_centreline_vs_face_confusion() -> None:
    """The exact failure mode that made this check fire on all six real attic chains.

    Walls placed on the printed dimension as CENTRELINES rather than faces put the outer
    faces at -125 and 7125, so the built envelope is 7250 against a printed 7000.
    """
    chain = {
        "id": "G_C1",
        "level": "ground",
        "axis": "x",
        "extent": "overall_width",
        "segments_cm": [300, 400],
        "total_cm": 700,
        "source_image": "data/source/plan_ground.png",
    }
    spec = {"walls": _rect_walls_for_chain([0, 3000, 7000]), "dimension_chains": [chain]}

    failures, checked, _ = _geometry_failures(spec)

    assert checked == 1
    assert len(failures) == 1
    assert "G_C1" in failures[0]
    assert "7250" in failures[0] and "7000" in failures[0]
    assert "+250" in failures[0]


def test_geometry_helper_survives_an_odd_wall_thickness() -> None:
    """465 mm walls halve to 232.5: the check stays exact rather than rounding to 232."""
    walls = [
        {
            "id": "G_W0",
            "level": "ground",
            "start": [2325, 0],
            "end": [2325, 3000],
            "thickness": 465,
            "type": "exterior",
        },
        {
            "id": "G_W1",
            "level": "ground",
            "start": [69675, 0],
            "end": [69675, 3000],
            "thickness": 465,
            "type": "exterior",
        },
    ]
    # Outer faces at 2092.5 and 69907.5 -> a 67815 mm span, which is a whole number of mm
    # only because the two half-thicknesses cancel. Chains are integer cm, so use 6781 cm
    # and expect the 5 mm delta to be reported at half-millimetre resolution.
    chain = {
        "id": "G_C1",
        "level": "ground",
        "axis": "x",
        "extent": "overall_width",
        "segments_cm": [6781],
        "total_cm": 6781,
        "source_image": "data/source/plan_ground.png",
    }
    failures, checked, _ = _geometry_failures({"walls": walls, "dimension_chains": [chain]})
    assert checked == 1
    assert len(failures) == 1
    assert "67815" in failures[0] and "+5" in failures[0]


def test_geometry_helper_reports_chains_it_cannot_span_check() -> None:
    """A chain with no overall/interior extent tag is counted, never assumed verified."""
    chain = {
        "id": "G_C9",
        "level": "ground",
        "axis": "x",
        "segments_cm": [300, 400],
        "total_cm": 700,
        "source_image": "data/source/plan_ground.png",
    }
    spec = {"walls": _rect_walls_for_chain(FACE_CONSISTENT_X), "dimension_chains": [chain]}
    failures, checked, unchecked = _geometry_failures(spec)
    assert failures == []
    assert (checked, unchecked) == (0, 1)


def test_chain_to_geometry_agreement_warns_with_the_checked_count() -> None:
    chain = {
        "id": "G_C1",
        "level": "ground",
        "axis": "x",
        "extent": "overall_width",
        "segments_cm": [300, 400],
        "total_cm": 700,
        "source_image": "data/source/plan_ground.png",
    }
    untagged = dict(chain, id="G_C9", extent=None)
    fake_spec = {
        "walls": _rect_walls_for_chain(FACE_CONSISTENT_X),
        "dimension_chains": [chain, untagged],
    }
    with pytest.warns(UserWarning, match=r"span-checked 1 chain"):
        test_chain_to_geometry_agreement(fake_spec)


def test_chain_to_geometry_agreement_skips_without_walls() -> None:
    fake_spec = {"walls": [], "dimension_chains": [KNOWN_GOOD_BOTTOM]}
    with pytest.raises(pytest.skip.Exception, match="no walls"):
        test_chain_to_geometry_agreement(fake_spec)


def test_chain_to_geometry_agreement_skips_without_chains() -> None:
    fake_spec = {
        "walls": _rect_walls_for_chain([0, 3000, 7000]),
        "dimension_chains": [],
    }
    with pytest.raises(pytest.skip.Exception, match="no dimension_chains"):
        test_chain_to_geometry_agreement(fake_spec)


def test_chain_closure_skips_without_chains() -> None:
    with pytest.raises(pytest.skip.Exception, match="dimension_chains"):
        test_chain_closure({"dimension_chains": []})


def test_cross_chain_consistency_skips_without_chains() -> None:
    with pytest.raises(pytest.skip.Exception, match="dimension_chains"):
        test_cross_chain_consistency({"dimension_chains": []})


# --------------------------------------------------------------------------------------
# Full-loader integration fixtures (mirrors tests/test_schema.py's pattern, kept local and
# minimal so this file has no import-time dependency on another task's test module).
# --------------------------------------------------------------------------------------


def minimal_meta() -> dict[str, Any]:
    return {
        "meta": {
            "schema_version": "1.0.0",
            "source_url": "https://example.invalid/projekt",
            "variant": "E",
            "transcribed_by": "test_chains",
            "date": "2026-08-30",
        },
        "levels": [
            {"id": "ground", "name": "PARTER", "elevation": 0, "ceiling_height": 2700},
            {"id": "attic", "name": "PODDASZE", "elevation": 3040, "ceiling_height": 2730},
        ],
        "construction": {
            "exterior_wall": {
                "thickness": 465,
                "layers": [
                    {"material": "Porotherm 25", "thickness": 250},
                    {"material": "EPS", "thickness": 200},
                    {"material": "tynk", "thickness": 15},
                ],
            },
            "ceiling": {
                "thickness": 340,
                "layers": [{"material": "żelbet", "thickness": 340}],
            },
            "knee_wall_height": 290,
            "finish_allowance": 20,
        },
        "section_elevations": {
            "terrain": -320,
            "ground_floor": 0,
            "eave_fascia_underside": 2880,
            "attic_floor": 3040,
            "ridge": 6770,
            "source_image": "data/source/section.png",
        },
        "roof": {"type": "gable", "pitch_deg": 35.0, "eaves_overhang": 600},
    }


def minimal_attic() -> dict[str, Any]:
    return {"walls": [], "openings": [], "rooms": [], "dimension_chains": []}


def write_spec(
    directory: Path,
    *,
    meta: dict[str, Any] | None = None,
    ground: dict[str, Any] | None = None,
    attic: dict[str, Any] | None = None,
) -> Path:
    for name, document in (("meta", meta or minimal_meta()), ("ground", ground), ("attic", attic)):
        if document is not None:
            (directory / f"{name}.json").write_text(
                json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    return directory


def test_known_good_chains_pass_all_four_checks_through_real_loader(tmp_path: Path) -> None:
    """Integration test: a spec built with T03's known-good bottom/left chains, loaded
    through the real kotewki.spec.load_spec, must pass closure, magnitude and cross-chain
    consistency outright (no partner chains here, so cross-chain has nothing to disagree
    on), and chain-to-geometry must skip cleanly since this fixture carries no walls."""
    write_spec(
        tmp_path,
        ground={
            "walls": [],
            "openings": [],
            "rooms": [],
            "dimension_chains": [KNOWN_GOOD_BOTTOM, KNOWN_GOOD_LEFT],
        },
        attic=minimal_attic(),
    )
    loaded = load_spec(tmp_path)

    assert _closure_failures(loaded) == []
    assert _magnitude_failures(loaded) == []
    failures, checked, total, unpartnered = _cross_chain_failures(loaded)
    assert failures == []
    assert total == 2 and checked == 0 and unpartnered == 2  # each extent is unique here

    with pytest.raises(pytest.skip.Exception, match="no walls"):
        test_chain_to_geometry_agreement(loaded)

"""T10 — Test 4: topology & sanity.

Cheap assertions that catch structurally impossible geometry (TESTS.md #4). These do
**not** check correctness against the source plans — that is T08 (room areas) and T13
(overlay). They check that the model is a coherent *building* at all: a closed wall
network, non-overlapping rooms that tile the envelope, openings that fit their walls, a
spec that matches the published room table in both directions, and a building that is
actually walkable from its own front door.

Every check below is implemented as a pure ``_xxx_failures(...)`` helper that takes plain
data (dicts, shapely geometries, lists of records) rather than a built `Model`, mirroring
`tests/test_chains.py`'s pattern. That is what makes each check unit-testable against
synthetic broken geometry (see the bottom half of this file) independently of whether the
real spec currently exhibits the failure. The real-spec-facing `test_*` functions are then
thin adapters that pull the right shape of data out of the `spec`/`model`/`published`
fixtures and hand it to the helper.

WHY ROOM POLYGONS CAN LEGITIMATELY BE IDENTICAL (read before "fixing" the overlap check)
------------------------------------------------------------------------------------
Hol (2), Salon (6), Hol (7) and Kuchnia (14) are one continuous open-plan space on the
ground floor: T07's wall network returns a *single* polygonised face for all four rooms.
That is not an overlap bug — the four room ids share the literal same polygon. The overlap
check groups rooms by polygon identity (symmetric-difference area ~0) before comparing
pairs, so members of the same group are never flagged against each other, and the
union-vs-footprint check counts that shared face once, not four times. See README.md
"Known area-check limitations".
"""

from __future__ import annotations

import json
import math
import warnings
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from kotewki.geometry import build_model, to_m
from kotewki.spec import load_spec

REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------------------
# Tolerances. Kept in one place and named so a failure message can explain *why* a value
# is flagged, not just that it is.
# --------------------------------------------------------------------------------------

#: Wall thickness floor, millimetres. A_W7 (60 mm Antresola balustrade) is real and must
#: pass; the floor is deliberately NOT raised to 80 -- see the module docstring in
#: kotewki.geometry and tasks/T10.md.
WALL_MIN_THICKNESS_MM = 50

#: Two wall centrelines whose endpoints match within this many millimetres, in either
#: pairing order, are the same wall transcribed twice.
DUPLICATE_WALL_TOL_MM = 10.0

#: A junction point within this many millimetres of a host wall's OWN endpoints is a
#: corner join, not a T-junction along its length, and within this distance of the
#: centreline counts as "touching" it.
JUNCTION_TOL_MM = 1.0

#: Two room polygons within this symmetric-difference area are the same wall-network face
#: (open-plan rooms), not two distinct rooms that happen to coincide.
SAME_FACE_TOL_M2 = 1e-9

#: Two DIFFERENT rooms overlapping by at least this much is a real defect.
ROOM_OVERLAP_TOL_M2 = 1e-6

#: Union(rooms + walls) vs the envelope: mitred corners leave slivers, so this is not
#: exact-zero. tasks/T10.md sets ~0.05 m2; a growing residual across runs is the signal
#: that wall joining is degrading, which is why the real-spec test always reports the
#: actual number via a warning rather than only asserting the bound.
UNION_FOOTPRINT_TOL_M2 = 0.05


# ========================================================================================
# 1. Wall network -- length/thickness floor, duplicates, closed loop, no overshoot
# ========================================================================================


def _short_or_zero_wall_failures(walls: Iterable[Mapping[str, Any]]) -> list[str]:
    """Zero-length centrelines and sub-50 mm THICKNESS walls.

    Thickness, not length: A_W7 is a genuine 60 mm-thick balustrade 7.12 m long, and it
    must pass. A wall thinner than 50 mm is almost always a transcription slip (a
    centimetre value typed as millimetres, or a digit dropped).
    """
    failures = []
    for wall in walls:
        length = math.dist(wall["start"], wall["end"])
        if length <= 0:
            failures.append(
                f"wall {wall['id']!r}: zero-length centreline (start == end == "
                f"{tuple(wall['start'])})."
            )
        thickness = wall["thickness"]
        if thickness < WALL_MIN_THICKNESS_MM:
            failures.append(
                f"wall {wall['id']!r}: thickness {thickness} mm is below the "
                f"{WALL_MIN_THICKNESS_MM} mm sanity floor."
            )
    return failures


def _duplicate_wall_failures(
    walls: Sequence[Mapping[str, Any]], tol_mm: float = DUPLICATE_WALL_TOL_MM
) -> list[str]:
    """Two walls on the same level whose centrelines match within ``tol_mm``, either end
    matched to either end -- the same physical wall transcribed twice."""

    def _close(a: Sequence[float], b: Sequence[float]) -> bool:
        return math.dist(a, b) <= tol_mm

    failures = []
    by_level: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for wall in walls:
        by_level[wall["level"]].append(wall)

    for level_walls in by_level.values():
        for i, a in enumerate(level_walls):
            for b in level_walls[i + 1 :]:
                same_order = _close(a["start"], b["start"]) and _close(a["end"], b["end"])
                reversed_order = _close(a["start"], b["end"]) and _close(a["end"], b["start"])
                if same_order or reversed_order:
                    failures.append(
                        f"walls {a['id']!r} and {b['id']!r} on level {a['level']!r} have "
                        f"near-identical centrelines ({a['start']}-{a['end']} vs "
                        f"{b['start']}-{b['end']}, within {tol_mm} mm) -- likely the same "
                        f"wall transcribed twice."
                    )
    return failures


def _wall_network_failures(level_id: str, network: Any) -> tuple[list[str], float]:
    """Closed-loop and no-overshoot checks against a built ``WallNetwork``.

    Returns ``(failures, overshoot_area_m2)``. The overshoot area is returned even when
    zero so a caller can report it unconditionally.
    """
    failures: list[str] = []
    solid = network.solid()
    envelope = network.envelope()

    if envelope.is_empty:
        failures.append(
            f"level {level_id!r}: the wall network's filled envelope is empty -- the "
            f"exterior loop never closes at all."
        )
        return failures, 0.0

    parts = list(getattr(envelope, "geoms", (envelope,)))
    if len(parts) != 1:
        failures.append(
            f"level {level_id!r}: the exterior envelope has {len(parts)} disconnected "
            f"parts, not one closed ring -- the wall network has a gap or a disconnected "
            f"group of walls."
        )

    void_area = envelope.area - solid.area
    if void_area <= 1e-6:
        failures.append(
            f"level {level_id!r}: the wall network encloses no interior space at all "
            f"(envelope {envelope.area:.4f} m2 <= wall footprint {solid.area:.4f} m2) -- "
            f"the exterior loop has a gap somewhere and never closes around a void."
        )

    overshoot_geom = solid.difference(envelope)
    overshoot_area = overshoot_geom.area
    if overshoot_area > 1e-9:
        offending = sorted(
            wall.id
            for wall in network.walls
            if wall.footprint().intersection(overshoot_geom).area > 1e-9
        )
        failures.append(
            f"level {level_id!r}: wall solids extend {overshoot_area:.6f} m2 outside the "
            f"exterior envelope -- overshooting wall id(s): {offending}. A partition "
            f"meeting an exterior wall must terminate at it, not protrude through it."
        )

    return failures, overshoot_area


# ========================================================================================
# 2. Room polygons -- validity, non-overlap, union-vs-footprint
# ========================================================================================


def _invalid_polygon_failures(
    records: Iterable[tuple[str, str, BaseGeometry]],
) -> list[str]:
    """``records``: (room_id, name, polygon)."""
    failures = []
    for room_id, name, polygon in records:
        if polygon is None or polygon.is_empty:
            failures.append(f"room {room_id!r} ({name!r}): polygon is empty.")
            continue
        if polygon.geom_type != "Polygon":
            failures.append(
                f"room {room_id!r} ({name!r}): resolved to a {polygon.geom_type}, not a "
                f"single simple Polygon -- likely self-intersecting."
            )
            continue
        if not polygon.is_valid:
            failures.append(
                f"room {room_id!r} ({name!r}): polygon is not valid (self-intersecting or "
                f"degenerate rings)."
            )
    return failures


def _room_overlap_failures(
    records: Iterable[tuple[str, str, str, BaseGeometry]],
    tol_m2: float = ROOM_OVERLAP_TOL_M2,
) -> list[str]:
    """``records``: (room_id, name, level, polygon).

    Rooms whose polygon is identical to within :data:`SAME_FACE_TOL_M2` are grouped first
    and never compared to each other -- see the module docstring for why that is a
    legitimate open-plan space, not an overlap bug.
    """
    failures: list[str] = []
    by_level: dict[str, list[tuple[str, str, BaseGeometry]]] = defaultdict(list)
    for room_id, name, level, polygon in records:
        by_level[level].append((room_id, name, polygon))

    for level, items in by_level.items():
        groups: list[list[tuple[str, str, BaseGeometry]]] = []
        for room_id, name, polygon in items:
            for group in groups:
                if group[0][2].symmetric_difference(polygon).area < SAME_FACE_TOL_M2:
                    group.append((room_id, name, polygon))
                    break
            else:
                groups.append([(room_id, name, polygon)])

        for i, group_a in enumerate(groups):
            for group_b in groups[i + 1 :]:
                for a_id, a_name, a_poly in group_a:
                    for b_id, b_name, b_poly in group_b:
                        overlap = a_poly.intersection(b_poly).area
                        if overlap >= tol_m2:
                            failures.append(
                                f"rooms {a_id!r} ({a_name!r}) and {b_id!r} ({b_name!r}) on "
                                f"level {level!r} overlap by {overlap:.6f} m2."
                            )
    return failures


def _union_vs_footprint_residual(
    room_polygons: Sequence[BaseGeometry], wall_solid: BaseGeometry, envelope: BaseGeometry
) -> float:
    """Area of the symmetric difference between (rooms + walls) and the envelope, m2.

    Zero means every square metre of the envelope is accounted for by exactly one room or
    the wall material occupying it, and nothing sticks out. Duplicate (open-plan) room
    polygons must be deduplicated by the caller before this is called, or they inflate the
    union with a redundant term that happens to be idempotent under ``unary_union`` anyway
    -- but callers should still dedupe so the room-count bookkeeping stays honest.
    """
    covered = unary_union([*room_polygons, wall_solid])
    return covered.symmetric_difference(envelope).area


def _dedupe_polygons(polygons: Iterable[BaseGeometry]) -> list[BaseGeometry]:
    out: list[BaseGeometry] = []
    for polygon in polygons:
        if not any(polygon.symmetric_difference(seen).area < SAME_FACE_TOL_M2 for seen in out):
            out.append(polygon)
    return out


def _slab_opening_polygons(slab_openings: Iterable[Mapping[str, Any]]) -> list[BaseGeometry]:
    """``bounds`` -> a shapely box in metres, one per slab opening.

    Both ``kind`` values ("void" and "stairwell") belong here: both mean "the slab is
    absent here", so both are legitimate floor-area coverage for the union-vs-footprint
    check, same as a room or a wall. Only ``"stairwell"`` additionally carries
    ``connects_levels`` and participates in the connectivity graph -- see
    :func:`_stairwell_bridge_edges`.
    """
    polygons = []
    for opening in slab_openings:
        x0, y0, x1, y1 = opening["bounds"]
        polygons.append(box(to_m(x0), to_m(y0), to_m(x1), to_m(y1)))
    return polygons


# ========================================================================================
# 3. Openings
# ========================================================================================


def _opening_wall_resolves_failures(
    openings: Iterable[Mapping[str, Any]], wall_ids: set
) -> list[str]:
    return [
        f"opening {o['id']!r} references wall {o['wall']!r}, which does not exist."
        for o in openings
        if o["wall"] not in wall_ids
    ]


def _opening_fit_failures(
    openings: Iterable[Mapping[str, Any]],
    wall_lengths: Mapping[str, float],
    tol_mm: float = 1e-6,
) -> list[str]:
    failures = []
    for o in openings:
        length = wall_lengths.get(o["wall"])
        if length is None:
            continue  # dangling reference; reported by _opening_wall_resolves_failures
        end = o["offset"] + o["width"]
        if end > length + tol_mm:
            failures.append(
                f"opening {o['id']!r} on wall {o['wall']!r}: offset {o['offset']} + width "
                f"{o['width']} = {end} mm exceeds the wall's length {length:.1f} mm by "
                f"{end - length:.1f} mm."
            )
    return failures


def _opening_overlap_failures(openings: Iterable[Mapping[str, Any]]) -> list[str]:
    failures = []
    by_wall: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for o in openings:
        by_wall[o["wall"]].append(o)
    for wall_id, group in by_wall.items():
        ordered = sorted(group, key=lambda o: o["offset"])
        for a, b in zip(ordered, ordered[1:]):
            a_end = a["offset"] + a["width"]
            if b["offset"] < a_end:
                failures.append(
                    f"openings {a['id']!r} ({a['offset']}-{a_end} mm) and {b['id']!r} "
                    f"({b['offset']}-{b['offset'] + b['width']} mm) on wall {wall_id!r} "
                    f"overlap."
                )
    return failures


def _opening_height_failures(
    openings: Iterable[Mapping[str, Any]], ceiling_height_by_wall: Mapping[str, int]
) -> list[str]:
    failures = []
    for o in openings:
        ceiling = ceiling_height_by_wall.get(o["wall"])
        if ceiling is None:
            continue
        head = o["sill"] + o["height"]
        if head > ceiling:
            failures.append(
                f"opening {o['id']!r} on wall {o['wall']!r}: sill {o['sill']} + height "
                f"{o['height']} = {head} mm exceeds the ceiling height {ceiling} mm by "
                f"{head - ceiling} mm."
            )
    return failures


def _wall_junction_offsets(
    walls: Sequence[Mapping[str, Any]], tol_mm: float = JUNCTION_TOL_MM
) -> dict[str, list[float]]:
    """For each wall, offsets (mm from its ``start``) where another wall's endpoint
    touches its centreline STRICTLY inside it -- a T-junction along its length, as
    opposed to a corner join at its own start/end."""
    by_level: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for wall in walls:
        by_level[wall["level"]].append(wall)

    out: dict[str, list[float]] = defaultdict(list)
    for level_walls in by_level.values():
        for host in level_walls:
            sx, sy = host["start"]
            ex, ey = host["end"]
            length = math.dist((sx, sy), (ex, ey))
            if length == 0:
                continue
            dx, dy = (ex - sx) / length, (ey - sy) / length
            for other in level_walls:
                if other["id"] == host["id"]:
                    continue
                for px, py in (other["start"], other["end"]):
                    t = (px - sx) * dx + (py - sy) * dy
                    if t <= tol_mm or t >= length - tol_mm:
                        continue  # at or beyond the host's OWN ends: a corner join
                    proj = (sx + dx * t, sy + dy * t)
                    if math.dist((px, py), proj) <= tol_mm:
                        out[host["id"]].append(t)
    return out


def _opening_junction_straddle_failures(
    openings: Iterable[Mapping[str, Any]], junctions: Mapping[str, Sequence[float]]
) -> list[str]:
    failures = []
    for o in openings:
        lo, hi = o["offset"], o["offset"] + o["width"]
        for t in junctions.get(o["wall"], ()):
            if lo < t < hi:
                failures.append(
                    f"opening {o['id']!r} on wall {o['wall']!r} spans {lo}-{hi} mm and "
                    f"straddles a wall junction at {t:.1f} mm, where another wall meets "
                    f"{o['wall']!r} partway along its length."
                )
    return failures


# ========================================================================================
# 4. Completeness against the published table
# ========================================================================================


def _completeness_failures(
    spec_rooms: Iterable[Mapping[str, Any]],
    published_rooms_by_level: Mapping[str, Iterable[Mapping[str, Any]]],
) -> list[str]:
    """Both directions, matched on ``(level, published_id)`` -- never on name, because
    names are not unique ('Pokój 11.99' x2, 'Strych ocieplony' x2)."""
    spec_keys = {(r["level"], r["published_id"]): r["name"] for r in spec_rooms}
    published_keys: dict[tuple[str, int], str] = {}
    for level, rooms in published_rooms_by_level.items():
        for r in rooms:
            published_keys[(level, r["id"])] = r["name"]

    failures = []
    for (level, published_id), name in published_keys.items():
        if (level, published_id) not in spec_keys:
            failures.append(
                f"published room {published_id} ({name!r}) on level {level!r} has no "
                f"matching room in the spec (no room with that published_id) -- a silently "
                f"dropped room."
            )
    for (level, published_id), name in spec_keys.items():
        if (level, published_id) not in published_keys:
            failures.append(
                f"spec room {name!r} (published_id {published_id}) on level {level!r} does "
                f"not exist in data/published.json -- an invented room."
            )
    return failures


# ========================================================================================
# 5. Connectivity
# ========================================================================================


def _unreachable_nodes(nodes: set[str], edges: Iterable[tuple[str, str]], start: str) -> set[str]:
    """Plain BFS reachability. Pure graph logic, independent of how the graph was built."""
    graph: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        graph[a].add(b)
        graph[b].add(a)
    seen = {start}
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        for neighbour in graph.get(current, ()):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return nodes - seen


def _stairwell_bridge_edges(
    slab_openings: Iterable[Mapping[str, Any]],
    polygons_by_level: Mapping[str, Mapping[str, BaseGeometry]],
    tol_m2: float = SAME_FACE_TOL_M2,
) -> list[tuple[str, str]]:
    """Vertical circulation: rooms bridged by a ``kind == "stairwell"`` slab opening.

    Every room -- on ANY level named in that opening's ``connects_levels`` -- whose
    polygon actually intersects the opening's ``bounds`` is mutually connected to every
    other room that does too, exactly as if the stair void itself were a shared face. A
    ``kind == "void"`` opening (an intentional double-height gap with no stair, e.g. the
    Antresola's "Pustka nad salonem") carries no ``connects_levels`` and produces no edge
    here -- it is floor-area accounting only, see :func:`_union_vs_footprint_residual`.
    Deliberately geometric rather than id-based: a room is bridged only if its polygon
    actually reaches the opening, so a room merely adjacent to the stairwell (separated by
    a real wall, no matter how thin) is correctly NOT bridged. That is precisely what keeps
    this check honest -- see the real-spec test's docstring for what it finds.
    """
    edges: list[tuple[str, str]] = []
    for opening in slab_openings:
        if opening.get("kind") != "stairwell":
            continue
        x0, y0, x1, y1 = opening["bounds"]
        opening_box = box(to_m(x0), to_m(y0), to_m(x1), to_m(y1))
        touching = [
            room_id
            for level_id in opening.get("connects_levels", ())
            for room_id, polygon in polygons_by_level.get(level_id, {}).items()
            if polygon.intersection(opening_box).area > tol_m2
        ]
        for i, a in enumerate(touching):
            for b in touching[i + 1 :]:
                edges.append((a, b))
    return edges


def _connectivity_graph(
    model: Any,
    openings: Iterable[Mapping[str, Any]],
    slab_openings: Iterable[Mapping[str, Any]] = (),
) -> tuple[set[str], list[tuple[str, str]], dict[str, str]]:
    """Build the room-reachability graph from the built model's geometry.

    Nodes are room ids plus a synthetic ``"OUTSIDE"`` node. Edges come from three sources:
    every pair of rooms sharing one wall-network face (open-plan rooms are always mutually
    reachable with no door needed); every ``door``/``passage`` opening, resolved to the
    room(s) it actually touches by probing just off the wall face at the opening's own
    position -- not by trusting the whole host wall's boundary list, because a single wall
    can border two DIFFERENT rooms along different stretches of its length (e.g. the
    attic's A_W5 borders both Antresola and Strych ocieplony over different y-ranges); and
    every ``kind == "stairwell"`` ``slab_openings`` entry, via :func:`_stairwell_bridge_edges`.
    """
    nodes: set[str] = {"OUTSIDE"}
    labels: dict[str, str] = {"OUTSIDE": "OUTSIDE"}
    edges: list[tuple[str, str]] = []

    polygons_by_level: dict[str, dict[str, BaseGeometry]] = {}
    for level in model.levels.values():
        polygons: dict[str, BaseGeometry] = {}
        for room in level.rooms:
            polygon = room.polygon()
            polygons[room.id] = polygon
            nodes.add(room.id)
            labels[room.id] = f"{room.name} ({room.id})"
        polygons_by_level[level.id] = polygons

        items = list(polygons.items())
        for i, (id_a, poly_a) in enumerate(items):
            for id_b, poly_b in items[i + 1 :]:
                if poly_a.symmetric_difference(poly_b).area < SAME_FACE_TOL_M2:
                    edges.append((id_a, id_b))

    wall_solid_by_id: dict[str, Any] = {}
    for level in model.levels.values():
        wall_solid_by_id.update(level.network.wall_by_id)

    for opening in openings:
        if opening["kind"] not in ("door", "passage"):
            continue
        wall = wall_solid_by_id.get(opening["wall"])
        if wall is None:
            continue  # dangling reference; reported elsewhere
        level = model.level(wall.level)
        t_m = to_m(opening["offset"] + opening["width"] / 2)
        cx = wall.start[0] + wall.direction[0] * t_m
        cy = wall.start[1] + wall.direction[1] * t_m
        nx, ny = wall.normal
        reach = wall.half_thickness_m + 0.05
        envelope = level.network.envelope()
        sides: list[str | None] = []
        for sign in (1, -1):
            probe = Point(cx + nx * reach * sign, cy + ny * reach * sign)
            hit = next(
                (
                    rid
                    for rid, poly in polygons_by_level[wall.level].items()
                    if poly.contains(probe)
                ),
                None,
            )
            if hit is None and not envelope.contains(probe):
                hit = "OUTSIDE"
            sides.append(hit)
        if sides[0] is not None and sides[1] is not None and sides[0] != sides[1]:
            edges.append((sides[0], sides[1]))

    edges.extend(_stairwell_bridge_edges(slab_openings, polygons_by_level))

    return nodes, edges, labels


# ========================================================================================
# Real-spec-facing tests
# ========================================================================================

# -- wall network --------------------------------------------------------------------


def test_no_short_or_zero_length_walls(spec) -> None:
    failures = _short_or_zero_wall_failures(spec["walls"])
    assert not failures, "\n".join(failures)


def test_no_duplicate_walls(spec) -> None:
    failures = _duplicate_wall_failures(spec["walls"])
    assert not failures, "\n".join(failures)


def test_wall_network_closes_with_no_overshoot(model) -> None:
    all_failures: list[str] = []
    for level_id, level in model.levels.items():
        failures, overshoot = _wall_network_failures(level_id, level.network)
        all_failures.extend(failures)
        warnings.warn(
            f"level {level_id!r}: wall-solid-outside-envelope overshoot = "
            f"{overshoot:.6f} m2.",
            UserWarning,
            stacklevel=1,
        )
    assert not all_failures, "\n".join(all_failures)


def test_oblique_wall_ids_is_empty(model) -> None:
    """The junction-extension rule is exact only at right angles (see kotewki.geometry's
    module docstring); this building is axis-aligned throughout, and this turns that
    assumption into a check rather than a silent trust."""
    for level_id, level in model.levels.items():
        assert level.network.oblique_wall_ids == (), (
            f"level {level_id!r} has oblique (non-axis-aligned) wall(s): "
            f"{level.network.oblique_wall_ids} -- the corner-closing rule is not exact "
            f"for these."
        )


# -- room polygons ---------------------------------------------------------------------


def test_room_polygons_are_valid(model) -> None:
    records = [(room.id, room.name, room.polygon()) for room in model.rooms]
    failures = _invalid_polygon_failures(records)
    assert not failures, "\n".join(failures)


def test_rooms_do_not_overlap(model) -> None:
    records = [
        (room.id, room.name, room.room.level, room.polygon()) for room in model.rooms
    ]
    failures = _room_overlap_failures(records)
    assert not failures, "\n".join(failures)


def test_union_of_rooms_and_walls_equals_footprint(model, spec) -> None:
    """Union of rooms + walls + slab openings must tile the exterior footprint.

    ``spec["slab_openings"]`` (schema addition following this check's own attic finding --
    see A_SO1/A_SO2 in spec/attic.json) supplies the regions where the floor slab is
    deliberately absent: the "Pustka nad salonem" double-height void and the stairwell.
    Both count as accounted-for floor-plan coverage, same as a room or a wall, so they go
    into the union alongside the rooms. If a residual survives after that, it is a REAL
    unaccounted gap and the tolerance is deliberately not widened to hide it.
    """
    slab_openings_by_level: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for opening in spec["slab_openings"]:
        slab_openings_by_level[opening["level"]].append(opening)

    failures = []
    for level_id, level in model.levels.items():
        room_polygons = _dedupe_polygons(room.polygon() for room in level.rooms)
        slab_polygons = _slab_opening_polygons(slab_openings_by_level.get(level_id, ()))
        covered_regions = room_polygons + slab_polygons
        if not covered_regions:
            continue  # nothing transcribed yet on this level
        residual = _union_vs_footprint_residual(
            covered_regions, level.network.solid(), level.network.envelope()
        )
        warnings.warn(
            f"level {level_id!r}: union(rooms + walls + slab_openings) vs footprint "
            f"residual = {residual:.6f} m2 (tolerance {UNION_FOOTPRINT_TOL_M2} m2).",
            UserWarning,
            stacklevel=1,
        )
        if residual > UNION_FOOTPRINT_TOL_M2:
            failures.append(
                f"level {level_id!r}: union(rooms + walls + slab_openings) differs from "
                f"the exterior footprint by {residual:.4f} m2, over the "
                f"{UNION_FOOTPRINT_TOL_M2} m2 tolerance -- either a room bleeds outside "
                f"the envelope, a slab opening is mis-transcribed, or some floor area is "
                f"still unaccounted for."
            )
    assert not failures, "\n".join(failures)


# -- openings ----------------------------------------------------------------------------


def _wall_lengths_mm(walls: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    return {w["id"]: math.dist(w["start"], w["end"]) for w in walls}


def _ceiling_height_by_wall(spec) -> dict[str, int]:
    level_ceiling = {lvl["id"]: lvl["ceiling_height"] for lvl in spec["levels"]}
    return {
        w["id"]: w["height"] if w.get("height") is not None else level_ceiling[w["level"]]
        for w in spec["walls"]
    }


def test_opening_walls_resolve(spec) -> None:
    wall_ids = {w["id"] for w in spec["walls"]}
    failures = _opening_wall_resolves_failures(spec["openings"], wall_ids)
    assert not failures, "\n".join(failures)


def test_opening_offsets_fit_within_their_walls(spec) -> None:
    failures = _opening_fit_failures(spec["openings"], _wall_lengths_mm(spec["walls"]))
    assert not failures, "\n".join(failures)


def test_openings_on_the_same_wall_do_not_overlap(spec) -> None:
    failures = _opening_overlap_failures(spec["openings"])
    assert not failures, "\n".join(failures)


def test_opening_heads_fit_under_the_ceiling(spec) -> None:
    failures = _opening_height_failures(spec["openings"], _ceiling_height_by_wall(spec))
    assert not failures, "\n".join(failures)


def test_no_opening_straddles_a_wall_junction(spec) -> None:
    junctions = _wall_junction_offsets(spec["walls"])
    failures = _opening_junction_straddle_failures(spec["openings"], junctions)
    assert not failures, "\n".join(failures)


# -- completeness ------------------------------------------------------------------------


def test_every_room_matches_the_published_table_in_both_directions(spec, published) -> None:
    failures = _completeness_failures(spec["rooms"], published["rooms"])
    assert not failures, "\n".join(failures)


# -- connectivity ------------------------------------------------------------------------


def test_every_room_is_reachable_from_the_entrance(model, spec) -> None:
    """Every room must be reachable from outside via doors, passages, or a stairwell
    slab opening (``spec["slab_openings"]``, ``kind == "stairwell"``, traversed via its
    ``connects_levels``).

    A room with genuinely neither a door nor a stairwell connecting it is either a
    transcription error or a sealed void; either way it is a human decision, so this fails
    and names the room(s) rather than skipping or being loosened to always pass.
    """
    nodes, edges, labels = _connectivity_graph(model, spec["openings"], spec["slab_openings"])
    unreachable = _unreachable_nodes(nodes, edges, "OUTSIDE")
    unreachable.discard("OUTSIDE")
    assert not unreachable, (
        "room(s) not reachable from the entrance via a door/passage opening or a "
        "stairwell slab opening: " + ", ".join(sorted(labels.get(n, n) for n in unreachable))
    )


# ========================================================================================
# Synthetic-fixture unit tests. Each helper above is exercised against a hand-built
# fixture that is known to violate exactly the rule the helper checks, independent of
# whether the real spec currently exhibits the failure.
# ========================================================================================

# ---- wall length / thickness floor ------------------------------------------------------


def test_short_wall_helper_flags_a_sub_50mm_thickness() -> None:
    walls = [{"id": "G_WX", "level": "ground", "start": [0, 0], "end": [1000, 0], "thickness": 30}]
    failures = _short_or_zero_wall_failures(walls)
    assert any("G_WX" in f and "30 mm" in f for f in failures)


def test_short_wall_helper_flags_zero_length() -> None:
    walls = [
        {"id": "G_WZ", "level": "ground", "start": [500, 500], "end": [500, 500], "thickness": 450}
    ]
    failures = _short_or_zero_wall_failures(walls)
    assert any("G_WZ" in f and "zero-length" in f for f in failures)


def test_short_wall_helper_admits_the_antresola_balustrade() -> None:
    """A_W7: 60 mm thick, 7.12 m long. Real geometry, must pass the floor."""
    walls = [
        {
            "id": "A_W7",
            "level": "attic",
            "start": [4440, 4730],
            "end": [11560, 4730],
            "thickness": 60,
        }
    ]
    assert _short_or_zero_wall_failures(walls) == []


# ---- duplicate walls --------------------------------------------------------------------


def test_duplicate_wall_helper_flags_identical_centreline() -> None:
    walls = [
        {"id": "G_W1", "level": "ground", "start": [0, 0], "end": [5000, 0], "thickness": 450},
        {"id": "G_W1B", "level": "ground", "start": [2, 1], "end": [4998, -1], "thickness": 450},
    ]
    failures = _duplicate_wall_failures(walls)
    assert len(failures) == 1
    assert "G_W1" in failures[0] and "G_W1B" in failures[0]


def test_duplicate_wall_helper_catches_reversed_direction() -> None:
    walls = [
        {"id": "G_WA", "level": "ground", "start": [0, 0], "end": [5000, 0], "thickness": 450},
        {"id": "G_WB", "level": "ground", "start": [5000, 0], "end": [0, 0], "thickness": 450},
    ]
    failures = _duplicate_wall_failures(walls)
    assert len(failures) == 1


def test_duplicate_wall_helper_passes_distinct_parallel_walls() -> None:
    walls = [
        {"id": "G_WA", "level": "ground", "start": [0, 0], "end": [5000, 0], "thickness": 450},
        {
            "id": "G_WB",
            "level": "ground",
            "start": [0, 3000],
            "end": [5000, 3000],
            "thickness": 450,
        },
    ]
    assert _duplicate_wall_failures(walls) == []


def test_duplicate_wall_helper_does_not_cross_levels() -> None:
    walls = [
        {"id": "G_WA", "level": "ground", "start": [0, 0], "end": [5000, 0], "thickness": 450},
        {"id": "A_WA", "level": "attic", "start": [0, 0], "end": [5000, 0], "thickness": 450},
    ]
    assert _duplicate_wall_failures(walls) == []


# ---- closed loop / overshoot, through the real kernel ------------------------------------

EXTERIOR_LAYERS = [
    {"material": "Porotherm 25", "thickness": 250},
    {"material": "EPS", "thickness": 200},
    {"material": "tynk", "thickness": 15},
]


def _wall(wall_id, start, end, thickness, *, level="ground", kind="exterior"):
    return {
        "id": wall_id,
        "level": level,
        "start": list(start),
        "end": list(end),
        "thickness": thickness,
        "type": kind,
    }


def _room(room_id, published_id, name, boundary, published_area, *, level="ground", seed=None):
    out = {
        "id": room_id,
        "published_id": published_id,
        "name": name,
        "level": level,
        "boundary": boundary,
        "published_area": published_area,
        "area_groups": ["usable", "net"],
    }
    if seed is not None:
        out["seed"] = list(seed)
    return out


def _meta_document():
    return {
        "meta": {
            "schema_version": "1.0.0",
            "source_url": "https://example.invalid/synthetic",
            "variant": "TEST",
            "transcribed_by": "tests/test_topology.py",
            "date": "2026-08-30",
        },
        "levels": [
            {"id": "ground", "name": "PARTER", "elevation": 0, "ceiling_height": 2700},
            {"id": "attic", "name": "PODDASZE", "elevation": 3040, "ceiling_height": 2730},
        ],
        "construction": {
            "exterior_wall": {"thickness": 465, "layers": EXTERIOR_LAYERS},
            "ceiling": {"thickness": 340, "layers": [{"material": "zelbet", "thickness": 340}]},
            "knee_wall_height": 290,
            "finish_allowance": 20,
        },
        "section_elevations": {
            "terrain": -320,
            "ground_floor": 0,
            "attic_floor": 3040,
            "ridge": 6770,
            "eave_fascia_underside": 2880,
            "source_image": "data/source/section.png",
        },
        "roof": {"type": "gable", "pitch_deg": 35.0, "eaves_overhang": 600},
    }


def _write_synthetic_spec(
    tmp_path,
    *,
    walls=None,
    rooms=None,
    openings=None,
    attic_walls=None,
    attic_rooms=None,
    attic_openings=None,
    attic_slab_openings=None,
):
    (tmp_path / "meta.json").write_text(json.dumps(_meta_document()), encoding="utf-8")
    (tmp_path / "ground.json").write_text(
        json.dumps(
            {
                "walls": walls or [],
                "openings": openings or [],
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
                "openings": attic_openings or [],
                "rooms": attic_rooms or [],
                "dimension_chains": [],
                "slab_openings": attic_slab_openings or [],
            }
        ),
        encoding="utf-8",
    )
    return load_spec(tmp_path)


BOX_WALLS = [
    _wall("G_S", (250, 250), (5750, 250), 500),
    _wall("G_N", (250, 3750), (5750, 3750), 500),
    _wall("G_W", (250, 250), (250, 3750), 500),
    _wall("G_E", (5750, 250), (5750, 3750), 500),
]
BOX_ROOMS = [_room("G_R1", 1, "Pokoj", ["G_S", "G_N", "G_W", "G_E"], 15.0, seed=(3000, 2000))]


def test_closed_loop_helper_passes_a_closed_box(tmp_path) -> None:
    spec = _write_synthetic_spec(tmp_path, walls=BOX_WALLS, rooms=BOX_ROOMS)
    model = build_model(spec)
    failures, overshoot = _wall_network_failures("ground", model.level("ground").network)
    assert failures == []
    assert overshoot == pytest.approx(0.0, abs=1e-12)


def test_closed_loop_helper_flags_an_unclosed_exterior_loop(tmp_path) -> None:
    """Drop one exterior wall: the ring never closes, and the void check must catch it
    even though a room's `boundary` still names the missing wall (T10 does not rely on
    `room.polygon()` succeeding -- it inspects the network directly)."""
    broken_walls = [w for w in BOX_WALLS if w["id"] != "G_E"]
    spec = _write_synthetic_spec(tmp_path, walls=broken_walls, rooms=[])
    model = build_model(spec)
    failures, _ = _wall_network_failures("ground", model.level("ground").network)
    assert any("gap" in f for f in failures)


# ---- room polygon validity ---------------------------------------------------------------


def test_invalid_polygon_helper_flags_a_bowtie() -> None:
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    failures = _invalid_polygon_failures([("G_RX", "Bowtie", bowtie)])
    assert any("G_RX" in f for f in failures)


def test_invalid_polygon_helper_passes_a_clean_rectangle() -> None:
    assert _invalid_polygon_failures([("G_R1", "Pokoj", box(0, 0, 5, 3))]) == []


# ---- room overlap -------------------------------------------------------------------------


def test_overlap_helper_flags_an_overlapping_room_pair() -> None:
    records = [
        ("G_RA", "Pokoj A", "ground", box(0, 0, 5, 5)),
        ("G_RB", "Pokoj B", "ground", box(3, 3, 8, 8)),
    ]
    failures = _room_overlap_failures(records)
    assert len(failures) == 1
    assert "G_RA" in failures[0] and "G_RB" in failures[0]


def test_overlap_helper_passes_adjacent_non_overlapping_rooms() -> None:
    records = [
        ("G_RA", "Pokoj A", "ground", box(0, 0, 5, 5)),
        ("G_RB", "Pokoj B", "ground", box(5, 0, 10, 5)),
    ]
    assert _room_overlap_failures(records) == []


def test_overlap_helper_admits_identical_open_plan_faces() -> None:
    """Four rooms resolving to the SAME polygon (Hol/Salon/Hol/Kuchnia's shared face) must
    not be flagged against each other."""
    shared = box(0, 0, 10, 10)
    records = [
        ("G_R2", "Hol", "ground", shared),
        ("G_R6", "Salon", "ground", shared),
        ("G_R7", "Hol", "ground", shared),
        ("G_R14", "Kuchnia", "ground", shared),
    ]
    assert _room_overlap_failures(records) == []


def test_overlap_helper_does_not_compare_across_levels() -> None:
    shared_shape = box(0, 0, 5, 5)
    records = [
        ("G_R1", "Pokoj", "ground", shared_shape),
        ("A_R1", "Antresola", "attic", shared_shape),
    ]
    assert _room_overlap_failures(records) == []


# ---- union vs footprint ---------------------------------------------------------------


def test_union_vs_footprint_helper_passes_an_exact_tiling() -> None:
    envelope = box(0, 0, 6, 4)
    wall_solid = envelope.difference(box(0.5, 0.5, 5.5, 3.5))
    room = box(0.5, 0.5, 5.5, 3.5)
    residual = _union_vs_footprint_residual([room], wall_solid, envelope)
    assert residual == pytest.approx(0.0, abs=1e-9)


def test_union_vs_footprint_helper_flags_a_growing_residual() -> None:
    """A room that does not reach the wall (a gap of unaccounted floor) must show up as a
    residual, not be silently absorbed. Clear rectangle is 5.0 x 3.0 m; the room is 1.0 m
    narrower in x, so 1.0 x 3.0 = 3.0 m2 of floor goes unaccounted for."""
    envelope = box(0, 0, 6, 4)
    wall_solid = envelope.difference(box(0.5, 0.5, 5.5, 3.5))
    shrunk_room = box(0.5, 0.5, 4.5, 3.5)
    residual = _union_vs_footprint_residual([shrunk_room], wall_solid, envelope)
    assert residual > UNION_FOOTPRINT_TOL_M2
    assert residual == pytest.approx(3.0, rel=1e-9)


def test_dedupe_polygons_collapses_identical_shapes() -> None:
    a, b, c = box(0, 0, 1, 1), box(0, 0, 1, 1), box(2, 2, 3, 3)
    assert len(_dedupe_polygons([a, b, c])) == 2


# ---- slab openings: floor-area coverage for the union-vs-footprint check ----------------


def test_slab_opening_polygons_helper_converts_bounds_to_a_box() -> None:
    """A_SO1's real bounds, mm -> the exact 22.5505 m2 residual T10 originally traced."""
    slab_openings = [
        {"id": "A_SO1", "level": "attic", "bounds": [5550, 4760, 11500, 8550], "kind": "void"}
    ]
    polygons = _slab_opening_polygons(slab_openings)
    assert len(polygons) == 1
    assert polygons[0].area == pytest.approx(22.5505, rel=1e-9)


def test_union_vs_footprint_helper_closes_the_gap_with_a_slab_opening() -> None:
    """The same shrunk-room fixture as the growing-residual test, but now the missing
    strip is supplied as a slab opening (a void, exactly like A_SO1) rather than floor --
    the residual must drop back to ~0, not stay at 3.0 m2."""
    envelope = box(0, 0, 6, 4)
    wall_solid = envelope.difference(box(0.5, 0.5, 5.5, 3.5))
    shrunk_room = box(0.5, 0.5, 4.5, 3.5)
    void = box(4.5, 0.5, 5.5, 3.5)  # exactly the missing 1.0 x 3.0 m strip
    residual = _union_vs_footprint_residual([shrunk_room, void], wall_solid, envelope)
    assert residual == pytest.approx(0.0, abs=1e-9)


# ---- stairwell slab openings: connectivity bridges between levels -----------------------


def test_stairwell_bridge_edges_connects_rooms_on_both_levels() -> None:
    polygons_by_level = {
        "ground": {"G_R6": box(0, 0, 2, 2)},
        "attic": {"A_R4": box(0, 0, 2, 2)},
    }
    slab_openings = [
        {
            "id": "A_SO2",
            "level": "attic",
            "bounds": [0, 0, 2000, 2000],
            "kind": "stairwell",
            "connects_levels": ["ground", "attic"],
        }
    ]
    edges = _stairwell_bridge_edges(slab_openings, polygons_by_level)
    assert edges == [("G_R6", "A_R4")]


def test_stairwell_bridge_edges_ignores_void_openings() -> None:
    """A void carries no connects_levels and must never bridge anything, even if two
    rooms on different levels happen to both touch its bounds."""
    polygons_by_level = {
        "ground": {"G_R6": box(0, 0, 2, 2)},
        "attic": {"A_R2": box(0, 0, 2, 2)},
    }
    slab_openings = [
        {"id": "A_SO1", "level": "attic", "bounds": [0, 0, 2000, 2000], "kind": "void"}
    ]
    assert _stairwell_bridge_edges(slab_openings, polygons_by_level) == []


def test_stairwell_bridge_edges_does_not_bridge_a_room_that_merely_touches_the_edge() -> None:
    """A room whose polygon stops short of the opening (separated by a real wall, like the
    Antresola's 60 mm balustrade against the real A_SO2) must not be bridged -- the rule is
    geometric intersection, not "on a connects_levels level"."""
    polygons_by_level = {
        "ground": {"G_R6": box(0, 0, 2, 2)},
        "attic": {"A_R1": box(0, 2.06, 2, 3)},  # 60 mm short of the opening below
    }
    slab_openings = [
        {
            "id": "A_SO2",
            "level": "attic",
            "bounds": [0, 0, 2000, 2000],
            "kind": "stairwell",
            "connects_levels": ["ground", "attic"],
        }
    ]
    edges = _stairwell_bridge_edges(slab_openings, polygons_by_level)
    assert edges == []  # A_R1 does not reach the opening; G_R6 alone bridges nothing


def test_connectivity_graph_integration_stairwell_bridges_a_doorless_room(tmp_path) -> None:
    """End-to-end through the real kernel: a ground-floor room with an exterior door, and
    an attic split into two rooms by an unbroken partition (no door between them). Only
    one attic room sits over a stairwell slab opening that also reaches into the ground
    room. Expect: the stairwell-side attic room becomes reachable; the other attic room,
    with neither a door nor a stairwell, correctly remains unreachable -- proving the
    check stays strict rather than becoming vacuous once slab_openings exist."""
    ground_walls = [
        _wall("G_S", (250, 250), (5750, 250), 500),
        _wall("G_N", (250, 3750), (5750, 3750), 500),
        _wall("G_W", (250, 250), (250, 3750), 500),
        _wall("G_E", (5750, 250), (5750, 3750), 500),
    ]
    ground_rooms = [
        _room("G_R1", 1, "Pokoj", ["G_S", "G_N", "G_W", "G_E"], 15.0, seed=(3000, 2000))
    ]
    ground_openings = [
        {
            "id": "G_O1",
            "wall": "G_S",
            "offset": 2000,
            "width": 900,
            "height": 2100,
            "sill": 0,
            "kind": "door",
            "swing": "left",
        }
    ]

    attic_walls = [
        _wall("A_S", (250, 250), (5750, 250), 500, level="attic"),
        _wall("A_N", (250, 3750), (5750, 3750), 500, level="attic"),
        _wall("A_W", (250, 250), (250, 3750), 500, level="attic"),
        _wall("A_E", (5750, 250), (5750, 3750), 500, level="attic"),
        _wall("A_P", (3000, 250), (3000, 3750), 120, level="attic", kind="partition"),
    ]
    attic_rooms = [
        _room(
            "A_STAIR",
            1,
            "Nad schodami",
            ["A_S", "A_W", "A_N", "A_P"],
            6.0,
            level="attic",
            seed=(1500, 2000),
        ),
        _room(
            "A_LONELY",
            2,
            "Strych",
            ["A_S", "A_P", "A_N", "A_E"],
            6.0,
            level="attic",
            seed=(4500, 2000),
        ),
    ]
    slab_openings = [
        {
            "id": "A_SO_TEST",
            "level": "attic",
            "bounds": [500, 500, 2000, 2000],
            "kind": "stairwell",
            "connects_levels": ["ground", "attic"],
            "note": "synthetic stairwell for the connectivity-graph integration test",
        }
    ]

    spec = _write_synthetic_spec(
        tmp_path,
        walls=ground_walls,
        rooms=ground_rooms,
        openings=ground_openings,
        attic_walls=attic_walls,
        attic_rooms=attic_rooms,
        attic_slab_openings=slab_openings,
    )
    model = build_model(spec)

    nodes, edges, labels = _connectivity_graph(model, spec["openings"], spec["slab_openings"])
    unreachable = _unreachable_nodes(nodes, edges, "OUTSIDE")
    unreachable.discard("OUTSIDE")
    assert unreachable == {"A_LONELY"}, (
        f"expected only the doorless, stairwell-less A_LONELY to be unreachable, got "
        f"{sorted(labels.get(n, n) for n in unreachable)}"
    )


# ---- opening: wall resolves --------------------------------------------------------------


def test_opening_wall_resolves_helper_flags_a_dangling_reference() -> None:
    openings = [{"id": "G_O1", "wall": "G_W99", "offset": 0, "width": 900}]
    failures = _opening_wall_resolves_failures(openings, {"G_W1", "G_W2"})
    assert any("G_O1" in f and "G_W99" in f for f in failures)


def test_opening_wall_resolves_helper_passes_a_real_reference() -> None:
    openings = [{"id": "G_O1", "wall": "G_W1", "offset": 0, "width": 900}]
    assert _opening_wall_resolves_failures(openings, {"G_W1"}) == []


# ---- opening: fits within its wall --------------------------------------------------------


def test_opening_fit_helper_flags_an_opening_wider_than_its_wall() -> None:
    openings = [{"id": "G_O1", "wall": "G_W1", "offset": 4000, "width": 2000}]
    failures = _opening_fit_failures(openings, {"G_W1": 5000.0})
    assert len(failures) == 1
    assert "G_O1" in failures[0] and "1000" in failures[0]


def test_opening_fit_helper_passes_an_opening_that_fits_exactly() -> None:
    openings = [{"id": "G_O1", "wall": "G_W1", "offset": 1000, "width": 4000}]
    assert _opening_fit_failures(openings, {"G_W1": 5000.0}) == []


# ---- opening: no overlap on the same wall -------------------------------------------------


def test_opening_overlap_helper_flags_two_openings_on_the_same_wall() -> None:
    openings = [
        {"id": "G_O1", "wall": "G_W1", "offset": 0, "width": 2000},
        {"id": "G_O2", "wall": "G_W1", "offset": 1500, "width": 1000},
    ]
    failures = _opening_overlap_failures(openings)
    assert len(failures) == 1
    assert "G_O1" in failures[0] and "G_O2" in failures[0]


def test_opening_overlap_helper_passes_adjacent_openings() -> None:
    openings = [
        {"id": "G_O1", "wall": "G_W1", "offset": 0, "width": 2000},
        {"id": "G_O2", "wall": "G_W1", "offset": 2000, "width": 1000},
    ]
    assert _opening_overlap_failures(openings) == []


def test_opening_overlap_helper_does_not_compare_across_walls() -> None:
    openings = [
        {"id": "G_O1", "wall": "G_W1", "offset": 0, "width": 2000},
        {"id": "G_O2", "wall": "G_W2", "offset": 0, "width": 2000},
    ]
    assert _opening_overlap_failures(openings) == []


# ---- opening: sill + height <= ceiling -----------------------------------------------------


def test_opening_height_helper_flags_sill_plus_height_over_ceiling() -> None:
    openings = [{"id": "G_O1", "wall": "G_W1", "sill": 900, "height": 2000}]
    failures = _opening_height_failures(openings, {"G_W1": 2700})
    assert len(failures) == 1
    assert "G_O1" in failures[0] and "200" in failures[0]


def test_opening_height_helper_passes_exact_head_at_the_ceiling() -> None:
    """A_O1/A_O2's gable windows: sill 0 + height 2730 == ceiling_height 2730 exactly."""
    openings = [{"id": "A_O1", "wall": "A_W4", "sill": 0, "height": 2730}]
    assert _opening_height_failures(openings, {"A_W4": 2730}) == []


# ---- opening: no straddling a wall junction -------------------------------------------------


def test_junction_offsets_helper_finds_an_interior_t_junction() -> None:
    host = {"id": "G_W7", "level": "ground", "start": [16875, 8775], "end": [225, 8775]}
    stem = {"id": "G_W9", "level": "ground", "start": [4375, 5260], "end": [4375, 8775]}
    junctions = _wall_junction_offsets([host, stem])
    assert junctions["G_W7"] == pytest.approx([12500.0])


def test_junction_offsets_helper_ignores_the_host_s_own_corners() -> None:
    host = {"id": "G_W7", "level": "ground", "start": [16875, 8775], "end": [225, 8775]}
    corner = {"id": "G_W6", "level": "ground", "start": [16875, 225], "end": [16875, 8775]}
    junctions = _wall_junction_offsets([host, corner])
    assert junctions.get("G_W7", []) == []


def test_opening_junction_straddle_helper_flags_a_straddling_opening() -> None:
    openings = [{"id": "G_O1", "wall": "G_W7", "offset": 12000, "width": 1000}]
    failures = _opening_junction_straddle_failures(openings, {"G_W7": [12500.0]})
    assert len(failures) == 1
    assert "G_O1" in failures[0]


def test_opening_junction_straddle_helper_passes_a_junction_clear_of_openings() -> None:
    openings = [{"id": "G_O1", "wall": "G_W7", "offset": 6935, "width": 4200}]
    assert _opening_junction_straddle_failures(openings, {"G_W7": [12500.0]}) == []


# ---- completeness against the published table ------------------------------------------


def test_completeness_helper_flags_a_room_missing_from_the_spec() -> None:
    spec_rooms = [{"level": "ground", "published_id": 1, "name": "Wiatrolap"}]
    published = {
        "ground": [
            {"id": 1, "name": "Wiatrolap"},
            {"id": 2, "name": "Hol"},
        ]
    }
    failures = _completeness_failures(spec_rooms, published)
    assert len(failures) == 1
    assert "published room 2" in failures[0] and "no matching room" in failures[0]


def test_completeness_helper_flags_a_room_invented_in_the_spec() -> None:
    spec_rooms = [
        {"level": "ground", "published_id": 1, "name": "Wiatrolap"},
        {"level": "ground", "published_id": 99, "name": "Ghost Room"},
    ]
    published = {"ground": [{"id": 1, "name": "Wiatrolap"}]}
    failures = _completeness_failures(spec_rooms, published)
    assert len(failures) == 1
    assert "Ghost Room" in failures[0] and "invented" in failures[0]


def test_completeness_helper_matches_on_published_id_not_name() -> None:
    """Two 'Strych ocieplony' rooms: matching on name alone would falsely pair them up
    (or falsely dedupe them) instead of matching id-for-id."""
    spec_rooms = [
        {"level": "attic", "published_id": 2, "name": "Strych ocieplony"},
        {"level": "attic", "published_id": 3, "name": "Strych ocieplony"},
    ]
    published = {
        "attic": [
            {"id": 2, "name": "Strych ocieplony"},
            {"id": 3, "name": "Strych ocieplony"},
        ]
    }
    assert _completeness_failures(spec_rooms, published) == []


def test_completeness_helper_passes_a_matching_table() -> None:
    spec_rooms = [{"level": "ground", "published_id": 1, "name": "Wiatrolap"}]
    published = {"ground": [{"id": 1, "name": "Wiatrolap"}]}
    assert _completeness_failures(spec_rooms, published) == []


# ---- connectivity -------------------------------------------------------------------------


def test_unreachable_nodes_helper_flags_a_room_with_no_door() -> None:
    nodes = {"OUTSIDE", "G_R1", "G_R2", "G_R3"}
    edges = [("OUTSIDE", "G_R1"), ("G_R1", "G_R2")]  # G_R3 has no edge at all
    unreachable = _unreachable_nodes(nodes, edges, "OUTSIDE")
    assert unreachable == {"G_R3"}


def test_unreachable_nodes_helper_passes_a_fully_connected_graph() -> None:
    nodes = {"OUTSIDE", "G_R1", "G_R2"}
    edges = [("OUTSIDE", "G_R1"), ("G_R1", "G_R2")]
    assert _unreachable_nodes(nodes, edges, "OUTSIDE") == set()


def test_unreachable_nodes_helper_flags_a_component_cut_off_from_the_entrance() -> None:
    """Two rooms connected to EACH OTHER but not to the entrance -- distinct from a room
    with literally no door at all, and both must be reported."""
    nodes = {"OUTSIDE", "G_R1", "A_R1", "A_R2"}
    edges = [("OUTSIDE", "G_R1"), ("A_R1", "A_R2")]
    assert _unreachable_nodes(nodes, edges, "OUTSIDE") == {"A_R1", "A_R2"}

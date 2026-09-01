"""geometry.py -- the 2D geometry kernel (T07).

Turns the spec's wall centrelines into real 2D geometry: wall solids, the wall network,
and room polygons. Every dimensional claim the project makes routes through here.

UNITS
-----
The spec is **integer millimetres**. This module converts to **metres exactly once**, at
the boundary into shapely (:func:`to_m` / :func:`point_m`), and works in metres from there
on. Nothing is rounded on the way out: areas are returned as full-precision floats and the
comparison tolerance lives in the assertions, not in the kernel.

THE MEASUREMENT FACE
--------------------
The single most important parameter in this module is ``measure_to``:

``"structure"``
    The inner face of the load-bearing block (Porotherm). This is what the printed
    dimension chains measure to -- plans dimension raw structure.

``"finish"``
    The inner face of the plaster, i.e. the structure face inset by
    ``construction.finish_allowance`` (default 20 mm) **per face**. This is *w swietle
    scian* per PN-ISO 9836, which is what Archon's published room areas are measured to.

Neither is hardcoded anywhere. T15 sweeps the parameter to confirm the allowance across
all 14 ground-floor rooms; T08 runs both to tell a uniform measurement-convention offset
apart from a real per-room geometry error. See README.md, "The finish allowance".

A room may override the face for itself with ``rooms[].measure_to`` (T19, from T18's
finding). Exactly one does: A_R4 *Schody* is a slab opening rather than a room *w swietle
scian*, its edges are a void edge and two guards with no plaster to deduct, and the
publisher's 3.64 m2 is the drawn opening measured at raw structure. That carve-out is
**data in the spec**, justified in the room's own ``note`` -- there is no room id in this
module, and adding one would hide the only exception in the project inside the kernel.
``rooms[].measure_to.extends_under`` is the second half of the same override: see
:meth:`RoomGeometry._extended_under_guards`.

Note the finish allowance is deliberately **not** a wall layer: ``exterior_wall.layers``
is 250 Porotherm + 200 EPS + 15 render = 465 mm and describes the wall as built, while the
allowance is a *measurement* convention applied to whichever face a room is measured to
(interior partitions get it on both sides, and they carry no plaster layer at all).

HOW A ROOM POLYGON IS FOUND
---------------------------
By **polygonisation**, never by half-plane intersection: the wall solids are unioned, the
boundary of that union is polygonised (:func:`shapely.ops.polygonize`), the wall-material
faces are dropped, and the room is the remaining minimal face containing the room's seed
point. Half-plane intersection is brittle on non-convex rooms and this house has them;
polygonisation handles an L-shaped room with no special case at all.

CORNERS AND T-JUNCTIONS
-----------------------
Each wall is a single straight segment (the schema gives a wall a ``start`` and an ``end``
and nothing else), buffered with ``cap_style="flat"``. A flat cap stops at the centreline
endpoint, which would leave a square notch missing at every outside corner. So before
buffering, each wall end is **extended by half the thickness of the thickest other wall
meeting there** (:func:`_junction_extensions`). That is exactly enough to close the corner
and provably never overshoots: the extension always terminates at or inside the far face
of the wall it runs into. At a T-junction the abutting wall's cap is buried inside its
host, so there is no overshoot there either.

This is exact for axis-aligned walls, which is what this building is. For an oblique
corner the true mitre apex is further out (acute) or closer in (obtuse) than half a
thickness; :func:`WallNetwork.oblique_wall_ids` reports any wall this would apply to so a
caller can assert the assumption rather than silently rely on it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Literal

from shapely.affinity import translate
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from kotewki.spec import Level, Room, Wall

__all__ = [
    "DEFAULT_FINISH_ALLOWANCE_MM",
    "MEASURE_FACES",
    "MM_PER_M",
    "GeometryError",
    "LevelGeometry",
    "MeasureTo",
    "Model",
    "RoomGeometry",
    "SlopedCeiling",
    "WallLayer",
    "WallNetwork",
    "WallSolid",
    "build",
    "build_model",
    "polygonise",
    "room_polygon",
    "to_m",
]

# --------------------------------------------------------------------------------------
# Units. The spec is integer millimetres; shapely works in metres. This is the only
# conversion in the module and it happens at the boundary, once.
# --------------------------------------------------------------------------------------

#: Millimetres per metre. Spec in, metres out -- see the module docstring.
MM_PER_M = 1000.0

#: Fallback finish allowance per face, millimetres, when the spec carries no
#: ``construction.finish_allowance``. The spec value wins whenever it is present.
DEFAULT_FINISH_ALLOWANCE_MM = 20

MeasureTo = Literal["structure", "finish"]

#: Both measurement faces, in the order T15 should sweep them.
MEASURE_FACES: tuple[MeasureTo, ...] = ("structure", "finish")

#: Coincidence tolerance for "these two wall centrelines meet here", metres. Coordinates
#: are integer millimetres, so real junctions are exact; this only absorbs float noise.
JOIN_TOL_M = 1e-7

#: A shared face edge shorter than this is treated as a touch at a corner, not adjacency.
EDGE_TOL_M = 1e-3

#: How far a wall footprint is grown before asking which face edges lie on it. Big enough
#: to absorb float noise on a coordinate that came out of a union, small enough that a wall
#: merely touching a face at one corner cannot accumulate :data:`EDGE_TOL_M` of length.
ADJACENCY_BUFFER_M = 1e-6

#: Faces smaller than this are polygonisation slivers, not rooms.
MIN_FACE_AREA_M2 = 1e-6


def to_m(millimetres: float) -> float:
    """Convert integer millimetres from the spec to metres for shapely."""
    return millimetres / MM_PER_M


def point_m(point: Sequence[float]) -> tuple[float, float]:
    """Convert an ``[x, y]`` spec point in millimetres to a metre tuple."""
    return (to_m(point[0]), to_m(point[1]))


class GeometryError(Exception):
    """The wall network or a room polygon could not be constructed.

    Always carries what was expected and what was found: a geometry failure that only says
    "not found" costs a debugging session, and the whole point of the polygonisation route
    is that the failure modes are diagnosable.
    """


# --------------------------------------------------------------------------------------
# Wall solids
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class WallLayer:
    """One material band of a wall, positioned relative to the wall's **inner** face.

    ``inner_offset_m`` / ``outer_offset_m`` are distances from the inner face of the wall,
    so for the 465 mm exterior wall the Porotherm band is 0.000-0.250, the EPS
    0.250-0.450 and the render 0.450-0.465. Layers are individually addressable so T11 can
    show a correct reveal depth in a window opening and T15 can move the measurement face
    without either of them re-deriving the build-up.
    """

    material: str
    thickness_m: float
    inner_offset_m: float
    outer_offset_m: float


@dataclass(frozen=True, kw_only=True)
class WallSolid:
    """A wall centreline with a thickness, in metres.

    The footprint is the centreline buffered by half the thickness with a flat cap and a
    mitre join, plus the junction extensions described in the module docstring.
    """

    id: str
    level: str
    type: str
    start: tuple[float, float]
    end: tuple[float, float]
    thickness_m: float
    layers: tuple[WallLayer, ...] = ()
    height_m: float | None = None
    start_extension_m: float = 0.0
    end_extension_m: float = 0.0

    # -- basic geometry -----------------------------------------------------------------

    @property
    def half_thickness_m(self) -> float:
        return self.thickness_m / 2.0

    @cached_property
    def length_m(self) -> float:
        return math.dist(self.start, self.end)

    @cached_property
    def direction(self) -> tuple[float, float]:
        """Unit vector start -> end."""
        length = self.length_m
        if length == 0.0:
            raise GeometryError(f"wall {self.id}: zero length (start == end == {self.start})")
        return ((self.end[0] - self.start[0]) / length, (self.end[1] - self.start[1]) / length)

    @cached_property
    def normal(self) -> tuple[float, float]:
        """Unit normal, 90 deg to the left of ``direction``."""
        dx, dy = self.direction
        return (-dy, dx)

    @property
    def is_axis_aligned(self) -> bool:
        return self.start[0] == self.end[0] or self.start[1] == self.end[1]

    @cached_property
    def centreline(self) -> LineString:
        return LineString([self.start, self.end])

    def _ends(self, extra_m: float = 0.0) -> tuple[tuple[float, float], tuple[float, float]]:
        """Endpoints extended into their junctions.

        ``extra_m`` (the measurement inset) is added only to ends that already have a
        junction extension: a free end must stay where the spec put it.
        """
        dx, dy = self.direction
        head = self.start_extension_m + (extra_m if self.start_extension_m > 0.0 else 0.0)
        tail = self.end_extension_m + (extra_m if self.end_extension_m > 0.0 else 0.0)
        return (
            (self.start[0] - dx * head, self.start[1] - dy * head),
            (self.end[0] + dx * tail, self.end[1] + dy * tail),
        )

    def extended_centreline(self, extra_m: float = 0.0) -> LineString:
        """The centreline extended into its junctions -- see the module docstring."""
        head, tail = self._ends(extra_m)
        return LineString([head, tail])

    def footprint(self, inset_m: float = 0.0) -> Polygon:
        """Plan footprint. ``inset_m`` grows the wall on every face by that much.

        ``inset_m`` is how the measurement face is moved: growing every wall by the finish
        allowance shrinks every room polygon by exactly the allowance per face, which is
        what *w swietle scian* means.
        """
        return self.extended_centreline(inset_m).buffer(
            self.half_thickness_m + inset_m,
            cap_style="flat",
            join_style="mitre",
        )

    def face_offset(self, measure_to: MeasureTo, finish_allowance_m: float) -> float:
        """Distance from the centreline to the measurement face, metres."""
        _check_measure_to(measure_to)
        return self.half_thickness_m + (finish_allowance_m if measure_to == "finish" else 0.0)

    def band(self, from_offset_m: float, to_offset_m: float, extra_m: float = 0.0) -> Polygon:
        """The strip of this wall between two signed offsets from the centreline.

        Offsets are measured along :attr:`normal` (positive = left of start -> end). Built
        analytically rather than by buffering, because a wall is a single straight segment
        and the corners of a band must land exactly on the wall's own corners.
        """
        lo, hi = sorted((from_offset_m, to_offset_m))
        head, tail = self._ends(extra_m)
        nx, ny = self.normal
        return Polygon(
            [
                (head[0] + nx * lo, head[1] + ny * lo),
                (tail[0] + nx * lo, tail[1] + ny * lo),
                (tail[0] + nx * hi, tail[1] + ny * hi),
                (head[0] + nx * hi, head[1] + ny * hi),
            ]
        )

    def face_line(self, side: int, measure_to: MeasureTo, finish_allowance_m: float) -> LineString:
        """The measurement face on one side of the wall, as a line.

        ``side`` is +1 for the left of start -> end and -1 for the right. Used by T11 for
        reveal depths and by T13 for the overlay.
        """
        offset = math.copysign(self.face_offset(measure_to, finish_allowance_m), side)
        head, tail = self._ends()
        nx, ny = self.normal
        return LineString(
            [
                (head[0] + nx * offset, head[1] + ny * offset),
                (tail[0] + nx * offset, tail[1] + ny * offset),
            ]
        )

    def layer_bands(self, interior_side: int = 1) -> tuple[tuple[WallLayer, Polygon], ...]:
        """The wall's layers as individually addressable polygons.

        ``interior_side`` says which side of the centreline the *inside* face is on: +1 for
        the left of start -> end, -1 for the right. Layers run inside face -> outside face,
        so on the +1 side the innermost layer occupies the offsets closest to +half.
        """
        if not self.layers:
            return ()
        sign = 1 if interior_side >= 0 else -1
        inner_face = sign * self.half_thickness_m
        bands = []
        for layer in self.layers:
            lo = inner_face - sign * layer.inner_offset_m
            hi = inner_face - sign * layer.outer_offset_m
            bands.append((layer, self.band(lo, hi)))
        return tuple(bands)

    # -- construction from the spec ------------------------------------------------------

    @classmethod
    def from_wall(cls, wall: Wall, **extensions: float) -> WallSolid:
        """Build from a :class:`kotewki.spec.Wall`. This is the mm -> m boundary."""
        return cls(
            id=wall.id,
            level=wall.level,
            type=wall.type,
            start=point_m(wall.start),
            end=point_m(wall.end),
            thickness_m=to_m(wall.thickness),
            layers=_layers_from_spec(wall.layers),
            height_m=to_m(wall.height) if wall.height is not None else None,
            **extensions,
        )


def _layers_from_spec(layers: Iterable[Any]) -> tuple[WallLayer, ...]:
    out: list[WallLayer] = []
    offset = 0.0
    for layer in layers:
        thickness = to_m(layer.thickness)
        out.append(
            WallLayer(
                material=layer.material,
                thickness_m=thickness,
                inner_offset_m=offset,
                outer_offset_m=offset + thickness,
            )
        )
        offset += thickness
    return tuple(out)


def _check_measure_to(measure_to: str) -> None:
    if measure_to not in MEASURE_FACES:
        raise GeometryError(
            f"measure_to={measure_to!r} is not a measurement face. Use one of "
            f"{list(MEASURE_FACES)}: 'structure' is the inner face of the block (what the "
            f"printed chains dimension), 'finish' is the inner face of the plaster (what "
            f"Archon's published areas are measured to, w swietle scian)."
        )


# --------------------------------------------------------------------------------------
# The wall network
# --------------------------------------------------------------------------------------


def _junction_extensions(walls: Sequence[WallSolid]) -> dict[str, tuple[float, float]]:
    """How far each wall must run past its endpoints to close its corners.

    Half the thickness of the thickest other wall whose centreline touches that endpoint.
    See the module docstring for why that is both sufficient and non-overshooting.
    """
    lines = [(wall.id, wall.centreline) for wall in walls]
    out: dict[str, tuple[float, float]] = {}
    for wall in walls:
        ends = []
        for endpoint in (wall.start, wall.end):
            probe = Point(endpoint)
            reach = 0.0
            for other_id, line in lines:
                if other_id == wall.id:
                    continue
                if line.distance(probe) <= JOIN_TOL_M:
                    other = next(w for w in walls if w.id == other_id)
                    reach = max(reach, other.half_thickness_m)
            ends.append(reach)
        out[wall.id] = (ends[0], ends[1])
    return out


def _polygon_parts(geometry: BaseGeometry) -> tuple[Polygon, ...]:
    if geometry.is_empty:
        return ()
    if isinstance(geometry, Polygon):
        return (geometry,)
    return tuple(part for part in getattr(geometry, "geoms", ()) if isinstance(part, Polygon))


def polygonise(solid: BaseGeometry) -> tuple[Polygon, ...]:
    """Polygonise a wall solid and return the enclosed void faces, smallest first.

    The union of the wall footprints is a planar figure; its boundary rings are a planar
    graph, and :func:`shapely.ops.polygonize` recovers every face of that graph. Two kinds
    of face come back: wall material, and voids. Wall material is dropped by testing an
    interior point against the solid. Of the voids, the outer ring of the building also
    comes back as a face and contains all the rooms, so any face that contains a strictly
    smaller face is dropped too, leaving the minimal faces -- the rooms.

    Comparing by area (rather than "contains any other face") matters: the building
    envelope and the single room of a one-room house each contain the other's
    representative point, and a naive containment filter would discard both.
    """
    parts = _polygon_parts(solid)
    if not parts:
        return ()

    boundaries = unary_union([part.boundary for part in parts])
    faces = [face for face in polygonize(boundaries) if face.area > MIN_FACE_AREA_M2]

    voids = [face for face in faces if not solid.contains(face.representative_point())]
    voids.sort(key=lambda face: face.area)

    minimal: list[Polygon] = []
    for index, face in enumerate(voids):
        smaller = voids[:index]
        if any(face.contains(other.representative_point()) for other in smaller):
            continue
        minimal.append(face)
    return tuple(minimal)


class WallNetwork:
    """Every wall on one level, plus the faces its solids enclose.

    Solids and faces are computed per measurement inset and cached, so switching
    ``measure_to`` costs one extra union and one extra polygonisation for the whole level
    rather than a rebuild per room.
    """

    def __init__(self, walls: Sequence[WallSolid]) -> None:
        self.walls: tuple[WallSolid, ...] = tuple(walls)
        self._solids: dict[float, BaseGeometry] = {}
        self._faces: dict[float, tuple[Polygon, ...]] = {}
        self._envelopes: dict[float, BaseGeometry] = {}

    def __repr__(self) -> str:
        return f"<WallNetwork walls={len(self.walls)}>"

    @classmethod
    def from_walls(cls, walls: Iterable[Wall]) -> WallNetwork:
        """Build from spec walls, resolving junction extensions across the whole set."""
        plain = [WallSolid.from_wall(wall) for wall in walls]
        extensions = _junction_extensions(plain)
        return cls(
            [
                WallSolid.from_wall(
                    wall,
                    start_extension_m=extensions[wall.id][0],
                    end_extension_m=extensions[wall.id][1],
                )
                for wall in walls
            ]
        )

    @cached_property
    def wall_by_id(self) -> dict[str, WallSolid]:
        return {wall.id: wall for wall in self.walls}

    @cached_property
    def oblique_wall_ids(self) -> tuple[str, ...]:
        """Walls that are neither horizontal nor vertical.

        The junction-extension rule is exact only at right angles. This building is
        axis-aligned; T10 can assert this stays empty rather than trusting it.
        """
        return tuple(wall.id for wall in self.walls if not wall.is_axis_aligned)

    # -- derived geometry ----------------------------------------------------------------

    def _key(self, inset_m: float) -> float:
        return round(inset_m, 9)

    def solid(self, inset_m: float = 0.0) -> BaseGeometry:
        """Union of every wall footprint on this level."""
        key = self._key(inset_m)
        if key not in self._solids:
            if not self.walls:
                self._solids[key] = Polygon()
            else:
                self._solids[key] = unary_union(
                    [wall.footprint(inset_m) for wall in self.walls]
                )
        return self._solids[key]

    def faces(self, inset_m: float = 0.0) -> tuple[Polygon, ...]:
        """The enclosed void faces of the network -- the candidate room polygons."""
        key = self._key(inset_m)
        if key not in self._faces:
            self._faces[key] = polygonise(self.solid(inset_m))
        return self._faces[key]

    def envelope(self, inset_m: float = 0.0) -> BaseGeometry:
        """The outer envelope: the wall solid with its interior voids filled in.

        This is the *pow. zabudowy* outline -- the outside of the render, because
        ``exterior_wall.thickness`` includes the 15 mm render layer.
        """
        key = self._key(inset_m)
        if key not in self._envelopes:
            parts = _polygon_parts(self.solid(inset_m))
            filled = [Polygon(part.exterior) for part in parts]
            self._envelopes[key] = unary_union(filled) if filled else Polygon()
        return self._envelopes[key]

    def interior_side(self, wall_id: str) -> int:
        """Which side of a wall faces the building interior: +1 left, -1 right, 0 both.

        Probes just outside each face and asks whether that point is inside the building
        envelope. Interior partitions have interior on both sides and return 0.
        """
        wall = self.wall_by_id[wall_id]
        envelope = self.envelope()
        midpoint = wall.centreline.interpolate(0.5, normalized=True)
        nx, ny = wall.normal
        reach = wall.half_thickness_m + 1e-4
        inside = []
        for sign in (1, -1):
            probe = Point(midpoint.x + nx * reach * sign, midpoint.y + ny * reach * sign)
            inside.append(envelope.contains(probe))
        if inside[0] and inside[1]:
            return 0
        if inside[0]:
            return 1
        if inside[1]:
            return -1
        return 0

    def layer_bands(self, wall_id: str) -> tuple[tuple[WallLayer, Polygon], ...]:
        """This wall's layers as polygons, oriented so layer 0 is on the interior side."""
        side = self.interior_side(wall_id)
        return self.wall_by_id[wall_id].layer_bands(side if side != 0 else 1)

    def face_containing(self, seed: Point, inset_m: float = 0.0) -> Polygon:
        """The single void face containing ``seed``. Raises if that is not exactly one."""
        faces = self.faces(inset_m)
        hits = [face for face in faces if face.contains(seed)]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise GeometryError(
                f"seed point ({seed.x:.4f}, {seed.y:.4f}) m falls in none of the "
                f"{len(faces)} enclosed faces of the wall network (inset {inset_m * 1000:.1f} "
                f"mm). Either the seed is inside a wall or outside the building, or the "
                f"wall network does not close around this room -- polygonisation only "
                f"finds a face when its walls form a closed loop."
            )
        raise GeometryError(
            f"seed point ({seed.x:.4f}, {seed.y:.4f}) m falls inside {len(hits)} faces at "
            f"once, which polygonisation should make impossible; the wall network is "
            f"probably self-overlapping."
        )

    def adjacent_wall_ids(self, face: Polygon) -> frozenset[str]:
        """Ids of the walls that bound ``face`` along a real edge, not just at a corner.

        The face's edges lie on the boundary of the wall union, so they are tested against
        each wall footprint grown by :data:`ADJACENCY_BUFFER_M` -- growing it is what makes
        a boundary-on-boundary intersection return the shared edge instead of a couple of
        endpoints.
        """
        out = set()
        for wall in self.walls:
            shared = face.exterior.intersection(wall.footprint().buffer(ADJACENCY_BUFFER_M))
            if shared.length > EDGE_TOL_M:
                out.add(wall.id)
        return frozenset(out)


# --------------------------------------------------------------------------------------
# Rooms
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomGeometry:
    """One room, resolved against its level's wall network.

    Polygons are computed lazily so that a single unresolvable room does not prevent the
    rest of the model from being built and inspected -- which is exactly the state the
    project is in while transcription lands.
    """

    room: Room
    network: WallNetwork
    finish_allowance_m: float
    default_measure_to: MeasureTo = "structure"
    #: Finished floor level of this room's storey, metres in the shared frame. Carried
    #: here so the attic height banding knows which floor to measure clear height above.
    floor_elevation_m: float = 0.0

    @property
    def id(self) -> str:
        return self.room.id

    @property
    def name(self) -> str:
        return self.room.name

    @property
    def published_area(self) -> float:
        return self.room.published_area

    @property
    def area_groups(self) -> frozenset[str]:
        return self.room.area_groups

    @cached_property
    def seed(self) -> Point:
        """The interior point identifying this room's face.

        ``rooms[].seed`` when the spec supplies one (mandatory in practice for L-shaped
        rooms), otherwise derived from the room's declared boundary walls and validated to
        fall inside exactly one face.
        """
        if self.room.seed is not None:
            return Point(point_m(self.room.seed))
        return self._derived_seed()

    def _derived_seed(self) -> Point:
        faces = self.network.faces()
        boundary = frozenset(self.room.boundary)
        adjacency = {id(face): self.network.adjacent_wall_ids(face) for face in faces}
        matches = [face for face in faces if adjacency[id(face)] == boundary]
        if len(matches) == 1:
            return matches[0].representative_point()

        # Second tier: the room may list a wall that turns out not to touch the face (a
        # transcription over-count). A face bounded only by declared walls still matches.
        subsets = [face for face in faces if adjacency[id(face)] <= boundary]
        if len(subsets) == 1:
            return subsets[0].representative_point()

        naive = self._naive_centroid()
        hits = [face for face in faces if face.contains(naive)]
        if len(hits) == 1:
            return naive

        raise GeometryError(
            f"room {self.room.id} ({self.room.name!r}) has no seed and one could not be "
            f"derived: of the {len(faces)} enclosed faces, {len(matches)} are bounded by "
            f"exactly its {len(boundary)} boundary walls and {len(subsets)} by a subset of "
            f"them, and the centroid of those walls falls inside {len(hits)} faces. Add an "
            f'explicit "seed": [x, y] in millimetres inside the room -- the schema has the '
            f"field for precisely this case."
        )

    def _naive_centroid(self) -> Point:
        """Centroid of the room's boundary wall centrelines.

        A weak last resort, and deliberately the last one tried: exterior walls run the
        full length of the building, so for a room in the middle of a plan this centroid
        is close to the centroid of the whole storey and can easily land in a neighbour.
        It is only trusted when it falls inside exactly one face.
        """
        lines = [
            self.network.wall_by_id[wall_id].centreline
            for wall_id in self.room.boundary
            if wall_id in self.network.wall_by_id
        ]
        if not lines:
            raise GeometryError(
                f"room {self.room.id} ({self.room.name!r}): none of its boundary walls "
                f"{list(self.room.boundary)} exist on level {self.room.level!r}."
            )
        return unary_union(lines).convex_hull.centroid

    def measured_to(self, measure_to: MeasureTo | None = None) -> MeasureTo:
        """Which face this room is actually measured to, given a request.

        Precedence, and the middle term is the whole point:

        1. ``rooms[].measure_to.face`` in the spec, if the room declares one;
        2. otherwise ``measure_to`` if the caller asked for one;
        3. otherwise the model-wide default chosen at build time.

        A room's declared face **wins over an explicit request** because it is a statement
        about that room's boundaries, not a preference: A_R4 (Schody) is bounded by a slab
        opening -- a void edge and two guards -- with no plaster on any edge to deduct, so
        there is no such thing as its finish face to ask for. T15's allowance sweep and
        T08's structure-vs-finish diagnosis therefore hold it at structure automatically,
        which is exactly what T18 found the drawing supports. The carve-out is one room
        deep and it lives in ``spec/attic.json``, justified in that room's ``note``; there
        is deliberately no room id anywhere in this module.
        """
        if measure_to is not None:
            _check_measure_to(measure_to)
        declared = self.room.measure_to.face if self.room.measure_to is not None else None
        if declared is not None:
            _check_measure_to(declared)
            return declared  # type: ignore[return-value]
        return measure_to or self.default_measure_to

    def polygon(self, measure_to: MeasureTo | None = None) -> Polygon:
        """This room's floor polygon, measured to the face :meth:`measured_to` resolves.

        ``measure_to=None`` uses the model-wide default chosen at build time, so T15 can
        sweep the convention by rebuilding the model rather than by editing call sites.
        """
        face = self.measured_to(measure_to)
        structure = self.network.face_containing(self.seed)
        base = structure if face == "structure" else self._finish_face(structure)
        return self._extended_under_guards(base)

    def _extended_under_guards(self, base: Polygon) -> Polygon:
        """Grow ``base`` across each ``measure_to.extends_under`` wall to its far face.

        A balustrade at the head of a slab opening is a rail standing *above* the void,
        not a boundary of it, so the opening's plan projection runs to the slab edge
        underneath the guard. Polygonisation cannot express that -- a wall of non-zero
        thickness always takes its own footprint out of both neighbours -- so the room
        says so in the spec instead and the footprint is handed back to it here.

        The band is recovered by sliding ``base`` bodily across the wall and intersecting
        with the wall's own footprint, which clips it to this room's extent along the wall
        and to the wall's thickness across it, without assuming the room is rectangular.
        """
        wall_ids = self.room.measure_to.extends_under if self.room.measure_to else ()
        if not wall_ids:
            return base

        grown: BaseGeometry = base
        for wall_id in wall_ids:
            wall = self.network.wall_by_id.get(wall_id)
            if wall is None:
                raise GeometryError(
                    f"room {self.room.id} ({self.room.name!r}): measure_to.extends_under "
                    f"names wall {wall_id!r}, which is not on level {self.room.level!r}."
                )
            grown = unary_union([grown, self._guard_band(base, wall)])

        if not isinstance(grown, Polygon):
            raise GeometryError(
                f"room {self.room.id} ({self.room.name!r}): extending under "
                f"{list(wall_ids)} produced a {grown.geom_type}, not a single polygon. "
                f"The named walls must lie along this room's boundary."
            )
        return grown

    def _guard_band(self, base: Polygon, wall: WallSolid) -> BaseGeometry:
        """The strip of ``wall``'s footprint that lies across ``base``'s edge."""
        if not wall.is_axis_aligned:
            raise GeometryError(
                f"room {self.room.id} ({self.room.name!r}): measure_to.extends_under names "
                f"wall {wall.id!r}, which is oblique. Sliding a face across a wall is only "
                f"exact at right angles -- see the module docstring on junction extensions."
            )
        nx, ny = wall.normal
        # Slide *towards* the wall: the room sits on one side of the centreline, so the
        # sign of the normal component of (room -> wall) says which way that is.
        seed = base.representative_point()
        start = wall.centreline.coords[0]
        side = (seed.x - start[0]) * nx + (seed.y - start[1]) * ny
        step = -math.copysign(wall.thickness_m, side)
        return wall.footprint().intersection(translate(base, xoff=nx * step, yoff=ny * step))

    def _finish_face(self, structure: Polygon) -> Polygon:
        """The same face, re-polygonised with every wall grown by the finish allowance.

        Selected by containment in the structure face rather than by the seed: the finish
        face is a strict subset of the structure face, but a representative point can sit
        within the allowance of an edge, so seed containment is not guaranteed.
        """
        candidates = [
            candidate
            for candidate in self.network.faces(self.finish_allowance_m)
            if structure.contains(candidate.representative_point())
        ]
        if not candidates:
            raise GeometryError(
                f"room {self.room.id} ({self.room.name!r}): no face survives insetting the "
                f"walls by the {self.finish_allowance_m * 1000:.0f} mm finish allowance. "
                f"The room is {structure.area:.4f} m2 to raw structure -- is the allowance "
                f"larger than the room?"
            )
        return max(candidates, key=lambda candidate: candidate.area)

    def area(self, measure_to: MeasureTo | None = None) -> float:
        """Floor area in m2, not rounded."""
        return self.polygon(measure_to).area


# --------------------------------------------------------------------------------------
# Levels and the model
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LevelGeometry:
    """One storey: its wall network and its rooms."""

    level: Level
    network: WallNetwork
    rooms: tuple[RoomGeometry, ...]

    @property
    def id(self) -> str:
        return self.level.id

    @property
    def elevation_m(self) -> float:
        return to_m(self.level.elevation)

    @property
    def ceiling_height_m(self) -> float:
        return to_m(self.level.ceiling_height)

    @cached_property
    def room_by_id(self) -> dict[str, RoomGeometry]:
        return {room.id: room for room in self.rooms}

    def envelope(self) -> BaseGeometry:
        """Outer envelope of the storey.

        Deliberately takes no ``measure_to``: the finish allowance is an *interior*
        measurement convention, and applying it here would push the outside of the
        building outwards, which is not a thing that happens. The outer face of the
        exterior wall is already the finished face, because the 15 mm render is a layer of
        the 465 mm build-up.
        """
        return self.network.envelope()


class Model:
    """The built 2D model: one :class:`LevelGeometry` per storey.

    ``measure_to`` is the model-wide default measurement face. It is a *parameter*, not a
    constant: build the model twice to compare conventions (T08's uniform-offset
    diagnosis), or sweep it in T15.
    """

    def __init__(
        self,
        spec: Any,
        *,
        measure_to: MeasureTo = "structure",
        finish_allowance_mm: float | None = None,
    ) -> None:
        _check_measure_to(measure_to)
        self.spec = spec
        self.measure_to: MeasureTo = measure_to
        self.finish_allowance_mm = (
            finish_allowance_mm
            if finish_allowance_mm is not None
            else _finish_allowance_mm(spec)
        )
        self.finish_allowance_m = to_m(self.finish_allowance_mm)

        walls = _spec_walls(spec)
        rooms = _spec_rooms(spec)
        levels = _spec_levels(spec)

        self.levels: dict[str, LevelGeometry] = {}
        for level in levels:
            network = WallNetwork.from_walls([w for w in walls if w.level == level.id])
            self.levels[level.id] = LevelGeometry(
                level=level,
                network=network,
                rooms=tuple(
                    RoomGeometry(
                        room=room,
                        network=network,
                        finish_allowance_m=self.finish_allowance_m,
                        default_measure_to=measure_to,
                        floor_elevation_m=to_m(level.elevation),
                    )
                    for room in rooms
                    if room.level == level.id
                ),
            )

    def __repr__(self) -> str:
        return (
            f"<Model measure_to={self.measure_to!r} "
            f"finish_allowance={self.finish_allowance_mm} mm "
            f"levels={sorted(self.levels)} rooms={len(self.rooms)}>"
        )

    def level(self, level_id: str) -> LevelGeometry:
        try:
            return self.levels[level_id]
        except KeyError:
            raise GeometryError(
                f"no level {level_id!r} in the model (have: {sorted(self.levels)})"
            ) from None

    @property
    def rooms(self) -> tuple[RoomGeometry, ...]:
        return tuple(room for level in self.levels.values() for room in level.rooms)

    def room(self, room_id: str) -> RoomGeometry:
        for level in self.levels.values():
            if room_id in level.room_by_id:
                return level.room_by_id[room_id]
        raise GeometryError(
            f"no room {room_id!r} in the model (have: {sorted(r.id for r in self.rooms)})"
        )

    def with_measure_to(self, measure_to: MeasureTo) -> Model:
        """A model over the same spec measured to the other face. T15's sweep."""
        return Model(
            self.spec,
            measure_to=measure_to,
            finish_allowance_mm=self.finish_allowance_mm,
        )


def build_model(
    spec: Any,
    *,
    measure_to: MeasureTo = "structure",
    finish_allowance_mm: float | None = None,
) -> Model:
    """Build the 2D model from a validated spec.

    Tolerates a spec whose levels carry no walls or rooms yet: transcription lands after
    this module does, and an empty level must not be an error.
    """
    return Model(spec, measure_to=measure_to, finish_allowance_mm=finish_allowance_mm)


#: ``tests/conftest.py``'s ``model`` fixture looks for ``build`` first, then ``build_model``.
build = build_model


def room_polygon(
    spec: Any,
    room_id: str,
    *,
    measure_to: MeasureTo,
    finish_allowance_mm: float | None = None,
) -> Polygon:
    """One room's polygon, measured to the requested face.

    ``measure_to`` is keyword-only and has **no default**: which face a room is measured to
    is the single largest lever on every published area in this project, and it must be a
    stated choice at every call site. Prefer :func:`build_model` when you need more than
    one room -- this rebuilds the model on each call.
    """
    _check_measure_to(measure_to)
    model = build_model(spec, measure_to=measure_to, finish_allowance_mm=finish_allowance_mm)
    return model.room(room_id).polygon(measure_to)


# --------------------------------------------------------------------------------------
# Spec accessors -- accept either a typed Spec or the raw merged mapping
# --------------------------------------------------------------------------------------


def _spec_walls(spec: Any) -> tuple[Wall, ...]:
    if hasattr(spec, "walls"):
        return tuple(spec.walls)
    return tuple(Wall.from_raw(raw) for raw in spec.get("walls", ()))


def _spec_rooms(spec: Any) -> tuple[Room, ...]:
    if hasattr(spec, "rooms"):
        return tuple(spec.rooms)
    return tuple(Room.from_raw(raw) for raw in spec.get("rooms", ()))


def _spec_levels(spec: Any) -> tuple[Level, ...]:
    if hasattr(spec, "levels"):
        return tuple(spec.levels)
    return tuple(Level.from_raw(raw) for raw in spec.get("levels", ()))


def _construction(spec: Any) -> Mapping[str, Any]:
    if hasattr(spec, "construction"):
        return spec.construction
    return spec.get("construction", {})


def _finish_allowance_mm(spec: Any) -> float:
    value = _construction(spec).get("finish_allowance")
    return DEFAULT_FINISH_ALLOWANCE_MM if value is None else value


# --------------------------------------------------------------------------------------
# The sloping attic ceiling
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class SlopedCeiling:
    """The underside of a symmetric gable roof, as a function of plan position.

    Everything is metres, absolute in the shared frame (the +-0.00 ground-floor datum).

    WHICH PLANE THIS IS
    -------------------
    This is the **ceiling** plane -- the inside of the roof, which is what a height band
    is measured to. It is *not* ``springing = attic_floor + knee_wall +
    roof_buildup_vertical = 3610`` from ``spec/meta.json``: that is the roof's **outer**
    plane at the wall face, 280 mm of build-up above the ceiling. Confusing the two is the
    exact conflation that produced T17's phantom roof discrepancy, so it is worth being
    blunt: for banding, the ceiling springs from the **top of the knee wall**, i.e.
    ``attic_floor + knee_wall = 3040 + 290 = 3330``.

    The check is T17's measured contours. With the ceiling springing at knee-wall top,
    35 deg predicts the 1.4 m contour at 1.585 m and the 2.2 m contour at 2.728 m from the
    interior wall face, against 1.589 m and 2.726 m measured off ``plan_attic.png`` -- 4 mm
    and 2 mm. Using 3610 instead would put them at 1.185 m and 2.328 m and miss by 0.4 m.
    """

    ridge_axis: Literal["x", "y"]
    #: Coordinate of the ridge line on the axis perpendicular to ``ridge_axis``.
    ridge_coord_m: float
    #: Horizontal distance from the ridge line to the springing line (half the span).
    springing_offset_m: float
    #: Absolute elevation of the ceiling plane at the springing line.
    springing_elevation_m: float
    pitch_deg: float

    @cached_property
    def slope(self) -> float:
        """tan(pitch). Rise per metre travelled towards the ridge."""
        return math.tan(math.radians(self.pitch_deg))

    @cached_property
    def ridge_elevation_m(self) -> float:
        return self.springing_elevation_m + self.springing_offset_m * self.slope

    def offset_of(self, point: Sequence[float]) -> float:
        """Perpendicular distance from the ridge line to a plan point, metres."""
        coord = point[1] if self.ridge_axis == "x" else point[0]
        return abs(coord - self.ridge_coord_m)

    def elevation_at_offset(self, offset_m: float) -> float:
        """Ceiling elevation at a given distance from the ridge line."""
        return self.ridge_elevation_m - abs(offset_m) * self.slope

    def elevation_at(self, point: Sequence[float]) -> float:
        return self.elevation_at_offset(self.offset_of(point))

    def height_at(self, point: Sequence[float], floor_elevation_m: float) -> float:
        """Clear height above a floor at a plan point, metres."""
        return self.elevation_at(point) - floor_elevation_m

    def offset_for_height(self, height_m: float, floor_elevation_m: float) -> float:
        """Distance from the ridge at which the clear height falls to ``height_m``.

        The inverse of :meth:`height_at`. Larger height -> smaller offset. May come back
        negative (the ridge itself is lower than ``height_m``) or larger than the span
        (the whole floor is higher than ``height_m``); callers clamp.
        """
        if self.slope <= 0.0:
            raise GeometryError(
                f"pitch {self.pitch_deg} deg gives a flat ceiling; height banding has no "
                f"contour to place."
            )
        return (self.ridge_elevation_m - floor_elevation_m - height_m) / self.slope

    def contour_distance_from_springing(self, height_m: float, floor_elevation_m: float) -> float:
        """Where a height contour sits measured from the springing line, not the ridge.

        This is the form T17 measured (``d140 = 1.589``, ``d220 = 2.726`` from the interior
        face of the knee wall), so it is the form to cross-check against.
        """
        return self.springing_offset_m - self.offset_for_height(height_m, floor_elevation_m)

    @classmethod
    def from_spec(
        cls,
        spec: Any,
        *,
        ridge_coord_mm: float,
        springing_offset_mm: float,
        ridge_axis: Literal["x", "y"] | None = None,
        springing_elevation_mm: float | None = None,
    ) -> SlopedCeiling:
        """Build from ``roof`` + ``construction`` + the attic level.

        ``springing_elevation_mm`` defaults to attic floor + knee wall (the ceiling plane
        at the top of the knee wall) -- see this class's docstring for why that and not
        the 3610 outer-plane figure.
        """
        roof = spec.roof if hasattr(spec, "roof") else None
        pitch = roof.pitch_deg if roof is not None else spec["roof"]["pitch_deg"]
        spec_axis = roof.ridge_axis if roof is not None else spec["roof"].get("ridge_axis")
        axis = ridge_axis or spec_axis
        if axis not in ("x", "y"):
            raise GeometryError(
                f"ridge_axis is {axis!r}; it must be 'x' or 'y' to place the height bands."
            )
        if springing_elevation_mm is None:
            attic = next(
                (level for level in _spec_levels(spec) if level.id == "attic"),
                None,
            )
            if attic is None:
                raise GeometryError("no attic level in the spec; cannot derive the springing.")
            knee = _construction(spec).get("knee_wall_height")
            if knee is None:
                raise GeometryError(
                    "construction.knee_wall_height is missing; it is the ceiling springing "
                    "for the 1.4 m / 2.2 m banding."
                )
            springing_elevation_mm = attic.elevation + knee
        return cls(
            ridge_axis=axis,
            ridge_coord_m=to_m(ridge_coord_mm),
            springing_offset_m=to_m(springing_offset_mm),
            springing_elevation_m=to_m(springing_elevation_mm),
            pitch_deg=pitch,
        )

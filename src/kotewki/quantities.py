"""quantities.py -- area take-off over the geometry kernel (T07).

Everything here is a thin, explicit layer over :mod:`kotewki.geometry`. It exists so that
the publisher's *definitions* -- which aggregate a room belongs to, which face it is
measured to, how a sloping ceiling is counted -- live in one readable place instead of
being re-derived inside each validator.

The three definitions that are easy to get subtly wrong, from README.md's reconciliation::

    163.57 (usable) - 32.88 (attic) - 7.31 (boiler) = 123.38
    123.38 + 3.64 (stairs)                          = 127.02   net area
    116.18 (ground) + 14.51 + 14.67 + 18.21         = 163.57   usable area

so **powierzchnia uzytkowa excludes the stairs and powierzchnia netto includes them**, and
net area additionally excludes the boiler room and the two `Strych ocieplony` rooms. None
of that is inferred here: ``rooms[].area_groups`` in the spec states it per room, and this
module only reads it.

Areas are floats in m2 and are never rounded. Comparisons belong in the assertions.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from kotewki.geometry import (
    GeometryError,
    LevelGeometry,
    MeasureTo,
    Model,
    RoomGeometry,
    SlopedCeiling,
)

__all__ = [
    "DEFAULT_HEIGHT_BANDS",
    "HeightBand",
    "area_in_group",
    "banded_polygons",
    "footprint_area",
    "footprint_polygon",
    "net_area",
    "room_area",
    "sloped_band_areas",
    "usable_area",
    "usable_area_sloped",
]

#: PN-ISO 9836 sloping-ceiling bands, highest threshold first: at or above 2.2 m of clear
#: height a floor counts in full, from 1.4 m up to 2.2 m it counts at 50 %, and below
#: 1.4 m it does not count at all.
#:
#: **1.4, not 1.9.** An earlier draft of the project docs had 1.9 m, which is the wrong
#: norm. 1.4/2.2 is confirmed twice: Archon states PN-ISO 9836 explicitly, and
#: ``plan_attic.png`` prints its ceiling-height contours labelled ``140`` and ``220``.
#: Passed as a parameter everywhere below so a different norm can be tried without an
#: edit -- but do not change this default without a source.
DEFAULT_HEIGHT_BANDS: tuple[tuple[float, float], ...] = ((2.2, 1.0), (1.4, 0.5))


# --------------------------------------------------------------------------------------
# Room and level areas
# --------------------------------------------------------------------------------------


def room_area(room: RoomGeometry | BaseGeometry, measure_to: MeasureTo | None = None) -> float:
    """Floor area of a room in m2.

    ``measure_to`` selects the measurement face -- ``"structure"`` (raw block, what the
    printed chains dimension) or ``"finish"`` (plastered face, what Archon publishes). It
    defaults to the face the model was built with, so a caller sweeping the convention
    changes it in one place.
    """
    if isinstance(room, RoomGeometry):
        return room.polygon(measure_to).area
    if isinstance(room, BaseGeometry):
        if measure_to is not None:
            raise GeometryError(
                "room_area() was given a bare polygon and a measure_to; the measurement "
                "face has to be chosen when the polygon is built, not after."
            )
        return room.area
    raise TypeError(f"room_area() expects a RoomGeometry or a shapely geometry, got {type(room)}")


def footprint_polygon(level: LevelGeometry) -> BaseGeometry:
    """Outer envelope of a storey -- the *pow. zabudowy* outline (published 154.42 m2).

    Taken from the wall solids with their interior voids filled, so it is the outside of
    the exterior wall including the 15 mm render (``exterior_wall.thickness`` = 465 counts
    the render as a layer). PN-ISO 9836 measures pow. zabudowy on the finished building,
    which is the same face.
    """
    return level.network.envelope()


def footprint_area(level: LevelGeometry) -> float:
    """Area of :func:`footprint_polygon` in m2."""
    return footprint_polygon(level).area


def usable_area(
    level: LevelGeometry | Model,
    include_stairs: bool = False,
    *,
    measure_to: MeasureTo | None = None,
    sloped_ceiling: SlopedCeiling | None = None,
    bands: Sequence[tuple[float, float]] = DEFAULT_HEIGHT_BANDS,
) -> float:
    """Powierzchnia uzytkowa: the sum of every room tagged ``usable``.

    ``include_stairs`` adds the rooms that are in ``net`` but not in ``usable`` -- i.e.
    Schody. This is **not** the same as :func:`net_area`, which additionally drops the
    boiler room and the two `Strych ocieplony` rooms; the flag exists because the two
    published aggregates differ by exactly the stairs and it is the difference that is
    diagnostic.

    ``sloped_ceiling`` applies the 1.4 m / 2.2 m banding to every room on the level, which
    is what the attic needs; leave it None for the ground floor.
    """
    rooms = _rooms_of(level)
    selected = [
        room
        for room in rooms
        if room.room.in_usable_area
        or (include_stairs and room.room.in_net_area and not room.room.in_usable_area)
    ]
    return _sum_areas(selected, measure_to, sloped_ceiling, bands)


def net_area(
    level: LevelGeometry | Model,
    *,
    measure_to: MeasureTo | None = None,
    sloped_ceiling: SlopedCeiling | None = None,
    bands: Sequence[tuple[float, float]] = DEFAULT_HEIGHT_BANDS,
) -> float:
    """Powierzchnia netto: every room tagged ``net``. Includes the stairs."""
    rooms = [room for room in _rooms_of(level) if room.room.in_net_area]
    return _sum_areas(rooms, measure_to, sloped_ceiling, bands)


def area_in_group(
    level: LevelGeometry | Model,
    group: str,
    *,
    measure_to: MeasureTo | None = None,
    sloped_ceiling: SlopedCeiling | None = None,
    bands: Sequence[tuple[float, float]] = DEFAULT_HEIGHT_BANDS,
) -> float:
    """Sum of the rooms in one ``area_groups`` tag: usable, net, attic or boiler."""
    rooms = [room for room in _rooms_of(level) if group in room.area_groups]
    return _sum_areas(rooms, measure_to, sloped_ceiling, bands)


def _rooms_of(level: LevelGeometry | Model) -> tuple[RoomGeometry, ...]:
    """Rooms of a storey, or of every storey when handed the whole model."""
    return tuple(level.rooms)


def _sum_areas(
    rooms: Iterable[RoomGeometry],
    measure_to: MeasureTo | None,
    sloped_ceiling: SlopedCeiling | None,
    bands: Sequence[tuple[float, float]],
) -> float:
    total = 0.0
    for room in rooms:
        polygon = room.polygon(measure_to)
        if sloped_ceiling is None:
            total += polygon.area
        else:
            total += usable_area_sloped(
                polygon,
                sloped_ceiling,
                room.floor_elevation_m,
                bands=bands,
            )
    return total


# --------------------------------------------------------------------------------------
# The sloping attic ceiling -- PN-ISO 9836 height banding
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HeightBand:
    """One height band of a sloping-ceiling room."""

    #: Clear height at the lower edge of the band, metres. 0.0 for the excluded band.
    min_height_m: float
    #: Fraction of the band's floor area that counts towards usable area.
    factor: float
    #: The part of the room polygon in this band.
    polygon: BaseGeometry

    @property
    def area(self) -> float:
        return self.polygon.area

    @property
    def counted_area(self) -> float:
        return self.polygon.area * self.factor


def banded_polygons(
    polygon: BaseGeometry,
    roof: SlopedCeiling,
    floor_elevation: float = 0.0,
    *,
    bands: Sequence[tuple[float, float]] = DEFAULT_HEIGHT_BANDS,
) -> tuple[HeightBand, ...]:
    """Split a room polygon into its height bands, highest band first.

    Under a symmetric gable the clear height depends only on the distance from the ridge
    line, so every band is a strip parallel to the ridge and the split is exact -- there is
    no sampling or rasterisation anywhere in this calculation.

    The last band returned is always the excluded (factor 0) remainder, even when empty, so
    the bands always tile the input polygon.
    """
    ordered = sorted(bands, key=lambda item: item[0], reverse=True)
    result: list[HeightBand] = []
    consumed = 0.0
    for min_height, factor in ordered:
        offset = max(roof.offset_for_height(min_height, floor_elevation), 0.0)
        strip = _ridge_strip(polygon, roof, consumed, offset)
        result.append(HeightBand(min_height_m=min_height, factor=factor, polygon=strip))
        consumed = max(consumed, offset)
    remainder = polygon.difference(unary_union([band.polygon for band in result]))
    result.append(HeightBand(min_height_m=0.0, factor=0.0, polygon=remainder))
    return tuple(result)


def _ridge_strip(
    polygon: BaseGeometry,
    roof: SlopedCeiling,
    inner_offset_m: float,
    outer_offset_m: float,
) -> BaseGeometry:
    """The part of ``polygon`` between two distances from the ridge line."""
    if outer_offset_m <= inner_offset_m:
        return Polygon()
    minx, miny, maxx, maxy = polygon.bounds
    pad = 1.0
    ridge = roof.ridge_coord_m
    strips = []
    for sign in (1, -1):
        near = ridge + sign * inner_offset_m
        far = ridge + sign * outer_offset_m
        lo, hi = sorted((near, far))
        if roof.ridge_axis == "x":
            strips.append(box(minx - pad, lo, maxx + pad, hi))
        else:
            strips.append(box(lo, miny - pad, hi, maxy + pad))
    return polygon.intersection(unary_union(strips))


def usable_area_sloped(
    polygon: BaseGeometry,
    roof: SlopedCeiling,
    floor_elevation: float = 0.0,
    *,
    bands: Sequence[tuple[float, float]] = DEFAULT_HEIGHT_BANDS,
) -> float:
    """Usable area of a room under a sloping ceiling, in m2.

    **Below 1.4 m counts 0 %, 1.4-2.2 m counts 50 %, above 2.2 m counts 100 %.**

    ``roof`` is a :class:`~kotewki.geometry.SlopedCeiling` carrying absolute elevations,
    and ``floor_elevation`` is the storey's finished floor level in metres (3.04 for the
    attic). Both are metres: this function is downstream of the kernel's single mm -> m
    conversion and never sees a millimetre.
    """
    return sum(band.counted_area for band in banded_polygons(
        polygon, roof, floor_elevation, bands=bands
    ))


def sloped_band_areas(
    polygon: BaseGeometry,
    roof: SlopedCeiling,
    floor_elevation: float = 0.0,
    *,
    bands: Sequence[tuple[float, float]] = DEFAULT_HEIGHT_BANDS,
) -> dict[str, float]:
    """Diagnostic breakdown behind :func:`usable_area_sloped`.

    Returns the raw floor area, the area in each band, and the counted total. When an
    attic room misses its published figure this says immediately whether the floor polygon
    is wrong (``floor`` is off) or the banding is (``floor`` agrees, ``counted`` does not)
    -- which is exactly the discrimination the optional ``floor_area_m2`` field on a room
    exists to support.
    """
    split = banded_polygons(polygon, roof, floor_elevation, bands=bands)
    out = {"floor": polygon.area, "counted": sum(band.counted_area for band in split)}
    for band in split:
        out[f"band_{band.min_height_m:g}"] = band.area
    return out

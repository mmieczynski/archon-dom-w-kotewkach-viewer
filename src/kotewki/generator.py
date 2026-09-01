"""generator.py -- the 3D generator (T11).

Pure function: ``spec -> trimesh.Scene``. Nothing in this module is hand-authored
geometry: every vertex is computed from ``spec/*.json`` through T07's 2D kernel
(:mod:`kotewki.geometry`), which supplies wall footprints, junction extensions, storey
envelopes and room faces. This module only adds the third dimension.

UNITS
-----
The spec is integer millimetres; the kernel converts to metres once, at its own boundary
(:func:`kotewki.geometry.to_m`). Everything here is **metres**, so the scene handed to T12
is already in glTF's unit and the exporter emits no scale transform at all.

THE ROOF -- READ THIS BEFORE CHANGING ANY NUMBER
------------------------------------------------
The ridge is an **output**::

    springing = attic_floor 3040 + knee_wall 290 + roof_buildup_vertical 280 = 3610 mm
    ridge     = springing + (span / 2) * tan(pitch_deg)                      = 6761 mm

and it is compared, afterwards, to the printed +6.77 m and to the published 7.09 m
building height. Never position the ridge to hit either figure. Both T09 assertions are
only meaningful because ``ridge_elevation_mm`` is derived from the pitch; assigning it
turns them into tautologies and deletes the project's only roof check. The single derived
input in that chain is ``roof_buildup_vertical`` -- see ``docs/roof-resolution.md``.

There are **three parallel planes** in this building and using the wrong one silently
corrupts a different check each time:

===============================  ========  =========================================
Plane                            Elevation Used for
===============================  ========  =========================================
Ceiling / knee wall top          3330      Attic area banding **only** (T07/T08 own it)
Roof outer plane at wall face    3610      Ridge and roof construction (this module)
Fascia underside at overhang     2880      The eave assertion only
===============================  ========  =========================================

``roof_buildup_vertical`` (280 mm, measured at the **wall face**) and ``fascia_depth``
(310 mm, measured at the **overhang edge**) are different quantities. This module uses
the first to place the springing and the second as the vertical thickness of the roof
slab, so the modelled fascia underside lands on the printed +2.88 m mark exactly. They
are never added together and never substituted for one another: collapsing them is what
produced the phantom roof discrepancy that cost T17 an entire investigation.

Known consequence, stated rather than hidden: the banding plane springs at 3330 on the
*interior* wall face (that is what T05's traced 1.4/2.2 contours measure, and it agrees
to 4 mm), while the roof plane springs at 3610 on the *outer* wall face with the ridge
4500 mm inboard. Those two definitions are 450 mm apart in plan, i.e. 315 mm in
elevation, so the roof modelled here sits ~285 mm above the ceiling plane the banding
uses -- which is, to 5 mm, the 280 mm build-up. The gap shows up as gable walls that run
315 mm higher than a banding-derived ceiling would put them. It is absorbed there because
that is the least visible place to put it, and because the ridge chain is the one with
printed checks at both ends.

BOOLEANS
--------
Openings are cut with **one batched difference per solid** (``trimesh.boolean.difference``
with manifold3d, which unions the cutters internally and applies a single subtraction).
Sequential per-opening booleans accumulate numerical degradation and are markedly slower.

DETERMINISM
-----------
Same spec in, byte-identical mesh out. Everything here iterates spec order or an
insertion-ordered dict; nothing iterates a ``set``, no seeds, no timestamps, no
``id()``-dependent ordering, no wall-clock anything. T13's golden-image diff depends on
this: a non-deterministic generator turns the overlay into a flaky test, and a flaky test
gets disabled, which would remove the project's only guard against building a perfect
mirror image of the right house.

SCENE ORGANISATION
------------------
Node names are ``level / category / id [/ part]``::

    ground/walls/G_W1/porotherm      attic/walls/A_W2/porotherm    roof/slab
    ground/walls/G_W1/eps            attic/walls/A_W7/structure    roof/windows/R_RW1
    ground/walls/G_W1/render         attic/chimneys/A_W9/structure
    ground/openings/windows/G_O1     attic/openings/windows/A_O1
    ground/openings/doors/G_O5       attic/slabs/floor
    ground/slabs/plinth              attic/rooms/A_R1
    ground/rooms/G_R3

so T14 can toggle by prefix (a level, a category, one wall, one material layer) and T12
can assert the names survived glTF export. Room names, published areas and provenance
ride along in each mesh's ``metadata``.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Literal

import numpy as np
import shapely
import trimesh
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry

from kotewki.geometry import Model, WallSolid, build_model, to_m
from kotewki.spec import Opening, Wall

__all__ = [
    "CHIMNEY_ABOVE_RIDGE_MM",
    "GLAZING_THICKNESS_MM",
    "ROOF_WINDOWS",
    "GeneratorError",
    "RoofGeometry",
    "RoofWindow",
    "build",
    "build_scene",
    "roof_geometry",
]


class GeneratorError(Exception):
    """The scene could not be built. Always says which element and what was expected."""


# --------------------------------------------------------------------------------------
# Tunables. Every one of these is a *modelling* choice with no printed source; none of
# them participates in a dimensional assertion. They are named constants rather than
# literals so that a reader can see the whole set at once and so a test can move one.
# --------------------------------------------------------------------------------------

#: How far a chimney stack stands above the ridge, millimetres. Not published. Both
#: elevation_side_1.png and elevation_side_2.png show the two stacks clearly above the
#: ridge line; 600 mm is the usual minimum freeboard for a stack within 1.5 m of a ridge.
CHIMNEY_ABOVE_RIDGE_MM = 600

#: Nominal thickness of a glazing pane / door leaf, millimetres. Cosmetic.
GLAZING_THICKNESS_MM = 40

#: How far an opening cutter reaches past each wall face, millimetres. Must exceed the
#: render layer so the cut goes through every layer including the outermost one.
CUTTER_MARGIN_MM = 200

#: Thickness of the per-room floor plate, millimetres. Plates exist to give T14 something
#: to hang a room label and a highlight on; they sit *on* the finished floor rather than
#: in it, so they share no face with the slab below and cannot z-fight with it.
ROOM_PLATE_THICKNESS_MM = 2

#: Vertical clearance used when a cutter would end exactly on a solid's own face.
#: Coplanar boolean faces are the classic source of zero-area triangles in the output,
#: which T12 asserts against.
COPLANAR_NUDGE_MM = 100


# --------------------------------------------------------------------------------------
# SCHEMA GAP 1: the three roof windows
#
# ``spec/schema.json`` requires every opening to name a host *wall*. These three are
# hosted by the roof plane, so there is nowhere in the spec to put them.
#
# RECOMMENDED FIX, and what this module is already written against: a sibling collection
# ``roof_openings[]``, shaped exactly like ``slab_openings[]`` --
#
#     {"id": "R_RW1", "kind": "roof_window", "slope": "south",
#      "bounds": [6574, 1149, 7354, 2446],          <- plan projection, shared-frame mm
#      "width": 780, "height_on_slope": 1600,       <- the printed '78/160' callout
#      "source_image": "data/source/plan_attic.png", "derived": true}
#
# An axis-aligned plan rectangle is *sufficient* and no plane/slope vector is needed: a
# roof window's jambs are cut square through the rafters and its head and sill are
# horizontal, so its projection onto the plan is exactly a rectangle, and the plane it
# lies in is already fully determined by ``roof.pitch_deg``, ``roof.ridge_axis`` and the
# derived ridge. ``slope`` says only which side of the ridge it is on, and is checked
# against the bounds rather than trusted. (A hipped or shed roof, or a window spanning
# the ridge, would need more -- see :meth:`_SceneBuilder._check_roof_window`, which
# refuses those cases loudly instead of modelling them wrongly.)
#
# :meth:`_SceneBuilder._roof_windows` reads ``spec["roof_openings"]`` whenever it is
# present, so the schema change is a drop-in: add the block and this constant stops being
# consulted. Until then the values below are traced from plan_attic.png:
#
#   '78/160' printed in the SOUTH roof slope over the Antresola; dashed outlines at plan
#   x 344-378, 380-415, 417-450 px -> centres x = 6964, 7855, 8733 mm; plan-projected
#   extent y = 1149..2446 mm, depth 1296 +- 12.
#
# The one cross-check available on numbers that live outside the spec: the projected
# depth ought to be the on-slope height times cos(pitch). It holds to 1.1 %
# (1600 * cos 35 = 1310.6 mm against a measured 1296 +- 12 mm) -- 0.6 px on a raster whose
# pixel is 24.46 mm, i.e. the two agree and are not distinguished. T05's original 1271 was
# a one-pixel error, measured to the inner faces of the two dashed lines rather than their
# centres; T18 re-measured it line-centre to line-centre and calibrated that convention in
# place, reproducing the same box's PRINTED 780 mm width to 0.07 px. The measured
# projection is what gets modelled -- it is the measurement -- and the implied on-slope
# height is published in ``scene.metadata['roof_windows']`` for a test to assert rather
# than buried here. See docs/T18-findings.md item 2.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class RoofWindow:
    """A roof-plane opening, as its plan projection. Millimetres, shared frame.

    ``bounds`` is ``[x0, y0, x1, y1]`` -- deliberately the same convention as
    ``slab_openings[].bounds``, so that the proposed schema entity needs no new geometry
    vocabulary. ``width`` and ``height_on_slope`` are the *printed callout* ("78/160"),
    carried alongside so the projection can be cross-checked against it instead of being
    assumed to agree with it.
    """

    id: str
    slope: Literal["south", "north"]
    bounds: tuple[float, float, float, float]
    width: float
    height_on_slope: float

    def fall_span(self, ridge_axis: Literal["x", "y"]) -> tuple[float, float]:
        """(min, max) of the plan projection on the axis the roof falls along."""
        return (
            (self.bounds[1], self.bounds[3])
            if ridge_axis == "x"
            else (self.bounds[0], self.bounds[2])
        )

    def plan_depth(self, ridge_axis: Literal["x", "y"]) -> float:
        low, high = self.fall_span(ridge_axis)
        return high - low

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> RoofWindow:
        """Build from a ``roof_openings[]`` entry once the schema grows one."""
        identifier = raw.get("id", "?")
        bounds = _required(raw, "bounds", f"roof_openings[{identifier}]")
        if len(bounds) != 4:
            raise GeneratorError(
                f"roof opening {identifier}: 'bounds' must be [x0, y0, x1, y1] in "
                f"millimetres, got {list(bounds)!r}."
            )
        return cls(
            id=identifier,
            slope=_required(raw, "slope", f"roof_openings[{identifier}]"),
            bounds=(bounds[0], bounds[1], bounds[2], bounds[3]),
            width=_required(raw, "width", f"roof_openings[{identifier}]"),
            height_on_slope=_required(
                raw, "height_on_slope", f"roof_openings[{identifier}]"
            ),
        )


def _traced_roof_window(identifier: str, centre: int, width: int) -> RoofWindow:
    """One of the traced roof windows, as the schema entity it should have been.

    Extent y 1149..2446 is T18's re-measurement, line centre to line centre; T05's
    1162..2433 was one pixel narrower and is the source of the retired 3.1 % residual.
    """
    return RoofWindow(
        id=identifier,
        slope="south",
        bounds=(centre - width // 2, 1149, centre + width // 2, 2446),
        width=width,
        height_on_slope=1600,
    )


#: Fallback for the missing ``spec["roof_openings"]`` block. Consulted only when the spec
#: has none; see the comment block above for the schema entity that retires it.
ROOF_WINDOWS: tuple[RoofWindow, ...] = tuple(
    _traced_roof_window(f"R_RW{index}", centre, 780)
    for index, centre in enumerate((6964, 7855, 8733), start=1)
)

# --------------------------------------------------------------------------------------
# SCHEMA GAP 2: chimney stacks are stored as walls
#
# A_W9, A_W10, G_W27 and G_W28 are chimney stacks (the ground pair also carries the
# fireplace mass) traced as walls, because ``walls[].type`` has no value for "this is not
# a wall, it is a stack, and it must not stop at the ceiling". They are identified below
# by an explicit id list, guarded against going stale -- see CHIMNEY_WALL_IDS for why
# matching on the transcribers' notes was tried and abandoned.
#
# RECOMMENDED FIX: add ``walls[].role`` with values like ``wall``/``chimney``/
# ``balustrade``, defaulting to ``wall``. That retires the id list entirely, and would
# let A_W7's balustrade nature be data rather than a consequence of its transcribed
# height. (A_W7 needs no special case here only because T05 already gave it an
# explicit ``height`` of 1100 mm.)
# --------------------------------------------------------------------------------------

#: Ids of the walls that are really chimney stacks.
#:
#: Matching on the transcribers' notes was tried first and is a trap: G_W22 and G_W25 both
#: *mention* the stack G_W27 in their notes ("between the chimney stack G_W27 and col
#: 613"), so a substring test promotes two ordinary partitions into stacks that shoot
#: through the roof. There is no field in the schema that distinguishes them, so the list
#: is explicit -- and guarded below, because a silently-wrong list is worse than no list.
CHIMNEY_WALL_IDS: tuple[str, ...] = ("G_W27", "G_W28", "A_W9", "A_W10")

#: A stack is drawn as solid masonry, so it is transcribed as a structural wall. Checked
#: rather than assumed: if a listed id turns out to be a partition, the list is stale.
CHIMNEY_WALL_TYPE = "structural"


def _is_chimney(wall: Wall) -> bool:
    if wall.id not in CHIMNEY_WALL_IDS:
        return False
    if wall.type != CHIMNEY_WALL_TYPE:
        raise GeneratorError(
            f"wall {wall.id} is listed in generator.CHIMNEY_WALL_IDS as a chimney stack "
            f"but its type is {wall.type!r}, not {CHIMNEY_WALL_TYPE!r}. Either the spec "
            f"renumbered its walls or the list is stale; extruding the wrong wall through "
            f"the roof would put a phantom stack on the elevation."
        )
    return True


# --------------------------------------------------------------------------------------
# Spec accessors -- accept a typed Spec or the raw merged mapping, as geometry.py does
# --------------------------------------------------------------------------------------


def _mapping(spec: Any, key: str) -> Mapping[str, Any]:
    """A raw spec block, whether ``spec`` is a :class:`~kotewki.spec.Spec` or a dict.

    Deliberately subscript-first: ``Spec`` is a Mapping over the merged document, and
    ``spec.roof`` is a *typed* view that drops the fields this module needs
    (``roof_buildup_vertical``, ``fascia_depth``, ``verge_overhang``) because they landed
    after T02 wrote it.
    """
    try:
        return spec[key]
    except (TypeError, KeyError) as exc:
        value = getattr(spec, key, None)
        if value is None:
            raise GeneratorError(f"the spec has no {key!r} block.") from exc
        return value


def _openings(spec: Any) -> tuple[Opening, ...]:
    if hasattr(spec, "openings"):
        return tuple(spec.openings)
    return tuple(Opening.from_raw(raw) for raw in spec.get("openings", ()))


def _required(source: Mapping[str, Any], key: str, what: str) -> Any:
    if key not in source:
        raise GeneratorError(
            f"{what}: '{key}' is missing from the spec. The 3D generator derives every "
            f"elevation from the spec and has no fallback for this one -- a default here "
            f"would be a hand-authored dimension."
        )
    return source[key]


# --------------------------------------------------------------------------------------
# The roof
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class RoofGeometry:
    """The gable roof, as a set of derived elevations. Millimetres in, metres out.

    Construction order is the point of this class: ``span``, ``pitch_deg`` and
    ``springing_elevation`` go in, :attr:`ridge_elevation_mm` comes out. There is no
    setter for the ridge and no path by which a printed ridge height can reach this
    object -- ``section_elevations.ridge`` is an assertion target and is never read here.
    """

    pitch_deg: float
    #: "x" means the ridge line runs parallel to x, so the roof falls in y.
    ridge_axis: Literal["x", "y"]
    #: Structural extent across the fall direction, millimetres (the span, 9000).
    span: float
    #: Coordinate of the low (structural) edge on the fall axis.
    cross_min: float
    #: Coordinate of the structural extent along the ridge axis.
    along_min: float
    along_max: float
    springing_elevation: float
    eaves_overhang: float
    verge_overhang: float
    roof_buildup_vertical: float
    fascia_depth: float

    # -- derived --------------------------------------------------------------------

    @cached_property
    def slope(self) -> float:
        """tan(pitch). Rise per millimetre travelled towards the ridge."""
        return math.tan(math.radians(self.pitch_deg))

    @cached_property
    def ridge_coord(self) -> float:
        """Where the ridge line sits on the fall axis: the middle of the span."""
        return self.cross_min + self.span / 2.0

    @cached_property
    def ridge_elevation_mm(self) -> float:
        """**The output.** springing + (span / 2) * tan(pitch). Never assigned."""
        return self.springing_elevation + (self.span / 2.0) * self.slope

    @cached_property
    def eave_fascia_underside_mm(self) -> float:
        """Fascia underside at the outer edge of the eaves overhang.

        ``springing - eaves_overhang * tan(pitch) - fascia_depth``. Printed +2.88 m; this
        is the only place ``fascia_depth`` enters a dimensional check, and it is a
        different quantity from ``roof_buildup_vertical``.
        """
        return self.springing_elevation - self.eaves_overhang * self.slope - self.fascia_depth

    def top_at(self, cross: float) -> float:
        """Outer roof plane elevation at a coordinate on the fall axis, millimetres."""
        return self.ridge_elevation_mm - abs(cross - self.ridge_coord) * self.slope

    def underside_at(self, cross: float) -> float:
        """Underside of the roof slab. Vertically ``fascia_depth`` below :meth:`top_at`.

        Using ``fascia_depth`` as the slab's vertical thickness is what puts the modelled
        fascia underside exactly on the printed +2.88 m mark at the overhang edge. The
        springing still comes from ``roof_buildup_vertical``; the two are used for the two
        different things they measure and are never interchanged.
        """
        return self.top_at(cross) - self.fascia_depth

    @cached_property
    def cross_max(self) -> float:
        return self.cross_min + self.span

    @cached_property
    def eaves_min(self) -> float:
        return self.cross_min - self.eaves_overhang

    @cached_property
    def eaves_max(self) -> float:
        return self.cross_max + self.eaves_overhang

    @cached_property
    def verge_min(self) -> float:
        return self.along_min - self.verge_overhang

    @cached_property
    def verge_max(self) -> float:
        return self.along_max + self.verge_overhang

    @cached_property
    def area_m2(self) -> float:
        """Sloped surface area of both planes including overhangs, m2.

        Plan area / cos(pitch). At 35 deg the ratio is 1.2208. Published 216.8 m2 against
        227.9 m2 from measured overhangs; T09 asserts this as a +-6 % sanity band and
        documents why -- see docs/roof-resolution.md, "Still open".
        """
        plan = (self.eaves_max - self.eaves_min) * (self.verge_max - self.verge_min)
        return to_m(to_m(plan)) / math.cos(math.radians(self.pitch_deg))

    # -- construction from the spec ---------------------------------------------------

    @classmethod
    def from_spec(cls, spec: Any, model: Model) -> RoofGeometry:
        """Assemble the roof inputs from ``spec/meta.json`` and the storey envelopes.

        The span and the plan extents are *measured off the wall network*, not typed in:
        the structural outline is whatever T04 and T05 transcribed, and if a wall moves
        the roof follows it.
        """
        roof = _mapping(spec, "roof")
        construction = _mapping(spec, "construction")
        levels = {level.id: level for level in model.levels.values()}
        if "attic" not in levels:
            raise GeneratorError(
                "no 'attic' level in the spec; the roof springs from the attic floor plus "
                "the knee wall and cannot be placed without it."
            )

        axis = _required(roof, "ridge_axis", "roof")
        if axis not in ("x", "y"):
            raise GeneratorError(f"roof.ridge_axis is {axis!r}; it must be 'x' or 'y'.")

        springing = _required(roof, "springing", "roof")
        if springing != "knee_wall_top":
            raise GeneratorError(
                f"roof.springing is {springing!r}; this generator only knows how to spring "
                f"a roof from 'knee_wall_top' (attic floor + knee wall + build-up). See "
                f"docs/roof-resolution.md before adding another rule."
            )

        minx, miny, maxx, maxy = _bounds_mm(model)
        if axis == "x":
            cross_min, cross_max, along_min, along_max = miny, maxy, minx, maxx
        else:
            cross_min, cross_max, along_min, along_max = minx, maxx, miny, maxy

        attic = levels["attic"].level
        return cls(
            pitch_deg=_required(roof, "pitch_deg", "roof"),
            ridge_axis=axis,
            span=cross_max - cross_min,
            cross_min=cross_min,
            along_min=along_min,
            along_max=along_max,
            springing_elevation=(
                attic.elevation
                + _required(construction, "knee_wall_height", "construction")
                + _required(roof, "roof_buildup_vertical", "roof")
            ),
            eaves_overhang=_required(roof, "eaves_overhang", "roof"),
            verge_overhang=_required(roof, "verge_overhang", "roof"),
            roof_buildup_vertical=_required(roof, "roof_buildup_vertical", "roof"),
            fascia_depth=_required(roof, "fascia_depth", "roof"),
        )


def _bounds_mm(model: Model) -> tuple[float, float, float, float]:
    """Structural plan bounds of the whole building, millimetres.

    The union of every storey envelope, so the ground floor's entrance recess (which
    makes that level's outline non-rectangular) cannot shrink the roof.
    """
    boxes = [level.network.envelope().bounds for level in model.levels.values()
             if not level.network.envelope().is_empty]
    if not boxes:
        raise GeneratorError(
            "the wall network is empty on every level, so the building has no plan extent "
            "and the roof span cannot be measured."
        )
    minx = min(item[0] for item in boxes) * 1000.0
    miny = min(item[1] for item in boxes) * 1000.0
    maxx = max(item[2] for item in boxes) * 1000.0
    maxy = max(item[3] for item in boxes) * 1000.0
    return minx, miny, maxx, maxy


def roof_geometry(spec: Any, model: Model | None = None) -> RoofGeometry:
    """The derived roof for a spec. Exposed so T09 can assert on it without a Scene."""
    return RoofGeometry.from_spec(spec, model if model is not None else build_model(spec))


# --------------------------------------------------------------------------------------
# Mesh primitives. Every solid in the scene comes out of one of these three.
# --------------------------------------------------------------------------------------


#: Snapping grid for plan polygons before extrusion, metres. One nanometre: far below any
#: real dimension (the spec is integer millimetres) and far above the float noise a
#: ``unary_union`` leaves behind.
SNAP_GRID_M = 1e-9


def _polygons(geometry: BaseGeometry) -> tuple[Polygon, ...]:
    if geometry.is_empty:
        return ()
    if isinstance(geometry, Polygon):
        return (geometry,)
    if isinstance(geometry, MultiPolygon):
        return tuple(geometry.geoms)
    return tuple(part for part in getattr(geometry, "geoms", ()) if isinstance(part, Polygon))


def _clean(geometry: BaseGeometry) -> BaseGeometry:
    """Snap a plan polygon to a nanometre grid and drop collinear vertices.

    Not cosmetic. ``WallNetwork.envelope()`` comes out of a ``unary_union`` and carries
    vertex pairs like ``(0.0, 0.45)`` and ``(2.78e-17, 0.45)`` -- 28 attometres apart.
    Extruding that produces two side quads separated by a zero-area sliver, the mesh is no
    longer watertight, and manifold3d then refuses the whole boolean with "Not all meshes
    are volumes". Snapping first is what makes every solid in the scene closed.

    Both steps are exact-by-construction rather than tolerant: the grid is nine orders of
    magnitude below a millimetre, and ``simplify(0)`` removes only vertices that lie
    exactly on the segment they interrupt.
    """
    return shapely.set_precision(geometry, SNAP_GRID_M).simplify(0.0)


def _extrude(geometry: BaseGeometry, z0_m: float, z1_m: float) -> trimesh.Trimesh:
    """Vertical extrusion of a plan polygon between two elevations, metres.

    Holes are carried through (the attic floor slab has one, over the Antresola void) and
    a MultiPolygon becomes one mesh with several bodies, each individually watertight --
    which is the form T12 asserts.
    """
    if z1_m <= z0_m:
        raise GeneratorError(
            f"cannot extrude between z0={z0_m:.4f} m and z1={z1_m:.4f} m: the top is not "
            f"above the bottom. A wall or slab has a non-positive height."
        )
    parts = [
        trimesh.creation.extrude_polygon(part, z1_m - z0_m)
        for part in _polygons(_clean(geometry))
        if part.area > 0.0
    ]
    if not parts:
        raise GeneratorError("cannot extrude an empty plan polygon.")
    mesh = parts[0] if len(parts) == 1 else trimesh.util.concatenate(parts)
    mesh.apply_translation([0.0, 0.0, z0_m])
    return _checked(mesh, f"extrusion of a {geometry.area:.4f} m2 plan polygon")


def _checked(mesh: trimesh.Trimesh, what: str) -> trimesh.Trimesh:
    """Every solid this module produces must be a closed volume, so say so immediately.

    manifold3d rejects a non-volume with "Not all meshes are volumes!", naming neither the
    mesh nor the reason. Failing here instead points at the element that is open, which is
    the difference between a five-minute fix and an afternoon.
    """
    if not mesh.is_volume:
        raise GeneratorError(
            f"{what} is not a closed volume: watertight={mesh.is_watertight}, "
            f"winding_consistent={mesh.is_winding_consistent}, faces={len(mesh.faces)}, "
            f"degenerate_faces={int((mesh.area_faces <= 0.0).sum())}. Near-duplicate "
            f"vertices in the plan polygon are the usual cause -- see _clean()."
        )
    return mesh


def _prism(
    profile_m: Sequence[tuple[float, float]],
    along_m: tuple[float, float],
    axis: Literal["x", "y"],
) -> trimesh.Trimesh:
    """A prism whose cross-section is drawn in the (fall axis, elevation) plane.

    This is how everything that follows the roof gets built -- the roof slab itself, the
    volume under the roof used to trim the gable walls. Drawing the chevron directly, as
    a polygon, means the ridge line is a single shared vertex row rather than two surfaces
    that have to be trusted to meet.

    ``profile_m`` is (cross, z) in metres, ``along_m`` is the extent along the ridge axis.
    """
    polygon = Polygon(profile_m)
    if not polygon.is_valid:
        raise GeneratorError(
            f"the prism profile is not a simple polygon: {polygon.is_valid_reason()}. "
            f"Profile: {[(round(c, 4), round(z, 4)) for c, z in profile_m]}"
        )
    a0, a1 = along_m
    mesh = trimesh.creation.extrude_polygon(polygon, a1 - a0)
    if axis == "x":
        # (cross, z, along) -> (x=along+a0, y=cross, z=z)
        matrix = np.array(
            [[0.0, 0.0, 1.0, a0], [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
        )
    else:
        # (cross, z, along) -> (x=cross, y=a1-along, z=z); the sign keeps the transform
        # right-handed so the extrusion's face winding survives it.
        matrix = np.array(
            [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, -1.0, a1], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
        )
    mesh.apply_transform(matrix)
    return _checked(mesh, f"prism along {axis} between {a0:.3f} m and {a1:.3f} m")


def _difference(solid: trimesh.Trimesh, cutters: Sequence[trimesh.Trimesh]) -> trimesh.Trimesh:
    """One batched boolean subtraction: ``solid - union(cutters)``.

    manifold3d unions the cutters internally and applies a single difference, so a wall
    with four openings costs one boolean rather than four. Sequential subtraction from the
    same solid re-triangulates the result each time, which both degrades numerically and
    is markedly slower.
    """
    if not cutters:
        return solid
    return trimesh.boolean.difference([solid, *cutters], engine="manifold")


def _intersection(a: trimesh.Trimesh, b: trimesh.Trimesh) -> trimesh.Trimesh:
    return trimesh.boolean.intersection([a, b], engine="manifold")


def _slug(text: str) -> str:
    """A stable, ascii, filesystem-and-glTF-safe node fragment.

    Deterministic by construction: no hashing, no ordering, pure character mapping.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    head = folded.split("(")[0]
    out = "".join(char.lower() if char.isalnum() else "_" for char in head)
    return "_".join(part for part in out.split("_") if part) or "layer"


# --------------------------------------------------------------------------------------
# The scene builder
# --------------------------------------------------------------------------------------


class _SceneBuilder:
    """Builds one scene from one spec. Instantiated per call; holds no global state."""

    def __init__(self, spec: Any, *, include_render: bool, include_room_plates: bool) -> None:
        self.spec = spec
        self.include_render = include_render
        self.include_room_plates = include_room_plates
        self.model = build_model(spec)
        self.roof = RoofGeometry.from_spec(spec, self.model)
        self.construction = _mapping(spec, "construction")
        self.sections = _mapping(spec, "section_elevations")
        self.openings_by_wall: dict[str, list[Opening]] = {}
        for opening in _openings(spec):
            self.openings_by_wall.setdefault(opening.wall, []).append(opening)
        self.scene = trimesh.Scene()
        self._chimneys: list[tuple[Wall, trimesh.Trimesh, float, float]] = []

    # -- entry point --------------------------------------------------------------------

    def build(self) -> trimesh.Scene:
        self._build_chimneys()
        for level_id in self.model.levels:
            self._build_level_walls(level_id)
        self._build_slabs()
        self._build_roof()
        for level_id in self.model.levels:
            self._build_openings(level_id)
            if self.include_room_plates:
                self._build_rooms(level_id)
        self._add_chimneys()
        self.scene.metadata.update(self._metadata())
        return self.scene

    # -- helpers ------------------------------------------------------------------------

    def _add(self, name: str, mesh: trimesh.Trimesh, metadata: dict[str, Any]) -> None:
        if mesh.is_empty or len(mesh.faces) == 0:
            raise GeneratorError(
                f"node {name!r} came out of the geometry pipeline empty. A boolean removed "
                f"the whole solid -- most likely an opening larger than its host wall."
            )
        mesh.metadata.update({"node": name, **metadata})
        self.scene.add_geometry(mesh, node_name=name, geom_name=name)

    def _level_elevation_m(self, level_id: str) -> float:
        return self.model.level(level_id).elevation_m

    @cached_property
    def _slab_thickness_m(self) -> float:
        ceiling = _required(self.construction, "ceiling", "construction")
        return to_m(_required(ceiling, "thickness", "construction.ceiling"))

    @cached_property
    def _render_thickness_m(self) -> float:
        """The exterior render, outboard of the dimensioned structural outline.

        ``exterior_wall.thickness`` is the *finished* 460 mm; the walls themselves are
        built at the 450 mm structural thickness the chains dimension. The difference is
        the render, and it is added outside the structural face so that including it
        cannot move a single dimensioned surface.
        """
        wall = _required(self.construction, "exterior_wall", "construction")
        finished = _required(wall, "thickness", "construction.exterior_wall")
        structural = sum(layer["thickness"] for layer in wall.get("layers", ())[:-1])
        return max(to_m(finished - structural), 0.0)

    @cached_property
    def _render_material(self) -> str:
        wall = _required(self.construction, "exterior_wall", "construction")
        layers = wall.get("layers", ())
        return layers[-1]["material"] if layers else "render"

    @cached_property
    def _under_roof(self) -> trimesh.Trimesh:
        """Everything below the roof slab's underside, as one solid.

        Attic walls with no printed height are trimmed against this, which is what makes
        the gable triangles: the wall is extruded past the ridge and intersected with the
        volume under the roof, so its top *is* the roof plane and cannot leave a slot.
        """
        roof = self.roof
        margin = to_m(2000.0)
        lo = to_m(roof.eaves_min) - margin
        hi = to_m(roof.eaves_max) + margin
        floor = to_m(roof.eave_fascia_underside_mm) - to_m(20000.0)
        profile = [
            (lo, floor),
            (hi, floor),
            (hi, to_m(roof.underside_at(roof.eaves_max + 2000.0))),
            (to_m(roof.ridge_coord), to_m(roof.underside_at(roof.ridge_coord))),
            (lo, to_m(roof.underside_at(roof.eaves_min - 2000.0))),
        ]
        along = (to_m(roof.verge_min) - margin, to_m(roof.verge_max) + margin)
        return _prism(profile, along, roof.ridge_axis)

    def _cross_of(self, x_m: float, y_m: float) -> float:
        """The fall-axis coordinate of a plan point, millimetres."""
        return (y_m if self.roof.ridge_axis == "x" else x_m) * 1000.0

    # -- walls ---------------------------------------------------------------------------

    def _wall_top_m(self, wall: Wall, level_id: str) -> float | None:
        """Top elevation of a wall, metres, or None when the roof trims it.

        Three cases, in the order they are tested:

        * an explicit ``height`` in the spec wins always. This is how the 290 mm knee
          walls and the 1100 mm Antresola balustrade get their heights -- T05 transcribed
          them, so nothing is inferred here.
        * on a level with a storey above it, an exterior wall runs past the ceiling to the
          top of the floor slab (it carries the slab); everything else stops at the
          ceiling.
        * on the top level a wall with no height is trimmed to the roof.
        """
        level = self.model.level(level_id).level
        if wall.height is not None:
            return to_m(level.elevation + wall.height)
        if level_id == self._top_level_id:
            return None
        top = level.elevation + level.ceiling_height
        if wall.type == "exterior":
            top += _required(_required(self.construction, "ceiling", "construction"),
                             "thickness", "construction.ceiling")
        return to_m(top)

    @cached_property
    def _top_level_id(self) -> str:
        return list(self.model.levels)[-1]

    def _build_level_walls(self, level_id: str) -> None:
        network = self.model.level(level_id).network
        base_m = self._level_elevation_m(level_id)
        for wall in _walls_of(self.spec, level_id):
            if _is_chimney(wall):
                continue
            solid = network.wall_by_id[wall.id]
            top_m = self._wall_top_m(wall, level_id)
            cutters = self._opening_cutters(wall, solid, level_id)
            for part, polygon, material in self._wall_parts(network, wall, solid):
                mesh = _extrude(polygon, base_m, top_m if top_m is not None
                                else to_m(self.roof.ridge_elevation_mm) + 1.0)
                if top_m is None:
                    mesh = _intersection(mesh, self._under_roof)
                mesh = _difference(mesh, cutters)
                self._add(
                    f"{level_id}/walls/{wall.id}/{part}",
                    mesh,
                    {
                        "kind": "wall",
                        "level": level_id,
                        "wall_id": wall.id,
                        "wall_type": wall.type,
                        "layer": part,
                        "material": material,
                        "thickness_m": solid.thickness_m,
                        "trimmed_to_roof": top_m is None,
                        # Where this wall's top came from, and whether that number was
                        # transcribed or inferred. A_W7's 1100 mm guard height is in the
                        # spec but flagged derived there (the plan draws a single thin
                        # line, not a hatched wall), and that provenance has to survive
                        # into the artifact rather than being flattened to "the spec said
                        # so" -- T14 shades derived geometry differently.
                        "height_source": (
                            "roof" if top_m is None
                            else "wall" if wall.height is not None
                            else "storey"
                        ),
                        "height_derived": top_m is None or wall.is_derived("height"),
                    },
                )

    def _wall_parts(
        self, network: Any, wall: Wall, solid: WallSolid
    ) -> list[tuple[str, Polygon, str]]:
        """A wall as its material layers, innermost first, plus the exterior render.

        Layered walls are not decoration: a window reveal in a single 450 mm slab shows a
        blank side face, whereas the real reveal steps through 250 mm of Porotherm, 200 mm
        of EPS and 10 mm of render, and that step is the most visible tell of a
        thickness-less model. Each layer is its own node so T14 can peel them.
        """
        bands = network.layer_bands(wall.id)
        if not bands:
            return [("structure", solid.footprint(), wall.type)]
        parts = [
            (f"{_slug(layer.material)}", polygon, layer.material)
            for layer, polygon in bands
        ]
        if self.include_render and wall.type == "exterior" and self._render_thickness_m > 0.0:
            side = network.interior_side(wall.id)
            if side != 0:
                sign = 1 if side >= 0 else -1
                outer = -sign * solid.half_thickness_m
                parts.append(
                    (
                        _slug(self._render_material),
                        solid.band(outer, outer - sign * self._render_thickness_m),
                        self._render_material,
                    )
                )
        return parts

    # -- openings -------------------------------------------------------------------------

    def _opening_cutters(
        self, wall: Wall, solid: WallSolid, level_id: str
    ) -> list[trimesh.Trimesh]:
        """One cutter box per opening in this wall, in spec order.

        They are returned as a list and subtracted in a single batched boolean by the
        caller. The box reaches ``CUTTER_MARGIN_MM`` past both faces so it cuts the render
        as well, and drops below the floor when the opening has no sill so that its bottom
        face is never coplanar with the wall's own base.
        """
        base = self.model.level(level_id).level.elevation
        cutters = []
        for opening in self.openings_by_wall.get(wall.id, ()):
            polygon = _opening_plan(solid, opening, to_m(CUTTER_MARGIN_MM))
            z0 = base + opening.sill
            z1 = base + opening.head
            if opening.sill == 0:
                z0 -= COPLANAR_NUDGE_MM
            cutters.append(_extrude(polygon, to_m(z0), to_m(z1)))
        return cutters

    def _build_openings(self, level_id: str) -> None:
        """Glazing panes and door leaves, as thin plates centred in the wall.

        Nothing dimensional depends on these; they exist so the openings read as openings
        in the viewer and so the overlay has the same lines the plans draw. Their nodes are
        under ``openings/`` precisely so T13 can drop them from a section if they get in
        the way.
        """
        network = self.model.level(level_id).network
        base = self.model.level(level_id).level.elevation
        for wall in _walls_of(self.spec, level_id):
            solid = network.wall_by_id.get(wall.id)
            if solid is None:
                continue
            trim = self._wall_top_m(wall, level_id) is None
            for opening in self.openings_by_wall.get(wall.id, ()):
                category = _CATEGORY.get(opening.kind, "other")
                polygon = _opening_plan(solid, opening, -0.5 * (
                    solid.thickness_m - to_m(GLAZING_THICKNESS_MM)
                ))
                mesh = _extrude(polygon, to_m(base + opening.sill), to_m(base + opening.head))
                if trim:
                    # The gable windows are stored as 100/273 rectangles, but 2730 mm is a
                    # MAXIMUM: elevation_side_2.png shows a raking head where the roof
                    # crosses the opening. Intersecting with the volume under the roof
                    # applies exactly that rake, and applies nothing where the rectangle
                    # already clears the roof.
                    mesh = _intersection(mesh, self._under_roof)
                self._add(
                    f"{level_id}/openings/{category}/{opening.id}",
                    mesh,
                    {
                        "kind": opening.kind,
                        "level": level_id,
                        "opening_id": opening.id,
                        "wall_id": opening.wall,
                        "width_m": to_m(opening.width),
                        "height_m": to_m(opening.height),
                        "sill_m": to_m(opening.sill),
                        "head_raked_to_roof": trim,
                    },
                )

    # -- slabs ------------------------------------------------------------------------------

    def _build_slabs(self) -> None:
        """The plinth and the reinforced-concrete floor slab between the storeys."""
        levels = list(self.model.levels)
        terrain = to_m(_required(self.sections, "terrain", "section_elevations"))

        ground_id = levels[0]
        ground = self.model.level(ground_id)
        # The plinth is cut by its level's declared slab openings on exactly the same rule
        # as every other slab. There are none on the ground floor today; the point is that
        # the rule is one rule, so a basement stairwell added to the spec tomorrow needs no
        # code here. Special-casing "the attic is the level with holes" is how the Pustka
        # gets floored over the next time the spec grows.
        self._add(
            f"{ground_id}/slabs/plinth",
            _extrude(
                ground.network.envelope().difference(self._floor_voids(ground_id)),
                terrain,
                ground.elevation_m,
            ),
            {"kind": "slab", "level": ground_id, "slab": "plinth",
             "from_m": terrain, "to_m": ground.elevation_m},
        )

        for lower_id, upper_id in zip(levels, levels[1:], strict=False):
            upper = self.model.level(upper_id)
            top = upper.elevation_m
            bottom = top - self._slab_thickness_m
            outline = upper.network.envelope().difference(self._floor_voids(upper_id))
            mesh = _extrude(outline, bottom, top)
            mesh = _difference(mesh, self._chimney_cutters(bottom, top))
            self._add(
                f"{upper_id}/slabs/floor",
                mesh,
                {"kind": "slab", "level": upper_id, "slab": "floor",
                 "from_m": bottom, "to_m": top, "thickness_m": self._slab_thickness_m},
            )

    def _floor_voids(self, level_id: str) -> BaseGeometry:
        """Where a storey has no floor slab, read from ``spec["slab_openings"]``.

        Two holes exist in this building's attic slab and both are *declared data*, not
        anything this module infers:

        * ``A_SO1`` the **Pustka nad salonem**, the double-height void over the Salon.
        * ``A_SO2`` the **stairwell**, coincident with room A_R4 ``Schody``.

        Slabbing either one is a defect that no dimensional check in the suite can see:
        every room area on both levels stays exactly right while the house's double-height
        living space silently disappears -- the same class of silent error as a mirrored
        plan, which is why it is worth a named entity in the schema rather than a rule
        here. Nothing in this method knows how many openings there are, which levels they
        are on, or what shape they are; move one in the spec and the slab follows.

        Cross-check, reported but not enforced (see ``undeclared_floor_faces`` in the
        scene metadata): an enclosed face of the wall network that no room seed falls in
        is either a slab opening or a transcription gap, and a slab poured over it would
        be invisible to every area assertion.
        """
        boxes = [
            box(*(to_m(value) for value in _required(opening, "bounds", opening.get("id", "?"))))
            for opening in self._slab_openings(level_id)
        ]
        if not boxes:
            return Polygon()
        return boxes[0] if len(boxes) == 1 else MultiPolygon(boxes)

    def _slab_openings(self, level_id: str) -> tuple[Mapping[str, Any], ...]:
        """Declared slab openings on a level, in spec order."""
        declared = self.spec.get("slab_openings", ()) if hasattr(self.spec, "get") else ()
        return tuple(item for item in declared if item.get("level") == level_id)

    def _undeclared_floor_faces(self, level_id: str) -> list[dict[str, Any]]:
        """Enclosed faces claimed by neither a room nor a slab opening."""
        level = self.model.level(level_id)
        seeds = [room.seed for room in level.rooms]
        declared = self._floor_voids(level_id)
        out = []
        for face in level.network.faces():
            if any(face.contains(seed) for seed in seeds):
                continue
            if declared.contains(face.representative_point()):
                continue
            out.append({
                "level": level_id,
                "area_m2": face.area,
                "bounds_m": [round(value, 6) for value in face.bounds],
            })
        return out

    # -- chimneys ------------------------------------------------------------------------

    def _build_chimneys(self) -> None:
        """The two stacks, traced four times (once per storey plan).

        Both are drawn above the ridge on elevation_side_1/2.png, so they are extruded
        from their storey through the roof to ``CHIMNEY_ABOVE_RIDGE_MM`` above the ridge.
        The ground pair and the attic pair are the same two stacks read off different
        images: each attic footprint lies inside the corresponding ground footprint (or
        vice versa), so the four solids merge into two silhouettes above the roof rather
        than four. They are kept as four nodes because they are four spec entities and
        merging them would be this module inventing an element the spec does not contain.

        Junction extensions are deliberately dropped: a stack is freestanding, so it is
        built from the bare centreline buffer rather than from the network footprint that
        runs walls into each other's corners.
        """
        top = to_m(self.roof.ridge_elevation_mm + CHIMNEY_ABOVE_RIDGE_MM)
        for level_id in self.model.levels:
            base = self._level_elevation_m(level_id)
            for wall in _walls_of(self.spec, level_id):
                if not _is_chimney(wall):
                    continue
                footprint = WallSolid.from_wall(wall).footprint()
                self._chimneys.append((wall, _extrude(footprint, base, top), base, top))

    def _chimney_cutters(self, z0_m: float, z1_m: float) -> list[trimesh.Trimesh]:
        """Chimney solids that pass through the slab of elevation band ``z0..z1``."""
        return [mesh for _, mesh, base, top in self._chimneys if base < z1_m and top > z0_m]

    def _add_chimneys(self) -> None:
        for wall, mesh, base, top in self._chimneys:
            self._add(
                f"{wall.level}/chimneys/{wall.id}/structure",
                mesh,
                {
                    "kind": "chimney",
                    "level": wall.level,
                    "wall_id": wall.id,
                    "from_m": base,
                    "to_m": top,
                    "above_ridge_m": to_m(CHIMNEY_ABOVE_RIDGE_MM),
                    "derived": True,
                },
            )

    # -- roof --------------------------------------------------------------------------

    def _roof_prism(self, thickness_mm: float) -> trimesh.Trimesh:
        """Both slopes as one chevron prism hanging below the outer roof plane.

        The profile is the outer plane from eave to ridge to eave, then back again
        ``thickness_mm`` lower. Nothing in it is positioned to hit a target elevation:
        every z is :meth:`RoofGeometry.top_at`, which is the ridge minus the fall, and the
        ridge is the derived one.

        ``thickness_mm`` is ``fascia_depth`` for the roof itself -- which is what puts the
        modelled fascia underside on the printed +2.88 m -- and the pane thickness for a
        roof window's glazing, so a pane is guaranteed to lie in the roof plane rather
        than merely near it.
        """
        roof = self.roof
        drop = to_m(thickness_mm)
        top_eave = to_m(roof.top_at(roof.eaves_min))
        ridge = to_m(roof.ridge_elevation_mm)
        profile = [
            (to_m(roof.eaves_min), top_eave),
            (to_m(roof.ridge_coord), ridge),
            (to_m(roof.eaves_max), top_eave),
            (to_m(roof.eaves_max), top_eave - drop),
            (to_m(roof.ridge_coord), ridge - drop),
            (to_m(roof.eaves_min), top_eave - drop),
        ]
        return _prism(profile, (to_m(roof.verge_min), to_m(roof.verge_max)), roof.ridge_axis)

    def _check_roof_window(self, window: RoofWindow) -> RoofWindow:
        """Refuse a roof opening this module cannot model correctly.

        The vertical-prism cut below is exact for a window lying wholly within one plane
        of the gable. A window straddling the ridge would need two planes and is not what
        any of these three are, so it is rejected rather than silently cut through the
        apex. The declared ``slope`` is checked against the bounds for the same reason a
        stale chimney list is: a mislabelled slope would put a window on the wrong
        elevation and no dimensional check would see it.

        With ``ridge_axis == "x"`` the roof falls along y and the spec's frame puts north
        at high y -- T05 transcribed A_W1 as the "South eaves wall" at y = 225 and A_W3 as
        the "North eaves wall" at y = 8775. That mapping is asserted here, not assumed.
        """
        roof = self.roof
        low, high = window.fall_span(roof.ridge_axis)
        if low < roof.ridge_coord < high:
            raise GeneratorError(
                f"roof opening {window.id} spans the ridge (plan {low:.0f}..{high:.0f} mm "
                f"across a ridge at {roof.ridge_coord:.0f} mm). A window in two roof "
                f"planes at once needs a plane-and-slope representation; this module only "
                f"models an opening lying wholly within one slope."
            )
        if roof.ridge_axis != "x":
            raise GeneratorError(
                f"roof opening {window.id}: 'slope' is named south/north, which only means "
                f"anything while the ridge runs east-west (roof.ridge_axis 'x'); the spec "
                f"says {roof.ridge_axis!r}."
            )
        expected = "south" if high <= roof.ridge_coord else "north"
        if window.slope != expected:
            raise GeneratorError(
                f"roof opening {window.id} declares slope {window.slope!r} but its plan "
                f"bounds {low:.0f}..{high:.0f} mm put it on the {expected} side of the "
                f"ridge at {roof.ridge_coord:.0f} mm."
            )
        return window

    def _roof_window_cutter(self, window: RoofWindow) -> trimesh.Trimesh:
        """A vertical prism over the window's plan rectangle.

        Vertical sides are correct here rather than approximate: a roof window's jambs are
        cut square through the rafters, so the hole in plan really is the rectangle the
        plan draws, and cutting it with a vertical prism reproduces the sloping head and
        sill without any trigonometry of its own.

        The prism is clamped from the lower of the roof underside at either edge to the
        higher of the roof top at either edge, so it is correct on both slopes rather than
        only on the falling one.
        """
        roof = self.roof
        x0, y0, x1, y1 = window.bounds
        plan = box(to_m(x0), to_m(y0), to_m(x1), to_m(y1))
        low, high = window.fall_span(roof.ridge_axis)
        lo = to_m(min(roof.underside_at(low), roof.underside_at(high))) - 1.0
        hi = to_m(max(roof.top_at(low), roof.top_at(high))) + 1.0
        return _extrude(plan, lo, hi)

    def _build_roof(self) -> None:
        slab = self._roof_prism(self.roof.fascia_depth)
        cutters = [self._roof_window_cutter(window) for window in self._roof_windows()]
        cutters.extend(mesh for _, mesh, _, _ in self._chimneys)
        self._add(
            "roof/slab",
            _difference(slab, cutters),
            {
                "kind": "roof",
                "pitch_deg": self.roof.pitch_deg,
                "ridge_height_m": to_m(self.roof.ridge_elevation_mm),
                "springing_m": to_m(self.roof.springing_elevation),
                "eaves_overhang_m": to_m(self.roof.eaves_overhang),
                "verge_overhang_m": to_m(self.roof.verge_overhang),
                "roof_area_m2": self.roof.area_m2,
            },
        )
        pane_stock = self._roof_prism(GLAZING_THICKNESS_MM)
        for window in self._roof_windows():
            # The pane is the same chevron prism at pane thickness, cut by the same box
            # that made the hole. Deriving it from the roof rather than building a
            # separate sloped box means it cannot disagree with the hole it sits in,
            # whatever the pitch -- and it stays *in the roof plane*, which a plug scaled
            # about its own centre would not (that flattens the slope as it thins).
            self._add(
                f"roof/windows/{window.id}",
                _intersection(pane_stock, self._roof_window_cutter(window)),
                {
                    "kind": "roof_window",
                    "opening_id": window.id,
                    "slope": window.slope,
                    "width_m": to_m(window.width),
                    "height_on_slope_m": to_m(window.height_on_slope),
                    "plan_depth_m": to_m(window.plan_depth(self.roof.ridge_axis)),
                    "from_spec": self._roof_windows_are_declared,
                    "derived": True,
                },
            )

    @cached_property
    def _roof_windows_are_declared(self) -> bool:
        return bool(self._declared_roof_openings)

    @cached_property
    def _declared_roof_openings(self) -> tuple[Mapping[str, Any], ...]:
        raw = self.spec.get("roof_openings", ()) if hasattr(self.spec, "get") else ()
        return tuple(raw)

    @cached_property
    def _roof_window_list(self) -> tuple[RoofWindow, ...]:
        """The roof openings, from the spec when it has them and the fallback when not.

        Reading ``spec["roof_openings"]`` first is what makes the recommended schema change
        a drop-in: the day T05 adds the block, these three stop being generator-side
        literals and nothing else in this module moves. The traced plan extent is used as
        traced either way; the printed '78/160' callout is only cross-checked against it,
        never substituted for it.

        The cross-check is deliberately *reported*, not enforced: the projected depth and
        the on-slope height are only consistent at one pitch, so raising on a mismatch
        would make ``pitch_deg`` un-sweepable and would break the anti-tautology test for
        the ridge -- the most important test in the suite -- to police a 40 mm tracing
        disagreement.
        """
        declared = self._declared_roof_openings
        windows = (
            tuple(RoofWindow.from_raw(raw) for raw in declared) if declared else ROOF_WINDOWS
        )
        return tuple(self._check_roof_window(window) for window in windows)

    def _roof_windows(self) -> tuple[RoofWindow, ...]:
        return self._roof_window_list

    # -- rooms --------------------------------------------------------------------------

    def _build_rooms(self, level_id: str) -> None:
        """A thin plate per room face, carrying the room's identity into the scene.

        Four ground-floor rooms -- Hol, Salon, Hol, Kuchnia -- are one continuous open-plan
        space and share a single face; the publisher's splits between them are virtual
        measuring lines, not walls. They get one plate whose metadata names all four,
        rather than four coincident plates that would z-fight and imply walls that do not
        exist. See README.md, "Known area-check limitations".
        """
        level = self.model.level(level_id)
        voids = self._floor_voids(level_id)
        grouped: dict[tuple[Any, ...], list[Any]] = {}
        for room in level.rooms:
            polygon = room.polygon("structure")
            key = (round(polygon.area, 9), tuple(round(value, 9) for value in polygon.bounds))
            grouped.setdefault(key, []).append(room)
        for rooms in grouped.values():
            # A plate is a piece of *floor*, so it stops where the floor does: the
            # declared slab openings are subtracted from it exactly as they are from the
            # slab. A_R4 Schody coincides with the A_SO2 stairwell opening and therefore
            # gets no plate at all -- it is a published floor area (3.64 m2, counted in
            # netto) but not a floor at attic level, and a 2 mm plate across it would
            # close the stairwell in the viewer exactly as a slab would.
            polygon = _clean(rooms[0].polygon("structure").difference(voids))
            if polygon.is_empty or polygon.area <= 0.0:
                continue
            base = level.elevation_m
            self._add(
                f"{level_id}/rooms/{rooms[0].id}",
                _extrude(polygon, base, base + to_m(ROOM_PLATE_THICKNESS_MM)),
                {
                    "kind": "room",
                    "level": level_id,
                    "room_ids": [room.id for room in rooms],
                    "room_names": [room.name for room in rooms],
                    "published_area_m2": sum(room.published_area for room in rooms),
                    "computed_area_m2": rooms[0].polygon("structure").area,
                    "plate_area_m2": polygon.area,
                    "shared_face": len(rooms) > 1,
                },
            )

    # -- metadata -----------------------------------------------------------------------

    def _metadata(self) -> dict[str, Any]:
        """Everything downstream needs as scalars, JSON-serialisable for T12.

        ``ridge_height_m`` is the derived output, and ``ridge_printed_m`` sits next to it
        so that the comparison is visible in the artifact rather than only in a test.
        """
        roof = self.roof
        terrain = to_m(_required(self.sections, "terrain", "section_elevations"))
        ridge_m = to_m(roof.ridge_elevation_mm)
        return {
            "generator": "kotewki.generator",
            "units": "m",
            "ridge_height_m": ridge_m,
            "building_height_m": ridge_m - terrain,
            "roof": {
                "pitch_deg": roof.pitch_deg,
                "ridge_axis": roof.ridge_axis,
                "span_m": to_m(roof.span),
                "springing_m": to_m(roof.springing_elevation),
                "ridge_m": ridge_m,
                "ridge_printed_m": to_m(self.sections.get("ridge", float("nan"))),
                "eave_fascia_underside_m": to_m(roof.eave_fascia_underside_mm),
                "eave_printed_m": to_m(
                    self.sections.get("eave_fascia_underside", float("nan"))
                ),
                "eaves_overhang_m": to_m(roof.eaves_overhang),
                "verge_overhang_m": to_m(roof.verge_overhang),
                "roof_buildup_vertical_m": to_m(roof.roof_buildup_vertical),
                "fascia_depth_m": to_m(roof.fascia_depth),
                "area_m2": roof.area_m2,
            },
            "levels": {
                level_id: {
                    "elevation_m": level.elevation_m,
                    "ceiling_height_m": level.ceiling_height_m,
                    "envelope_area_m2": level.network.envelope().area,
                    "rooms": len(level.rooms),
                }
                for level_id, level in self.model.levels.items()
            },
            "terrain_m": terrain,
            "floor_voids": [
                {
                    "id": opening.get("id"),
                    "level": level_id,
                    "kind": opening.get("kind"),
                    "area_m2": (
                        to_m(opening["bounds"][2] - opening["bounds"][0])
                        * to_m(opening["bounds"][3] - opening["bounds"][1])
                    ),
                }
                for level_id in self.model.levels
                for opening in self._slab_openings(level_id)
            ],
            "undeclared_floor_faces": [
                face
                for level_id in self.model.levels
                for face in self._undeclared_floor_faces(level_id)
            ],
            "roof_windows": [
                {
                    "id": window.id,
                    "slope": window.slope,
                    "from_spec": self._roof_windows_are_declared,
                    "bounds_m": [to_m(value) for value in window.bounds],
                    "width_m": to_m(window.width),
                    "height_on_slope_m": to_m(window.height_on_slope),
                    "plan_depth_m": to_m(window.plan_depth(roof.ridge_axis)),
                    "implied_height_on_slope_m": to_m(
                        window.plan_depth(roof.ridge_axis)
                        / math.cos(math.radians(roof.pitch_deg))
                    ),
                }
                for window in self._roof_windows()
            ],
            "schema_gaps": [
                gap
                for gap, present in (
                    (
                        "roof windows are hosted by a roof PLANE, not a wall and not a "
                        "level; openings[].wall cannot express that. Add roof_openings[] "
                        "shaped like slab_openings[] -- {id, kind, slope, bounds, width, "
                        "height_on_slope} -- and generator.ROOF_WINDOWS retires itself; "
                        "the generator already reads the block when it is present.",
                        not self._roof_windows_are_declared,
                    ),
                    (
                        "chimney stacks are stored as walls; walls[] has no role field, "
                        "so the four stack ids are listed in generator.CHIMNEY_WALL_IDS. "
                        "Add walls[].role with wall/chimney/balustrade, default 'wall'.",
                        True,
                    ),
                )
                if present
            ],
        }


_CATEGORY: dict[str, str] = {"window": "windows", "door": "doors"}


def _walls_of(spec: Any, level_id: str) -> tuple[Wall, ...]:
    """Walls on a level, in spec order. Spec order is the scene's iteration order."""
    if hasattr(spec, "walls_on"):
        return spec.walls_on(level_id)
    walls: Iterable[Any] = spec.get("walls", ())
    return tuple(Wall.from_raw(raw) for raw in walls if raw["level"] == level_id)


def _opening_plan(solid: WallSolid, opening: Opening, margin_m: float) -> Polygon:
    """The plan rectangle of an opening in its host wall.

    ``offset`` runs along the centreline from the wall's ``start``, before any junction
    extension -- the same convention T10 asserts ``offset + width <= wall.length`` in.
    ``margin_m`` grows the rectangle across the wall (positive for a cutter that must
    clear both faces, negative for a glazing pane sitting inside them).
    """
    origin = np.array(solid.start, dtype=float)
    direction = np.array(solid.direction, dtype=float)
    normal = np.array(solid.normal, dtype=float)
    start = origin + direction * to_m(opening.offset)
    end = origin + direction * to_m(opening.offset + opening.width)
    across = solid.half_thickness_m + margin_m
    if across <= 0.0:
        raise GeneratorError(
            f"opening {opening.id}: the {margin_m * 1000:.0f} mm inset is thicker than its "
            f"host wall {solid.id} ({solid.thickness_m * 1000:.0f} mm)."
        )
    corners = [
        start + normal * across,
        end + normal * across,
        end - normal * across,
        start - normal * across,
    ]
    return Polygon([(point[0], point[1]) for point in corners])


# --------------------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------------------


def build_scene(
    spec: Any,
    *,
    include_render: bool = True,
    include_room_plates: bool = True,
) -> trimesh.Scene:
    """Build the 3D scene from a validated spec. Pure and deterministic.

    Args:
        spec: a :class:`kotewki.spec.Spec` or the merged mapping.
        include_render: emit the 10 mm exterior render as its own outboard layer. It sits
            outside the dimensioned structural outline and moves nothing; T04 solved it
            against the published *pow. zabudowy*, which is measured on the finished
            building. Turn it off to model raw structure only.
        include_room_plates: emit one thin plate per room face carrying the room's name
            and published area, for T14's labels and T13's debugging.

    Returns:
        A :class:`trimesh.Scene` in metres whose ``metadata`` carries the derived
        ``ridge_height_m`` among the other computed quantities.
    """
    return _SceneBuilder(
        spec,
        include_render=include_render,
        include_room_plates=include_room_plates,
    ).build()


#: Alias, matching :func:`kotewki.geometry.build`.
build = build_scene


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI convenience
    """``just build`` entry point: build the scene and report the derived quantities.

    Writing ``build/model.glb`` is T12's job (``src/kotewki/export.py``); this defers to
    it when it is implemented and otherwise only reports, so that `just build` never
    produces a half-exported artifact.
    """
    import json

    from kotewki.spec import load_spec

    scene = build_scene(load_spec())
    print(json.dumps(scene.metadata, indent=2, ensure_ascii=False))
    print(f"{len(scene.geometry)} nodes")

    from kotewki import export as export_module

    writer = getattr(export_module, "export_scene", None) or getattr(
        export_module, "export", None
    )
    if writer is None:
        print("kotewki.export has no exporter yet (T12); nothing written.")
        return 0
    print(writer(scene))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

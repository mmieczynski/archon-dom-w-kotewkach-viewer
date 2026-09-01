"""tests/test_overlay.py -- Test 6: the orthographic overlay (T13).

The only non-numeric check in the suite, and the only guard against the **(E) variant
mirroring problem**. Archon's suffixes denote mirrored or modified variants, and a
perfectly-built mirror image of the right house closes every dimension chain, hits every
published room area and satisfies all five global invariants. Nothing else here can see
it. Two rooms of equal area swapped, a door on the wrong wall and a wing on the wrong side
of the corridor are the same class of defect.

WHAT IS BEING COMPARED
----------------------
A **horizontal section of the real generated mesh**, cut at 1.0 m above each finished
floor, rasterised onto the *source bitmap's own pixel grid*, and composited against the
wall ink of that bitmap.

Cutting the mesh rather than re-drawing T07's shapely polygons is deliberate and must not
be "optimised" away: it is what makes this test see **generator** bugs -- a dropped node, a
boolean that removed a whole wall, an extrusion at the wrong elevation -- and not only spec
bugs. The redundancy is the point.

1.0 m is chosen because it is the conventional Polish plan-section height: it cuts through
door openings (so doorways read as gaps, exactly as the plans draw them) and sits below
most window heads.

THE TWO PLAN FRAMES ARE NOT THE SAME FRAME
------------------------------------------
``plan_ground.png`` and ``plan_attic.png`` are both 853x853 and both drawn at the same
scale, but their **origins differ** -- by 7 px in x and 44 px in y. Assuming one frame for
both puts the ground floor 44 px (1.08 m) out and produces a spectacular fake failure.
Each frame is therefore anchored independently, and :func:`derive_frame` re-derives both
from the bitmaps at test time so the recorded constants cannot rot.

Each frame is anchored on the **printed overall dimensions** -- 1710 cm across and 900 cm
deep, transcribed in the spec's ``dimension_chains`` with ``extent`` ``overall_x`` /
``overall_y`` -- laid on the outermost exterior-wall ink. No pixel is ever measured to
*obtain* a dimension; the dimension is printed, and the pixels only say where it lands.

THE SOURCE IS THE UNMIRRORED BITMAP, AS SERVED
----------------------------------------------
``data/source/PROVENANCE.md``: the site's "Pokaz lustrzane odbicie" toggle is client-side
CSS (``class="has-mirror"``), not a different image. The stored bitmaps are the canonical
(E) orientation and no flip was applied at any stage. This test must run against them
exactly as stored -- flipping either side here would delete the entire point of the test.

DETERMINISM
-----------
A flaky overlay test gets disabled by the next person, and disabling it removes the
project's only mirroring defence. Determinism therefore matters more here than the pixel
threshold. Three things are pinned:

* the generator is deterministic across processes (T11, verified under differing
  ``PYTHONHASHSEED``); :func:`test_the_overlay_is_byte_identical_across_processes` re-checks
  that end-to-end through the raster,
* the raster is produced through ``Figure`` + ``FigureCanvasAgg`` **directly, never
  pyplot** -- no global backend state, no rcParams-dependent styling, explicit figure size,
  dpi, facecolor and colours, ``antialiased=False``, and not a single glyph drawn,
* the composite is assembled in numpy from boolean masks, so there is no second resampling
  step and no interpolation kernel to depend on.

GOLDENS ARE PENDING HUMAN SIGN-OFF
----------------------------------
``tests/golden/`` is deliberately **empty**. The first generation of goldens must be
eyeballed by a human before being committed, and that sign-off is scheduled for the very
end of the project (TASKS.md, user instruction 2026-08-31). An auto-accepted golden locks
in whatever was wrong at the time and would leave the project with *no* mirroring defence
while appearing to have one, so nothing in this module writes into ``tests/golden/`` under
any circumstance. :func:`test_the_overlay_matches_its_golden` skips, with a message saying
so, until a human puts the files there.

Everything else in this module runs today, including the check that matters most:
:func:`test_a_mirrored_spec_fails_the_overlay_diff`.
"""

from __future__ import annotations

import copy
import hashlib
import os
import subprocess
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import shapely
import trimesh
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from PIL import Image
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

from kotewki.generator import build_scene, roof_geometry
from kotewki.spec import Spec, load_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "data" / "source"
BUILD_DIR = REPO_ROOT / "build"
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

# --------------------------------------------------------------------------------------
# Pinned rendering parameters. Every one of these is a determinism control, not taste.
# --------------------------------------------------------------------------------------

#: Both source plans are 853x853. The raster is produced at exactly that size so the
#: generated section lands on the bitmap's own pixel grid and no resampling ever happens.
IMAGE_PX = 853

#: 8.53 in at 100 dpi = 853 px. Both are pinned; either alone is not enough.
FIGURE_DPI = 100
FIGURE_INCHES = IMAGE_PX / FIGURE_DPI

#: Composite colours, RGB. Fixed literals so a golden cannot drift with a style change.
COLOUR_BACKGROUND = (255, 255, 255)  # neither
COLOUR_CONTEXT = (222, 222, 222)  # source ink that is not wall: text, furniture, trees
COLOUR_SOURCE_ONLY = (255, 90, 90)  # plan draws a wall, the model has none
COLOUR_GENERATED_ONLY = (60, 120, 255)  # model has a wall, the plan draws none
COLOUR_OVERLAP = (20, 20, 20)  # both -- what the whole image should be

#: Height of the section above each finished floor, metres. See the module docstring.
SECTION_HEIGHT_M = 1.0

#: Snapping grid for the raw section segments, metres. One nanometre, the same grid
#: ``kotewki.generator.SNAP_GRID_M`` uses, and for the same reason: ``mesh_plane`` returns
#: the four sides of a rectangular cut as independent segments whose shared endpoints can
#: differ in the last float bit, and ``polygonize`` then silently returns *nothing* for
#: that wall. Snapping first is what makes every cut close. Nine orders of magnitude below
#: a millimetre, so it cannot move a real dimension.
SNAP_GRID_M = 1e-9


# --------------------------------------------------------------------------------------
# Source-bitmap masks
# --------------------------------------------------------------------------------------

#: A wall on these plans is solid black fill. Room shading is light grey, dimension text
#: and room numbers are red, trees are green, furniture is thin mid-grey. Requiring a
#: pixel to be both *dark* and *neutral* isolates the masonry almost perfectly -- see
#: build/overlay_*.png, where the residual red is door swing arcs and dimension glyphs.
WALL_INK_MAX_CHANNEL = 90
WALL_INK_MAX_CHROMA = 40

#: Anything darker than this counts as "the drawing", and is painted in as pale context so
#: a human reviewing the overlay can see the plan the walls belong to.
CONTEXT_INK_MAX_LUMA = 200


# --------------------------------------------------------------------------------------
# Plan frames
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PlanFrame:
    """Where the building's printed outline sits on one source bitmap.

    Coordinates are **continuous image coordinates**: ``u = 0.0`` is the left edge of
    column 0 and ``u = 853.0`` the right edge of column 852, so a wall face reported at
    ``u0`` is the boundary the ink starts on rather than an ambiguous pixel index.

    ``u0``/``u1`` carry the structural outline's west and east faces (spec x = 0 and
    x = 17100), ``v0``/``v1`` its south and north faces (spec y = 0 and y = 9000). Note
    ``v0 > v1``: image rows run downward and the plans put north at the top, which is the
    single sign that decides whether the model is drawn upside down.
    """

    image: str
    u0: float
    u1: float
    v0: float
    v1: float

    def px_per_mm_x(self, overall_x_mm: float) -> float:
        return (self.u1 - self.u0) / overall_x_mm

    def px_per_mm_y(self, overall_y_mm: float) -> float:
        return (self.v0 - self.v1) / overall_y_mm


#: Measured off the two bitmaps by locating the outermost exterior-wall ink and laying the
#: printed 1710 x 900 cm overall dimensions on it. `test_the_recorded_plan_frames_are_the
#: _measured_ones` re-derives these from the images so they cannot go stale, and
#: `test_the_two_plans_do_not_share_a_frame` guards the 7 px / 44 px offset between them.
#:
#: An earlier note in this project gave the attic frame as
#: ``col = 76.0 + x_mm * 0.04089`` / ``row = 188 + (8555 - y_mm) * 0.04089``. That agrees
#: with the numbers below to about half a pixel (12 mm) and is the same frame; it is
#: restated here in face-to-face form because a mapping anchored on two *printed*
#: dimensions is checkable, whereas one anchored on a single scale factor is not.
PLAN_FRAMES: dict[str, PlanFrame] = {
    "ground": PlanFrame(image="plan_ground.png", u0=69.0, u1=769.0, v0=583.0, v1=215.0),
    "attic": PlanFrame(image="plan_attic.png", u0=76.0, u1=776.0, v0=539.0, v1=171.0),
}

#: The levels this test covers, and the node prefixes it sections. Room plates (2 mm at
#: floor level), floor slabs and the roof are never reached by a cut 1.0 m above the floor;
#: glazing panes and door leaves *are*, and are deliberately excluded, because a Polish
#: plan draws a doorway as a gap in the wall and this test compares masonry to masonry.
SECTIONED_CATEGORIES = ("walls", "chimneys")

LEVEL_IDS = ("ground", "attic")


# --------------------------------------------------------------------------------------
# Thresholds. Recorded next to the value actually measured, so that loosening one is a
# visible edit rather than a quiet one.
# --------------------------------------------------------------------------------------

#: Golden diff: fraction of the 853x853 composite allowed to differ. Everything upstream
#: is deterministic, so the honest value is exactly 0.0; the allowance exists only so a
#: matplotlib patch release that moves one edge pixel does not fail the build. A mirrored
#: model misses this by two orders of magnitude (measured 0.0140 attic, 0.0501 ground).
GOLDEN_MAX_DIFF_FRACTION = 1e-4

#: Registration: the fraction of *generated* section pixels that land on drawn wall ink.
#: Precision rather than IoU, because the two sides are not symmetric -- the source mask
#: also holds dimension glyphs, door arcs and (on the attic) the eaves bands, none of which
#: a 1.0 m section can contain. Measured 0.977 ground, 0.903 attic; a mirrored model
#: scores 0.516 and 0.570.
MIN_REGISTRATION_PRECISION = {"ground": 0.95, "attic": 0.85}

#: A mirrored model must fall below this. Sits well clear of both the honest scores above
#: and the mirrored ones.
MIRRORED_MAX_REGISTRATION_PRECISION = 0.75

#: How far the shift search looks when confirming the best alignment is no shift at all.
REGISTRATION_SEARCH_PX = 3


# --------------------------------------------------------------------------------------
# Spec helpers
# --------------------------------------------------------------------------------------


def overall_mm(spec: Any, axis: str) -> float:
    """The printed overall building dimension on an axis, millimetres.

    Read from the ``dimension_chains`` whose ``extent`` is ``overall_x`` / ``overall_y``
    -- i.e. from the numbers printed on the plans (1710 and 900 cm), converted by the
    loader's single x10. Six chains print the 1710 and six the 900; they must all agree,
    and T06 already asserts each of them closes on its own segments.
    """
    extent = f"overall_{axis}"
    totals = {
        chain.total_mm for chain in spec.dimension_chains if chain.extent == extent
    }
    if not totals:
        raise AssertionError(f"no dimension chain declares extent {extent!r}")
    if len(totals) != 1:
        raise AssertionError(
            f"chains with extent {extent!r} disagree on the overall dimension: "
            f"{sorted(totals)} mm. T06 asserts cross-chain consistency; if that passes and "
            f"this fails, the two tests disagree and one of them is wrong."
        )
    return float(next(iter(totals)))


def mirror_spec(spec: Any) -> Spec:
    """A left-right mirrored copy of the spec: the (E) variant's evil twin.

    Deep-copied, exactly as ``tests/test_invariants.py::_respec`` does, so a session-scoped
    fixture cannot be poisoned by this. Returns a real :class:`~kotewki.spec.Spec` (the
    generator reaches through to ``spec.walls``), constructed directly rather than through
    ``load_spec``, because the mirrored document never touches disk.

    Mirroring is ``x -> overall_x - x`` about the structural outline's own centreline, so
    the **exterior envelope maps exactly onto itself** and only the interior layout flips.
    That is the strongest available form of the test: nothing moves, nothing changes size,
    every area is preserved to the last square millimetre, and the overlay has to catch it
    on layout alone. An off-centre axis would also translate the building, and the test
    could then pass on the translation instead of on the mirroring.

    Opening offsets need no adjustment. ``offset`` runs from the wall's ``start``, and
    mirroring moves ``start`` with the wall: an opening at ``a + o`` on a wall from ``a``
    to ``b`` lands at ``X - a - o`` on a wall now running from ``X - a`` to ``X - b``,
    which is still offset ``o`` from the (new) start. Verified by
    :func:`test_mirroring_preserves_every_dimension_it_should`.
    """
    document = copy.deepcopy(spec.to_dict() if hasattr(spec, "to_dict") else dict(spec))
    axis = overall_mm(spec, "x")

    for wall in document["walls"]:
        wall["start"] = [axis - wall["start"][0], wall["start"][1]]
        wall["end"] = [axis - wall["end"][0], wall["end"][1]]
    for room in document["rooms"]:
        seed = room.get("seed")
        if seed is not None:
            room["seed"] = [axis - seed[0], seed[1]]
    for opening in document.get("slab_openings", ()):
        x0, y0, x1, y1 = opening["bounds"]
        opening["bounds"] = [axis - x1, y0, axis - x0, y1]
    # The spec carries no roof_openings today (the generator falls back to T05's traced
    # constants -- see generator.ROOF_WINDOWS, "SCHEMA GAP 1"). Mirror them anyway so this
    # helper stays correct the day the block lands. Roof windows sit in the roof plane and
    # never appear in a 1.0 m section, so this changes nothing about the overlay either way.
    for opening in document.get("roof_openings", ()):
        x0, y0, x1, y1 = opening["bounds"]
        opening["bounds"] = [axis - x1, y0, axis - x0, y1]

    return Spec(document)


# --------------------------------------------------------------------------------------
# Sectioning the real mesh
# --------------------------------------------------------------------------------------


def section_polygons(scene: trimesh.Scene, level_id: str, z_m: float) -> Any:
    """The filled horizontal cut through a level's masonry, as shapely geometry in metres.

    Cut from the generated mesh, node by node, and unioned. Per-node rather than through
    one concatenated mesh because the scene deliberately contains overlapping solids where
    the chimney stacks abut walls (README, "Outstanding" 4); a union of per-node cuts is
    unaffected by that, whereas polygonising one merged non-manifold cross-section is not.

    ``trimesh.Trimesh.section`` is *not* used: it routes through
    ``trimesh.graph.traversals``, which needs scipy, and scipy is not a dependency of this
    project. ``trimesh.intersections.mesh_plane`` returns the same cut as raw segments with
    numpy alone. Snapping those to :data:`SNAP_GRID_M` before polygonising is mandatory --
    see the constant.
    """
    normal = np.array([0.0, 0.0, 1.0])
    origin = np.array([0.0, 0.0, float(z_m)])
    prefixes = tuple(f"{level_id}/{name}/" for name in SECTIONED_CATEGORIES)

    polygons: list[Polygon] = []
    for name, mesh in scene.geometry.items():
        if not name.startswith(prefixes):
            continue
        segments = trimesh.intersections.mesh_plane(
            mesh, plane_normal=normal, plane_origin=origin
        )
        if len(segments) == 0:
            continue
        lines = [
            LineString([(a[0], a[1]), (b[0], b[1])])
            for a, b in segments
            if a[0] != b[0] or a[1] != b[1]
        ]
        if not lines:
            continue
        noded = shapely.set_precision(unary_union(lines), SNAP_GRID_M)
        polygons.extend(polygonize(noded))

    if not polygons:
        raise AssertionError(
            f"the {level_id} section at z = {z_m:.3f} m is empty. Either the generator "
            f"emitted no {SECTIONED_CATEGORIES} nodes for this level, or the cut misses "
            f"every solid -- which is itself the failure this test exists to report."
        )
    return unary_union(polygons)


def rasterise(geometry: Any, frame: PlanFrame, overall: tuple[float, float]) -> np.ndarray:
    """Rasterise a plan geometry (metres) onto a source bitmap's pixel grid.

    Returns an ``(853, 853)`` boolean array, ``True`` where the section is solid.

    The axes fill the whole canvas, so image column ``u`` maps linearly to data x and the
    section lands on the plan's own pixels with no resampling anywhere in the pipeline.
    ``Figure`` + ``FigureCanvasAgg`` are used directly rather than pyplot: pyplot would
    mutate the process-wide backend and figure registry, and this module has to be
    importable by a subprocess (the cross-process determinism check) without side effects.
    """
    overall_x_mm, overall_y_mm = overall
    px_per_m_x = frame.px_per_mm_x(overall_x_mm) * 1000.0
    px_per_m_y = frame.px_per_mm_y(overall_y_mm) * 1000.0

    fig = Figure(
        figsize=(FIGURE_INCHES, FIGURE_INCHES),
        dpi=FIGURE_DPI,
        facecolor="white",
        linewidth=0.0,
    )
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
    ax.set_axis_off()
    ax.set_xlim((0.0 - frame.u0) / px_per_m_x, (IMAGE_PX - frame.u0) / px_per_m_x)
    ax.set_ylim((frame.v0 - IMAGE_PX) / px_per_m_y, frame.v0 / px_per_m_y)

    parts = geometry.geoms if isinstance(geometry, MultiPolygon) else [geometry]
    vertices: list[np.ndarray] = []
    codes: list[np.ndarray] = []
    for polygon in parts:
        for ring in (polygon.exterior, *polygon.interiors):
            coords = np.asarray(ring.coords)[:, :2].astype(float)
            vertices.append(coords)
            ring_codes = np.full(len(coords), MplPath.LINETO, dtype=MplPath.code_type)
            ring_codes[0] = MplPath.MOVETO
            ring_codes[-1] = MplPath.CLOSEPOLY
            codes.append(ring_codes)
    path = MplPath(np.concatenate(vertices), np.concatenate(codes))
    ax.add_patch(
        PathPatch(path, facecolor="black", edgecolor="none", antialiased=False, linewidth=0.0)
    )

    canvas.draw()
    buffer = np.asarray(canvas.buffer_rgba())[:, :, 0]
    return buffer < 128


# --------------------------------------------------------------------------------------
# Composites
# --------------------------------------------------------------------------------------


def source_masks(image_name: str) -> tuple[np.ndarray, np.ndarray]:
    """``(wall_ink, other_ink)`` boolean masks for one source plan, as stored.

    No flip, no rotation, no rescale: ``data/source/PROVENANCE.md`` is explicit that these
    bitmaps are the un-mirrored canonical (E) as served, and that T13 must run against them
    exactly as stored.
    """
    pixels = np.asarray(Image.open(SOURCE_DIR / image_name).convert("RGB")).astype(np.int16)
    channel_max = pixels.max(axis=2)
    chroma = channel_max - pixels.min(axis=2)
    wall = (channel_max < WALL_INK_MAX_CHANNEL) & (chroma < WALL_INK_MAX_CHROMA)
    luma = pixels.mean(axis=2)
    return wall, (luma < CONTEXT_INK_MAX_LUMA) & ~wall


def composite(generated: np.ndarray, wall: np.ndarray, context: np.ndarray) -> np.ndarray:
    """The three-channel overlay: source-only red, generated-only blue, overlap black.

    Non-wall source ink is laid down first in pale grey so a human reviewing the image can
    see which plan the walls belong to. It is drawn from the fixed source bitmap and so
    adds nothing non-deterministic; the three diagnostic colours always paint over it.
    """
    image = np.full((IMAGE_PX, IMAGE_PX, 3), COLOUR_BACKGROUND, dtype=np.uint8)
    image[context] = COLOUR_CONTEXT
    image[wall & ~generated] = COLOUR_SOURCE_ONLY
    image[generated & ~wall] = COLOUR_GENERATED_ONLY
    image[generated & wall] = COLOUR_OVERLAP
    return image


def pixel_difference(a: np.ndarray, b: np.ndarray) -> float:
    """Fraction of pixels that differ between two composites. The golden-diff metric."""
    if a.shape != b.shape:
        raise AssertionError(f"composite shapes differ: {a.shape} vs {b.shape}")
    return float((a != b).any(axis=2).mean())


# --------------------------------------------------------------------------------------
# Building one overlay
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Overlay:
    """One level's overlay and the numbers derived from it."""

    level: str
    frame: PlanFrame
    z_m: float
    generated: np.ndarray
    wall: np.ndarray
    image: np.ndarray

    @property
    def _building(self) -> tuple[slice, slice]:
        """The image window the building occupies, with a small margin.

        Registration is scored inside it so that the archon logo, the dimension chains and
        the landscaping outside the walls cannot flatter or spoil the score.
        """
        margin = 6
        return (
            slice(int(self.frame.v1) - margin, int(self.frame.v0) + margin),
            slice(int(self.frame.u0) - margin, int(self.frame.u1) + margin),
        )

    def precision(self, dx: int = 0, dy: int = 0) -> float:
        """Fraction of generated section pixels sitting on drawn wall ink."""
        generated = self.generated[self._building]
        if dx or dy:
            generated = np.roll(np.roll(generated, dy, axis=0), dx, axis=1)
        return float((generated & self.wall[self._building]).sum() / generated.sum())

    @property
    def recall(self) -> float:
        """Fraction of drawn wall ink covered by the section. Reported, not asserted."""
        return float(
            (self.generated[self._building] & self.wall[self._building]).sum()
            / self.wall[self._building].sum()
        )

    @property
    def iou(self) -> float:
        generated = self.generated[self._building]
        wall = self.wall[self._building]
        return float((generated & wall).sum() / (generated | wall).sum())

    def best_offset(self, radius: int = REGISTRATION_SEARCH_PX) -> tuple[int, int, float]:
        """``(dx, dy, precision)`` of the best whole-pixel alignment within ``radius``."""
        best = (0, 0, self.precision())
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                score = self.precision(dx, dy)
                if score > best[2]:
                    best = (dx, dy, score)
        return best


def build_overlay(scene: trimesh.Scene, spec: Any, level_id: str) -> Overlay:
    """Section, rasterise and composite one level."""
    frame = PLAN_FRAMES[level_id]
    overall = (overall_mm(spec, "x"), overall_mm(spec, "y"))
    elevation_m = spec.level_by_id[level_id].elevation / 1000.0
    z_m = elevation_m + SECTION_HEIGHT_M
    generated = rasterise(section_polygons(scene, level_id, z_m), frame, overall)
    wall, context = source_masks(frame.image)
    return Overlay(
        level=level_id,
        frame=frame,
        z_m=z_m,
        generated=generated,
        wall=wall,
        image=composite(generated, wall, context),
    )


def write_png(image: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image, mode="RGB").save(path, optimize=False)
    return path


def overlay_digests(spec: Any | None = None) -> dict[str, str]:
    """SHA-256 of each level's composite. The cross-process determinism probe.

    Module-level and fixture-free on purpose: a subprocess imports this module and calls
    it directly under a different ``PYTHONHASHSEED``. Keep it that way.
    """
    spec = load_spec() if spec is None else spec
    scene = build_scene(spec)
    return {
        level: hashlib.sha256(build_overlay(scene, spec, level).image.tobytes()).hexdigest()
        for level in LEVEL_IDS
    }


# --------------------------------------------------------------------------------------
# Frame derivation -- the recorded constants, re-measured from the bitmaps
# --------------------------------------------------------------------------------------


def _dense_runs(profile: np.ndarray, threshold: float) -> list[tuple[int, int]]:
    """Contiguous index runs where ``profile`` exceeds ``threshold``, as ``[start, stop)``."""
    dense = profile > threshold
    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(dense):
        if dense[index]:
            start = index
            while index < len(dense) and dense[index]:
                index += 1
            runs.append((start, index))
        else:
            index += 1
    return runs


def derive_frame(wall: np.ndarray, overall_x_mm: float, overall_y_mm: float) -> PlanFrame:
    """Re-derive a plan frame from the bitmap plus the two printed overall dimensions.

    The gable walls are the two outermost near-full-height columns of ink, so the outer
    face of one and of the other are ``overall_x_mm`` apart -- that fixes the x anchors and
    the drawing's scale in one step, with no pixel ever being converted into a dimension.

    The rows need one extra step. On ``plan_attic.png`` the eaves overhang is drawn as a
    full-width hatched band above and below the walls, and it is exactly as dense as the
    facades; taking the outermost runs there would anchor y on the overhang and put the
    attic 25 px out. The pair of runs whose separation best matches the printed 900 cm at
    the x-derived scale is chosen instead, which picks the facades on both plans.
    """
    columns = _dense_runs(wall.sum(axis=0), 0.6 * wall.sum(axis=0).max())
    u0, u1 = float(columns[0][0]), float(columns[-1][1])
    px_per_mm = (u1 - u0) / overall_x_mm

    rows = _dense_runs(wall.sum(axis=1), 0.6 * wall.sum(axis=1).max())
    expected = overall_y_mm * px_per_mm
    candidates = [
        (float(top[0]), float(bottom[1]))
        for top in rows
        for bottom in rows
        if bottom[1] > top[0]
    ]
    v1, v0 = min(candidates, key=lambda pair: abs((pair[1] - pair[0]) - expected))
    return PlanFrame(image="", u0=u0, u1=u1, v0=v0, v1=v1)


# ======================================================================================
# Fixtures
# ======================================================================================


@pytest.fixture(scope="session")
def scene(spec) -> trimesh.Scene:
    """The generated 3D scene. Built by calling the generator, never loaded from disk.

    ``build/model.glb`` is T12's artifact and may be rewritten mid-run; nothing here may
    depend on it.
    """
    return build_scene(spec)


@pytest.fixture(scope="session")
def overlays(scene, spec) -> dict[str, Overlay]:
    """Both overlays, written to ``build/overlay_{level}.png`` as a side effect.

    Written to ``build/`` and *never* to ``tests/golden/`` -- see the module docstring.
    """
    result = {level: build_overlay(scene, spec, level) for level in LEVEL_IDS}
    for level, overlay in result.items():
        write_png(overlay.image, BUILD_DIR / f"overlay_{level}.png")
    return result


@pytest.fixture(scope="session")
def mirrored_overlays(spec) -> dict[str, Overlay]:
    """The same overlays built from a left-right mirrored spec. The evil twin.

    Also written out, as ``build/overlay_{level}_mirrored.png``, because the human doing
    the golden sign-off should be able to see what a *failing* overlay looks like next to
    the one they are being asked to accept.
    """
    twin = mirror_spec(spec)
    twin_scene = build_scene(twin)
    result = {level: build_overlay(twin_scene, twin, level) for level in LEVEL_IDS}
    for level, overlay in result.items():
        write_png(overlay.image, BUILD_DIR / f"overlay_{level}_mirrored.png")
    return result


# ======================================================================================
# 1. The frames
# ======================================================================================


@pytest.mark.parametrize("level", LEVEL_IDS)
def test_the_recorded_plan_frames_are_the_measured_ones(level: str, spec) -> None:
    """Re-derive each frame from its bitmap and confirm the recorded constants.

    The frames are the one place this test converts between millimetres and pixels, so a
    stale constant here would silently rotate every other result in this module into
    nonsense. Half a pixel is 12 mm.
    """
    frame = PLAN_FRAMES[level]
    wall, _ = source_masks(frame.image)
    derived = derive_frame(wall, overall_mm(spec, "x"), overall_mm(spec, "y"))
    for name in ("u0", "u1", "v0", "v1"):
        assert getattr(derived, name) == pytest.approx(getattr(frame, name), abs=0.5), (
            f"{frame.image}: recorded {name} = {getattr(frame, name)} but the bitmap "
            f"measures {getattr(derived, name)}. The frame moved, or the wall-ink mask "
            f"is picking up something that is not a wall."
        )


def test_the_two_plans_do_not_share_a_frame(spec) -> None:
    """``plan_ground.png`` and ``plan_attic.png`` are drawn at the same scale, offset.

    Stated as an assertion because the cheap mistake is to derive one frame and reuse it.
    The y offset is 44 px = 1.08 m, which would not look like a frame error in the
    overlay -- it would look like the whole ground floor being in the wrong place.
    """
    ground, attic = PLAN_FRAMES["ground"], PLAN_FRAMES["attic"]
    assert attic.u0 - ground.u0 == pytest.approx(7.0, abs=0.5)
    assert ground.v0 - attic.v0 == pytest.approx(44.0, abs=0.5)

    overall_x, overall_y = overall_mm(spec, "x"), overall_mm(spec, "y")
    for frame in (ground, attic):
        # 40.9 px/m on both plans, ~24.5 mm per pixel.
        assert frame.px_per_mm_x(overall_x) == pytest.approx(0.04094, abs=1e-4)
        assert frame.px_per_mm_y(overall_y) == pytest.approx(0.04089, abs=1e-4)


# ======================================================================================
# 2. The section is cut from the real mesh
# ======================================================================================


@pytest.mark.parametrize("level", LEVEL_IDS)
def test_the_section_is_cut_from_the_generated_mesh(scene, spec, level: str) -> None:
    """The cut is taken 1.0 m above the finished floor and lands inside the envelope.

    Cheap, but it is the assertion that says *what* is being compared. If this ever starts
    reading T07's shapely polygons instead of the mesh, the test stops seeing generator
    bugs while continuing to pass, and nothing else in the suite would notice.
    """
    z_m = spec.level_by_id[level].elevation / 1000.0 + SECTION_HEIGHT_M
    cut = section_polygons(scene, level, z_m)
    assert cut.area > 0.0

    # The finished outline is the 17.100 x 9.000 m structure plus 10 mm of render per face.
    # The two gable walls run to the roof on both levels, so the x extent is the full
    # finished width either way.
    render_m = 0.010
    minx, miny, maxx, maxy = cut.bounds
    assert minx == pytest.approx(-render_m, abs=0.002)
    assert maxx == pytest.approx(overall_mm(spec, "x") / 1000.0 + render_m, abs=0.002)

    if level == "ground":
        assert miny == pytest.approx(-render_m, abs=0.002)
        assert maxy == pytest.approx(overall_mm(spec, "y") / 1000.0 + render_m, abs=0.002)
    else:
        # The attic's north and south walls are the 290 mm knee walls; a cut 1.0 m above
        # the attic floor is 710 mm above their tops and legitimately misses both. The
        # gable walls survive, but only where the roof is still 1.0 m clear of the floor,
        # so the section starts 1.057 m in from the south face -- which is exactly
        # (roof top at the springing + rise) resolved for z, and therefore also a cheap
        # confirmation that the gables really were trimmed against the roof rather than
        # left as full rectangles.
        rise_to_cut = SECTION_HEIGHT_M + spec.level_by_id["attic"].elevation / 1000.0
        assert miny == pytest.approx(_roof_clearance_offset_m(spec, rise_to_cut), abs=0.01)
        # maxy still reaches the north facade: the stair balustrade A_W8 is 1100 mm high
        # and runs into it, and it is 1.0 m off the floor that this cut is taken.
        assert maxy <= overall_mm(spec, "y") / 1000.0 + render_m


def _roof_clearance_offset_m(spec: Any, z_m: float) -> float:
    """Where the roof underside first rises to ``z_m``, measured on the fall axis, metres.

    Derived from :class:`~kotewki.generator.RoofGeometry`, so it is the same ridge the
    generator computed from the pitch rather than a number typed in here.
    """
    roof = roof_geometry(spec)
    assert roof.ridge_axis == "x", (
        f"roof.ridge_axis is {roof.ridge_axis!r}; this helper assumes the roof falls "
        f"along y, which is what makes the offset a y coordinate."
    )
    run = (roof.ridge_elevation_mm - roof.fascia_depth - z_m * 1000.0) / roof.slope
    return (roof.ridge_coord - run) / 1000.0


def test_the_attic_section_is_short_of_the_drawn_plan_by_design(overlays) -> None:
    """The attic knee walls are 290 mm high, so a cut at +1.0 m passes clean over them.

    This is the building, not a bug, and it is asserted rather than merely noted so that
    nobody "fixes" the low attic recall by lowering the section height. What survives the
    cut is the two gable walls, the two internal cross walls, the balustrades and the
    stacks; the north and south facades appear in ``build/overlay_attic.png`` as solid red
    bands and are *supposed* to.

    Ground recall is 0.93 and attic recall is 0.28 for exactly this reason.
    """
    assert overlays["ground"].recall > 0.85
    assert overlays["attic"].recall < 0.50


# ======================================================================================
# 3. Registration against the published plans
# ======================================================================================


@pytest.mark.parametrize("level", LEVEL_IDS)
def test_the_generated_walls_land_on_the_drawn_walls(overlays, level: str) -> None:
    """Precision against the plan's own wall ink. Measured 0.977 ground, 0.903 attic."""
    overlay = overlays[level]
    precision = overlay.precision()
    assert precision >= MIN_REGISTRATION_PRECISION[level], (
        f"{level}: only {precision:.1%} of the generated section falls on drawn wall ink "
        f"(threshold {MIN_REGISTRATION_PRECISION[level]:.0%}). IoU {overlay.iou:.3f}, "
        f"recall {overlay.recall:.3f}. Look at build/overlay_{level}.png: blue is geometry "
        f"the model invented, red is a wall the plan draws and the model does not have."
    )


@pytest.mark.parametrize("level", LEVEL_IDS)
def test_the_best_alignment_is_no_alignment(overlays, level: str) -> None:
    """Sliding the section around can only make it worse. The registration proof.

    A high precision score on its own could be bought by a coincidence of thick walls; a
    score that *peaks at zero offset* over a +-3 px (+-74 mm) search cannot. This is also
    what certifies the frame constants: an origin one pixel out would show up here as a
    peak at (1, 0) long before it dented the precision.
    """
    dx, dy, best = overlays[level].best_offset()
    assert (dx, dy) == (0, 0), (
        f"{level}: the section registers better shifted by ({dx}, {dy}) px "
        f"({best:.4f} vs {overlays[level].precision():.4f} at zero). That is "
        f"{abs(dx) * 24.5:.0f} mm in x and {abs(dy) * 24.5:.0f} mm in y -- either the plan "
        f"frame is off by a pixel or the model genuinely sits in the wrong place."
    )


def test_the_orientation_anchor_holds(scene, spec) -> None:
    """The cheap mirroring check: three named rooms, in the quadrants they belong in.

    From ``data/source/PROVENANCE.md`` and ``tasks/T13.md``: on ``plan_ground.png`` the
    entrance (Wiatrolap, room 1) is bottom-centre-left, the Kotlownia (room 11) is
    bottom-right, and the two front bedrooms (rooms 3 and 5) are on the left edge.

    Deliberately independent of the image diff, and deliberately measured off the
    **mesh** -- the room plates the generator emits -- not off the spec. If the overlay is
    ever disabled, this survives and still catches a mirrored build.
    """
    centroids = _room_centroids(scene, spec)
    width = overall_mm(spec, "x") / 1000.0
    depth = overall_mm(spec, "y") / 1000.0

    entrance = centroids[1]
    assert entrance[1] < 0.5 * depth, "Wiatrolap is not on the south (entrance) side"
    assert 0.25 * width < entrance[0] < 0.55 * width, "Wiatrolap is not centre-left"

    boiler = centroids[11]
    assert boiler[1] < 0.5 * depth, "Kotlownia is not at the bottom of the plan"
    assert boiler[0] > 0.75 * width, "Kotlownia is not on the right (east) side"

    for published_id in (3, 5):
        assert centroids[published_id][0] < 0.25 * width, (
            f"bedroom {published_id} is not on the left (west) edge -- the classic "
            f"signature of a mirrored plan"
        )
    # The east bedroom, room 8, is the asymmetry the two west bedrooms are measured against.
    assert centroids[8][0] > 0.75 * width


def _room_centroids(
    scene: trimesh.Scene, spec: Any, level_id: str = "ground"
) -> dict[int, tuple[float, float]]:
    """``published_id -> (x, y)`` plan centroid of each room plate in the mesh, metres.

    Scoped to one level because ``published_id`` is the number printed on *that level's*
    plan and is only unique within it: the ground floor's Pokoj and the attic's Strych
    ocieplony are both room 3. Keying on the bare number silently lets the attic overwrite
    the ground floor, and the anchor check then reads the wrong room's centroid -- which
    is exactly how this helper failed the first time it was run.
    """
    published_by_room = {
        room.id: room.published_id for room in spec.rooms if room.level == level_id
    }
    centroids: dict[int, tuple[float, float]] = {}
    for name, mesh in scene.geometry.items():
        if not name.startswith(f"{level_id}/rooms/"):
            continue
        centre = mesh.centroid
        for room_id in mesh.metadata["room_ids"]:
            centroids[published_by_room[room_id]] = (float(centre[0]), float(centre[1]))
    return centroids


# ======================================================================================
# 4. THE DELIBERATE TEST -- mirror the spec and confirm the overlay fails
# ======================================================================================


def test_mirroring_preserves_every_dimension_it_should(spec) -> None:
    """The twin really is a *mirror*: same sizes, same areas, same openings, flipped.

    This runs first because the meta-test below is only evidence if the mirrored spec is
    the pathological case it claims to be. A mirror that also shrank the building, or
    dropped an opening, would fail the overlay for the wrong reason and would prove
    nothing about mirroring at all.
    """
    twin = mirror_spec(spec)
    axis = overall_mm(spec, "x")

    assert len(twin.walls) == len(spec.walls)
    assert len(twin.openings) == len(spec.openings)
    assert len(twin.rooms) == len(spec.rooms)

    for original, flipped in zip(spec.walls, twin.walls, strict=True):
        assert flipped.id == original.id
        assert flipped.thickness == original.thickness
        assert flipped.length == pytest.approx(original.length)
        assert flipped.start[1] == original.start[1]
        assert flipped.start[0] == axis - original.start[0]
        assert flipped.end[0] == axis - original.end[0]

    # Offsets are preserved by construction -- the wall's own start moved with it.
    for original, flipped in zip(spec.openings, twin.openings, strict=True):
        assert (flipped.offset, flipped.width, flipped.sill, flipped.height) == (
            original.offset,
            original.width,
            original.sill,
            original.height,
        )

    # The exterior envelope maps onto itself: only the interior layout has moved.
    xs = [value for wall in spec.walls for value in (wall.start[0], wall.end[0])]
    assert min(xs) + max(xs) == axis, (
        "the mirror axis is not the outline's own centreline, so the twin is translated "
        "as well as flipped and the overlay could fail on the translation instead"
    )


@pytest.mark.parametrize("level", LEVEL_IDS)
def test_a_mirrored_spec_fails_the_overlay_diff(overlays, mirrored_overlays, level: str) -> None:
    """**The most important assertion in this module.**

    If a mirrored model passed here, this test would not work and the project would have
    no defence at all against shipping a perfect mirror image of the right house -- which
    satisfies every chain sum, every published room area and all five global invariants.

    Stated through :func:`pixel_difference`, the *same* function
    :func:`test_the_overlay_matches_its_golden` uses, and against the honest overlay
    standing in for the golden that has not been signed off yet. So this is literally the
    statement "the mirrored build fails the golden diff", not a weaker proxy for it.

    Measured: 5.01% of the ground composite and 1.40% of the attic composite change,
    against a 0.01% threshold -- 500x and 140x.
    """
    honest, twin = overlays[level], mirrored_overlays[level]
    difference = pixel_difference(honest.image, twin.image)
    assert difference > GOLDEN_MAX_DIFF_FRACTION, (
        f"{level}: a MIRRORED model produced a composite {difference:.6f} different from "
        f"the honest one, within the {GOLDEN_MAX_DIFF_FRACTION} golden threshold. The "
        f"overlay does not discriminate mirroring and the project has no mirroring guard."
    )
    assert difference > 20 * GOLDEN_MAX_DIFF_FRACTION, (
        f"{level}: the mirrored model differs by only {difference:.6f}, which is inside "
        f"one order of magnitude of the golden threshold. Nominally a pass, but far too "
        f"close to call the overlay a defence."
    )


@pytest.mark.parametrize("level", LEVEL_IDS)
def test_a_mirrored_spec_stops_registering_against_the_plan(mirrored_overlays, level: str) -> None:
    """The same finding again, without reference to any image the test itself produced.

    :func:`test_a_mirrored_spec_fails_the_overlay_diff` compares two generated composites;
    this compares the mirrored model against the **published bitmap**. Both legs have to
    hold, because the first would still pass if the whole rendering path were broken in
    some way that happened to differ between the two runs.

    Measured 0.516 ground and 0.570 attic, against 0.977 and 0.903 honest.
    """
    twin = mirrored_overlays[level]
    assert twin.precision() < MIRRORED_MAX_REGISTRATION_PRECISION, (
        f"{level}: a mirrored model still puts {twin.precision():.1%} of its section on "
        f"drawn wall ink. The plan is too symmetric for this metric to see the flip, or "
        f"the mirror is not being applied."
    )
    assert twin.precision() < MIN_REGISTRATION_PRECISION[level]


def test_a_mirrored_spec_fails_the_orientation_anchor(spec) -> None:
    """The cheap quadrant check must fail on the twin too, or it is decorative."""
    twin = mirror_spec(spec)
    centroids = _room_centroids(build_scene(twin), twin)
    width = overall_mm(spec, "x") / 1000.0

    assert centroids[11][0] < 0.25 * width, "mirrored Kotlownia did not move to the west"
    assert centroids[3][0] > 0.75 * width, "mirrored bedroom 3 did not move to the east"
    assert not 0.25 * width < centroids[1][0] < 0.55 * width, (
        "mirrored Wiatrolap still reads as centre-left, so the anchor is not discriminating"
    )


# ======================================================================================
# 5. Determinism
# ======================================================================================


def test_the_overlay_is_identical_across_three_runs(scene, spec, overlays) -> None:
    """Three renders, zero pixel delta. The acceptance criterion, run literally.

    Cheap insurance against the failure mode that actually kills this kind of test: an
    overlay that wobbles by a handful of pixels gets its threshold raised, then raised
    again, and then switched off -- and switching it off is what removes the only mirroring
    guard in the project.
    """
    for run in range(3):
        for level in LEVEL_IDS:
            again = build_overlay(scene, spec, level)
            delta = int((again.image != overlays[level].image).sum())
            assert delta == 0, f"{level}: run {run + 1} differs from the first by {delta} px"


#: Run by ``python -c`` in a fresh interpreter; ``sys.argv[1]`` is this ``tests/``
#: directory, which is how the module is found without depending on pytest's sys.path
#: manipulation being in effect.
SUBPROCESS_PROBE = (
    "import sys;"
    "sys.path.insert(0, sys.argv[1]);"
    "import test_overlay;"
    "print(' '.join(f'{k}={v}' for k, v in "
    "sorted(test_overlay.overlay_digests().items())))"
)


def test_the_overlay_is_byte_identical_across_processes(overlays) -> None:
    """Same overlay from a fresh interpreter under a different ``PYTHONHASHSEED``.

    T11 established that the *generator* is deterministic across processes. This carries
    that guarantee through the section, the raster and the composite -- the three steps
    T11's check does not cover -- and it is the guarantee a committed golden actually
    rests on. Two seeds, because one could agree with the parent by luck.
    """
    expected = {
        level: hashlib.sha256(overlay.image.tobytes()).hexdigest()
        for level, overlay in overlays.items()
    }
    for seed in ("0", "524287"):
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            [sys.executable, "-c", SUBPROCESS_PROBE, str(Path(__file__).resolve().parent)],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO_ROOT,
            env=environment,
        )
        assert completed.returncode == 0, completed.stderr
        digests = dict(item.split("=") for item in completed.stdout.split())
        assert digests == expected, (
            f"PYTHONHASHSEED={seed} produced a different overlay:\n"
            f"  in-process {expected}\n  subprocess {digests}"
        )


# ======================================================================================
# 6. The golden diff -- gated on human sign-off
# ======================================================================================


@pytest.mark.parametrize("level", LEVEL_IDS)
def test_the_overlay_matches_its_golden(overlays, level: str) -> None:
    """Diff against ``tests/golden/overlay_{level}.png``.

    Skips -- never fails, and never writes -- while the golden is absent. The first
    generation of goldens is pending human sign-off, scheduled for the end of the project
    (TASKS.md). Auto-accepting one would lock in whatever was wrong at the time and leave
    the project believing it had a mirroring guard when it had only a tautology.
    """
    golden_path = GOLDEN_DIR / f"overlay_{level}.png"
    if not golden_path.is_file():
        pytest.skip(
            f"golden {golden_path.relative_to(REPO_ROOT)} is absent: the first generation "
            f"of goldens is PENDING HUMAN SIGN-OFF and this test will not create it. "
            f"Review build/overlay_{level}.png by eye (black = model and plan agree, red = "
            f"plan-only, blue = model-only), compare it against "
            f"build/overlay_{level}_mirrored.png to see what a failure looks like, and only "
            f"then copy it into tests/golden/."
        )

    golden = np.asarray(Image.open(golden_path).convert("RGB"))
    difference = pixel_difference(overlays[level].image, golden)
    assert difference <= GOLDEN_MAX_DIFF_FRACTION, (
        f"{level}: build/overlay_{level}.png differs from its signed-off golden in "
        f"{difference:.6%} of pixels (limit {GOLDEN_MAX_DIFF_FRACTION:.6%}). Look at the "
        f"two images before touching this threshold -- raising it is how the mirroring "
        f"guard gets quietly deleted."
    )


def test_the_goldens_are_absent_or_are_real_goldens() -> None:
    """Whatever is in ``tests/golden/`` must be a 853x853 RGB PNG, or nothing at all.

    Guards the sign-off gate from the other side: a half-written or wrong-sized golden
    would make the diff above fail for a reason that has nothing to do with the model.
    """
    for level in LEVEL_IDS:
        path = GOLDEN_DIR / f"overlay_{level}.png"
        if not path.is_file():
            continue
        with Image.open(path) as image:
            assert image.size == (IMAGE_PX, IMAGE_PX), f"{path.name}: {image.size}"
            assert image.mode in ("RGB", "RGBA"), f"{path.name}: mode {image.mode}"


# ======================================================================================
# 7. Reporting
# ======================================================================================


def test_report_the_overlay_registration(overlays, mirrored_overlays, record_property) -> None:
    """Not an assertion -- the numbers, reported, so a run says how well the model fits.

    Everything asserted above is a threshold; this is the measurement behind it, and it is
    what a reviewer should read before signing off a golden. Emitted as a warning, the same
    way ``tests/test_topology.py`` reports its residuals, so the figures appear in a plain
    ``pytest`` run instead of only under ``-s``.
    """
    lines = [
        "",
        f"{'level':<8} {'precision':>10} {'recall':>8} {'IoU':>7} "
        f"{'offset':>8} {'mirrored':>10} {'diff':>9}",
    ]
    for level in LEVEL_IDS:
        overlay, twin = overlays[level], mirrored_overlays[level]
        dx, dy, _ = overlay.best_offset()
        difference = pixel_difference(overlay.image, twin.image)
        lines.append(
            f"{level:<8} {overlay.precision():>10.4f} {overlay.recall:>8.4f} "
            f"{overlay.iou:>7.4f} {f'({dx},{dy})':>8} {twin.precision():>10.4f} "
            f"{difference:>9.5f}"
        )
        record_property(f"overlay_{level}_precision", overlay.precision())
        record_property(f"overlay_{level}_mirrored_diff", difference)
    lines.append("")
    lines.append("overlays written to build/overlay_{ground,attic}.png")
    lines.append("goldens: PENDING HUMAN SIGN-OFF -- tests/golden/ is deliberately empty")
    warnings.warn("\n".join(lines), UserWarning, stacklevel=1)

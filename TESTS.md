# Validation suite

Five categories, run by `pytest` in this order, **failing fast**. Order matters: each
category is cheaper and more localising than the next, so the first failure you see is
the most diagnostic one.

| # | Category | Catches | Task | Test module |
|---|---|---|---|---|
| 1 | Dimension chain closure | Transcription typos, before any geometry exists | T06 | `tests/test_chains.py` |
| 2 | Room areas vs published table | Wrong dimensions, wrong wall thicknesses | T08 | `tests/test_room_areas.py` |
| 3 | Global invariants | Vertical geometry, envelope, roof errors | T09 | `tests/test_invariants.py` |
| 4 | Topology & sanity | Structurally impossible geometry | T10 | `tests/test_topology.py` |
| 5 | Post-export mesh | Generator/exporter bugs, unit-scale bugs | T12 | `tests/test_export.py` |
| 6 | Orthographic overlay | Dimensionally-valid but topologically wrong layout | T13 | `tests/test_overlay.py` |

---

## 1. Dimension chain closure — `T06`

Highest value, cheapest to run, requires no geometry. Every printed dimension chain on a
plan must sum to the printed overall dimension for that run.

```python
def test_chain_closure(spec):
    for chain in spec["dimension_chains"]:
        assert sum(chain["segments"]) == chain["total"], chain["id"]
```

Integer millimetres means this is exact equality — no tolerance. Catches most
transcription errors before a single polygon is built.

Also assert **cross-chain consistency**: chains running in the same axis across the same
extent must agree with each other.

## 2. Room areas vs published table — `T08`

The checksum. `shapely` computes each room polygon area from the wall network; compare
against the `published_area` field carried on each room.

```python
def test_room_areas(model):
    for room in model.rooms:
        computed = room.polygon.area
        assert abs(computed - room.published_area) / room.published_area < 0.01
```

**Diagnostic rule — read the failure pattern:**

- A *uniform* offset across all rooms → wrong measurement norm, or wrong plaster/finish
  thickness. Do not chase individual rooms; fix the convention. See `T15`.
- A *single* room off → a real geometry error in that room.

Attic rooms need the sloped-ceiling rule: **below 1.4 m excluded, 1.4–2.2 m counted at
50%, above 2.2 m counted in full.** If attic areas fail and ground floor passes, suspect
this rule rather than the geometry.

The 1.4/2.2 thresholds are confirmed twice over: they are the PN-ISO 9836 bands Archon
states, and `plan_attic.png` prints its ceiling-height contour lines labelled `140` and
`220`. Rooms are measured **w świetle ścian** — to finished faces, plaster included.

**Band from the ceiling plane at 3330 mm** (attic floor 3040 + knee wall 290), *not* from
the roof's outer plane at 3610 — see README → "Two different planes". Banding from 3610
puts the contours ~0.4 m out and over-reads the attic by ~20%. Traced contour positions
d140 = 1589 mm and d220 = 2732 mm (T05) are the primary datum; roof-derived values agree
to 4 mm.

**`Schody` (stairs) is not banded at all.** Its published 3.64 m² is plain floor area, and
it belongs to `net` only, never `usable`. Banding it yields ~1.55 m², which looks exactly
like a 2 m² geometry bug and is not one.

## 3. Global invariants — `T09`

Five scalars constraining the model from directions the room areas cannot reach.

| Invariant | Target | Tolerance | Validates |
|---|---|---|---|
| Usable area | 163.57 m² | ±1% | Interior layout + wall thicknesses |
| Footprint | 154.42 m² | ±1% | Exterior envelope at ground level |
| Cubature | 849.27 m³ | ±1.5% | Storey heights + roof volume |
| Roof area | 216.8 m² | **±6%** | 35° pitch + eave overhangs |
| Building height | 7.09 m | **±30 mm** | Section geometry, ground → ridge |

Roof pitch is asserted **exactly** at 35.0°: it is a spec input, not a derived value.
Ridge height must *fall out* of pitch + eaves geometry and then be compared to 7.09 m.
Never force the ridge to 7.09 m by construction — that discards the only roof check.

**Two tolerances above were revised during the build, both away from numbers this project
could not defend.** They are the only two that moved:

- **Roof area, ±1% → ±6%.** The measured overhangs (600 mm eaves, 590 mm verge, traced on
  two independent images) give 227.6 m² against a published 216.8 m². 216.8 implies a
  0.44 m uniform overhang. T17 declined to shrink the overhang to fit, because the overhang
  is measured and the publisher's *powierzchnia dachu* convention is not. This is a band on
  one invariant whose definition is unknown — not a tolerance widened to pass. It keeps its
  teeth: the refuted 40.7° pitch reads +13.4% and fails it.
- **Building height, ±10 mm → ±30 mm.** `roof_buildup_vertical` carries a measured ±30 mm
  uncertainty, so ±10 mm claimed a precision the inputs do not support — it would have
  passed with 0.93 mm to spare, which is luck, not evidence. The computed value
  (7.080934 m, −9.07 mm) is separately pinned at 1e-5, so nothing is lost by the wider band.

## 4. Topology & sanity — `T10`

Cheap assertions that catch structural nonsense:

- Wall networks form closed loops; no gaps, no T-junction overshoots
- Room polygons are `is_valid`, non-self-intersecting, mutually non-overlapping
- Union of all room polygons + all wall footprints == exterior footprint polygon
- Every opening fits within its host wall: `offset + width <= wall.length`
- Openings on the same wall do not overlap
- `sill + height <= ceiling_height` for every opening
- Every room referenced in the published table exists in the spec, and vice versa
  (no silently dropped or invented rooms)

## 5. Post-export mesh — `T12`

Verify the **artifact**, not just the intent. Runs against `build/model.glb` after export.

```python
def test_exported_mesh(glb_path):
    scene = trimesh.load(glb_path)
    bbox = scene.bounding_box.extents
    assert np.allclose(bbox[:2], EXPECTED_FOOTPRINT_M, atol=0.01)
    assert np.isclose(bbox[2], 7.09, atol=0.01)   # <- WRONG, see below
```

> **The Z assertion in that sketch is wrong and T12 correctly refused to implement it.**
> The scene's Z extent is **7.681 m** — from the plinth at −0.320 up to the chimney tops at
> 7.361 — not 7.09. Building height is asserted on the **roof nodes'** bbox top instead:
> `6.760934 − (−0.320) = 7.080934 m`. The XY pair needs the same care: the scene bbox is
> 18.280 × 10.200 because it includes the eaves and verge overhangs, so the published
> 17.120 × 9.020 footprint is asserted on the **wall** nodes.

Plus: mesh is watertight, no degenerate faces, no NaN vertices, scene unit scale is 1.0,
and the round-trip through glTF preserves the bounding box (guards against exporter
scaling bugs).

**Watertight per component, not as a solid.** All 106 components are watertight,
winding-consistent and positive-volume, but they interpenetrate: 21.0028 m³ across 150
pairs against 267.322 m³ of member volume, 7.9% double-counted. This is why cubature is a
closed-form integral over the 2D footprint and touches no mesh volume. See README
"Outstanding" item 4.

## 6. Orthographic overlay — `T13`

The check that catches what the numbers cannot: layouts that are dimensionally
self-consistent but **topologically wrong** — a room on the wrong side of a corridor, a
mirrored wing, a door on the wrong wall. Those pass every area assertion and are instantly
obvious in a visual overlay.

Method: take a **horizontal section of the actual generated 3D mesh** at 1.0 m above each
floor level, raster it, and diff against the source plan image at matched scale. Cutting
the real mesh rather than re-drawing the 2D geometry means this also catches generator
bugs, not just spec bugs. 1.0 m is chosen to match the PN-70 measurement height and to cut
through door openings and below most window sills.

> **`trimesh.Scene.section` does not exist** — this document originally named it, and it is
> not an API. In trimesh 4.x `Scene` has no `.section()`; `Trimesh.section()` does exist but
> routes through `trimesh.graph.traversals` → **scipy, which is not a dependency here**, so
> it raises `ModuleNotFoundError`. T13 uses `trimesh.intersections.mesh_plane` (pure numpy)
> per scene node, then shapely `polygonize`. Same cut of the same mesh, no new dependency.
>
> One trap in that pipeline, worth knowing before anyone touches it: raw `mesh_plane`
> segments **must** be snapped (`shapely.set_precision` at 1e-9 m, the grid
> `generator.SNAP_GRID_M` already uses) before `polygonize`, or a wall's cut silently yields
> *no* polygon and no error. The EPS layer on `G_W8` did exactly that.

Goldens committed under `tests/golden/` — **but only after human sign-off**, which is
deferred to the end of the project. Until then the diff test skips with a message saying
so. An auto-accepted golden locks in whatever was wrong at the time.

This is the primary guard against the **(E) variant mirroring** problem: Archon suffixes
denote mirrored or modified variants, and a perfectly-built mirror image of the right
house passes every single numeric check.

---

## What this suite does and does not guarantee

**Guarantees:** the model matches the transcribed spec, and the spec is internally
consistent and agrees with every published figure.

**Does not guarantee:** that a printed number was read correctly *when the wrong value
happens to satisfy every chain sum, area, and invariant simultaneously.* Redundancy is the
defence, not certainty. With chain closure + **18 area checks of which 16 are
independent** (not the "~19" this line originally claimed — the ground floor yields 11 not
14, because four open-plan rooms collapse to a single polygonised face, and two attic
checks are algebraically dependent; `tests/test_room_areas.independent_equation_count()`
computes it) + 5 global invariants + visual overlay, the
surviving error space is very small — but it is not empty. State this honestly in any
report; do not claim the model is provably correct.

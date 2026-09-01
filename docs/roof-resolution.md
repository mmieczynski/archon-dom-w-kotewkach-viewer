# T17 — Roof geometry resolution

**Status:** RESOLVED. High confidence.
**Verdict:** the roof pitch is **35.0°**, exactly as published. **No published figure is wrong.**
The discrepancy in `README.md` comes from a misreading of what the section's **+2.88**
level mark points at: it is the **eave line at the outer edge of the ~0.6 m roof overhang**,
not the top of the wall plate. Once that is corrected, every published number closes to
within a few centimetres.

---

## 1. Summary of recommendations

| Field | Value | Provenance |
|---|---|---|
| `pitch_deg` | **35.0** | **published** — confirmed by measurement, `derived: false` |
| `eaves_overhang` | **0.60 m** (±0.05) | `derived: true` — measured, section + attic plan |
| `verge_overhang` (gable ends) | **0.59 m** (±0.05) | `derived: true` — measured, attic plan |
| roof build-up above knee-wall top, measured vertically | **0.28 m** (±0.03) | `derived: true` — measured, section |
| roof form | **simple symmetric two-slope gable**, ridge along the 17.10 m axis, centred | measured |

Everything else stays as published: attic floor +3.04, knee wall 0.29, ridge +6.77,
terrain −0.32, building height 7.09.

**Span used: 9.00 m (structural).** See §1a — this is the printed chain figure, not the
back-solved 9.03, and it is what the rafters bear on.

**Uncertainty range on the pitch: 34.5°–35.5°.** 40.7° is *excluded*, not merely disfavoured
(see §5 — it requires a physically impossible negative knee wall).

---

## 1a. Which building width this analysis uses, and why

The T02 correction is adopted: **9.03 m is not a confirmed figure** (it was back-solved from
the published 154.42 m² footprint, so citing the resulting match as confirmation is circular).
All roof calculations below use the **structural width 9.00 m**, for three reasons:

1. It is what is *printed*: the left chain on `plan_ground.png` reads `820` + `80` = `900`,
   and `site.png` prints the same `900` in its setback chain.
2. It is what the drawings *measure*. Independently, at this task's own calibrations:
   `plan_attic.png` gives an outer width of **9.006 m** (368 px at 40.86 px/m) with 0.453 m
   walls and an 8.10 m interior — i.e. 9.00 = 8.10 + 2 × 0.45, exactly. `section.png` gives
   **8.95–9.00 m** between the drawn wall faces (227.4 px), with a drawn wall thickness of
   0.449 m. Neither drawing shows 9.03.
3. Rafters bear on structure, not on render.

**Sensitivity — this choice does not affect any conclusion:**

| Span | Roof plane at wall face required for ridge +6.77 at 35° | Measured | Residual |
|---|---|---|---|
| **9.00 m** | +3.619 | +3.646 | +27 mm |
| 9.03 m | +3.609 | +3.646 | +37 mm |

| Span | Ridge predicted from published components (3.04 + 0.29 + 0.28 + half-span·tan 35°) | vs printed +6.77 |
|---|---|---|
| **9.00 m** | +6.761 | −9 mm |
| 9.03 m | +6.771 | +1 mm |

Both are inside the ±30 mm tolerance. The half-span difference is 15 mm against a
discrepancy of 730 mm, so the pitch finding is completely insensitive to it. 9.00 m is used
throughout; where a number below was computed at 9.03 the difference is noted and is ≤ 10 mm.

I did **not** resolve the 154.42 vs 153.90 m² footprint-area residual — that is T02's
question, not this task's, and the roof does not depend on it. I note only that my
measurements are consistent with a *structural* 9.00 × 17.10 rectangle and give no evidence
either way about a finished-face allowance or a small projection.

---

## 2. Where the README's arithmetic went wrong

The README computed:

```
springing +2.88  +  4.50 · tan(35°)  =  +2.88 + 3.15  =  +6.03     ✗  (section prints +6.77)
```

This assumes the roof plane springs from +2.88 **at the exterior wall face**. It does not.
Measurement on the section shows:

- **+2.88 is the underside of the eave at the outer edge of the overhang**, i.e. ~0.60 m
  *outboard* of the wall face and ~0.31 m *below* the roof plane at that point.
- The roof's outer (covering) surface crosses the **exterior wall face at ≈ +3.61 m**.

The 0.73 m "gap" decomposes exactly:

```
overhang drop      0.61 m · tan(35°)  =  0.427 m
roof build-up at the fascia (vertical) =  0.310 m
                                          -------
                                          0.737 m   vs the README's 0.73 m gap
```

And the chain then closes:

```
attic floor           +3.04
+ knee wall            0.29   (published)      →  top of knee wall  +3.33
+ roof build-up        0.28   (measured)       →  roof plane at wall face  +3.61
+ 4.50 · tan(35°)      3.15                    →  ridge  +6.76   ✓  printed +6.77 (−9 mm)
ridge − terrain = 6.76 − (−0.32) = 7.08        ✓  published building height 7.09 (−9 mm)
```

The three "implied roof spans" in the README (11.11 m from rise-at-35°, 10.39 m from
roof-area-at-35°) were artefacts of the same wrong springing point. They dissolve.

---

## 3. Measurement method

All measurements are sub-pixel, computed with NumPy/PIL over the as-stored bitmaps in
`data/source/` (read-only; nothing was modified). Ink coverage was treated as fractional
pixel coverage and edges located at the 50 % crossing.

### 3.1 `section.png` (400×300) — primary, dimensioned source

**Vertical calibration.** Three printed level marks were located by their symbol geometry
(downward triangle whose apex touches the level), and cross-checked against the drawn
construction they annotate:

| Level | Feature it marks | Pixel row |
|---|---|---|
| ±0.00 | top edge of ground-floor slab | 219.74 |
| +3.04 | top edge of attic slab | 142.97 |
| +6.77 | roof apex (intersection of the two fitted roof planes) | 47.8 |

Least-squares fit through those three points: **s = 25.41 px/m**, ±0.00 at row 219.93.
Residuals ≤ 0.3 px (≤ 12 mm). An independent check on the printed clear height `270`
(slab soffit at row 151.72) gives 25.19 px/m — 0.9 % agreement.

**Horizontal calibration.** The cut exterior walls are drawn solid black at the attic-storey
level: left x = 56.6…68, right x = 272.6…284. Wall thickness 11.4 px = 0.45 m, matching the
published Porotherm 25 + EPS 20 + render ≈ 465–480 mm build-up. Outer faces 56.6 and 284.0
→ **227.4 px = 9.00 m** at the vertical scale. Horizontal and vertical scales agree to
within 0.9 %; the drawing is isotropic.

**Pitch.** For every row 58…138 the outer edge of the roof band was located to sub-pixel
accuracy and a straight line fitted:

| Roof plane | Fitted slope (dx/dr) | Fit RMS | Implied pitch |
|---|---|---|---|
| left | −1.42798 | **0.06 px** over 81 rows | **35.003°** |
| right | +1.4418 | 1.2 px (watermark contamination) | 34.74° |

The left slope is essentially a perfect straight line — the section is a vector render, and
it is drawn at **exactly 35°**. This alone settles the question; the printed `35°` label is
not a rounded marketing figure, it is the drawn geometry.

**Symmetry.** The two fitted roof planes intersect at x = 170.1. The mid-point of the two
wall faces is x = 170.3. The ridge is centred on the building to within 0.2 px (≈ 8 mm).
The roof is a **symmetric** gable.

**Derived section levels** (using s = 25.41 px/m, ±0.00 at row 219.93):

| Feature | Row | Height |
|---|---|---|
| roof apex (outer surface) | 47.8 | **+6.774** (printed +6.77) |
| top of masonry / knee wall (where the roof soffit meets the wall face) | 135.0 | **+3.342** (= 3.04 + 0.30) |
| attic slab top | 142.97 | **+3.029** (printed +3.04) |
| eave underside at the fascia | 146.2 | **+2.902** (printed +2.88) |
| roof outer plane at the wall face x = 56.6 | 127.3 | **+3.646** |

Predicted roof plane at the wall face from the published numbers alone,
6.77 − 4.50·tan 35° = **+3.619**. Measured +3.646. Difference 27 mm ≈ 0.7 px, less than the
stroke width of the drawn roof line. **The section is internally consistent to ~1 px
everywhere.**

### 3.1.1 The +2.88 / +3.04 "wall plate below the floor it supports" oddity — RESOLVED

This was flagged as a possible key to the discrepancy. It is not an oddity at all once
+2.88 is correctly identified: **+2.88 is not the wall plate**, so nothing is below anything.
The section's own construction confirms this directly.

The attic slab was measured edge-to-edge in the section:

| | Row | Level |
|---|---|---|
| slab top (= attic floor) | 142.97 | **+3.029** (printed +3.04) |
| slab soffit (= ground-floor ceiling) | 151.72 | **+2.684** (printed clear height `270` → +2.70) |
| **thickness** | **8.75 px** | **0.344 m** |

The spec sheet gives the reinforced-concrete ceiling slab as **340 mm**. The section draws
**344 mm**. The ground-floor vertical chain therefore closes on its own terms:

```
±0.00  +  2.70 clear  =  +2.70 slab soffit
       +  0.34 slab   =  +3.04 attic floor      ✓  both printed, both measured
```

The coordinator's observation that "3.04 − 2.88 = 0.16 does not reconcile with a 340 mm slab"
is correct, and the reason is that **+2.88 is not part of that chain at all**. It is an
exterior level on the roof, 0.60 m outboard of the wall:

```
roof plane at wall face  +3.61
 − overhang drop 0.61 · tan 35°   0.427
 − roof build-up at fascia        0.310
                                  = +2.88   ✓  the printed eave level
```

Nothing is missing and nothing contradicts. The slab is 340 mm, the knee wall is 290 mm, and
they sit in sequence above +2.70 — none of them near +2.88.

### 3.1.2 What springing level would make 35° correct, and does the section show a feature there?

Working the coordinator's question in the requested direction: for a symmetric 35° gable on a
9.00 m span to reach the printed ridge +6.77, the roof plane must pass the wall face at

```
6.77 − 4.50 · tan 35°  =  +3.619 m
```

i.e. **0.74 m above +2.88**, not at it. Does the section show anything at that height?
Yes — two distinct, mutually consistent features:

| Required feature | Predicted | Measured in the section |
|---|---|---|
| roof **outer covering** surface at the wall face | +3.619 | **+3.646** (row 127.3, from the fitted 35° plane) |
| structural bearing beneath it (top of knee wall) | +3.619 − 0.28 build-up = +3.34 | **+3.342** (row 135, where the roof soffit band meets the wall face) |

And the second of those is independently predicted by two *published* numbers that were never
used to derive it: attic floor **+3.04** plus knee wall **0.29** = **+3.33**. Measured +3.342.
Agreement 12 mm.

So the answer to "is +6.77 reachable at 35°?" is yes, and the springing level it requires is
not an invented number — it is the top of the published 29 cm knee wall sitting on the
published +3.04 attic floor, plus a measured 0.28 m of roof build-up. The datum mismatch was
real, but it is a mismatch in the README's *label* for +2.88, not in the drawing.

**Eave overhang.** Fascia outer face at x = 40.5 (left) and x = 300 (right); wall faces
56.6 and 284.0 → **16.1 px = 0.636 m** and **16.0 px = 0.632 m**. Symmetric.

**Roof build-up.** Vertical distance from the fascia underside to the roof outer plane at the
same x: 7.6–8.5 px = **0.30–0.34 m** vertical, i.e. 0.25–0.28 m measured perpendicular to the
slope. Consistent with a ~20 cm rafter plus counterbatten/batten/tile. The same figure read
at the wall face (3.646 − 3.342) is **0.304 m**.

### 3.2 `plan_attic.png` (853×853) — the independent cross-check

**Calibration.** Three mutually consistent scale sources:

| From | px | m | px/m |
|---|---|---|---|
| interior clear width (printed `810`), rows 189.5→519.5 | 331.0 | 8.10 | 40.86 |
| outer width (printed chain `820`+`80`=`900`), rows 170.5→538.5 | 368.0 | 9.00 | 40.89 |
| outer length (printed chain, 17.10), cols 75.5→776.5 | 701.0 | 17.10 | 40.99 |

Adopted **40.90 px/m** (spread 0.3 %). Note this also confirms the wall thickness
independently: (368 − 331)/2 = 18.5 px = **0.45 m**.

**Contour positions.** The plan is rendered in three flat greys —
`(157,158,160)` below 1.4 m, `(217,217,218)` between 1.4 and 2.2 m, `(235,236,236)` above
2.2 m. Band boundaries were read on twelve independent columns spanning both `Strych
ocieplony` rooms and both ends of the building. **All twelve columns give identical rows:**

| Contour | Row | Distance from interior wall face |
|---|---|---|
| `140`, north side | 254.5 | 66.0 px = 1.615 m |
| `220`, north side | 300.5 | 112.0 px = 2.741 m |
| `220`, south side | 408.5 | 111.0 px = 2.716 m |
| `140`, south side | 455.5 | 64.0 px = 1.566 m |

Means: **d140 = 1.589 m, d220 = 2.726 m.**

---

## 4. Candidate explanations — each tested

### (1) "The pitch at the section is not 35°; the implied value is 40.7°." — **REFUTED**

The drawn roof line in the section is a straight line at 35.003° with a 0.06 px fit residual.
The attic contours (§5) independently give 35.1°. Three of four clean fits on the
photorealistic gable elevations give 34.92°, 34.95°, 34.99°, 35.01°. Nothing anywhere
measures 40.7°. The 40.7° was an artefact of the wrong springing point.

### (2) "The roof is not a simple symmetric gable." — **REFUTED**

- Section: two straight planes, equal slope, apex centred on the building to 0.2 px.
  No mansard break, no second slope, no knee-wall step in the roof surface.
- `elevation_front.png` and `elevation_garden.png`: unbroken slope from eave to ridge over
  the full 17.10 m. Three flush roof windows on the front; no dormers on either long face.
- `elevation_side_1/2.png`: a plain isoceles gable triangle.
- `plan_attic.png`: the four ceiling contours are straight, parallel to the eaves, and
  symmetric about the ridge — a single-slope-each-side gable.

It is a **simple symmetric two-slope gable**, as the spec sheet ("dach dwuspadowy") says.

### (3) "Datum mismatch — +6.77 is not the ridge, or +2.88 is not what we think." — **PARTLY TRUE, and this is the answer**

+6.77 *is* the ridge (of the outer roof surface): the level symbol's apex sits on the drawn
roof apex to within 1 px.

**+2.88 is NOT the wall plate.** Its level symbol sits on the eave underside at the outer
edge of the overhang (row 146.2 → +2.902 measured, +2.88 printed). The wall plate / knee-wall
top is at **+3.33**, and the roof plane at the wall face is at **+3.61**. `README.md`'s
vertical-geometry table row "Eave / wall plate top | +2.88" conflates two different levels
0.73 m apart. **This single misreading is the whole discrepancy.**

The corroborating tell — raised by the coordinator and worth stating as a standalone
argument — is that +2.88 makes no sense as a wall plate: it would put the plate *below* the
+3.04 floor it carries, and 3.04 − 2.88 = 0.16 m cannot be the 340 mm slab. Both objections
are correct, and both dissolve the moment +2.88 is removed from the wall chain. The real
chain is 0.00 → 2.70 (clear) → 3.04 (slab top, slab measured 0.344 m against a published
0.34 m) → 3.33 (knee wall) → 3.61 (roof plane). See §3.1.1 and §3.1.2 for the measurements.

### (4) "Eave overhangs are large (~0.4 m) and reconcile the area but not the rise." — **HALF RIGHT**

The reasoning in the brief is correct: an overhang cannot move the ridge. But an overhang
*does* move the **eave line**, and +2.88 is on the eave line — so the overhang is exactly
what produced the apparent 0.73 m rise gap. The measured overhang is 0.60 m, not 0.4 m
(see §6 for the residual this leaves in the published roof *area*).

---

## 5. The decisive evidence — attic contour cross-check: **SUPPORTS 35°**

This check uses only `plan_attic.png` and the published 29 cm knee wall. It does not use the
section at all.

Under PN-ISO 9836 the contours are the loci where the finished sloping ceiling reaches 1.40 m
and 2.20 m above the attic floor. With knee-wall height *k* and pitch *θ*, measured from the
interior face of the knee wall:

```
d140 = (1.40 − k)/tan θ        d220 = (2.20 − k)/tan θ
```

**Prediction vs measurement:**

| | d140 | d220 |
|---|---|---|
| predicted, θ = 35.0°, k = 0.29 | 1.585 m (64.8 px) | 2.728 m (111.6 px) |
| **measured on the plan** | **1.589 m (65.0 px)** | **2.726 m (111.5 px)** |
| predicted, θ = 40.7°, k = 0.29 | 1.290 m (52.8 px) | 2.221 m (90.8 px) |

**35° reproduces the drawn contours to 4 mm and 2 mm — well under one pixel.**
40.7° misses them by 0.30 m and 0.51 m, i.e. by 12 and 21 pixels on an 853-px drawing.

Inverting instead of predicting (so the result does not assume the knee wall):

```
tan θ = 0.80 / (d220 − d140) = 0.80 / 1.137  →  θ = 35.13°
implied knee wall = 1.40 − d140·tan 35° = 0.287 m   (published 0.29)
                  = 2.20 − d220·tan 35° = 0.291 m   (published 0.29)
```

Two contour pairs, read at four locations, independently return **35.1° and a 28.7–29.1 cm
knee wall**. The published knee wall is 29 cm.

**40.7° is not merely unlikely, it is impossible**: forcing θ = 40.7° onto the drawn contour
positions requires a knee wall of **+3 cm** (from the 140 line) and **−14.5 cm** (from the
220 line). A negative knee wall is not a geometry.

Uncertainty: contour edges and wall faces are each located to ±0.5 px, so the 46.5 px
separation carries ±0.7 px ≈ ±1.5 %, giving **θ = 35.1° ± 0.6°** from this source alone.

### Secondary check — banded attic areas

Counted depth per PN-ISO 9836 = (100 % of the >2.2 band) + (50 % of the 1.4–2.2 bands)
= (8.10 − 2·d220) + (d220 − d140) = **3.785 m**.

| Room | Printed length | Predicted counted area | Published |
|---|---|---|---|
| `Strych ocieplony` (2) | 393 cm | 14.87 m² | 14.67 m² (+1.4 %) |
| `Strych ocieplony` (3) | 503 cm | 19.04 m² | 18.21 m² (+4.5 %) |
| **sum** | | **33.91 m²** | **32.88 m²** (+3.1 %) |

At 40.7° the counted depth would be 4.589 m and the sum **41.12 m² — 25 % over published**.

The +3.1 % residual at 35° is the expected direction and rough magnitude for the ~20 mm/face
finish allowance (T15) plus the chimney and internal-wall deductions visible on the plan; it
is a soft check and it is not used to discriminate. Inverting it gives θ ≈ 34.3–34.5°, i.e.
within 0.7° of 35° — again nowhere near 40.7°.

---

## 6. Corroborating evidence — elevations (weak, as expected)

`elevation_side_1/2.png` are photorealistic renders with residual perspective, so these are
corroboration only. The roof edge against the sky was traced per-column and fitted robustly:

| Image | Branch | Angle | Fit RMS | n |
|---|---|---|---|---|
| `elevation_side_2` | left | **34.95°** | 0.29 px | 98 |
| `elevation_side_1` | right | **34.99°** | 0.29 px | 48 |
| `elevation_side_1` | right (wider window) | **34.92°** | 0.54 px | 209 |
| `elevation_side_1` | left | 36.8–37.4° | 0.6–1.1 px | 47–60 |

The clean branches cluster tightly on 35.0°. The one branch reading ~37° is the short,
tree-occluded side and demonstrates the ~2° perspective bias these renders can carry — which
is precisely why they are not used as proof. Nothing measures anywhere near 40.7°.

---

## 7. Overhangs, and the one figure that does *not* fully close

`plan_attic.png` draws the roof outline (eave band with rafter tails) beyond the walls:

| Direction | Roof extent (px) | Wall faces (px) | Overhang |
|---|---|---|---|
| across the span (eaves) | 145.5 … 563.5 | 170.5 … 538.5 | 25.0 px = **0.611 m** each side |
| along the ridge (verges) | 51.5 … 800.5 | 75.5 … 776.5 | 24.0 px = **0.587 m** each end |

The section independently gives an eave overhang of **0.636 m / 0.632 m**. Adopt
**0.60 m eaves**, **0.59 m verges**, both `derived: true`, ±0.05 m.

### Residual: published roof area 216.8 m²

(all at span 9.00 × 17.10)

| Assumption | Roof area |
|---|---|
| measured overhangs (0.60 all round), 35° | **227.9 m²** |
| directly from the plan-measured roof outline 18.31 × 10.22, 35° | **228.5 m²** |
| 0.44 m uniform overhang, 35° | **216.9 m²** ← the published figure |
| 0.60 m overhang, 40.7° | 246.2 m² |
| 0.44 m overhang, 40.7° | 234.3 m² |

The published 216.8 m² corresponds to a uniform 0.44 m overhang, which is 5 % less roof than
the drawings actually show. **This is a genuine, unresolved, secondary inconsistency** and it
should be recorded as such rather than papered over.

Two things to note about it:

1. It does not touch the pitch conclusion. At *any* overhang consistent with the drawings,
   216.8 m² is far closer to 35° than to 40.7°. Forcing 216.8 m² at 40.7° would require a
   **0.197 m** overhang, contradicting a directly measured 0.60 m by a factor of three.
2. I could not determine the publisher's convention for *powierzchnia dachu* (whether it
   nets off roof windows, stops at the fascia rather than the tile edge, or excludes the
   verge overhang — 17.10 m × 10.20 m / cos 35° = 212.9 m², within 1.8 %, if verges are
   excluded). **Do not use 216.8 m² as a hard assertion target for the roof.** Use it as a
   ±6 % sanity band, or assert against the geometry instead.

---

## 8. Recommended spec values, and how to keep the height check honest

T09/T11 must not hard-code the ridge at +6.77 — that would turn the 7.09 m building-height
invariant into a tautology. Build the roof from independent inputs and let the ridge fall out:

```
pitch_deg              = 35.0     # published, derived: false
span                   = 9000 mm  # printed chain 820+80=900 (structural), derived: false
attic_floor            = 3040 mm  # printed on the section, derived: false
ceiling_slab           = 340 mm   # spec sheet; section measures 344 mm, derived: false
knee_wall              = 290 mm   # printed on the spec sheet, derived: false
roof_buildup_vertical  = 280 mm   # derived: true  (measured 0.28–0.30; ±30 mm)
eaves_overhang         = 600 mm   # derived: true  (±50 mm)
verge_overhang         = 590 mm   # derived: true  (±50 mm)
```

Then, with nothing forced:

```
springing (roof outer plane at the wall face) = 3040 + 290 + 280        = 3610 mm
ridge = 3610 + (9000/2)·tan 35°               = 3610 + 3151            = 6761 mm
        → assert |ridge − 6770| ≤ 30 mm                                ✓ (−9 mm)
building height = ridge − terrain = 6761 − (−320)                      = 7081 mm
        → assert |height − 7090| ≤ 30 mm                               ✓ (−9 mm)
eave line (fascia underside) = 3610 − 600·tan 35° − 310                = 2880 mm
        → assert |eave − 2880| ≤ 30 mm                                 ✓ (0 mm)
ground-floor chain: 0 + 2700 clear + 340 slab                          = 3040 mm
        → assert attic_floor == 3040                                   ✓ (exact)
```

(If T02 later confirms a finished-face span of 9030 mm instead, the ridge becomes 6771 mm
and both roof assertions land within 1 mm. Either width passes; the tolerance covers both.)

Three real checks, each with a ~30 mm margin, and only one derived input
(`roof_buildup_vertical`) in the chain. That is the strongest formulation available: the
single free parameter is a physically meaningful roof thickness that was measured
independently in the section (0.30 m vertical at the wall face, 0.30–0.34 m at the fascia),
and it is confirmed by an entirely separate route — the attic contours, which fix the
*ceiling* plane and hence the knee wall to 28.7–29.1 cm against a published 29 cm.

---

## 9. Confidence, and what I could not determine

**Pitch: 35.0°, high confidence. Range 34.5°–35.5°. 40.7° is excluded.**

Four independent sources agree: the section's drawn roof line (35.003°, vector-exact), the
attic ceiling contours (35.13° ± 0.6°, and they exclude 40.7° on physical grounds), the
banded attic areas (34.3–34.5°, excludes 40.7° by 25 %), and the clean gable-render branches
(34.92–35.01°). The published 35° is correct.

**Which published figures are wrong: none.** The error was in `README.md`'s interpretation of
the +2.88 level mark as "eave / wall plate top". It is the eave only. That table row should
read:

```
| Ground-floor ceiling (slab soffit)      | +2.70  (printed clear height 270)     |
| Eave (underside of overhang, at fascia) | +2.88  (0.60 m OUTBOARD of the wall)  |
| Attic floor (slab top, slab = 0.34)     | +3.04                                 |
| Top of knee wall / wall plate           | +3.33  (derived: 3.04 + 0.29)         |
| Roof plane at exterior wall face        | +3.61  (derived: 3.33 + 0.28)         |
| Ridge                                   | +6.77                                 |
```

The `+2.88` row must be moved out of the wall's vertical chain entirely — it is an exterior
level on the roof overhang and does not sit between +2.70 and +3.04 in any structural sense.

**Not determined:**

- **The convention behind the published roof area 216.8 m².** The drawn overhangs give
  228.5 m² (+5.4 %). I could not establish whether the publisher nets off roof windows,
  measures to the fascia rather than the tile edge, or excludes the verge overhang (that
  last one gets to within 1.6 %). Flagged in §7; do not assert hard against it.
- **9.00 m vs 9.03 m building width.** Not resolved here, and not this task's question
  (T02 owns it). Everything I measured says the *drawings* are 9.00 m structural: the printed
  chain `820`+`80`=`900`, the attic-plan outline (9.006 m with 0.453 m walls and an 8.10 m
  interior), and the section (8.95–9.00 m, wall thickness 0.449 m). The 9.03 figure exists
  only as a back-solve from the published 154.42 m² and I found no independent support for
  it. I used 9.00 throughout; it shifts the computed ridge by 10 mm and changes no conclusion.
  I also found **no evidence bearing on the 154.42 − 153.90 = 0.52 m² footprint residual** —
  no projection or bump is visible in the section, but the section is a single cut and cannot
  rule one out.
- **Exact rafter depth vs total build-up.** The section is 400×300; I can measure the roof
  band's total vertical thickness (0.30–0.34 m) but cannot separate rafter from insulation
  from covering. `roof_buildup_vertical = 280 mm` is a single lumped derived value.
- **Whether the section's cut plane passes through a ground-floor window on the left.**
  The left ground-floor wall renders differently from the right (a 1.2 m-wide solid block at
  lintel level, thin outlines below). It does not affect the roof and I did not chase it.

**What would settle the open items:** the purchased documentation, or any dimensioned
elevation. The public site publishes no larger section (`PROVENANCE.md` §"Resolution" — the
lightbox for the section is dead), so 400×300 is the ceiling for the section. That turned out
to be enough, because the attic plan at 853×853 carries the decisive constraint at 40.9 px/m.

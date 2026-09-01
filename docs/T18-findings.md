# T18 — Three residuals re-examined against the source bitmaps

**Rule:** no spec file is touched by this task. Everything below is a *recommendation*,
reported to the coordinator, who applies it.

## Summary

| Item | Verdict | Confidence | Action |
|---|---|---|---|
| 1 — Schody −6.3 % | **Measurement convention, not missing geometry.** The drawn polygon at *structure* is 3.64 ± 0.05 m². Any allowance above 4.1 mm/face fails ±1 %. | high | Exempt `A_R4` from the finish allowance, move its south edge to the printed `y = 4700` → 3.6575, +0.5 %. **Do not add the "nub"** — on the current polygon it overshoots to +6.8 %. |
| 1b — is the "nub" floor? | **Yes, it is floor** — the part of the bottom landing east of the flight. Not a post, not furniture. But the record's stairwell east edge (5450) is wrong by ~76 mm, and that error is what makes the nub look like a free 0.25 m². | high | Rewrite the `A_W8` note; it cites a line that does not exist over the rows it claims. |
| 2 — roof-window depth | **No discrepancy.** Drawn depth is 1296 ± 12 mm, not 1271. Against `1600·cos35° = 1310.6` that is 0.6 px. The recorded 3.1 % was a one-pixel edge-convention error. | high | Close Outstanding #2; correct 1271 → 1296 in `spec/meta.json` ×3, `generator.py`, `report.py`, `test_generator.py`. |
| 3 — gable windows | **Spec is right.** Near jamb on the ridge to under 1 px on both gables; centred is excluded by 29 px. The two windows sit on opposite sides of the ridge, exactly as the spec encodes. | very high | Close Outstanding #3 as confirmed; add the corroboration to the `A_O1`/`A_O2` notes. |

Two of the three "open" items were open because of **one-pixel measurement errors made
earlier in this project**, not because the source is ambiguous. Nothing here required
inventing geometry, and nothing here changes a published number.

## 0. Frames, scales and the size of one pixel

All plan measurements below use the T13 frames, re-derived here from the bitmaps and
confirmed against printed dimensions before use.

| | `u0` (x=0) | `u1` (x=17100) | `v0` (y=0) | `v1` (y=9000) |
|---|---|---|---|---|
| `plan_ground.png` | 69.0 | 769.0 | 583.0 | 215.0 |
| `plan_attic.png` | 76.0 | 776.0 | 539.0 | 171.0 |

* x scale `SX = 700 px / 17100 mm = 0.0409357 px/mm` → **24.43 mm per pixel**
* y scale `SY = 368 px / 9000 mm = 0.0408889 px/mm` → **24.46 mm per pixel**
* `col = u0 + x·SX`, `row = v0 − y·SY`

Both plans are 853×853 and that is the largest raster the source offers
(`data/source/raw/*.gif` are the same 853×853; there is no higher-resolution original).
**One pixel is ~24.4 mm, so a half-pixel reading is ±12 mm and no measurement below can be
better than about ±25 mm without sub-pixel intensity modelling.**

Frame confirmed independently before it was trusted, on `plan_attic.png`:

| Feature | Measured (sub-pixel, ink-coverage) | Spec / printed | Δ |
|---|---|---|---|
| `A_W5` partition east face | col 260.28 → **x = 4502** | 4500 (printed chain A_C1) | +2 mm |
| `A_W6` partition west face | col 546.06 → **x = 11482** | 11500 (printed chain A_C1) | −18 mm |
| `A_W6` partition centre | col 549.05 → **x = 11555** | 11560 | −5 mm |
| North wall inner face | row 189.3 → **y = 8552** | 8550 | +2 mm |

so the middle bay measures 6980 mm against the printed 7000 (−0.8 px). Good to ±20 mm.

---

# Item 1 — Schody (`A_R4`), the −6.3 % residual

## Verdict

**The residual is a measurement-convention error, not missing geometry.** The polygon the
drawing actually shows, measured at *structure* with **no finish allowance on any edge**,
is **3.64 ± 0.05 m²** against the published **3.64 m²**. Applying the 20 mm/face allowance
to it is what produces the −6 % miss.

Both the coordinator and the existing record are partly right and partly wrong:

* The coordinator is **right** that the "add a 0.25 m² nub to the current polygon" fix is
  wrong, and right that the finish convention is the real culprit. The record's arithmetic
  (`3.412 + 0.25 = 3.66`) would land in tolerance for the wrong reason.
* The coordinator is **wrong** that the nub is a balustrade post / handrail return /
  furniture. It is drawn in the *floor* tone on `plan_attic.png`, the stair's **walking
  line** (`linia biegu`) starts inside it, and it is the part of the bottom landing that
  lies east of the flight. It is floor.
* The record is **wrong** about the stairwell's east edge. `A_W8`'s note traces it to
  "plan column 298.9 … present on rows 188–342". That line is present on rows **190–225
  only**; it is the *west edge of the landing box*, not the east edge of the flight. Over
  the flight itself the boundary is 3 px further west.

The two errors in the current spec very nearly cancel, which is why `3.6005` looks close.

## 1.1 What is actually drawn

All ink positions sub-pixel, from linear ink-coverage on 1-px grey lines (line grey ≈ 117,
floor fill 236, `Pustka` fill 197, walls 0/55). Where a feature appears on both bitmaps the
two independent readings are given — they agree to 0.5–1 px, which is the cross-check on
the frames.

| Feature | `plan_attic.png` | → mm | `plan_ground.png` | → mm | adopted |
|---|---|---|---|---|---|
| `A_W5` partition, east face | col 260.28 | 4502 | col 252.83 | 4491 | **4502 ± 12** |
| Flight, east boundary | cols 294.6–297.5, white-cored double line, centre **296.0** | 5338 / 5409, centre **5374** | col 289.0 (dashed) | **5374** | **5374 ± 15** |
| Landing box, west line | col 299.0 | 5448 | col 291.0 | 5423 | **5435 ± 15** |
| Landing box, east line | col 309.55 | 5705 | col 302.0 | 5692 | **5698 ± 15** |
| Step in the boundary (landing/flight) | rows 225–226 | 7655–7679 | row 270.0 | **7655** | **7660 ± 15** |
| North exterior wall, inner face | row 189.3 | 8552 | row 233.5 | 8548 | **8550** |
| Void / guard edge (south end) | row 346.0, single 1-px line | **4720** | — | — | **4720 ± 12** |
| Walking-line origin dot (filled) | col 309.5, row 207 | 5702, 8120 | col 302, row 251.5 | 5692, 8107 | — |

Crops for eyeball confirmation: `plan_attic.png` cols 252–338 × rows 183–245 (x 4300–6400,
y 7200–8700) and the same window on `plan_ground.png` at cols 245–331 × rows 227–289.

### The shape

North of `y ≈ 7660` the stair is **wider** than the flight. On both bitmaps the flight's
east boundary line simply **stops** at that row and the floor tone continues east to
`x ≈ 5698`. So `Schody` is an **L**, not a rectangle:

```
flight   x 4502..5374   y 4700..7660     0.872 × 2.960 = 2.581 m²
landing  x 4502..5698   y 7660..8550     1.196 × 0.890 = 1.064 m²
                                          total        = 3.645 m²   (+0.1 % vs 3.64)
```

The south edge is taken at the **printed** 4700 (chain `A_C5`: 450 wall + 4250 printed
Antresola depth), not at the traced 4720 — the traced line is 1 px wide, so 4700 (0.8 px
away) and 4760 (1.6 px away) are both inside its blur and the printed number must win.

Propagated uncertainty on the L, at ±15 mm per traced edge, is **±0.05 m² (±1.4 %)**.

### Why the "nub" is floor and not a post

1. **Tone.** On `plan_attic.png` the box interior is grey **236** — the same value as both
   `Strych ocieplony` rooms and the stair flight itself. The `Pustka nad salonem` around it
   is **197**. 236 is the plan's floor tone; 197 is its void tone. A post or a piece of
   furniture would be drawn *over* the 197 fill, as the Antresola furniture is.
2. **The walking line runs through it.** The "filled dot" is the origin marker of the
   stair's `linia biegu`. From it a polyline runs west along `y = 8120` (row 207,
   cols 277→309) to `x = 4910`, then south down the middle of the flight to a solid
   triangular **arrowhead** at rows 338–343 (`y ≈ 4830`). `x = 4910` is the centreline of
   the flight (4502..5374 → 4938, agreement 0.5 px) and `y = 8120` is the centreline of
   the landing (7660..8550 → 8105, agreement 0.6 px). A dot-to-arrow polyline centred in
   both legs is a walking line and nothing else. Its origin is *inside* the box, so the box
   is part of the route.
3. **The "rounded corner"** is the box's own south-east corner at (col 309.5, row 225.5) =
   `x 5705, y 7667`, drawn with a 1-px fillet. It is not a handrail return.
4. The identical box appears on `plan_ground.png` for the obvious reason: it is the bottom
   landing of the same stair, at ground-floor level, seen from above through the stairwell
   opening. Its line weight (grey 117, 1 px) is the same as every other floor-edge line on
   the sheet; walls on these bitmaps render 0–55.

## 1.2 The arithmetic that settles the convention

| Reading of `Schody` | Area | vs 3.64 |
|---|---|---|
| L as drawn, **structure** | **3.645** | **+0.1 %** |
| L as drawn, 20 mm/face all round (≈ A − 0.020 × 10.08 m perimeter) | 3.44 | −5.4 % |
| Current spec rectangle 4500..5450 × 4760..8550, structure | 3.6005 | −1.1 % |
| Current spec rectangle, **finish** (what T08 reports) | 3.4125 | −6.3 % |
| Spec rectangle with south edge moved to the printed 4700, structure | 3.6575 | +0.5 % |
| Spec rectangle at 4700, finish | 3.467 | −4.8 % |
| Spec rectangle 4700 **+ the 0.25 m² nub**, structure | 3.89 | **+6.8 %** |

Two things fall out:

* **The finish convention is excluded, not merely mis-tuned.** The L has a 10.084 m
  perimeter, so `A(t) ≈ 3.645 − 10.084·t`. Staying inside ±1 % of 3.64 needs
  `t ≤ 4.1 mm/face`. Every allowance from 5 mm upwards fails — 20 mm misses by 5.4 %.
  No value of the allowance rescues this room; only dropping it does.
* **Adding the nub to the *current* polygon overshoots badly** (+6.8 % at structure). The
  nub is only admissible together with narrowing the flight from 950 to 872 — and those two
  corrections are 0.23 m² and −0.22 m², so they cancel. That is precisely why the wrong
  rectangle scores −1.1 %.

Because they cancel, **the raster cannot distinguish the L from the rectangle**: the plain
rectangle `4500..5450 × 4700..8550` gives 3.6575 (+0.5 %) and the L gives 3.645 (+0.1 %),
and 0.4 % is well inside the ±1.4 % the pixels support. The choice between them is a
faithfulness decision, not a measurable one.

Why no allowance is the *right* convention rather than a convenient one: `Schody` is not a
room measured *w świetle ścian*. Under PN-ISO 9836 a stair is counted as the plan
projection of the flight and landings, i.e. the slab opening — two of its four edges
(south and east) are a void edge and a guard, which have no plaster to deduct, and the
publisher's own figure equals the drawn opening. `spec/attic.json`'s `A_SO2` note already
records that the opening is 3.6005 m² "which matches Schody's raw structure-convention
area"; that agreement is the same fact seen from the other side.

## 1.3 Recommendation

**Recommended (minimal, low-risk):** keep `A_R4` a rectangle, move its south edge to the
printed `y = 4700`, and measure it at **structure**, exempt from the finish allowance.

* `A_R4` polygon `x 4500..5450, y 4700..8550` → **3.6575 m², +0.5 %**, inside ±1 %.
* Requires `A_W7`'s 60 mm guard to stop eating into `Schody` — either place the guard
  centreline on 4700 (occupying 4670..4730, standing astride the slab edge) or keep 4730
  and let `A_R4`'s south edge be the slab edge rather than the guard's north face.
  The drawing cannot decide between the three positions: the guard is a **single 1-px
  line** at 4720 ± 12, and 4700 / 4730 / 4760 span 60 mm = 2.4 px. Report it as
  undetermined and let the printed 4700 fix the *floor edge*, which is what the areas
  depend on.
* `A_R4` must be flagged so `MeasureTo` is not applied to it. This is a one-room carve-out
  and it must be justified in the note, not hidden.

**Faithful alternative:** re-shape `A_R4` to the measured L (`3.645 m², +0.1 %`). More
accurate to the drawing but it drags `A_SO2`, `A_W8` and the `A_SO2`/`A_SO1` split with it
and re-opens T10's connectivity check. Not worth it for 0.4 % that the raster cannot see.

**Do NOT** add a 0.25 m² nub to the existing polygon. That is the one change that is
definitely wrong: +6.8 %.

## 1.4 Corrections to the existing record

1. `README.md` lines ~344–348, "**Schody is a geometry gap, not a measurement question.**"
   — **wrong, invert it.** It is a measurement question. Delete the paragraph, keep the
   ⚠ dispute note's competing explanation, and replace with the convention finding.
2. `README.md` line ~327, table row `Schody (attic) | 3.412 | 3.64 | −6.25% | Missing
   geometry — fixable` — the status is wrong; it is "measured at the wrong convention".
3. `README.md` line ~430, Outstanding item 1 "**Add the Schody stairwell landing nub**
   (~0.25 m²) to `spec/attic.json`" — **withdraw this action.** Performing it overshoots to
   +6.8 %.
4. `README.md` line ~363, "Schody must be excluded from the objective: its fixed 0.25 m²
   deficit is something no allowance can supply" — the *conclusion* (exclude Schody from
   the allowance sweep) stays correct, but the *reason* is wrong: there is no 0.25 m²
   deficit; the room simply does not take the allowance.
5. `spec/attic.json` line 407 (`A_R4.note`), final sentence "The plan additionally shows a
   ~0.25 m² landing nub at the north-east corner of the stairwell (see A_W8) which is not
   modelled; adding it would over-read, so it is left out and reported." — half wrong.
   "Adding it would over-read" is correct; "landing nub" is correct as *floor*; but the
   note must say the polygon it is being added to is 76 mm too wide, and that the
   −1.1 % / −6.3 % gap is a convention error. Also `Polygon x 4500..5450, y 4760..8550 =
   3.60 m² against 3.64 (−1.1%)` should become `x 4500..5450, y 4700..8550 = 3.6575
   (+0.5%)`.
6. `spec/attic.json` line 194 (`A_W8.note`) — **factually wrong and must be rewritten.**
   "Traced from the continuous vertical line at plan column 298.9 (present on rows
   188-342), which is the east side of the stairwell" — no line exists at col 298.9 on rows
   226–346. On those rows the flight's east boundary is a white-cored double line at cols
   294.6–297.5 (`x 5338..5409`, centre **5374**). The col-299 line exists on rows 190–225
   only and is the **west edge of the bottom landing**, `x 5448`. Consequently "giving a
   950 mm clear stairwell" is not supported: the drawn clear width over the flight is
   **872 ± 20 mm**. Likewise the nub coordinates quoted there (`x 5450..5721, y
   7630..8555`) should read `x 5435..5698, y 7660..8550`, ≈ 0.23 m².
7. `tests/test_room_areas.py` `RECORDED_RESIDUALS["A_R4:usable"]` (lines 129–136) — the
   whole entry is wrong: "spec/attic.json records a ~0.25 m2 landing nub … the unmodelled
   nub explains the whole residual and the finish convention is not at fault. This is the
   one check where the deficiency is a known missing piece of geometry rather than a
   measurement question." The finish convention **is** at fault and it **is** a measurement
   question. If the recommendation is applied the entry should be **deleted** (the check
   comes inside ±1 %), which the set-equality assertion will force anyway.
8. `README.md` "Known area-check limitations" — worth adding `Schody` as a fourth bullet:
   it is the one room whose boundaries are a slab opening rather than plastered masonry, so
   the finish allowance does not apply to it.

**Confidence: high** on "the finish convention is the fault" (no allowance in 0–30 mm gets
within 1 %, and structure lands at +0.1…+0.5 % from three independent edge readings).
**High** on "the nub is floor, not a post" (floor tone + the walking line originates in it,
on both bitmaps). **Medium** on the exact shape (L vs rectangle) — the two are 0.4 % apart
and the raster resolves ±1.4 %.

---

# Item 2 — roof-window plan depth (README Outstanding #2)

## Verdict

**There is no 3.1 % discrepancy. The recorded 1271 mm is a one-pixel tracing error.** The
drawn plan-projected depth is **1296 ± 12 mm**, against `1600 · cos 35° = 1310.6 mm` — a
residual of **0.6 px / 14.6 mm / 1.1 %**, which is below what this raster can resolve. The
callout identity holds. No change to the building is implied.

## The measurement

The three roof windows are drawn on `plan_attic.png` as dashed rectangles with `78/160`
callout circles, cols 344–450 × rows 439–492 (x 6547–9136, y 1149–2446). Crop
`plan_attic.png` (337, 424)–(461, 507) at ×10 to see them.

Measured **line-centre to line-centre**, on the third (east) window, whose interior is
clear of the Antresola furniture — profiled at cols 419–423 and 444–448, where the
background is a flat 158 and the box lines read 45–119:

| Edge | Row / col | Neighbours clean? | → mm |
|---|---|---|---|
| top (ridge side) | **row 439.0 ± 0.3** | rows 438, 440 = flat 217 | y = 2446 |
| bottom (eave side) | **row 492.0 ± 0.3** | rows 491, 493 = flat 158 | y = 1149 |
| left (window 2) | **col 381.0** | cols 378–380, 382–383 have zero dark px | x = 7451 |
| right (window 2) | **col 413.0** | cols 411–412, 414–417 have zero dark px | x = 8232 |

**Depth = 492.0 − 439.0 = 53.0 ± 0.5 px.** At `SY = 0.0408889 px/mm` → **1296 ± 12 mm**.

## Why this measurement can be trusted — the width is a built-in calibration

The same box, measured the same way in x: `413.0 − 381.0 = 32.0 px`, at
`SX = 0.0409357 px/mm` → **781.7 mm** against the printed callout **780 mm**. That is
**+1.7 mm, 0.07 px**. So the line-centre-to-line-centre convention reproduces a *printed*
dimension on this very object to a fifteenth of a pixel. The depth reading is the same
convention on the same rectangle.

## The arithmetic on the disagreement

| Quantity | mm | px (y) |
|---|---|---|
| `1600 · cos 35°` | 1310.6 | 53.60 |
| **this task, line centres** | **1296** | **53.0** |
| T05 as recorded in `spec/meta.json` | 1271 | 51.97 |

All three lie within **1.6 px** of one another, and one pixel is **24.46 mm**. T05's value
is exactly one pixel below mine, i.e. half a pixel in at each edge — the signature of
measuring the *inner* faces of the two dashed lines rather than their centres. It is not a
different fact about the drawing; it is a different edge convention on the same two lines.

Implied on-slope height from the re-measured depth: `1296 / cos 35° = 1582 ± 15 mm`
against the printed 1600 — **−18 mm, −1.1 %**. The alternative the record floats,
"the on-slope height is nearer 1552 mm", is 1.6 px away from the ink and is **not
supported**; 1600 is 0.6 px away and is.

## Recommendation

Treat the callout `1600` as correct and the traced projection as `1296 ± 12 mm`.

* `spec/meta.json` lines 134 / 154 / 174 (the identical note on all three roof windows) —
  **rewrite.** "plan-projected extent y = 1162..2433 (depth 1271). DISCREPANCY, recorded
  not smoothed: 1600 * cos35 = 1310.6 mm against the traced 1271 mm, a 39.6 mm / 3.1%
  disagreement" is wrong. The extent is `y = 1149..2446`, depth **1296 ± 12**, and the
  residual against 1310.6 is 14.6 mm / 1.1 % / 0.6 px. Also drop "Either the on-slope
  height is nearer 1552 mm (1271 / cos35) or the traced projection is short" — the traced
  projection *was* short, by one pixel, and that is now measured.
* `README.md` line ~436, Outstanding item 2 — **close it.** The 3.1 % is an artefact.
* `tests/test_generator.py::test_roof_window_plan_depth_matches_its_on_slope_height`
  (lines 637–655) — its docstring states "It holds to **3.1 %**, not exactly". Update to
  1.1 %. The 5 % assertion still passes; the companion assertion that it does **not** hold
  at 1 % is now **marginal** (1.1 % vs a 1 % threshold) and, if the depth is corrected in
  the spec to 1296, that assertion will start failing for the right reason and should be
  deleted rather than re-tuned.
* `src/kotewki/generator.py` line ~185 and `src/kotewki/report.py` line ~866 repeat the
  1271 / 3.1 % figures and need the same correction.

**Confidence: high.** The width of the same rectangle, measured identically, reproduces its
printed callout to 0.07 px, so the method is calibrated in-place. The one thing I cannot
do is beat the raster: 1296 and 1310.6 differ by 0.6 px and I do **not** claim to have
distinguished them — I claim only that they agree, and that 1271 does not belong.

---

# Item 3 — gable windows not centred (README Outstanding #3)

## Verdict

**The spec is right. The windows are genuinely not centred, and the "suspicious
coincidence" is the design.** On both gables the window's near jamb sits **on the ridge
line**, measured to under 1 px, and a centred window is excluded by 28 px — roughly
fourteen times the measurement uncertainty. `offset 4275` is confirmed, on both walls,
including which side of the ridge each window falls.

## What the images are, and why they can still be measured

`elevation_side_1.png` / `elevation_side_2.png` (1280×513) are **photoreal renders, not
dimensioned orthographic elevations** — there is not a single printed dimension on either.
They are nevertheless usable in the gable plane, and here is the evidence that they are:

* The two rake lines of the gable are symmetric: fitted slopes **−1.4370** and **+1.4286**
  col/row (side_1), 0.6 % apart.
* That slope is `atan(1/1.437)` = **34.83°** against the published 35.0° — a sixth
  independent confirmation of the pitch, and the reason a perspective distortion large
  enough to matter can be ruled out.
* Scale from the gable wall's two vertical edges, cols **371** and **854** (both rakes stop
  dead there, rows 315 onward): 483 px for the 9000 mm gable → **0.05367 px/mm = 53.7 px/m
  → 18.6 mm per pixel.**
* That scale is confirmed twice more, without being fitted to either:
  * gable rise apex→eaves = `314 − 144.5 = 169.5 px`; predicted `4500 · tan 35° = 3151 mm
    = 169.1 px` → **0.4 px**;
  * the eaves row 314 back-converts to `z = 6761 − 169.5/0.05367 = 3603 mm` against the
    spec's roof-outer-plane-at-wall-face **3610 mm** → **−7 mm**.

So in the gable plane these renders are orthographic to well inside a pixel, and a claim at
±2 px (±37 mm) is safe.

## The measurement (no scale needed for the verdict)

The ridge apex is obtained by **intersecting the two fitted rake lines**, which requires no
scale and no assumption about where the wall edges are:

| | side_1 | side_2 |
|---|---|---|
| left rake, rows 155–290 | `col = 599 − 1.4370(r−155)` | `col = 637 − 1.4273(r−180)` |
| right rake, rows 190–290 | `col = 679 + 1.4286(r−190)` | `col = 754 + 1.4300(r−190)` |
| **apex** | **col 614.1, row 144.5** | **col 688.3, row 144.1** |
| wall centre from the two vertical edges (371 / 854) | col 612.5 (Δ 1.6 px) | — |
| window bay, left edge | **col 614.5** | **col 689.0** |
| window bay, right edge | col 671.0 | col 746.0 |
| glazing proper (dark run, rows 190–315) | cols 612–672 | — |

**Left jamb minus apex: +0.4 px on side_1, +0.7 px on side_2** — i.e. **7 mm and 13 mm**,
comfortably inside the ±37 mm the render supports. A *centred* window would put the left
jamb at `apex − 28.5 px`. It is at `apex + 0.5 px`. The two hypotheses are **29 px** apart
where the noise is 2 px.

Converted:

* window bay width `671 − 614.5 = 56.5 px` → **1053 mm** against the printed callout
  `100/273` = 1000 mm (+5 %; the render's reveal/frame is inside this run, so the
  agreement is as good as a render allows);
* window **centre** offset from the apex `642.8 − 614.1 = 28.7 px` → **535 mm**, against
  the spec's 500 mm — **1.9 px**.

## The two windows are on opposite sides of the ridge, and the spec already says so

Both renders show the bay to the *right* of the apex, and the two renders are opposite
views (the pergola is on the right in side_1 and on the left in side_2, and the chimney
pattern is mirrored: offsets from apex **−82.1 / +36.9 px** on side_1 against **−40.3 /
+78.2 px** on side_2). Opposite views with the bay on the same screen side means the two
windows are on **opposite sides of the ridge**.

Which is which, from the chimneys. `A_W9`'s stack is at plan `y 4825..5435`, i.e. 630 mm
**north** of the ridge; the ground/attic pair at `y 2860..3480` is 1330 mm **south**.
On side_1 the near stack reads cols 633–669 → `+18.9..+54.9 px` → **y 4852..5522**
(against 4825..5435, +27 / +87 mm), so on side_1 **north is to the right**, and a viewer
with north on the right is looking **west** — side_1 is the **east** gable.

* side_1 = east gable = wall `A_W2`, bay north of the ridge → **y 4500..5509**.
  `A_O2` on `A_W2` (start (16875,225), offset 4275) spans **y 4500..5500**. Agreement 9 mm.
* side_2 = west gable = wall `A_W4`, bay south of the ridge → **y 3479..4500**.
  `A_O1` on `A_W4` (start (225,8775), offset 4275) spans **y 3500..4500**. Agreement 21 mm.

The far stack on side_1 reads cols 513–551 → **y 2617..3325** against the spec's
2860..3480 — 155–243 mm out. That stack is the deepest thing in the frame, so I treat it
as a **sanity check that passes**, not as a measurement, and I would not change the spec on
its evidence.

## Recommendation

**No change. Close Outstanding #3 as confirmed.**

* `README.md` line ~440ish, Outstanding item 3 — the entry says the 500 mm off-centre
  placement "is equally consistent with a tracing error". It is not: the elevations settle
  it, and `offset 4275` is correct on both walls.
* `spec/attic.json` `A_O1` / `A_O2` notes — `offset` is currently in `derived_fields`. It
  is still derived (no printed dimension locates it), but the notes should record that the
  placement is **corroborated on `elevation_side_1.png` and `elevation_side_2.png`**: near
  jamb on the ridge to under 1 px, centred excluded by 29 px, and the north/south sense of
  each window independently confirmed by the chimney positions.
* Worth recording separately: the renders put the *glazing* at `z ≈ 3584..5913` (rows
  190–315), whereas the spec has `A_O1/A_O2` at `3040..5770` (sill 0 on the attic floor,
  height 2730). The bay below the eaves line reads as a concrete spandrel in the render.
  This is a **vertical** discrepancy of roughly 0.5 m and is *not* something I am claiming
  against the spec — the `100/273` callout is the authority and a marketing render is not.
  Flagging it only so the next reader is not surprised by it.

**Confidence: very high** on "not centred, near jamb on the ridge" (29 px against 2 px of
noise, on two independent images, with the frame validated three ways).
**High** on the north/south assignment of each window (chimney cross-check, 9 mm and
21 mm).

---

# Limits — what this task can and cannot support

Recorded so a later reader does not over-read anything above.

1. **One pixel is 24.4 mm on the plans and 18.6 mm on the gable renders.** Every traced
   figure here carries a ± and none of them is better than about half a pixel. Where two
   hypotheses are under 1 px apart I say they agree, not that I chose between them:
   * item 2's `1296` vs `1310.6` (0.6 px) — **agree, not distinguished**;
   * item 1's guard at `4700` vs `4730` vs `4760` (2.4 px across the three) —
     **undetermined from the ink**; the printed 4700 decides it, not me;
   * item 1's L-shape vs the plain rectangle (0.4 % in area) — **not distinguished**.
2. **Nothing here was measured on a shared frame by assumption.** The two plans differ by
   7 px in x and 44 px in y and each was anchored separately; every plan claim that could
   be checked on both bitmaps was, and the two readings are quoted side by side. The x and
   y scales were carried separately throughout (`0.0409357` vs `0.0408889` px/mm); for
   item 2's roof window that distinction is 0.7 mm on the width and does not matter, but
   it was not assumed away.
3. **The elevations have their own scale and it was derived on the image**
   (`0.05367 px/mm` from the 9000 mm gable), then checked against two quantities it was
   not fitted to (the 35° rise, and the 3610 mm springing) before any claim was made on it.
   They remain **renders, not dimensioned drawings**, and I have not used them for any
   claim finer than ±2 px.
4. **`data/source/` is the ceiling.** Both plans and both `*_areas` variants are 853×853,
   and `data/source/raw/*.gif` are the same 853×853 files — there is no higher-resolution
   original to go back to. The section is 400×300. So the numbers above are the best this
   evidence base can produce, and re-running this investigation will not improve them.
5. **Nothing was added to the model to make a number fit.** The one change recommended for
   item 1 is a *convention* change plus moving an edge onto a **printed** dimension. Items
   2 and 3 recommend no geometric change at all.

---

# Consolidated corrections to the existing record

Exact file and line, for the coordinator to apply. Line numbers as of this task.

| # | File : line | What is there now | What is wrong | Action |
|---|---|---|---|---|
| 1 | `README.md` : 327 | table row `Schody (attic) │ 3.412 │ 3.64 │ −6.25% │ **Missing geometry — fixable**` | status is wrong | → `Wrong convention — see docs/T18-findings.md`; figure becomes 3.6575 / +0.5 % at structure |
| 2 | `README.md` : 344–348 | "**Schody is a geometry gap, not a measurement question.**" | inverted | replace with the convention finding |
| 3 | `README.md` : 363 | "its fixed 0.25 m² deficit is something no allowance can supply" | conclusion right, reason wrong | there is no 0.25 m² deficit; Schody simply takes no allowance |
| 4 | `README.md` : 430–435 | Outstanding 1, "**Add the Schody stairwell landing nub** (~0.25 m²)" | **withdraw** — doing it gives +6.8 % | replace with the convention change |
| 5 | `README.md` : 436–440 | Outstanding 2, "disagrees with its callout by 3.1% … traced plan depth of **1271 mm**" | 1271 is one pixel short; the true drawn depth is 1296 ± 12 and the residual is 1.1 % | **close the item** |
| 6 | `README.md` : 441–445 | Outstanding 3, "Worth one look at the elevation to confirm the offset is real." | done: it is real | **close the item**, cite the two gable renders |
| 7 | `README.md` : ~318 block | "Known area-check limitations" | incomplete | add a bullet: Schody is bounded by a slab opening, not plastered masonry, so the finish allowance does not apply to it |
| 8 | `spec/attic.json` : 194 | `A_W8.note`: "continuous vertical line at plan column 298.9 (present on rows 188-342), which is the east side of the stairwell … giving a 950 mm clear stairwell" | **factually false.** No line at col 298.9 on rows 226–346. Flight east boundary is cols 294.6–297.5, centre 296.0 → x 5374. Clear width 872 ± 20, not 950. The col-299 line exists on rows 190–225 and is the landing box's west edge. | rewrite; nub coords become `x 5435..5698, y 7660..8550`, ≈ 0.23 m² |
| 9 | `spec/attic.json` : 407 | `A_R4.note`: "Polygon x 4500..5450, y 4760..8550 = 3.60 m2 against 3.64 (−1.1%)" and the closing nub sentence | south edge should be the printed 4700 | → `x 4500..5450, y 4700..8550 = 3.6575 (+0.5%)`, measured at structure; keep "adding the nub would over-read" but say *why* |
| 10 | `spec/attic.json` : 173 | `A_W7.note`: "the drawn line sits at plan row 346.0 = 4708 mm in this frame" | row 346.0 is right; in the T13 frame it converts to **4720**, not 4708 (the old note used `188 + (8555 − y)·0.04089`, and 8555 should be 8550 — README already flags that) | correct the conversion; note the guard's *thickness* and *side* are undetermined from the ink (single 1-px line) |
| 11 | `spec/meta.json` : 134, 154, 174 | the identical roof-window note, "plan-projected extent y = 1162..2433 (depth 1271). DISCREPANCY … 39.6 mm / 3.1% disagreement" | one-pixel error | → `y = 1149..2446`, depth **1296 ± 12**, residual 14.6 mm / 1.1 % / 0.6 px against `1600·cos35°`; delete the "1552 mm" alternative |
| 12 | `src/kotewki/generator.py` : ~185 | "(1600 * cos 35 = 1310.5 mm against a traced 1271 mm)" | same | update to 1296 |
| 13 | `src/kotewki/report.py` : ~866 | repeats the 1271 / 3.1 % text | same | update |
| 14 | `tests/test_generator.py` : 637–655 | `test_roof_window_plan_depth_matches_its_on_slope_height` — "It holds to **3.1 %**, not exactly" and an assertion that it does *not* hold at 1 % | 1.1 %, not 3.1 % | update the docstring; if the spec depth is corrected to 1296 the "does not hold at 1 %" assertion becomes marginal (1.1 % vs 1 %) and should be **deleted**, not re-tuned |
| 15 | `tests/test_room_areas.py` : 129–136 | `RECORDED_RESIDUALS["A_R4:usable"]` — "the unmodelled nub explains the whole residual and the finish convention is not at fault … a known missing piece of geometry rather than a measurement question" | inverted on both counts | **delete the entry** once the fix lands (the check comes inside ±1 %); the set-equality assertion will force this anyway |
| 16 | `spec/attic.json` `A_O1` / `A_O2` notes | offset 4275 recorded as derived, uncorroborated | now corroborated | add: near jamb on the ridge to <1 px on `elevation_side_1.png` / `elevation_side_2.png`; centred excluded by 29 px; north/south sense of each window confirmed by the chimney positions |

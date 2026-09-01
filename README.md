# Dom w Kotewkach 6 (E) — Verified 3D Reconstruction

Browser-viewable 3D model of the Archon house project
[Dom w Kotewkach 6 (E)](https://www.archon.pl/projekty-domow/projekt-dom-w-kotewkach-6-e-m3fd297061d375),
reconstructed from the published top-down floor plans and verified programmatically
against the project's published dimensional data.

## Core principle

**The model is generated, never hand-authored.**

```
plan images  →  spec.json  →  validators  →  generator  →  model.glb  →  three.js viewer
   (source)     (transcribed)   (pytest)     (Python)     (artifact)      (browser)
```

`spec/` is the single source of truth. `build/model.glb` is a build artifact and is
never edited by hand. If the model is wrong, the spec is wrong — fix the spec and rebuild.
This is what makes the dimensional guarantee possible: there is no path by which the
rendered geometry can disagree with the validated data.

## Why this is verifiable at all

The source floor plans carry **printed dimension chains**. We transcribe numbers; we do
not measure pixels. There is no scale calibration step and no accumulated tracing drift.
The accuracy ceiling is typing accuracy — and typos are precisely what the validators catch.

The published room-area table acts as a **checksum over the transcribed dimensions**. A
typo in any dimension participates in at least one room and will break at least one
assertion.

**How strong is that checksum, measured rather than claimed: 18 area checks run, of which
16 are independent** (plus 5 global invariants). This is lower than the "~19 room
equations" originally asserted here, for two concrete reasons T08 established:

- **Ground floor gives 11, not 14** — Hol (2), Salon, Hol (7) and Kuchnia are one
  open-plan face and collapse to a single equation (14 − 4 + 1).
- **Two attic checks are algebraically dependent** — the two `Strych ocieplony` rooms span
  the full building depth, so both slopes band them by the same constant counted depth
  (3.779 m). Their banded and floor figures are two readings of one unknown. Only
  Antresola stops short of the ridge and so adds genuine information.

The count is pinned in code by `independent_equation_count()` and a test, so it cannot
quietly drift back up.

## Units — read this before transcribing anything

**The plans print CENTIMETRES.** (Confirmed in T03: the ground-floor overall chain reads
`470 · 224 · 1016 = 1710`, i.e. a 17.10 m building.) The spec stores **integer
millimetres**. So there is a ×10 conversion between source and spec.

A missed ×10 produces a house one tenth the size **that still closes every dimension
chain**. Chain closure cannot catch it. Two defences, both mandatory:

1. **`dimension_chains[].segments` are transcribed verbatim in centimetres, exactly as
   printed, and the loader multiplies by 10.** Transcription does no arithmetic at all,
   and the ×10 lives in exactly one place in the codebase.
2. **Magnitude sanity assertions** (T06): overall building dimensions must fall in
   5 000–50 000 mm, storey heights in 2 000–4 000 mm. Fires instantly on a scale error.

Everywhere else in the spec: integer millimetres, no floats. Integers make equality checks
exact rather than epsilon-tolerant. Conversion to metres happens **only** at glTF export.
- glTF is defined in metres (1 unit = 1 m), so there is no unit ambiguity at the browser
  boundary. This eliminates the entire "model is 100× too big" bug class.

### Reading the plan bitmaps — three traps

Established by T13 while registering the overlays. All three are silent failures, not
errors, so they are written down here rather than left to be rediscovered.

1. **The two plans do NOT share a pixel frame.** Anchored on the printed 1710/900 cm
   overalls laid on the outermost exterior-wall ink:

   | | `u0` | `u1` | `v0` | `v1` |
   |---|---|---|---|---|
   | `plan_ground.png` | 69.0 | 769.0 | 583.0 | 215.0 |
   | `plan_attic.png` | 76.0 | 776.0 | 539.0 | 171.0 |

   That is 7 px in x and **44 px in y — 1.08 m**. Reusing the attic frame for the ground
   floor puts the whole storey a metre out. `tests/test_overlay.py` re-derives both from
   the bitmaps at run time so these constants cannot rot.

2. **The plans are slightly anisotropic.** 700 px / 17 100 mm = **40.936 px/m in x**, but
   368 px / 9 000 mm = **40.889 px/m in y** — 0.11% apart. The single `40.89` / `0.04089`
   figure quoted throughout this project is the **y** scale; using it for x is ~0.4 px out
   at the far end. Carry the two separately.

3. **`published_id` is not unique across levels.** `G_R3` (Pokój, ground) and `A_R3`
   (Strych ocieplony, attic) are both `published_id: 3`; so are 1, 2 and 4. That is correct
   — they are per-level plan numbers — but any code keying rooms on the bare number lets
   the attic silently overwrite the ground floor. It already bit T13's orientation anchor
   once, which read the attic's centroid and "failed" on a correct model. Scope room
   lookups by level.

Also: the attic frame constant recorded elsewhere as `8555` is properly **8550**, the inner
face of the north wall (9000 − 450). Five millimetres, inside half-pixel noise, so nothing
computed changes — but it reads as a measured value when it is a structural one.

## Published reference data

Transcribed from the project card. These are the assertion targets — see `data/published.json`.

| Quantity | Value |
|---|---|
| Usable area (pow. użytkowa, excl. stairs) | 163.57 m² |
| Net area (excl. boiler room, attic) | 127.02 m² |
| Attic area (poddasze) | 32.88 m² |
| Boiler room | 7.31 m² |
| Footprint (pow. zabudowy) | 154.42 m² |
| Floor area | 222.64 m² |
| Total area | 307.07 m² |
| Cubature | 849.27 m³ |
| Building height (ground → ridge) | 7.09 m |
| Roof pitch | 35° |
| Roof area | 216.8 m² |
| Minimum plot size | 25.1 × 17.0 m |

**Note:** minimum plot size is *not* the building footprint. Building overall dimensions
are unknown and must be transcribed from the plan dimension chains.

### Ground floor rooms — 116.18 m² total

Wiatrołap 5.35 · Hol 1.81 · Pokój 11.99 · Łazienka 3.48 · Pokój 11.99 ·
Salon z jadalnią 30.57 · Hol 2.30 · Pokój 13.05 · Garderoba 3.96 · Pralnia 4.94 ·
Kotłownia 7.31 · Łazienka 5.85 · Spiżarnia 1.54 · Kuchnia 12.04

### Attic

Antresola 14.51 · **Strych ocieplony** 14.67 · **Strych ocieplony** 18.21 · Schody 3.64

Publisher's verbatim names — downstream name-matching (T10) keys on `Strych ocieplony`.
Note two distinct quantities that are easy to conflate: published *"powierzchnia strychu"*
32.88 m² is exactly the two `Strych ocieplony` rooms (14.67 + 18.21), whereas the **level**
`PODDASZE` totals 51.03 m² including Antresola and Schody.

### Vertical geometry — from `data/source/section.png`

| Level | Elevation | |
|---|---|---|
| Terrain | −0.32 m | printed |
| Ground floor | ±0.00 m | datum |
| Ground-floor ceiling (slab soffit) | +2.70 m | printed clear height 270 |
| **Eave — underside of overhang at fascia** | **+2.88 m** | printed; **0.60 m outboard of the wall** |
| Attic floor (slab top, slab 0.34 m) | +3.04 m | printed |
| Top of knee wall / wall plate | +3.33 m | derived: 3.04 + 0.29 |
| Roof outer plane at wall face | +3.61 m | derived: + 0.28 build-up |
| Ridge | +6.77 m | printed |

Clear heights 2.70 m (ground) and 2.73 m (attic). Knee wall 29 cm. Pitch 35°.
Ridge − terrain = 6.77 − (−0.32) = **7.09 m**, matching the published building height exactly.

> The +2.88 mark is the **eave only** — the fascia underside at the outer edge of the
> overhang. It is not in the wall chain at all, which is why 3.04 − 2.88 = 0.16 never
> reconciled with the 0.34 m slab. Mislabelling it "wall plate top" produced a phantom
> roof discrepancy that cost a full investigation task; see below.

### Reconciliation (worked, confirms definitions)

```
163.57 (usable) − 32.88 (attic) − 7.31 (boiler) = 123.38
123.38 + 3.64 (stairs)                          = 127.02  ✓ net area
116.18 (ground) + 14.51 + 14.67 + 18.21         = 163.57  ✓ usable area
```

So: "usable area" **excludes** stairs, "net area" **includes** them. Establishing this
*before* modelling is what lets the area assertions be written correctly.

## Known unknowns

Most of the original unknowns were closed by T03. What remains:

- **Roof build-up split** — `roof_buildup_vertical` 280 mm and `fascia_depth` 310 mm are
  measured lumped values; the rafter/insulation/covering split is undetermined. Only
  matters for visual detail, not for dimensions.
- **Published roof area 216.8 m²** vs 227.62 m² from measured overhangs. See below.

**Resolved by T08:**

- **Finish allowance = 20 mm per face**, confirmed by a full sweep across all 18 area
  equations (optimum 19 mm, 20 mm indistinguishable). Stays `derived: true` — it is
  solved-for, not published.

**Resolved by T04:**

- **Footprint residual** — the published figure is the *finished* outline at 10 mm
  render/face, giving 154.4224 m². See below.
- **Exterior wall** — structural 450 mm (what walls are built at), finished 460 mm.

**Resolved by T17** (see `docs/roof-resolution.md`):

- Pitch **35.0°** confirmed — the published figure was right all along
- Eaves overhang **600 mm**, verge overhang **590 mm**, both measured, `derived: true`
- Springing = knee wall top; the +2.88 mark identified as the eave, not the wall plate

**Resolved by T03** (now transcribed source fact, not derived):

- Storey heights, plinth offset, ridge, eave height — all printed on `section.png`
- Knee wall 29 cm — printed on the spec sheet
- Measurement norm — Archon states **PN-ISO 9836** explicitly, four times on the page.
  Areas are measured *w świetle ścian*: to finished faces, plaster included.
  Sloped-ceiling bands are **1.4 m / 2.2 m**, corroborated by the `140`/`220` contour
  labels printed on the attic plan.

### The finish allowance

Chains are printed to raw structure; published areas are *w świetle ścian*. The residual
is a uniform ~2–4% area offset, fully explained by a **20 mm per-face** finish allowance:

| Room | Chain | Raw | +20 mm/face | Published |
|---|---|---|---|---|
| Pokój | 380 × 323 cm | 12.27 m² (+2.4%) | 11.994 m² (+0.04%) | 11.99 m² |
| Łazienka | 260 × 140 cm | 3.64 m² (+4.6%) | 3.482 m² (+0.05%) | 3.48 m² |

Two samples, both within 0.05%. Compelling but not proof — T15 confirms across all 14.

## RESOLVED — the roof (was: OPEN DISCREPANCY)

**The published 35° is correct. No published figure was wrong.** The apparent discrepancy
was our own mislabelling of the +2.88 section mark as "wall plate top" when it is the
**eave fascia underside**, 0.60 m outboard of the wall. Full workings in
[`docs/roof-resolution.md`](docs/roof-resolution.md).

35° is confirmed four independent ways: the section's drawn roof line fits a straight line
at **35.003°** (0.06 px RMS over 81 rows — it is a vector render); the attic 140/220
contours give 35.13° ± 0.6°; the banded attic areas give 34.3–34.5°; three clean gable
branches give 34.92/34.95/34.99°. Nothing measures near the 40.7° we had inferred.

The decisive evidence was the attic contour cross-check. Measured d140 = 1.589 m,
d220 = 2.726 m; predicted at 35° with the published 29 cm knee wall, 1.585 m and 2.728 m —
agreement of **4 mm and 2 mm**, under one pixel. 40.7° misses by 0.30 m and 0.51 m and is
excluded on physical grounds, requiring a knee wall of +3 cm from one contour and −14.5 cm
from the other.

The old 0.73 m "gap" decomposes exactly: 0.61 × tan 35° = 0.427 m of overhang drop, plus
0.310 m fascia depth = **0.737 m**.

### Roof reconstruction — nothing forced

The ridge is an **output**. One derived input in the whole chain:

```
springing = attic_floor 3040 + knee_wall 290 + roof_buildup_vertical 280  = 3610 mm
ridge     = 3610 + (9000/2)·tan 35°                                       = 6761 mm   (printed 6770, −9 mm)
height    = 6761 − (−320)                                                 = 7081 mm   (published 7090, −9 mm)
eave      = 3610 − 600·tan 35° − 310                                      = 2880 mm   (printed 2880, 0 mm)
```

Three genuine checks at ~30 mm margin. **`roof_buildup_vertical` (280 mm, at the wall face)
and `fascia_depth` (310 mm, at the overhang edge) are different quantities** — collapsing
them into one number is precisely the conflation that caused the original phantom problem.

### Two different planes — do not confuse them

There are **three** parallel planes here, and using the wrong one silently corrupts a
different check each time:

| Plane | Elevation | Use it for |
|---|---|---|
| Ceiling / knee wall top | **3330** = 3040 + 290 | **Attic area banding** (the 1.4/2.2 contours) |
| Roof outer plane at wall face | **3610** = 3330 + 280 | **Ridge and roof construction** |
| Fascia underside at overhang edge | 2880 | The eave assertion only |

The banding contours are measured to the **ceiling**, which is `roof_buildup_vertical`
lower than the roof's outer plane. Banding from 3610 puts the contours at 1.185 m and
2.328 m — roughly 0.4 m out, and the attic areas then over-read by ~20%.

Confirmed both ways: `(1.4 − 0.29)/tan 35° = 1.585 m` matches T05's traced d140 = 1.589 m,
and T07's kernel run against real data gives attic total 32.93 m² vs published 32.88 m²
using 3330 — against 39.92 m² using 3610.

Resolved values: pitch **35.0°**, eaves overhang **600 mm**, verge overhang **590 mm**,
springing **knee_wall_top**. Overhangs and build-up are `derived: true`.

### Still open: the published roof area

Measured overhangs give **227.62 m²**; Archon publishes **216.8 m²** (+5%), which implies a
0.44 m uniform overhang against 0.60 m measured on two independent images. T17 declined to
resolve this by shrinking the overhang to fit. **T09 treats roof area as a ±6% sanity band,
not a hard assertion**, and documents why. It does not threaten the pitch: 40.7° would
require a 0.197 m overhang.

### RESOLVED: the footprint residual

**T04 settled it: the published 154.42 m² is the *finished* outline, and the render is
10 mm per face — not the 15 mm we had guessed.**

```
(17.100 + 2t)(9.000 + 2t) = 154.42   →   t = 9.95 mm
17.120 × 9.020 = 154.4224 m²          (15 mm → 154.68, 20 mm → 154.95, both too large)
```

This matches Archon's own stated definition verbatim: *powierzchnia terenu zajęta przez
budynek **w stanie wykończonym*** (PN-ISO 9836). So the **finished** exterior wall is
**460 mm** = 250 Porotherm + 200 EPS + 10 render. The **structural centreline thickness
stays 450 mm** and is what walls are built at — the render sits outboard of the
dimensioned outline. `spec/meta.json` updated accordingly.

There *is* also a genuine non-rectangular feature — an **800 mm deep × 2240 mm wide
entrance recess** in the south facade, confirmed because the bottom chain's middle segment
`224` **is** the recess opening and the left chain's `80` **is** its depth; both chains
close on that reading and no other. But it goes the wrong way (152.11 m²) and does not
count against *pow. zabudowy* anyway, since the attic above is a full rectangle built over
it.

## Known area-check limitations

Recorded before T08 runs, so they are not mistaken for geometry bugs:

- **Four ground-floor rooms are one continuous open-plan space** — Hol (2), Salon, Hol (7),
  Kuchnia. The wall network cannot separate them; T07's kernel correctly returns a single
  face of 49.679 m² against their published floor areas summing to 49.35 m² (+0.7%). Their
  published splits are the publisher's *virtual measuring lines* (x 4500, y 3800, x 11500),
  not walls. **T08 must check them as one combined area, never individually.** This reduces
  the ground floor from 14 independent area equations to 11 — the checksum is weaker than
  originally claimed, and that should be stated honestly in the final report.
- **Room 6's 33.2 m² floor vs 30.57 m² usable = 2.63 m² is the ground-floor stair run**, and
  equals the whole level's floor/usable gap (118.81 − 116.18). Deduct it there and nowhere
  else. It is **not** the attic's Schody 3.64 m².
- **Two rooms are L-shaped** in ways their printed dimension pairs do not reveal: Pokój (8)
  and Łazienka (12). Polygonisation handles them (13.064 vs 13.05, +0.1%).
- **One room does not take the finish allowance at all: Schody (`A_R4`).** It is the only
  room whose boundary is a slab opening rather than plastered masonry — two of its four
  edges are a void edge and a guard, with no plaster to deduct. Under PN-ISO 9836 a stair is
  counted as the plan projection of flight and landings. This is a genuine one-room
  carve-out, recorded in the spec as data rather than as a branch in the kernel.
### Measured results (T08, at the published convention)

**17 of 18 checks inside ±1%.** Mean −0.06%, RMS 0.56%. One outlier, not overridden:

| Room | Computed | Published | Rel | Status |
|---|---|---|---|---|
| Kotłownia | 7.183 | 7.31 | −1.74% | Irreducible under any single allowance |

Two rooms that were on this list have come off it, and **neither was a measurement problem
that more arithmetic would have found**:

- **Schody**, −6.25% → **+0.48%**. Never missing geometry; a finish-convention error.
  T18 diagnosed it from the bitmaps, T19 applied it. See below.
- **Łazienka (12)**, −1.27% → **+0.18%**. `G_W16` ran 610 mm too far north, pushing a
  120 × 550 mm stub of wall into the shower and eating 0.066 m² of the room. This project
  had written the residual off as an *"irreducible publisher-vs-plan disagreement"*. It was
  a transcription error, and the note on the wall asserted ink ("unbroken rows 462..564")
  that a column scan shows is flat white until row 486.

**The Łazienka error was found by a human looking at `build/overlay_ground.png`, and could
not have been found any other way in this suite.** A wall in the wrong place still closes
every dimension chain, still leaves the union of rooms and walls equal to the footprint,
and still satisfies every global invariant — it just moves area from one room to a wall.
The overlay showed it as a blue (model-only) rectangle with nothing beneath it. This is the
justification for Test 6 existing at all, and it earned its place on first use.

Mean and RMS improved from −0.51% and 1.60% across the two fixes.

**Wiatrołap passes** at +0.96% — it was listed here as a >1% residual and that was wrong.

**Schody is a measurement-convention error, not a geometry gap.** T18 settled this from
the bitmaps; the previous text here claimed the opposite and was wrong, as was the
coordinator's competing guess that the disputed "nub" was a balustrade post or furniture.

What the drawing actually shows: the nub **is floor** — it is drawn in the plan's floor
tone (grey 236) rather than its void tone (197), and the stair's walking line
(*linia biegu*) originates inside it, running to a centred arrowhead down the flight. It is
the part of the bottom landing lying east of the flight.

But adding it to the current polygon is still wrong, and badly so: **+6.8%**. The nub is
only admissible together with narrowing the flight from 950 mm to the drawn **872 ± 20 mm**
— and those two corrections are +0.23 m² and −0.22 m², so they cancel. That cancellation is
the only reason the wrong rectangle scored −1.1%. Two compensating errors.

The real fault is the finish allowance. The L actually drawn measures **3.645 m² at
structure (+0.1%)**, and its 10.084 m perimeter means staying inside ±1% requires
≤ 4.1 mm/face. **No allowance between 5 and 30 mm rescues this room; only dropping it
does.** That is principled rather than convenient: under PN-ISO 9836 a stair is counted as
the plan projection of flight and landings — the slab opening — and two of Schody's four
edges are a void edge and a guard, with no plaster to deduct. `A_SO2`'s note already
recorded that the opening equals Schody's raw structure-convention area; that is the same
fact seen from the other side.

Applied in T19: `A_R4` becomes `x 4500..5450, y 4700..8550` (south edge at the **printed**
4700) measured at structure → **3.6575 m², +0.5%**. The measured L and this rectangle differ
by 0.4%, which is inside the ±1.4% the raster supports, so the choice between them is a
faithfulness decision rather than a measurable one.

Kotłownia and Łazienka (12) are not explained by any allowance in 0–30 mm. They look like
genuine publisher-vs-plan disagreements of ~0.07–0.13 m².

### The finish allowance — CONFIRMED at 20 mm

T08 swept 0–30 mm across all 18 equations. The curve is convex with a single minimum at
**19 mm**; 20 mm is indistinguishable (RMS 0.64% vs 0.63%, excluding Schody). T03's 20 mm,
originally inferred from just two rooms, holds across the whole set.

`finish` beats `structure` on every metric: RMS 1.60% vs 3.00%, mean −0.51% vs +2.37%,
3 failures vs 15. At `structure` the classifier returns `uniform_offset` — exactly the
predicted pre-allowance signature.

Schody must be excluded from the objective — but not for the reason first given here. There
is no 0.25 m² deficit; the room simply does not take the allowance at all (see above). The
conclusion stands, the reasoning behind it was wrong. Leaving it in drags the apparent
optimum down to 16 mm.

### Global invariants (T09) — all five pass

Five scalars that constrain the model from directions the room areas cannot reach.

| Invariant | Computed | Published | Residual | Tolerance |
|---|---|---|---|---|
| Usable area | 163.016 m² | 163.57 m² | −0.34% | ±1% |
| Footprint | 154.422 m² | 154.42 m² | **+0.002%** | ±1% |
| Cubature | 849.63 m³ | 849.27 m³ | +0.04% | ±1.5% |
| Roof area | 227.62 m² | 216.8 m² | +4.99% | ±6%, see below |
| Building height | 7.0809 m | 7.09 m | −9 mm | ±30 mm |

Two of these are only meaningful because **the ridge is an output**: it is computed as
`3040 + 290 + 280 + 4500·tan 35° = 6760.93 mm` and never assigned. That wire is now cut
in four places — three in T11 (including a rebuild with `section_elevations.ridge` set to
99999) and `test_no_invariant_moves_when_the_printed_ridge_is_corrupted` here, which
asserts the same guarantee from the invariants' side.

**The usable-area check is weaker than it looks, and says so.** It deducts a 2.63 m²
ground-floor stair run that nothing in `spec/ground.json` models. That figure is read two
independent ways off the published table — `Salon 33.2 − 30.57` and `ground 118.81 −
116.18` — but it is still published information handed to the check, so the invariant
independently constrains 160.94 m² of the 163.57, not all of it.

**Cubature is computed from the 2D kernel, not from mesh volumes.** The generator's solids
interpenetrate by a measured **21.0 m³** against a 267.3 m³ sum of member volumes, so
`sum(mesh.volume)` would double-count by ~8% — and is material volume anyway, not the
gross enclosed volume cubature asks for.

The ±6% roof band still has teeth: the refuted 40.7° pitch reads +13.4% and fails it.

## Construction (from spec sheet)

- Exterior wall: Porotherm 25 cm + Termo Organika EPS 20 cm + plaster ≈ **465–480 mm**
- Ceiling: reinforced concrete slab
- Roof: two-slope timber structure, 35°

## Layout

```
data/source/     plan images + scraped published figures (read-only)
data/published.json   authoritative assertion targets
spec/            THE source of truth (hand-transcribed), split by owner:
  schema.json      JSON Schema for the merged spec
  meta.json        levels, roof, construction
  ground.json      ground floor walls/openings/rooms/chains
  attic.json       attic walls/openings/rooms/chains
src/kotewki/     geometry kernel, generator, exporter
tests/           the validation suite
tests/golden/    golden overlay images
build/           generated artifacts (gitignored except goldens)
viewer/          three.js browser viewer
tasks/           task definitions, each offloadable to a subagent
```

## Getting started

See `TASKS.md` for the work breakdown and dependency graph, and `TESTS.md` for the
validation suite and where each check is implemented.

## Outstanding

Tracked work, in priority order.

1. **Schody — RESOLVED by T18, being applied in T19.** The action originally recorded here
   ("add the ~0.25 m² landing nub") is **withdrawn**: performing it overshoots to +6.8%.
   The residual is a finish-convention error, not missing geometry. `A_R4` moves its south
   edge to the printed `y = 4700` and is measured at structure → 3.6575 m², +0.5%.
   When it lands, `tests/test_room_areas.py` will fail until its `RECORDED_RESIDUALS` entry
   for `A_R4` is **deleted** — the set-equality assertion is deliberate so the record cannot
   rot, and a residual coming back inside tolerance must be removed from it.

2. **Roof-window plan depth — CLOSED by T18. There was no discrepancy.** The recorded
   1271 mm was measured to the *inner* faces of two dashed lines instead of their centres:
   exactly one pixel low. Line-centre to line-centre the depth is **1296 ± 12 mm** against
   `1600 × cos 35° = 1310.6`, a residual of 0.6 px / 1.1%, below what a 24.4 mm/pixel raster
   can resolve. The method was calibrated in place — the same box measured the same way in x
   gives 781.7 mm against a printed 780 callout, 0.07 px. The "on-slope height is nearer
   1552 mm" alternative is 1.6 px from the ink and is not supported. Corrected in T19.

3. **Gable windows — CLOSED by T18. The spec was right and the doubt recorded here was
   wrong.** They are genuinely not centred: on both gables the window's near jamb sits on
   the ridge line to under 1 px, and a centred window is excluded by 28 px — about fourteen
   times the measurement uncertainty, on two independent renders. `offset 4275` is confirmed
   on both walls, including which side of the ridge each window falls (cross-checked against
   the chimney positions to 9 and 21 mm). It stays `derived` — no printed dimension locates
   it — but it is now corroborated rather than merely plausible.

   Noted without action: the renders put the glazing at `z ≈ 3584..5913` where the spec has
   3040..5770, a ~0.5 m difference, with the bay below the eaves reading as a concrete
   spandrel. The printed `100/273` callout is the authority; a marketing render is not.

4. **Mesh solids overlap — now measured exhaustively: 21.0028 m³ across 150 pairs**, against
   267.322 m³ of member volume, i.e. **7.9% double-counted**. Two classes: chimney stacks
   traced once per storey pass through the walls they abut (largest single pair, 1.79 m³),
   and ground exterior walls run up into the attic slab band — which supplies the 2nd, 3rd,
   5th, 6th and 7th largest overlaps, the biggest being
   `ground/walls/G_W7/porotherm_25 × attic/slabs/floor` at 1.4535 m³.
   All 106 components are individually watertight and positive-volume; the scene is not a
   solid. **Not invisible, though — this entry said "invisible in glTF" and T14 disproved
   it.** The `A_SO1` void's north reveal at y = 8550 is exactly the plane of the north wall's
   inner Porotherm face, in the z-band 2.70–3.04 where ground walls run up into the slab: a
   single ray returns `ground/walls/G_W7/porotherm_25` and `attic/slabs/floor` at the *same*
   distance, 3.5479 m, and they z-fight visibly in the walkthrough. The viewer mitigates it
   with `polygonOffset` (a rasteriser tie-break; no vertex moves). It is invisible to the
   *dimensional checks*, not to the eye. Both T09 and T12 compute cubature as a closed-form
   integral over the 2D footprint rather than from mesh volumes, so nothing double-counts
   today — but summing mesh volumes for cubature would be a trap. Totals pinned in
   `tests/test_export.py`
   (`RECORDED_OVERLAP_M3`, `RECORDED_TOP_OVERLAPS`); `test_invariants.RECORDED_MESH_OVERLAPS`
   holds only the chimney subset (3.664 m³, 17%) and now says so.
5. **Published roof area 216.8 m² vs 227.62 m² measured** (+5%). Asserted as a ±6% band with
   the reasoning documented, not silently widened.
6. **Kotłownia** (−1.74%) — the last remaining area outlier, and the only one left that
   looks like a genuine publisher-vs-plan disagreement. Reproducing 7.31 needs ~9 mm per
   face, or a width of 311 cm; 311 is the Pralnia's bay one storey north, and adopting it
   here breaks the lower x chain `G_C3`. **Treat the "irreducible" label with suspicion**
   — Łazienka (12) carried it too, and turned out to be a 610 mm wall-length error that a
   human found in the overlay.

## Schema extensions made during the build

Both came from a validator failing on real data, and both fixed the *data model* rather
than the test. The rule applied each time: if the building genuinely has a feature, the
spec must be able to describe it — loosening the assertion, or hardcoding the geometry in
the generator, would both break the guarantee that the model cannot disagree with the
validated data.

### `slab_openings` — regions where the floor slab is absent

T10's union-equals-footprint check left an unexplained **22.5505 m²** residual on the
attic. It traced the residual polygon to `x 5550–11500, y 4760–8550` — the *Pustka nad
salonem*, the double-height void over the living room. The schema had no way to say "there
is no floor here".

| Id | Kind | Bounds (mm) | Area |
|---|---|---|---|
| `A_SO1` | `void` | 5550, 4760 → 11500, 8550 | 22.5505 m² |
| `A_SO2` | `stairwell` | 4500, 4760 → 5450, 8550 | 3.6005 m² |

Independently corroborated: A_SO1 reproduces T10's residual exactly, A_SO2's 3.6005 m²
matches Schody's raw structure-convention area measured separately by T08, and the 100 mm
gap between them is A_W8, the stair balustrade. `connects_levels` on the stairwell lets the
connectivity check traverse storeys. Attic residual is now **0.000000 m²**, matching ground.

**Why this mattered beyond a red test:** slabbing the void would have given the Salon a
ceiling at attic floor level and destroyed the double-height space — and *no dimensional
check would have caught it*, because every room area on both levels stays exactly correct.
Same silent-failure class as a mirrored plan.

### `A_O5` — the stair-arrival passage

With the void modelled, connectivity still failed: Antresola and both `Strych ocieplony`
rooms were unreachable. A_W7, the 60 mm balustrade, had been transcribed running the full
width `x 4440–11560`, sealing the top of the stairs.

Confirmed against `plan_attic.png`: balustrade **posts** are drawn along the y = 4730 line,
and the westernmost sits at x ≈ 5450 — exactly the stairwell's east edge. The balustrade
starts there and does not cross the stair arrival.

Modelled as a **passage opening**, not by shortening A_W7. Shortening would merge Antresola
and Schody into one polygonised face and destroy both their room areas; an opening restores
circulation while keeping the faces separate. Room areas verified unchanged afterwards.

### `roof_openings` — openings in a roof plane

Three 78/160 cm roof windows sit in the south slope over the Antresola. `openings` cannot
express them: every opening must name a host wall, and a roof plane is not a wall.

| Id | Slope | Plan bounds (mm) |
|---|---|---|
| `R_RW1` | south | 6574, 1162 → 7354, 2433 |
| `R_RW2` | south | 7465, 1162 → 8245, 2433 |
| `R_RW3` | south | 8343, 1162 → 9123, 2433 |

**An axis-aligned plan rectangle is sufficient — no plane or slope vector needed.** A roof
window's jambs are cut square through the rafters and its head and sill are horizontal, so
its plan projection *is* a rectangle, and the plane is fully determined by `pitch_deg` +
`ridge_axis` + the derived ridge. `slope` only names which side of the ridge it sits on,
and is *checked* against the bounds rather than trusted. A window straddling the ridge, or
a hipped/shed roof, would genuinely need more — the generator refuses that case loudly
rather than cutting through the apex.

Entity ids gained an `R_` prefix for roof-scoped entities, alongside `G_` and `A_` for the
two levels. The per-file prefix check still applies to level files only.

## Consuming `build/model.glb` — four traps

Established by T14 while building the viewer. Each is a silent failure: no error is raised,
you just get nothing, or something wrong that looks plausible.

1. **`object.name` is NOT the node name in three.js.** GLTFLoader runs names through
   `PropertyBinding.sanitizeNodeName`, which strips `/` — so `ground/walls/G_W1/porotherm_25`
   arrives as `groundwallsG_W1porotherm_25`. Any consumer that prefix-matches on
   `object.name` gets **zero matches and no error**. The original survives in
   `userData.name` and in the generator's `extras.node`; use those.

2. **The GLB carries no `NORMAL` attribute** — POSITION and indices only. GLTFLoader
   compensates by flipping *its own* materials to `flatShading`, so a viewer that replaces
   materials must set `flatShading: true` or **the model renders black**. It also makes
   `DirectionalLight.shadow.normalBias` inert.

3. **All 24 opening nodes are `level/openings/{windows,doors,other}/id`**, not just
   `A_O2` — an earlier note here singled that one out and understated the class. `roof/`
   likewise has both a 2-field node (`roof/slab`) and 3-field ones (`roof/windows/R_RWn`).
   **Prefix-match; never index `name.split("/")[3]` expecting a layer.**

4. **The room-plate `extras` and `quantities.json` use different area conventions.** Plate
   extras carry `computed_area_m2` at the **structure** convention (`G_R3`: 12.274 m²),
   while `quantities.json`'s room rows are at the **finish** convention (11.994 vs published
   11.99). Both are correct under the 20 mm allowance, but sitting `computed_area_m2` beside
   `published_area_m2` invites a direct comparison that reads +2.4% and looks like a bug.
   For any published comparison, use `quantities.json`.

Two more properties of the model, not of the format: the scene is **Z-up and deliberately
not rotated**, so every on-screen coordinate matches `spec/`, `quantities.json` and the
plans — which rules out three.js's `PointerLockControls`, as it composes a `YXZ` Euler and
assumes Y-up. And **there are no stairs to climb**: nothing in `spec/ground.json` models the
ground-floor flight, whose 2.63 m² is arithmetic rather than geometry, so the walkthrough
switches storeys explicitly instead of using gravity.

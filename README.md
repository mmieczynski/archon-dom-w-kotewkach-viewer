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

The published room-area table acts as a **checksum over the transcribed dimensions**:
14 ground-floor areas plus attic figures plus 5 global invariants give ~19 independent
scalar equations constraining the dimension set. A typo in any dimension participates in
at least one room and will break at least one assertion.

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

| Level | Elevation |
|---|---|
| Terrain | −0.32 m |
| Ground floor | ±0.00 m |
| Eave / wall plate top | +2.88 m |
| Attic floor | +3.04 m |
| Ridge | +6.77 m |

Clear heights 2.70 m (ground) and 2.73 m (attic). Knee wall 29 cm. Pitch 35°.
Ridge − terrain = 6.77 − (−0.32) = **7.09 m**, matching the published building height exactly.

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

- **Eave overhang** — still derived. Solve against the 216.8 m² roof area at 35°.
- **Finish allowance ≈ 20 mm per face** — strongly indicated, not yet confirmed across all
  rooms. See below. Mark `derived: true` until T15 confirms it on all 14 rooms.

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

## OPEN DISCREPANCY — roof geometry does not close

Three published facts are mutually incompatible under the simplest roof model. Found
before any geometry was written, which is the framework working as intended — but it must
be resolved before T11 builds the roof.

Given footprint **17.10 × 9.03 m** (= 154.4 m², matching published 154.42 exactly, so the
footprint is a confirmed rectangle), a symmetric gable at the published **35°** springing
from the section's wall plate at **+2.88** puts the ridge at:

```
4.515 m half-span × tan(35°) = 3.16 m rise  →  ridge +6.04 m
```

The section prints the ridge at **+6.77 m**. A 0.73 m gap. Alternative springing points do
not close it either: attic floor +3.04 → +6.20; attic floor + 29 cm knee wall → +6.49.

Inverting the problem, two independent figures agree with *each other* but not with the
footprint width:

| Derived from | Implied roof span |
|---|---|
| Rise 3.89 m at 35° | 11.11 m |
| Roof area 216.8 m² at 35°, ridge 17.10 m | 10.39 m |
| **Footprint** | **9.03 m** |

Candidate explanations, none yet verified:

1. Pitch at the section is not 35° — the implied value is **40.7°**. The 35° may apply to
   a different roof plane, or be a rounded marketing figure.
2. The roof is not a simple symmetric gable — knee walls, a changed slope, or dormers.
3. `+6.77` is not the ridge, or the two figures use different datums.
4. Eave overhangs are large (~0.4 m all round reconciles the *area* but not the *rise*).

**Resolution path:** re-read `data/source/section.png` and the gable elevation directly,
measuring the roof triangle. Do not resolve it by forcing the ridge to +6.77 — see T09 and
T11 on why that destroys the check.

Note that 7.09 m building height (= 6.77 − (−0.32)) matches the published figure exactly,
so the *vertical* chain is internally consistent. The conflict is between that chain and
the pitch/footprint pair.

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

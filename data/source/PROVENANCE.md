# Source provenance — T03

Everything in this directory is **read-only evidence**. Never edit, never regenerate,
never "clean up" an image. If something here is wrong, re-download it and update this file.

## Project

| | |
|---|---|
| Project | **Dom w kotewkach 6 (E)** |
| Publisher | ARCHON+ Biuro Projektów (archon.pl) |
| Product ID | `m3fd297061d375` |
| Source page | <https://www.archon.pl/projekty-domow/projekt-dom-w-kotewkach-6-e-m3fd297061d375> |
| Retrieved | **2026-08-30** |
| Method | `curl` against the public page + `assets.archon.pl` CDN; no login, no paywall |

## Variant confirmation — (E)

The `(E)` variant is confirmed on every artifact independently:

1. **URL / product ID.** The page slug is `projekt-dom-w-kotewkach-6-e-m3fd297061d375`
   and every asset path is under `/images/products/m3fd297061d375/`.
2. **Filenames.** Every downloaded file's own name contains `dom-w-kotewkach-6-e`
   (see the raw filenames in `raw/`), e.g.
   `rzut-parteru-z-powierzchniami-projekt-dom-w-kotewkach-6-e-...__11915.gif`.
3. **`alt` text.** Each `<img>` carries
   `alt="gotowy projekt Dom w kotewkach 6 (E) rzut parteru"` (and the corresponding
   `rzut poddasza`, `przekroj budynku`, `elewacja frontowa`, `sytuacja`, …).
4. **Data table heading.** The spec table on the page is titled
   *"Dane projektu Domu w kotewkach 6 (E)"*.
5. **Interactive-plan XML.** `/rzut/m3fd297061d375/1` returns
   `<rzut name="Projekt Dom w kotewkach 6 (E) - PARTER" …>`.

### Mirroring — IMPORTANT

The page has a **"Pokaż lustrzane odbicie"** ("show mirror image") toggle, and the plan
`<img>` elements carry the CSS class `has-mirror`. The mirroring is applied **client-side
by CSS at display time** — it is not a different image file.

**The files stored here are the un-mirrored, as-served bitmaps**, i.e. the canonical (E)
orientation as published. No flip of any kind was applied during download or conversion.
Do not mirror them. T13's overlay check is the guard against this class of error; it must
be run against these files exactly as stored.

Orientation sanity anchor for T13: on `plan_ground.png` the **entrance (Wiatrołap, room 1)
is at the bottom-centre-left**, the **Kotłownia (room 11) is at the bottom-right**, and the
two front bedrooms (rooms 3 and 5) are on the **left** edge. `site.png` shows the same
handedness from above with an entry arrow at the bottom edge (it carries no north arrow —
the page renders orientation separately).

## Files

Images are stored twice: byte-exact originals in `raw/` (evidence), and lossless PNG
conversions at the top level under the names the task brief specifies (working copies).
The conversions are pure format changes — no resampling, no cropping, no rotation.

| Working file | Px | Source URL (prefix `https://assets.archon.pl/images/products/m3fd297061d375/`) |
|---|---|---|
| `plan_ground.png` | 853×853 | `projekt-dom-w-kotewkach-6-e-cd739df3d5c31a8ee8da4cae60baf098__11815.gif` |
| `plan_attic.png` | 853×853 | `projekt-dom-w-kotewkach-6-e-65279fd95dcfe3c0975387e3449b06b4__11817.gif` |
| `plan_ground_areas.png` | 853×853 | `rzut-parteru-z-powierzchniami-projekt-dom-w-kotewkach-6-e-081616c0dc7368e1c7e0b03c10c3614a__11915.gif` |
| `plan_attic_areas.png` | 853×853 | `rzut-poddasza-z-powierzchniami-projekt-dom-w-kotewkach-6-e-92cae35da46475eaa5237ab62b117215__11917.gif` |
| `section.png` | 400×300 | `przekroj-budynku-projekt-dom-w-kotewkach-6-e-11af766e8cb6b748a7941774ba93f0c7__256.jpg` |
| `site.png` | 734×854 | `projekt-dom-w-kotewkach-6-e-0fce2a95e6c9f2ae78c9f6f0a8792fa8__11255.jpg` |
| `elevation_front.png` | 1280×513 | `projekt-dom-w-kotewkach-6-e-19e4308dbcd36311247caf8545a01579__11264.jpg` |
| `elevation_side_1.png` | 1280×513 | `projekt-dom-w-kotewkach-6-e-3f3d288dbe29fc6ad2b83444cbf4a455__11265.jpg` |
| `elevation_side_2.png` | 1280×513 | `projekt-dom-w-kotewkach-6-e-3540cfad69bcadefc96581ba1dc8faeb__11266.jpg` |
| `elevation_garden.png` | 1280×513 | `projekt-dom-w-kotewkach-6-e-0da0c01865288e3b0c79f053fc4c302b__11267.jpg` |

Two XML files are also captured — the site's own machine-readable room table, used to
cross-check the transcription in `data/published.json`:

| File | Source URL |
|---|---|
| `published_rooms_ground.xml` | `https://www.archon.pl/rzut/m3fd297061d375/1` |
| `published_rooms_attic.xml` | `https://www.archon.pl/rzut/m3fd297061d375/3` |

**`plan_ground.png` / `plan_attic.png` carry the printed dimension chains.**
**`*_areas.png` carry the room names and areas instead** (same drawing, different
annotation layer). Both are needed: the dimension version for T06, the area version for
mapping room numbers → names.

## Resolution — is this the maximum available?

Yes, as far as the public site exposes.

- The page thumbnail and the fancybox/lightbox variant for the floor plans resolve to the
  **same** 853×853 GIF. `/product_fancybox_floor/m3fd297061d375/1` was fetched directly and
  returns the identical `__11815` / `__11915` URLs — there is no larger plan asset.
- Elevations and the site plan **do** have larger lightbox variants (`__112xx` ≈ 1280 px vs
  `__2xx` ≈ 550 px thumbnails); the larger ones are what is stored here.
- The **section has no lightbox variant**. The page's `onclick` handler targets
  `#przekrojMINI`, but no element with that id exists in the document, so the zoom is dead.
  400×300 is all that is published. It is still legible (see below).

## Legibility — GO

Verdict: **GO**. Digits on the plans are individually unambiguous at native resolution.

Verbatim values read off `plan_ground.png` (all printed in **centimetres**):

- Overall bottom chain: `470` · `224` · `1016`, with overall `1710` below it.
  Closure holds exactly: 470 + 224 + 1016 = **1710**.
- Overall left chain: `820` · `80`, with overall `900`. Closure: 820 + 80 = **900**.
- Interior dimensions: `380`, `323`, `260`, `140`, `230`, `243`, `700`, `475`, `365`,
  `335`, `91`, `181`, `183`, `283`, `306`, `242`, `171`, `241`, `311`, `320`, `373`.
- Opening tags (width/height): `180/230`, `420/230`, `220/140`, `90/140`, `160/210`,
  `90/230`.

From `plan_attic.png`: `393`, `700`, `503`, `810`, `425`; ceiling-height contour labels
`140` and `220` on both sides; roof-window tag `100/273`; text *"Pustka nad salonem"*.

From `section.png` (upscaled 3× for reading, but the glyphs are clean at native size):
level marks `-0,32`, `±0,00`, `+2,88`, `+3,04`, `+6,77`; clear heights `270` and `273`;
pitch label `35°`.

**Units note:** the plans print centimetres, not millimetres. Transcription into the spec
(integer mm, per README) is therefore a ×10 conversion. This is a silent-error risk — a
missed ×10 gives a house one tenth the size that still closes every chain.

## Independent cross-checks passed at acquisition time

These were not forced; they fell out of separately-transcribed sources and all agree:

1. `470 + 224 + 1016 = 1710` — bottom dimension chain closes.
2. `820 + 80 = 900` — left dimension chain closes.
3. Overall footprint from the plan, 17.10 m × 9.00 m, reproduces the published
   **minimum plot 25.1 × 17.0 m** with the statutory 4 m setback on each side:
   17.10 + 8 = 25.1 and 9.00 + 8 = 17.0. Exact — and `site.png` prints this chain
   explicitly: `400 · 1710 · 400 = 2510` and `400 · 900 · 400 = 1700`, a third
   independent source for the 1710 × 900 overall.
4. Section ridge minus terrain: `6.77 − (−0.32) = 7.09 m` = published building height.
5. Room-number → room-name mapping on the plans matches the site's own XML room table
   for both levels.

## Measurement norm — RESOLVED (relevant to T15)

The source page **explicitly states the norm**. It is embedded in the page's definition
tooltips as `application/json` payloads (`#powierzchnia-uzytkowa-bez-schodow_json`,
`#powierzchnia-zabudowy_json`, `#powierzchnia-calkowita_json`, `#kubatura_json`):

> „Powierzchnia użytkowa wg obowiązującej normy **PN-ISO 9836**, to powierzchnia
> wszystkich pomieszczeń budynku liczona **w świetle ścian**. Zgodnie z obowiązującym
> stanem prawnym w pomieszczeniach na poddaszu (tj. ze skosami) powierzchnia użytkowa:
> – powyżej 2,2 m jest liczona w 100 %, – pomiędzy 1,4 m a 2,2 m w 50 %,
> – poniżej 1,4 m nie jest brana pod uwagę."

> „Powierzchnia terenu, zajęta przez budynek w stanie wykończonym, bez tarasów, schodów
> zewnętrznych i podjazdów (**PN-ISO 9836**)"

> „Objętość budynku, liczona zgodnie z normą **PN-ISO-9836**, czyli wraz z przegrodami
> zewnętrznymi i wewnętrznymi (ścianami, stropem, dachem i podmurówką)."

> „Wysokość mierzona od poziomu terenu przy wejściu do budynku do kalenicy."

`PN-70/B-02365` is **not** mentioned anywhere on the page.

Consequences (for T15 to confirm and adopt):

- The norm is **PN-ISO 9836**, not PN-70/B-02365.
- Areas are measured **w świetle ścian** — clear between finished wall faces, i.e.
  including plaster/finish thickness, not to raw structure.
- The attic sloped-ceiling thresholds are **1.4 m / 2.2 m**, *not* the 1.9 m / 2.2 m
  stated in `TESTS.md` §2. `TESTS.md` needs correcting. The plans corroborate this
  directly: `plan_attic.png` prints its ceiling-height contour lines labelled **140** and
  **220**, matching PN-ISO 9836 exactly.
- The parenthesised second figure on the plans (e.g. `30,57 (33,2) m²`) is
  *powierzchnia podłóg* — raw floor area ignoring ceiling height. It is a second,
  independent constraint per room and is captured in `data/published.json`.

`data/published.json` still leaves `measurement_norm: null` per the T03 brief — T15 owns
that field. The evidence is parked under `measurement_norm_evidence`.

## Additional figures captured from the spec sheet

Not in `README.md`, but published and load-bearing for the "known unknowns":

- **Ścianka kolankowa (knee wall): 29 cm.** Constrains attic geometry directly.
- **Minimalne wymiary po adaptacji:** 23.1 × 17.0 m.
- Strop: płyta żelbetowa. Wentylacja: mechaniczna z odzyskiem ciepła.
- Roof covering: blacha dachówkowa / dachówka ceramiczna.
- Section levels: terrain −0.32, ground floor ±0.00, eave wall top +2.88,
  attic floor +3.04, ridge +6.77; clear heights 270 (ground) and 273 (attic).

## Naming discrepancy vs README.md

`README.md` lists the two large attic rooms as *"Poddasze 14.67 · Poddasze 18.21"*.
The publisher's actual label for both is **"Strych ocieplony"** (insulated loft), and the
published figure *"Powierzchnia strychu 32.88 m²"* is precisely the sum of those two rooms
(14.67 + 18.21 = 32.88). `data/published.json` uses the publisher's verbatim names.
Downstream tasks that key on room names should use `Strych ocieplony`.

Note also that "PODDASZE" as a *level* totals 51.03 m² (including Schody 3.64), which is a
different quantity from "Powierzchnia strychu" 32.88 m². Both are recorded.

## Checksums (SHA-256)

```
50b10a9e06a2998ab2e8c557da56e67ebbbf50f948f7e596e3b73e64ebbc5adb  elevation_front.png
889e6fb467ae0cb8a14f66677cef20e65216697e454e09e186431cba424b8e0f  elevation_garden.png
d5a6b82576d6c71e476566070946431b323d45aa35189bcc9ac60cdcc269ae06  elevation_side_1.png
d41c95281142fa03056f823f9508e0e466661620b12ff8fe1c540943c778a4a7  elevation_side_2.png
b7ffec1e8da994794e580c237b3b919c7c617b763fac61e13272ae15c2706060  plan_attic_areas.png
7ec5479abcd43c588066f27cc639d3566427d0e392095c781f75b193d1987dc1  plan_attic.png
eebeaf7b53b5fcd30be2401a7a42ddfff64dfb83b023803890059cfea857ebe3  plan_ground_areas.png
099cd632c488465acc318ed0d228222369016e3ca3716610f3595e04aa2550dc  plan_ground.png
afcf0cfd0d50af89ddc86ec8e4ee745f87b4bae7bc62f22c762af41facc21d01  section.png
02a6250ea3f3a421bc173d4dc1da78184d59ceae8ed67de5862af10de16412f1  site.png
416baf81c49d9122a107de314b2f5980e0a1e474b5ba3c8decbf7df126a72034  published_rooms_attic.xml
957a083a214473082ad50c33bbcd905b3da0556455317f0147fe46226e337544  published_rooms_ground.xml
59431323270ff44b9c9d4d355d92335466ac12b1595c5db088d3032ea8074feb  raw/projekt-dom-w-kotewkach-6-e-0da0c01865288e3b0c79f053fc4c302b__11267.jpg
1efc0324c07501ae46a9b263173408cf1707aab5192fda6dbade09e691038655  raw/projekt-dom-w-kotewkach-6-e-0fce2a95e6c9f2ae78c9f6f0a8792fa8__11255.jpg
a8905f29a5ed3a58b12cf34ec023df1137bf1974ac467e96097fa9c3e72816a5  raw/projekt-dom-w-kotewkach-6-e-19e4308dbcd36311247caf8545a01579__11264.jpg
1c610accc29827e8933e7732535c257a519e95c7227ee1e964a67702297a5182  raw/projekt-dom-w-kotewkach-6-e-3540cfad69bcadefc96581ba1dc8faeb__11266.jpg
1d28145affe2a8c1f2c7acf663d79ad20ee2465762e9a666f9e375b8c0780c66  raw/projekt-dom-w-kotewkach-6-e-3f3d288dbe29fc6ad2b83444cbf4a455__11265.jpg
178be6473f9304310b3c70c3b668aba31dc7b92aee403a4daf9f4adea685a456  raw/projekt-dom-w-kotewkach-6-e-65279fd95dcfe3c0975387e3449b06b4__11817.gif
7211c0301b3cd858d6af08331809ce9937d7dc5dec8074673eaa9d6551c7c527  raw/projekt-dom-w-kotewkach-6-e-cd739df3d5c31a8ee8da4cae60baf098__11815.gif
1c01a1e31fe70a8b9c11a595993ea8d48141e0797f57df12e5b65efac01c207c  raw/przekroj-budynku-projekt-dom-w-kotewkach-6-e-11af766e8cb6b748a7941774ba93f0c7__256.jpg
9ff42437a02c74a9a98ca1cddc88a245d2aef66311758db592d0f163433d79c2  raw/rzut-parteru-z-powierzchniami-projekt-dom-w-kotewkach-6-e-081616c0dc7368e1c7e0b03c10c3614a__11915.gif
7ba77f5fc8fde65dd9951a328c6dfa99962848d16aef5cf476c1b8423970c505  raw/rzut-poddasza-z-powierzchniami-projekt-dom-w-kotewkach-6-e-92cae35da46475eaa5237ab62b117215__11917.gif
```

## Copyright

These drawings are © ARCHON+ Biuro Projektów. They are retained here solely as
verification evidence for a non-commercial reconstruction exercise and are not
redistributed as a substitute for the purchased documentation.

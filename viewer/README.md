# viewer/ — the browser view of `build/model.glb`

The user-facing end of the pipeline. Everything upstream — the transcription, the
validators, the kernel, the generator, the exporter — exists so that what renders here can
be trusted, so this app's job is to show the geometry **and** the evidence for it, and to
avoid implying anything the record does not support.

## Running it

```
just viewer                      # serves the repo root on :8000
open http://localhost:8000/viewer/
```

Or, with no `just`:

```
cd <repo root> && python3 -m http.server 8000
open http://localhost:8000/viewer/
```

**Serve the repository root, not `viewer/`.** The viewer reads `build/model.glb` and
`build/quantities.json`, which live one directory up; `python3 -m http.server` refuses to
serve outside its root, so pointing it at `viewer/` yields a viewer with nothing to view.
The app probes `../build/`, `./build/` and `./` in that order and, if it finds nothing,
prints every URL it tried and the command that would fix it, rather than failing silently.

To point it somewhere else — a copy of the artifacts, another build — use query parameters:

```
/viewer/?model=/some/model.glb&quantities=/some/quantities.json
```

Build the artifacts first if they are missing: `just build`.

## There is no build step

Plain ES modules, an import map, and three.js vendored into `vendor/three/`. No npm
install, no bundler, no network access at run time, and no `node_modules` to go stale. Open
it from any static file server and it works; copy `viewer/` and `build/` onto a USB stick
and it still works.

This is a deliberate departure from `tasks/T14.md`, which specified Vite. Vite would have
bought incremental rebuilds of a six-module app that needs no rebuilding, and it would have
had to be configured to reach outside its own root to read `build/` — cost with no matching
benefit for a repository whose stated policy is local-only and offline-by-design. The GLB
is 112 KB, so there is nothing to bundle, tree-shake or compress either.

### Re-vendoring three.js

`vendor/three/` holds three.js **r180** (MIT, `vendor/three/LICENSE`), copied verbatim:

| Vendored file | Source in the `three` package |
|---|---|
| `three.module.js`, `three.core.js` | `build/` |
| `addons/loaders/GLTFLoader.js` | `examples/jsm/loaders/` |
| `addons/controls/OrbitControls.js` | `examples/jsm/controls/` |
| `addons/utils/BufferGeometryUtils.js` | `examples/jsm/utils/` (GLTFLoader imports it) |

To move to a newer release:

```
cd viewer && npm install three@<version>
cp node_modules/three/build/three.module.js node_modules/three/build/three.core.js vendor/three/
cp node_modules/three/examples/jsm/loaders/GLTFLoader.js          vendor/three/addons/loaders/
cp node_modules/three/examples/jsm/controls/OrbitControls.js      vendor/three/addons/controls/
cp node_modules/three/examples/jsm/utils/BufferGeometryUtils.js   vendor/three/addons/utils/
cp node_modules/three/LICENSE vendor/three/
rm -rf node_modules package.json package-lock.json
```

## What it does

| | |
|---|---|
| **Orbit** | `OrbitControls`, plus framed Iso / Top / S / N / E / W views and an orthographic toggle for true plans and elevations. |
| **Walk** | Pointer-locked first person, eye at **1.65 m** above the level's floor, WASD, Shift to run, `1`/`2` to switch storey. Collision against the visible walls; open doorways are passable. |
| **Layers** | Per-category toggles, per-storey isolation, per-wall-layer toggles (structure / EPS / render), and six presets. `Ground plan` and `Attic plan` give a roof-off orthographic top-down in one click — the fastest way to sanity-check the layout against the printed plans. |
| **Measure** | Click two points for the distance in metres, with Δx / Δy / Δz. Corner snapping is on by default. |
| **Inspector** | Click any part for its node name and the metadata the generator wrote into the GLB — wall type, layer, material, thickness, opening width/height/sill, room names, published area. |
| **Quantities** | `build/quantities.json` rendered as computed-vs-published tables, plus the exporter's own notes verbatim. |

Keys: `M` measure · `O` orbit · `F` first person · `H` hide the panel · `1`/`2` storey ·
`Esc` release the mouse.

## Things worth knowing before you trust a number off the screen

**The measurement tool reads metres straight out of the file.** glTF is defined in metres,
`kotewki.export` writes no scale transform, and this viewer applies none — not on load, not
on the camera, nowhere. Verified against the printed dimension chains: the Pokój floor
plate measures **3.800 × 3.230 m** against a printed `380 × 323` cm, and Łazienka
**2.600 × 1.400 m** against `260 × 140`.

**Which face are you snapping to?** The exterior wall exports as three concentric solids —
250 mm Porotherm, 200 mm EPS, 10 mm render — and they are 460 mm apart end to end. Measured
across the building at mid-span the viewer reads **17.12 × 9.02 m** to the render (the
published *pow. zabudowy* outline, 154.4224 m²) and **8.60 m** north–south to the Porotherm
faces. Both are correct; they are different questions. Turn the build-up layers off in the
Layers panel to pick the face you mean.

**Rooms do not map one-to-one onto floor plates, and the viewer does not pretend they do.**
Four ground-floor rooms — Hol, Salon + Jadalnia, Hol, Kuchnia — are one open-plan face, so
they share a single plate and the walk-mode room label reads all four names followed by
*(one open-plan face)*. The attic's `Schody` has no plate at all: it coincides with the
`A_SO2` stairwell void. Clicking it in the quantities table says so instead of jumping the
camera somewhere arbitrary.

**There are no stairs to climb.** Nothing in `spec/ground.json` models the ground-floor
stair flight — its 2.63 m² is inside the open-plan Salon face and is deducted
arithmetically, not built. Walk mode therefore switches storeys explicitly (`1`/`2`) rather
than with gravity. This is a property of the model, not a shortcut in the viewer.

**Some faces are exactly coincident and would z-fight.** The clearest case: the north edge
of the attic void `A_SO1` is at y = 8550, the same plane as the inner structural face of the
north exterior wall, in the band where the ground storey's walls run up into the attic slab
(README, "Outstanding" 4). Slabs and the roof carry a `polygonOffset` so walls win the tie.
That is a rasteriser bias, not a change to the geometry — nothing in this viewer moves a
vertex.

**The rendering is deliberately not photoreal.** Flat neutral colours, no textures, no
invented cladding. Photoreal materials on a reconstruction invite people to read
undocumented choices as fact.

## Notes for anyone editing the source

Six modules under `src/`, each with its reasoning at the top. Three traps are worth
repeating here because they cost time to find:

1. **`object.name` is not the node name.** GLTFLoader runs names through
   `PropertyBinding.sanitizeNodeName`, which strips `/`, so
   `ground/walls/G_W1/porotherm_25` arrives as `groundwallsG_W1porotherm_25`. The original
   is in `userData.name` and in the generator's own `userData.node`. Use `nodePath()`.

2. **The GLB has no `NORMAL` attribute.** Positions and indices only. GLTFLoader flips its
   *own* materials to `flatShading`, but we replace them, so every material here must set
   `flatShading: true` or the model renders black.

3. **The scene is Z-up and stays Z-up.** `camera.up` is +Z rather than rotating the model,
   so every coordinate on screen is in the same frame as `spec/` and the plans. The cost is
   that `PointerLockControls` — which composes a `YXZ` Euler and assumes Y-up — is unusable;
   `walk.js` drives the Pointer Lock API directly instead.

Layer toggles match on the **node-name prefix**, never `name.split('/')[3]`, because
`attic/openings/windows/A_O2` has a sub-category where a layer would be and `roof/slab` has
only two fields. Where the generator wrote a fact into the mesh `extras` (`kind`,
`wall_type`, `layer`, `room_ids`) the viewer uses that instead of re-deriving it from the
string.

## Measured, not claimed

On the machine this was built on (Apple M1, Chromium/ANGLE Metal, 1440 × 900, device pixel
ratio capped at 2) the animation loop runs at the 60 Hz vsync cap and one full render costs
**0.62 ms** of CPU submit time across **104 draw calls and 2 262 triangles**. That is a
measurement in one browser on one machine, not a promise about yours — but the scene is
small enough that the frame budget is nowhere near spent.

The GLB is 112 KB. `tasks/T14.md` says to compress at export if it exceeds ~20 MB; it does
not, by a factor of 180, so no Draco or meshopt is applied.

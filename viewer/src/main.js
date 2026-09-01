/**
 * Wiring: renderer, cameras, lights, panels.
 *
 * COORDINATE FRAME — Z IS UP AND STAYS UP
 * ---------------------------------------
 * `kotewki.generator` works in plan coordinates with Z as elevation, and the GLB keeps
 * them: X 0…17.10 east, Y 0…9.00 north, Z −0.32 (plinth underside) … 7.361 (chimney tops).
 * three.js conventionally uses Y-up, and the usual fix is to rotate the loaded scene by
 * −90° about X. We do not, and instead set `camera.up` to +Z, so that every coordinate the
 * inspector, the HUD and the measurement tool print is in the same frame as `spec/`,
 * `quantities.json` and the printed plans. The only cost is that `PointerLockControls` is
 * unusable (see `walk.js`); `OrbitControls` already honours an arbitrary `camera.up`.
 *
 * NEAR / FAR
 * ----------
 * The exterior wall exports as three concentric solids and the outermost, the render, is
 * **10 mm** thick. A near plane pushed out to the usual 0.1–1 m would put depth resolution
 * in the same order as that layer and it would z-fight. 0.05 m against a 400 m far plane
 * leaves roughly a millimetre of depth resolution at 30 m, which is an order of magnitude
 * clear.
 *
 * WHAT IS NOT CLAIMED
 * -------------------
 * The frame-rate figure in the HUD is measured live in *this* browser. It is not a promise
 * about any other machine.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

import { notFoundMessage, resolveModelUrl, resolveQuantitiesUrl } from './artifacts.js';
import { buildEdges, loadModel, nodePath } from './model.js';
import { createLayers } from './layers.js';
import { createMeasure } from './measure.js';
import { artifactLine, renderQuantities } from './quantities.js';
import { buildCollider, createWalker } from './walk.js';

const $ = (selector) => document.querySelector(selector);

const canvas = $('#stage');
const labels = $('#labels');

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.NeutralToneMapping ?? THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1b1e22);

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

const camera = new THREE.PerspectiveCamera(52, 1, 0.05, 400);
camera.up.set(0, 0, 1);
camera.position.set(30, -22, 17);

const ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, -200, 400);
ortho.up.set(0, 0, 1);

let activeCamera = camera;

// ------------------------------------------------------------------ lighting
// Soft ambient + one directional key with shadows: enough to read form and depth, nothing
// that pretends to be a photograph.
scene.add(new THREE.HemisphereLight(0xdfe6ee, 0x5f5a52, 1.15));
scene.add(new THREE.AmbientLight(0xffffff, 0.18));

const sun = new THREE.DirectionalLight(0xfff4e2, 2.1);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0006;
sun.shadow.normalBias = 0.02;
scene.add(sun);
scene.add(sun.target);

// -------------------------------------------------------------------- ground
const groundGroup = new THREE.Group();
const groundPlane = new THREE.Mesh(
  new THREE.PlaneGeometry(160, 160),
  new THREE.MeshStandardMaterial({ color: 0x6d7169, roughness: 1 }),
);
groundPlane.receiveShadow = true;
groundGroup.add(groundPlane);
const grid = new THREE.GridHelper(80, 80, 0x555f55, 0x424a44);
grid.rotation.x = Math.PI / 2;
grid.material.transparent = true;
grid.material.opacity = 0.4;
groundGroup.add(grid);
scene.add(groundGroup);

// ------------------------------------------------------------------ controls
const orbit = new OrbitControls(camera, canvas);
orbit.enableDamping = true;
orbit.dampingFactor = 0.09;
orbit.maxPolarAngle = Math.PI;
orbit.screenSpacePanning = true;

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

const state = {
  mode: 'orbit',
  measuring: false,
  parts: [],
  plates: [],
  layers: null,
  walker: null,
  edges: null,
  levels: { ground: { elevation_m: 0 }, attic: { elevation_m: 3.04 } },
  centre: new THREE.Vector3(),
  radius: 12,
};

// ---------------------------------------------------------------------- boot

async function boot() {
  const status = $('#boot-status');
  const fill = $('#boot-bar-fill');

  const model = await resolveModelUrl();
  if (!model.url) return fail(notFoundMessage('build/model.glb', model.tried));

  status.textContent = `Loading ${model.url} …`;
  let loaded;
  try {
    loaded = await loadModel(model.url, (event) => {
      if (event.lengthComputable) fill.style.width = `${(event.loaded / event.total) * 100}%`;
    });
  } catch (error) {
    return fail(`Failed to parse ${model.url}\n\n${error.stack || error.message}`);
  }
  fill.style.width = '100%';

  scene.add(loaded.root);
  state.parts = loaded.parts;
  state.plates = loaded.parts.filter((part) => part.userData.info.kind === 'room');

  const extras = loaded.root.userData || {};
  if (extras.levels) state.levels = extras.levels;
  const terrain = typeof extras.terrain_m === 'number' ? extras.terrain_m : -0.32;
  groundGroup.position.z = terrain - 0.001;

  loaded.bounds.getCenter(state.centre);
  state.radius = loaded.bounds.getSize(new THREE.Vector3()).length() / 2;
  frameSun(loaded.bounds);

  state.edges = buildEdges(loaded.parts);
  state.edges.visible = false;
  scene.add(state.edges);

  state.layers = createLayers(loaded.parts, () => {
    syncEdges();
    if (state.walker) state.walker.setBoxes(buildCollider(state.parts));
  });
  state.layers.mount($('#layer-groups'));

  state.walker = createWalker({ camera, canvas, levels: state.levels });
  state.walker.setBoxes(buildCollider(state.parts));

  setView('iso');
  $('#boot').classList.add('gone');
  setTimeout(() => $('#boot').remove(), 400);

  loadQuantities();

  // A small handle for the console and for automated checks: everything the UI drives is
  // reachable from here, so a spot-check does not have to synthesise mouse events.
  window.__viewer = {
    THREE,
    scene,
    camera,
    renderer,
    state,
    orbit,
    measure,
    setMode,
    setView,
    raycastFrom,
    nodePath,
    get activeCamera() {
      return activeCamera;
    },
    partByPath: (path) => state.parts.find((part) => nodePath(part) === path),
  };
}

function fail(message) {
  const box = $('#boot-error');
  box.hidden = false;
  box.textContent = message;
  $('#boot-status').textContent = 'Nothing to show.';
  $('#boot-bar-fill').style.background = 'var(--bad)';
}

function frameSun(bounds) {
  const size = bounds.getSize(new THREE.Vector3());
  const centre = bounds.getCenter(new THREE.Vector3());
  const span = Math.max(size.x, size.y) * 0.9;
  sun.position.set(centre.x + span, centre.y - span * 1.1, centre.z + span * 1.3);
  sun.target.position.copy(centre);
  const shadow = sun.shadow.camera;
  shadow.left = -span * 1.4;
  shadow.right = span * 1.4;
  shadow.top = span * 1.4;
  shadow.bottom = -span * 1.4;
  shadow.near = 1;
  shadow.far = span * 6;
  shadow.updateProjectionMatrix();
}

async function loadQuantities() {
  const found = await resolveQuantitiesUrl();
  const out = $('#quantities-out');
  if (!found.url) {
    out.innerHTML = `<pre class="readout">${notFoundMessage('build/quantities.json', found.tried)}</pre>`;
    return;
  }
  try {
    const q = await (await fetch(found.url)).json();
    renderQuantities(out, q, { onRoomClick: focusRoom });
    $('#artifact-line').textContent = artifactLine(q);
  } catch (error) {
    out.innerHTML = `<pre class="readout">Could not read ${found.url}\n${error.message}</pre>`;
  }
}

/** Frame a room from the quantities table. Rooms with no plate say so instead of jumping. */
function focusRoom(room) {
  const ids = new Set(room.ids);
  const plate = state.plates.find((part) =>
    (part.userData.info.roomIds || []).some((id) => ids.has(id)),
  );
  if (!plate) {
    $('#hud-room').textContent = `${room.names.join(' + ')} — no floor plate in the mesh`;
    return;
  }
  const box = new THREE.Box3().setFromObject(plate);
  const centre = box.getCenter(new THREE.Vector3());
  if (state.mode === 'walk') {
    state.walker.placeAt(centre.x, centre.y, room.level);
  } else {
    orbit.target.copy(centre);
    const distance = Math.max(box.getSize(new THREE.Vector3()).length() * 1.6, 6);
    camera.position.set(centre.x + distance * 0.6, centre.y - distance * 0.8, centre.z + distance * 0.7);
  }
  $('#hud-room').textContent = room.names.join(' + ');
}

// ------------------------------------------------------------------- viewing

/**
 * Frame the whole model from a named direction.
 *
 * The distance is derived from the bounding sphere and the vertical field of view rather
 * than being a hand-tuned multiple of the building size, so the model stays framed when the
 * window is resized or a layer preset shrinks what is on screen.
 */
const VIEW_DIRECTIONS = {
  iso: [0.72, -0.78, 0.58],
  // Not exactly (0,0,1): a dead-vertical view direction is parallel to camera.up and the
  // look-at basis degenerates. A 0.4° lean is invisible and well clear of OrbitControls' EPS.
  top: [0, -0.007, 1],
  south: [0, -1, 0.06],
  north: [0, 1, 0.06],
  east: [1, 0, 0.06],
  west: [-1, 0, 0.06],
};

function setView(name) {
  const centre = state.centre;
  const radius = Math.max(state.radius, 6);
  const half = THREE.MathUtils.degToRad(camera.fov / 2);
  const distance = (radius / Math.sin(half)) * 1.06;
  const direction = new THREE.Vector3(...(VIEW_DIRECTIONS[name] || VIEW_DIRECTIONS.iso)).normalize();
  if (state.mode === 'walk') setMode('orbit');
  camera.position.copy(centre).addScaledVector(direction, distance);
  orbit.target.copy(centre);
  orbit.update();
  syncOrtho();
}

function syncOrtho() {
  if (activeCamera !== ortho) return;
  const distance = camera.position.distanceTo(orbit.target);
  const halfHeight = distance * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  const halfWidth = halfHeight * camera.aspect;
  ortho.left = -halfWidth;
  ortho.right = halfWidth;
  ortho.top = halfHeight;
  ortho.bottom = -halfHeight;
  ortho.position.copy(camera.position);
  ortho.up.copy(camera.up);
  ortho.lookAt(orbit.target);
  ortho.updateProjectionMatrix();
}

function setMode(mode) {
  if (!state.walker) return;
  state.mode = mode;
  const walking = mode === 'walk';
  orbit.enabled = !walking;
  $('#crosshair').hidden = !walking;
  $('#walk-hint').hidden = !walking;
  $('#walk-level-row').hidden = !walking;
  $('#walk-collide-row').hidden = !walking;
  $('#hud-mode').textContent = walking ? 'Walk' : 'Orbit';
  document.querySelectorAll('#mode-buttons button').forEach((button) => {
    button.classList.toggle('on', button.dataset.mode === mode);
  });
  if (walking) {
    $('#opt-ortho').checked = false;
    activeCamera = camera;
    state.walker.enter(buildCollider(state.parts));
  } else {
    state.walker.exit();
    $('#hud-room').textContent = '';
  }
}

function syncEdges() {
  if (!state.edges) return;
  for (const line of state.edges.children) line.visible = line.userData.owner.visible;
}

// ------------------------------------------------------------------ pointing

const measure = createMeasure({ scene, labels });

function raycastFrom(ndcX, ndcY) {
  pointer.set(ndcX, ndcY);
  raycaster.setFromCamera(pointer, activeCamera);
  const targets = state.parts.filter((part) => part.visible);
  return raycaster.intersectObjects(targets, false);
}

function describeSelection(hit) {
  const info = hit.object.userData.info;
  const data = hit.object.userData;
  const rows = [
    ['node', info.path],
    ['kind', info.kind],
    ['level', info.level],
  ];
  if (info.wallType) rows.push(['wall type', info.wallType]);
  if (info.layer) rows.push(['layer', info.layer]);
  if (data.material) rows.push(['material', data.material]);
  if (typeof data.thickness_m === 'number') rows.push(['thickness', `${data.thickness_m.toFixed(3)} m`]);
  if (typeof data.width_m === 'number') rows.push(['width', `${data.width_m.toFixed(3)} m`]);
  if (typeof data.height_m === 'number') rows.push(['height', `${data.height_m.toFixed(3)} m`]);
  if (typeof data.sill_m === 'number') rows.push(['sill', `${data.sill_m.toFixed(3)} m`]);
  if (info.roomNames.length) rows.push(['rooms', info.roomNames.join(' + ')]);
  if (typeof data.published_area_m2 === 'number') {
    rows.push(['published area', `${data.published_area_m2.toFixed(2)} m²`]);
  }
  if (typeof data.plate_area_m2 === 'number') {
    rows.push(['plate area', `${data.plate_area_m2.toFixed(4)} m²`]);
  }
  const box = new THREE.Box3().setFromObject(hit.object);
  const size = box.getSize(new THREE.Vector3());
  rows.push(['bbox size', `${size.x.toFixed(3)} × ${size.y.toFixed(3)} × ${size.z.toFixed(3)} m`]);
  rows.push([
    'bbox min',
    `${box.min.x.toFixed(3)}, ${box.min.y.toFixed(3)}, ${box.min.z.toFixed(3)}`,
  ]);
  rows.push(['hit point', `${hit.point.x.toFixed(3)}, ${hit.point.y.toFixed(3)}, ${hit.point.z.toFixed(3)}`]);
  $('#inspect-out').innerHTML = rows
    .map(([key, value]) => `<div><b>${key}</b> ${value}</div>`)
    .join('');
}

let downAt = null;

canvas.addEventListener('pointerdown', (event) => {
  downAt = { x: event.clientX, y: event.clientY };
});

canvas.addEventListener('pointerup', (event) => {
  if (state.mode === 'walk') {
    if (!state.walker.state.locked) {
      state.walker.requestLock();
      return;
    }
    if (state.measuring) {
      const hits = raycastFrom(0, 0);
      if (hits.length) {
        measure.addFromHit(hits[0]);
        $('#measure-list').innerHTML = measure.summary();
      }
    }
    return;
  }
  if (!downAt) return;
  const moved = Math.hypot(event.clientX - downAt.x, event.clientY - downAt.y);
  downAt = null;
  if (moved > 4) return;

  const rect = canvas.getBoundingClientRect();
  const hits = raycastFrom(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  );
  if (!hits.length) return;
  if (state.measuring) {
    measure.addFromHit(hits[0]);
    $('#measure-list').innerHTML = measure.summary();
  } else {
    describeSelection(hits[0]);
    $('#panel-inspect').open = true;
  }
});

// ------------------------------------------------------------------------ UI

document.querySelectorAll('#mode-buttons button').forEach((button) => {
  button.addEventListener('click', () => setMode(button.dataset.mode));
});
document.querySelectorAll('#view-buttons button').forEach((button) => {
  button.addEventListener('click', () => setView(button.dataset.view));
});
document.querySelectorAll('#isolate-buttons button').forEach((button) => {
  button.addEventListener('click', () => {
    if (!state.layers) return;
    document.querySelectorAll('#isolate-buttons button').forEach((other) => {
      other.classList.toggle('on', other === button);
    });
    state.layers.setIsolation(button.dataset.isolate);
  });
});
document.querySelectorAll('#preset-buttons button').forEach((button) => {
  button.addEventListener('click', () => {
    if (!state.layers) return;
    const preset = state.layers.applyPreset(button.dataset.preset, $('#layer-groups'));
    if (!preset) return;
    document.querySelectorAll('#isolate-buttons button').forEach((other) => {
      other.classList.toggle('on', other.dataset.isolate === preset.isolate);
    });
    if (preset.view) {
      setView(preset.view);
      $('#opt-ortho').checked = true;
      activeCamera = ortho;
      syncOrtho();
    }
  });
});
document.querySelectorAll('#level-buttons button').forEach((button) => {
  button.addEventListener('click', () => {
    if (!state.walker) return;
    document.querySelectorAll('#level-buttons button').forEach((other) => {
      other.classList.toggle('on', other === button);
    });
    state.walker.setLevel(button.dataset.level);
  });
});

$('#opt-ortho').addEventListener('change', (event) => {
  activeCamera = event.target.checked ? ortho : camera;
  syncOrtho();
});
$('#opt-ground').addEventListener('change', (event) => {
  groundGroup.visible = event.target.checked;
});
$('#opt-shadows').addEventListener('change', (event) => {
  renderer.shadowMap.enabled = event.target.checked;
  scene.traverse((object) => {
    if (object.isMesh && object.material) object.material.needsUpdate = true;
  });
});
$('#opt-edges').addEventListener('change', (event) => {
  if (state.edges) state.edges.visible = event.target.checked;
});
$('#opt-measure').addEventListener('change', (event) => {
  state.measuring = event.target.checked;
});
$('#opt-snap').addEventListener('change', (event) => measure.setSnap(event.target.checked));
$('#opt-collide').addEventListener('change', (event) => {
  if (state.walker) state.walker.setCollide(event.target.checked);
});
$('#measure-clear').addEventListener('click', () => {
  measure.clear();
  $('#measure-list').innerHTML = measure.summary();
});
$('#sidebar-toggle').addEventListener('click', () => $('#sidebar').classList.toggle('hidden'));

window.addEventListener('keydown', (event) => {
  if (event.target.tagName === 'INPUT') return;
  if (event.code === 'KeyM') {
    const box = $('#opt-measure');
    box.checked = !box.checked;
    state.measuring = box.checked;
    $('#panel-measure').open = true;
  }
  if (event.code === 'KeyO') setMode('orbit');
  if (event.code === 'KeyF') setMode('walk');
  if (event.code === 'KeyH') $('#sidebar').classList.toggle('hidden');
});

// -------------------------------------------------------------------- resize

function resize() {
  const width = window.innerWidth;
  const height = window.innerHeight;
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  syncOrtho();
}
window.addEventListener('resize', resize);
resize();

// --------------------------------------------------------------------- frame

const clock = new THREE.Clock();
let frames = 0;
let fpsTime = 0;

function tick() {
  requestAnimationFrame(tick);
  const dt = clock.getDelta();

  if (state.mode === 'walk') {
    state.walker.update(dt);
    const info = state.walker.roomAt(raycaster, state.plates);
    $('#hud-room').textContent = info
      ? info.roomNames.join(' + ') + (info.roomNames.length > 1 ? ' (one open-plan face)' : '')
      : '—';
    const p = state.walker.state.position;
    $('#hud-pos').textContent =
      `x ${p.x.toFixed(2)} y ${p.y.toFixed(2)} eye ${p.z.toFixed(2)} m`;
  } else {
    orbit.update();
    syncOrtho();
    const p = camera.position;
    $('#hud-pos').textContent = `x ${p.x.toFixed(1)} y ${p.y.toFixed(1)} z ${p.z.toFixed(1)}`;
  }

  measure.update(activeCamera, window.innerWidth, window.innerHeight);
  renderer.render(scene, activeCamera);

  frames += 1;
  fpsTime += dt;
  if (fpsTime >= 0.5) {
    const fps = frames / fpsTime;
    $('#hud-fps').textContent = `${fps.toFixed(0)} fps`;
    window.__fps = fps;
    frames = 0;
    fpsTime = 0;
  }
}

$('#measure-list').innerHTML = measure.summary();
$('#hud-mode').textContent = 'Orbit';
tick();
boot().catch((error) => fail(error.stack || String(error)));

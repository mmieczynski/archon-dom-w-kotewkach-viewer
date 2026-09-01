/**
 * First-person walkthrough with collision.
 *
 * WHY NOT `PointerLockControls`
 * -----------------------------
 * three.js's `PointerLockControls` composes its rotation as a `YXZ` Euler on the camera,
 * i.e. it assumes Y is up. This scene is Z-up (the generator's plan coordinates with Z as
 * elevation) and we deliberately do not rotate the model, so those controls would yaw
 * around the wrong axis. Pointer Lock itself is four lines of browser API; only the
 * *rotation convention* was ever the problem, so we keep the lock and supply the yaw/pitch.
 *
 * WHY AABBs AND NOT A VOLUMETRIC METHOD
 * -------------------------------------
 * The exported solids interpenetrate by 21.0 m³ across 150 pairs — by design; chimney
 * stacks are traced once per storey and pass through the walls they abut, and the ground
 * storey's exterior walls run up into the attic slab band (README, "Outstanding" 4). Any
 * collision scheme that assumes a clean solid partition would misbehave. Overlapping
 * axis-aligned boxes do not care: two boxes covering the same space push the player the
 * same way one would.
 *
 * DOORWAYS
 * --------
 * The generator cuts openings out of the wall solids with a boolean, but a wall's *bounding
 * box* obviously does not have the hole in it, so colliding against raw AABBs would seal
 * every door. Because everything here is axis-aligned, the fix is exact rather than
 * approximate: for each wall we take the interval its door and passage openings occupy
 * along the wall's long axis and emit the complement as one or two sub-boxes. Windows are
 * *not* subtracted — they have a 0.9 m sill and you should not be able to walk through one.
 *
 * WHAT IS NOT MODELLED, AND SO IS NOT WALKABLE
 * --------------------------------------------
 * Nothing in `spec/ground.json` models the ground-floor stair flight — its 2.63 m² is
 * inside the open-plan Salon face and is deducted arithmetically, not built. There is
 * therefore no geometry to climb. Levels are switched explicitly (1 / 2) rather than by
 * gravity, and the eye sits a fixed 1.65 m above the *declared* floor elevation of the
 * level you are on.
 */

import * as THREE from 'three';

export const EYE_HEIGHT = 1.65;
const RADIUS = 0.25;
const SPEED = 2.6; // m/s
const RUN = 5.4;
const PITCH_LIMIT = THREE.MathUtils.degToRad(85);
const LOOK_SPEED = 0.0022;

const _box = new THREE.Box3();
const _down = new THREE.Vector3(0, 0, -1);

function boxOf(mesh) {
  _box.setFromObject(mesh);
  return {
    x0: _box.min.x,
    y0: _box.min.y,
    z0: _box.min.z,
    x1: _box.max.x,
    y1: _box.max.y,
    z1: _box.max.z,
  };
}

/**
 * Blocking boxes for the current visibility state.
 *
 * `parts` is everything; `blockers` are the *visible* wall and chimney solids, so hiding a
 * layer really does open it up — that is a feature (hide all walls and you can roam), and
 * it keeps what you see and what you bump into in agreement. Openings are read from the
 * full part list, not the visible one: turning the door leaves off must not weld the
 * doorways shut.
 */
export function buildCollider(parts) {
  const cuts = new Map();
  for (const part of parts) {
    const info = part.userData.info;
    if (info.kind !== 'door' && info.kind !== 'passage') continue;
    if (!info.wallId) continue;
    if (!cuts.has(info.wallId)) cuts.set(info.wallId, []);
    cuts.get(info.wallId).push(boxOf(part));
  }

  const boxes = [];
  for (const part of parts) {
    if (!part.visible) continue;
    const info = part.userData.info;
    if (info.kind !== 'wall' && info.kind !== 'chimney') continue;
    const wall = boxOf(part);
    const along = wall.x1 - wall.x0 >= wall.y1 - wall.y0 ? 'x' : 'y';
    const lo = along === 'x' ? wall.x0 : wall.y0;
    const hi = along === 'x' ? wall.x1 : wall.y1;

    const intervals = (cuts.get(info.wallId) || [])
      .map((cut) => (along === 'x' ? [cut.x0, cut.x1] : [cut.y0, cut.y1]))
      .filter(([a, b]) => b > lo + 1e-6 && a < hi - 1e-6)
      .sort((a, b) => a[0] - b[0]);

    let cursor = lo;
    const spans = [];
    for (const [a, b] of intervals) {
      if (a > cursor + 1e-6) spans.push([cursor, Math.min(a, hi)]);
      cursor = Math.max(cursor, b);
    }
    if (cursor < hi - 1e-6) spans.push([cursor, hi]);

    for (const [a, b] of spans) {
      boxes.push(
        along === 'x'
          ? { ...wall, x0: a, x1: b }
          : { ...wall, y0: a, y1: b },
      );
    }
  }
  return boxes;
}

function clamp(value, lo, hi) {
  return value < lo ? lo : value > hi ? hi : value;
}

/** Push a circle of radius `RADIUS` out of every box it overlaps. Two passes settle corners. */
function resolve(position, boxes, feet, head) {
  for (let pass = 0; pass < 2; pass += 1) {
    for (const box of boxes) {
      if (box.z1 <= feet || box.z0 >= head) continue;
      const cx = clamp(position.x, box.x0, box.x1);
      const cy = clamp(position.y, box.y0, box.y1);
      const dx = position.x - cx;
      const dy = position.y - cy;
      const d2 = dx * dx + dy * dy;
      if (d2 >= RADIUS * RADIUS) continue;
      if (d2 > 1e-10) {
        const d = Math.sqrt(d2);
        position.x = cx + (dx / d) * RADIUS;
        position.y = cy + (dy / d) * RADIUS;
      } else {
        // Dead centre inside the box: leave by the nearest face.
        const out = [
          [position.x - box.x0, () => { position.x = box.x0 - RADIUS; }],
          [box.x1 - position.x, () => { position.x = box.x1 + RADIUS; }],
          [position.y - box.y0, () => { position.y = box.y0 - RADIUS; }],
          [box.y1 - position.y, () => { position.y = box.y1 + RADIUS; }],
        ].sort((a, b) => a[0] - b[0])[0];
        out[1]();
      }
    }
  }
}

export function createWalker({ camera, canvas, levels }) {
  const state = {
    active: false,
    locked: false,
    level: 'ground',
    position: new THREE.Vector3(8.5, 2.4, EYE_HEIGHT),
    yaw: Math.PI / 2,
    pitch: -0.05,
    collide: true,
    boxes: [],
    keys: new Set(),
  };

  const target = new THREE.Vector3();
  const forward = new THREE.Vector3();
  const right = new THREE.Vector3();

  function floorZ() {
    const level = levels[state.level];
    return level ? level.elevation_m : 0;
  }

  function applyCamera() {
    camera.position.copy(state.position);
    const cp = Math.cos(state.pitch);
    target.set(
      state.position.x + cp * Math.cos(state.yaw),
      state.position.y + cp * Math.sin(state.yaw),
      state.position.z + Math.sin(state.pitch),
    );
    camera.up.set(0, 0, 1);
    camera.lookAt(target);
  }

  function onMouseMove(event) {
    if (!state.locked) return;
    state.yaw -= event.movementX * LOOK_SPEED;
    state.pitch = clamp(state.pitch - event.movementY * LOOK_SPEED, -PITCH_LIMIT, PITCH_LIMIT);
    applyCamera();
  }

  function onLockChange() {
    state.locked = document.pointerLockElement === canvas;
    if (!state.locked) state.keys.clear();
  }

  function onKeyDown(event) {
    if (!state.active) return;
    state.keys.add(event.code);
    if (event.code === 'Digit1') setLevel('ground');
    if (event.code === 'Digit2') setLevel('attic');
    if (['KeyW', 'KeyA', 'KeyS', 'KeyD', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.code)) {
      event.preventDefault();
    }
  }

  function onKeyUp(event) {
    state.keys.delete(event.code);
  }

  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('pointerlockchange', onLockChange);
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);

  function setLevel(level) {
    if (!levels[level]) return;
    state.level = level;
    state.position.z = levels[level].elevation_m + EYE_HEIGHT;
    applyCamera();
  }

  function enter(boxes) {
    state.active = true;
    state.boxes = boxes;
    state.position.z = floorZ() + EYE_HEIGHT;
    applyCamera();
  }

  function exit() {
    state.active = false;
    if (document.pointerLockElement === canvas) document.exitPointerLock();
  }

  function requestLock() {
    if (state.active && document.pointerLockElement !== canvas) canvas.requestPointerLock();
  }

  function update(dt) {
    if (!state.active) return;
    const keys = state.keys;
    let ax = 0;
    let ay = 0;
    if (keys.has('KeyW') || keys.has('ArrowUp')) ay += 1;
    if (keys.has('KeyS') || keys.has('ArrowDown')) ay -= 1;
    if (keys.has('KeyD') || keys.has('ArrowRight')) ax += 1;
    if (keys.has('KeyA') || keys.has('ArrowLeft')) ax -= 1;

    const feet = floorZ() + 0.08;
    const head = floorZ() + EYE_HEIGHT + 0.12;

    if (ax || ay) {
      const speed = keys.has('ShiftLeft') || keys.has('ShiftRight') ? RUN : SPEED;
      forward.set(Math.cos(state.yaw), Math.sin(state.yaw), 0).normalize();
      right.set(forward.y, -forward.x, 0);
      const step = Math.min(dt, 0.05) * speed;
      const length = Math.hypot(ax, ay) || 1;
      state.position.x += ((forward.x * ay + right.x * ax) / length) * step;
      state.position.y += ((forward.y * ay + right.y * ax) / length) * step;
      if (state.collide) resolve(state.position, state.boxes, feet, head);
    }
    state.position.z = floorZ() + EYE_HEIGHT;
    applyCamera();
  }

  return {
    state,
    enter,
    exit,
    update,
    setLevel,
    requestLock,
    setBoxes(boxes) {
      state.boxes = boxes;
    },
    setCollide(on) {
      state.collide = on;
    },
    placeAt(x, y, level) {
      if (level) state.level = level;
      state.position.set(x, y, floorZ() + EYE_HEIGHT);
      applyCamera();
    },
    /** Which room is the walker standing in? Raycast down onto the floor plates. */
    roomAt(raycaster, plates) {
      if (!plates.length) return null;
      raycaster.set(state.position, _down);
      raycaster.far = EYE_HEIGHT + 0.5;
      const hits = raycaster.intersectObjects(plates, false);
      raycaster.far = Infinity;
      if (!hits.length) return null;
      return hits[0].object.userData.info;
    },
  };
}

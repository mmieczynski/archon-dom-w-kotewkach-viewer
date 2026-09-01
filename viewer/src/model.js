/**
 * Loading `build/model.glb` and dressing it.
 *
 * THREE THINGS THAT WILL BITE ANYONE EDITING THIS FILE
 * ----------------------------------------------------
 * 1. **No scale, ever.** glTF is defined in metres and `kotewki.export` writes the scene
 *    with the identity transform on every one of its 106 flat root nodes. One `unit = 1 m`
 *    here as well: the measurement tool reads world coordinates straight out and a scale
 *    factor anywhere in this file would silently corrupt every number the viewer prints.
 *
 * 2. **`object.name` is NOT the node name.** GLTFLoader runs every name through
 *    `PropertyBinding.sanitizeNodeName`, which strips `/` among other reserved characters,
 *    so `ground/walls/G_W1/porotherm_25` arrives as `groundwallsG_W1porotherm_25`. The
 *    original survives in two places — `userData.name` (set by the loader from the node)
 *    and `userData.node` (written by the generator into the *mesh* extras). Use
 *    {@link nodePath}, never `object.name`.
 *
 * 3. **The mesh carries no NORMAL attribute.** The exporter writes POSITION and indices
 *    only. GLTFLoader compensates by flipping its own materials to `flatShading`, but we
 *    replace those materials wholesale, so ours must set `flatShading: true` too or every
 *    surface renders black.
 *
 * NODE NAMES
 * ----------
 * `level / category / id [/ layer]`, with two documented exceptions that make naive field
 * indexing wrong:
 *
 *     attic/openings/windows/A_O2     field 3 is a SUB-CATEGORY, not a layer
 *     roof/slab                       only two fields at all
 *
 * So classification is by prefix and by the generator's own `extras`, never by
 * `name.split('/')[3]`.
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

/** The original, unsanitised node path. See note 2 above. */
export function nodePath(object) {
  const data = object.userData || {};
  return data.node || data.name || object.name || '';
}

/**
 * Neutral, untextured materials.
 *
 * Deliberately not photoreal. This model represents *documented geometry*; inventing
 * brickwork or roof tiles would invite a reader to treat undecided detail as fact. The
 * only concession to realism is that the two insulation/render layers read slightly
 * differently from the structural core, because being able to see the wall build-up is the
 * point of exporting it as three separate solids.
 */
const PALETTE = {
  wall_structure: 0xd5cfc4,
  wall_partition: 0xdcd7ce,
  wall_eps: 0xefe7d2,
  wall_render: 0xf7f4ee,
  slab: 0xb2ada4,
  plinth: 0x9d998f,
  roof: 0x807a71,
  glass: 0x8fb3c6,
  door: 0xa08e77,
  passage: 0xc9b89e,
  chimney: 0xb3a598,
  plate_ground: 0xcbc4b3,
  plate_attic: 0xc0c5cb,
  fallback: 0xc8c8c8,
};

function standard(color, extra = {}) {
  return new THREE.MeshStandardMaterial({
    color,
    roughness: 0.92,
    metalness: 0.0,
    flatShading: true, // no NORMAL attribute in the GLB — see note 3
    ...extra,
  });
}

/**
 * A material that loses depth ties.
 *
 * Some faces in this scene are *exactly* coplanar and genuinely so. The clearest case is
 * the attic slab: `A_SO1`, the void over the Salon, has its north edge at y = 8550, which is
 * the same plane as the inner structural face of the north exterior wall — so the void's
 * reveal and the wall's inner face occupy identical geometry between z 2.70 and 3.04, where
 * the ground storey's walls run up into the slab band (README, "Outstanding" 4). Two
 * coincident surfaces z-fight at any depth precision; a nearer near plane cannot help.
 *
 * `polygonOffset` breaks the tie in the rasteriser without moving a vertex, which is the
 * only kind of fix allowed here: the geometry is the evidence and the viewer does not edit
 * it. Slabs and the roof are biased *away* from the eye, so walls win.
 */
function biasedBack(material) {
  material.polygonOffset = true;
  material.polygonOffsetFactor = 1.0;
  material.polygonOffsetUnits = 1.0;
  return material;
}

function glass() {
  return new THREE.MeshStandardMaterial({
    color: PALETTE.glass,
    roughness: 0.25,
    metalness: 0.0,
    transparent: true,
    opacity: 0.34,
    side: THREE.DoubleSide,
    flatShading: true,
  });
}

/**
 * Everything the viewer wants to know about one exported solid, derived once at load.
 *
 * `kind`, `level`, `layer`, `wallType` and `roomNames` all come from the generator's mesh
 * `extras`, which GLTFLoader copies onto `userData`. They are authoritative — the node name
 * is a human-readable echo of them. `path` is kept because the layer UI matches on prefixes
 * and because it is what a reader recognises from `tests/test_export.py`.
 */
function describe(object) {
  const data = object.userData || {};
  const path = nodePath(object);
  const segments = path.split('/');
  return {
    path,
    level: data.level || segments[0] || '',
    category: segments[1] || '',
    kind: data.kind || '',
    layer: data.layer || '',
    wallType: data.wall_type || '',
    wallId: data.wall_id || '',
    openingId: data.opening_id || '',
    roomIds: data.room_ids || [],
    roomNames: data.room_names || [],
    sill: typeof data.sill_m === 'number' ? data.sill_m : null,
  };
}

function materialFor(info) {
  switch (info.kind) {
    case 'wall':
      if (info.layer === 'termo_organika_eps') return standard(PALETTE.wall_eps);
      if (info.layer === 'tynk_cienkowarstwowy') return standard(PALETTE.wall_render);
      return standard(info.wallType === 'partition' ? PALETTE.wall_partition : PALETTE.wall_structure);
    case 'chimney':
      return standard(PALETTE.chimney);
    case 'slab':
      return biasedBack(standard(info.path.endsWith('/plinth') ? PALETTE.plinth : PALETTE.slab));
    case 'roof':
      return biasedBack(standard(PALETTE.roof, { side: THREE.DoubleSide }));
    case 'window':
    case 'roof_window':
      return glass();
    case 'door':
      return standard(PALETTE.door);
    case 'passage':
      return standard(PALETTE.passage, { transparent: true, opacity: 0.35 });
    case 'room':
      return standard(info.level === 'attic' ? PALETTE.plate_attic : PALETTE.plate_ground, {
        side: THREE.DoubleSide,
      });
    default:
      return standard(PALETTE.fallback);
  }
}

/**
 * Load the GLB and return the scene plus a flat, pre-classified part list.
 *
 * The glTF is Z-up (the generator works in plan coordinates with Z as elevation) and we
 * keep it that way: `camera.up` is set to +Z in `main.js` instead. Rotating the model into
 * three.js's usual Y-up would be harmless for rendering but would mean every coordinate the
 * inspector and the measurement tool print is in a different frame from the spec, the
 * plans and `quantities.json`. Matching the record beats matching the convention.
 */
export async function loadModel(url, onProgress) {
  const gltf = await new GLTFLoader().loadAsync(url, onProgress);
  const root = gltf.scene;

  const parts = [];
  root.traverse((object) => {
    if (!object.isMesh) return;
    const info = describe(object);
    object.userData.info = info;
    object.material = materialFor(info);
    const transparent = object.material.transparent === true;
    object.castShadow = !transparent && info.kind !== 'room';
    object.receiveShadow = !transparent;
    object.renderOrder = transparent ? 1 : 0;
    parts.push(object);
  });

  const bounds = new THREE.Box3().setFromObject(root);
  return { gltf, root, parts, bounds, sceneExtras: root.userData || {} };
}

/** Wireframe outlines. Off by default; useful when judging whether a joint is real. */
export function buildEdges(parts) {
  const group = new THREE.Group();
  group.name = 'edges';
  const material = new THREE.LineBasicMaterial({ color: 0x2f3338, transparent: true, opacity: 0.55 });
  for (const part of parts) {
    if (part.userData.info.kind === 'room') continue;
    const lines = new THREE.LineSegments(new THREE.EdgesGeometry(part.geometry, 25), material);
    lines.userData.owner = part;
    group.add(lines);
  }
  return group;
}

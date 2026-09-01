/**
 * Layer toggles.
 *
 * MATCHING IS BY PREFIX, ON PURPOSE
 * ---------------------------------
 * The node grammar is `level / category / id [/ layer]`, and two shapes in the exported
 * scene legitimately break it:
 *
 *     attic/openings/windows/A_O2     field 3 is a sub-category, not a layer
 *     roof/slab                       two fields, no id and no layer
 *
 * Both are documented in `kotewki.generator` and enumerated in `tests/test_export.py`.
 * `name.split('/')[3] === layer` therefore misclassifies the first and throws on the
 * second; a `startsWith` on the category prefix handles both without special cases. Where
 * the generator wrote a fact into the mesh `extras` — `wall_type`, `layer` — we use that
 * instead of re-deriving it from the string, because the string is the echo and the extras
 * are the source.
 */

const GROUPS = [
  {
    section: 'Building fabric',
    items: [
      {
        id: 'walls-exterior',
        label: 'Exterior walls',
        test: (i) => i.path.startsWith(`${i.level}/walls/`) && i.wallType === 'exterior',
      },
      {
        id: 'walls-interior',
        label: 'Interior walls, partitions & balustrades',
        test: (i) => i.path.startsWith(`${i.level}/walls/`) && i.wallType !== 'exterior',
      },
      { id: 'chimneys', label: 'Chimney stacks', test: (i) => i.path.startsWith(`${i.level}/chimneys/`) },
      { id: 'slabs', label: 'Slabs (plinth, attic floor)', test: (i) => i.path.startsWith(`${i.level}/slabs/`) },
      { id: 'roof-slab', label: 'Roof', test: (i) => i.path.startsWith('roof/slab') },
    ],
  },
  {
    section: 'Openings',
    items: [
      { id: 'windows', label: 'Windows', test: (i) => i.path.startsWith(`${i.level}/openings/windows/`) },
      { id: 'doors', label: 'Doors', test: (i) => i.path.startsWith(`${i.level}/openings/doors/`) },
      {
        id: 'other-openings',
        label: 'Passages (A_O5 stair arrival)',
        test: (i) => i.path.startsWith(`${i.level}/openings/other/`),
        on: false,
      },
      { id: 'roof-windows', label: 'Roof windows', test: (i) => i.path.startsWith('roof/windows/') },
    ],
  },
  {
    section: 'Measured surfaces',
    items: [
      {
        id: 'plates',
        label: 'Room floor plates',
        test: (i) => i.path.startsWith(`${i.level}/rooms/`),
        on: false,
      },
    ],
  },
];

/**
 * Wall build-up sub-toggles. These apply *only* to `kind === 'wall'` solids, and every wall
 * solid carries exactly one of these `layer` values, so a wall is hidden when its own layer
 * is off. `structure` covers the single-layer interior walls; `porotherm_25` is the
 * structural core of the three-layer exterior wall.
 */
const WALL_LAYERS = [
  { id: 'layer-structure', label: 'Structure (Porotherm 25 / masonry)', match: ['structure', 'porotherm_25'] },
  { id: 'layer-eps', label: 'Insulation (EPS 200 mm)', match: ['termo_organika_eps'] },
  { id: 'layer-render', label: 'Render (10 mm, outboard of the dimensioned face)', match: ['tynk_cienkowarstwowy'] },
];

const PRESETS = {
  everything: { isolate: 'all', on: '*', layers: '*' },
  'roof-off': { isolate: 'all', on: '*', off: ['roof-slab', 'roof-windows'], layers: '*' },
  'ground-plan': {
    isolate: 'ground',
    on: '*',
    off: ['roof-slab', 'roof-windows'],
    layers: '*',
    view: 'top',
  },
  'attic-plan': {
    isolate: 'attic',
    on: '*',
    off: ['roof-slab', 'roof-windows'],
    layers: '*',
    view: 'top',
  },
  shell: {
    isolate: 'all',
    on: ['walls-exterior', 'roof-slab', 'slabs', 'windows', 'doors', 'roof-windows', 'chimneys'],
    layers: '*',
  },
  structure: {
    isolate: 'all',
    on: ['walls-exterior', 'walls-interior', 'slabs', 'chimneys'],
    layers: ['layer-structure'],
  },
};

export function createLayers(parts, onChange) {
  const state = {
    isolate: 'all',
    groups: new Map(),
    layers: new Map(),
  };
  for (const section of GROUPS) {
    for (const item of section.items) state.groups.set(item.id, item.on !== false);
  }
  for (const layer of WALL_LAYERS) state.layers.set(layer.id, true);

  // Assign every part to exactly one fabric group, once. Parts that match nothing stay
  // visible and are reported, so a new node category shows up rather than vanishing.
  const byGroup = new Map();
  const unmatched = [];
  for (const part of parts) {
    const info = part.userData.info;
    let assigned = null;
    for (const section of GROUPS) {
      for (const item of section.items) {
        if (item.test(info)) {
          assigned = item.id;
          break;
        }
      }
      if (assigned) break;
    }
    if (!assigned) {
      unmatched.push(info.path);
      continue;
    }
    part.userData.group = assigned;
    if (!byGroup.has(assigned)) byGroup.set(assigned, []);
    byGroup.get(assigned).push(part);
  }

  function layerIdFor(info) {
    if (info.kind !== 'wall') return null;
    for (const layer of WALL_LAYERS) if (layer.match.includes(info.layer)) return layer.id;
    return null;
  }

  function isVisible(part) {
    const info = part.userData.info;
    if (state.isolate !== 'all' && !info.path.startsWith(`${state.isolate}/`)) return false;
    const group = part.userData.group;
    if (group && state.groups.get(group) === false) return false;
    const layer = layerIdFor(info);
    if (layer && state.layers.get(layer) === false) return false;
    return true;
  }

  function apply() {
    for (const part of parts) part.visible = isVisible(part);
    if (onChange) onChange();
  }

  function mount(container) {
    container.innerHTML = '';
    for (const section of GROUPS) {
      const block = document.createElement('div');
      block.className = 'group';
      block.innerHTML = `<div class="group-head">${section.section}</div>`;
      for (const item of section.items) {
        const count = (byGroup.get(item.id) || []).length;
        const label = document.createElement('label');
        label.className = 'check';
        label.innerHTML =
          `<input type="checkbox" data-group="${item.id}"${state.groups.get(item.id) ? ' checked' : ''} />` +
          `<span>${item.label}</span><span class="cnt">${count}</span>`;
        label.querySelector('input').addEventListener('change', (event) => {
          state.groups.set(item.id, event.target.checked);
          apply();
        });
        block.appendChild(label);
      }
      container.appendChild(block);
    }

    const wall = document.createElement('div');
    wall.className = 'group';
    wall.innerHTML = '<div class="group-head">Wall build-up</div>';
    for (const layer of WALL_LAYERS) {
      const count = parts.filter((p) => layerIdFor(p.userData.info) === layer.id).length;
      const label = document.createElement('label');
      label.className = 'check';
      label.innerHTML =
        `<input type="checkbox" data-layer="${layer.id}" checked />` +
        `<span>${layer.label}</span><span class="cnt">${count}</span>`;
      label.querySelector('input').addEventListener('change', (event) => {
        state.layers.set(layer.id, event.target.checked);
        apply();
      });
      wall.appendChild(label);
    }
    container.appendChild(wall);

    if (unmatched.length) {
      const warn = document.createElement('div');
      warn.className = 'note';
      warn.textContent =
        `${unmatched.length} node(s) matched no layer group and are always shown: ` +
        `${unmatched.slice(0, 6).join(', ')}${unmatched.length > 6 ? ' …' : ''}`;
      container.appendChild(warn);
    }
    syncInputs(container);
  }

  function syncInputs(container) {
    container.querySelectorAll('input[data-group]').forEach((input) => {
      input.checked = state.groups.get(input.dataset.group) !== false;
    });
    container.querySelectorAll('input[data-layer]').forEach((input) => {
      input.checked = state.layers.get(input.dataset.layer) !== false;
    });
  }

  function setIsolation(level) {
    state.isolate = level;
    apply();
  }

  function applyPreset(name, container) {
    const preset = PRESETS[name];
    if (!preset) return null;
    state.isolate = preset.isolate;
    for (const id of state.groups.keys()) {
      const on = preset.on === '*' ? true : preset.on.includes(id);
      state.groups.set(id, on && !(preset.off || []).includes(id));
    }
    for (const id of state.layers.keys()) {
      state.layers.set(id, preset.layers === '*' ? true : preset.layers.includes(id));
    }
    apply();
    if (container) syncInputs(container);
    return preset;
  }

  apply();

  return {
    mount,
    setIsolation,
    applyPreset,
    state,
    unmatched,
    groupCounts: () => new Map([...byGroup].map(([id, list]) => [id, list.length])),
    visibleParts: () => parts.filter((part) => part.visible),
  };
}

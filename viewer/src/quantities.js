/**
 * The quantities panel: `build/quantities.json`, rendered beside the thing it describes.
 *
 * Shipping the model with its own evidence is the point of the exercise, so this panel
 * copies rather than curates. Every published/computed pair the exporter wrote is shown,
 * including the ones that do not agree, and all seven of the exporter's notes are rendered
 * verbatim — they are where the awkward parts are explained (the 2.63 m² stair-run
 * deduction consumes published information; `solid_volume_sum` is not a material volume;
 * the scene bounding box is not the building).
 *
 * THE COLOURS ARE A READING AID, NOT A VERDICT
 * --------------------------------------------
 * Rows are tinted by |residual|. That is *not* the test suite's tolerance: the roof-area
 * check is a deliberate ±6 % sanity band (README, "Still open: the published roof area"),
 * building height is asserted at ±30 mm rather than a percentage, and Kotłownia and
 * Łazienka (12) are recorded, understood disagreements rather than regressions. The
 * authoritative tolerances live in `tests/`; the legend says so on screen.
 */

const BANDS = [
  [0.5, 'good'],
  [1.0, 'ok'],
  [6.0, 'warn'],
];

function bandOf(residual) {
  if (residual === null || residual === undefined) return '';
  const magnitude = Math.abs(residual);
  for (const [limit, name] of BANDS) if (magnitude < limit) return name;
  return 'bad';
}

function num(value, dp = 3) {
  if (value === null || value === undefined) return '—';
  return Number(value).toFixed(dp);
}

function deltaCell(residual) {
  if (residual === null || residual === undefined) return '<td class="delta">—</td>';
  const sign = residual >= 0 ? '+' : '';
  return `<td class="delta ${bandOf(residual)}">${sign}${residual.toFixed(2)} %</td>`;
}

function comparisonRows(rows) {
  return rows
    .filter((row) => row.value)
    .map(
      (row) =>
        `<tr><td>${row.label}</td>` +
        `<td class="num">${num(row.value.computed, row.dp ?? 3)}</td>` +
        `<td class="num">${num(row.value.published, row.dp ?? 2)}</td>` +
        deltaCell(row.value.residual_pct) +
        `<td>${row.unit}</td></tr>`,
    )
    .join('');
}

function table(caption, head, body) {
  return `<table class="q"><caption>${caption}</caption><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

const HEAD = '<th>Quantity</th><th>Computed</th><th>Published</th><th>Δ</th><th></th>';

export function renderQuantities(container, q, { onRoomClick } = {}) {
  const areas = q.areas_m2 || {};
  const html = [];

  html.push(
    table(
      'Global invariants — computed from spec/, compared with the project card',
      HEAD,
      comparisonRows([
        { label: 'Usable area (bez schodów)', value: areas.usable, unit: 'm²' },
        { label: 'Net area', value: areas.net, unit: 'm²' },
        { label: 'Floor area', value: areas.floor, unit: 'm²' },
        { label: 'Attic (pow. strychu)', value: areas.attic, unit: 'm²' },
        { label: 'Boiler room', value: areas.boiler, unit: 'm²' },
        { label: 'Footprint (pow. zabudowy)', value: areas.footprint, unit: 'm²' },
        { label: 'Cubature', value: (q.volumes_m3 || {}).cubature, unit: 'm³', dp: 2 },
        { label: 'Roof area', value: (q.roof || {}).area_m2, unit: 'm²', dp: 2 },
        { label: 'Building height (terrain → ridge)', value: (q.heights || {}).building_m, unit: 'm' },
        { label: 'Ridge above ground', value: (q.heights || {}).ridge_above_ground_m, unit: 'm' },
        { label: 'Eave (fascia underside)', value: (q.heights || {}).eave_above_ground_m, unit: 'm' },
      ]),
    ),
  );

  const levels = areas.by_level || {};
  const levelRows = Object.entries(levels)
    .map(([id, level]) => {
      const name = id === 'ground' ? 'Ground' : 'Attic';
      return (
        `<tr><td>${name} — floor area</td><td class="num">${num(level.floor_area?.computed)}</td>` +
        `<td class="num">${num(level.floor_area?.published, 2)}</td>` +
        deltaCell(level.floor_area?.residual_pct) +
        '<td>m²</td></tr>' +
        `<tr><td>${name} — usable (counted)</td><td class="num">${num(level.counted_area?.computed)}</td>` +
        `<td class="num">${num(level.counted_area?.published, 2)}</td>` +
        deltaCell(level.counted_area?.residual_pct) +
        '<td>m²</td></tr>'
      );
    })
    .join('');
  html.push(table('By storey', HEAD, levelRows));

  const roomRows = (q.rooms || [])
    .map((room, index) => {
      const shared = room.shared_face ? ' shared' : '';
      const names = room.names.join(' + ');
      return (
        `<tr class="clickable${shared}" data-room="${index}">` +
        `<td>${names} <span class="delta">${room.level === 'attic' ? 'A' : 'G'}</span></td>` +
        `<td class="num">${num(room.counted_area_m2)}</td>` +
        `<td class="num">${num(room.published_area_m2, 2)}</td>` +
        deltaCell(room.residual_pct) +
        '<td>m²</td></tr>'
      );
    })
    .join('');
  html.push(
    table(
      `Rooms — ${(q.rooms || []).length} polygonised faces for 18 published rooms`,
      '<th>Room</th><th>Counted</th><th>Published</th><th>Δ</th><th></th>',
      roomRows,
    ),
  );

  const elevations = q.elevations_m || {};
  const elevationRows = Object.entries(elevations)
    .map(
      ([key, value]) =>
        `<tr><td>${key.replace(/_/g, ' ')}</td><td class="num">${num(value)}</td><td></td><td></td><td>m</td></tr>`,
    )
    .join('');
  html.push(table('Elevations (0.00 = ground-floor level)', HEAD, elevationRows));

  const roof = q.roof || {};
  html.push(
    table(
      'Roof',
      '<th>Property</th><th>Value</th><th></th><th></th><th></th>',
      [
        ['Pitch', num(roof.pitch_deg, 1), '°'],
        ['Ridge axis', roof.ridge_axis, ''],
        ['Span', num(roof.span_m, 2), 'm'],
        ['Eaves overhang', num(roof.eaves_overhang_m, 2), 'm'],
        ['Verge overhang', num(roof.verge_overhang_m, 2), 'm'],
        ['Build-up (vertical, at wall face)', num(roof.roof_buildup_vertical_m, 2), 'm'],
        ['Fascia depth (at overhang edge)', num(roof.fascia_depth_m, 2), 'm'],
      ]
        .map(([k, v, u]) => `<tr><td>${k}</td><td class="num">${v}</td><td></td><td></td><td>${u}</td></tr>`)
        .join(''),
    ),
  );

  html.push(
    '<div class="legend">' +
      'Shading is by |Δ| only — <span class="swatch delta good">&lt;0.5 %</span> ' +
      '<span class="swatch delta ok">&lt;1 %</span> ' +
      '<span class="swatch delta warn">&lt;6 %</span> ' +
      '<span class="swatch delta bad">≥6 %</span> — and is a reading aid, not a pass/fail. ' +
      'The real tolerances are asserted in <code>tests/</code>: roof area is a deliberate ' +
      '±6 % sanity band, building height is ±30 mm, and Schody / Kotłownia / Łazienka (12) ' +
      'are recorded, diagnosed residuals rather than regressions.' +
      '</div>',
  );

  if (q.notes && q.notes.length) {
    html.push(
      '<div class="group"><div class="group-head">Exporter notes (verbatim)</div><ul class="notes">' +
        q.notes.map((note) => `<li>${note}</li>`).join('') +
        '</ul></div>',
    );
  }

  container.innerHTML = html.join('');

  if (onRoomClick) {
    container.querySelectorAll('tr[data-room]').forEach((row) => {
      row.addEventListener('click', () => onRoomClick(q.rooms[Number(row.dataset.room)]));
    });
  }
}

export function artifactLine(q) {
  const a = q.artifact || {};
  const mesh = q.mesh || {};
  const kib = a.bytes ? `${(a.bytes / 1024).toFixed(0)} KiB` : '?';
  return (
    `${a.path || 'build/model.glb'} · ${kib} · ${mesh.nodes} nodes · ` +
    `${mesh.vertices} vertices · ${mesh.faces} faces\nsha256 ${(a.sha256 || '').slice(0, 16)}… · ` +
    `schema ${q.schema}`
  );
}

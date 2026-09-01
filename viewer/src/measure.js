/**
 * Two-point measurement, in metres.
 *
 * This is the escape hatch the whole project is for: it lets someone with the printed plans
 * and no Python check any dimension against the model by hand. So it has to be *exact*, not
 * indicative:
 *
 * * It reads world coordinates directly. glTF is metres, the exporter writes no scale and
 *   this viewer applies none, so the number on screen is the number in `spec/` divided by
 *   1000 and nothing else has touched it.
 * * Corner snapping is on by default. A free-hand click lands wherever the ray met the
 *   triangle, which is fine for "roughly how wide" and useless for "is this 3.80 m". With
 *   snapping the click resolves to the nearest actual mesh vertex within 0.20 m, which for
 *   this model is a real corner of a real wall face, so the readout is the geometry rather
 *   than an estimate of it.
 * * Δx / Δy / Δz are shown alongside the straight-line distance, because plan dimension
 *   chains are axis-aligned and the single number is the one you least often want.
 *
 * Remember which faces you are snapping to. The exterior wall exports as three concentric
 * solids — 250 mm Porotherm, 200 mm EPS, 10 mm render — so "the outside of the wall" is
 * three different surfaces 460 mm apart in total. Turn the build-up layers off in the Layers
 * panel to measure to the face you mean. The published *pow. zabudowy* is measured to the
 * render, the dimension chains to the structure.
 */

import * as THREE from 'three';

const SNAP_RADIUS = 0.2;

export function createMeasure({ scene, labels }) {
  const group = new THREE.Group();
  group.name = 'measurements';
  scene.add(group);

  const lineMaterial = new THREE.LineBasicMaterial({ color: 0x7fb2d8, depthTest: false });
  const pointMaterial = new THREE.MeshBasicMaterial({ color: 0xffd489, depthTest: false });
  const pointGeometry = new THREE.SphereGeometry(0.035, 10, 8);

  const measurements = [];
  let pending = null;
  let snap = true;

  function marker(point) {
    const mesh = new THREE.Mesh(pointGeometry, pointMaterial);
    mesh.position.copy(point);
    mesh.renderOrder = 999;
    group.add(mesh);
    return mesh;
  }

  const _vertex = new THREE.Vector3();

  /**
   * Nearest vertex of the whole hit solid, if one is close enough to be the corner meant.
   *
   * Scanning the *object*, not just the hit triangle. Triangulation is arbitrary: a wall
   * face is two triangles spanning metres, so a click 15 cm from a corner frequently lands
   * on the triangle that does not contain that corner, and a triangle-local search would
   * silently decline to snap exactly when snapping matters most. These solids carry 8–64
   * vertices each, so the full scan costs nothing.
   */
  function snapped(hit) {
    if (!snap) return hit.point.clone();
    const position = hit.object.geometry.attributes.position;
    let best = null;
    let bestDistance = SNAP_RADIUS;
    for (let index = 0; index < position.count; index += 1) {
      _vertex.fromBufferAttribute(position, index).applyMatrix4(hit.object.matrixWorld);
      const distance = _vertex.distanceTo(hit.point);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = _vertex.clone();
      }
    }
    return best || hit.point.clone();
  }

  function addFromHit(hit) {
    const point = snapped(hit);
    if (!pending) {
      pending = { a: point, marker: marker(point) };
      return null;
    }
    const a = pending.a;
    const b = point;
    group.remove(pending.marker);
    pending = null;

    const geometry = new THREE.BufferGeometry().setFromPoints([a, b]);
    const line = new THREE.Line(geometry, lineMaterial);
    line.renderOrder = 998;
    group.add(line);

    const element = document.createElement('div');
    element.className = 'mlabel';
    labels.appendChild(element);

    const record = {
      a,
      b,
      line,
      markers: [marker(a), marker(b)],
      element,
      distance: a.distanceTo(b),
    };
    const d = new THREE.Vector3().subVectors(b, a);
    element.innerHTML =
      `<b>${record.distance.toFixed(3)} m</b>` +
      `<div class="sub">Δx ${d.x.toFixed(3)} · Δy ${d.y.toFixed(3)} · Δz ${d.z.toFixed(3)}` +
      ` · plan ${Math.hypot(d.x, d.y).toFixed(3)}</div>`;
    measurements.push(record);
    return record;
  }

  const projected = new THREE.Vector3();

  function update(camera, width, height) {
    for (const record of measurements) {
      projected.addVectors(record.a, record.b).multiplyScalar(0.5).project(camera);
      const visible = projected.z > -1 && projected.z < 1;
      record.element.style.display = visible ? '' : 'none';
      record.element.style.left = `${(projected.x * 0.5 + 0.5) * width}px`;
      record.element.style.top = `${(-projected.y * 0.5 + 0.5) * height}px`;
    }
  }

  function clear() {
    for (const record of measurements) {
      group.remove(record.line, ...record.markers);
      record.element.remove();
    }
    measurements.length = 0;
    if (pending) {
      group.remove(pending.marker);
      pending = null;
    }
  }

  function summary() {
    if (!measurements.length) {
      return pending
        ? 'First point placed — click the second.'
        : 'No measurements yet.';
    }
    return measurements
      .map((record, index) => {
        const d = new THREE.Vector3().subVectors(record.b, record.a);
        return (
          `<div><b>${index + 1}. ${record.distance.toFixed(3)} m</b> ` +
          `(Δx ${d.x.toFixed(3)}, Δy ${d.y.toFixed(3)}, Δz ${d.z.toFixed(3)})</div>`
        );
      })
      .join('');
  }

  return {
    addFromHit,
    clear,
    update,
    summary,
    measurements,
    setSnap(on) {
      snap = on;
    },
    get pending() {
      return pending;
    },
  };
}

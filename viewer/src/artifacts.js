/**
 * Locating the two build artifacts.
 *
 * `build/` sits beside `viewer/`, not inside it, so the viewer has to be served from the
 * repository root (`just viewer` does that) for `../build/model.glb` to resolve. Rather
 * than hard-fail when someone serves `viewer/` itself, we probe a short candidate list and
 * report every URL we tried — a wrong server root is the single most likely reason this
 * app shows nothing, and a silent 404 would be a miserable way to find that out.
 */

const MODEL_CANDIDATES = ['../build/model.glb', './build/model.glb', './model.glb'];
const QUANTITIES_CANDIDATES = [
  '../build/quantities.json',
  './build/quantities.json',
  './quantities.json',
];

function overrides() {
  const params = new URLSearchParams(location.search);
  return { model: params.get('model'), quantities: params.get('quantities') };
}

async function probe(candidates) {
  const tried = [];
  for (const url of candidates) {
    try {
      const response = await fetch(url, { method: 'HEAD' });
      if (response.ok) return { url, tried };
      tried.push(`${url} → HTTP ${response.status}`);
    } catch (error) {
      tried.push(`${url} → ${error.message}`);
    }
  }
  return { url: null, tried };
}

export async function resolveModelUrl() {
  const forced = overrides().model;
  if (forced) return { url: forced, tried: [] };
  return probe(MODEL_CANDIDATES);
}

export async function resolveQuantitiesUrl() {
  const forced = overrides().quantities;
  if (forced) return { url: forced, tried: [] };
  return probe(QUANTITIES_CANDIDATES);
}

export function notFoundMessage(what, tried) {
  return [
    `Could not find ${what}.`,
    '',
    'Tried:',
    ...tried.map((line) => `  ${line}`),
    '',
    'The viewer reads build/model.glb and build/quantities.json, which live one level',
    'above viewer/. Serve the REPOSITORY ROOT and open /viewer/, e.g.',
    '',
    '  cd <repo> && python3 -m http.server 8000',
    '  open http://localhost:8000/viewer/',
    '',
    'If the files are missing entirely, build them first:  just build',
    'Or point the viewer at them explicitly:  /viewer/?model=<url>&quantities=<url>',
  ].join('\n');
}

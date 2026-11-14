// DOM interaction tests. Real API smoke tests and controlled progress fixtures.
// GPU/canvas renderers are excluded; this does not replace browser visual QA.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const requireWeb = createRequire(resolve(web, 'package.json'));
const directory = mkdtempSync(resolve(tmpdir(), 'irradium-results-ui-'));
const server = process.env.IRRADIUM_TEST_SERVER ?? 'http://127.0.0.1:8001';
const api = `${server}/irradiators/IV/api`;
const nativeFetch = globalThis.fetch;
async function realGet(path) {
  const response = await nativeFetch(`${api}${path}`);
  assert.equal(response.status, 200, path);
  return response.json();
}
const { cases } = await realGet('/cases');
const statuses = await Promise.all(cases.map((item) => realGet(`/cases/${item.case_id}/monte-carlo`)));
const activeIndex = statuses.findIndex((job) => ['running', 'queued'].includes(job.state));
assert.ok(activeIndex >= 0, 'This live smoke test requires an existing running study; it never starts one.');
const liveCase = cases[activeIndex];
const liveJob = statuses[activeIndex];
const historical = cases.find((item, index) => item.workflow === 'suspended_litters' && !item.analysis_method && statuses[index].state === 'completed');
assert.ok(historical, 'A completed historical case is required to test switching.');
const liveRates = await realGet(`/route/cases/${liveCase.case_id}/results?run_id=${liveJob.run_id}`);
const { studies } = await realGet(`/route/cases/${liveCase.case_id}/studies`);
const liveStudy = studies.find((study) => study.run_id === liveJob.run_id);
assert.ok(liveRates.positions.length >= 6, 'At least six published positions are required for progress fixtures.');

await build({
  stdin: { contents: `export {default as Home} from './app/page'; export {default as Results} from './features/route/RouteResultsView';`, resolveDir: web, loader: 'tsx' },
  outfile: resolve(directory, 'components.mjs'), bundle: true, platform: 'node', format: 'esm', jsx: 'automatic',
  banner: { js: "import {createRequire as createNodeRequire} from 'node:module'; const require = createNodeRequire(import.meta.url);" },
  define: { 'process.env.NEXT_PUBLIC_IRRADIUM_API_URL': JSON.stringify(server) },
  plugins: [{ name: 'dom-renderer-boundary', setup(builder) {
    builder.onResolve({ filter: /\/(VtkDoseViewer|Isodose3DViewer|StepViewer|IrradiatorScene)$/ }, (args) => ({ path: args.path, namespace: 'renderer-boundary' }));
    builder.onLoad({ filter: /.*/, namespace: 'renderer-boundary' }, (args) => ({
      contents: `import {createElement} from 'react'; export default function Renderer(props) { return createElement('div', {'data-test-renderer':${JSON.stringify(args.path)}, 'data-field-count':props.route?.fields.length ?? props.primitives?.length ?? props.parts?.length ?? 0, 'data-map-url':props.url ?? ''}, 'GPU/canvas renderer excluded from DOM test'); }`,
      loader: 'js', resolveDir: web,
    }));
    builder.onResolve({ filter: /^(react|react-dom|lucide-react)(\/|$)/ }, (args) => ({ path: requireWeb.resolve(args.path), external: true }));
  } }],
});

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://127.0.0.1:3001/', pretendToBeVisual: true });
for (const key of ['window', 'document', 'Node', 'HTMLElement', 'Element', 'HTMLInputElement', 'HTMLSelectElement', 'Event', 'MouseEvent', 'DOMParser', 'CustomEvent']) {
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
}
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true });
Object.defineProperty(globalThis, 'localStorage', { value: dom.window.localStorage, configurable: true });
globalThis.requestAnimationFrame = window.requestAnimationFrame.bind(window);
globalThis.cancelAnimationFrame = window.cancelAnimationFrame.bind(window);
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.scrollTo = () => {};
HTMLElement.prototype.scrollIntoView = function () {};
// JSDOM has no native dialog top layer. Keep the open/close event contract;
// browser focus trapping and modal layout remain part of visual browser QA.
window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
window.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new Event('close')); };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = await import(pathToFileURL(requireWeb.resolve('react')).href);
const { createRoot } = await import(pathToFileURL(requireWeb.resolve('react-dom/client')).href);
const { Home, Results } = await import(pathToFileURL(resolve(directory, 'components.mjs')).href);
const requests = [], errors = [], checks = [];
// Freeze the live snapshot while still decoding its real, published VTI files.
// The background study may publish more maps during this test.
let fixture = { count: liveRates.positions.length, calculations: liveStudy.completed_calculations,
  position: liveRates.positions.length + 1, rateFactor: 1 };
const originalError = console.error;
console.error = (...args) => { errors.push(args.map(String).join(' ')); originalError(...args); };

globalThis.fetch = async (input, options = {}) => {
  const url = String(input), method = (options.method ?? 'GET').toUpperCase();
  const request = { url, method };
  requests.push(request);
  // No test may submit/cancel a run, edit a case, or change preferences.
  if (method === 'PUT' && url.endsWith('/preferences')) return Response.json({ theme: 'light' });
  if (method !== 'GET' && !url.endsWith('/estimate')) {
    request.forbidden = true;
    throw new Error(`Unexpected mutation: ${method} ${url}`);
  }
  if (fixture && method === 'GET') {
    const route = `${api}/route/cases/${liveCase.case_id}`;
    if (url === `${api}/cases/${liveCase.case_id}/monte-carlo`) return Response.json({ ...liveJob, state: 'running', position_number: fixture.position, position_name: `I${fixture.position}` });
    if (url === `${route}/studies`) return Response.json({ studies: [{ ...liveStudy, count: fixture.count, complete: false, completed_calculations: fixture.calculations, interval_precision: null }] });
    if (url.startsWith(`${route}/results?`)) {
      const positions = liveRates.positions.slice(0, fixture.count).map((point, index) => index === 0 ? { ...point, mean_kgy_h: point.mean_kgy_h * fixture.rateFactor } : point);
      return Response.json({ ...liveRates, positions, complete: false, completed_calculations: fixture.calculations, interval_precision: null });
    }
  }
  const response = await nativeFetch(input, options);
  request.status = response.status;
  return response;
};

let root;
const visible = (element) => element && !element.closest('[hidden]');
const button = (text) => [...document.querySelectorAll('button')].find((item) => visible(item) && item.textContent.trim() === text);
const navigation = (name) => document.querySelector(`button.nav-button[aria-label="${name}"]`);
const label = (name) => [...document.querySelectorAll('label')].find((item) => visible(item) && item.querySelector('span')?.textContent === name);
const field = (name) => label(name)?.querySelector('select');
const liveBar = () => document.querySelector('[aria-label="Seguimiento del estudio en curso"]');
async function waitFor(predicate, name, timeout = 25000) {
  const began = Date.now();
  while (Date.now() - began < timeout) {
    await React.act(async () => { await new Promise((done) => setTimeout(done, 100)); });
    if (predicate()) return;
  }
  throw new Error(`Timeout: ${name}`);
}
async function click(element) { assert.ok(element, 'Missing clickable control'); await React.act(async () => element.click()); }
async function select(element, value) {
  assert.ok(element, 'Missing selector');
  await React.act(async () => { element.value = String(value); element.dispatchEvent(new Event('change', { bubbles: true })); });
}
async function mount(component, props) {
  if (root) await React.act(async () => root.unmount());
  document.getElementById('root').replaceChildren();
  root = createRoot(document.getElementById('root'));
  await React.act(async () => root.render(React.createElement(component, props)));
}
function passed(name) { checks.push(name); console.log(`PASS ${name}`); }
const props = { preferredCaseId: historical.case_id, revision: 0, visible: true, onInspect() {}, onSelectCase() {} };

try {
  await mount(Home);
  await waitFor(() => button('Entrar en Categoría IV'), 'irradiator selector');
  await click(button('Entrar en Categoría IV'));
  await waitFor(() => navigation('Resultados'), 'workspace');
  await click(navigation('Resultados'));
  await waitFor(() => liveBar() && field('Estudios guardados')?.value === liveJob.run_id, 'live results through main navigation');
  assert.equal(document.querySelector('.result-case-select select').value, liveCase.case_id);
  assert.match(liveBar().textContent, liveRates.positions.length >= liveStudy.requested_position_count ? /Primera estimación completa/ : /Posiciones ya calculadas/);
  assert.equal(liveBar().querySelector('progress').hasAttribute('value'), !liveStudy.accumulated_error_target || liveRates.positions.length < liveStudy.requested_position_count);
  passed('Home → Category IV → Results: current case, progress, rates and integration selectors');
  await waitFor(() => document.querySelector('.study-dose-scene [data-field-count]')?.getAttribute('data-field-count') === String(liveRates.positions.length), 'all real VTI maps decoded', 45000);
  passed('All real position maps decode successfully before reaching the GPU renderer');
  assert.equal(document.querySelector('.route-study input[type="date"]').value, liveJob.route_study.irradiation_date);
  passed('The running study displays its actual irradiation date after reloading');
  assert.equal(button('Irradiador 3D').getAttribute('aria-selected'), 'true');
  assert.equal(field('Posición final para integrar'), undefined);
  await click(button('Por posición'));
  await waitFor(() => field('Posición final para integrar'), 'position analysis tab');
  await click(button('Dosis y tiempos'));
  await waitFor(() => field('Dato de entrada'), 'dose and time tab');
  await click(button('Por posición'));
  passed('Workspace tabs expose one task at a time while preserving interval controls');
  await click(button('Mostrar todo'));
  assert.ok(visible(document.getElementById('study-scene-panel')));
  assert.ok(visible(document.getElementById('study-position-panel')));
  assert.ok(visible(document.getElementById('study-dose-panel')));
  assert.equal(localStorage.getItem('irradium:IV:results-layout'), 'stacked');
  await click(button('Usar pestañas'));
  assert.equal(localStorage.getItem('irradium:IV:results-layout'), 'tabs');
  passed('The layout button displays all three result sections together or restores tabs');
  const transportBar = liveBar().querySelector('.study-phase-progress progress');
  assert.equal(transportBar.hasAttribute('value'), true);
  assert.ok(!liveBar().querySelector('.study-phase-progress').textContent.includes('Fase 2'));
  passed('Fixed-budget transport reports coverage without claiming automatic refinement');

  for (const name of ['Carga CAD', 'Casos de irradiación', 'Fuentes', 'Parámetros de simulación', 'Posiciones', 'Calibración del simulador']) {
    await click(navigation(name));
    await waitFor(() => navigation(name)?.getAttribute('aria-current') === 'page', name);
    await waitFor(() => document.querySelector('[aria-label="Simulación activa"]'), `persistent simulation ribbon in ${name}`);
    if (name === 'Carga CAD') {
      await waitFor(() => document.querySelector('.saved-load-facts'), 'saved STEP dimensions and components');
      assert.match(document.querySelector('.saved-load-facts').textContent, /500.+400.+1[.,]?000/);
      await select(document.querySelector('#saved-load-case'), historical.case_id);
      await waitFor(() => document.querySelector('.saved-load-facts'), 'historical STEP geometry');
      await click(button('Preparar carga'));
      await waitFor(() => document.querySelector('.cad-empty-state'), 'helpful empty CAD viewport');
      assert.match(document.querySelector('.step-materials-panel').textContent, /Pendiente de geometría/);
      await click(button('Cargas guardadas'));
    } else if (name === 'Casos de irradiación') {
      await waitFor(() => [...document.querySelectorAll('.case-rows .case-row')].filter(visible).length > 0, 'saved case rows');
      assert.equal(document.querySelector('.new-case-panel').open, false);
      const search = document.querySelector('[aria-label="Buscar casos guardados"]');
      await React.act(async () => {
        Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(search, 'NO-CASE-MATCH');
        search.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await waitFor(() => document.querySelectorAll('.case-rows .case-row').length === 0, 'case filtering');
      await click(button('Limpiar búsqueda'));
      await click(button('Nuevo caso'));
      assert.equal(document.querySelector('.new-case-panel').open, true);
    } else if (name === 'Fuentes') {
      await waitFor(() => document.querySelectorAll('.rack-source').length > 1, 'source inventory');
      const source = document.querySelectorAll('.rack-source')[1];
      await click(source);
      assert.ok(source.classList.contains('selected'));
    } else if (name === 'Parámetros de simulación') {
      await waitFor(() => field('Alcance predeterminado'), 'configured route');
      const positionEditor = [...document.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent.startsWith('Posiciones de cálculo'));
      assert.equal(positionEditor.open, false);
      await click(positionEditor.querySelector('summary'));
      assert.equal(positionEditor.open, true);
      assert.match(field('Alcance predeterminado').options[0].textContent, /56 posiciones/);
      await select(field('Alcance predeterminado'), 'selected');
      await waitFor(() => field('Posición del recorrido'), 'position dropdown');
      assert.equal(field('Posición del recorrido').options.length, 57);
      assert.ok([...field('Posición del recorrido').options].some(option => /I8.*-180/.test(option.textContent)));
    } else if (name === 'Posiciones') {
      await waitFor(() => [...document.querySelectorAll('tbody tr')].filter(visible).length === 56, '56 route geometry rows');
    } else if (name === 'Calibración del simulador') {
      await waitFor(() => document.querySelector('[aria-label="Composición elemental del aire"]'), 'air calibration composition');
      await click(document.querySelector('[aria-label="Editar calibración"]'));
      await waitFor(() => document.querySelector('[aria-label="Cerrar"]'), 'calibration editor');
      await click(document.querySelector('[aria-label="Cerrar"]'));
    }
    passed(`Main menu: ${name}`);
  }
  assert.equal(navigation('Secciones de trabajo'), null);
  await click(button('Ver resultados'));
  await waitFor(() => liveBar() && field('Posición final para integrar'), 'results after visiting all menu tabs');
  assert.equal(document.querySelector('.result-case-select select').value, liveCase.case_id);
  passed('Returning from other menu tabs preserves the live study');

  fixture = { count: 1, calculations: 1, position: 2, rateFactor: 1 };
  await mount(Results, props);
  await waitFor(() => button('Por posición'), 'workspace tabs');
  await click(button('Por posición'));
  await waitFor(() => liveBar() && field('Posición final para integrar')?.options.length === 1, 'first published result');
  assert.equal(field('Posición final para integrar').value, '0');
  passed('First published position is the only available integration position');

  fixture = { count: 3, calculations: 3, position: 4, rateFactor: 1 };
  await waitFor(() => field('Posición final para integrar')?.options.length === 3, 'automatic interval growth');
  assert.equal(field('Posición final para integrar').value, '2');
  assert.equal(field('Posición inicial para integrar').value, '0');
  passed('New positions automatically update both selectors and extend the complete interval');

  await select(field('Posición inicial para integrar'), '1');
  await select(field('Posición final para integrar'), '2');
  fixture = { count: 5, calculations: 5, position: 6, rateFactor: 1 };
  await waitFor(() => field('Posición final para integrar')?.options.length === 5, 'manual interval while new positions arrive');
  assert.equal(field('Posición inicial para integrar').value, '1');
  assert.equal(field('Posición final para integrar').value, '2');
  passed('A manual integration interval remains selected while new options appear');

  const follow = [...document.querySelectorAll('label')].find((item) => item.textContent.includes('Seguir el intervalo disponible')).querySelector('input');
  await click(follow);
  assert.equal(field('Posición inicial para integrar').value, '0');
  assert.equal(field('Posición final para integrar').value, '4');
  passed('Following the available interval restores its current full bounds');

  const previousRate = document.querySelector('.route-rate-table tbody tr td:nth-child(2)').textContent;
  fixture = { count: 5, calculations: 6, position: 6, rateFactor: 1.2 };
  await waitFor(() => document.querySelector('.route-rate-table tbody tr td:nth-child(2)')?.textContent !== previousRate, 'periodic refresh during the same position', 10000);
  passed('Rates refresh without clicking Update even when the position number is unchanged');

  await click(button('Irradiador 3D'));
  await waitFor(() => document.querySelector('.study-dose-scene [data-field-count]')?.getAttribute('data-field-count') === '5', 'cached preview ready');
  const viewerBeforePublication = document.querySelector('.study-dose-scene [data-field-count]');
  fixture = { count: 6, calculations: 7, position: 7, rateFactor: 1.2 };
  await waitFor(() => document.querySelector('.study-dose-scene [data-field-count]')?.getAttribute('data-field-count') === '6', 'new map in mounted scene');
  assert.equal(document.querySelector('.study-dose-scene [data-field-count]'), viewerBeforePublication,
    'Publishing a position unmounted the viewer and reset its controls');
  fixture = { count: 5, calculations: 8, position: 6, rateFactor: 1.2 };
  await waitFor(() => document.querySelector('.study-dose-scene [data-field-count]')?.getAttribute('data-field-count') === '5', 'restored progress fixture');
  passed('Publishing another position preserves the mounted 3D viewer and its controls');
  const mapsBeforeSwitch = requests.filter((request) => /\/maps\/\d+$/.test(request.url)).length;
  await click(button('Por posición'));
  await click(button('Irradiador 3D'));
  await waitFor(() => document.querySelector('.study-dose-scene [data-field-count]')?.getAttribute('data-field-count') === '5', 'cached preview after switching');
  assert.equal(requests.filter((request) => /\/maps\/\d+$/.test(request.url)).length, mapsBeforeSwitch);
  await click(button('Por posición'));
  passed('Switching workspaces reuses loaded dose maps instead of downloading them again');

  const details = document.querySelector('.route-study details');
  await React.act(async () => { details.open = false; details.dispatchEvent(new Event('toggle')); });
  assert.ok(liveBar());
  assert.equal(liveBar().closest('details'), null);
  passed('Progress remains outside the collapsed configuration panel');

  await mount(Results, props);
  await waitFor(() => button('Por posición'), 'workspace tabs after reload');
  await click(button('Por posición'));
  await waitFor(() => liveBar() && field('Posición final para integrar')?.options.length === 5, 'reload reconnects to the current study');
  assert.equal(field('Estudios guardados').value, liveJob.run_id);
  assert.equal(field('Posición final para integrar').value, '4');
  passed('Reload reconnects to the same execution and restores all published positions');

  await select(document.querySelector('.result-case-select select'), historical.case_id);
  await waitFor(() => button('Seguir simulación en curso'), 'historical case selected');
  await waitFor(() => document.querySelector('[aria-label="Simulación activa"]'), 'progress persists while inspecting historical results');
  await click(button('Seguir simulación en curso'));
  await waitFor(() => liveBar() && field('Estudios guardados')?.value === liveJob.run_id, 'return to running study');
  passed('Historical inspection can return to the running simulation');

  await click(button('Mostrar todo'));
  await mount(Results, props);
  await waitFor(() => button('Usar pestañas'), 'continuous layout preference restored');
  assert.ok(visible(document.getElementById('study-scene-panel')));
  assert.ok(visible(document.getElementById('study-position-panel')));
  assert.ok(visible(document.getElementById('study-dose-panel')));
  await click(button('Usar pestañas'));
  passed('The selected result layout survives unmounting and reloading');

  assert.ok(!requests.some((request) => request.forbidden), 'A component attempted an unexpected mutation');
  assert.deepEqual(requests.filter((request) => request.status >= 400), [], 'API request failures occurred');
  let after;
  await React.act(async () => { after = await realGet(`/cases/${liveCase.case_id}/monte-carlo`); });
  assert.deepEqual(errors, [], 'React/runtime errors occurred');
  assert.equal(after.run_id, liveJob.run_id);
  assert.equal(after.state, 'running');
  passed('No simulation submission/cancellation and no React runtime errors');
} finally {
  writeFileSync(resolve(directory, 'dom.html'), document.documentElement.outerHTML);
  if (root) await React.act(async () => root.unmount());
  writeFileSync(resolve(directory, 'report.json'), JSON.stringify({ checks, requests, errors, live_case: liveCase.case_id, run_id: liveJob.run_id, excluded: ['GPU/canvas rendering', 'browser visual layout'] }, null, 2));
  console.log(`Functional report: ${directory}/report.json`);
  dom.window.close();
}

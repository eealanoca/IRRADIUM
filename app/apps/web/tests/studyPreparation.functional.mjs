// Exercises a new run before its first map exists. All mutations are fixtures.
// Configuration is read from the local API; no calculation is submitted to it.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const requireWeb = createRequire(resolve(web, 'package.json'));
const directory = mkdtempSync(resolve(tmpdir(), 'irradium-preparation-'));
const server = process.env.IRRADIUM_TEST_SERVER ?? 'http://127.0.0.1:8001';
const api = `${server}/irradiators/IV/api`;
const nativeFetch = globalThis.fetch;
async function read(path) {
  const response = await nativeFetch(`${api}${path}`);
  assert.equal(response.status, 200, path);
  return response.json();
}
const { cases } = await read('/cases');
const source = cases.find(item => item.workflow === 'suspended_litters' && !item.analysis_method);
assert.ok(source, 'A saved route case is needed for the read-only configuration fixture.');
const configuration = await read(`/route/cases/${source.case_id}/study`);
const testCase = { ...source, case_id: 'preparation-fixture', name: 'Preparation regression fixture' };
const route = `${api}/route/cases/${testCase.case_id}`;
const jobPath = `${api}/cases/${testCase.case_id}/monte-carlo`;
await build({
  stdin: { contents: "export {default} from './features/route/RouteResultsView';", resolveDir: web, loader: 'tsx' },
  outfile: resolve(directory, 'component.mjs'), bundle: true, platform: 'node', format: 'esm', jsx: 'automatic',
  banner: { js: "import {createRequire as createNodeRequire} from 'node:module'; const require = createNodeRequire(import.meta.url);" },
  define: { 'process.env.NEXT_PUBLIC_IRRADIUM_API_URL': JSON.stringify(server) },
  plugins: [{ name: 'exclude-gpu', setup(builder) {
    builder.onResolve({ filter: /\/(VtkDoseViewer|Isodose3DViewer|StepViewer|IrradiatorScene)$/ }, args => ({ path: args.path, namespace: 'gpu' }));
    builder.onLoad({ filter: /.*/, namespace: 'gpu' }, () => ({ contents: 'export default function Renderer(){return null}', loader: 'js' }));
    builder.onResolve({ filter: /^(react|react-dom|lucide-react)(\/|$)/ }, args => ({ path: requireWeb.resolve(args.path), external: true }));
  } }],
});
const dom = new JSDOM('<div id="root"></div>', { url: 'http://127.0.0.1:3001/', pretendToBeVisual: true });
for (const key of ['window','document','Node','HTMLElement','Element','HTMLInputElement','HTMLSelectElement','Event','MouseEvent','DOMParser','CustomEvent']) {
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
}
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true });
Object.defineProperty(globalThis, 'localStorage', { value: dom.window.localStorage, configurable: true });
globalThis.requestAnimationFrame = window.requestAnimationFrame.bind(window);
globalThis.cancelAnimationFrame = window.cancelAnimationFrame.bind(window);
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
HTMLElement.prototype.scrollIntoView = function() {};
const React = await import(pathToFileURL(requireWeb.resolve('react')).href);
const { createRoot } = await import(pathToFileURL(requireWeb.resolve('react-dom/client')).href);
const { default: Results } = await import(pathToFileURL(resolve(directory,'component.mjs')).href);
let job = { case_id: testCase.case_id, state: 'not-started', execution_enabled: true };
let submissions = 0;
const resultRequests = [];
globalThis.fetch = async (input, options = {}) => {
  const url = String(input), method = options.method ?? 'GET';
  if (url === `${api}/cases` && method === 'GET') return Response.json({ cases: [testCase] });
  if (url === jobPath && method === 'GET') return Response.json(job);
  if (url === `${route}/study` && method === 'GET') return Response.json(configuration);
  if (url === `${route}/study` && method === 'POST') {
    submissions++;
    job = { case_id: testCase.case_id, run_id: 'pending-run', state: 'running', stage: 'preparing-step-and-dagmc', position_number: 1, position_name: 'I1', position_count: 56, route_study: JSON.parse(options.body) };
    return Response.json(job);
  }
  if (url === `${route}/studies`) return Response.json({ studies: [] });
  if (url === `${route}/estimate`) return Response.json({ cells: 1600000, estimated_ram_mb: 1800, memory_limit_mb: 4096, feasible: true });
  if (url.startsWith(`${route}/results?`)) {
    resultRequests.push(url);
    return Response.json({ detail: 'Estudio no encontrado' }, { status: 404 });
  }
  throw new Error(`Unexpected fixture request: ${method} ${url}`);
};
let root;
async function waitFor(predicate, message) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    await React.act(async () => { await new Promise(done => setTimeout(done, 50)); });
    if (predicate()) return;
  }
  assert.fail(`${message}: ${document.body.textContent}`);
}
async function mount() {
  if (root) await React.act(async () => root.unmount());
  document.getElementById('root').replaceChildren();
  root = createRoot(document.getElementById('root'));
  await React.act(async () => root.render(React.createElement(Results, { preferredCaseId: testCase.case_id, revision: 0, visible: true, onInspect() {} })));
}
try {
  await mount();
  await waitFor(() => [...document.querySelectorAll('button')].some(b => b.textContent === 'Calcular tasas' && !b.disabled), 'Calculation form did not become ready');
  const start = [...document.querySelectorAll('button')].find(b => b.textContent === 'Calcular tasas');
  await React.act(async () => start.click());
  await waitFor(() => document.querySelector('[aria-label="Seguimiento del estudio en curso"]'), 'Progress card missing after submission');
  await React.act(async () => { await new Promise(done => setTimeout(done, 200)); });
  assert.equal(submissions, 1);
  assert.deepEqual(resultRequests, [], 'A not-yet-published archive was fetched immediately after submitting');
  assert.ok(!document.body.textContent.includes('Estudio no encontrado'));
  console.log('PASS New runs display preparation and progress without a false missing-study error');
  job.route_study = { ...job.route_study, cell_size_cm: 0.5, memory_limit_mb: 6144, time_limit_seconds: 86400, extended_time: true };
  await mount();
  await waitFor(() => document.querySelector('[aria-label="Seguimiento del estudio en curso"]'), 'Progress card lost after remount');
  assert.equal(submissions, 1, 'Reloading the results component submitted another simulation');
  assert.deepEqual(resultRequests, []);
  assert.ok(document.body.textContent.includes('I1'));
  const field = name => [...document.querySelectorAll('label')].find(label => label.querySelector('span')?.textContent === name)?.querySelector('input');
  assert.equal(field('Malla del estudio [cm]')?.value, '0.5');
  assert.equal(field('Tiempo máximo del estudio [min]')?.value, '1440');
  assert.equal(field('RAM máxima del estudio [MiB]')?.value, '6144');
  console.log('PASS Reload restores the same unpublished run without restarting it');
  console.log('PASS Reload displays the actual running mesh, memory and time budget');
  job = { ...job, state: 'cancelled', can_resume: true, execution_enabled: true,
    route_study: { ...job.route_study, particles: 25000001, irradiation_date: '2026-11-13' } };
  await mount();
  await waitFor(() => field('Historias por posición')?.value === '25000001', 'Stopped study settings were not restored');
  const setValue = (input, value) => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  };
  await React.act(async () => setValue(field('Historias por posición'), '25000000'));
  await React.act(async () => setValue(field('Fecha de irradiación'), '2026-11-12'));
  assert.equal(field('Historias por posición').value, '25000000', 'Editable draft was replaced with the resumable run budget');
  assert.equal(job.route_study.particles, 25000001, 'Editing a new run changed the old resumable request');
  await waitFor(() => [...document.querySelectorAll('button')].some(b => b.textContent === 'Calcular tasas' && !b.disabled), 'Edited configuration is not ready');
  await React.act(async () => [...document.querySelectorAll('button')].find(b => b.textContent === 'Calcular tasas').click());
  await waitFor(() => submissions === 2, 'New study was not submitted');
  assert.equal(job.route_study.particles, 25000000);
  assert.equal(job.route_study.irradiation_date, '2026-11-12');
  console.log('PASS A new run from a stopped study submits exactly the edited date and history budget');
} finally {
  if (root) await React.act(async () => root.unmount());
  globalThis.fetch = nativeFetch;
  dom.window.close();
}

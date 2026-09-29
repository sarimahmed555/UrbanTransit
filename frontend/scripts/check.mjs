import { readdir, readFile } from 'node:fs/promises';
import { join, relative } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const required = [
  'index.html',
  'src/main.js',
  'src/app.js',
  'src/styles.css',
  'src/navigation.js',
  'src/api/contracts.js',
  'src/api/index.js',
  'src/api/filters.js',
  'src/api/map-adapter.js',
  'src/api/client.js',
  'src/api/view-models.js',
  'src/data/demo-data.js',
  'src/data/page-data.js',
  'src/pages/index.js',
];

async function javascriptFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await javascriptFiles(path)));
    else if (entry.name.endsWith('.js')) files.push(path);
  }
  return files;
}

for (const file of required) {
  try {
    await readFile(join(root, file));
  } catch {
    throw new Error(`Missing required frontend file: ${file}`);
  }
}

const jsFiles = await javascriptFiles(join(root, 'src'));
for (const file of jsFiles) {
  const result = spawnSync(process.execPath, ['--check', file], { encoding: 'utf8' });
  if (result.status !== 0) {
    throw new Error(`Syntax check failed for ${relative(root, file)}\n${result.stderr}`);
  }
}

const contracts = await readFile(join(root, 'src/api/contracts.js'), 'utf8');
if ((contracts.match(/state: 'contract-only'/g) ?? []).length < 10) {
  throw new Error('API contracts are not explicitly marked as contract-only.');
}

const demo = await readFile(join(root, 'src/data/demo-data.js'), 'utf8');
if (!demo.includes('DEMO / MOCK') || !demo.includes('not production')) {
  throw new Error('Demo fixtures are missing their explicit non-production marker.');
}

const pageIndex = await readFile(join(root, 'src/pages/index.js'), 'utf8');
for (const page of ['executive', 'demand', 'routes-stops', 'delays', 'occupancy', 'forecasting', 'clustering', 'passenger-flow', 'what-if', 'recommendations', 'data-quality', 'system']) {
  if (!pageIndex.includes(page)) throw new Error(`Page registry is missing ${page}.`);
}

const { renderPage } = await import('../src/pages/index.js');
const landingMarkup = renderPage('home', 'api');
if (!landingMarkup.includes('DASHBOARD ↗') || !landingMarkup.includes('href="#/executive"') || !landingMarkup.includes('LIVE NETWORK MODEL')) {
  throw new Error('Landing page must provide the dashboard navigation link and compact live network model.');
}
if (/<canvas|landing-map|animateMotion/.test(landingMarkup)) {
  throw new Error('Landing page must stay free of a large animated transit graphic.');
}
if (/data-form="(?:sign-in|create-account)"|type="password"|Authentication service is not configured or ready/i.test(landingMarkup)) {
  throw new Error('Landing page must not render authentication controls or service warnings.');
}
const pages = ['executive', 'demand', 'routes-stops', 'delays', 'occupancy', 'forecasting', 'clustering', 'passenger-flow', 'what-if', 'recommendations', 'data-quality', 'system'];
for (const mode of ['demo', 'api']) {
  for (const page of pages) {
    const markup = renderPage(page, mode);
    if (typeof markup !== 'string' || markup.length < 200) {
      throw new Error(`Page render smoke failed for ${page}/${mode}.`);
    }
    if (mode === 'api') {
      const leakedDemoValue = ['18,420', '87.4%', '128.6k', 'R-101', 'R-107', 'R-214', '+6.8%'].find((value) => markup.includes(value));
      if (leakedDemoValue) throw new Error(`Demo value leaked into API mode on ${page}: ${leakedDemoValue}`);
    }
  }
}

const domRoot = { innerHTML: '' };
globalThis.document = { title: '', querySelector: () => domRoot, addEventListener: () => {} };
globalThis.location = { hash: '#/executive' };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.requestAnimationFrame = (callback) => callback();
globalThis.window = { location: globalThis.location, scrollTo: () => {} };
const bootRequests = [];
globalThis.fetch = async (input) => {
  const url = new URL(input);
  bootRequests.push(url.pathname);
  if (url.pathname.endsWith('/config')) return { ok: true, json: async () => ({ demo_mode: false }) };
  const data = url.pathname.endsWith('/health') || url.pathname.endsWith('/status')
    ? { dataset_version: 'production-v1.1' }
    : [];
  return { ok: true, json: async () => ({ status: 'ready', data, meta: { source: 'check-fixture' } }) };
};
const { mountApp } = await import('../src/app.js');
await mountApp();
if (!domRoot.innerHTML.includes('main-content')) throw new Error('Application shell mount smoke failed.');
await new Promise((resolve) => setTimeout(resolve, 0));
if (globalThis.urbanTransitApp.state.mode !== 'api') throw new Error('Fresh frontend sessions must use API/evidence mode.');
if (globalThis.urbanTransitApp.state.apiConnection !== 'connected') throw new Error('Application did not reflect the ready evidence API response.');
if (globalThis.urbanTransitApp.state.localPreview) throw new Error('A non-loopback hostname must not receive local preview routing.');
if (globalThis.urbanTransitApp.state.activePage !== 'login' || !domRoot.innerHTML.includes('data-form="sign-in"')) {
  throw new Error('Unauthenticated application startup must render the sign-in page.');
}
if (!bootRequests.includes('/api/health') || bootRequests.some((path) => path.startsWith('/api/v1/evidence/'))) {
  throw new Error('Unauthenticated startup must check health without requesting dashboard evidence.');
}

const originalFetch = globalThis.fetch;
const demoBootRequests = [];
globalThis.location.hash = '#/executive';
globalThis.location.hostname = 'localhost';
globalThis.fetch = async (input) => {
  const url = new URL(input);
  demoBootRequests.push(url.pathname);
  if (url.pathname.endsWith('/config')) return { ok: true, json: async () => ({ demo_mode: true }) };
  const data = url.pathname.endsWith('/health')
      ? { dataset_version: 'production-v1.1' }
      : [];
  return { ok: true, json: async () => ({ status: 'ready', data, meta: { source: 'check-fixture' } }) };
};
const demoWindowListeners = {};
globalThis.window.addEventListener = (name, callback) => { demoWindowListeners[name] = callback; };
domRoot.focus = () => {};
const { mountApp: mountDemoApp } = await import('../src/app.js?local-preview-check');
await mountDemoApp();
if (globalThis.urbanTransitApp.state.activePage !== 'executive') throw new Error('Loopback preview must open directly on the Executive Dashboard.');
if (!globalThis.urbanTransitApp.state.localPreview || globalThis.urbanTransitApp.state.session || !demoBootRequests.includes('/api/config')) throw new Error('Local preview must be loopback-scoped and must not create a session.');
if (!demoBootRequests.includes('/api/v1/evidence/status') || !demoBootRequests.includes('/api/v1/evidence/tasks')) throw new Error('Local Executive preview must load existing evidence API endpoints.');
for (const page of ['demand', 'routes-stops', 'delays', 'occupancy', 'forecasting', 'recommendations', 'data-quality', 'system']) {
  globalThis.location.hash = `#/${page}`;
  demoWindowListeners.hashchange();
  if (globalThis.urbanTransitApp.state.activePage !== page) throw new Error(`Demo mode blocked the ${page} route.`);
}
globalThis.location.hash = '#/login';
demoWindowListeners.hashchange();
if (globalThis.urbanTransitApp.state.activePage !== 'login' || !domRoot.innerHTML.includes('data-form="sign-in"')) {
  throw new Error('Local preview must preserve the existing login route and form implementation.');
}
if (globalThis.urbanTransitApp.state.session) throw new Error('Local preview must not fabricate an authenticated session.');
globalThis.fetch = originalFetch;

// ---------------------------------------------------------------------------
// Network map: data flow from contract to rendered geometry, and mock isolation
// ---------------------------------------------------------------------------
const { NETWORK_GEOMETRY_CONTRACT } = await import('../src/api/contracts.js');
const { normalizeNetworkGeometry, projectNetworkGeometry, connectGeometryAdapter, createMapAdapter } =
  await import('../src/api/map-adapter.js');
const { hasNetworkGeometry } = await import('../src/api/view-models.js');
const { networkMap } = await import('../src/components/map.js');
const { renderPage: renderMapPage } = await import('../src/pages/index.js');

function assert(condition, message) {
  if (!condition) throw new Error(`Map check failed: ${message}`);
}

// A payload built only from declared contract fields. The numbers are arbitrary
// in-range test inputs chosen to exercise projection; they are not transit data.
function geometryPayload(overrides = {}) {
  return {
    available: true,
    asOf: '2026-04-01T00:00:00Z',
    datasetVersion: 'fixture-not-production',
    routes: [
      { routeId: 'route-a', routeCode: 'RA-1', name: 'Alpha', mode: 'BUS', coordinates: [[73.0, 33.0], [73.1, 33.1], [73.2, 33.05]] },
      { routeId: 'route-b', routeCode: 'RB-2', name: 'Beta', mode: 'TRAM', coordinates: [[73.05, 33.15], [73.15, 33.2]] },
    ],
    stops: [
      { stopId: 'stop-a', name: 'Alpha Terminus', latitude: 33.0, longitude: 73.0 },
      { stopId: 'stop-b', name: 'Beta Junction', latitude: 33.15, longitude: 73.05 },
    ],
    ...overrides,
  };
}

// --- contract shape ---------------------------------------------------------
for (const field of NETWORK_GEOMETRY_CONTRACT.required) {
  assert(field in geometryPayload(), `contract required field ${field} is not produced by the fixture`);
}
assert(NETWORK_GEOMETRY_CONTRACT.state === 'contract-only', 'geometry contract must stay marked contract-only');
assert(hasNetworkGeometry(geometryPayload()), 'valid geometry must satisfy the view-model shape check');
assert(!hasNetworkGeometry({ available: false, routes: [], stops: [] }), 'unavailable geometry must fail the shape check');
assert(!hasNetworkGeometry(null), 'absent geometry must fail the shape check');

// --- no fabrication: unusable coordinates are rejected, not repaired --------
const base = geometryPayload();
const rejection = normalizeNetworkGeometry({
  ...base,
  routes: [
    ...base.routes,
    { routeId: 'route-degenerate', coordinates: [[73.3, 33.3]] },
    { routeId: 'route-badcoord', coordinates: [[73.3, 999], [73.4, 33.4]] },
    { routeCode: 'no-id', coordinates: [[73.3, 33.3], [73.4, 33.4]] },
  ],
  stops: [
    ...base.stops,
    { stopId: 'stop-nocoord', latitude: null, longitude: 73.0 },
    { stopId: 'stop-nan', latitude: Number.NaN, longitude: 73.0 },
    { name: 'no stop id', latitude: 33.0, longitude: 73.0 },
  ],
});
assert(rejection.state === 'ready', 'a partially valid response is still renderable');
assert(rejection.counts.routes === 2, `expected 2 usable routes, got ${rejection.counts.routes}`);
assert(rejection.counts.stops === 2, `expected 2 usable stops, got ${rejection.counts.stops}`);
assert(rejection.counts.rejectedRoutes === 3, `expected 3 rejected routes, got ${rejection.counts.rejectedRoutes}`);
assert(rejection.counts.rejectedStops === 3, `expected 3 rejected stops, got ${rejection.counts.rejectedStops}`);
assert(rejection.warnings.length > 0, 'rejected records must be reported, not silently dropped');
const drawable = JSON.stringify(rejection.routes) + JSON.stringify(rejection.stops);
assert(!drawable.includes('999'), 'an out-of-range coordinate leaked into drawable geometry');
assert(!drawable.includes('route-degenerate'), 'a one-point route was not rejected');

// --- explicit states --------------------------------------------------------
const unavailablePayload = { available: false, routes: [], stops: [], reason: 'No certified geometry.' };
const stateCases = [
  [normalizeNetworkGeometry(null), 'not_configured'],
  [normalizeNetworkGeometry(undefined), 'not_configured'],
  [normalizeNetworkGeometry(unavailablePayload), 'unavailable'],
  [normalizeNetworkGeometry({ available: true, routes: [], stops: [] }), 'empty'],
  [normalizeNetworkGeometry('nonsense'), 'not_configured'],
];
for (const [result, expected] of stateCases) {
  assert(result.state === expected, `expected state ${expected}, got ${result.state}`);
  assert(result.routes.length === 0 && result.stops.length === 0, `${expected} must carry no geometry`);
  assert(result.warnings.length > 0, `${expected} must explain itself`);
}
assert(normalizeNetworkGeometry(unavailablePayload).warnings[0] === 'No certified geometry.',
  'the service reason must be surfaced verbatim');
assert(normalizeNetworkGeometry({ routes: base.routes, stops: base.stops }).state !== 'ready',
  'geometry without available:true must not be treated as drawable');

// --- projection derives from the supplied bounds only -----------------------
const projection = projectNetworkGeometry(rejection.routes, rejection.stops);
assert(!projection.degenerate, 'valid geometry must project');
assert(projection.bounds.minLng === 73 && projection.bounds.maxLng === 73.2, 'longitude bounds must come from the data');
assert(projection.bounds.minLat === 33 && projection.bounds.maxLat === 33.2, 'latitude bounds must come from the data');
assert(projection.points.length === 2 && projection.stopPoints.length === 2, 'every usable record must be projected');
for (const point of projection.points) {
  assert(point.d.startsWith('M') && !point.d.includes('NaN') && !point.d.includes('undefined'), 'path data must be finite');
}
const degenerate = projectNetworkGeometry([{ routeId: 'r', points: [[10, 20], [10, 20]] }], []);
assert(!degenerate.degenerate && !degenerate.points[0].d.includes('NaN'), 'a degenerate extent must still project finitely');
assert(projectNetworkGeometry([], []).degenerate, 'an empty projection must be flagged degenerate');

// --- adapter never yields demo geometry -------------------------------------
const inertResult = await createMapAdapter({}).load();
assert(inertResult.state === 'not_configured', 'an unconfigured adapter must report not_configured');
assert(inertResult.geometry.routes.length === 0, 'an unconfigured adapter must not supply routes');
const thrownResult = await createMapAdapter({ loadNetwork: async () => { throw new Error('boom'); } }).load();
assert(thrownResult.state === 'error', 'a failed load must report error, not fall back');
assert(thrownResult.geometry.routes.length === 0, 'a failed load must not supply routes');
assert((await createMapAdapter({ loadNetwork: async () => null }).load()).state === 'not_configured',
  'a null payload must not become geometry');

// --- adapter bridges the declared routesAndStops contract -------------------
const bridged = await connectGeometryAdapter({
  async routesAndStops() { return { state: 'ready', data: { mapGeometry: geometryPayload() } }; },
}).load({});
assert(bridged.state === 'ready', `bridged adapter should be ready, got ${bridged.state}`);
assert(bridged.geometry.counts.routes === 2, 'bridged adapter must expose validated route counts');
const noGeometry = await connectGeometryAdapter({
  async routesAndStops() { return { state: 'ready', data: { meta: {}, kpis: [] } }; },
}).load({});
assert(noGeometry.state === 'not_configured', 'a response without mapGeometry must not invent geometry');
const failingBridge = await connectGeometryAdapter({
  async routesAndStops() { return { state: 'error', error: new Error('http 500') }; },
}).load({});
assert(failingBridge.state === 'error', 'a failed analytics request must surface as error');
assert(connectGeometryAdapter(null).configured === false, 'a missing client must yield an inert adapter');

// --- rendering: verified geometry becomes a data-driven map -----------------
const readyMarkup = networkMap({ mode: 'api', state: 'ready', geometry: { ...rejection, projection } });
assert(readyMarkup.includes('data-map-state="ready"'), 'ready state must be observable');
assert(readyMarkup.includes('data-map-source="api"'), 'verified geometry must be marked as API sourced');
for (const id of ['route-a', 'route-b', 'stop-a', 'stop-b']) {
  assert(readyMarkup.includes(`data-entity-id="${id}"`), `${id} must be individually addressable`);
}
assert(readyMarkup.includes('data-action="map-select-route"'), 'routes need a selection affordance');
assert(readyMarkup.includes('data-action="map-select-stop"'), 'stops need a selection affordance');
assert(readyMarkup.includes('Alpha'), 'route identification must be visible without hover');
assert(readyMarkup.includes('Alpha Terminus'), 'stop identification must be visible');
assert(readyMarkup.includes('map-network__path'), 'routes must be drawn as data-driven paths');
assert(readyMarkup.includes('map-key'), 'a route key is required for identification without hover');
assert(!readyMarkup.includes('DEMO NETWORK PREVIEW'), 'verified geometry must not carry the demo label');
assert(!readyMarkup.includes('Central Loop'), 'verified geometry must not contain demo route names');
assert(!readyMarkup.includes('Central Hub'), 'verified geometry must not contain demo stop names');

for (const state of ['loading', 'not_configured', 'unavailable', 'empty', 'error']) {
  const markup = networkMap({ mode: 'api', state, geometry: { warnings: [] } });
  assert(markup.includes(`data-map-state="${state}"`), `state ${state} must be observable`);
  assert(!markup.includes('map-network__path'), `state ${state} must not draw geometry`);
  assert(!markup.includes('DEMO'), `state ${state} must not fall back to demo fixtures`);
  assert(!markup.includes('Central Loop') && !markup.includes('Riverside Connector'), `state ${state} leaked demo routes`);
  assert(!markup.includes('Central Hub'), `state ${state} leaked demo stops`);
  assert(!markup.includes('data-entity-id='), `state ${state} must expose no selectable entity`);
}

// --- selection highlights the chosen entity only ----------------------------
const selected = networkMap({
  mode: 'api',
  state: 'ready',
  geometry: { ...rejection, projection },
  selection: { kind: 'route', id: 'route-b' },
});
assert(selected.includes('data-selection-id="route-b"'), 'the selected entity must be identified');
assert((selected.match(/is-selected/g) ?? []).length > 0, 'selection must be visually marked');
assert(!selected.includes('data-selection-id="route-a"'), 'only the selected entity may be reported as the selection');

// --- page wiring: routes-stops threads geometry through ---------------------
const readyPage = renderMapPage('routes-stops', 'api', { mapGeometry: geometryPayload() });
assert(readyPage.includes('data-map-state="ready"'), 'the page must derive the ready state from the response');
assert(readyPage.includes('data-entity-id="route-a"'), 'the page must render response routes');
assert(readyPage.includes('data-entity-id="stop-a"'), 'the page must render response stops');
assert(!readyPage.includes('DEMO NETWORK PREVIEW'), 'API mode must not render the demo canvas');
assert(!readyPage.includes('Central Loop'), 'API mode must not contain demo routes');

const unavailablePage = renderMapPage('routes-stops', 'api', { mapGeometry: unavailablePayload });
assert(unavailablePage.includes('data-map-state="unavailable"'), 'the unavailable state must reach the page');
assert(!unavailablePage.includes('map-network__path'), 'an unavailable response must draw nothing');
const emptyPage = renderMapPage('routes-stops', 'api', { mapGeometry: { available: true, routes: [], stops: [] } });
assert(emptyPage.includes('data-map-state="empty"'), 'an empty response must reach the page as empty');
assert(!emptyPage.includes('Central Loop'), 'an empty response must not borrow demo routes');
const noGeometryPage = renderMapPage('routes-stops', 'api', null);
assert(noGeometryPage.includes('data-map-state="not_configured"'), 'a missing response must render not_configured');
assert(!noGeometryPage.includes('map-network__path'), 'a missing response must draw nothing');
const demoPage = renderMapPage('routes-stops', 'demo', { mapGeometry: geometryPayload() });
assert(demoPage.includes('data-map-source="demo"'), 'demo mode must stay labelled as demo');
assert(demoPage.includes('DEMO NETWORK PREVIEW'), 'demo mode must keep its explicit preview label');
assert(!demoPage.includes('data-entity-id="route-a"'), 'demo mode must not adopt response geometry');
assert(renderMapPage('routes-stops', 'demo', null, { kind: 'route', id: 'route-a' }).includes('DEMO NETWORK PREVIEW'),
  'a demo selection must not remove the demo labelling');

// --- mock isolation in source ----------------------------------------------
const mapComponent = await readFile(join(root, 'src/components/map.js'), 'utf8');
const demoIndex = mapComponent.indexOf('function renderDemoCanvas');
const verifiedIndex = mapComponent.indexOf('function renderVerifiedCanvas');
const stateIndex = mapComponent.indexOf('function renderStateCanvas');
assert(demoIndex > 0 && verifiedIndex > demoIndex && stateIndex > verifiedIndex, 'map renderers must be separate functions');
const verifiedBody = mapComponent.slice(verifiedIndex, stateIndex);
assert(!verifiedBody.includes('DEMO_ROUTES') && !verifiedBody.includes('DEMO_STOPS'),
  'the verified renderer must not reference demo fixtures');
assert(mapComponent.slice(demoIndex, verifiedIndex).includes('data-map-source="demo"'),
  'the demo renderer must mark its own output as demo');
const pageSource = await readFile(join(root, 'src/pages/routes-stops.js'), 'utf8');
const adapterSource = await readFile(join(root, 'src/api/map-adapter.js'), 'utf8');
for (const [name, source] of [['src/pages/routes-stops.js', pageSource], ['src/api/map-adapter.js', adapterSource]]) {
  assert(!source.includes('demo-data.js'), `${name} must not import demo fixtures`);
}
const resolveBody = pageSource.slice(
  pageSource.indexOf('function resolveMap'),
  pageSource.indexOf('function mapUpdated'),
);
assert(resolveBody.indexOf("mode === 'demo'") < resolveBody.indexOf('normalizeNetworkGeometry'),
  'demo mode must short-circuit before any response parsing');

// --- partial responses must not promote untouched demo collections ----------
const { getPageData } = await import('../src/data/page-data.js');
const { demoData, getDemoPageData } = await import('../src/data/demo-data.js');

const partial = getPageData('routes-stops', { mode: 'api', apiViewModel: { mapGeometry: geometryPayload() } });
for (const key of ['kpis', 'routePerformance', 'stopRanking']) {
  assert(Array.isArray(partial[key]), `${key} must stay present for page components`);
  assert(partial[key].length === 0, `demo ${key} must not survive a response that omitted it`);
}
assert(partial.__hasApiData === true, 'a supplied response must be flagged as API data');
assert(Array.isArray(demoData.routesStops.kpis), 'the fixture must still own its demo lists');
const full = getPageData('routes-stops', { mode: 'api', apiViewModel: { ...demoData.routesStops, meta: {} } });
assert(full.kpis.length === demoData.routesStops.kpis.length, 'a response that supplies the list must win');
const demoStillFull = getPageData('routes-stops', { mode: 'demo', apiViewModel: { mapGeometry: geometryPayload() } });
assert(demoStillFull.routePerformance.length === demoData.routesStops.routePerformance.length,
  'demo mode must keep its fixtures even when a response object exists');

// Generic status and pending labels are shared vocabulary, not demo content.
const SHARED_VOCABULARY = new Set([
  'Awaiting API', 'API required', 'Not supplied', 'contract only', 'no claims', 'design families',
  'Historical', 'Past only', 'UI catalog ready', 'UI interface', 'Awaiting results', 'Locked',
  'design contract', 'Required', 'Review', 'Investigate', 'Protect', 'Awaiting result',
  'Ready for wiring', 'Not connected', 'Interface ready', 'Good', 'Stable', 'Elevated',
  'model', 'bus', 'Buses', 'index', 'percent', 'rides', 'capacity', 'corridor', 'feeder',
  'Capacity', 'Reliability', 'Scenario', 'Decision', 'Query', 'Data', 'Model', 'System',
]);

function collectStrings(value, sink) {
  if (typeof value === 'string') sink.add(value);
  else if (Array.isArray(value)) value.forEach((item) => collectStrings(item, sink));
  else if (value && typeof value === 'object') Object.values(value).forEach((item) => collectStrings(item, sink));
}

// Demo route and stop identities are the entity labels an evaluator could
// mistake for real network data, so they get an explicit per-page assertion.
const DEMO_ENTITIES = new Set();
for (const row of [
  ...demoData.routesStops.routePerformance,
  ...demoData.routesStops.stopRanking,
  ...demoData.demand.routeDemand,
  ...demoData.demand.stopDemand,
  ...demoData.delays.routeReliability,
  ...demoData.clustering.assignments,
]) {
  for (const field of ['name', 'code', 'route']) {
    if (typeof row[field] === 'string' && row[field].length >= 4) DEMO_ENTITIES.add(row[field]);
  }
}
assert(DEMO_ENTITIES.size > 5, 'demo-entity set is unexpectedly small');

// Structural isolation, checked on the mechanism rather than on harvested
// strings: in API mode every demo collection the response did not supply must
// be empty, and in demo mode every fixture collection must survive.
for (const page of pages) {
  const fixture = getDemoPageData(page);
  const stripped = getPageData(page, { mode: 'api', apiViewModel: { meta: { source: 'api' } } });
  for (const [key, value] of Object.entries(fixture)) {
    if (!Array.isArray(value) || value.length === 0) continue;
    assert(Array.isArray(stripped[key]), `${page}/${key} must stay present in API mode`);
    assert(stripped[key].length === 0, `${page}/${key} kept ${stripped[key].length} demo row(s) in API mode`);
  }
  const demoKept = getPageData(page, { mode: 'demo', apiViewModel: { meta: { source: 'api' } } });
  for (const [key, value] of Object.entries(fixture)) {
    if (!Array.isArray(value) || value.length === 0) continue;
    assert(demoKept[key].length === value.length, `${page}/${key} lost demo rows in demo mode`);
  }

  const markup = renderMapPage(page, 'api', { meta: { source: 'api' }, kpis: [] });
  const leakedEntities = [...DEMO_ENTITIES].filter((text) => markup.includes(text));
  assert(leakedEntities.length === 0, `demo entity leaked into ${page} API mode: ${leakedEntities.slice(0, 3)}`);
  assert(!markup.includes('DEMO NETWORK PREVIEW'), `demo map leaked into ${page} API mode`);
}

console.log(`Frontend foundation check passed: ${jsFiles.length} JavaScript files, ${required.length} required entry files, ${pages.length * 2} page/mode renders, ${pages.length} partial-response isolation renders, shell mount, network-map data flow and mock isolation.`);

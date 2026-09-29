import assert from 'node:assert/strict';
import { ALL_NAV_ITEMS, isPageId } from '../src/navigation.js';
import { renderPage } from '../src/pages/index.js';
import { resultPanel } from '../src/components/result-state.js';

const routeIds = ALL_NAV_ITEMS.map(item => item.id);
assert.equal(new Set(routeIds).size, routeIds.length, 'Route identifiers must be unique');
for (const id of routeIds) {
  for (const mode of ['api', 'demo']) {
    const markup = renderPage(id, mode);
    assert(markup.includes('<h1'), `${id} has a page heading`);
    assert(!markup.includes('undefined'), `${id} has no missing labels`);
    for (const [, target] of markup.matchAll(/data-action="navigate-([^"]+)"/g)) {
      assert(isPageId(target), `${id} links to a registered route: ${target}`);
    }
    if (mode === 'api') {
      for (const value of ['18,420', '87.4%', '128.6k', 'R-101', 'R-107', 'R-214', '+6.8%', 'DEMO NETWORK PREVIEW']) {
        assert(!markup.includes(value), `${id} cannot leak demo value ${value}`);
      }
    }
  }
}
for (const state of ['loading', 'empty', 'not_configured', 'error', 'ready']) {
  const markup = resultPanel({ title: 'Results', result: { state, rows: [{ name: '<script>unsafe</script>' }] }, columns: [{ label: 'Name', key: 'name' }] });
  assert(!markup.includes('<script>'));
  if (state === 'ready') assert(markup.includes('&lt;script&gt;'));
  if (state === 'loading') assert(markup.includes('aria-busy="true"'));
  if (state === 'error') assert(markup.includes('could not be loaded'));
}
const root = { innerHTML: '', focus() {} };
const events = {};
globalThis.document = { title: '', querySelector: () => root, addEventListener: (name, handler) => { events[name] = handler; } };
globalThis.location = { hash: '#/executive' };
globalThis.location.hostname = 'localhost';
globalThis.window = { location: globalThis.location, scrollTo() {}, addEventListener: (name, handler) => { events[name] = handler; } };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.requestAnimationFrame = callback => callback();
let requests = 0;
const requestedPaths = [];
globalThis.fetch = async input => {
  requests += 1;
  const path = new URL(input).pathname;
  requestedPaths.push(path);
  if (path.endsWith('/config')) return { ok: true, json: async () => ({ demo_mode: false }) };
  const data = path.endsWith('/health') ? { dataset_version: 'production-v1.1' } : { rows: [] };
  return { ok: true, json: async () => ({ status: 'ready', data, meta: { source: 'product-check-fixture' } }) };
};
const { mountApp } = await import('../src/app.js');
await mountApp();
assert.equal(globalThis.urbanTransitApp.state.mode, 'api', 'First visit defaults to empty live view');
assert.equal(globalThis.urbanTransitApp.state.localPreview, true, 'Local dashboard preview is explicitly loopback-only');
assert.equal(globalThis.urbanTransitApp.state.session, null, 'Local preview does not create a session');
assert.equal(typeof events.hashchange, 'function', 'Hash routing listens on window');
for (const id of routeIds) {
  globalThis.location.hash = `#/${id}`;
  events.hashchange();
  assert.equal(globalThis.urbanTransitApp.state.activePage, id);
  assert(!root.innerHTML.includes('Dashboard view could not render'), `${id} mounts without render errors`);
}
for (const invalid of ['missing-page', 'toString', '__proto__']) {
  globalThis.location.hash = `#/${invalid}`;
  events.hashchange();
  assert.equal(globalThis.urbanTransitApp.state.activePage, 'executive');
}
assert(requests > 0, 'Local preview calls configured APIs');
assert(requestedPaths.includes('/api/v1/evidence/status'), 'Executive preview loads evidence status');
assert(requestedPaths.includes('/api/v1/evidence/tasks'), 'Executive preview loads evidence tasks');
assert.equal(globalThis.urbanTransitApp.state.session, null, 'Route changes do not fabricate a session');
console.log(`Product check passed: ${routeIds.length} routes, ${routeIds.length * 2} page renders, loopback preview, evidence API routing, 5 result states, safe defaults and escaped output.`);

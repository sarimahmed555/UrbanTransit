import { getDemoPageData } from './demo-data.js';

/**
 * Page-data boundary for the UI.
 *
 * The fixture supplies the current view shape only. A future adapter can
 * provide a normalized API view model without changing page components; the
 * page layer should not import demo values directly.
 *
 * Mock-isolation rule: in API mode a demo *collection* (KPI list, table rows,
 * chart series) is only rendered when the response supplied that same field.
 * A partial response must never promote untouched demo rows into a view that is
 * labelled as API-sourced, and it must never substitute them for absent data.
 *
 * @param {string} pageId
 * @param {{mode?: 'demo'|'api', apiViewModel?: Record<string, unknown>|null}} options
 */
export function getPageData(pageId, { mode = 'demo', apiViewModel = null } = {}) {
  const fixtureShape = getDemoPageData(pageId);
  const base = { ...fixtureShape, ...(apiViewModel || {}) };
  base.__hasApiData = mode === 'api' && Boolean(apiViewModel);
  if (mode !== 'api' || !apiViewModel) return base;

  for (const [key, value] of Object.entries(fixtureShape)) {
    if (!Array.isArray(value)) continue;
    if (Object.prototype.hasOwnProperty.call(apiViewModel, key)) continue;
    // Present but empty: pages can rely on the field existing, and the
    // component layer renders its own empty state instead of demo rows.
    base[key] = [];
  }
  return base;
}

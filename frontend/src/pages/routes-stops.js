import { button, chartState, panel, statusBadge } from '../components/ui.js';
import { icon } from '../components/icons.js';
import { networkMap } from '../components/map.js';
import { normalizeNetworkGeometry, projectNetworkGeometry } from '../api/map-adapter.js';
import {
  apiPlaceholder,
  chartPanel,
  demoBlock,
  kpiRow,
  pageFrame,
  panelHeaderActions,
  routeNameCell,
  stateNotice,
  stopNameCell,
  tablePanel,
} from './shared.js';

export function renderRoutesStops({ data, mode, selection = null }) {
  const map = resolveMap({ data, mode, selection });
  return pageFrame({
    data,
    mode,
    updated: mode === 'demo' ? 'Demo network geometry · not production' : map.updated,
    actions: `${button({ label: 'Layer controls', action: 'map-settings', iconName: 'layers', variant: 'outline', size: 'sm' })}${button({ label: 'Export network', action: 'export-network', iconName: 'download', variant: 'outline', size: 'sm' })}`,
    children: `
      ${stateNotice(mode)}
      ${kpiRow(data.kpis, mode, '', Boolean(data.__hasApiData))}
      <div class="dashboard-grid dashboard-grid--map">
        ${networkMap({ mode, title: 'Routes, stops, and spatial context', state: map.state, geometry: map.geometry, selection })}
        ${panel({ title: 'Network lens', eyebrow: 'Structure at a glance', description: 'Keep geography optional so the core analytics remains usable without a map provider.', body: `<div class="network-lens"><div class="network-lens__item"><span class="network-lens__icon network-lens__icon--teal">${iconRoute()}</span><span><strong>Pattern-aware</strong><small>Route direction and version context</small></span></div><div class="network-lens__item"><span class="network-lens__icon network-lens__icon--blue">${iconStop()}</span><span><strong>Stop-aware</strong><small>Location, zone, and activity context</small></span></div><div class="network-lens__item"><span class="network-lens__icon network-lens__icon--amber">${iconLink()}</span><span><strong>As-of safe</strong><small>No future geometry by default</small></span></div></div>${mapNote({ mode, map })}` })}
      </div>
      <div class="dashboard-grid dashboard-grid--split">
        ${tablePanel({ title: 'Route performance', description: 'A comparison surface for reliability, load, and delay context.', columns: [
          { label: 'Route', render: (row) => routeNameCell(row) },
          { label: 'Reliability', key: 'reliability', align: 'right' },
          { label: 'Load', key: 'load', align: 'right' },
          { label: 'Median delay', key: 'delay', align: 'right' },
          { label: 'Signal', render: (row) => statusBadge(row.status, row.tone) },
        ], rows: data.routePerformance, mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Open performance', 'open-route-performance') })}
        ${tablePanel({ title: 'Stop demand ranking', description: 'Boarding, alighting, dwell, and activity bands.', columns: [
          { label: 'Stop', render: (row) => stopNameCell(row) },
          { label: 'Boardings', key: 'boardings', align: 'right' },
          { label: 'Alightings', key: 'alightings', align: 'right' },
          { label: 'Dwell', key: 'dwell', align: 'right' },
          { label: 'Activity', render: (row) => statusBadge(row.status, row.tone) },
        ], rows: data.stopRanking, mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Open stop analysis', 'open-stop-analysis') })}
      </div>
      ${chartPanel({ title: 'Route and stop comparison', subtitle: 'A future drill-down slot for pattern, version, direction, and service-day filters.', chart: demoBlock(`<div class="drilldown-placeholder"><span class="drilldown-placeholder__icon">${icon('table')}</span><strong>Comparison canvas ready</strong><small>Connect route/stop metrics to render a linked detail view.</small></div>`, mode, chartState({ state: 'empty', title: 'Route comparison awaiting API', description: 'The linked table and detail surface are prepared for analytics responses.' })), source: mode, className: 'chart-panel--wide' })}
    `,
  });
}

function iconRoute() { return '<span aria-hidden="true">↗</span>'; }
function iconStop() { return '<span aria-hidden="true">●</span>'; }
function iconLink() { return '<span aria-hidden="true">∞</span>'; }

/**
 * Resolve the map's state and geometry for the current mode.
 *
 * Demo mode never touches the response. API mode derives everything from the
 * `mapGeometry` view-model field: when that field is absent the state is
 * `not_configured`, when the service says it has no geometry the state is
 * `unavailable`, and when a response carries no usable coordinate the state is
 * `empty`. No branch substitutes the demo fixtures.
 */
function resolveMap({ data, mode, selection }) {
  if (mode === 'demo') {
    return { state: 'demo', geometry: null, updated: 'Demo network geometry · not production' };
  }
  if (data.__mapState === 'loading') {
    return { state: 'loading', geometry: null, updated: 'Requesting verified geometry' };
  }
  if (data.__mapState === 'error') {
    return { state: 'error', geometry: null, updated: 'Geometry request failed' };
  }
  const normalized = normalizeNetworkGeometry(data.mapGeometry);
  if (normalized.state !== 'ready') {
    return {
      state: normalized.state,
      geometry: { ...normalized, projection: null },
      updated: mapUpdated(normalized),
    };
  }
  const projection = projectNetworkGeometry(normalized.routes, normalized.stops);
  return {
    state: 'ready',
    geometry: { ...normalized, projection, selection },
    updated: mapUpdated(normalized),
  };
}

function mapUpdated(normalized) {
  if (normalized.state !== 'ready') return 'No verified geometry in this response';
  return `${normalized.counts.routes} route(s) · ${normalized.counts.stops} stop(s) from analytics response`;
}

function mapNote({ mode, map }) {
  if (mode === 'demo') {
    return '<div class="inline-empty-note"><span class="demo-mini-mark">DEMO</span> The geometry above is illustrative, not a production map.</div>';
  }
  if (map.state === 'ready') {
    const asOf = map.geometry.asOf ? ` As of ${map.geometry.asOf}.` : ' No as-of timestamp was supplied.';
    return `<div class="inline-empty-note">Route paths and stop coordinates came from the analytics response.${escapeText(asOf)}</div>`;
  }
  return '<div class="inline-empty-note">No verified geometry is available for this response, so the map shows an explicit state instead of substitute data.</div>';
}

function escapeText(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

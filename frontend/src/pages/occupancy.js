import { donutChart, lineChart } from '../components/charts.js';
import { icon } from '../components/icons.js';
import { button, chartState, panel, statusBadge } from '../components/ui.js';
import {
  chartPanel,
  demoBlock,
  hasProductionEvidence,
  kpiRow,
  pageFrame,
  panelHeaderActions,
  routeNameCell,
  stateNotice,
  tablePanel,
} from './shared.js';

export function renderOccupancy({ data, mode }) {
  const bands = donutChart({ segments: data.bands, totalLabel: 'demo', ariaLabel: 'Demo occupancy band distribution' });
  const trend = lineChart({ labels: data.trend.labels, series: [{ values: data.trend.values, color: 'coral', label: 'Demo peak load', area: true }, { values: data.trend.comparison, color: 'slate', label: 'Comparison', dashed: true }], ariaLabel: 'Demo peak load factor by hour' });
  return pageFrame({
    data,
    mode,
    updated: mode === 'demo' ? 'Demo occupancy fixture · capacity context required' : 'Awaiting occupancy response',
    actions: `${button({ label: 'Capacity rules', action: 'open-capacity-rules', iconName: 'gauge', variant: 'outline', size: 'sm' })}${button({ label: 'Export crowding', action: 'export-crowding', iconName: 'download', variant: 'outline', size: 'sm' })}`,
    children: `
      ${stateNotice(mode, hasProductionEvidence(data))}
      ${kpiRow(data.kpis, mode, '', Boolean(data.__hasApiData))}
      <div class="dashboard-grid dashboard-grid--hero dashboard-grid--occupancy">
        ${chartPanel({ title: 'Occupancy bands', subtitle: 'Low, moderate, high, overcrowded, and critical categories are configurable contracts.', chart: demoBlock(bands, mode, chartState({ state: 'empty', title: 'Occupancy bands awaiting API', description: 'The response should provide capacity-aware segments and the threshold definitions used.' })), source: mode, legendItems: data.bands.map((band) => ({ color: band.color, label: band.label })) })}
        ${chartPanel({ title: data.trend.label, subtitle: 'A recurring-load view is distinct from a single overloaded trip.', chart: demoBlock(trend, mode, chartState({ state: 'empty', title: 'Occupancy trend awaiting API', description: 'Connect segment load, time window, and capacity metadata to render this view.' })), source: mode, legendItems: [{ color: 'coral', label: 'Demo peak load' }, { color: 'slate', label: 'Comparison' }] })}
      </div>
      ${tablePanel({ title: 'Persistent overcrowding signals', description: 'Repeated overload, direction, time window, and stop-sequence context stay visible together.', columns: [
        { label: 'Route', render: (row) => routeNameCell(row) },
        { label: 'Window', key: 'window' },
        { label: 'Load', key: 'load', align: 'right' },
        { label: 'Duration', key: 'duration', align: 'right' },
        { label: 'Stops', key: 'stops', align: 'right' },
        { label: 'Signal', render: (row) => statusBadge(row.status, row.tone) },
      ], rows: data.persistent, mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Open crowding evidence', 'open-crowding-evidence'), className: 'panel--wide' })}
      ${panel({ title: 'Crowding evidence contract', eyebrow: 'Interpretation guardrails', body: `<div class="guardrail-grid"><div><span class="guardrail-icon">${icon('gauge')}</span><strong>Capacity-aware</strong><p>Use effective departure capacity and segment load; never infer a ratio from a planned vehicle.</p></div><div><span class="guardrail-icon">${icon('layers')}</span><strong>Persistent, not isolated</strong><p>Repeated overload needs time, direction, stop sequence, and day-of-week context.</p></div><div><span class="guardrail-icon">${icon('shield')}</span><strong>Unavailable is not zero</strong><p>Unknown assignments and missing capacity disable the dependent measure explicitly.</p></div></div>` })}
    `,
  });
}

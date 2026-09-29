import { heatmap, lineChart } from '../components/charts.js';
import { button, chartState, panel, statusBadge } from '../components/ui.js';
import { networkMap } from '../components/map.js';
import { DEMO_FILTER_OPTIONS } from '../data/demo-data.js';
import {
  chartPanel,
  demoBlock,
  filterRow,
  kpiRow,
  pageFrame,
  panelHeaderActions,
  stateNotice,
  tablePanel,
} from './shared.js';

export function renderPassengerFlow({ data, mode }) {
  const matrix = heatmap({ values: data.matrix, labels: ['Hub', 'Civic', 'Market', 'University', 'Airport', 'East', 'North', 'South', 'Yard'], ariaLabel: 'Demo origin destination matrix' });
  const directional = lineChart({ labels: ['06', '09', '12', '15', '18', '21'], series: [{ values: [34, 62, 48, 55, 71, 42], color: 'teal', label: 'Demo directional demand', area: true }, { values: [30, 56, 44, 50, 64, 39], color: 'slate', label: 'Comparison', dashed: true }], ariaLabel: 'Demo directional flow trend' });
  const filters = [
    { key: 'route', label: 'Route', icon: 'route', options: DEMO_FILTER_OPTIONS.routes, demoOptions: true },
    { key: 'period', label: 'Time period', icon: 'clock', options: ['All periods', 'Morning peak', 'Midday', 'Evening peak'] },
    { key: 'day-type', label: 'Day type', icon: 'calendar', options: DEMO_FILTER_OPTIONS.serviceDays },
    { key: 'service-type', label: 'Service type', icon: 'sliders', options: ['All service types', 'Feeder', 'Corridor', 'Express', 'Social'] },
  ];
  return pageFrame({
    data,
    mode,
    filter: filterRow({ mode, filters, action: button({ label: 'Update matrix', action: 'apply-od-filters', iconName: 'check', variant: 'primary', size: 'sm' }) }),
    updated: mode === 'demo' ? 'Demo OD fixture · endpoints illustrative' : 'Awaiting passenger flow response',
    actions: `${button({ label: 'OD matrix', action: 'open-od-matrix', iconName: 'grid', variant: 'outline', size: 'sm' })}${button({ label: 'Export flow', action: 'export-flow', iconName: 'download', variant: 'outline', size: 'sm' })}`,
    children: `
      ${stateNotice(mode)}
      ${kpiRow(data.kpis, mode, '', Boolean(data.__hasApiData))}
      <div class="dashboard-grid dashboard-grid--flow">
        ${chartPanel({ title: 'Origin–destination intensity', subtitle: 'A filter-aware matrix slot with explicit origin, destination, period, route, and day-type context.', chart: demoBlock(matrix, mode, chartState({ state: 'empty', title: 'OD matrix awaiting API', description: 'The response should preserve unresolved endpoints and their exclusion count rather than infer zero demand.' })), source: mode, className: 'chart-panel--wide', action: panelHeaderActions('Download matrix', 'export-od-matrix') })}
        ${chartPanel({ title: 'Directional demand pattern', subtitle: 'A linked trend for route direction and service period.', chart: demoBlock(directional, mode, chartState({ state: 'empty', title: 'Directional trend awaiting API', description: 'Connect direction, service type, and time period to render this comparison.' })), source: mode, legendItems: [{ color: 'teal', label: 'Demo demand' }, { color: 'slate', label: 'Comparison' }] })}
      </div>
      <div class="dashboard-grid dashboard-grid--split">
        ${tablePanel({ title: 'Top origin–destination pairs', description: 'Complete endpoints are shown separately from unresolved evidence.', columns: [
          { label: 'Origin', key: 'origin' },
          { label: 'Destination', key: 'destination' },
          { label: 'Route', key: 'route' },
          { label: 'Passenger count', key: 'count', align: 'right' },
          { label: 'Share', key: 'share', align: 'right' },
        ], rows: data.topPairs, mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Open flow detail', 'open-flow-detail') })}
        ${networkMap({ mode, title: 'Flow corridors and stop context', compact: true })}
      </div>
      ${panel({ title: 'Flow contract', eyebrow: 'Measurement notes', body: `<div class="guardrail-grid"><div><span class="guardrail-icon">↔</span><strong>Both endpoints required</strong><p>Completed OD evidence excludes unresolved endpoints and reports the excluded count.</p></div><div><span class="guardrail-icon">⇄</span><strong>Direction-aware</strong><p>Keep route direction and service type attached to every aggregate.</p></div><div><span class="guardrail-icon">◌</span><strong>No invented zero</strong><p>Missing or inactive intervals are distinct from valid zero-demand cells.</p></div></div>` })}
    `,
  });
}

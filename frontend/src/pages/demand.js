import { barChart, heatmap, lineChart } from '../components/charts.js';
import { icon } from '../components/icons.js';
import { button, chartState, emptyState, panel, statusBadge, table } from '../components/ui.js';
import { DEMO_FILTER_OPTIONS } from '../data/demo-data.js';
import {
  chartPanel,
  demoBlock,
  filterRow,
  hasProductionEvidence,
  kpiRow,
  pageFrame,
  panelHeaderActions,
  routeNameCell,
  stateNotice,
  stopNameCell,
  tablePanel,
  trendCell,
} from './shared.js';

export function renderDemand({ data, mode }) {
  const trend = lineChart({
    labels: data.trend.labels,
    series: [
      { values: data.trend.values, color: 'teal', label: data.trend.label, area: true },
      { values: data.trend.comparison, color: 'slate', label: 'Previous period', dashed: true },
    ],
    ariaLabel: 'Demo passenger demand by time of day',
  });
  const peakChart = barChart({ labels: data.peakBars.map((item) => item.label), values: data.peakBars.map((item) => item.value), colors: data.peakBars.map((item) => item.tone || 'teal'), ariaLabel: 'Demo peak and off-peak comparison' });
  const demandHeatmap = heatmap({ values: data.heatmap, labels: ['06', '07', '08', '09', '10', '11', '12', '13'], ariaLabel: 'Demo demand intensity heatmap' });
  const filters = [
    { key: 'date-range', label: 'Date range', icon: 'calendar', options: DEMO_FILTER_OPTIONS.dateRanges },
    { key: 'service-day', label: 'Service day', icon: 'pulse', options: DEMO_FILTER_OPTIONS.serviceDays },
    { key: 'route', label: 'Route', icon: 'route', options: DEMO_FILTER_OPTIONS.routes, demoOptions: true },
    { key: 'granularity', label: 'Granularity', icon: 'sliders', options: DEMO_FILTER_OPTIONS.granularities },
  ];
  return pageFrame({
    data,
    mode,
    filter: filterRow({ mode, filters, action: button({ label: 'Apply view', action: 'apply-filters', iconName: 'check', variant: 'primary', size: 'sm' }) }),
    updated: mode === 'demo' ? 'Demo filter context · synthetic series' : 'Awaiting API response',
    actions: `${button({ label: 'Compare periods', action: 'compare-periods', iconName: 'sliders', variant: 'outline', size: 'sm' })}${button({ label: 'Export demand', action: 'export-demand', iconName: 'download', variant: 'outline', size: 'sm' })}`,
    children: `
      ${stateNotice(mode, hasProductionEvidence(data))}
      ${kpiRow(data.kpis, mode, '', Boolean(data.__hasApiData))}
      <div class="dashboard-grid dashboard-grid--hero dashboard-grid--demand">
        ${chartPanel({ title: data.trend.label, subtitle: 'Hourly shape is a contract placeholder until the demand response is connected.', chart: demoBlock(trend, mode, chartState({ state: 'empty', title: 'Demand trend awaiting API', description: 'The hourly series interface is ready for route, stop, and service-day filters.' })), source: mode, legendItems: [{ color: 'teal', label: 'Demo current period' }, { color: 'slate', label: 'Demo comparison' }], action: panelHeaderActions('Export series', 'export-demand-series') })}
        ${chartPanel({ title: 'Peak and off-peak pattern', subtitle: 'A visual slot for demand-derived peak windows.', chart: demoBlock(peakChart, mode, chartState({ state: 'empty', title: 'Peak pattern awaiting API', description: 'Peak windows will be calculated from actual demand, not hard-coded labels.', compact: true })), source: mode, legendItems: [{ color: 'teal', label: 'Demo demand index' }] })}
      </div>
      <div class="dashboard-grid dashboard-grid--split">
        ${tablePanel({ title: 'Top routes by boardings', description: 'Ranking is a placeholder for a filter-aware response.', columns: [
          { label: '#', key: 'rank', align: 'left' },
          { label: 'Route', render: (row) => routeNameCell(row) },
          { label: 'Boardings', key: 'boardings', align: 'right' },
          { label: 'Trend', render: (row) => trendCell(row.trend, row.trend) },
          { label: 'Signal', render: (row) => statusBadge(row.status, row.tone) },
        ], rows: data.routeDemand, mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Route details', 'open-routes') })}
        ${tablePanel({ title: 'Busiest stops', description: 'Boarding and alighting activity by stop.', columns: [
          { label: '#', key: 'rank' },
          { label: 'Stop', render: (row) => stopNameCell(row) },
          { label: 'Boardings', key: 'boardings', align: 'right' },
          { label: 'Alightings', key: 'alightings', align: 'right' },
          { label: 'Peak', key: 'peak', align: 'right' },
        ], rows: data.stopDemand, mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Stop details', 'open-stops') })}
      </div>
      ${chartPanel({ title: 'Demand intensity by time window', subtitle: 'A reusable heatmap slot for route, stop, and day-type comparisons.', chart: demoBlock(demandHeatmap, mode, chartState({ state: 'empty', title: 'Demand intensity awaiting API', description: 'The heatmap accepts a matrix of demand values and an explicit scale legend.' })), source: mode, className: 'chart-panel--wide', action: panelHeaderActions('Download matrix', 'export-heatmap') })}
      ${panel({ title: 'Interpretation guardrails', eyebrow: 'Analytics contract', body: `<div class="guardrail-grid"><div><span class="guardrail-icon">${icon('pulse', 16)}</span><strong>Actual demand first</strong><p>Peak windows should be derived from observed boardings and requests, not static labels.</p></div><div><span class="guardrail-icon">${icon('users', 16)}</span><strong>Separate grains</strong><p>Keep unique riders, boardings, alightings, and stop visits in separate measures.</p></div><div><span class="guardrail-icon">${icon('filter', 16)}</span><strong>Make filters explicit</strong><p>Every result should retain route, stop, period, and day-type context.</p></div></div>` })}
    `,
  });
}

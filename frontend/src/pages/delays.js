import { barChart, lineChart } from '../components/charts.js';
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

export function renderDelays({ data, mode }) {
  const distribution = barChart({ labels: data.distribution.labels, values: data.distribution.values, colors: ['teal', 'blue', 'amber', 'coral', 'purple', 'slate'], ariaLabel: 'Demo positive delay distribution' });
  const trend = lineChart({ labels: data.trend.labels, series: [{ values: data.trend.values, color: 'teal', label: 'Demo punctuality', area: true }, { values: data.trend.comparison, color: 'slate', label: 'Comparison', dashed: true }], ariaLabel: 'Demo punctuality trend' });
  const timePattern = barChart({ labels: data.timePattern.map((item) => item.label), values: data.timePattern.map((item) => item.value), colors: data.timePattern.map((item) => item.tone), ariaLabel: 'Demo delay exposure by time period' });
  return pageFrame({
    data,
    mode,
    updated: mode === 'demo' ? 'Demo delay fixture · no production timing' : 'Awaiting delay analysis response',
    actions: `${button({ label: 'Delay thresholds', action: 'open-thresholds', iconName: 'sliders', variant: 'outline', size: 'sm' })}${button({ label: 'Export delays', action: 'export-delays', iconName: 'download', variant: 'outline', size: 'sm' })}`,
    children: `
      ${stateNotice(mode, hasProductionEvidence(data))}
      ${kpiRow(data.kpis, mode, '', Boolean(data.__hasApiData))}
      <div class="dashboard-grid dashboard-grid--hero">
        ${chartPanel({ title: data.distribution.label, subtitle: 'Distribution is a visual contract for positive delay observations.', chart: demoBlock(distribution, mode, chartState({ state: 'empty', title: 'Delay distribution awaiting API', description: 'The response should retain signed timing context and the denominator used for each band.' })), source: mode, legendItems: [{ color: 'teal', label: 'Demo share' }], action: panelHeaderActions('Export distribution', 'export-delay-distribution') })}
        ${chartPanel({ title: data.trend.label, subtitle: 'Punctuality is a derived measure, not a padded delay count.', chart: demoBlock(trend, mode, chartState({ state: 'empty', title: 'Punctuality trend awaiting API', description: 'Connect route, stop, and time-window metrics to render the trend.' })), source: mode, legendItems: [{ color: 'teal', label: 'Demo current period' }, { color: 'slate', label: 'Comparison' }] })}
      </div>
      <div class="dashboard-grid dashboard-grid--split">
        ${tablePanel({ title: 'Route reliability', description: 'Rank routes with explicit on-time denominators and delay context.', columns: [
          { label: 'Route', render: (row) => routeNameCell(row) },
          { label: 'On-time', key: 'onTime', align: 'right' },
          { label: 'Median delay', key: 'median', align: 'right' },
          { label: 'Variance', key: 'variance' },
          { label: 'Signal', render: (row) => statusBadge(row.status, row.tone) },
        ], rows: data.routeReliability, mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Open reliability', 'open-reliability') })}
        ${chartPanel({ title: 'Delay exposure by time period', subtitle: 'A compact pattern view for peak and off-peak investigation.', chart: demoBlock(timePattern, mode, chartState({ state: 'empty', title: 'Time pattern awaiting API', description: 'The interface accepts a period-wise delay summary with explicit denominators.' })), source: mode, legendItems: [{ color: 'coral', label: 'High exposure' }, { color: 'teal', label: 'Lower exposure' }] })}
      </div>
      ${panel({ title: 'Delay interpretation', eyebrow: 'Safe analysis', body: `<div class="guardrail-grid"><div><span class="guardrail-icon">${iconClock()}</span><strong>Keep signed context</strong><p>Early arrivals are valid observations and should not be padded into the positive-delay table.</p></div><div><span class="guardrail-icon">${iconRoute()}</span><strong>Respect stop grain</strong><p>Delay analysis stays tied to a specific stop visit and applicable plan version.</p></div><div><span class="guardrail-icon">${iconShield()}</span><strong>Separate evidence</strong><p>A missing vehicle assignment can limit attribution without erasing valid timing evidence.</p></div></div>` })}
    `,
  });
}

function iconClock() { return '<span aria-hidden="true">◷</span>'; }
function iconRoute() { return '<span aria-hidden="true">↗</span>'; }
function iconShield() { return '<span aria-hidden="true">◇</span>'; }

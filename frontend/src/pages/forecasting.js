import { lineChart } from '../components/charts.js';
import { icon } from '../components/icons.js';
import { button, chartState, panel, statusBadge } from '../components/ui.js';
import {
  chartPanel,
  demoBlock,
  hasProductionEvidence,
  kpiRow,
  pageFrame,
  panelHeaderActions,
  readinessRow,
  stateNotice,
  tablePanel,
} from './shared.js';

export function renderForecasting({ data, mode }) {
  const forecast = lineChart({
    labels: data.forecast.labels,
    series: [
      { values: data.forecast.actual, color: 'slate', label: 'Actual / observed' },
      { values: data.forecast.forecast, color: 'teal', label: 'Forecast', area: true },
      { values: data.forecast.lower, color: 'blue', label: 'Lower interval', dashed: true },
      { values: data.forecast.upper, color: 'blue', label: 'Upper interval', dashed: true },
    ],
    ariaLabel: 'Demo forecast versus actual interface',
  });
  return pageFrame({
    data,
    mode,
    updated: mode === 'demo' ? 'Demo forecast fixture · no model result' : 'Awaiting forecasting response',
    actions: `${button({ label: 'Model card', action: 'open-model-card', iconName: 'info', variant: 'outline', size: 'sm' })}${button({ label: 'Export forecast', action: 'export-forecast', iconName: 'download', variant: 'outline', size: 'sm' })}`,
    children: `
      ${stateNotice(mode, hasProductionEvidence(data))}
      ${kpiRow(data.kpis, mode, '', Boolean(data.__hasApiData))}
      ${chartPanel({ title: data.forecast.label, subtitle: 'Actual, forecast, and interval slots preserve evaluation context instead of asserting model performance.', chart: demoBlock(forecast, mode, chartState({ state: 'empty', title: 'Forecast versus actual awaiting API', description: 'The response should provide actual values, forecast values, interval bounds, issue time, and model version.' })), source: mode, legendItems: [{ color: 'slate', label: 'Actual / observed' }, { color: 'teal', label: 'Forecast' }, { color: 'blue', label: 'Interval bounds' }], action: panelHeaderActions('Export series', 'export-forecast-series') })}
      <div class="dashboard-grid dashboard-grid--split">
        ${panel({ title: 'Forecast horizons', eyebrow: 'Interaction surface', description: 'Ready for horizon selection and as-of metadata.', body: `<div class="horizon-list">${data.horizon.map((item) => `<div class="horizon-row"><span><strong>${item.label}</strong><small>${item.status === 'pending' ? 'API result required' : item.value}</small></span>${statusBadge(item.value, item.status)}</div>`).join('')}</div>` })}
        ${panel({ title: 'Evaluation readiness', eyebrow: 'Evidence gate', body: readinessRow(data.readiness), className: 'evaluation-panel' })}
      </div>
      ${tablePanel({ title: 'Forecast case explorer', description: 'A table contract for case ID, target interval, issue time, actual, predicted, and model lineage.', columns: [
        { label: 'Case ID', key: 'case', render: () => '<span class="pending-value">Awaiting API</span>' },
        { label: 'Target interval', render: () => '<span class="pending-value">Awaiting API</span>' },
        { label: 'Actual', render: () => '<span class="pending-value">—</span>' },
        { label: 'Forecast', render: () => '<span class="pending-value">—</span>' },
        { label: 'Model / issue time', render: () => '<span class="pending-value">Awaiting API</span>' },
      ], rows: [{}], mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Open case explorer', 'open-case-explorer'), className: 'panel--wide' })}
      ${panel({ title: 'Forecasting guardrails', eyebrow: 'No performance claims yet', body: `<div class="foundation-note"><div class="foundation-note__icon">${icon('shield')}</div><div><strong>UI contract before result.</strong><p>Forecasts should be compared against a documented simple baseline on a locked chronological test set. The dashboard keeps those metrics hidden until the analytics service supplies them.</p></div></div>` })}
    `,
  });
}

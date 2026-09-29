import { barChart } from '../components/charts.js';
import { button, chartState, panel, statusBadge } from '../components/ui.js';
import {
  chartPanel,
  demoBlock,
  kpiRow,
  pageFrame,
  panelHeaderActions,
  stateNotice,
  tablePanel,
} from './shared.js';

export function renderDataQuality({ data, mode }) {
  const severity = barChart({ labels: data.severity.map((item) => item.label), values: data.severity.map((item) => item.value === '—' ? null : item.value), colors: ['coral', 'amber', 'blue', 'slate'], ariaLabel: 'Data quality issue severity distribution' });
  return pageFrame({
    data,
    mode,
    updated: mode === 'demo' ? 'Design families only · no DQ results' : 'Awaiting data quality response',
    actions: `${button({ label: 'Rule catalog', action: 'open-rule-catalog', iconName: 'table', variant: 'outline', size: 'sm' })}${button({ label: 'Export DQ summary', action: 'export-dq', iconName: 'download', variant: 'outline', size: 'sm' })}`,
    children: `
      ${stateNotice(mode)}
      ${kpiRow(data.kpis, mode, '', Boolean(data.__hasApiData))}
      <div class="dashboard-grid dashboard-grid--hero">
        ${chartPanel({ title: 'Issue severity summary', subtitle: 'Severity counts will be linked to the run/table reconciliation when the quality API is available.', chart: demoBlock(severity, mode, chartState({ state: 'empty', title: 'DQ severity awaiting API', description: 'No issue counts are inferred from the design catalog.' })), source: mode, legendItems: [{ color: 'coral', label: 'Blocking' }, { color: 'amber', label: 'High' }, { color: 'blue', label: 'Medium' }, { color: 'slate', label: 'Low' }] })}
        ${panel({ title: 'Reconciliation gates', eyebrow: 'Trust checks', body: `<div class="reconciliation-list">${data.reconciliation.map((item) => `<div class="reconciliation-row"><span>${item.label}</span>${statusBadge(item.value, item.status, item.status === 'good' ? 'check' : 'clock')}</div>`).join('')}</div><div class="inline-empty-note">${mode === 'demo' ? '<span class="demo-mini-mark">DEMO</span> Catalog values are placeholders, not measured outcomes.' : 'Expected result is hidden until the quality service responds.'}</div>` })}
      </div>
      ${tablePanel({ title: 'Quality rule families', description: 'A representative contract for the SRS-required issue families.', columns: [
        { label: 'Rule', key: 'code' },
        { label: 'Issue family', key: 'name' },
        { label: 'Stage', key: 'stage' },
        { label: 'Result', render: (row) => statusBadge(row.status, row.tone) },
      ], rows: data.issueFamilies, mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Open issue table', 'open-issues'), className: 'panel--wide' })}
      <div class="dashboard-grid dashboard-grid--split">
        ${panel({ title: 'Lineage and audit contract', eyebrow: 'Evidence surface', body: '<div class="dq-contract-list"><div><span class="dq-contract-icon">01</span><span><strong>Raw evidence retained</strong><small>Original record and source row remain traceable.</small></span></div><div><span class="dq-contract-icon">02</span><span><strong>Disposition visible</strong><small>Accepted, corrected, flagged, unresolved, or quarantined.</small></span></div><div><span class="dq-contract-icon">03</span><span><strong>Reconciled by run</strong><small>Totals account for every source ordinal without double counting.</small></span></div></div>' })}
        ${panel({ title: 'Review checklist', eyebrow: 'Before publication', body: '<div class="checklist"><div class="checklist__row"><span class="checklist__mark checklist__mark--pending">◷</span><span>Accepted row counts</span><span class="checklist__state">API required</span></div><div class="checklist__row"><span class="checklist__mark checklist__mark--pending">◷</span><span>Dependency loss budget</span><span class="checklist__state">API required</span></div><div class="checklist__row"><span class="checklist__mark checklist__mark--pending">◷</span><span>Manifest and split hashes</span><span class="checklist__state">API required</span></div><div class="checklist__row"><span class="checklist__mark checklist__mark--good">✓</span><span>UI for all states</span><span class="checklist__state">Ready</span></div></div>' })}
      </div>
    `,
  });
}

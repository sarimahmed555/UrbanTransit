import { inventoryPanel } from './verified-context.js';
import { lineChart } from '../components/charts.js';
import { networkMap } from '../components/map.js';
import { resultPanel } from '../components/result-state.js';
import { button, chartState, emptyState, panel, escapeHtml } from '../components/ui.js';
import { productionEvidencePanel, chartPanel, demoBlock, hasProductionEvidence, kpiRow, numberedInsightList, pageFrame, signalList, stateNotice } from './shared.js';

export function renderExecutive({ data, mode }) {
  if (mode === 'api') {
    const evidence = data.__productionEvidence;
    const ready = evidence?.state === 'ready';
    const tasks = evidence?.data?.tasks;
    const list = Array.isArray(tasks) ? tasks : tasks?.data || [];
    const destinations = { passenger_demand: 'demand', delay_severity: 'delay-prediction', occupancy_forecast: 'occupancy-forecast', crowding_risk: 'occupancy', route_clustering: 'clustering' };
    return pageFrame({ data: { title: 'Executive overview', eyebrow: 'TransitVerse / Control room', description: 'A clear view of the evidence. A measured view of the network.', __apiConnection: data.__apiConnection }, mode, children: `
      <section class="evidence-ribbon"><div><span class="section-index">Evidence environment</span><strong>production-v1.1</strong></div><div><span>Source</span><strong>Frozen audited artifacts</strong></div><div><span>Serving scope</span><strong>Historical model evaluations</strong></div><a href="#/system">Review provenance ↗</a></section>
      <div class="command-grid"><section class="intelligence-surface"><div class="section-index">01 / Demand intelligence</div><h2>Understand the journey.<br><span>Start with the evidence.</span></h2><p>Passenger demand models are available as bounded historical evaluations. A certified temporal ridership feed is not available.</p><div class="recessed-state"><span class="availability-glyph" aria-hidden="true">⌁</span><div><strong>Temporal demand evidence unavailable</strong><p>No certified ridership time series is connected.</p></div></div><a class="button button--primary button--sm" href="#/demand">Explore demand evaluation ↗</a></section><section class="network-surface"><div class="section-index">02 / Network & service</div><h2>Network intelligence</h2><div class="network-placeholder"><span aria-hidden="true">⊞</span><strong>Verified geometry unavailable</strong><p>Routes and stops require certified spatial evidence.</p></div><a class="text-link" href="#/network-map">Open network workspace →</a></section></div>
      ${inventoryPanel(data.__dataset)}<section class="evaluation-directory"><div class="directory-heading"><div><span class="section-index">03 / Model evidence</span><h2>Evaluation register</h2></div><a class="text-link" href="#/model-comparison">Compare Spark / Python →</a></div>${list.length ? list.map(task => `<a class="evaluation-link" href="#/${destinations[task.task_name] || 'system'}"><span class="evaluation-name">${escapeHtml(task.task_name.replaceAll('_', ' '))}</span><span>Frozen held-out evidence</span><span>${Object.keys(task.pipelines || {}).length} pipelines</span><b>↗</b></a>`).join('') : `<div class="recessed-state">${ready ? 'No task evidence supplied.' : 'Awaiting evidence API response.'}</div>`}</section>
      <details class="evidence-details"><summary>Inspect model metrics, acceptance and limitations</summary>${productionEvidencePanel(evidence)}</details>
      <div class="scope-strip"><strong>Operational scope</strong><p>Live ridership, service alerts, recommendations and infrastructure telemetry are unavailable.</p><a href="#/system">System / Evidence status →</a></div>` });
  }
  const trend = mode === 'demo' ? lineChart({
    labels: data.trend.labels,
    series: [
      { values: data.trend.values, color: 'purple', label: data.trend.label, area: true },
      { values: data.trend.comparison, color: 'slate', label: 'Previous demo period', dashed: true },
    ],
    ariaLabel: 'Synthetic ridership trend — demo only',
  }) : chartState({ title: 'Awaiting ridership data', description: 'Passenger trends will appear with their source period and comparison window.' });
  return pageFrame({
    data: { ...data, eyebrow: 'Network overview', title: 'Executive dashboard', description: 'Demand, reliability and capacity. One view of the network.' },
    mode, updated: mode === 'demo' ? data.lastUpdated : hasProductionEvidence(data) ? 'Frozen model evidence · ridership unavailable' : 'Ridership source unavailable',
    actions: button({ label: 'View reports', action: 'navigate-reports', iconName: 'download', variant: 'primary', size: 'sm' }),
    children: `
      ${stateNotice(mode, hasProductionEvidence(data))}
      ${kpiRow(data.kpis, mode, '', Boolean(data.__hasApiData))}
      <div class="dashboard-grid dashboard-grid--hero">
        ${chartPanel({ title: 'Passenger demand over time', subtitle: 'Ridership and the previous comparison period', chart: trend, source: mode, legendItems: mode === 'demo' ? [{ color: 'purple', label: 'Demo period' }, { color: 'slate', label: 'Previous demo period' }] : [] })}
        ${panel({ title: 'Operational health', eyebrow: 'At a glance', description: 'Service signals that deserve attention.', body: demoBlock(signalList(data.health), mode, emptyState({ title: 'Awaiting service signals', description: 'Delay, occupancy and service alerts will appear here.', iconName: 'activity' })) })}
      </div>
      <div class="dashboard-grid dashboard-grid--split">
        ${resultPanel({ title: 'Route performance', description: 'Reliability and demand across routes' })}
        ${resultPanel({ title: 'Delay & occupancy', description: 'Service delays and capacity pressure' })}
        ${resultPanel({ title: 'Demand forecast', description: 'Future demand, observed outcomes and uncertainty' })}
        ${resultPanel({ title: 'Crowding forecast', description: 'Future occupancy and capacity signals' })}
      </div>
      ${panel({ title: 'Intelligence & priority alerts', eyebrow: 'Decision support', description: 'Review evidence before acting on a recommendation.', action: button({ label: 'View intelligence', action: 'navigate-recommendations', variant: 'outline', size: 'sm' }), body: demoBlock(numberedInsightList(data.priority), mode, emptyState({ title: 'Awaiting intelligence results', description: 'Evidence-backed recommendations will appear when processed results are available.', iconName: 'spark' })) })}
      ${networkMap({ mode, title: 'Network overview', compact: true })}
    `,
  });
}

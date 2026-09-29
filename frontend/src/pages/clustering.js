import { scatterPlot } from '../components/charts.js';
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

export function renderClustering({ data, mode }) {
  const scatter = scatterPlot({ points: data.points, ariaLabel: 'Demo route cluster scatter plot' });
  const clusterCards = `<div class="cluster-card-grid">${data.clusters.map((cluster) => `<article class="cluster-card cluster-card--${cluster.color}"><div class="cluster-card__top"><span class="cluster-card__dot"></span><strong>${cluster.name}</strong></div><div class="cluster-card__routes"><span>Routes</span><b>${cluster.routes}</b></div><div class="cluster-card__signal">${cluster.signal}</div><div class="cluster-card__action">${cluster.action}</div></article>`).join('')}</div>`;
  return pageFrame({
    data,
    mode,
    updated: mode === 'demo' ? 'Demo cluster fixture · no learned result' : 'Awaiting route clustering response',
    actions: `${button({ label: 'Feature contract', action: 'open-cluster-features', iconName: 'info', variant: 'outline', size: 'sm' })}${button({ label: 'Export clusters', action: 'export-clusters', iconName: 'download', variant: 'outline', size: 'sm' })}`,
    children: `
      ${stateNotice(mode, hasProductionEvidence(data))}
      ${kpiRow(data.kpis, mode, '', Boolean(data.__hasApiData))}
      <div class="dashboard-grid dashboard-grid--cluster">
        ${chartPanel({ title: 'Route behavior space', subtitle: 'A normalized feature-space placeholder for demand, load, reliability, frequency, and travel-time context.', chart: demoBlock(scatter, mode, chartState({ state: 'empty', title: 'Cluster scatter awaiting API', description: 'The response should provide cluster id, assignment, confidence, and training-window metadata.' })), source: mode, legendItems: [{ color: 'coral', label: 'High-load' }, { color: 'teal', label: 'Growing feeder' }, { color: 'amber', label: 'Coverage-first' }, { color: 'blue', label: 'Premium' }] })}
        ${panel({ title: 'Cluster interpretation', eyebrow: 'Explainer ready', description: 'Labels are descriptive UI groupings until a trained model is connected.', body: demoBlock(clusterCards, mode, '<div class="empty-cluster-state"><span>⊕</span><strong>Cluster cards awaiting API</strong><small>Assignments and explanations will render here.</small></div>') })}
      </div>
      ${tablePanel({ title: 'Route assignments', description: 'Keep assignment, confidence, and rationale together for review.', columns: [
        { label: 'Route', render: (row) => routeNameCell(row) },
        { label: 'Cluster', key: 'cluster' },
        { label: 'Confidence', key: 'confidence', align: 'right' },
        { label: 'Rationale', key: 'rationale' },
      ], rows: data.assignments, mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Open assignments', 'open-cluster-assignments'), className: 'panel--wide' })}
      ${panel({ title: 'Clustering guardrails', eyebrow: 'Interpretability first', body: `<div class="guardrail-grid"><div><span class="guardrail-icon">⊕</span><strong>Past-only features</strong><p>Route vectors use a declared historical training window and do not include future outcomes.</p></div><div><span class="guardrail-icon">↗</span><strong>No latent labels</strong><p>Generator archetypes and hidden test truth never become dashboard features.</p></div><div><span class="guardrail-icon">?</span><strong>Confidence is visible</strong><p>Low-confidence assignments should remain reviewable rather than presented as facts.</p></div></div>` })}
    `,
  });
}

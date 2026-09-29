import { button, escapeHtml, panel, statusBadge } from '../components/ui.js';
import {
  kpiRow,
  pageFrame,
  panelHeaderActions,
  stateNotice,
  tablePanel,
} from './shared.js';

export function renderRecommendations({ data, mode }) {
  const insightCards = `<div class="insight-card-grid">${data.insights.map((insight) => `<article class="insight-card insight-card--${insight.tone}"><div class="insight-card__head"><span class="insight-card__type">${insight.type}</span>${statusBadge(insight.confidence, insight.tone)}</div><h3>${insight.title}</h3><p>${insight.detail}</p><div class="insight-card__footer"><span>${insight.tone === 'coral' ? 'Capacity lens' : insight.tone === 'amber' ? 'Evidence lens' : 'Scenario lens'}</span><button type="button" class="text-button" data-action="open-insight">Open evidence ${iconArrow()}</button></div></article>`).join('')}</div>`;
  return pageFrame({
    data,
    mode,
    updated: mode === 'demo' ? 'Demo insight fixtures · no recommendation claims' : 'Awaiting recommendations response',
    actions: `${button({ label: 'Evidence policy', action: 'open-evidence-policy', iconName: 'shield', variant: 'outline', size: 'sm' })}${button({ label: 'Export insights', action: 'export-insights', iconName: 'download', variant: 'outline', size: 'sm' })}`,
    children: `
      ${stateNotice(mode)}
      ${kpiRow(data.summary.map((item) => ({ ...item, icon: 'spark', delta: mode === 'demo' ? 'Demo queue' : 'Not supplied', unit: mode === 'demo' ? item.unit : 'API required', direction: item.tone === 'coral' ? 'attention' : 'neutral' })), mode, '', Boolean(data.__hasApiData))}
      <div class="recommendation-banner"><div class="recommendation-banner__mark">✦</div><div><strong>Recommendations are evidence-backed proposals, not automatic decisions.</strong><span>Each item should retain a source window, supporting metric, confidence, and owner so operators can validate before acting.</span></div><button type="button" class="text-button" data-action="open-evidence-policy">Read the evidence policy ${iconArrow()}</button></div>
      <section class="section-heading"><div><p class="eyebrow">Signal cards</p><h2 class="section-heading__title">What deserves a closer look</h2><p class="section-heading__description">Insight surfaces are prepared for evidence-linked explanations.</p></div><div class="section-heading__action">${statusBadge(mode === 'demo' ? 'Demo insight set' : 'API response pending', mode === 'demo' ? 'demo' : 'pending')}</div></section>
      ${mode === 'demo' ? insightCards : '<div class="empty-insight-grid"><span>✦</span><strong>Insights awaiting analytics API</strong><small>Evidence-backed recommendation cards will render when the service responds.</small></div>'}
      ${tablePanel({ title: 'Action queue', description: 'A reviewable handoff from evidence to an operational decision.', columns: [
        { label: 'Recommendation', key: 'title' },
        { label: 'Owner', key: 'owner' },
        { label: 'Evidence', render: (row) => `<span class="evidence-link">${escapeHtml(row.evidence)}</span>` },
        { label: 'Impact', render: (row) => statusBadge(row.impact, row.tone) },
        { label: 'State', render: (row) => statusBadge(row.status, row.tone) },
      ], rows: data.recommendations, mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Open action queue', 'open-action-queue'), className: 'panel--wide' })}
      <div class="dashboard-grid dashboard-grid--split">
        ${panel({ title: 'Insight evidence drawer', eyebrow: 'Prepared interaction', body: '<div class="drawer-placeholder"><span class="drawer-placeholder__icon">⌁</span><strong>Select an insight to inspect its evidence</strong><p>The future drawer will show supporting measures, source window, lineage, and uncertainty without hiding the underlying context.</p><button class="button button--outline button--sm" type="button" data-action="open-insight">Open demo insight</button></div>' })}
        ${panel({ title: 'Human-in-the-loop checklist', eyebrow: 'Before action', body: '<div class="checklist"><div class="checklist__row"><span class="checklist__mark checklist__mark--done">✓</span><span>Confirm the affected route or stop</span><span class="checklist__state">Required</span></div><div class="checklist__row"><span class="checklist__mark checklist__mark--pending">◷</span><span>Review supporting metrics</span><span class="checklist__state">Required</span></div><div class="checklist__row"><span class="checklist__mark checklist__mark--pending">◇</span><span>Check social-service context</span><span class="checklist__state">Required</span></div><div class="checklist__row"><span class="checklist__mark checklist__mark--pending">□</span><span>Run a what-if estimate</span><span class="checklist__state">Recommended</span></div></div>' })}
      </div>
    `,
  });
}

function iconArrow() { return '<span aria-hidden="true">→</span>'; }

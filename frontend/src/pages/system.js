import { button, escapeHtml, panel, statusBadge, timelineItem } from '../components/ui.js';
import { API_CAPABILITY_STATUS } from '../api/contracts.js';
import {
  kpiRow,
  hasProductionEvidence,
  pageFrame,
  panelHeaderActions,
  stateNotice,
  tablePanel,
} from './shared.js';

export function renderSystem({ data, mode }) {
  if (mode === 'api') return pageFrame({ data: { ...data, title: 'System / Evidence status', description: 'Certified artifact availability and serving scope.' }, mode, children: panel({ title: 'Infrastructure telemetry', description: 'Not configured', body: '<div class="availability-layout"><span class="availability-glyph" aria-hidden="true">⊞</span><div><h3>Evidence serving is independent of live infrastructure monitoring</h3><p class="muted">Hadoop, Spark, database health, uptime and event streams are not monitored by this workspace. Evidence refresh reads frozen artifacts through the existing API.</p></div></div>' }) });
  const pipeline = `<div class="pipeline-list">${data.pipeline.map((stage, index) => `<div class="pipeline-row"><span class="pipeline-row__index">${String(index + 1).padStart(2, '0')}</span><span class="pipeline-row__icon pipeline-row__icon--${stage.tone}"><span>${stage.icon === 'database' ? '▣' : stage.icon === 'shield' ? '◇' : stage.icon === 'sliders' ? '☷' : stage.icon === 'activity' ? '⌁' : '⊞'}</span></span><span class="pipeline-row__copy"><strong>${stage.stage}</strong><small>${stage.detail}</small></span>${statusBadge(stage.status, stage.tone, stage.tone === 'good' ? 'check' : 'clock')}<span class="pipeline-row__connector"></span></div>`).join('')}</div>`;
  const contracts = Object.entries(API_CAPABILITY_STATUS).map(([name, contract]) => ({ name: name.replace(/([A-Z])/g, ' $1'), path: contract.path, state: contract.state }));
  return pageFrame({
    data,
    mode,
    updated: mode === 'demo' ? 'Foundation status · no runtime connected' : 'Awaiting system status response',
    actions: `${button({ label: 'Refresh status', action: 'refresh', iconName: 'refresh', variant: 'outline', size: 'sm' })}${button({ label: 'Open logs', action: 'open-logs', iconName: 'external', variant: 'outline', size: 'sm' })}`,
    children: `
      ${stateNotice(mode, hasProductionEvidence(data))}
      ${kpiRow([
        { label: 'Frontend foundation', value: 'Ready', unit: 'local state', delta: 'Prepared', direction: 'up', icon: 'grid', tone: 'teal' },
        { label: 'Analytics service', value: 'Not connected', unit: 'runtime state', delta: 'Contract only', direction: 'attention', icon: 'activity', tone: 'amber' },
        { label: 'API contracts', value: '12', unit: 'capabilities', delta: 'Defined', direction: 'neutral', icon: 'route', tone: 'blue' },
        { label: 'Demo data mode', value: mode === 'demo' ? 'On' : 'Off', unit: 'UI state', delta: 'Isolated', direction: 'neutral', icon: 'database', tone: 'purple' },
      ], mode, '', Boolean(data.__hasApiData))}
      <div class="dashboard-grid dashboard-grid--system">
        ${panel({ title: 'Pipeline status', eyebrow: 'Contract lifecycle', description: 'The stages are visual integration points, not a claim that the pipeline has run.', body: pipeline })}
        ${panel({ title: 'Integration readiness', eyebrow: 'Adapters', body: `<div class="adapter-list">${data.sources.map((source) => `<div class="adapter-row"><span class="adapter-row__icon">${source.status === 'Not connected' ? '○' : source.status === 'Contract only' ? '◌' : '◇'}</span><span><strong>${source.name}</strong><small>${source.detail}</small></span>${statusBadge(source.status, source.tone, source.tone === 'good' ? 'check' : 'clock')}</div>`).join('')}</div>` })}
      </div>
      ${tablePanel({ title: 'Frontend capability contracts', description: 'Proposed paths are shown for integration planning only; none is represented as a completed backend endpoint.', columns: [
        { label: 'Capability', key: 'name' },
        { label: 'Proposed path', key: 'path', render: (row) => `<code>${escapeHtml(row.path)}</code>` },
        { label: 'State', render: (row) => statusBadge(row.state === 'contract-only' ? 'Contract only' : row.state, 'pending') },
      ], rows: contracts, mode, dataReady: Boolean(data.__hasApiData), action: panelHeaderActions('Open contract list', 'open-contracts'), className: 'panel--wide' })}
      <div class="dashboard-grid dashboard-grid--split">
        ${panel({ title: 'Event stream', eyebrow: 'Status timeline', body: `<div class="timeline">${data.events.map((event) => timelineItem(event)).join('')}</div>` })}
        ${panel({ title: 'Safe operating mode', eyebrow: 'Foundation defaults', body: '<div class="operating-mode"><div><span class="operating-mode__mark">✓</span><span><strong>No production data access</strong><small>The UI reads no dataset files or local database.</small></span></div><div><span class="operating-mode__mark">✓</span><span><strong>No analytics execution</strong><small>No ML, forecasting, clustering, or pipeline jobs run here.</small></span></div><div><span class="operating-mode__mark">✓</span><span><strong>Explicit source state</strong><small>Demo and API-ready states are visible at all times.</small></span></div><div><span class="operating-mode__mark">◇</span><span><strong>Map adapter optional</strong><small>Spatial rendering can remain a container until a provider is selected.</small></span></div></div>' })}
      </div>
    `,
  });
}

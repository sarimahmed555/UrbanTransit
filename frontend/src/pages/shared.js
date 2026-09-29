import { evidenceControls, evidenceVisualizations, exportButtons, filterSample } from '../components/evidence-analysis.js';
import { icon } from '../components/icons.js';
import { barChart, chartEmpty, donutChart, heatmap, lineChart, scatterPlot, sparkline } from '../components/charts.js';
import { networkMap } from '../components/map.js';
import {
  button,
  chartFrame,
  callout,
  chartState,
  demoNote,
  emptyState,
  escapeHtml,
  field,
  filterBar,
  kpiGrid,
  legend,
  notice,
  pageHeader,
  panel,
  pill,
  sectionHeading,
  statusBadge,
  statusDot,
  table,
} from '../components/ui.js';

export { barChart, chartEmpty, chartState, donutChart, heatmap, lineChart, networkMap, sparkline, scatterPlot };

export function pageFrame({ data, mode, children, actions = '', filter = null, updated = '' }) {
  const filterMarkup = filter
    ? typeof filter === 'string'
      ? filter
      : filterBar({ filters: filter, mode })
    : '';
  const evidence = mode === 'api' ? productionEvidencePanel(data.__productionEvidence) : '';
  const sourceLabel = mode === 'demo'
    ? 'DEMO / MOCK'
    : data.__productionEvidence?.state === 'ready'
      ? 'VERIFIED EVIDENCE'
      : data.__apiConnection === 'connected'
        ? 'EVIDENCE API CONNECTED'
        : data.__apiConnection === 'unavailable'
          ? 'EVIDENCE API UNAVAILABLE'
          : 'CONNECTING TO EVIDENCE API';
  return `${pageHeader({ eyebrow: data.eyebrow || 'Analytics workspace', title: data.title || data.name || 'Analytics view', description: data.description || '', mode, sourceLabel, updated: updated || data.lastUpdated || '', actions })}${filter ? `<div class="page-filter">${filterMarkup}</div>` : ''}${evidence}${children}`;
}

export function productionEvidencePanel(evidence) {
  if (!evidence) return '';
  if (evidence.state !== 'ready' || !evidence.data) {
    const description = evidence.state === 'loading'
      ? 'Loading audited result artifacts.'
      : evidence.state === 'error'
        ? 'The API request failed. Production evidence is not replaced by demo data.'
        : 'No verified evidence response is available for this view.';
    return panel({
      title: 'Verified model evidence',
      description,
      body: evidence.state === 'loading'
        ? '<p class="muted">No raw data scan, Spark job, or model training is run.</p>'
        : `<p class="muted">${escapeHtml(evidence.error?.message || evidence.message || 'Source unavailable')}</p>`,
      className: 'panel--wide evidence-panel',
    });
  }

  const payload = evidence.data;
  const filters = evidence.filters || {};
  let tasks = [];
  if (payload.kind === 'task' && payload.task) {
    tasks = [payload.task];
  } else if (payload.kind === 'tasks' || payload.kind === 'comparison') {
    tasks = payload.tasks || [];
  } else if (payload.kind === 'summary') {
    tasks = Array.isArray(payload.tasks) ? payload.tasks : payload.tasks?.data || [];
  }
  const taskRows = tasks.flatMap((task) => Object.entries(task?.pipelines || {}).map(([pipeline, result]) => {
    const metrics = result.test_metrics || {};
    const metricLabel = result.model_type === 'clustering'
      ? 'Silhouette'
      : result.model_type === 'regression'
        ? 'MAE'
        : 'Accuracy / macro-F1';
    const metricValue = result.model_type === 'clustering'
      ? `Silhouette ${formatMetric(metrics.silhouette)}`
      : result.model_type === 'regression'
        ? `MAE ${formatMetric(metrics.mae)}`
        : `Accuracy ${formatMetric(metrics.accuracy)} / F1 ${formatMetric(metrics.macro_f1)}`;
    const baseline = result.baseline_vs_selected?.metric
      ? `${escapeHtml(result.baseline_vs_selected.metric)} ${formatMetric(result.baseline_vs_selected.baseline)} → ${formatMetric(result.baseline_vs_selected.selected)}`
      : 'Not measured';
    const acceptance = result.acceptance?.passed === true
      ? 'PASS'
      : result.acceptance?.passed === false
        ? 'FAIL'
        : 'Not specified';
    return {
      task: task.task_name,
      pipeline: pipeline === 'spark' ? 'Spark MLlib' : 'Python',
      model: result.selected_model || '—',
      metric: metricValue,
      baseline,
      cases: result.sample_counts?.test ?? metrics.sample_count ?? '—',
      acceptance,
    };
  }));
  const metricDetails = tasks.flatMap(task => Object.entries(task?.pipelines || {}).map(([pipeline, result]) => `<details class="metric-detail"><summary>${escapeHtml(task.task_name.replaceAll('_', ' '))} / ${escapeHtml(pipeline === 'spark' ? 'Spark MLlib' : 'Python')} · Full evaluation record</summary><pre>${escapeHtml(JSON.stringify({ test_metrics: result.test_metrics, baseline: result.baseline_vs_selected, acceptance: result.acceptance, sample_counts: result.sample_counts }, null, 2))}</pre></details>`)).join('');
  const comparison = payload.comparison?.data || payload.comparison;
  const comparisonRows = Array.isArray(comparison?.mismatches)
    ? comparison.mismatches.map((item) => ({
      case_id: item.case_id,
      date: item.service_date,
      actual: item.actual,
      python: item.python_prediction,
      spark: item.spark_prediction,
      truth: item.truth_match ? 'Matched' : 'Mismatch',
    }))
    : [];
  const predictionRows = tasks.flatMap((task) => Object.entries(task?.pipelines || {}).flatMap(([pipeline, result]) =>
    (filters.pipeline && filters.pipeline !== pipeline ? [] : filterSample(result.prediction_sample, filters)).map((item) => ({
      task: task.task_name,
      pipeline: pipeline === 'spark' ? 'Spark MLlib' : 'Python',
      case_id: typeof item.case_id === 'string' ? `${item.case_id.slice(0, 14)}…` : '—',
      target: item.target_start || item.target_end || '—',
      actual: item.actual,
      prediction: item.prediction,
    })),
  ));
  const comparisonSummary = comparison?.shared_test_cases ? `<div class="comparison-scoreboard">${[
    ['Shared cases', comparison.shared_test_cases.toLocaleString()], ['Matching truths', comparison.truths_matched.toLocaleString()], ['Prediction agreements', comparison.prediction_agreements.toLocaleString()], ['Disagreements', comparison.prediction_disagreements.toLocaleString()], ['Agreement', `${(comparison.agreement_rate * 100).toFixed(4)}%`],
  ].map(([label,value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join('')}</div>` : '';
  const comparisonMarkup = comparison?.shared_test_cases
    ? `<div class="evidence-comparison" role="group" aria-label="Verified delay comparison"><strong>Delay comparison:</strong> ${comparison.shared_test_cases.toLocaleString()} shared held-out cases · ${comparison.truths_matched.toLocaleString()} truths matched · ${comparison.prediction_agreements.toLocaleString()} agreements · ${comparison.prediction_disagreements.toLocaleString()} disagreements · ${(comparison.agreement_rate * 100).toFixed(4)}% agreement.</div>${table({ columns: [
      { label: 'Hashed case ID', key: 'case_id' },
      { label: 'Service date', key: 'date' },
      { label: 'Actual truth', key: 'actual' },
      { label: 'Python', key: 'python' },
      { label: 'Spark', key: 'spark' },
      { label: 'Truth alignment', key: 'truth' },
    ], rows: comparisonRows, mode: 'api', dataReady: true, emptyTitle: 'No disagreements recorded' })}`
    : '';
  const limitations = [
    ...(payload.limitations || []),
    ...(payload.summary?.limitations || []),
    ...tasks.flatMap((task) => task.limitations || []),
    ...(comparison?.limitations || []),
  ];
  const partialTasks = payload.summary?.partial_tasks || [];
  const completedTasks = payload.summary?.completed_predictive_tasks || [];
  const heading = payload.kind === 'comparison'
    ? 'Verified Spark / Python comparison'
    : payload.kind === 'summary'
      ? 'Verified project result status'
      : 'Verified model evaluation results';
  return panel({
    title: heading,
    description: 'Frozen results from audited production-v1.1 artifacts. These are model evaluations, not live service KPIs.',
    body: `${payload.summary?.serving_database ? `<p class="api-note">PostgreSQL serving state: ${escapeHtml(payload.summary.serving_database.state)}</p>` : ''}${comparisonSummary}${payload.kind === 'comparison' ? exportButtons('delay-comparison') : ''}${tasks.some(t => Object.values(t.pipelines || {}).some(p => p.prediction_sample)) ? evidenceControls(filters) + evidenceVisualizations(tasks, filters) : ''}${taskRows.length ? table({ columns: [
      { label: 'Task', key: 'task' },
      { label: 'Pipeline', key: 'pipeline' },
      { label: 'Selected model', key: 'model' },
      { label: 'Held-out metric', key: 'metric' },
      { label: 'Baseline', key: 'baseline' },
      { label: 'Test cases', key: 'cases' },
      { label: 'Acceptance', key: 'acceptance' },
    ], rows: taskRows, mode: 'api', dataReady: true }) : ''}${completedTasks.length ? `<p class="muted">Completed predictive evaluations: ${completedTasks.map(escapeHtml).join(', ')}.</p>` : ''}${predictionRows.length ? `<h3 class="evidence-subheading">Persisted held-out prediction sample</h3>${table({ columns: [
      { label: 'Task', key: 'task' },
      { label: 'Pipeline', key: 'pipeline' },
      { label: 'Case', key: 'case_id' },
      { label: 'Target start', key: 'target' },
      { label: 'Actual', key: 'actual' },
      { label: 'Prediction', key: 'prediction' },
    ], rows: predictionRows, mode: 'api', dataReady: true })}` : ''}${comparisonMarkup}${metricDetails ? `<div class="metric-details"><h3 class="evidence-subheading">Evaluation details</h3>${metricDetails}</div>` : ''}${comparison?.source_hashes ? `<details class="metric-detail"><summary>Frozen comparison provenance / source hashes</summary><pre>${escapeHtml(JSON.stringify(comparison.source_hashes, null, 2))}</pre></details>` : ''}${partialTasks.length ? `<div class="evidence-limitations"><strong>Partial analytics</strong><ul>${partialTasks.map((item) => `<li>${escapeHtml(item.task_name)}: ${escapeHtml(item.reason)}</li>`).join('')}</ul></div>` : ''}${limitations.length ? `<div class="evidence-limitations"><strong>Limitations</strong><ul>${[...new Set(limitations)].map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>` : ''}<small class="api-note">Source: ${escapeHtml(evidence.meta?.source || 'audited result artifacts')} · Dataset: production-v1.1</small>`,
    className: 'panel--wide evidence-panel',
  });
}

function formatMetric(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

export function kpiRow(items, mode, className = '', dataReady = false) {
  return kpiGrid(items, mode, { className, dataReady });
}

export function chartPanel({ title, subtitle, chart, source = 'demo', legendItems = [], action = '', footer = '', className = '' }) {
  return chartFrame({ title, subtitle, body: chart, source, legend: legendItems.length ? legend(legendItems) : '', action, footer, className });
}

export function tablePanel({ title, description, columns, rows, mode, dataReady = false, action = '', className = '' }) {
  return panel({
    title,
    description,
    action,
    className,
    body: table({ columns, rows, mode, dataReady }),
  });
}

export function routeNameCell(row) {
  return `<div class="entity-cell"><span class="entity-cell__mark">${escapeHtml((row.code || row.route || 'R').slice(0, 2))}</span><span><strong>${escapeHtml(row.name || row.route || '—')}</strong><small>${escapeHtml(row.code || row.route || '')}</small></span></div>`;
}

export function stopNameCell(row) {
  return `<div class="entity-cell"><span class="entity-cell__mark entity-cell__mark--stop">${icon('map', 14)}</span><span><strong>${escapeHtml(row.name || '—')}</strong><small>${escapeHtml(row.code || row.zone || '')}</small></span></div>`;
}

export function trendCell(value, trend) {
  const positive = String(trend || '').startsWith('+');
  const negative = String(trend || '').startsWith('-');
  return `<span class="trend-cell ${positive ? 'is-positive' : negative ? 'is-negative' : ''}">${sparkline({ values: positive ? [4, 5, 5, 7, 8] : negative ? [8, 7, 7, 5, 4] : [4, 5, 5, 5, 5], colorName: positive ? 'teal' : negative ? 'coral' : 'slate', width: 58, height: 24, label: 'Demo trend' })}<strong>${escapeHtml(value || '—')}</strong></span>`;
}

export function statusCell(row, field = 'status') {
  return statusBadge(row[field] || 'Awaiting API', row.tone || 'pending');
}

export function apiPlaceholder({ title, description, compact = false, action = '' }) {
  return emptyState({ title, description, compact, iconName: 'database', action });
}

export function signalList(items = []) {
  return `<div class="signal-list">${items.map((item) => `<div class="signal-item signal-item--${escapeHtml(item.tone || 'neutral')}"><span class="signal-item__icon">${icon(item.icon || 'activity', 16)}</span><span class="signal-item__copy"><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.detail)}</small></span><span class="signal-item__value">${escapeHtml(item.value)}</span></div>`).join('')}</div>`;
}

export function numberedInsightList(items = []) {
  return `<div class="numbered-list">${items.map((item) => `<div class="numbered-list__item"><span class="numbered-list__rank">${escapeHtml(item.rank)}</span><span class="numbered-list__body"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.detail)}</span></span>${item.tag ? pill(item.tag, item.tone || 'neutral') : ''}</div>`).join('')}</div>`;
}

export function filterRow({ mode, filters, action = '' }) {
  return filterBar({ filters, mode, actions: action });
}

export function readinessRow(items = []) {
  return `<div class="readiness-list">${items.map((item) => `<div class="readiness-row"><span class="readiness-row__label">${escapeHtml(item.label)}</span><span class="readiness-row__value">${statusBadge(item.value, item.status === 'good' ? 'good' : 'pending', item.status === 'good' ? 'check' : 'clock')}</span></div>`).join('')}</div>`;
}

export function metricComparison({ label, baseline, scenario, delta = '' }) {
  return `<div class="comparison-metric"><span>${escapeHtml(label)}</span><div><strong>${escapeHtml(baseline)}</strong><span class="comparison-arrow">→</span><strong>${escapeHtml(scenario)}</strong></div>${delta ? `<small>${escapeHtml(delta)}</small>` : ''}</div>`;
}

export function chartOrApi(mode, chart, emptyTitle, emptyDescription, action = '') {
  return mode === 'demo' ? chart : emptyState({ title: emptyTitle, description: emptyDescription, action, iconName: 'chart' });
}

export function stateNotice(mode, apiReady = false) {
  if (mode === 'api' && !apiReady) return notice({ tone: 'info', title: 'Awaiting processed data', description: 'Analytics and model results will appear here when a verified source is connected.', iconName: 'plug' });
  return '';
}

export function hasProductionEvidence(data) {
  return data?.__productionEvidence?.state === 'ready';
}

export function demoBlock(markup, mode, placeholder) {
  return mode === 'demo' ? markup : placeholder;
}

export function kpiOrPending(items, mode) {
  return kpiRow(items, mode);
}

export function panelHeaderActions(label, action = 'export') {
  return button({ label, action, iconName: 'download', variant: 'outline', size: 'sm' });
}

export function fieldGrid(fields = []) {
  return `<div class="field-grid">${fields.map((fieldItem) => field(fieldItem)).join('')}</div>`;
}

export function sourceFooter(mode) {
  return mode === 'demo' ? demoNote('Synthetic UI fixture', mode) : '<span class="api-note">API response contract</span>';
}

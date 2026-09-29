import { icon } from './icons.js';

export function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

export function sourceLabel(mode) {
  return mode === 'demo' ? 'DEMO / MOCK' : 'AWAITING DATA';
}

export function sourceTag(mode, label = sourceLabel(mode)) {
  return `<span class="source-tag source-tag--${mode === 'demo' ? 'demo' : 'api'}"><span class="source-tag__dot"></span>${escapeHtml(label)}</span>`;
}

export function statusBadge(label, tone = 'neutral', iconName = '') {
  const iconMarkup = iconName ? icon(iconName, 13, 'status-badge__icon') : '';
  return `<span class="status-badge status-badge--${escapeHtml(tone)}">${iconMarkup}${escapeHtml(label)}</span>`;
}

export function demoValue(value, mode, fallback = '—') {
  return mode === 'demo' ? escapeHtml(value ?? fallback) : `<span class="pending-value">${escapeHtml(fallback)}</span>`;
}

export function demoNote(label = 'Synthetic preview', mode = 'demo') {
  return mode === 'demo'
    ? `<span class="demo-note"><span class="demo-note__mark">D</span>${escapeHtml(label)}</span>`
    : '<span class="api-note">Awaiting API response</span>';
}

export function kpiCard(item, mode = 'demo', options = {}) {
  const tone = item.tone || 'teal';
  const dataReady = mode === 'demo' || options.dataReady === true;
  const directionIcon = item.direction === 'up' ? 'arrowUp' : item.direction === 'down' ? 'arrowDown' : 'spark';
  const directionClass = item.direction === 'up' ? 'positive' : item.direction === 'down' ? 'negative' : item.direction === 'attention' ? 'attention' : 'neutral';
  const delta = dataReady ? (item.delta || 'Awaiting API') : 'Awaiting API';
  const unit = dataReady ? item.unit : (item.apiUnit || 'API required');
  const note = dataReady ? item.note : (item.apiNote || 'Awaiting API response');
  return `
    <article class="kpi-card kpi-card--${escapeHtml(tone)} ${options.compact ? 'kpi-card--compact' : ''}" ${mode === 'demo' ? 'data-demo="true"' : ''}>
      <div class="kpi-card__topline">
        <span class="kpi-card__label">${escapeHtml(item.label)}</span>
        <span class="kpi-card__icon">${icon(item.icon || 'pulse', 17)}</span>
      </div>
      <div class="kpi-card__value">${dataReady ? escapeHtml(item.value ?? '—') : '<span class="pending-value">—</span>'}</div>
      <div class="kpi-card__meta">
        <span class="kpi-card__delta kpi-card__delta--${directionClass}">${icon(directionIcon, 13)} ${escapeHtml(delta)}</span>
        ${unit ? `<span class="kpi-card__unit">${escapeHtml(unit)}</span>` : ''}
      </div>
      ${note ? `<div class="kpi-card__note">${mode === 'demo' ? '<span class="demo-mini-mark">DEMO</span>' : icon('info', 12)} ${escapeHtml(note)}</div>` : ''}
    </article>`;
}

export function kpiGrid(items, mode = 'demo', options = {}) {
  return `<div class="kpi-grid ${options.className || ''}">${items.map((item) => kpiCard(item, mode, options)).join('')}</div>`;
}

export function panel({ title, eyebrow = '', description = '', action = '', body = '', className = '', tone = '' }) {
  return `
    <section class="panel ${tone ? `panel--${tone}` : ''} ${className}" ${tone === 'loading' ? 'aria-busy="true"' : ''}>
      ${(title || eyebrow || description || action) ? `<div class="panel__header">
        <div>
          ${eyebrow ? `<p class="eyebrow">${escapeHtml(eyebrow)}</p>` : ''}
          ${title ? `<h2 class="panel__title">${escapeHtml(title)}</h2>` : ''}
          ${description ? `<p class="panel__description">${escapeHtml(description)}</p>` : ''}
        </div>
        ${action ? `<div class="panel__action">${action}</div>` : ''}
      </div>` : ''}
      <div class="panel__body">${body}</div>
    </section>`;
}

export function sectionHeading({ eyebrow = '', title, description = '', action = '' }) {
  return `<div class="section-heading">
    <div>
      ${eyebrow ? `<p class="eyebrow">${escapeHtml(eyebrow)}</p>` : ''}
      <h2 class="section-heading__title">${escapeHtml(title)}</h2>
      ${description ? `<p class="section-heading__description">${escapeHtml(description)}</p>` : ''}
    </div>
    ${action ? `<div class="section-heading__action">${action}</div>` : ''}
  </div>`;
}

export function pageHeader({ eyebrow, title, description, mode, sourceLabel, updated = '', actions = '' }) {
  return `<div class="page-header">
    <div class="page-header__copy">
      <div class="page-header__eyebrow"><span class="eyebrow">${escapeHtml(eyebrow)}</span>${sourceTag(mode, sourceLabel)}</div>
      <h1>${escapeHtml(title)}</h1>
      <p>${escapeHtml(description)}</p>
    </div>
    <div class="page-header__aside">
      ${updated ? `<div class="last-updated">${icon('clock', 14)}<span>${escapeHtml(updated)}</span></div>` : ''}
      ${actions ? `<div class="page-header__actions">${actions}</div>` : ''}
    </div>
  </div>`;
}

export function button({ label, action, iconName = '', variant = 'secondary', size = 'md', disabled = false, type = 'button', extra = '' }) {
  // Exports require an authenticated report adapter; never imply a download occurred.
  disabled = disabled || action === 'export' || Boolean(action?.startsWith('export-'));
  return `<button class="button button--${escapeHtml(variant)} button--${escapeHtml(size)}" type="${escapeHtml(type)}" ${action ? `data-action="${escapeHtml(action)}"` : ''} ${disabled ? 'disabled' : ''} ${extra}>${iconName ? icon(iconName, size === 'sm' ? 15 : 16) : ''}<span>${escapeHtml(label)}</span></button>`;
}

export function iconButton({ label, action, iconName = 'more', variant = 'ghost', size = 'md', extra = '' }) {
  return `<button class="icon-button icon-button--${escapeHtml(variant)} icon-button--${escapeHtml(size)}" type="button" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}" data-action="${escapeHtml(action)}" ${extra}>${icon(iconName, size === 'sm' ? 16 : 18)}</button>`;
}

export function filterBar({ filters = [], mode = 'demo', actions = '' }) {
  return `<div class="filter-bar">
    <div class="filter-bar__fields">
      ${filters.map((filter) => {
        const options = mode === 'api' && filter.demoOptions
          ? [filter.options[0] || 'All', 'Select from API']
          : filter.options;
        return `<label class="filter-control">
        <span class="filter-control__label">${escapeHtml(filter.label)}</span>
        <span class="filter-control__select-wrap">${icon(filter.icon || 'filter', 14)}<select data-filter="${escapeHtml(filter.key)}" aria-label="${escapeHtml(filter.label)}">${options.map((option) => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`).join('')}</select>${icon('chevron', 14, 'filter-control__chevron')}</span>
      </label>`;
      }).join('')}
    </div>
    <div class="filter-bar__actions">${mode === 'demo' ? '<span class="filter-context">Demo filter context</span>' : '<span class="filter-context filter-context--api">API filter context</span>'}${actions}</div>
  </div>`;
}

export function segmentedControl({ items = [], active = '', action = 'set-segment', label = 'View' }) {
  return `<div class="segmented-control" role="group" aria-label="${escapeHtml(label)}">${items.map((item) => {
    const value = typeof item === 'string' ? item : item.value;
    const text = typeof item === 'string' ? item : item.label;
    return `<button type="button" class="segmented-control__item ${value === active ? 'is-active' : ''}" data-action="${escapeHtml(action)}" data-value="${escapeHtml(value)}">${escapeHtml(text)}</button>`;
  }).join('')}</div>`;
}

export function table({ columns = [], rows = [], mode = 'demo', dataReady = false, emptyTitle = 'No records available', emptyDescription = 'Results will appear when the analytics service is connected.', rowKey = 'id', className = '' }) {
  const visibleRows = mode === 'demo' || dataReady ? rows : [];
  return `<div class="table-wrap ${className}" data-source="${mode === 'demo' ? 'demo' : 'api'}">
    <table class="data-table">
      <thead><tr>${columns.map((column) => `<th scope="col" class="${column.align ? `is-${column.align}` : ''}">${escapeHtml(column.label)}</th>`).join('')}</tr></thead>
      <tbody>${visibleRows.length ? visibleRows.map((row, index) => `<tr data-row-key="${escapeHtml(row[rowKey] ?? index)}">${columns.map((column) => {
        const value = typeof column.render === 'function' ? column.render(row, index) : escapeHtml(row[column.key] ?? '—');
        return `<td class="${column.align ? `is-${column.align}` : ''}">${value}</td>`;
      }).join('')}</tr>`).join('') : `<tr><td colspan="${Math.max(columns.length, 1)}">${emptyState({ title: emptyTitle, description: emptyDescription, compact: true })}</td></tr>`}</tbody>
    </table>
  </div>`;
}

export function chartFrame({ title, subtitle = '', legend = '', body, footer = '', className = '', action = '', source = 'demo' }) {
  return `<section class="chart-panel ${className}" ${source === 'demo' ? 'data-demo="true"' : ''}>
    <div class="chart-panel__header">
      <div>
        <h3>${escapeHtml(title)}</h3>
        ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ''}
      </div>
      <div class="chart-panel__header-right">${legend}${action}</div>
    </div>
    <div class="chart-panel__body">${body}</div>
    ${footer ? `<div class="chart-panel__footer">${footer}</div>` : ''}
  </section>`;
}

export function legend(items = []) {
  return `<div class="legend">${items.map((item) => `<span class="legend__item"><span class="legend__swatch legend__swatch--${escapeHtml(item.color)}"></span>${escapeHtml(item.label)}</span>`).join('')}</div>`;
}

export function chartState({ state = 'empty', title = '', description = '', compact = false }) {
  if (state === 'loading') return loadingState({ rows: compact ? 2 : 4, compact });
  if (state === 'error') return errorState({ title: title || 'Unable to load visualization', description: description || 'The analytics service did not return a usable response.', compact });
  return emptyState({ title: title || 'Visualization awaiting data', description: description || 'Connect the analytics API to render this view.', compact });
}

export function loadingState({ rows = 4, compact = false, label = 'Loading analytics view' } = {}) {
  return `<div class="state-block state-block--loading ${compact ? 'state-block--compact' : ''}" aria-label="${escapeHtml(label)}" aria-busy="true">
    <div class="state-block__spinner"></div>
    <div class="state-block__copy"><strong>${escapeHtml(label)}</strong><span>Preparing the visualization interface…</span></div>
    <div class="skeleton-lines">${Array.from({ length: rows }, (_, index) => `<span class="skeleton-line skeleton-line--${index % 2 ? 'short' : 'full'}"></span>`).join('')}</div>
  </div>`;
}

export function emptyState({ title = 'Nothing to show yet', description = 'Results will appear when a data source is connected.', iconName = 'database', action = '', compact = false } = {}) {
  return `<div class="state-block ${compact ? 'state-block--compact' : ''}">
    <span class="state-block__icon">${icon(iconName, 22)}</span>
    <div class="state-block__copy"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(description)}</span></div>
    ${action}
  </div>`;
}

export function errorState({ title = 'Something went wrong', description = 'The analytics service could not be reached.', action = '', compact = false } = {}) {
  return `<div class="state-block state-block--error ${compact ? 'state-block--compact' : ''}">
    <span class="state-block__icon">${icon('alert', 22)}</span>
    <div class="state-block__copy"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(description)}</span></div>
    ${action || button({ label: 'Try again', action: 'refresh', iconName: 'refresh', variant: 'outline', size: 'sm' })}
  </div>`;
}

export function notice({ tone = 'info', title, description = '', iconName = tone === 'warning' ? 'warning' : 'info', action = '' }) {
  return `<div class="notice notice--${escapeHtml(tone)}">
    <span class="notice__icon">${icon(iconName, 18)}</span>
    <div class="notice__copy"><strong>${escapeHtml(title)}</strong>${description ? `<span>${escapeHtml(description)}</span>` : ''}</div>
    ${action ? `<div class="notice__action">${action}</div>` : ''}
  </div>`;
}

export function progressBar({ value, label = '', tone = 'teal', showValue = true }) {
  const safeValue = Math.max(0, Math.min(100, Number(value) || 0));
  return `<div class="progress-row">
    ${label ? `<span class="progress-row__label">${escapeHtml(label)}</span>` : ''}
    <span class="progress-track"><span class="progress-fill progress-fill--${escapeHtml(tone)}" style="width:${safeValue}%"></span></span>
    ${showValue ? `<span class="progress-row__value">${escapeHtml(value ?? '—')}</span>` : ''}
  </div>`;
}

export function inlineMetric({ label, value, detail = '', tone = 'neutral' }) {
  return `<div class="inline-metric inline-metric--${escapeHtml(tone)}"><span class="inline-metric__label">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${detail ? `<span class="inline-metric__detail">${escapeHtml(detail)}</span>` : ''}</div>`;
}

export function callout({ iconName = 'spark', title, description = '', tone = 'teal', action = '' }) {
  return `<div class="callout callout--${escapeHtml(tone)}">
    <span class="callout__icon">${icon(iconName, 20)}</span>
    <div class="callout__copy"><strong>${escapeHtml(title)}</strong>${description ? `<span>${escapeHtml(description)}</span>` : ''}</div>
    ${action ? `<div class="callout__action">${action}</div>` : ''}
  </div>`;
}

export function splitColumns(left, right, className = '') {
  return `<div class="split-columns ${className}">${left}${right}</div>`;
}

export function demoBanner() {
  return `<div class="demo-banner" role="status">
    <span class="demo-banner__icon">${icon('spark', 15)}</span>
    <span><strong>Demo preview</strong> · Synthetic UI fixtures are visible for layout development. Values are not production results and will be replaced by API responses.</span>
    <button type="button" class="demo-banner__dismiss" data-action="dismiss-demo-banner" aria-label="Dismiss demo notice">${icon('close', 15)}</button>
  </div>`;
}

export function apiBanner() {
  return `<div class="api-banner" role="status">
    <span class="api-banner__icon">${icon('plug', 15)}</span>
    <span><strong>API-ready state</strong> · No analytics adapter is connected. Unverified values are intentionally hidden.</span>
  </div>`;
}

export function statusDot(tone = 'neutral') {
  return `<span class="status-dot status-dot--${escapeHtml(tone)}"></span>`;
}

export function pill(label, tone = 'neutral', iconName = '') {
  return `<span class="pill pill--${escapeHtml(tone)}">${iconName ? icon(iconName, 13) : ''}${escapeHtml(label)}</span>`;
}

export function field({ label, value, detail = '', iconName = '', action = '' }) {
  return `<div class="field-card">${iconName ? `<span class="field-card__icon">${icon(iconName, 17)}</span>` : ''}<div class="field-card__copy"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${detail ? `<small>${escapeHtml(detail)}</small>` : ''}</div>${action ? `<div class="field-card__action">${action}</div>` : ''}</div>`;
}

export function timelineItem({ time, title, detail, tone = 'neutral', iconName = 'activity' }) {
  return `<div class="timeline-item"><span class="timeline-item__marker timeline-item__marker--${escapeHtml(tone)}">${icon(iconName, 14)}</span><div class="timeline-item__time">${escapeHtml(time)}</div><div class="timeline-item__copy"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div></div>`;
}

import { escapeHtml } from './ui.js';

const WIDTH = 720;
const HEIGHT = 270;
const PAD = { top: 22, right: 22, bottom: 36, left: 42 };

const COLOR_MAP = {
  teal: '#18b6a4',
  blue: '#4b8df8',
  coral: '#f26b5e',
  amber: '#f2b84b',
  purple: '#9b7cf6',
  slate: '#8293a7',
};

function color(value) {
  return COLOR_MAP[value] ?? value ?? COLOR_MAP.teal;
}

function number(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function niceMax(value) {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / magnitude) * magnitude;
}

function linePath(values, x, y) {
  let path = '';
  let penDown = false;
  values.forEach((value, index) => {
    if (value === null || value === undefined) {
      penDown = false;
      return;
    }
    const command = penDown ? 'L' : 'M';
    path += `${command}${x(index).toFixed(1)},${y(value).toFixed(1)} `;
    penDown = true;
  });
  return path.trim();
}

function areaPath(values, x, y, baseline) {
  const valid = values.map((value, index) => ({ value, index })).filter(({ value }) => value !== null && value !== undefined);
  if (!valid.length) return '';
  const first = valid[0];
  const last = valid[valid.length - 1];
  return `M${x(first.index).toFixed(1)},${baseline.toFixed(1)} L${valid.map(({ value, index }) => `${x(index).toFixed(1)},${y(value).toFixed(1)}`).join(' L')} L${x(last.index).toFixed(1)},${baseline.toFixed(1)} Z`;
}

function grid({ y, labels, format = (value) => value }) {
  return Array.from({ length: 5 }, (_, index) => {
    const value = y(index / 4);
    const label = format(index / 4);
    return `<line class="chart-grid-line" x1="${PAD.left}" x2="${WIDTH - PAD.right}" y1="${value}" y2="${value}"/><text class="chart-axis-label" x="${PAD.left - 10}" y="${value + 4}" text-anchor="end">${escapeHtml(label)}</text>`;
  }).join('');
}

function xLabels(labels, x) {
  return labels.map((label, index) => `<text class="chart-axis-label" x="${x(index)}" y="${HEIGHT - 10}" text-anchor="middle">${escapeHtml(label)}</text>`).join('');
}

function getBounds(series) {
  const values = series.flatMap((item) => item.values).map(number).filter((value) => value !== null);
  const minValue = values.length ? Math.min(...values) : 0;
  const maxValue = values.length ? Math.max(...values) : 1;
  const spread = maxValue - minValue || 1;
  return {
    min: minValue - spread * 0.12,
    max: maxValue + spread * 0.12,
  };
}

/**
 * Lightweight SVG line/area chart. A charting library can replace this
 * implementation without changing page contracts: pass API series to the
 * same shape (`{ values, color, label }`).
 */
export function lineChart({ labels = [], series = [], height = HEIGHT, ariaLabel = 'Line chart', showGrid = true, formatValue = (value) => Math.round(value) } = {}) {
  const normalized = series.map((item) => ({ ...item, values: item.values.map((value) => number(value)) }));
  if (!labels.length || !normalized.some((item) => item.values.some((value) => value !== null))) {
    return chartEmpty(height, ariaLabel);
  }
  const bounds = getBounds(normalized);
  const x = (index) => PAD.left + (index / Math.max(labels.length - 1, 1)) * (WIDTH - PAD.left - PAD.right);
  const y = (value) => PAD.top + (1 - (value - bounds.min) / (bounds.max - bounds.min || 1)) * (height - PAD.top - PAD.bottom);
  const gridMarkup = showGrid
    ? Array.from({ length: 5 }, (_, index) => {
        const ratio = index / 4;
        const value = bounds.max - ratio * (bounds.max - bounds.min);
        const yPosition = PAD.top + ratio * (height - PAD.top - PAD.bottom);
        return `<line class="chart-grid-line" x1="${PAD.left}" x2="${WIDTH - PAD.right}" y1="${yPosition}" y2="${yPosition}"/><text class="chart-axis-label" x="${PAD.left - 10}" y="${yPosition + 4}" text-anchor="end">${escapeHtml(formatValue(value))}</text>`;
      }).join('')
    : '';
  const areas = normalized.filter((item) => item.area).map((item) => `<path class="chart-area chart-area--${escapeHtml(item.color || 'teal')}" d="${areaPath(item.values, x, y, height - PAD.bottom)}"/>`).join('');
  const paths = normalized.map((item) => {
    const dash = item.dashed ? ' stroke-dasharray="6 6"' : '';
    return `<path class="chart-line chart-line--${escapeHtml(item.color || 'teal')}" d="${linePath(item.values, x, y)}"${dash}/>`;
  }).join('');
  const points = normalized.flatMap((item) => item.values.map((value, index) => ({ value, index, color: item.color })).filter(({ value }) => value !== null).map(({ value, index, color: pointColor }) => `<circle class="chart-point chart-point--${escapeHtml(pointColor || 'teal')}" cx="${x(index)}" cy="${y(value)}" r="3.5"><title>${escapeHtml(item.label || 'Series')}: ${escapeHtml(formatValue(value))}</title></circle>`).slice(-24)).join('');
  return `<svg class="chart-svg" viewBox="0 0 ${WIDTH} ${height}" role="img" aria-label="${escapeHtml(ariaLabel)}" preserveAspectRatio="none">
    <title>${escapeHtml(ariaLabel)}</title>${gridMarkup}${areas}${paths}${points}${xLabels(labels, x)}
  </svg>`;
}

export function barChart({ labels = [], values = [], colors = [], height = HEIGHT, ariaLabel = 'Bar chart', showValues = true } = {}) {
  if (!labels.length || !values.length) return chartEmpty(height, ariaLabel);
  const numeric = values.map(number);
  const max = niceMax(Math.max(...numeric.filter((value) => value !== null), 1));
  const plotWidth = WIDTH - PAD.left - PAD.right;
  const plotHeight = height - PAD.top - PAD.bottom;
  const slot = plotWidth / labels.length;
  const barWidth = Math.min(44, slot * 0.62);
  const bars = numeric.map((value, index) => {
    if (value === null) return '';
    const barHeight = (value / max) * plotHeight;
    const xPosition = PAD.left + slot * index + (slot - barWidth) / 2;
    const yPosition = height - PAD.bottom - barHeight;
    const fill = color(colors[index] || 'teal');
    return `<rect class="chart-bar" x="${xPosition}" y="${yPosition}" width="${barWidth}" height="${barHeight}" rx="6" fill="${fill}"><title>${escapeHtml(labels[index])}: ${escapeHtml(value)}</title></rect>${showValues ? `<text class="chart-bar-value" x="${xPosition + barWidth / 2}" y="${Math.max(18, yPosition - 8)}" text-anchor="middle">${escapeHtml(value)}</text>` : ''}`;
  }).join('');
  return `<svg class="chart-svg" viewBox="0 0 ${WIDTH} ${height}" role="img" aria-label="${escapeHtml(ariaLabel)}" preserveAspectRatio="none">
    <title>${escapeHtml(ariaLabel)}</title>${Array.from({ length: 5 }, (_, index) => {
      const ratio = index / 4;
      const yPosition = PAD.top + ratio * plotHeight;
      return `<line class="chart-grid-line" x1="${PAD.left}" x2="${WIDTH - PAD.right}" y1="${yPosition}" y2="${yPosition}"/>`;
    }).join('')}${bars}${xLabels(labels, (index) => PAD.left + slot * index + slot / 2)}
  </svg>`;
}

export function sparkline({ values = [], colorName = 'teal', width = 120, height = 34, label = 'Trend' } = {}) {
  const numeric = values.map(number);
  if (!numeric.some((value) => value !== null)) return `<span class="sparkline-empty">—</span>`;
  const min = Math.min(...numeric.filter((value) => value !== null));
  const max = Math.max(...numeric.filter((value) => value !== null));
  const x = (index) => (index / Math.max(numeric.length - 1, 1)) * width;
  const y = (value) => height - 4 - ((value - min) / (max - min || 1)) * (height - 8);
  return `<svg class="sparkline" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(label)}"><title>${escapeHtml(label)}</title><path d="${linePath(numeric, x, y)}" fill="none" stroke="${color(colorName)}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

export function donutChart({ segments = [], totalLabel = '', height = 220, ariaLabel = 'Distribution chart' } = {}) {
  if (!segments.length || !segments.some((segment) => number(segment.value) > 0)) return chartEmpty(height, ariaLabel);
  const radius = 68;
  const circumference = 2 * Math.PI * radius;
  const total = segments.reduce((sum, segment) => sum + (number(segment.value) || 0), 0) || 1;
  let offset = 0;
  const arcs = segments.map((segment) => {
    const fraction = (number(segment.value) || 0) / total;
    const dash = fraction * circumference;
    const arc = `<circle class="donut-arc donut-arc--${escapeHtml(segment.color || 'teal')}" cx="110" cy="105" r="${radius}" fill="none" stroke-width="18" stroke-dasharray="${dash} ${circumference - dash}" stroke-dashoffset="${-offset}" transform="rotate(-90 110 105)"><title>${escapeHtml(segment.label)}: ${escapeHtml(segment.value)}</title></circle>`;
    offset += dash;
    return arc;
  }).join('');
  return `<div class="donut-layout"><svg class="donut-chart" viewBox="0 0 220 ${height}" role="img" aria-label="${escapeHtml(ariaLabel)}"><title>${escapeHtml(ariaLabel)}</title><circle cx="110" cy="105" r="${radius}" fill="none" stroke="#edf1f5" stroke-width="18"/>${arcs}<text class="donut-chart__total" x="110" y="102" text-anchor="middle">${escapeHtml(totalLabel || total)}</text><text class="donut-chart__caption" x="110" y="123" text-anchor="middle">${escapeHtml(totalLabel ? 'total' : 'demo')}</text></svg><div class="donut-legend">${segments.map((segment) => `<div class="donut-legend__item"><span class="legend__swatch legend__swatch--${escapeHtml(segment.color || 'teal')}"></span><span>${escapeHtml(segment.label)}</span><strong>${escapeHtml(segment.value)}</strong></div>`).join('')}</div></div>`;
}

export function scatterPlot({ points = [], height = 300, ariaLabel = 'Cluster scatter plot' } = {}) {
  if (!points.length) return chartEmpty(height, ariaLabel);
  const width = WIDTH;
  const pad = { top: 24, right: 28, bottom: 42, left: 46 };
  const x = (value) => pad.left + value * (width - pad.left - pad.right);
  const y = (value) => pad.top + (1 - value) * (height - pad.top - pad.bottom);
  const clusters = [...new Set(points.map((point) => point.cluster))];
  return `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(ariaLabel)}" preserveAspectRatio="none">
    <title>${escapeHtml(ariaLabel)}</title>${Array.from({ length: 5 }, (_, index) => {
      const ratio = index / 4;
      const yPosition = pad.top + ratio * (height - pad.top - pad.bottom);
      const xPosition = pad.left + ratio * (width - pad.left - pad.right);
      return `<line class="chart-grid-line" x1="${pad.left}" x2="${width - pad.right}" y1="${yPosition}" y2="${yPosition}"/><line class="chart-grid-line" x1="${xPosition}" x2="${xPosition}" y1="${pad.top}" y2="${height - pad.bottom}"/>`;
    }).join('')}${points.map((point) => `<g class="scatter-point"><circle cx="${x(point.x)}" cy="${y(point.y)}" r="11" fill="${color(point.color || 'teal')}" fill-opacity=".16" stroke="${color(point.color || 'teal')}" stroke-width="1.5"/><circle cx="${x(point.x)}" cy="${y(point.y)}" r="4" fill="${color(point.color || 'teal')}"/><title>${escapeHtml(point.label || 'Route')}: ${escapeHtml(point.cluster)}</title></g>`).join('')}<text class="chart-axis-label" x="${width / 2}" y="${height - 8}" text-anchor="middle">normalized demand</text><text class="chart-axis-label chart-axis-label--rotated" x="14" y="${height / 2}" text-anchor="middle" transform="rotate(-90 14 ${height / 2})">normalized reliability</text><text class="chart-cluster-legend" x="${pad.left + 8}" y="${pad.top + 15}">${clusters.length} interface clusters</text></svg>`;
}

export function heatmap({ values = [], labels = [], ariaLabel = 'Demand heatmap', colorName = 'teal' } = {}) {
  if (!values.length || !values[0]?.length) return chartEmpty(280, ariaLabel);
  const rows = values.length;
  const columns = values[0].length;
  const cellWidth = Math.min(58, (WIDTH - 54) / columns);
  const cellHeight = 38;
  const left = 54;
  const top = 28;
  const flat = values.flat().map(number).filter((value) => value !== null);
  const min = Math.min(...flat);
  const max = Math.max(...flat, min + 1);
  const cells = values.flatMap((row, rowIndex) => row.map((value, columnIndex) => {
    const ratio = (value - min) / (max - min || 1);
    const fill = mixColor(color(colorName), ratio);
    return `<g class="heatmap-cell"><rect x="${left + columnIndex * cellWidth + 2}" y="${top + rowIndex * cellHeight + 2}" width="${cellWidth - 4}" height="${cellHeight - 4}" rx="6" fill="${fill}"/><text x="${left + columnIndex * cellWidth + cellWidth / 2}" y="${top + rowIndex * cellHeight + cellHeight / 2 + 4}" text-anchor="middle">${escapeHtml(value)}</text></g>`;
  }).join('')).join('');
  const xLabelsMarkup = Array.from({ length: columns }, (_, index) => `<text class="chart-axis-label" x="${left + index * cellWidth + cellWidth / 2}" y="${top - 10}" text-anchor="middle">${escapeHtml(labels[index] ?? index + 1)}</text>`).join('');
  const yLabelsMarkup = Array.from({ length: rows }, (_, index) => `<text class="chart-axis-label" x="${left - 10}" y="${top + index * cellHeight + cellHeight / 2 + 4}" text-anchor="end">${escapeHtml(labels[index] ?? index + 1)}</text>`).join('');
  return `<svg class="chart-svg chart-svg--heatmap" viewBox="0 0 ${WIDTH} ${top + rows * cellHeight + 12}" role="img" aria-label="${escapeHtml(ariaLabel)}"><title>${escapeHtml(ariaLabel)}</title>${xLabelsMarkup}${yLabelsMarkup}${cells}</svg>`;
}

export function miniDistribution({ values = [], colorName = 'coral' } = {}) {
  const numeric = values.map(number).filter((value) => value !== null);
  if (!numeric.length) return '<span class="pending-value">—</span>';
  return sparkline({ values: numeric, colorName, width: 90, height: 30, label: 'Mini trend' });
}

function mixColor(base, ratio) {
  const hex = base.replace('#', '');
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  const white = 255;
  const t = Math.max(0, Math.min(1, ratio));
  return `rgb(${Math.round(r + (white - r) * t)},${Math.round(g + (white - g) * t)},${Math.round(b + (white - b) * t)})`;
}

export function chartEmpty(height, label) {
  return `<div class="chart-empty chart-empty--${Number(height) || 240}" role="img" aria-label="${escapeHtml(label)}"><span class="chart-empty__axis"></span><span class="chart-empty__line"></span><span>${escapeHtml(label)}</span><small>Connect an analytics response to render this visualization.</small></div>`;
}

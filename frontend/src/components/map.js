import { escapeHtml, iconButton, sourceTag } from './ui.js';
import { icon } from './icons.js';

const DEMO_ROUTES = [
  { name: 'Central Loop', color: '#f26b5e', path: 'M102 232 C150 205 170 168 218 152 S302 112 365 144 S446 188 510 160 S573 100 640 116' },
  { name: 'Riverside Connector', color: '#18b6a4', path: 'M86 128 C160 150 215 198 294 204 S417 164 488 198 S564 244 655 218' },
  { name: 'Airport Link', color: '#4b8df8', path: 'M122 270 C172 242 234 232 291 262 S386 270 447 230 S538 72 658 58' },
  { name: 'University Loop', color: '#f2b84b', path: 'M122 92 C170 72 224 84 255 124 S297 214 364 246 S466 248 532 206' },
];

const DEMO_STOPS = [
  { x: 102, y: 232, label: 'Central Hub', size: 8, tone: 'coral' },
  { x: 160, y: 209, label: 'Civic', size: 5, tone: 'blue' },
  { x: 218, y: 152, label: 'Riverside', size: 6, tone: 'teal' },
  { x: 294, y: 204, label: 'Market', size: 5, tone: 'teal' },
  { x: 365, y: 144, label: 'University', size: 7, tone: 'amber' },
  { x: 447, y: 230, label: 'Airport', size: 8, tone: 'blue' },
  { x: 510, y: 160, label: 'East Hub', size: 5, tone: 'purple' },
  { x: 640, y: 116, label: 'North Park', size: 5, tone: 'slate' },
  { x: 655, y: 218, label: 'South Yard', size: 5, tone: 'slate' },
];

/**
 * Every non-drawable outcome gets its own message. None of them may render
 * geometry, and none of them may borrow the demo fixtures.
 */
const STATE_COPY = Object.freeze({
  loading: {
    title: 'Loading verified route geometry',
    body: 'Waiting for the analytics service to return route paths and stop coordinates.',
    iconName: 'refresh',
  },
  not_configured: {
    title: 'Map adapter awaiting route geometry',
    body: 'Stop coordinates and pattern geometry remain unmounted until the analytics service provides them.',
    iconName: 'map',
  },
  unavailable: {
    title: 'No verified route geometry available',
    body: 'The analytics service reported that route geometry is unavailable for this snapshot. No substitute geometry is drawn.',
    iconName: 'map',
  },
  empty: {
    title: 'Response carried no drawable geometry',
    body: 'The service responded, but no route or stop had usable coordinates. Review the rejected-record warnings.',
    iconName: 'map',
  },
  error: {
    title: 'Route geometry could not be read',
    body: 'The analytics request for map geometry failed. No geometry is drawn and no cached value is substituted.',
    iconName: 'alert',
  },
});

const MAP_ACTIONS = Object.freeze({
  route: 'map-select-route',
  stop: 'map-select-stop',
});

/**
 * Map surface intentionally has no live map provider dependency. A future
 * provider can populate the same `networkMap` container with route geometry,
 * stop coordinates, selection, and layer controls.
 *
 * Geometry rules:
 *  - `mode: 'demo'` renders the labelled synthetic preview only.
 *  - Any other mode renders geometry exclusively from the `geometry` argument.
 *    When that argument is absent or not drawable, an explicit state is shown
 *    and no geometry is drawn. Demo fixtures are unreachable from this path.
 *
 * @param {{mode?: 'demo'|'api', title?: string, compact?: boolean, state?: string, geometry?: {routes: object[], stops: object[], projection: object, warnings?: string[], asOf?: string|null, counts?: object}|null, selection?: {kind?: 'route'|'stop', id?: string}|null}} options
 */
export function networkMap({ mode = 'demo', title = 'Network spatial view', compact = false, state, geometry = null, selection = null } = {}) {
  const effectiveState = mode === 'demo' ? 'demo' : (state ?? 'not_configured');
  const header = renderHeader({ mode, title, effectiveState });
  const body = mode === 'demo' ? renderDemoCanvas() : renderVerifiedCanvas({ effectiveState, geometry, selection });
  const footer = renderFooter({ mode, effectiveState, geometry });

  return `<section class="map-card ${compact ? 'map-card--compact' : ''}" data-map-mode="${escapeHtml(mode)}" data-map-state="${escapeHtml(effectiveState)}"${mode === 'demo' ? ' data-map-source="demo"' : ''}>${header}${body}${footer}</section>`;
}

function renderHeader({ mode, title, effectiveState }) {
  const verified = mode !== 'demo' && effectiveState === 'ready';
  const description = mode === 'demo'
    ? 'Illustrative network geometry for UI development.'
    : verified
      ? 'Route paths and stop coordinates from the analytics response. No map provider is required.'
      : 'Geometry renders only from a verified analytics response.';
  return `<div class="map-card__header">
    <div><div class="map-card__title-row"><h3>${escapeHtml(title)}</h3>${sourceTag(mode, mode === 'demo' ? 'DEMO GEOMETRY' : 'API CONTRACT')}</div><p>${escapeHtml(description)}</p></div>
    <div class="map-card__controls">${iconButton({ label: 'Zoom in', action: 'map-zoom-in', iconName: 'plus', variant: 'outline', size: 'sm' })}${iconButton({ label: 'Zoom out', action: 'map-zoom-out', iconName: 'minus', variant: 'outline', size: 'sm' })}${iconButton({ label: 'Map settings', action: 'map-settings', iconName: 'sliders', variant: 'outline', size: 'sm' })}</div>
  </div>`;
}

function renderFooter({ mode, effectiveState, geometry }) {
  if (mode === 'demo') {
    return `<div class="map-card__footer"><span>${icon('lock', 13)} No live provider or production coordinates loaded</span><span>Click a stop to open evidence drawer</span></div>`;
  }
  if (effectiveState === 'ready' && geometry) {
    const { counts = {}, asOf } = geometry;
    const coverage = `${counts.routes ?? 0} route path(s) · ${counts.stops ?? 0} stop coordinate(s)`;
    const rejected = (counts.rejectedRoutes ?? 0) + (counts.rejectedStops ?? 0) + (counts.rejectedPoints ?? 0);
    return `<div class="map-card__footer"><span>${icon('check', 13)} ${escapeHtml(coverage)}</span><span>${escapeHtml(asOf ? `As of ${asOf}` : 'As-of timestamp not supplied')}${rejected ? ` · ${rejected} record(s) rejected` : ''}</span></div>`;
  }
  return `<div class="map-card__footer"><span>${icon('lock', 13)} No live provider or production coordinates loaded</span><span>Interface contract ready</span></div>`;
}

function renderDemoCanvas() {
  const lines = DEMO_ROUTES.map((route) => `<path class="map-route map-route--${escapeHtml(route.color.replace('#', ''))}" d="${route.path}" stroke="${route.color}"/><text class="map-route-label" x="${route.name === 'Airport Link' ? 526 : route.name === 'Riverside Connector' ? 380 : route.name === 'University Loop' ? 160 : 188}" y="${route.name === 'Airport Link' ? 98 : route.name === 'Riverside Connector' ? 218 : route.name === 'University Loop' ? 108 : 140}">${escapeHtml(route.name)}</text>`).join('');
  const stops = DEMO_STOPS.map((stop) => `<g class="map-stop map-stop--${escapeHtml(stop.tone)}"><circle cx="${stop.x}" cy="${stop.y}" r="${stop.size + 5}" fill="currentColor" fill-opacity=".10"/><circle cx="${stop.x}" cy="${stop.y}" r="${stop.size}" fill="currentColor" stroke="#ffffff" stroke-width="2"/><title>${escapeHtml(stop.label)} · demo stop</title></g>`).join('');
  return `<div class="map-canvas" data-map-source="demo"><div class="map-canvas__grid"></div><div class="map-canvas__label">DEMO NETWORK PREVIEW</div><svg viewBox="0 0 740 310" role="img" aria-label="Illustrative transit network map"><title>Illustrative network map, not production geography</title>${lines}${stops}</svg><div class="map-legend"><span><i class="map-legend__line map-legend__line--coral"></i>Corridor</span><span><i class="map-legend__line map-legend__line--teal"></i>Feeder</span><span><i class="map-legend__dot"></i>Stop</span></div><div class="map-canvas__scale">Scale / geography not production data</div></div>`;
}

function renderVerifiedCanvas({ effectiveState, geometry, selection }) {
  if (effectiveState !== 'ready' || !geometry) {
    return renderStateCanvas(effectiveState, geometry);
  }
  const projection = geometry.projection;
  if (!projection || projection.degenerate || !projection.points.length) {
    return renderStateCanvas('empty', geometry);
  }
  const { width, height, points, stopPoints, bounds } = projection;
  const selectedKind = selection?.kind ?? null;
  const selectedId = selection?.id ?? null;

  const routes = points.map((route) => {
    const isSelected = selectedKind === 'route' && selectedId === route.routeId;
    const label = [route.routeCode, route.name].filter(Boolean).join(' · ') || route.routeId;
    return `<g class="map-network__route map-network__route--${escapeHtml(route.tone)}${isSelected ? ' is-selected' : ''}${selectedKind && !isSelected ? ' is-dimmed' : ''}" data-action="${MAP_ACTIONS.route}" data-entity-kind="route" data-entity-id="${escapeHtml(route.routeId)}" data-tone="${escapeHtml(route.tone)}" tabindex="0" role="button" aria-pressed="${isSelected}" aria-label="${escapeHtml(`Route ${label}, ${route.pointCount} points`)}"><title>${escapeHtml(`Route ${label} · ${route.pointCount} points`)}</title><path class="map-network__path" d="${escapeHtml(route.d)}"/><text class="map-network__label" x="${route.labelAt.x.toFixed(2)}" y="${(route.labelAt.y - 7).toFixed(2)}">${escapeHtml(route.label)}</text></g>`;
  }).join('');

  const stops = stopPoints.map((stop) => {
    const isSelected = selectedKind === 'stop' && selectedId === stop.stopId;
    return `<g class="map-network__stop${isSelected ? ' is-selected' : ''}${selectedKind && !isSelected ? ' is-dimmed' : ''}" data-action="${MAP_ACTIONS.stop}" data-entity-kind="stop" data-entity-id="${escapeHtml(stop.stopId)}" tabindex="0" role="button" aria-pressed="${isSelected}" aria-label="${escapeHtml(`Stop ${stop.label}`)}"><circle class="map-network__stop-halo" cx="${stop.x.toFixed(2)}" cy="${stop.y.toFixed(2)}" r="9"/><circle class="map-network__stop-dot" cx="${stop.x.toFixed(2)}" cy="${stop.y.toFixed(2)}" r="4.5"/><text class="map-network__stop-label" x="${(stop.x + 8).toFixed(2)}" y="${(stop.y + 3).toFixed(2)}">${escapeHtml(stop.label)}</text><title>${escapeHtml(`Stop ${stop.label} · ${stop.latitude}, ${stop.longitude}`)}</title></g>`;
  }).join('');

  const key = renderRouteKey(points, selection);
  const boundsNote = bounds
    ? `Lat ${bounds.minLat.toFixed(4)}–${bounds.maxLat.toFixed(4)} · Lon ${bounds.minLng.toFixed(4)}–${bounds.maxLng.toFixed(4)}`
    : 'Bounds unavailable';

  return `<div class="map-canvas map-canvas--verified" data-map-source="api"><div class="map-canvas__grid"></div><div class="map-canvas__label">VERIFIED GEOMETRY</div><svg viewBox="0 0 ${width} ${height}" role="group" aria-label="Transit network map rendered from analytics route geometry">${routes}${stops}</svg>${renderSelection(selection, points, stopPoints)}${renderWarnings(geometry)}<div class="map-legend"><span><i class="map-legend__dot"></i>Stop</span><span>${escapeHtml(`${points.length} route(s)`)}</span></div><div class="map-canvas__scale">${escapeHtml(boundsNote)}</div></div>${key}`;
}

function renderStateCanvas(state, geometry) {
  const copy = STATE_COPY[state] ?? STATE_COPY.not_configured;
  return `<div class="map-placeholder map-placeholder--api" data-map-source="api"><div class="map-placeholder__grid"></div><div class="map-placeholder__content"><span class="map-placeholder__icon">${icon(copy.iconName, 24)}</span><strong>${escapeHtml(copy.title)}</strong><span>${escapeHtml(geometry?.warnings?.[0] ?? copy.body)}</span></div></div>`;
}

/**
 * Route key: identification without hover, and the same selection entry point
 * as the map itself.
 */
function renderRouteKey(points, selection) {
  if (!points.length) return '';
  const rows = points.map((route) => {
    const isSelected = selection?.kind === 'route' && selection?.id === route.routeId;
    const label = [route.routeCode, route.name].filter(Boolean).join(' · ') || route.routeId;
    return `<li class="map-key__item${isSelected ? ' is-selected' : ''}"><button type="button" class="map-key__button" data-action="${MAP_ACTIONS.route}" data-entity-kind="route" data-entity-id="${escapeHtml(route.routeId)}" aria-pressed="${isSelected}"><i class="map-key__swatch map-key__swatch--${escapeHtml(route.tone)}"></i><span class="map-key__label">${escapeHtml(label)}</span><span class="map-key__meta">${escapeHtml(`${route.pointCount} pt`)}</span></button></li>`;
  }).join('');
  return `<ul class="map-key" aria-label="Routes in this response">${rows}</ul>`;
}

function renderSelection(selection, points, stopPoints) {
  if (!selection?.kind || !selection?.id) {
    return '<div class="map-selection map-selection--none">Select a route or stop on the map to identify it.</div>';
  }
  if (selection.kind === 'route') {
    const route = points.find((item) => item.routeId === selection.id);
    if (!route) return '<div class="map-selection map-selection--none">Selected route is not in this response.</div>';
    const label = [route.routeCode, route.name].filter(Boolean).join(' · ') || route.routeId;
    const mode = route.mode ? ` · ${route.mode}` : '';
    return `<div class="map-selection" data-selection-kind="route" data-selection-id="${escapeHtml(route.routeId)}"><span class="map-selection__kind">Route</span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(`${route.pointCount} geometry point(s)${mode}`)}</small></div>`;
  }
  const stop = stopPoints.find((item) => item.stopId === selection.id);
  if (!stop) return '<div class="map-selection map-selection--none">Selected stop is not in this response.</div>';
  return `<div class="map-selection" data-selection-kind="stop" data-selection-id="${escapeHtml(stop.stopId)}"><span class="map-selection__kind">Stop</span><strong>${escapeHtml(stop.label)}</strong><small>${escapeHtml(`${stop.latitude}, ${stop.longitude}`)}</small></div>`;
}

function renderWarnings(geometry) {
  const warnings = geometry?.warnings ?? [];
  if (!warnings.length) return '';
  const items = warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join('');
  return `<details class="map-warnings"><summary>${warnings.length} geometry warning(s)</summary><ul>${items}</ul></details>`;
}

export function mapStat({ label, value, detail, tone = 'teal' }) {
  return `<div class="map-stat"><span class="map-stat__dot map-stat__dot--${escapeHtml(tone)}"></span><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></div>`;
}

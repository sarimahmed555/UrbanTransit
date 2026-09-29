/**
 * Optional map boundary. The dashboard does not select or require a map
 * provider; this adapter turns a verified analytics response into drawable
 * geometry, or into an explicit reason why it cannot.
 *
 * Hard rules enforced here:
 *  - No fabricated geometry. A record without usable coordinates is rejected
 *    and counted, never replaced with a default or an interpolated point.
 *  - No silent fallback. This module never returns demo/mock geometry; the
 *    only source of a drawing is the caller-supplied response.
 *  - Explicit states. Loading, empty, unavailable, not_configured and error
 *    are distinguishable, so a caller can never mistake one for real data.
 */

/**
 * @typedef {Object} MapViewport
 * @property {number} [zoom]
 * @property {{lat: number, lng: number}} [center]
 * @property {number} [bearing]
 */

/**
 * @typedef {Object} MapLayer
 * @property {string} id
 * @property {'route'|'stop'|'flow'|'occupancy'|'delay'} kind
 * @property {boolean} visible
 */

/**
 * @typedef {Object} NetworkGeometry
 * @property {string} source
 * @property {string} [asOf]
 * @property {Array<{id: string, coordinates: number[][]}>} routes
 * @property {Array<{id: string, name: string, latitude: number, longitude: number}>} stops
 */

/** @typedef {'ready'|'empty'|'unavailable'|'not_configured'|'error'} MapState */

const MIN_ROUTE_POINTS = 2;
const LATITUDE_RANGE = [-90, 90];
const LONGITUDE_RANGE = [-180, 180];

/**
 * Presentation-only categorical palette. These are rendering choices for
 * telling routes apart, never a value read from or written to the data.
 */
const ROUTE_TONES = Object.freeze(['coral', 'teal', 'blue', 'amber', 'purple', 'slate']);

function isFiniteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function isInRange(value, [low, high]) {
  return isFiniteNumber(value) && value >= low && value <= high;
}

function nonEmptyString(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

/**
 * Validate one route geometry record.
 * @param {unknown} record
 * @returns {{ok: true, route: object} | {ok: false, reason: string}}
 */
export function validateRouteGeometry(record) {
  if (!record || typeof record !== 'object') return { ok: false, reason: 'route is not an object' };
  const routeId = record.routeId ?? record.id;
  if (!nonEmptyString(routeId)) return { ok: false, reason: 'route is missing routeId' };
  const raw = record.coordinates;
  if (!Array.isArray(raw)) return { ok: false, reason: `route ${routeId} has no coordinates array` };

  const points = [];
  let rejectedPoints = 0;
  for (const pair of raw) {
    if (!Array.isArray(pair) || pair.length < 2) {
      rejectedPoints += 1;
      continue;
    }
    const [longitude, latitude] = pair;
    if (isInRange(latitude, LATITUDE_RANGE) && isInRange(longitude, LONGITUDE_RANGE)) {
      points.push([longitude, latitude]);
    } else {
      rejectedPoints += 1;
    }
  }
  if (points.length < MIN_ROUTE_POINTS) {
    return {
      ok: false,
      reason: `route ${routeId} has ${points.length} usable point(s); ${MIN_ROUTE_POINTS} are required to draw`,
    };
  }
  return {
    ok: true,
    route: {
      routeId,
      routeCode: nonEmptyString(record.routeCode) ? record.routeCode : null,
      name: nonEmptyString(record.name) ? record.name : null,
      mode: nonEmptyString(record.mode) ? record.mode : null,
      points,
      rejectedPoints,
      pointCount: points.length,
      // Identity for interaction, never a substitute for a name.
      label: [record.routeCode, record.name].find(nonEmptyString) ?? routeId,
    },
  };
}

/**
 * Validate one stop coordinate record.
 * @param {unknown} record
 * @returns {{ok: true, stop: object} | {ok: false, reason: string}}
 */
export function validateStopGeometry(record) {
  if (!record || typeof record !== 'object') return { ok: false, reason: 'stop is not an object' };
  const stopId = record.stopId ?? record.id;
  if (!nonEmptyString(stopId)) return { ok: false, reason: 'stop is missing stopId' };
  const { latitude, longitude } = record;
  if (!isInRange(latitude, LATITUDE_RANGE) || !isInRange(longitude, LONGITUDE_RANGE)) {
    return { ok: false, reason: `stop ${stopId} has no finite in-range coordinate` };
  }
  return {
    ok: true,
    stop: {
      stopId,
      name: nonEmptyString(record.name) ? record.name : null,
      latitude,
      longitude,
      label: nonEmptyString(record.name) ? record.name : stopId,
    },
  };
}

/**
 * Normalize a `mapGeometry` view-model field into drawable records.
 *
 * Returns explicit counts so a caller can report coverage instead of implying
 * completeness. An absent `available: true` never produces drawable geometry.
 *
 * @param {unknown} value the `mapGeometry` field from a routesAndStops view model
 * @returns {{state: MapState, routes: object[], stops: object[], warnings: string[], counts: object, asOf: string|null, datasetVersion: string|null}}
 */
export function normalizeNetworkGeometry(value) {
  const empty = {
    state: 'not_configured',
    routes: [],
    stops: [],
    warnings: [],
    counts: { routes: 0, stops: 0, rejectedRoutes: 0, rejectedStops: 0, rejectedPoints: 0 },
    asOf: null,
    datasetVersion: null,
  };

  if (value === null || value === undefined) {
    return { ...empty, warnings: ['No map geometry was supplied.'] };
  }
  if (!value || typeof value !== 'object') {
    return { ...empty, warnings: ['Map geometry is not an object.'] };
  }
  if (value.available !== true) {
    const reason = nonEmptyString(value.reason)
      ? value.reason
      : 'The analytics service reported no verified route geometry.';
    return { ...empty, state: 'unavailable', warnings: [reason] };
  }

  const rawRoutes = Array.isArray(value.routes) ? value.routes : [];
  const rawStops = Array.isArray(value.stops) ? value.stops : [];
  const routes = [];
  const stops = [];
  const warnings = [];
  let rejectedRoutes = 0;
  let rejectedStops = 0;
  let rejectedPoints = 0;

  rawRoutes.forEach((record) => {
    const result = validateRouteGeometry(record);
    if (result.ok) {
      rejectedPoints += result.route.rejectedPoints;
      routes.push(result.route);
    } else {
      rejectedRoutes += 1;
      warnings.push(result.reason);
    }
  });
  rawStops.forEach((record) => {
    const result = validateStopGeometry(record);
    if (result.ok) stops.push(result.stop);
    else {
      rejectedStops += 1;
      warnings.push(result.reason);
    }
  });

  if (routes.length === 0 && stops.length === 0) {
    return {
      ...empty,
      state: 'empty',
      warnings: ['The response carried no drawable route or stop geometry.'],
      asOf: nonEmptyString(value.asOf) ? value.asOf : null,
      datasetVersion: nonEmptyString(value.datasetVersion) ? value.datasetVersion : null,
    };
  }
  if (rejectedRoutes || rejectedStops || rejectedPoints) {
    warnings.push(
      `Rejected ${rejectedRoutes} route(s), ${rejectedStops} stop(s), ${rejectedPoints} point(s) with unusable coordinates.`,
    );
  }
  return {
    state: 'ready',
    routes,
    stops,
    warnings,
    counts: {
      routes: routes.length,
      stops: stops.length,
      rejectedRoutes,
      rejectedStops,
      rejectedPoints,
    },
    asOf: nonEmptyString(value.asOf) ? value.asOf : null,
    datasetVersion: nonEmptyString(value.datasetVersion) ? value.datasetVersion : null,
  };
}

/**
 * Project validated geography into a fixed viewBox using the data's own bounds.
 *
 * The bounding box comes only from the supplied records. A single degenerate
 * extent is widened to a unit box around the real value so the projection stays
 * finite; it is never replaced by invented coordinates.
 *
 * @param {object[]} routes
 * @param {object[]} stops
 * @param {{width?: number, height?: number, padding?: number}} [options]
 */
export function projectNetworkGeometry(routes, stops, options = {}) {
  const width = options.width ?? 740;
  const height = options.height ?? 310;
  const padding = options.padding ?? 26;
  const longitudes = [];
  const latitudes = [];
  routes.forEach((route) => route.points.forEach(([lng, lat]) => {
    longitudes.push(lng);
    latitudes.push(lat);
  }));
  stops.forEach((stop) => {
    longitudes.push(stop.longitude);
    latitudes.push(stop.latitude);
  });

  if (longitudes.length === 0) {
    return { width, height, points: [], stopPoints: [], bounds: null, degenerate: true };
  }
  let [minLng, maxLng] = [Math.min(...longitudes), Math.max(...longitudes)];
  let [minLat, maxLat] = [Math.min(...latitudes), Math.max(...latitudes)];
  if (minLng === maxLng) {
    const nudge = 0.0005;
    minLng -= nudge;
    maxLng += nudge;
  }
  if (minLat === maxLat) {
    const nudge = 0.0005;
    minLat -= nudge;
    maxLat += nudge;
  }
  const plotWidth = Math.max(width - padding * 2, 1);
  const plotHeight = Math.max(height - padding * 2, 1);
  const spanLng = maxLng - minLng || 1;
  const spanLat = maxLat - minLat || 1;
  const x = (longitude) => padding + ((longitude - minLng) / spanLng) * plotWidth;
  // Latitude increases northwards; SVG y increases downwards, so invert.
  const y = (latitude) => padding + (1 - (latitude - minLat) / spanLat) * plotHeight;

  return {
    width,
    height,
    bounds: { minLng, maxLng, minLat, maxLat },
    degenerate: false,
    points: routes.map((route) => ({
      ...route,
      tone: ROUTE_TONES[routes.indexOf(route) % ROUTE_TONES.length],
      d: route.points.map(([lng, lat], index) => `${index === 0 ? 'M' : 'L'}${x(lng).toFixed(2)} ${y(lat).toFixed(2)}`).join(' '),
      // Anchor the label on the route's own midpoint, not a fixed offset.
      labelAt: midpointOf(route.points.map(([lng, lat]) => [x(lng), y(lat)])),
    })),
    stopPoints: stops.map((stop) => ({
      ...stop,
      x: x(stop.longitude),
      y: y(stop.latitude),
    })),
  };
}

function midpointOf(points) {
  const middle = points[Math.floor(points.length / 2)];
  return { x: middle[0], y: middle[1] };
}

/**
 * Create an inert adapter for a future map implementation.
 *
 * The adapter is the only sanctioned path from an analytics response to the
 * map. It has no access to demo fixtures, so API mode cannot inherit them.
 *
 * @param {{loadNetwork?: (context: {filters: Record<string, unknown>, viewport?: MapViewport}) => Promise<unknown>, provider?: unknown}} options
 */
export function createMapAdapter({ loadNetwork, provider } = {}) {
  const configured = typeof loadNetwork === 'function';
  return {
    configured,
    provider: provider ?? null,
    /**
     * @param {{filters?: Record<string, unknown>, viewport?: MapViewport, response?: unknown}} [context]
     * @returns {Promise<{state: MapState, geometry: object, warnings: string[], error?: Error}>}
     */
    async load(context = {}) {
      if (!configured) {
        return {
          state: 'not_configured',
          geometry: normalizeNetworkGeometry(null),
          warnings: ['No map provider or geometry loader is connected.'],
        };
      }
      try {
        const payload = context.response ?? (await loadNetwork(context));
        const geometry = normalizeNetworkGeometry(payload);
        return {
          state: geometry.state,
          geometry,
          warnings: geometry.warnings,
        };
      } catch (error) {
        return {
          state: 'error',
          geometry: normalizeNetworkGeometry(null),
          warnings: ['The analytics service could not be read for map geometry.'],
          error: error instanceof Error ? error : new Error('Unknown map adapter error'),
        };
      }
    },
  };
}

/**
 * Bridge an `apiClient` into a map adapter.
 *
 * This is the wiring between the declared `routesAndStops` contract and the
 * map. It reads only the `mapGeometry` field of the view model, so a
 * contract-shaped response without geometry yields `unavailable`, never demo
 * geometry.
 *
 * @param {{routesAndStops: (params?: Record<string, unknown>) => Promise<{state: string, data: unknown|null}>}} apiClient
 */
export function connectGeometryAdapter(apiClient) {
  if (!apiClient || typeof apiClient.routesAndStops !== 'function') {
    return createMapAdapter({});
  }
  return createMapAdapter({
    async loadNetwork({ filters, response } = {}) {
      if (response !== undefined) {
        return viewModelGeometry(response);
      }
      const result = await apiClient.routesAndStops(filters ?? {});
      if (result?.state === 'error') {
        throw result.error ?? new Error('routesAndStops request failed');
      }
      return viewModelGeometry(result?.data);
    },
  });
}

function viewModelGeometry(viewModel) {
  if (!viewModel || typeof viewModel !== 'object') return null;
  return viewModel.mapGeometry ?? null;
}

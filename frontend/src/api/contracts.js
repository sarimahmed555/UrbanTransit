/**
 * Frontend capability contracts for the analytics service.
 *
 * The analytics page contracts remain proposals until their serving
 * projections are connected. The audited evidence routes are implemented and
 * marked `connected`.
 */

/**
 * @typedef {'ready'|'empty'|'loading'|'error'|'not_configured'} ApiState
 * @typedef {{ source: 'api'|'demo'|'system', asOf?: string, warnings?: string[] }} ApiMeta
 * @typedef {{ state: ApiState, data: unknown|null, meta: ApiMeta, error?: Error }} ApiResult
 * @typedef {{ get: (path: string, params?: Record<string, string|number|boolean|null|undefined>) => Promise<ApiResult> }} ApiTransport
 */

/** @type {Readonly<Record<string, {key: string, method: 'GET'|'POST', path: string, state: 'contract-only'|'connected', description: string}>>} */
export const API_CONTRACTS = Object.freeze({
  executiveSummary: {
    key: 'executive',
    method: 'GET',
    path: '/api/v1/analytics/executive-summary',
    state: 'contract-only',
    description: 'Network KPIs, trends, and priority signals.',
  },
  passengerDemand: {
    key: 'demand',
    method: 'GET',
    path: '/api/v1/analytics/passenger-demand',
    state: 'contract-only',
    description: 'Ridership trends, peak windows, and stop/route rankings.',
  },
  routesAndStops: {
    key: 'routes-stops',
    method: 'GET',
    path: '/api/v1/analytics/routes-stops',
    state: 'contract-only',
    description: 'Network map geometry, route performance, and stop demand.',
  },
  delayAnalysis: {
    key: 'delays',
    method: 'GET',
    path: '/api/v1/analytics/delay-analysis',
    state: 'contract-only',
    description: 'Delay distributions, punctuality, and reliability summaries.',
  },
  occupancy: {
    key: 'occupancy',
    method: 'GET',
    path: '/api/v1/analytics/occupancy-crowding',
    state: 'contract-only',
    description: 'Capacity utilization, load bands, and crowding risk.',
  },
  demandForecast: {
    key: 'forecasting',
    method: 'GET',
    path: '/api/v1/analytics/demand-forecast',
    state: 'contract-only',
    description: 'Forecast versus actual series and model readiness metadata.',
  },
  routeClusters: {
    key: 'clustering',
    method: 'GET',
    path: '/api/v1/analytics/route-clusters',
    state: 'contract-only',
    description: 'Route cluster assignments and explanatory features.',
  },
  passengerFlow: {
    key: 'passenger-flow',
    method: 'GET',
    path: '/api/v1/analytics/passenger-flow',
    state: 'contract-only',
    description: 'OD matrix, directional flow, and top stop pairs.',
  },
  whatIf: {
    key: 'what-if',
    method: 'POST',
    path: '/api/v1/analytics/what-if',
    state: 'contract-only',
    description: 'Estimate a cloned schedule/capacity/demand scenario.',
  },
  recommendations: {
    key: 'recommendations',
    method: 'GET',
    path: '/api/v1/analytics/recommendations',
    state: 'contract-only',
    description: 'Evidence-backed recommendations and supporting metrics.',
  },
  dataQuality: {
    key: 'data-quality',
    method: 'GET',
    path: '/api/v1/analytics/data-quality',
    state: 'contract-only',
    description: 'DQ summaries, issue counts, reconciliation, and lineage.',
  },
  systemStatus: {
    key: 'system',
    method: 'GET',
    path: '/api/v1/analytics/system-status',
    state: 'contract-only',
    description: 'Pipeline stages, freshness, and integration health.',
  },
  evidenceStatus: {
    key: 'system',
    method: 'GET',
    path: '/api/v1/evidence/status',
    state: 'connected',
    description: 'Audited artifact and PostgreSQL readiness status.',
  },
  evidenceTasks: {
    key: 'model-evidence',
    method: 'GET',
    path: '/api/v1/evidence/tasks',
    state: 'connected',
    description: 'Frozen Spark and Python evaluation summaries.',
  },
  evidenceTask: {
    key: 'model-evidence',
    method: 'GET',
    path: '/api/v1/evidence/tasks/{taskName}',
    state: 'connected',
    description: 'Verified task metrics and optional bounded predictions.',
  },
  delayComparison: {
    key: 'model-comparison',
    method: 'GET',
    path: '/api/v1/evidence/comparison/delay',
    state: 'connected',
    description: 'Audited delay-pipeline agreement and truth alignment.',
  },
});

/** @type {Readonly<Record<string, {path: string, state: 'contract-only'|'connected'}>>} */
export const API_CAPABILITY_STATUS = Object.freeze(
  Object.fromEntries(
    Object.entries(API_CONTRACTS).map(([name, contract]) => [
      name,
      { path: contract.path, state: contract.state },
    ]),
  ),
);

export function getContract(name) {
  const contract = API_CONTRACTS[name];
  if (!contract) {
    throw new Error(`Unknown UrbanTransit IQ API contract: ${name}`);
  }
  return contract;
}

/**
 * Network geometry contract expected by the map adapter.
 *
 * This is the exact shape the frontend will render. It is a *requirement on
 * the analytics service*, not a claim that the service currently returns it.
 * The map never invents geometry: an absent, partial or out-of-range value is
 * reported as a rejected record, never substituted with a plausible number.
 *
 * Transport: `routesAndStops` view model, field `mapGeometry`.
 *
 * @example
 * // mapGeometry as the service must return it
 * {
 *   //   available: false        -> the map renders the "unavailable" state
 *   //   available: true         -> routes/stops are validated below
 *   available: true,
 *   //   Only when available is true. Omit to stay unverified.
 *   asOf: '2026-04-01T00:00:00Z',
 *   datasetVersion: '<dataset version>',
 *   routes: [
 *     {
 *       routeId: '<stable route id>',       // required, non-empty
 *       routeCode: '<human code>',          // optional label
 *       name: '<route name>',               // optional label
 *       mode: '<mode>',                     // optional label
 *       // required, >= 2 points, GeoJSON order
 *       coordinates: [[longitude, latitude], ...],
 *     },
 *   ],
 *   stops: [
 *     {
 *       stopId: '<stable stop id>',         // required, non-empty
 *       name: '<stop name>',                // optional label
 *       latitude: 0,                        // required, -90..90
 *       longitude: 0,                       // required, -180..180
 *     },
 *   ],
 * }
 */
export const NETWORK_GEOMETRY_CONTRACT = Object.freeze({
  state: 'contract-only',
  capability: 'routesAndStops',
  viewModelField: 'mapGeometry',
  required: ['available', 'routes', 'stops'],
  optional: ['asOf', 'datasetVersion', 'reason'],
  routeRequired: ['routeId', 'coordinates'],
  routeOptional: ['routeCode', 'name', 'mode'],
  stopRequired: ['stopId', 'latitude', 'longitude'],
  stopOptional: ['name'],
  coordinateOrder: '[longitude, latitude]',
  minimumRoutePoints: 2,
  latitudeRange: Object.freeze([-90, 90]),
  longitudeRange: Object.freeze([-180, 180]),
  notes: Object.freeze([
    'A route with fewer than two usable points cannot be drawn and is rejected.',
    'A stop without finite in-range coordinates is rejected, not approximated.',
    'Rejected records are counted and reported; they are never silently dropped.',
    'The map renders no geometry at all unless this contract is satisfied.',
  ]),
});

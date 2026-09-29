/**
 * Shared filter contract for analytics requests.
 * Empty values are omitted by the API client so an unselected filter does not
 * become a misleading production constraint.
 *
 * @typedef {Object} AnalyticsFilters
 * @property {string} [dateRange]
 * @property {string} [startDate]
 * @property {string} [endDate]
 * @property {string} [serviceDay]
 * @property {string} [routeId]
 * @property {string} [stopId]
 * @property {string} [direction]
 * @property {string} [serviceType]
 * @property {string} [period]
 * @property {string} [granularity]
 * @property {string} [datasetVersion]
 */

/** @type {AnalyticsFilters} */
export const EMPTY_FILTERS = Object.freeze({});

/**
 * Convert a filter object into the query shape expected by the proposed API
 * contracts. This is a serialization helper only; it does not resolve IDs or
 * make assumptions about backend naming beyond the contract paths.
 * @param {AnalyticsFilters} filters
 */
export function toQueryParams(filters = EMPTY_FILTERS) {
  return Object.fromEntries(
    Object.entries(filters).filter(([, value]) => value !== undefined && value !== null && value !== ''),
  );
}

/**
 * Merge a filter patch without mutating the current context.
 * @param {AnalyticsFilters} current
 * @param {Partial<AnalyticsFilters>} patch
 * @returns {AnalyticsFilters}
 */
export function mergeFilters(current = EMPTY_FILTERS, patch = {}) {
  const next = { ...current, ...patch };
  Object.keys(next).forEach((key) => {
    if (next[key] === undefined || next[key] === null || next[key] === '') delete next[key];
  });
  return next;
}

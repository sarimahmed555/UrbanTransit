/**
 * Proposed response/view-model shapes for analytics adapters.
 *
 * These are frontend contracts, not claims about a deployed API. Numeric
 * values remain nullable/unavailable until an adapter supplies an explicit
 * source, as-of timestamp, denominator, and lineage.
 */

/**
 * @typedef {Object} SourceContext
 * @property {'api'|'system'} source
 * @property {string} [asOf]
 * @property {string} [datasetVersion]
 * @property {string[]} [warnings]
 * @property {string} [pipelineRunId]
 */

/**
 * @typedef {Object} MetricView
 * @property {string} key
 * @property {string} label
 * @property {number|string|null} value
 * @property {string} [unit]
 * @property {number|string|null} [delta]
 * @property {'up'|'down'|'attention'|'neutral'|'pending'} [direction]
 * @property {string} [note]
 */

/**
 * @typedef {Object} TrendSeriesView
 * @property {string[]} labels
 * @property {Array<number|null>} values
 * @property {Array<number|null>} [comparison]
 * @property {string} label
 * @property {string} unit
 */

/**
 * @typedef {Object} TableRowView
 * @property {string} [id]
 * @property {Record<string, string|number|null>} [values]
 */

/**
 * @typedef {Object} ApiPageView
 * @property {SourceContext} meta
 * @property {MetricView[]} kpis
 * @property {TrendSeriesView[]} [trends]
 * @property {TableRowView[]} [tables]
 * @property {Record<string, unknown>} [series]
 */

/** @type {Readonly<Record<string, {required: string[], optional: string[]}>>} */
export const VIEW_MODEL_CONTRACTS = Object.freeze({
  executiveSummary: { required: ['meta', 'kpis', 'trends'], optional: ['health', 'prioritySignals', 'mapSummary'] },
  passengerDemand: { required: ['meta', 'kpis', 'trends', 'routeDemand', 'stopDemand'], optional: ['peakWindows', 'heatmap', 'filters'] },
  routesAndStops: { required: ['meta', 'kpis', 'routePerformance', 'stopRanking'], optional: ['mapGeometry', 'layers'] },
  delayAnalysis: { required: ['meta', 'kpis', 'delayDistribution', 'routeReliability'], optional: ['timePattern', 'earlyArrivalContext'] },
  occupancy: { required: ['meta', 'kpis', 'bands', 'persistentSignals'], optional: ['segmentTrend', 'capacityCoverage'] },
  demandForecast: { required: ['meta', 'forecastSeries', 'evaluationReadiness'], optional: ['modelCard', 'caseTable', 'baselineComparison'] },
  routeClusters: { required: ['meta', 'clusters', 'assignments'], optional: ['featureWindow', 'explanations'] },
  passengerFlow: { required: ['meta', 'odMatrix', 'topPairs', 'directionalSummary'], optional: ['mapGeometry', 'endpointReconciliation'] },
  whatIf: { required: ['meta', 'baseline', 'scenario', 'assumptions'], optional: ['comparisonSeries', 'auditTrail'] },
  recommendations: { required: ['meta', 'insights', 'actionQueue'], optional: ['evidenceDrawer', 'ownerSummary'] },
  dataQuality: { required: ['meta', 'kpis', 'issueFamilies', 'reconciliation'], optional: ['lineage', 'runHistory'] },
  systemStatus: { required: ['meta', 'pipelineStages', 'sources', 'events'], optional: ['freshness', 'runtimeLogs'] },
});

/**
 * Validate the top-level shape of a proposed response without coercing
 * missing measures into zero. It intentionally does not validate business
 * semantics; those belong to the analytics service and its own contracts.
 * @param {unknown} value
 * @param {keyof typeof VIEW_MODEL_CONTRACTS} capability
 */
export function hasViewModelShape(value, capability) {
  const contract = VIEW_MODEL_CONTRACTS[capability];
  if (!contract || !value || typeof value !== 'object') return false;
  return contract.required.every((field) => Object.prototype.hasOwnProperty.call(value, field));
}

/**
 * Shape check for the optional `mapGeometry` field of the `routesAndStops`
 * view model. It confirms the field is structurally present and explicitly
 * available; it does not validate coordinates and it is not a claim that the
 * service returns geometry. Use `normalizeNetworkGeometry` for that.
 * @param {unknown} value
 */
export function hasNetworkGeometry(value) {
  if (!value || typeof value !== 'object') return false;
  if (value.available !== true) return false;
  return Array.isArray(value.routes) && Array.isArray(value.stops);
}

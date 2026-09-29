# TransitVerse frontend handoff

## Scope

Frontend only. Existing dependency-free ES modules, hash routes, SVG charts,
map normalization/projection, and demo fixtures are retained. No dependencies
were installed. No API, database, Spark, Hadoop or ML process was started.

The first visit defaults to the empty live-data view. Demo mode requires an
explicit selection and uses a new preference key so a previously saved demo
preference does not silently enable synthetic results on the first visit to
this version. Existing demo labels remain visible. Exports are disabled until
a report transport is connected.

## Routes

All 12 original routes remain: executive, demand, routes-stops,
passenger-flow, delays, occupancy, forecasting, clustering, what-if,
recommendations, data-quality and system.

Additional routes: peak-hours, underutilization, delay-prediction,
occupancy-forecast, passenger-clustering, network-map, model-comparison,
reports, login and profile. Each uses the existing page shell. OD analysis
remains under passenger-flow; route performance remains under routes-stops.

## Files changed in this task

Created:
- `src/theme.css`: lavender light theme, responsive grids, focus and reduced-motion rules.
- `src/components/result-state.js`: reusable result panel with loading, empty,
  unavailable, error and ready states, escaped cells and source/freshness footer.
- `src/pages/product-areas.js`: additional product pages, report categories,
  unavailable authentication/session UI and validated network map.
- `scripts/check-product.mjs`: lightweight render, routing, isolation and no-network checks.
- `IMPLEMENTATION_NOTES.md`: this handoff.

Modified:
- `index.html`: theme stylesheet and browser theme color.
- `src/navigation.js`: additional routes without removing existing identifiers.
- `src/pages/index.js`: dispatch new routes; pass through supplied view models.
- `src/app.js`: safe default source preference, window hash-change handling,
  honest notification and refresh messages.
- `src/components/layout.js`: TransitVerse labels, profile navigation, source state.
- `src/components/ui.js`: unavailable-source label and disabled exports.
- `src/pages/shared.js`: user-facing awaiting-data notice.
- `src/pages/executive.js`: executive dashboard sections and working navigation.
- `src/pages/what-if.js`: disabled setup form based on verified scenario fields.

Pre-existing edits to frontend files were retained through targeted edits.
The original stylesheet, API contract files, map implementation, fixture data,
and existing check script were not changed by this task.

## Verified contracts and integration boundaries

Read-only inspection of `backend/contracts.py`, `backend/fastapi_app.py`,
`backend/app.py`, `backend/reporting.py`, `recommendation_engine/scenarios.py`
and `documentation/API_COMPOSITION_CONTRACT.md` confirmed:

- Existing analytics paths correspond to the frontend capability names.
  Backend data still needs explicit mapping into the existing frontend view
  models. Registered routes do not establish deployed service readiness.
- Login uses `POST /api/v1/auth/login` with `username` and `password`; account
  creation uses `POST /api/v1/auth/register` and always receives the Evaluator
  role. Sign-up requires the configured HTTPS API and PostgreSQL auth schema;
  no default credentials are seeded. Sessions remain server-persisted.
- Reports use `GET /api/v1/reports/{report_type}` and its `/download` route,
  with CSV or JSON formats. The 12 displayed categories match
  `REPORT_CAPABILITIES`. No report history, generated file or job is invented.
- Model summaries exist for demandForecast, routeClusters, delayAnalysis and
  occupancy. The summary exposes metrics, baseline metrics, model comparison,
  sample counts, periods and run/version fields. Pipeline comparison also has
  an existing analytics route. No metrics or winning model are populated.
- What-if has schema version 1.0, baseline evidence (`source_id`, `pointer`),
  `scenario_type`, `entity_ids` and `proposed_changes`. Displayed scenario types
  match SCENARIOS; the default frequency change uses `trip_count`. All controls
  remain disabled until certified baseline selection and execution are wired.
- The standalone map reuses `normalizeNetworkGeometry` and
  `projectNetworkGeometry`; it draws no invented coordinates or geometry.

No new backend endpoint or response contract was introduced. The new
`resultPanel` accepts a frontend presentation object `{state, rows, source,
asOf}`. This is not an API response contract. `setApiViewModel` remains the
injection boundary. A transport mapper must provide explicit display rows;
raw backend responses should not be passed straight to the UI.

## Remaining integration work

All production KPI values, trends, tables, forecasts, alerts, model results,
reports, pipeline status and geometry await verified source data. Existing
chart functions and chart-state components are retained for future mapping.

Needed later:
- Session transport and role-aware UI connected to existing authentication.
- Certified response-to-view-model mapping, applied filters, freshness and
  provenance for each analytics capability; existing legacy charts still
  require their page-specific mapping before displaying live series.
- Authenticated report download transport and ready/unavailable handling.
- Baseline/catalog selection and scenario-specific fields/validation before
  enabling scenario submission; results and assumptions mapped from responses.
- Stable mappings for peak windows, underutilization, delay prediction,
  occupancy forecasts, passenger aggregate clusters and comparable test/unseen
  metrics. Dedicated backend contracts for these additional surfaces were not
  established in this task; do not assume new routes exist.
- Verified network geometry mapped into the existing geometry view model.

## Lightweight validation

- `npm --prefix frontend run check` — PASS. 32 source JS files syntax checked,
  14 required entries, 24 original page/mode renders, 12 partial-response
  isolation renders, shell mount, map flow and mock isolation.
- `node frontend/scripts/check-product.mjs` — PASS. 22 routes, 44 page renders,
  every hash transition, five result states, safe first-visit default, escaped
  result content and zero network requests during navigation.

These checks run locally with Node, without dependencies or external services.
No browser visual pass was run; responsive styling has not been screenshot
validated on physical desktop/tablet/mobile devices. No broad project tests,
commits or pushes were performed.

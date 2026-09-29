# Recommendation and what-if execution layer

**IMPLEMENTED:** SRS Steps 45–49 rule execution, deterministic scenarios and explicit
request/result contracts. **FIXTURE-TESTED:** tiny synthetic analytical packages.
**PENDING CERTIFIED RUNTIME EVIDENCE:** real recommendations, scenario baselines and
operational benefit. This package never runs raw-data analysis, feature engineering,
Spark, HDFS or ML training, and calls no generative-AI service.

## Existing implementation reused

`recommendation_engine/` previously contained only `.gitkeep`. The existing backend
contracts expose `recommendations` (`insights`, `actionQueue`) and `whatIf`
(`baseline`, `scenario`, `assumptions`) but contain no recommendation/scenario logic.
They are unchanged. This package produces execution artifacts; wiring those artifacts
into those API fields remains an application integration step, not a backend change.

The package reuses `evidence_framework.schema.NOT_READY`,
`ml_execution.contracts.finite` and `ml_execution.results.read_result` for ML source
readiness. It does not modify ML execution code or duplicate analytic aggregations.
Current ML service summaries contain useful delay/occupancy metrics, but not all
passenger-impact, feasibility, coverage or scenario inputs below. Missing fields
must be published by the analytical producer; the engine does not invent them.

## Evidence package contract (v1.0)

A local manifest references explicitly selected analytical JSON artifacts:

```json
{
  "schema_version": "1.0",
  "status": "CERTIFIED",
  "dataset_version": "<certified dataset version>",
  "analytics_version": "<analytical bundle version>",
  "sources": {
    "service": {
      "path": "service_analytics.json",
      "kind": "analytics",
      "version": "<source analytical version>",
      "sha256": "<SHA-256 of exact source bytes>"
    }
  }
}
```

An `analytics` source requires `status: SUCCEEDED`, `certification_status: CERTIFIED`,
matching `dataset_version`, and `analytics_version` matching the source reference.
An `ml_execution` source instead passes the existing result/artifact readiness
validator and uses its `feature_version` as source version. Only explicit local paths
inside the analytical package are allowed; raw_data paths are rejected. Missing
files, hashes, versions and unsuccessful artifacts produce NOT_READY.

Source certification is the publishing analytics pipeline's attestation. Hashes
bind the selected facts to its exact output; they do not independently prove raw
correctness or statistical validity. The producer must certify the window, grain,
passenger counts, thresholds, applicable vehicle phase and model evaluation.

For synthetic tests only, the manifest uses `status: FIXTURE`, sources use
`status: FIXTURE_TESTED`, and execution requires `fixture=True` / `--fixture`.
Fixture artifacts are refused by normal execution. No certified artifacts are
provided or fabricated by this implementation.

Evidence references use `{"source_id": "service", "pointer": "/cases/0"}`.
The JSON pointer resolves directly into the hash-checked source. Facts are copied
from that source, rather than supplied as unexplained recommendation request values.
Every output includes source path, pointer, checksum, version, dataset version and
manifest hash. Engine source hashes, request/policy hashes, Python version and UTC
generation time support reproduction. No random numbers are used. Callers may pass
`generated_at` explicitly to reproduce an entire result including its timestamp.

## Recommendation contract and rules

A recommendation request:

```json
{
  "schema_version": "1.0",
  "cases": [{
    "evidence": {"source_id": "service", "pointer": "/cases/0"},
    "rules": ["increase_frequency", "improve_headways"]
  }],
  "policy": {
    "minimum_samples": 10,
    "minimum_days": 3,
    "overload_ratio": 1.0,
    "underutilization_ratio": 0.4,
    "late_fraction": 0.25,
    "bottleneck_delay_sec": 300,
    "headway_cv": 0.3,
    "passenger_impact_bands": [50, 200, 500],
    "severity_bands": [1.1, 1.5, 2.0]
  }
}
```

**These numeric policy values are illustrative fixture choices, not SRS-prescribed
thresholds or approved production policy.** All values must be supplied explicitly;
there are no silent defaults. The approved policy must match the producer's metric
definitions (especially overload/underutilized day counts).

Each referenced analytical case contains:

- `entity_ids`: route_id required; trip_id/stop_id where applicable; preserve direction
  and time-segment IDs when available.
- `window`: timezone-aware `start` and `end`, with start before end.
- `evidence_kind`: `OBSERVED` or `MODEL_ESTIMATE`. Estimated effects must be labelled
  as such by the analytical producer, including evaluated departure alternatives.
- `metrics`: positive `sample_count`, nonnegative `affected_passengers`, and the
  rule-specific fields below. Passenger impact uses the same case window and scope;
  count unique affected passengers or document the passenger-event denominator.
- Boolean policy/feasibility facts listed below, when needed.

| Rule | Required measured evidence and trigger | Operational severity |
| --- | --- | --- |
| `increase_frequency` | mean_occupancy_ratio > overload_ratio; overload_days >= minimum_days | occupancy / overload_ratio |
| `reduce_underutilized_service` | mean_occupancy_ratio < underutilization_ratio; underutilized_days >= minimum_days; social_service_required=false; coverage_preserved=true | 1 + fractional shortfall below underutilization threshold |
| `modify_schedule` | late_fraction >= policy late_fraction; late_days >= minimum_days | late_fraction / threshold |
| `higher_capacity_vehicle` | peak_occupancy_ratio > overload_ratio; overload_days >= minimum_days; higher_capacity_vehicle_feasible=true | peak occupancy / overload_ratio |
| `investigate_bottleneck` | stop_id; mean_delay_sec >= bottleneck_delay_sec; late_days >= minimum_days | mean delay / threshold |
| `adjust_departure_time` | trip_id; nonzero evaluated_shift_minutes; current_expected_wait_minutes > alternative_expected_wait_minutes | 1 + fractional expected-wait reduction |
| `improve_headways` | headway_cv >= threshold; irregular_headway_days >= minimum_days | headway coefficient of variation / threshold |

Day counts are distinct observed service days, not observation counts. Late fraction
uses all eligible services, not a positive-delay-only denominator. Occupancy uses
certified positive phase-correct capacities; unknown capacity is not zero occupancy.
Coverage-required social services are protected from blanket low-load reductions.
A higher-capacity recommendation requires supplied feasibility evidence. Actions
are reviewable operational recommendations; no dispatch/schedule mutations occur.

### Priority

Impact band = number of configured passenger thresholds met (0..3). Severity band =
number of configured operational-severity thresholds met (0..3). Priority index =
`floor((impact band + severity band) / 2)`:

| Index | Priority |
| --- | --- |
| 0 | LOW |
| 1 | MEDIUM |
| 2 | HIGH |
| 3 | CRITICAL |

CRITICAL therefore requires both highest passenger impact and highest operational
severity. Every recommendation saves the observed inputs, bands, severity ratio,
policy and formula. Confidence is `EVIDENCE_SUPPORTED` with a null probability; the
engine does not fabricate calibrated confidence or claim proven causal benefit.

Output includes action, entity IDs, measured reason, supporting metrics and complete
source evidence, priority, evidence kind/status, generated timestamp and provenance.
Stable recommendation IDs derive from rule, source reference, entity and policy.
Duplicate rules cannot emit duplicate actions. A non-triggering rule returns
`NO_ACTION` with no recommendation. Missing required evidence returns NOT_READY;
where some rules have enough evidence, their supported recommendations are retained
but the overall request remains NOT_READY with per-rule readiness reasons.

## Scenario request and baseline

```json
{
  "schema_version": "1.0",
  "scenario_type": "increase_frequency",
  "entity_ids": {"route_id": "<route>", "direction_id": 0},
  "baseline_evidence": {"source_id": "service", "pointer": "/scenario_baselines/0"},
  "proposed_changes": {"trip_count": 4}
}
```

All scenario baselines are selected from a hash-checked analytical source and require:

- `entity_ids` with route_id and direction_id; timezone-aware `window_start`;
  positive `window_minutes`, `cycle_minutes`, integer `vehicle_count`.
- `trips`: unique trip_id, departure_minute within [0, window_minutes], positive
  capacity. Departure times must be distinct. Capacity uses the modeled phase.
- `stop_ids`: unique existing stop IDs.
- `initial_queue`: explicitly known zero. Unknown/nonzero initial queue returns
  NOT_READY because this model lacks earlier-arrival waiting-time evidence.
- `demand_profile`: ordered, gap-free/non-overlapping intervals covering the whole
  window; each has start_minute, end_minute and nonnegative passengers. Zero demand
  must be explicit; missing intervals cannot be filled with zero.
- `demand_kind`: OBSERVED or MODEL_FORECAST; forecast demand requires
  `demand_model_version`. Increasing predicted demand specifically requires a forecast.
- Positive `crowding_threshold_ratio`.
- For trip removal: explicit social_service_required=false and
  `removal_eligible_trip_ids`, certified using low-demand and service-coverage analysis.

### Eight deterministic scenarios

| scenario_type | proposed_changes and entity IDs | Calculation |
| --- | --- | --- |
| increase_frequency | trip_count greater than original | Evenly re-space that many departures in the same window |
| decrease_frequency | positive trip_count smaller than original | Same re-spacing with fewer departures |
| add_vehicle | empty changes; route/direction IDs | Add one vehicle and floor(window/cycle) departures; evenly re-space |
| change_vehicle_capacity | positive capacity | Apply capacity to every route departure |
| shift_trip_start_time | nonzero shift_minutes; trip_id | Shift only that trip, retaining the demand profile |
| remove_low_demand_trip | empty changes; trip_id | Remove only the eligible trip; retain all demand in the queue |
| add_new_stop | stop_id; positive added_cycle_minutes; complete additional_demand_profile | Add explicitly assumed stop demand; departures=floor(original departures × old cycle / new cycle) |
| increase_predicted_demand | factor >1 | Multiply forecast demand in every interval |

Frequency/cycle scenarios require homogeneous original capacities, avoiding an
invented replacement capacity for mixed fleets. New-stop demand uses the same
explicit interval grid as the baseline. Its demand and added cycle time are clearly
reported **user scenario assumptions**, not observed facts or model outputs.

An invalid direction of change, unknown/mismatched ID, duplicate stop/departure,
negative capacity, outside-window shift, noninteger trip count or unsupported field
returns INVALID_REQUEST. Removing the only departure or a protected/ineligible trip
is rejected. If an added vehicle/stop cannot support a full-cycle departure under
this approximation, return NOT_READY for a more detailed duty model. There is no
unsubstantiated fallback estimate. These are service-window estimates, not an
operational feasibility solver; fleet duties/resources need separate review.

### Supported impact calculations

Demand arrives uniformly within each supplied interval. At each departure, the
model boards the oldest waiting passengers up to capacity. Expected passenger
counts may be fractional. It analytically integrates arrival cohorts to estimate
served-passenger waiting times; it does not sample synthetic individual passengers.

- **Occupancy:** boarded passengers / sum of departure capacities; capped boarding
  means observed-style occupancy does not exceed 1. Unconstrained load ratio separately
  reveals demand pressure above capacity.
- **Passenger load:** mean boarded passengers per departure; per-trip loads also saved.
- **Waiting time:** FIFO served-passenger mean wait, calculated from arrival intervals.
- **Route capacity:** sum of capacities offered during the service window.
- **Demand coverage:** passengers boarded / total input demand; a service coverage
  ratio for this modeled segment, not geographic/network coverage.
- **Overcrowding risk:** fraction of departures where queued demand exceeds
  crowding_threshold_ratio × capacity. This is a deterministic pressure indicator,
  **not a calibrated probability**.
- **Additional evidence:** total served/unserved passengers, peak unconstrained load,
  trip/vehicle counts and per-departure estimates. Arrivals after the last departure
  remain unserved; removed-trip demand is not deleted.

Zero input demand yields null coverage and null waiting time. No served passengers
yields null waiting time. Deltas involving null stay null; undefined quantities do
not become zero. All other deltas are scenario minus baseline in the named units.

Assumptions: one route/direction and common boarding point/critical segment; one
ride per passenger; FIFO/hard capacities; no abandonment, alighting, induced demand,
network rerouting or feedback into predictions. Added-stop effects are a common-
segment approximation, not route-wide OD simulation. The result states these limits.

### Result contract and readiness

Scenario results contain `scenario_type`, entity_ids, original_values,
proposed_changes/proposed_values, assumptions, baseline_metrics, estimated_metrics,
deltas, evidence_source/version, limitations, status, generated_at and provenance.
`result_type`, `baseline_metrics_type` and `estimated_metrics_type` are **ESTIMATE**.
Even baseline metrics are modeled estimates from source inputs, not relabelled
observations. Original input state is copied and preserved without modification.

The public `recommendation_engine.execution.execute(mode, manifest, request, ...)`
returns explicit NOT_READY with no calculated metrics if required artifacts or
inputs are absent. Invalid scenario requests return INVALID_REQUEST. Neither is a
successful result. Synthetic successes are FIXTURE_TESTED, never production evidence.

## Execution (after certified analytics exist)

```bash
python3 -m recommendation_engine recommendations --manifest /path/to/manifest.json --request /path/to/recommendation_request.json --output /path/to/new_recommendations.json
python3 -m recommendation_engine what-if --manifest /path/to/manifest.json --request /path/to/scenario_request.json --output /path/to/new_scenario.json
```

The output parent must exist; an existing output is never overwritten. These
commands read only named artifacts and write only the requested result. Exit 0
indicates completed execution; exit 2 indicates NOT_READY/INVALID_REQUEST. The
backend adapter remains untouched and does not automatically consume these files.

Runtime: Python >=3.10 and repository-local evidence/ML contract modules. The
execution logic uses only the standard library; no ML packages are needed.

## Verification and exact pending inputs

```bash
python3 -m unittest discover -s tests -p test_recommendation_engine.py -v
python3 -m compileall -q recommendation_engine tests/test_recommendation_engine.py
```

Tests cover every rule/scenario, all priority levels, recurrence and no-action cases,
service protection, evidence absence/hash/version checks, FIFO calculations,
conservation, adverse effects, null metrics, immutable baselines, estimate labels,
request boundaries, deterministic calculations and result provenance.

Still required for certified execution:

1. Certified, versioned analytical JSON artifacts with exact hashes and source pointers.
2. Per-entity/window sample counts, affected-passenger counts and applicable measured
   occupancy/overload, delay/recurrence, headway variability and evaluated shift facts.
3. Social-service flags, coverage-preservation evidence, vehicle feasibility and
   certified low-demand removal eligibility.
4. Reviewed policy thresholds and consistent metric denominator/period definitions.
5. Scenario schedule/capacity/stop state, route/direction, cycle/fleet counts, explicitly
   known initial queue and complete demand intervals; evaluated model/version for forecasts.
6. User scenario choices, including explicit new-stop demand and cycle-time assumptions.
7. Actual certified execution/review and application artifact mapping. Fixture tests
   do not establish real operational benefit, forecast accuracy or scenario validity.

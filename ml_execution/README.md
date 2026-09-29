# Mandatory ML and analytics execution

**IMPLEMENTED:** executable consumers of certified task features, independent Python
and Spark model adapters, evaluation, selection and artifact contracts.
**FIXTURE-TESTED:** standard-library contracts, metrics, selection, analytics and
readiness. **PENDING CERTIFIED RUNTIME EVIDENCE:** all actual model fitting and
production performance. NumPy, scikit-learn and PySpark are absent in this environment.
No dependency installation, production data access or model training was performed.

## Scope and reuse

This package leaves feature engineering, production remediation, backend and frontend
untouched. It imports `python_pipeline.splits.SPLITS` / `assign_split` and
`evidence_framework.schema.NOT_READY`. Task package locations can sit beneath the
existing `python_pipeline.outputs.OutputContract.split(task, split)` locations, but
publication/export remains owned by feature engineering. No joins, target labels,
lag calculations, feature encoding or raw-data cleaning are duplicated here.
Numeric model scaling is independently fitted on TRAIN by each model adapter.

Existing `spark_jobs.analytics` remains the independent Spark analytics entry point;
this package adds the required richer Python service summaries. The existing
`spark_jobs.model` trains and predicts on the supplied split and does not provide
held-out evaluation. It is unchanged and is **not** used for acceptance evidence.

### Tasks and candidates

| Task | Prepared execution | Validation selection |
| --- | --- | --- |
| `delay_severity` | Logistic regression, decision tree, random forest | Highest macro F1 |
| `passenger_demand` | Regularized linear regression, decision tree, random forest | Lowest MAE |
| `occupancy_forecast` | Same three regressors, capacity eligibility required | Lowest MAE |
| `crowding_risk` | Same three classifiers; class 1 means threshold exceeded | Highest macro F1 at declared probability threshold |
| `route_clustering` | K-means at k=2, 3, 4; independently fitted standardization | Highest validation silhouette |
| `delay_service_analytics` | Python delay, punctuality, recurrence, occupancy and service summaries | No predictive model selection |

Both Python/scikit-learn and Spark MLlib adapters implement the five predictive /
clustering tasks. Spark fits its own scaler, models and predictions from its own
feature producer. No Python fitted object or prediction is supplied to Spark.
The Spark analytics task returns NOT_READY here and points to the existing entry
point; it does not label Python calculations as Spark execution.

Default seed is 20260926. Trees/forests have bounded depth; forest uses 80 trees and
Python uses one job. All candidate parameters, effective estimator defaults and
per-candidate timings are recorded. Alphabetical model name breaks exact validation
ties. Three valid measured candidate scores are required; failures are recorded and
never replaced with synthetic scores. Test scores do not influence selection.
Candidates are fixed in advance: no internal random cross-validation or test tuning.
Models/scalers stay fitted on TRAIN after validation selection; no TRAIN+VALIDATION
refit. Clustering quality uses at most 500 deterministically sampled validation/test
cases and reports sample size, distance and seed. Undefined silhouette is null, and
cannot select a winner. Saved cluster interpretation includes original-unit feature
means, route IDs, counts and split-specific assignments rather than invented names.

## Feature handoff contract v1.0

Feature engineering must publish **one independently certified package per task and
engine**. `feature_manifest.example.json` is a deliberately pending template, not
certification evidence. The consumer accepts bounded numeric JSONL task matrices.
The existing foundation currently emits Parquet/shared features, so FE still needs
to publish this adapter handoff; this layer does not pretend those files already
match. No upstream files have been edited to force this interface.

This is a bounded model execution handoff (default 100,000 cases total), not a raw
production loader or proof of 10-million-row scalability. Python holds matrices in
memory; Spark receives that bounded matrix and computes its own MLlib models.
Do not raise the bound without assessing memory. Full-scale distributed loading and
scalability evidence are outside what this package verifies.

Required manifest fields:

- `schema_version`: `1.0`; `task_name`: one of the task names above.
- `dataset_version`, `feature_version`, `producer` (`python` or `spark`). Producer
  must equal the selected execution engine. Same raw snapshot and case IDs may be
  shared; derived feature values must come from each independent pipeline.
- `features`: ordered numeric allowlist, finite values only. Upstream must publish
  appropriate numeric predictors. IDs, labels, generator truth and audit outcomes
  are not predictors; explicit known identifiers and target fields are rejected.
  Certification must review the full allowlist, including aliases this consumer
  cannot semantically identify. No silent imputation is performed.
- `grain`, `target_definition`: explicit task unit/label policy, including units,
  served vs requested demand and phase/eligibility. Publish separate demand runs for
  route, stop, time-period and scheduled-trip grains as supported by certified data.
- `partitions`: exactly `train`, `validation`, `test`, each with relative JSONL `path`
  and `sha256`. Paths remain inside the feature package; raw_data is rejected.
- `certification`: `status: CERTIFIED` and relative `evidence_path`. The evidence
  JSON must contain `status: CERTIFIED`, matching dataset/feature/task/producer,
  identical `partitions`, and `contract_sha256`. Calculate the latter with
  `ml_execution.contracts.contract_digest(manifest)` after the final manifest is
  formed; it binds all fields except the certification reference. This is a handoff
  attestation by the certifying pipeline, not an independent certification of raw
  correctness. A copied raw-dataset marker is insufficient.

Every JSONL row requires:

- Globally unique `case_id`; local `service_date` in ISO date format.
- Timezone-aware `target_start`, `target_end` (exclusive), `feature_cutoff_at`,
  `value_available_at`, `target_available_at`.
- `operational_departure_id`, or nonempty `source_departure_ids` listing stable
  departure lineage for aggregate cases. All representations of a departure must
  stay in one partition. Cases may share a departure within the same partition.
- Every declared numeric predictor and, for supervised tasks, finite `target` value.

`value_available_at` is the maximum actual availability of **all** predictor inputs,
including historical corrections. FE owns the per-feature evidence supporting that
maximum, frozen historical revisions, lags, label construction and exclusions. The
consumer enforces availability <= cutoff, prediction cutoff <= target start, label
availability >= target end, and label availability by the TRAIN/VALIDATION boundary.
Aggregate analytics/clustering target intervals must finish before their historical
cutoff, and cutoff must stay inside the partition. Unknown availability is rejected.
`delay_severity`, `occupancy_forecast`, and `crowding_risk` are point-event targets
and may represent their event instants with `target_start == target_end`. Occupancy
and crowding additionally require both the event time and target availability to be
strictly later than the feature cutoff.

### Frozen chronological periods

| Partition | Inclusive local service dates |
| --- | --- |
| TRAIN | 2025-01-01 through 2025-12-31 |
| VALIDATION | 2026-01-01 through 2026-03-31 |
| TEST | 2026-04-01 through 2026-06-30 |

Complete target windows are checked against Asia/Karachi local midnights converted
to timezone-aware instants. A target may end exactly at the exclusive boundary;
it must not cross it. Boundary-straddling cases must be purged/reissued upstream.
No random partitioning is used. Frozen models support only
`rolling_origin_frozen_model`: new certified lag observations may become available
at each issue time, but fitted parameters never change. Fixed-origin evaluation is
not silently mixed into that protocol.

### Supervised task additions

- `target`: exact upstream label/value column.
- Classifiers: `class_labels` ordered by integer label 0..n-1; `thresholds` records
  FE's configurable severity or crowding label boundaries. TRAIN must include every
  declared class. The runner does not derive or alter labels.
- Crowding: exactly two classes, 1 meaning exceeds the configured occupancy ratio;
  `risk_probability_threshold` strictly between 0 and 1. Output includes measured
  probabilities, high-risk flags and Brier score; probability calibration is not
  claimed from the existence of probabilities.
- Occupancy/crowding cases additionally require `occupancy_status: AVAILABLE` and
  positive `capacity_snapshot`. Upstream must bind that capacity to the correct
  actual departure phase. Unknown capacity must remain excluded, not imputed.
  The predictor allowlist rejects current-event load, capacity, utilization, delay,
  and target/availability fields.
- Regressors: nonnegative demand/occupancy targets; `baseline_type` must be
  `seasonal_naive` or `last_observation`; `baseline_column` names a finite historical
  predictor in `features`. Supply `forecast_horizon` and the protocol above.

### Forecast baseline and evaluation

The baseline prediction is exactly the certified lag value at each forecast issue
time (e.g. last week's same route/stop/time interval). FE declares the lag, units,
horizon, availability and valid zero-demand grid in `target_definition`. Cold starts
without certified history must be explicitly handled upstream; the runner fails on
missing history and never fabricates a fallback baseline.

Baseline and candidate metrics use the same cases. Metrics: MAE, RMSE, percentage
MAPE excluding zero actuals with denominator counts, and R². All-zero actuals give
null MAPE; constant/single-case actuals give null R². Signed errors are not hidden by
clipping predictions. Linear regressors can produce negative predictions; inspect
these before operational use. Model selection minimizes validation MAE; acceptance
requires **strictly lower TEST MAE than the documented baseline**. Equal/worse values
fail. Both absolute and relative improvement are saved; a zero baseline error makes
relative improvement undefined (null). No improvement is presumed.

Classification metrics: accuracy, support-weighted precision/recall/F1, macro F1 over
all declared classes, per-class scores/support and confusion matrix (rows=true,
columns=predicted). Undefined per-class divisions use the documented zero convention.
The test acceptance check is accuracy >=0.85 **OR** macro F1 >=0.80. Fixture acceptance
is explicitly labelled synthetic and cannot satisfy production acceptance.

### Service analytics additions

Manifest `analytics_policy` declares nonnegative early/late tolerances in seconds,
`recurrence_min_days` >=2, and three increasing positive `occupancy_bands` ratio
boundaries. No operational threshold is represented as prescribed by the SRS.

Supply certified, unique full service/stop cases, including on-time/early visits and
explicit service statuses. Never use the positive-only Delays table as the denominator.
Expected existing feature/identity columns:

- `delay_status: AVAILABLE`, `signed_departure_delay_sec` for eligible delay cases.
- `route_id`, `trip_id`, `stop_id`, `direction_id`, `event_hour`, `weekday`,
  `distance_band`; `departure_vehicle_id` plus `departure_assignment_status: KNOWN`
  for vehicle attribution. FE owns time/distance grouping features.
- `occupancy_status: AVAILABLE`, positive `capacity_snapshot`, finite nonnegative
  `capacity_utilization`; ratios over 1 are retained as real overload.
- `service_status`, including cancellations and unavailable observations.

Results include delay mean/dispersion, late/early/on-time counts/rates; route, trip,
stop, vehicle, time, day, direction and distance breakdowns; recurring route/time
patterns by distinct days; occupancy band counts, repeated overload and underutilized
counts. Missing attribution affects only that dimension and is counted. No eligible
cases means null/NOT_READY for that capability. Punctuality excludes unavailable
observations and reports service statuses separately. Recurrence means observed on
at least the configured number of days; it is not a significance or causality claim.

## Result artifacts

Each new run directory contains `result.json`, and successful models also produce
`predictions.jsonl`, `selected_model.pkl` (Python) or `selected_spark_model/` (Spark).
Clustering additionally saves `cluster_interpretation.json`. Never overwrite a run.
Only load pickle models from trusted local runs.

The result contract records task/model/type, requested and effective parameters,
seed, feature version/list, dataset version, periods, all sample counts, validation
and test metrics, baseline metrics/comparison, every candidate's validation result
or failure, measured selection rationale, total/per-candidate runtime, artifact
paths, exact input/source hashes, Python/library versions, thread settings and Spark
configuration. Predictions retain shared case IDs, truth, split, issue/target times,
model and dataset versions for later independent-pipeline comparison. A >=100 unseen
case comparison and production-scale evidence remain later runtime obligations.

Consumers should call `ml_execution.results.read_result(path)`. Missing, failed,
incomplete or fixture-only artifacts return NOT_READY with empty metrics and no
acceptance claim. Passing `allow_fixture=True` is only for tests. Runtime exceptions
produce FAILED; unavailable data/dependencies produce NOT_READY. Failures never
become successful zero-valued metrics. Candidate failures remain in the comparison.

## Later execution (not run here)

From `/home/manal/UrbanTransit-IQ`, after certified publication and dependency setup:

```bash
python3 -m ml_execution --manifest /path/to/python-task/manifest.json --output /path/to/new-python-run
spark-submit /path/to/repository/ml_execution/__main__.py --engine spark \
  --manifest /path/to/spark-task/manifest.json --output /path/to/new-spark-run
```

Each command must consume a handoff whose `producer` matches its engine. The Spark
command requires a compatible Spark/PySpark/Java runtime; it must not be replaced by
the Python runner on Spark-produced features.

These placeholder paths must be replaced with the certified task package and a new
run directory. Run each task separately; neither command discovers raw data. Exit 0
means execution succeeded (inspect acceptance separately); exit 2 means unavailable
or failed. `--fixture` requires certification status `FIXTURE` and labels all output
as fixture evidence. It cannot consume a CERTIFIED manifest as fixture evidence.

Runtime requirements: Python >=3.10 with timezone data; NumPy/scikit-learn for Python
model fitting; PySpark plus compatible Java/Spark runtime for Spark MLlib. Model
versions and preprocessing differ between libraries and should not be expected to
produce identical predictions. Runtime compatibility/model serialization must still
be verified once dependencies exist. Standard-library analytics/tests need none of
those optional ML packages. No dependencies were installed or requirements changed.

## Lightweight verification

```bash
python3 -m unittest discover -s tests -p test_ml_execution.py -v
python3 -m compileall -q ml_execution tests/test_ml_execution.py
```

Tests create only tiny synthetic data in temporary directories. They check period
boundaries, label/feature availability, group isolation, certified hashes, capacity
eligibility, known metric calculations, data-driven selection, baseline failure/tie,
undefined metrics, deterministic configuration/silhouette, result contracts, analytics
and missing-artifact/dependency readiness. An optional tiny real-model test is skipped
when scikit-learn is absent. Spark fitting is not executed by this suite.

## Exact pending handoff

1. Independent Python and Spark task matrices, case IDs, lineage and hashes.
2. Final allowlists and feature version; actual as-of availability/revision evidence.
3. Configurable delay-severity labels, occupancy/crowding thresholds and correct
   capacity eligibility; documented task grain and target units.
4. Certified demand/occupancy historical baseline values, forecast horizon/protocol,
   complete demand grid and explicit cold-start/missing-history treatment.
5. TRAIN/VALIDATION/TEST cases meeting the fixed windows and all class coverage;
   route-period aggregates with enough distinct routes for k=2, 3, 4 evaluation.
6. Full-denominator delay/service cases and dimension/attribution columns above.
7. Matching per-task feature certification artifacts and final runtime dependencies.
8. Actual candidate fits, held-out metrics, forecast improvement, classification
   target evidence, saved models and independent >=100-case comparison. All remain
   **PENDING CERTIFIED RUNTIME EVIDENCE**.

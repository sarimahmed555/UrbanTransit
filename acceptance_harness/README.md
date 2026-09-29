# Read-only SRS acceptance harness

**IMPLEMENTED:** traceable requirement groups, bounded evidence reader, numeric and
review checks, dependency blockers, JSON report contract and Markdown/action summary.
**FIXTURE-TESTED:** synthetic evidence only. **PENDING REAL EVIDENCE:** project runtime
acceptance. Neither this README nor the existence of implementation code proves
compliance. No project acceptance report was generated against production artifacts.

## Scope and authority

The official `documentation/UrbanTransit IQ-Data Science Intelligence Arena_SRS.pdf`
is authoritative. `catalog.py` records section/step references and separates:

- `SRS_MANDATORY`: mandatory capabilities, thresholds and deliverables, including
  conditional wording under the §1.6/1.7 blanket implementation requirement.
- `IMPLEMENTATION_CHOICE`: the project's 21 physical tables, frozen 2025/2026 split
  dates, named feature families, and the eight chosen scenario examples.
- Missing execution/review evidence: `PENDING_RUNTIME`, regardless of code existence.

The existing evidence framework's status vocabulary is reused. Current table,
split and feature definitions are imported from the existing pure contract modules.
No recorder, pipeline, model, authentication runtime, database adapter or exporter
is invoked. Native evidence categories/ML/recommendation/security contracts were
inspected; this package does not modify or replace them.

The checks are **groups, not a complete automated transcription of every SRS clause**.
The mandatory `srs.remaining_review` gate requires full-SRS traceability review,
verification of unencoded mandatory clauses and resolution of remaining ambiguities.
It prevents an all-green subset from claiming whole-project acceptance. Qualitative
requirements and authenticity cannot be proved from JSON fields alone.

## Coverage

- Official dataset minima: 2M ticketing **or** movement records, 500K trip-level
  passenger records, 100 routes, 500 stops, 250 vehicles, 50K unique passengers,
  12 historical months, 250K delay records, multiple calendars and schedules.
  Multiple is interpreted as >=2; no project-specific larger targets are substituted.
  Trip-level passenger records are not counted as distinct trips. Overlapping ticket
  and movement channels must not be summed to manufacture the 2M minimum.
- Connectedness/keys, explicit project table inventory, ≥2M actual processing,
  HDFS, CSV/JSON/Parquet use, Spark ingestion/SQL joins/aggregations and large
  analytical Parquet readback. A path or script alone is insufficient.
- DQ/cleaning, chronological/as-of guards, fitted preprocessing separation,
  feature outputs and project contract coverage.
- Required predictive tasks; ≥3 suitable delay models and ≥3 distinct suitable
  Spark algorithms. There is no invented universal Python three-model requirement.
- TEST classification accuracy >=.85 OR macro F1 >=.80 when appropriate;
  confusion-matrix reconciliation; measured forecast-baseline improvement on the
  same TEST cases; conditional R²; route clustering and quality/interpretation;
  occupancy/crowding and delay/service analytics.
- Independent Python processing and ≥100 unseen Spark/Python comparison cases.
  No invented minimum agreement rate, and no substitution of the 1,000-case project
  target for the SRS minimum. Identical outputs require independence review; they
  are not automatically evidence of copying.
- Evidence-based recommendations, four priority values and reviewed negative
  controls. Legitimately empty recommendations require reviewed no-action evidence.
  What-if outputs must be labelled ESTIMATE with baseline/changes/assumptions,
  deltas, sources and limitations. Exact eight-scenario coverage is a separate
  implementation-choice check because Step 48 introduces examples with “such as”.
- API integration, four-role authentication/RBAC runtime, application storage,
  prepared-dashboard response evidence, ≥10M architecture capability and >=99%
  evaluation uptime. A database interface or authentication unit test does not prove
  deployment. PostgreSQL is the project choice; the SRS permits relational/NoSQL
  storage where required, so database acceptance does not mandate a vendor.
- Reproducibility, executed tests, AI_USAGE content review, five-day Git/team
  evidence, development log, README/install/run reviews and final submission items.
  The MP4 demonstration and published >=2,000-word blog have separate checks.

## States and aggregation

| State | Meaning |
| --- | --- |
| PASS | Supplied, provenance-gated evidence satisfies this encoded criterion |
| FAIL | Supplied measured/reviewed evidence violates it, or source reports failure |
| PENDING_RUNTIME | Execution/review/facts are absent, only READY/code/fixtures exist, or upstream runtime proof is pending |
| NOT_APPLICABLE | Only an explicitly encoded conditional exclusion; currently undefined R² with measured constant targets or fewer than two cases |
| BLOCKED | Evidence hash/version/schema/path is inconsistent, or a prerequisite has failed/is blocked |

There is no free-text “waiver” that can mark mandatory requirements NOT_APPLICABLE.
The missing-evidence action list names each unresolved check and the first missing
field/reason; publication of that field can expose further incomplete fields.

Mandatory-state precedence is FAIL, BLOCKED, PENDING_RUNTIME, then PASS. Project
choices remain separately visible and do not redefine the SRS. A downstream PASS is
withheld while its declared prerequisites are pending. Dependencies are deliberately
limited to defensible prerequisites; this is not a speculative workflow scheduler.
`FIXTURE_ONLY` reports always have overall PENDING_RUNTIME and mark rows ineligible
for real acceptance, even when an individual fixture criterion returns PASS.

## Evidence interface

The harness reads only explicitly selected JSON summaries, at most 4 MiB each,
inside the project root. It does not glob evidence directories or scan data. It
rejects raw_data, data_generator, .git and production-prefixed paths. During active
materialization, the owning agent must publish an approved bounded acceptance
summary outside those paths. This harness does not copy or rewrite their evidence.
It does not fetch URLs, run Git, connect to PostgreSQL, invoke shell commands or
execute Python/Spark/HDFS/ML workloads. Remote/artifact accessibility must be supplied
as a recorded verification, not assumed from a URL string.

Input manifest:

```json
{
  "schema_version": "1.0",
  "dataset_version": "<certified dataset version>",
  "source_revision": "<source revision used for this acceptance snapshot>",
  "checks": {
    "processing.2m": {
      "path": "bounded_evidence.json",
      "sha256": "<SHA-256 of exact file bytes>",
      "pointer": "/evidence/processing"
    }
  }
}
```

Paths are relative to the manifest and must resolve inside the repository. JSON
pointers bind each requirement to the original record. Unknown requirement IDs are
blocked, avoiding misspellings silently removing checks. The reader hashes and parses
one bounded byte snapshot, so concurrent changes cannot mix one read's values with
another read's checksum. A changed evidence file needs a fresh publisher-provided
manifest hash; the harness never repairs it.

Runtime record at the referenced pointer:

```json
{
  "status": "SUCCEEDED",
  "evidence_scope": "CERTIFIED_RUNTIME",
  "provenance": {
    "run_id": "<actual run>",
    "command": "<actual command>",
    "recorded_at_utc": "<timezone-aware timestamp>",
    "dataset_version": "<matching certified version>",
    "source_revision": "<matching source revision>",
    "artifact_references": ["<actual run/output/validation references>"]
  },
  "facts": {
    "rows_processed": 2000000,
    "runtime_sec": 1.0,
    "passenger_or_ticket_scope": true,
    "no_duplicated_counting": true
  }
}
```

**The numbers above describe the schema only and are not project measurements.**
A publisher must populate them from real execution. Do not copy example values to
claim compliance. `status: READY` alone cannot pass. `SUCCEEDED`/`PASSED` still need
scope, provenance and the actual check fields. A source-reported FAILED record with
valid scope/provenance is a failure, not a synthetic fallback metric.

Review records use `evidence_scope: REVIEWED_ARTIFACT` and require source_revision,
reviewer, reviewed_at_utc, review_reference and artifact_references. Reviewed flags
mean recorded review outcomes; they must not be set merely because a file exists.
Review remains pending until the responsible reviewer has performed that work.

Native result formats are not silently treated as these normalized acceptance
records. The reporting/evidence publisher should provide the bounded proof envelope
and preserve references to original native records. No edits to completed modules
are needed. Fields and policy are explicit in `evaluators.py`; `--catalog` lists
requirement IDs, dependencies and all common Boolean verification fields.

Synthetic tests use `evidence_scope: FIXTURE` and require `--fixture`. Real execution
rejects fixture evidence; no production-scope example evidence is shipped.

## Report contract and invocation

[report.schema.json](report.schema.json) defines the machine-readable output.
Each check records ID/title, SRS reference, basis, evidence kind, source hash/pointer,
state/reason, prerequisite blockers and acceptance eligibility. Top-level output
includes counts, dataset/source version, catalog hash, action list and limitations.
`report.markdown` renders the same decisions without changing them.

```bash
python3 -m acceptance_harness --catalog
python3 -m acceptance_harness --manifest /home/manal/UrbanTransit-IQ/reports/acceptance_input.json
python3 -m acceptance_harness --manifest /home/manal/UrbanTransit-IQ/reports/acceptance_input.json --format markdown
```

These are later-use examples; the input file is not presumed to exist. Output is
**stdout only**. The harness does not create or overwrite a report/evidence file.
Callers may explicitly capture stdout to a new report location when authorized.
Exit 0 means all mandatory checks passed on real evidence; exit 2 means acceptance
is incomplete/failed/blocked or the report is fixture-only. `--catalog` returns 0
for metadata listing, not acceptance.

## Ambiguities and human review

The following cannot safely receive invented automatic thresholds:

1. “Large” analytical Parquet dataset, “efficiently” processing, realistic data,
   algorithm suitability and architectural capacity need reviewed scope/evidence.
2. Dashboard five-second wording does not prescribe percentile, concurrency or test
   duration. The current check verifies every supplied timing <=5 seconds **and**
   requires review that the sample/protocol covers standard dashboards; it does not
   call an arbitrary p95 measurement an official SRS rule.
3. ≥10M is architectural capability without complete redesign, not an unconditional
   requirement to execute a 10M workload now. A documented capacity review is required;
   actual benchmark evidence can support it but is not invented.
4. Severity/occupancy/recurrence thresholds, model choice, forecast horizon, clustering
   quality cutoff and confidence policy are unspecified. Require documented,
   reviewed applicability; no hardcoded winner, minimum silhouette or margin is added.
5. Baseline improvement metric/margin is unspecified. The encoded choice supports
   a documented MAE/RMSE primary error metric with strict improvement and identical
   test cases. Other justified metrics require a reviewed evaluator extension.
   Macro F1 used instead of accuracy requires an applicability review.
6. “Such as” entity/scenario lists are not converted into mandatory physical schemas.
   The exact table names and eight scenario examples remain project choices.
7. Evaluation availability window/normal conditions, meaningful commits, all-team
   contribution, usability, security adequacy, complete video coverage and document
   quality require authentic human/runtime verification.
8. MAPE zero policy, R² applicability and other metrics must be interpreted for the
   task. R² NOT_APPLICABLE never exempts the forecast-baseline improvement requirement.
9. Passenger-flow/OD, peaks, anomaly handling, route scoring, report/export coverage,
   network map, all demonstration topics, integrity explanations and remaining SRS
   deliverables need the full-SRS review gate. Grouped checks are not substitutes for
   clause-by-clause traceability. Never approve that gate just because this harness ran.

Authenticity limitation: checks validate the supplied evidence's structure,
consistency, thresholds and provenance binding. They cannot independently prove that
an asserted review occurred or that measurements were honestly collected. Source
logs/artifacts and reviewer accountability remain necessary. This is an acceptance
harness, not a replacement data validator, runtime executor or forensic audit.

## Tests and pending inputs

```bash
python3 -m unittest discover -s tests -p test_acceptance_harness.py -v
python3 -m compileall -q acceptance_harness tests/test_acceptance_harness.py
```

Tests use tiny synthetic JSON under temporary directories inside the repository and
verify no input changes. They cover states, thresholds, scope isolation, provenance,
blockers, deterministic reports, baseline ties, model counts, comparison minimum,
architecture-vs-runtime distinction, conditional metrics and document review gates.
Only the standard library and existing pure contract modules are required.

Pending real inputs: versioned runtime evidence for data/HDFS/Spark/Python/features/
models/comparison/analytics/API/storage/security/performance; original run/output
references and hashes; reviewed documentation/Git/AI-use/architecture/submission
findings; and full-SRS traceability/ambiguity resolution. Existing code and the
fixture test results do not satisfy these pending runtime obligations.

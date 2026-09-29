# UrbanTransit IQ — TransitVerse Intelligence

Transit intelligence platform: a deterministic synthetic transport dataset, a
HDFS/Spark Big Data pipeline, a genuinely independent Python Data Science
pipeline, dual-engine ML with a case-level comparison, a PostgreSQL-backed
FastAPI service, and a static dashboard.

## Read this first: verification status

> **No installation, dataset certification, HDFS, Spark, ML, PostgreSQL or API
> runtime has been executed by this repository.** Every number in this project is
> either a design constant, a code-level contract, or the literal placeholder
> `PENDING CERTIFIED RUNTIME EVIDENCE`.
>
> - The dataset is owned by a separate workstream and is **not certified**; see
>   [dataset certification status](documentation/DATASET_CERTIFICATION_STATUS.md).
> - Big Data, ML, database and application code is **implemented in code** but
>   **unexecuted**: heavy dependencies (Pandas, NumPy, scikit-learn, PyArrow,
>   PySpark, FastAPI/httpx, psycopg) are not installed in this environment, so the
>   dependent tests report `skipped`. A skip is not a pass.
> - `requirements.txt` intentionally lists no runtime packages; nothing is
>   installed for you.
> - Documentation, checklists and templates in `documentation/` are **preparation,
>   not completion**. A mandatory artifact is not finished because its template
>   exists.

Start here: **[documentation/README.md](documentation/README.md)** — the index
that maps every mandatory SRS deliverable to a document and an honest status.

## Setup

Full installation instructions (14 mandatory items: Python, Java, Hadoop, HDFS
configuration, Spark, PySpark, virtual environment, database, dataset
generation, Spark execution, model execution, web application, tests,
troubleshooting) are in
**[documentation/INSTALLATION_AND_RUNTIME.md](documentation/INSTALLATION_AND_RUNTIME.md)**.

| Requirement | Version / note |
| --- | --- |
| CPython | ≥ 3.10 (the dataset generator and validator are standard-library only) |
| Java | JDK/JRE 8/11/17 with `JAVA_HOME` set (Spark/Hadoop prerequisite) |
| Hadoop + HDFS | `HADOOP_HOME`, `HADOOP_CONF_DIR`, `core-site.xml` with an HDFS-compatible `fs.defaultFS` |
| Apache Spark + PySpark | `SPARK_HOME`, `spark-submit` on `PATH`, PySpark matching the cluster runtime |
| Python data stack | NumPy, Pandas, scikit-learn, PyArrow |
| Serving stack | FastAPI, httpx (tests), `psycopg`/`psycopg2`, `psql` |
| Frontend | Node only for the static check/preview scripts; the app itself is dependency-free ES modules |

The single authoritative readiness probe is local-only and starts no workload:

```bash
python3 -m runtime_orchestration preflight --project-root .
```

It exits `0` only when every required check is `READY` and `2` otherwise. Its
per-check `detail` field is the only trustworthy statement about environment
readiness; see [runtime orchestration](documentation/RUNTIME_ORCHESTRATION.md).

## Execution workflow index

Each row is a **supported entry point**, not a record of a completed run. The
status column states what is actually true today. Follow the linked document for
arguments and verification steps.

| # | Workflow (DEL-11) | Command or surface | Reference | Status |
| ---: | --- | --- | --- | --- |
| 01 | Login | `POST /api/v1/auth/login` via the application UI | [security](backend/security/README.md) | `BLOCKED UNTIL RUNTIME` |
| 02 | Dataset generation | `python3 -m data_generator.preflight` then `python3 -m data_generator.generate --profile production --output <dir> --allow-production` | [Phase 1 generator notes](documentation/DATA_GENERATOR_PHASE1.md), [generation plan](documentation/DATA_GENERATION_PLAN.md) | `BLOCKED UNTIL CERTIFIED DATASET` (owned by the dataset workstream) |
| 03 | Data ingestion | `bash hdfs_scripts/ingest_certified_dataset.sh <package> <marker> <version> --dry-run` | [HDFS + Spark runbook](documentation/HDFS_SPARK_RUNBOOK.md) | `BLOCKED UNTIL RUNTIME` |
| 04 | HDFS storage | `bash hdfs_scripts/prepare_urbantransit_dirs.sh`, then publication to `$HDFS_ROOT/raw/<dataset-version>` | [HDFS + Spark runbook](documentation/HDFS_SPARK_RUNBOOK.md) | `BLOCKED UNTIL RUNTIME` |
| 05 | Spark processing | `python3 -m spark_jobs.pipeline --certified-root … --hdfs-root … --dataset-version … --severity-thresholds-sec …` | [HDFS + Spark runbook](documentation/HDFS_SPARK_RUNBOOK.md) | `BLOCKED UNTIL RUNTIME` |
| 06 | Spark SQL queries | `spark_sql/integration.sql` executed inside the Spark integration stage | [HDFS + Spark runbook](documentation/HDFS_SPARK_RUNBOOK.md) | `BLOCKED UNTIL RUNTIME`; SQL contract covered by `tests/test_spark_sql_contracts.py` |
| 07 | Passenger-flow analysis | `GET /api/v1/analytics/passenger-flow` | [capability map](documentation/ANALYTICS_CAPABILITY_MAP.md) | `IMPLEMENTED IN CODE`; values `PENDING CERTIFIED RUNTIME EVIDENCE` |
| 08 | Route analysis | `GET /api/v1/analytics/routes-stops` | [capability map](documentation/ANALYTICS_CAPABILITY_MAP.md) | `IMPLEMENTED IN CODE`; values `PENDING CERTIFIED RUNTIME EVIDENCE` |
| 09 | Delay analysis | `GET /api/v1/analytics/delay-analysis` | [capability map](documentation/ANALYTICS_CAPABILITY_MAP.md) | `IMPLEMENTED IN CODE`; values `PENDING CERTIFIED RUNTIME EVIDENCE` |
| 10 | Forecasting | `GET /api/v1/analytics/demand-forecast` | [ML execution and comparison](documentation/ML_EXECUTION_AND_COMPARISON.md) | `IMPLEMENTED IN CODE`; metrics `PENDING CERTIFIED RUNTIME EVIDENCE` |
| 11 | Occupancy prediction | `GET /api/v1/analytics/occupancy-crowding` | [feature contract](documentation/FEATURE_ENGINEERING_CONTRACT.md) | `IMPLEMENTED IN CODE`; metrics `PENDING CERTIFIED RUNTIME EVIDENCE` |
| 12 | Route clustering | `GET /api/v1/analytics/route-clusters` | [ML execution and comparison](documentation/ML_EXECUTION_AND_COMPARISON.md) | `IMPLEMENTED IN CODE`; cluster evidence `PENDING CERTIFIED RUNTIME EVIDENCE` |
| 13 | Spark model execution | `python3 -m ml_execution --engine spark --manifest <spark-manifest> --output <new-run-dir>` | [ML execution and comparison](documentation/ML_EXECUTION_AND_COMPARISON.md) | `BLOCKED UNTIL RUNTIME` |
| 14 | Python model execution | `python3 -m ml_execution --engine python --manifest <python-manifest> --output <new-run-dir>` | [ML execution and comparison](documentation/ML_EXECUTION_AND_COMPARISON.md) | `BLOCKED UNTIL RUNTIME` |
| 15 | Model comparison | `python3 -m runtime_orchestration compare --python-result … --spark-result … --dataset-version … --output-root …` (≥100 shared unseen cases) | [ML execution and comparison](documentation/ML_EXECUTION_AND_COMPARISON.md) | `BLOCKED UNTIL RUNTIME` |
| 16 | Recommendation generation | `python3 -m recommendation_engine recommendations --manifest … --request … --output …`; `GET /api/v1/analytics/recommendations` | [capability map](documentation/ANALYTICS_CAPABILITY_MAP.md) | `IMPLEMENTED IN CODE`; output `PENDING CERTIFIED RUNTIME EVIDENCE` |
| 17 | Dashboard usage | `cd frontend && npm run preview`, then `GET /api/v1/dashboard`; the UI can switch from `DEMO PREVIEW` to `API CONTRACT` mode | [frontend README](frontend/README.md) | `IMPLEMENTED IN CODE`; rendered evidence `PENDING CERTIFIED RUNTIME EVIDENCE` |
| 18 | Report export | `GET /api/v1/reports/{report_type}/download?format=csv\|json` | [reporting and export contract](documentation/REPORTING_EXPORT_CONTRACT.md) | `IMPLEMENTED IN CODE`; exported files `PENDING CERTIFIED RUNTIME EVIDENCE` |

What-if analysis (part of the recommendation surface) runs through
`python3 -m recommendation_engine what-if --manifest … --request … --output …`
and `POST /api/v1/analytics/what-if`; its output is labelled `ESTIMATE` and is
compared against an unchanged baseline.

### Serving and application launch

For a local academic demo without login, set `UTIQ_DEMO_MODE=true` in the API
process environment before starting the FastAPI application. The frontend reads
`/api/config` and opens directly on the Executive Dashboard; backend auth bypass
is limited to loopback clients. Login/database readiness and dataset/API
readiness remain real and unchanged. Unset the variable or set it to `false` to
restore the normal PostgreSQL-backed sign-in flow. See
[security configuration](backend/security/README.md#configuration-and-failure-behavior).

```bash
python3 -m backend.serving migrate      # applies pending local SQL migrations once
python3 -m backend.serving readiness    # reports only measured state
python3 -m backend.serving serve --artifact-root <dir>   # the one supported launch path
```

A missing driver, database, or artifact directory produces `NOT_CONFIGURED` /
`NOT_READY` — never a default value. See
[serving boundary](backend/serving/README.md) and
[API composition contract](documentation/API_COMPOSITION_CONTRACT.md).

## Testing

```bash
python3 -m unittest discover -s tests -v
cd frontend && npm run check
```

The suite is standard-library only and starts no server, cluster or database.
Tests requiring Pandas, scikit-learn, PySpark, FastAPI/httpx or psycopg report
`skipped` in this environment; **a skip is not a pass** and must not be reported
as runtime evidence. Category coverage against the 18 mandatory test categories,
known gaps, and the result-recording template are in
[documentation/TESTING.md](documentation/TESTING.md).

## Dataset generation (dataset-owner phase)

The default command creates only the isolated smoke package:

```bash
python3 -m data_generator.generate \
  --profile smoke \
  --output sample_data/smoke \
  --force \
  --timestamp 2026-09-24T00:00:00Z
```

Validate the generated package:

```bash
python3 -m data_generator.validate sample_data/smoke
```

The generator refuses to overwrite an unmarked output directory. The
`--allow-production` flag is a later safety opt-in and is intentionally not used
for the smoke run. Do not use smoke output as evidence of SRS production minima
or of HDFS/Spark/model completion; the smoke package is a
generator/validation fixture, not production-readiness or independent-cleaning
evidence. See [Phase 1 generator notes](documentation/DATA_GENERATOR_PHASE1.md).

## Repository layout

| Path | Contents |
| --- | --- |
| `data_generator/` | Deterministic dataset generator, preflight, validators (separate ownership) |
| `runtime_orchestration/` | Preflight, 18-stage plan, publication, comparison, allowlisted runner |
| `hdfs_scripts/` | HDFS directory contract and certified-package publication |
| `spark_jobs/`, `spark_sql/` | Spark ingestion, DQ, features, analytics, MLlib entry points, SQL integration |
| `python_pipeline/` | Independent Pandas/Python pipeline |
| `ml_execution/`, `feature_contracts.py` | Dual-engine ML execution and the shared feature contract catalog |
| `recommendation_engine/` | Recommendations and what-if scenarios |
| `backend/` | API application, artifact repository, reporting, security, PostgreSQL serving |
| `frontend/` | Static ES-module dashboard |
| `evidence_framework/`, `acceptance_harness/` | Evidence vocabulary/thresholds and read-only SRS acceptance reporting |
| `tests/` | Standard-library test suite |
| `documentation/` | All project documentation, indexed in [documentation/README.md](documentation/README.md) |
| `database/`, `models/`, `sample_data/`, `screenshots/`, `reports/`, `raw_data/`, `processed_data/`, `parquet_data/` | Dataset and artifact locations; several currently hold placeholders only and are filled from certified runtime output |

## Documentation

| Document | Purpose |
| --- | --- |
| [Documentation index and SRS traceability map](documentation/README.md) | Deliverable → document → honest status |
| [Installation and runtime](documentation/INSTALLATION_AND_RUNTIME.md) | Mandatory installation items and runtime commands |
| [Architecture](documentation/ARCHITECTURE.md) | Application/Big Data architecture and the four mandatory diagram sources |
| [Runtime orchestration](documentation/RUNTIME_ORCHESTRATION.md) | Preflight contract and execution plan |
| [HDFS + Spark runbook](documentation/HDFS_SPARK_RUNBOOK.md) | Publication semantics and Spark output safety |
| [ML execution and comparison](documentation/ML_EXECUTION_AND_COMPARISON.md) | Dual-engine execution and the ≥100-case comparison |
| [Analytics capability map](documentation/ANALYTICS_CAPABILITY_MAP.md) | Capability → code → endpoint → SRS mapping |
| [Security and privacy](documentation/SECURITY_AND_PRIVACY.md) | AuthN/AuthZ, transport security, privacy decisions |
| [Testing](documentation/TESTING.md) | Test inventory, DEL-09 coverage, gaps |
| [Evidence directory and manifest](documentation/EVIDENCE_MANIFEST.md) | Evidence layout, bundle schema, claim rules |
| [Dataset certification status](documentation/DATASET_CERTIFICATION_STATUS.md) | DEL-03 package requirements and the certification gate |
| [Project report template](documentation/PROJECT_REPORT_TEMPLATE.md) | The 41 mandatory report sections and their sources |
| [Demo video checklist](documentation/DEMO_VIDEO_CHECKLIST.md) | 24 mandatory workflows and the timestamp table |
| [Blog preparation](documentation/BLOG_PREPARATION.md) | ≥2,000-word structure, word budget, publication gate |
| [Final submission checklist](documentation/FINAL_SUBMISSION_CHECKLIST.md) | The 21 final artifacts and integrity gates |
| [GitHub readiness checklist](documentation/GITHUB_READINESS_CHECKLIST.md) | Public repository, history, hygiene, link readiness |
| [Dataset architecture](documentation/DATASET_ARCHITECTURE.md) / [Data dictionary](documentation/DATA_DICTIONARY.md) / [Data generation plan](documentation/DATA_GENERATION_PLAN.md) | Dataset design, fields, generation methodology |
| [AI usage declaration](AI_USAGE.md) | Mandatory AI tool declaration |
| [Development log](DEVELOPMENT_LOG.md) | Chronological work record |
| [Official SRS traceability checklist](MASTER_SRS_CHECKLIST.md) | Authoritative requirement inventory |

## Evidence honesty rules

1. A missing measurement stays `null`/`NOT_RUN`/`NOT_READY`; it never becomes
   zero, an empty success, or a plausible-looking number.
2. Every reported number must be traceable to a stored artifact from the run that
   produced it.
3. A skipped test, a dry run, a fixture, or a template is not evidence.
4. The frontend `DEMO PREVIEW` dataset is mock data and must never be presented
   as a result.
5. No external generative-AI API produces any prediction, classification,
   forecast or recommendation in this project.

## Known gaps

| Gap | Status |
| --- | --- |
| No certified dataset package | `BLOCKED UNTIL CERTIFIED DATASET` |
| No installed Java/Hadoop/HDFS/Spark/ML/database stack | `BLOCKED UNTIL RUNTIME` |
| No HDFS, Spark, ML, PostgreSQL or API runtime evidence | `PENDING CERTIFIED RUNTIME EVIDENCE` |
| No deployed application URL, screenshots, credentials, video, or blog | `PENDING CERTIFIED RUNTIME EVIDENCE` / `MANUAL TEAM ACTION` |
| Network map renders from a response, but no analytics geometry has been supplied (DEL-14-22) | `PENDING CERTIFIED RUNTIME EVIDENCE` |
| Anomaly and Spark SQL tests exist but are contract/unit level; their engine cases are `skipped` here (DEL-09-07/15) | `PENDING CERTIFIED RUNTIME EVIDENCE` |
| `models/`, `sample_data/`, `screenshots/`, `database/` hold placeholders only | Filled after the certified run |

## License

`LICENSE` is currently a placeholder: no license has been selected or granted.
The project owners must select and review a license before final submission.
Third-party material remains subject to its applicable licenses and terms.

## AI assistance

AI-assisted development tools were used and are declared in
[AI_USAGE.md](AI_USAGE.md) with purpose, type of help, files affected,
modifications made, testing completed, and the verifying team member. No AI tool
generated or altered dataset rows, analytics values, model outputs, or evidence
artifacts.

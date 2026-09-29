# PostgreSQL serving persistence

This package adds a PostgreSQL application-serving adapter to the existing
backend. `PostgresServingRepository` follows the existing `get(capability,
filters, body)` artifact-repository contract and can be injected into the
current backend composition; it does not replace `ApiApplication`, FastAPI,
or the existing artifact reader. Until deployment configures and injects it,
the existing backend behavior is unchanged.

PostgreSQL is for compact serving projections and versioned result summaries,
not a copy of raw/analytical history. Big Data processing and Parquet/HDFS
remain the analytical source of truth. The migration creates:

- Dataset-scoped route, stop, vehicle, route-pattern, route-stop, schedule,
  and trip summaries required for application lookup.
- Feature-version and model-run metadata.
- `serving_results` for analytical summaries, forecasts, clusters,
  recommendations, what-if estimates, pipeline metadata, and result payloads.
- Report metadata referencing a serving result and an external artifact URI.

Result records retain dataset/feature/model/analytics versions, generating
time, source artifact URI and SHA-256, optional route/stop/vehicle/trip and
direction, and optional analysis window. Scenario request metadata and payload
are explicitly marked; what-if results require a scenario type. Model metrics
and all JSON payloads must be finite JSON objects. No ticket, GPS, passenger
journey, or stop-event fact table is created.

## Configuration and state

Set `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD` in the
runtime environment. `PGSSLMODE` defaults to `require`. Credentials are not
embedded, returned from readiness, or included in configuration reprs. The
connection driver is imported lazily (`psycopg`, then `psycopg2`); no optional
driver is installed by this change.

The database reports `NOT_CONFIGURED` when required values are absent,
`NOT_READY` when connection/driver/schema readiness fails, and `READY` only
when the expected serving table is present. Repository writes use one
database transaction and roll back on errors. Duplicate keys and FK failures
are surfaced; inserts do not use overwrite/upsert semantics. Reads exclude
`FIXTURE_TESTED` records and never substitute in-memory or fabricated results.

## Migration

After certified infrastructure provisions an empty PostgreSQL database and
deployment credentials, apply the versioned local SQL files explicitly:

```bash
python3 -m backend.serving migrate
python3 -m backend.serving readiness
```

`migrate` prints the applied migration names as JSON and exits 0 only when the
ledger advanced; `readiness` prints the measured state and exits 0 only for
`READY`. Both exit 2 when the environment is not configured, the driver is
absent, or a migration fails, and neither creates a database or writes an
applied-migration claim it did not measure. The same operations are available
in-process:

```python
from backend.serving.config import PostgresConfig
from backend.serving.database import PostgresDatabase
from backend.serving.migrate import apply_migrations

apply_migrations(PostgresDatabase(PostgresConfig.from_env()))
```

The runner keeps a migration ledger in `app_serving.schema_migrations`;
each unapplied migration is applied and recorded in a single transaction.
User identity is an opaque nullable `actor_subject`, not a fabricated users
table or FK. Auth/RBAC integration can bind this to its approved subject
contract in a later migration without coupling this persistence package to
the active authentication implementation.

The same deployment is also expected to provide the reviewed authentication
schema from `backend/security/postgres_schema.sql`. That file stays a
contract: this package does not execute it, and
`PostgresAuthRepository.readiness()` reports `NOT_READY` until `app_auth.users`,
`app_auth.sessions` and `app_auth.login_attempt_buckets` exist.

## Launching the ASGI application

There is one supported launch path for the existing FastAPI application. It
composes the same `backend.fastapi_app.create_app` factory with the PostgreSQL
serving repository and the persistent authentication repository, then hands the
composed application to the approved ASGI server:

```bash
python3 -m backend.serving serve \
  --artifact-root /abs/path/to/certified-results \
  --host 127.0.0.1 --port 8000
```

`serve` exits `2` and starts nothing when the PostgreSQL environment is
incomplete, when `UTIQ_AUTH_DATABASE_DSN` is unset and demo mode is disabled
(there is no in-memory production authentication), when the certified artifact directory is missing,
when the environment values are invalid, or when the ASGI server is not
installed. It never prints a credential, and it neither reloads nor composes a
second, divergent application. TLS termination and trusted reverse-proxy
configuration remain deployment responsibilities. See the
[security contracts](../security/README.md) and the
[API composition contract](../../documentation/API_COMPOSITION_CONTRACT.md).

Schema/contract and DB-API mock tests require no PostgreSQL service. A real
PostgreSQL server, driver, migration run, constraints, query plans, backup,
retention, deployment-secret configuration and a real ASGI launch remain
pending runtime evidence.

## Local audited ML evidence API

The finalized ML evaluation artifacts can be served without PostgreSQL, raw
data access, HDFS, Spark, or model loading. Install the API dependencies with
`python3 -m pip install -r backend/requirements-api.txt`, then start the
loopback-only evidence service from the repository root:

```bash
python3 -m backend.serving serve-evidence \
  --repository-root /home/manal/Desktop/UrbanTransit-IQ \
  --host 127.0.0.1 --port 8000
```

This is a read-only artifact-serving mode of the existing FastAPI composition.
It verifies the final closure summary, result hashes, run audit, and comparison
hash before returning results. Only the explicit `/api/v1/evidence/*` GET
routes are public; all existing application and authentication routes retain
their production fail-closed auth behavior. Local HTTP is allowed only for
these evidence routes on a loopback host, and CORS is limited to the frontend's
`localhost:4173` and `127.0.0.1:4173` origins. Do not bind this mode to a
non-loopback host.

Available routes are `/api/health`, `/api/v1/evidence/status`,
`/api/v1/evidence/tasks`, `/api/v1/evidence/tasks/{task_name}`, and
`/api/v1/evidence/comparison/delay`. The task route accepts an optional
`prediction_limit` from 0 to 50; any requested prediction sample is read from
the audited frozen prediction file and hash-checked. The browser uses the
summary metrics route by default, not the prediction sample. The status
response separately reports PostgreSQL readiness; a ready artifact response
does not claim that PostgreSQL is configured or ready.

Install `backend/requirements-test.txt` to run the focused FastAPI integration
tests. The dependency-free frontend remains served with `npm run preview`;
its API base URL can be overridden through the `urbantransit-api-base-url`
meta tag or `globalThis.__URBANTRANSIT_API_BASE_URL__`.

# Authentication, role access and security execution

**IMPLEMENTED:** password authentication, Analyst-only self-registration,
expiring server-side bearer sessions, current-user/logout services, route
permissions, FastAPI adapter, persistence contracts, and a PostgreSQL
`AuthRepository` adapter composed automatically by the FastAPI application.
**FIXTURE-TESTED:** service, password/token,
role/dispatcher, configuration, repository boundaries, and the persistent
adapter's parameterized SQL through a scripted DB-API store. **PENDING
RUNTIME/DEPLOYMENT EVIDENCE:** a real PostgreSQL server and driver run, the
reviewed `app_auth` migration, account provisioning, TLS/proxy setup and
deployed security validation.

No accounts, passwords or evaluator credentials are provisioned here. No database,
server, production dataset or model was run. No dependencies were installed.

## Fit with the existing backend

The repository has `ApiApplication`, `AnalyticsService`, an artifact repository and
an endpoint catalog. It did not contain authentication or a FastAPI server.
`backend.fastapi_app.create_app(...)` hosts those existing routes and delegates their
work to the same dispatcher. It does not introduce another application framework.

The dispatcher now requires a trusted `Principal`, authorizes the endpoint before
reading artifacts, and defaults to 401 when identity is missing. The FastAPI adapter
obtains that principal from the auth service on **every request**, then repeats the
same permission check. Internal callers must supply a freshly authenticated
principal; never construct one from request body/header role or user fields.

## Endpoints

| Method/path | Access and behavior |
| --- | --- |
| POST `/api/v1/auth/register` | Public signup; validates username/password, throttles by client, and always assigns the least-privilege Evaluator role |
| POST `/api/v1/auth/login` | JSON username/password; shared account/client rate limits; returns opaque bearer token and expiry |
| GET `/api/v1/auth/me` | Valid bearer session; current user ID, username, role, permissions and expiry only |
| POST `/api/v1/auth/logout` | Valid bearer session; revokes its server-side digest |
| GET `/api/v1/admin/security-status` | Administrator configuration permission; sanitized configuration state, no secrets |
| Existing `/api/v1/analytics/*` and `/api/v1/system/*` | Valid session plus the explicit capability permission below |

Login accepts only `username` and `password`; it has an 8-KiB streamed JSON-body
limit. Existing what-if POST bodies have a 64-KiB limit. Additional fields, malformed
JSON, wrong content type, oversized inputs, unknown routes and malformed bearer
headers are rejected. Credentials are never accepted in query strings or cookies.
The login UI supports account creation and signs up users in after successful
registration. There is no password-reset or refresh-token system.

### Role/permission matrix

| Permission | Administrator | Operator | Analyst | Evaluator |
| --- | --- | --- | --- | --- |
| View transport analytics, forecasts, clusters, recommendations and system/pipeline status | Yes | Yes | Yes | Yes |
| View data-quality and Python/Spark comparison diagnostics | Yes | No | Yes | Yes |
| Execute/request what-if scenarios | Yes | Yes | Yes | No |
| User-management operations (`users:manage`) | Yes | No | No | No |
| Protected configuration (`configuration:manage`) | Yes | No | No | No |

This is the minimal policy mapped to the existing endpoint catalog. Operators use
operational views and what-if; Analysts also inspect model/data diagnostics;
Evaluators inspect outputs and diagnostics without mutation/execution rights.
Administrator user-management/configuration permissions and dependency contracts
are prepared; **user-management/configuration mutation endpoints are not implemented
or implied to exist**. A future endpoint must explicitly use its corresponding guard.
No broad role wildcard grants permission to unknown API capabilities.

`make_dependencies(auth_service)` supplies `current_user` and
`require_permissions(...)` for FastAPI `Depends`. `required_permission(endpoint)`
is the explicit current route mapping; unknown capabilities/methods deny access.

## Password handling

Passwords use Python/OpenSSL `hashlib.scrypt`: N=131072, r=8, p=1, 16 random salt
bytes and a 32-byte derived key. The versioned encoded hash stores algorithm,
parameters, salt and derived key. Comparison uses `hmac.compare_digest`. This follows
[OWASP's scrypt guidance when Argon2id is unavailable](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).

Provisioning requires 12–1024 characters and at most 4096 UTF-8 bytes. Passwords are
not stripped, lowercased or truncated. Username normalization is explicitly lowercase
ASCII with a bounded syntax. Hash parameters are fixed and strict; malformed or
unsupported hashes are rejected. OpenSSL/KDF failures produce NOT_READY, never a
weaker hash fallback. Memory is about 128 MiB per derivation; login derivation is
limited to two concurrent operations per process. Measure deployment latency and
worker/resource budgets before exposing authentication.

Unknown usernames perform the same configured KDF against an ephemeral dummy hash.
Unknown user, wrong password and inactive account produce the same generic 401.
No dummy account or credential can authenticate. User/session dataclass
representations omit stored password hashes and token digests. No password field
exists in persistence contracts, and none is logged by this implementation.

## Session mechanism

Each successful login creates a fresh `secrets.token_urlsafe(32)` opaque bearer token
(256 random bits). Only its SHA-256 digest is stored. SHA-256 is appropriate here for
high-entropy token lookup; it is **not** used to hash passwords. Random server-side
session identifiers and expiry follow the approach described in
[OWASP session management guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

There is no self-issued JWT, configurable signing algorithm or embedded role claim.
Opaque sessions do not require a signing secret; secure shared persistence and
HTTPS are required. Raw tokens are returned once to the login caller and are not
stored server-side. Clients send `Authorization: Bearer <token>`.

Validation checks syntax, stored digest, issuance time, absolute expiry, configured
maximum lifetime, revocation, current user activity, current role and security
version. Expiry is exclusive: now >= expires_at is rejected. Logout revokes the
digest. Password/role changes and deactivation must atomically increment the user's
security_version in the production adapter; existing sessions then fail. Roles and
active status are also read fresh each time, never trusted from client claims.
There is no sliding extension or automatic refresh.

## Configuration and failure behavior

Production is the default. `SecurityConfig.from_env()` reads:

| Variable | Purpose/default |
| --- | --- |
| `UTIQ_AUTH_DATABASE_DSN` | Secret PostgreSQL connection URI; no default credentials |
| `UTIQ_DEMO_MODE` | Default `false`; `true` enables a loopback-only auth bypass for local demos |
| `UTIQ_SESSION_TTL_SECONDS` | Default 900; permitted 60–3600 |
| `UTIQ_LOGIN_WINDOW_SECONDS` | Default 300; permitted 60–3600 |
| `UTIQ_LOGIN_ACCOUNT_LIMIT` | Default 10 attempts/window; permitted 1–100 |
| `UTIQ_LOGIN_CLIENT_LIMIT` | Default 50 attempts/window; permitted 1–1000 |

DSNs are excluded from configuration representations and never returned in errors.
Malformed settings fail with field names only. Demo mode is not test mode: it
does not create users or sessions and does not make authentication/readiness
appear configured. It bypasses authorization only for loopback product/API
requests and should not be enabled on a publicly reachable deployment. Login,
registration, `/auth/me`, and logout continue to use the real repository. Test
mode requires explicit Python construction; the FastAPI host additionally
requires `allow_test_mode=True`.

A DSN alone does not create persistence. Missing DSN/repository produces 503
`NOT_CONFIGURED`; repository outages produce 503 `NOT_READY`. Missing/invalid/expired
credentials produce 401 with `WWW-Authenticate: Bearer`; insufficient permission
produces 403. Throttling returns 429; HTTP responses include Retry-After. Unexpected
HTTP errors are sanitized; validation responses do not echo Pydantic input values,
passwords, DSNs, filesystem paths or stack traces. Artifact failures are also sanitized.

The host rejects non-HTTPS requests in production, sends no-store/no-cache and
nosniff headers, and adds HSTS over HTTPS. No permissive CORS, cookie login, test
users or public API documentation endpoints are enabled. Deployment must configure
TLS and trusted reverse-proxy IPs so ASGI's request scheme/client address is reliable.
This implementation does not independently trust an arbitrary X-Forwarded-* header.
Do not log Authorization headers, login bodies, DSNs or returned tokens at the proxy.
Browser token storage and cross-origin/UI integration remain a later frontend task.

## Persistence boundary

`AuthRepository` defines exact operations for users, sessions, revocation and atomic
login-attempt counters. `postgres_repository.PostgresAuthRepository` is the production
adapter and satisfies that contract:

- Returns typed User/Session values from parameterized queries; roles map to the Role enum.
- Stores hashes and token digests only; enforces username uniqueness and foreign keys.
- Shares sessions/rate counters across all workers; counts known/unknown attempts alike.
- Atomically increments attempts and expires old buckets; never silently allows login
  when its limiter/database fails.
- Converts session epoch seconds to/from PostgreSQL timestamptz in UTC.
- Increments security_version on password/role/security changes, and supports revocation.
- Propagates infrastructure errors for safe NOT_READY handling.

Every statement binds its values as driver parameters; no value is interpolated into
SQL, and this module never hashes, compares, generates or logs a password or token. An
unknown stored role or malformed record raises instead of being downgraded to a default
identity, so it fails closed. Connection settings come only from
`UTIQ_AUTH_DATABASE_DSN`, translated into the existing `backend.serving` PostgreSQL
config and database boundary, so all workers share one durable store. There is no
in-memory, demo or fixture fallback in this adapter.

[postgres_schema.sql](postgres_schema.sql) is a PostgreSQL-compatible contract in an
isolated `app_auth` schema. It creates no users or seed credentials and has **not**
been executed. It is kept here to avoid altering other database work.
`PostgresAuthRepository.readiness()` measures `app_auth.users`, `app_auth.sessions` and
`app_auth.login_attempt_buckets` and reports `NOT_READY` until they exist; it never
reports a `READY` state it did not measure. A reviewed migration, database driver and
least-privilege database role remain pending. Schema review must also establish
backup/security, cleanup and transaction policies. No promise of production
persistence is made from these interfaces.

The only in-memory auth implementation is `tests/auth_fixtures.py`; it is explicitly
marked `is_fixture=True`, contains no default users and is rejected by production
AuthService construction. Test instances do not share users/sessions. Production
adapters must explicitly declare `is_fixture=False`; there is no fixture fallback.

## FastAPI composition and launch

`create_app` composes the persistent adapter from configuration. An explicitly
injected `auth_repository` always wins, so test and approved alternative adapters
keep working; otherwise the PostgreSQL adapter is built when a DSN is configured:

```python
from backend.fastapi_app import create_app
from backend.repository import ArtifactRepository
from backend.security.config import SecurityConfig

config = SecurityConfig.from_env()
# No auth_repository argument: the PostgreSQL AuthRepository is composed from
# UTIQ_AUTH_DATABASE_DSN. The composed adapter is exposed as
# app.state.auth_repository for readiness reporting.
app = create_app(ArtifactRepository(certified_result_root),
                 security_config=config)
```

With no DSN the factory still composes and login returns 503 `NOT_CONFIGURED`; a
DSN that cannot be used fails composition explicitly rather than starting a server
with broken authentication. The supported deployment launch path is
`python3 -m backend.serving serve --artifact-root <dir>`; see
[the serving README](../serving/README.md).

The snippet requires actually configured variables; it is not a running server or
seeded deployment. Without FastAPI, the factory raises an explicit
NOT_CONFIGURED dependency error. Core service tests need only the standard library
and Python/OpenSSL scrypt support. FastAPI/Starlette, an ASGI server, PostgreSQL driver
and database adapter are runtime dependencies still to supply and validate. HTTP
fixture tests additionally require httpx. No packages were installed here.

## Verification

```bash
python3 -m unittest discover -s tests -p 'test_backend*.py' -v
python3 -m compileall -q backend tests/auth_fixtures.py tests/test_backend_security.py tests/test_backend_api_foundation.py
```

Core fixture tests cover hashes/verification, no plaintext persistence, login,
unknown/wrong/inactive credentials, token randomness/tampering/expiry/revocation,
role changes, every role/permission, route enforcement before artifact reads,
missing authentication/configuration, repository failure, rate limits, safe errors
and fixture isolation. Existing API foundation tests now pass explicit trusted test
principals. FastAPI HTTP tests are prepared but skipped when dependencies are absent.

Remaining evidence: a reviewed schema migration actually applied to a real
PostgreSQL server with a real driver; secure account provisioning outside source
control (including evaluator access); HTTP integration execution;
TLS/trusted-proxy and shared rate-limit behavior; session revocation across
workers; deployed authorization/error/logging validation. This package does not
claim deployed security or regulatory compliance.

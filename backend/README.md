# UrbanTransit IQ backend/API foundation

This package defines the backend integration boundary without starting a
server, reading raw datasets, or running analytics. The FastAPI adapter delegates
requests to `ApiApplication.handle(method, path, query=..., body=..., principal=...)`
after authenticating the caller.

`ArtifactRepository` reads only JSON result artifacts from an explicitly
configured directory. Artifacts are named after their capability (for
example, `passengerDemand.json` and `pipelineComparison.json`). Missing
artifacts return HTTP 200 with `status: "not_ready"` and a warning; they never
become fabricated zeroes or production claims.

Responses preserve the frontend's existing view-model fields and add:

```json
{
  "status": "ready | not_ready",
  "meta": {
    "state": "ready | not_ready",
    "source": "artifact",
    "filters": {}
  }
}
```

The route catalog is in `contracts.py`. Filters are validated centrally and
forwarded to the repository adapter, so a later certified-results adapter can
apply date range, route, stop, direction, vehicle, and period constraints at
the correct artifact grain.

## Authentication and FastAPI host

Existing dispatcher routes now require an authenticated `Principal` and an explicit
capability permission before reading artifacts. Production identity must come from
`AuthService.authenticate`, not request-supplied user/role fields.

`backend.fastapi_app.create_app` provides the FastAPI adapter, login/current-user/
logout endpoints and shared permission dependencies. Missing persistence/configuration
fails closed unless the explicitly enabled local demo mode bypasses product-route
authentication. No demo user or session is created. Set `UTIQ_DEMO_MODE=true` to
enable the loopback-only bypass; the PostgreSQL serving repository, artifact
availability, and readiness checks are unchanged. In normal mode, missing
`UTIQ_AUTH_DATABASE_DSN` returns `NOT_CONFIGURED`; with it configured, the
factory composes `PostgresAuthRepository` from the PostgreSQL serving boundary.
An explicitly injected repository still wins, so fixture and approved alternative
adapters keep working. `python3 -m backend.serving serve --artifact-root <dir>`
is the supported application launch path. See
[security contracts and runtime requirements](security/README.md) and the
[serving boundary](serving/README.md). A real PostgreSQL driver/server run, the
reviewed `app_auth` migration, and a real ASGI launch remain pending runtime
evidence; nothing here was executed against a live service.

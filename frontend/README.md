# UrbanTransit IQ dashboard foundation

This directory contains the frontend-only foundation for UrbanTransit IQ. The repository did not contain an existing web application or frontend dependency configuration, so this implementation uses browser-native ES modules, semantic HTML, and CSS with no package installation required.

## Run a local preview

The app is static and can be served from this directory:

```bash
cd frontend
npm run preview
```

The preview command is intentionally not started by the implementation agent. Do not use it for production analytics or expose a development server publicly.

## Authentication demo mode

For a local academic/demo run, set `UTIQ_DEMO_MODE=true` in the environment of
the FastAPI process before starting it. The frontend reads the mode from
`GET /api/config`, opens on the Executive Dashboard, and allows the existing
product routes without a login. Backend data routes bypass authentication only
for loopback clients; no user or session is created. Login and `/auth/me` remain
backed by the configured PostgreSQL auth repository and will continue to report
`NOT_CONFIGURED` if it is absent. API/data readiness is not changed by demo mode.

From the repository root, start the local audited-evidence API in one terminal:

```bash
UTIQ_DEMO_MODE=true python3 -m backend.serving serve-evidence \
  --repository-root . --host 127.0.0.1 --port 8000
```

In a second terminal, start the frontend with `cd frontend && npm run preview`.
The dashboard will use the normal API base URL at `127.0.0.1:8000`. Analytics
views still require their certified serving artifacts; missing artifacts remain
explicitly `not_ready`, while available audited evidence endpoints continue to
serve their verified results.

Unset the variable or set `UTIQ_DEMO_MODE=false` to restore the normal
PostgreSQL-backed sign-in flow. Keep demo mode confined to local demonstrations;
it is not an authentication configuration for a publicly reachable deployment.

## Integration contract

- `src/api/contracts.js` contains proposed frontend capability contracts. Every path is marked `contract-only`; no endpoint is represented as implemented.
- `src/api/view-models.js` documents the expected response shapes, nullable measures, source context, and lineage fields without coercing missing values to zero.
- `src/api/filters.js` provides a shared, serializable filter context for future page requests.
- `src/api/map-adapter.js` defines an optional provider boundary for route geometry, stops, layers, and viewport state without loading live map data. It validates coordinates, projects them into an SVG viewBox, and bridges the `routesAndStops` contract. It contains no map-provider dependency.
- `src/api/client.js` accepts an injected `baseUrl`/`fetcher` and is inert until an adapter is configured. A future host can provide `globalThis.__URBANTRANSIT_API_BASE_URL__` or construct the client directly.
- `src/data/demo-data.js` is the only source of synthetic preview values. It is explicitly marked `DEMO / MOCK` and is kept separate from API adapters.
- `src/data/page-data.js` is the page-data boundary; normalized API view models can be injected without editing page components. In API mode a demo collection (KPI list, table rows, chart series) is rendered only when the response supplied that same field, so a partial response can never promote demo rows into an API-sourced view.
- The UI can be switched from `DEMO PREVIEW` to `API CONTRACT` to review empty/loading integration states without presenting unverified values.

## Network map

The map renders a real, data-driven network as soon as the analytics service
returns geometry. It requires no external map provider, tile service or
geocoder: verified coordinates are projected into an inline SVG viewBox whose
bounds come only from the supplied records.

Data path: `routesAndStops` response → `mapGeometry` view-model field →
`normalizeNetworkGeometry` (validation) → `projectNetworkGeometry` (bounds →
viewBox) → `components/map.js` → `pages/routes-stops.js`. Selection state lives
in `app.js` and is cleared whenever the mode changes.

### Backend data contract expected by the frontend

Transported in the `routesAndStops` view model
(`GET /api/v1/analytics/routes-stops`) as the optional field `mapGeometry`. The
declared shape is `NETWORK_GEOMETRY_CONTRACT` in `src/api/contracts.js`.

| Field | Required | Meaning |
| --- | --- | --- |
| `available` | yes | Must be `true` for any geometry to be drawn. Omit it and the map stays `not_configured`. |
| `routes` | yes | Array of route geometry records. |
| `routes[].routeId` | yes | Stable non-empty route identifier. Also the interaction key. |
| `routes[].coordinates` | yes | Two or more points, GeoJSON order `[longitude, latitude]`. |
| `routes[].routeCode` | no | Human-readable route code. |
| `routes[].name` | no | Human-readable route name. |
| `routes[].mode` | no | Mode label, shown on selection. |
| `stops` | yes | Array of stop coordinate records. |
| `stops[].stopId` | yes | Stable non-empty stop identifier. Also the interaction key. |
| `stops[].latitude` | yes | Finite, -90 to 90. |
| `stops[].longitude` | yes | Finite, -180 to 180. |
| `stops[].name` | no | Human-readable stop name. |
| `asOf` | no | ISO-8601 timestamp the geometry is valid as of. Surfaced in the map footer. |
| `datasetVersion` | no | Dataset version the geometry came from. |
| `reason` | no | Supply when `available: false`; shown verbatim in the unavailable state. |

Minimum payload for the map to draw anything:

```json
{
  "mapGeometry": {
    "available": true,
    "asOf": "2026-04-01T00:00:00Z",
    "routes": [
      { "routeId": "<id>", "routeCode": "<code>", "name": "<name>",
        "coordinates": [[73.0, 33.0], [73.1, 33.1]] }
    ],
    "stops": [
      { "stopId": "<id>", "name": "<name>", "latitude": 33.0, "longitude": 73.0 }
    ]
  }
}
```

### Map states

Every non-drawable outcome is explicit and distinguishable via
`data-map-state` on the map root. None of them draws geometry, and none of them
borrows the demo fixtures.

| State | Trigger | Behaviour |
| --- | --- | --- |
| `ready` | `available: true` and at least one usable record | Routes and stops drawn from the response; coverage and rejected-record counts in the footer |
| `not_configured` | No response, no `mapGeometry`, or no adapter | Placeholder: awaiting geometry |
| `unavailable` | `available: false` | Placeholder showing the service `reason`; nothing drawn |
| `empty` | `available: true` but no record has usable coordinates | Placeholder: no drawable geometry |
| `loading` | Caller-supplied in-flight state | Placeholder: loading |
| `error` | Request or parse failure | Placeholder: geometry could not be read |

Rejection rules, so coverage is never overstated: a route with fewer than two
usable points is rejected; an out-of-range coordinate, a non-finite number, a
missing identifier, or a malformed pair is rejected. Rejected records are
counted and reported in a warnings disclosure, never repaired, interpolated, or
replaced with a default.

### Demo isolation

Demo geometry exists only in `renderDemoCanvas` in `src/components/map.js` and
is reachable only when `mode === 'demo'`. The verified renderer cannot reference
those fixtures, `src/pages/routes-stops.js` and `src/api/map-adapter.js` do not
import `demo-data.js`, and `resolveMap` short-circuits on `mode === 'demo'`
before it parses any response. `npm run check` asserts all of this.

Provider independence is preserved: `createMapAdapter` still accepts an optional
`provider`, and the SVG renderer works without one.

## Frontend checks

```bash
cd frontend
npm run check
```

The check is a lightweight Node syntax, module-import, page-render, and shell-mount smoke check; it does not run analytics, access datasets, or start a server. It also covers the network-map data flow: contract shape, coordinate rejection, all six map states, the adapter/client bridge, route and stop interaction attributes, selection, and demo isolation.

**Status: code-side only.** No analytics service response has been rendered.
Every map state other than `ready` is what the application currently shows, and
a rendered network map remains `PENDING CERTIFIED RUNTIME EVIDENCE`.

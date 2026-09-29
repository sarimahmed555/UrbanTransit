"""FastAPI adapter around the existing dispatcher; no server/database runs on import."""
import ipaddress
import json

from .app import ApiApplication, sanitize_public
from .contracts import ENDPOINTS
from .reporting import EXPORT_FORMATS, REPORT_CAPABILITIES
from .security.config import SecurityConfig
from .security.contracts import Permission, SecurityError
from .security.dependencies import make_dependencies
from .security.rbac import require_permission, required_permission
from .security.service import AuthService


_DIAGNOSTIC_REPORTS = frozenset({"dataQuality", "pipelineComparison"})
_REPORT_FILENAMES = {name: name.lower() for name in REPORT_CAPABILITIES}


def _public_report(value):
    return sanitize_public(value)


def persistent_auth_repository(config):
    """Compose the PostgreSQL AuthRepository when the environment configures one.

    Returns None when no authentication database is configured, which keeps the
    existing fail-closed `NOT_CONFIGURED` behavior. There is no in-memory,
    demo or fixture fallback in production: an injected repository (tests, an
    approved alternative adapter) always wins, and an unusable DSN fails
    loudly at composition time instead of starting a server with broken auth.
    """
    if config.database_dsn is None:
        return None
    from .security.postgres_repository import PostgresAuthRepository

    return PostgresAuthRepository.from_security_config(config)


def create_app(
    artifact_repository,
    *,
    serving_repository=None,
    auth_repository=None,
    security_config=None,
    allow_test_mode=False,
    evidence_repository=None,
    allow_local_evidence_http=False,
):
    try:
        from fastapi import FastAPI, Depends, Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse, Response
        from starlette.concurrency import run_in_threadpool
        from starlette.middleware.cors import CORSMiddleware
    except ImportError as exc:
        raise RuntimeError("FastAPI runtime is NOT_CONFIGURED; install approved FastAPI/ASGI dependencies during deployment") from exc
    config = security_config or SecurityConfig.from_env()
    if config.mode == "test" and not allow_test_mode:
        raise ValueError("Test authentication requires explicit test host opt-in")
    auth = AuthService(
        auth_repository if auth_repository is not None else persistent_auth_repository(config),
        config=config,
    )
    dispatcher = ApiApplication(
        artifact_repository, serving_repository=serving_repository
    )
    app = FastAPI(title="UrbanTransit IQ API", docs_url=None, redoc_url=None, openapi_url=None)
    if allow_local_evidence_http and evidence_repository is None:
        raise ValueError("Local HTTP is permitted only when audited evidence routes are configured")
    if evidence_repository is not None or config.web_origins or config.demo_mode:
        local_origins = ["http://localhost:4173", "http://127.0.0.1:4173"] if evidence_repository is not None or config.demo_mode else []
        if config.demo_mode:
            local_origins.extend(["http://localhost:4174", "http://127.0.0.1:4174"])
        app.add_middleware(
            CORSMiddleware,
            allow_origins=local_origins + list(config.web_origins),
            allow_methods=["GET", "POST"] if config.web_origins or config.demo_mode else ["GET"],
            allow_headers=["Accept", "Content-Type", "Authorization"] if config.web_origins or config.demo_mode else ["Accept", "Content-Type"],
            allow_credentials=False,
        )
    current_user, require_permissions, demo_user = make_dependencies(auth, demo_mode=config.demo_mode)
    app.state.auth_service = auth
    app.state.auth_repository = auth.repository

    @app.get("/api/config", name="publicConfiguration")
    def public_configuration():
        return {"demo_mode": config.demo_mode}

    @app.exception_handler(SecurityError)
    async def security_error(request: Request, exc: SecurityError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else {}
        if exc.status_code == 429:
            headers["Retry-After"] = str(config.login_window_seconds)
        return JSONResponse(exc.body(), status_code=exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc):
        # Never return Pydantic's input values: they can contain passwords/tokens.
        return JSONResponse({"status": "error", "error": {"code": "invalid_request", "message": "Invalid request"}}, status_code=422)

    @app.middleware("http")
    async def secure_transport(request: Request, call_next):
        client_host = request.client.host if request.client else ""
        try:
            client_is_loopback = ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            client_is_loopback = False
        local_evidence_request = (
            allow_local_evidence_http
            and evidence_repository is not None
            and client_is_loopback
            and (
                request.url.path == "/api/health"
                or request.url.path.startswith("/api/v1/evidence/")
            )
            and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        local_demo_request = (
            config.demo_mode
            and client_is_loopback
            and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if config.mode == "production" and request.url.scheme != "https" and not local_evidence_request and not local_demo_request:
            response = JSONResponse({"status": "error", "error": {"code": "https_required", "message": "HTTPS required"}}, status_code=400)
        else:
            try:
                response = await call_next(request)
            except Exception:
                # Do not expose connection strings, paths, headers, credentials or traces.
                response = JSONResponse({"status": "error", "error": {"code": "internal_error", "message": "Request could not be completed"}}, status_code=500)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    if evidence_repository is not None:
        def postgres_state():
            from .serving.config import PostgresConfig
            from .serving.database import PostgresDatabase

            return PostgresDatabase(PostgresConfig.from_env()).readiness()["state"]

        @app.get("/api/health", name="publicHealth")
        @app.get("/api/v1/evidence/status", name="evidenceStatus")
        async def evidence_status():
            state = await run_in_threadpool(postgres_state)
            return await run_in_threadpool(
                evidence_repository.status, postgres_state=state
            )

        @app.get("/api/v1/evidence/tasks", name="evidenceTasks")
        async def evidence_tasks():
            return await run_in_threadpool(evidence_repository.tasks)

        @app.get("/api/v1/evidence/tasks/{task_name}", name="taskEvidence")
        async def task_evidence(task_name: str, prediction_limit: int = 0):
            if not 0 <= prediction_limit <= 50:
                return JSONResponse(
                    {"status": "error", "error": {"code": "invalid_request", "message": "Invalid request"}},
                    status_code=400,
                )
            try:
                return await run_in_threadpool(
                    evidence_repository.task,
                    task_name,
                    prediction_limit=prediction_limit,
                )
            except KeyError:
                return JSONResponse(
                    {"status": "error", "error": {"code": "not_found", "message": "Evidence task not found"}},
                    status_code=404,
                )

        @app.get("/api/v1/evidence/comparison/delay", name="delayModelComparison")
        async def delay_comparison():
            return await run_in_threadpool(evidence_repository.comparison)

    if evidence_repository is not None:
        from .evidence_catalog import EvidenceCatalog
        from .evidence_exports import comparison_cases, report, serialize_report
        catalog = EvidenceCatalog(evidence_repository.root)

        @app.get("/api/v1/evidence/dataset")
        async def evidence_dataset():
            return await run_in_threadpool(catalog.summary)

        @app.get("/api/v1/evidence/catalog/{kind}")
        async def evidence_catalog(kind: str, query: str = "", route_id: str = "", limit: int = 100, offset: int = 0):
            try:
                return await run_in_threadpool(catalog.catalog, kind, query=query, route_id=route_id, limit=limit, offset=offset)
            except (ValueError, KeyError):
                return JSONResponse({"status":"error", "error":{"code":"invalid_request", "message":"Invalid catalog selection"}}, status_code=400)

        @app.get("/api/v1/evidence/comparison/delay/cases")
        async def evidence_comparison_cases():
            return await run_in_threadpool(comparison_cases, evidence_repository)

        @app.get("/api/v1/evidence/reports/{name}/download")
        async def evidence_report_download(name: str, format: str = "json"):
            try:
                payload = await run_in_threadpool(report, evidence_repository, name)
                body, media = serialize_report(payload, format)
            except KeyError:
                return JSONResponse({"status":"not_ready", "error":{"code":"not_found", "message":"Verified report unavailable"}}, status_code=404)
            except ValueError:
                return JSONResponse({"status":"error", "error":{"code":"invalid_request", "message":"Invalid report format"}}, status_code=400)
            return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}-production-v1.1.{format}"'})

    async def json_body(request, limit):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise SecurityError(415, "invalid_request", "A JSON request body is required")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > limit:
                raise SecurityError(413, "invalid_request", "Request body too large")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise SecurityError(400, "invalid_request", "Invalid request") from None
        if not isinstance(value, dict):
            raise SecurityError(400, "invalid_request", "Invalid request")
        return value

    @app.get("/api/v1/auth/status")
    def authentication_status():
        if auth.repository is None:
            return {"status":"ready", "data":{"state":"NOT_CONFIGURED", "transport":"HTTPS"}}
        readiness = getattr(auth.repository, "readiness", None)
        measured = readiness() if callable(readiness) else {"state":"NOT_READY"}
        return {"status":"ready", "data":{"state":measured.get("state", "NOT_READY"), "transport":"HTTPS"}}

    @app.post("/api/v1/auth/register", status_code=201)
    async def register(request: Request):
        payload = await json_body(request, 8192)
        if set(payload) != {"username", "password"}:
            raise SecurityError(400, "invalid_request", "Invalid request")
        client_id = request.client.host if request.client else None
        return await run_in_threadpool(
            auth.register, payload["username"], payload["password"], client_id=client_id
        )

    @app.post("/api/v1/auth/login")
    async def login(request: Request):
        payload = await json_body(request, 8192)
        if set(payload) != {"username", "password"}:
            raise SecurityError(400, "invalid_request", "Invalid request")
        client_id = request.client.host if request.client else None
        result = await run_in_threadpool(auth.login, payload["username"], payload["password"], client_id=client_id)
        return result

    @app.get("/api/v1/auth/me")
    def me(principal=Depends(current_user)):
        return principal.public()

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, principal=Depends(current_user)):
        return auth.logout(request.headers.get("authorization"))

    @app.get("/api/v1/admin/security-status")
    def security_status(principal=Depends(require_permissions(Permission.CONFIGURATION_MANAGE))):
        return auth.readiness()

    def register(endpoint):
        guard = require_permissions(required_permission(endpoint))

        async def dispatch(request: Request, principal=Depends(guard)):
            body = await json_body(request, 65536) if endpoint.body else None
            response = await run_in_threadpool(dispatcher.handle, endpoint.method, endpoint.path,
                                               query=dict(request.query_params), body=body, principal=principal,
                                               demo_mode=config.demo_mode)
            return JSONResponse(dict(response.body), status_code=response.status_code, headers=dict(response.headers))

        app.add_api_route(endpoint.path, dispatch, methods=[endpoint.method], name=endpoint.name)

    for endpoint in ENDPOINTS:
        register(endpoint)

    @app.get("/api/v1/system/readiness", name="systemReadiness")
    def system_readiness(principal=Depends(require_permissions(Permission.ANALYTICS_READ))):
        return dispatcher.service.execute("systemHealth")

    @app.get("/api/v1/system/dataset-status", name="datasetStatus")
    async def dataset_status(request: Request, principal=Depends(require_permissions(Permission.ANALYTICS_READ))):
        try:
            return await run_in_threadpool(
                dispatcher.dataset_status, query=dict(request.query_params)
            )
        except ValueError:
            return JSONResponse(
                {"status": "error", "error": {"code": "invalid_request", "message": "Invalid request"}},
                status_code=400,
            )
        except RuntimeError:
            return JSONResponse(
                {"status": "NOT_READY", "data": None, "meta": {"state": "NOT_READY"}},
                status_code=503,
            )

    @app.get("/api/v1/catalog/{kind}", name="servingCatalog")
    async def serving_catalog(
        kind: str,
        request: Request,
        principal=Depends(require_permissions(Permission.ANALYTICS_READ)),
    ):
        try:
            result = await run_in_threadpool(
                dispatcher.catalog, kind, query=dict(request.query_params)
            )
        except ValueError:
            return JSONResponse(
                {"status": "error", "error": {"code": "invalid_request", "message": "Invalid request"}},
                status_code=400,
            )
        except RuntimeError:
            return JSONResponse(
                {"status": "NOT_READY", "data": None, "meta": {"state": "NOT_READY"}},
                status_code=503,
            )
        return result

    @app.get("/api/v1/dashboard", name="dashboardData")
    async def dashboard_data(
        request: Request,
        principal=Depends(require_permissions(Permission.ANALYTICS_READ)),
    ):
        query = dict(request.query_params)
        sections = query.pop("sections", None)
        try:
            result = await run_in_threadpool(
                dispatcher.dashboard, query=query, sections=sections
            )
            return JSONResponse(_public_report(result))
        except ValueError:
            return JSONResponse(
                {"status": "error", "error": {"code": "invalid_request", "message": "Invalid request"}},
                status_code=400,
            )
        except RuntimeError:
            return JSONResponse(
                {"status": "NOT_READY", "data": None, "meta": {"state": "NOT_READY"}},
                status_code=503,
            )

    @app.get("/api/v1/models/{capability}/summary", name="modelResultSummary")
    async def model_result_summary(
        capability: str,
        request: Request,
        principal=Depends(demo_user),
    ):
        permission = (
            Permission.DIAGNOSTICS_READ
            if capability in _DIAGNOSTIC_REPORTS
            else Permission.ANALYTICS_READ
        )
        if not config.demo_mode:
            require_permission(principal, permission)
        try:
            result = await run_in_threadpool(
                dispatcher.model_result_summary,
                capability,
                query=dict(request.query_params),
            )
            return JSONResponse(_public_report(result))
        except ValueError:
            return JSONResponse(
                {"status": "error", "error": {"code": "invalid_request", "message": "Invalid request"}},
                status_code=400,
            )
        except RuntimeError:
            return JSONResponse(
                {"status": "NOT_READY", "data": None, "meta": {"state": "NOT_READY"}},
                status_code=503,
            )

    async def build_report(report_type, request, principal, *, download=False):
        if report_type not in REPORT_CAPABILITIES:
            return JSONResponse(
                {"status": "error", "error": {"code": "not_found", "message": "Report not found"}},
                status_code=404,
            )
        permission = (
            Permission.DIAGNOSTICS_READ
            if REPORT_CAPABILITIES[report_type] in _DIAGNOSTIC_REPORTS
            else Permission.ANALYTICS_READ
        )
        if not config.demo_mode:
            require_permission(principal, permission)
        query = dict(request.query_params)
        export_format = query.pop("format", "csv") if download else "json"
        try:
            report = await run_in_threadpool(dispatcher.report, report_type, query=query)
            report = _public_report(report)
            if not download:
                return JSONResponse(report)
            if export_format not in EXPORT_FORMATS:
                return JSONResponse(
                    {"status": "error", "error": {"code": "invalid_request", "message": "Invalid export format"}},
                    status_code=400,
                )
            content, media_type = await run_in_threadpool(
                dispatcher.reporting.export, report, export_format
            )
            filename = f"urbantransit-{_REPORT_FILENAMES[report_type]}.{export_format}"
            return Response(
                content=content,
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except ValueError:
            return JSONResponse(
                {"status": "error", "error": {"code": "invalid_request", "message": "Invalid request"}},
                status_code=400,
            )
        except RuntimeError:
            return JSONResponse(
                {"status": "NOT_READY", "data": None, "meta": {"state": "NOT_READY"}},
                status_code=503,
            )

    @app.get("/api/v1/reports/{report_type}", name="reportJson")
    async def report_json(
        report_type: str,
        request: Request,
        principal=Depends(demo_user),
    ):
        return await build_report(report_type, request, principal)

    @app.get("/api/v1/reports/{report_type}/download", name="reportDownload")
    async def report_download(
        report_type: str,
        request: Request,
        principal=Depends(demo_user),
    ):
        return await build_report(report_type, request, principal, download=True)

    registered_paths = [
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
        if method not in {"HEAD", "OPTIONS"}
    ]
    if len(registered_paths) != len(set(registered_paths)):
        raise ValueError("Duplicate API method/path registered")
    return app

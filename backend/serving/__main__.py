"""python -m backend.serving migrate|readiness|serve|serve-evidence.

This is a thin invocation surface for the existing `apply_migrations`,
`PostgresDatabase.readiness` and `backend.fastapi_app.create_app`
implementations. It never creates a database, never embeds credentials, never
reports readiness it did not measure, and only starts an ASGI server when an
explicit serve operation is requested.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import PostgresConfig
from .database import PostgresDatabase
from .migrate import apply_migrations


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))


def build_application(artifact_root):
    """Compose the existing FastAPI application from environment configuration.

    Serving and authentication both read the PostgreSQL environment; artifacts
    come from an explicitly supplied certified-results directory. No default
    users, credentials, or in-memory authentication are introduced.
    """
    from ..fastapi_app import create_app
    from ..repository import ArtifactRepository
    from ..security.config import SecurityConfig
    from .repository import PostgresServingRepository

    return create_app(
        ArtifactRepository(Path(artifact_root)),
        serving_repository=PostgresServingRepository(
            PostgresDatabase(PostgresConfig.from_env())
        ),
        security_config=SecurityConfig.from_env(),
    )


def build_evidence_application(repository_root):
    """Compose read-only routes for audited result artifacts without PostgreSQL."""
    from ..evidence import EvidenceRepository
    from ..fastapi_app import create_app
    from ..repository import ArtifactRepository

    root = Path(repository_root)
    return create_app(
        ArtifactRepository(root / "reports" / "api-serving-artifacts"),
        evidence_repository=EvidenceRepository(root),
        allow_local_evidence_http=True,
    )


def load_asgi_runner():
    """Return the supported ASGI server entry point, or fail explicitly."""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "ASGI server is NOT_CONFIGURED; install approved uvicorn during deployment"
        ) from exc
    return uvicorn.run


def _serve(artifact_root, host, port) -> int:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("--port must be an integer between 1 and 65535")
    if not isinstance(artifact_root, str) or not artifact_root.strip():
        raise ValueError("--artifact-root must be a directory path")
    root = Path(artifact_root)
    if not root.is_dir():
        # A launch with no certified artifacts would serve empty responses.
        _emit(
            {
                "stage": "asgi_application",
                "status": "NOT_CONFIGURED",
                "reason": "Certified result artifact directory does not exist; no server was started",
            }
        )
        return 2
    config = PostgresConfig.from_env()
    if not config.configured:
        _emit(
            {
                "stage": "asgi_application",
                "status": "NOT_CONFIGURED",
                "reason": "PostgreSQL environment is incomplete; no server was started",
            }
        )
        return 2
    from ..security.config import SecurityConfig

    security_config = SecurityConfig.from_env()
    if security_config.database_dsn is None and not security_config.demo_mode:
        _emit(
            {
                "stage": "asgi_application",
                "status": "NOT_CONFIGURED",
                "reason": "UTIQ_AUTH_DATABASE_DSN is unset and demo mode is disabled; no server was started",
            }
        )
        return 2
    try:
        application = build_application(artifact_root)
    except Exception as exc:
        # Controlled reason only: an exception message could carry a path, a
        # connection string, or a credential.
        _emit(
            {
                "stage": "asgi_application",
                "status": "NOT_CONFIGURED",
                "reason": "Application composition failed; no server was started",
                "error": type(exc).__name__,
            }
        )
        return 2
    try:
        run = load_asgi_runner()
    except RuntimeError as exc:
        _emit(
            {
                "stage": "asgi_server",
                "status": "NOT_CONFIGURED",
                "reason": str(exc),
            }
        )
        return 2
    # The application object is passed directly, so this process never
    # re-imports or re-composes a second, divergent application.
    run(application, host=host, port=port)
    return 0


def _serve_evidence(repository_root, host, port) -> int:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("--port must be an integer between 1 and 65535")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("serve-evidence may bind only to a loopback host")
    root = Path(repository_root)
    if not root.is_dir():
        _emit(
            {
                "stage": "evidence_application",
                "status": "NOT_CONFIGURED",
                "reason": "Repository root does not exist; no server was started",
            }
        )
        return 2
    try:
        application = build_evidence_application(root)
        run = load_asgi_runner()
    except Exception as exc:
        _emit(
            {
                "stage": "evidence_application",
                "status": "NOT_CONFIGURED",
                "reason": "Evidence application is unavailable; no server was started",
                "error": type(exc).__name__,
            }
        )
        return 2
    run(application, host=host, port=port)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("migrate", help="Apply pending local SQL migrations once")
    subparsers.add_parser("readiness", help="Report measured serving readiness")
    serve = subparsers.add_parser(
        "serve", help="Launch the FastAPI application with the supported ASGI server"
    )
    serve.add_argument(
        "--artifact-root",
        required=True,
        help="Directory holding certified <capability>.json result artifacts",
    )
    serve.add_argument(
        "--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)"
    )
    serve.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    evidence = subparsers.add_parser(
        "serve-evidence", help="Serve audited read-only ML evidence on loopback"
    )
    evidence.add_argument(
        "--repository-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="Repository containing final audited evidence (default: current project)",
    )
    evidence.add_argument(
        "--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)"
    )
    evidence.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    args = parser.parse_args(argv)
    if args.operation == "serve":
        try:
            return _serve(args.artifact_root, args.host, args.port)
        except ValueError as exc:
            _emit(
                {
                    "stage": f"asgi_application_{args.operation}",
                    "status": "NOT_CONFIGURED",
                    "reason": f"Invalid application environment: {exc}",
                }
            )
            return 2
    if args.operation == "serve-evidence":
        try:
            return _serve_evidence(args.repository_root, args.host, args.port)
        except ValueError as exc:
            _emit(
                {
                    "stage": "evidence_application",
                    "status": "NOT_CONFIGURED",
                    "reason": f"Invalid application environment: {exc}",
                }
            )
            return 2
    try:
        config = PostgresConfig.from_env()
    except ValueError as exc:
        _emit(
            {
                "stage": f"postgresql_{args.operation}",
                "status": "NOT_CONFIGURED",
                "reason": f"Invalid PostgreSQL environment: {exc}",
            }
        )
        return 2
    database = PostgresDatabase(config)
    if args.operation == "readiness":
        state = database.readiness()
        _emit({"stage": "postgresql_readiness", "status": state["state"], **state})
        return 0 if state["state"] == "READY" else 2
    try:
        applied = apply_migrations(database)
    except Exception as exc:
        # Already-applied migrations stay recorded in the ledger; report the
        # failure without claiming any migration succeeded in this invocation.
        _emit(
            {
                "stage": "postgresql_migration",
                "status": "NOT_READY" if not config.configured else "FAILED",
                "applied": [],
                "reason": f"{type(exc).__name__}: {exc}",
                "note": "Migrations committed before a failure remain applied and listed in app_serving.schema_migrations",
            }
        )
        print("PostgreSQL migration did not complete", file=sys.stderr)
        return 2
    _emit(
        {
            "stage": "postgresql_migration",
            "status": "SUCCEEDED",
            "applied": list(applied),
            "reason": None,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

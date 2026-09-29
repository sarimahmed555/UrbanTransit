"""Authorized API dispatcher used by the FastAPI host and trusted internal callers."""

import json
from pathlib import PurePosixPath
from typing import Any, Mapping

from .contracts import ROUTES
from .models import AnalyticsFilters, HttpResponse
from .reporting import ReportingService
from .services import AnalyticsService
from .security.contracts import SecurityError
from .security.rbac import required_permission, require_permission


_PRIVATE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "token_digest",
        "database_dsn",
        "dsn",
        "connection_string",
        "credentials",
        "secret",
    }
)
_PRIVATE_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "credential",
    "connection_string",
    "database_dsn",
    "api_key",
    "private_key",
    "authorization",
    "dsn",
)


def sanitize_public(value):
    if isinstance(value, Mapping):
        return {
            key: sanitize_public(item)
            for key, item in value.items()
            if str(key).lower() not in _PRIVATE_KEYS
            and not any(part in str(key).lower() for part in _PRIVATE_KEY_PARTS)
        }
    if isinstance(value, list):
        return [sanitize_public(item) for item in value]
    if isinstance(value, str):
        candidate = value.removeprefix("file://")
        if candidate.startswith("/"):
            return PurePosixPath(candidate).name
    return value


class ApiApplication:
    def __init__(self, repository, *, serving_repository=None):
        self.repository = repository
        self.serving_repository = serving_repository or repository
        self.service = AnalyticsService(
            repository, readiness_repository=self.serving_repository
        )
        self.reporting = ReportingService(repository)

    def handle(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        principal=None,
        demo_mode=False,
    ) -> HttpResponse:
        endpoint = ROUTES.get((method.upper(), path))
        if endpoint is None:
            return HttpResponse(404, {"status": "error", "error": {"code": "not_found", "message": "Route not found"}})
        try:
            if not demo_mode:
                require_permission(principal, required_permission(endpoint))
            filters = AnalyticsFilters.from_mapping(query)
            unsupported = set(filters.values) - endpoint.filters
            if unsupported:
                names = ", ".join(sorted(unsupported))
                raise ValueError(f"Filter(s) not supported by this route: {names}")
            if endpoint.body and body is None:
                raise ValueError("A JSON request body is required")
            if endpoint.capability == "whatIf":
                self._validate_what_if(body)
            result = self.service.execute(endpoint.capability, filters=filters, body=body)
            return HttpResponse(200, sanitize_public(result))
        except SecurityError as exc:
            return HttpResponse(exc.status_code, exc.body(), {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else {})
        except ValueError:
            return HttpResponse(400, {"status": "error", "error": {"code": "invalid_request", "message": "Invalid request"}})
        except RuntimeError as exc:
            state = "NOT_CONFIGURED" if "NOT_CONFIGURED" in str(exc) else "NOT_READY"
            return HttpResponse(
                503,
                {
                    "status": state,
                    "error": {
                        "code": state,
                        "message": "Requested application data source is unavailable.",
                    },
                },
            )

    def handle_json(self, method: str, path: str, *, query=None, body=None, principal=None) -> tuple[int, str]:
        response = self.handle(method, path, query=query, body=body, principal=principal)
        return response.status_code, json.dumps(response.body, sort_keys=True, allow_nan=False)

    def report(self, report_type, *, query=None):
        return sanitize_public(self.reporting.report(
            report_type,
            filters=AnalyticsFilters.from_mapping(query).values,
        ))

    def dashboard(self, *, query=None, sections=None):
        selected = None
        if sections is not None:
            selected = tuple(section for section in sections.split(",") if section)
        return sanitize_public(self.reporting.dashboard(
            filters=AnalyticsFilters.from_mapping(query).values,
            **({"sections": selected} if selected is not None else {}),
        ))

    def model_result_summary(self, capability, *, query=None):
        return sanitize_public(self.reporting.model_result_summary(
            capability,
            filters=AnalyticsFilters.from_mapping(query).values,
        ))

    def catalog(self, kind, *, query=None):
        if kind not in {"routes", "stops", "vehicles", "service"}:
            raise ValueError("Unknown catalog kind")
        filters = AnalyticsFilters.from_mapping(query).values
        method = getattr(self.serving_repository, "get_catalog", None)
        if not callable(method):
            return self._boundary_not_ready("catalog", "Serving catalog read interface is unavailable.", filters)
        try:
            result = method(kind, filters=filters)
        except Exception:
            return self._boundary_not_ready("catalog", "Serving catalog is not ready.", filters)
        return sanitize_public(
            self._validated_boundary_result(result, "catalog", filters, ("data", "results"))
        )

    def dataset_status(self, *, query=None):
        filters = AnalyticsFilters.from_mapping(query).values
        method = getattr(self.serving_repository, "dataset_status", None)
        if not callable(method):
            return self._boundary_not_ready(
                "dataset_status", "Dataset version read interface is unavailable.", filters
            )
        try:
            result = method(filters=filters)
        except Exception:
            return self._boundary_not_ready("dataset_status", "Dataset status is not ready.", filters)
        return sanitize_public(
            self._validated_boundary_result(result, "dataset_status", filters, ("data", "results"))
        )

    def _boundary_not_ready(self, capability, reason, filters):
        health = getattr(self.serving_repository, "health", None)
        state = "NOT_READY"
        if callable(health):
            try:
                readiness = health()
                if isinstance(readiness, Mapping) and readiness.get("state") in {
                    "NOT_CONFIGURED",
                    "NOT_READY",
                }:
                    state = readiness["state"]
            except Exception:
                state = "NOT_READY"
        return {
            "status": state,
            "data": None,
            "meta": {
                "state": state,
                "source": capability,
                "filters": dict(filters),
                "reason": reason,
                "warnings": [reason],
            },
        }

    @staticmethod
    def _validated_boundary_result(result, capability, filters, payload_fields):
        if not isinstance(result, Mapping):
            return ApiApplication._boundary_not_ready(
                capability, "Serving result is missing or invalid.", filters
            )
        status = result.get("status")
        task_contract = result.get("task_contract")
        task_certification = (
            task_contract.get("certification")
            if isinstance(task_contract, Mapping)
            else None
        )
        certified = result.get("certification_status") == "CERTIFIED" or (
            isinstance(task_certification, Mapping)
            and task_certification.get("status") == "CERTIFIED"
        )
        if status not in {"SUCCEEDED", "ready"} or not certified:
            return ApiApplication._boundary_not_ready(
                capability, "Certified serving result is unavailable.", filters
            )
        payload = next((result[key] for key in payload_fields if key in result), None)
        if (
            payload is None
            or not result.get("dataset_version")
            or not result.get("generated_at")
            or not any(result.get(key) for key in ("source_artifact", "provenance", "input_hashes"))
            or (filters and result.get("applied_filters") != dict(filters))
        ):
            return ApiApplication._boundary_not_ready(
                capability, "Serving result is incomplete or does not attest the requested filters.", filters
            )
        return {
            "status": "ready",
            "data": payload,
            "meta": {
                "state": "ready",
                "source": capability,
                "filters": dict(filters),
                "dataset_version": result["dataset_version"],
                "generated_at": result["generated_at"],
                "provenance": result.get("provenance"),
                "pipeline": result.get("pipeline", result.get("producer")),
            },
        }

    @staticmethod
    def _validate_what_if(body):
        if not isinstance(body, Mapping):
            raise ValueError("What-if body must be an object")
        required = {
            "schema_version",
            "baseline_evidence",
            "scenario_type",
            "entity_ids",
            "proposed_changes",
        }
        if set(body) != required or body.get("schema_version") != "1.0":
            raise ValueError("What-if body does not match the scenario request contract")
        baseline = body.get("baseline_evidence")
        if (
            not isinstance(baseline, Mapping)
            or set(baseline) != {"source_id", "pointer"}
            or not all(isinstance(baseline.get(key), str) for key in ("source_id", "pointer"))
            or not baseline["source_id"]
            or (
                baseline["pointer"] != ""
                and not baseline["pointer"].startswith("/")
            )
        ):
            raise ValueError("What-if baseline evidence reference is invalid")
        if not isinstance(body.get("entity_ids"), Mapping) or not isinstance(
            body.get("proposed_changes"), Mapping
        ):
            raise ValueError("What-if entity_ids and proposed_changes must be objects")
        from recommendation_engine.scenarios import SCENARIOS

        scenario_type = body.get("scenario_type")
        if not isinstance(scenario_type, str) or scenario_type not in SCENARIOS:
            raise ValueError("Unknown what-if scenario type")
        if set(body["proposed_changes"]) != SCENARIOS[scenario_type]:
            raise ValueError("What-if proposed_changes do not match the scenario type")
        expected_ids = {"route_id", "direction_id"}
        if scenario_type in {"shift_trip_start_time", "remove_low_demand_trip"}:
            expected_ids.add("trip_id")
        if scenario_type == "add_new_stop":
            expected_ids.add("stop_id")
        if set(body["entity_ids"]) != expected_ids:
            raise ValueError("What-if entity_ids do not match the scenario type")

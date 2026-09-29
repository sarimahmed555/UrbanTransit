"""Application services translating artifact results into frontend-safe responses."""

from datetime import datetime
from typing import Any, Mapping

from .contracts import ANALYTICS_FIELDS
from .models import AnalyticsFilters, response_meta


class AnalyticsService:
    def __init__(self, repository, *, readiness_repository=None):
        self.repository = repository
        self.readiness_repository = readiness_repository or repository

    def execute(
        self,
        capability: str,
        *,
        filters: AnalyticsFilters | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        filters = filters or AnalyticsFilters()
        if capability == "systemHealth":
            return self._health(filters)
        artifact = self.repository.get(capability, filters=filters.values, body=body)
        fields = ANALYTICS_FIELDS[capability]
        if not self._certified(artifact, fields, filters.values):
            reason = (
                "No certified result artifact is available."
                if artifact is None
                else "Result artifact is uncertified, incomplete, or does not attest the requested filters."
            )
            return self._not_ready(capability, fields, filters, reason)
        result = dict(artifact)
        result["status"] = "ready"
        meta = dict(result.get("meta", {})) if isinstance(result.get("meta"), Mapping) else {}
        meta.update(
            {
                "state": "ready",
                "source": meta.get("source", "certified_artifact"),
                "filters": dict(filters.values),
                "dataset_version": artifact["dataset_version"],
                "generated_at": artifact["generated_at"],
                "provenance": artifact.get("provenance"),
                "pipeline": artifact.get("pipeline", artifact.get("producer")),
                "analytics_version": artifact.get("analytics_version"),
                "feature_version": artifact.get("feature_version"),
                "model_version": artifact.get("model_version"),
                "model_run_id": artifact.get("model_run_id", artifact.get("run_id")),
            }
        )
        result["meta"] = meta
        return result

    def _health(self, filters: AnalyticsFilters) -> dict[str, Any]:
        health = getattr(self.readiness_repository, "health", None)
        if not callable(health):
            readiness = {"state": "NOT_CONFIGURED", "database": "postgresql"}
        else:
            try:
                readiness = health()
            except Exception:
                readiness = {"state": "NOT_READY", "database": "postgresql"}
        if not isinstance(readiness, Mapping) or readiness.get("state") not in {
            "READY",
            "NOT_READY",
            "NOT_CONFIGURED",
        }:
            readiness = {"state": "NOT_READY", "database": "postgresql"}
        ready = readiness["state"] == "READY"
        return {
            "status": "ready" if ready else readiness["state"],
            "health": {
                "state": readiness["state"],
                "database": readiness.get("database", "postgresql"),
            },
            "meta": response_meta(
                state="ready" if ready else readiness["state"],
                source="serving_repository",
                warnings=[] if ready else ["Serving database is not ready."],
                filters=filters.values,
            ),
        }

    @staticmethod
    def _certified(artifact, fields, filters):
        if not isinstance(artifact, Mapping):
            return False
        status = artifact.get("status")
        task_contract = artifact.get("task_contract")
        certification = artifact.get("certification_status")
        if isinstance(task_contract, Mapping):
            task_certification = task_contract.get("certification")
            if isinstance(task_certification, Mapping):
                certification = certification or task_certification.get("status")
        if status not in {"SUCCEEDED", "ready"} or certification != "CERTIFIED":
            return False
        if any(field not in artifact for field in fields):
            return False
        if not artifact.get("dataset_version"):
            return False
        if not any(
            artifact.get(key)
            for key in ("analytics_version", "feature_version")
        ):
            return False
        if not artifact.get("generated_at"):
            return False
        generated_at = artifact["generated_at"]
        if isinstance(generated_at, str):
            try:
                generated_at = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            except ValueError:
                return False
        if not isinstance(generated_at, datetime) or generated_at.tzinfo is None:
            return False
        if not any(
            artifact.get(key)
            for key in ("source_artifact", "artifact_paths", "input_hashes", "provenance")
        ):
            return False
        if filters and artifact.get("applied_filters") != dict(filters):
            return False
        return True

    @staticmethod
    def _not_ready(capability, fields, filters, reason):
        return {
            "status": "not_ready",
            "meta": {
                "state": "NOT_READY",
                "source": "artifact",
                "filters": dict(filters.values),
                "reason": reason,
                "warnings": [reason],
                "evidence_status": "PENDING CERTIFIED RUNTIME EVIDENCE",
            },
            **{field: None for field in fields},
        }

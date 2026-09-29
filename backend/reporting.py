"""Deterministic report and dashboard exports over certified result artifacts.

This module packages existing analytics results; it does not calculate metrics,
read transport datasets, or treat an available-but-uncertified artifact as ready.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import ANALYTICS_FIELDS
from .models import FILTER_NAMES


REPORT_CAPABILITIES = {
    "passengerDemand": "passengerDemand",
    "routePerformance": "routesAndStops",
    "delay": "delayAnalysis",
    "occupancy": "occupancy",
    "stopPerformance": "routesAndStops",
    "forecast": "demandForecast",
    "routeClustering": "routeClusters",
    "recommendations": "recommendations",
    "pipelineComparison": "pipelineComparison",
    "dataQuality": "dataQuality",
    "executiveSummary": "executiveSummary",
    "passengerFlow": "passengerFlow",
}

DASHBOARD_SECTIONS = tuple(REPORT_CAPABILITIES)
EXPORT_FORMATS = frozenset({"json", "csv"})
_LINEAGE_FIELDS = (
    "dataset_version",
    "pipeline",
    "producer",
    "analytics_version",
    "feature_version",
    "model_version",
    "model_name",
    "model_run_id",
    "run_id",
    "source_artifact",
    "source_sha256",
    "artifact_paths",
    "input_hashes",
    "generated_at",
    "provenance",
    "reproducibility",
    "task_contract",
    "applied_filters",
)
_MODEL_FIELDS = frozenset(
    {"demandForecast", "routeClusters", "delayAnalysis", "occupancy"}
)
_MODEL_SUMMARY_FIELDS = (
    "task_name",
    "task_contract",
    "model_name",
    "model_type",
    "model_version",
    "model_run_id",
    "run_id",
    "selected_model",
    "metrics",
    "baseline_metrics",
    "model_comparison",
    "baseline_vs_selected",
    "sample_counts",
    "periods",
)


def _json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _utc_timestamp(value: datetime | str | None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("generated_at must be a timezone-aware datetime or ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ReportingService:
    """Package API-contract results and expose JSON/CSV without inventing values.

    The injected repository must return the requested, certified artifact and
    attest to applied filters using ``applied_filters``. For fixture tests only,
    ``allow_fixtures=True`` accepts explicit ``FIXTURE_TESTED`` artifacts.
    """

    def __init__(
        self,
        repository,
        *,
        clock: Callable[[], datetime] | None = None,
        allow_fixtures: bool = False,
    ):
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.allow_fixtures = allow_fixtures

    def report(
        self,
        report_type: str,
        *,
        filters: Mapping[str, Any] | None = None,
        generated_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        if report_type == "modelResultSummary":
            raise ValueError("Use model_result_summary with a model-result capability")
        if report_type not in REPORT_CAPABILITIES:
            raise ValueError(f"Unsupported report type: {report_type}")
        normalized_filters = self._filters(filters)
        capability = REPORT_CAPABILITIES[report_type]
        return self._build(
            {report_type: capability},
            filters=normalized_filters,
            generated_at=generated_at,
            report_type=report_type,
        )

    def model_result_summary(
        self,
        capability: str,
        *,
        filters: Mapping[str, Any] | None = None,
        generated_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        if capability not in _MODEL_FIELDS:
            raise ValueError("Model summary capability must expose an existing model result")
        normalized_filters = self._filters(filters)
        return self._build(
            {"modelResultSummary": capability},
            filters=normalized_filters,
            generated_at=generated_at,
            report_type="modelResultSummary",
        )

    def dashboard(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        sections: tuple[str, ...] = DASHBOARD_SECTIONS,
        generated_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        if not sections or len(set(sections)) != len(sections):
            raise ValueError("Dashboard sections must be a nonempty unique sequence")
        unknown = set(sections) - REPORT_CAPABILITIES.keys()
        if unknown:
            raise ValueError(f"Unsupported dashboard section(s): {', '.join(sorted(unknown))}")
        normalized_filters = self._filters(filters)
        mapping = {section: REPORT_CAPABILITIES[section] for section in sections}
        return self._build(
            mapping,
            filters=normalized_filters,
            generated_at=generated_at,
            report_type="dashboard",
        )

    def export(self, report: Mapping[str, Any], format: str) -> tuple[str, str]:
        if format not in EXPORT_FORMATS:
            raise ValueError(f"Unsupported export format: {format}")
        if not isinstance(report, Mapping):
            raise ValueError("Report must be an object")
        serialized = _json(report)
        if format == "json":
            return serialized, "application/json; charset=utf-8"

        stream = io.StringIO(newline="")
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "report_type",
                "status",
                "section",
                "record_index",
                "value_json",
                "metadata_json",
            ),
            lineterminator="\n",
        )
        writer.writeheader()
        metadata = _json(report.get("meta", {}))
        data = report.get("data")
        if isinstance(data, Mapping) and data:
            for section in sorted(data):
                value = data[section]
                if isinstance(value, list):
                    for index, record in enumerate(value):
                        writer.writerow(
                            self._csv_row(report, section, str(index), record, metadata)
                        )
                    if not value:
                        writer.writerow(self._csv_row(report, section, "", [], metadata))
                else:
                    writer.writerow(self._csv_row(report, section, "", value, metadata))
        else:
            writer.writerow(self._csv_row(report, "", "", None, metadata))
        return stream.getvalue(), "text/csv; charset=utf-8"

    def write_export(
        self, report: Mapping[str, Any], path: str | Path, format: str
    ) -> None:
        """Create a new export only; existing files are never overwritten."""
        content, _ = self.export(report, format)
        target = Path(path)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
                output.write(content)
        except BaseException:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise

    def _build(
        self,
        requested: Mapping[str, str],
        *,
        filters: Mapping[str, Any],
        generated_at: datetime | str | None,
        report_type: str,
    ) -> dict[str, Any]:
        report_time = _utc_timestamp(generated_at if generated_at is not None else self.clock())
        sections: dict[str, Any] = {}
        source_metadata: dict[str, Any] = {}
        dataset_versions: set[str] = set()
        source_statuses: set[str] = set()
        pending: list[str] = []

        for section, capability in requested.items():
            artifact = self.repository.get(
                capability, filters=dict(filters), body=None
            )
            model_summary = section == "modelResultSummary"
            if not self._is_ready(artifact, filters, capability, model_summary):
                sections[section] = {"status": "not_ready", "data": None}
                pending.append(section)
                continue
            source_statuses.add(artifact["status"])
            dataset_versions.add(artifact["dataset_version"])
            sections[section] = {
                "status": "ready",
                "data": {
                    name: artifact[name]
                    for name in (
                        _MODEL_SUMMARY_FIELDS
                        if model_summary
                        else ANALYTICS_FIELDS[capability]
                    )
                    if name in artifact
                },
            }
            source_metadata[section] = {
                "status": artifact["status"],
                "certification_status": artifact.get("certification_status"),
                "task_certification_status": self._task_certification(artifact),
                **{
                    name: artifact[name]
                    for name in _LINEAGE_FIELDS
                    if name in artifact
                },
            }
            if section == "modelResultSummary":
                sections[section]["data"].update(
                    {
                        name: artifact[name]
                        for name in _MODEL_SUMMARY_FIELDS
                        if name in artifact
                    }
                )

        if len(dataset_versions) > 1:
            pending.extend(section for section in requested if section not in pending)
            sections = {
                section: {"status": "not_ready", "data": None}
                for section in requested
            }

        ready = not pending
        return {
            "schema_version": "1.0",
            "report_type": report_type,
            "status": "ready" if ready else "not_ready",
            "data": sections if report_type == "dashboard" else (
                next(iter(sections.values()))["data"] if ready else None
            ),
            "meta": {
                "state": "ready" if ready else "not_ready",
                "generated_at": report_time,
                "dataset_version": next(iter(dataset_versions)) if len(dataset_versions) == 1 else None,
                "filters": dict(filters),
                "sources": source_metadata,
                "warnings": [] if ready else [
                    "Certified analytics are unavailable or do not match the requested filters."
                ],
                "evidence_status": (
                    "FIXTURE-TESTED"
                    if ready and "FIXTURE_TESTED" in source_statuses
                    else "CERTIFIED_ANALYTICS"
                    if ready
                    else "PENDING CERTIFIED RUNTIME EVIDENCE"
                ),
            },
        }

    def _is_ready(
        self,
        artifact: Any,
        filters: Mapping[str, Any],
        capability: str,
        model_summary: bool = False,
    ) -> bool:
        if not isinstance(artifact, Mapping):
            return False
        status = artifact.get("status")
        certification = artifact.get("certification_status")
        task_certification = self._task_certification(artifact)
        if status == "SUCCEEDED":
            certified = certification == "CERTIFIED" or task_certification == "CERTIFIED"
        elif self.allow_fixtures and status == "FIXTURE_TESTED":
            certified = certification == "FIXTURE" or task_certification == "FIXTURE"
        else:
            return False
        if not certified:
            return False
        if not isinstance(artifact.get("dataset_version"), str) or not artifact["dataset_version"]:
            return False
        if not artifact.get("feature_version" if model_summary else "analytics_version"):
            return False
        if not model_summary and not self._has_timezone(artifact.get("generated_at")):
            return False
        if not any(
            artifact.get(key)
            for key in ("source_artifact", "artifact_paths", "input_hashes", "provenance")
        ):
            return False
        required_fields = (
            {"task_name", "metrics", "baseline_metrics", "model_comparison"}
            if model_summary
            else set(ANALYTICS_FIELDS[capability])
        )
        if not required_fields.issubset(artifact):
            return False
        if filters and artifact.get("applied_filters") != dict(filters):
            return False
        return True

    @staticmethod
    def _task_certification(artifact: Mapping[str, Any]) -> Any:
        task_contract = artifact.get("task_contract")
        if not isinstance(task_contract, Mapping):
            return None
        certification = task_contract.get("certification")
        return certification.get("status") if isinstance(certification, Mapping) else None

    @staticmethod
    def _has_timezone(value: Any) -> bool:
        if not isinstance(value, str):
            return isinstance(value, datetime) and value.tzinfo is not None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None and parsed.utcoffset() is not None

    @staticmethod
    def _filters(filters: Mapping[str, Any] | None) -> dict[str, Any]:
        filters = filters or {}
        if not isinstance(filters, Mapping):
            raise ValueError("Filters must be an object")
        unsupported = set(filters) - FILTER_NAMES
        if unsupported:
            raise ValueError(f"Unsupported filter(s): {', '.join(sorted(unsupported))}")
        selected = {key: value for key, value in filters.items() if value not in (None, "")}
        _json(selected)
        return selected

    @staticmethod
    def _csv_row(report, section, record_index, value, metadata):
        return {
            "report_type": report.get("report_type", ""),
            "status": report.get("status", ""),
            "section": section,
            "record_index": record_index,
            "value_json": "" if value is None else _json(value),
            "metadata_json": metadata,
        }

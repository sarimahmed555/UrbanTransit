"""Read-only API adapter for audited ML run artifacts.

This adapter reads bounded result JSON and prediction JSONL only. It never
opens production source data, starts analytics jobs, or loads fitted models.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Any


TASKS = (
    "delay_severity",
    "passenger_demand",
    "occupancy_forecast",
    "crowding_risk",
    "route_clustering",
)
SUMMARY_PATH = Path("reports/recovery/ml-closure-final-20260928-v2/summary.json")
AUDIT_PATH = Path("reports/recovery/artifact-audit-20260928T174705Z.json")
COMPARISON_PATH = Path(
    "reports/ml_comparisons/stage=spark_python_comparison/dataset=production-v1.1/"
    "run=python-delay-v11-normalized-20260928T2100Z/comparison.json"
)
MAX_PREDICTION_ARTIFACT_BYTES = 100 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class EvidenceRepository:
    """Serve verified, immutable result summaries and bounded saved predictions."""

    def __init__(self, repository_root: str | Path):
        self.root = Path(repository_root).resolve()
        self._lock = Lock()
        self._prediction_cache: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        self._result_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._summary = self._read_json(SUMMARY_PATH, max_bytes=1_000_000)
        self._audit = self._read_json(AUDIT_PATH, max_bytes=1_000_000)
        if self._summary.get("predictive_task_execution_complete") is not True:
            raise RuntimeError("Certified predictive task closure is unavailable")
        if self._summary.get("full_srs_ml_closure") is not False:
            raise RuntimeError("Unexpected full-SRS closure state")
        if self._summary.get("status") != "FIVE_TASK_EXECUTION_COMPLETE_WITH_SRS_GAPS":
            raise RuntimeError("Unexpected ML closure status")
        self._tasks = {
            task["task_name"]: task
            for task in self._summary.get("tasks", [])
            if task.get("task_name") in TASKS
        }
        self._audit_runs = {}
        for task_name, task in self._tasks.items():
            for engine in ("python", "spark"):
                evidence = task.get(engine)
                if not isinstance(evidence, dict):
                    continue
                audit = next(
                    (
                        run for run in self._audit.get("runs", [])
                        if run.get("task") == task_name
                        and run.get("result") == evidence.get("result_path")
                        and run.get("status") == "SUCCEEDED"
                    ),
                    None,
                )
                if audit:
                    self._audit_runs[(task_name, engine)] = audit
        self._comparison = None

    def _path(self, relative: str | Path) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise RuntimeError("Evidence path escapes the repository")
        return path

    def _read_json(self, relative: str | Path, *, max_bytes: int) -> dict[str, Any]:
        path = self._path(relative)
        if not path.is_file() or path.stat().st_size > max_bytes:
            raise RuntimeError("Required bounded evidence artifact is missing or oversized")
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Required evidence artifact could not be read") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Evidence artifact must contain a JSON object")
        return result

    def _verified_run(
        self, task_name: str, engine: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        key = (task_name, engine)
        task = self._tasks.get(task_name)
        if task is None or engine not in {"python", "spark"}:
            raise KeyError("Unknown evidence task or pipeline")
        evidence = task.get(engine)
        audit = self._audit_runs.get(key)
        if not isinstance(evidence, dict) or not isinstance(audit, dict):
            raise RuntimeError("Audited task result is unavailable")
        if any(
            audit.get(name) is not True
            for name in (
                "input_hashes_verified",
                "metrics_recomputed_from_predictions",
                "validation_only_model_selection_verified",
            )
        ) or audit.get("test_metrics_used_for_selection") is not False:
            raise RuntimeError("Task result does not satisfy the final audit contract")
        result_path = self._path(evidence["result_path"])
        if _sha256(result_path) != audit["artifact_sha256"]["result"][evidence["result_path"]]:
            raise RuntimeError("Audited task result hash mismatch")
        cache_key = key
        result = self._result_cache.get(cache_key)
        if result is None:
            result = self._read_json(evidence["result_path"], max_bytes=1_000_000)
            if (
                result.get("status") != "SUCCEEDED"
                or result.get("engine") != engine
                or result.get("task_name") != task_name
                or result.get("dataset_version") != "production-v1.1"
                or result.get("selected_model") != evidence.get("selected_model")
            ):
                raise RuntimeError("Task result metadata does not match its certified summary")
            self._result_cache[cache_key] = result
        return evidence, audit, result

    def status(self, *, postgres_state: str) -> dict[str, Any]:
        completed = [
            name for name in TASKS
            if all((name, engine) in self._audit_runs for engine in ("python", "spark"))
        ]
        return {
            "status": "ready",
            "data": {
                "dataset_version": "production-v1.1",
                "predictive_task_execution_complete": len(completed) == len(TASKS),
                "full_srs_ml_closure": False,
                "completed_predictive_tasks": completed,
                "partial_tasks": [
                    {
                        "task_name": item["task_name"],
                        "status": item["status"],
                        "reason": item["evidence_status"],
                    }
                    for item in self._summary.get("additional_tasks", [])
                ],
                "serving_database": {"state": postgres_state},
                "limitations": self._summary.get("limitations", []),
            },
            "meta": {
                "source": "audited_result_artifacts",
                "audit_artifact": str(AUDIT_PATH),
                "closure_artifact": str(SUMMARY_PATH),
                "generated_at": self._summary.get("created_at_utc"),
                "freshness": "frozen",
            },
        }

    def tasks(self) -> dict[str, Any]:
        rows = []
        for task_name in TASKS:
            task = self._tasks.get(task_name)
            if not task:
                continue
            pipelines = {}
            for engine in ("spark", "python"):
                evidence, audit, result = self._verified_run(task_name, engine)
                pipelines[engine] = {
                    "selected_model": evidence["selected_model"],
                    "model_type": result.get("model_type"),
                    "validation_metrics": result["metrics"].get("validation"),
                    "test_metrics": result["metrics"].get("test"),
                    "baseline_metrics": result.get("baseline", {}).get("test"),
                    "baseline_vs_selected": result.get("baseline_vs_selected", {}).get("test"),
                    "acceptance": result.get("acceptance"),
                    "sample_counts": result.get("sample_counts"),
                    "generated_at": result.get("reproducibility", {}).get("created_at_utc"),
                    "input_hashes_verified": audit["input_hashes_verified"],
                }
            rows.append(
                {
                    "task_name": task_name,
                    "status": task["status"],
                    "limitations": task.get("limitations", []),
                    "generated_at": max(
                        pipelines["python"]["generated_at"],
                        pipelines["spark"]["generated_at"],
                    ),
                    "pipelines": pipelines,
                }
            )
        return {
            "status": "ready",
            "data": rows,
            "meta": {
                "source": "audited_result_artifacts",
                "dataset_version": "production-v1.1",
                "freshness": "frozen",
            },
        }

    def task(self, task_name: str, *, prediction_limit: int = 0) -> dict[str, Any]:
        if task_name not in TASKS:
            raise KeyError("Unknown evidence task")
        if type(prediction_limit) is not int or not 0 <= prediction_limit <= 50:
            raise ValueError("prediction_limit must be between 0 and 50")
        task = self._tasks.get(task_name)
        if task is None:
            raise RuntimeError("Task closure evidence is unavailable")
        pipelines = {}
        for engine in ("spark", "python"):
            evidence, audit, run = self._verified_run(task_name, engine)
            artifact_paths = run["artifact_paths"]
            prediction_path = self._path(artifact_paths["predictions"])
            prediction_rel = prediction_path.relative_to(self.root).as_posix()
            audited_hash = audit["artifact_sha256"]["predictions"].get(
                prediction_rel
            )
            if not audited_hash:
                raise RuntimeError("Prediction artifact hash is absent from audit evidence")
            candidates = run.get("model_comparison", [])
            selected = run["selected_model"]
            selected_candidate = next(
                (item for item in candidates if item.get("model_name") == selected), None
            )
            payload = {
                "pipeline": engine,
                "feature_version": run.get("feature_version"),
                "model_run_id": self._path(evidence["result_path"]).parent.name,
                "result_sha256": audit["artifact_sha256"]["result"][evidence["result_path"]],
                "task_name": task_name,
                "selected_model": selected,
                "model_type": run.get("model_type"),
                "generated_at": run.get("reproducibility", {}).get("created_at_utc"),
                "selection_metric": run.get("selection_metric"),
                "selection_rationale": run.get("selection_rationale"),
                "validation_metrics": run["metrics"].get("validation"),
                "test_metrics": run["metrics"].get("test"),
                "baseline_metrics": run.get("baseline", {}).get("test"),
                "baseline_vs_selected": run.get("baseline_vs_selected", {}).get("test"),
                "acceptance": run.get("acceptance"),
                "sample_counts": run.get("sample_counts"),
                "periods": run.get("periods"),
                "candidate_models": [
                    {
                        "model_name": item.get("model_name"),
                        "status": item.get("status"),
                        "validation_metrics": item.get("validation_metrics"),
                    }
                    for item in candidates
                ],
                "artifact_status": {
                    "saved_model_exists": self._path(artifact_paths["model"]).exists(),
                    "predictions_exist": prediction_path.is_file(),
                    "prediction_sha256": audited_hash,
                    "model_path": self._relative_artifact(artifact_paths["model"]),
                    "prediction_path": prediction_rel,
                },
                "provenance": {
                    "manifest_path": evidence["manifest_path"],
                    "input_hashes": run.get("input_hashes"),
                    "audit_path": self._relative_artifact(self._summary["artifact_audit_path"]),
                    "reload_prediction_parity_cases_per_split": audit.get(
                        "reload_prediction_parity_cases_per_split"
                    ),
                    "owner_marker_hash_verified": audit.get("owner_marker_hash_verified"),
                },
                "limitations": task.get("limitations", []),
            }
            if selected_candidate:
                payload["selected_validation_metrics"] = selected_candidate.get("validation_metrics")
            if prediction_limit:
                payload["prediction_sample"] = self._prediction_sample(
                    task_name, engine, prediction_path, audited_hash, prediction_limit
                )
            pipelines[engine] = payload
        return {
            "status": "ready",
            "data": {
                "task_name": task_name,
                "status": task["status"],
                "dataset_version": "production-v1.1",
                "pipelines": pipelines,
                "limitations": task.get("limitations", []),
                "generated_at": max(
                    pipelines["python"]["generated_at"],
                    pipelines["spark"]["generated_at"],
                ),
            },
            "meta": {
                "source": "audited_result_artifacts",
                "dataset_version": "production-v1.1",
                "provenance": self._summary["artifact_audit_path"],
                "freshness": "frozen",
            },
        }

    def _prediction_sample(
        self,
        task_name: str,
        engine: str,
        path: Path,
        expected_sha256: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        key = (task_name, engine, limit)
        with self._lock:
            cached = self._prediction_cache.get(key)
            if cached is not None:
                return cached
            if path.stat().st_size > MAX_PREDICTION_ARTIFACT_BYTES:
                raise RuntimeError("Prediction artifact exceeds the bounded serving limit")
            digest = hashlib.sha256()
            sample = []
            try:
                with path.open("rb") as stream:
                    for line in stream:
                        digest.update(line)
                        if len(sample) < limit:
                            row = json.loads(line)
                            if row.get("split") == "test":
                                sample.append(
                                    {
                                        "case_id": row.get("case_id"),
                                        "target_start": row.get("target_start"),
                                        "target_end": row.get("target_end"),
                                        "actual": row.get("actual"),
                                        "prediction": row.get("prediction"),
                                        "crowding_probability": row.get("crowding_probability"),
                                    }
                                )
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Prediction artifact could not be read") from exc
            if digest.hexdigest() != expected_sha256:
                raise RuntimeError("Prediction artifact hash mismatch")
            if not sample:
                raise RuntimeError("Prediction artifact contains no held-out cases")
            self._prediction_cache[key] = sample
            return sample

    def _prediction_rows_by_comparison_id(
        self, task_name: str, engine: str, wanted: set[str]
    ) -> dict[str, dict[str, Any]]:
        _, audit, run = self._verified_run(task_name, engine)
        prediction_path = self._path(run["artifact_paths"]["predictions"])
        if prediction_path.stat().st_size > MAX_PREDICTION_ARTIFACT_BYTES:
            raise RuntimeError("Prediction artifact exceeds the bounded serving limit")
        relative_path = prediction_path.relative_to(self.root).as_posix()
        expected_hash = audit["artifact_sha256"]["predictions"].get(relative_path)
        if not expected_hash:
            raise RuntimeError("Prediction artifact hash is absent from audit evidence")
        selected = {}
        digest = hashlib.sha256()
        try:
            with prediction_path.open("rb") as stream:
                for line in stream:
                    digest.update(line)
                    row = json.loads(line)
                    if row.get("split") != "test":
                        continue
                    case_id = row.get("case_id")
                    if not isinstance(case_id, str) or not case_id:
                        raise ValueError
                    comparison_id = case_id
                    if engine == "spark":
                        separator = case_id.index(":")
                        trip_length = int(case_id[:separator])
                        remainder = case_id[separator + 1:]
                        if trip_length < 1 or len(remainder) <= trip_length:
                            raise ValueError
                        comparison_id = remainder[trip_length:]
                    if comparison_id in wanted:
                        selected[comparison_id] = row
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise RuntimeError("Comparison source predictions could not be read") from exc
        if digest.hexdigest() != expected_hash:
            raise RuntimeError("Comparison source prediction hash mismatch")
        if selected.keys() != wanted:
            raise RuntimeError("Comparison mismatch cases are absent from audited predictions")
        return selected

    def comparison(self) -> dict[str, Any]:
        if self._comparison is not None:
            return self._comparison
        audit = next(
            (
                item for item in self._audit.get("comparisons", [])
                if item.get("path") == COMPARISON_PATH.as_posix()
                and item.get("status") == "SUCCEEDED"
            ),
            None,
        )
        if not audit or audit.get("source_hashes_verified") is not True:
            raise RuntimeError("Verified delay comparison is unavailable")
        comparison = self._read_json(COMPARISON_PATH, max_bytes=100_000)
        if _sha256(self._path(COMPARISON_PATH)) != audit["sha256"]:
            raise RuntimeError("Comparison artifact hash mismatch")
        metrics = comparison.get("metrics")
        if not isinstance(metrics, dict):
            raise RuntimeError("Comparison metrics are unavailable")
        mismatch_records = comparison.get("mismatches", [])
        mismatch_ids = {item["case_id"] for item in mismatch_records}
        python_rows = self._prediction_rows_by_comparison_id(
            "delay_severity", "python", mismatch_ids
        )
        spark_rows = self._prediction_rows_by_comparison_id(
            "delay_severity", "spark", mismatch_ids
        )
        mismatches = []
        for item in mismatch_records:
            python_row = python_rows[item["case_id"]]
            spark_row = spark_rows[item["case_id"]]
            if (
                python_row.get("actual") != spark_row.get("actual")
                or python_row.get("prediction") != item["python_prediction"]
                or spark_row.get("prediction") != item["spark_prediction"]
                or python_row.get("service_date") != item["service_date"]
                or spark_row.get("service_date") != item["service_date"]
            ):
                raise RuntimeError("Persisted comparison cases disagree with source predictions")
            mismatches.append(
                {
                    **item,
                    "actual": python_row["actual"],
                    "truth_match": True,
                }
            )
        result = {
            "status": "ready",
            "data": {
                "task_name": "delay_severity",
                "dataset_version": comparison["dataset_version"],
                "comparison_policy": comparison.get("alignment", {}).get("policy"),
                "case_identity": comparison.get("alignment", {}).get("case_identity"),
                "shared_test_cases": metrics["shared_test_cases"],
                "truths_matched": audit["truths_matched_count"],
                "prediction_agreements": audit["prediction_agreements"],
                "prediction_disagreements": audit["prediction_disagreements"],
                "agreement_rate": metrics["agreement_rate"],
                "python_only_test_cases": metrics["python_only_test_cases"],
                "spark_only_test_cases": metrics["spark_only_test_cases"],
                "mismatches": mismatches,
                "source_hashes": comparison.get("input_sha256"),
                "limitations": [
                    "Comparison-only UTC+05:00 normalization; original predictions remain unchanged.",
                    "Disagreement explanations are descriptive; causal attribution was not established.",
                ],
            },
            "meta": {
                "source": "verified_pipeline_comparison",
                "artifact": str(COMPARISON_PATH),
                "sha256": audit["sha256"],
                "generated_at": comparison.get("ended_at_utc"),
                "freshness": "frozen",
            },
        }
        self._comparison = result
        return result

    def _relative_artifact(self, raw: str | Path) -> str:
        path = Path(raw)
        if path.is_absolute():
            path = self._path(path).relative_to(self.root)
        return path.as_posix()

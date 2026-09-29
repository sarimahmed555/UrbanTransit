"""Compare independently measured test predictions from Spark and Python runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import time
import uuid

from evidence_framework import EvidenceRecorder
from .output_safety import create_output_directory, versioned_output_path


MINIMUM_UNSEEN_CASES = 100
STRICT_COMPARISON = "strict"
PRODUCTION_V11_DELAY_TIME_NORMALIZATION = (
    "production-v1.1-delay-source-event-utc5"
)


class NotReadyError(ValueError):
    """Required independently certified comparison inputs are unavailable."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path):
    if not path.is_file():
        raise NotReadyError(f"Pipeline result artifact is absent: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NotReadyError(f"Pipeline result artifact is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise NotReadyError(f"Pipeline result is not a JSON object: {path}")
    return value


def _validate_result(path: Path, expected_engine: str, dataset_version: str):
    result = _load_json(path)
    contract = result.get("task_contract")
    certification = contract.get("certification") if isinstance(contract, dict) else None
    if (
        result.get("status") != "SUCCEEDED"
        or result.get("engine") != expected_engine
        or not isinstance(contract, dict)
        or contract.get("producer") != expected_engine
        or not isinstance(certification, dict)
        or certification.get("status") != "CERTIFIED"
    ):
        raise NotReadyError(
            f"{expected_engine} artifact is absent, uncertified, fixture-only, or has wrong provenance"
        )
    if (
        result.get("dataset_version") != dataset_version
        or contract.get("dataset_version") != dataset_version
        or result.get("task_name") != contract.get("task_name")
    ):
        raise NotReadyError(f"{expected_engine} artifact dataset/task identity does not match")
    if not isinstance(result.get("feature_version"), str) or not result["feature_version"]:
        raise NotReadyError(f"{expected_engine} feature version is missing")
    predictions = result.get("artifact_paths", {}).get("predictions")
    if not isinstance(predictions, str):
        raise NotReadyError(f"{expected_engine} prediction artifact reference is missing")
    prediction_path = Path(predictions).resolve()
    if not prediction_path.is_file():
        raise NotReadyError(f"{expected_engine} prediction artifact is absent: {prediction_path}")
    return result, prediction_path


def _read_test_predictions(
    path: Path,
    *,
    expected_engine: str,
    dataset_version: str,
    task_name: str,
    feature_version: str,
    comparison_policy: str = STRICT_COMPARISON,
):
    rows = {}
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise NotReadyError(
                        f"Invalid prediction JSON at {path}:{line_number}"
                    ) from exc
                if not isinstance(row, dict):
                    raise NotReadyError(f"Prediction row is not an object at {path}:{line_number}")
                if row.get("split") != "test":
                    continue
                case_id = row.get("case_id")
                if not isinstance(case_id, str) or not case_id:
                    raise NotReadyError(f"Test prediction lacks case_id at {path}:{line_number}")
                if comparison_policy == PRODUCTION_V11_DELAY_TIME_NORMALIZATION:
                    if expected_engine == "python":
                        comparison_id = case_id
                    else:
                        try:
                            separator = case_id.index(":")
                            trip_length = int(case_id[:separator])
                            remainder = case_id[separator + 1:]
                            if trip_length < 1 or len(remainder) <= trip_length:
                                raise ValueError
                            comparison_id = remainder[trip_length:]
                        except (ValueError, IndexError) as exc:
                            raise NotReadyError(
                                f"Invalid Spark trip/event case identity at {path}:{line_number}"
                            ) from exc
                    if not comparison_id.startswith("TSE"):
                        raise NotReadyError(
                            f"Expected stop-event comparison identity at {path}:{line_number}"
                        )
                else:
                    comparison_id = case_id
                if comparison_id in rows:
                    raise NotReadyError(
                        f"Duplicate comparison identity in {expected_engine} artifact: {comparison_id}"
                    )
                if row.get("engine") != expected_engine:
                    raise NotReadyError(
                        f"Prediction pipeline identity mismatch for case {case_id}: "
                        f"expected {expected_engine}"
                    )
                if (
                    row.get("dataset_version") != dataset_version
                    or row.get("task_name") != task_name
                    or row.get("feature_version") != feature_version
                ):
                    raise NotReadyError(
                        f"Prediction provenance mismatch for case {case_id}"
                    )
                prediction = row.get("prediction")
                if isinstance(prediction, (float, int)) and (
                    isinstance(prediction, bool) or not math.isfinite(prediction)
                ):
                    raise NotReadyError(f"Prediction is non-finite: {case_id}")
                if not isinstance(prediction, (float, int, str, bool)):
                    raise NotReadyError(f"Prediction value is unsupported: {case_id}")
                rows[comparison_id] = row
    except OSError as exc:
        raise NotReadyError(f"Prediction artifact is unreadable: {path}") from exc
    return rows


def _timestamp(value: str, *, correction_hours: int = 0) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise NotReadyError(f"Invalid comparison timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise NotReadyError("Comparison timestamps require an explicit timezone")
    return parsed - timedelta(hours=correction_hours)


def compare_artifacts(
    *,
    python_result_path: str | Path,
    spark_result_path: str | Path,
    dataset_version: str,
    output_root: str | Path,
    run_id: str | None = None,
    minimum_cases: int = MINIMUM_UNSEEN_CASES,
    comparison_policy: str = STRICT_COMPARISON,
) -> dict:
    if minimum_cases < MINIMUM_UNSEEN_CASES:
        raise ValueError(f"Production comparison requires at least {MINIMUM_UNSEEN_CASES} test cases")
    if comparison_policy not in {
        STRICT_COMPARISON,
        PRODUCTION_V11_DELAY_TIME_NORMALIZATION,
    }:
        raise ValueError(f"Unsupported comparison policy: {comparison_policy}")
    run_id = run_id or str(uuid.uuid4())
    destination = versioned_output_path(
        output_root,
        stage="spark_python_comparison",
        dataset_version=dataset_version,
        run_id=run_id,
    )
    destination = create_output_directory(
        destination,
        input_root=Path(python_result_path).resolve().parent,
    )
    started_at = _now()
    started = time.perf_counter()
    status, reason = "NOT_READY", None
    comparison_cases, mismatches = 0, []
    metrics = {}
    alignment = {
        "policy": comparison_policy,
        "scope": "comparison only; source result and prediction artifacts are unchanged",
    }
    if comparison_policy == PRODUCTION_V11_DELAY_TIME_NORMALIZATION:
        alignment.update({
            "case_identity": "stop_event_id extracted from Spark length-prefixed trip_id + stop_event_id",
            "spark_target_timestamp_correction_hours": -5,
            "timestamp_basis": "Asia/Karachi fixed UTC+05:00; normalize Spark target timestamps to UTC",
        })
    input_refs, input_hashes, pipeline_ids = {}, {}, {}
    try:
        python_path = Path(python_result_path).resolve()
        spark_path = Path(spark_result_path).resolve()
        if python_path == spark_path:
            raise NotReadyError("Python and Spark must be separate result artifacts")
        python_result, python_predictions_path = _validate_result(
            python_path, "python", dataset_version
        )
        spark_result, spark_predictions_path = _validate_result(
            spark_path, "spark", dataset_version
        )
        input_refs = {
            "python_result": str(python_path),
            "python_predictions": str(python_predictions_path),
            "spark_result": str(spark_path),
            "spark_predictions": str(spark_predictions_path),
        }
        input_hashes = {name: _hash(Path(value)) for name, value in input_refs.items()}
        if python_result["task_name"] != spark_result["task_name"]:
            raise NotReadyError("Pipeline task identities differ")
        if comparison_policy == PRODUCTION_V11_DELAY_TIME_NORMALIZATION and (
            dataset_version != "production-v1.1"
            or python_result["task_name"] != "delay_severity"
        ):
            raise NotReadyError(
                "The UTC+05:00 comparison normalization is restricted to production-v1.1 delay severity"
            )
        if python_predictions_path == spark_predictions_path:
            raise NotReadyError("Python and Spark prediction artifacts must be independently produced")
        python_rows = _read_test_predictions(
            python_predictions_path,
            expected_engine="python",
            dataset_version=dataset_version,
            task_name=python_result["task_name"],
            feature_version=python_result["feature_version"],
            comparison_policy=comparison_policy,
        )
        spark_rows = _read_test_predictions(
            spark_predictions_path,
            expected_engine="spark",
            dataset_version=dataset_version,
            task_name=spark_result["task_name"],
            feature_version=spark_result["feature_version"],
            comparison_policy=comparison_policy,
        )
        python_case_ids, spark_case_ids = set(python_rows), set(spark_rows)
        shared_case_ids = python_case_ids & spark_case_ids
        if comparison_policy == STRICT_COMPARISON and python_case_ids != spark_case_ids:
            raise NotReadyError("Test case identities differ between independent pipelines")
        comparison_cases = len(shared_case_ids)
        alignment.update({
            "python_test_cases": len(python_case_ids),
            "spark_test_cases": len(spark_case_ids),
            "shared_test_cases": comparison_cases,
            "python_only_test_cases": len(python_case_ids - spark_case_ids),
            "spark_only_test_cases": len(spark_case_ids - python_case_ids),
        })
        if comparison_cases < minimum_cases:
            raise NotReadyError(
                f"Only {comparison_cases} shared unseen test cases; {minimum_cases} are required"
            )

        numeric_deltas = []
        for case_id in sorted(shared_case_ids):
            python_row, spark_row = python_rows[case_id], spark_rows[case_id]
            for field in (
                "dataset_version", "task_name", "service_date", "actual",
            ):
                if python_row.get(field) != spark_row.get(field):
                    raise NotReadyError(
                        f"Shared test case {case_id} disagrees on {field}"
                    )
            for field in ("target_start", "target_end"):
                python_timestamp = _timestamp(python_row.get(field))
                spark_correction = (
                    5
                    if comparison_policy == PRODUCTION_V11_DELAY_TIME_NORMALIZATION
                    else 0
                )
                spark_timestamp = _timestamp(
                    spark_row.get(field),
                    correction_hours=spark_correction,
                )
                if python_timestamp != spark_timestamp:
                    raise NotReadyError(
                        f"Shared test case {case_id} disagrees on {field} "
                        "after the declared comparison-only timestamp normalization"
                    )
            python_prediction = python_row["prediction"]
            spark_prediction = spark_row["prediction"]
            equal = python_prediction == spark_prediction
            numeric = (
                isinstance(python_prediction, (int, float))
                and not isinstance(python_prediction, bool)
                and isinstance(spark_prediction, (int, float))
                and not isinstance(spark_prediction, bool)
            )
            delta = float(spark_prediction - python_prediction) if numeric else None
            if delta is not None:
                numeric_deltas.append(delta)
            if not equal:
                mismatches.append(
                    {
                        "case_id": case_id,
                        "service_date": python_row.get("service_date"),
                        "python_prediction": python_prediction,
                        "spark_prediction": spark_prediction,
                        "delta_spark_minus_python": delta,
                    }
                )
        metrics = {
            "shared_test_cases": comparison_cases,
            "python_test_cases": len(python_case_ids),
            "spark_test_cases": len(spark_case_ids),
            "python_only_test_cases": len(python_case_ids - spark_case_ids),
            "spark_only_test_cases": len(spark_case_ids - python_case_ids),
            "matching_predictions": comparison_cases - len(mismatches),
            "mismatching_predictions": len(mismatches),
            "agreement_rate": (comparison_cases - len(mismatches)) / comparison_cases,
            "numeric_delta_mean": (
                sum(numeric_deltas) / len(numeric_deltas) if numeric_deltas else None
            ),
            "numeric_delta_max_abs": (
                max(abs(value) for value in numeric_deltas) if numeric_deltas else None
            ),
        }
        status = "SUCCEEDED"
        pipeline_ids = {
            "python": {
                "pipeline_id": python_result.get("pipeline_id") or "ml_execution:python",
                "feature_version": python_result["feature_version"],
                "result_hash": input_hashes["python_result"],
                "prediction_hash": input_hashes["python_predictions"],
            },
            "spark": {
                "pipeline_id": spark_result.get("pipeline_id") or "ml_execution:spark",
                "feature_version": spark_result["feature_version"],
                "result_hash": input_hashes["spark_result"],
                "prediction_hash": input_hashes["spark_predictions"],
            },
        }
    except NotReadyError as exc:
        reason = str(exc)
        if python_result_path:
            input_refs["python_result"] = str(Path(python_result_path).resolve())
        if spark_result_path:
            input_refs["spark_result"] = str(Path(spark_result_path).resolve())
        for name, ref in input_refs.items():
            path = Path(ref)
            if path.is_file():
                input_hashes[name] = _hash(path)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        status = "FAILED"
        reason = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        status = "FAILED"
        reason = f"Unexpected {type(exc).__name__}: {exc}"

    ended_at = _now()
    elapsed = time.perf_counter() - started
    result = {
        "schema_version": "1.0",
        "status": status,
        "dataset_version": dataset_version,
        "task_name": (
            python_result.get("task_name")
            if "python_result" in locals() and isinstance(python_result, dict)
            else None
        ),
        "run_id": run_id,
        "comparison_stage": "spark_python_comparison",
        "started_at_utc": started_at,
        "ended_at_utc": ended_at,
        "runtime_sec": elapsed,
        "minimum_unseen_test_cases": minimum_cases,
        "comparison_case_count": comparison_cases,
        "metrics": metrics,
        "alignment": alignment,
        "mismatches": mismatches,
        "pipelines": pipeline_ids,
        "input_artifacts": input_refs,
        "input_sha256": input_hashes,
        "output_artifact": str(destination / "comparison.json"),
        "reason": reason,
    }
    _write_json_new(destination / "comparison.json", result)
    recorder = EvidenceRecorder(
        dataset_version=dataset_version,
        command="python -m runtime_orchestration.compare",
        input_artifact=json.dumps(input_refs, sort_keys=True),
        output_artifact=result["output_artifact"],
    )
    recorder.record(
        "pipeline_comparison",
        status=status,
        independent_results=pipeline_ids,
        comparison_sample_size=comparison_cases,
        alignment=alignment,
        agreement=metrics,
        discrepancies=mismatches,
        runtime_sec=elapsed,
        input_sha256=input_hashes,
        started_at_utc=started_at,
        ended_at_utc=ended_at,
        reason=reason,
    )
    _write_json_new(destination / "evidence.json", recorder.bundle.payload)
    return result


def _write_json_new(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-result", required=True)
    parser.add_argument("--spark-result", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--comparison-policy",
        choices=(
            STRICT_COMPARISON,
            PRODUCTION_V11_DELAY_TIME_NORMALIZATION,
        ),
        default=STRICT_COMPARISON,
        help="Opt into the source-backed production-v1.1 delay timestamp normalization",
    )
    args = parser.parse_args(argv)
    try:
        result = compare_artifacts(
            python_result_path=args.python_result,
            spark_result_path=args.spark_result,
            dataset_version=args.dataset_version,
            output_root=args.output_root,
            run_id=args.run_id,
            comparison_policy=args.comparison_policy,
        )
    except Exception as exc:
        print(f"Comparison execution failed: {type(exc).__name__}: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "SUCCEEDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

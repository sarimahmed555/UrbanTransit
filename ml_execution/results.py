"""Readiness contract for downstream consumers. Never generate placeholder metrics."""
import json
from pathlib import Path
from .contracts import pending


def validate_result(result):
    if result.get("status") not in {"SUCCEEDED", "FIXTURE_TESTED"}:
        return result
    required = {"task_name", "model_name", "model_type", "parameters", "random_seed",
                "feature_version", "feature_list", "dataset_version", "periods", "sample_counts",
                "metrics", "baseline_metrics", "model_comparison", "selection_rationale",
                "runtime_sec", "artifact_paths", "reproducibility", "input_hashes"}
    if required - result.keys():
        raise ValueError(f"Incomplete result contract: {sorted(required - result.keys())}")
    if result["status"] == "SUCCEEDED" and result.get("task_contract", {}).get("certification", {}).get("status") != "CERTIFIED":
        raise ValueError("Successful certified evidence requires a certified input contract")
    if result["model_type"] != "descriptive_analytics":
        if not result.get("selected_model") or len(result["model_comparison"]) < 3 or not result["metrics"].get("test"):
            raise ValueError("Successful model result requires comparison, selection and test evaluation")
    if result["task_name"] in {"passenger_demand", "occupancy_forecast"} and not result.get("baseline_vs_selected"):
        raise ValueError("Forecast result requires baseline comparison")
    return result


def read_result(path, *, allow_fixture=False):
    path = Path(path)
    if not path.is_file():
        return pending(None, "Production result artifact absent")
    try:
        result = validate_result(json.loads(path.read_text()))
    except (ValueError, KeyError, TypeError) as exc:
        return pending(None, f"Invalid result artifact: {exc}")
    if result.get("status") != "SUCCEEDED" and not (allow_fixture and result.get("status") == "FIXTURE_TESTED"):
        return pending(result.get("task_name"), "Successful certified runtime result absent")
    for artifact in result.get("artifact_paths", {}).values():
        if not Path(artifact).exists():
            return pending(result.get("task_name"), f"Referenced artifact absent: {artifact}")
    return result

"""Strict, versioned boundary with feature engineering; no feature derivations."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from python_pipeline.splits import SPLITS, assign_split
from evidence_framework.schema import NOT_READY

TASKS = {
    "delay_severity": "classification",
    "passenger_demand": "regression",
    "occupancy_forecast": "regression",
    "crowding_risk": "classification",
    "route_clustering": "clustering",
    "delay_service_analytics": "analytics",
}
PERIODS = {s.name: {"start": s.start.isoformat(), "end": s.end.isoformat()} for s in SPLITS}
FORBIDDEN = {"quality_status", "scenario_id", "latent_parameters", "injection_id",
             "case_id", "operational_departure_id", "target_available_at"}
CURRENT_OCCUPANCY_OUTCOMES = {
    "actual_departure_utc", "outcome_available_at_utc", "event_value_available_at",
    "departure_assignment_id", "onboard_departure", "boardings", "alightings",
    "capacity_snapshot", "capacity_utilization", "occupancy_status", "over_capacity",
    "departure_delay_sec", "occupancy_target", "crowding_target",
}
GUARDS = {"case_id", "service_date", "target_start", "target_end", "feature_cutoff_at",
          "value_available_at", "target_available_at"}


class NotReady(ValueError):
    """An input or runtime dependency is unavailable; never a successful result."""


def pending(task, reason):
    return {"schema_version": "1.0", "task_name": task, "status": NOT_READY,
            "evidence_status": "PENDING CERTIFIED RUNTIME EVIDENCE", "reason": str(reason),
            "metrics": {}, "baseline_metrics": {}, "model_comparison": [],
            "selected_model": None, "acceptance": None, "periods": PERIODS}


def instant(value):
    result = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Availability and target timestamps require explicit timezone")
    return result


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def contract_digest(manifest):
    contract = {k: v for k, v in manifest.items() if k != "certification"}
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_manifest(m, *, fixture=False):
    required = {"schema_version", "task_name", "dataset_version", "feature_version", "producer",
                "features", "partitions", "certification", "grain", "target_definition"}
    if required - m.keys():
        raise ValueError(f"Missing manifest fields: {sorted(required - m.keys())}")
    if m["schema_version"] != "1.0" or m["task_name"] not in TASKS:
        raise ValueError("Unsupported manifest version/task")
    for key in ("dataset_version", "feature_version", "grain", "target_definition"):
        if not isinstance(m[key], str) or not m[key].strip():
            raise ValueError(f"Empty {key}")
    if m["producer"] not in {"python", "spark"}:
        raise ValueError("producer must identify the independent feature pipeline")
    cert = m["certification"]
    if cert.get("status") != ("FIXTURE" if fixture else "CERTIFIED"):
        raise NotReady("Certified task features are absent")
    if not fixture and not cert.get("evidence_path"):
        raise NotReady("Missing feature certification evidence path")
    kind = TASKS[m["task_name"]]
    features = m["features"]
    if not isinstance(features, list) or (kind != "analytics" and not features):
        raise ValueError("A numeric feature allowlist is required")
    if len(set(features)) != len(features) or any(not isinstance(x, str) for x in features):
        raise ValueError("Feature names must be unique strings")
    if set(features) & (FORBIDDEN | GUARDS | {m.get("target")}):
        raise ValueError("Target, identifiers or audit/provenance fields cannot be predictors")
    if m["task_name"] in {"occupancy_forecast", "crowding_risk"} and (
        set(features) & CURRENT_OCCUPANCY_OUTCOMES
    ):
        raise ValueError("Current-event occupancy, capacity, delay or availability cannot be predictors")
    if set(m["partitions"]) != set(PERIODS):
        raise ValueError("Exactly train, validation and test partitions are required")
    if kind in {"classification", "regression"} and not m.get("target"):
        raise ValueError("A feature-owned target is required")
    if kind == "classification":
        labels = m.get("class_labels", [])
        if len(labels) < 2 or len(set(labels)) != len(labels):
            raise ValueError("Declare all class names; target must be their zero-based index")
        if not m.get("thresholds"):
            raise ValueError("Feature-owned configurable severity/crowding thresholds required")
        if m["task_name"] == "crowding_risk" and (len(labels) != 2 or not 0 < m.get("risk_probability_threshold", 0) < 1):
            raise ValueError("Crowding requires binary classes (1=exceeds threshold) and a probability threshold")
    if kind == "regression":
        if m.get("baseline_column") not in features:
            raise ValueError("Declare a certified historical lag feature as baseline_column")
        if m.get("baseline_type") not in {"seasonal_naive", "last_observation"}:
            raise ValueError("Declare seasonal_naive or last_observation baseline")
        if m.get("forecast_protocol") != "rolling_origin_frozen_model" or not m.get("forecast_horizon"):
            raise ValueError("Declare rolling_origin_frozen_model and forecast_horizon")
    return m


def validate_rows(m, partitions):
    """Reject horizon overlap, unknown availability and cross-period group leakage."""
    kind = TASKS[m["task_name"]]
    if set(partitions) != set(PERIODS):
        raise ValueError("Exactly the three chronological partitions are required")
    seen, groups = set(), {}
    for split, rows in partitions.items():
        if not rows:
            raise NotReady(f"Empty {split} feature partition")
        for row in rows:
            if GUARDS - row.keys():
                raise ValueError(f"Missing case guards: {sorted(GUARDS - row.keys())}")
            if not row["case_id"] or row["case_id"] in seen:
                raise ValueError("case_id must be nonempty and globally unique")
            seen.add(row["case_id"])
            if assign_split(row["service_date"]) != split:
                raise ValueError("Service date disagrees with partition")
            start, end = instant(row["target_start"]), instant(row["target_end"])
            period = PERIODS[split]
            low = dt.datetime.combine(dt.date.fromisoformat(period["start"]), dt.time(), ZoneInfo("Asia/Karachi"))
            high = dt.datetime.combine(dt.date.fromisoformat(period["end"]) + dt.timedelta(days=1), dt.time(), ZoneInfo("Asia/Karachi"))
            point_target = (
                m["task_name"] in {"delay_severity", "occupancy_forecast", "crowding_risk"}
                and start == end
            )
            if not low <= start <= end <= high or (start == end and not point_target):
                raise ValueError("Target interval crosses its chronological period")
            cutoff, available = instant(row["feature_cutoff_at"]), instant(row["value_available_at"])
            if available > cutoff:
                raise ValueError("Feature availability exceeds prediction cutoff")
            if m["task_name"] in {"occupancy_forecast", "crowding_risk"} and (
                cutoff >= instant(row["target_available_at"]) or cutoff >= start
            ):
                raise ValueError("Occupancy/crowding target must be a strictly future event")
            if kind in {"classification", "regression"}:
                if cutoff > start or instant(row["target_available_at"]) < end:
                    raise ValueError("Future prediction cutoff/target availability is invalid")
                if split != "test" and instant(row["target_available_at"]) > high:
                    raise ValueError("Training/validation label was unavailable at the split boundary")
            elif cutoff > high or end > cutoff:
                raise ValueError("Historical aggregate is not available inside its period")
            lineage = row.get("source_departure_ids", [])
            if not isinstance(lineage, list) or any(not isinstance(g, str) or not g for g in lineage):
                raise ValueError("source_departure_ids must be a list of nonempty stable IDs")
            if m["task_name"] == "route_clustering":
                if not isinstance(row.get("route_id"), str) or not row["route_id"]:
                    raise ValueError("Route-clustering cases require a stable route_id")
                if not finite(row.get("direction_id")):
                    raise ValueError("Route-clustering cases require a numeric direction_id")
            elif not lineage and not row.get("operational_departure_id"):
                raise ValueError("Departure lineage is required to enforce group isolation")
            for group in lineage:
                if group in groups and groups[group] != split:
                    raise ValueError("Operational departure appears across chronological partitions")
                groups[group] = split
            if row.get("operational_departure_id"):
                group = row["operational_departure_id"]
                if group in groups and groups[group] != split:
                    raise ValueError("Operational departure appears across chronological partitions")
                groups[group] = split
            if not all(finite(row.get(name)) for name in m["features"]):
                raise ValueError("Certified numeric predictors must be finite; no silent imputation")
            if kind in {"classification", "regression"}:
                y = row.get(m["target"])
                if not finite(y):
                    raise ValueError("Missing/nonfinite target")
                if kind == "classification" and (int(y) != y or not 0 <= y < len(m["class_labels"])):
                    raise ValueError("Class target is outside declared label vocabulary")
                if kind == "regression" and y < 0:
                    raise ValueError("Demand/occupancy targets cannot be negative")
            if m["task_name"] in {"occupancy_forecast", "crowding_risk"}:
                if row.get("occupancy_status") != "AVAILABLE" or not finite(row.get("capacity_snapshot")) or row["capacity_snapshot"] <= 0:
                    raise ValueError("Occupancy requires certified capacity eligibility")
    if kind == "classification" and {int(r[m["target"]]) for r in partitions["train"]} != set(range(len(m["class_labels"]))):
        raise NotReady("Training data does not cover all declared classes")


def load_features(path, *, fixture=False, max_rows=100000):
    """Bounded JSONL handoff; hashes bind evidence to exact input bytes."""
    path = Path(path).resolve()
    if "raw_data" in path.parts:
        raise ValueError("ML execution accepts certified feature packages, not raw data")
    if not path.is_file():
        raise NotReady(f"Feature manifest absent: {path}")
    m = validate_manifest(json.loads(path.read_text()), fixture=fixture)
    root = path.parent
    def resolve(relative):
        result = (root / relative).resolve()
        if not result.is_relative_to(root) or "raw_data" in result.parts:
            raise ValueError("Input must stay within its certified feature package")
        return result
    if not fixture:
        evidence = resolve(m["certification"]["evidence_path"])
        if not evidence.is_file():
            raise NotReady("Feature certification artifact absent")
        cert = json.loads(evidence.read_text())
        if (cert.get("status") != "CERTIFIED" or any(cert.get(k) != m[k] for k in
                ("dataset_version", "feature_version", "task_name", "producer"))):
            raise NotReady("Certification does not match this task dataset")
        if cert.get("partitions") != m["partitions"]:
            raise NotReady("Certification does not bind the feature partition hashes")
        if cert.get("contract_sha256") != contract_digest(m):
            raise NotReady("Certification does not bind the task/feature policy")
    partitions, hashes, total = {}, {}, 0
    for split, spec in m["partitions"].items():
        file = resolve(spec["path"])
        if not file.is_file():
            raise NotReady(f"Missing {split} features")
        digest, rows = hashlib.sha256(), []
        with file.open("rb") as stream:
            for line in stream:
                digest.update(line)
                if line.strip():
                    total += 1
                    if total > max_rows:
                        raise NotReady(f"Bounded feature handoff exceeds max_rows={max_rows}; publish an approved bounded cohort")
                    rows.append(json.loads(line))
        if digest.hexdigest() != spec["sha256"]:
            raise ValueError(f"{split} feature hash mismatch")
        partitions[split], hashes[split] = rows, digest.hexdigest()
    validate_rows(m, partitions)
    return m, partitions, {"manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "partition_sha256": hashes}

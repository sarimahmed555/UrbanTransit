"""Build pre-event occupancy and crowding packages from completed Spark outputs."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from ml_execution.contracts import PERIODS, contract_digest, load_features, validate_manifest
from ml_execution.models import SEED

SOURCE_TASK = "occupancy"
TASKS = {
    "occupancy_forecast": {
        "target": "occupancy_target",
        "kind": "regression",
        "features": [
            "scheduled_hour_utc", "service_weekday", "is_weekend", "service_month",
            "direction_id", "distance_km", "stop_sequence",
            "historical_occupancy_ratio_lag_1", "historical_onboard_departure_lag_1",
        ],
    },
    "crowding_risk": {
        "target": "crowding_target",
        "kind": "classification",
        "features": [
            "scheduled_hour_utc", "service_weekday", "is_weekend", "service_month",
            "direction_id", "distance_km", "stop_sequence",
            "historical_occupancy_ratio_lag_1", "historical_onboard_departure_lag_1",
        ],
    },
}
SPLIT_START_UTC = {
    "train": "2024-12-31T19:00:00Z",
    "validation": "2025-12-31T19:00:00Z",
    "test": "2026-03-31T19:00:00Z",
}
SPLIT_END_UTC = {
    "train": "2025-12-31T19:00:00Z",
    "validation": "2026-03-31T19:00:00Z",
    "test": "2026-06-30T19:00:00Z",
}
KNOWN_AT = (
    "known_schedule_available_at", "route_value_available_at",
    "route_stop_value_available_at", "pattern_published_at_utc",
    "schedule_time_available_at",
)


def _iso(value) -> str:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dt.date):
        return value.isoformat()
    raise TypeError(f"Expected date/time value, got {type(value).__name__}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_record(spark, evidence_path: str, run_id: str, split: str, output: str):
    evidence_files = []
    for evidence_root in evidence_path.split(","):
        root = spark._jvm.org.apache.hadoop.fs.Path(evidence_root)
        filesystem = root.getFileSystem(spark._jsc.hadoopConfiguration())
        iterator = filesystem.listFiles(root, True)
        while iterator.hasNext():
            path = iterator.next().getPath()
            if path.getName().startswith("part-"):
                evidence_files.append(path.toString())
    if not evidence_files:
        raise FileNotFoundError(f"No event files in occupancy evidence: {evidence_path}")
    stages = []
    for line in spark.sparkContext.textFile(",".join(evidence_files)).collect():
        if not line.strip():
            continue
        record = json.loads(line)
        if (
            record.get("run_id") == run_id
            and record.get("stage") == f"features_{SOURCE_TASK}_{split}"
            and record.get("status") == "SUCCEEDED"
            and record.get("output") == output
        ):
            stages.append(record)
    if not stages:
        return {
            "stage": f"features_{SOURCE_TASK}_{split}",
            "status": "SUCCESS_MARKER_ONLY",
            "output": output,
            "evidence_note": (
                "The HDFS _SUCCESS marker exists, but no final per-stage event record "
                "was found in supplied run evidence."
            ),
        }
    return stages[-1]


def _exists(spark, uri: str) -> bool:
    path = spark._jvm.org.apache.hadoop.fs.Path(uri)
    filesystem = path.getFileSystem(spark._jsc.hadoopConfiguration())
    return bool(filesystem.exists(path))


def _write_package(output: Path, task_name: str, source_provenance: dict,
                   partition_specs: dict, sample_counts: dict, class_counts: dict):
    spec = TASKS[task_name]
    manifest = {
        "schema_version": "1.0",
        "task_name": task_name,
        "dataset_version": "production-v1.1",
        "feature_version": f"spark-occupancy-asof-{task_name}-20260928",
        "producer": "spark",
        "grain": "one scheduled route-stop departure with a later observed load outcome",
        "target_definition": (
            "Future observed onboard_departure divided by the actual positive "
            "ACTUAL departure assignment capacity_snapshot, or the existing over_capacity "
            "flag derived by the producer from that measured ratio (>1.0). "
            "The current event's load, capacity, utilization, actual departure, delay, "
            "and crowding label are targets/availability guards only and never predictors."
        ),
        "features": spec["features"],
        "target": "target",
        "partitions": partition_specs,
        "certification": {
            "status": "CERTIFIED",
            "evidence_path": "feature_certification.json",
            "scope": "bounded ML handoff integrity and pre-event temporal-contract validation only",
        },
        "source_provenance": source_provenance,
    }
    if task_name == "occupancy_forecast":
        manifest.update({
            "target": "target",
            "baseline_column": "historical_occupancy_ratio_lag_1",
            "baseline_type": "last_observation",
            "forecast_protocol": "rolling_origin_frozen_model",
            "forecast_horizon": (
                "scheduled departure cutoff to later actual departure occupancy; "
                "unavailable at prediction time until event and capacity assignment data arrive"
            ),
        })
    else:
        manifest.update({
            "class_labels": ["WITHIN_CAPACITY", "OVER_CAPACITY"],
            "thresholds": {"capacity_utilization_ratio": 1.0, "positive_class": "strictly greater"},
            "risk_probability_threshold": 0.5,
        })
    validate_manifest(manifest)
    certification = {
        "status": "CERTIFIED",
        "dataset_version": manifest["dataset_version"],
        "feature_version": manifest["feature_version"],
        "task_name": task_name,
        "producer": "spark",
        "partitions": partition_specs,
        "contract_sha256": contract_digest(manifest),
        "certification_scope": manifest["certification"]["scope"],
        "source_provenance": source_provenance,
    }
    (output / "feature_certification.json").write_text(
        json.dumps(certification, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    loaded, _, hashes = load_features(manifest_path, max_rows=100000)
    if hashes["partition_sha256"] != {
        split: values["sha256"] for split, values in partition_specs.items()
    }:
        raise ValueError(f"Re-read partition hashes changed for {task_name}")
    print(json.dumps({
        "status": "HANDOFF_VALIDATED",
        "task_name": loaded["task_name"],
        "manifest": str(manifest_path),
        "sample_counts": sample_counts,
        "class_counts": class_counts,
        "partition_sha256": hashes["partition_sha256"],
        "contract_sha256": certification["contract_sha256"],
        "source_dataset_certification": source_provenance["source_dataset_certification"],
    }, sort_keys=True), flush=True)
    return manifest_path


def export_occupancy(*, spark, source_base: str, evidence_path: str, run_id: str,
                     assignments_path: Path, output_root: Path, max_rows: int = 100000):
    from pyspark.sql import Window, functions as F
    from spark_jobs.schemas import raw_schema_for

    if max_rows < 3:
        raise ValueError("max_rows must allow at least one case in every split")
    if output_root.exists():
        raise FileExistsError(f"Refusing to overwrite occupancy handoffs: {output_root}")
    if spark.conf.get("spark.sql.session.timeZone") != "UTC":
        raise ValueError("Spark session timezone must be UTC for exact timestamp export")
    assignment_files = sorted(assignments_path.glob("part-*.csv"))
    if not assignments_path.is_dir() or not assignment_files:
        raise FileNotFoundError(f"Existing assignment source files are absent: {assignments_path}")
    assignment_hashes = {
        str(path): {"sha256": _sha256(path), "bytes": path.stat().st_size}
        for path in assignment_files
    }
    assignment_source = (
        spark.read.schema(raw_schema_for("trip_vehicle_assignments"))
        .option("header", "true")
        .option("pathGlobFilter", "*.csv")
        .option("enforceSchema", "false")
        .option("nullValue", "\\N")
        .option("mode", "FAILFAST")
        .option("quote", '"')
        .option("escape", '"')
        .csv(assignments_path.resolve().as_uri())
    )
    assignments = assignment_source.select(
        F.col("assignment_id").alias("__assignment_id"),
        F.col("capacity_snapshot").alias("__assignment_capacity"),
        F.col("assignment_kind").alias("__assignment_kind"),
        F.to_timestamp("announced_at_utc").alias("__assignment_announced_at"),
        F.to_timestamp("value_available_at").alias("__assignment_value_available_at"),
        F.to_timestamp("correction_time").alias("__assignment_correction_at"),
        F.to_timestamp("effective_start_utc").alias("__assignment_start"),
        F.to_timestamp("effective_end_utc").alias("__assignment_end"),
        F.col("quality_status").alias("__assignment_quality_status"),
    ).withColumn(
        "__assignment_available_at",
        F.greatest(
            "__assignment_announced_at",
            "__assignment_value_available_at",
            "__assignment_correction_at",
        ),
    )

    source_paths = {
        split: f"{source_base.rstrip('/')}/task=occupancy/split={split}"
        for split in PERIODS
    }
    frames, source_rows, source_stages = {}, {}, {}
    required = {
        "service_date", "stop_event_id", "route_stop_id", "route_id", "direction_id",
        "trip_id", "operational_departure_id", "scheduled_departure_utc",
        "actual_departure_utc", "outcome_available_at_utc", "event_value_available_at",
        "departure_assignment_id",
        "known_schedule_available_at", "route_value_available_at",
        "route_stop_value_available_at", "pattern_published_at_utc",
        "schedule_time_available_at", "distance_km", "stop_sequence",
        "onboard_departure", "capacity_snapshot", "capacity_utilization",
        "occupancy_status", "over_capacity",
    }
    for split, path in source_paths.items():
        marker = f"{path}/_SUCCESS"
        if not _exists(spark, marker):
            raise FileNotFoundError(f"Completed occupancy marker missing: {marker}")
        source_stages[split] = _source_record(spark, evidence_path, run_id, split, path)
        frame = spark.read.parquet(path)
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"{split} occupancy output missing required fields: {missing}")
        source_rows[split] = frame.count()
        if source_rows[split] != source_stages[split].get("rows"):
            if source_stages[split].get("status") == "SUCCESS_MARKER_ONLY":
                source_stages[split]["rows"] = source_rows[split]
            else:
                raise ValueError(f"Source evidence row count differs for occupancy {split}")
        frames[split] = frame

    combined = None
    for split in PERIODS:
        frame = frames[split].withColumn("__source_split", F.lit(split))
        combined = frame if combined is None else combined.unionByName(frame)
    assert combined is not None

    event_availability = F.greatest(
        F.col("event_value_available_at"), F.col("outcome_available_at_utc")
    )
    schedule_availability = F.greatest(*[F.col(name) for name in KNOWN_AT])
    base = (
        combined.join(
            assignments,
            F.col("departure_assignment_id") == F.col("__assignment_id"),
            "left",
        )
        .withColumn("__event_available_at", event_availability)
        .withColumn(
            "__occupancy_available_at",
            F.greatest("__event_available_at", "__assignment_available_at"),
        )
        .withColumn("__known_at", schedule_availability)
        .withColumn("__cutoff", F.col("scheduled_departure_utc"))
        .withColumn("__ratio", F.col("capacity_utilization").cast("double"))
        .withColumn("__load", F.col("onboard_departure").cast("double"))
    )
    source_bounds = {}
    candidate_frames = {}
    for split, period in PERIODS.items():
        frame = frames[split]
        bounds = frame.agg(
            F.min("service_date").alias("min_date"),
            F.max("service_date").alias("max_date"),
        ).first()
        if bounds.min_date is None:
            raise ValueError(f"Empty completed occupancy output in {split}")
        min_date, max_date = bounds.min_date.isoformat(), bounds.max_date.isoformat()
        if min_date < period["start"] or max_date > period["end"]:
            raise ValueError(f"Occupancy source {split} contains dates outside its period")
        source_bounds[split] = {"min": min_date, "max": max_date}
        valid = (
            (F.col("__source_split") == split)
            & F.col("service_date").isNotNull()
            & F.col("stop_event_id").isNotNull()
            & F.col("route_stop_id").isNotNull()
            & F.col("route_id").isNotNull()
            & F.col("direction_id").isNotNull()
            & F.col("trip_id").isNotNull()
            & F.col("operational_departure_id").isNotNull()
            & F.col("__cutoff").isNotNull()
            & F.col("__event_available_at").isNotNull()
            & F.col("__assignment_available_at").isNotNull()
            & (F.col("__assignment_quality_status") == "VALID")
            & (F.col("__assignment_kind") == "ACTUAL")
            & (F.col("__assignment_capacity") == F.col("capacity_snapshot"))
            & F.col("__known_at").isNotNull()
            & F.col("known_schedule_available_at").isNotNull()
            & F.col("route_value_available_at").isNotNull()
            & F.col("route_stop_value_available_at").isNotNull()
            & F.col("pattern_published_at_utc").isNotNull()
            & F.col("schedule_time_available_at").isNotNull()
            & (F.col("__known_at") <= F.col("__cutoff"))
            & (F.col("__occupancy_available_at") > F.col("__cutoff"))
            & (F.col("actual_departure_utc") > F.col("__cutoff"))
            & (F.col("__cutoff") >= F.to_timestamp(F.lit(SPLIT_START_UTC[split])))
            & (F.col("__cutoff") < F.to_timestamp(F.lit(SPLIT_END_UTC[split])))
            & (F.col("__occupancy_available_at") <= F.to_timestamp(F.lit(SPLIT_END_UTC[split])))
            & (F.col("__assignment_start") <= F.col("actual_departure_utc"))
            & (F.col("actual_departure_utc") < F.col("__assignment_end"))
            & (F.col("occupancy_status") == F.lit("AVAILABLE"))
            & F.col("onboard_departure").isNotNull()
            & (F.col("onboard_departure") >= 0)
            & F.col("capacity_snapshot").isNotNull()
            & (F.col("capacity_snapshot") > 0)
            & F.col("capacity_utilization").isNotNull()
            & (F.col("capacity_utilization") >= 0)
            & (F.col("capacity_utilization") == (
                F.col("onboard_departure").cast("double")
                / F.col("capacity_snapshot").cast("double")
            ))
            & (F.col("over_capacity") == (F.col("capacity_utilization") > 1.0))
            & F.col("distance_km").isNotNull()
            & F.col("stop_sequence").isNotNull()
        )
        candidate_frames[split] = base.filter(valid).select(
            "service_date", "stop_event_id", "route_stop_id", "route_id",
            "direction_id", "trip_id", "operational_departure_id",
            "scheduled_departure_utc", "known_schedule_available_at",
            "route_value_available_at", "route_stop_value_available_at",
            "pattern_published_at_utc", "schedule_time_available_at",
            "distance_km", "stop_sequence", "actual_departure_utc",
            "capacity_snapshot", "__cutoff", "__known_at",
            "__event_available_at", "__occupancy_available_at", "__ratio", "__load",
            "over_capacity", "__source_split",
        )
        print(json.dumps({
            "split": split,
            "source_rows": source_rows[split],
            "pre_event_eligible_targets": candidate_frames[split].count(),
            "date_min": min_date,
            "date_max": max_date,
            "source_stage": source_stages[split]["stage"],
        }), flush=True)

    predictions = None
    for split in PERIODS:
        frame = candidate_frames[split].select(
            "service_date", "stop_event_id", "route_stop_id", "route_id",
            "direction_id", "trip_id", "operational_departure_id",
            "scheduled_departure_utc", "known_schedule_available_at",
            "route_value_available_at", "route_stop_value_available_at",
            "pattern_published_at_utc", "schedule_time_available_at",
            "distance_km", "stop_sequence", "actual_departure_utc",
            "capacity_snapshot", "__cutoff", "__known_at",
            "__event_available_at", "__occupancy_available_at", "__ratio", "__load",
            "over_capacity", "__source_split",
        )
        predictions = frame if predictions is None else predictions.unionByName(frame)
    assert predictions is not None

    history_window = Window.partitionBy("route_stop_id").orderBy(
        "scheduled_departure_utc", "trip_id", "stop_event_id"
    )
    valid_history_ratio = (
        (F.col("occupancy_status") == "AVAILABLE")
        & F.col("capacity_utilization").isNotNull()
        & (F.col("capacity_snapshot") > 0)
        & (F.col("__assignment_capacity") == F.col("capacity_snapshot"))
        & (F.col("__assignment_kind") == "ACTUAL")
        & (F.col("__assignment_quality_status") == "VALID")
        & F.col("__assignment_start").isNotNull()
        & F.col("actual_departure_utc").between(
            F.col("__assignment_start"), F.col("__assignment_end")
        )
        & (F.col("actual_departure_utc") < F.col("__assignment_end"))
    )
    ordered = base.withColumn(
        "historical_occupancy_ratio_lag_1",
        F.lag(
            F.when(
                valid_history_ratio,
                F.col("capacity_utilization").cast("double"),
            )
        ).over(history_window),
    ).withColumn(
        "historical_onboard_departure_lag_1",
        F.lag(
            F.when(
                valid_history_ratio
                & F.col("onboard_departure").isNotNull()
                & (F.col("onboard_departure") >= 0),
                F.col("onboard_departure").cast("double"),
            )
        ).over(history_window),
    ).withColumn(
        "historical_occupancy_available_at",
        F.lag(F.col("__occupancy_available_at")).over(history_window),
    ).withColumn(
        "historical_occupancy_actual_at",
        F.lag(F.col("actual_departure_utc")).over(history_window),
    )
    prepared = predictions.join(
        ordered.select(
            "route_stop_id", "stop_event_id",
            "historical_occupancy_ratio_lag_1",
            "historical_onboard_departure_lag_1",
            "historical_occupancy_available_at",
            "historical_occupancy_actual_at",
        ),
        ["route_stop_id", "stop_event_id"],
        "inner",
    ).filter(
        F.col("historical_occupancy_ratio_lag_1").isNotNull()
        & F.col("historical_onboard_departure_lag_1").isNotNull()
        & (F.col("historical_occupancy_available_at") <= F.col("__cutoff"))
        & (F.col("historical_occupancy_actual_at") < F.col("__cutoff"))
    )
    prepared = prepared.withColumn(
        "value_available_at",
        F.greatest(F.col("__known_at"), F.col("historical_occupancy_available_at")),
    ).withColumn(
        "scheduled_hour_utc", F.hour("scheduled_departure_utc").cast("double")
    ).withColumn(
        "service_weekday",
        (F.dayofweek("service_date") - F.lit(2) + F.lit(7)) % F.lit(7),
    ).withColumn(
        "is_weekend",
        F.when(F.dayofweek("service_date").isin(1, 7), 1.0).otherwise(0.0),
    ).withColumn(
        "service_month", F.month("service_date").cast("double")
    ).withColumn(
        "direction_id", F.col("direction_id").cast("double")
    ).withColumn(
        "distance_km", F.col("distance_km").cast("double")
    ).withColumn(
        "stop_sequence", F.col("stop_sequence").cast("double")
    ).filter(F.col("value_available_at") <= F.col("__cutoff"))

    eligible_counts = {
        split: prepared.filter(F.col("__source_split") == split).count()
        for split in PERIODS
    }
    if any(count < 1 for count in eligible_counts.values()):
        raise ValueError(f"No pre-event rows with real same-stop history: {eligible_counts}")
    available = sum(eligible_counts.values())
    budget = min(max_rows, available)
    quotas = {split: max(1, round(budget * eligible_counts[split] / available)) for split in PERIODS}
    while sum(quotas.values()) > budget:
        key = max(quotas, key=lambda split: (quotas[split], split))
        if quotas[key] > 1:
            quotas[key] -= 1
    while sum(quotas.values()) < budget:
        key = max(PERIODS, key=lambda split: eligible_counts[split] - quotas[split])
        if quotas[key] >= eligible_counts[key]:
            break
        quotas[key] += 1

    output_root.mkdir(parents=True, exist_ok=False)
    task_outputs = {}
    for task_name in TASKS:
        task_output = output_root / task_name
        task_output.mkdir()
        partition_specs, sample_counts = {}, {}
        for split in PERIODS:
            selected = (
                prepared.filter(F.col("__source_split") == split)
                .orderBy(
                    F.xxhash64(F.lit(SEED), "trip_id", "stop_event_id"),
                    "trip_id", "stop_event_id",
                )
                .limit(quotas[split])
                .collect()
            )
            if len(selected) != quotas[split]:
                raise ValueError(f"Deterministic sample mismatch in {split}")
            target_path = task_output / f"{split}.jsonl"
            with target_path.open("x", encoding="utf-8", newline="\n") as stream:
                for row in selected:
                    start = _iso(row["actual_departure_utc"])
                    end = start
                    record = {
                        "case_id": f"{len(str(row['trip_id']))}:{row['trip_id']}{row['stop_event_id']}",
                        "service_date": _iso(row["service_date"]),
                        "target_start": start,
                        "target_end": end,
                        "feature_cutoff_at": _iso(row["__cutoff"]),
                        "value_available_at": _iso(row["value_available_at"]),
                        "target_available_at": _iso(row["__occupancy_available_at"]),
                        "operational_departure_id": row["operational_departure_id"],
                        "source_trip_id": row["trip_id"],
                        "route_id": row["route_id"],
                        "route_stop_id": row["route_stop_id"],
                        "source_stop_event_id": row["stop_event_id"],
                        "source_feature_task": SOURCE_TASK,
                        "historical_occupancy_available_at": _iso(
                            row["historical_occupancy_available_at"]
                        ),
                        "feature_availability_policy": (
                            "Schedule/route/stop metadata is included only when its source "
                            "availability is no later than scheduled departure. The predictor "
                            "lag is the immediately preceding scheduled same-route-stop event; "
                            "if its real outcome was not available before this cutoff, the "
                            "target row is excluded rather than backfilled. "
                            "Current event load, actual departure, capacity, utilization, "
                            "delay and crowding status are excluded from predictors."
                        ),
                        "scheduled_hour_utc": row["scheduled_hour_utc"],
                        "service_weekday": float(row["service_weekday"]),
                        "is_weekend": row["is_weekend"],
                        "service_month": row["service_month"],
                        "direction_id": row["direction_id"],
                        "distance_km": row["distance_km"],
                        "stop_sequence": row["stop_sequence"],
                        "historical_occupancy_ratio_lag_1": row["historical_occupancy_ratio_lag_1"],
                        "historical_onboard_departure_lag_1": row["historical_onboard_departure_lag_1"],
                        "occupancy_target": row["__ratio"],
                        "crowding_target": int(row["over_capacity"]),
                        "target": (
                            int(row["over_capacity"])
                            if task_name == "crowding_risk"
                            else row["__ratio"]
                        ),
                        "occupancy_status": "AVAILABLE",
                        "capacity_snapshot": float(row["capacity_snapshot"]),
                    }
                    if not all(
                        isinstance(record[name], (int, float))
                        and math.isfinite(record[name])
                        for name in TASKS[task_name]["features"]
                    ):
                        raise ValueError(f"Nonfinite predictor in {split} handoff")
                    stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
            sample_counts[split] = len(selected)
            partition_specs[split] = {
                "path": target_path.name,
                "sha256": _sha256(target_path),
                "source_hdfs_path": source_paths[split],
                "source_success_marker": f"{source_paths[split]}/_SUCCESS",
                "source_stage": source_stages[split]["stage"],
                "source_rows": source_rows[split],
                "eligible_rows": eligible_counts[split],
                "sample_rows": len(selected),
            }
        class_counts = {
            split: {
                str(label): sum(
                    1 for line in (task_output / f"{split}.jsonl").read_text().splitlines()
                    if json.loads(line)["target"] == label
                )
                for label in (0, 1)
            }
            for split in PERIODS
        } if task_name == "crowding_risk" else {}
        provenance = {
            "dataset_version": "production-v1.1",
            "producer": "spark",
            "spark_run_id": run_id,
            "source_feature_task": SOURCE_TASK,
            "source_feature_base": source_base,
            "source_evidence_path": evidence_path,
            "assignment_availability_source": {
                "path": str(assignments_path),
                "input_files": assignment_hashes,
                "availability_policy": (
                    "Maximum of announced_at_utc, row value_available_at, and any "
                    "correction_time for the exact ACTUAL departure assignment; only "
                    "VALID rows whose effective interval contains actual departure qualify."
                ),
            },
            "source_row_counts": source_rows,
            "pre_event_eligible_counts": eligible_counts,
            "sample_counts": sample_counts,
            "source_date_ranges": source_bounds,
            "sample_policy": (
                "Deterministic xxhash64 selection by trip_id and stop_event_id within "
                "the original chronological split; no labels or target values were created."
            ),
            "sample_seed": SEED,
            "max_rows_total": max_rows,
            "same_event_target_availability_must_follow_cutoff": True,
            "history_scope": "same route_stop_id, latest real occupancy outcome available by issue time",
            "crowding_threshold": {
                "ratio": 1.0,
                "comparison": ">",
                "source": "existing spark_jobs.features.build_occupancy_features over_capacity definition",
            },
            "source_dataset_certification": "REQUIRES_REVALIDATION",
            "source_quality_notice": (
                "The handoff certificate covers package integrity and temporal checks only; "
                "the underlying production-v1.1 dataset remains REQUIRES_REVALIDATION."
            ),
            "source_feature_stage_evidence": {
                split: {
                    key: source_stages[split].get(key)
                    for key in ("stage", "status", "output", "rows", "elapsed_sec")
                }
                for split in PERIODS
            },
        }
        task_outputs[task_name] = _write_package(
            task_output, task_name, provenance, partition_specs,
            sample_counts, class_counts,
        )
    print(json.dumps({
        "status": "OCCUPANCY_HANDOFFS_VALIDATED",
        "manifests": {key: str(value) for key, value in task_outputs.items()},
        "eligible_rows": eligible_counts,
        "sample_rows": quotas,
    }, sort_keys=True), flush=True)
    return task_outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-base", required=True)
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--assignments-path", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--max-rows", type=int, default=100000)
    args = parser.parse_args()
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.appName("urbantransit-occupancy-ml-handoff").getOrCreate()
    try:
        export_occupancy(
            spark=spark,
            source_base=args.source_base,
            evidence_path=args.evidence_path,
            run_id=args.run_id,
            assignments_path=Path(args.assignments_path),
            output_root=Path(args.output_root),
            max_rows=args.max_rows,
        )
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

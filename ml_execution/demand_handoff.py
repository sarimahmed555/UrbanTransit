"""Export bounded demand-regression input from completed Spark feature outputs."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

from ml_execution.contracts import PERIODS, contract_digest, load_features, validate_manifest
from ml_execution.models import SEED

TASK = "passenger_demand"
SOURCE_TASK = "demand_forecast"
FEATURES = [
    "scheduled_hour_utc",
    "service_weekday",
    "is_weekend",
    "service_month",
    "historical_demand_lag_1",
    "historical_demand_mean_7",
]
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
    records = []
    for line in spark.sparkContext.textFile(evidence_path).collect():
        if line.strip():
            record = json.loads(line)
            if record.get("run_id") == run_id and record.get("stage") == f"features_{SOURCE_TASK}_{split}":
                records.append(record)
    matches = [
        row for row in records
        if row.get("status") == "SUCCEEDED" and row.get("output") == output
    ]
    if not matches:
        raise ValueError(f"No successful source evidence for {split}: {output}")
    return matches[-1]


def _exists(spark, uri: str) -> bool:
    path = spark._jvm.org.apache.hadoop.fs.Path(uri)
    filesystem = path.getFileSystem(spark._jsc.hadoopConfiguration())
    return bool(filesystem.exists(path))


def export_demand(*, spark, source_base: str, evidence_path: str, run_id: str,
                  output: Path, max_rows: int = 100000):
    from pyspark.sql import functions as F

    if max_rows < len(PERIODS):
        raise ValueError("max_rows must allow at least one case in every split")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite handoff: {output}")
    if spark.conf.get("spark.sql.session.timeZone") != "UTC":
        raise ValueError("Spark session timezone must be UTC for exact timestamp export")

    source_paths = {
        split: f"{source_base.rstrip('/')}/task={SOURCE_TASK}/split={split}"
        for split in PERIODS
    }
    eligible = {}
    source_rows = {}
    source_bounds = {}
    source_stages = {}
    eligible_counts = {}
    for split, path in source_paths.items():
        marker = f"{path}/_SUCCESS"
        if not _exists(spark, marker):
            raise FileNotFoundError(f"Completed source feature marker missing: {marker}")
        source_stages[split] = _source_record(
            spark, evidence_path, run_id, split, path
        )
        frame = spark.read.parquet(path)
        missing = sorted({
            "service_date", "trip_id", "operational_departure_id",
            "scheduled_departure_utc", "feature_cutoff_at",
            "known_schedule_available_at", "target_available_at",
            "passenger_demand", *FEATURES,
        } - set(frame.columns))
        if missing:
            raise ValueError(f"{split} Spark demand output missing columns: {missing}")
        source_rows[split] = frame.count()
        if source_rows[split] != source_stages[split].get("rows"):
            raise ValueError(f"Spark source evidence row count differs for {split}")
        bounds = frame.agg(
            F.min("service_date").alias("min_date"),
            F.max("service_date").alias("max_date"),
        ).first()
        if bounds.min_date is None:
            raise ValueError(f"Empty source demand partition: {split}")
        min_date, max_date = bounds.min_date.isoformat(), bounds.max_date.isoformat()
        if min_date < PERIODS[split]["start"] or max_date > PERIODS[split]["end"]:
            raise ValueError(f"Source {split} contains dates outside the approved period")
        source_bounds[split] = {"min": min_date, "max": max_date}

        valid = (
            F.col("service_date").isNotNull()
            & F.col("trip_id").isNotNull()
            & F.col("operational_departure_id").isNotNull()
            & F.col("scheduled_departure_utc").isNotNull()
            & F.col("feature_cutoff_at").isNotNull()
            & F.col("known_schedule_available_at").isNotNull()
            & F.col("target_available_at").isNotNull()
            & (F.col("feature_cutoff_at") == F.col("scheduled_departure_utc"))
            & (F.col("known_schedule_available_at") <= F.col("feature_cutoff_at"))
            & (F.col("scheduled_departure_utc") >= F.to_timestamp(F.lit(SPLIT_START_UTC[split])))
            & (F.col("scheduled_departure_utc") < F.to_timestamp(F.lit(SPLIT_END_UTC[split])))
            & (F.col("target_available_at") > F.col("scheduled_departure_utc"))
            & (F.col("target_available_at") <= F.to_timestamp(F.lit(SPLIT_END_UTC[split])))
            & F.col("passenger_demand").isNotNull()
            & (F.col("passenger_demand") >= 0)
        )
        for feature in FEATURES:
            value = F.col(feature).cast("double")
            valid = valid & F.col(feature).isNotNull() & ~F.isnan(value) & (
                F.abs(value) < F.lit(float("inf"))
            )
        eligible_frame = frame.filter(valid).select(
            "service_date", "trip_id", "operational_departure_id",
            "scheduled_departure_utc", "feature_cutoff_at",
            "known_schedule_available_at", "target_available_at",
            F.col("scheduled_hour_utc").cast("double").alias("scheduled_hour_utc"),
            F.col("service_weekday").cast("double").alias("service_weekday"),
            F.col("is_weekend").cast("int").cast("double").alias("is_weekend"),
            F.col("service_month").cast("double").alias("service_month"),
            F.col("historical_demand_lag_1").cast("double").alias("historical_demand_lag_1"),
            F.col("historical_demand_mean_7").cast("double").alias("historical_demand_mean_7"),
            F.col("passenger_demand").cast("double").alias("target"),
        ).persist()
        eligible_counts[split] = eligible_frame.count()
        if eligible_counts[split] < 1:
            raise ValueError(f"No complete, as-of eligible demand cases in {split}")
        eligible[split] = eligible_frame
        print(json.dumps({
            "split": split,
            "source_rows": source_rows[split],
            "eligible_rows": eligible_counts[split],
            "excluded_rows": source_rows[split] - eligible_counts[split],
            "service_date_min": min_date,
            "service_date_max": max_date,
            "source_stage": source_stages[split]["stage"],
        }), flush=True)

    available = sum(eligible_counts.values())
    budget = min(max_rows, available)
    quotas = {
        split: max(1, round(budget * eligible_counts[split] / available))
        for split in PERIODS
    }
    while sum(quotas.values()) > budget:
        key = max(quotas, key=lambda split: (quotas[split], split))
        if quotas[key] > 1:
            quotas[key] -= 1
    while sum(quotas.values()) < budget:
        key = max(PERIODS, key=lambda split: eligible_counts[split] - quotas[split])
        if quotas[key] >= eligible_counts[key]:
            break
        quotas[key] += 1

    output.mkdir(parents=True, exist_ok=False)
    partition_specs, sample_counts = {}, {}
    for split in PERIODS:
        frame = eligible[split]
        selected = (
            frame.orderBy(
                F.xxhash64(F.lit(SEED), "trip_id", "operational_departure_id"),
                "trip_id",
            )
            .limit(quotas[split])
            .collect()
        )
        path = output / f"{split}.jsonl"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            for row in selected:
                start = _iso(row["scheduled_departure_utc"])
                end = _iso(row["target_available_at"])
                record = {
                    "case_id": row["trip_id"],
                    "service_date": _iso(row["service_date"]),
                    "target_start": start,
                    "target_end": end,
                    "feature_cutoff_at": _iso(row["feature_cutoff_at"]),
                    "value_available_at": _iso(row["feature_cutoff_at"]),
                    "target_available_at": end,
                    "operational_departure_id": row["operational_departure_id"],
                    "source_trip_id": row["trip_id"],
                    "known_schedule_available_at": _iso(row["known_schedule_available_at"]),
                    "feature_availability_policy": (
                        "Upstream Spark past-only history admits each lag input only when "
                        "its target_available_at is <= feature_cutoff_at; the cutoff is "
                        "the conservative availability upper bound for the full predictor set."
                    ),
                    **{feature: row[feature] for feature in FEATURES},
                    "passenger_demand": row["target"],
                    "target": row["target"],
                }
                stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        if not selected:
            raise ValueError(f"Deterministic sample is empty for {split}")
        partition_specs[split] = {
            "path": path.name,
            "sha256": _sha256(path),
            "source_hdfs_path": source_paths[split],
            "source_success_marker": f"{source_paths[split]}/_SUCCESS",
            "source_stage": source_stages[split]["stage"],
            "source_rows": source_rows[split],
            "eligible_rows": eligible_counts[split],
            "sample_rows": len(selected),
        }
        sample_counts[split] = len(selected)

    manifest = {
        "schema_version": "1.0",
        "task_name": TASK,
        "dataset_version": "production-v1.1",
        "feature_version": f"spark-{run_id}-{TASK}",
        "producer": "spark",
        "source_feature_task": SOURCE_TASK,
        "grain": "one observed trip/service date; served passenger journey count",
        "target_definition": (
            "Existing Spark demand_forecast passenger_demand sum of passenger_count per "
            "trip; no target aggregation was repeated. Feature cutoff is scheduled trip "
            "departure. The target interval ends at the observed maximum target_available_at "
            "for the trip's journey evidence; rows whose outcome availability crosses the "
            "chronological period boundary are excluded."
        ),
        "features": FEATURES,
        "target": "target",
        "baseline_column": "historical_demand_lag_1",
        "baseline_type": "last_observation",
        "forecast_protocol": "rolling_origin_frozen_model",
        "forecast_horizon": "scheduled trip departure to final available counted journey",
        "certification": {
            "status": "CERTIFIED",
            "evidence_path": "feature_certification.json",
            "scope": "bounded ML handoff integrity and temporal-contract validation only",
        },
        "partitions": partition_specs,
        "source_provenance": {
            "dataset_version": "production-v1.1",
            "producer": "spark",
            "spark_run_id": run_id,
            "source_feature_base": source_base,
            "source_evidence_path": evidence_path,
            "source_row_counts": source_rows,
            "eligible_row_counts": eligible_counts,
            "sample_counts": sample_counts,
            "source_date_ranges": source_bounds,
            "sample_policy": (
                "Deterministic Spark xxhash64 ranking of trip_id and operational_departure_id "
                "within each original chronological split; no feature or target derivation."
            ),
            "sample_seed": SEED,
            "max_rows": max_rows,
            "feature_availability_policy": (
                "Spark's past-only history transformation promotes outcome rows into lag "
                "history only when target_available_at <= the current scheduled-departure cutoff."
            ),
            "source_dataset_certification": "REQUIRES_REVALIDATION",
            "source_quality_notice": (
                "The handoff certificate does not certify the underlying production dataset "
                "or override its REQUIRES_REVALIDATION status."
            ),
        },
    }
    validate_manifest(manifest)
    certification = {
        "status": "CERTIFIED",
        "dataset_version": manifest["dataset_version"],
        "feature_version": manifest["feature_version"],
        "task_name": TASK,
        "producer": "spark",
        "partitions": partition_specs,
        "contract_sha256": contract_digest(manifest),
        "certification_scope": manifest["certification"]["scope"],
        "source_provenance": manifest["source_provenance"],
        "source_feature_stage_evidence": {
            split: {
                key: source_stages[split].get(key)
                for key in ("stage", "status", "output", "rows", "elapsed_sec")
            }
            for split in PERIODS
        },
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
    loaded, _, hashes = load_features(manifest_path, max_rows=max_rows)
    print(json.dumps({
        "status": "HANDOFF_VALIDATED",
        "manifest": str(manifest_path),
        "sample_counts": sample_counts,
        "partition_sha256": hashes["partition_sha256"],
        "contract_sha256": certification["contract_sha256"],
        "baseline_column": loaded["baseline_column"],
        "source_dataset_certification": loaded["source_provenance"]["source_dataset_certification"],
    }, sort_keys=True), flush=True)
    return manifest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-base", required=True)
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-rows", type=int, default=100000)
    args = parser.parse_args()
    if args.max_rows < 1:
        parser.error("--max-rows must be positive")
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.appName("urbantransit-demand-handoff-export").getOrCreate()
    try:
        export_demand(
            spark=spark, source_base=args.source_base,
            evidence_path=args.evidence_path, run_id=args.run_id,
            output=args.output, max_rows=args.max_rows,
        )
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

"""Export a bounded ML handoff from completed Spark feature Parquet outputs."""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from ml_execution.contracts import PERIODS, contract_digest, load_features, validate_manifest
from ml_execution.models import SEED

TASK = "delay_severity"
LABELS = ["ON_TIME", "MINOR_DELAY", "MODERATE_DELAY", "MAJOR_DELAY", "SEVERE_DELAY"]
FEATURES = [
    "scheduled_hour_utc",
    "service_weekday",
    "is_weekend",
    "service_month",
    "direction_id",
    "distance_km",
]
SPLIT_END_UTC = {
    "train": "2025-12-31T19:00:00Z",
    "validation": "2026-03-31T19:00:00Z",
    "test": "2026-06-30T19:00:00Z",
}
SPLIT_START_UTC = {
    "train": "2024-12-31T19:00:00Z",
    "validation": "2025-12-31T19:00:00Z",
    "test": "2026-03-31T19:00:00Z",
}


def _allocate(counts: dict[str, int], budget: int, *, minimum: int = 0) -> dict[str, int]:
    """Largest-remainder allocation with optional one-per-group representation."""
    budget = min(budget, sum(counts.values()))
    result = {key: min(value, minimum) for key, value in counts.items()}
    remaining = budget - sum(result.values())
    residual = {key: counts[key] - result[key] for key in counts}
    residual_total = sum(residual.values())
    if remaining <= 0 or residual_total == 0:
        return result
    exact = {key: remaining * value / residual_total for key, value in residual.items()}
    for key, value in exact.items():
        result[key] += min(value, math.floor(exact[key]))
    remainder = budget - sum(result.values())
    ranked = sorted(
        residual,
        key=lambda key: (-(exact[key] - math.floor(exact[key])), str(key)),
    )
    for key in ranked:
        if remainder == 0:
            break
        if result[key] < counts[key]:
            result[key] += 1
            remainder -= 1
    return result


def _iso(value) -> str:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, str):
        return value[:10]
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
            if record.get("run_id") == run_id and record.get("stage") == f"features_{TASK}_{split}":
                records.append(record)
    matches = [r for r in records if r.get("status") == "SUCCEEDED" and r.get("output") == output]
    if not matches:
        raise ValueError(f"No successful source evidence for {split}: {output}")
    return matches[-1]


def _hdfs_exists(spark, uri: str) -> bool:
    path = spark._jvm.org.apache.hadoop.fs.Path(uri)
    filesystem = path.getFileSystem(spark._jsc.hadoopConfiguration())
    return bool(filesystem.exists(path))


def _source_frame(spark, path: str, split: str, upper_bound: str):
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window

    frame = spark.read.parquet(path)
    required = {
        "service_date", "trip_id", "stop_event_id", "scheduled_departure_utc",
        "feature_cutoff_at", "known_schedule_available_at", "target_available_at",
        "delay_severity", *FEATURES,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{split} Spark feature output missing columns: {missing}")
    label_index = F.create_map(*[
        item
        for index, label in enumerate(LABELS)
        for item in (F.lit(label), F.lit(index))
    ])
    numeric = [
        "scheduled_hour_utc", "service_weekday", "service_month",
        "direction_id", "distance_km",
    ]
    eligible = frame.filter(
        F.col("delay_severity").isin(LABELS)
        & F.col("service_date").isNotNull()
        & F.col("trip_id").isNotNull()
        & F.col("stop_event_id").isNotNull()
        & F.col("scheduled_departure_utc").isNotNull()
        & F.col("feature_cutoff_at").isNotNull()
        & F.col("known_schedule_available_at").isNotNull()
        & F.col("target_available_at").isNotNull()
        & (F.col("feature_cutoff_at") == F.col("scheduled_departure_utc"))
        & (F.col("known_schedule_available_at") <= F.col("feature_cutoff_at"))
        & (F.col("scheduled_departure_utc") >= F.to_timestamp(F.lit(SPLIT_START_UTC[split])))
        & (F.col("scheduled_departure_utc") < F.to_timestamp(F.lit(upper_bound)))
        & (F.col("target_available_at") >= F.col("scheduled_departure_utc"))
        & (F.col("target_available_at") <= F.to_timestamp(F.lit(upper_bound)))
    )
    for name in numeric:
        value = F.col(name).cast("double")
        eligible = eligible.filter(
            F.col(name).isNotNull()
            & ~F.isnan(value)
            & (F.abs(value) < F.lit(float("inf")))
        )
    eligible = eligible.select(
        "service_date", "trip_id", "stop_event_id", "scheduled_departure_utc",
        "feature_cutoff_at", "known_schedule_available_at", "target_available_at",
        label_index[F.col("delay_severity")].cast("int").alias("target"),
        *[F.col(name).cast("double").alias(name) for name in numeric],
        F.col("is_weekend").cast("int").alias("is_weekend"),
    ).persist()
    class_counts = {
        row["target"]: row["count"]
        for row in eligible.groupBy("target").count().collect()
    }
    count = sum(class_counts.values())
    rank_window = Window.partitionBy("target").orderBy(
        F.xxhash64(F.lit(SEED), "trip_id", "stop_event_id"),
        F.col("trip_id"), F.col("stop_event_id"),
    )
    return eligible, count, {LABELS[key]: value for key, value in class_counts.items()}, rank_window


def export_handoff(*, spark, source_base: str, evidence_path: str, run_id: str,
                   output: Path, max_rows: int = 100000):
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window

    if max_rows < len(PERIODS) + len(LABELS):
        raise ValueError("max_rows is too small to represent every split and class")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite handoff: {output}")
    if spark.conf.get("spark.sql.session.timeZone") != "UTC":
        raise ValueError("Spark session timezone must be UTC for exact timestamp export")

    source_paths = {
        split: f"{source_base.rstrip('/')}/task={TASK}/split={split}"
        for split in PERIODS
    }
    stages, eligible, counts, label_counts = {}, {}, {}, {}
    total_source = {}
    for split, path in source_paths.items():
        marker = f"{path}/_SUCCESS"
        if not _hdfs_exists(spark, marker):
            raise FileNotFoundError(f"Completed source feature marker missing: {marker}")
        stages[split] = _source_record(spark, evidence_path, run_id, split, path)
        frame = spark.read.parquet(path)
        total_source[split] = frame.count()
        if total_source[split] != stages[split].get("rows"):
            raise ValueError(f"Spark source evidence row count differs for {split}")
        bounds = frame.agg(
            F.min("service_date").alias("min_date"),
            F.max("service_date").alias("max_date"),
        ).first()
        if bounds.min_date is None:
            raise ValueError(f"Empty source feature partition: {split}")
        low, high = PERIODS[split]["start"], PERIODS[split]["end"]
        min_date, max_date = _iso(bounds.min_date), _iso(bounds.max_date)
        if min_date < low or max_date > high:
            raise ValueError(f"Source {split} contains service dates outside {low}..{high}")
        eligible[split], counts[split], label_counts[split], _ = _source_frame(
            spark, path, split, SPLIT_END_UTC[split]
        )
        print(json.dumps({
            "split": split,
            "source_rows": total_source[split],
            "eligible_rows": counts[split],
            "class_counts": label_counts[split],
            "service_date_min": min_date,
            "service_date_max": max_date,
            "source_stage": stages[split]["stage"],
        }), flush=True)

    if not counts["train"] or not all(counts[s] for s in ("validation", "test")):
        raise ValueError("Each chronological partition needs eligible target rows")
    supported_labels = [
        label for label in LABELS
        if all(label in label_counts[split] for split in PERIODS)
    ]
    split_label_sets = {
        split: {label for label, count in label_counts[split].items() if count}
        for split in PERIODS
    }
    if len(supported_labels) < 2 or any(
        labels != set(supported_labels) for labels in split_label_sets.values()
    ):
        raise ValueError(
            f"Eligible target classes differ between partitions: {split_label_sets}"
        )
    if supported_labels != LABELS[:len(supported_labels)]:
        raise ValueError(
            "Observed severity labels are not a contiguous prefix of the source vocabulary"
        )

    budget = min(max_rows, sum(counts.values()))
    split_budget = _allocate(counts, budget, minimum=1)
    output.mkdir(parents=True, exist_ok=False)
    partition_specs, sample_counts, source_counts = {}, {}, {}
    for split in PERIODS:
        class_budget = _allocate(
            label_counts[split], split_budget[split],
            minimum=1 if split == "train" else 0,
        )
        frame = eligible[split]
        rank_window = Window.partitionBy("target").orderBy(
            F.xxhash64(F.lit(SEED), "trip_id", "stop_event_id"),
            F.col("trip_id"), F.col("stop_event_id"),
        )
        quota = F.create_map(*[
            item
            for label, count in class_budget.items()
            for item in (F.lit(LABELS.index(label)), F.lit(int(count)))
        ])
        selected = (
            frame.withColumn("__rank", F.row_number().over(rank_window))
            .filter(F.col("__rank") <= quota[F.col("target")])
            .drop("__rank")
            .orderBy("service_date", "scheduled_departure_utc", "stop_event_id")
            .collect()
        )
        if len(selected) != split_budget[split]:
            raise ValueError(f"Deterministic sample size mismatch for {split}")
        path = output / f"{split}.jsonl"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            for row in selected:
                timestamp = _iso(row["scheduled_departure_utc"])
                trip_id = str(row["trip_id"])
                stop_event_id = str(row["stop_event_id"])
                record = {
                    "case_id": f"{len(trip_id)}:{trip_id}{stop_event_id}",
                    "source_stop_event_id": stop_event_id,
                    "service_date": _iso(row["service_date"]),
                    "target_start": timestamp,
                    "target_end": timestamp,
                    "feature_cutoff_at": _iso(row["feature_cutoff_at"]),
                    "value_available_at": _iso(row["known_schedule_available_at"]),
                    "target_available_at": _iso(row["target_available_at"]),
                    "source_departure_ids": [row["trip_id"]],
                    **{name: row[name] for name in FEATURES},
                    "target": row["target"],
                }
                stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        partition_specs[split] = {
            "path": path.name,
            "sha256": _sha256(path),
            "source_hdfs_path": source_paths[split],
            "source_success_marker": f"{source_paths[split]}/_SUCCESS",
            "source_stage": stages[split]["stage"],
            "source_rows": total_source[split],
            "eligible_rows": counts[split],
            "sample_rows": len(selected),
            "eligible_class_counts": label_counts[split],
            "sample_class_counts": dict(Counter(LABELS[row["target"]] for row in selected)),
        }
        sample_counts[split] = len(selected)
        source_counts[split] = total_source[split]

    manifest = {
        "schema_version": "1.0",
        "task_name": TASK,
        "dataset_version": "production-v1.1",
        "feature_version": f"spark-{run_id}-{TASK}",
        "producer": "spark",
        "grain": "one observed trip stop event from completed Spark task output",
        "target_definition": (
            "Existing Spark delay_severity output; labels encoded by declared class index "
            "without changing the upstream label. Target is the instantaneous scheduled "
            "departure event. Thresholds are the source run's 60, 300, 600, and 1800 seconds. "
            "Only labels with eligible observations in all three splits are modeled; "
            "unsupported output labels are listed explicitly."
        ),
        "features": FEATURES,
        "target": "target",
        "class_labels": supported_labels,
        "unsupported_source_labels": [
            label for label in LABELS if label not in supported_labels
        ],
        "thresholds": {"delay_seconds": [60.0, 300.0, 600.0, 1800.0]},
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
            "source_row_counts": source_counts,
            "eligible_row_counts": counts,
            "sample_policy": (
                "Deterministic Spark xxhash64 ranking of trip_id and stop_event_id within source "
                "label and chronological split; proportional split and class allocation; "
                "no label resynthesis or feature calculation."
            ),
            "sample_seed": SEED,
            "max_rows": max_rows,
            "unsupported_source_labels": [
                label for label in LABELS if label not in supported_labels
            ],
            "source_dataset_certification": "REQUIRES_REVALIDATION",
            "source_quality_notice": (
                "This attestation does not certify the underlying production dataset or "
                "override its REQUIRES_REVALIDATION status."
            ),
        },
    }
    validate_manifest(manifest)
    cert = {
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
                key: stages[split].get(key)
                for key in ("stage", "status", "output", "rows", "date_min", "date_max", "elapsed_sec")
            }
            for split in PERIODS
        },
        "sample_counts": sample_counts,
        "label_encoding": {label: index for index, label in enumerate(supported_labels)},
    }
    (output / "feature_certification.json").write_text(
        json.dumps(cert, indent=2, sort_keys=True, allow_nan=False) + "\n",
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
        "contract_sha256": cert["contract_sha256"],
        "row_validation": "PASSED",
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
    spark = SparkSession.builder.appName("urbantransit-ml-handoff-export").getOrCreate()
    try:
        export_handoff(
            spark=spark, source_base=args.source_base,
            evidence_path=args.evidence_path, run_id=args.run_id,
            output=args.output, max_rows=args.max_rows,
        )
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

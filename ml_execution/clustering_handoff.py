"""Build chronological route snapshots from completed Spark feature outputs."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from ml_execution.contracts import PERIODS, contract_digest, load_features, validate_manifest

ROUTE_RUN_ID = "spark-prod-v11-route-clustering-20260928T1443Z-01"
OCCUPANCY_RUN_ID = "spark-prod-v11-occupancy-20260928T1124Z-4c9e1f"
DEMAND_RUN_ID = "spark-prod-v11-20260928T0944Z-1a7c3f"
FEATURES = [
    "mean_observed_load",
    "mean_capacity_utilization",
    "mean_delay_sec",
    "mean_route_distance_km",
    "mean_abs_headway_deviation_sec",
    "distinct_stops",
    "mean_served_demand_per_trip",
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


def export_clustering(*, spark, route_train_path: str, occupancy_base: str,
                      demand_base: str, output: Path, max_rows: int = 100000):
    from pyspark.sql import functions as F
    from spark_jobs.features import build_headway_features

    if output.exists():
        raise FileExistsError(f"Refusing to overwrite clustering handoff: {output}")
    if spark.conf.get("spark.sql.session.timeZone") != "UTC":
        raise ValueError("Spark session timezone must be UTC for exact timestamp export")

    train_path = f"{route_train_path.rstrip('/')}/"
    train_marker = train_path.rstrip("/") + "/_SUCCESS"
    if not _exists(spark, train_marker):
        raise FileNotFoundError(f"Completed route training marker missing: {train_marker}")
    train = spark.read.parquet(train_path)
    required_train = {"route_id", "direction_id", *FEATURES}
    if required_train - set(train.columns):
        raise ValueError(f"Route training profile lacks fields: {sorted(required_train-set(train.columns))}")
    train_profiles = train.select("route_id", "direction_id", *FEATURES).withColumn(
        "__split", F.lit("train")
    )

    occupancy_sources, demand_sources, profiles = {}, {}, {"train": train_profiles}
    for split, period in PERIODS.items():
        occ_path = f"{occupancy_base.rstrip('/')}/task=occupancy/split={split}"
        demand_path = f"{demand_base.rstrip('/')}/task=demand_forecast/split={split}"
        if not _exists(spark, f"{occ_path}/_SUCCESS"):
            raise FileNotFoundError(f"Completed occupancy marker missing: {occ_path}/_SUCCESS")
        if not _exists(spark, f"{demand_path}/_SUCCESS"):
            raise FileNotFoundError(f"Completed demand marker missing: {demand_path}/_SUCCESS")
        operations = spark.read.parquet(occ_path)
        required_occupancy = {
            "service_date", "route_id", "direction_id", "stop_id",
            "operational_departure_id", "trip_id", "stop_event_id",
            "scheduled_departure_utc", "actual_departure_utc", "onboard_departure",
            "capacity_utilization", "distance_km",
        }
        if required_occupancy - set(operations.columns):
            raise ValueError(
                f"Occupancy {split} lacks cluster source fields: "
                f"{sorted(required_occupancy-set(operations.columns))}"
            )
        high = F.to_timestamp(F.lit(SPLIT_END_UTC[split]))
        operations = operations.filter(
            F.col("actual_departure_utc").isNotNull()
            & F.col("scheduled_departure_utc").isNotNull()
            & (F.greatest(
                F.col("event_value_available_at"),
                F.col("outcome_available_at_utc"),
            ) <= high)
        )
        headways = build_headway_features(operations).withColumn(
            "__delay",
            F.greatest(
                F.lit(0.0),
                F.col("actual_departure_utc").cast("long")
                - F.col("scheduled_departure_utc").cast("long"),
            ),
        ).withColumn(
            "__abs_headway", F.abs(F.col("headway_deviation_sec"))
        )
        operational_profiles = headways.groupBy("route_id", "direction_id").agg(
            F.avg("onboard_departure").alias("mean_observed_load"),
            F.avg("capacity_utilization").alias("mean_capacity_utilization"),
            F.avg("__delay").alias("mean_delay_sec"),
            F.avg("distance_km").alias("mean_route_distance_km"),
            F.avg("__abs_headway").alias("mean_abs_headway_deviation_sec"),
            F.countDistinct("stop_id").cast("double").alias("distinct_stops"),
        )
        demand = spark.read.parquet(demand_path)
        required_demand = {
            "route_id", "direction_id", "trip_id", "passenger_demand",
            "target_available_at", "scheduled_departure_utc",
        }
        if required_demand - set(demand.columns):
            raise ValueError(
                f"Demand {split} lacks cluster source fields: "
                f"{sorted(required_demand-set(demand.columns))}"
            )
        demand_profiles = demand.filter(
            F.col("route_id").isNotNull()
            & F.col("direction_id").isNotNull()
            & F.col("trip_id").isNotNull()
            & F.col("passenger_demand").isNotNull()
            & F.col("target_available_at").isNotNull()
            & (F.col("target_available_at") <= high)
            & (F.col("scheduled_departure_utc") >= F.to_timestamp(F.lit(SPLIT_START_UTC[split])))
            & (F.col("scheduled_departure_utc") < high)
        ).groupBy("route_id", "direction_id").agg(
            F.countDistinct("trip_id").alias("__supported_demand_trips"),
            F.avg("passenger_demand").alias("mean_served_demand_per_trip"),
        )
        profile = operational_profiles.join(
            demand_profiles, ["route_id", "direction_id"], "inner"
        ).select(
            "route_id", "direction_id", *[F.col(name) for name in FEATURES]
        ).withColumn("__split", F.lit(split))
        for name in FEATURES:
            value = F.col(name).cast("double")
            profile = profile.filter(
                F.col(name).isNotNull() & ~F.isnan(value)
                & (F.abs(value) < F.lit(float("inf")))
            )
        if profile.limit(1).count() == 0:
            raise ValueError(f"No fully supported, finite route profiles for {split}")
        profiles[split] = profile
        occupancy_sources[split] = occ_path
        demand_sources[split] = demand_path
        print(json.dumps({
            "split": split,
            "profile_rows": profile.count(),
            "occupancy_rows": operations.count(),
            "demand_profile_rows": demand_profiles.count(),
            "period_start": period["start"],
            "period_end": period["end"],
        }), flush=True)

    counts = {split: profiles[split].count() for split in PERIODS}
    if sum(counts.values()) > max_rows:
        quotas = {split: max(1, round(max_rows * counts[split] / sum(counts.values()))) for split in PERIODS}
    else:
        quotas = counts
    output.mkdir(parents=True, exist_ok=False)
    partitions = {}
    for split in PERIODS:
        start = dt.datetime.fromisoformat(SPLIT_START_UTC[split].replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(SPLIT_END_UTC[split].replace("Z", "+00:00"))
        selected = (
            profiles[split]
            .orderBy(F.xxhash64("route_id", "direction_id"), "route_id", "direction_id")
            .limit(quotas[split])
            .collect()
        )
        path = output / f"{split}.jsonl"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            for row in selected:
                features = {name: float(row[name]) for name in FEATURES}
                if not all(math.isfinite(value) for value in features.values()):
                    raise ValueError(f"Nonfinite route profile in {split}")
                route_id, direction = str(row["route_id"]), int(row["direction_id"])
                case_id = f"{split}:{route_id}:{direction}"
                stream.write(json.dumps({
                    "case_id": case_id,
                    "route_id": route_id,
                    "direction_id": float(direction),
                    "service_date": PERIODS[split]["start"],
                    "target_start": _iso(start),
                    "target_end": _iso(end),
                    "feature_cutoff_at": _iso(end),
                    "value_available_at": _iso(end),
                    "target_available_at": _iso(end),
                    "feature_availability_policy": (
                        "Fitted training profiles use only existing 2025 route-clustering "
                        "output. Validation/test vectors aggregate their own later-period "
                        "occupancy/delay/headway and served-demand observations, complete "
                        "and available by that period's end. No labels/archetypes are injected."
                    ),
                    **features,
                }, sort_keys=True, allow_nan=False) + "\n")
        partitions[split] = {
            "path": path.name,
            "sha256": _sha256(path),
            "source_rows": counts[split],
            "sample_rows": len(selected),
            "source_hdfs_paths": (
                [train_path.rstrip("/")]
                if split == "train"
                else [occupancy_sources[split], demand_sources[split]]
            ),
        }

    manifest = {
        "schema_version": "1.0",
        "task_name": "route_clustering",
        "dataset_version": "production-v1.1",
        "feature_version": "spark-route-clustering-temporal-prod-v11-20260928",
        "producer": "spark",
        "grain": "one route and direction profile aggregated within one chronological period",
        "target_definition": (
            "Unsupervised route operational profile; no generated cluster labels or "
            "predefined archetypes. Fit the scaler and K-means candidates using 2025 "
            "profiles only, then apply them unchanged to 2026-Q1 and 2026-Q2 profiles."
        ),
        "features": FEATURES,
        "partitions": partitions,
        "certification": {
            "status": "CERTIFIED",
            "evidence_path": "feature_certification.json",
            "scope": "bounded handoff integrity, chronology, and temporal holdout provenance",
        },
        "source_provenance": {
            "dataset_version": "production-v1.1",
            "producer": "spark",
            "training_profile_run_id": ROUTE_RUN_ID,
            "occupancy_run_id": OCCUPANCY_RUN_ID,
            "demand_run_id": DEMAND_RUN_ID,
            "training_profile_path": train_path.rstrip("/"),
            "occupancy_source_paths": occupancy_sources,
            "demand_source_paths": demand_sources,
            "temporal_evaluation": "train-fit; validation-select; test-held-out",
            "feature_definitions": {
                "mean_observed_load": "mean available onboard_departure observations",
                "mean_capacity_utilization": "mean available observed capacity_utilization",
                "mean_delay_sec": "mean max(0, actual_departure-scheduled_departure)",
                "mean_route_distance_km": "mean observed route-stop distance_km",
                "mean_abs_headway_deviation_sec": "mean absolute actual-minus-scheduled headway deviation",
                "distinct_stops": "count of distinct observed stop_id values",
                "mean_served_demand_per_trip": "mean existing passenger_demand labels with availability within the period",
            },
            "source_dataset_certification": "REQUIRES_REVALIDATION",
            "source_quality_notice": (
                "Handoff certification binds package hashes and temporal policy only; "
                "production-v1.1 remains REQUIRES_REVALIDATION."
            ),
        },
    }
    validate_manifest(manifest)
    certification = {
        "status": "CERTIFIED",
        "dataset_version": manifest["dataset_version"],
        "feature_version": manifest["feature_version"],
        "task_name": manifest["task_name"],
        "producer": manifest["producer"],
        "partitions": partitions,
        "contract_sha256": contract_digest(manifest),
        "certification_scope": manifest["certification"]["scope"],
        "source_provenance": manifest["source_provenance"],
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
    loaded, loaded_partitions, hashes = load_features(manifest_path, max_rows=max_rows)
    print(json.dumps({
        "status": "HANDOFF_VALIDATED",
        "task": loaded["task_name"],
        "manifest": str(manifest_path),
        "profile_rows": {split: len(values) for split, values in loaded_partitions.items()},
        "partition_sha256": hashes["partition_sha256"],
        "contract_sha256": certification["contract_sha256"],
        "source_dataset_certification": loaded["source_provenance"]["source_dataset_certification"],
    }, sort_keys=True), flush=True)
    return manifest_path


def _exists(spark, uri: str) -> bool:
    path = spark._jvm.org.apache.hadoop.fs.Path(uri)
    filesystem = path.getFileSystem(spark._jsc.hadoopConfiguration())
    return bool(filesystem.exists(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-train-path", required=True)
    parser.add_argument("--occupancy-base", required=True)
    parser.add_argument("--demand-base", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-rows", type=int, default=100000)
    args = parser.parse_args()
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.appName("urbantransit-route-clustering-handoff").getOrCreate()
    try:
        export_clustering(
            spark=spark,
            route_train_path=args.route_train_path,
            occupancy_base=args.occupancy_base,
            demand_base=args.demand_base,
            output=Path(args.output),
            max_rows=args.max_rows,
        )
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

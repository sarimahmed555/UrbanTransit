"""Main Spark integration pipeline entry point; no work occurs on import."""

from __future__ import annotations

import argparse
import uuid

from pyspark.sql import SparkSession

from feature_contracts import validate_severity_thresholds
from spark_jobs.contracts import SparkPaths, TABLES, require_hdfs_uri
from spark_jobs.integration import register_views, build_integrated_views
from spark_jobs.features import build_feature_frames_from_tables, write_feature_splits
from spark_jobs.ingest import read_certified_tables
from spark_jobs.output_safety import (
    assert_spark_path_absent,
    reserve_spark_output,
    versioned_spark_path,
)
from spark_jobs.quality import apply_hooks
from spark_jobs.runtime import EvidenceLogger


FEATURE_TASKS = (
    "delay_severity", "demand_forecast", "occupancy",
    "headway", "delay_analytics", "route_clustering",
)
OPERATION_FEATURE_TABLES = (
    "context_events", "delays", "passenger_counts", "route_patterns",
    "route_stops", "routes", "schedule_stop_times", "schedules",
    "trip_stop_events", "trip_vehicle_assignments", "trips", "vehicles",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--certified-root", required=True)
    parser.add_argument("--hdfs-root", required=True, type=require_hdfs_uri)
    parser.add_argument("--hdfs-input-tables", nargs="*", choices=TABLES, default=[])
    parser.add_argument("--evidence-root", default=None)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--feature-task", choices=FEATURE_TASKS)
    parser.add_argument("--feature-splits", nargs="+", choices=("train", "validation", "test"))
    parser.add_argument("--resume-feature-task", action="store_true")
    parser.add_argument(
        "--severity-thresholds-sec", required=True, nargs=4, type=float,
        metavar=("ON_TIME", "MINOR", "MODERATE", "MAJOR"),
        help="Increasing delay-severity boundaries in seconds",
    )
    args = parser.parse_args()
    if args.resume_feature_task and not args.feature_task:
        parser.error("--resume-feature-task requires --feature-task")
    args.severity_thresholds_sec = validate_severity_thresholds(args.severity_thresholds_sec)
    args.run_id = args.run_id or str(uuid.uuid4())
    run_root = versioned_spark_path(
        args.hdfs_root,
        stage="spark_pipeline",
        dataset_version=args.dataset_version,
        run_id=args.run_id,
    )
    paths = SparkPaths(run_root)
    spark = SparkSession.builder.appName("urbantransit-spark-pipeline").getOrCreate()
    evidence = args.evidence_root or paths.evidence
    logger = EvidenceLogger(
        spark,
        evidence,
        pipeline_id="spark_integration_features_v1",
        run_id=args.run_id,
    )
    try:
        selected_tasks = (args.feature_task,) if args.feature_task else FEATURE_TASKS
        selected_splits = tuple(args.feature_splits or ("train", "validation", "test"))
        destinations = [
            ("evidence", f"{evidence}/run_id={args.run_id}"),
        ]
        if not args.resume_feature_task:
            destinations.append(("dq_issues", f"{paths.curated}/dq_issues"))
        for task in selected_tasks:
            if task == "route_clustering":
                destinations.append(
                    (task, f"{paths.features}/task={task}/training_window=2025")
                )
            else:
                destinations.extend(
                    (f"{task}_{split}", f"{paths.features}/task={task}/split={split}")
                    for split in selected_splits
                )
        for stage, output in destinations:
            assert_spark_path_absent(
                spark,
                output,
                stage=stage,
                run_id=args.run_id,
                dataset_version=args.dataset_version,
                resume_reserved=args.resume_feature_task and stage.startswith(
                    f"{args.feature_task}_"
                ),
            )
        for stage, output in destinations:
            reserve_spark_output(
                spark,
                output,
                stage=stage,
                run_id=args.run_id,
                dataset_version=args.dataset_version,
                resume_reserved=args.resume_feature_task and stage.startswith(
                    f"{args.feature_task}_"
                ),
            )
        with logger.stage("ingest") as record:
            tables_to_read = (
                OPERATION_FEATURE_TABLES
                if args.resume_feature_task and args.feature_task in {
                    "delay_severity", "occupancy", "headway", "delay_analytics"
                }
                else None
            )
            tables = read_certified_tables(
                spark, args.certified_root,
                tables=tables_to_read or TABLES,
                table_roots={name: args.hdfs_root for name in args.hdfs_input_tables},
            )
            record["tables"] = sorted(tables)
            record["hdfs_input_tables"] = args.hdfs_input_tables
            record["severity_thresholds_sec"] = args.severity_thresholds_sec
            record["spark_version"] = spark.version
        if not args.resume_feature_task:
            # A count alone lets Spark prune CSV fields and miss invalid typed values.
            from pyspark.sql import functions as F
            for name, frame in tables.items():
                with logger.stage(f"validate_schema_{name}") as record:
                    stats = frame.agg(
                        F.count("*").alias("rows"),
                        F.sum(F.xxhash64(*frame.columns).cast("decimal(38,0)"))
                        .alias("typed_checksum"),
                    ).first()
                    record["rows"] = stats.rows
                    record["typed_checksum"] = str(stats.typed_checksum)
                    record["schema"] = frame.schema.jsonValue()
        checked, dq = apply_hooks(tables, logger)
        if not args.resume_feature_task:
            with logger.stage("data_quality", f"{paths.curated}/dq_issues") as record:
                dq.write.mode("errorifexists").parquet(f"{paths.curated}/dq_issues")
                record["issues"] = spark.read.parquet(f"{paths.curated}/dq_issues").count()
                record["policy"] = "Flag and audit; preserve source rows without silent repair"
            register_views(checked)
            for name, frame in build_integrated_views(spark).items():
                with logger.stage(f"spark_sql_{name}") as record:
                    record["rows"] = frame.count()
                    record["schema"] = frame.schema.jsonValue()
                    record["logical_plan"] = frame._jdf.queryExecution().analyzed().toString()
        with logger.stage("build_features"):
            feature_frames = build_feature_frames_from_tables(
                checked,
                severity_thresholds_sec=args.severity_thresholds_sec,
                tasks=selected_tasks,
            )
        for task, frame in feature_frames.items():
            if task == "route_clustering":
                output = f"{paths.features}/task={task}/training_window=2025"
                with logger.stage(f"features_{task}", output) as record:
                    frame.write.mode("errorifexists").parquet(output)
                    logger.record_count(record, frame)
                    record["schema"] = frame.schema.jsonValue()
                continue
            write_feature_splits(
                frame, paths.features, logger, task=task, splits=selected_splits
            )
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

"""Certified-package ingestion and typed staging."""

from __future__ import annotations

from .contracts import TABLES, SparkPaths, validate_certified_input
from .output_safety import reserve_spark_output
from .schemas import raw_schema_for


def read_certified_tables(spark, certified_root: str, *, tables=TABLES, table_roots=None):
    """Read CSV structure strictly; preserve timestamps for explicit DQ casting."""
    result = {}
    table_roots = table_roots or {}
    for table in tables:
        result[table] = (
            spark.read.schema(raw_schema_for(table))
            .option("header", "true")
            .option("pathGlobFilter", "{*.csv,*.csv.gz}")
            .option("enforceSchema", "false")
            .option("nullValue", "\\N")
            .option("mode", "FAILFAST")
            .option("quote", chr(34))
            .option("escape", chr(34))
            .csv(f"{table_roots.get(table, certified_root)}/raw/{table}")
        )
    return result


def write_staging(
    tables: dict,
    paths: SparkPaths,
    logger,
    *,
    dataset_version: str,
    run_id: str,
) -> dict:
    staged = {}
    for name, frame in tables.items():
        output = f"{paths.staging}/{name}"
        reserve_spark_output(
            logger.spark,
            output,
            stage=f"staging_{name}",
            run_id=run_id,
            dataset_version=dataset_version,
        )
        with logger.stage(f"stage_{name}", output) as record:
            frame.write.mode("errorifexists").partitionBy("service_date").parquet(output)
            logger.record_count(record, frame)
        staged[name] = output
    return staged


def validate_input_args(dataset_root: str, certification_marker: str):
    return validate_certified_input(dataset_root, certification_marker)

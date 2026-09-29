"""Spark DQ hooks; they flag and audit, never silently repair source rows."""

from __future__ import annotations

from pyspark.sql import functions as F
from pyspark.sql.types import StringType, TimestampType

from .schemas import schema_for


def apply_hooks(tables: dict, logger) -> tuple[dict, object]:
    issues = []
    checked = {}
    for name, frame in tables.items():
        # Timestamp parsing is a DQ operation, not a structural CSV operation.
        # Retain every source row and the original lexeme beside its typed value.
        casts = {}
        raw_columns = []
        invalid_timestamp = F.lit(False)
        for field in schema_for(name):
            if (not isinstance(field.dataType, TimestampType)
                    or field.name not in frame.columns
                    or not isinstance(frame.schema[field.name].dataType, StringType)):
                continue
            source = F.col(field.name)
            typed = F.expr(f"try_cast(`{field.name}` AS TIMESTAMP)")
            invalid = source.isNotNull() & typed.isNull()
            invalid_timestamp = invalid_timestamp | invalid
            casts[field.name] = typed.alias(field.name)
            raw_columns.append(source.alias(f"raw_timestamp__{field.name}"))
            issues.append(frame.filter(invalid).select(
                F.lit(name).alias("table_name"), F.col("source_row_id"),
                F.lit("INVALID_TIMESTAMP").alias("rule_id"),
                F.lit("FLAGGED").alias("status"),
                F.lit(field.name).alias("column_name"),
                source.alias("raw_value"),
            ))
        current = frame.select(
            *[casts.get(column, F.col(column)) for column in frame.columns],
            *raw_columns,
            invalid_timestamp.alias("__invalid_timestamp"),
        ).withColumn(
            "quality_status",
            F.when(F.col("__invalid_timestamp"), F.lit("FLAGGED"))
            .otherwise(F.col("quality_status")),
        ).drop("__invalid_timestamp")
        checks = [
            ("missing_source_row_id", F.col("source_row_id").isNull()),
            ("invalid_quality_status", F.col("quality_status").isNull() | ~F.col("quality_status").isin("VALID", "ACCEPTED", "FLAGGED", "UNRESOLVED")),
        ]
        if "service_date" in frame.columns:
            checks.append(("missing_service_date", F.col("service_date").isNull()))
        for rule_id, condition in checks:
            issues.append(
                frame.filter(condition).select(
                    F.lit(name).alias("table_name"), F.col("source_row_id"),
                    F.lit(rule_id).alias("rule_id"), F.lit("FLAGGED").alias("status"),
                    F.lit(None).cast("string").alias("column_name"),
                    F.lit(None).cast("string").alias("raw_value")
                )
            )
        checked[name] = current
    dq = issues[0]
    for item in issues[1:]:
        dq = dq.unionByName(item)
    return checked, dq


def assert_no_future_leakage(frame, *, cutoff="feature_cutoff_at",
                             available="value_available_at"):
    violation = frame.filter(F.col(available) > F.col(cutoff)).limit(1).count()
    if violation:
        raise ValueError(f"Feature availability exceeds {cutoff}")
    return frame

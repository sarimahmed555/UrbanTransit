"""Stable evidence vocabulary.

This module describes evidence shape only. It does not assert that any run
has happened or that any metric has passed.
"""

from copy import deepcopy

NOT_RUN = "NOT_RUN"
NOT_READY = "NOT_READY"
READY_STATUSES = frozenset({"READY", "SUCCEEDED", "PASSED"})

EVIDENCE_CATEGORIES = (
    "dataset_certification",
    "hdfs_ingestion",
    "spark_pipeline",
    "python_pipeline",
    "ml_experiment",
    "forecasting",
    "classification",
    "clustering",
    "occupancy_crowding",
    "delay_analytics",
    "pipeline_comparison",
    "performance",
    "api_dashboard",
)

_COMMON = {
    "status": NOT_RUN,
    "recorded_at_utc": None,
    "command": None,
    "dataset_version": None,
    "input_artifact": None,
    "output_artifact": None,
    "metrics": {},
    "notes": [],
}

_SCHEMAS = {
    "dataset_certification": {"certification_status": NOT_READY, "version": None},
    "hdfs_ingestion": {"source": None, "hdfs_paths": [], "row_counts": {}, "timings_sec": {}},
    "spark_pipeline": {
        "input_counts": {},
        "output_counts": {},
        "dq_counts": {},
        "joins": {},
        "feature_outputs": {},
        "runtime_sec": None,
        "resource_capture": {},
    },
    "python_pipeline": {
        "input_counts": {},
        "output_counts": {},
        "dq_counts": {},
        "joins": {},
        "feature_outputs": {},
        "runtime_sec": None,
    },
    "ml_experiment": {
        "model_name": None,
        "parameters": {},
        "periods": {"train": None, "validation": None, "test": None},
        "metrics": {},
        "selected_model": False,
        "selection_rationale": None,
    },
    "forecasting": {"baseline_metrics": {}, "candidate_metrics": {}, "improvement": {}},
    "classification": {"metrics": {"accuracy": None, "f1": None, "precision": None, "recall": None}},
    "clustering": {
        "candidates": [],
        "selected_k": None,
        "selection_evidence": {},
        "silhouette": None,
    },
    "occupancy_crowding": {"metrics": {}, "segment_counts": {}, "threshold_evidence": {}},
    "delay_analytics": {"metrics": {}, "segment_counts": {}, "threshold_evidence": {}},
    "pipeline_comparison": {
        "independent_results": {},
        "comparison_sample_size": None,
        "agreement": {},
        "discrepancies": [],
    },
    "performance": {
        "rows_processed": None,
        "runtime_sec": None,
        "resource_capture": {},
        "scale_target_rows": 10000000,
        "architecture_evidence": [],
    },
    "api_dashboard": {
        "artifact_readiness": {},
        "endpoint_checks": {},
        "dashboard_checks": {},
    },
}


def schema_for(category: str) -> dict:
    if category not in _SCHEMAS:
        raise KeyError(f"Unknown evidence category: {category}")
    schema = deepcopy(_COMMON)
    schema.update(deepcopy(_SCHEMAS[category]))
    return schema


def empty_bundle() -> dict:
    return {
        "schema_version": "1.0",
        "bundle_status": NOT_READY,
        "created_at_utc": None,
        "updated_at_utc": None,
        "provenance": {
            "command": None,
            "dataset_version": None,
            "input_artifact": None,
            "output_artifact": None,
        },
        "evidence": {category: schema_for(category) for category in EVIDENCE_CATEGORIES},
    }

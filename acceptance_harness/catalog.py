"""Traceable groups, not a claim to mechanically cover every clause of the SRS."""
from dataclasses import dataclass, asdict
from spark_jobs.contracts import TABLES
from feature_contracts import CHRONOLOGICAL_SPLITS, FEATURE_CONTRACTS


@dataclass(frozen=True)
class Requirement:
    id: str
    title: str
    reference: str
    evaluator: str
    basis: str = "SRS_MANDATORY"
    evidence_kind: str = "runtime"
    depends_on: tuple = ()
    fields: tuple = ()

    def public(self):
        return asdict(self)


R = Requirement
CATALOG = (
    R("dataset.minimums", "Official dataset minimums", "§1.2 Hint pp.29–30", "minimums"),
    R("dataset.connected", "Connected transport entities and integrity", "§1.2 Hint; Step 1", "flags", fields=("keys_verified", "relationships_verified", "entity_mapping_reviewed", "no_count_inflation")),
    R("dataset.project_tables", "Project's 21-table contract", "DATASET_ARCHITECTURE.md; spark_jobs.contracts", "tables", basis="IMPLEMENTATION_CHOICE"),
    R("processing.2m", "Process at least two million passenger/ticket records", "§1.7(1)", "processing", depends_on=("dataset.minimums",)),
    R("storage.hdfs", "HDFS storage demonstrated", "Step 2; §1.6", "flags", fields=("hdfs_write_verified", "hdfs_read_verified", "inventory_reconciled")),
    R("storage.formats", "CSV, JSON and Parquet use", "Step 2", "formats"),
    R("spark.ingestion", "Spark/PySpark ingestion", "Step 3", "flags", depends_on=("storage.hdfs",), fields=("multiple_files", "explicit_schema", "schema_inference", "large_dataset_loaded")),
    R("spark.sql", "Spark SQL integration and aggregations", "Steps 6–8; §1.6", "flags", depends_on=("spark.ingestion",), fields=("joins_executed", "aggregations_executed", "join_cardinality_verified", "outputs_reconciled")),
    R("storage.parquet", "Large analytical Parquet dataset", "Step 2", "flags", fields=("analytical_dataset_written", "readback_verified", "large_dataset_scope_reviewed")),
    R("quality.cleaning", "DQ detection, cleaning and report", "Steps 4–5", "flags", fields=("dq_report_verified", "defect_detection_verified", "cleaning_executed", "quarantine_audit_verified", "before_after_reconciled")),
    R("splits.chronological", "Chronological separation and leakage prevention", "Step 23; §1.2 Hint", "flags", fields=("train_validation_test_separate", "chronological", "availability_checked", "no_future_leakage", "preprocessing_train_only", "target_windows_checked")),
    R("splits.project_periods", "2025 / 2026 Q1 / 2026 Q2 periods", "DATASET_ARCHITECTURE.md; feature_contracts", "periods", basis="IMPLEMENTATION_CHOICE"),
    R("features.runtime", "Feature engineering outputs verified", "Step 7; §1.6", "flags", fields=("feature_outputs_verified", "definitions_reviewed", "lineage_verified", "null_handling_verified", "availability_verified")),
    R("features.project_contract", "Current named feature families", "feature_contracts.FEATURE_CONTRACTS", "features", basis="IMPLEMENTATION_CHOICE"),
    R("ml.tasks", "Required predictive/analytical capabilities", "Steps 19–26; §1.6", "tasks", depends_on=("features.runtime", "splits.chronological")),
    R("ml.delay_models", "Three suitable delay models evaluated", "Step 19", "candidates", depends_on=("splits.chronological",)),
    R("ml.spark_models", "Three suitable Spark MLlib algorithms evaluated", "Step 41; §1.10(5)", "spark_candidates", depends_on=("spark.ingestion", "splits.chronological")),
    R("ml.classification", "Classification accuracy or macro F1 target", "§1.7(4)", "classification", depends_on=("splits.chronological",)),
    R("forecast.improvement", "Forecast improves documented baseline", "Step 24; §1.7(4)", "forecast", depends_on=("splits.chronological",)),
    R("forecast.r2", "R² where appropriate", "Step 24", "r2"),
    R("clustering.runtime", "Route clustering and interpretation", "Step 21; §1.6", "clustering"),
    R("occupancy.runtime", "Occupancy forecasting and crowding risk", "Steps 25–26; §1.6", "flags", fields=("occupancy_predictions_evaluated", "crowding_predictions_evaluated", "capacity_eligibility_verified", "thresholds_documented")),
    R("delay.analytics", "Delay analysis and repeated patterns", "Steps 17–18", "flags", fields=("route_trip_stop_vehicle_breakdowns", "time_day_direction_distance_breakdowns", "recurrence_verified", "full_service_denominators")),
    R("python.independent", "Independent Python analytical pipeline", "Step 42; §1.2", "flags", fields=("python_executed", "own_preprocessing", "own_models", "own_predictions", "same_underlying_snapshot")),
    R("comparison.independent", "Dual pipeline comparison on ≥100 unseen cases", "Steps 43–44", "comparison", depends_on=("python.independent", "ml.spark_models")),
    R("recommendations.runtime", "Evidence-based prioritized recommendations", "Steps 45–47", "recommendations"),
    R("scenarios.runtime", "What-if analysis and labelled estimates", "Steps 48–49", "scenarios"),
    R("scenarios.project_coverage", "All eight selected scenario examples", "Step 48 examples; recommendation_engine contract", "scenario_inventory", basis="IMPLEMENTATION_CHOICE"),
    R("api.runtime", "Application/API integration", "§1.2; §1.6; Steps 50–59", "flags", fields=("endpoint_tests_executed", "certified_artifacts_served", "not_ready_behavior_verified", "request_validation_verified")),
    R("security.runtime", "Authentication and four-role access control", "§1.6(i–ii)", "security"),
    R("database.runtime", "Application storage runtime", "Step 2 where required; §1.6 management functions", "flags", fields=("storage_connected", "schema_applied", "write_read_roundtrip", "parameterized_access_verified")),
    R("dashboard.performance", "Standard dashboards within five seconds", "§1.7(1)", "dashboard"),
    R("scale.architecture", "Architecture capable of ≥10M without redesign", "§1.7(2)", "scale", evidence_kind="review"),
    R("availability.runtime", "At least 99% evaluation uptime", "§1.7(5)", "uptime"),
    R("reproducibility.runtime", "Reproducible artifacts and provenance", "§1.8; §1.10 deliverables", "flags", fields=("source_revision_recorded", "dataset_version_recorded", "parameters_seeds_recorded", "environment_recorded", "artifact_hashes_recorded", "rerun_verified")),
    R("testing.runtime", "Testing evidence and outcomes", "§1.10(9)", "testing"),
    R("documentation.ai_usage", "Reviewed AI-use disclosure", "§1.8(14); §1.10(16)", "flags", evidence_kind="review", fields=("ai_usage_reviewed", "tools_purposes_files_disclosed", "team_verification_recorded", "no_external_generative_predictions")),
    R("development.git", "Meaningful five-day commits and team contributions", "§1.8(1–2); §1.10", "git", evidence_kind="review"),
    R("development.log", "Development log completeness", "§1.8; development evidence", "flags", evidence_kind="review", fields=("log_reviewed", "work_and_validation_traceable", "team_contributions_reviewed")),
    R("documentation.runbook", "README/install/run documentation", "§1.10(10–11)", "flags", evidence_kind="review", fields=("readme_reviewed", "installation_reviewed", "execution_reviewed", "limitations_reviewed", "instructions_verified")),
    R("submission.artifacts", "Required final submission package", "§1.10(17)", "submission", evidence_kind="review"),
    R("submission.video", "Mandatory MP4 demonstration", "§1.10(14)", "video", evidence_kind="review"),
    R("submission.blog", "Published technical blog ≥2000 words", "§1.10(15)", "blog", evidence_kind="review"),
    R("srs.remaining_review", "Review all remaining mandatory clauses", "§1.6/1.7 blanket MUST; §1.8/1.10", "flags", evidence_kind="review", fields=("full_srs_traceability_reviewed", "unencoded_mandatory_clauses_verified", "remaining_ambiguities_resolved")),
)
BY_ID = {r.id: r for r in CATALOG}
FINAL_ARTIFACTS = (
    "project_report", "public_github_url", "complete_source", "data_generation_scripts", "big_data_dataset",
    "data_dictionary", "hdfs_scripts", "spark_jobs", "spark_sql_files", "parquet_data", "spark_models",
    "python_models", "dual_pipeline_comparison_report", "transport_intelligence_report", "installation_instructions",
    "execution_instructions", "deployment_url", "demonstration_video", "technical_blog", "ai_usage", "team_contribution_record",
)
TASKS = {"delay_severity", "passenger_demand", "route_clustering", "occupancy_forecast", "crowding_risk"}
SCENARIOS = {"increase_frequency", "decrease_frequency", "add_vehicle", "change_vehicle_capacity",
             "shift_trip_start_time", "remove_low_demand_trip", "add_new_stop", "increase_predicted_demand"}

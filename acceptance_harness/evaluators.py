"""Pure checks of measured facts; absence is distinct from observed failure."""
import math
from .catalog import TABLES, CHRONOLOGICAL_SPLITS, FEATURE_CONTRACTS, TASKS, SCENARIOS, FINAL_ARTIFACTS


class MissingEvidence(ValueError):
    pass


class FailedEvidence(ValueError):
    pass


def field(facts, name):
    if name not in facts or facts[name] is None:
        raise MissingEvidence(f"Missing evidence field: {name}")
    return facts[name]


def number(facts, name, *, minimum=None, maximum=None, integer=False):
    v = field(facts, name)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (integer and int(v) != v):
        raise FailedEvidence(f"Invalid measured number: {name}")
    if minimum is not None and v < minimum or maximum is not None and v > maximum:
        raise FailedEvidence(f"{name}={v} violates required bounds [{minimum}, {maximum}]")
    return v


def flags(facts, names):
    for name in names:
        if field(facts, name) is not True:
            raise FailedEvidence(f"Required verification failed: {name}")


def measured_items(facts, key):
    items = field(facts, key)
    if not isinstance(items, list) or not items:
        raise MissingEvidence(f"Nonempty measured {key} required")
    return items


def evaluate(req, f):
    kind = req.evaluator
    if kind == "flags":
        flags(f, req.fields)
    elif kind == "minimums":
        limits = {"ticket_or_movement_records": 2000000, "trip_level_passenger_records": 500000,
                  "routes": 100, "stops": 500, "vehicles": 250, "unique_passengers": 50000,
                  "historical_months": 12, "delay_records": 250000, "service_calendars": 2, "schedules": 2}
        flags(f, ("counts_reconciled", "unique_entity_counts", "movement_channels_not_double_counted"))
        for name, low in limits.items():
            number(f, name, minimum=low, integer=True)
    elif kind == "tables":
        if not set(TABLES) <= set(field(f, "tables")):
            raise FailedEvidence("Project table inventory incomplete")
        flags(f, ("foreign_keys_verified",))
    elif kind == "processing":
        number(f, "rows_processed", minimum=2000000, integer=True)
        number(f, "runtime_sec", minimum=0.000001)
        flags(f, ("passenger_or_ticket_scope", "no_duplicated_counting"))
    elif kind == "formats":
        if not {"CSV", "JSON", "Parquet"} <= set(field(f, "formats_verified")):
            raise FailedEvidence("Required storage formats not demonstrated")
    elif kind == "periods":
        expected = {k: list(v) for k, v in CHRONOLOGICAL_SPLITS.items()}
        if field(f, "periods_exclusive_end") != expected:
            raise FailedEvidence("Project chronological periods differ")
    elif kind == "features":
        if not set(FEATURE_CONTRACTS) <= set(field(f, "verified_families")):
            raise FailedEvidence("Project feature families incomplete")
    elif kind == "tasks":
        items = measured_items(f, "tasks")
        passed = {i["task_name"] for i in items if i.get("status") == "SUCCEEDED" and i.get("output_verified") is True}
        if not TASKS <= passed:
            raise FailedEvidence(f"Missing measured tasks: {sorted(TASKS-passed)}")
    elif kind in {"candidates", "spark_candidates"}:
        items = measured_items(f, "model_comparison")
        suitable = [i for i in items if i.get("status") == "SUCCEEDED" and i.get("suitability_reviewed") is True]
        for i in suitable:
            number(i, "validation_score")
            field(i, "parameters")
        identity = "algorithm" if kind == "spark_candidates" else "model_name"
        if len({field(i, identity) for i in suitable}) < 3:
            raise FailedEvidence(f"Fewer than three suitable measured candidates by {identity}")
        if kind == "spark_candidates" and field(f, "engine") != "spark":
            raise FailedEvidence("Spark MLlib evidence must be independently executed with Spark")
        if kind == "candidates" and field(f, "task_family") != "delay":
            raise FailedEvidence("Delay-model comparison must concern delay prediction")
        flags(f, ("selection_validation_only", "test_not_used_for_tuning"))
    elif kind == "classification":
        if field(f, "split") != "test":
            raise FailedEvidence("Classification threshold requires held-out TEST evidence")
        m = field(f, "metrics")
        for metric in ("accuracy", "precision", "recall", "f1", "macro_f1"):
            number(m, metric, minimum=0, maximum=1)
        matrix = field(m, "confusion_matrix")
        if not isinstance(matrix, list) or not matrix or any(not isinstance(row, list) or len(row) != len(matrix) for row in matrix):
            raise FailedEvidence("Invalid confusion matrix")
        if any(type(x) is not int or x < 0 for row in matrix for x in row):
            raise FailedEvidence("Confusion matrix requires nonnegative integer counts")
        n = sum(map(sum, matrix))
        if n < 1 or number(m, "sample_count", minimum=1, integer=True) != n:
            raise FailedEvidence("Confusion matrix sample counts do not reconcile")
        accuracy = sum(matrix[i][i] for i in range(len(matrix)))/n
        macro = sum(2*matrix[i][i]/(sum(matrix[i])+sum(row[i] for row in matrix))
                    if sum(matrix[i])+sum(row[i] for row in matrix) else 0 for i in range(len(matrix)))/len(matrix)
        if not math.isclose(accuracy, m["accuracy"], abs_tol=1e-6) or not math.isclose(macro, m["macro_f1"], abs_tol=1e-6):
            raise FailedEvidence("Reported classification metrics disagree with confusion matrix")
        if not (accuracy >= .85 or macro >= .80):
            raise FailedEvidence("Neither accuracy >=0.85 nor macro F1 >=0.80 met")
        if accuracy < .85:
            flags(f, ("macro_f1_applicability_reviewed",))
    elif kind == "forecast":
        flags(f, ("baseline_documented", "selection_validation_only", "test_not_used_for_tuning"))
        if field(f, "split") != "test":
            raise FailedEvidence("Forecast improvement requires held-out TEST evidence")
        baseline, selected = field(f, "baseline_metrics"), field(f, "selected_metrics")
        metric = field(f, "primary_error_metric")
        if metric not in {"mae", "rmse"}:
            raise MissingEvidence("Declare a reviewed comparable MAE/RMSE criterion; SRS does not specify a margin")
        for m in (baseline, selected):
            number(m, "mae", minimum=0)
            number(m, "rmse", minimum=0)
            number(m, "sample_count", minimum=1, integer=True)
        if field(baseline, "case_ids_sha256") != field(selected, "case_ids_sha256") or baseline["sample_count"] != selected["sample_count"]:
            raise FailedEvidence("Baseline and selected model evaluated on different cases")
        if selected[metric] >= baseline[metric]:
            raise FailedEvidence("Selected forecast does not improve baseline")
        flags(f, ("zero_demand_policy_documented", "forecast_protocol_documented"))
    elif kind == "r2":
        variance = number(f, "target_variance", minimum=0)
        n = number(f, "sample_count", minimum=1, integer=True)
        if variance == 0 or n < 2:
            return "NOT_APPLICABLE", "R² undefined for constant targets or fewer than two cases; measured scope only"
        number(f, "r2")
    elif kind == "clustering":
        flags(f, ("preprocessing_train_only", "parameters_recorded", "assignments_saved", "interpretation_saved"))
        number(f, "occupied_clusters", minimum=2, integer=True)
        if "silhouette" in f and f["silhouette"] is not None:
            number(f, "silhouette", minimum=-1, maximum=1)
        else:
            field(f, "alternative_quality_measure")
            number(f, "quality_score")
            flags(f, ("quality_applicability_reviewed",))
    elif kind == "comparison":
        number(f, "unseen_case_count", minimum=100, integer=True)
        flags(f, ("same_underlying_snapshot", "independent_preprocessing", "independent_models", "independent_predictions", "truth_locked", "case_ids_reconciled", "disagreements_explained"))
        number(f, "agreement_rate", minimum=0, maximum=1)
        if field(f, "python_prediction_sha256") == field(f, "spark_prediction_sha256"):
            # Equal predictions can be valid. Their producing runs must still be independent.
            flags(f, ("identical_output_independence_reviewed",))
        if field(f, "python_run_id") == field(f, "spark_run_id"):
            raise FailedEvidence("Independent runs cannot share a run ID")
    elif kind == "recommendations":
        flags(f, ("rules_executed", "negative_controls_verified", "priorities_impact_severity_based", "no_generative_output"))
        recommendations = field(f, "recommendations")
        if not isinstance(recommendations, list):
            raise FailedEvidence("Recommendations must be a measured list")
        if not recommendations:
            flags(f, ("no_action_evidence_reviewed",))
        for i in recommendations:
            for key in ("action", "entity_ids", "reason", "supporting_metrics", "source_analytical_artifact", "generated_at", "provenance"):
                if not field(i, key):
                    raise FailedEvidence(f"Empty recommendation evidence: {key}")
            if field(i, "priority") not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
                raise FailedEvidence("Unknown recommendation priority")
    elif kind == "scenarios":
        items = measured_items(f, "scenarios")
        flags(f, ("required_capability_scope_reviewed",))
        for i in items:
            if field(i, "result_type") != "ESTIMATE":
                raise FailedEvidence("Scenario output is not labelled ESTIMATE")
            for key in ("entity_ids", "original_values", "proposed_changes", "assumptions", "baseline_metrics", "estimated_metrics", "deltas", "evidence_source", "limitations"):
                field(i, key)
            flags(i, ("calculations_verified",))
    elif kind == "scenario_inventory":
        if not SCENARIOS <= set(field(f, "executed_scenario_types")):
            raise FailedEvidence("Project scenario execution coverage incomplete")
    elif kind == "security":
        if set(field(f, "roles_tested")) != {"Administrator", "Operator", "Analyst", "Evaluator"}:
            raise FailedEvidence("Four-role coverage incomplete")
        flags(f, ("deployed_http_tests", "valid_login", "invalid_credentials_rejected", "expiry_rejected", "inactive_rejected", "forbidden_access_rejected", "missing_auth_rejected", "secrets_redacted", "production_persistence", "https_verified"))
    elif kind == "dashboard":
        flags(f, ("processed_datasets_prepared", "standard_dashboard_coverage_reviewed", "measurement_protocol_reviewed"))
        times = measured_items(f, "response_times_sec")
        for t in times:
            number({"response": t}, "response", minimum=0, maximum=5)
    elif kind == "scale":
        number(f, "target_passenger_movement_records", minimum=10000000, integer=True)
        flags(f, ("architecture_reviewed", "resource_capacity_plan_verified", "bottlenecks_assessed", "no_complete_redesign_required"))
        field(f, "review_basis")
    elif kind == "uptime":
        duration = number(f, "evaluation_duration_sec", minimum=1)
        available = number(f, "available_sec", minimum=0, maximum=duration)
        flags(f, ("normal_conditions_scope_reviewed",))
        if available/duration < .99:
            raise FailedEvidence("Evaluation availability below 99%")
    elif kind == "testing":
        number(f, "executed_tests", minimum=1, integer=True)
        if number(f, "failed_tests", minimum=0, integer=True) != 0:
            raise FailedEvidence("Unresolved test failures")
        flags(f, ("mandatory_test_scope_reviewed", "skipped_requirements_not_counted_pass", "results_traceable"))
    elif kind == "git":
        if len(set(field(f, "competition_days_with_meaningful_commits"))) < 5:
            raise FailedEvidence("Meaningful commits not evidenced across five competition days")
        flags(f, ("competition_window_verified", "all_team_members_contributed", "meaningfulness_reviewed"))
    elif kind == "submission":
        artifacts = field(f, "artifacts")
        for name in FINAL_ARTIFACTS:
            a = field(artifacts, name)
            field(a, "reference")
            flags(a, ("content_reviewed", "accessible_verified"))
        flags(f, ("credentials_delivered_securely",))
    elif kind == "video":
        if field(f, "format").lower() != "mp4":
            raise FailedEvidence("Mandatory demonstration is not MP4")
        flags(f, ("playback_verified", "mandatory_demonstrations_reviewed"))
    elif kind == "blog":
        number(f, "word_count", minimum=2000, integer=True)
        flags(f, ("public_access_verified", "required_topics_reviewed"))
    else:
        raise MissingEvidence("No safe automatic evaluator; human review required")
    return "PASS", "Supplied evidence satisfies the encoded criterion; code presence was not used"

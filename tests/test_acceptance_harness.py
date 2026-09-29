"""Tiny synthetic evidence only; never opens project runtime/production artifacts."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy

from acceptance_harness.catalog import BY_ID, CATALOG, FINAL_ARTIFACTS
from acceptance_harness.engine import assess
from acceptance_harness.evaluators import evaluate, MissingEvidence, FailedEvidence
from acceptance_harness.report import markdown

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-09-26T00:00:00Z"


def proof(facts, *, scope="FIXTURE", kind="runtime", status="SUCCEEDED"):
    return {"status": status, "evidence_scope": scope, "facts": facts,
            "provenance": {"run_id": "synthetic-run", "command": "fixture-only",
                           "recorded_at_utc": STAMP, "source_revision": "fixture-revision",
                           "dataset_version": "fixture-dataset", "reviewer": "fixture-reviewer",
                           "reviewed_at_utc": STAMP, "review_reference": "fixture-review",
                           "artifact_references": ["fixture-artifact"]}}


def publish(root, records):
    artifact = root/"evidence.json"
    artifact.write_text(json.dumps(records))
    manifest = {"schema_version": "1.0", "dataset_version": "fixture-dataset", "source_revision": "fixture-revision",
                "checks": {key: {"path": artifact.name, "pointer": "/"+key,
                                 "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()} for key in records}}
    path = root/"manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def rows(report):
    return {r["id"]: r for r in report["checks"]}


def minimums():
    return {"ticket_or_movement_records": 2000000, "trip_level_passenger_records": 500000,
            "routes": 100, "stops": 500, "vehicles": 250, "unique_passengers": 50000,
            "historical_months": 12, "delay_records": 250000, "service_calendars": 2,
            "schedules": 2, "counts_reconciled": True, "unique_entity_counts": True,
            "movement_channels_not_double_counted": True}


class HarnessTests(unittest.TestCase):
    def test_absence_is_pending_not_pass_or_failure(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            r = assess(Path(tmp)/"missing.json", project_root=tmp, generated_at=STAMP)
            self.assertEqual(r["overall_state"], "PENDING_RUNTIME")
            self.assertEqual(r["counts"]["PASS"], 0)
            self.assertEqual(r["counts"]["PENDING_RUNTIME"], len(CATALOG))
            self.assertTrue(r["actions"])

    def test_code_ready_flag_and_fixture_cannot_pass_real_acceptance(self):
        for status, scope in (("READY", "CERTIFIED_RUNTIME"), ("SUCCEEDED", "FIXTURE"), ("SUCCEEDED", "CODE_EXISTS")):
            with self.subTest(status=status, scope=scope), tempfile.TemporaryDirectory(dir=ROOT) as tmp:
                path = publish(Path(tmp), {"dataset.minimums": proof(minimums(), status=status, scope=scope)})
                r = assess(path, project_root=tmp, generated_at=STAMP)
                self.assertEqual(rows(r)["dataset.minimums"]["state"], "PENDING_RUNTIME")

    def test_fixture_rows_are_never_overall_acceptance(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path = publish(Path(tmp), {"dataset.minimums": proof(minimums())})
            r = assess(path, project_root=tmp, fixture=True, generated_at=STAMP)
            self.assertEqual(rows(r)["dataset.minimums"]["state"], "PASS")
            self.assertFalse(rows(r)["dataset.minimums"]["acceptance_eligible"])
            self.assertEqual(r["scope"], "FIXTURE_ONLY")
            self.assertEqual(r["overall_state"], "PENDING_RUNTIME")

    def test_numeric_failure_blocks_downstream(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            facts = minimums()
            facts["ticket_or_movement_records"] -= 1
            path = publish(Path(tmp), {"dataset.minimums": proof(facts)})
            r = assess(path, project_root=tmp, fixture=True, generated_at=STAMP)
            self.assertEqual(rows(r)["dataset.minimums"]["state"], "FAIL")
            self.assertEqual(rows(r)["processing.2m"]["state"], "BLOCKED")
            self.assertTrue(r["blockers"])

    def test_hash_mismatch_and_version_mismatch_are_blockers(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root = Path(tmp)
            path = publish(root, {"dataset.minimums": proof(minimums())})
            (root/"evidence.json").write_text("{}")
            self.assertEqual(rows(assess(path, project_root=tmp, fixture=True))["dataset.minimums"]["state"], "BLOCKED")
            record = proof(minimums())
            record["provenance"]["dataset_version"] = "different"
            path = publish(root, {"dataset.minimums": record})
            self.assertEqual(rows(assess(path, project_root=tmp, fixture=True))["dataset.minimums"]["state"], "BLOCKED")

    def test_no_provenance_no_pass(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            record = proof(minimums())
            record["provenance"] = {}
            path = publish(Path(tmp), {"dataset.minimums": record})
            self.assertEqual(rows(assess(path, project_root=tmp, fixture=True))["dataset.minimums"]["state"], "PENDING_RUNTIME")

    def test_readonly_and_repeatable_summary(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root = Path(tmp)
            path = publish(root, {"dataset.minimums": proof(minimums())})
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            r = assess(path, project_root=tmp, fixture=True, generated_at=STAMP)
            self.assertEqual(r, assess(path, project_root=tmp, fixture=True, generated_at=STAMP))
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            summary = markdown(r)
            self.assertIn("FIXTURE_ONLY", summary)
            self.assertIn("PENDING_RUNTIME", summary)
            self.assertIn("Missing evidence", summary)
            json.dumps(r, allow_nan=False)

    def test_disallowed_path_and_unknown_requirement_block(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            self.assertEqual(assess(Path(tmp)/"raw_data"/"x.json", project_root=tmp)["overall_state"], "BLOCKED")
            path = publish(Path(tmp), {"invented.requirement": proof({})})
            self.assertEqual(assess(path, project_root=tmp)["overall_state"], "BLOCKED")

    def test_evidence_failure_and_missing_pointer(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path = publish(Path(tmp), {"dataset.minimums": proof({}, status="FAILED")})
            r = assess(path, project_root=tmp, fixture=True)
            self.assertEqual(rows(r)["dataset.minimums"]["state"], "FAIL")
            manifest = json.loads(path.read_text())
            manifest["checks"]["dataset.minimums"]["pointer"] = "/missing"
            path.write_text(json.dumps(manifest))
            self.assertEqual(rows(assess(path, project_root=tmp, fixture=True))["dataset.minimums"]["state"], "BLOCKED")


class CriterionTests(unittest.TestCase):
    def test_srs_choices_are_separate(self):
        for key in ("dataset.project_tables", "splits.project_periods", "features.project_contract", "scenarios.project_coverage"):
            self.assertEqual(BY_ID[key].basis, "IMPLEMENTATION_CHOICE")
        self.assertEqual(BY_ID["scale.architecture"].evidence_kind, "review")
        self.assertEqual(BY_ID["srs.remaining_review"].basis, "SRS_MANDATORY")

    def test_bool_nan_and_infinity_never_become_numeric_pass(self):
        for bad in (True, float("nan"), float("inf")):
            f = minimums()
            f["routes"] = bad
            with self.assertRaises(FailedEvidence):
                evaluate(BY_ID["dataset.minimums"], f)

    def test_classifier_threshold_and_matrix_consistency(self):
        f = {"split": "test", "metrics": {"accuracy": 1., "precision": 1., "recall": 1., "f1": 1., "macro_f1": 1.,
                                          "confusion_matrix": [[2, 0], [0, 2]], "sample_count": 4}}
        self.assertEqual(evaluate(BY_ID["ml.classification"], f)[0], "PASS")
        f["metrics"]["accuracy"] = .99
        with self.assertRaises(FailedEvidence):
            evaluate(BY_ID["ml.classification"], f)
        for k in ("accuracy", "precision", "recall", "f1", "macro_f1"):
            f["metrics"][k] = .5
        f["metrics"]["confusion_matrix"] = [[1, 1], [1, 1]]
        with self.assertRaises(FailedEvidence):
            evaluate(BY_ID["ml.classification"], f)

    def test_forecast_improvement_not_tie_or_different_cases(self):
        f = {"baseline_documented": True, "selection_validation_only": True, "test_not_used_for_tuning": True,
             "split": "test", "primary_error_metric": "mae", "zero_demand_policy_documented": True, "forecast_protocol_documented": True,
             "baseline_metrics": {"mae": 2., "rmse": 3., "sample_count": 3, "case_ids_sha256": "a"*64},
             "selected_metrics": {"mae": 1., "rmse": 2., "sample_count": 3, "case_ids_sha256": "a"*64}}
        self.assertEqual(evaluate(BY_ID["forecast.improvement"], f)[0], "PASS")
        for value in (2., 3.):
            f["selected_metrics"]["mae"] = value
            with self.assertRaises(FailedEvidence):
                evaluate(BY_ID["forecast.improvement"], f)
        f["selected_metrics"]["mae"] = 1
        f["selected_metrics"]["case_ids_sha256"] = "b"*64
        with self.assertRaises(FailedEvidence):
            evaluate(BY_ID["forecast.improvement"], f)

    def test_conditional_r2_only_when_mathematically_undefined(self):
        self.assertEqual(evaluate(BY_ID["forecast.r2"], {"sample_count": 10, "target_variance": 0})[0], "NOT_APPLICABLE")
        with self.assertRaises(MissingEvidence):
            evaluate(BY_ID["forecast.r2"], {"sample_count": 10, "target_variance": 1})

    def test_three_distinct_suitable_measured_algorithms(self):
        f = {"engine": "spark", "selection_validation_only": True, "test_not_used_for_tuning": True,
             "model_comparison": [{"algorithm": n, "status": "SUCCEEDED", "suitability_reviewed": True,
                                   "validation_score": .5, "parameters": {"seed": 1}} for n in ("a", "b", "c")]}
        self.assertEqual(evaluate(BY_ID["ml.spark_models"], f)[0], "PASS")
        f["model_comparison"][2]["algorithm"] = "a"
        with self.assertRaises(FailedEvidence):
            evaluate(BY_ID["ml.spark_models"], f)

    def test_dashboard_and_architecture_do_not_invent_10m_runtime(self):
        f = {"processed_datasets_prepared": True, "standard_dashboard_coverage_reviewed": True,
             "measurement_protocol_reviewed": True, "response_times_sec": [1, 5]}
        self.assertEqual(evaluate(BY_ID["dashboard.performance"], f)[0], "PASS")
        f["response_times_sec"].append(5.01)
        with self.assertRaises(FailedEvidence):
            evaluate(BY_ID["dashboard.performance"], f)
        scale = {"target_passenger_movement_records": 10000000, "architecture_reviewed": True,
                 "resource_capacity_plan_verified": True, "bottlenecks_assessed": True,
                 "no_complete_redesign_required": True, "review_basis": "synthetic capacity assessment"}
        self.assertEqual(evaluate(BY_ID["scale.architecture"], scale)[0], "PASS")

    def test_independent_comparison_requires_100_not_1000(self):
        req = BY_ID["comparison.independent"]
        f = dict.fromkeys(("same_underlying_snapshot", "independent_preprocessing", "independent_models", "independent_predictions",
                           "truth_locked", "case_ids_reconciled", "disagreements_explained"), True)
        f.update(unseen_case_count=100, agreement_rate=.2, python_prediction_sha256="a"*64,
                 spark_prediction_sha256="b"*64, python_run_id="python", spark_run_id="spark")
        self.assertEqual(evaluate(req, f)[0], "PASS")
        f["unseen_case_count"] = 99
        with self.assertRaises(FailedEvidence):
            evaluate(req, f)

    def test_submission_requires_content_review_not_file_presence(self):
        f = {"artifacts": {name: {"reference": "fixture", "content_reviewed": True, "accessible_verified": True} for name in FINAL_ARTIFACTS},
             "credentials_delivered_securely": True}
        self.assertEqual(evaluate(BY_ID["submission.artifacts"], f)[0], "PASS")
        del f["artifacts"]["project_report"]["content_reviewed"]
        with self.assertRaises(MissingEvidence):
            evaluate(BY_ID["submission.artifacts"], f)


if __name__ == "__main__":
    unittest.main()

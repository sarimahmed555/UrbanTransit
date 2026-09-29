"""Tiny synthetic fixtures only. No production files or Spark sessions are read/run."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta

from ml_execution.analytics import silhouette, cluster_interpretation, service_analytics
from ml_execution.contracts import PERIODS, NotReady, load_features, pending, validate_manifest, validate_rows
from ml_execution.metrics import classification, regression, risk_metrics, compare_baseline, select_model, classification_acceptance
from ml_execution.models import candidates
from ml_execution.results import read_result, validate_result
from ml_execution.runner import execute


def fixture(task="passenger_demand"):
    m = {"schema_version": "1.0", "task_name": task, "dataset_version": "tiny-synthetic-v1",
         "feature_version": "tiny-features-v1", "producer": "python", "certification": {"status": "FIXTURE"},
         "features": ["historic_value", "known_time"], "grain": "synthetic route-hour",
         "target_definition": "synthetic next-hour value", "target": "label", "partitions": {s: {} for s in PERIODS},
         "baseline_column": "historic_value", "baseline_type": "seasonal_naive",
         "forecast_protocol": "rolling_origin_frozen_model", "forecast_horizon": "one hour",
         "class_labels": ["on_time", "late"], "thresholds": {"late_seconds": 300}, "risk_probability_threshold": .6,
         "analytics_policy": {"early_tolerance_sec": 60, "late_tolerance_sec": 300,
                              "recurrence_min_days": 2, "occupancy_bands": [.4, .8, 1.0]}}
    data = {}
    for split, day in (("train", "2025-02-03"), ("validation", "2026-02-03"), ("test", "2026-05-03")):
        data[split] = []
        for i in range(12):
            row = {"case_id": f"{split}-{i}", "service_date": day,
                   "target_start": day+"T08:00:00+05:00", "target_end": day+"T09:00:00+05:00",
                   "feature_cutoff_at": day+"T07:00:00+05:00", "value_available_at": day+"T06:00:00+05:00",
                   "target_available_at": day+"T09:00:00+05:00", "operational_departure_id": f"departure-{split}-{i}",
                   "historic_value": float(i+1), "known_time": float(i % 3), "label": float(i+2),
                   "route_id": f"r{i%3}", "trip_id": f"t{i}", "stop_id": f"s{i%2}",
                   "direction_id": 0, "weekday": 1, "event_hour": 8, "distance_band": "short",
                   "departure_vehicle_id": "v1", "departure_assignment_status": "KNOWN",
                   "delay_status": "AVAILABLE", "signed_departure_delay_sec": i*100,
                   "occupancy_status": "AVAILABLE", "capacity_snapshot": 40, "capacity_utilization": i/10,
                   "service_status": "OBSERVED"}
            if task in {"delay_severity", "crowding_risk"}:
                row["label"] = i % 2
            if task in {"route_clustering", "delay_service_analytics"}:
                row["feature_cutoff_at"] = day+"T10:00:00+05:00"
            data[split].append(row)
    return m, data


def publish(root, m, data):
    root.mkdir()
    for split, rows in data.items():
        path = root / f"{split}.jsonl"
        path.write_text("".join(json.dumps(r)+"\n" for r in rows))
        m["partitions"][split] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(m))
    return manifest


class ChronologyTests(unittest.TestCase):
    def test_fixed_periods(self):
        self.assertEqual(PERIODS["train"], {"start": "2025-01-01", "end": "2025-12-31"})
        self.assertEqual(PERIODS["validation"]["end"], "2026-03-31")
        self.assertEqual(PERIODS["test"]["end"], "2026-06-30")
        m, rows = fixture()
        validate_manifest(m, fixture=True)
        validate_rows(m, rows)

    def test_rejects_wrong_partition_future_availability_horizon_group_and_label(self):
        mutations = [
            ("service_date", "2026-02-03"),
            ("value_available_at", "2025-02-03T08:00:00+05:00"),
            ("target_end", "2026-01-01T01:00:00+05:00"),
            ("target_available_at", "2026-01-02T00:00:00+05:00"),
            ("feature_cutoff_at", "2025-02-03T10:00:00+05:00"),
            ("feature_cutoff_at", "2025-02-03T07:00:00"),
            ("historic_value", float("nan")),
            ("operational_departure_id", "departure-test-0"),
        ]
        for key, value in mutations:
            with self.subTest(key=key):
                m, rows = fixture()
                rows["train"][0][key] = value
                with self.assertRaises(ValueError):
                    validate_rows(m, rows)

    def test_exact_local_midnight_exclusive_boundary(self):
        m, rows = fixture()
        r = rows["train"][0]
        r.update(service_date="2025-12-31", target_start="2025-12-31T18:00:00Z",
                 target_end="2025-12-31T19:00:00Z", target_available_at="2025-12-31T19:00:00Z")
        validate_rows(m, rows)
        r["target_end"] = "2025-12-31T19:00:01Z"
        with self.assertRaises(ValueError):
            validate_rows(m, rows)

    def test_delay_severity_accepts_instantaneous_event_target_only(self):
        m, rows = fixture("delay_severity")
        for row in rows["train"]:
            row["target_end"] = row["target_start"]
        validate_rows(m, rows)
        for task in ("occupancy_forecast", "crowding_risk"):
            m, rows = fixture(task)
            m["target"] = "label"
            for split_rows in rows.values():
                for row in split_rows:
                    row["target_end"] = row["target_start"]
                    row["target_available_at"] = (
                        datetime.fromisoformat(row["target_start"]) + timedelta(seconds=1)
                    ).isoformat()
            validate_rows(m, rows)
        m, rows = fixture("passenger_demand")
        rows["train"][0]["target_end"] = rows["train"][0]["target_start"]
        with self.assertRaises(ValueError):
            validate_rows(m, rows)

    def test_no_missing_labels_or_capacity(self):
        m, rows = fixture("crowding_risk")
        rows["train"][0]["capacity_snapshot"] = None
        with self.assertRaises(ValueError):
            validate_rows(m, rows)
        m, rows = fixture("delay_severity")
        for r in rows["train"]:
            r["label"] = 0
        with self.assertRaises(NotReady):
            validate_rows(m, rows)

    def test_occupancy_targets_are_future_and_current_outcome_is_not_a_predictor(self):
        m, rows = fixture("occupancy_forecast")
        m["target"] = "label"
        m["baseline_column"] = "historic_value"
        m["features"] = ["historic_value", "known_time"]
        for split_rows in rows.values():
            for row in split_rows:
                row["label"] = row["capacity_snapshot"] / 2
        validate_manifest(m, fixture=True)
        validate_rows(m, rows)
        m["features"].extend(["capacity_snapshot", "capacity_utilization", "onboard_departure"])
        with self.assertRaises(ValueError):
            validate_manifest(m, fixture=True)
        m["features"] = ["historic_value", "known_time"]
        rows["train"][0]["target_available_at"] = rows["train"][0]["feature_cutoff_at"]
        rows["train"][0]["target_end"] = rows["train"][0]["feature_cutoff_at"]
        with self.assertRaises(ValueError):
            validate_rows(m, rows)

    def test_route_clustering_reuses_routes_as_later_period_snapshots(self):
        m, rows = fixture("route_clustering")
        for split_rows in rows.values():
            for row in split_rows:
                row.pop("operational_departure_id")
        validate_rows(m, rows)

    def test_no_audit_target_predictors(self):
        for name in ("label", "quality_status", "target_available_at"):
            m, _ = fixture()
            m["features"].append(name)
            with self.assertRaises(ValueError):
                validate_manifest(m, fixture=True)

    def test_aggregate_lineage_and_missing_partition(self):
        m, rows = fixture()
        del rows["train"][0]["operational_departure_id"]
        with self.assertRaises(ValueError):
            validate_rows(m, rows)
        rows["train"][0]["source_departure_ids"] = ["departure-test-0"]
        with self.assertRaises(ValueError):
            validate_rows(m, rows)
        m, rows = fixture()
        del rows["validation"]
        with self.assertRaises(ValueError):
            validate_rows(m, rows)


class MetricTests(unittest.TestCase):
    def test_classification_confusion_weighted_macro(self):
        m = classification([0, 0, 1, 1], [0, 1, 1, 1], ["a", "b"])
        self.assertEqual(m["confusion_matrix"], [[1, 1], [0, 2]])
        self.assertEqual(m["accuracy"], .75)
        self.assertAlmostEqual(m["precision"], 5/6)
        self.assertEqual(m["recall"], .75)
        self.assertAlmostEqual(m["macro_f1"], (2/3+.8)/2)
        self.assertFalse(classification_acceptance(m)["passed"])
        self.assertIsNone(classification_acceptance({})["passed"])

    def test_regression_known_values_and_undefined(self):
        m = regression([0, 2, 4], [1, 2, 3])
        self.assertAlmostEqual(m["mae"], 2/3)
        self.assertAlmostEqual(m["rmse"], (2/3)**.5)
        self.assertEqual(m["mape"], 12.5)
        self.assertEqual(m["r2"], .75)
        self.assertEqual(m["mape_excluded_zero_count"], 1)
        self.assertIsNone(regression([0, 0], [1, 1])["mape"])
        self.assertIsNone(regression([1, 1], [2, 2])["r2"])
        with self.assertRaises(ValueError):
            regression([], [])

    def test_baseline_comparison_never_invents_improvement(self):
        self.assertTrue(compare_baseline({"mae": 2}, {"mae": 1})["improved"])
        self.assertFalse(compare_baseline({"mae": 1}, {"mae": 1})["improved"])
        self.assertFalse(compare_baseline({"mae": 1}, {"mae": 2})["improved"])
        self.assertIsNone(compare_baseline({}, {})["improved"])
        self.assertIsNone(compare_baseline({"mae": 0}, {"mae": 1})["relative_improvement"])

    def test_selection_is_measured_and_ignores_test(self):
        entries = [{"model_name": name, "status": "SUCCEEDED", "validation_metrics": {"mae": score},
                    "test_metrics": {"mae": 100-score}} for name, score in [("a", 3), ("b", 2), ("c", 1)]]
        self.assertEqual(select_model(entries, "mae")[0], "c")
        entries[0]["validation_metrics"]["mae"] = .5
        self.assertEqual(select_model(entries, "mae")[0], "a")
        entries[0]["status"] = "FAILED"
        with self.assertRaises(ValueError):
            select_model(entries, "mae")

    def test_probability_metrics(self):
        self.assertAlmostEqual(risk_metrics([0, 1], [.2, .8], .6)["brier_score"], .04)
        with self.assertRaises(ValueError):
            risk_metrics([0], [1.1], .6)

    def test_cluster_quality_and_interpretation(self):
        m = silhouette([[0], [1], [10], [11]], [0, 0, 1, 1], seed=1)
        self.assertGreater(m["silhouette"], .8)
        self.assertEqual(m, silhouette([[0], [1], [10], [11]], [0, 0, 1, 1], seed=1))
        self.assertIsNone(silhouette([[0], [1]], [0, 0], seed=1)["silhouette"])
        r = cluster_interpretation([{"x": 2, "route_id": "r1"}, {"x": 4, "route_id": "r2"}], [0, 0], ["x"])
        self.assertEqual(r[0]["feature_means_original_units"]["x"], 3)


class ArtifactTests(unittest.TestCase):
    def test_missing_artifacts_are_pending_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = execute(Path(tmp)/"absent.json", Path(tmp)/"out")
            self.assertEqual(result["status"], "NOT_READY")
            self.assertEqual(result["metrics"], {})
            self.assertIsNone(result["acceptance"])
            self.assertFalse((Path(tmp)/"out").exists())
            self.assertEqual(read_result(Path(tmp)/"result.json")["status"], "NOT_READY")

    def test_handoff_hash_limit_and_certification(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, rows = fixture()
            path = publish(Path(tmp)/"features", m, rows)
            loaded, data, hashes = load_features(path, fixture=True)
            self.assertEqual(len(data["train"]), 12)
            self.assertIn("train", hashes["partition_sha256"])
            with self.assertRaises(NotReady):
                load_features(path)
            with self.assertRaises(NotReady):
                load_features(path, fixture=True, max_rows=1)
            with (path.parent/"train.jsonl").open("a") as f:
                f.write("\n")
            with self.assertRaises(ValueError):
                load_features(path, fixture=True)

    def test_independent_producer_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, rows = fixture()
            path = publish(Path(tmp)/"features", m, rows)
            with self.assertRaises(ValueError):
                execute(path, Path(tmp)/"out", engine="spark", fixture=True)

    def test_analytics_fixture_result_and_not_production_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, rows = fixture("delay_service_analytics")
            path = publish(Path(tmp)/"features", m, rows)
            result = execute(path, Path(tmp)/"out", fixture=True)
            self.assertEqual(result["status"], "FIXTURE_TESTED", result)
            validate_result(result)
            file = Path(tmp)/"out"/"result.json"
            self.assertEqual(read_result(file)["status"], "NOT_READY")
            self.assertEqual(read_result(file, allow_fixture=True)["status"], "FIXTURE_TESTED")
            promoted = copy.deepcopy(result)
            promoted["status"] = "SUCCEEDED"
            with self.assertRaises(ValueError):
                validate_result(promoted)
            incomplete = copy.deepcopy(result)
            del incomplete["reproducibility"]
            with self.assertRaises(ValueError):
                validate_result(incomplete)
            with self.assertRaises(FileExistsError):
                execute(path, Path(tmp)/"out", fixture=True)

    def test_raw_manifest_is_rejected_before_read(self):
        with self.assertRaises(ValueError):
            load_features("raw_data/does-not-exist/manifest.json")

    def test_missing_certification_evidence_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, rows = fixture()
            m["certification"] = {"status": "CERTIFIED", "evidence_path": "absent.json"}
            path = publish(Path(tmp)/"features", m, rows)
            with self.assertRaises(NotReady):
                load_features(path)

    def test_candidate_configuration_is_deterministic(self):
        for engine in ("python", "spark"):
            for kind in ("classification", "regression", "clustering"):
                self.assertEqual(candidates(kind, engine), candidates(kind, engine))
                self.assertEqual(len(candidates(kind, engine)), 3)
                self.assertTrue(all(c["parameters"] for c in candidates(kind, engine)))

    @unittest.skipIf(importlib.util.find_spec("sklearn") is not None, "Missing-dependency case requires absent sklearn")
    def test_absent_runtime_does_not_fabricate_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, rows = fixture("delay_severity")
            path = publish(Path(tmp)/"features", m, rows)
            r = execute(path, Path(tmp)/"out", fixture=True)
            self.assertEqual(r["status"], "NOT_READY")
            self.assertEqual(r["metrics"], {})
            self.assertIsNone(r["selected_model"])

    @unittest.skipUnless(importlib.util.find_spec("sklearn") is not None, "scikit-learn unavailable; no dependencies installed")
    def test_optional_real_python_models_on_tiny_features(self):
        for task in ("delay_severity", "passenger_demand", "occupancy_forecast", "crowding_risk", "route_clustering"):
            with self.subTest(task=task), tempfile.TemporaryDirectory() as tmp:
                m, rows = fixture(task)
                path = publish(Path(tmp)/"features", m, rows)
                result = execute(path, Path(tmp)/"out", fixture=True)
                self.assertEqual(result["status"], "FIXTURE_TESTED", result)
                validate_result(result)
                self.assertTrue(Path(result["artifact_paths"]["model"]).exists())


class AnalyticsTests(unittest.TestCase):
    def test_counts_and_unavailable_vehicle_capacity(self):
        m, rows = fixture("delay_service_analytics")
        data = rows["train"][:2]
        data[0]["capacity_snapshot"] = None
        data[0]["departure_assignment_status"] = "UNKNOWN"
        r = service_analytics(data, m["analytics_policy"])
        self.assertEqual(r["delay_eligible_count"], 2)
        self.assertEqual(r["occupancy_unavailable_count"], 1)
        self.assertEqual(r["delay_segments"]["vehicle"]["excluded_count"], 1)
        self.assertEqual(r["delay_summary"]["on_time_fraction"], 1)

    def test_persistence_requires_multiple_days(self):
        m, rows = fixture("delay_service_analytics")
        data = [copy.deepcopy(rows["train"][-1]) for _ in range(2)]
        self.assertFalse(service_analytics(data, m["analytics_policy"])["persistent_crowding"][0]["persistent"])
        data[1]["service_date"] = "2025-02-10"
        self.assertTrue(service_analytics(data, m["analytics_policy"])["persistent_crowding"][0]["persistent"])

    def test_unavailable_observations_are_not_zero_metrics(self):
        m, rows = fixture("delay_service_analytics")
        for row in rows["train"]:
            row["delay_status"] = "UNAVAILABLE"
            row["occupancy_status"] = "UNAVAILABLE"
            row.pop("service_status")
        r = service_analytics(rows["train"], m["analytics_policy"])
        self.assertIsNone(r["delay_summary"])
        self.assertIsNone(r["occupancy_bands"])
        self.assertEqual(r["delay_segments"]["route"]["status"], "NOT_READY")
        self.assertEqual(r["service_status_counts"]["UNKNOWN"], 12)


if __name__ == "__main__":
    unittest.main()

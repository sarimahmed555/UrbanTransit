"""Small synthetic analytical artifacts only; no dataset, Spark or ML execution."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

from recommendation_engine.contracts import EvidencePackage, NotReady, pointer
from recommendation_engine.execution import execute
from recommendation_engine.recommendations import priority, validate_policy
from recommendation_engine.scenarios import apply_scenario, simulate, validate_state

STAMP = "2026-07-01T00:00:00Z"
POLICY = {"minimum_samples": 10, "minimum_days": 3, "overload_ratio": 1,
          "underutilization_ratio": .4, "late_fraction": .25, "bottleneck_delay_sec": 300,
          "headway_cv": .3, "passenger_impact_bands": [50, 200, 500], "severity_bands": [1.1, 1.5, 2]}


def state():
    return {"entity_ids": {"route_id": "fixture-route", "direction_id": 0},
            "window_start": "2026-06-01T08:00:00+05:00", "window_minutes": 60,
            "cycle_minutes": 30, "vehicle_count": 1, "initial_queue": 0,
            "crowding_threshold_ratio": 1, "stop_ids": ["fixture-stop"],
            "demand_kind": "MODEL_FORECAST", "demand_model_version": "synthetic-model-contract-v1",
            "demand_profile": [{"start_minute": 0, "end_minute": 60, "passengers": 60}],
            "trips": [{"trip_id": "t1", "departure_minute": 30, "capacity": 30},
                      {"trip_id": "t2", "departure_minute": 60, "capacity": 30}],
            "social_service_required": False, "removal_eligible_trip_ids": ["t1"]}


def record():
    return {"entity_ids": {"route_id": "fixture-route", "trip_id": "t1", "stop_id": "fixture-stop"},
            "window": {"start": "2026-06-01T00:00:00Z", "end": "2026-06-15T00:00:00Z"},
            "evidence_kind": "OBSERVED", "social_service_required": False,
            "coverage_preserved": True, "higher_capacity_vehicle_feasible": True,
            "metrics": {"sample_count": 100, "affected_passengers": 500,
                        "mean_occupancy_ratio": 1.8, "peak_occupancy_ratio": 2.5,
                        "overload_days": 5, "underutilized_days": 5,
                        "late_fraction": .8, "late_days": 5, "mean_delay_sec": 800,
                        "headway_cv": .8, "irregular_headway_days": 5,
                        "current_expected_wait_minutes": 20, "alternative_expected_wait_minutes": 5,
                        "evaluated_shift_minutes": -5}}


def publish(root, *, rec=None, baseline=None):
    artifact = {"status": "FIXTURE_TESTED", "dataset_version": "tiny-fixture-v1",
                "analytics_version": "tiny-analytics-v1", "record": record() if rec is None else rec,
                "baseline": state() if baseline is None else baseline}
    path = root / "analytics.json"
    path.write_text(json.dumps(artifact))
    manifest = {"schema_version": "1.0", "status": "FIXTURE", "dataset_version": "tiny-fixture-v1",
                "analytics_version": "tiny-analytics-v1",
                "sources": {"source": {"path": "analytics.json", "kind": "analytics", "version": "tiny-analytics-v1",
                                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}}
    manifest_path = root/"manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path


def scenario(kind, changes=None):
    ids = {"route_id": "fixture-route", "direction_id": 0}
    if kind in {"shift_trip_start_time", "remove_low_demand_trip"}:
        ids["trip_id"] = "t1"
    if kind == "add_new_stop":
        ids["stop_id"] = "fixture-new-stop"
    return {"schema_version": "1.0", "scenario_type": kind, "entity_ids": ids,
            "baseline_evidence": {"source_id": "source", "pointer": "/baseline"},
            "proposed_changes": changes or {}}


def recommendation(rules):
    return {"schema_version": "1.0", "policy": deepcopy(POLICY),
            "cases": [{"evidence": {"source_id": "source", "pointer": "/record"}, "rules": rules}]}


class RecommendationsTests(unittest.TestCase):
    def run_case(self, rec, rules):
        with tempfile.TemporaryDirectory() as tmp:
            path = publish(Path(tmp), rec=rec)
            return execute("recommendations", path, recommendation(rules), fixture=True, generated_at=STAMP)

    def test_seven_rules_supported_by_evidence(self):
        rules = ["increase_frequency", "modify_schedule", "higher_capacity_vehicle", "investigate_bottleneck", "adjust_departure_time", "improve_headways"]
        result = self.run_case(record(), rules)
        self.assertEqual(result["status"], "FIXTURE_TESTED", result)
        self.assertEqual({r["rule_id"] for r in result["recommendations"]}, set(rules))
        low = record()
        low["metrics"]["mean_occupancy_ratio"] = .1
        r = self.run_case(low, ["reduce_underutilized_service"])
        self.assertEqual(len(r["recommendations"]), 1)
        for item in result["recommendations"] + r["recommendations"]:
            for key in ("action", "entity_ids", "reason", "supporting_metrics", "source_analytical_artifact",
                        "priority", "evidence_status", "confidence", "generated_at", "provenance"):
                self.assertIn(key, item)
            self.assertIsNone(item["confidence"]["probability"])

    def test_priority_all_four_levels_and_joint_severity(self):
        validate_policy(POLICY)
        for impact, severity, level in [(0, 1, "LOW"), (50, 1.1, "MEDIUM"), (200, 1.5, "HIGH"), (500, 2, "CRITICAL")]:
            self.assertEqual(priority(impact, severity, POLICY)["priority"], level)
        self.assertNotEqual(priority(1, 10, POLICY)["priority"], "CRITICAL")
        self.assertNotEqual(priority(10000, 1, POLICY)["priority"], "CRITICAL")

    def test_recurrence_and_negative_control(self):
        rec = record()
        rec["metrics"]["overload_days"] = 1
        result = self.run_case(rec, ["increase_frequency"])
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["rule_checks"][0]["status"], "NO_ACTION")
        rec["metrics"]["overload_days"] = 5
        rec["metrics"]["mean_occupancy_ratio"] = 1
        self.assertEqual(self.run_case(rec, ["increase_frequency"])["recommendations"], [])

    def test_missing_metrics_and_small_sample_never_recommend(self):
        for missing in ("sample_count", "affected_passengers", "mean_occupancy_ratio", "overload_days"):
            rec = record()
            del rec["metrics"][missing]
            result = self.run_case(rec, ["increase_frequency"])
            self.assertEqual(result["status"], "NOT_READY", result)
            self.assertEqual(result["recommendations"], [])
        rec = record()
        rec["metrics"]["sample_count"] = 1
        self.assertEqual(self.run_case(rec, ["increase_frequency"])["status"], "NOT_READY")

    def test_coverage_and_vehicle_feasibility_protection(self):
        rec = record()
        rec["metrics"]["mean_occupancy_ratio"] = .1
        rec["social_service_required"] = True
        self.assertEqual(self.run_case(rec, ["reduce_underutilized_service"])["recommendations"], [])
        del rec["coverage_preserved"]
        self.assertEqual(self.run_case(rec, ["reduce_underutilized_service"])["status"], "NOT_READY")
        rec = record()
        del rec["higher_capacity_vehicle_feasible"]
        self.assertEqual(self.run_case(rec, ["higher_capacity_vehicle"])["status"], "NOT_READY")

    def test_no_claimed_benefit_for_worse_shift_and_no_duplicate_actions(self):
        rec = record()
        rec["metrics"]["alternative_expected_wait_minutes"] = 25
        self.assertEqual(self.run_case(rec, ["adjust_departure_time"])["recommendations"], [])
        result = self.run_case(rec, ["increase_frequency", "increase_frequency"])
        self.assertEqual(len(result["recommendations"]), 1)

    def test_invalid_policy_does_not_supply_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = publish(Path(tmp))
            request = recommendation(["increase_frequency"])
            del request["policy"]["overload_ratio"]
            result = execute("recommendations", path, request, fixture=True)
            self.assertEqual(result["status"], "NOT_READY")
            self.assertEqual(result["recommendations"], [])


class ScenarioTests(unittest.TestCase):
    def run_scenario(self, request, baseline=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = publish(Path(tmp), baseline=baseline)
            return execute("what-if", path, request, fixture=True, generated_at=STAMP)

    def test_analytic_fifo_baseline(self):
        metrics, events = simulate(state())
        self.assertEqual(metrics["passengers_served"], 60)
        self.assertEqual(metrics["waiting_time_minutes"], 15)
        self.assertEqual(metrics["occupancy_ratio"], 1)
        self.assertEqual(metrics["demand_coverage"], 1)
        self.assertEqual(metrics["overcrowding_risk"], 0)
        self.assertEqual(len(events), 2)

    def test_all_eight_scenarios_expected_impacts(self):
        cases = [
            (scenario("increase_frequency", {"trip_count": 4}), 120, 60, 7.5),
            (scenario("decrease_frequency", {"trip_count": 1}), 30, 30, 45),
            (scenario("add_vehicle"), 120, 60, 7.5),
            (scenario("change_vehicle_capacity", {"capacity": 60}), 120, 60, 15),
            (scenario("shift_trip_start_time", {"shift_minutes": -10}), 60, 50, 19),
            (scenario("remove_low_demand_trip"), 30, 30, 45),
            (scenario("add_new_stop", {"added_cycle_minutes": 15,
                                      "additional_demand_profile": [{"start_minute": 0, "end_minute": 60, "passengers": 30}]}), 30, 30, 50),
            (scenario("increase_predicted_demand", {"factor": 2}), 60, 60, 30),
        ]
        for request, capacity, served, wait in cases:
            with self.subTest(scenario=request["scenario_type"]):
                result = self.run_scenario(request)
                self.assertEqual(result["status"], "FIXTURE_TESTED", result)
                self.assertEqual(result["estimated_metrics"]["route_capacity"], capacity)
                self.assertAlmostEqual(result["estimated_metrics"]["passengers_served"], served)
                self.assertAlmostEqual(result["estimated_metrics"]["waiting_time_minutes"], wait)
                self.assertEqual(result["result_type"], "ESTIMATE")
                self.assertEqual(result["baseline_metrics_type"], "ESTIMATE")
                self.assertEqual(result["estimated_metrics_type"], "ESTIMATE")
                for key in ("entity_ids", "original_values", "proposed_changes", "assumptions", "deltas", "evidence_source", "limitations", "provenance"):
                    self.assertIn(key, result)

    def test_zero_demand_has_undefined_wait_and_coverage(self):
        baseline = state()
        baseline["demand_profile"][0]["passengers"] = 0
        result = self.run_scenario(scenario("increase_frequency", {"trip_count": 4}), baseline)
        self.assertEqual(result["status"], "FIXTURE_TESTED")
        self.assertIsNone(result["estimated_metrics"]["waiting_time_minutes"])
        self.assertIsNone(result["estimated_metrics"]["demand_coverage"])
        self.assertIsNone(result["deltas"]["waiting_time_minutes"])
        self.assertEqual(result["estimated_metrics"]["passengers_served"], 0)

    def test_invalid_changes_and_boundaries(self):
        requests = [scenario("increase_frequency", {"trip_count": 2}), scenario("decrease_frequency", {"trip_count": 0}),
                    scenario("increase_frequency", {"trip_count": 2.5}), scenario("increase_predicted_demand", {"factor": 1}),
                    scenario("change_vehicle_capacity", {"capacity": -1}), scenario("shift_trip_start_time", {"shift_minutes": -31}),
                    scenario("shift_trip_start_time", {"shift_minutes": 30}), scenario("add_vehicle", {"unknown": 1})]
        for request in requests:
            with self.subTest(request=request):
                self.assertEqual(self.run_scenario(request)["status"], "INVALID_REQUEST")

    def test_no_hidden_queue_demand_capacity_or_model_fallback(self):
        for missing in ("initial_queue", "demand_profile", "demand_model_version"):
            baseline = state()
            del baseline[missing]
            result = self.run_scenario(scenario("add_vehicle"), baseline)
            self.assertEqual(result["status"], "NOT_READY")
            self.assertIsNone(result["estimated_metrics"])
        baseline = state()
        del baseline["trips"][0]["capacity"]
        self.assertEqual(self.run_scenario(scenario("add_vehicle"), baseline)["status"], "NOT_READY")
        baseline = state()
        baseline["initial_queue"] = 10
        self.assertEqual(self.run_scenario(scenario("add_vehicle"), baseline)["status"], "NOT_READY")

    def test_demand_grid_conservation_and_last_departure_censoring(self):
        baseline = state()
        baseline["demand_profile"] = [{"start_minute": 0, "end_minute": 20, "passengers": 10},
                                      {"start_minute": 20, "end_minute": 60, "passengers": 80}]
        baseline["trips"][-1]["departure_minute"] = 50
        m, _ = simulate(baseline)
        self.assertAlmostEqual(m["passengers_served"]+m["passengers_unserved"], 90)
        self.assertGreaterEqual(m["passengers_unserved"], 20)
        baseline["demand_profile"][1]["start_minute"] = 21
        with self.assertRaises(ValueError):
            validate_state(baseline)

    def test_source_state_is_immutable_and_calculation_deterministic(self):
        original = state()
        saved = deepcopy(original)
        request = scenario("increase_frequency", {"trip_count": 4})
        self.assertEqual(apply_scenario(original, request), apply_scenario(original, request))
        self.assertEqual(original, saved)
        self.assertEqual(simulate(original), simulate(original))
        with tempfile.TemporaryDirectory() as tmp:
            path = publish(Path(tmp))
            self.assertEqual(execute("what-if", path, request, fixture=True, generated_at=STAMP),
                             execute("what-if", path, request, fixture=True, generated_at=STAMP))

    def test_social_service_and_unknown_trip_protected(self):
        baseline = state()
        baseline["social_service_required"] = True
        self.assertEqual(self.run_scenario(scenario("remove_low_demand_trip"), baseline)["status"], "INVALID_REQUEST")
        request = scenario("shift_trip_start_time", {"shift_minutes": -5})
        request["entity_ids"]["trip_id"] = "unknown"
        self.assertEqual(self.run_scenario(request)["status"], "INVALID_REQUEST")


class EvidenceTests(unittest.TestCase):
    def test_absent_artifact_and_fixture_not_certified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"missing.json"
            result = execute("what-if", path, scenario("add_vehicle"))
            self.assertEqual(result["status"], "NOT_READY")
            self.assertIsNone(result["estimated_metrics"])
            path = publish(Path(tmp))
            result = execute("what-if", path, scenario("add_vehicle"))
            self.assertEqual(result["status"], "NOT_READY")

    def test_hash_version_and_pointer_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = publish(root)
            p = EvidencePackage(path, fixture=True)
            value, source = p.resolve({"source_id": "source", "pointer": "/baseline"})
            self.assertEqual(value, state())
            self.assertEqual(len(source["sha256"]), 64)
            self.assertEqual(source["version"], "tiny-analytics-v1")
            with self.assertRaises(NotReady):
                p.resolve({"source_id": "source", "pointer": "/missing"})
            artifact = root/"analytics.json"
            artifact.write_text(artifact.read_text()+" ")
            self.assertEqual(execute("what-if", path, scenario("add_vehicle"), fixture=True)["status"], "NOT_READY")

    def test_version_mismatch_and_raw_data_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = publish(Path(tmp))
            m = json.loads(path.read_text())
            m["sources"]["source"]["version"] = "wrong"
            path.write_text(json.dumps(m))
            self.assertEqual(execute("what-if", path, scenario("add_vehicle"), fixture=True)["status"], "NOT_READY")
        with self.assertRaises(ValueError):
            EvidencePackage("raw_data/not-read/manifest.json")

    def test_pointer_escaping(self):
        self.assertEqual(pointer({"a/b": {"~x": 42}}, "/a~1b/~0x"), 42)
        with self.assertRaises(NotReady):
            pointer([1, 2], "/-1")

    def test_public_boundary_rejects_nonobject_request(self):
        self.assertEqual(execute("what-if", "unused", [1])["status"], "INVALID_REQUEST")

    def test_missing_metrics_object_has_no_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = record()
            rec["metrics"] = None
            path = publish(Path(tmp), rec=rec)
            result = execute("recommendations", path, recommendation(["increase_frequency"]), fixture=True)
            self.assertEqual(result["status"], "NOT_READY")
            self.assertEqual(result["recommendations"], [])

    def test_cli_missing_evidence_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request, output = root/"request.json", root/"result.json"
            request.write_text(json.dumps(scenario("add_vehicle")))
            command = [sys.executable, "-m", "recommendation_engine", "what-if", "--manifest", str(root/"absent.json"),
                       "--request", str(request), "--output", str(output)]
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2, completed.stderr)
            payload = output.read_text()
            self.assertEqual(json.loads(payload)["status"], "NOT_READY")
            self.assertIsNone(json.loads(payload)["estimated_metrics"])
            repeated = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(output.read_text(), payload)


if __name__ == "__main__":
    unittest.main()

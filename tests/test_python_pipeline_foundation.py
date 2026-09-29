"""Tiny synthetic tests for the independent Python pipeline foundation."""

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from python_pipeline.config import PipelinePaths, default_paths
from python_pipeline.dq import RULES, evaluate_rules
from python_pipeline.ingestion import discover_table_files, require_tables
from python_pipeline.outputs import output_contract
from python_pipeline.splits import SPLITS, assign_split, assert_no_future_leakage


class FoundationTests(unittest.TestCase):
    def test_split_boundaries_are_exact(self):
        self.assertEqual(assign_split(date(2025, 1, 1)), "train")
        self.assertEqual(assign_split(date(2025, 12, 31)), "train")
        self.assertEqual(assign_split("2026-01-01"), "validation")
        self.assertEqual(assign_split("2026-06-30"), "test")
        with self.assertRaises(ValueError):
            assign_split("2026-07-01")

    def test_paths_and_outputs_are_contract_only(self):
        with TemporaryDirectory() as directory:
            paths = default_paths(directory)
            contract = output_contract(paths)
            self.assertFalse(contract.clean_table("trips").exists())
            self.assertIn("train", str(contract.split("delay", "train")))
            self.assertRaises(ValueError, paths.raw_table_dir, "../production-v1")

    def test_discovery_requires_table_and_does_not_create_or_scan_it(self):
        with TemporaryDirectory() as directory:
            paths = PipelinePaths(Path(directory), dataset_version="fixture")
            with self.assertRaises(FileNotFoundError):
                discover_table_files(paths, "trips")
            with self.assertRaises(FileNotFoundError):
                require_tables(paths, ["trips"])

    def test_dq_preserves_original_and_rule_evidence(self):
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("Pandas is not installed in this VM")
        frame = pd.DataFrame([
            {"source_row_id": "r1", "ticket_id": "T1", "trip_id": "X", "passenger_id": "P1",
             "passenger_count": -1, "delay_sec": -2, "distance_km": 0},
            {"source_row_id": "r2", "ticket_id": "T1", "trip_id": "X", "passenger_id": "UNKNOWN",
             "passenger_count": 2, "delay_sec": 5, "distance_km": 4},
        ])
        issues = evaluate_rules(frame, known_passengers={"P1"}, known_trips={"X"})
        self.assertIn(RULES["duplicate_ticket"].code, set(issues.rule))
        self.assertIn(RULES["negative_passenger_count"].code, set(issues.rule))
        self.assertEqual(set(issues.status), {"FLAGGED"})
        self.assertIn("original_value", issues.columns)

    def test_leakage_guard_rejects_future_features(self):
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("Pandas is not installed in this VM")
        valid = pd.DataFrame({
            "value_available_at": pd.to_datetime(["2025-01-01"], utc=True),
            "feature_cutoff_at": pd.to_datetime(["2025-01-02"], utc=True),
        })
        self.assertTrue(assert_no_future_leakage(valid))
        invalid = valid.copy()
        invalid.loc[0, "value_available_at"] = pd.Timestamp("2025-01-03", tz="UTC")
        with self.assertRaises(ValueError):
            assert_no_future_leakage(invalid)

    def test_tiny_join_and_feature_fixture(self):
        try:
            import pandas as pd
            from python_pipeline.joins import join_tables
            from python_pipeline.features import boarding_alighting, occupancy, passenger_demand
        except ImportError:
            self.skipTest("Pandas is not installed in this VM")
        journeys = pd.DataFrame([
            {"journey_id": "J1", "trip_id": "T1", "origin_route_stop_id": "RS1",
             "destination_route_stop_id": "RS2"},
            {"journey_id": "J2", "trip_id": "T1", "origin_route_stop_id": "RS1",
             "destination_route_stop_id": "RS3"},
        ])
        trips = pd.DataFrame([{"trip_id": "T1", "route_id": "R1"}])
        counts = pd.DataFrame([{"trip_id": "T1", "departure_assignment_id": "A1",
                                "onboard_departure": 12}])
        assignments = pd.DataFrame([{"assignment_id": "A1", "capacity_snapshot": 10}])
        joined = join_tables(journeys, trips, left_on="trip_id", right_on="trip_id")
        self.assertEqual(len(joined), 2)
        self.assertEqual(passenger_demand(journeys).loc[0, "passenger_demand"], 2)
        self.assertEqual(boarding_alighting(journeys).loc[0, "boardings"], 2)
        self.assertEqual(occupancy(counts, assignments).loc[0, "capacity_utilization"], 1.2)


if __name__ == "__main__":
    unittest.main()

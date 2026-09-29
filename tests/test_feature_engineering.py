"""Small synthetic checks for feature contracts and leakage semantics."""

import unittest

from feature_contracts import CHRONOLOGICAL_SPLITS, FEATURE_CONTRACTS
from python_pipeline.splits import SPLITS, assign_split
from spark_jobs.contracts import SPLITS as SPARK_SPLITS
from spark_jobs.schemas import schema_spec


class FeatureContractTests(unittest.TestCase):
    def test_chronological_splits_are_identical_and_half_open(self):
        self.assertEqual(CHRONOLOGICAL_SPLITS, SPARK_SPLITS)
        self.assertEqual(
            [(split.name, split.start.isoformat(), (split.end).isoformat()) for split in SPLITS],
            [
                ("train", "2025-01-01", "2025-12-31"),
                ("validation", "2026-01-01", "2026-03-31"),
                ("test", "2026-04-01", "2026-06-30"),
            ],
        )
        self.assertEqual(assign_split("2025-12-31"), "train")
        self.assertEqual(assign_split("2026-01-01"), "validation")
        self.assertEqual(assign_split("2026-04-01"), "test")

    def test_contract_covers_required_feature_families(self):
        self.assertEqual(
            set(FEATURE_CONTRACTS),
            {
                "delay_severity", "demand_forecast", "occupancy",
                "headway", "delay_analytics", "route_clustering",
            },
        )
        for contract in FEATURE_CONTRACTS.values():
            self.assertTrue(contract.output_features)
            self.assertFalse(set(contract.predictor_features) & {
                "delay_sec", "delay_severity", "passenger_demand",
                "capacity_utilization", "actual_headway_sec",
            })
            self.assertTrue(contract.source_tables)
            self.assertTrue(contract.source_columns)
            self.assertTrue(contract.transformation)
            self.assertTrue(contract.grain)
            self.assertTrue(contract.target)
            self.assertTrue(contract.null_handling)
            self.assertTrue(contract.leakage_rule)
            self.assertTrue(contract.split_semantics)
            source_columns = {
                item.split(":", 1)[0]
                for table in contract.source_tables
                for item in schema_spec(table).split(",")
            }
            self.assertTrue(
                set(contract.source_columns) <= source_columns,
                f"{contract.name} references columns outside its declared source schemas",
            )

    def test_severity_threshold_configuration_is_explicit(self):
        from feature_contracts import validate_severity_thresholds

        self.assertEqual(validate_severity_thresholds((0, 60, 180, 300)), (0, 60, 180, 300))
        for invalid in ((0, 60, 60, 300), (-1, 0, 1, 2), (0, 1, 2), (0, 1, 2, float("nan"))):
            with self.assertRaises(ValueError):
                validate_severity_thresholds(invalid)

    def test_python_and_spark_implementations_share_task_entrypoints(self):
        from python_pipeline import features as pandas_features
        from spark_jobs import features as spark_features

        for function_name in (
            "prepare_operation_features", "aggregate_trip_demand",
            "build_feature_frames", "build_feature_frames_from_tables",
            "build_delay_features", "build_demand_forecast_features",
            "build_occupancy_features", "build_headway_features",
            "build_delay_analytics", "build_route_clustering_features",
        ):
            self.assertTrue(hasattr(pandas_features, function_name), function_name)
            self.assertTrue(hasattr(spark_features, function_name), function_name)


class PandasFeatureFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pandas  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Pandas is not installed in this VM")

    def test_history_split_and_determinism(self):
        import pandas as pd

        from python_pipeline.features import (
            assign_feature_splits,
            build_delay_features,
            build_demand_forecast_features,
        )

        demand = pd.DataFrame([
            {
                "service_date": day, "route_id": "R1", "direction_id": 0,
                "trip_id": trip, "scheduled_departure_utc": f"{day}T08:00:00Z",
                "passenger_demand": amount, "target_available_at": available,
                "known_schedule_available_at": f"{day}T07:00:00Z",
            }
            for day, trip, amount, available in (
                ("2025-01-15", "T3", 30, "2025-01-15T08:05:00Z"),
                ("2025-01-01", "T1", 10, "2025-01-01T08:05:00Z"),
                ("2025-01-08", "T2", 20, "2025-01-08T08:05:00Z"),
                ("2026-01-01", "V1", 40, "2026-01-01T08:05:00Z"),
            )
        ])
        first = build_demand_forecast_features(demand)
        second = build_demand_forecast_features(demand.sample(frac=1, random_state=41))
        by_trip = first.set_index("trip_id")
        self.assertTrue(pd.isna(by_trip.loc["T1", "historical_demand_lag_1"]))
        self.assertEqual(by_trip.loc["T2", "historical_demand_lag_1"], 10)
        self.assertEqual(by_trip.loc["T3", "historical_demand_lag_1"], 20)
        self.assertEqual(
            first.set_index("trip_id")["historical_demand_lag_1"].sort_index().to_dict(),
            second.set_index("trip_id")["historical_demand_lag_1"].sort_index().to_dict(),
        )
        altered = demand.copy()
        altered.loc[altered.trip_id == "T1", "target_available_at"] = "2026-02-01T00:00:00Z"
        guarded = build_demand_forecast_features(altered).set_index("trip_id")
        self.assertTrue(pd.isna(guarded.loc["T2", "historical_demand_lag_1"]))

        split = assign_feature_splits(demand)
        self.assertEqual(set(split), {"train", "validation", "test"})
        self.assertEqual(set(split["train"].trip_id), {"T1", "T2", "T3"})
        self.assertEqual(set(split["validation"].trip_id), {"V1"})
        self.assertTrue(split["test"].empty)

        delay = pd.DataFrame([
            {
                "service_date": "2025-01-01", "route_id": "R1",
                "direction_id": 0, "stop_id": "S1", "trip_id": "T1",
                "stop_event_id": "E1",
                "scheduled_departure_utc": "2025-01-01T08:00:00Z",
                "actual_departure_utc": "2025-01-01T08:02:00Z",
                "outcome_available_at_utc": "2025-01-01T08:03:00Z",
                "known_schedule_available_at": "2025-01-01T07:00:00Z",
            },
            {
                "service_date": "2025-01-01", "route_id": "R1",
                "direction_id": 0, "stop_id": "S1", "trip_id": "T2",
                "stop_event_id": "E2",
                "scheduled_departure_utc": "2025-01-01T08:05:00Z",
                "actual_departure_utc": "2025-01-01T08:05:00Z",
                "outcome_available_at_utc": "2025-01-01T08:06:00Z",
                "known_schedule_available_at": "2025-01-01T07:00:00Z",
            },
            {
                "service_date": "2025-01-01", "route_id": "R1",
                "direction_id": 0, "stop_id": "S1", "trip_id": "T3",
                "stop_event_id": "E3",
                "scheduled_departure_utc": "2025-01-01T08:10:00Z",
                "actual_departure_utc": "2025-01-01T08:16:00Z",
                "outcome_available_at_utc": "2025-01-01T08:17:00Z",
                "known_schedule_available_at": "2025-01-01T07:00:00Z",
            },
        ])
        delays = build_delay_features(delay, severity_thresholds_sec=(0, 60, 180, 300))
        by_event = delays.set_index("stop_event_id")
        self.assertEqual(by_event.loc["E2", "historical_delay_sec_lag_1"], 120)
        self.assertEqual(by_event.loc["E3", "historical_delay_sec_lag_1"], 0)
        self.assertEqual(by_event.loc["E2", "delay_severity"], "ON_TIME")
        self.assertEqual(by_event.loc["E3", "delay_severity"], "SEVERE_DELAY")
        self.assertNotIn("actual_departure_utc", delays.columns)
        self.assertNotIn("departure_delay_sec", delays.columns)
        late_schedule = delay.copy()
        late_schedule.loc[0, "known_schedule_available_at"] = "2025-01-01T08:01:00Z"
        with self.assertRaises(ValueError):
            build_delay_features(late_schedule, severity_thresholds_sec=(0, 60, 180, 300))

    def test_occupancy_nulls_headway_order_and_training_only_clusters(self):
        import pandas as pd

        from python_pipeline.features import (
            build_headway_features,
            build_occupancy_features,
            build_route_clustering_features,
        )

        occupancy = build_occupancy_features(pd.DataFrame([
            {"service_date": "2025-01-01", "stop_event_id": "E1",
             "onboard_departure": 12, "capacity_snapshot": 10},
            {"service_date": "2025-01-01", "stop_event_id": "E2",
             "onboard_departure": 3, "capacity_snapshot": None},
        ]))
        self.assertEqual(occupancy.loc[0, "capacity_utilization"], 1.2)
        self.assertEqual(occupancy.loc[0, "over_capacity"], True)
        self.assertTrue(pd.isna(occupancy.loc[1, "capacity_utilization"]))
        self.assertEqual(occupancy.loc[1, "occupancy_status"], "UNAVAILABLE")

        events = pd.DataFrame([
            {"service_date": "2025-01-01", "route_id": "R1",
             "direction_id": 0, "stop_id": "S1", "trip_id": "T2",
             "stop_event_id": "E2", "actual_departure_utc": "2025-01-01T08:10:00Z",
             "scheduled_departure_utc": "2025-01-01T08:10:00Z"},
            {"service_date": "2025-01-01", "route_id": "R1",
             "direction_id": 0, "stop_id": "S1", "trip_id": "T1",
             "stop_event_id": "E1", "actual_departure_utc": "2025-01-01T08:00:00Z",
             "scheduled_departure_utc": "2025-01-01T08:00:00Z"},
        ])
        headway = build_headway_features(events).set_index("stop_event_id")
        self.assertEqual(headway.loc["E2", "actual_headway_sec"], 600)
        self.assertTrue(pd.isna(headway.loc["E1", "actual_headway_sec"]))

        cluster_input = pd.DataFrame([
            {"service_date": year, "route_id": "R1", "direction_id": 0,
             "stop_id": "S1", "operational_departure_id": f"OP{year}",
             "onboard_departure": 5, "capacity_snapshot": 10,
             "distance_km": 7.5,
             "scheduled_departure_utc": f"{year}T08:00:00Z",
             "actual_departure_utc": f"{year}T08:01:00Z"}
            for year in ("2025-01-01", "2026-01-01")
        ])
        features = build_route_clustering_features(cluster_input)
        self.assertEqual(features.loc[0, "training_departures"], 1)
        self.assertEqual(features.loc[0, "mean_capacity_utilization"], 0.5)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from ml_execution.python_delay_handoff import (
    _allocate,
    _fixture_exclusions,
    _row_features,
)


class PythonDelayHandoffTests(unittest.TestCase):
    def setUp(self):
        self.context = (
            {
                "trip": {
                    "operational_departure_id": "departure",
                    "plan_status": "CURRENT",
                    "route_id": "route",
                    "pattern_id": "pattern",
                    "schedule_id": "schedule",
                    "service_date": "2025-01-01",
                    "scheduled_start_utc": "2025-01-01T00:00:00Z",
                    "published_at_utc": "2024-12-01T00:00:00Z",
                    "trip_status": "COMPLETED",
                }
            },
            {
                "pattern": {
                    "route_id": "route",
                    "direction_id": "1",
                    "distance_km": "12.5",
                    "published_at_utc": "2024-12-01T00:00:00Z",
                }
            },
            {
                "route-stop": {
                    "stop_id": "stop",
                    "value_available_at": "2024-12-01T00:00:00Z",
                }
            },
            {
                "stop-time": {
                    "schedule_id": "schedule",
                    "route_stop_id": "route-stop",
                    "departure_offset_sec": "3600",
                    "value_available_at": "2024-12-01T00:00:00Z",
                }
            },
            {
                "schedule": {
                    "pattern_id": "pattern",
                    "published_at_utc": "2024-12-01T00:00:00Z",
                }
            },
            {
                "route": {"value_available_at": "2024-12-01T00:00:00Z"}
            },
        )
        self.event = {
            "service_date": "2025-01-01",
            "trip_id": "trip",
            "stop_event_id": "event",
            "schedule_stop_time_id": "stop-time",
            "route_stop_id": "route-stop",
            "actual_departure_utc": "2025-01-01T01:02:00Z",
            "outcome_available_at_utc": "2025-01-01T01:03:00Z",
        }

    def test_derives_only_schedule_known_features_and_source_label(self):
        row = _row_features(self.event, self.context, (60, 300, 600, 1800))
        self.assertEqual(row["__split"], "train")
        self.assertEqual(row["target_label"], "MINOR_DELAY")
        self.assertEqual(row["target"], 1)
        self.assertEqual(row["scheduled_hour_utc"], 1.0)
        self.assertEqual(row["service_weekday"], 3.0)
        self.assertEqual(row["distance_km"], 12.5)
        self.assertEqual(row["feature_cutoff_at"], "2025-01-01T01:00:00Z")
        self.assertEqual(row["value_available_at"], "2024-12-01T00:00:00Z")
        self.assertEqual(row["case_id"], "event")
        self.assertEqual(row["source_departure_ids"], ["departure"])

    def test_rejects_schedule_information_published_after_cutoff(self):
        context = list(self.context)
        context[1] = {
            "pattern": {
                **context[1]["pattern"],
                "published_at_utc": "2025-01-01T02:00:00Z",
            }
        }
        self.assertIsNone(_row_features(self.event, tuple(context), (60, 300, 600, 1800)))

    def test_rejects_outcome_unavailable_at_the_prediction_cutoff(self):
        event = {**self.event, "outcome_available_at_utc": "2025-01-01T00:59:00Z"}
        self.assertIsNone(_row_features(event, self.context, (60, 300, 600, 1800)))

    def test_rejects_cross_schedule_stop_join(self):
        context = list(self.context)
        context[3] = {
            "stop-time": {**context[3]["stop-time"], "route_stop_id": "other"}
        }
        self.assertIsNone(_row_features(self.event, tuple(context), (60, 300, 600, 1800)))

    def test_quota_allocation_is_bounded_and_represents_every_group(self):
        quotas = _allocate({"train": 700, "validation": 200, "test": 100}, 100)
        self.assertEqual(sum(quotas.values()), 100)
        self.assertTrue(all(value > 0 for value in quotas.values()))

    def test_private_quality_fixture_rows_are_excluded_by_source_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "private_injection_manifest.json"
            manifest.write_text(json.dumps({
                "injections": [
                    {
                        "table_name": "Trips",
                        "source_row_id": "duplicate",
                    },
                    {
                        "table_name": "trip_stop_events",
                        "source_row_id": "quarantined",
                    },
                    {
                        "table_name": "tickets",
                        "source_row_id": "unrelated",
                    },
                ],
            }), encoding="utf-8")
            exclusions = _fixture_exclusions(manifest)
        self.assertIn("duplicate", exclusions["trips"])
        self.assertIn("quarantined", exclusions["trip_stop_events"])
        self.assertNotIn("tickets", exclusions)


if __name__ == "__main__":
    unittest.main()

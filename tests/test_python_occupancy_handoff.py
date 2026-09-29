import json
import tempfile
import unittest
import csv
from pathlib import Path

from ml_execution.python_occupancy_handoff import _fixture_exclusions, _timestamp, _unique


class PythonOccupancyHandoffTests(unittest.TestCase):
    def test_csv_null_marker_is_missing_optional_assignment_correction(self):
        self.assertIsNone(_timestamp("\\N"))
        self.assertIsNone(_timestamp("NULL"))
        self.assertIsNotNone(_timestamp("2026-04-20T01:48:00Z"))

    def test_count_and_assignment_injections_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "private_injection_manifest.json"
            manifest.write_text(json.dumps({
                "injections": [
                    {"table_name": "Passenger_Counts", "source_row_id": "bad-count"},
                    {"table_name": "Trip_Vehicle_Assignments", "source_row_id": "bad-assignment"},
                    {"table_name": "tickets", "source_row_id": "unused"},
                ],
            }), encoding="utf-8")
            excluded = _fixture_exclusions(manifest)
        self.assertEqual(excluded["passenger_counts"], {"bad-count"})
        self.assertEqual(excluded["trip_vehicle_assignments"], {"bad-assignment"})
        self.assertNotIn("tickets", excluded)

    def test_flagged_real_counts_are_retained_for_overload_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "counts.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=("quality_status", "source_row_id", "stop_event_id", "load"),
                )
                writer.writeheader()
                writer.writerows((
                    {"quality_status": "FLAGGED", "source_row_id": "real", "stop_event_id": "event-1", "load": "31"},
                    {"quality_status": "UNRESOLVED", "source_row_id": "unresolved", "stop_event_id": "event-2", "load": "12"},
                    {"quality_status": "FLAGGED", "source_row_id": "injected", "stop_event_id": "event-3", "load": "99"},
                ))
            counts = _unique(
                [path], "passenger_counts", "stop_event_id", ("load",), {"injected"},
                accepted_statuses=("VALID", "ACCEPTED", "FLAGGED"),
            )
        self.assertEqual(counts, {"event-1": {"stop_event_id": "event-1", "load": "31"}})


if __name__ == "__main__":
    unittest.main()

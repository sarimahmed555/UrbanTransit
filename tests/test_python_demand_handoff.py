import json
import tempfile
import unittest
from pathlib import Path

from ml_execution.python_demand_handoff import _allocate, _fixture_exclusions


class PythonDemandHandoffTests(unittest.TestCase):
    def test_injected_journey_sources_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "private_injection_manifest.json"
            manifest.write_text(json.dumps({
                "injections": [
                    {"table_name": "Passenger_Journeys", "source_row_id": "bad-journey"},
                    {"table_name": "trips", "source_row_id": "bad-trip"},
                    {"table_name": "tickets", "source_row_id": "not-read"},
                ],
            }), encoding="utf-8")
            excluded = _fixture_exclusions(manifest)
        self.assertEqual(excluded["passenger_journeys"], {"bad-journey"})
        self.assertEqual(excluded["trips"], {"bad-trip"})
        self.assertNotIn("tickets", excluded)

    def test_row_quotas_are_bounded_and_keep_every_split(self):
        quotas = _allocate({"train": 700, "validation": 200, "test": 100}, 100)
        self.assertEqual(sum(quotas.values()), 100)
        self.assertTrue(all(quotas[name] > 0 for name in quotas))
        self.assertEqual(_allocate({"train": 5, "validation": 2, "test": 1}, 100),
                         {"train": 5, "validation": 2, "test": 1})


if __name__ == "__main__":
    unittest.main()

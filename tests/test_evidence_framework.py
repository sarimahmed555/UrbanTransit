import json
import tempfile
import unittest
from pathlib import Path

from evidence_framework import (
    EVIDENCE_CATEGORIES,
    EvidenceRecorder,
    evaluate_thresholds,
    require_certified_run,
)


class EvidenceFrameworkTests(unittest.TestCase):
    def test_new_bundle_is_not_ready_and_covers_all_categories(self):
        recorder = EvidenceRecorder(dataset_version="fixture-v1", command="fixture-run")
        payload = recorder.bundle.payload
        self.assertEqual(payload["bundle_status"], "NOT_READY")
        self.assertEqual(set(payload["evidence"]), set(EVIDENCE_CATEGORIES))
        self.assertEqual(payload["evidence"]["spark_pipeline"]["status"], "NOT_RUN")

    def test_record_preserves_provenance_and_writes_machine_readable_json(self):
        recorder = EvidenceRecorder(
            dataset_version="fixture-v1",
            command="python fixture.py",
            input_artifact="fixture/input.json",
            output_artifact="fixture/output.json",
        )
        recorder.record(
            "performance",
            status="READY",
            rows_processed=4,
            runtime_sec=0.01,
            resource_capture={"workers": 1},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = recorder.write(Path(directory) / "evidence.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
        record = payload["evidence"]["performance"]
        self.assertEqual(record["dataset_version"], "fixture-v1")
        self.assertEqual(record["input_artifact"], "fixture/input.json")
        self.assertEqual(record["rows_processed"], 4)
        self.assertEqual(payload["bundle_status"], "READY")

    def test_thresholds_do_not_pass_before_a_ready_runtime(self):
        result = evaluate_thresholds(
            {"status": "NOT_READY", "rows_processed": 99999999},
            minimum_rows=2_000_000,
        )
        self.assertEqual(result[0].status, "NOT_RUN")
        self.assertIsNone(result[0].passed)

    def test_thresholds_check_real_observed_values_only(self):
        results = evaluate_thresholds(
            {
                "status": "READY",
                "rows_processed": 2_000_001,
                "agreement": {"rate": 0.99},
                "metrics": {"f1": 0.8},
            },
            minimum_rows=2_000_000,
            minimum_agreement=0.95,
            required_metrics=("f1",),
        )
        self.assertTrue(all(item.passed for item in results))

    def test_certified_run_guard_rejects_not_ready(self):
        with self.assertRaises(AssertionError):
            require_certified_run({"status": "NOT_RUN"}, category="spark_pipeline")


if __name__ == "__main__":
    unittest.main()

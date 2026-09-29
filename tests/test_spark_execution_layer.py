import ast
import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from spark_jobs.contracts import SparkPaths, SPLITS, TABLES, validate_certified_input
from spark_jobs.runtime import EvidenceLogger, _json_default
from spark_jobs.schemas import schema_spec


ROOT = Path(__file__).resolve().parents[1]


class SparkExecutionLayerTests(unittest.TestCase):
    def test_contract_has_all_transport_tables(self):
        self.assertEqual(len(TABLES), 21)
        self.assertEqual(SPLITS["test"], ("2026-04-01", "2026-07-01"))

    def test_paths_are_separate(self):
        paths = SparkPaths()
        self.assertTrue(paths.features.endswith("/features"))
        self.assertNotEqual(paths.analytics, paths.models)

    def test_schema_catalog_is_explicit(self):
        for table in TABLES:
            spec = schema_spec(table)
            self.assertIn("source_row_id:string", spec)
            self.assertIn("value_available_at:timestamp", spec)

    def test_uncertified_current_production_path_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_certified_input(
                "/home/manal/UrbanTransit-IQ/raw_data/production-v1",
                "/absolute/CERTIFIED",
            )

    def test_feature_writers_never_overwrite_reserved_run_outputs(self):
        feature_writer = (ROOT / "spark_jobs" / "features.py").read_text(encoding="utf-8")
        pipeline_writer = (ROOT / "spark_jobs" / "pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn('.mode("overwrite")', feature_writer)
        self.assertNotIn('.mode("overwrite")', pipeline_writer)
        self.assertIn('.mode("errorifexists")', feature_writer)
        self.assertIn('.mode("errorifexists")', pipeline_writer)

    def test_pipeline_evidence_uses_the_reserved_cli_run_id(self):
        logger = EvidenceLogger(
            None, "/urbantransit/evidence", pipeline_id="test", run_id="run-17"
        )
        self.assertEqual(logger.run_id, "run-17")

        tree = ast.parse((ROOT / "spark_jobs" / "pipeline.py").read_text(encoding="utf-8"))
        logger_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "EvidenceLogger"
        ]
        self.assertEqual(len(logger_calls), 1)
        self.assertTrue(any(
            keyword.arg == "run_id"
            and isinstance(keyword.value, ast.Attribute)
            and isinstance(keyword.value.value, ast.Name)
            and keyword.value.value.id == "args"
            and keyword.value.attr == "run_id"
            for keyword in logger_calls[0].keywords
        ))

    def test_pipeline_supports_task_scoped_feature_materialization(self):
        source = (ROOT / "spark_jobs" / "pipeline.py").read_text(encoding="utf-8")
        self.assertIn('--feature-task', source)
        self.assertIn('--feature-splits', source)
        self.assertIn("tasks=selected_tasks", source)
        features = (ROOT / "spark_jobs" / "features.py").read_text(encoding="utf-8")
        self.assertIn("if {\"demand_forecast\", \"route_clustering\"} & selected", features)
        self.assertIn('record["schema"] = current.schema.jsonValue()', features)

    def test_runtime_evidence_serializes_dates_as_iso8601(self):
        values = {
            "date": date(2026, 9, 28),
            "timestamp": datetime(2026, 9, 28, 11, 20, 23, tzinfo=timezone.utc),
        }
        encoded = json.dumps(values, default=_json_default, sort_keys=True)
        self.assertEqual(
            json.loads(encoded),
            {
                "date": "2026-09-28",
                "timestamp": "2026-09-28T11:20:23+00:00",
            },
        )
        with self.assertRaisesRegex(TypeError, "not JSON serializable"):
            json.dumps({"unexpected": object()}, default=_json_default)


if __name__ == "__main__":
    unittest.main()

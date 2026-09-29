import csv
import tempfile
import unittest
from pathlib import Path

from pyspark.sql import SparkSession, functions as F
from spark_jobs.contracts import require_hdfs_uri
from spark_jobs.ingest import read_certified_tables
from spark_jobs.quality import apply_hooks
from spark_jobs.schemas import schema_for

class RuntimeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spark = SparkSession.builder.master('local[2]').appName('runtime-regressions').getOrCreate()
        cls.spark.sparkContext.setLogLevel('ERROR')
    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()
    def write_routes(self, root, **updates):
        target=Path(root)/'raw/routes'
        target.mkdir(parents=True)
        columns=schema_for('routes').fieldNames()
        row=dict.fromkeys(columns, '\\N')
        row.update(source_row_id='source-1', quality_status='VALID', row_ordinal='1',
                   raw_record_text='{"name":"quoted, route","active":true}',
                   route_id='R1', route_code='R001', social_service_required='true',
                   opened_on='2025-01-01')
        row.update(updates)
        with (target/'part.csv').open('w', newline='') as f:
            writer=csv.DictWriter(f, fieldnames=columns)
            writer.writeheader(); writer.writerow(row)
    def test_rfc4180_null_types_and_explicit_source_override(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_routes(root)
            frame=read_certified_tables(self.spark, '/missing', tables=['routes'], table_roots={'routes':root})['routes']
            row=frame.first()
            self.assertEqual(row.route_code, 'R001')
            self.assertEqual(row.raw_record_text, '{"name":"quoted, route","active":true}')
            self.assertIsNone(row.closed_on)
            self.assertIsNone(row.correction_time)
            self.assertTrue(row.social_service_required)
            self.assertEqual(row.opened_on.isoformat(), '2025-01-01')
    def test_csv_and_gzip_are_read_without_jsonl_mirrors(self):
        import gzip
        with tempfile.TemporaryDirectory() as root:
            self.write_routes(root)
            directory=Path(root)/'raw/routes'
            with gzip.open(directory/'part-001.csv.gz', 'wt') as f:
                f.write((directory/'part.csv').read_text())
            (directory/'part.jsonl').write_text('{"mirror":true}\n')
            frame=read_certified_tables(self.spark, root, tables=['routes'])['routes']
            rows=frame.collect()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row.route_code == 'R001' for row in rows))

    def test_malformed_typed_value_fails_full_schema_action(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_routes(root, row_ordinal='invalid-number')
            frame=read_certified_tables(self.spark, root, tables=['routes'])['routes']
            with self.assertRaises(Exception):
                frame.agg(F.sum(F.xxhash64(*frame.columns).cast('decimal(38,0)'))).collect()
    def test_dq_accepts_valid_and_flags_null_or_unknown_status(self):
        frame=self.spark.createDataFrame([('a','VALID'),('b',None),('c','nonsense')], 'source_row_id string, quality_status string')
        checked, dq=apply_hooks({'routes':frame}, None)
        self.assertEqual(checked['routes'].count(), 3)
        self.assertEqual({r.source_row_id for r in dq.collect()}, {'b','c'})
    def test_delay_history_self_join_is_qualified_and_past_only(self):
        from datetime import datetime
        from spark_jobs.features import build_delay_features

        schema = (
            "service_date string, route_id string, direction_id int, stop_id string, "
            "trip_id string, stop_event_id string, scheduled_departure_utc timestamp, "
            "actual_departure_utc timestamp, outcome_available_at_utc timestamp, "
            "known_schedule_available_at timestamp"
        )
        rows = [
            ("2025-01-01", "R1", 0, "S1", "T4", "E4",
             datetime(2025, 1, 1, 8, 15), datetime(2025, 1, 1, 8, 30),
             datetime(2025, 1, 1, 8, 31), datetime(2025, 1, 1, 7)),
            ("2025-01-01", "R1", 0, "S1", "T1", "E1",
             datetime(2025, 1, 1, 8), datetime(2025, 1, 1, 8, 2),
             datetime(2025, 1, 1, 8, 3), datetime(2025, 1, 1, 7)),
            ("2025-01-01", "R1", 0, "S1", "T3", "E3",
             datetime(2025, 1, 1, 8, 10), datetime(2025, 1, 1, 8, 16),
             datetime(2025, 1, 1, 8, 17), datetime(2025, 1, 1, 7)),
            ("2025-01-01", "R1", 0, "S1", "T2", "E2",
             datetime(2025, 1, 1, 8, 5), datetime(2025, 1, 1, 8, 5),
             datetime(2025, 1, 1, 8, 6), datetime(2025, 1, 1, 7)),
        ]
        operations = self.spark.createDataFrame(rows, schema)
        result = build_delay_features(
            operations, severity_thresholds_sec=(0, 60, 180, 300)
        )
        by_event = {row.stop_event_id: row for row in result.collect()}

        self.assertIsNone(by_event["E1"].historical_delay_sec_lag_1)
        self.assertEqual(by_event["E2"].historical_delay_sec_lag_1, 120)
        self.assertEqual(by_event["E2"].delay_severity, "ON_TIME")
        self.assertEqual(by_event["E3"].historical_delay_sec_lag_1, 0)
        self.assertEqual(by_event["E3"].historical_delay_sec_mean_7, 60)
        self.assertEqual(by_event["E3"].delay_severity, "SEVERE_DELAY")
        self.assertEqual(by_event["E4"].historical_delay_sec_lag_1, 0)
        self.assertEqual(by_event["E4"].historical_delay_sec_mean_7, 60)
        self.assertNotIn("actual_departure_utc", result.columns)

    def test_streamed_history_matches_reference_for_availability_and_boundaries(self):
        from datetime import datetime
        from pyspark.sql import Window, functions as F
        from spark_jobs.features import _past_only_history

        schema = (
            "row_id string, route_id string, feature_cutoff_at timestamp, "
            "target double, target_available_at timestamp, trip_id string, "
            "stop_event_id string"
        )
        rows = [
            ("a0", "R1", datetime(2025, 1, 1, 8, 0), 5.0,
             datetime(2025, 1, 1, 7, 59), "T0", "E0"),
            ("a1", "R1", datetime(2025, 1, 1, 8, 5), 10.0,
             datetime(2025, 1, 1, 8, 1), "T1", "E1"),
            ("a2", "R1", datetime(2025, 1, 1, 8, 6), 20.0,
             datetime(2025, 1, 1, 8, 12), "T2", "E2"),
            ("a3", "R1", datetime(2025, 1, 1, 8, 6), 30.0,
             datetime(2025, 1, 1, 8, 7), "T3", "E3"),
            ("a4", "R1", datetime(2025, 1, 1, 8, 13), 40.0,
             datetime(2025, 1, 1, 8, 13), "T4", "E4"),
            ("a5", "R1", datetime(2025, 1, 1, 8, 14), None,
             datetime(2025, 1, 1, 8, 14), "T5", "E5"),
            ("a6", "R1", datetime(2025, 1, 1, 8, 15), 55.0,
             None, "T6", "E6"),
            ("a7", "R1", datetime(2025, 1, 1, 8, 30), 99.0,
             datetime(2025, 1, 1, 8, 30), "T7", "E7"),
            ("b0", "R2", datetime(2025, 1, 1, 8, 2), 7.0,
             datetime(2025, 1, 1, 8, 2), "U0", "F0"),
            ("b1", "R2", datetime(2025, 1, 1, 8, 3), 8.0,
             datetime(2025, 1, 1, 8, 3), "U1", "F1"),
            ("n0", None, datetime(2025, 1, 1, 8, 2), 11.0,
             datetime(2025, 1, 1, 8, 2), "N0", "G0"),
            ("n1", None, datetime(2025, 1, 1, 8, 4), 12.0,
             datetime(2025, 1, 1, 8, 4), "N1", "G1"),
        ]
        frame = self.spark.createDataFrame(rows, schema)
        optimized = _past_only_history(
            frame, keys=["route_id"], time_column="feature_cutoff_at",
            target_column="target", available_column="target_available_at",
            prefix="history",
        )

        current = frame.withColumn("__reference_id", F.monotonically_increasing_id()).alias("c")
        history = frame.alias("h")
        prior = (
            (F.col("h.feature_cutoff_at") < F.col("c.feature_cutoff_at"))
            & (F.col("h.target_available_at") <= F.col("c.feature_cutoff_at"))
            & F.col("h.target").isNotNull()
            & F.col("h.target_available_at").isNotNull()
        )
        candidates = current.join(
            history,
            F.col("c.route_id").eqNullSafe(F.col("h.route_id")) & prior,
            "left",
        ).select(
            F.col("c.row_id").alias("row_id"),
            F.col("h.feature_cutoff_at").alias("__prior_time"),
            F.col("h.target").alias("__prior_target"),
            F.col("h.trip_id").alias("__prior_trip"),
            F.col("h.stop_event_id").alias("__prior_event"),
        )
        ranked = candidates.withColumn(
            "__prior_rank",
            F.row_number().over(
                Window.partitionBy("row_id").orderBy(
                    F.col("__prior_time").desc_nulls_last(),
                    F.col("__prior_trip").desc_nulls_last(),
                    F.col("__prior_event").desc_nulls_last(),
                )
            ),
        ).filter(F.col("__prior_rank") <= 7)
        reference = ranked.groupBy("row_id").agg(
            F.max(F.when(F.col("__prior_rank") == 1, F.col("__prior_target")))
            .alias("history_lag_1"),
            F.avg("__prior_target").alias("history_mean_7"),
        )
        expected = {
            row.row_id: (row.history_lag_1, row.history_mean_7)
            for row in frame.join(reference, "row_id", "left").collect()
        }
        self.assertEqual(
            optimized.schema,
            frame.join(reference, "row_id", "left").schema,
        )
        actual = {
            row.row_id: (row.history_lag_1, row.history_mean_7)
            for row in optimized.collect()
        }
        self.assertEqual(actual, expected)
        self.assertEqual(actual["a0"], (None, None))
        self.assertEqual(actual["a1"], (5.0, 5.0))
        self.assertEqual(actual["a2"], (10.0, 7.5))
        self.assertEqual(actual["a4"], (30.0, 16.25))
        self.assertEqual(actual["b0"], (None, None))
        self.assertEqual(actual["n1"], (11.0, 11.0))

    def test_history_physical_plan_is_streamed_and_linear_in_rows(self):
        from datetime import datetime, timedelta
        from spark_jobs.features import _past_only_history

        start = datetime(2025, 1, 1)
        rows = [
            (f"r{index}", "R1", 0, start + timedelta(minutes=index),
             index, start + timedelta(minutes=index), f"T{index}", f"E{index}")
            for index in range(4000)
        ]
        frame = self.spark.createDataFrame(
            rows,
            "row_id string, route_id string, direction_id int, "
            "feature_cutoff_at timestamp, target long, "
            "target_available_at timestamp, trip_id string, stop_event_id string",
        )
        result = _past_only_history(
            frame, keys=["route_id", "direction_id"],
            time_column="feature_cutoff_at", target_column="target",
            available_column="target_available_at", prefix="history",
        )
        self.assertEqual(result.count(), len(rows))
        self.assertEqual(result.schema["history_lag_1"].dataType.typeName(), "long")
        self.assertEqual(result.schema["history_mean_7"].dataType.typeName(), "double")
        plan = result._jdf.queryExecution().executedPlan().toString().lower()
        lineage = result.rdd.toDebugString().decode("utf-8").lower()
        self.assertNotIn("join", plan)
        self.assertNotIn("window", plan)
        self.assertIn("shuffledrowrdd", lineage)
        self.assertIn("pythonrdd", lineage)
        self.assertIn("map", lineage)
    def test_timestamp_dq_preserves_rows_values_and_ansi(self):
        from pyspark.sql.types import StringType, TimestampType
        original_ansi = self.spark.conf.get("spark.sql.ansi.enabled")
        self.spark.conf.set("spark.sql.ansi.enabled", "true")
        try:
            with tempfile.TemporaryDirectory() as root:
                directory = Path(root)/"raw/tickets"
                directory.mkdir(parents=True)
                columns = schema_for("tickets").fieldNames()
                values = ["2025-13-99T25:61:00Z", "2025-01-01T00:39:10.000000Z", None]
                with (directory/"part.csv").open("w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=columns)
                    writer.writeheader()
                    for index, value in enumerate(values):
                        row = dict.fromkeys(columns, "\\N")
                        row.update(source_row_id=str(index), row_ordinal=str(index+1),
                                   quality_status="FLAGGED" if index == 0 else "VALID",
                                   unresolved_reason="INVALID_TIMESTAMP" if index == 0 else "\\N",
                                   issued_at_utc=value if value is not None else "\\N",
                                   service_date="2025-01-01")
                        writer.writerow(row)
                raw = read_certified_tables(self.spark, root, tables=["tickets"])["tickets"]
                self.assertIsInstance(raw.schema["issued_at_utc"].dataType, StringType)
                self.assertEqual(raw.orderBy("source_row_id").first().issued_at_utc, values[0])
                checked, dq = apply_hooks({"tickets": raw}, None)
                clean = checked["tickets"]
                self.assertIsInstance(clean.schema["issued_at_utc"].dataType, TimestampType)
                rows = {row.source_row_id: row for row in clean.collect()}
                self.assertEqual(len(rows), 3)
                self.assertIsNone(rows["0"].issued_at_utc)
                self.assertEqual(rows["0"].raw_timestamp__issued_at_utc, values[0])
                self.assertEqual(rows["0"].quality_status, "FLAGGED")
                self.assertEqual(rows["0"].unresolved_reason, "INVALID_TIMESTAMP")
                self.assertEqual(rows["1"].issued_at_utc.year, 2025)
                self.assertEqual(rows["1"].raw_timestamp__issued_at_utc, values[1])
                self.assertIsNone(rows["2"].issued_at_utc)
                issues = dq.collect()
                self.assertEqual(len(issues), 1)
                self.assertEqual((issues[0].rule_id, issues[0].status, issues[0].column_name,
                                  issues[0].raw_value),
                                 ("INVALID_TIMESTAMP", "FLAGGED", "issued_at_utc", values[0]))
                self.assertEqual(self.spark.conf.get("spark.sql.ansi.enabled"), "true")
        finally:
            self.spark.conf.set("spark.sql.ansi.enabled", original_ansi)

    def test_structural_csv_corruption_still_fails_fast(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_routes(root)
            path = Path(root)/"raw/routes/part.csv"
            lines = path.read_text().splitlines()
            path.write_text(lines[0]+"\n"+lines[1]+",unexpected-extra-field\n")
            frame = read_certified_tables(self.spark, root, tables=["routes"])["routes"]
            with self.assertRaises(Exception):
                frame.collect()

    def test_hdfs_root_is_explicit(self):
        self.assertEqual(require_hdfs_uri('hdfs://localhost:9000/urbantransit/'), 'hdfs://localhost:9000/urbantransit')
        for bad in ['/urbantransit', 'file:///urbantransit', 'hdfs:///urbantransit']:
            with self.assertRaises(ValueError): require_hdfs_uri(bad)
    def test_runtime_plan_retains_explicit_hdfs_uri(self):
        from tests.test_runtime_orchestration import all_ready_preflight, ROOT
        from runtime_orchestration.plan import build_plan
        plan=build_plan(project_root=ROOT, preflight=all_ready_preflight(), certified_root='/certified', marker_path='/marker', dataset_version='production-v1.1', hdfs_root='hdfs://localhost:9000/urbantransit', spark_severity_thresholds=(60,300,600,1800))
        argv=plan[5].argv
        self.assertEqual(argv[argv.index('--hdfs-root')+1], 'hdfs://localhost:9000/urbantransit')
        self.assertNotIn('hdfs://hdfs://', str(plan))

if __name__=='__main__': unittest.main()

"""Small fixtures for non-destructive publication and execution CLIs."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from runtime_orchestration.certification import CertificationAttestation
from runtime_orchestration.compare import (
    PRODUCTION_V11_DELAY_TIME_NORMALIZATION,
    compare_artifacts,
)
from runtime_orchestration.output_safety import (
    create_output_directory,
    versioned_output_path,
)
from runtime_orchestration.publish import execute_publication
from spark_jobs.output_safety import (
    assert_spark_path_absent,
    reserve_spark_output,
    versioned_spark_path,
)


ROOT = Path(__file__).resolve().parents[1]


class _FakeFrame:
    def __len__(self):
        return 2


class _FixtureAdapter:
    def certify(self, dataset_root, marker_path):
        return CertificationAttestation("fixture-dataset-v2", "fixture-attestation")


class OutputSafetyTests(unittest.TestCase):
    def test_local_output_paths_are_versioned_and_collisions_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            output_root = Path(temp) / "outputs"
            destination = versioned_output_path(
                output_root,
                stage="python_features",
                dataset_version="dataset-v3",
                run_id="run-17",
            )
            self.assertEqual(
                destination.relative_to(output_root).as_posix(),
                "stage=python_features/dataset=dataset-v3/run=run-17",
            )
            create_output_directory(destination, input_root=Path(temp) / "input")
            with self.assertRaises(FileExistsError):
                create_output_directory(destination, input_root=Path(temp) / "input")

    def test_spark_output_path_and_collision_guard(self):
        self.assertEqual(
            versioned_spark_path(
                "hdfs:///urbantransit",
                stage="spark_pipeline",
                dataset_version="dataset-v3",
                run_id="run-17",
            ),
            "hdfs:///urbantransit/runs/stage=spark_pipeline/dataset=dataset-v3/run=run-17",
        )

        class FakePath:
            def __init__(self, value):
                self.value = value

            def getFileSystem(self, _configuration):
                return SimpleNamespace(exists=lambda _path: True)

        spark = SimpleNamespace(
            _jvm=SimpleNamespace(
                org=SimpleNamespace(
                    apache=SimpleNamespace(
                        hadoop=SimpleNamespace(
                            fs=SimpleNamespace(Path=FakePath)
                        )
                    )
                )
            ),
            _jsc=SimpleNamespace(hadoopConfiguration=lambda: object()),
        )
        with self.assertRaisesRegex(FileExistsError, "run=run-17"):
            assert_spark_path_absent(
                spark,
                "hdfs:///out/run=run-17",
                stage="spark_pipeline",
                run_id="run-17",
                dataset_version="dataset-v3",
            )

    def test_spark_output_reservation_is_exclusive_and_persistent(self):
        existing = set()
        create_flags = []

        class FakePath:
            def __init__(self, value):
                self.value = value

            def __str__(self):
                return self.value

            def getParent(self):
                return FakePath(self.value.rsplit("/", 1)[0])

            def getFileSystem(self, _configuration):
                return filesystem

        class FakeStream:
            def close(self):
                pass

        class FakeFilesystem:
            def exists(self, path):
                return path.value in existing

            def mkdirs(self, _path):
                return True

            def create(self, path, overwrite):
                create_flags.append(overwrite)
                if path.value in existing:
                    raise FileExistsError(path.value)
                existing.add(path.value)
                return FakeStream()

        filesystem = FakeFilesystem()
        spark = SimpleNamespace(
            _jvm=SimpleNamespace(
                org=SimpleNamespace(
                    apache=SimpleNamespace(
                        hadoop=SimpleNamespace(fs=SimpleNamespace(Path=FakePath))
                    )
                )
            ),
            _jsc=SimpleNamespace(hadoopConfiguration=lambda: object()),
        )
        output = "hdfs:///out/run=one"
        lock = reserve_spark_output(
            spark,
            output,
            stage="features",
            run_id="one",
            dataset_version="dataset-v1",
        )
        self.assertEqual(lock, output + "._execution.lock")
        self.assertEqual(create_flags, [False])
        with self.assertRaises(FileExistsError):
            assert_spark_path_absent(
                spark,
                output,
                stage="features",
                run_id="one",
                dataset_version="dataset-v1",
            )


class HdfsPublisherTests(unittest.TestCase):
    def _publisher_fixture(self, temp):
        package = Path(temp) / "certified"
        (package / "raw").mkdir(parents=True)
        marker = Path(temp) / "marker.bin"
        marker.write_bytes(b"opaque authoritative marker")
        fake_hdfs = Path(temp) / "hdfs-mock"
        log = Path(temp) / "hdfs-commands.log"
        fake_hdfs.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "with open(os.environ['HDFS_LOG'], 'a') as out: out.write(' '.join(sys.argv[1:])+'\\n')\n"
            "if sys.argv[2:4] == ['-test', '-d']: raise SystemExit(0)\n"
            "if sys.argv[2:4] == ['-test', '-e']: raise SystemExit(0)\n"
            "raise SystemExit(9)\n",
            encoding="utf-8",
        )
        fake_hdfs.chmod(0o755)
        env = {**os.environ, "HDFS_BIN": str(fake_hdfs), "HDFS_LOG": str(log)}
        return package, marker, fake_hdfs, log, env

    def test_existing_hdfs_version_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            package, marker, _fake_hdfs, log, env = self._publisher_fixture(temp)
            process = subprocess.run(
                [
                    "bash",
                    str(ROOT / "hdfs_scripts" / "ingest_certified_dataset.sh"),
                    str(package),
                    str(marker),
                    "dataset-v7",
                ],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            commands = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(process.returncode, 3)
        self.assertIn("already exists", process.stderr)
        self.assertEqual(len(commands), 2)
        self.assertTrue(all("-put" not in command and "-mkdir" not in command for command in commands))

    def test_dry_run_generates_non_destructive_versioned_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            package, marker, _fake_hdfs, log, env = self._publisher_fixture(temp)
            process = subprocess.run(
                [
                    "bash",
                    str(ROOT / "hdfs_scripts" / "ingest_certified_dataset.sh"),
                    str(package),
                    str(marker),
                    "dataset-v7",
                    "--dry-run",
                ],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertFalse(log.exists())
        self.assertIn("/urbantransit/raw/dataset-v7", process.stdout)
        self.assertIn("-put", process.stdout)
        self.assertNotIn("-put -f", process.stdout)
        self.assertNotIn("-format", process.stdout)

    def test_evidence_wrapped_publisher_dry_run_captures_versions_hashes_and_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "certified"
            (package / "raw" / "trips").mkdir(parents=True)
            (package / "raw" / "trips" / "part.csv").write_text(
                "trip_id\nfixture-1\n", encoding="utf-8"
            )
            marker = root / "opaque.marker"
            marker.write_text("authoritative marker fixture", encoding="utf-8")
            result = execute_publication(
                certified_root=package,
                marker_path=marker,
                adapter=_FixtureAdapter(),
                hdfs_root="/fixture",
                output_root=root / "evidence",
                run_id="publish-fixture",
            )
            evidence_root = Path(result["evidence_artifact"]).parent
            evidence = json.loads((evidence_root / "evidence.json").read_text())
            # The exclusive run directory only exists inside the fixture scope.
            captured_logs = {
                name: (evidence_root / name).read_text(encoding="utf-8")
                for name in ("stdout.log", "stderr.log")
            }
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertEqual(result["dataset_version"], "fixture-dataset-v2")
        self.assertEqual(result["input_file_count"], 1)
        self.assertTrue(result["marker_sha256"])
        self.assertTrue(result["input_sha256"])
        self.assertIn("DRY_RUN", captured_logs["stdout.log"])
        self.assertEqual(captured_logs["stderr.log"], "")
        self.assertEqual(evidence["evidence"]["hdfs_ingestion"]["status"], "NOT_READY")
        self.assertTrue(evidence["evidence"]["hdfs_ingestion"]["notes"])

    def test_actual_publication_requires_explicit_availability_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "certified"
            (package / "raw").mkdir(parents=True)
            marker = root / "marker"
            marker.write_text("fixture", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "confirm-hdfs-available"):
                execute_publication(
                    certified_root=package,
                    marker_path=marker,
                    adapter=_FixtureAdapter(),
                    hdfs_root="/fixture",
                    output_root=root / "evidence",
                    execute=True,
                )


class PythonFeatureCliTests(unittest.TestCase):
    def _execute(self, temp, *, marker=True, input_name="certified", build_error=None):
        from python_pipeline import __main__ as feature_cli

        input_root = Path(temp) / input_name
        input_root.mkdir()
        if input_name == "certified":
            (input_root / "raw").mkdir()
        marker_path = Path(temp) / "marker.json"
        if marker:
            marker_path.write_text("not interpreted by the CLI", encoding="utf-8")
        output_root = Path(temp) / "output"
        build = patch(
            "python_pipeline.features.build_feature_frames_from_tables",
            side_effect=build_error,
        ) if build_error else patch(
            "python_pipeline.features.build_feature_frames_from_tables",
            return_value={"delay_severity": _FakeFrame()},
        )

        def fake_write(frames, output):
            target = Path(output) / "task=delay_severity" / "split=test"
            target.mkdir(parents=True)
            (target / "part.parquet").write_bytes(b"fixture bytes")

        with (
            patch.object(feature_cli, "_read_table_files", return_value=(_FakeFrame(), 2, {"fixture.csv": "abc"})),
            patch("python_pipeline.features.write_feature_frames", side_effect=fake_write),
            build,
        ):
            result = feature_cli.execute(
                input_root=input_root,
                marker_path=marker_path,
                output_root=output_root,
                run_id="run-fixture",
                adapter=_FixtureAdapter(),
                severity_thresholds_sec=(0, 60, 180, 300),
            )
        return result

    def test_missing_certification_is_not_ready_and_writes_failure_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self._execute(temp, marker=False)
            manifest = json.loads(
                (Path(result["output_artifact"]) / "run_manifest.json").read_text()
            )
            evidence = json.loads(
                (Path(result["output_artifact"]) / "evidence.json").read_text()
            )
        self.assertEqual(result["status"], "NOT_READY")
        self.assertIn("marker is missing", result["failure_reason"])
        self.assertEqual(manifest["dataset_version"], None)
        self.assertEqual(evidence["evidence"]["python_pipeline"]["status"], "NOT_READY")

    def test_spark_output_root_is_rejected_as_feature_source(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self._execute(temp, input_name="spark-output")
        self.assertEqual(result["status"], "NOT_READY")
        self.assertIn("raw directory is missing", result["failure_reason"])
        self.assertFalse(result["input_counts"])

    def test_success_records_independent_provenance_counts_hashes_and_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self._execute(temp)
            root = Path(result["output_artifact"])
            evidence = json.loads((root / "evidence.json").read_text())
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["pipeline_id"], "independent_python_features_v1")
        self.assertEqual(result["producer"], "python")
        self.assertEqual(result["dataset_version"], "fixture-dataset-v2")
        self.assertFalse(result["independent_provenance"]["spark_inputs_allowed"])
        self.assertTrue(result["output_sha256"])
        self.assertEqual(evidence["evidence"]["python_pipeline"]["status"], "SUCCEEDED")
        self.assertEqual(
            evidence["evidence"]["python_pipeline"]["output_counts"],
            {"delay_severity": 2},
        )

    def test_feature_failure_is_persisted_and_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self._execute(temp, build_error=RuntimeError("fixture failure"))
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("fixture failure", result["failure_reason"])
        self.assertEqual(result["output_counts"], {})


class ComparisonCliTests(unittest.TestCase):
    @staticmethod
    def _publish(root: Path, engine: str, *, count: int, certified=True):
        root.mkdir(parents=True)
        predictions = root / "predictions.jsonl"
        rows = []
        for index in range(count):
            prediction = float(index % 7)
            if engine == "spark" and index == 3:
                prediction += 1.5
            rows.append(
                {
                    "case_id": f"heldout-{index}",
                    "split": "test",
                    "task_name": "delay_severity",
                    "engine": engine,
                    "dataset_version": "fixture-dataset-v1",
                    "feature_version": f"{engine}-features-v2",
                    "service_date": "2026-05-01",
                    "target_start": "2026-05-01T08:00:00+05:00",
                    "target_end": "2026-05-01T09:00:00+05:00",
                    "actual": float(index % 5),
                    "prediction": prediction,
                }
            )
        predictions.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        result = {
            "status": "SUCCEEDED",
            "engine": engine,
            "task_name": "delay_severity",
            "dataset_version": "fixture-dataset-v1",
            "feature_version": f"{engine}-features-v2",
            "task_contract": {
                "producer": engine,
                "task_name": "delay_severity",
                "dataset_version": "fixture-dataset-v1",
                "certification": {"status": "CERTIFIED"} if certified else {"status": "PENDING"},
            },
            "artifact_paths": {"predictions": str(predictions)},
            "reproducibility": {"pipeline_id": f"measured-{engine}-fixture"},
        }
        path = root / "result.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return path

    def test_comparison_rejects_fewer_than_one_hundred_unseen_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            python_result = self._publish(root / "python", "python", count=99)
            spark_result = self._publish(root / "spark", "spark", count=99)
            result = compare_artifacts(
                python_result_path=python_result,
                spark_result_path=spark_result,
                dataset_version="fixture-dataset-v1",
                output_root=root / "comparison",
                run_id="run-99",
            )
        self.assertEqual(result["status"], "NOT_READY")
        self.assertIn("99 shared unseen test cases", result["reason"])

    def test_missing_pipeline_or_uncertified_pipeline_is_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            python_result = self._publish(root / "python", "python", count=100)
            result = compare_artifacts(
                python_result_path=python_result,
                spark_result_path=root / "spark" / "absent.json",
                dataset_version="fixture-dataset-v1",
                output_root=root / "comparison",
                run_id="missing-spark",
            )
            self.assertEqual(result["status"], "NOT_READY")
            self.assertIn("absent", result["reason"])

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            python_result = self._publish(
                root / "python", "python", count=100, certified=False
            )
            spark_result = self._publish(root / "spark", "spark", count=100)
            result = compare_artifacts(
                python_result_path=python_result,
                spark_result_path=spark_result,
                dataset_version="fixture-dataset-v1",
                output_root=root / "comparison",
                run_id="uncertified",
            )
        self.assertEqual(result["status"], "NOT_READY")
        self.assertIn("uncertified", result["reason"])

    def test_mismatches_provenance_and_evidence_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            python_result = self._publish(root / "python", "python", count=100)
            spark_result = self._publish(root / "spark", "spark", count=100)
            result = compare_artifacts(
                python_result_path=python_result,
                spark_result_path=spark_result,
                dataset_version="fixture-dataset-v1",
                output_root=root / "comparison",
                run_id="measured-fixture",
            )
            out = Path(result["output_artifact"]).parent
            evidence = json.loads((out / "evidence.json").read_text())
            persisted = json.loads((out / "comparison.json").read_text())
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["comparison_case_count"], 100)
        self.assertEqual(result["metrics"]["mismatching_predictions"], 1)
        self.assertEqual(result["mismatches"][0]["case_id"], "heldout-3")
        self.assertEqual(result["mismatches"][0]["delta_spark_minus_python"], 1.5)
        self.assertEqual(result["pipelines"]["python"]["feature_version"], "python-features-v2")
        self.assertTrue(result["input_sha256"])
        self.assertEqual(evidence["evidence"]["pipeline_comparison"]["status"], "SUCCEEDED")
        self.assertEqual(persisted["mismatches"], result["mismatches"])

    def test_approved_v11_policy_compares_shared_stop_events_without_rewriting_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            python_result = self._publish(root / "python", "python", count=120)
            spark_result = self._publish(root / "spark", "spark", count=120)
            for result_path, engine, indices in (
                (python_result, "python", range(120)),
                (spark_result, "spark", range(20, 140)),
            ):
                result = json.loads(result_path.read_text())
                result["dataset_version"] = "production-v1.1"
                result["task_contract"]["dataset_version"] = "production-v1.1"
                prediction_path = Path(result["artifact_paths"]["predictions"])
                rows = [json.loads(line) for line in prediction_path.read_text().splitlines()]
                for row, index in zip(rows, indices):
                    trip_id = f"TRIP{index}"
                    event_id = f"TSE{index}"
                    row["case_id"] = (
                        event_id
                        if engine == "python"
                        else f"{len(trip_id)}:{trip_id}{event_id}"
                    )
                    row["dataset_version"] = "production-v1.1"
                    row["service_date"] = "2026-05-01"
                    hour = 5 if engine == "python" else 10
                    row["target_start"] = f"2026-05-01T{hour:02d}:00:00Z"
                    row["target_end"] = row["target_start"]
                    row["actual"] = float(index % 5)
                    row["prediction"] = float(index % 7) + (
                        1.5 if engine == "spark" and index == 23 else 0.0
                    )
                prediction_path.write_text(
                    "".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8",
                )
                result_path.write_text(json.dumps(result), encoding="utf-8")
            original_python_hash = hashlib.sha256(
                Path(json.loads(python_result.read_text())["artifact_paths"]["predictions"]).read_bytes()
            ).hexdigest()
            original_spark_hash = hashlib.sha256(
                Path(json.loads(spark_result.read_text())["artifact_paths"]["predictions"]).read_bytes()
            ).hexdigest()
            result = compare_artifacts(
                python_result_path=python_result,
                spark_result_path=spark_result,
                dataset_version="production-v1.1",
                output_root=root / "comparison",
                run_id="approved-normalization",
                comparison_policy=PRODUCTION_V11_DELAY_TIME_NORMALIZATION,
            )
            persisted_hashes = result["input_sha256"]
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["comparison_case_count"], 100)
        self.assertEqual(result["alignment"]["python_only_test_cases"], 20)
        self.assertEqual(result["alignment"]["spark_only_test_cases"], 20)
        self.assertEqual(result["metrics"]["mismatching_predictions"], 1)
        self.assertEqual(result["mismatches"][0]["case_id"], "TSE23")
        self.assertEqual(persisted_hashes["python_predictions"], original_python_hash)
        self.assertEqual(persisted_hashes["spark_predictions"], original_spark_hash)


if __name__ == "__main__":
    unittest.main()

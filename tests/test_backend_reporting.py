import csv
from datetime import datetime, timezone
import io
import json
import tempfile
import unittest
from pathlib import Path

from backend.contracts import ANALYTICS_FIELDS
from backend.reporting import ReportingService


STAMP = "2026-09-26T04:00:00Z"


class FixtureRepository:
    def __init__(self, artifacts=None):
        self.artifacts = dict(artifacts or {})
        self.requests = []

    def get(self, capability, *, filters=None, body=None):
        self.requests.append((capability, dict(filters or {}), body))
        return self.artifacts.get(capability)


def certified_fixture(capability, *, dataset_version="fixture-v1", **overrides):
    artifact = {
        "status": "FIXTURE_TESTED",
        "certification_status": "FIXTURE",
        "dataset_version": dataset_version,
        "analytics_version": "analytics-fixture-v1",
        "pipeline": "python",
        "source_artifact": f"fixture/{capability}.json",
        "source_sha256": "a" * 64,
        "generated_at": STAMP,
        "provenance": {"input_sha256": "b" * 64},
    }
    artifact.update({field: [] for field in ANALYTICS_FIELDS[capability]})
    artifact.update(overrides)
    return artifact


class BackendReportingTests(unittest.TestCase):
    def service(self, repository):
        return ReportingService(
            repository,
            clock=lambda: datetime(2026, 9, 26, 4, tzinfo=timezone.utc),
            allow_fixtures=True,
        )

    def test_report_carries_source_lineage_and_only_existing_values(self):
        data = [{"key": "mean_delay_sec", "value": 127.5}]
        repository = FixtureRepository(
            {"delayAnalysis": certified_fixture("delayAnalysis", kpis=data)}
        )
        report = self.service(repository).report("delay")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["data"]["kpis"], data)
        self.assertEqual(report["meta"]["dataset_version"], "fixture-v1")
        source = report["meta"]["sources"]["delay"]
        self.assertEqual(source["pipeline"], "python")
        self.assertEqual(source["analytics_version"], "analytics-fixture-v1")
        self.assertEqual(source["source_artifact"], "fixture/delayAnalysis.json")
        self.assertEqual(source["generated_at"], STAMP)
        self.assertEqual(source["status"], "FIXTURE_TESTED")
        self.assertEqual(report["meta"]["evidence_status"], "FIXTURE-TESTED")

    def test_json_serialization_is_deterministic_and_validates_nonfinite_values(self):
        repository = FixtureRepository(
            {"dataQuality": certified_fixture("dataQuality")}
        )
        service = self.service(repository)
        report = service.report("dataQuality", generated_at=STAMP)
        first, content_type = service.export(report, "json")
        second, _ = service.export(report, "json")
        self.assertEqual(first, second)
        self.assertEqual(content_type, "application/json; charset=utf-8")
        self.assertEqual(json.loads(first), report)
        report["data"]["kpis"] = [float("nan")]
        with self.assertRaises(ValueError):
            service.export(report, "json")

    def test_csv_is_stable_and_keeps_metadata_and_structured_values(self):
        records = [{"route_id": "R1", "value": 4}]
        repository = FixtureRepository(
            {"passengerDemand": certified_fixture("passengerDemand", routeDemand=records)}
        )
        service = self.service(repository)
        report = service.report("passengerDemand", generated_at=STAMP)
        exported, content_type = service.export(report, "csv")
        self.assertEqual(content_type, "text/csv; charset=utf-8")
        rows = list(csv.DictReader(io.StringIO(exported)))
        row = next(item for item in rows if item["section"] == "routeDemand")
        self.assertEqual(json.loads(row["value_json"]), records[0])
        self.assertEqual(json.loads(row["metadata_json"])["dataset_version"], "fixture-v1")
        self.assertEqual(exported, service.export(report, "csv")[0])

    def test_filters_are_forwarded_and_results_require_matching_filter_attestation(self):
        filters = {"routeId": "R1", "period": "AM"}
        artifact = certified_fixture(
            "delayAnalysis",
            applied_filters=filters,
        )
        repository = FixtureRepository({"delayAnalysis": artifact})
        report = self.service(repository).report("delay", filters=filters)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(repository.requests[0], ("delayAnalysis", filters, None))
        self.assertEqual(report["meta"]["filters"], filters)

        artifact["applied_filters"] = {"routeId": "R2"}
        rejected = self.service(repository).report("delay", filters=filters)
        self.assertEqual(rejected["status"], "not_ready")
        self.assertIsNone(rejected["data"])

    def test_unknown_filters_are_rejected(self):
        with self.assertRaises(ValueError):
            self.service(FixtureRepository()).report("delay", filters={"invented": "x"})

    def test_missing_uncertified_or_incomplete_analytics_are_not_ready(self):
        fixture_only = FixtureRepository(
            {"delayAnalysis": certified_fixture("delayAnalysis")}
        )
        self.assertEqual(
            ReportingService(fixture_only).report("delay")["status"],
            "not_ready",
        )

        repository = FixtureRepository(
            {
                "passengerDemand": {
                    **certified_fixture("passengerDemand"),
                    "certification_status": "PENDING",
                }
            }
        )
        service = self.service(repository)
        for report in (
            service.report("delay"),
            service.report("passengerDemand"),
        ):
            self.assertEqual(report["status"], "not_ready")
            self.assertIsNone(report["data"])
            self.assertEqual(report["meta"]["evidence_status"], "PENDING CERTIFIED RUNTIME EVIDENCE")
        self.assertNotIn("kpis", service.report("delay"))

        missing_lineage = certified_fixture("delayAnalysis")
        del missing_lineage["source_artifact"]
        del missing_lineage["provenance"]
        self.assertEqual(
            self.service(FixtureRepository({"delayAnalysis": missing_lineage}))
            .report("delay")["status"],
            "not_ready",
        )

    def test_dashboard_composes_supported_sections_without_calculating_metrics(self):
        original = [{"key": "delay", "value": 37}]
        repository = FixtureRepository(
            {
                "delayAnalysis": certified_fixture("delayAnalysis", kpis=original),
                "demandForecast": certified_fixture(
                    "demandForecast", forecastSeries=[{"period": "AM", "forecast": 11}]
                ),
            }
        )
        report = self.service(repository).dashboard(
            sections=("delay", "forecast"), generated_at=STAMP
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["data"]["delay"]["data"]["kpis"], original)
        self.assertEqual(
            report["data"]["forecast"]["data"]["forecastSeries"],
            [{"period": "AM", "forecast": 11}],
        )
        self.assertEqual(report["meta"]["dataset_version"], "fixture-v1")

    def test_dashboard_with_missing_or_mixed_dataset_versions_is_not_ready(self):
        repository = FixtureRepository(
            {
                "delayAnalysis": certified_fixture("delayAnalysis"),
                "demandForecast": certified_fixture(
                    "demandForecast", dataset_version="other-v1"
                ),
            }
        )
        report = self.service(repository).dashboard(sections=("delay", "forecast"))
        self.assertEqual(report["status"], "not_ready")
        self.assertEqual(
            {section["status"] for section in report["data"].values()},
            {"not_ready"},
        )
        del repository.artifacts["demandForecast"]
        report = self.service(repository).dashboard(sections=("delay", "forecast"))
        self.assertEqual(report["status"], "not_ready")
        self.assertEqual(report["data"]["forecast"]["data"], None)

    def test_model_summary_retains_measured_model_result_fields(self):
        metrics = {"test": {"mae": 3.25}}
        result = certified_fixture(
            "demandForecast",
            task_name="passenger_demand",
            task_contract={"certification": {"status": "FIXTURE"}},
            feature_version="features-v1",
            model_name="fixture-model",
            selected_model="fixture-model",
            metrics=metrics,
            baseline_metrics={"test": {"mae": 4.0}},
            model_comparison=[{"model": "fixture-model", "test": metrics["test"]}],
        )
        report = self.service(FixtureRepository({"demandForecast": result})).model_result_summary(
            "demandForecast"
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["data"]["metrics"], metrics)
        self.assertEqual(report["data"]["selected_model"], "fixture-model")
        self.assertEqual(
            report["meta"]["sources"]["modelResultSummary"]["feature_version"],
            "features-v1",
        )

    def test_supported_report_families_pass_through_their_contract_fields(self):
        cases = (
            ("routePerformance", "routesAndStops"),
            ("stopPerformance", "routesAndStops"),
            ("occupancy", "occupancy"),
            ("routeClustering", "routeClusters"),
            ("recommendations", "recommendations"),
            ("pipelineComparison", "pipelineComparison"),
            ("dataQuality", "dataQuality"),
        )
        repository = FixtureRepository(
            {
                capability: certified_fixture(
                    capability,
                    **{
                        field: [{"fixture_value": field}]
                        for field in ANALYTICS_FIELDS[capability]
                    },
                )
                for _, capability in cases
            }
        )
        service = self.service(repository)
        for report_type, capability in cases:
            with self.subTest(report_type=report_type):
                report = service.report(report_type)
                self.assertEqual(report["status"], "ready")
                expected = set(ANALYTICS_FIELDS[capability])
                self.assertEqual(set(report["data"]), expected)

    def test_output_file_creation_never_overwrites(self):
        service = self.service(FixtureRepository({"delayAnalysis": certified_fixture("delayAnalysis")}))
        report = service.report("delay", generated_at=STAMP)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delay.json"
            service.write_export(report, path, "json")
            original = path.read_text(encoding="utf-8")
            with self.assertRaises(FileExistsError):
                service.write_export(report, path, "json")
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_unsupported_report_export_and_empty_dashboard_inputs_fail_explicitly(self):
        service = self.service(FixtureRepository())
        with self.assertRaises(ValueError):
            service.report("pdf")
        with self.assertRaises(ValueError):
            service.export({}, "pdf")
        with self.assertRaises(ValueError):
            service.dashboard(sections=())
        with self.assertRaises(ValueError):
            service.model_result_summary("recommendations")


if __name__ == "__main__":
    unittest.main()

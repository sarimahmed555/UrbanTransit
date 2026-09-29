import json
import tempfile
import unittest
from pathlib import Path

from backend import ApiApplication
from backend.contracts import COMPOSITION_PATHS, ENDPOINTS, ROUTES
from backend.repository import ArtifactRepository, InMemoryArtifactRepository
from backend.security.contracts import Principal, Role
from backend.serving.config import PostgresConfig
from backend.serving.database import PostgresDatabase
from backend.serving.repository import PostgresServingRepository


FIXTURE_PRINCIPAL = Principal("fixture-admin", "fixture-admin", Role.ADMINISTRATOR, 9999999999)


class BackendApiFoundationTests(unittest.TestCase):
    def test_all_frontend_capabilities_have_routes(self):
        expected = {
            "/api/v1/analytics/executive-summary",
            "/api/v1/analytics/passenger-demand",
            "/api/v1/analytics/routes-stops",
            "/api/v1/analytics/delay-analysis",
            "/api/v1/analytics/occupancy-crowding",
            "/api/v1/analytics/demand-forecast",
            "/api/v1/analytics/route-clusters",
            "/api/v1/analytics/passenger-flow",
            "/api/v1/analytics/what-if",
            "/api/v1/analytics/recommendations",
            "/api/v1/analytics/data-quality",
            "/api/v1/analytics/system-status",
        }
        self.assertTrue(expected.issubset({path for _, path in ROUTES}))
        paths = [(endpoint.method, endpoint.path) for endpoint in ENDPOINTS] + list(COMPOSITION_PATHS)
        self.assertEqual(len(paths), len(set(paths)))

    def test_missing_artifact_is_explicitly_not_ready(self):
        response = ApiApplication(InMemoryArtifactRepository()).handle(
            "GET",
            "/api/v1/analytics/passenger-demand",
            query={"routeId": "R1", "period": "AM"},
            principal=FIXTURE_PRINCIPAL,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body["status"], "not_ready")
        self.assertEqual(response.body["meta"]["state"], "NOT_READY")
        self.assertEqual(response.body["meta"]["filters"], {"routeId": "R1", "period": "AM"})
        self.assertIsNone(response.body["routeDemand"])

    def test_artifact_response_is_forwarded_without_fabricating_values(self):
        repository = InMemoryArtifactRepository(
            {
                "executiveSummary": {
                    "status": "SUCCEEDED",
                    "certification_status": "CERTIFIED",
                    "dataset_version": "fixture-v1",
                    "analytics_version": "analytics-1",
                    "generated_at": "2026-09-26T04:00:00Z",
                    "source_artifact": "fixture/executive.json",
                    "provenance": {"fixture": True},
                    "kpis": [{"key": "ridership", "value": 17}],
                    "trends": [],
                    "meta": {"state": "ready", "source": "certified-test-fixture"},
                }
            }
        )
        response = ApiApplication(repository).handle("GET", "/api/v1/analytics/executive-summary", principal=FIXTURE_PRINCIPAL)
        self.assertEqual(response.body["kpis"][0]["value"], 17)
        self.assertEqual(response.body["meta"]["source"], "certified-test-fixture")
        self.assertEqual(repository.requests[0][0], "executiveSummary")

    def test_what_if_requires_body_and_passes_filters_and_body_to_adapter(self):
        repository = InMemoryArtifactRepository({"whatIf": {"baseline": [], "scenario": [], "assumptions": []}})
        app = ApiApplication(repository)
        missing = app.handle("POST", "/api/v1/analytics/what-if", principal=FIXTURE_PRINCIPAL)
        self.assertEqual(missing.status_code, 400)
        body = {
            "schema_version": "1.0",
            "baseline_evidence": {"source_id": "result-1", "pointer": "/baseline"},
            "scenario_type": "add_vehicle",
            "entity_ids": {"route_id": "R2", "direction_id": 0},
            "proposed_changes": {},
        }
        response = app.handle(
            "POST",
            "/api/v1/analytics/what-if",
            query={"routeId": "R2"},
            body=body,
            principal=FIXTURE_PRINCIPAL,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(repository.requests[0][1], {"routeId": "R2"})
        self.assertEqual(repository.requests[0][2]["scenario_type"], "add_vehicle")
        invalid = app.handle(
            "POST",
            "/api/v1/analytics/what-if",
            body={"operation": "capacity"},
            principal=FIXTURE_PRINCIPAL,
        )
        self.assertEqual(invalid.status_code, 400)

    def test_json_artifact_repository_reads_only_configured_result_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dataQuality.json"
            path.write_text(json.dumps({"kpis": [], "issueFamilies": [], "reconciliation": {}}), encoding="utf-8")
            result = ArtifactRepository(directory).get("dataQuality")
        self.assertEqual(result["issueFamilies"], [])

    def test_invalid_filter_is_a_client_error(self):
        response = ApiApplication(InMemoryArtifactRepository()).handle(
            "GET",
            "/api/v1/analytics/delay-analysis",
            query={"notAFilter": "x"},
            principal=FIXTURE_PRINCIPAL,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.body["error"]["code"], "invalid_request")

    def test_filtering_requires_artifact_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delayAnalysis.json"
            path.write_text(
                json.dumps({"status": "SUCCEEDED", "kpis": []}), encoding="utf-8"
            )
            repository = ArtifactRepository(directory)
            self.assertIsNone(repository.get("delayAnalysis", filters={"routeId": "R1"}))
            path.write_text(
                json.dumps(
                    {
                        "status": "SUCCEEDED",
                        "applied_filters": {"routeId": "R1"},
                        "kpis": [],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                repository.get("delayAnalysis", filters={"routeId": "R1"})["applied_filters"],
                {"routeId": "R1"},
            )

    def test_catalog_and_dataset_status_do_not_fake_serving_data(self):
        dispatcher = ApiApplication(InMemoryArtifactRepository())
        self.assertEqual(dispatcher.catalog("routes")["status"], "NOT_READY")
        self.assertIsNone(dispatcher.catalog("routes")["data"])
        self.assertEqual(dispatcher.dataset_status()["status"], "NOT_READY")

    def test_postgres_repository_injects_through_existing_repository_boundary(self):
        database = PostgresDatabase(PostgresConfig.from_env({}))
        repository = PostgresServingRepository(database)
        response = ApiApplication(repository).handle(
            "GET",
            "/api/v1/analytics/passenger-demand",
            principal=FIXTURE_PRINCIPAL,
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.body["status"], "NOT_CONFIGURED")
        self.assertEqual(
            ApiApplication(repository).service.execute("systemHealth")["health"]["state"],
            "NOT_CONFIGURED",
        )

    def test_api_redacts_secrets_and_local_filesystem_paths(self):
        repository = InMemoryArtifactRepository(
            {
                "executiveSummary": {
                    "status": "SUCCEEDED",
                    "certification_status": "CERTIFIED",
                    "dataset_version": "fixture-v1",
                    "analytics_version": "analytics-1",
                    "generated_at": "2026-09-26T04:00:00Z",
                    "source_artifact": "/srv/private/results/executive.json",
                    "provenance": {
                        "password": "secret",
                        "token_digest": "digest",
                        "database_dsn": "postgres://secret",
                    },
                    "kpis": [],
                    "trends": [],
                }
            }
        )
        response = ApiApplication(repository).handle(
            "GET",
            "/api/v1/analytics/executive-summary",
            principal=FIXTURE_PRINCIPAL,
        )
        text = json.dumps(response.body)
        self.assertNotIn("/srv/private", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("token_digest", text)
        self.assertIn("executive.json", text)


if __name__ == "__main__":
    unittest.main()

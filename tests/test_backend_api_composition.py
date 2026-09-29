import importlib.util
import json
import secrets
import time
import unittest

from backend.contracts import COMPOSITION_PATHS, ENDPOINTS
from backend.repository import InMemoryArtifactRepository
from backend.security.config import SecurityConfig
from backend.security.contracts import Role, Session, User
from backend.security.service import token_digest
from backend.app import ApiApplication
from auth_fixtures import InMemoryAuthRepository


STAMP = "2026-09-26T04:00:00Z"


def certified(capability, *, filters=None, **fields):
    from backend.contracts import ANALYTICS_FIELDS

    result = {
        "status": "SUCCEEDED",
        "certification_status": "CERTIFIED",
        "dataset_version": "fixture-dataset-v1",
        "analytics_version": "fixture-analytics-v1",
        "generated_at": STAMP,
        "source_artifact": "/private/fixture/results.json",
        "provenance": {"source": "tiny test fixture"},
    }
    result.update({name: [] for name in ANALYTICS_FIELDS[capability]})
    if filters:
        result["applied_filters"] = filters
    result.update(fields)
    return result


class ApiCompositionContractTests(unittest.TestCase):
    def test_contract_paths_are_unique_across_existing_and_composition_routes(self):
        paths = [(endpoint.method, endpoint.path) for endpoint in ENDPOINTS]
        paths.extend(COMPOSITION_PATHS)
        self.assertEqual(len(paths), len(set(paths)))

    def test_reports_preserve_recommendation_contract_and_filters(self):
        report_data = [{"recommendation_id": "fixture-rec-1", "priority": "HIGH"}]
        repo = InMemoryArtifactRepository(
            {
                "recommendations": certified(
                    "recommendations",
                    filters={"routeId": "R1"},
                    actionQueue=report_data,
                )
            }
        )
        response = ApiApplication(repo).report(
            "recommendations", query={"routeId": "R1"}
        )
        self.assertEqual(response["status"], "ready")
        self.assertEqual(response["data"]["actionQueue"], report_data)
        self.assertEqual(response["meta"]["sources"]["recommendations"]["dataset_version"], "fixture-dataset-v1")

    def test_what_if_request_validation_rejects_non_contract_body(self):
        from backend.security.contracts import Principal

        app = ApiApplication(InMemoryArtifactRepository())
        response = app.handle(
            "POST",
            "/api/v1/analytics/what-if",
            body={"scenario": "fake"},
            principal=Principal("fixture", "fixture", Role.ADMINISTRATOR, 9999999999),
        )
        self.assertEqual(response.status_code, 400)

    def test_postgres_readiness_boundary_reports_not_configured_without_secrets(self):
        from backend.serving.config import PostgresConfig
        from backend.serving.database import PostgresDatabase
        from backend.serving.repository import PostgresServingRepository

        repository = PostgresServingRepository(
            PostgresDatabase(PostgresConfig.from_env({}))
        )
        app = ApiApplication(repository)
        health = app.service.execute("systemHealth")
        self.assertEqual(health["health"], {"state": "NOT_CONFIGURED", "database": "postgresql"})
        self.assertNotIn("password", json.dumps(health).lower())


HAS_HTTP = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))


@unittest.skipUnless(HAS_HTTP, "FastAPI/httpx unavailable; no packages installed")
class FastApiCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.fastapi_app import create_app

        cls.TestClient = TestClient
        cls.create_app = staticmethod(create_app)

    def build_app(self, artifacts, *, role=Role.ANALYST):
        now = int(time.time())
        user = User("fixture-user", "fixture-user", "not-used", role)
        auth_repository = InMemoryAuthRepository([user])
        token = "A" * 43
        auth_repository.create_session(
            Session(token_digest(token), user.user_id, now, now + 600, 1)
        )
        app = self.create_app(
            InMemoryArtifactRepository(artifacts),
            auth_repository=auth_repository,
            security_config=SecurityConfig(mode="test"),
            allow_test_mode=True,
        )
        return app, {"Authorization": f"Bearer {token}"}

    def test_fastapi_paths_unique_and_readiness_explicit(self):
        app, headers = self.build_app({})
        routes = [
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
            if method not in {"HEAD", "OPTIONS"}
        ]
        self.assertEqual(len(routes), len(set(routes)))
        with self.TestClient(app) as client:
            response = client.get("/api/v1/system/readiness", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "NOT_CONFIGURED")
            catalog = client.get("/api/v1/catalog/routes", headers=headers)
            self.assertEqual(catalog.json()["status"], "NOT_READY")
            dataset = client.get("/api/v1/system/dataset-status", headers=headers)
            self.assertEqual(dataset.json()["status"], "NOT_READY")

    def test_registration_creates_evaluator_account_that_can_sign_in(self):
        app, _ = self.build_app({})
        password = secrets.token_urlsafe(24)
        with self.TestClient(app) as client:
            created = client.post(
                "/api/v1/auth/register",
                json={"username": "new.user@example.com", "password": password},
            )
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["username"], "new.user@example.com")

            login = client.post(
                "/api/v1/auth/login",
                json={"username": "new.user@example.com", "password": password},
            )
            self.assertEqual(login.status_code, 200)
            identity = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            )
            self.assertEqual(identity.status_code, 200)
            self.assertEqual(identity.json()["role"], "Evaluator")

            elevated = client.post(
                "/api/v1/auth/register",
                json={"username": "role.try", "password": password, "role": "Administrator"},
            )
            self.assertEqual(elevated.status_code, 400)

    def test_json_report_and_csv_download_preserve_lineage_safely(self):
        filters = {"routeId": "R1"}
        artifact = certified(
            "delayAnalysis",
            filters=filters,
            kpis=[{"key": "delay", "value": 12}],
        )
        app, headers = self.build_app({"delayAnalysis": artifact})
        with self.TestClient(app) as client:
            report = client.get("/api/v1/reports/delay?routeId=R1", headers=headers)
            self.assertEqual(report.status_code, 200)
            self.assertEqual(report.json()["data"]["kpis"][0]["value"], 12)
            self.assertEqual(report.json()["meta"]["filters"], filters)
            self.assertNotIn("/private/fixture", report.text)
            download = client.get(
                "/api/v1/reports/delay/download?routeId=R1&format=csv",
                headers=headers,
            )
            self.assertEqual(download.status_code, 200)
            self.assertTrue(download.headers["content-type"].startswith("text/csv"))
            self.assertEqual(
                download.headers["content-disposition"],
                'attachment; filename="urbantransit-delay.csv"',
            )
            self.assertIn("fixture-dataset-v1", download.text)

    def test_missing_artifact_and_filter_mismatch_do_not_yield_values(self):
        app, headers = self.build_app({})
        with self.TestClient(app) as client:
            response = client.get("/api/v1/reports/delay", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "not_ready")
            self.assertIsNone(response.json()["data"])

        app, headers = self.build_app(
            {
                "delayAnalysis": certified(
                    "delayAnalysis",
                    filters={"routeId": "R2"},
                    kpis=[{"key": "delay", "value": 999}],
                )
            }
        )
        with self.TestClient(app) as client:
            response = client.get(
                "/api/v1/reports/delay?routeId=R1", headers=headers
            )
            self.assertEqual(response.json()["status"], "not_ready")
            self.assertIsNone(response.json()["data"])

    def test_csv_validation_and_rbac_are_enforced(self):
        app, headers = self.build_app({}, role=Role.OPERATOR)
        with self.TestClient(app) as client:
            invalid = client.get(
                "/api/v1/reports/delay/download?format=pdf", headers=headers
            )
            self.assertEqual(invalid.status_code, 400)
            diagnostics = client.get("/api/v1/reports/dataQuality", headers=headers)
            self.assertEqual(diagnostics.status_code, 403)

    def test_what_if_validation_uses_existing_scenario_request_contract(self):
        app, headers = self.build_app({}, role=Role.ANALYST)
        with self.TestClient(app) as client:
            response = client.post(
                "/api/v1/analytics/what-if",
                json={"scenario": "dummy"},
                headers=headers,
            )
            self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

from backend.evidence import EvidenceRepository


ROOT = Path(__file__).resolve().parents[1]


class EvidenceRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository = EvidenceRepository(ROOT)

    def test_serves_exactly_audited_task_results_and_preserves_partial_scope(self):
        result = self.repository.status(postgres_state="NOT_CONFIGURED")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["data"]["completed_predictive_tasks"]), 5)
        self.assertFalse(result["data"]["full_srs_ml_closure"])
        self.assertEqual(
            [item["task_name"] for item in result["data"]["partial_tasks"]],
            ["delay_service_analytics", "anomaly_detection"],
        )

    def test_demand_evaluation_and_comparison_use_frozen_authoritative_metrics(self):
        demand = self.repository.task("passenger_demand")["data"]["pipelines"]
        self.assertAlmostEqual(demand["python"]["test_metrics"]["mae"], 10.893826656805667)
        self.assertAlmostEqual(demand["spark"]["test_metrics"]["mae"], 10.899681220436031)
        self.assertTrue(demand["python"]["baseline_vs_selected"]["improved"])
        comparison = self.repository.comparison()["data"]
        self.assertEqual(comparison["shared_test_cases"], 2429)
        self.assertEqual(comparison["truths_matched"], 2429)
        self.assertEqual(comparison["prediction_agreements"], 2422)
        self.assertEqual(comparison["prediction_disagreements"], 7)
        self.assertAlmostEqual(comparison["agreement_rate"], 0.9971181556195965)
        self.assertTrue(all(item["truth_match"] for item in comparison["mismatches"]))
        self.assertTrue(all("actual" in item for item in comparison["mismatches"]))
        self.assertEqual({item["actual"] for item in comparison["mismatches"]}, {0, 2})

    def test_crowding_limitations_and_weak_cluster_separation_remain_explicit(self):
        crowding = self.repository.task("crowding_risk")["data"]
        for pipeline in crowding["pipelines"].values():
            over_capacity = next(
                item for item in pipeline["test_metrics"]["per_class"]
                if item["label"] == "OVER_CAPACITY"
            )
            self.assertEqual(over_capacity["recall"], 0)
        self.assertIn("zero recall", " ".join(crowding["limitations"]).lower())
        clustering = self.repository.task("route_clustering")["data"]
        self.assertIn("weak out-of-time", " ".join(clustering["limitations"]).lower())

    def test_prediction_sample_is_bounded_and_hash_verified(self):
        result = self.repository.task("passenger_demand", prediction_limit=2)
        for pipeline in result["data"]["pipelines"].values():
            sample = pipeline["prediction_sample"]
            self.assertEqual(len(sample), 2)
            self.assertTrue(all(item["actual"] is not None for item in sample))
            self.assertTrue(all(item["prediction"] is not None for item in sample))
            self.assertTrue(pipeline["artifact_status"]["predictions_exist"])
            self.assertNotIn(str(ROOT), str(pipeline))

    def test_unknown_task_and_unbounded_prediction_limit_are_rejected(self):
        with self.assertRaises(KeyError):
            self.repository.task("invented_task")
        with self.assertRaises(ValueError):
            self.repository.task("passenger_demand", prediction_limit=51)


try:
    from fastapi.testclient import TestClient
    from backend.fastapi_app import create_app
    from backend.repository import InMemoryArtifactRepository

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


@unittest.skipUnless(HAS_FASTAPI, "FastAPI/httpx are not installed")
class EvidenceFastApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(
            InMemoryArtifactRepository(),
            evidence_repository=EvidenceRepository(ROOT),
            allow_local_evidence_http=True,
        )

    def test_read_only_evidence_routes_are_public_and_cors_enabled_for_local_frontend(self):
        with TestClient(
            self.app, base_url="http://localhost", client=("127.0.0.1", 50100)
        ) as client:
            health = client.get("/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(
                health.json()["data"]["serving_database"]["state"], "NOT_CONFIGURED"
            )
            response = client.get("/api/v1/evidence/comparison/delay")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["prediction_agreements"], 2422)
            preflight = client.options(
                "/api/v1/evidence/status",
                headers={
                    "Origin": "http://localhost:4173",
                    "Access-Control-Request-Method": "GET",
                },
            )
            self.assertEqual(preflight.status_code, 200)
            self.assertEqual(
                preflight.headers["access-control-allow-origin"], "http://localhost:4173"
            )

    def test_other_api_routes_remain_fail_closed_without_auth_database(self):
        with TestClient(self.app, base_url="https://localhost") as client:
            response = client.get("/api/v1/system/readiness")
            self.assertEqual(response.status_code, 503)

    def test_unknown_task_is_not_found_and_predictions_are_bounded(self):
        with TestClient(
            self.app, base_url="http://localhost", client=("127.0.0.1", 50101)
        ) as client:
            unknown = client.get("/api/v1/evidence/tasks/not_a_real_task")
            self.assertEqual(unknown.status_code, 404)
            invalid = client.get(
                "/api/v1/evidence/tasks/passenger_demand?prediction_limit=51"
            )
            self.assertEqual(invalid.status_code, 400)

    def test_local_http_exemption_is_limited_to_loopback_evidence_routes(self):
        with TestClient(
            self.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50102)
        ) as client:
            evidence = client.get("/api/v1/evidence/status")
            self.assertEqual(evidence.status_code, 200)
            external_host = client.get(
                "/api/v1/evidence/status", headers={"Host": "api.example.test"}
            )
            self.assertEqual(external_host.status_code, 400)
            self.assertEqual(external_host.json()["error"]["code"], "https_required")
            other_route = client.get("/api/v1/system/readiness")
            self.assertEqual(other_route.status_code, 400)
            self.assertEqual(other_route.json()["error"]["code"], "https_required")
        with TestClient(
            self.app, base_url="http://localhost", client=("192.0.2.10", 50103)
        ) as remote_client:
            remote = remote_client.get("/api/v1/evidence/status")
            self.assertEqual(remote.status_code, 400)
            self.assertEqual(remote.json()["error"]["code"], "https_required")

    def test_evidence_cors_does_not_grant_unlisted_origins(self):
        with TestClient(
            self.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50104)
        ) as client:
            response = client.options(
                "/api/v1/evidence/status",
                headers={
                    "Origin": "http://untrusted.example",
                    "Access-Control-Request-Method": "GET",
                },
            )
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("access-control-allow-origin", response.headers)
            same_network_host = client.options(
                "/api/v1/evidence/status",
                headers={
                    "Origin": "http://localhost:4174",
                    "Access-Control-Request-Method": "GET",
                },
            )
            self.assertEqual(same_network_host.status_code, 400)
            self.assertNotIn("access-control-allow-origin", same_network_host.headers)

    def test_local_http_flag_requires_evidence_repository(self):
        with self.assertRaisesRegex(
            ValueError, "only when audited evidence routes are configured"
        ):
            create_app(
                InMemoryArtifactRepository(),
                allow_local_evidence_http=True,
            )


if __name__ == "__main__":
    unittest.main()

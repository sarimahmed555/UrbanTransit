"""Lightweight contract and DB-API fixture tests; no PostgreSQL is started."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import uuid

from backend.serving.config import PostgresConfig
from backend.serving.contracts import (
    DatasetVersion,
    FeatureVersion,
    ModelRun,
    RouteRecord,
    RouteStopRecord,
    ScheduleStopTimeRecord,
    ServingResult,
    StopRecord,
)
from backend.serving.database import PostgresDatabase
from backend.serving.migrate import MIGRATIONS
from backend.serving.repository import PostgresServingRepository


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = MIGRATIONS / "001_serving.sql"
STAMP = datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc)
DIGEST = "a" * 64


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.last_query = None
        self.closed = False

    def execute(self, query, params=None):
        self.last_query = query
        self.connection.executed.append((query, params))
        if self.connection.fail_on and self.connection.fail_on in query:
            raise ValueError("fixture database constraint violation")
        if query.strip().startswith("SELECT to_regclass"):
            self.connection.next_row = ("app_serving.serving_results",)
        elif query.strip().startswith("SELECT payload::text"):
            self.connection.next_row = self.connection.result_row
        elif query.strip().startswith("SELECT 1 FROM app_serving.schema_migrations"):
            self.connection.next_row = None
        else:
            self.connection.next_row = None

    def fetchone(self):
        return self.connection.next_row

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, *, fail_on=None, result_row=None):
        self.fail_on = fail_on
        self.result_row = result_row
        self.next_row = None
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class LedgerCursor(FakeCursor):
    """Reports the migration ledger so a second run must be a no-op."""

    def execute(self, query, params=None):
        super().execute(query, params)
        statement = query.strip()
        if statement.startswith("SELECT 1 FROM app_serving.schema_migrations"):
            self.connection.next_row = (
                (params[0],) if params[0] in self.connection.applied else None
            )
        elif statement.startswith("INSERT INTO app_serving.schema_migrations"):
            self.connection.applied.add(params[0])


class LedgerConnection(FakeConnection):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.applied = set()

    def cursor(self):
        return LedgerCursor(self)


class DatabaseContractTests(unittest.TestCase):
    def test_schema_is_serving_scope_with_relations_and_dashboard_indexes(self):
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        expected = {
            "dataset_versions", "routes", "stops", "vehicles", "route_patterns",
            "route_stops", "schedules", "schedule_stop_times", "trips",
            "feature_versions", "model_runs", "serving_results", "reports",
        }
        created = set(re.findall(r"CREATE TABLE IF NOT EXISTS app_serving\.(\w+)", sql))
        self.assertEqual(created, expected | {"schema_migrations"})
        for constraint in (
            "PRIMARY KEY (dataset_version, route_id)",
            "UNIQUE (dataset_version, route_code)",
            "REFERENCES app_serving.route_patterns(dataset_version, pattern_id)",
            "REFERENCES app_serving.stops(dataset_version, stop_id)",
            "REFERENCES app_serving.feature_versions(dataset_version, feature_version)",
            "REFERENCES app_serving.model_runs(model_run_id, dataset_version)",
            "REFERENCES app_serving.trips(dataset_version, trip_id)",
            "REFERENCES app_serving.serving_results(result_id)",
        ):
            self.assertIn(constraint, sql)
        self.assertIn("ix_serving_results_kind_status_time", sql)
        self.assertIn("ix_serving_results_route_window", sql)
        self.assertIn("ix_serving_results_stop_window", sql)
        self.assertIn("ix_trips_service_date_route", sql)
        self.assertNotRegex(sql, r"CREATE TABLE IF NOT EXISTS app_serving\.(tickets|gps_events|trip_stop_events)")

    def test_environment_configuration_and_secrets(self):
        missing = PostgresConfig.from_env({})
        self.assertFalse(missing.configured)
        self.assertEqual(PostgresDatabase(missing).readiness()["state"], "NOT_CONFIGURED")
        with self.assertRaisesRegex(RuntimeError, "NOT_CONFIGURED"):
            PostgresDatabase(missing).connect()

        env = {
            "PGHOST": "db.internal", "PGPORT": "5544", "PGDATABASE": "urbantransit",
            "PGUSER": "serving", "PGPASSWORD": "never-print-me",
        }
        config = PostgresConfig.from_env(env)
        self.assertTrue(config.configured)
        self.assertEqual(config.port, 5544)
        self.assertNotIn("never-print-me", repr(config))
        self.assertEqual(config.connection_parameters()["sslmode"], "require")
        with self.assertRaises(ValueError):
            PostgresConfig.from_env({**env, "PGPORT": "70000"})
        with self.assertRaises(ValueError):
            PostgresConfig.from_env({**env, "PGSSLMODE": "invalid"})

    def test_contract_rejects_invalid_entities_timestamps_and_json(self):
        route = RouteRecord(
            "dataset-1", "route-1", "R1", "Main Street Route", "BUS", "FEEDER", False
        )
        self.assertEqual(route.route_name, "Main Street Route")
        with self.assertRaises(ValueError):
            StopRecord("dataset-1", "stop-1", "S1", "Stop", 91, 0)
        with self.assertRaises(ValueError):
            RouteStopRecord("dataset-1", "rs1", "p1", "s1", 0, 0, True, True)
        with self.assertRaises(ValueError):
            ScheduleStopTimeRecord("dataset-1", "sst1", "schedule-1", "rs1", 1, 300, 299)
        with self.assertRaises(ValueError):
            RouteRecord("dataset-1", "route-1", "R1", "Route", "BUS", "FEEDER", 1)
        with self.assertRaises(ValueError):
            ServingResult(
                uuid.uuid4(), "whatIf", "ESTIMATE", "dataset-1", "result.json",
                DIGEST, STAMP, {}, scenario_type=None,
            )
        with self.assertRaises(ValueError):
            ServingResult(
                uuid.uuid4(), "passengerDemand", "SUCCEEDED", "dataset-1",
                "result.json", DIGEST, STAMP, {"metric": float("nan")},
            )
        with self.assertRaises(ValueError):
            ServingResult(
                uuid.uuid4(), "passengerDemand", "SUCCEEDED", "dataset-1",
                "result.json", DIGEST, "2026-09-26T05:00:00",
                {},
            )
        result = ServingResult(
            uuid.uuid4(), "passengerDemand", "SUCCEEDED", "dataset-1",
            "hdfs://warehouse/results.json", DIGEST, STAMP, {},
        )
        self.assertEqual(result.source_artifact, "hdfs://warehouse/results.json")

    def test_model_and_result_keep_dataset_feature_run_artifact_lineage(self):
        dataset = DatasetVersion("dataset-1", "CERTIFIED", "cert.json", DIGEST, STAMP)
        feature = FeatureVersion("dataset-1", "features-2", "spark", "features.json", DIGEST, STAMP)
        run_id = uuid.uuid4()
        run = ModelRun(
            run_id, dataset.dataset_version, feature.feature_version, "passenger_demand",
            "random_forest", "model-3", "SUCCEEDED", STAMP, "model-result.json",
            DIGEST, {"mae": 1.25},
        )
        result = ServingResult(
            uuid.uuid4(), "demandForecast", "SUCCEEDED", dataset.dataset_version,
            "forecast.json", DIGEST, STAMP, {"forecastSeries": []},
            feature_version=feature.feature_version, model_run_id=run.model_run_id,
            analytics_version="analytics-4", window_start=STAMP,
            window_end=datetime(2026, 9, 27, tzinfo=timezone.utc),
            route_id="route-1",
        )
        self.assertEqual(run.dataset_version, result.dataset_version)
        self.assertEqual(run.feature_version, result.feature_version)
        self.assertEqual(run.model_run_id, result.model_run_id)
        self.assertEqual(result.source_artifact, "forecast.json")
        self.assertEqual(result.source_sha256, DIGEST)
        self.assertEqual(result.generated_at, STAMP)
        self.assertEqual(result.analytics_version, "analytics-4")

    def test_transaction_commit_and_rollback(self):
        connections = []

        def connect(**kwargs):
            connection = FakeConnection(fail_on="INSERT INTO")
            connections.append(connection)
            return connection

        database = PostgresDatabase(
            PostgresConfig("localhost", 5432, "db", "user", "secret"),
            connect_callable=connect,
        )
        repository = PostgresServingRepository(database)
        with self.assertRaisesRegex(ValueError, "constraint violation"):
            repository.register_dataset(DatasetVersion("dataset-1", "FIXTURE"))
        failed = connections[-1]
        self.assertTrue(failed.rolled_back)
        self.assertFalse(failed.committed)
        self.assertTrue(failed.closed)

        successful_connection = FakeConnection()
        database = PostgresDatabase(
            PostgresConfig("localhost", 5432, "db", "user", "secret"),
            connect_callable=lambda **kwargs: successful_connection,
        )
        PostgresServingRepository(database).register_dataset(
            DatasetVersion("dataset-1", "FIXTURE")
        )
        self.assertTrue(successful_connection.committed)
        self.assertFalse(successful_connection.rolled_back)
        self.assertTrue(successful_connection.closed)

    def test_repository_query_is_parameterized_and_never_serves_fixtures(self):
        connection = FakeConnection(result_row=('{"kpis":[]}',))
        config = PostgresConfig("localhost", 5432, "db", "user", "secret")
        repository = PostgresServingRepository(
            PostgresDatabase(config, connect_callable=lambda **kwargs: connection)
        )
        result = repository.get(
            "passengerDemand",
            filters={"routeId": "route-1", "startDate": "2026-01-01"},
        )
        self.assertEqual(result, {"kpis": []})
        query, params = next(
            item for item in connection.executed if "SELECT payload::text" in item[0]
        )
        self.assertIn("result_status <> 'FIXTURE_TESTED'", query)
        self.assertIn("route_id = %s", query)
        self.assertEqual(params, ("passengerDemand", "route-1", "2026-01-01"))
        self.assertTrue(connection.committed)

    def test_repository_does_not_create_fallback_persistence(self):
        database = PostgresDatabase(PostgresConfig.from_env({}))
        repository = PostgresServingRepository(database)
        with self.assertRaisesRegex(RuntimeError, "NOT_CONFIGURED"):
            repository.get("passengerDemand")
        self.assertEqual(repository.health()["state"], "NOT_CONFIGURED")
        self.assertEqual(database.readiness(), {"state": "NOT_CONFIGURED", "database": "postgresql"})

    def test_configured_database_failure_is_not_ready_without_error_details(self):
        def unavailable(**kwargs):
            raise RuntimeError("private connection details")

        database = PostgresDatabase(
            PostgresConfig("host", 5432, "db", "user", "private"),
            connect_callable=unavailable,
        )
        self.assertEqual(
            database.readiness(),
            {"state": "NOT_READY", "database": "postgresql"},
        )

    def test_migration_files_are_versioned_and_serving_local(self):
        names = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
        self.assertEqual(names, ["001_serving.sql"])
        self.assertRegex(names[0], r"^[0-9]{3}_[a-z0-9_]+\.sql$")


class ServingCliTests(unittest.TestCase):
    """The migration/readiness CLI is a real entry point; no server is started."""

    ENV = {
        "PGHOST": "db.internal", "PGPORT": "5432", "PGDATABASE": "urbantransit",
        "PGUSER": "serving", "PGPASSWORD": "never-print-me",
    }

    def _run(self, argv, *, env):
        from backend.serving import __main__ as serving_cli

        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
            code = serving_cli.main(argv)
        return code, json.loads(out.getvalue()), err.getvalue()

    def test_unconfigured_environment_never_connects_and_exits_nonzero(self):
        def forbidden(**kwargs):
            raise AssertionError("no connection may be attempted")

        with patch.object(PostgresDatabase, "connect", forbidden):
            readiness_code, readiness, _ = self._run(["readiness"], env={})
            migrate_code, migrate, _ = self._run(["migrate"], env={})
        self.assertEqual((readiness_code, migrate_code), (2, 2))
        self.assertEqual(readiness["status"], "NOT_CONFIGURED")
        self.assertEqual(migrate["status"], "NOT_READY")
        self.assertEqual(migrate["applied"], [])
        self.assertNotIn("never-print-me", json.dumps(migrate))

    def test_invalid_port_environment_is_not_configured(self):
        code, result, _ = self._run(
            ["readiness"], env={**self.ENV, "PGPORT": "70000"}
        )
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "NOT_CONFIGURED")

    def test_connection_failure_never_reports_applied_migrations(self):
        def unavailable(**kwargs):
            raise RuntimeError("fixture driver absent")

        from backend.serving import __main__ as serving_cli

        with patch.object(
            serving_cli,
            "PostgresDatabase",
            lambda config: PostgresDatabase(config, connect_callable=unavailable),
        ):
            code, result, stderr = self._run(["migrate"], env=self.ENV)
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["applied"], [])
        self.assertIn("did not complete", stderr)

    def test_migration_cli_applies_local_sql_once_through_the_ledger(self):
        connection = LedgerConnection()

        from backend.serving import __main__ as serving_cli

        with patch.object(
            serving_cli,
            "PostgresDatabase",
            lambda config: PostgresDatabase(
                config, connect_callable=lambda **kwargs: connection
            ),
        ):
            code, result, _ = self._run(["migrate"], env=self.ENV)
            second_code, second, _ = self._run(["migrate"], env=self.ENV)
        self.assertEqual((code, second_code), (0, 0))
        self.assertEqual(result["applied"], ["001_serving.sql"])
        self.assertEqual(second["applied"], [])
        statements = " ".join(query for query, _ in connection.executed)
        self.assertIn("CREATE SCHEMA IF NOT EXISTS app_serving", statements)
        self.assertIn("app_serving.serving_results", statements)
        self.assertTrue(connection.committed)

    def test_readiness_cli_reports_only_measured_state(self):
        def connected(**kwargs):
            return FakeConnection()

        from backend.serving import __main__ as serving_cli

        with patch.object(
            serving_cli,
            "PostgresDatabase",
            lambda config: PostgresDatabase(config, connect_callable=connected),
        ):
            code, result, _ = self._run(["readiness"], env=self.ENV)
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["database"], "postgresql")


if __name__ == "__main__":
    unittest.main()

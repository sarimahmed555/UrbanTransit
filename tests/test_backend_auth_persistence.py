"""Persistent-auth contract, composition and launch tests.

Standard library and DB-API fixtures only: no PostgreSQL server, no driver, no
ASGI server, no production data and no credentials are used. Runtime
verification against a real driver/server remains pending and is reported as
pending, not as a pass.
"""

import ast
import importlib.util
import io
import json
import os
import re
import secrets
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from auth_fixtures import InMemoryAuthRepository
from backend.repository import InMemoryArtifactRepository
from backend.security.config import SecurityConfig
from backend.security.contracts import Permission, Role, SecurityError, Session, User
from backend.security.passwords import hash_password
from backend.security.postgres_repository import (
    PostgresAuthRepository,
    postgres_config_from_dsn,
)
from backend.security.rbac import require_permission
from backend.security.service import AuthService, token_digest
from backend.serving.config import PostgresConfig
from backend.serving.database import PostgresDatabase

ROOT = Path(__file__).resolve().parents[1]
NOW = 1800000000
FIXED_DSN = "postgresql://auth_fixture:never-print-me@db.internal:5544/urbantransit"
USER_ID = "0f0f0f0f-0000-4000-8000-000000000001"
STORED_HASH = "scrypt$v1$131072$8$1$c2FsdHNhbHRzYWx0c2E$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGE"
USER_ROW = (USER_ID, "fixture-analyst", STORED_HASH, "Analyst", True, 1)


def statements(connection):
    return " ".join(query for query, _ in connection.executed)


class Store:
    """A tiny scripted DB-API store used to prove the adapter's own mapping.

    It is not a PostgreSQL emulator: it returns rows shaped the way the
    reviewed `app_auth` contract and this adapter's SQL expect, and it records
    every statement so tests can inspect the bound parameters.
    """

    def __init__(self, users=(USER_ROW,), sessions=(), relations=("app_auth",)):
        self.users = list(users)
        self.sessions = dict(sessions)
        self.attempts = {}
        self.relations = set(relations)
        self.fail_on = None
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def rows_for(self, query, params):
        flat = " ".join(query.split())
        if self.fail_on and self.fail_on in query:
            raise ValueError("fixture database failure")
        if flat.startswith("SELECT to_regclass"):
            return (params[0] if params[0] in self.relations else None,)
        if flat.startswith(
            "SELECT user_id, username, password_hash, role, active, security_version "
            "FROM app_auth.users WHERE username"
        ):
            return next((row for row in self.users if row[1] == params[0]), None)
        if flat.startswith(
            "SELECT user_id, username, password_hash, role, active, security_version "
            "FROM app_auth.users WHERE user_id"
        ):
            return next((row for row in self.users if row[0] == params[0]), None)
        if flat.startswith("INSERT INTO app_auth.users"):
            if any(row[1] == params[1] for row in self.users):
                return None
            self.users.append((params[0], params[1], params[2], params[3], True, 1))
            return (params[0],)
        if flat.startswith("INSERT INTO app_auth.sessions"):
            self.sessions[params[0]] = tuple(params)
            return None
        if flat.startswith("SELECT token_digest, user_id, EXTRACT(EPOCH FROM issued_at)"):
            return self.sessions.get(params[0])
        if flat.startswith("UPDATE app_auth.sessions SET revoked"):
            stored = self.sessions.get(params[0])
            if stored:
                self.sessions[params[0]] = stored[:5] + (True,)
            return None
        if flat.startswith("INSERT INTO app_auth.login_attempt_buckets"):
            bucket = (params[0], params[1])
            self.attempts[bucket] = self.attempts.get(bucket, 0) + 1
            return (self.attempts[bucket],)
        raise AssertionError("unexpected statement: " + flat)


class StoreCursor:
    def __init__(self, connection):
        self.connection = connection
        self.closed = False

    def execute(self, query, params=None):
        self.connection.executed.append((query, params))
        self.connection.next_row = self.connection.store.rows_for(query, params)

    def fetchone(self):
        return self.connection.next_row

    def close(self):
        self.closed = True


class StoreConnection:
    def __init__(self, store):
        self.store = store
        self.executed = store.executed
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.next_row = None

    def cursor(self):
        return StoreCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def repository(store):
    """Wire the adapter to a scripted DB-API connection (never a real server)."""
    connection = StoreConnection(store)
    config = PostgresConfig("db.internal", 5432, "urbantransit", "auth", "secret")
    database = PostgresDatabase(config, connect_callable=lambda **kwargs: connection)
    return PostgresAuthRepository(database), connection


class AuthDsnConfigurationTests(unittest.TestCase):
    """Configuration comes from the existing environment boundary only."""

    def test_dsn_translates_to_the_existing_postgres_config(self):
        config = postgres_config_from_dsn(FIXED_DSN)
        self.assertEqual(
            (config.host, config.port, config.database, config.username),
            ("db.internal", 5544, "urbantransit", "auth_fixture"),
        )
        self.assertEqual(config.connection_parameters()["sslmode"], "require")
        self.assertNotIn("never-print-me", repr(config))

    def test_dsn_sslmode_and_defaults_follow_libpq_rules(self):
        self.assertEqual(
            postgres_config_from_dsn(FIXED_DSN + "?sslmode=verify-full").sslmode,
            "verify-full",
        )
        self.assertEqual(
            postgres_config_from_dsn("postgres://auth:secret@db/urbantransit").port, 5432
        )
        for dsn in (FIXED_DSN + "?sslmode=maybe", FIXED_DSN.replace(":5544", ":70000")):
            with self.assertRaisesRegex(RuntimeError, "invalid"):
                postgres_config_from_dsn(dsn)

    def test_incomplete_or_absent_configuration_fails_explicitly(self):
        for dsn in (
            "postgresql://auth@db.internal/urbantransit",
            "postgresql://auth:secret@db.internal",
            "postgresql://auth:secret@/urbantransit",
            "mysql://auth:secret@db.internal/urbantransit",
            "",
        ):
            with self.assertRaisesRegex(RuntimeError, "NOT_CONFIGURED"):
                postgres_config_from_dsn(dsn)
        with self.assertRaisesRegex(RuntimeError, "NOT_CONFIGURED"):
            PostgresAuthRepository.from_security_config(SecurityConfig())
        with self.assertRaisesRegex(RuntimeError, "NOT_CONFIGURED"):
            PostgresAuthRepository.from_security_config(
                SecurityConfig.from_env({"UTIQ_AUTH_DATABASE_DSN": "postgresql://auth@db/db"})
            )

    def test_repository_declares_itself_non_fixture_and_reuses_shared_settings(self):
        with self.assertRaises(TypeError):
            PostgresAuthRepository(object())
        adapter = PostgresAuthRepository.from_security_config(
            SecurityConfig(database_dsn=FIXED_DSN)
        )
        self.assertIs(adapter.is_fixture, False)
        self.assertEqual(adapter.persistence, "POSTGRESQL")
        self.assertEqual(adapter.database.config.host, "db.internal")
        self.assertNotIn("never-print-me", repr(adapter.database.config))


class PersistentRepositorySqlTests(unittest.TestCase):
    """Parameterized SQL, transactions, typed mapping and fail-closed errors."""

    def test_every_statement_binds_parameters_and_never_interpolates_values(self):
        store = Store()
        adapter, connection = repository(store)
        digest = "b" * 64
        adapter.get_user_by_username("fixture-analyst")
        adapter.get_user_by_id(USER_ID)
        adapter.create_session(Session(digest, USER_ID, NOW, NOW + 900, 1, False))
        adapter.get_session(digest)
        adapter.revoke_session(digest)
        adapter.consume_login_attempt("a" * 72, NOW, 300, 10)
        self.assertEqual(len(connection.executed), 6)
        for query, params in connection.executed:
            self.assertIsNotNone(params, "every statement must use bind parameters")
            for value in ("fixture-analyst", digest, USER_ID, "Analyst", STORED_HASH):
                self.assertNotIn(value, query)
            # No Python-side formatting of values: only driver placeholders.
            self.assertNotIn("{}".format(USER_ID), query)
            self.assertEqual(sorted(re.findall(r"%(?![s%])", query)), [])
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertTrue(connection.closed)

    def test_user_and_session_rows_map_to_typed_domain_values(self):
        store = Store()
        adapter, connection = repository(store)
        user = adapter.get_user_by_username("fixture-analyst")
        self.assertEqual(
            (user.user_id, user.username, user.role, user.active, user.security_version),
            (USER_ID, "fixture-analyst", Role.ANALYST, True, 1),
        )
        self.assertEqual(user.password_hash, STORED_HASH)
        self.assertNotIn(STORED_HASH, repr(user))
        self.assertIsNotNone(adapter.get_user_by_id(USER_ID))
        self.assertIsNone(adapter.get_user_by_username("absent-user"))
        self.assertIsNone(adapter.get_user_by_id("00000000-0000-4000-8000-000000000009"))
        self.assertIsNone(adapter.get_session("c" * 64))

        issued, expires, digest = NOW, NOW + 900, "d" * 64
        adapter.create_session(Session(digest, user.user_id, issued, expires, 1, False))
        query, params = connection.executed[-1]
        self.assertIn("to_timestamp(%s)", query)
        self.assertEqual(params, (digest, user.user_id, issued, expires, 1, False))
        stored = adapter.get_session(digest)
        self.assertEqual(
            (
                stored.token_digest, stored.user_id, stored.issued_at, stored.expires_at,
                stored.security_version, stored.revoked,
            ),
            (digest, user.user_id, issued, expires, 1, False),
        )
        self.assertIsInstance(stored.issued_at, int)
        self.assertIsInstance(stored.expires_at, int)
        self.assertNotIn(digest, repr(stored))
        self.assertIn("EXTRACT(EPOCH FROM issued_at)::bigint", statements(connection))

        adapter.revoke_session(digest)
        self.assertIs(adapter.get_session(digest).revoked, True)
        self.assertIs(store.sessions[digest][5], True)
        self.assertIn("UPDATE app_auth.sessions SET revoked", statements(connection))

    def test_create_user_binds_hash_and_handles_duplicate_username(self):
        store = Store()
        adapter, connection = repository(store)
        user = User("new-user-id", "new.user", STORED_HASH, Role.ANALYST)
        self.assertTrue(adapter.create_user(user))
        self.assertFalse(adapter.create_user(user))
        query, params = connection.executed[0]
        self.assertIn("ON CONFLICT (username) DO NOTHING", query)
        self.assertEqual(params, (user.user_id, user.username, STORED_HASH, "Analyst"))
        self.assertNotIn(user.username, query)
        self.assertNotIn(STORED_HASH, query)

    def test_unknown_stored_role_or_record_fails_closed(self):
        store = Store(users=[USER_ROW[:3] + ("Superuser",) + USER_ROW[4:]])
        adapter, _ = repository(store)
        with self.assertRaisesRegex(RuntimeError, "record is invalid"):
            adapter.get_user_by_username("fixture-analyst")
        store.users = [USER_ROW[:5] + ("not-an-integer",)]
        with self.assertRaisesRegex(RuntimeError, "record is invalid"):
            adapter.get_user_by_username("fixture-analyst")
        store.users = [USER_ROW]
        store.sessions["e" * 64] = ("e" * 64, USER_ID, None, NOW + 900, 1, False)
        with self.assertRaisesRegex(RuntimeError, "record is invalid"):
            adapter.get_session("e" * 64)

    def test_login_attempt_counter_is_atomic_bucketed_and_boolean(self):
        store = Store()
        adapter, connection = repository(store)
        key = "k" * 72
        self.assertIs(adapter.consume_login_attempt(key, NOW, 300, 1), True)
        self.assertIs(adapter.consume_login_attempt(key, NOW, 300, 1), False)
        self.assertIs(adapter.consume_login_attempt(key, NOW + 299, 300, 1), False)
        self.assertIs(adapter.consume_login_attempt(key, NOW + 300, 300, 1), True)
        query, params = connection.executed[0]
        self.assertIn("ON CONFLICT (bucket_key, window_start)", query)
        self.assertIn("DO UPDATE SET attempts = app_auth.login_attempt_buckets.attempts + 1", query)
        self.assertIn("RETURNING attempts", query)
        self.assertEqual(params, (key, NOW - NOW % 300))
        self.assertEqual(
            sorted(store.attempts), sorted({(key, NOW), (key, NOW + 300 - NOW % 300)})
        )
        for invalid in (
            (key, NOW, 0, 1), (key, NOW, 300, 0), ("", NOW, 300, 1), ("k" * 73, NOW, 300, 1)
        ):
            with self.assertRaises(ValueError):
                adapter.consume_login_attempt(*invalid)

    def test_invalid_contract_arguments_are_rejected_before_any_sql(self):
        adapter, connection = repository(Store())
        for call, argument in (
            (adapter.get_user_by_username, ""),
            (adapter.get_user_by_username, None),
            (adapter.get_user_by_id, 7),
            (adapter.get_session, ""),
            (adapter.revoke_session, None),
        ):
            with self.assertRaises(ValueError):
                call(argument)
        with self.assertRaises(TypeError):
            adapter.create_session({"token_digest": "x"})
        self.assertEqual(connection.executed, [])

    def test_database_failure_rolls_back_and_never_falls_back(self):
        store = Store()
        store.fail_on = "app_auth.sessions"
        adapter, connection = repository(store)
        with self.assertRaisesRegex(ValueError, "fixture database failure"):
            adapter.create_session(Session("f" * 64, USER_ID, NOW, NOW + 900, 1, False))
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)
        self.assertEqual(store.sessions, {})

    def test_unconfigured_database_is_not_configured_without_fallback(self):
        adapter = PostgresAuthRepository(PostgresDatabase(PostgresConfig.from_env({})))
        self.assertIs(adapter.is_fixture, False)
        self.assertEqual(adapter.readiness(), {"state": "NOT_CONFIGURED", "database": "postgresql"})
        calls = (
            lambda: adapter.get_user_by_username("fixture-analyst"),
            lambda: adapter.get_user_by_id(USER_ID),
            lambda: adapter.get_session("e" * 64),
            lambda: adapter.revoke_session("e" * 64),
            lambda: adapter.consume_login_attempt("f" * 72, NOW, 300, 5),
        )
        for call in calls:
            with self.assertRaisesRegex(RuntimeError, "NOT_CONFIGURED"):
                call()

    def test_readiness_measures_auth_relations_only(self):
        store = Store(relations=("app_auth", "app_auth.users", "app_auth.sessions"))
        adapter, _ = repository(store)
        self.assertEqual(adapter.readiness(), {"state": "NOT_READY", "database": "postgresql"})
        store.relations.add("app_auth.login_attempt_buckets")
        self.assertEqual(adapter.readiness(), {"state": "READY", "database": "postgresql"})
        store.fail_on = "to_regclass"
        self.assertEqual(adapter.readiness(), {"state": "NOT_READY", "database": "postgresql"})


class AuthServicePersistentRepositoryTests(unittest.TestCase):
    """The existing auth service driven through the persistent adapter."""

    def setUp(self):
        self.password = secrets.token_urlsafe(24)
        self.store = Store(
            users=[(USER_ID, "fixture-analyst", hash_password(self.password), "Analyst", True, 1)]
        )
        self.adapter, self.connection = repository(self.store)
        self.clock = NOW
        self.service = AuthService(
            self.adapter, config=SecurityConfig(mode="test"), clock=lambda: self.clock
        )

    def assert_security_error(self, status, call):
        with self.assertRaises(SecurityError) as caught:
            call()
        self.assertEqual(caught.exception.status_code, status)
        return caught.exception

    def login(self):
        return self.service.login("fixture-analyst", self.password, client_id="fixture-client")

    def test_login_authenticate_logout_round_trip_through_postgres(self):
        login = self.login()
        self.assertEqual(login["token_type"], "bearer")
        self.assertEqual(login["expires_at"], NOW + 900)
        self.assertNotIn(login["access_token"], statements(self.connection))
        self.assertNotIn(self.password, statements(self.connection))
        # Only the digest is persisted; the raw bearer token never is.
        self.assertEqual(list(self.store.sessions), [token_digest(login["access_token"])])
        principal = self.service.authenticate("Bearer " + login["access_token"])
        self.assertEqual((principal.role, principal.username), (Role.ANALYST, "fixture-analyst"))
        self.assertNotIn(self.password, json.dumps(principal.public()))
        self.assertEqual(
            self.service.logout("Bearer " + login["access_token"])["status"], "signed_out"
        )
        self.assert_security_error(
            401, lambda: self.service.authenticate("Bearer " + login["access_token"])
        )

    def test_expiry_revocation_deactivation_and_security_version_are_enforced(self):
        login = self.login()
        header = "Bearer " + login["access_token"]
        self.clock = login["expires_at"] - 1
        self.assertEqual(self.service.authenticate(header).role, Role.ANALYST)
        self.clock = login["expires_at"]
        self.assert_security_error(401, lambda: self.service.authenticate(header))

        self.clock = NOW
        self.adapter.revoke_session(token_digest(login["access_token"]))
        self.assert_security_error(401, lambda: self.service.authenticate(header))

        second = self.login()
        self.store.users[0] = self.store.users[0][:4] + (False, 1)
        self.assert_security_error(
            401, lambda: self.service.authenticate("Bearer " + second["access_token"])
        )
        self.store.users[0] = self.store.users[0][:4] + (True, 2)
        self.assert_security_error(
            401, lambda: self.service.authenticate("Bearer " + second["access_token"])
        )
        # A session issued after the change inherits the stored version.
        self.clock += 1
        third = self.login()
        self.assertEqual(self.store.sessions[token_digest(third["access_token"])][4], 2)
        self.assertEqual(
            self.service.authenticate("Bearer " + third["access_token"]).role, Role.ANALYST
        )
        self.assert_security_error(
            401, lambda: self.service.login("fixture-analyst", "wrong", client_id="fixture")
        )

    def test_rbac_is_not_bypassed_by_persistent_storage(self):
        principal = self.service.authenticate("Bearer " + self.login()["access_token"])
        self.assertEqual(require_permission(principal, Permission.ANALYTICS_READ), principal)
        self.assertEqual(require_permission(principal, Permission.SCENARIOS_EXECUTE), principal)
        self.assert_security_error(403, lambda: require_permission(principal, Permission.USERS_MANAGE))
        self.assert_security_error(403, lambda: require_permission(principal, Permission.CONFIGURATION_MANAGE))
        self.store.users[0] = self.store.users[0][:3] + ("Evaluator",) + self.store.users[0][4:]
        self.clock += 1
        reloaded = self.service.authenticate("Bearer " + self.login()["access_token"])
        self.assertEqual(reloaded.role, Role.EVALUATOR)
        self.assert_security_error(403, lambda: require_permission(reloaded, Permission.SCENARIOS_EXECUTE))

    def test_rate_limiter_is_shared_across_workers_in_production_mode(self):
        config = SecurityConfig(database_dsn=FIXED_DSN, login_account_limit=1)
        worker_a_repository, _ = repository(self.store)
        worker_b_repository, _ = repository(self.store)
        with patch("backend.security.service.time.time", return_value=NOW):
            worker_a = AuthService(worker_a_repository, config=config)
            worker_b = AuthService(worker_b_repository, config=config)
            issued = worker_a.login("fixture-analyst", self.password, client_id="a")
            blocked = self.assert_security_error(
                429, lambda: worker_b.login("fixture-analyst", self.password, client_id="b")
            )
        self.assertEqual(issued["token_type"], "bearer")
        self.assertEqual(blocked.code, "rate_limited")
        # Both workers counted into the same durable bucket for this account.
        self.assertEqual(max(self.store.attempts.values()), 2)
        self.assertTrue(all(window == NOW - NOW % 300 for _, window in self.store.attempts))

    def test_persistence_outage_is_not_ready_and_never_authenticates(self):
        self.store.fail_on = "app_auth.users"
        error = self.assert_security_error(503, self.login)
        self.assertEqual(error.code, "NOT_READY")
        self.assertNotIn(self.password, str(error))
        self.assertEqual(self.store.sessions, {})

    def test_production_mode_accepts_only_the_persistent_adapter(self):
        with self.assertRaises(ValueError):
            AuthService(InMemoryAuthRepository(), config=SecurityConfig(database_dsn=FIXED_DSN))
        service = AuthService(self.adapter, config=SecurityConfig(database_dsn=FIXED_DSN))
        self.assertIs(service.repository, self.adapter)
        self.assertEqual(service.readiness()["status"], "CONFIGURED")
        self.assertEqual(service.readiness()["persistence"], "POSTGRESQL")

    def test_production_mode_without_a_driver_reports_not_ready(self):
        dsn = "postgresql://auth:secret@127.0.0.1:1/absent"
        service = AuthService(
            PostgresAuthRepository.from_security_config(SecurityConfig(database_dsn=dsn)),
            config=SecurityConfig(database_dsn=dsn),
        )
        error = self.assert_security_error(
            503, lambda: service.login("fixture-analyst", self.password, client_id="fixture")
        )
        self.assertEqual(error.code, "NOT_READY")
        self.assertNotIn("secret", str(error))

    def test_missing_configuration_still_reports_not_configured(self):
        unconfigured = AuthService(config=SecurityConfig())
        self.assertIsNone(unconfigured.repository)
        error = self.assert_security_error(
            503, lambda: unconfigured.login("fixture-analyst", self.password, client_id="x")
        )
        self.assertEqual(error.code, "NOT_CONFIGURED")


HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(HAS_FASTAPI, "FastAPI unavailable; no packages installed")
class ApplicationCompositionTests(unittest.TestCase):
    """Dependency composition selects persistent auth only when configured."""

    def build(self, **kwargs):
        from backend.fastapi_app import create_app

        return create_app(InMemoryArtifactRepository(), **kwargs)

    def assert_security_error(self, status, call):
        with self.assertRaises(SecurityError) as caught:
            call()
        self.assertEqual(caught.exception.status_code, status)
        return caught.exception

    def test_configured_deployment_composes_the_postgres_auth_repository(self):
        app = self.build(security_config=SecurityConfig(database_dsn=FIXED_DSN))
        composed = app.state.auth_repository
        self.assertIsInstance(composed, PostgresAuthRepository)
        self.assertIs(composed.is_fixture, False)
        self.assertIs(app.state.auth_service.repository, composed)
        self.assertEqual(app.state.auth_service.readiness()["persistence"], "POSTGRESQL")
        self.assertNotIn("never-print-me", repr(composed.database.config))

    def test_unconfigured_deployment_keeps_fail_closed_not_configured(self):
        app = self.build(security_config=SecurityConfig())
        self.assertIsNone(app.state.auth_repository)
        self.assertNotIsInstance(app.state.auth_repository, InMemoryAuthRepository)
        # Unconfigured authentication never reports a usable state.
        self.assert_security_error(503, app.state.auth_service.readiness)
        error = self.assert_security_error(
            503,
            lambda: app.state.auth_service.login("fixture-analyst", "irrelevant", client_id="fixture"),
        )
        self.assertEqual(error.code, "NOT_CONFIGURED")

    def test_fixture_injection_remains_possible_for_tests_only(self):
        fixture = InMemoryAuthRepository()
        app = self.build(
            auth_repository=fixture,
            security_config=SecurityConfig(mode="test"),
            allow_test_mode=True,
        )
        self.assertIs(app.state.auth_repository, fixture)
        self.assertEqual(app.state.auth_service.readiness()["persistence"], "TEST_FIXTURE")
        with self.assertRaises(ValueError):
            self.build(auth_repository=fixture, security_config=SecurityConfig(mode="test"))
        with self.assertRaises(ValueError):
            self.build(
                auth_repository=InMemoryAuthRepository(),
                security_config=SecurityConfig(database_dsn=FIXED_DSN),
            )

    def test_unusable_dsn_fails_composition_instead_of_starting_without_auth(self):
        with self.assertRaisesRegex(RuntimeError, "NOT_CONFIGURED"):
            self.build(
                security_config=SecurityConfig(database_dsn="postgresql://auth@db.internal/urbantransit")
            )

    def test_http_login_against_persistent_auth_never_fabricates_success(self):
        from fastapi.testclient import TestClient

        dsn = "postgresql://auth:secret@127.0.0.1:1/absent"
        app = self.build(security_config=SecurityConfig(database_dsn=dsn))
        with TestClient(app) as client:
            # Production mode refuses a non-HTTPS request before any lookup.
            self.assertEqual(client.get("/api/v1/auth/me").status_code, 400)
        with TestClient(app, base_url="https://testserver") as client:
            response = client.post(
                "/api/v1/auth/login", json={"username": "fixture-analyst", "password": "irrelevant"}
            )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["status"], "NOT_READY")
            self.assertNotIn("secret", response.text)


class AsgiLaunchTests(unittest.TestCase):
    """One supported launch path; these tests never start a server."""

    def setUp(self):
        from backend.serving import __main__ as serving_cli

        self.cli = serving_cli
        self.env = {
            "PGHOST": "db.internal", "PGPORT": "5432", "PGDATABASE": "urbantransit",
            "PGUSER": "serving", "PGPASSWORD": "never-print-me",
            "UTIQ_AUTH_DATABASE_DSN": FIXED_DSN,
        }

    def run_cli(self, argv, *, env):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
            code = self.cli.main(argv)
        printed = out.getvalue().strip()
        return code, (json.loads(printed) if printed else None)

    def test_serve_requires_configuration_and_starts_nothing(self):
        started = []

        def forbidden():
            started.append("runner")
            raise AssertionError("no ASGI runner may be loaded")

        with patch.object(self.cli, "load_asgi_runner", forbidden):
            unconfigured_code, unconfigured = self.run_cli(
                ["serve", "--artifact-root", str(ROOT)], env={}
            )
            no_dsn_code, no_dsn = self.run_cli(
                ["serve", "--artifact-root", str(ROOT)],
                env={key: value for key, value in self.env.items() if key != "UTIQ_AUTH_DATABASE_DSN"},
            )
            broken_port_code, broken_port = self.run_cli(
                ["serve", "--artifact-root", str(ROOT), "--port", "70000"], env=self.env
            )
        self.assertEqual((unconfigured_code, no_dsn_code), (2, 2))
        self.assertEqual(unconfigured["status"], "NOT_CONFIGURED")
        self.assertEqual(no_dsn["status"], "NOT_CONFIGURED")
        self.assertEqual(broken_port_code, 2)
        self.assertEqual(broken_port["status"], "NOT_CONFIGURED")
        self.assertEqual(started, [])
        self.assertNotIn("never-print-me", json.dumps(unconfigured))

    def test_serve_requires_an_existing_artifact_directory(self):
        def forbidden():
            raise AssertionError("no ASGI runner may be loaded")

        with patch.object(self.cli, "load_asgi_runner", forbidden):
            code, payload = self.run_cli(
                ["serve", "--artifact-root", str(ROOT / "absent-results")], env=self.env
            )
        self.assertEqual((code, payload["status"]), (2, "NOT_CONFIGURED"))
        with self.assertRaises(SystemExit):
            self.run_cli(["serve"], env=self.env)
        with self.assertRaisesRegex(ValueError, "artifact-root"):
            self.cli._serve("   ", "127.0.0.1", 8000)

    def test_missing_asgi_server_is_reported_without_starting_one(self):
        if importlib.util.find_spec("uvicorn"):
            self.assertTrue(callable(self.cli.load_asgi_runner()))
            return

        def absent():
            raise RuntimeError(
                "ASGI server is NOT_CONFIGURED; install approved uvicorn during deployment"
            )

        with patch.object(self.cli, "load_asgi_runner", absent):
            code, payload = self.run_cli(["serve", "--artifact-root", str(ROOT)], env=self.env)
        self.assertEqual((code, payload["status"]), (2, "NOT_CONFIGURED"))
        self.assertIn("NOT_CONFIGURED", payload["reason"])

    @unittest.skipUnless(HAS_FASTAPI, "FastAPI unavailable; no packages installed")
    def test_composed_application_is_passed_to_the_single_asgi_entry_point(self):
        launched = []

        def runner(application, *, host, port):
            launched.append((application, host, port))

        with patch.object(self.cli, "load_asgi_runner", lambda: runner):
            code, _ = self.run_cli(
                ["serve", "--artifact-root", str(ROOT), "--host", "0.0.0.0", "--port", "8123"],
                env=self.env,
            )
        self.assertEqual(code, 0)
        application, host, port = launched[0]
        self.assertEqual((host, port), ("0.0.0.0", 8123))
        self.assertIsInstance(application.state.auth_repository, PostgresAuthRepository)
        self.assertEqual(application.state.auth_service.readiness()["persistence"], "POSTGRESQL")
        paths = {route.path for route in application.routes}
        self.assertIn("/api/v1/auth/login", paths)
        self.assertIn("/api/v1/system/readiness", paths)

    def test_launch_module_imports_no_server_or_driver_at_module_scope(self):
        tree = ast.parse(Path(self.cli.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported & {"uvicorn", "fastapi", "starlette", "psycopg", "psycopg2"}, set())
        self.assertFalse(hasattr(self.cli, "uvicorn"))

    def test_backend_keeps_one_launch_surface_and_one_application_factory(self):
        sources = {
            str(path.relative_to(ROOT)): path.read_text(encoding="utf-8")
            for path in (ROOT / "backend").rglob("*.py")
        }
        self.assertEqual(
            sorted(name for name, text in sources.items() if "uvicorn" in text),
            ["backend/serving/__main__.py"],
        )
        self.assertEqual(
            sorted(name for name, text in sources.items() if "def create_app(" in text),
            ["backend/fastapi_app.py"],
        )
        readme = (ROOT / "backend/serving/README.md").read_text(encoding="utf-8")
        self.assertIn("python3 -m backend.serving serve", readme)

    def test_launch_module_never_starts_a_server_on_import(self):
        source = Path(self.cli.__file__).read_text(encoding="utf-8")
        # The server callable is obtained lazily and only the composed
        # application object is handed to it; nothing runs at import time.
        self.assertEqual(source.count("uvicorn.run"), 1)
        self.assertIn("return uvicorn.run", source)
        self.assertIn("run(application, host=host, port=port)", source)
        self.assertIn('if __name__ == "__main__":', source)
        self.assertTrue(callable(self.cli.build_application))
        self.assertTrue(callable(self.cli.main))


if __name__ == "__main__":
    unittest.main()

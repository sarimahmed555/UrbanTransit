"""Ephemeral credentials and fixture storage only; no real DB, server or users."""
from dataclasses import replace
import importlib.util
import json
import secrets
import unittest
from unittest.mock import patch

from auth_fixtures import InMemoryAuthRepository
from backend.app import ApiApplication
from backend.contracts import ENDPOINTS, EndpointContract
from backend.repository import InMemoryArtifactRepository
from backend.security.config import SecurityConfig
from backend.security.contracts import User, Role, Permission, Principal, SecurityError
from backend.security.passwords import hash_password, verify_password
from backend.security.rbac import require_permission, required_permission
from backend.security.service import AuthService, token_digest, bearer_token


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_urlsafe(24)
        cls.password_hash = hash_password(cls.password)

    def setUp(self):
        self.now = 1800000000
        self.users = [User(f"fixture-{role.value}", role.value.lower(), self.password_hash, role) for role in Role]
        self.repo = InMemoryAuthRepository(self.users)
        self.service = AuthService(self.repo, config=SecurityConfig(mode="test"), clock=lambda: self.now)

    def login(self, role=Role.ANALYST):
        return self.service.login(role.value.lower(), self.password, client_id="fixture-client")

    def assert_security_error(self, status, call):
        with self.assertRaises(SecurityError) as caught:
            call()
        self.assertEqual(caught.exception.status_code, status)
        return caught.exception

    def test_password_hash_salt_and_verification(self):
        another = hash_password(self.password)
        self.assertNotEqual(another, self.password_hash)
        self.assertTrue(verify_password(self.password, self.password_hash))
        self.assertFalse(verify_password(secrets.token_urlsafe(24), self.password_hash))
        self.assertFalse(verify_password(self.password, "malformed"))
        self.assertFalse(verify_password(self.password, self.password_hash.replace("131072", "2")))
        self.assertNotIn(self.password, self.password_hash)
        self.assertNotIn(self.password_hash, repr(self.users[0]))
        with self.assertRaises(ValueError):
            hash_password(secrets.token_hex(2))

    def test_valid_login_current_identity_and_no_plaintext_storage(self):
        login = self.login()
        principal = self.service.authenticate("Bearer "+login["access_token"])
        self.assertEqual(principal.role, Role.ANALYST)
        self.assertEqual(login["token_type"], "bearer")
        self.assertEqual(login["expires_in"], 900)
        self.assertNotIn("password_hash", principal.public())
        self.assertNotIn(self.password, repr(self.repo.__dict__))
        self.assertNotIn(login["access_token"], repr(self.repo.__dict__))
        self.assertIn(token_digest(login["access_token"]), self.repo.sessions)
        self.assertNotIn(login["access_token"], repr(next(iter(self.repo.sessions.values()))))

    def test_registration_creates_limited_evaluator_account_and_can_sign_in(self):
        password = secrets.token_urlsafe(24)
        created = self.service.register("New.User@example.com", password, client_id="signup-client")
        self.assertEqual(created, {"status": "created", "username": "new.user@example.com"})
        user = self.repo.get_user_by_username(created["username"])
        self.assertEqual(user.role, Role.EVALUATOR)
        self.assertNotEqual(user.password_hash, password)
        self.assertTrue(verify_password(password, user.password_hash))
        self.assertEqual(
            self.service.register("NEW.USER@EXAMPLE.COM", password, client_id="signup-client"),
            created,
        )
        login = self.service.login(created["username"], password, client_id="login-client")
        self.assertEqual(self.service.authenticate("Bearer "+login["access_token"]).role, Role.EVALUATOR)

    def test_registration_rejects_invalid_password_duplicate_username_and_rate_limit(self):
        self.assert_security_error(400, lambda: self.service.register("valid.user", "short", client_id="signup-client"))
        password = secrets.token_urlsafe(24)
        self.service.register("new.user", password, client_id="signup-client")
        duplicate = self.service.register("NEW.USER", password, client_id="signup-client")
        self.assertEqual(duplicate["username"], "new.user")
        self.assert_security_error(401, lambda: self.service.login("new.user", secrets.token_urlsafe(24), client_id="duplicate-login"))
        self.service = AuthService(self.repo, config=SecurityConfig(mode="test", login_client_limit=1), clock=lambda: self.now)
        self.service.register("other.user", password, client_id="limited-signup")
        self.assert_security_error(429, lambda: self.service.register("third.user", password, client_id="limited-signup"))

    def test_unknown_wrong_and_inactive_share_safe_error(self):
        wrong = self.assert_security_error(401, lambda: self.service.login("analyst", secrets.token_urlsafe(24), client_id="fixture"))
        unknown = self.assert_security_error(401, lambda: self.service.login("unknown-user", self.password, client_id="fixture"))
        self.repo.users[self.users[2].user_id] = replace(self.users[2], active=False)
        inactive = self.assert_security_error(401, lambda: self.service.login("analyst", self.password, client_id="fixture"))
        self.assertEqual(wrong.body(), unknown.body())
        self.assertEqual(wrong.body(), inactive.body())
        self.assertEqual(self.repo.sessions, {})
        for secret in (self.password, self.password_hash, "unknown-user"):
            self.assertNotIn(secret, json.dumps(wrong.body()))

    def test_token_randomness_tamper_expiry_and_unknown(self):
        a, b = self.login(), self.login()
        self.assertNotEqual(a["access_token"], b["access_token"])
        self.assert_security_error(401, lambda: self.service.authenticate("Bearer "+secrets.token_urlsafe(32)))
        tampered = a["access_token"][:-1]+("a" if a["access_token"][-1] != "a" else "b")
        self.assert_security_error(401, lambda: self.service.authenticate("Bearer "+tampered))
        self.now = a["expires_at"]-1
        self.assertEqual(self.service.authenticate("Bearer "+a["access_token"]).role, Role.ANALYST)
        self.now += 1
        self.assert_security_error(401, lambda: self.service.authenticate("Bearer "+a["access_token"]))

    def test_logout_revokes_session(self):
        token = self.login()["access_token"]
        self.assertEqual(self.service.logout("Bearer "+token)["status"], "signed_out")
        self.assert_security_error(401, lambda: self.service.authenticate("Bearer "+token))

    def test_disabled_changed_password_or_role_version_invalidates(self):
        token = self.login()["access_token"]
        user = self.users[2]
        self.repo.users[user.user_id] = replace(user, active=False)
        self.assert_security_error(401, lambda: self.service.authenticate("Bearer "+token))
        self.repo.users[user.user_id] = replace(user, security_version=2)
        self.assert_security_error(401, lambda: self.service.authenticate("Bearer "+token))

    def test_current_role_is_read_not_trusted_from_client(self):
        token = self.login()["access_token"]
        user = self.users[2]
        self.repo.users[user.user_id] = replace(user, role=Role.EVALUATOR)
        principal = self.service.authenticate("Bearer "+token)
        self.assertEqual(principal.role, Role.EVALUATOR)
        self.assert_security_error(403, lambda: require_permission(principal, Permission.SCENARIOS_EXECUTE))

    def test_missing_malformed_and_jwt_style_tokens_rejected(self):
        for header in (None, "", "Basic abc", "Bearer abc.def.ghi", "Bearer ", "Bearer a b", "Bearer "+"x"*10000):
            self.assert_security_error(401, lambda: self.service.authenticate(header))

    def test_each_role_allowed_and_forbidden_boundaries(self):
        for role in Role:
            principal = self.service.authenticate("Bearer "+self.login(role)["access_token"])
            self.assertEqual(require_permission(principal, Permission.ANALYTICS_READ), principal)
            for permission in (Permission.USERS_MANAGE, Permission.CONFIGURATION_MANAGE):
                if role == Role.ADMINISTRATOR:
                    self.assertEqual(require_permission(principal, permission), principal)
                else:
                    self.assert_security_error(403, lambda: require_permission(principal, permission))
            if role == Role.EVALUATOR:
                self.assert_security_error(403, lambda: require_permission(principal, Permission.SCENARIOS_EXECUTE))
            else:
                require_permission(principal, Permission.SCENARIOS_EXECUTE)
            if role == Role.OPERATOR:
                self.assert_security_error(403, lambda: require_permission(principal, Permission.DIAGNOSTICS_READ))
            else:
                require_permission(principal, Permission.DIAGNOSTICS_READ)

    def test_every_existing_route_has_explicit_permission_and_unknown_denies(self):
        for endpoint in ENDPOINTS:
            self.assertIsInstance(required_permission(endpoint), Permission)
        endpoint = EndpointContract("unknown", "POST", "/unknown", "futureCapability", "future")
        self.assert_security_error(403, lambda: required_permission(endpoint))

    def test_dispatcher_protects_existing_routes_before_artifact_reads(self):
        repo = InMemoryArtifactRepository()
        app = ApiApplication(repo)
        unauthenticated = app.handle("GET", "/api/v1/analytics/executive-summary")
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(unauthenticated.headers["WWW-Authenticate"], "Bearer")
        principal = self.service.authenticate("Bearer "+self.login(Role.EVALUATOR)["access_token"])
        forbidden = app.handle("POST", "/api/v1/analytics/what-if", body={"scenario": "fixture"}, principal=principal)
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(repo.requests, [])
        response = app.handle("GET", "/api/v1/analytics/executive-summary", principal=principal)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body["status"], "not_ready")

    def test_missing_configuration_and_fixture_isolation(self):
        unconfigured = AuthService(config=SecurityConfig())
        error = self.assert_security_error(503, lambda: unconfigured.login("analyst", self.password, client_id="fixture"))
        self.assertEqual(error.code, "NOT_CONFIGURED")
        self.assert_security_error(503, lambda: unconfigured.authenticate(None))
        with self.assertRaises(ValueError):
            AuthService(self.repo, config=SecurityConfig())
        with self.assertRaises(ValueError):
            AuthService(config=SecurityConfig(), clock=lambda: 0)
        other = InMemoryAuthRepository()
        self.login()
        self.assertEqual(other.users, {})
        self.assertEqual(other.sessions, {})

    def test_configuration_redaction_and_bounds(self):
        dsn = "postgresql://fixture:"+secrets.token_urlsafe(24)+"@invalid/auth"
        config = SecurityConfig.from_env({"UTIQ_AUTH_DATABASE_DSN": dsn, "UTIQ_SESSION_TTL_SECONDS": "120", "UTIQ_ENVIRONMENT": "test"})
        self.assertEqual(config.mode, "production")
        self.assertEqual(config.session_ttl_seconds, 120)
        self.assertNotIn(dsn, repr(config))
        self.assertFalse(SecurityConfig.from_env({}).demo_mode)
        self.assertTrue(SecurityConfig.from_env({"UTIQ_DEMO_MODE": "true"}).demo_mode)
        with self.assertRaises(ValueError):
            SecurityConfig.from_env({"UTIQ_DEMO_MODE": "yes"})
        for value in ("0", "3601", "not-an-integer"):
            with self.assertRaises(ValueError) as caught:
                SecurityConfig.from_env({"UTIQ_SESSION_TTL_SECONDS": value})
            self.assertNotIn(value, str(caught.exception))

    def test_persistence_failure_is_safe_and_not_authenticated(self):
        with patch.object(self.repo, "get_user_by_username", side_effect=RuntimeError(self.password)):
            error = self.assert_security_error(503, lambda: self.login())
        self.assertEqual(error.code, "NOT_READY")
        self.assertNotIn(self.password, str(error))
        self.assertEqual(self.repo.sessions, {})

    def test_limiter_counts_known_and_unknown_accounts_before_hashing(self):
        self.service = AuthService(self.repo, config=SecurityConfig(mode="test", login_account_limit=1), clock=lambda: self.now)
        self.login()
        self.assert_security_error(429, lambda: self.login())
        self.now += 301
        self.login()

    def test_input_validation_no_normalization_of_password(self):
        self.assert_security_error(401, lambda: self.service.login("analyst", self.password+" ", client_id="fixture"))
        for username, password in [(None, self.password), ("x", self.password), ("analyst", []), ("analyst", "x"*1025), ("analyst", "\ud800")]:
            self.assert_security_error(401, lambda: self.service.login(username, password, client_id="fixture"))

    def test_session_creation_failure_never_returns_token(self):
        with patch.object(self.repo, "create_session", side_effect=RuntimeError("fixture outage")):
            self.assert_security_error(503, lambda: self.login())

    def test_kdf_unavailable_does_not_use_weaker_fallback(self):
        with patch("backend.security.service.hash_password", side_effect=RuntimeError("KDF unavailable")):
            error = self.assert_security_error(503, lambda: self.login())
        self.assertEqual(error.code, "NOT_READY")
        self.assertEqual(self.repo.sessions, {})

    def test_kdf_concurrency_limit_rejects_without_token(self):
        with patch("backend.security.service._HASH_SLOTS") as slots:
            slots.acquire.return_value = False
            self.assert_security_error(429, lambda: self.login())
            slots.release.assert_not_called()
        self.assertEqual(self.repo.sessions, {})

    def test_artifact_failure_does_not_leak_internal_paths_or_secrets(self):
        repo = InMemoryArtifactRepository()
        principal = Principal("fixture-id", "fixture-user", Role.ANALYST, self.now+900)
        with patch.object(repo, "get", side_effect=RuntimeError(self.password)):
            response = ApiApplication(repo).handle("GET", "/api/v1/analytics/executive-summary", principal=principal)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(self.password, json.dumps(response.body))


HAS_HTTP = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))


@unittest.skipUnless(HAS_HTTP, "FastAPI/httpx unavailable; no packages installed")
class FastApiSecurityTests(unittest.TestCase):
    def test_demo_mode_bypasses_product_auth_but_not_authentication_or_readiness(self):
        from fastapi.testclient import TestClient
        from backend.fastapi_app import create_app
        config = SecurityConfig(demo_mode=True)
        app = create_app(InMemoryArtifactRepository(), security_config=config)
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
            self.assertEqual(client.get("/api/config").json(), {"demo_mode": True})
            dashboard = client.get("/api/v1/analytics/executive-summary")
            self.assertEqual(dashboard.status_code, 200)
            self.assertEqual(dashboard.json()["status"], "not_ready")
            self.assertEqual(client.get("/api/v1/auth/me").status_code, 503)
            self.assertEqual(client.post("/api/v1/auth/login", json={"username": "demo", "password": "demo"}).status_code, 503)
            self.assertEqual(client.get("/api/v1/admin/security-status").status_code, 401)
            preflight = client.options(
                "/api/v1/analytics/what-if",
                headers={
                    "Origin": "http://127.0.0.1:4173",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            self.assertEqual(preflight.status_code, 200)
            self.assertIn("POST", preflight.headers["access-control-allow-methods"])
        with TestClient(app, base_url="https://testserver", client=("192.0.2.1", 50000)) as remote_client:
            self.assertEqual(remote_client.get("/api/v1/analytics/executive-summary").status_code, 403)

    def test_host_login_dependencies_errors_and_expiry(self):
        from fastapi.testclient import TestClient
        from backend.fastapi_app import create_app
        password = secrets.token_urlsafe(24)
        repo = InMemoryAuthRepository([User("fixture-evaluator", "evaluator", hash_password(password), Role.EVALUATOR)])
        app = create_app(InMemoryArtifactRepository(), auth_repository=repo,
                         security_config=SecurityConfig(mode="test"), allow_test_mode=True)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/auth/me").status_code, 401)
            login = client.post("/api/v1/auth/login", json={"username": "evaluator", "password": password})
            self.assertEqual(login.status_code, 200)
            self.assertEqual(login.headers["cache-control"], "no-store")
            headers = {"Authorization": "Bearer "+login.json()["access_token"]}
            self.assertEqual(client.get("/api/v1/auth/me", headers=headers).json()["role"], "Evaluator")
            self.assertEqual(client.post("/api/v1/analytics/what-if", json={}, headers=headers).status_code, 403)
            self.assertEqual(client.get("/api/v1/admin/security-status", headers=headers).status_code, 403)
            self.assertEqual(client.post("/api/v1/auth/logout", headers=headers).status_code, 200)
            self.assertEqual(client.get("/api/v1/auth/me", headers=headers).status_code, 401)
            invalid = client.post("/api/v1/auth/login", json={"password": password})
            self.assertEqual(invalid.status_code, 400)
            self.assertNotIn(password, invalid.text)

    def test_production_https_and_not_configured(self):
        from fastapi.testclient import TestClient
        from backend.fastapi_app import create_app
        app = create_app(InMemoryArtifactRepository(), security_config=SecurityConfig())
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/auth/me").status_code, 400)
        with TestClient(app, base_url="https://testserver") as client:
            response = client.get("/api/v1/auth/me")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["status"], "NOT_CONFIGURED")


if __name__ == "__main__":
    unittest.main()

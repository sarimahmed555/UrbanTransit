"""PostgreSQL AuthRepository adapter over the existing serving connection layer.

This implements the `AuthRepository` contract declared in `contracts.py` using
`backend.serving`'s `PostgresConfig`/`PostgresDatabase` conventions: environment
configuration only, lazily imported driver (`psycopg`, then `psycopg2`), one
explicit transaction per operation, and bind parameters for every value.

Design rules preserved here:

* Salted scrypt `password_hash` values are stored and returned verbatim; this
  module never hashes, compares, generates or logs a password.
* Only SHA-256 token digests are persisted; raw bearer tokens are never stored.
* Roles map to the existing `Role` enum. An unknown stored role is an error, not
  a downgrade, so it fails closed instead of granting access.
* Sessions, revocation, deactivation and `security_version` are read on every
  request by `AuthService`; nothing is trusted from a client claim.
* Login-attempt counters are shared and atomic across every worker
  (`INSERT ... ON CONFLICT DO UPDATE ... RETURNING`).
* Database errors propagate so `AuthService` can map them to a safe
  `NOT_READY`. There is no in-memory, demo or fixture fallback here.

The reviewed `app_auth` DDL lives in `postgres_schema.sql`. It is a deployment
migration contract, not executed by this module; `readiness()` reports
`NOT_READY` until those relations exist.
"""

from urllib.parse import parse_qsl, unquote, urlsplit

from ..serving.config import PostgresConfig
from ..serving.database import PostgresDatabase
from .config import SecurityConfig
from .contracts import Role, Session, User

USER_LOOKUP = """
            SELECT user_id, username, password_hash, role, active, security_version
            FROM app_auth.users
            WHERE username = %s
            """
USER_LOOKUP_BY_ID = """
            SELECT user_id, username, password_hash, role, active, security_version
            FROM app_auth.users
            WHERE user_id = %s
            """
USER_INSERT = """
            INSERT INTO app_auth.users (user_id, username, password_hash, role)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (username) DO NOTHING
            RETURNING user_id
            """
SESSION_INSERT = """
            INSERT INTO app_auth.sessions
                (token_digest, user_id, issued_at, expires_at, security_version, revoked)
            VALUES (%s, %s, to_timestamp(%s), to_timestamp(%s), %s, %s)
            """
SESSION_LOOKUP = """
            SELECT token_digest, user_id,
                   EXTRACT(EPOCH FROM issued_at)::bigint,
                   EXTRACT(EPOCH FROM expires_at)::bigint,
                   security_version, revoked
            FROM app_auth.sessions
            WHERE token_digest = %s
            """
SESSION_REVOKE = """
            UPDATE app_auth.sessions SET revoked = true
            WHERE token_digest = %s
            """
LOGIN_ATTEMPT_CONSUME = """
            INSERT INTO app_auth.login_attempt_buckets (bucket_key, window_start, attempts)
            VALUES (%s, %s, 1)
            ON CONFLICT (bucket_key, window_start)
            DO UPDATE SET attempts = app_auth.login_attempt_buckets.attempts + 1
            RETURNING attempts
            """
REQUIRED_RELATIONS = (
    "app_auth.users",
    "app_auth.sessions",
    "app_auth.login_attempt_buckets",
)


def postgres_config_from_dsn(dsn):
    """Translate the security DSN into the existing PostgreSQL serving config.

    The DSN is the only source of authentication connection settings, so the
    same database is used for every worker. Validation, port bounds, the libpq
    SSL-mode allowlist and the `NOT_CONFIGURED` rule stay in `PostgresConfig`;
    this function never embeds or reports a credential.
    """
    if not isinstance(dsn, str) or not dsn.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("Authentication database is NOT_CONFIGURED")
    try:
        parts = urlsplit(dsn)
        port = parts.port
    except ValueError:
        raise RuntimeError("Authentication database DSN is invalid") from None
    query = dict(parse_qsl(parts.query))
    environment = {
        "PGHOST": parts.hostname,
        "PGPORT": str(port if port is not None else 5432),
        "PGDATABASE": unquote(parts.path[1:]) if parts.path.startswith("/") else None,
        "PGUSER": unquote(parts.username) if parts.username else None,
        "PGPASSWORD": unquote(parts.password) if parts.password else None,
        "PGSSLMODE": query.get("sslmode") or "require",
    }
    try:
        config = PostgresConfig.from_env(environment)
    except ValueError:
        raise RuntimeError("Authentication database configuration is invalid") from None
    if not config.configured:
        raise RuntimeError("Authentication database is NOT_CONFIGURED")
    return config


class PostgresAuthRepository:
    """Production `AuthRepository` over shared PostgreSQL storage."""

    is_fixture = False
    persistence = "POSTGRESQL"

    def __init__(self, database: PostgresDatabase):
        if not isinstance(database, PostgresDatabase):
            raise TypeError("PostgresAuthRepository requires a PostgresDatabase")
        self.database = database

    @classmethod
    def from_security_config(cls, config):
        """Build the adapter from the existing environment/configuration boundary."""
        if not isinstance(config, SecurityConfig) or config.database_dsn is None:
            raise RuntimeError("Authentication database is NOT_CONFIGURED")
        return cls(PostgresDatabase(postgres_config_from_dsn(config.database_dsn)))

    # -- helpers ---------------------------------------------------------
    def _fetchone(self, sql, parameters):
        with self.database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, parameters)
                return cursor.fetchone()
            finally:
                cursor.close()

    def _execute(self, sql, parameters):
        with self.database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, parameters)
            finally:
                cursor.close()

    @staticmethod
    def _user(row):
        user_id, username, password_hash, role, active, security_version = row
        try:
            return User(
                str(user_id),
                username,
                password_hash,
                Role(role),
                bool(active),
                int(security_version),
            )
        except (TypeError, ValueError):
            # A stored role/record outside the contract is never downgraded to
            # a lower-privilege or default identity.
            raise RuntimeError("Stored authentication record is invalid") from None

    @staticmethod
    def _session(row):
        token_digest, user_id, issued_at, expires_at, security_version, revoked = row
        try:
            return Session(
                str(token_digest),
                str(user_id),
                int(issued_at),
                int(expires_at),
                int(security_version),
                bool(revoked),
            )
        except (TypeError, ValueError):
            raise RuntimeError("Stored session record is invalid") from None

    # -- AuthRepository contract ----------------------------------------
    def create_user(self, user):
        if not isinstance(user, User):
            raise TypeError("create_user requires a User")
        row = self._fetchone(
            USER_INSERT,
            (user.user_id, user.username, user.password_hash, user.role.value),
        )
        return row is not None

    def get_user_by_username(self, normalized_username):
        if not isinstance(normalized_username, str) or not normalized_username:
            raise ValueError("normalized_username must be a non-empty string")
        row = self._fetchone(USER_LOOKUP, (normalized_username,))
        return None if row is None else self._user(row)

    def get_user_by_id(self, user_id):
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a non-empty string")
        row = self._fetchone(USER_LOOKUP_BY_ID, (user_id,))
        return None if row is None else self._user(row)

    def create_session(self, session):
        if not isinstance(session, Session):
            raise TypeError("create_session requires a Session")
        self._execute(
            SESSION_INSERT,
            (
                session.token_digest,
                session.user_id,
                session.issued_at,
                session.expires_at,
                session.security_version,
                session.revoked,
            ),
        )

    def get_session(self, token_digest):
        if not isinstance(token_digest, str) or not token_digest:
            raise ValueError("token_digest must be a non-empty string")
        row = self._fetchone(SESSION_LOOKUP, (token_digest,))
        return None if row is None else self._session(row)

    def revoke_session(self, token_digest):
        if not isinstance(token_digest, str) or not token_digest:
            raise ValueError("token_digest must be a non-empty string")
        self._execute(SESSION_REVOKE, (token_digest,))

    def consume_login_attempt(self, key, now, window_seconds, limit):
        """Count one attempt in a fixed bucket and report whether it is allowed.

        The counter row is created or incremented by a single statement, so
        concurrent workers share the same total. Failure propagates: a limiter
        that cannot be evaluated must never allow a login.
        """
        if not isinstance(key, str) or not key or len(key) > 72:
            raise ValueError("Login attempt bucket key is invalid")
        if type(now) is not int or type(limit) is not int or limit <= 0:
            raise ValueError("Login attempt arguments are invalid")
        if type(window_seconds) is not int or window_seconds <= 0:
            raise ValueError("Login attempt window is invalid")
        window_start = (now // window_seconds) * window_seconds
        row = self._fetchone(LOGIN_ATTEMPT_CONSUME, (key, window_start))
        if row is None:
            raise RuntimeError("Login attempt counter returned no count")
        return int(row[0]) <= limit

    # -- deployment readiness -------------------------------------------
    def readiness(self):
        """Report measured `app_auth` readiness; never a fabricated `READY`."""
        if not self.database.config.configured:
            return {"state": "NOT_CONFIGURED", "database": "postgresql"}
        try:
            with self.database.transaction() as connection:
                cursor = connection.cursor()
                try:
                    for relation in REQUIRED_RELATIONS:
                        cursor.execute("SELECT to_regclass(%s)", (relation,))
                        row = cursor.fetchone()
                        if not row or not row[0]:
                            return {"state": "NOT_READY", "database": "postgresql"}
                finally:
                    cursor.close()
        except Exception:
            return {"state": "NOT_READY", "database": "postgresql"}
        return {"state": "READY", "database": "postgresql"}

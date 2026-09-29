-- CONTRACT ONLY: not executed. Isolated auth schema; no production data or seed users.
-- A deployment migration must review ownership, TLS/role permissions and transactions.
CREATE SCHEMA IF NOT EXISTS app_auth;

CREATE TABLE app_auth.users (
    user_id uuid PRIMARY KEY,
    username varchar(128) NOT NULL UNIQUE
        CHECK (username = lower(username) AND username ~ '^[a-z0-9][a-z0-9_.@+-]{2,127}$'),
    password_hash varchar(256) NOT NULL CHECK (password_hash LIKE 'scrypt$v1$131072$8$1$%'),
    role varchar(16) NOT NULL CHECK (role IN ('Administrator', 'Operator', 'Analyst', 'Evaluator')),
    active boolean NOT NULL DEFAULT true,
    security_version bigint NOT NULL DEFAULT 1 CHECK (security_version >= 1),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE app_auth.sessions (
    token_digest char(64) PRIMARY KEY CHECK (token_digest ~ '^[0-9a-f]{64}$'),
    user_id uuid NOT NULL REFERENCES app_auth.users(user_id) ON DELETE CASCADE,
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > issued_at),
    security_version bigint NOT NULL CHECK (security_version >= 1),
    revoked boolean NOT NULL DEFAULT false
);
CREATE INDEX sessions_user_idx ON app_auth.sessions(user_id);
CREATE INDEX sessions_expiry_idx ON app_auth.sessions(expires_at);

CREATE TABLE app_auth.login_attempt_buckets (
    bucket_key varchar(72) NOT NULL,
    window_start bigint NOT NULL,
    attempts integer NOT NULL CHECK (attempts > 0),
    PRIMARY KEY (bucket_key, window_start)
);
-- consume_login_attempt uses a parameterized, atomic INSERT ... ON CONFLICT DO UPDATE
-- SET attempts = login_attempt_buckets.attempts + 1 RETURNING attempts;
-- window_start = floor(now_epoch / window_seconds) * window_seconds.
-- Return attempts <= configured limit. Do not reset on success or disclose user existence.
-- Password changes/role changes/deactivation increment users.security_version atomically.
-- Delete expired sessions/old buckets via bounded maintenance. Never store bearer tokens.
-- Query parameters must use the adapter driver's bind parameters, never string interpolation.

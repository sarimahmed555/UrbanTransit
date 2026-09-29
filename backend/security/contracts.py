"""Small security domain and persistence boundary."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class Role(str, Enum):
    ADMINISTRATOR = "Administrator"
    OPERATOR = "Operator"
    ANALYST = "Analyst"
    EVALUATOR = "Evaluator"


class Permission(str, Enum):
    ANALYTICS_READ = "analytics:read"
    DIAGNOSTICS_READ = "diagnostics:read"
    SCENARIOS_EXECUTE = "scenarios:execute"
    USERS_MANAGE = "users:manage"
    CONFIGURATION_MANAGE = "configuration:manage"


ROLE_PERMISSIONS = {
    Role.ADMINISTRATOR: frozenset(Permission),
    Role.OPERATOR: frozenset({Permission.ANALYTICS_READ, Permission.SCENARIOS_EXECUTE}),
    Role.ANALYST: frozenset({Permission.ANALYTICS_READ, Permission.DIAGNOSTICS_READ, Permission.SCENARIOS_EXECUTE}),
    Role.EVALUATOR: frozenset({Permission.ANALYTICS_READ, Permission.DIAGNOSTICS_READ}),
}


@dataclass(frozen=True)
class User:
    user_id: str
    username: str
    password_hash: str = field(repr=False)
    role: Role
    active: bool = True
    security_version: int = 1


@dataclass(frozen=True)
class Session:
    token_digest: str = field(repr=False)
    user_id: str
    issued_at: int
    expires_at: int
    security_version: int
    revoked: bool = False


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: Role
    session_expires_at: int

    def public(self):
        return {"user_id": self.user_id, "username": self.username, "role": self.role.value,
                "permissions": sorted(p.value for p in ROLE_PERMISSIONS[self.role]),
                "session_expires_at": self.session_expires_at}


class SecurityError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message

    def body(self):
        return {"status": self.code if self.status_code == 503 else "error",
                "error": {"code": self.code, "message": self.message}}


def unauthorized():
    return SecurityError(401, "invalid_credentials", "Authentication required or credentials invalid")


def unavailable(configured=False):
    return SecurityError(503, "NOT_READY" if configured else "NOT_CONFIGURED", "Authentication infrastructure unavailable")


class AuthRepository(Protocol):
    """Production adapter must use shared durable storage and atomic rate counters.

    No implementation/default users are supplied. Database errors must propagate;
    callers map them to a safe 503. Implementations must not log credentials/tokens.
    """
    is_fixture: bool
    def create_user(self, user: User) -> bool: ...
    def get_user_by_username(self, normalized_username: str) -> User | None: ...
    def get_user_by_id(self, user_id: str) -> User | None: ...
    def create_session(self, session: Session) -> None: ...
    def get_session(self, token_digest: str) -> Session | None: ...
    def revoke_session(self, token_digest: str) -> None: ...
    def consume_login_attempt(self, key: str, now: int, window_seconds: int, limit: int) -> bool:
        """Atomically count every attempt in a fixed bucket and return count <= limit."""
        ...

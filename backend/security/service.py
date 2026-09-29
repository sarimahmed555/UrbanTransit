"""Opaque bearer sessions; current roles/active state are read on every request."""
import hashlib
import hmac
import re
import secrets
import time
import uuid
from threading import BoundedSemaphore
from .config import SecurityConfig
from .contracts import Principal, Role, Session, User, SecurityError, unauthorized, unavailable
from .passwords import hash_password, verify_password, valid_password_input

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.@+-]{2,127}\Z")
_HASH_SLOTS = BoundedSemaphore(2)  # At most two 128-MiB scrypt operations per process.


def token_digest(token):
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        raise unauthorized()
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def bearer_token(header):
    if not isinstance(header, str) or len(header) > 128:
        raise unauthorized()
    parts = header.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise unauthorized()
    token_digest(parts[1])
    return parts[1]


class AuthService:
    def __init__(self, repository=None, *, config=None, clock=None):
        self.config = config or SecurityConfig.from_env()
        self._repository = repository
        if repository is not None and self.config.mode != "test" and getattr(repository, "is_fixture", True) is not False:
            raise ValueError("Fixture authentication repositories are forbidden in production")
        if self.config.mode != "test" and clock is not None:
            raise ValueError("Injected security clocks are only allowed in tests")
        self._clock = clock or time.time
        self._dummy_hash = None

    @property
    def repository(self):
        """The composed AuthRepository adapter; None until one is configured."""
        return self._repository

    def _ready(self):
        if self._repository is None or (self.config.mode == "production" and not self.config.database_dsn):
            raise unavailable()
        return self._repository

    def _call(self, method, *args):
        repository = self._ready()
        try:
            return getattr(repository, method)(*args)
        except Exception:
            raise unavailable(configured=True) from None

    def readiness(self):
        self._ready()
        if self.config.mode == "test":
            persistence = "TEST_FIXTURE"
        else:
            persistence = getattr(self._repository, "persistence", "REPOSITORY_SUPPLIED")
        return {"status": "CONFIGURED", "persistence": persistence,
                "note": "Configuration state only; not database health or deployed security evidence"}

    def register(self, username, password, *, client_id):
        self._ready()
        if (not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username.lower())
                or not valid_password_input(password) or len(password) < 12):
            raise SecurityError(400, "invalid_registration", "Account details are invalid")
        username = username.lower()
        if not isinstance(client_id, str) or not client_id or len(client_id) > 256:
            raise unavailable(configured=True)
        now = int(self._clock())
        client_key = "signup:"+hashlib.sha256(client_id.encode()).hexdigest()
        allowed = self._call("consume_login_attempt", client_key, now,
                             self.config.login_window_seconds,
                             min(5, self.config.login_client_limit))
        if allowed is not True:
            raise SecurityError(429, "rate_limited", "Account creation temporarily unavailable; try again later")
        if not _HASH_SLOTS.acquire(blocking=False):
            raise SecurityError(429, "rate_limited", "Account creation temporarily unavailable; try again later")
        try:
            password_hash = hash_password(password)
        except Exception:
            raise unavailable(configured=True) from None
        finally:
            _HASH_SLOTS.release()
        user = User(str(uuid.uuid4()), username, password_hash, Role.EVALUATOR)
        created = self._call("create_user", user)
        if type(created) is not bool:
            raise unavailable(configured=True)
        return {"status": "created", "username": username}

    def login(self, username, password, *, client_id):
        self._ready()
        if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username.lower()) or not valid_password_input(password):
            raise unauthorized()
        username = username.lower()
        if not isinstance(client_id, str) or not client_id or len(client_id) > 256:
            raise unavailable(configured=True)
        now = int(self._clock())
        # Count all attempts before expensive hashing; both counters must be shared/atomic.
        account_allowed = self._call("consume_login_attempt", "account:"+hashlib.sha256(username.encode()).hexdigest(),
                                     now, self.config.login_window_seconds, self.config.login_account_limit)
        client_allowed = self._call("consume_login_attempt", "client:"+hashlib.sha256(client_id.encode()).hexdigest(),
                                    now, self.config.login_window_seconds, self.config.login_client_limit)
        if account_allowed is not True or client_allowed is not True:
            raise SecurityError(429, "rate_limited", "Login temporarily unavailable; try again later")
        user = self._call("get_user_by_username", username)
        if user is not None and not isinstance(user, User):
            raise unavailable(configured=True)
        if not _HASH_SLOTS.acquire(blocking=False):
            raise SecurityError(429, "rate_limited", "Login temporarily unavailable; try again later")
        try:
            if self._dummy_hash is None:
                self._dummy_hash = hash_password(secrets.token_urlsafe(32))
            verified = verify_password(password, user.password_hash if user else self._dummy_hash)
        except Exception:
            raise unavailable(configured=True) from None
        finally:
            _HASH_SLOTS.release()
        if not user or not verified or user.active is not True or not isinstance(user.role, Role):
            raise unauthorized()
        token = secrets.token_urlsafe(32)
        session = Session(token_digest(token), user.user_id, now, now+self.config.session_ttl_seconds, user.security_version)
        self._call("create_session", session)
        return {"access_token": token, "token_type": "bearer", "expires_in": self.config.session_ttl_seconds,
                "expires_at": session.expires_at}

    def authenticate(self, authorization):
        self._ready()
        token = bearer_token(authorization)
        hashed = token_digest(token)
        session = self._call("get_session", hashed)
        if session is not None and not isinstance(session, Session):
            raise unavailable(configured=True)
        now = int(self._clock())
        if (session is None or session.revoked or not hmac.compare_digest(session.token_digest, hashed)
                or not session.issued_at <= now < session.expires_at
                or session.expires_at-session.issued_at > self.config.session_ttl_seconds):
            raise unauthorized()
        user = self._call("get_user_by_id", session.user_id)
        if user is not None and not isinstance(user, User):
            raise unavailable(configured=True)
        if (user is None or user.active is not True or not isinstance(user.role, Role)
                or user.security_version != session.security_version):
            raise unauthorized()
        return Principal(user.user_id, user.username, user.role, session.expires_at)

    def logout(self, authorization):
        self.authenticate(authorization)
        self._call("revoke_session", token_digest(bearer_token(authorization)))
        return {"status": "signed_out"}

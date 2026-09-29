"""Environment-only deployment settings, with safe representations and errors."""
from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SecurityConfig:
    mode: str = "production"
    database_dsn: str | None = field(default=None, repr=False)
    session_ttl_seconds: int = 900
    login_window_seconds: int = 300
    login_account_limit: int = 10
    login_client_limit: int = 50
    web_origins: tuple[str, ...] = ()
    demo_mode: bool = False

    def __post_init__(self):
        if self.mode not in {"production", "test"}:
            raise ValueError("Invalid security mode")
        if type(self.demo_mode) is not bool:
            raise ValueError("Invalid demo mode setting")
        for origin in self.web_origins:
            parsed = urlsplit(origin)
            if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or "*" in origin:
                raise ValueError("Web origins must be exact HTTPS origins")
        limits = {"session_ttl_seconds": (60, 3600), "login_window_seconds": (60, 3600),
                  "login_account_limit": (1, 100), "login_client_limit": (1, 1000)}
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid security setting: {name}")
        if self.database_dsn is not None and (not isinstance(self.database_dsn, str) or not self.database_dsn.startswith(("postgresql://", "postgres://"))):
            raise ValueError("Invalid authentication database configuration")

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        # Test mode cannot be enabled through deployment environment variables.
        demo_mode = env.get("UTIQ_DEMO_MODE", "false").lower()
        if demo_mode not in {"true", "false"}:
            raise ValueError("Invalid security setting: UTIQ_DEMO_MODE")
        values = {"database_dsn": env.get("UTIQ_AUTH_DATABASE_DSN") or None, "web_origins": tuple(v.strip() for v in env.get("UTIQ_WEB_ORIGINS", "").split(",") if v.strip()), "demo_mode": demo_mode == "true"}
        for field_name, variable in (
            ("session_ttl_seconds", "UTIQ_SESSION_TTL_SECONDS"),
            ("login_window_seconds", "UTIQ_LOGIN_WINDOW_SECONDS"),
            ("login_account_limit", "UTIQ_LOGIN_ACCOUNT_LIMIT"),
            ("login_client_limit", "UTIQ_LOGIN_CLIENT_LIMIT"),
        ):
            if variable in env:
                try:
                    values[field_name] = int(env[variable])
                except (TypeError, ValueError):
                    raise ValueError(f"Invalid security setting: {variable}") from None
        return cls(**values)

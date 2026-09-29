"""Environment-only PostgreSQL configuration without secret-bearing reprs."""

from dataclasses import dataclass, field
import os
from typing import Mapping


@dataclass(frozen=True)
class PostgresConfig:
    host: str | None = None
    port: int = 5432
    database: str | None = None
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    sslmode: str = "require"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "PostgresConfig":
        values = os.environ if environ is None else environ
        port_value = values.get("PGPORT", "5432")
        try:
            port = int(port_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("PGPORT must be an integer between 1 and 65535") from exc
        if not 1 <= port <= 65535:
            raise ValueError("PGPORT must be an integer between 1 and 65535")
        sslmode = values.get("PGSSLMODE", "require")
        if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise ValueError("PGSSLMODE is not a supported libpq SSL mode")
        return cls(
            host=values.get("PGHOST") or None,
            port=port,
            database=values.get("PGDATABASE") or None,
            username=values.get("PGUSER") or None,
            password=values.get("PGPASSWORD") or None,
            sslmode=sslmode,
        )

    @property
    def configured(self) -> bool:
        return all((self.host, self.database, self.username, self.password))

    def connection_parameters(self) -> dict[str, object]:
        if not self.configured:
            raise RuntimeError("PostgreSQL is NOT_CONFIGURED")
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.username,
            "password": self.password,
            "sslmode": self.sslmode,
            "connect_timeout": 5,
        }

"""Lazy PostgreSQL connection and explicit readiness/transaction behavior."""

from contextlib import contextmanager
from typing import Callable, Iterator

from .config import PostgresConfig


class PostgresDatabase:
    def __init__(
        self,
        config: PostgresConfig,
        *,
        connect_callable: Callable[..., object] | None = None,
    ):
        self.config = config
        self._connect_callable = connect_callable

    def connect(self):
        if not self.config.configured:
            raise RuntimeError("PostgreSQL is NOT_CONFIGURED")
        connector = self._connect_callable
        if connector is None:
            try:
                import psycopg
            except ImportError:
                try:
                    import psycopg2 as psycopg
                except ImportError as exc:
                    raise RuntimeError("PostgreSQL driver is NOT_CONFIGURED") from exc
            connector = psycopg.connect
        return connector(**self.config.connection_parameters())

    @contextmanager
    def transaction(self) -> Iterator[object]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def readiness(self) -> dict[str, str]:
        if not self.config.configured:
            return {"state": "NOT_CONFIGURED", "database": "postgresql"}
        try:
            connection = self.connect()
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute(
                        "SELECT to_regclass('app_serving.serving_results')"
                    )
                    row = cursor.fetchone()
                finally:
                    cursor.close()
            finally:
                connection.close()
        except Exception:
            return {"state": "NOT_READY", "database": "postgresql"}
        if not row or not row[0]:
            return {"state": "NOT_READY", "database": "postgresql"}
        return {"state": "READY", "database": "postgresql"}

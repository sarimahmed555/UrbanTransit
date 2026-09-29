"""Explicit, transaction-safe PostgreSQL SQL migration runner."""

from pathlib import Path
import re

from .database import PostgresDatabase

_MIGRATION_NAME = re.compile(r"^[0-9]{3}_[a-z0-9_]+\.sql$")
MIGRATIONS = Path(__file__).parent / "migrations"


def apply_migrations(database: PostgresDatabase, migration_dir: Path = MIGRATIONS) -> list[str]:
    """Apply ordered local SQL migrations once; failures roll back the migration."""
    directory = Path(migration_dir)
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise RuntimeError(f"No PostgreSQL migrations found in {directory}")
    applied_now = []
    for path in files:
        if not _MIGRATION_NAME.fullmatch(path.name):
            raise ValueError(f"Invalid migration filename: {path.name}")
        sql = path.read_text(encoding="utf-8")
        with database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("CREATE SCHEMA IF NOT EXISTS app_serving")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_serving.schema_migrations (
                        migration_id text PRIMARY KEY,
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cursor.execute(
                    "SELECT 1 FROM app_serving.schema_migrations WHERE migration_id = %s",
                    (path.name,),
                )
                if cursor.fetchone():
                    continue
                for statement in (part.strip() for part in sql.split(";")):
                    if statement:
                        cursor.execute(statement)
                cursor.execute(
                    "INSERT INTO app_serving.schema_migrations (migration_id) VALUES (%s)",
                    (path.name,),
                )
                applied_now.append(path.name)
            finally:
                cursor.close()
    return applied_now

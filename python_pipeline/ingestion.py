"""Chunked Pandas ingestion with explicit schemas and production safety guards."""

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from .config import FORBIDDEN_DATASET_PARTS, PipelinePaths

TABLES = frozenset(
    {
        "passengers", "tickets", "routes", "stops", "route_stops", "trips",
        "schedules", "vehicles", "passenger_counts", "delays", "gps_events",
        "service_calendar", "route_patterns", "schedule_stop_times",
        "trip_stop_events", "trip_vehicle_assignments", "service_exceptions",
        "passenger_journeys", "demand_requests", "context_events",
        "passenger_transfer_events",
    }
)

REQUIRED_COLUMNS = {
    "tickets": ("ticket_id", "passenger_id", "trip_id"),
    "trips": ("trip_id", "operational_departure_id", "service_date", "route_id"),
    "route_stops": ("route_stop_id", "pattern_id", "stop_id", "stop_sequence"),
    "passenger_counts": ("count_id", "stop_event_id", "trip_id"),
    "delays": ("delay_id", "stop_event_id", "trip_id"),
}

DEFAULT_DTYPES = {
    "passenger_id": "string",
    "ticket_id": "string",
    "trip_id": "string",
    "route_id": "string",
    "stop_id": "string",
    "vehicle_id": "string",
    "route_stop_id": "string",
    "stop_event_id": "string",
    "assignment_id": "string",
    "service_date": "string",
}


def _require_pandas():
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Pandas is required for Python pipeline ingestion") from exc
    return pd


def _assert_safe_path(path: Path) -> None:
    resolved = path.resolve()
    if any(part in FORBIDDEN_DATASET_PARTS for part in resolved.parts):
        raise ValueError("Refusing to inspect raw_data/production-v1")


def discover_table_files(paths: PipelinePaths, table: str) -> list[Path]:
    directory = paths.raw_table_dir(table)
    _assert_safe_path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"Required raw table directory is missing: {directory}")
    files = sorted(
        file for file in directory.rglob("*")
        if file.is_file() and file.suffix.lower() in {".csv", ".jsonl", ".json"}
    )
    if not files:
        raise FileNotFoundError(f"Required raw table has no CSV/JSON files: {directory}")
    return files


def require_tables(paths: PipelinePaths, tables: Sequence[str]) -> dict[str, list[Path]]:
    unknown = sorted(set(tables) - TABLES)
    if unknown:
        raise ValueError(f"Unknown approved table(s): {', '.join(unknown)}")
    return {table: discover_table_files(paths, table) for table in tables}


def iter_table(
    paths: PipelinePaths,
    table: str,
    *,
    chunksize: int = 100_000,
    columns: Sequence[str] | None = None,
    dtypes: Mapping[str, str] | None = None,
) -> Iterator[object]:
    """Yield one Pandas DataFrame per file/chunk; never concatenates production data."""
    if chunksize <= 0:
        raise ValueError("chunksize must be positive")
    pd = _require_pandas()
    files = discover_table_files(paths, table)
    dtype = {**DEFAULT_DTYPES, **(dict(dtypes) if dtypes else {})}
    for file in files:
        if file.suffix.lower() == ".csv":
            reader = pd.read_csv(file, chunksize=chunksize, usecols=columns, dtype=dtype)
        else:
            reader = (pd.read_json(file, lines=True, columns=columns, dtype=dtype)
                      if file.suffix.lower() == ".jsonl" else pd.read_json(file, dtype=dtype))
        for chunk in (reader if hasattr(reader, "__iter__") and not hasattr(reader, "columns") else (reader,)):
            missing = [column for column in REQUIRED_COLUMNS.get(table, ()) if column not in chunk.columns]
            if missing:
                raise ValueError(f"{file} is missing required column(s): {', '.join(missing)}")
            yield chunk

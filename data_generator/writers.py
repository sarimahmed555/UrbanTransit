"""Streaming CSV/JSONL writers and artifact accounting.

Fact tables are written incrementally and rotate at ``config.chunk_rows``.  The
writer never materializes a table just to count or serialize it, and it assigns
source-row identities from the physical file and ordinal rather than from a
mutable business row order.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Sequence

from .config import GeneratorConfig, canonical_json
from .ids import entity_id, source_row_id as make_source_row_id
from .schemas import TABLE_LABELS, all_columns, column_names


RAW_META_FIELDS = {"raw_file", "row_ordinal", "raw_bytes_sha256", "raw_record_text", "parse_status"}


def format_value(value: Any) -> str:
    if value is None:
        return r"\N"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=None)
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple, dict)):
        return canonical_json(value)
    return str(value)


def json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat(timespec="microseconds") + "Z"
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return value


def _typed_csv_value(value: str, type_name: str) -> Any:
    """Decode a CSV field for a lossless typed JSONL mirror.

    Malformed lexical values remain strings in the mirror rather than being
    silently coerced or discarded.  The canonical CSV remains the raw source
    of truth for provenance and exact bytes.
    """
    if value == r"\N":
        return None
    if type_name == "INT":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if type_name.startswith("DEC"):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if type_name == "BOOL":
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return value


@dataclass
class Artifact:
    path: str
    table: str
    format: str
    rows: int
    bytes: int
    sha256: str
    partition: str = "all"


class OutputManager:
    def __init__(self, config: GeneratorConfig):
        self.config = config
        self.root = Path(config.output_dir)
        self.artifacts: List[Artifact] = []
        self._writers: Dict[str, "TableWriter"] = {}
        self._ensure_disk()
        self._prepare_root()

    def _ensure_disk(self) -> None:
        parent = self.root.parent if self.root.parent.exists() else Path(".")
        free = shutil.disk_usage(parent).free
        required = 100 * 1024 * 1024 if self.config.is_smoke else 55 * 1024 * 1024 * 1024
        if free < required:
            raise OSError(f"insufficient disk space: {free} bytes free; at least {required} required")

    def _prepare_root(self) -> None:
        marker = self.root / ".uti_dataset_output"
        if self.root.exists() and any(self.root.iterdir()):
            if not marker.exists():
                raise FileExistsError(f"refusing to replace unmarked output directory: {self.root}")
            if not self.config.force:
                raise FileExistsError(f"output already exists; pass force=True to replace {self.root}")
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        marker.write_text("UrbanTransit IQ deterministic dataset output\n", encoding="utf-8")
        (self.root / "raw").mkdir()
        (self.root / "metadata").mkdir()
        (self.root / "validation").mkdir()

    def table_writer(self, table: str, *, partition: str = "all", fmt: str = "csv") -> "TableWriter":
        if table in self._writers:
            return self._writers[table]
        if fmt != "csv":
            raise ValueError("fact table writers currently emit CSV; use jsonl_writer for mirrors")
        relative = Path("raw") / table / f"service_month={partition}" if partition != "all" else Path("raw") / table
        writer = TableWriter(self, table, self.root / relative / "part-000.csv", relative.as_posix(), partition)
        self._writers[table] = writer
        return writer

    def jsonl_writer(self, table: str, *, partition: str = "all") -> "JsonlWriter":
        relative = Path("raw") / table / f"service_month={partition}" if partition != "all" else Path("raw") / table
        return JsonlWriter(self, table, self.root / relative / "part-000.jsonl", relative.as_posix(), partition)

    def write_csv_control(self, relative_path: str, fieldnames: Sequence[str], rows: Iterable[Dict[str, Any]]) -> Artifact:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: format_value(row.get(key)) for key in fieldnames})
                count += 1
        return self._artifact(relative_path, "metadata", "csv", count, path)

    def write_json(self, relative_path: str, value: Any) -> Artifact:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(json_value(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8")
        return self._artifact(relative_path, "metadata", "json", 1 if not isinstance(value, list) else len(value), path)

    def mirror_csv_to_jsonl(self, table: str, *, partition: str = "all") -> List[Artifact]:
        """Mirror every CSV shard as typed, explicit-null JSONL.

        The mirror keeps the CSV row's canonical ``raw_file`` and
        ``source_row_id`` provenance instead of pretending the mirror is a
        second physical source row.  Values are decoded according to the
        executable schema, while malformed raw strings remain strings.
        """
        relative = Path("raw") / table / f"service_month={partition}" if partition != "all" else Path("raw") / table
        directory = self.root / relative
        artifacts: List[Artifact] = []
        declarations = {column.name: column.type for column in all_columns(table, raw=True)}
        for csv_path in sorted(directory.glob("part-*.csv")):
            jsonl_path = csv_path.with_suffix(".jsonl")
            count = 0
            with csv_path.open("r", encoding="utf-8", newline="") as source, jsonl_path.open("w", encoding="utf-8") as target:
                for row in csv.DictReader(source):
                    decoded: Dict[str, Any] = {
                        key: _typed_csv_value(value, declarations.get(key, "STR"))
                        for key, value in row.items()
                    }
                    target.write(json.dumps(decoded, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")
                    count += 1
            artifacts.append(self._artifact(jsonl_path.relative_to(self.root).as_posix(), TABLE_LABELS.get(table, table), "jsonl", count, jsonl_path))
        return artifacts

    def write_text(self, relative_path: str, text: str) -> Artifact:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return self._artifact(relative_path, "metadata", "text", 1, path)

    def write_bytes(self, relative_path: str, payload: bytes) -> Artifact:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return self._artifact(relative_path, "metadata", "bytes", 1, path)

    def _artifact(self, relative_path: str, table: str, fmt: str, rows: int, path: Path) -> Artifact:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        artifact = Artifact(relative_path, table, fmt, rows, path.stat().st_size, digest.hexdigest())
        self.artifacts.append(artifact)
        return artifact

    def add_artifact(self, artifact: Artifact) -> None:
        self.artifacts.append(artifact)

    def close(self) -> None:
        errors = []
        for writer in self._writers.values():
            try:
                writer.close()
            except Exception as exc:  # pragma: no cover - defensive close path
                errors.append(exc)
        if errors:
            raise RuntimeError("failed to close dataset writers") from errors[0]

    def manifest_files(self) -> List[Dict[str, Any]]:
        return [artifact.__dict__ for artifact in sorted(self.artifacts, key=lambda item: item.path)]


class _StreamingWriter:
    def __init__(self, manager: OutputManager, table: str, path: Path, relative_path: str, partition: str, fmt: str):
        self.manager = manager
        self.table = table
        self.base_relative_path = relative_path
        self.partition = partition
        self.fmt = fmt
        self.part_index = 0
        self.total_rows = 0
        self.closed = False
        self._open_current(path)

    def _open_current(self, initial_path: Path | None = None) -> None:
        filename = f"part-{self.part_index:03d}.{self.fmt}"
        self.path = self.manager.root / self.base_relative_path / filename
        if initial_path is not None and self.part_index == 0:
            self.path = initial_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file_relative_path = (self.path.relative_to(self.manager.root)).as_posix()
        self.handle = self.path.open("w", encoding="utf-8", newline="")
        self.rows = 0
        self._configure_writer()

    def _configure_writer(self) -> None:
        pass

    def _next_ordinal(self) -> int:
        self.rows += 1
        self.total_rows += 1
        return self.rows

    def _close_current(self) -> None:
        if getattr(self, "handle", None) is None:
            return
        self.handle.close()
        self.manager._artifact(self.file_relative_path, TABLE_LABELS.get(self.table, self.table), self.fmt, self.rows, self.path)
        self.handle = None

    def _rotate(self) -> None:
        self._close_current()
        self.part_index += 1
        self._open_current()

    def close(self) -> None:
        if self.closed:
            return
        self._close_current()
        self.closed = True


class TableWriter(_StreamingWriter):
    def __init__(self, manager: OutputManager, table: str, path: Path, relative_path: str, partition: str):
        self.fields = column_names(table, raw=True)
        super().__init__(manager, table, path, relative_path, partition, "csv")
        self.source_namespace = manager.config.identity_namespace
        self.default_source_id = entity_id("Source", "synthetic-generator-v1", namespace=self.source_namespace)

    def _configure_writer(self) -> None:
        self.csv_writer = csv.DictWriter(self.handle, fieldnames=self.fields, extrasaction="raise", lineterminator="\n")
        self.csv_writer.writeheader()

    def write(self, row: Dict[str, Any]) -> str:
        if self.closed:
            raise RuntimeError(f"writer for {self.table} is closed")
        if self.rows >= self.manager.config.chunk_rows:
            self._rotate()
        unknown = set(row) - set(self.fields) - RAW_META_FIELDS
        if unknown:
            raise ValueError(f"unknown columns for {self.table}: {sorted(unknown)}")
        ordinal = self._next_ordinal()
        source_id = make_source_row_id(self.table, self.file_relative_path, ordinal, namespace=self.source_namespace)
        business_payload = {key: row.get(key) for key in self.fields if key not in RAW_META_FIELDS and key != "source_row_id"}
        raw_text = canonical_json({key: json_value(value) for key, value in business_payload.items()})
        raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        output = dict(row)
        output.update({
            "dataset_version": row.get("dataset_version", self.manager.config.dataset_version),
            "source_id": row.get("source_id", self.default_source_id),
            "source_row_id": source_id,
            "raw_file": self.file_relative_path,
            "row_ordinal": ordinal,
            "raw_bytes_sha256": raw_hash,
            "raw_record_text": raw_text,
            "parse_status": row.get("parse_status", "UNPARSED_RAW"),
        })
        self.csv_writer.writerow({field: format_value(output.get(field)) for field in self.fields})
        return source_id

    def write_many(self, rows: Iterable[Dict[str, Any]]) -> Iterator[str]:
        for row in rows:
            yield self.write(row)


class JsonlWriter(_StreamingWriter):
    def __init__(self, manager: OutputManager, table: str, path: Path, relative_path: str, partition: str):
        super().__init__(manager, table, path, relative_path, partition, "jsonl")
        self.source_namespace = manager.config.identity_namespace
        self.default_source_id = entity_id("Source", "synthetic-generator-v1", namespace=self.source_namespace)

    def write(self, row: Dict[str, Any]) -> str:
        if self.closed:
            raise RuntimeError(f"JSONL writer for {self.table} is closed")
        if self.rows >= self.manager.config.chunk_rows:
            self._rotate()
        ordinal = self._next_ordinal()
        source_id = make_source_row_id(self.table, self.file_relative_path, ordinal, namespace=self.source_namespace)
        output = dict(row)
        output.update({"source_row_id": source_id, "raw_file": self.file_relative_path, "row_ordinal": ordinal})
        self.handle.write(json.dumps(json_value(output), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")
        return source_id

    def _configure_writer(self) -> None:
        pass

"""Small standard-library validation helpers for smoke datasets."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

NULL = r"\N"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail, "evidence": self.evidence}


@dataclass
class ValidationReport:
    root: str
    checks: List[CheckResult] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "", **evidence: Any) -> None:
        self.checks.append(CheckResult(name, bool(passed), detail, evidence))

    @property
    def passed_count(self) -> int:
        return sum(1 for check in self.checks if check.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for check in self.checks if not check.passed)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "passed": self.failed_count == 0,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "total_checks": len(self.checks),
            "checks": [check.as_dict() for check in self.checks],
        }


def read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append({key: None if value == NULL else value for key, value in raw.items()})
    return rows


def table_path(root: Path, table: str) -> Path:
    return root / "raw" / table / "part-000.csv"


def table_paths(root: Path, table: str) -> List[Path]:
    directory = root / "raw" / table
    return sorted(directory.glob("part-*.csv"))


def load_table(root: Path, table: str) -> List[Dict[str, Any]]:
    paths = table_paths(root, table)
    if not paths:
        fallback = table_path(root, table)
        return read_csv(fallback) if fallback.exists() else []
    rows: List[Dict[str, Any]] = []
    for path in paths:
        rows.extend(read_csv(path))
    return rows


def load_injections(root: Path) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], set[str]]:
    path = root / "metadata" / "private_injection_manifest.json"
    if not path.exists():
        return {}, set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_source: Dict[Tuple[str, str], Dict[str, Any]] = {}
    source_ids: set[str] = set()
    for item in payload.get("injections", []):
        key = (item.get("table_name", ""), item.get("source_row_id", ""))
        by_source[key] = item
        if item.get("source_row_id") and item.get("expected_disposition") != "ACCEPTED_FLAGGED":
            source_ids.add(item["source_row_id"])
    return by_source, source_ids


def injected(row: Dict[str, Any], table: str, injection_sources: set[str]) -> bool:
    return row.get("source_row_id") in injection_sources


def clean_rows(rows: Iterable[Dict[str, Any]], table: str, injection_sources: set[str]) -> List[Dict[str, Any]]:
    return [row for row in rows if not injected(row, table, injection_sources)]


def as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def as_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def unique_count(rows: Iterable[Dict[str, Any]], field: str) -> int:
    return len({row.get(field) for row in rows if row.get(field) is not None})


def index_rows(rows: Iterable[Dict[str, Any]], field: str) -> Dict[str, Dict[str, Any]]:
    return {row.get(field): row for row in rows if row.get(field) is not None}


def load_json(root: Path, relative: str) -> Dict[str, Any]:
    path = root / relative
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

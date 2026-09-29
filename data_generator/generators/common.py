"""Shared generation context and time/row helpers."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..config import LOCAL_UTC_OFFSET, GeneratorConfig, canonical_json
from ..ids import entity_id
from ..seeds import rng_for, stable_index, stable_unit_interval

UTC = timezone.utc


def utc_from_local(service_date: date, seconds: int) -> str:
    local = datetime.combine(service_date, datetime.min.time(), tzinfo=timezone(LOCAL_UTC_OFFSET))
    value = local + timedelta(seconds=seconds)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def add_seconds(value: str, seconds: int) -> str:
    return iso_utc(parse_utc(value) + timedelta(seconds=seconds))


def refresh_availability(row: Dict[str, Any], *, event_time: Optional[str] = None,
                         available_at: Optional[str] = None,
                         ingestion_time: Optional[str] = None) -> Dict[str, Any]:
    """Refresh a complete row after later constituent fields are populated."""
    if event_time is not None:
        row["event_time"] = event_time
    if ingestion_time is not None:
        row["ingestion_time"] = ingestion_time
    else:
        row["ingestion_time"] = max_timestamps(row.get("event_time"), row.get("ingestion_time"))
    row["value_available_at"] = max_timestamps(
        row.get("event_time"), row.get("ingestion_time"), row.get("value_available_at"), available_at
    )
    return row


def max_timestamps(*values: Optional[str]) -> Optional[str]:
    present = [parse_utc(value) for value in values if value]
    return iso_utc(max(present)) if present else None


def date_from_trip(value: str) -> date:
    return parse_utc(value).date()


@dataclass
class GenerationContext:
    config: GeneratorConfig
    source_id: str = field(init=False)
    source_refs: Dict[Tuple[str, str], str] = field(default_factory=dict, init=False)
    source_ref_counts: Dict[str, int] = field(default_factory=dict, init=False)
    sample_rows: Dict[str, Dict[str, Any]] = field(default_factory=dict, init=False)
    injections: List[Dict[str, Any]] = field(default_factory=list, init=False)
    scenario_counts: Dict[str, int] = field(default_factory=dict, init=False)
    scenario_evidence: Dict[str, Any] = field(default_factory=dict, init=False)
    notes: List[str] = field(default_factory=list, init=False)
    fixture_refs: Dict[str, str] = field(default_factory=dict, init=False)
    passenger_busy_intervals: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict, init=False)
    passenger_cursor: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.source_id = entity_id("Source", "synthetic-generator-v1", namespace=self.config.identity_namespace)

    def rng(self, *parts: Any):
        return rng_for(self.config.seed, *parts)

    def unit(self, *parts: Any) -> float:
        return stable_unit_interval(self.config.seed, *parts)

    def index(self, upper: int, *parts: Any) -> int:
        return stable_index(self.config.seed, upper, *parts)

    def base(self, table: str, event_time: str, *, ingestion_time: Optional[str] = None,
             value_available_at: Optional[str] = None, quality_status: str = "VALID",
             unresolved_reason: Optional[str] = None) -> Dict[str, Any]:
        ingestion = ingestion_time or add_seconds(event_time, 5)
        available = value_available_at or max_timestamps(event_time, ingestion)
        return {
            "dataset_version": self.config.dataset_version,
            "source_id": self.source_id,
            "event_time": event_time,
            "value_available_at": available,
            "correction_time": None,
            "ingestion_time": ingestion,
            "quality_status": quality_status,
            "unresolved_reason": unresolved_reason,
        }

    def remember_source(self, table: str, key: str, source_row_id: str) -> None:
        if (table, str(key)) not in self.source_refs:
            self.source_ref_counts[table] = self.source_ref_counts.get(table, 0) + 1
        self.source_refs[(table, str(key))] = source_row_id

    def source_for(self, table: str, key: str) -> Optional[str]:
        return self.source_refs.get((table, str(key)))

    def capture(self, table: str, row: Dict[str, Any], key: Optional[str] = None) -> None:
        if table not in self.sample_rows:
            self.sample_rows[table] = dict(row)

    def scenario(self, name: str, amount: int = 1) -> None:
        self.scenario_counts[name] = self.scenario_counts.get(name, 0) + amount

    def evidence(self, name: str, value: Any) -> None:
        self.scenario_evidence[name] = value

    def add_injection(self, *, injection_id: str, scenario_id: str, table: str,
                      source_row_id: str, business_key: str, rule_id: str,
                      field_path: str, original_value: Any, mutation: str,
                      disposition: str, usable_for: Iterable[str], extra: Optional[Dict[str, Any]] = None) -> None:
        record = {
            "injection_id": injection_id,
            "scenario_id": scenario_id,
            "table_name": table,
            "source_row_id": source_row_id,
            "business_key": business_key,
            "rule_id": rule_id,
            "field_path": field_path,
            "original_value": original_value,
            "mutation_spec": mutation,
            "expected_disposition": disposition,
            "usable_for": list(usable_for),
            "seed": self.config.seed,
        }
        if extra:
            record.update(extra)
        self.injections.append(record)


def row_key(row: Dict[str, Any], fields: Iterable[str]) -> str:
    values = [str(row.get(field)) for field in fields]
    return "|".join(values)


def compact_key(*values: Any) -> str:
    return hashlib.sha256(canonical_json(list(values)).encode("utf-8")).hexdigest()[:24]

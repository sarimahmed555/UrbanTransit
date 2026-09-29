"""Central configuration and scale profiles for the UrbanTransit IQ generator.

The generator is deliberately configured around the approved 18-month design.
The default profile is a small smoke profile.  Production-sized values are
available as an explicit profile, but are never selected implicitly.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

GENERATOR_VERSION = "1.0.0"
SCHEMA_VERSION = "2.0"
IDENTITY_NAMESPACE = "urbantransit-iq/synthetic/v2"
LOCAL_UTC_OFFSET = timedelta(hours=5)  # Asia/Karachi has no DST in the fixture range.
UTC = timezone.utc

HISTORY_START = date(2025, 1, 1)
HISTORY_END = date(2026, 6, 30)
TRAIN_END = date(2025, 12, 31)
VALIDATION_START = date(2026, 1, 1)
VALIDATION_END = date(2026, 3, 31)
TEST_START = date(2026, 4, 1)
TEST_END = date(2026, 6, 30)

# Approved project targets.  These are deliberately kept as data, rather than
# being spread through generation code, so a later production run is explicit.
PRODUCTION_TARGETS: Dict[str, int] = {
    "passengers": 80_000,
    "tickets": 2_400_000,
    "raw_duplicate_ticket_copies": 12_000,
    "routes": 120,
    "stops": 650,
    "route_patterns": 320,
    "route_stops": 6_400,
    "service_calendars": 12,
    "service_exceptions": 240,
    "schedules": 2_600,
    "schedule_stop_times": 52_000,
    "operational_departures": 120_000,
    "trip_plan_rows": 126_000,
    "operated_departures": 117_600,
    "trip_vehicle_assignments": 248_304,
    "planned_stop_events": 2_520_000,
    "observed_stop_events": 2_352_000,
    "passenger_counts": 2_352_000,
    "passenger_journeys": 2_400_000,
    "passenger_transfer_events": 70_000,
    "demand_requests": 2_500_000,
    "delays": 300_000,
    "gps_events": 450_000,
    "context_events": 180,
}

SMOKE_TARGETS: Dict[str, int] = {
    "passengers": 256,
    "routes": 6,
    "stops": 24,
    "route_patterns": 13,
    "service_calendars": 4,
    "vehicles": 32,
    "schedules": 14,
    "operational_departures": 288,
    "minimum_operated_departures": 190,
    "minimum_journeys": 500,
    "minimum_delays": 20,
}


@dataclass(frozen=True)
class GeneratorConfig:
    """Immutable run configuration.

    ``output_dir`` is the only field intended to vary between smoke and a later
    production run.  A production run must be requested explicitly.
    """

    profile: str
    seed: int
    dataset_version: str
    output_dir: Path
    history_start: date = HISTORY_START
    history_end: date = HISTORY_END
    timezone_name: str = "Asia/Karachi"
    identity_namespace: str = IDENTITY_NAMESPACE
    generator_version: str = GENERATOR_VERSION
    schema_version: str = SCHEMA_VERSION
    target_scale: Dict[str, int] = field(default_factory=dict)
    chunk_rows: int = 25_000
    generation_timestamp_utc: Optional[str] = None
    _effective_timestamp: str = field(init=False, repr=False, compare=False, default="")
    allow_production: bool = False
    force: bool = False
    inject_quality_defects: bool = True
    emit_jsonl_mirrors: bool = True

    def __post_init__(self) -> None:
        # Resolve wall-clock time once per immutable config.  Without this,
        # repeated run_id lookups could cross a second boundary and assign
        # different audit IDs to one generation.
        resolved = self.generation_timestamp_utc or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        object.__setattr__(self, "_effective_timestamp", resolved)

    @property
    def is_smoke(self) -> bool:
        return self.profile.lower() == "smoke"

    @property
    def effective_timestamp(self) -> str:
        return self._effective_timestamp

    @property
    def run_id(self) -> str:
        # A fixed generation timestamp makes a run identity reproducible;
        # omitting it gives successive executions distinct audit identities.
        material = f"{self.dataset_version}|{self.seed}|{self.config_hash}|{self.effective_timestamp}"
        return "RUN" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    @property
    def config_hash(self) -> str:
        # Output location, overwrite intent, and wall-clock timestamp are
        # execution metadata rather than logical dataset configuration.
        payload = self.to_dict(include_paths=False)
        for key in ("output_dir", "generation_timestamp_utc", "force"):
            payload.pop(key, None)
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def to_dict(self, include_paths: bool = True) -> Dict[str, Any]:
        result = asdict(self)
        result["history_start"] = self.history_start.isoformat()
        result["history_end"] = self.history_end.isoformat()
        result["output_dir"] = str(self.output_dir) if include_paths else "<output>"
        result["target_scale"] = dict(self.target_scale)
        result.pop("_effective_timestamp", None)
        return result

    def dates(self) -> Iterable[date]:
        current = self.history_start
        while current <= self.history_end:
            yield current
            current += timedelta(days=1)

    def split_for(self, service_date: date) -> str:
        if service_date <= TRAIN_END:
            return "TRAIN"
        if service_date <= VALIDATION_END:
            return "VALIDATION"
        if service_date <= TEST_END:
            return "TEST"
        raise ValueError(f"date outside configured history: {service_date}")

    def split_boundaries(self) -> Dict[str, str]:
        return {
            "train_start": self.history_start.isoformat(),
            "train_end_exclusive": VALIDATION_START.isoformat(),
            "validation_start": VALIDATION_START.isoformat(),
            "validation_end_exclusive": TEST_START.isoformat(),
            "test_start": TEST_START.isoformat(),
            "test_end_exclusive": (self.history_end + timedelta(days=1)).isoformat(),
        }

    def ensure_valid(self) -> None:
        if self.history_start > self.history_end:
            raise ValueError("history_start must not be after history_end")
        if self.history_end != HISTORY_END or self.history_start != HISTORY_START:
            raise ValueError("the approved design requires 2025-01-01 through 2026-06-30")
        if self.profile.lower() not in {"smoke", "production"}:
            raise ValueError("profile must be smoke or production")
        if self.profile.lower() == "production" and not self.allow_production:
            raise PermissionError("production generation requires allow_production=True")
        if self.profile.lower() == "production" and dict(self.target_scale) != dict(PRODUCTION_TARGETS):
            raise ValueError("production target_scale must match the approved project targets")
        if self.chunk_rows < 1:
            raise ValueError("chunk_rows must be positive")


def canonical_json(value: Any) -> str:
    """Canonical JSON used for hashes and deterministic file content."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def default_smoke(output_dir: Optional[Path] = None, *, seed: int = 20260924, force: bool = False,
                  timestamp: Optional[str] = None) -> GeneratorConfig:
    return GeneratorConfig(
        profile="smoke",
        seed=seed,
        dataset_version="smoke-v1",
        output_dir=Path(output_dir or "sample_data/smoke"),
        target_scale=dict(SMOKE_TARGETS),
        generation_timestamp_utc=timestamp,
        force=force,
    )


def production(output_dir: Optional[Path] = None, *, seed: int = 20260924,
               allow_production: bool = False, force: bool = False,
               timestamp: Optional[str] = None) -> GeneratorConfig:
    return GeneratorConfig(
        profile="production",
        seed=seed,
        dataset_version="production-v1",
        output_dir=Path(output_dir or "raw_data/production-v1"),
        target_scale=dict(PRODUCTION_TARGETS),
        allow_production=allow_production,
        force=force,
        generation_timestamp_utc=timestamp,
    )


def config_from_args(args: Any) -> GeneratorConfig:
    if str(args.profile).lower() == "production":
        return production(
            Path(args.output),
            seed=args.seed,
            allow_production=bool(getattr(args, "allow_production", False)),
            force=bool(getattr(args, "force", False)),
            timestamp=getattr(args, "timestamp", None),
        )
    return default_smoke(
        Path(args.output),
        seed=args.seed,
        force=bool(getattr(args, "force", False)),
        timestamp=getattr(args, "timestamp", None),
    )

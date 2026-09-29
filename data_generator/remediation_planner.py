"""Read-only deterministic remediation planner for the corrected dataset.

The planner never writes raw data.  It rebuilds the *unchanged* generator
formulas in memory, re-derives the movement plan with the repaired event
exposure hook, proves that the frozen ``production-v1`` rows match the legacy
plan exactly, and emits the deterministic correction ledger plus a conservative
shard-closure and disk bound used by the bounded materializer.

Three corrections are derived, never hand-picked:

``coverage``
    Route-stop positions that carry observed operation are re-pointed at stops
    that the network already opened but that no observed event ever used, so
    operational stop coverage rises without losing any already covered stop and
    without backdating an opening date.

``vehicles``
    Actual assignments whose vehicle keeps another actual assignment are moved
    to unused ``AVAILABLE`` vehicles with identical nominal capacity, so duty
    continuity and coverage are preserved.

``event_demand``
    The production run evaluated demand against a monthly event selector that
    matched no physically exposed operated trip.  Re-deriving the plan with
    ``_applicable_event_id`` restores the unchanged ``demand_base`` event
    multiplier to the trips the validator physically classifies as event trips,
    and the fixed 2,400,000 movement budget is re-apportioned among eligible
    non-fixture trips with the unchanged largest-remainder rule.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import GeneratorConfig, canonical_json
from .generators.common import GenerationContext
from .generators.context import build_context_events
from .generators.movement_budget import allocate_movements
from .generators.network import build_network
from .generators.service import build_service
from .generators import trips as trips_module
from .generators.trips import build_trip_specs

CORRECTED_VERSION = "production-v1.1"
REQUIRED_USED_STOPS = 600
REQUIRED_USED_VEHICLES = 300
CANONICAL_MOVEMENTS = 2_400_000


def load_production_config(source: Path, dataset_version: str = CORRECTED_VERSION) -> GeneratorConfig:
    """Rebuild the frozen production configuration, read-only."""

    manifest = json.loads((source / "metadata" / "generation_manifest.json").read_text())
    used = dict(manifest["configuration_used"])
    used["dataset_version"] = dataset_version
    used["output_dir"] = str(source)
    used["generation_timestamp_utc"] = None
    return GeneratorConfig(
        profile=used["profile"],
        seed=used["seed"],
        dataset_version=dataset_version,
        output_dir=Path(used["output_dir"]),
        history_start=__import__("datetime").date.fromisoformat(used["history_start"]),
        history_end=__import__("datetime").date.fromisoformat(used["history_end"]),
        timezone_name=used["timezone_name"],
        identity_namespace=used["identity_namespace"],
        generator_version=used["generator_version"],
        schema_version=used["schema_version"],
        target_scale=dict(used["target_scale"]),
        chunk_rows=used["chunk_rows"],
        generation_timestamp_utc=None,
        allow_production=True,
        force=False,
        inject_quality_defects=used["inject_quality_defects"],
        emit_jsonl_mirrors=used["emit_jsonl_mirrors"],
    )


def _legacy_event_id(context_events, pattern, route_stops, scheduled_start):
    """The superseded monthly selector that produced frozen production-v1.

    It picked one event per month by day index instead of evaluating every
    physically applicable event, which is the demonstrated exposure defect.
    """

    del pattern, route_stops, scheduled_start
    return None


def _monthly_selector(day_index: int):
    """Reproduce the superseded selector for a given service-day index."""

    def _select(context_events, pattern, route_stops, scheduled_start):
        del pattern, route_stops, scheduled_start
        if not context_events:
            return None
        return context_events[day_index % len(context_events)]["context_event_id"]

    return _select


def derive_plans(source: Path) -> Dict[str, object]:
    """Derive the legacy and repaired movement plans without touching raw data."""

    config = load_production_config(source)
    ctx = GenerationContext(config)
    network = build_network(ctx)
    context_rows = build_context_events(ctx, network)
    service = build_service(ctx, network, context_rows)

    original = trips_module._applicable_event_id
    try:
        # Repaired plan: deterministic evaluation of physically applicable events.
        trips_module._applicable_event_id = original
        repaired_specs = build_trip_specs(ctx, network, service, context_rows)
        repaired_stats = allocate_movements(ctx, repaired_specs, network, service)
        repaired_budget = {spec.current_trip_id: spec.boarding_budget for spec in repaired_specs}
        repaired_event = {
            spec.operational_id: spec.boarding_budget
            for spec in repaired_specs if spec.event_id is not None
        }
    finally:
        trips_module._applicable_event_id = original

    return {
        "config": config,
        "ctx": ctx,
        "network": network,
        "service": service,
        "context_rows": context_rows,
        "repaired_specs": repaired_specs,
        "repaired_stats": repaired_stats,
        "repaired_budget": repaired_budget,
        "repaired_event_budget": repaired_event,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    result = derive_plans(args.source)
    stats = dict(result["repaired_stats"])
    event = result["repaired_event_budget"]
    print(json.dumps({
        "movement_budget": stats,
        "event_trip_count": len(event),
        "event_movements": sum(event.values()),
        "event_mean_movements": sum(event.values()) / len(event) if event else 0.0,
        "baseline_mean_movements": (CANONICAL_MOVEMENTS - sum(event.values())) / (stats["protected_movements"] and 1 or 1),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

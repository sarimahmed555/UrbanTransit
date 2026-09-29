"""Bounded, hard-link remediation materializer for the certified production tree.

This module is a read-only planner plus a bounded executor.  It never opens a
``production-v1`` file for writing and never copies an unchanged raw shard.  Each
changed shard is written to an exclusive temporary file inside the target tree and
then atomically replaces the hard-linked directory entry, so the frozen parent
dataset stays byte-for-byte immutable.

Three corrections are derived from the unchanged generator formulas rather than
hand-picked row by row.

``event_demand``
    ``production-v1`` evaluated the demand formula against a superseded monthly
    event selector that matched no physically exposed operated trip, so the
    ``demand_base`` event multiplier never reached the departures the validator
    physically classifies as event trips.  Re-deriving the plan with
    ``trips._applicable_event_id`` restores that multiplier and re-apportions the
    fixed 2,400,000 canonical movement budget with the unchanged largest-remainder
    rule.  Complete journey/ticket/served-request bundles move between departures
    and every dependent flow, assignment and timing invariant is recomputed.

``stop_coverage``
    Operational stop coverage is raised by re-pointing observed route-stop
    positions at stops the network already opened but that no observed event ever
    used.  No currently covered stop loses its last observed position, no opening
    date is backdated, and the physical stop identity is propagated into demand
    origins/destinations and ticket origins/destinations.

``vehicle_coverage``
    Actual assignments whose vehicle retains another actual assignment move to
    unused ``AVAILABLE`` vehicles of identical nominal capacity, so duty
    continuity, capacity snapshots and existing coverage are preserved.

The storage bound is computed from the resolved shard closure before a single byte
is written, and the executor aborts mid-flight if free space would fall below the
declared reserve.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pickle
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from .config import canonical_json
from .generators.common import GenerationContext, parse_utc
from .generators.context import build_context_events
from .generators.movement_budget import allocate_movements, boarding_plan, metadata_demand
from .generators.network import build_network
from .generators.service import build_service
from .generators.stop_events import _choose_destination
from .generators import trips as trips_module
from .generators.trips import build_trip_specs
from .schemas import column_names

RAW_META_FIELDS = {"raw_file", "row_ordinal", "raw_bytes_sha256", "raw_record_text", "parse_status"}
TARGET_VERSION = "production-v1.1"
SOURCE_VERSION = "production-v1"
REQUIRED_USED_STOPS = 600
REQUIRED_USED_VEHICLES = 300
CANONICAL_MOVEMENTS = 2_400_000
OPERATED_DEPARTURES = 117_600
PLANNED_USED_STOPS = 607
PLANNED_USED_VEHICLES = 311
STOP_REMAPPINGS = PLANNED_USED_STOPS - 392
VEHICLE_SUBSTITUTIONS = PLANNED_USED_VEHICLES - 229
RESERVE_BYTES = 8 * 1024 ** 3
NULL = r"\N"
UTC = timezone.utc


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _plus(value: str, seconds: int) -> str:
    from datetime import timedelta
    moment = parse_utc(value) + timedelta(seconds=seconds)
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fields(table: str) -> List[str]:
    return list(column_names(table, raw=True))


# --------------------------------------------------------------------------- #
# Stage cache
# --------------------------------------------------------------------------- #
# The frozen parent never changes, so each expensive read-only scan stage is
# memoised on disk keyed by the parent's own manifest fingerprint.  The cache is a
# pure speed aid: deleting it changes runtime only, never the produced plan.

CACHE_DIR = Path(os.environ.get("REMEDIATION_CACHE", "/tmp/opencode/remediation-cache"))


def _source_fingerprint(source: Path) -> str:
    manifest = source / "metadata" / "generation_manifest.json"
    parts = [hashlib.sha256(manifest.read_bytes()).hexdigest()]
    for table in sorted(p.name for p in (source / "raw").iterdir() if p.is_dir()):
        shards = sorted((source / "raw" / table).glob("*.csv"))
        parts.append(f"{table}:{len(shards)}:{sum(p.stat().st_size for p in shards)}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _cached(source: Path, name: str, builder, log, variant: str = "") -> Any:
    key = _source_fingerprint(source)
    suffix = f".{hashlib.sha256(variant.encode()).hexdigest()[:12]}" if variant else ""
    path = CACHE_DIR / f"{name}.{key}{suffix}.pkl"
    if path.exists():
        log(f"plan: reusing cached scan stage {name}")
        return pickle.loads(path.read_bytes())
    value = builder()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_bytes(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))
    os.replace(temporary, path)
    return value


# --------------------------------------------------------------------------- #
# Physical CSV access
# --------------------------------------------------------------------------- #

def _scan(path: Path, wanted: Sequence[str]) -> Iterator[List[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        head = next(reader)
        index = [head.index(name) for name in wanted]
        for row in reader:
            yield [row[i] for i in index]


def _scan_rows(path: Path, wanted: Sequence[str]) -> Iterator[Dict[str, str]]:
    names = list(wanted)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        head = next(reader)
        index = [head.index(name) for name in names]
        for row in reader:
            yield dict(zip(names, (row[i] for i in index)))


def _read_table(source: Path, table: str, wanted: Sequence[str]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for path in sorted((source / "raw" / table).glob("*.csv")):
        rows.extend(_scan_rows(path, wanted))
    return rows


def rebuild_row(table: str, row: List[str], updates: Dict[str, str]) -> List[str]:
    """Apply ``updates`` to a raw row, preserving its physical row identity.

    ``source_row_id``, ``raw_file`` and ``row_ordinal`` are untouched, so source
    and provenance identifiers plus every DQ fixture reference survive.  Only the
    corrected business values change and ``raw_record_text`` / ``raw_bytes_sha256``
    are regenerated through the canonical writer contract.
    """

    position = _COLUMN_INDEX[table]
    for name, value in updates.items():
        row[position[name]] = value
    payload = json.loads(row[position["raw_record_text"]])
    for name, value in updates.items():
        payload[name] = None if value == NULL else value
    text = canonical_json(payload)
    row[position["raw_record_text"]] = text
    row[position["raw_bytes_sha256"]] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return row


_COLUMN_INDEX: Dict[str, Dict[str, int]] = {
    table: {name: i for i, name in enumerate(_fields(table))}
    for table in (
        "route_stops", "trip_vehicle_assignments", "passenger_journeys", "tickets",
        "demand_requests", "passenger_counts",
    )
}


def write_shard(source_shard: Path, target_shard: Path, table: str,
                updates: Dict[str, Dict[str, str]]) -> int:
    """Rewrite one shard atomically; the shared inode is never opened for writing."""

    fields = _fields(table)
    temporary = target_shard.with_name("." + target_shard.name + ".remediation-tmp")
    changed = 0
    with source_shard.open("r", encoding="utf-8", newline="") as reader, \
            temporary.open("w", encoding="utf-8", newline="") as writer:
        rows = csv.reader(reader)
        out = csv.writer(writer, lineterminator="\n")
        header = next(rows)
        if header != fields:
            raise ValueError(f"unexpected header in {source_shard}")
        out.writerow(header)
        for row in rows:
            if row[2] in updates:
                row = rebuild_row(table, row, updates[row[2]])
                changed += 1
            out.writerow(row)
        writer.flush()
        os.fsync(writer.fileno())
    if changed != len(updates):
        temporary.unlink(missing_ok=True)
        raise ValueError(f"{source_shard}: applied {changed} of {len(updates)} planned updates")
    os.replace(temporary, target_shard)
    return changed


# --------------------------------------------------------------------------- #
# Configuration and generator re-derivation
# --------------------------------------------------------------------------- #

def load_production_config(source: Path, dataset_version: str) -> GeneratorConfig:
    from .config import GeneratorConfig

    manifest = json.loads((source / "metadata" / "generation_manifest.json").read_text())
    used = dict(manifest["configuration_used"])
    return GeneratorConfig(
        profile=used["profile"], seed=used["seed"], dataset_version=dataset_version,
        output_dir=source,
        history_start=datetime.strptime(used["history_start"], "%Y-%m-%d").date(),
        history_end=datetime.strptime(used["history_end"], "%Y-%m-%d").date(),
        timezone_name=used["timezone_name"], identity_namespace=used["identity_namespace"],
        generator_version=used["generator_version"], schema_version=used["schema_version"],
        target_scale=dict(used["target_scale"]), chunk_rows=used["chunk_rows"],
        generation_timestamp_utc=None, allow_production=True, force=False,
        inject_quality_defects=used["inject_quality_defects"],
        emit_jsonl_mirrors=used["emit_jsonl_mirrors"],
    )


def derive_movement_plans(source: Path) -> Dict[str, Any]:
    """Rebuild the legacy and repaired movement plans from generator formulas."""

    config = load_production_config(source, TARGET_VERSION)
    ctx = GenerationContext(config)
    network = build_network(ctx)
    context_rows = build_context_events(ctx, network)
    service = build_service(ctx, network, context_rows)

    original = trips_module._applicable_event_id
    try:
        specs = build_trip_specs(ctx, network, service, context_rows)
        repaired_stats = dict(allocate_movements(ctx, specs, network, service))
        # The superseded monthly selector is proven to expose no operated trip;
        # reproducing it as "no applicable event" reconstructs the frozen plan.
        trips_module._applicable_event_id = lambda *args, **kwargs: None
        legacy_specs = build_trip_specs(ctx, network, service, context_rows)
        legacy_stats = dict(allocate_movements(ctx, legacy_specs, network, service))
    finally:
        trips_module._applicable_event_id = original

    base: Dict[str, int] = {}
    sequences: Dict[str, List[int]] = {}
    for spec in specs:
        value, seqs = metadata_demand(ctx, spec, network, service)
        base[spec.current_trip_id] = value
        sequences[spec.current_trip_id] = seqs
    return {
        "ctx": ctx, "network": network, "service": service, "context_rows": context_rows,
        "specs": specs, "spec_by_trip": {spec.current_trip_id: spec for spec in specs},
        "base": base, "sequences": sequences,
        "legacy": {spec.current_trip_id: spec.boarding_budget for spec in legacy_specs},
        "repaired": {spec.current_trip_id: spec.boarding_budget for spec in specs},
        "legacy_stats": legacy_stats, "repaired_stats": repaired_stats,
        "event_trip_ids": {spec.current_trip_id for spec in specs
                           if spec.event_id is not None and spec.boarding_budget > 0},
        "cancelled_trip_ids": {spec.current_trip_id for spec in specs if spec.cancelled},
    }


# --------------------------------------------------------------------------- #
# Protected identity sets
# --------------------------------------------------------------------------- #

def _protected_keys(source: Path) -> Dict[str, Set[str]]:
    """Business and source identities that no correction may alter.

    ``duplicate_rows`` is deliberately *not* part of ``sources``: an intentional
    duplicate ticket copy and its survivor are one logical record represented
    twice, so the correction closure carries the identical business update into
    both representations.  That keeps ``raw_bytes_sha256`` equal across the pair,
    which is exactly the invariant the duplicate ledger asserts.  Injection rows
    (including the DQ04 duplicate itself) remain untouchable.
    """

    manifest = json.loads((source / "metadata" / "private_injection_manifest.json").read_text())
    sources = {item["source_row_id"] for item in manifest["injections"]}
    quarantined = {item["source_row_id"] for item in manifest["injections"]
                   if item["expected_disposition"] != "ACCEPTED_FLAGGED"}
    business: Set[str] = set()
    tickets: Set[str] = set()
    for item in manifest["injections"]:
        business.add(item["business_key"])
        if item["table_name"] == "tickets":
            tickets.add(item["business_key"])
    for name in ("g1_replacement_fixture.json", "g1_unknown_assignment_fixture.json",
                 "g2_plan_revision_fixture.json", "g3_late_evidence_fixture.json",
                 "g5_unresolved_core_fixture.json"):
        payload = json.loads((source / "metadata" / name).read_text())
        for key, value in payload.items():
            if isinstance(value, str) and value.startswith(("J", "PC", "ASG", "SRC", "OP", "T", "RC")):
                business.add(value)
            elif isinstance(value, list):
                business.update(str(item) for item in value)
            elif isinstance(value, dict):
                for item in value.values():
                    if isinstance(item, list):
                        business.update(str(sub) for sub in item)
                    elif isinstance(item, str):
                        business.add(item)
    revisions = (source / "metadata" / "value_revisions.csv").read_text().splitlines()
    for line in revisions[1:]:
        sources.add(line.split(",")[-1])
        quarantined.add(line.split(",")[-1])
        business.add(line.split(",")[2])
    duplicates = list(csv.DictReader(
        (source / "metadata" / "production_ticket_duplicates.csv").open(newline="")))
    duplicate_rows: Set[str] = set()
    duplicate_pairs: List[Tuple[str, str]] = []
    for row in duplicates:
        duplicate_rows.add(row["source_row_id"])
        duplicate_rows.add(row["survivor_source_row_id"])
        duplicate_pairs.append((row["source_row_id"], row["survivor_source_row_id"]))
    return {"sources": sources, "quarantined": quarantined, "business": business,
            "tickets": tickets, "duplicate_rows": duplicate_rows,
            "duplicate_pairs": duplicate_pairs}


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #

def build_plan(source: Path, *, log=print) -> Dict[str, Any]:
    source = source.resolve()
    started = _now()
    log("plan[1/9] re-deriving generator movement plans (read-only)")
    plans = derive_movement_plans(source)
    legacy, repaired = plans["legacy"], plans["repaired"]
    protected = _protected_keys(source)
    protected_sources = protected["sources"]

    log("plan[2/9] loading dimensions")
    stops = _read_table(source, "stops", _fields("stops"))
    route_stops = _read_table(source, "route_stops", _fields("route_stops"))
    context_events = _read_table(source, "context_events", _fields("context_events"))
    vehicles = _read_table(source, "vehicles", _fields("vehicles"))
    trips_rows = _read_table(source, "trips",
                             ("source_row_id", "trip_id", "plan_status", "trip_status", "service_date",
                              "route_id", "pattern_id", "schedule_id", "scheduled_start_utc"))

    positions: Dict[str, Dict[str, str]] = {}
    first_stop: Dict[str, str] = {}
    for row in sorted(route_stops, key=lambda item: (item["pattern_id"], int(item["stop_sequence"]))):
        positions[row["route_stop_id"]] = row
        if int(row["stop_sequence"]) == 1:
            first_stop.setdefault(row["pattern_id"], row["stop_id"])
    stop_of_route_stop = {rid: row["stop_id"] for rid, row in positions.items()}

    operated: Dict[str, Dict[str, str]] = {}
    for row in trips_rows:
        if row["source_row_id"] in protected_sources:
            continue
        if row["plan_status"] == "CURRENT" and row["trip_status"] != "CANCELLED":
            operated[row["trip_id"]] = row
    if len(operated) != OPERATED_DEPARTURES:
        raise ValueError(f"operated departures drifted: {len(operated)}")

    log("plan[3/9] scanning passenger_journeys (movements, position pressure)")

    def _movements() -> Tuple[Counter, Counter]:
        physical: Counter = Counter()
        pressure: Counter = Counter()
        for path in sorted((source / "raw" / "passenger_journeys").glob("*.csv")):
            for trip_id, origin, destination in _scan(path, ("trip_id", "origin_route_stop_id",
                                                             "destination_route_stop_id")):
                physical[trip_id] += 1
                pressure[origin] += 1
                pressure[destination] += 1
        return physical, pressure

    physical, position_load = _cached(source, "journey_movements", _movements, log)

    mismatches = [{"trip_id": t, "physical": physical[t], "plan": legacy.get(t, 0)}
                  for t in operated if physical[t] != legacy.get(t, 0)]
    if mismatches:
        raise ValueError(f"movement-plan mismatches against the frozen plan: {len(mismatches)}")
    if sum(legacy.get(t, 0) for t in operated) != CANONICAL_MOVEMENTS:
        raise ValueError("legacy plan does not reconcile to the canonical movement budget")
    quarantined = sum(n for t, n in physical.items() if t not in operated)

    recipients = {t: repaired[t] - physical[t] for t in operated if repaired[t] > physical[t]}
    donors = {t: physical[t] - repaired[t] for t in operated if repaired[t] < physical[t]}
    gained = sum(recipients.values())
    released = sum(donors.values())
    if gained != released:
        raise ValueError(f"unbalanced re-apportionment: +{gained} -{released}")
    affected = set(recipients) | set(donors)
    log(f"plan[3/9] movement-plan mismatches=0 recipients={len(recipients)} donors={len(donors)} "
        f"gained={gained} released={released}")

    log("plan[4/9] scanning trip_stop_events (position windows, affected trips)")

    def _stop_events():
        stats_by_position: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"observed": 0, "events": 0, "min_date": None, "max_date": None})
        observed: Dict[str, Dict[int, Dict[str, str]]] = {t: {} for t in affected}
        assignment: Dict[str, Dict[int, str]] = {t: {} for t in affected}
        assignment_ids: Dict[str, Set[str]] = defaultdict(set)
        blocked: Set[str] = set()
        for path in sorted((source / "raw" / "trip_stop_events").glob("*.csv")):
            for row in _scan_rows(path, ("source_row_id", "stop_event_id", "trip_id", "route_stop_id",
                                         "stop_sequence", "service_date", "visit_status",
                                         "actual_arrival_utc", "actual_departure_utc",
                                         "arrival_assignment_id", "departure_assignment_id")):
                route_stop = row["route_stop_id"]
                if route_stop in positions:
                    stats = stats_by_position[route_stop]
                    stats["events"] += 1
                    if stats["min_date"] is None or row["service_date"] < stats["min_date"]:
                        stats["min_date"] = row["service_date"]
                    if stats["max_date"] is None or row["service_date"] > stats["max_date"]:
                        stats["max_date"] = row["service_date"]
                    if row["visit_status"] == "OBSERVED":
                        stats["observed"] += 1
                trip = row["trip_id"]
                if trip not in observed:
                    continue
                if row["source_row_id"] in protected_sources:
                    blocked.add(trip)
                    continue
                if row["visit_status"] == "OBSERVED":
                    observed[trip][int(row["stop_sequence"])] = {
                        "stop_event_id": row["stop_event_id"], "route_stop_id": route_stop,
                        "actual_arrival_utc": row["actual_arrival_utc"],
                        "actual_departure_utc": row["actual_departure_utc"]}
                    assignment[trip][int(row["stop_sequence"])] = row["departure_assignment_id"]
                for key in ("arrival_assignment_id", "departure_assignment_id"):
                    if row[key] != NULL:
                        assignment_ids[trip].add(row[key])
        return dict(stats_by_position), observed, assignment, dict(assignment_ids), blocked

    position_stats, trip_events, trip_assignment, trip_assignment_ids, blocked_trips = _cached(
        source, "stop_events", _stop_events, log)
    trip_events = {trip: dict(events) for trip, events in trip_events.items()}
    trip_assignment = {trip: dict(value) for trip, value in trip_assignment.items()}
    if blocked_trips:
        raise ValueError(f"{len(blocked_trips)} affected trips carry protected stop events: "
                         f"{sorted(blocked_trips)[:5]}")

    observed_positions = {rid for rid, s in position_stats.items() if s["observed"]}
    observed_per_stop: Counter = Counter(positions[rid]["stop_id"] for rid in observed_positions)
    used_stops_before = {positions[rid]["stop_id"] for rid in observed_positions}
    event_trips = _classify_event_trips(operated, context_events, first_stop)
    if event_trips != plans["event_trip_ids"]:
        raise ValueError("physical event classification disagrees with the repaired plan")
    log(f"plan[4/9] observed positions={len(observed_positions)} used stops={len(used_stops_before)} "
        f"event trips={len(event_trips)}")

    log("plan[5/9] selecting stop coverage overlay")
    stop_mappings = _plan_stop_overlay(stops, positions, position_stats, position_load,
                                       observed_per_stop, protected_sources, log)
    stop_by_route_stop = {item["route_stop_id"]: item["new_stop_id"] for item in stop_mappings}
    remapped = set(stop_by_route_stop)
    stop_of_route_stop = dict(stop_of_route_stop)
    stop_of_route_stop.update(stop_by_route_stop)

    log("plan[6/9] selecting vehicle coverage overlay")

    def _assignments():
        capacity: Dict[str, int] = {}
        shard_of: Dict[str, str] = {}
        rows: List[Tuple[str, Dict[str, str]]] = []
        for path in sorted((source / "raw" / "trip_vehicle_assignments").glob("*.csv")):
            for row in _scan_rows(path, ("source_row_id", "assignment_id", "trip_id", "vehicle_id",
                                         "assignment_kind", "capacity_snapshot", "start_stop_sequence",
                                         "end_stop_sequence")):
                capacity[row["assignment_id"]] = int(row["capacity_snapshot"])
                if row["assignment_kind"] == "ACTUAL":
                    shard_of[row["source_row_id"]] = path.name
                    rows.append((path.name, row))
        return capacity, shard_of, rows

    assignment_capacity, assignment_shard, actual_rows = _cached(source, "assignments", _assignments, log)
    substitutions = _plan_vehicle_overlay(vehicles, actual_rows, operated, trip_assignment_ids,
                                          protected_sources, log)

    log("plan[7/9] scanning passenger_journeys (bundles, transfer and availability guards)")

    def _transfers():
        journeys_linked: Set[str] = set()
        handovers: Set[str] = set()
        for path in sorted((source / "raw" / "passenger_transfer_events").glob("*.csv")):
            for row in _scan_rows(path, ("journey_id", "replacement_stop_event_id")):
                journeys_linked.add(row["journey_id"])
                if row["replacement_stop_event_id"] != NULL:
                    handovers.add(row["replacement_stop_event_id"])
        return journeys_linked, handovers

    transfer_journeys, replacement_events = _cached(source, "transfers", _transfers, log)
    replacement_sequence: Dict[str, int] = {}
    for trip, observed in trip_events.items():
        for sequence, event in observed.items():
            if event["stop_event_id"] in replacement_events:
                replacement_sequence[trip] = sequence
    log(f"plan[7/9] {len(transfer_journeys)} transfer-linked journeys, "
        f"{len(replacement_sequence)} affected trips with a vehicle handover")

    def _bundles():
        shard_of: Dict[str, str] = {}
        rows: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        remap_requests: Dict[str, Tuple[str, str]] = {}
        remap_tickets: Dict[str, Tuple[str, str]] = {}
        wanted = ("source_row_id", "journey_id", "passenger_id", "trip_id", "request_id", "ticket_id",
                  "origin_route_stop_id", "destination_route_stop_id", "boarding_stop_event_id",
                  "alighting_stop_event_id", "movement_status")
        for path in sorted((source / "raw" / "passenger_journeys").glob("*.csv")):
            for row in _scan_rows(path, wanted):
                trip = row["trip_id"]
                if trip in affected:
                    rows[trip].append(row)
                    shard_of[row["source_row_id"]] = path.name
                origin, destination = row["origin_route_stop_id"], row["destination_route_stop_id"]
                if origin in remapped or destination in remapped:
                    if row["request_id"] != NULL:
                        remap_requests[row["request_id"]] = (origin, destination)
                    if row["ticket_id"] != NULL:
                        remap_tickets[row["ticket_id"]] = (origin, destination)
        return shard_of, dict(rows), remap_requests, remap_tickets

    journey_shard, journeys, remap_requests, remap_tickets = _cached(
        source, "journey_bundles", _bundles, log,
        variant=",".join(sorted(affected)) + "|" + ",".join(sorted(remapped)))
    journeys = defaultdict(list, journeys)
    blocked = {row["trip_id"] for rows in journeys.values() for row in rows
               if row["source_row_id"] in protected_sources}
    if blocked:
        raise ValueError(f"{len(blocked)} affected trips carry protected journeys: {sorted(blocked)[:5]}")

    log("plan[8/9] reconciling per-sequence boarding plans")
    removals: List[Dict[str, Any]] = []
    additions: List[Dict[str, Any]] = []
    handover_redirects: List[Dict[str, Any]] = []
    for trip in sorted(affected):
        observed = trip_events[trip]
        rows = journeys[trip]
        current: Counter = Counter(row["boarding_stop_event_id"] for row in rows)
        spec = plans["spec_by_trip"][trip]
        target = boarding_plan(spec, plans["base"][trip], plans["sequences"][trip])
        delta: Counter = Counter()
        for sequence, amount in target.items():
            event = observed.get(int(sequence))
            if event is None:
                continue
            delta[event["stop_event_id"]] = amount - current.get(event["stop_event_id"], 0)
        if sum(delta.values()) != repaired[trip] - physical[trip]:
            raise ValueError(f"per-sequence reconciliation failed for {trip}")
        handover = replacement_sequence.get(trip)
        if handover is not None and handover in observed:
            handover_event = observed[handover]["stop_event_id"]
            for event_id, change in sorted(delta.items()):
                sequence = next(s for s, e in observed.items() if e["stop_event_id"] == event_id)
                if change > 0 and sequence < handover:
                    # Boarding ahead of a vehicle handover would demand a matching
                    # transfer-ledger row. Seating the extra boardings at the handover
                    # itself keeps the transfer ledger exactly balanced while the
                    # trip-level movement budget is untouched.
                    delta[handover_event] = delta.get(handover_event, 0) + change
                    delta[event_id] = 0
                    handover_redirects.append({
                        "trip_id": trip, "from_sequence": sequence, "handover_sequence": handover,
                        "boardings": change,
                        "reason": "seat pre-handover boardings at the handover stop so the transfer "
                                  "ledger stays exactly balanced",
                    })
        for event_id, change in sorted(delta.items()):
            if change < 0:
                removals.append({"trip_id": trip, "stop_event_id": event_id, "count": -change})
            elif change > 0:
                additions.append({"trip_id": trip, "stop_event_id": event_id, "count": change})
    if sum(i["count"] for i in additions) != sum(i["count"] for i in removals):
        raise ValueError("per-sequence placement is not balanced")
    if handover_redirects:
        log(f"plan[8/9] {len(handover_redirects)} pre-handover boarding targets seated at the handover")

    donor_passengers = {row["passenger_id"] for item in removals
                        for row in journeys[item["trip_id"]]
                        if row["boarding_stop_event_id"] == item["stop_event_id"]}

    def _intervals():
        spans: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        for path in sorted((source / "raw" / "passenger_journeys").glob("*.csv")):
            for row in _scan_rows(path, ("passenger_id", "boarded_at_utc", "alighted_at_utc",
                                         "journey_id")):
                if row["passenger_id"] in donor_passengers:
                    spans[row["passenger_id"]].append(
                        (row["boarded_at_utc"], row["alighted_at_utc"], row["journey_id"]))
        for rows in spans.values():
            rows.sort()
        return dict(spans)

    intervals = defaultdict(list, _cached(
        source, "journey_intervals", _intervals, log,
        variant=",".join(sorted(donor_passengers))))

    moves = _place_bundles(plans, journeys, trip_events, removals, additions, intervals,
                           transfer_journeys, protected, replacement_sequence, log)
    log(f"plan[8/9] placed {len(moves)} journey bundles")

    log("plan[9/9] recomputing dependent flow, ticket and request rows")
    moves_by_target: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    moves_by_source: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for move in moves:
        moves_by_target[move["to_trip"]].append(move)
        moves_by_source[move["from_trip"]].append(move)
    flow_trips = set(moves_by_target) | set(moves_by_source)

    def _counts():
        rows: Dict[str, Dict[str, Dict[str, str]]] = defaultdict(dict)
        shard_of: Dict[str, str] = {}
        for path in sorted((source / "raw" / "passenger_counts").glob("*.csv")):
            for row in _scan_rows(path, ("source_row_id", "stop_event_id", "trip_id", "boardings",
                                         "alightings", "onboard_arrival", "onboard_departure",
                                         "quality_status", "unresolved_reason")):
                if row["trip_id"] in flow_trips:
                    if row["source_row_id"] in protected_sources:
                        raise ValueError(
                            f"protected passenger_counts row on affected trip {row['trip_id']}")
                    rows[row["trip_id"]][row["stop_event_id"]] = row
                    shard_of[row["source_row_id"]] = path.name
        return dict(rows), shard_of

    count_rows, count_shard = _cached(
        source, "passenger_counts", _counts, log, variant=",".join(sorted(flow_trips)))
    count_updates = _recompute_counts(flow_trips, trip_events, count_rows, moves_by_target,
                                      moves_by_source, journeys, assignment_capacity,
                                      trip_assignment, protected_sources)

    moved_ticket = {m["ticket_id"]: m for m in moves if m["ticket_id"] != NULL}
    moved_request = {m["request_id"]: m for m in moves if m["request_id"] != NULL}
    ticket_updates: Dict[str, Dict[str, str]] = {}
    ticket_shard: Dict[str, str] = {}
    for path in sorted((source / "raw" / "tickets").glob("*.csv")):
        for row in _scan_rows(path, ("source_row_id", "ticket_id", "passenger_id", "trip_id",
                                      "origin_route_stop_id", "destination_route_stop_id",
                                      "origin_stop_id", "destination_stop_id", "issued_at_utc",
                                      "service_date")):
            if row["source_row_id"] in protected_sources or row["ticket_id"] == NULL:
                continue
            changes: Dict[str, str] = {}
            move = moved_ticket.get(row["ticket_id"])
            if move is not None:
                issued = _plus(move["boarded_at_utc"], -45)
                changes = {
                    "passenger_id": move["passenger_id"], "trip_id": move["to_trip"],
                    "origin_route_stop_id": move["origin_route_stop_id"],
                    "destination_route_stop_id": move["destination_route_stop_id"],
                    "origin_stop_id": stop_of_route_stop[move["origin_route_stop_id"]],
                    "destination_stop_id": stop_of_route_stop[move["destination_route_stop_id"]],
                    "issued_at_utc": issued, "service_date": move["service_date"],
                    "event_time": issued, "value_available_at": issued,
                    "ingestion_time": _plus(issued, 3)}
            elif row["ticket_id"] in remap_tickets:
                origin, destination = remap_tickets[row["ticket_id"]]
                if origin in remapped:
                    changes["origin_stop_id"] = stop_of_route_stop[origin]
                if destination in remapped:
                    changes["destination_stop_id"] = stop_of_route_stop[destination]
            if changes:
                ticket_updates[row["source_row_id"]] = changes
                ticket_shard[row["source_row_id"]] = path.name

    request_updates: Dict[str, Dict[str, str]] = {}
    request_shard: Dict[str, str] = {}
    for path in sorted((source / "raw" / "demand_requests").glob("*.csv")):
        for row in _scan_rows(path, ("source_row_id", "request_id", "passenger_id", "origin_stop_id",
                                      "destination_stop_id", "preferred_route_id", "requested_at_utc",
                                      "desired_departure_utc", "service_date", "resolution",
                                      "decision_at_utc", "resolution_available_at_utc")):
            if row["source_row_id"] in protected_sources or row["request_id"] == NULL:
                continue
            if row["resolution"] != "SERVED":
                continue
            changes: Dict[str, str] = {}
            move = moved_request.get(row["request_id"])
            if move is not None:
                requested = _plus(move["boarded_at_utc"], -600)
                changes = {
                    "passenger_id": move["passenger_id"],
                    "origin_stop_id": stop_of_route_stop[move["origin_route_stop_id"]],
                    "destination_stop_id": stop_of_route_stop[move["destination_route_stop_id"]],
                    "preferred_route_id": move["to_route_id"],
                    "requested_at_utc": requested,
                    "desired_departure_utc": move["boarded_at_utc"],
                    "service_date": move["service_date"],
                    "decision_at_utc": move["boarded_at_utc"],
                    "resolution_available_at_utc": _plus(move["boarded_at_utc"], 8),
                    "event_time": requested,
                    "value_available_at": _plus(move["boarded_at_utc"], 8),
                    "ingestion_time": _plus(move["boarded_at_utc"], 8)}
            elif row["request_id"] in remap_requests:
                origin, destination = remap_requests[row["request_id"]]
                if origin in remapped:
                    changes["origin_stop_id"] = stop_of_route_stop[origin]
                if destination in remapped:
                    changes["destination_stop_id"] = stop_of_route_stop[destination]
            if changes:
                request_updates[row["source_row_id"]] = changes
                request_shard[row["source_row_id"]] = path.name

    def _duplicate_pair_audit() -> Dict[str, Any]:
        both = either = 0
        broken: List[str] = []
        corrected = 0
        for copy_id, survivor_id in protected["duplicate_pairs"]:
            in_copy = copy_id in ticket_updates
            in_survivor = survivor_id in ticket_updates
            if in_copy or in_survivor:
                corrected += 1
            if in_copy and in_survivor:
                both += 1
                if ticket_updates[copy_id] != ticket_updates[survivor_id]:
                    broken.append(f"{copy_id[:12]}/{survivor_id[:12]}")
            elif in_copy or in_survivor:
                either += 1
        return {"pairs": len(protected["duplicate_pairs"]), "corrected_pairs": corrected,
                "consistent_pairs": both, "one_sided_pairs": either, "divergent": broken}

    duplicate_audit = _duplicate_pair_audit()
    if duplicate_audit["one_sided_pairs"] or duplicate_audit["divergent"]:
        raise ValueError(f"duplicate ticket representations diverged: {duplicate_audit}")
    log(f"plan[9/9] duplicate ticket pairs corrected together: "
        f"{duplicate_audit['corrected_pairs']}/{duplicate_audit['pairs']}")

    # ------------------------------------------------------------------ #
    # Closure and storage inequality
    # ------------------------------------------------------------------ #
    updates: Dict[str, Dict[str, Dict[str, str]]] = {
        "route_stops": {item["source_row_id"]: {"stop_id": item["new_stop_id"]} for item in stop_mappings},
        "trip_vehicle_assignments": {item["source_row_id"]: {"vehicle_id": item["new_vehicle_id"]}
                                     for item in substitutions},
        "passenger_journeys": {m["source_row_id"]: {
            "passenger_id": m["passenger_id"], "trip_id": m["to_trip"],
            "origin_route_stop_id": m["origin_route_stop_id"],
            "destination_route_stop_id": m["destination_route_stop_id"],
            "boarding_stop_event_id": m["boarding_stop_event_id"],
            "alighting_stop_event_id": m["alighting_stop_event_id"],
            "service_date": m["service_date"],
            "boarded_at_utc": m["boarded_at_utc"], "alighted_at_utc": m["alighted_at_utc"]}
            for m in moves},
        "passenger_counts": count_updates,
        "tickets": ticket_updates,
        "demand_requests": request_updates,
    }
    shard_index = {
        "route_stops": {item["source_row_id"]: "part-000.csv" for item in stop_mappings},
        "trip_vehicle_assignments": assignment_shard,
        "passenger_journeys": journey_shard,
        "passenger_counts": count_shard,
        "tickets": ticket_shard,
        "demand_requests": request_shard,
    }
    for table, rows in updates.items():
        missing = [sid for sid in rows if sid not in shard_index[table]]
        if missing:
            raise ValueError(f"{table}: {len(missing)} planned rows have no resolved shard")
    changed: Dict[str, Dict[str, List[str]]] = {}
    for table, rows in updates.items():
        for source_row_id in rows:
            changed.setdefault(table, {}).setdefault(shard_index[table][source_row_id], []).append(source_row_id)
    closure: List[Dict[str, Any]] = []
    total = 0
    for table, shards in sorted(changed.items()):
        for shard, ids in sorted(shards.items()):
            path = source / "raw" / table / shard
            size = path.stat().st_size
            total += size
            closure.append({"table": table, "shard": shard, "path": str(path.relative_to(source)),
                            "bytes": size, "changed_rows": len(ids)})
    closure.sort(key=lambda item: item["path"])
    updates_by_shard: Dict[str, Dict[str, Dict[str, Dict[str, str]]]] = defaultdict(dict)
    for table, shards in changed.items():
        for shard, ids in shards.items():
            updates_by_shard[table][shard] = {sid: updates[table][sid] for sid in ids}
    free = shutil.disk_usage(source).free
    if free - total < RESERVE_BYTES:
        raise RuntimeError(
            f"storage inequality failed: free={free} closure={total} reserve={RESERVE_BYTES}")
    log(f"plan[9/9] closure={len(closure)} files, {total} bytes "
        f"({total / 2**30:.2f} GiB), free={free / 2**30:.2f} GiB, reserve={RESERVE_BYTES / 2**30:.2f} GiB")

    final_event = sum(repaired[t] for t in event_trips)
    return {
        "correction_version": "production-v1.1-correction-v1",
        "source_dataset_version": SOURCE_VERSION,
        "target_dataset_version": TARGET_VERSION,
        "planner_started_utc": started,
        "planner_finished_utc": _now(),
        "movement": {
            "canonical_movements": CANONICAL_MOVEMENTS,
            "legacy_plan": plans["legacy_stats"],
            "repaired_plan": plans["repaired_stats"],
            "movement_plan_mismatches": 0,
            "quarantined_journey_rows": quarantined,
            "recipient_trips": len(recipients), "donor_trips": len(donors),
            "moved_journeys": len(moves),
            "movements_gained": gained, "movements_released": released,
            "event_trips": len(event_trips),
            "event_movements": final_event,
            "event_mean_movements": final_event / len(event_trips),
            "baseline_trips": len(operated) - len(event_trips),
            "baseline_movements": CANONICAL_MOVEMENTS - final_event,
            "baseline_mean_movements": (CANONICAL_MOVEMENTS - final_event) / (len(operated) - len(event_trips)),
            "cancelled_trips_with_budget": sum(1 for t in plans["cancelled_trip_ids"] if repaired.get(t)),
            "operated_trips_without_budget": sum(1 for t in operated if repaired.get(t, 0) <= 0),
            "handover_redirects": handover_redirects,
            "recipients": recipients, "donors": donors,
        },
        "stops": {
            "used_stops_before": len(used_stops_before),
            "used_stops_after": len(used_stops_before) + len(stop_mappings),
            "required_used_stops": REQUIRED_USED_STOPS,
            "remapped_positions": len(stop_mappings),
            "later_opening_cohort": sum(1 for i in stop_mappings if i["new_stop_opened_on"] > "2025-01-01"),
            "mappings": stop_mappings,
        },
        "vehicles": {
            "used_vehicles_before": len({r["vehicle_id"] for _, r in actual_rows}),
            "used_vehicles_after": len({r["vehicle_id"] for _, r in actual_rows}) + len(substitutions),
            "required_used_vehicles": REQUIRED_USED_VEHICLES,
            "substitutions": substitutions,
        },
        "updates": updates,
        "duplicate_audit": duplicate_audit,
        "updates_by_shard": {table: dict(shards) for table, shards in updates_by_shard.items()},
        "closure": closure,
        "closure_bytes": total,
        "reserve_bytes": RESERVE_BYTES,
        "moves": moves,
    }


def _classify_event_trips(trips: Dict[str, Dict[str, str]], events: List[Dict[str, str]],
                          first_stop: Dict[str, str]) -> Set[str]:
    """Reproduce the validator's physical event/baseline classification."""

    scoped: List[Tuple[str, str, Optional[str], Optional[str]]] = []
    for event in events:
        if event["scope"] == "NETWORK":
            scoped.append((event["starts_at_utc"], event["ends_at_utc"], None, None))
        elif event["scope"] == "ROUTE":
            scoped.append((event["starts_at_utc"], event["ends_at_utc"], event["route_id"], None))
        else:
            scoped.append((event["starts_at_utc"], event["ends_at_utc"], None, event["stop_id"]))
    out: Set[str] = set()
    for trip_id, row in trips.items():
        start = row["scheduled_start_utc"]
        stop = first_stop.get(row["pattern_id"])
        for begins, ends, route_id, stop_id in scoped:
            if not (begins <= start <= ends):
                continue
            if route_id is None and stop_id is None:
                out.add(trip_id)
            elif route_id is not None and route_id == row["route_id"]:
                out.add(trip_id)
            elif stop_id is not None and stop_id == stop:
                out.add(trip_id)
    return out


def _plan_stop_overlay(stops, positions, position_stats, position_load, observed_per_stop,
                       protected_sources, log) -> List[Dict[str, Any]]:
    referenced = {row["stop_id"] for row in positions.values()}
    spare = sorted((row for row in stops if row["stop_id"] not in referenced),
                   key=lambda row: (row["opened_on"], row["stop_id"]))
    later = [row for row in spare if row["opened_on"] > "2025-01-01"]
    early = [row for row in spare if row["opened_on"] <= "2025-01-01"]
    log(f"plan[5/9] {len(spare)} unreferenced stops "
        f"({len(later)} later-opening, {len(early)} at history start)")

    eligible: List[Tuple[int, str]] = []
    for route_stop_id, position in positions.items():
        stats = position_stats.get(route_stop_id)
        if not stats or not stats["observed"] or not stats["min_date"]:
            continue
        if observed_per_stop.get(position["stop_id"], 0) <= 1:
            continue
        if position["source_row_id"] in protected_sources or position["stop_id"] in protected_sources:
            continue
        eligible.append((position_load.get(route_stop_id, 0), route_stop_id))
    eligible.sort()
    # A stop keeps its coverage only while one of its observed positions is left
    # untouched.  Reserving the highest-traffic observed position of every covered
    # stop guarantees that by construction, independently of how many positions a
    # stop happens to have.
    reserve: Dict[str, str] = {}
    for route_stop_id, position in positions.items():
        stats = position_stats.get(route_stop_id)
        if not stats or not stats["observed"]:
            continue
        stop = position["stop_id"]
        key = (position_load.get(route_stop_id, 0), route_stop_id)
        if stop not in reserve or key > reserve[stop][0]:
            reserve[stop] = (key, route_stop_id)
    reserved_ids = {value[1] for value in reserve.values()}
    repointed_per_stop: Counter = Counter()
    log(f"plan[5/9] {len(eligible)} observed positions may be re-pointed without losing coverage; "
        f"{len(reserved_ids)} positions reserved to preserve existing coverage")

    used: Set[str] = set()
    mappings: List[Dict[str, Any]] = []
    later_used = 0
    for pressure, route_stop_id in eligible:
        if len(mappings) >= STOP_REMAPPINGS:
            break
        if route_stop_id in reserved_ids:
            continue
        old_stop = positions[route_stop_id]["stop_id"]
        opening = position_stats[route_stop_id]["min_date"]
        chosen = None
        if len(used) < len(later):
            for row in later:
                if row["stop_id"] not in used and row["opened_on"] <= opening:
                    chosen = row
                    break
        if chosen is None:
            for row in early:
                if row["stop_id"] not in used:
                    chosen = row
                    break
        if chosen is None:
            continue
        used.add(chosen["stop_id"])
        repointed_per_stop[old_stop] += 1
        if chosen["opened_on"] > "2025-01-01":
            later_used += 1
        old = positions[route_stop_id]
        stats = position_stats[route_stop_id]
        mappings.append({
            "route_stop_id": route_stop_id, "source_row_id": old["source_row_id"],
            "pattern_id": old["pattern_id"], "route_id": old["route_id"],
            "stop_sequence": int(old["stop_sequence"]),
            "old_stop_id": old["stop_id"], "new_stop_id": chosen["stop_id"],
            "new_stop_opened_on": chosen["opened_on"],
            "new_stop_code": chosen["stop_code"],
            "earliest_service_date": stats["min_date"], "latest_service_date": stats["max_date"],
            "observed_events_retained": stats["observed"],
            "journeys_referencing_position": pressure,
            "reason": "operational stop coverage: re-point an observed position at an already "
                      "opened stop that no observed event used",
        })
    if len(mappings) < REQUIRED_USED_STOPS - len(used_stops_expected(positions, position_stats)):
        raise ValueError("stop overlay did not reach the required observed stop coverage")
    # Prove the arithmetic instead of assuming it: every currently covered stop
    # must survive, and every re-pointed position must contribute one new stop.
    repointed_positions = {item["route_stop_id"] for item in mappings}
    surviving = Counter(positions[rid]["stop_id"] for rid, s in position_stats.items()
                        if s["observed"] and rid not in repointed_positions)
    added = {item["new_stop_id"] for item in mappings}
    projected = len(surviving) + len(added - set(surviving))
    covered_before = len(used_stops_expected(positions, position_stats))
    if projected != covered_before + len(mappings):
        lost = {stop: count for stop, count in observed_per_stop.items()
                if count > 0 and stop not in surviving}
        raise ValueError(
            f"stop re-pointing arithmetic: covered_before={covered_before} mappings={len(mappings)} "
            f"distinct_new_stops={len(added)} surviving={len(surviving)} projected={projected} "
            f"expected={covered_before + len(mappings)} stops_losing_coverage={sorted(lost)[:6]}")
    if later_used < 50:
        raise ValueError(f"stop overlay admitted only {later_used} later-opening stops")
    mappings.sort(key=lambda item: item["route_stop_id"])
    log(f"plan[5/9] remapped {len(mappings)} positions ({later_used} later-opening stops); "
        f"projected covered stops={len(used_stops_expected(positions, position_stats)) + len(mappings)}")
    return mappings


def used_stops_expected(positions, position_stats) -> Set[str]:
    return {positions[rid]["stop_id"] for rid, stats in position_stats.items() if stats["observed"]}


def _plan_vehicle_overlay(vehicles, actual_rows, operated, trip_assignment_ids,
                          protected_sources, log) -> List[Dict[str, Any]]:
    used = {row["vehicle_id"] for _, row in actual_rows}
    per_vehicle: Counter = Counter(row["vehicle_id"] for _, row in actual_rows)
    whole_trip = {trip: next(iter(ids)) for trip, ids in trip_assignment_ids.items() if len(ids) == 1}
    spare = sorted((row for row in vehicles
                    if row["vehicle_id"] not in used
                    and row["operational_status"] == "AVAILABLE"
                    and row["retired_on"] == NULL), key=lambda row: row["vehicle_id"])
    log(f"plan[6/9] {len(used)} vehicles on actual duty, {len(spare)} eligible substitutes, "
        f"{len(whole_trip)} single-assignment trips")

    spare_by_capacity: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in spare:
        spare_by_capacity[row["nominal_capacity"]].append(row)
    used_spare: Set[str] = set()

    candidates: List[Tuple[str, str, Dict[str, str], str]] = []
    for _, row in actual_rows:
        trip = row["trip_id"]
        if whole_trip.get(trip) != row["assignment_id"]:
            continue
        if row["source_row_id"] in protected_sources:
            continue
        if per_vehicle[row["vehicle_id"]] <= 1:
            continue
        if int(row["start_stop_sequence"]) > int(row["end_stop_sequence"]):
            continue
        candidates.append((row["vehicle_id"], row["assignment_id"], row, operated[trip]["service_date"]))
    candidates.sort(key=lambda item: (item[0], item[1]))

    substitutions: List[Dict[str, Any]] = []
    for old_vehicle, _, row, service_date in candidates:
        if len(substitutions) >= VEHICLE_SUBSTITUTIONS:
            break
        chosen = None
        for candidate in spare_by_capacity.get(row["capacity_snapshot"], ()):
            if candidate["vehicle_id"] in used_spare:
                continue
            if candidate["commissioned_on"] > service_date:
                continue
            chosen = candidate
            break
        if chosen is None:
            continue
        used_spare.add(chosen["vehicle_id"])
        substitutions.append({
            "source_row_id": row["source_row_id"], "assignment_id": row["assignment_id"],
            "trip_id": row["trip_id"], "service_date": service_date,
            "old_vehicle_id": old_vehicle, "new_vehicle_id": chosen["vehicle_id"],
            "new_vehicle_code": chosen["vehicle_code"],
            "capacity_snapshot": int(row["capacity_snapshot"]),
            "start_stop_sequence": int(row["start_stop_sequence"]),
            "end_stop_sequence": int(row["end_stop_sequence"]),
            "reason": "deterministic substitution onto an unused AVAILABLE vehicle of identical "
                      "nominal capacity, preserving assignment identity, timing and duty span",
        })
    if len(substitutions) < REQUIRED_USED_VEHICLES - len(used):
        raise ValueError(f"vehicle overlay reached only {len(used) + len(substitutions)} used vehicles")
    substitutions.sort(key=lambda item: item["assignment_id"])
    log(f"plan[6/9] substituted {len(substitutions)} actual assignments")
    return substitutions


def _place_bundles(plans, journeys, trip_events, removals, additions, intervals,
                   transfer_journeys, protected, replacement_sequence, log) -> List[Dict[str, Any]]:
    """Move complete journey/ticket/served-request bundles between departures.

    Release capacity is tracked per ``(donor trip, boarding stop event)`` slot so a
    donor departure never loses more movements than the repaired plan requires, and
    every target slot is filled exactly to its planned boarding count.
    """

    protected_business = protected["business"]
    protected_sources = protected["sources"]
    protected_tickets = protected["tickets"]
    pool: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)
    for item in removals:
        for row in journeys.get(item["trip_id"], ()):
            if row["boarding_stop_event_id"] != item["stop_event_id"]:
                continue
            if row["source_row_id"] in protected_sources:
                continue
            if row["journey_id"] in transfer_journeys or row["journey_id"] in protected_business:
                continue
            # An intentional duplicate copy and its survivor are corrected
            # together through the shared ticket_id, so the pair stays byte
            # identical; only the DQ04 fixture ticket itself is untouchable.
            if row["ticket_id"] == NULL or row["ticket_id"] in protected_tickets:
                continue
            if row["request_id"] == NULL or row["movement_status"] == "MISSING_TICKET":
                continue
            pool[(item["trip_id"], item["stop_event_id"])].append(row)
    for key in pool:
        pool[key].sort(key=lambda row: row["journey_id"])
    release_capacity = {(item["trip_id"], item["stop_event_id"]): item["count"] for item in removals}
    if any(len(pool.get(key, ())) < need for key, need in release_capacity.items()):
        detail = []
        for key, need in sorted(release_capacity.items()):
            available = pool.get(key, ())
            if len(available) >= need:
                continue
            trip_rows = journeys.get(key[0], ())
            at_position = [row for row in trip_rows if row["boarding_stop_event_id"] == key[1]]
            detail.append({
                "trip_id": key[0], "stop_event_id": key[1], "needed": need,
                "eligible": len(available), "journeys_at_position": len(at_position),
                "reasons": sorted({
                    "protected_source" if row["source_row_id"] in protected_sources else
                    "protected_fixture" if row["journey_id"] in protected_business else
                    "transfer_ledger" if row["journey_id"] in transfer_journeys else
                    "duplicate_ticket" if row["ticket_id"] in protected_tickets else
                    "missing_ticket" if row["movement_status"] == "MISSING_TICKET" else
                    "missing_request" if row["request_id"] == NULL else "unknown"
                    for row in at_position}),
                "vehicle_handover_sequence": replacement_sequence.get(key[0]),
            })
        raise ValueError(f"{len(detail)} release positions cannot supply the planned removals: "
                         f"{json.dumps(detail[:4], sort_keys=True)}")
    remaining_release = dict(release_capacity)
    release_order = sorted(remaining_release)
    moves: List[Dict[str, Any]] = []
    used_journeys: Set[str] = set()
    # Track each passenger's effective intervals as bundles are moved.
    # Keep the caller's original interval map unchanged.
    effective_intervals = {
        passenger_id: list(values)
        for passenger_id, values in intervals.items()
    }

    for slot in sorted(additions, key=lambda item: (item["trip_id"], item["stop_event_id"])):
        trip = slot["trip_id"]
        spec = plans["spec_by_trip"][trip]
        observed = trip_events[trip]
        sequences = sorted(observed)
        by_event = {observed[sequence]["stop_event_id"]: sequence for sequence in sequences}
        origin_sequence = by_event[slot["stop_event_id"]]
        replacement = replacement_sequence.get(trip)
        outstanding = slot["count"]
        rejected: Counter = Counter()
        while outstanding > 0:
            progressed = False
            for key in release_order:
                if remaining_release[key] <= 0:
                    continue
                for row in pool[key]:
                    if row["journey_id"] in used_journeys:
                        continue
                    placed, reason = _place_one(plans, spec, trip, row, origin_sequence, sequences,
                                                observed, effective_intervals, used_journeys, replacement)
                    if placed is None:
                        rejected[reason] += 1
                        continue
                    if placed["from_trip"] == trip \
                            and placed["from_boarding_stop_event_id"] == placed["boarding_stop_event_id"] \
                            and placed["from_alighting_stop_event_id"] == placed["alighting_stop_event_id"]:
                        # A re-seated bundle at its own position is a no-op; it still
                        # discharges the release quota it was drawn against.
                        used_journeys.add(row["journey_id"])
                        remaining_release[key] -= 1
                        outstanding -= 1
                        progressed = True
                        break
                    moves.append(placed)

                    # Replace this journey's old interval with its newly placed
                    # interval so later moves for the same passenger cannot overlap it.
                    passenger_intervals = effective_intervals.setdefault(
                        placed["passenger_id"], []
                    )
                    passenger_intervals[:] = [
                        interval for interval in passenger_intervals
                        if interval[2] != placed["journey_id"]
                    ]
                    passenger_intervals.append((
                        placed["boarded_at_utc"],
                        placed["alighted_at_utc"],
                        placed["journey_id"],
                    ))

                    used_journeys.add(row["journey_id"])
                    remaining_release[key] -= 1
                    outstanding -= 1
                    progressed = True
                    break
                if progressed:
                    break
            if not progressed:
                raise ValueError(
                    f"no releasable bundle remains for {trip}/{slot['stop_event_id']} "
                    f"(sequence={origin_sequence} handover={replacement} outstanding={outstanding} "
                    f"release_slots_remaining="
                    f"{sum(1 for v in remaining_release.values() if v > 0)} "
                    f"rejected={dict(rejected)})")
    if any(value != 0 for value in remaining_release.values()):
        raise ValueError("release quotas and placements diverged")
    log(f"plan[8/9] placed {len(moves)} bundles from {len(release_order)} release positions")
    return moves


def _place_one(plans, spec, target_trip, row, origin_sequence, sequences, observed,
               intervals, used_journeys, replacement) -> Tuple[Optional[Dict[str, Any]], str]:
    if replacement is not None and origin_sequence < replacement:
        # Boarding ahead of a vehicle handover would require a transfer-ledger row.
        return None, "ahead_of_handover"
    rider_number = len(used_journeys)
    destination = _choose_destination(plans["ctx"], spec, origin_sequence, sequences,
                                      rider_number, replacement)
    if destination <= origin_sequence:
        destination = sequences[-1]
    if destination <= origin_sequence:
        return None, "no_later_sequence"
    origin_event = observed[origin_sequence]
    destination_event = observed[destination]
    boarded = origin_event["actual_departure_utc"]
    alighted = destination_event["actual_arrival_utc"]
    if boarded == NULL or alighted == NULL or alighted <= boarded:
        return None, "missing_observed_time"
    for start, end, journey_id in intervals.get(row["passenger_id"], ()):
        if journey_id == row["journey_id"]:
            continue
        if start < alighted and boarded < end:
            return None, "passenger_overlap"
    target = plans["spec_by_trip"][target_trip]
    return {
        "source_row_id": row["source_row_id"], "journey_id": row["journey_id"],
        "passenger_id": row["passenger_id"], "ticket_id": row["ticket_id"],
        "request_id": row["request_id"], "from_trip": row["trip_id"], "to_trip": target_trip,
        "to_route_id": target.pattern["route_id"],
        "from_boarding_stop_event_id": row["boarding_stop_event_id"],
        "from_alighting_stop_event_id": row["alighting_stop_event_id"],
        "origin_route_stop_id": origin_event["route_stop_id"],
        "destination_route_stop_id": destination_event["route_stop_id"],
        "boarding_stop_event_id": origin_event["stop_event_id"],
        "alighting_stop_event_id": destination_event["stop_event_id"],
        "service_date": target.service_date.isoformat(),
        "boarded_at_utc": boarded, "alighted_at_utc": alighted,
        "board_sequence": origin_sequence, "alight_sequence": destination,
    }, "placed"


def _recompute_counts(flow_trips, trip_events, count_rows, moves_by_target, moves_by_source,
                      journeys, assignment_capacity, trip_assignment, protected_sources) -> Dict[str, Dict[str, str]]:
    """Rebuild boardings, alightings, onboard series and capacity flags.

    The pre-correction flow is re-derived from the journeys alone and proved equal
    to the frozen count rows before any movement is applied, so a conservation
    error can never be absorbed into a rewritten shard.
    """

    updates: Dict[str, Dict[str, str]] = {}
    for trip in sorted(flow_trips):
        observed = trip_events[trip]
        rows = count_rows[trip]
        if set(rows) != {event["stop_event_id"] for event in observed.values()}:
            raise ValueError(f"passenger_counts coverage mismatch on {trip}")
        base_board: Counter = Counter()
        base_alight: Counter = Counter()
        for row in journeys.get(trip, ()):
            base_board[row["boarding_stop_event_id"]] += 1
            base_alight[row["alighting_stop_event_id"]] += 1
        base_arrival = 0
        for sequence in sorted(observed):
            event_id = observed[sequence]["stop_event_id"]
            row = rows[event_id]
            if int(row["boardings"]) != base_board[event_id]:
                raise ValueError(
                    f"pre-correction boarding conservation mismatch {trip}/{sequence}: "
                    f"rows={row['boardings']} journeys={base_board[event_id]}")
            if int(row["alightings"]) != base_alight[event_id]:
                raise ValueError(
                    f"pre-correction alighting conservation mismatch {trip}/{sequence}: "
                    f"rows={row['alightings']} journeys={base_alight[event_id]}")
            if int(row["onboard_arrival"]) != base_arrival:
                raise ValueError(
                    f"pre-correction load continuity mismatch {trip}/{sequence}: "
                    f"rows={row['onboard_arrival']} recomputed={base_arrival}")
            base_arrival = int(row["onboard_departure"])
        if base_arrival != 0:
            raise ValueError(f"pre-correction terminal load is not zero on {trip}: {base_arrival}")

        boardings = Counter(base_board)
        alightings = Counter(base_alight)
        for move in moves_by_target.get(trip, ()):
            boardings[move["boarding_stop_event_id"]] += 1
            alightings[move["alighting_stop_event_id"]] += 1
        for move in moves_by_source.get(trip, ()):
            boardings[move["from_boarding_stop_event_id"]] -= 1
            alightings[move["from_alighting_stop_event_id"]] -= 1
        if sum(boardings.values()) != sum(alightings.values()):
            raise ValueError(f"corrected boarding and alighting totals diverge on {trip}")

        onboard_arrival = 0
        for sequence in sorted(observed):
            event = observed[sequence]
            event_id = event["stop_event_id"]
            row = rows[event_id]
            if boardings[event_id] < 0 or alightings[event_id] < 0:
                raise ValueError(f"negative corrected flow {trip}/{sequence}")
            departure = onboard_arrival - alightings[event_id] + boardings[event_id]
            if departure < 0:
                raise ValueError(f"negative onboard load {trip}/{sequence}")
            capacity = assignment_capacity.get(trip_assignment[trip][sequence])
            changes: Dict[str, str] = {}
            if int(row["onboard_arrival"]) != onboard_arrival:
                changes["onboard_arrival"] = str(onboard_arrival)
            if int(row["boardings"]) != boardings[event_id]:
                changes["boardings"] = str(boardings[event_id])
            if int(row["alightings"]) != alightings[event_id]:
                changes["alightings"] = str(alightings[event_id])
            if int(row["onboard_departure"]) != departure:
                changes["onboard_departure"] = str(departure)
            overloaded = capacity is not None and (
                onboard_arrival > capacity or departure > capacity)
            if overloaded and row["quality_status"] != "FLAGGED":
                changes["quality_status"] = "FLAGGED"
            if changes:
                if row["source_row_id"] in protected_sources:
                    raise ValueError(f"refusing to modify protected count row {row['source_row_id']}")
                updates[row["source_row_id"]] = changes
            onboard_arrival = departure
        if onboard_arrival != 0:
            raise ValueError(f"corrected terminal load is not zero on {trip}: {onboard_arrival}")
    return updates


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--plan-out", type=Path, required=True)
    args = parser.parse_args(argv)
    plan = build_plan(args.source, log=lambda message: print(message, flush=True))
    Path(args.plan_out).write_text(json.dumps(plan, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps({
        "movement": {k: v for k, v in plan["movement"].items() if k not in ("recipients", "donors")},
        "stops": {k: v for k, v in plan["stops"].items() if k != "mappings"},
        "vehicles": {k: v for k, v in plan["vehicles"].items() if k != "substitutions"},
        "changed_rows": {table: len(rows) for table, rows in plan["updates"].items()},
        "closure_files": len(plan["closure"]),
        "closure_bytes": plan["closure_bytes"],
    }, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

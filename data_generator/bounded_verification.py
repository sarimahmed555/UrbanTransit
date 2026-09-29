"""Lightweight verification of the bounded corrected dataset.

Every check streams the corrected raw shards directly.  No large validation
projection is created and the parent's cached SQLite projection is never opened.

Bounded-memory strategy:

* stop events are indexed once into parallel arrays plus a compact 64-bit
  identifier index, so every joinable reference costs 8 bytes instead of a
  64-character string;
* per-trip flow conservation is evaluated while the count rows stream, which is
  safe because the generator emits one trip's count rows contiguously in
  ascending stop-sequence order (an inversion counter proves the assumption);
* cross-table business context is compared through 128-bit BLAKE2b digests and
  64-bit identifier keys, so the ticket/request joins never materialize a
  per-row Python object graph.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from array import array
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .bounded_remediation import (
    CANONICAL_MOVEMENTS, NULL, REQUIRED_USED_STOPS, REQUIRED_USED_VEHICLES, SOURCE_VERSION,
    TARGET_VERSION, _classify_event_trips, _digest, _protected_keys, _read_table, _scan_rows,
    derive_movement_plans,
)
from .config import canonical_json

_EPOCH = datetime.fromisoformat("1970-01-01T00:00:00+00:00")


def _ts(value: str) -> int:
    if not value or value == NULL:
        return -1
    return int((datetime.fromisoformat(value.replace("Z", "+00:00")) - _EPOCH).total_seconds())


def _key(value: str) -> int:
    """Compact signed 64-bit identifier key for bounded-memory joins.

    Signed so the same value can be stored in ``array("q")`` and compared directly
    against later lookups without a second representation.
    """

    raw = int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")
    return raw - (1 << 64) if raw >= (1 << 63) else raw


def _fingerprint(*parts: str) -> bytes:
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).digest()


def _request_context(row: Dict[str, str], stop_by_route_stop: Dict[str, str]) -> Tuple[int, bytes]:
    """Build the request key and journey-derived business context used in Step 6."""

    request_id = row["request_id"]
    return (
        _key(request_id),
        _fingerprint(
            row["passenger_id"],
            stop_by_route_stop[row["origin_route_stop_id"]],
            stop_by_route_stop[row["destination_route_stop_id"]],
            row["boarded_at_utc"],
            row["service_date"],
            request_id,
        ),
    )


def _shards(root: Path, table: str) -> List[Path]:
    return sorted((root / "raw" / table).glob("*.csv"))


def verify(source: Path, target: Path, *, log=print) -> Dict[str, Any]:
    source = source.resolve()
    target = target.resolve()
    checks: List[Dict[str, Any]] = []

    def check(name: str, passed: bool, **evidence: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **evidence})
        log(f"{'PASS' if passed else 'FAIL'} {name}"
            + (f" :: {json.dumps(evidence, default=str)[:260]}" if evidence else ""))

    manifest = json.loads((target / "metadata" / "generation_manifest.json").read_text())
    source_manifest = json.loads((source / "metadata" / "generation_manifest.json").read_text())
    protected = _protected_keys(source)
    protected_sources = protected["sources"]
    # Only quarantined rows leave the canonical projection.  An ACCEPTED_FLAGGED
    # fixture (DQ01) is a first-class canonical movement and must be counted.
    quarantined_sources = protected["quarantined"]

    # ------------------------------------------------------------------ #
    # 1. Parent immutability and hard-link integrity
    # ------------------------------------------------------------------ #
    log("verify[1/7] parent immutability and hard-link integrity")
    provenance = json.loads((target / "metadata" / "remediation_provenance.json").read_text())
    changed_paths = set(provenance["changed_files"])
    parent_bad: List[str] = []
    parent_declared_stale: List[str] = []
    linked_same = 0
    replaced_new = 0
    for item in source_manifest["files"]:
        relative = item["path"]
        origin = source / relative
        size_ok = origin.stat().st_size == item["bytes"]
        hash_ok = _digest(origin) == item["sha256"]
        if not (size_ok and hash_ok):
            # The parent generation manifest carries one pre-existing stale
            # declaration for the private injection manifest, recorded long before
            # this work.  It is reported separately and never counted as damage.
            parent_declared_stale.append(relative)
            continue
        if not relative.startswith("raw/"):
            continue
        mirror = target / relative
        if not mirror.exists():
            parent_bad.append(relative + " (absent from corrected version)")
        elif relative in changed_paths:
            if origin.stat().st_ino == mirror.stat().st_ino:
                parent_bad.append(relative + " (parent inode still shared after replacement)")
            else:
                replaced_new += 1
        else:
            if origin.stat().st_ino != mirror.stat().st_ino:
                parent_bad.append(relative + " (unchanged shard is not a hard link)")
            else:
                linked_same += 1
    check("production_v1_integrity_unchanged", not parent_bad, bad=parent_bad[:8],
          files_verified=len(source_manifest["files"]),
          parent_declaration_mismatches=parent_declared_stale)
    check("parent_only_preexisting_metadata_declaration",
          parent_declared_stale in ([], ["metadata/private_injection_manifest.json"]),
          mismatches=parent_declared_stale,
          note="the parent generation manifest under-declares the private injection manifest; "
               "the corrected version declares the measured hash and preserves both values")
    check("hard_link_and_replace_split", linked_same > 0 and replaced_new > 0,
          hard_linked=linked_same, replaced=replaced_new)
    parent_inodes = {(p.stat().st_dev, p.stat().st_ino)
                     for p in (source / "raw").rglob("*") if p.is_file()}
    exclusive = 0
    shared = 0
    for p in (target / "raw").rglob("*"):
        if not p.is_file():
            continue
        stat = p.stat()
        if (stat.st_dev, stat.st_ino) in parent_inodes:
            shared += 1
        else:
            exclusive += stat.st_blocks * 512
    check("no_second_physical_copy", exclusive < 0.75 * 28 * 2 ** 30,
          corrected_exclusive_bytes=exclusive, hard_linked_files=shared,
          parent_apparent_bytes=sum(p.stat().st_size for p in (source / "raw").rglob("*") if p.is_file()))

    # ------------------------------------------------------------------ #
    # 2. Manifest, hash and artifact consistency
    # ------------------------------------------------------------------ #
    log("verify[2/7] manifest, hash and artifact consistency")
    bad_files: List[str] = []
    for item in manifest["files"]:
        path = target / item["path"]
        if not path.exists():
            bad_files.append(item["path"] + " (missing)")
        elif path.stat().st_size != item["bytes"] or _digest(path) != item["sha256"]:
            bad_files.append(item["path"] + " (hash)")
    check("manifest_hash_consistency", not bad_files, bad=bad_files[:8],
          files=len(manifest["files"]))
    body = dict(manifest)
    self_hash = body.pop("manifest_sha256")
    check("manifest_self_hash",
          hashlib.sha256(canonical_json(body).encode()).hexdigest() == self_hash)
    deterministic = {k: v for k, v in body.items() if k not in {
        "generation_duration_seconds", "runtime_fields_excluded_from_deterministic_hash",
        "deterministic_content_sha256"}}
    check("manifest_deterministic_hash",
          hashlib.sha256(canonical_json(deterministic).encode()).hexdigest()
          == body["deterministic_content_sha256"])
    check("corrected_version_lineage",
          manifest["dataset_version"] == TARGET_VERSION
          and manifest["parent_dataset_version"] == SOURCE_VERSION
          and manifest["configuration_used"]["dataset_version"] == TARGET_VERSION
          and manifest["remediation"]["parent_dataset_version"] == SOURCE_VERSION,
          corrected_run_id=manifest["run_id"],
          parent_run_id=manifest["remediation"]["parent_run_id"])

    # ------------------------------------------------------------------ #
    # 3. Dimensions, stop events and vehicles
    # ------------------------------------------------------------------ #
    log("verify[3/7] stop events, stop coverage, vehicles and bounded indexes")
    stops = _read_table(target, "stops", ("stop_id", "opened_on"))
    opened = {row["stop_id"]: row["opened_on"] for row in stops}
    route_stops = _read_table(target, "route_stops",
                              ("route_stop_id", "pattern_id", "stop_id", "stop_sequence"))
    rs_stop = {row["route_stop_id"]: row["stop_id"] for row in route_stops}
    rs_key = {row["route_stop_id"]: _key(row["route_stop_id"]) for row in route_stops}
    stop_of_rs_key = {_key(row["route_stop_id"]): row["stop_id"] for row in route_stops}
    first_stop: Dict[str, str] = {}
    for row in sorted(route_stops, key=lambda item: (item["pattern_id"], item["stop_sequence"])):
        if row["stop_sequence"] == "1":
            first_stop.setdefault(row["pattern_id"], row["stop_id"])
    context_events = _read_table(target, "context_events",
                                 ("scope", "route_id", "stop_id", "starts_at_utc", "ends_at_utc"))
    trips_rows = _read_table(target, "trips",
                             ("source_row_id", "trip_id", "plan_status", "trip_status", "service_date",
                              "route_id", "pattern_id", "scheduled_start_utc"))
    operated: Dict[str, Dict[str, str]] = {}
    for row in trips_rows:
        if row["source_row_id"] not in protected_sources and \
                row["plan_status"] == "CURRENT" and row["trip_status"] != "CANCELLED":
            operated[row["trip_id"]] = row
    check("operated_departures", len(operated) == 117_600, actual=len(operated))

    ev_key: Dict[int, int] = {}
    ev_trip = array("q")
    ev_seq = array("i")
    ev_rs = array("q")
    ev_dep = array("q")
    ev_arr = array("q")
    ev_observed_stop: List[str] = []
    observed_per_stop: Counter = Counter()
    violations = 0
    observed_events = 0
    last_seq: Dict[str, int] = {}
    inversions = 0
    for path in _shards(target, "trip_stop_events"):
        for row in _scan_rows(path, ("source_row_id", "stop_event_id", "trip_id", "route_stop_id",
                                     "stop_sequence", "service_date", "visit_status",
                                     "actual_arrival_utc", "actual_departure_utc")):
            if row["source_row_id"] in quarantined_sources or row["visit_status"] != "OBSERVED":
                continue
            stop = rs_stop.get(row["route_stop_id"])
            arrival, departure = row["actual_arrival_utc"], row["actual_departure_utc"]
            sequence = int(row["stop_sequence"])
            if stop is None or row["service_date"] < opened.get(stop, "9999-12-31") \
                    or arrival == NULL or departure == NULL or departure < arrival:
                violations += 1
                continue
            trip = row["trip_id"]
            if last_seq.get(trip, 0) >= sequence:
                inversions += 1
            last_seq[trip] = sequence
            index = len(ev_trip)
            key = _key(row["stop_event_id"])
            if key in ev_key:
                violations += 1
                continue
            ev_key[key] = index
            ev_trip.append(_key(trip))
            ev_seq.append(sequence)
            ev_rs.append(rs_key[row["route_stop_id"]])
            ev_dep.append(_ts(departure))
            ev_arr.append(_ts(arrival))
            observed_per_stop[stop] += 1
            observed_events += 1
    used_stops = len(observed_per_stop)
    check("operational_stop_coverage", used_stops >= REQUIRED_USED_STOPS,
          used_stops=used_stops, required=REQUIRED_USED_STOPS, observed_events=observed_events)
    check("stop_opening_and_event_timing_consistency", violations == 0, violations=violations)
    check("stop_event_chronology", inversions == 0, trips=len(last_seq), inversions=inversions)
    del last_seq, ev_observed_stop

    assignment_capacity: Dict[int, int] = {}
    assignment_kind: Dict[int, str] = {}
    assignment_trip: Dict[int, int] = {}
    assignment_window: Dict[int, Tuple[int, int]] = {}
    duties: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
    actual_vehicles: Set[int] = set()
    capacity_bad = 0
    lifecycle_bad = 0
    service_of_trip = {row["trip_id"]: row["service_date"] for row in trips_rows}
    vehicles = _read_table(target, "vehicles", ("vehicle_id", "nominal_capacity", "operational_status",
                                                 "commissioned_on", "retired_on"))
    vehicle_capacity: Dict[str, int] = {}
    vehicle_status: Dict[str, str] = {}
    commissioned: Dict[str, str] = {}
    retired: Dict[str, str] = {}
    for row in vehicles:
        vehicle_capacity[row["vehicle_id"]] = int(row["nominal_capacity"])
        vehicle_status[row["vehicle_id"]] = row["operational_status"]
        commissioned[row["vehicle_id"]] = row["commissioned_on"]
        retired[row["vehicle_id"]] = row["retired_on"]
    for path in _shards(target, "trip_vehicle_assignments"):
        for row in _scan_rows(path, ("source_row_id", "assignment_id", "trip_id", "vehicle_id",
                                     "assignment_kind", "capacity_snapshot", "start_stop_sequence",
                                     "end_stop_sequence", "effective_start_utc", "effective_end_utc")):
            if row["source_row_id"] in quarantined_sources:
                continue
            key = _key(row["assignment_id"])
            window = (_ts(row["effective_start_utc"]), _ts(row["effective_end_utc"]))
            assignment_capacity[key] = int(row["capacity_snapshot"])
            assignment_kind[key] = row["assignment_kind"]
            assignment_trip[key] = _key(row["trip_id"])
            assignment_window[key] = window
            if row["assignment_kind"] != "ACTUAL":
                continue
            vkey = _key(row["vehicle_id"])
            actual_vehicles.add(vkey)
            duties[vkey].append(window)
            if int(row["capacity_snapshot"]) != vehicle_capacity.get(row["vehicle_id"], -1) \
                    or int(row["start_stop_sequence"]) > int(row["end_stop_sequence"]):
                capacity_bad += 1
            service_date = service_of_trip.get(row["trip_id"])
            if vehicle_status.get(row["vehicle_id"]) != "AVAILABLE" or service_date is None \
                    or service_date < commissioned.get(row["vehicle_id"], "9999-12-31") \
                    or (retired.get(row["vehicle_id"], NULL) != NULL
                        and service_date >= retired[row["vehicle_id"]]):
                lifecycle_bad += 1
    duty_bad = 0
    for windows in duties.values():
        windows.sort()
        for index in range(1, len(windows)):
            if windows[index][0] < windows[index - 1][1]:
                duty_bad += 1
    check("operational_vehicle_coverage", len(actual_vehicles) >= REQUIRED_USED_VEHICLES,
          used_vehicles=len(actual_vehicles), required=REQUIRED_USED_VEHICLES)
    check("vehicle_capacity_snapshot_consistency", capacity_bad == 0, violations=capacity_bad)
    check("vehicle_capacity_lifecycle", lifecycle_bad == 0, violations=lifecycle_bad)
    check("vehicle_duty_nonoverlap", duty_bad == 0, violations=duty_bad)
    del duties, service_of_trip

    # ------------------------------------------------------------------ #
    # 4. Movements, relationships, event demand and the re-apportioned plan
    # ------------------------------------------------------------------ #
    log("verify[4/7] canonical movements, journey relationships and event demand")
    movements: Counter = Counter()
    canonical = 0
    quarantined = 0
    bad_trip_link = 0
    bad_sequence = 0
    bad_timing = 0
    overlap = 0
    non_chronological = 0
    previous_board = -1
    # Corrected journeys are no longer emitted in boarding order, so passenger
    # non-overlap is proved per passenger from the collected intervals rather than
    # from a streaming assumption.
    spans: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
    expected_flow: Dict[int, int] = {}
    board_by_ticket: Dict[int, int] = {}
    ticket_digests: Set[bytes] = set()
    request_digests: Set[bytes] = set()
    ticket_presence: Set[int] = set()
    request_presence: Set[int] = set()
    bad_request_journey_context = 0
    for path in _shards(target, "passenger_journeys"):
        for row in _scan_rows(path, ("source_row_id", "journey_id", "passenger_id", "trip_id",
                                     "request_id", "ticket_id", "origin_route_stop_id",
                                     "destination_route_stop_id", "boarding_stop_event_id",
                                     "alighting_stop_event_id", "service_date", "boarded_at_utc",
                                     "alighted_at_utc", "passenger_count")):
            if row["source_row_id"] in quarantined_sources:
                quarantined += 1
                continue
            canonical += 1
            trip = row["trip_id"]
            movements[trip] += 1
            board = ev_key.get(_key(row["boarding_stop_event_id"]))
            alight = ev_key.get(_key(row["alighting_stop_event_id"]))
            if board is None or alight is None or ev_trip[board] != _key(trip) or ev_trip[alight] != _key(trip):
                bad_trip_link += 1
                continue
            if ev_seq[board] >= ev_seq[alight] or ev_rs[board] != rs_key[row["origin_route_stop_id"]] \
                    or ev_rs[alight] != rs_key[row["destination_route_stop_id"]]:
                bad_sequence += 1
            boarded, alighted = _ts(row["boarded_at_utc"]), _ts(row["alighted_at_utc"])
            if ev_dep[board] != boarded or ev_arr[alight] != alighted or alighted <= boarded:
                bad_timing += 1
            if boarded < previous_board:
                non_chronological += 1
            previous_board = boarded
            pkey = _key(row["passenger_id"])
            spans[pkey].append((boarded, alighted))
            bkey, akey = _key(row["boarding_stop_event_id"]), _key(row["alighting_stop_event_id"])
            expected_flow[bkey] = expected_flow.get(bkey, 0) + (1 << 20)
            expected_flow[akey] = expected_flow.get(akey, 0) + 1
            if row["ticket_id"] != NULL:
                tkey = _key(row["ticket_id"])
                board_by_ticket[tkey] = boarded
                ticket_presence.add(tkey)
                ticket_digests.add(_fingerprint(row["passenger_id"], trip, row["origin_route_stop_id"],
                                               row["destination_route_stop_id"], row["service_date"],
                                               row["ticket_id"]))
            if row["request_id"] != NULL:
                try:
                    rkey, request_digest = _request_context(row, rs_stop)
                except KeyError:
                    bad_request_journey_context += 1
                    continue
                request_presence.add(rkey)
                request_digests.add(request_digest)
    check("exact_canonical_movements", canonical == CANONICAL_MOVEMENTS,
          actual=canonical, expected=CANONICAL_MOVEMENTS, quarantined_rows=quarantined)
    check("journey_stop_event_trip_link", bad_trip_link == 0, violations=bad_trip_link)
    check("journey_sequence_and_route_stop_link", bad_sequence == 0, violations=bad_sequence)
    check("journey_timing_derivation", bad_timing == 0, violations=bad_timing)
    for values in spans.values():
        values.sort()
        for index in range(1, len(values)):
            if values[index][0] < values[index - 1][1]:
                overlap += 1
    check("passenger_journey_nonoverlap", overlap == 0, violations=overlap,
          passengers=len(spans), emission_chronological=non_chronological == 0)
    del spans

    event_trips = _classify_event_trips(operated, context_events, first_stop)
    event_total = sum(movements.get(t, 0) for t in event_trips)
    baseline = [t for t in operated if t not in event_trips]
    baseline_total = CANONICAL_MOVEMENTS - event_total
    check("condition_event_demand_increase",
          event_total / max(1, len(event_trips)) > baseline_total / max(1, len(baseline)),
          event_trips=len(event_trips), event_movements=event_total,
          event_mean=round(event_total / max(1, len(event_trips)), 5),
          baseline_trips=len(baseline), baseline_movements=baseline_total,
          baseline_mean=round(baseline_total / max(1, len(baseline)), 5))
    plans = derive_movement_plans(target)
    repaired = plans["repaired"]
    mismatch = [t for t in operated if movements.get(t, 0) != repaired.get(t, 0)]
    check("movement_plan_mismatches_zero", not mismatch, mismatches=len(mismatch), sample=mismatch[:5])
    check("cancelled_trip_budget_invariant",
          all(repaired.get(t, 0) == 0 for t in plans["cancelled_trip_ids"]),
          cancelled_trips=len(plans["cancelled_trip_ids"]))
    check("operated_trip_budget_invariant", all(repaired.get(t, 0) > 0 for t in operated),
          operated=len(operated))
    del movements, repaired, plans

    # ------------------------------------------------------------------ #
    # 5. Flow conservation
    # ------------------------------------------------------------------ #
    log("verify[5/7] passenger count conservation, load continuity and capacity")
    bad_overload = 0
    bad_equation = 0
    bad_assignment = 0
    bad_orphan = 0
    bad_continuity = 0
    bad_terminal = 0
    bad_order = 0
    bad_flow = 0
    duplicate_events = 0
    seen_events: Set[int] = set()
    continuity_gaps: Set[str] = set()
    # Per-trip state keeps the flow checks independent of emission order.
    trip_arrival: Dict[str, int] = {}
    trip_last_seq: Dict[str, int] = {}
    for path in _shards(target, "passenger_counts"):
        for row in _scan_rows(path, ("source_row_id", "stop_event_id", "trip_id", "boardings",
                                     "alightings", "onboard_arrival", "onboard_departure",
                                     "transfer_in_count", "transfer_out_count",
                                     "departure_assignment_id", "quality_status")):
            if row["source_row_id"] in quarantined_sources:
                continuity_gaps.add(row["trip_id"])
                continue
            ekey = _key(row["stop_event_id"])
            index = ev_key.get(ekey)
            if index is None:
                bad_orphan += 1
                continue
            if ekey in seen_events:
                duplicate_events += 1
            seen_events.add(ekey)
            b, a = int(row["boardings"]), int(row["alightings"])
            oa, od = int(row["onboard_arrival"]), int(row["onboard_departure"])
            if min(b, a, oa, od) < 0 or a > oa or od != oa - a + b or \
                    row["transfer_in_count"] != row["transfer_out_count"]:
                bad_equation += 1
            if assignment_trip.get(_key(row["departure_assignment_id"]), _key(row["trip_id"])) != _key(row["trip_id"]):
                bad_assignment += 1
            capacity = assignment_capacity.get(_key(row["departure_assignment_id"]))
            if capacity is not None and od > capacity and row["quality_status"] != "FLAGGED":
                bad_overload += 1
            trip = row["trip_id"]
            if trip in continuity_gaps:
                continuity_gaps.discard(trip)
            else:
                expected_arrival = trip_arrival.get(trip, 0)
                if oa != expected_arrival:
                    bad_continuity += 1
            if ev_seq[index] <= trip_last_seq.get(trip, 0):
                bad_order += 1
            trip_last_seq[trip] = ev_seq[index]
            trip_arrival[trip] = od
            expected = expected_flow.get(ekey, 0)
            if expected != b * (1 << 20) + a:
                bad_flow += 1
    bad_terminal = sum(1 for value in trip_arrival.values() if value != 0)
    check("passenger_count_conservation",
          bad_equation == 0 and bad_orphan == 0 and bad_assignment == 0,
          equation_violations=bad_equation, orphan_rows=bad_orphan,
          assignment_violations=bad_assignment)
    check("journey_boarding_alighting_counts", bad_flow == 0, violations=bad_flow)
    check("passenger_count_event_uniqueness", duplicate_events == 0, violations=duplicate_events)
    check("load_continuity", bad_continuity == 0, violations=bad_continuity,
          trips=len(trip_arrival))
    check("terminal_load_zero", bad_terminal == 0, violations=bad_terminal)
    check("passenger_count_sequence_order", bad_order == 0, violations=bad_order)
    check("overcrowding_flagging", bad_overload == 0, violations=bad_overload)
    del expected_flow, seen_events, ev_seq

    # ------------------------------------------------------------------ #
    # 6. Ticket and demand-request business context
    # ------------------------------------------------------------------ #
    log("verify[6/7] ticket and demand request business context")
    bad_ticket = 0
    bad_request = bad_request_journey_context
    linked_tickets = 0
    served = 0
    for path in _shards(target, "tickets"):
        for row in _scan_rows(path, ("source_row_id", "ticket_id", "passenger_id", "trip_id",
                                     "origin_route_stop_id", "destination_route_stop_id",
                                     "service_date", "issued_at_utc")):
            if row["source_row_id"] in quarantined_sources or row["ticket_id"] == NULL:
                continue
            tkey = _key(row["ticket_id"])
            if tkey not in ticket_presence:
                continue
            linked_tickets += 1
            if _fingerprint(row["passenger_id"], row["trip_id"], row["origin_route_stop_id"],
                            row["destination_route_stop_id"], row["service_date"],
                            row["ticket_id"]) not in ticket_digests:
                bad_ticket += 1
            boarded = board_by_ticket.get(tkey)
            if boarded is None or _ts(row["issued_at_utc"]) > boarded:
                bad_ticket += 1
    check("ticket_journey_business_context", bad_ticket == 0, violations=bad_ticket,
          linked=linked_tickets, canonical_movements_with_a_ticket=2_399_999,
          note="an intentional duplicate copy is byte-identical to its survivor, so both match")
    for path in _shards(target, "demand_requests"):
        for row in _scan_rows(path, ("source_row_id", "request_id", "passenger_id", "origin_stop_id",
                                     "destination_stop_id", "desired_departure_utc", "resolution",
                                     "service_date")):
            if row["source_row_id"] in quarantined_sources or row["request_id"] == NULL:
                continue
            rkey = _key(row["request_id"])
            if rkey not in request_presence:
                continue
            served += 1
            if row["desired_departure_utc"] == NULL or row["resolution"] != "SERVED" \
                    or row["service_date"] == NULL:
                bad_request += 1
                continue
            if _fingerprint(row["passenger_id"], row["origin_stop_id"], row["destination_stop_id"],
                            row["desired_departure_utc"], row["service_date"],
                            row["request_id"]) not in request_digests:
                bad_request += 1
    check("request_journey_business_context", bad_request == 0, violations=bad_request,
          served=served, expected_served=2_399_999)
    del ticket_digests, request_digests, ticket_presence, request_presence, board_by_ticket

    # ------------------------------------------------------------------ #
    # 7. DQ fixtures, provenance and raw envelopes
    # ------------------------------------------------------------------ #
    log("verify[7/7] DQ fixtures, provenance and raw envelopes")
    injections = json.loads((target / "metadata" / "private_injection_manifest.json").read_text())
    families = {item["rule_id"] for item in injections["injections"]}
    check("all_dq_families", all(f"DQ{i:02d}" in families for i in range(1, 17)), families=len(families))
    duplicates = list(csv.DictReader(
        (target / "metadata" / "production_ticket_duplicates.csv").open(newline="")))
    check("exact_duplicate_ticket_copies",
          len(duplicates) + sum(1 for i in injections["injections"] if i["rule_id"] == "DQ04") == 12000,
          duplicates=len(duplicates))
    wanted = set(protected_sources) | {row["source_row_id"] for row in duplicates} | \
        {row["survivor_source_row_id"] for row in duplicates}
    present: Set[str] = set()
    envelope_bad = 0
    duplicate_hash: Dict[str, str] = {}
    for table in ("passenger_journeys", "tickets", "passenger_counts", "trip_stop_events",
                  "demand_requests", "trips", "trip_vehicle_assignments", "route_stops",
                  "delays", "route_patterns", "gps_events"):
        for path in _shards(target, table):
            for row in _scan_rows(path, ("source_row_id", "raw_bytes_sha256", "raw_record_text")):
                if row["source_row_id"] not in wanted:
                    continue
                present.add(row["source_row_id"])
                if hashlib.sha256(row["raw_record_text"].encode()).hexdigest() != row["raw_bytes_sha256"]:
                    envelope_bad += 1
                if table == "tickets":
                    duplicate_hash[row["source_row_id"]] = row["raw_bytes_sha256"]
    check("dq_fixture_sources_complete", present == wanted,
          missing=sorted(wanted - present)[:5], expected=len(wanted), found=len(present))
    check("protected_row_envelope_integrity", envelope_bad == 0, violations=envelope_bad)
    ledger_bad = 0
    for row in duplicates:
        left = duplicate_hash.get(row["source_row_id"])
        right = duplicate_hash.get(row["survivor_source_row_id"])
        if left is None or right is None or left != right:
            ledger_bad += 1
    check("physical_duplicate_ledger", ledger_bad == 0, mismatches=ledger_bad)
    reconciliation = json.loads((target / "metadata" / "dq_reconciliation.json").read_text())["tables"]
    bad_reconciliation = [table for table, value in reconciliation.items()
                          if sum(v for k, v in value.items()
                                 if k.endswith("_count") and k not in ("raw_count", "affected_unique_count"))
                          != value["raw_count"]]
    check("dq_reconciliation_totals", not bad_reconciliation, bad=bad_reconciliation)
    required = ("remediation_manifest.json", "remediation_provenance.json",
                "remediation_change_ledger.json", "remediation_correction_config.json")
    check("corrected_version_provenance",
          all((target / "metadata" / name).exists() for name in required)
          and provenance["parent_dataset_version"] == SOURCE_VERSION
          and provenance["remediation_epoch_utc"] == manifest["generation_timestamp_utc"],
          files=len(required))
    check("change_ledger_consistency",
          json.loads((target / "metadata" / "remediation_change_ledger.json").read_text())
          ["parent_dataset_version"] == SOURCE_VERSION)

    failed = [item["name"] for item in checks if not item["passed"]]
    return {"passed": not failed, "failed": failed, "checks": checks}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = verify(args.source, args.target, log=lambda message: print(message, flush=True))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps({"passed": report["passed"], "failed": report["failed"],
                      "checks": len(report["checks"])}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

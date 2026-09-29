"""Passenger-flow conservation, replacement, and capacity checks."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .common import ValidationReport, as_float, as_int, clean_rows, index_rows, load_injections, load_table, parse_ts


def _bool(value) -> bool:
    return str(value).lower() == "true"


def run(root: Path, report: ValidationReport) -> None:
    _, injection_sources = load_injections(root)
    counts = clean_rows(load_table(root, "passenger_counts"), "passenger_counts", injection_sources)
    events = clean_rows(load_table(root, "trip_stop_events"), "trip_stop_events", injection_sources)
    assignments = clean_rows(load_table(root, "trip_vehicle_assignments"), "trip_vehicle_assignments", injection_sources)
    journeys = clean_rows(load_table(root, "passenger_journeys"), "passenger_journeys", injection_sources)
    transfers = clean_rows(load_table(root, "passenger_transfer_events"), "passenger_transfer_events", injection_sources)
    event_by_id = index_rows(events, "stop_event_id")
    assignment_by_id = index_rows(assignments, "assignment_id")
    events_by_trip: Dict[str, List[dict]] = defaultdict(list)
    for event in events:
        events_by_trip[event.get("trip_id")].append(event)
    transfer_by_event: Counter[str] = Counter(row.get("replacement_stop_event_id") for row in transfers)

    physical_failures = []
    conservation_failures = []
    boundary_failures = []
    transfer_failures = []
    capacity_failures = []
    count_journey_mismatches = []
    for row in counts:
        count_id = row.get("count_id")
        event = event_by_id.get(row.get("stop_event_id"), {})
        arrival = as_int(row.get("onboard_arrival"))
        departure = as_int(row.get("onboard_departure"))
        boardings = as_int(row.get("boardings"))
        alightings = as_int(row.get("alightings"))
        if any(value is None for value in (arrival, departure, boardings, alightings)) or min(arrival, departure, boardings, alightings) < 0 or alightings > arrival:
            physical_failures.append(count_id)
            continue
        if departure != arrival - alightings + boardings:
            conservation_failures.append(count_id)
        if not _bool(row.get("replacement_event")) and (as_int(row.get("transfer_out_count"), 0) != 0 or as_int(row.get("transfer_in_count"), 0) != 0):
            transfer_failures.append(count_id)
        if _bool(row.get("replacement_event")):
            if as_int(row.get("transfer_out_count"), -1) != as_int(row.get("transfer_in_count"), -2) or as_int(row.get("transfer_out_count"), 0) < 0:
                transfer_failures.append(count_id)
            if transfer_by_event[row.get("stop_event_id")] != as_int(row.get("transfer_out_count"), -1):
                transfer_failures.append(count_id)
        trip_events = sorted(events_by_trip[row.get("trip_id")], key=lambda item: as_int(item.get("stop_sequence"), 0) or 0)
        observed = [item for item in trip_events if item.get("visit_status") == "OBSERVED"]
        if observed:
            origin_sequence = min(as_int(item.get("stop_sequence"), 0) or 0 for item in observed)
            terminal_sequence = max(as_int(item.get("stop_sequence"), 0) or 0 for item in observed)
            sequence = as_int(event.get("stop_sequence"))
            if sequence == origin_sequence and (arrival != 0 or alightings != 0 or as_int(row.get("transfer_in_count"), 0) != 0):
                boundary_failures.append(count_id)
            if sequence == terminal_sequence and (boardings != 0 or as_int(row.get("transfer_out_count"), 0) != 0 or departure != 0):
                boundary_failures.append(count_id)
        # Journey event IDs are reconciled below through the stop-event map.
        arrival_assignment = row.get("arrival_assignment_id")
        departure_assignment = row.get("departure_assignment_id")
        if departure_assignment and departure_assignment in assignment_by_id:
            capacity = as_int(assignment_by_id[departure_assignment].get("capacity_snapshot"), 0) or 0
            if departure > capacity and row.get("quality_status") != "FLAGGED":
                capacity_failures.append(count_id)
        if departure_assignment is None and row.get("departure_assignment_status") not in {"UNKNOWN", "NOT_APPLICABLE"}:
            capacity_failures.append(count_id)
    report.add("passenger_count_physical_constraints", not physical_failures, "counts are nonnegative and alightings do not exceed arrival", bad=physical_failures[:10])
    report.add("passenger_count_conservation", not conservation_failures, "onboard_departure = onboard_arrival - alightings + boardings", bad=conservation_failures[:10])
    report.add("passenger_count_boundaries", not boundary_failures, "origin and terminal count semantics are explicit", bad=boundary_failures[:10])
    report.add("replacement_transfer_counters", not transfer_failures, "replacement counters reconcile to the transfer ledger", bad=transfer_failures[:10])
    report.add("capacity_flags_and_unknown_states", not capacity_failures, "real overload is flagged and unknown assignment is explicit", bad=capacity_failures[:10])

    # Reconcile ordinary boardings/alightings to journey endpoint evidence.
    journey_counts: Dict[Tuple[str, int], Dict[str, int]] = defaultdict(lambda: {"boardings": 0, "alightings": 0})
    sequence_by_event = {event.get("stop_event_id"): as_int(event.get("stop_sequence")) for event in events}
    trip_by_event = {event.get("stop_event_id"): event.get("trip_id") for event in events}
    for journey in journeys:
        boarding = journey.get("boarding_stop_event_id"); alighting = journey.get("alighting_stop_event_id")
        if boarding in sequence_by_event:
            key = (trip_by_event[boarding], sequence_by_event[boarding])
            journey_counts[key]["boardings"] += 1
        if alighting in sequence_by_event:
            key = (trip_by_event[alighting], sequence_by_event[alighting])
            journey_counts[key]["alightings"] += 1
    for row in counts:
        event = event_by_id.get(row.get("stop_event_id"), {})
        key = (row.get("trip_id"), as_int(event.get("stop_sequence")))
        expected = journey_counts.get(key, {"boardings": 0, "alightings": 0})
        if (as_int(row.get("boardings"), -1), as_int(row.get("alightings"), -1)) != (expected["boardings"], expected["alightings"]):
            count_journey_mismatches.append(row.get("count_id"))
    report.add("counts_reconcile_to_journeys", not count_journey_mismatches, "ordinary count movements reconcile to passenger journeys", bad=count_journey_mismatches[:10])

    unknown_rows = [row for row in counts if row.get("departure_assignment_status") == "UNKNOWN"]
    report.add("unknown_vehicle_partial_usability", bool(unknown_rows) and all(row.get("quality_status") == "FLAGGED" and row.get("unresolved_reason") == "UNKNOWN_VEHICLE" for row in unknown_rows), "unknown vehicle retains counts while withholding capacity attribution", rows=len(unknown_rows))
    by_passenger = {}
    overlap_rows = []
    for journey in journeys:
        by_passenger.setdefault(journey.get("passenger_id"), []).append(journey)
    for passenger_id, rows in by_passenger.items():
        rows.sort(key=lambda item: parse_ts(item.get("boarded_at_utc")) or datetime.min.replace(tzinfo=timezone.utc))
        for left, right in zip(rows, rows[1:]):
            left_end = parse_ts(left.get("alighted_at_utc")); right_start = parse_ts(right.get("boarded_at_utc"))
            if left_end and right_start and right_start < left_end:
                overlap_rows.append((passenger_id, left.get("journey_id"), right.get("journey_id")))
    report.add("passenger_journeys_nonoverlap", not overlap_rows, "a passenger has no overlapping journey intervals", bad=overlap_rows[:10])
    overload_rows = [row for row in counts if row.get("quality_status") == "FLAGGED" and row.get("unresolved_reason") is None and (as_int(row.get("onboard_departure"), 0) or 0) > 0]
    report.add("real_overload_is_not_clipped", any((as_int(row.get("onboard_departure"), 0) or 0) > (as_int((assignment_by_id.get(row.get("departure_assignment_id"), {}) or {}).get("capacity_snapshot"), 10**9) or 0) for row in overload_rows), "at least one genuine overload remains observable", flagged_rows=len(overload_rows))

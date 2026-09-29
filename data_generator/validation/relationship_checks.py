"""Relational integrity checks for valid (non-injected) raw records."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import math
from pathlib import Path
from typing import Dict, List, Tuple

from .common import (ValidationReport, as_date, as_float, as_int, clean_rows, index_rows, injected,
                     load_injections, load_table, parse_ts)


def run(root: Path, report: ValidationReport) -> None:
    _, injection_sources = load_injections(root)
    rows = {table: clean_rows(load_table(root, table), table, injection_sources) for table in (
        "passengers", "routes", "stops", "route_patterns", "route_stops", "service_calendar",
        "service_exceptions", "schedules", "schedule_stop_times", "vehicles", "trips",
        "trip_vehicle_assignments", "trip_stop_events", "passenger_counts", "delays", "gps_events",
        "tickets", "passenger_journeys", "demand_requests", "context_events", "passenger_transfer_events",
    )}

    # Dimension PKs and route-stop ordering.
    pk_fields = {
        "passengers": "passenger_id", "routes": "route_id", "stops": "stop_id", "route_patterns": "pattern_id",
        "route_stops": "route_stop_id", "service_calendar": "service_id", "service_exceptions": "exception_id",
        "schedules": "schedule_id", "schedule_stop_times": "schedule_stop_time_id", "vehicles": "vehicle_id",
        "trips": "trip_id", "trip_vehicle_assignments": "assignment_id", "trip_stop_events": "stop_event_id",
        "passenger_counts": "count_id", "delays": "delay_id", "gps_events": "gps_event_id", "tickets": "ticket_id",
        "passenger_journeys": "journey_id", "demand_requests": "request_id", "context_events": "context_event_id",
        "passenger_transfer_events": "transfer_id",
    }
    for table, field in pk_fields.items():
        values = [row.get(field) for row in rows[table] if row.get(field) is not None]
        report.add(f"pk_unique:{table}", len(values) == len(set(values)), "valid business PKs are unique", rows=len(values), unique=len(set(values)))

    route_by_id = index_rows(rows["routes"], "route_id")
    stop_by_id = index_rows(rows["stops"], "stop_id")
    pattern_by_id = index_rows(rows["route_patterns"], "pattern_id")
    calendar_by_id = index_rows(rows["service_calendar"], "service_id")
    schedule_by_id = index_rows(rows["schedules"], "schedule_id")
    route_stop_by_id = index_rows(rows["route_stops"], "route_stop_id")
    vehicle_by_id = index_rows(rows["vehicles"], "vehicle_id")
    trip_by_id = index_rows(rows["trips"], "trip_id")
    assignment_by_id = index_rows(rows["trip_vehicle_assignments"], "assignment_id")
    event_by_id = index_rows(rows["trip_stop_events"], "stop_event_id")

    # Route-stop parent and contiguous sequence checks.
    bad_route_stops = []
    grouped_rs: Dict[str, List[dict]] = defaultdict(list)
    for row in rows["route_stops"]:
        grouped_rs[row.get("pattern_id")].append(row)
        if row.get("route_id") != (pattern_by_id.get(row.get("pattern_id"), {}) or {}).get("route_id") or row.get("stop_id") not in stop_by_id:
            bad_route_stops.append(row.get("route_stop_id"))
    for pattern_id, group in grouped_rs.items():
        group = sorted(group, key=lambda item: as_int(item.get("stop_sequence"), 0) or 0)
        sequences = [as_int(item.get("stop_sequence")) for item in group]
        distances = [as_float(item.get("distance_from_start_km")) for item in group]
        if sequences != list(range(1, len(group) + 1)) or not distances or distances[0] != 0 or any(distances[index] <= distances[index - 1] for index in range(1, len(distances))):
            bad_route_stops.append(pattern_id)
    report.add("route_stop_integrity", not bad_route_stops, "patterns have valid parents and contiguous increasing sequences", bad=bad_route_stops[:10])
    bad_distance_geometry = []
    for pattern_id, group in grouped_rs.items():
        ordered = sorted(group, key=lambda item: as_int(item.get("stop_sequence"), 0) or 0)
        if len(ordered) < 2:
            continue
        first = stop_by_id.get(ordered[0].get("stop_id")); last = stop_by_id.get(ordered[-1].get("stop_id"))
        try:
            straight = math.sqrt(
                ((float(last["latitude"]) - float(first["latitude"])) * 111.0) ** 2
                + ((float(last["longitude"]) - float(first["longitude"])) * 104.0) ** 2
            )
            cumulative = float(ordered[-1].get("distance_from_start_km"))
            if cumulative + 0.05 < straight:
                bad_distance_geometry.append((pattern_id, cumulative, straight))
        except (TypeError, ValueError, KeyError):
            bad_distance_geometry.append(pattern_id)
    report.add("route_distance_geometry", not bad_distance_geometry, "cumulative route distance is not shorter than the straight-line endpoint bound", bad=bad_distance_geometry[:10])

    bad_patterns = []
    for row in rows["route_patterns"]:
        if row.get("route_id") not in route_by_id or as_date(row.get("valid_from")) is None or as_date(row.get("valid_to")) is not None and as_date(row.get("valid_to")) <= as_date(row.get("valid_from")):
            bad_patterns.append(row.get("pattern_id"))
    report.add("route_pattern_temporal", not bad_patterns, "pattern route and effective ranges are valid", bad=bad_patterns[:10])

    bad_schedules = []
    stop_times_by_schedule: Dict[str, List[dict]] = defaultdict(list)
    for row in rows["schedule_stop_times"]:
        stop_times_by_schedule[row.get("schedule_id")].append(row)
        if row.get("schedule_id") not in schedule_by_id or row.get("route_stop_id") not in route_stop_by_id:
            bad_schedules.append(row.get("schedule_stop_time_id"))
    for row in rows["schedules"]:
        if row.get("pattern_id") not in pattern_by_id or row.get("service_id") not in calendar_by_id:
            bad_schedules.append(row.get("schedule_id"))
    for schedule_id, group in stop_times_by_schedule.items():
        group = sorted(group, key=lambda item: as_int(item.get("stop_sequence"), 0) or 0)
        sequences = [as_int(item.get("stop_sequence")) for item in group]
        if sequences != list(range(1, len(group) + 1)):
            bad_schedules.append(schedule_id)
    report.add("schedule_relationships", not bad_schedules, "schedules and stop offsets reference valid immutable patterns", bad=bad_schedules[:10])

    # Trips, plan versions, and stable operational identity.
    bad_trips = []
    op_versions: Dict[str, List[dict]] = defaultdict(list)
    for row in rows["trips"]:
        op_versions[row.get("operational_departure_id")].append(row)
        pattern = pattern_by_id.get(row.get("pattern_id"), {})
        schedule = schedule_by_id.get(row.get("schedule_id"), {})
        temporal_compatible = True
        if row.get("plan_status") == "CURRENT":
            service_day = as_date(row.get("service_date"))
            pattern_start = as_date(pattern.get("valid_from")); pattern_end = as_date(pattern.get("valid_to")) if pattern.get("valid_to") else None
            schedule_start = as_date(schedule.get("valid_from")); schedule_end = as_date(schedule.get("valid_to")) if schedule.get("valid_to") else None
            if service_day and ((pattern_start and service_day < pattern_start) or (pattern_end and service_day >= pattern_end) or (schedule_start and service_day < schedule_start) or (schedule_end and service_day >= schedule_end)):
                temporal_compatible = False
        else:
            effective = parse_ts(row.get("effective_from"))
            pattern_published = parse_ts(pattern.get("published_at_utc"))
            schedule_published = parse_ts(schedule.get("published_at_utc"))
            if effective and ((pattern_published and effective < pattern_published) or (schedule_published and effective < schedule_published)):
                temporal_compatible = False
        if (row.get("route_id") not in route_by_id or row.get("route_id") != pattern.get("route_id") or
                row.get("pattern_id") not in pattern_by_id or row.get("schedule_id") not in schedule_by_id or
                row.get("service_id") not in calendar_by_id or schedule.get("pattern_id") != row.get("pattern_id") or
                schedule.get("service_id") != row.get("service_id") or not temporal_compatible or
                row.get("planned_vehicle_id") is not None and row.get("planned_vehicle_id") not in vehicle_by_id):
            bad_trips.append(row.get("trip_id"))
    for op_id, group in op_versions.items():
        group = sorted(group, key=lambda item: as_int(item.get("plan_version"), 0) or 0)
        versions = [as_int(row.get("plan_version")) for row in group]
        if versions != list(range(1, len(group) + 1)) or sum(row.get("plan_status") == "CURRENT" for row in group) != 1:
            bad_trips.append(op_id)
        for index in range(len(group) - 1):
            if group[index].get("effective_to") != group[index + 1].get("effective_from"):
                bad_trips.append(op_id)
                break
    report.add("trip_plan_version_integrity", not bad_trips, "trip FKs and plan-version intervals are valid", bad=bad_trips[:10])
    bad_calendar_service = []
    exception_keys = {(row.get("service_id"), row.get("exception_date")) for row in rows["service_exceptions"]}
    day_fields = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    for trip in rows["trips"]:
        calendar = calendar_by_id.get(trip.get("service_id"), {})
        service_day = as_date(trip.get("service_date"))
        if not service_day:
            continue
        day_name = day_fields[service_day.weekday()]
        allowed = bool(calendar.get(day_name)) or (trip.get("service_id"), service_day.isoformat()) in exception_keys
        if not allowed:
            bad_calendar_service.append(trip.get("trip_id"))
    report.add("calendar_date_compatibility", not bad_calendar_service, "current service dates are covered by their calendar or an explicit exception", bad=bad_calendar_service[:10])
    bad_opening_dates = []
    for trip in rows["trips"]:
        service_day = as_date(trip.get("service_date")); route = route_by_id.get(trip.get("route_id"), {})
        if service_day and route.get("opened_on") and service_day < as_date(route.get("opened_on")):
            bad_opening_dates.append(trip.get("trip_id"))
    for pattern in rows["route_patterns"]:
        valid_from = as_date(pattern.get("valid_from")); route = route_by_id.get(pattern.get("route_id"), {})
        if valid_from and route.get("opened_on") and valid_from < as_date(route.get("opened_on")):
            bad_opening_dates.append(pattern.get("pattern_id"))
    report.add("new_entity_opening_temporal", not bad_opening_dates, "new routes and patterns do not operate before opening", bad=bad_opening_dates[:10])

    # Vehicle assignment validity and actual duty overlap.
    bad_assignments = []
    actual_intervals: Dict[str, List[Tuple[object, object, str]]] = defaultdict(list)
    for row in rows["trip_vehicle_assignments"]:
        trip = trip_by_id.get(row.get("trip_id"), {})
        pattern = pattern_by_id.get(trip.get("pattern_id"), {})
        start_seq = as_int(row.get("start_stop_sequence"))
        end_seq = as_int(row.get("end_stop_sequence"))
        vehicle = vehicle_by_id.get(row.get("vehicle_id"), {})
        effective_start = parse_ts(row.get("effective_start_utc"))
        lifecycle_bad = False
        if row.get("assignment_kind") == "ACTUAL" and effective_start:
            service_day = effective_start.date().isoformat()
            lifecycle_bad = (vehicle.get("commissioned_on") and service_day < vehicle.get("commissioned_on")) or (vehicle.get("retired_on") and service_day >= vehicle.get("retired_on")) or vehicle.get("operational_status") == "MAINTENANCE"
        if (row.get("vehicle_id") not in vehicle_by_id or row.get("trip_id") not in trip_by_id or lifecycle_bad or
                start_seq is None or end_seq is None or start_seq < 1 or end_seq < start_seq or
                end_seq > len([rs for rs in grouped_rs.get(trip.get("pattern_id"), [])]) or
                as_int(row.get("capacity_snapshot"), 0) is None or as_int(row.get("capacity_snapshot"), 0) <= 0):
            bad_assignments.append(row.get("assignment_id"))
        if row.get("assignment_kind") == "ACTUAL":
            start = parse_ts(row.get("effective_start_utc")); end = parse_ts(row.get("effective_end_utc"))
            if start and end and end > start:
                actual_intervals[row.get("vehicle_id")].append((start, end, row.get("assignment_id")))
    overlaps = []
    for vehicle_id, intervals in actual_intervals.items():
        intervals.sort(key=lambda item: item[0])
        for left, right in zip(intervals, intervals[1:]):
            if right[0] < left[1]:
                overlaps.append((vehicle_id, left[2], right[2]))
    report.add("vehicle_assignment_integrity", not bad_assignments, "assignments reference valid trips/vehicles and positive capacities", bad=bad_assignments[:10])
    report.add("actual_vehicle_duties_nonoverlap", not overlaps, "actual duties for a vehicle do not overlap", overlaps=overlaps[:10])

    # Stop events, counts, delays, and GPS.
    bad_events = []
    events_by_trip: Dict[str, List[dict]] = defaultdict(list)
    for row in rows["trip_stop_events"]:
        trip = trip_by_id.get(row.get("trip_id"), {})
        route_stop = route_stop_by_id.get(row.get("route_stop_id"), {})
        phase_bad = False
        for id_field, status_field in (("arrival_assignment_id", "arrival_assignment_status"), ("departure_assignment_id", "departure_assignment_status")):
            phase_id = row.get(id_field); phase_status = row.get(status_field)
            if phase_status == "KNOWN" and not phase_id:
                phase_bad = True
            if phase_status in {"UNKNOWN", "NOT_APPLICABLE"} and phase_id:
                phase_bad = True
            if phase_id and phase_id not in assignment_by_id:
                phase_bad = True
        if (row.get("trip_id") not in trip_by_id or row.get("schedule_stop_time_id") not in {item.get("schedule_stop_time_id") for item in rows["schedule_stop_times"]} or
                row.get("route_stop_id") not in route_stop_by_id or route_stop.get("pattern_id") != trip.get("pattern_id") or
                as_int(row.get("stop_sequence")) != as_int(route_stop.get("stop_sequence")) or phase_bad):
            bad_events.append(row.get("stop_event_id"))
        events_by_trip[row.get("trip_id")].append(row)
        if row.get("visit_status") == "OBSERVED":
            arrival = parse_ts(row.get("actual_arrival_utc")); departure = parse_ts(row.get("actual_departure_utc"))
            if not arrival or not departure or departure < arrival:
                bad_events.append(row.get("stop_event_id"))
    report.add("trip_stop_event_relationships", not bad_events, "stop events reference valid trip/sequence/assignment records", bad=bad_events[:10])

    bad_counts = []
    seen_count_events = set()
    for row in rows["passenger_counts"]:
        event = event_by_id.get(row.get("stop_event_id"), {})
        phase_bad = False
        for id_field, status_field in (("arrival_assignment_id", "arrival_assignment_status"), ("departure_assignment_id", "departure_assignment_status")):
            phase_id = row.get(id_field); phase_status = row.get(status_field)
            if phase_status == "KNOWN" and not phase_id:
                phase_bad = True
            if phase_status in {"UNKNOWN", "NOT_APPLICABLE"} and phase_id:
                phase_bad = True
            if phase_id and phase_id not in assignment_by_id:
                phase_bad = True
        if (row.get("stop_event_id") in seen_count_events or row.get("trip_id") not in trip_by_id or event.get("trip_id") != row.get("trip_id") or
                event.get("visit_status") == "CANCELLED" or phase_bad):
            bad_counts.append(row.get("count_id"))
        seen_count_events.add(row.get("stop_event_id"))
    report.add("passenger_count_relationships", not bad_counts, "counts attach one-to-one to observed stop events", bad=bad_counts[:10])

    bad_delays = []
    seen_delay_events = set()
    for row in rows["delays"]:
        event = event_by_id.get(row.get("stop_event_id"), {})
        phase_bad = False
        for id_field, status_field in (("arrival_assignment_id", "arrival_assignment_status"), ("departure_assignment_id", "departure_assignment_status")):
            phase_id = row.get(id_field); phase_status = row.get(status_field)
            if phase_status == "KNOWN" and not phase_id:
                phase_bad = True
            if phase_status in {"UNKNOWN", "NOT_APPLICABLE"} and phase_id:
                phase_bad = True
            if phase_id and phase_id not in assignment_by_id:
                phase_bad = True
        if (row.get("stop_event_id") in seen_delay_events or
                row.get("trip_id") not in trip_by_id or event.get("trip_id") != row.get("trip_id") or
                (as_int(row.get("arrival_delay_sec"), 0) or 0) <= 0 and (as_int(row.get("departure_delay_sec"), 0) or 0) <= 0 or phase_bad):
            bad_delays.append(row.get("delay_id"))
        seen_delay_events.add(row.get("stop_event_id"))
    report.add("delay_relationships", not bad_delays, "positive delay rows attach to valid stop events and assignments", bad=bad_delays[:10])

    bad_gps = []
    gps_keys = set()
    for row in rows["gps_events"]:
        key = (row.get("trip_id"), row.get("observed_at_utc"), row.get("observation_index"))
        if key in gps_keys or row.get("trip_id") not in trip_by_id or (row.get("assignment_id") is not None and row.get("assignment_id") not in assignment_by_id) or (row.get("stop_event_id") is not None and row.get("stop_event_id") not in event_by_id):
            bad_gps.append(row.get("gps_event_id"))
        gps_keys.add(key)
    report.add("gps_relationships", not bad_gps, "GPS observations reference valid optional entities", bad=bad_gps[:10])

    # Passenger, ticket, journey, request, and transfer relationships.
    bad_tickets = []
    for row in rows["tickets"]:
        origin = route_stop_by_id.get(row.get("origin_route_stop_id"), {})
        destination = route_stop_by_id.get(row.get("destination_route_stop_id"), {})
        if (row.get("passenger_id") not in index_rows(rows["passengers"], "passenger_id") or row.get("trip_id") not in trip_by_id or
                origin.get("pattern_id") != destination.get("pattern_id") or origin.get("pattern_id") != trip_by_id[row.get("trip_id")].get("pattern_id") or
                (as_int(origin.get("stop_sequence"), 0) or 0) >= (as_int(destination.get("stop_sequence"), 0) or 0) or
                row.get("origin_stop_id") != origin.get("stop_id") or row.get("destination_stop_id") != destination.get("stop_id")):
            bad_tickets.append(row.get("ticket_id"))
    report.add("ticket_relationships", not bad_tickets, "tickets reference valid passengers/trips/ordered route stops", bad=bad_tickets[:10])

    bad_journeys = []
    request_by_id = index_rows(rows["demand_requests"], "request_id")
    ticket_by_id = index_rows(rows["tickets"], "ticket_id")
    for row in rows["passenger_journeys"]:
        origin = route_stop_by_id.get(row.get("origin_route_stop_id"), {})
        destination = route_stop_by_id.get(row.get("destination_route_stop_id"), {})
        trip = trip_by_id.get(row.get("trip_id"), {})
        if (row.get("passenger_id") not in index_rows(rows["passengers"], "passenger_id") or row.get("trip_id") not in trip_by_id or
                origin.get("pattern_id") != trip.get("pattern_id") or destination.get("pattern_id") != trip.get("pattern_id") or
                (as_int(origin.get("stop_sequence"), 0) or 0) >= (as_int(destination.get("stop_sequence"), 0) or 0) or
                row.get("boarding_stop_event_id") not in event_by_id or row.get("alighting_stop_event_id") not in event_by_id or
                event_by_id.get(row.get("boarding_stop_event_id"), {}).get("trip_id") != row.get("trip_id") or
                event_by_id.get(row.get("alighting_stop_event_id"), {}).get("trip_id") != row.get("trip_id") or
                row.get("request_id") is not None and row.get("request_id") not in request_by_id or
                row.get("ticket_id") is not None and row.get("ticket_id") not in ticket_by_id or
                as_int(row.get("passenger_count")) != 1):
            bad_journeys.append(row.get("journey_id"))
    report.add("journey_relationships", not bad_journeys, "journeys reference valid passenger/trip/ordered events and optional channels", bad=bad_journeys[:10])

    bad_requests = []
    for row in rows["demand_requests"]:
        origin = stop_by_id.get(row.get("origin_stop_id")); destination = stop_by_id.get(row.get("destination_stop_id"))
        service_day = as_date(row.get("service_date"))
        if (row.get("passenger_id") not in index_rows(rows["passengers"], "passenger_id") or origin is None or destination is None or
                origin.get("stop_id") == destination.get("stop_id") or
                (row.get("preferred_route_id") is not None and row.get("preferred_route_id") not in route_by_id) or
                service_day is None or origin.get("opened_on") > service_day.isoformat() or (destination.get("closed_on") is not None and service_day.isoformat() >= destination.get("closed_on"))):
            bad_requests.append(row.get("request_id"))
    report.add("demand_request_relationships", not bad_requests, "requests reference valid passengers/stops/routes and dates", bad=bad_requests[:10])

    bad_transfers = []
    for row in rows["passenger_transfer_events"]:
        event = event_by_id.get(row.get("replacement_stop_event_id"), {})
        transfer_at = parse_ts(row.get("transfer_at_utc"))
        event_arrival = parse_ts(event.get("actual_arrival_utc"))
        event_departure = parse_ts(event.get("actual_departure_utc"))
        if (row.get("journey_id") not in index_rows(rows["passenger_journeys"], "journey_id") or row.get("replacement_stop_event_id") not in event_by_id or
                event.get("trip_id") != index_rows(rows["passenger_journeys"], "journey_id").get(row.get("journey_id"), {}).get("trip_id") or
                row.get("from_assignment_id") is not None and row.get("from_assignment_id") not in assignment_by_id or
                row.get("to_assignment_id") is not None and row.get("to_assignment_id") not in assignment_by_id or
                not transfer_at or event_arrival and transfer_at < event_arrival or event_departure and transfer_at > event_departure):
            bad_transfers.append(row.get("transfer_id"))
    report.add("transfer_event_relationships", not bad_transfers, "replacement transfers link continuing journeys and handovers", bad=bad_transfers[:10])

    # Context and exception scopes.
    bad_context = []
    for row in rows["context_events"]:
        if (row.get("scope") == "ROUTE" and row.get("route_id") not in route_by_id) or (row.get("scope") == "STOP" and row.get("stop_id") not in stop_by_id) or (row.get("scope") not in {"ROUTE", "STOP", "NETWORK"}):
            bad_context.append(row.get("context_event_id"))
    report.add("context_event_relationships", not bad_context, "context scopes use valid optional parents", bad=bad_context[:10])
    bad_exceptions = []
    context_ids = {row.get("context_event_id") for row in rows["context_events"]}
    for row in rows["service_exceptions"]:
        if row.get("service_id") not in calendar_by_id or row.get("context_event_id") is not None and row.get("context_event_id") not in context_ids:
            bad_exceptions.append(row.get("exception_id"))
    report.add("service_exception_relationships", not bad_exceptions, "calendar exceptions reference valid calendars/context", bad=bad_exceptions[:10])

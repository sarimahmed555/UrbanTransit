"""Temporal, availability, split-boundary, and G2/G3 checks."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

from .common import (ValidationReport, as_date, as_int, clean_rows, index_rows, load_injections,
                     load_json, load_table, parse_ts)


def run(root: Path, report: ValidationReport) -> None:
    _, injection_sources = load_injections(root)
    table_names = ("passengers", "tickets", "routes", "stops", "route_patterns", "route_stops", "service_calendar",
                   "service_exceptions", "schedules", "schedule_stop_times", "vehicles", "trips",
                   "trip_vehicle_assignments", "trip_stop_events", "passenger_counts", "delays", "gps_events",
                   "passenger_journeys", "demand_requests", "context_events", "passenger_transfer_events")
    bad_metadata = []
    out_of_range = []
    for table in table_names:
        for row in clean_rows(load_table(root, table), table, injection_sources):
            event = parse_ts(row.get("event_time")); ingestion = parse_ts(row.get("ingestion_time")); available = parse_ts(row.get("value_available_at"))
            if not event or not ingestion or not available or ingestion < event or available < ingestion:
                bad_metadata.append((table, row.get("source_row_id"), row.get("event_time"), row.get("ingestion_time"), row.get("value_available_at")))
            service_date = as_date(row.get("service_date"))
            if service_date and not (date(2025, 1, 1) <= service_date <= date(2026, 6, 30)):
                out_of_range.append((table, row.get("source_row_id"), row.get("service_date")))
            correction = parse_ts(row.get("correction_time"))
            if correction and available and correction > available:
                bad_metadata.append((table, row.get("source_row_id"), "correction_after_availability"))
    report.add("temporal_metadata_contract", not bad_metadata, "event/ingestion/availability timestamps are ordered and parseable", bad=bad_metadata[:10])
    complete_availability_bad = []
    for table in table_names:
        for row in clean_rows(load_table(root, table), table, injection_sources):
            event = parse_ts(row.get("event_time")); available = parse_ts(row.get("value_available_at"))
            latest_event = None
            latest_available = None
            if table == "passenger_journeys":
                latest_event = parse_ts(row.get("alighted_at_utc"))
                latest_available = latest_event
            elif table == "demand_requests":
                latest_event = parse_ts(row.get("decision_at_utc"))
                latest_available = parse_ts(row.get("resolution_available_at_utc")) or latest_event
            elif table == "trips":
                latest_event = parse_ts(row.get("actual_end_utc")) or parse_ts(row.get("actual_start_utc"))
                latest_available = parse_ts(row.get("outcome_available_at_utc")) or latest_event
            elif table == "trip_stop_events":
                latest_event = parse_ts(row.get("actual_departure_utc")) or parse_ts(row.get("actual_arrival_utc"))
                latest_available = parse_ts(row.get("outcome_available_at_utc")) or latest_event
            elif table == "passenger_counts":
                latest_event = event
                latest_available = parse_ts(row.get("counted_at_utc")) or event
            elif table == "delays":
                latest_event = event
                latest_available = parse_ts(row.get("recorded_at_utc")) or event
            if latest_event and (not event or not available or event < latest_event or available < latest_available):
                complete_availability_bad.append((table, row.get("source_row_id"), row.get("event_time"), latest_event.isoformat() if latest_event else None))
    report.add("complete_row_availability", not complete_availability_bad, "complete rows expose later actual/outcome constituents only after their availability", bad=complete_availability_bad[:10])
    report.add("service_date_history_bounds", not out_of_range, "service dates stay within the approved history", bad=out_of_range[:10])

    # Trip schedule and actual chronology.
    trips = clean_rows(load_table(root, "trips"), "trips", injection_sources)
    events = clean_rows(load_table(root, "trip_stop_events"), "trip_stop_events", injection_sources)
    trip_by_id = index_rows(trips, "trip_id")
    bad_trip_times = []
    for trip in trips:
        scheduled_start = parse_ts(trip.get("scheduled_start_utc")); scheduled_end = parse_ts(trip.get("scheduled_end_utc"))
        actual_start = parse_ts(trip.get("actual_start_utc")); actual_end = parse_ts(trip.get("actual_end_utc"))
        if not scheduled_start or not scheduled_end or scheduled_end < scheduled_start or (actual_start and actual_end and actual_end < actual_start):
            bad_trip_times.append(trip.get("trip_id"))
    report.add("trip_time_order", not bad_trip_times, "scheduled and actual trip endpoints are ordered", bad=bad_trip_times[:10])
    late_publication = []
    for trip in trips:
        published = parse_ts(trip.get("published_at_utc")); start = parse_ts(trip.get("scheduled_start_utc"))
        if published and start and published > start:
            late_publication.append(trip.get("trip_id"))
    report.add("schedule_publication_before_use", not late_publication, "schedule/plan information is published before first service use", bad=late_publication[:10])
    bad_event_order = []
    by_trip: Dict[str, List[dict]] = defaultdict(list)
    for event in events:
        by_trip[event.get("trip_id")].append(event)
        arrival = parse_ts(event.get("actual_arrival_utc")); departure = parse_ts(event.get("actual_departure_utc"))
        if event.get("visit_status") == "OBSERVED" and (not arrival or not departure or departure < arrival):
            bad_event_order.append(event.get("stop_event_id"))
    for trip_id, group in by_trip.items():
        observed = sorted([row for row in group if row.get("visit_status") == "OBSERVED"], key=lambda item: as_int(item.get("stop_sequence"), 0) or 0)
        previous = None
        for event in observed:
            arrival = parse_ts(event.get("actual_arrival_utc")); departure = parse_ts(event.get("actual_departure_utc"))
            if previous and arrival and previous > arrival:
                bad_event_order.append(event.get("stop_event_id"))
            if departure:
                previous = departure
    report.add("stop_event_time_order", not bad_event_order, "observed arrivals and departures are physically ordered", bad=bad_event_order[:10])

    # Delay values must be derived from actual/scheduled timestamps, not random.
    schedules = clean_rows(load_table(root, "schedules"), "schedules", injection_sources)
    schedule_by_id = index_rows(schedules, "schedule_id")
    stop_time_by_id = index_rows(clean_rows(load_table(root, "schedule_stop_times"), "schedule_stop_times", injection_sources), "schedule_stop_time_id")
    event_by_id = index_rows(events, "stop_event_id")
    bad_delay_math = []
    for delay in clean_rows(load_table(root, "delays"), "delays", injection_sources):
        event = event_by_id.get(delay.get("stop_event_id"), {})
        trip = trip_by_id.get(delay.get("trip_id"), {})
        schedule = schedule_by_id.get(trip.get("schedule_id"), {})
        stop_time = stop_time_by_id.get(event.get("schedule_stop_time_id"), {})
        scheduled_start = parse_ts(trip.get("scheduled_start_utc"))
        if scheduled_start and stop_time:
            expected_arrival = scheduled_start.timestamp() + int(stop_time.get("arrival_offset_sec", 0))
            expected_departure = scheduled_start.timestamp() + int(stop_time.get("departure_offset_sec", 0))
            actual_arrival = parse_ts(event.get("actual_arrival_utc")); actual_departure = parse_ts(event.get("actual_departure_utc"))
            expected_arrival_delay = max(0, int((actual_arrival.timestamp() - expected_arrival) if actual_arrival else 0))
            expected_departure_delay = max(0, int((actual_departure.timestamp() - expected_departure) if actual_departure else 0))
            if as_int(delay.get("arrival_delay_sec"), -1) != expected_arrival_delay or as_int(delay.get("departure_delay_sec"), -1) != expected_departure_delay:
                bad_delay_math.append(delay.get("delay_id"))
    report.add("delay_values_time_derived", not bad_delay_math, "positive delay rows equal timestamp-derived components", bad=bad_delay_math[:10])

    # G2 plan applicability and stable grouping.
    op_groups: Dict[str, List[dict]] = defaultdict(list)
    for trip in trips:
        op_groups[trip.get("operational_departure_id")].append(trip)
    bad_versions = []
    for op_id, group in op_groups.items():
        group = sorted(group, key=lambda row: as_int(row.get("plan_version"), 0) or 0)
        for row in group:
            start = parse_ts(row.get("effective_from")); end = parse_ts(row.get("effective_to"))
            if start is None or (end is not None and end <= start):
                bad_versions.append(op_id)
        if sum(row.get("plan_status") == "CURRENT" for row in group) != 1:
            bad_versions.append(op_id)
    report.add("g2_plan_versions_auditable", not bad_versions, "plan versions have one current row and valid effective intervals", bad=bad_versions[:10])
    superseded_with_outcomes = []
    event_trip_ids = {row.get("trip_id") for row in events if row.get("visit_status") in {"OBSERVED", "INCOMPLETE"}}
    journey_trip_ids = {row.get("trip_id") for row in clean_rows(load_table(root, "passenger_journeys"), "passenger_journeys", injection_sources)}
    for trip in trips:
        if trip.get("plan_status") == "SUPERSEDED" and (trip.get("actual_start_utc") or trip.get("actual_end_utc") or trip.get("trip_id") in event_trip_ids or trip.get("trip_id") in journey_trip_ids):
            superseded_with_outcomes.append(trip.get("trip_id"))
    report.add("g2_superseded_not_missed_or_executed", not superseded_with_outcomes, "superseded plan versions remain auditable without automatic cancellation or actual facts", bad=superseded_with_outcomes[:10])
    cancelled_current = [trip for trip in trips if trip.get("plan_status") == "CURRENT" and trip.get("trip_status") == "CANCELLED"]
    report.add("g2_cancelled_final_version", bool(cancelled_current) and all(trip.get("actual_start_utc") is None and trip.get("actual_end_utc") is None and trip.get("cancellation_reason") for trip in cancelled_current), "a cancelled final plan is explicit and has no actual outcome", count=len(cancelled_current))
    # At arbitrary cutoffs, exactly one version is applicable.  Superseded
    # rows remain present but are not counted as extra scheduled frequency.
    applicability_failures = []
    for op_id, group in op_groups.items():
        for row in group:
            for cutoff in (parse_ts(row.get("effective_from")), parse_ts("2026-01-01T00:00:00Z"), parse_ts("2026-04-01T00:00:00Z")):
                if not cutoff:
                    continue
                selected = [candidate for candidate in group if parse_ts(candidate.get("effective_from")) <= cutoff and (parse_ts(candidate.get("effective_to")) is None or cutoff < parse_ts(candidate.get("effective_to")))]
                if len(selected) > 1:
                    applicability_failures.append((op_id, cutoff.isoformat()))
        current = [row for row in group if row.get("plan_status") == "CURRENT"]
        if current:
            cutoff = parse_ts(current[0].get("effective_from"))
            selected = [candidate for candidate in group if parse_ts(candidate.get("effective_from")) <= cutoff and (parse_ts(candidate.get("effective_to")) is None or cutoff < parse_ts(candidate.get("effective_to")))]
            if len(selected) != 1:
                applicability_failures.append((op_id, "current-effective-boundary"))
    report.add("g2_single_applicable_version", not applicability_failures, "a cutoff selects at most one applicable plan version", bad=applicability_failures[:10])

    # Split membership and purge semantics.
    cases = []
    case_path = root / "metadata/prediction_cases.csv"
    if case_path.exists():
        with case_path.open("r", encoding="utf-8", newline="") as handle:
            cases = list(csv.DictReader(handle))
    bad_cases = []
    for case in cases:
        service_day = None
        if case.get("operational_departure_id"):
            service_day = op_groups.get(case["operational_departure_id"], [{}])[0].get("service_date")
        expected_split = "TRAIN" if service_day and service_day <= "2025-12-31" else "VALIDATION" if service_day and service_day <= "2026-03-31" else "TEST" if service_day else None
        cutoff = parse_ts(case.get("prediction_cutoff")); label_start = parse_ts(case.get("label_start_utc")); label_end = parse_ts(case.get("label_end_utc"))
        if expected_split != case.get("split") or not cutoff or not label_start or not label_end or label_start < cutoff:
            bad_cases.append(case.get("case_id"))
        boundary = date(2026, 1, 1) if service_day and service_day < "2026-01-01" else date(2026, 4, 1) if service_day and service_day < "2026-04-01" else date(2026, 7, 1) if service_day and service_day < "2026-07-01" else None
        if boundary and label_end and label_end.date() >= boundary and str(case.get("purged")).lower() != "true":
            bad_cases.append(case.get("case_id"))
    report.add("chronological_split_membership", not bad_cases, "cases use chronological service-date splits and purge boundary horizons", bad=bad_cases[:10])
    report.add("split_horizon_purge_coverage", any(str(case.get("purged")).lower() == "true" for case in cases), "at least one boundary-straddling label horizon is explicitly purged", cases=len(cases))

    # G3 revision history and availability fixture.
    revisions = []
    revision_path = root / "metadata/value_revisions.csv"
    if revision_path.exists():
        with revision_path.open("r", encoding="utf-8", newline="") as handle:
            revisions = list(csv.DictReader(handle))
    # The manifest does not duplicate the fixture ID; identify the two explicit
    # revision rows by their field/entity contract instead.
    g3 = [row for row in revisions if row.get("revision_id") in {"REV-G3-ORIGINAL", "REV-G3-CORRECTED"}]
    g3_ok = False
    count_rows = clean_rows(load_table(root, "passenger_counts"), "passenger_counts", injection_sources)
    count_by_id = {row.get("count_id"): row for row in count_rows}
    if len(g3) == 2:
        original = next((row for row in g3 if row.get("value_revision") == "1"), None)
        corrected = next((row for row in g3 if row.get("value_revision") == "2"), None)
        if original and corrected:
            early = parse_ts(original.get("prediction_cutoff_early")); late = parse_ts(original.get("prediction_cutoff_late"))
            source_count = count_by_id.get(original.get("entity_id"))
            try:
                expected_corrected = str(int(original.get("original_value")) + 2)
            except (TypeError, ValueError):
                expected_corrected = ""
            g3_ok = bool(source_count and original.get("corrected_value") in (None, "", r"\N") and corrected.get("corrected_value") == expected_corrected and
                         source_count.get("onboard_departure") == original.get("original_value") and source_count.get("source_row_id") == original.get("source_row_id") and
                         parse_ts(corrected.get("value_available_at_utc")) > early and parse_ts(corrected.get("value_available_at_utc")) <= late and
                         original.get("event_time_utc") == corrected.get("event_time_utc") == source_count.get("event_time"))
    report.add("g3_late_evidence_revision", g3_ok, "corrected value is unavailable at the early cutoff and available at the late cutoff", revisions=len(g3))

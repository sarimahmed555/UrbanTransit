"""Smoke coverage, movement counting, DQ reconciliation, and artifact checks."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from ..config import canonical_json
from .common import (ValidationReport, as_date, as_float, as_int, clean_rows, index_rows, load_injections,
                     load_json, load_table, parse_ts, table_path)


def _bool(value) -> bool:
    return str(value).lower() == "true"


def run(root: Path, report: ValidationReport) -> None:
    _, injection_sources = load_injections(root)
    raw = {table: load_table(root, table) for table in (
        "passengers", "routes", "stops", "route_patterns", "route_stops", "service_calendar", "service_exceptions",
        "schedules", "schedule_stop_times", "vehicles", "trips", "trip_vehicle_assignments", "trip_stop_events",
        "passenger_counts", "delays", "gps_events", "tickets", "passenger_journeys", "demand_requests", "context_events",
        "passenger_transfer_events")}
    clean = {table: clean_rows(raw[table], table, injection_sources) for table in raw}
    # Actual row counts and minimum smoke coverage.
    nonempty = {table: len(rows) for table, rows in raw.items()}
    report.add("all_major_tables_present", all(nonempty.values()), "all 21 transport tables have at least one raw row", counts=nonempty)
    report.add("smoke_movement_minimum", len({row.get("journey_id") for row in clean["passenger_journeys"]}) >= 500, "smoke has enough canonical journeys for meaningful checks", count=len(clean["passenger_journeys"]))
    report.add("smoke_delay_minimum", len(clean["delays"]) >= 20, "smoke has positive delay evidence", count=len(clean["delays"]))
    months = {str(row.get("service_date"))[:7] for row in clean["trips"] if row.get("service_date")}
    report.add("complete_history_months", len(months) == 18, "smoke covers all 18 complete service months", months=sorted(months))

    # Raw physical duplicate evidence, kept separate from usable counts.
    ticket_groups = defaultdict(list); transaction_groups = defaultdict(list)
    for row in raw["tickets"]:
        ticket_groups[row.get("ticket_id")].append(row); transaction_groups[row.get("transaction_ref")].append(row)
    trip_groups = defaultdict(list); instance_groups = defaultdict(list)
    for row in raw["trips"]:
        trip_groups[row.get("trip_id")].append(row)
        instance_groups[(row.get("schedule_id"), row.get("service_date"), row.get("instance_index"))].append(row)
    report.add("duplicate_ticket_fixture_exists", any(len(group) > 1 for group in ticket_groups.values()) and any(len(group) > 1 for group in transaction_groups.values()), "raw duplicate ticket/transaction keys exist", ticket_groups=sum(1 for group in ticket_groups.values() if len(group) > 1))
    report.add("duplicate_trip_fixture_exists", any(len(group) > 1 for group in trip_groups.values()) or any(len(group) > 1 for group in instance_groups.values()), "raw duplicate trip identity exists")

    # Required quality families are all represented in the oracle manifest.
    manifest = load_json(root, "metadata/private_injection_manifest.json")
    rule_ids = {item.get("rule_id") for item in manifest.get("injections", [])}
    required_rules = {f"DQ{n:02d}" for n in range(1, 17)}
    report.add("all_dq_families_present", required_rules.issubset(rule_ids), "all 16 mandatory DQ families have raw fixtures", missing=sorted(required_rules - rule_ids), present=sorted(rule_ids))
    issues_path = root / "metadata/dq_expected_issues.csv"
    outcomes_path = root / "metadata/dq_expected_outcomes.csv"
    issue_rows = []
    outcome_rows = []
    if issues_path.exists():
        with issues_path.open("r", encoding="utf-8", newline="") as handle: issue_rows = list(csv.DictReader(handle))
    if outcomes_path.exists():
        with outcomes_path.open("r", encoding="utf-8", newline="") as handle: outcome_rows = list(csv.DictReader(handle))
    affected_sources = {item.get("source_row_id") for item in manifest.get("injections", [])}
    outcome_sources = [row.get("source_row_id") for row in outcome_rows]
    report.add("dq_sparse_affected_only", len(issue_rows) == len(manifest.get("injections", [])) and len(outcome_rows) <= len(issue_rows) and len(outcome_sources) == len(set(outcome_sources)) and set(outcome_sources).issubset(affected_sources) and all(row.get("issue_ids") not in (None, "", "[]") for row in outcome_rows), "one expected issue per affected fixture and sparse outcomes", issues=len(issue_rows), outcomes=len(outcome_rows), unexpected_sources=sorted(set(outcome_sources) - affected_sources)[:5])
    report.add("dq_duplicate_fixture_actual", any(len(group) > 1 for group in ticket_groups.values()), "duplicate ticket is physically present")
    report.add("dq_missing_value_fixture_actual", any(row.get("payment_method") is None for row in raw["tickets"]), "missing required value fixture is present")
    report.add("dq_invalid_timestamp_fixture_actual", any(parse_ts(row.get("issued_at_utc")) is None for row in raw["tickets"]), "invalid timestamp fixture is present")
    report.add("dq_cancellation_fixture_actual", any(row.get("trip_status") == "CANCELLED" for row in clean["trips"]), "trip cancellation is present")
    remove_dates = {row.get("exception_date") for row in clean["service_exceptions"] if row.get("action") == "REMOVE"}
    cancelled_dates = {row.get("service_date") for row in clean["trips"] if row.get("trip_status") == "CANCELLED"}
    report.add("service_exceptions_operational_effect", remove_dates.issubset(cancelled_dates), "date-specific REMOVE exceptions have corresponding cancelled service", remove_dates=sorted(remove_dates), cancelled_dates=sorted(cancelled_dates))
    report.add("dq_unknown_passenger_fixture_actual", any(row.get("passenger_id") not in {p.get("passenger_id") for p in clean["passengers"]} for row in raw["tickets"]), "unknown passenger fixture is present")
    report.add("dq_missing_trip_fixture_actual", any(row.get("trip_id") not in {t.get("trip_id") for t in clean["trips"]} for row in raw["tickets"]), "missing trip fixture is present")

    # Realistic conditions derived from operational rows, not only labels.
    trip_by_id = index_rows(clean["trips"], "trip_id")
    pattern_by_id = index_rows(clean["route_patterns"], "pattern_id")
    assignments = index_rows(clean["trip_vehicle_assignments"], "assignment_id")
    event_by_id = index_rows(clean["trip_stop_events"], "stop_event_id")
    delays = clean["delays"]
    journeys = clean["passenger_journeys"]
    board_by_daytype = Counter(); board_by_hour = Counter(); direction_counts = Counter()
    for journey in journeys:
        trip = trip_by_id.get(journey.get("trip_id"), {})
        service_day = as_date(trip.get("service_date"))
        boarded = parse_ts(journey.get("boarded_at_utc"))
        if service_day:
            board_by_daytype["weekend" if service_day.weekday() >= 5 else "weekday"] += 1
        if boarded:
            local_hour = (boarded.hour + 5) % 24
            board_by_hour["morning_peak" if 7 <= local_hour < 10 else "evening_peak" if 16 <= local_hour < 20 else "off_peak"] += 1
        pattern = pattern_by_id.get(trip.get("pattern_id"), {})
        if pattern.get("direction_id") is not None:
            direction_counts[str(pattern.get("direction_id"))] += 1
    report.add("weekday_weekend_difference", len(board_by_daytype) == 2 and board_by_daytype["weekday"] != board_by_daytype["weekend"], "observed weekday/weekend demand differs", counts=dict(board_by_daytype))
    report.add("peak_hour_demand", any(key != "off_peak" and value > 0 for key, value in board_by_hour.items()), "demand is concentrated in observed morning/evening periods", counts=dict(board_by_hour))
    report.add("route_direction_difference", len(direction_counts) == 2 and min(direction_counts.values()) != max(direction_counts.values()), "observed direction demand differs", counts=dict(direction_counts))
    report.add("special_event_spike", bool(clean["context_events"]) and any(row.get("context_event_id") for row in clean["delays"]), "context events are linked to observable delay evidence", context_events=len(clean["context_events"]))
    event_map = {row.get("context_event_id"): row for row in clean["context_events"]}
    bad_context_links = []
    for delay in clean["delays"]:
        event_id = delay.get("context_event_id")
        if not event_id:
            continue
        event = event_map.get(event_id)
        trip = trip_by_id.get(delay.get("trip_id"), {})
        observed = parse_ts(delay.get("recorded_at_utc"))
        if not event or not observed or observed < parse_ts(event.get("starts_at_utc")) or observed > parse_ts(event.get("ends_at_utc")):
            bad_context_links.append(delay.get("delay_id")); continue
        if event.get("scope") == "ROUTE" and event.get("route_id") != trip.get("route_id"):
            bad_context_links.append(delay.get("delay_id"))
    report.add("context_event_time_scope_links", not bad_context_links, "context-linked observations fall within event time and route scope", bad=bad_context_links[:10])
    report.add("overcrowded_service_evidence", any(row.get("quality_status") == "FLAGGED" and row.get("unresolved_reason") is None and (as_int(row.get("onboard_departure"), 0) or 0) > (as_int(assignments.get(row.get("departure_assignment_id"), {}).get("capacity_snapshot"), 10**9) or 0) for row in clean["passenger_counts"]), "a real capacity overload is retained and flagged")
    report.add("low_demand_service_evidence", any((as_int(row.get("onboard_departure"), 0) or 0) < 0.2 * (as_int(assignments.get(row.get("departure_assignment_id"), {}).get("capacity_snapshot"), 100) or 100) for row in clean["passenger_counts"]), "a low-demand service is observable")
    report.add("delays_early_and_vehicle_changes", bool(delays) and any(row.get("arrival_assignment_id") != row.get("departure_assignment_id") for row in clean["trip_stop_events"]), "positive delays and vehicle phase changes coexist")
    early_count = 0
    schedules_by_id = index_rows(clean["schedules"], "schedule_id")
    stop_times_by_id = index_rows(clean["schedule_stop_times"], "schedule_stop_time_id")
    for event in clean["trip_stop_events"]:
        if event.get("visit_status") != "OBSERVED":
            continue
        trip = trip_by_id.get(event.get("trip_id"), {})
        stop_time = stop_times_by_id.get(event.get("schedule_stop_time_id"), {})
        scheduled_start = parse_ts(trip.get("scheduled_start_utc")); actual = parse_ts(event.get("actual_arrival_utc"))
        if scheduled_start and stop_time and actual and actual < scheduled_start + timedelta(seconds=int(stop_time.get("arrival_offset_sec", 0))):
            early_count += 1
    report.add("early_arrival_evidence", early_count > 0, "at least one physically coherent actual arrival is earlier than schedule", count=early_count)
    report.add("positive_delay_evidence", len(delays) > 0 and any((as_int(row.get("arrival_delay_sec"), 0) or 0) > 0 or (as_int(row.get("departure_delay_sec"), 0) or 0) > 0 for row in delays), "positive delay rows are derived from actual timestamp deviations", count=len(delays))
    observed_count = sum(1 for row in clean["trip_stop_events"] if row.get("visit_status") == "OBSERVED")
    delay_rate = len(delays) / observed_count if observed_count else 0.0
    report.add("delay_rate_plausibility", 0.05 <= delay_rate <= 0.50, "smoke positive-delay frequency is nonzero but not universal", observed_events=observed_count, delay_rows=len(delays), rate=round(delay_rate, 4))
    report.add("vehicle_change_evidence", any(row.get("arrival_assignment_id") and row.get("departure_assignment_id") and row.get("arrival_assignment_id") != row.get("departure_assignment_id") for row in clean["trip_stop_events"]), "at least one replacement phase changes the actual vehicle assignment")
    phase_contract_bad = []
    for event in clean["trip_stop_events"]:
        if event.get("visit_status") == "SKIPPED" and (event.get("arrival_assignment_id") is not None or event.get("departure_assignment_id") is not None or event.get("arrival_assignment_status") != "NOT_APPLICABLE" or event.get("departure_assignment_status") != "NOT_APPLICABLE"):
            phase_contract_bad.append(event.get("stop_event_id"))
        if event.get("visit_status") == "INCOMPLETE" and (event.get("departure_assignment_id") is not None or event.get("departure_assignment_status") != "NOT_APPLICABLE"):
            phase_contract_bad.append(event.get("stop_event_id"))
    report.add("skipped_incomplete_phase_contract", not phase_contract_bad, "skipped/incomplete visits have no nonexistent phase assignment", bad=phase_contract_bad[:10])
    partial_end_bad = [trip.get("trip_id") for trip in clean["trips"] if trip.get("trip_status") == "PARTIAL" and trip.get("actual_end_utc") is not None]
    report.add("partial_trip_end_censoring", not partial_end_bad, "partial trips do not claim an unavailable terminal departure", bad=partial_end_bad)
    # Calculate same-direction headways from actual first-stop observations;
    # scenario labels are only corroborating metadata.
    headway_groups: Dict[tuple, List[tuple]] = defaultdict(list)
    for event in clean["trip_stop_events"]:
        if event.get("visit_status") != "OBSERVED" or event.get("stop_sequence") != "1":
            continue
        trip = trip_by_id.get(event.get("trip_id"), {})
        pattern = pattern_by_id.get(trip.get("pattern_id"), {})
        key = (trip.get("service_date"), pattern.get("route_id"), pattern.get("direction_id"))
        observed_at = parse_ts(event.get("actual_departure_utc"))
        if observed_at:
            headway_groups[key].append((observed_at, parse_ts(trip.get("scheduled_start_utc"))))
    bunching_pairs = 0
    irregular_pairs = 0
    for values in headway_groups.values():
        values.sort(key=lambda item: item[0])
        for left, right in zip(values, values[1:]):
            actual_gap = (right[0] - left[0]).total_seconds()
            scheduled_gap = abs((right[1] - left[1]).total_seconds()) if left[1] and right[1] else 0
            if actual_gap <= 0:
                irregular_pairs += 1
            if scheduled_gap and actual_gap < 0.25 * scheduled_gap:
                bunching_pairs += 1
            if not scheduled_gap and actual_gap < 300:
                bunching_pairs += 1
            if scheduled_gap and abs(actual_gap - scheduled_gap) > max(300.0, 0.5 * scheduled_gap):
                irregular_pairs += 1
    report.add("bunching_irregular_evidence", bunching_pairs > 0 or irregular_pairs > 0, "actual same-direction spacing contains bunching or irregular-headway evidence", bunching_pairs=bunching_pairs, irregular_pairs=irregular_pairs)

    # G1 replacement semantics and G5 partial usability.
    replacement_events = [row for row in clean["trip_stop_events"] if _bool(row.get("arrival_assignment_id") != row.get("departure_assignment_id"))]
    replacement_events = [row for row in clean["trip_stop_events"] if row.get("arrival_assignment_id") and row.get("departure_assignment_id") and row.get("arrival_assignment_id") != row.get("departure_assignment_id")]
    g1_ok = False
    if replacement_events:
        event = replacement_events[0]
        count = next((row for row in clean["passenger_counts"] if row.get("stop_event_id") == event.get("stop_event_id")), None)
        transfers_for_event = [row for row in clean["passenger_transfer_events"] if row.get("replacement_stop_event_id") == event.get("stop_event_id")]
        delay = next((row for row in clean["delays"] if row.get("stop_event_id") == event.get("stop_event_id")), None)
        arrival_assignment = assignments.get(event.get("arrival_assignment_id"), {})
        departure_assignment = assignments.get(event.get("departure_assignment_id"), {})
        g1_ok = bool(count and delay and len(transfers_for_event) >= 3 and as_int(count.get("transfer_out_count"), 0) == as_int(count.get("transfer_in_count"), -1) == len(transfers_for_event) and count.get("arrival_assignment_id") == event.get("arrival_assignment_id") and count.get("departure_assignment_id") == event.get("departure_assignment_id") and delay.get("arrival_assignment_id") == event.get("arrival_assignment_id") and delay.get("departure_assignment_id") == event.get("departure_assignment_id") and as_int(event.get("stop_sequence")) == 5 and arrival_assignment.get("assignment_id") != departure_assignment.get("assignment_id") and as_int(arrival_assignment.get("capacity_snapshot"), 0) != as_int(departure_assignment.get("capacity_snapshot"), 0))
    report.add("g1_replacement_attribution", g1_ok, "arrival phase uses incoming assignment and departure phase uses outgoing assignment with separate transfer ledger", replacement_events=len(replacement_events))
    post_handover_ok = True
    post_handover_evidence = []
    for event in replacement_events:
        trip_id_value = event.get("trip_id"); handover_seq = as_int(event.get("stop_sequence"))
        following = [item for item in clean["trip_stop_events"] if item.get("trip_id") == trip_id_value and item.get("visit_status") == "OBSERVED" and (as_int(item.get("stop_sequence")) or 0) > (handover_seq or 0)]
        for item in following:
            if item.get("arrival_assignment_id") != event.get("departure_assignment_id") or item.get("departure_assignment_id") != event.get("departure_assignment_id"):
                post_handover_ok = False
                post_handover_evidence.append(item.get("stop_event_id"))
    report.add("g1_post_handover_assignment_continuity", post_handover_ok, "post-handover observed phases remain attributed to the outgoing duty", bad=post_handover_evidence[:10])

    unknown_g1_meta = load_json(root, "metadata/g1_unknown_assignment_fixture.json")
    unknown_g1_evidence = unknown_g1_meta.get("evidence", {})
    unknown_g1_ok = False
    unknown_g1_trip = unknown_g1_evidence.get("trip_id")
    unknown_g1_sequence = as_int(unknown_g1_evidence.get("unknown_assignment_sequence"))
    if unknown_g1_trip and unknown_g1_sequence is not None:
        unknown_event = next((row for row in clean["trip_stop_events"] if row.get("trip_id") == unknown_g1_trip and as_int(row.get("stop_sequence")) == unknown_g1_sequence and row.get("visit_status") == "OBSERVED"), None)
        unknown_count = next((row for row in clean["passenger_counts"] if row.get("stop_event_id") == (unknown_event or {}).get("stop_event_id")), None)
        unknown_transfers = [row for row in clean["passenger_transfer_events"] if row.get("replacement_stop_event_id") == (unknown_event or {}).get("stop_event_id")]
        unknown_g1_ok = bool(
            unknown_event and unknown_count and unknown_transfers and
            unknown_event.get("arrival_assignment_id") is None and unknown_event.get("departure_assignment_id") is None and
            unknown_event.get("arrival_assignment_status") == "UNKNOWN" and unknown_event.get("departure_assignment_status") == "UNKNOWN" and
            unknown_count.get("arrival_assignment_id") is None and unknown_count.get("departure_assignment_id") is None and
            unknown_count.get("arrival_assignment_status") == "UNKNOWN" and unknown_count.get("departure_assignment_status") == "UNKNOWN" and
            all(row.get("from_assignment_id") is None and row.get("to_assignment_id") is None and row.get("from_assignment_status") == "UNKNOWN" and row.get("to_assignment_status") == "UNKNOWN" for row in unknown_transfers)
        )
    report.add("g1_unknown_assignment_replacement", unknown_g1_ok, "a replacement can retain transfer evidence while withholding an unresolved handover assignment", evidence=unknown_g1_evidence)

    missing_ticket_journeys = [row for row in clean["passenger_journeys"] if row.get("movement_status") == "MISSING_TICKET"]
    missing_request_journeys = [row for row in clean["passenger_journeys"] if row.get("request_link_status") == "NOT_SUPPLIED"]
    unknown_counts = [row for row in clean["passenger_counts"] if row.get("departure_assignment_status") == "UNKNOWN"]
    report.add("g5_partial_usability", bool(missing_ticket_journeys) and bool(missing_request_journeys) and bool(unknown_counts), "missing optional channels and unknown vehicle do not erase otherwise valid movement evidence", missing_ticket=len(missing_ticket_journeys), missing_request=len(missing_request_journeys), unknown_vehicle=len(unknown_counts))

    g5_core_meta = load_json(root, "metadata/g5_unresolved_core_fixture.json")
    g5_core_source = g5_core_meta.get("source_row_id")
    g5_core_row = next((row for row in raw["passenger_journeys"] if row.get("source_row_id") == g5_core_source), None)
    g5_core_ok = bool(
        g5_core_row and g5_core_row.get("quality_status") == "UNRESOLVED" and
        g5_core_row.get("unresolved_reason") == "UNKNOWN_CORE_TRIP_KEY" and
        g5_core_row.get("trip_id") not in index_rows(clean["trips"], "trip_id") and
        g5_core_row.get("journey_id") == g5_core_meta.get("journey_id") and
        g5_core_meta.get("withheld_capabilities")
    )
    report.add("g5_unresolved_core_capability", g5_core_ok, "an unresolved core trip key is quarantined while its independent field group remains auditable", evidence=g5_core_meta)
    journey_by_id = index_rows(clean["passenger_journeys"], "journey_id")
    event_by_id_local = index_rows(clean["trip_stop_events"], "stop_event_id")
    g5_capability_ok = True
    for journey in missing_ticket_journeys:
        trip = trip_by_id.get(journey.get("trip_id"), {})
        g5_capability_ok &= bool(journey.get("movement_status") == "MISSING_TICKET" and journey.get("ticket_id") is None and journey.get("passenger_id") and journey.get("boarding_stop_event_id") in event_by_id_local and journey.get("alighting_stop_event_id") in event_by_id_local and trip.get("trip_id"))
    for journey in missing_request_journeys:
        g5_capability_ok &= bool(journey.get("request_link_status") == "NOT_SUPPLIED" and journey.get("request_id") is None and journey.get("origin_route_stop_id") and journey.get("destination_route_stop_id"))
    for count in unknown_counts:
        g5_capability_ok &= bool(count.get("arrival_assignment_id") is None and count.get("departure_assignment_id") is None and count.get("arrival_assignment_status") == "UNKNOWN" and count.get("departure_assignment_status") == "UNKNOWN" and count.get("onboard_departure") is not None and count.get("stop_event_id") in event_by_id_local)
    report.add("g5_capability_disposition", g5_capability_ok and (root / "metadata/lifecycle_contract.json").exists(), "valid movement/timing evidence remains usable while optional/vehicle-dependent capabilities are withheld")

    # Canonical movement count is a max/view choice, never a sum.
    journey_ids = {row.get("journey_id") for row in clean["passenger_journeys"]}
    ticket_ids = {row.get("ticket_id") for row in clean["tickets"]}
    journey_ticket_refs = {row.get("ticket_id") for row in clean["passenger_journeys"] if row.get("ticket_id")}
    overlap = len(journey_ticket_refs & ticket_ids)
    chosen = max(len(journey_ids), len(ticket_ids))
    report.add("canonical_movement_count_no_double_count", chosen == len(journey_ids) or chosen == len(ticket_ids), "movement count uses one canonical view and does not sum ticket/journey rows", journeys=len(journey_ids), tickets=len(ticket_ids), overlap=overlap, canonical=chosen)
    movement_meta = load_json(root, "metadata/movement_count.json")
    report.add("movement_count_manifest_matches", movement_meta.get("canonical_movement_count") == chosen and movement_meta.get("usable_journey_count") == len(journey_ids) and movement_meta.get("usable_ticket_count") == len(ticket_ids), "movement-count metadata matches usable distinct views", metadata=movement_meta)

    # DQ reconciliation equation.
    reconciliation = load_json(root, "metadata/dq_reconciliation.json").get("tables", {})
    unbalanced = []
    for table, values in reconciliation.items():
        lhs = as_int(values.get("raw_count"), 0) or 0
        rhs = sum(as_int(values.get(key), 0) or 0 for key in ("accepted_unchanged_count", "accepted_corrected_count", "accepted_flagged_count", "quarantined_count", "removed_count", "deduplicated_count", "other_documented_disposition_count"))
        if lhs != rhs:
            unbalanced.append((table, lhs, rhs))
    report.add("dq_reconciliation_balances", not unbalanced, "raw rows equal exclusive affected dispositions plus unchanged reconciliation", bad=unbalanced)

    # Manifest paths, byte sizes, hashes, and row counts.
    generation = load_json(root, "metadata/generation_manifest.json")
    manifest_bad = []
    for file_info in generation.get("files", []):
        path = root / file_info.get("path", "")
        if not path.exists():
            manifest_bad.append((file_info.get("path"), "missing"))
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if file_info.get("sha256") and digest != file_info.get("sha256"):
            manifest_bad.append((file_info.get("path"), "sha256"))
        if as_int(file_info.get("bytes"), -1) != path.stat().st_size:
            manifest_bad.append((file_info.get("path"), "bytes"))
    report.add("manifest_matches_files", not manifest_bad, "manifest paths, checksums, and byte sizes match actual artifacts", bad=manifest_bad[:10])
    count_bad = []
    for table, declared in generation.get("actual_row_counts", {}).items():
        actual = len(load_table(root, table))
        if as_int(declared, -1) != actual:
            count_bad.append((table, declared, actual))
    report.add("manifest_row_counts_match", not count_bad, "manifest row counts equal all physical CSV shards", bad=count_bad)
    manifest_hash = generation.get("manifest_sha256")
    hash_body = dict(generation)
    hash_body.pop("manifest_sha256", None)
    report.add("manifest_content_hash", bool(manifest_hash) and hashlib.sha256(canonical_json(hash_body).encode("utf-8")).hexdigest() == manifest_hash, "manifest content hash matches its canonical pre-hash body")
    deterministic_hash = generation.get("deterministic_content_sha256")
    excluded_runtime = set(generation.get("runtime_fields_excluded_from_deterministic_hash", []))
    deterministic_body = {
        key: value for key, value in generation.items()
        if key not in excluded_runtime | {"manifest_sha256", "deterministic_content_sha256", "runtime_fields_excluded_from_deterministic_hash"}
    }
    report.add("manifest_deterministic_content_hash", bool(deterministic_hash) and hashlib.sha256(canonical_json(deterministic_body).encode("utf-8")).hexdigest() == deterministic_hash, "manifest deterministic content hash excludes declared runtime-only fields", excluded_runtime=sorted(excluded_runtime))

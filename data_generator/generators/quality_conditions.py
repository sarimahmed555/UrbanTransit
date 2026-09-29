"""Controlled raw-quality fixtures and sparse expected audit records.

These are generator/test-oracle artifacts, not a claim that a Spark/Python
cleaning pipeline has already run.  The raw defects are real rows in the raw
snapshot; the manifest and expected issue/outcome fixtures make them auditable
without creating an outcome for every unchanged valid row.  The additional
G5 capability row is explicitly quarantined and is not counted as a canonical
movement.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..config import canonical_json
from ..ids import entity_id
from .common import GenerationContext


QUALITY_FAMILIES = [
    ("DQ01", "MISSING_TICKET", "missing ticket record"),
    ("DQ02", "MISSING_ROUTE_ID", "missing route identifier"),
    ("DQ03", "INVALID_STOP_ID", "invalid stop identifier"),
    ("DQ04", "DUPLICATE_TICKET", "duplicate ticket transaction"),
    ("DQ05", "DUPLICATE_TRIP", "duplicate trip"),
    ("DQ06", "NEGATIVE_PASSENGER_COUNT", "negative passenger count"),
    ("DQ07", "INVALID_TIMESTAMP", "invalid timestamp"),
    ("DQ08", "IMPOSSIBLE_ARRIVAL", "impossible arrival time"),
    ("DQ09", "DEPARTURE_BEFORE_ARRIVAL", "departure before arrival"),
    ("DQ10", "CAPACITY_VIOLATION", "capacity violation"),
    ("DQ11", "INVALID_DELAY", "invalid delay value"),
    ("DQ12", "MISSING_VEHICLE_ASSIGNMENT", "missing vehicle assignment"),
    ("DQ13", "BROKEN_STOP_SEQUENCE", "broken stop sequence"),
    ("DQ14", "INVALID_ROUTE_DISTANCE", "invalid route distance"),
    ("DQ15", "UNKNOWN_PASSENGER", "unknown passenger"),
    ("DQ16", "MISSING_TRIP", "missing trip record"),
    ("C01", "MISSING_REQUIRED_VALUE", "missing required value"),
    ("G5_CORE_TRIP", "UNKNOWN_CORE_TRIP_KEY", "unresolved core trip capability"),
]


@dataclass
class QualityResult:
    issues: List[dict]
    outcomes: List[dict]
    reconciliation: Dict[str, dict]
    manifest: List[dict]


def _sample(ctx: GenerationContext, table: str) -> dict:
    if table not in ctx.sample_rows:
        raise RuntimeError(f"quality fixture requires a base {table} row")
    return copy.deepcopy(ctx.sample_rows[table])


def _unknown_id(ctx: GenerationContext, entity_type: str, key: str) -> str:
    return entity_id(entity_type, key, namespace=ctx.config.identity_namespace)


def _new_business_id(ctx: GenerationContext, entity_type: str, key: str) -> str:
    return entity_id(entity_type, key, namespace=ctx.config.identity_namespace)


def _base_for_injected(ctx: GenerationContext, row: dict, *, issue: str) -> dict:
    result = copy.deepcopy(row)
    result["quality_status"] = "FLAGGED"
    result["unresolved_reason"] = issue
    result["parse_status"] = "INJECTED_RAW"
    return result


def _record(ctx: GenerationContext, defect_id: str, scenario: str, table: str, source_id: str,
            business_key: str, rule_id: str, field_path: str, original_value: Any,
            mutation: str, disposition: str, usable_for: Iterable[str]) -> None:
    ctx.add_injection(
        injection_id=defect_id,
        scenario_id=scenario,
        table=table,
        source_row_id=source_id,
        business_key=business_key,
        rule_id=rule_id,
        field_path=field_path,
        original_value=original_value,
        mutation=mutation,
        disposition=disposition,
        usable_for=usable_for,
    )


def apply_quality_conditions(ctx: GenerationContext, writers: Dict[str, Any], audit_time: str) -> QualityResult:
    """Inject one bounded fixture for every mandatory DQ family.

    The function returns expected audit fixtures.  It intentionally does not
    inspect the private pristine values at cleaning time; validators use the
    manifest only to separate deliberate raw defects from clean truth.
    """
    if not ctx.config.inject_quality_defects:
        return QualityResult([], [], {}, [])

    defects: List[Dict[str, Any]] = []

    def add(table: str, row: dict, defect_id: str, scenario: str, rule_id: str,
            field_path: str, original_value: Any, mutation: str, disposition: str,
            usable_for: Iterable[str], business_key: Optional[str] = None) -> None:
        source_id = writers[table].write(row)
        key = business_key or str(row.get("ticket_id") or row.get("trip_id") or row.get("count_id") or row.get("stop_event_id") or row.get("route_stop_id") or row.get("pattern_id") or row.get("assignment_id") or source_id)
        _record(ctx, defect_id, scenario, table, source_id, key, rule_id, field_path, original_value, mutation, disposition, usable_for)
        defects.append({"table": table, "source_row_id": source_id, "business_key": key, "rule_id": rule_id, "defect_id": defect_id, "disposition": disposition, "original_record": row})

    # DQ04 exact duplicate ticket: all business values intentionally match the
    # retained original; only physical source identity differs.
    ticket = _sample(ctx, "tickets")
    duplicate = copy.deepcopy(ticket)
    add("tickets", duplicate, "INJ-DQ04-DUP-TICKET", "C02", "DQ04", "record", canonical_json({key: ticket.get(key) for key in ("ticket_id", "transaction_ref")}), "append exact business-key copy", "DUPLICATE_REMOVED", ["deduplication", "audit"])

    # DQ05 exact duplicate trip.
    trip = _sample(ctx, "trips")
    duplicate_trip = copy.deepcopy(trip)
    add("trips", duplicate_trip, "INJ-DQ05-DUP-TRIP", "C02", "DQ05", "record", trip.get("trip_id"), "append exact business-key copy", "DUPLICATE_REMOVED", ["deduplication", "audit"])

    # DQ01 is a missing physical ticket represented on its valid journey row;
    # no replacement ticket is fabricated.
    missing_ticket_source = ctx.fixture_refs.get("missing_ticket_source")
    if missing_ticket_source:
        _record(ctx, "INJ-DQ01-MISSING-TICKET", "C01", "passenger_journeys", missing_ticket_source,
                ctx.fixture_refs.get("missing_ticket_journey_id", ""), "DQ01", "ticket_id", None,
                "omit corresponding raw ticket while retaining journey", "ACCEPTED_FLAGGED", ["movement", "od", "timing", "boarding"])
        defects.append({"table": "passenger_journeys", "source_row_id": missing_ticket_source, "business_key": ctx.fixture_refs.get("missing_ticket_journey_id", ""), "rule_id": "DQ01", "defect_id": "INJ-DQ01-MISSING-TICKET", "disposition": "ACCEPTED_FLAGGED", "original_record": ctx.sample_rows.get("passenger_journeys", {})})

    # DQ02 missing route ID.
    missing_route = _base_for_injected(ctx, _sample(ctx, "trips"), issue="MISSING_ROUTE_ID")
    missing_route["trip_id"] = _new_business_id(ctx, "Trip", "missing-route-fixture")
    missing_route["route_id"] = None
    add("trips", missing_route, "INJ-DQ02-MISSING-ROUTE", "C01", "DQ02", "route_id", None, "null required route reference", "QUARANTINED", ["audit"])

    # DQ03 invalid stop ID.
    invalid_stop = _base_for_injected(ctx, _sample(ctx, "tickets"), issue="INVALID_STOP_ID")
    invalid_stop["ticket_id"] = _new_business_id(ctx, "Ticket", "invalid-stop-fixture")
    invalid_stop["transaction_ref"] = "TXN-INVALID-STOP-FIXTURE"
    invalid_stop["origin_stop_id"] = _unknown_id(ctx, "Stop", "unknown-stop-fixture")
    add("tickets", invalid_stop, "INJ-DQ03-INVALID-STOP", "C01", "DQ03", "origin_stop_id", invalid_stop["origin_stop_id"], "replace stop reference with unknown key", "QUARANTINED", ["audit"])

    # DQ06 negative count with a unique physical row but a broken count grain.
    negative_count = _base_for_injected(ctx, _sample(ctx, "passenger_counts"), issue="NEGATIVE_PASSENGER_COUNT")
    negative_count["count_id"] = _new_business_id(ctx, "PassengerCount", "negative-count-fixture")
    negative_count["boardings"] = -1
    negative_count["onboard_departure"] = -1
    add("passenger_counts", negative_count, "INJ-DQ06-NEGATIVE-COUNT", "C01", "DQ06", "boardings", -1, "negative sensor count", "QUARANTINED", ["audit"])

    # DQ07 lexical timestamp defect.
    invalid_timestamp = _base_for_injected(ctx, _sample(ctx, "tickets"), issue="INVALID_TIMESTAMP")
    invalid_timestamp["ticket_id"] = _new_business_id(ctx, "Ticket", "invalid-timestamp-fixture")
    invalid_timestamp["transaction_ref"] = "TXN-INVALID-TIMESTAMP-FIXTURE"
    invalid_timestamp["issued_at_utc"] = "2025-13-99T25:61:00Z"
    add("tickets", invalid_timestamp, "INJ-DQ07-BAD-TIMESTAMP", "C03", "DQ07", "issued_at_utc", invalid_timestamp["issued_at_utc"], "malformed timestamp string", "QUARANTINED", ["audit"])

    # DQ08 impossible arrival chronology.
    bad_arrival = _base_for_injected(ctx, _sample(ctx, "trip_stop_events"), issue="IMPOSSIBLE_ARRIVAL")
    bad_arrival["stop_event_id"] = _new_business_id(ctx, "TripStopEvent", "impossible-arrival-fixture")
    bad_arrival["actual_arrival_utc"] = "2000-01-01T00:00:00.000000Z"
    add("trip_stop_events", bad_arrival, "INJ-DQ08-IMPOSSIBLE-ARRIVAL", "C03", "DQ08", "actual_arrival_utc", bad_arrival["actual_arrival_utc"], "arrival before feasible trip chronology", "QUARANTINED", ["audit"])

    # DQ09 departure before arrival.
    bad_departure = _base_for_injected(ctx, _sample(ctx, "trip_stop_events"), issue="DEPARTURE_BEFORE_ARRIVAL")
    bad_departure["stop_event_id"] = _new_business_id(ctx, "TripStopEvent", "departure-before-arrival-fixture")
    bad_departure["actual_departure_utc"] = "2000-01-01T00:00:00.000000Z"
    add("trip_stop_events", bad_departure, "INJ-DQ09-DEPARTURE-BEFORE-ARRIVAL", "C03", "DQ09", "actual_departure_utc", bad_departure["actual_departure_utc"], "departure earlier than arrival", "QUARANTINED", ["audit"])

    # DQ10 impossible capacity observation; real overload remains a separate
    # FLAGGED clean row in the main data.
    capacity = _base_for_injected(ctx, _sample(ctx, "passenger_counts"), issue="CAPACITY_VIOLATION")
    capacity["count_id"] = _new_business_id(ctx, "PassengerCount", "capacity-fixture")
    capacity["onboard_arrival"] = 9999
    capacity["onboard_departure"] = 10000
    add("passenger_counts", capacity, "INJ-DQ10-CAPACITY-VIOLATION", "C13", "DQ10", "onboard_departure", 10000, "unexplained capacity spike", "QUARANTINED", ["audit"])

    # DQ11 invalid positive-delay row.
    bad_delay = _base_for_injected(ctx, _sample(ctx, "delays"), issue="INVALID_DELAY")
    bad_delay["delay_id"] = _new_business_id(ctx, "Delay", "invalid-delay-fixture")
    bad_delay["arrival_delay_sec"] = -5
    bad_delay["departure_delay_sec"] = 0
    add("delays", bad_delay, "INJ-DQ11-INVALID-DELAY", "C01", "DQ11", "arrival_delay_sec", -5, "negative value in positive-only delay table", "QUARANTINED", ["audit"])

    # DQ12 unresolved vehicle assignment.
    missing_assignment = _base_for_injected(ctx, _sample(ctx, "trip_vehicle_assignments"), issue="MISSING_VEHICLE_ASSIGNMENT")
    missing_assignment["assignment_id"] = _new_business_id(ctx, "Assignment", "missing-vehicle-fixture")
    missing_assignment["vehicle_id"] = None
    missing_assignment["capacity_snapshot"] = 0
    add("trip_vehicle_assignments", missing_assignment, "INJ-DQ12-MISSING-ASSIGNMENT", "C01", "DQ12", "vehicle_id", None, "null vehicle and non-positive capacity", "QUARANTINED", ["audit"])

    # DQ13 duplicate sequence in a challenge copy.
    broken_sequence = _base_for_injected(ctx, _sample(ctx, "route_stops"), issue="BROKEN_STOP_SEQUENCE")
    broken_sequence["route_stop_id"] = _new_business_id(ctx, "RouteStop", "broken-sequence-fixture")
    broken_sequence["stop_sequence"] = 1
    add("route_stops", broken_sequence, "INJ-DQ13-BROKEN-SEQUENCE", "C01", "DQ13", "stop_sequence", 1, "duplicate sequence within pattern", "QUARANTINED", ["audit"])

    # DQ14 invalid distance in a challenge pattern copy.
    invalid_distance = _base_for_injected(ctx, _sample(ctx, "route_patterns"), issue="INVALID_ROUTE_DISTANCE")
    invalid_distance["pattern_id"] = _new_business_id(ctx, "RoutePattern", "invalid-distance-fixture")
    invalid_distance["distance_km"] = 0
    add("route_patterns", invalid_distance, "INJ-DQ14-BAD-DISTANCE", "C01", "DQ14", "distance_km", 0, "non-positive pattern distance", "QUARANTINED", ["audit"])

    # DQ15 unknown passenger.
    unknown_passenger = _base_for_injected(ctx, _sample(ctx, "tickets"), issue="UNKNOWN_PASSENGER")
    unknown_passenger["ticket_id"] = _new_business_id(ctx, "Ticket", "unknown-passenger-fixture")
    unknown_passenger["transaction_ref"] = "TXN-UNKNOWN-PASSENGER-FIXTURE"
    unknown_passenger["passenger_id"] = _unknown_id(ctx, "Passenger", "unknown-passenger-fixture")
    add("tickets", unknown_passenger, "INJ-DQ15-UNKNOWN-PASSENGER", "C01", "DQ15", "passenger_id", unknown_passenger["passenger_id"], "fact references absent passenger", "QUARANTINED", ["audit"])

    # DQ16 missing trip parent.
    missing_trip = _base_for_injected(ctx, _sample(ctx, "tickets"), issue="MISSING_TRIP")
    missing_trip["ticket_id"] = _new_business_id(ctx, "Ticket", "missing-trip-fixture")
    missing_trip["transaction_ref"] = "TXN-MISSING-TRIP-FIXTURE"
    missing_trip["trip_id"] = _unknown_id(ctx, "Trip", "missing-trip-fixture")
    add("tickets", missing_trip, "INJ-DQ16-MISSING-TRIP", "C01", "DQ16", "trip_id", missing_trip["trip_id"], "fact references absent trip", "QUARANTINED", ["audit"])

    # C01 explicit required-value missing fixture.
    missing_value = _base_for_injected(ctx, _sample(ctx, "tickets"), issue="MISSING_REQUIRED_VALUE")
    missing_value["ticket_id"] = _new_business_id(ctx, "Ticket", "missing-value-fixture")
    missing_value["transaction_ref"] = "TXN-MISSING-VALUE-FIXTURE"
    missing_value["payment_method"] = None
    add("tickets", missing_value, "INJ-C01-MISSING-VALUE", "C01", "C01", "payment_method", None, "null required fare/payment attribute", "QUARANTINED", ["audit"])

    # G5 capability fixture: retain an otherwise useful journey-shaped field
    # group while making its core trip relationship explicitly unresolved.  It
    # is quarantined from canonical movement counts and is not presented as a
    # valid journey with a guessed parent key.
    unresolved_core = _base_for_injected(ctx, _sample(ctx, "passenger_journeys"), issue="UNKNOWN_CORE_TRIP_KEY")
    unresolved_core["journey_id"] = _new_business_id(ctx, "Journey", "unresolved-core-trip-fixture")
    unresolved_core["trip_id"] = _unknown_id(ctx, "Trip", "unresolved-core-trip-fixture")
    unresolved_core["quality_status"] = "UNRESOLVED"
    unresolved_core["movement_status"] = "UNRESOLVED"
    add("passenger_journeys", unresolved_core, "INJ-G5-UNRESOLVED-CORE-TRIP", "G5", "G5_CORE_TRIP", "trip_id", unresolved_core["trip_id"], "retain independent journey fields while withholding unresolved trip capability", "QUARANTINED", ["audit", "independent_field_group"], business_key=unresolved_core["journey_id"])
    ctx.fixture_refs["g5_unresolved_core_source"] = ctx.injections[-1]["source_row_id"]
    ctx.fixture_refs["g5_unresolved_core_journey_id"] = unresolved_core["journey_id"]

    # Build sparse expected issue/outcome fixtures.  The run is explicitly
    # labelled as a generator validation fixture, not a completed pipeline run.
    issues: List[dict] = []
    outcomes_by_source: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for index, defect in enumerate(defects, start=1):
        rule_id = defect["rule_id"]
        issue_id = entity_id("Issue", {"run_id": ctx.config.run_id, "defect_id": defect["defect_id"]}, namespace=ctx.config.identity_namespace, prefix="DQ")
        original_record = canonical_json({key: value for key, value in defect["original_record"].items() if key not in {"raw_file", "row_ordinal", "raw_bytes_sha256", "raw_record_text", "source_row_id"}})
        issue = {
            "issue_id": issue_id,
            "run_id": ctx.config.run_id,
            "pipeline_id": "GENERATOR_VALIDATION_FIXTURE",
            "dataset_version": ctx.config.dataset_version,
            "table_name": defect["table"],
            "source_row_id": defect["source_row_id"],
            "business_key_raw": defect["business_key"],
            "rule_id": rule_id,
            "rule_version": "2.0",
            "field_path": "record",
            "issue_index": 1,
            "detected_issue": rule_id,
            "severity": "ERROR" if defect["disposition"] == "QUARANTINED" else "WARNING",
            "original_record": original_record,
            "original_value": canonical_json(ctx.injections[-1]["original_value"]) if ctx.injections else None,
            "cleaning_rule": f"GEN-{rule_id}",
            "corrected_value": None,
            "correction_time": None,
            "evidence_available_at": None,
            "value_available_at": audit_time,
            "prior_value_revision": None,
            "value_revision": 1,
            "action": "DEDUPLICATE" if defect["disposition"] == "DUPLICATE_REMOVED" else "KEEP_FLAGGED" if defect["disposition"] == "ACCEPTED_FLAGGED" else "QUARANTINE",
            "reason": "Expected fixture for the controlled raw-quality condition; independent detectors must reproduce it.",
            "detected_at_utc": audit_time,
            "evidence_refs": json.dumps([], separators=(",", ":")),
        }
        # Correct the field path/value to the specific injection metadata.
        matching = next(item for item in ctx.injections if item["injection_id"] == defect["defect_id"])
        issue["field_path"] = matching["field_path"]
        issue["original_value"] = None if matching["original_value"] is None else str(matching["original_value"])
        issues.append(issue)
        key = (defect["table"], defect["source_row_id"])
        outcome = outcomes_by_source.setdefault(key, {
            "outcome_id": entity_id("Outcome", {"run_id": ctx.config.run_id, "table": defect["table"], "source_row_id": defect["source_row_id"]}, namespace=ctx.config.identity_namespace, prefix="OUT"),
            "run_id": ctx.config.run_id,
            "pipeline_id": "GENERATOR_VALIDATION_FIXTURE",
            "dataset_version": ctx.config.dataset_version,
            "source_row_id": defect["source_row_id"],
            "table_name": defect["table"],
            "issue_ids": [],
            "original_record_ref": defect["source_row_id"],
            "corrected_record": None,
            "final_status": defect["disposition"],
            "usable_for": matching["usable_for"],
            "canonical_business_key": defect["business_key"],
            "survivor_source_row_id": None,
            "finalized_at_utc": audit_time,
            "value_revision": 1,
            "value_available_at": audit_time,
        })
        outcome["issue_ids"].append(issue_id)
    for outcome in outcomes_by_source.values():
        outcome["issue_ids"] = json.dumps(sorted(outcome["issue_ids"]), separators=(",", ":"))
    outcomes = list(outcomes_by_source.values())

    reconciliation: Dict[str, dict] = {}
    for table, writer in writers.items():
        affected = [outcome for outcome in outcomes if outcome["table_name"] == table]
        disposition_counts: Dict[str, int] = {}
        for outcome in affected:
            disposition_counts[outcome["final_status"]] = disposition_counts.get(outcome["final_status"], 0) + 1
        streamed_duplicates = (getattr(ctx, 'ticket_duplicates', None).emitted
                               if table == 'tickets' and hasattr(ctx, 'ticket_duplicates') else 0)
        if streamed_duplicates:
            disposition_counts['DUPLICATE_REMOVED'] = disposition_counts.get('DUPLICATE_REMOVED', 0) + streamed_duplicates
        raw_count = writer.total_rows
        accounted = sum(disposition_counts.values())
        reconciliation[table] = {
            "raw_count": raw_count,
            "accepted_unchanged_count": raw_count - accounted,
            "accepted_corrected_count": 0,
            "accepted_flagged_count": disposition_counts.get("ACCEPTED_FLAGGED", 0),
            "quarantined_count": disposition_counts.get("QUARANTINED", 0),
            "removed_count": disposition_counts.get("REMOVED", 0),
            "deduplicated_count": disposition_counts.get("DUPLICATE_REMOVED", 0),
            "other_documented_disposition_count": 0,
            "affected_unique_count": len(affected) + streamed_duplicates,
        }
    return QualityResult(issues, outcomes, reconciliation, ctx.injections)

"""Machine-readable table schemas used by the writer and validators.

The field lists mirror ``documentation/DATA_DICTIONARY.md``.  The common
provenance columns and raw-envelope columns are included in raw CSV files so
that the smoke package is self-describing.  The dictionary remains the human
contract; this module is the executable schema snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = False


COMMON_COLUMNS: List[Column] = [
    Column("dataset_version", "STR", False),
    Column("source_id", "ID", False),
    Column("source_row_id", "ID", False),
    Column("event_time", "TS", False),
    Column("value_available_at", "TS", False),
    Column("correction_time", "TS", True),
    Column("ingestion_time", "TS", False),
    Column("quality_status", "STR", False),
    Column("unresolved_reason", "STR", True),
]

RAW_ENVELOPE_COLUMNS: List[Column] = [
    Column("raw_file", "STR", False),
    Column("row_ordinal", "INT", False),
    Column("raw_bytes_sha256", "STR", False),
    Column("raw_record_text", "STR", False),
    Column("parse_status", "STR", False),
]


def cols(*names: str) -> List[Column]:
    return [Column(name, "STR", True) for name in names]


TABLE_COLUMNS: Dict[str, List[Column]] = {
    "passengers": cols("passenger_id", "registered_at_utc", "passenger_type", "home_zone", "accessibility_need", "valid_from", "valid_to"),
    "tickets": cols("ticket_id", "transaction_ref", "passenger_id", "trip_id", "origin_route_stop_id", "destination_route_stop_id", "origin_stop_id", "destination_stop_id", "issued_at_utc", "service_date", "fare_amount", "currency", "fare_product", "payment_method", "transaction_status"),
    "routes": cols("route_id", "route_code", "route_name", "mode", "service_type", "social_service_required", "opened_on", "closed_on", "route_status"),
    "stops": cols("stop_id", "stop_code", "stop_name", "latitude", "longitude", "zone_id", "stop_type", "opened_on", "closed_on", "wheelchair_accessible"),
    "route_stops": cols("route_stop_id", "pattern_id", "route_id", "stop_id", "stop_sequence", "distance_from_start_km", "pickup_allowed", "dropoff_allowed"),
    "trips": cols("trip_id", "operational_departure_id", "plan_version", "predecessor_trip_id", "effective_from", "effective_to", "plan_status", "route_id", "pattern_id", "schedule_id", "service_id", "service_date", "instance_index", "planned_vehicle_id", "planned_vehicle_status", "scheduled_start_utc", "scheduled_end_utc", "published_at_utc", "trip_status", "cancellation_reason", "actual_start_utc", "actual_end_utc", "outcome_available_at_utc"),
    "schedules": cols("schedule_id", "pattern_id", "service_id", "departure_offset_sec", "valid_from", "valid_to", "published_at_utc", "schedule_version"),
    "vehicles": cols("vehicle_id", "vehicle_code", "vehicle_type", "seated_capacity", "standing_capacity", "nominal_capacity", "commissioned_on", "retired_on", "operational_status"),
    "passenger_counts": cols("count_id", "stop_event_id", "trip_id", "arrival_assignment_id", "departure_assignment_id", "arrival_assignment_status", "departure_assignment_status", "service_date", "counted_at_utc", "boardings", "alightings", "onboard_arrival", "onboard_departure", "replacement_event", "transfer_out_count", "transfer_in_count", "measurement_method"),
    "delays": cols("delay_id", "stop_event_id", "trip_id", "arrival_assignment_id", "departure_assignment_id", "arrival_assignment_status", "departure_assignment_status", "service_date", "arrival_delay_sec", "departure_delay_sec", "recorded_at_utc", "reported_cause", "context_event_id"),
    "gps_events": cols("gps_event_id", "trip_id", "assignment_id", "assignment_status", "stop_event_id", "service_date", "observed_at_utc", "observation_index", "latitude", "longitude", "distance_along_pattern_km", "speed_kph", "accuracy_m"),
    "service_calendar": cols("service_id", "service_name", "timezone", "valid_from", "valid_to", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "calendar_day_type", "published_at_utc"),
    "route_patterns": cols("pattern_id", "route_id", "direction_id", "pattern_version", "distance_km", "valid_from", "valid_to", "published_at_utc"),
    "schedule_stop_times": cols("schedule_stop_time_id", "schedule_id", "route_stop_id", "stop_sequence", "arrival_offset_sec", "departure_offset_sec"),
    "trip_stop_events": cols("stop_event_id", "trip_id", "schedule_stop_time_id", "route_stop_id", "stop_sequence", "service_date", "arrival_assignment_id", "departure_assignment_id", "arrival_assignment_status", "departure_assignment_status", "actual_arrival_utc", "actual_departure_utc", "visit_status", "outcome_available_at_utc"),
    "trip_vehicle_assignments": cols("assignment_id", "trip_id", "vehicle_id", "assignment_kind", "start_stop_sequence", "end_stop_sequence", "capacity_snapshot", "capacity_reason", "effective_start_utc", "effective_end_utc", "announced_at_utc"),
    "service_exceptions": cols("exception_id", "service_id", "exception_date", "action", "day_type_override", "context_event_id", "published_at_utc"),
    "passenger_journeys": cols("journey_id", "passenger_id", "trip_id", "request_id", "request_link_status", "ticket_id", "origin_route_stop_id", "destination_route_stop_id", "boarding_stop_event_id", "alighting_stop_event_id", "service_date", "boarded_at_utc", "alighted_at_utc", "passenger_count", "movement_status"),
    "demand_requests": cols("request_id", "passenger_id", "origin_stop_id", "destination_stop_id", "preferred_route_id", "requested_at_utc", "request_available_at_utc", "desired_departure_utc", "service_date", "resolution", "decision_at_utc", "reason", "resolution_available_at_utc"),
    "context_events": cols("context_event_id", "event_type", "event_name", "scope", "route_id", "stop_id", "starts_at_utc", "ends_at_utc", "announced_at_utc", "expected_in_advance", "description"),
    "passenger_transfer_events": cols("transfer_id", "journey_id", "replacement_stop_event_id", "from_assignment_id", "to_assignment_id", "from_assignment_status", "to_assignment_status", "transfer_at_utc", "transfer_type", "transfer_status"),
}

# Audit tables intentionally do not carry the transport common columns.
AUDIT_COLUMNS: Dict[str, List[Column]] = {
    "dq_issues": [
        Column("issue_id", "ID", False), Column("run_id", "ID", False), Column("pipeline_id", "STR", False),
        Column("dataset_version", "STR", False), Column("table_name", "STR", False), Column("source_row_id", "ID", False),
        Column("business_key_raw", "STR", True), Column("rule_id", "STR", False), Column("rule_version", "STR", False),
        Column("field_path", "STR", False), Column("issue_index", "INT", False), Column("detected_issue", "STR", False),
        Column("severity", "STR", False), Column("original_record", "STR", False), Column("original_value", "STR", True),
        Column("cleaning_rule", "STR", False), Column("corrected_value", "STR", True), Column("correction_time", "TS", True),
        Column("evidence_available_at", "TS", True), Column("value_available_at", "TS", False), Column("prior_value_revision", "INT", True),
        Column("value_revision", "INT", False), Column("action", "STR", False), Column("reason", "STR", False),
        Column("detected_at_utc", "TS", False), Column("evidence_refs", "STR", True),
    ],
    "dq_record_outcomes": [
        Column("outcome_id", "ID", False), Column("run_id", "ID", False), Column("pipeline_id", "STR", False),
        Column("dataset_version", "STR", False), Column("source_row_id", "ID", False), Column("table_name", "STR", False),
        Column("issue_ids", "STR", False), Column("original_record_ref", "STR", False), Column("corrected_record", "STR", True),
        Column("final_status", "STR", False), Column("usable_for", "STR", False), Column("canonical_business_key", "STR", True),
        Column("survivor_source_row_id", "ID", True), Column("finalized_at_utc", "TS", False), Column("value_revision", "INT", True),
        Column("value_available_at", "TS", True),
    ],
}

# Apply the dictionary's field types/nullability after the compact table
# declarations above.  Headers remain stable while the machine-readable schema
# carries useful type semantics for later Spark ingestion.
FIELD_TYPES = {
    "passenger_id": "ID", "ticket_id": "ID", "route_id": "ID", "stop_id": "ID", "pattern_id": "ID",
    "route_stop_id": "ID", "trip_id": "ID", "schedule_id": "ID", "vehicle_id": "ID", "service_id": "ID",
    "schedule_stop_time_id": "ID", "stop_event_id": "ID", "assignment_id": "ID", "count_id": "ID",
    "delay_id": "ID", "gps_event_id": "ID", "exception_id": "ID", "journey_id": "ID", "request_id": "ID",
    "context_event_id": "ID", "transfer_id": "ID", "operational_departure_id": "ID", "predecessor_trip_id": "ID",
    "planned_vehicle_id": "ID", "arrival_assignment_id": "ID", "departure_assignment_id": "ID", "assignment_id_ref": "ID",
    "from_assignment_id": "ID", "to_assignment_id": "ID", "ticket_id_ref": "ID",
    "registered_at_utc": "TS", "issued_at_utc": "TS", "scheduled_start_utc": "TS", "scheduled_end_utc": "TS",
    "published_at_utc": "TS", "actual_start_utc": "TS", "actual_end_utc": "TS", "outcome_available_at_utc": "TS",
    "effective_start_utc": "TS", "effective_end_utc": "TS", "announced_at_utc": "TS", "actual_arrival_utc": "TS",
    "actual_departure_utc": "TS", "counted_at_utc": "TS", "recorded_at_utc": "TS", "observed_at_utc": "TS",
    "requested_at_utc": "TS", "request_available_at_utc": "TS", "desired_departure_utc": "TS", "decision_at_utc": "TS",
    "resolution_available_at_utc": "TS", "starts_at_utc": "TS", "ends_at_utc": "TS", "transfer_at_utc": "TS",
    "effective_from": "TS", "effective_to": "TS", "correction_time": "TS", "event_time": "TS", "value_available_at": "TS",
    "valid_from": "DATE", "valid_to": "DATE", "opened_on": "DATE", "closed_on": "DATE", "commissioned_on": "DATE",
    "retired_on": "DATE", "service_date": "DATE", "exception_date": "DATE", "arrival_offset_sec": "INT",
    "departure_offset_sec": "INT", "stop_sequence": "INT", "direction_id": "INT", "pattern_version": "INT",
    "plan_version": "INT", "instance_index": "INT", "seated_capacity": "INT", "standing_capacity": "INT",
    "nominal_capacity": "INT", "capacity_snapshot": "INT", "boardings": "INT", "alightings": "INT",
    "onboard_arrival": "INT", "onboard_departure": "INT", "transfer_out_count": "INT", "transfer_in_count": "INT",
    "arrival_delay_sec": "INT", "departure_delay_sec": "INT", "observation_index": "INT", "passenger_count": "INT",
    "row_ordinal": "INT", "issue_index": "INT", "prior_value_revision": "INT", "value_revision": "INT",
    "distance_from_start_km": "DEC(10,3)", "distance_km": "DEC(10,3)", "distance_along_pattern_km": "DEC(10,3)",
    "latitude": "DEC(10,7)", "longitude": "DEC(10,7)", "speed_kph": "DEC(7,2)", "accuracy_m": "DEC(8,2)",
    "fare_amount": "DEC(12,2)", "monday": "BOOL", "tuesday": "BOOL", "wednesday": "BOOL", "thursday": "BOOL",
    "friday": "BOOL", "saturday": "BOOL", "sunday": "BOOL", "social_service_required": "BOOL", "pickup_allowed": "BOOL",
    "dropoff_allowed": "BOOL", "wheelchair_accessible": "BOOL", "accessibility_need": "BOOL", "replacement_event": "BOOL",
    "expected_in_advance": "BOOL",
}

TABLE_NULLABLE = {
    "passengers": {"home_zone", "accessibility_need", "valid_to"},
    "routes": {"closed_on"}, "stops": {"closed_on", "wheelchair_accessible"},
    "trips": {"predecessor_trip_id", "effective_to", "planned_vehicle_id", "actual_start_utc", "actual_end_utc", "outcome_available_at_utc", "cancellation_reason"},
    "schedules": {"valid_to"}, "vehicles": {"retired_on"},
    "passenger_counts": {"arrival_assignment_id", "departure_assignment_id"},
    "delays": {"arrival_assignment_id", "departure_assignment_id", "reported_cause", "context_event_id"},
    "gps_events": {"assignment_id", "stop_event_id", "speed_kph", "accuracy_m"},
    "route_patterns": {"valid_to"}, "trip_stop_events": {"arrival_assignment_id", "departure_assignment_id", "actual_arrival_utc", "actual_departure_utc", "outcome_available_at_utc"},
    "trip_vehicle_assignments": {"capacity_reason"}, "service_exceptions": {"day_type_override", "context_event_id"},
    "passenger_journeys": {"request_id", "ticket_id"}, "demand_requests": {"preferred_route_id", "reason"},
    "context_events": {"route_id", "stop_id", "description"}, "passenger_transfer_events": {"from_assignment_id", "to_assignment_id"},
}

for _table, _columns in TABLE_COLUMNS.items():
    _nullable = TABLE_NULLABLE.get(_table, set())
    TABLE_COLUMNS[_table] = [Column(column.name, FIELD_TYPES.get(column.name, "STR"), column.name in _nullable) for column in _columns]


ALL_TABLES = tuple(TABLE_COLUMNS.keys())

# Correct physical table names used in paths/manifests.
TABLE_LABELS = {
    "service_calendar": "Service_Calendar",
    "route_patterns": "Route_Patterns",
    "schedule_stop_times": "Schedule_Stop_Times",
    "trip_stop_events": "Trip_Stop_Events",
    "trip_vehicle_assignments": "Trip_Vehicle_Assignments",
    "service_exceptions": "Service_Exceptions",
    "passenger_journeys": "Passenger_Journeys",
    "demand_requests": "Demand_Requests",
    "context_events": "Context_Events",
    "passenger_transfer_events": "Passenger_Transfer_Events",
}


def all_columns(table: str, *, audit: bool = False, raw: bool = True) -> List[Column]:
    if audit:
        return list(AUDIT_COLUMNS[table])
    base = list(COMMON_COLUMNS)
    if raw:
        base.extend(RAW_ENVELOPE_COLUMNS)
    base.extend(TABLE_COLUMNS[table])
    return base


def column_names(table: str, *, audit: bool = False, raw: bool = True) -> List[str]:
    return [column.name for column in all_columns(table, audit=audit, raw=raw)]


def schema_snapshot() -> Dict[str, object]:
    result: Dict[str, object] = {"schema_version": "2.0", "common_columns": [c.__dict__ for c in COMMON_COLUMNS], "raw_envelope_columns": [c.__dict__ for c in RAW_ENVELOPE_COLUMNS], "tables": {}}
    for table in TABLE_COLUMNS:
        result["tables"][table] = [c.__dict__ for c in all_columns(table)]
    result["audit_tables"] = {table: [c.__dict__ for c in columns] for table, columns in AUDIT_COLUMNS.items()}
    return result

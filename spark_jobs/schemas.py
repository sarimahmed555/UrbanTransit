"""Explicit Spark schemas for all approved transport tables.

The catalog mirrors the approved metadata contract but is bundled so a cluster
does not depend on local Python/Pandas files or production metadata paths.
"""

from __future__ import annotations

from typing import Any


_COMMON = (
    "dataset_version:string,source_id:string,source_row_id:string,event_time:timestamp,"
    "value_available_at:timestamp,correction_time:timestamp,ingestion_time:timestamp,"
    "quality_status:string,unresolved_reason:string,raw_file:string,row_ordinal:long,"
    "raw_bytes_sha256:string,raw_record_text:string,parse_status:string,"
)

_TABLE_COLUMNS = {
    "context_events": "context_event_id:string,event_type:string,event_name:string,scope:string,route_id:string,stop_id:string,starts_at_utc:timestamp,ends_at_utc:timestamp,announced_at_utc:timestamp,expected_in_advance:boolean,description:string",
    "delays": "delay_id:string,stop_event_id:string,trip_id:string,arrival_assignment_id:string,departure_assignment_id:string,arrival_assignment_status:string,departure_assignment_status:string,service_date:date,arrival_delay_sec:int,departure_delay_sec:int,recorded_at_utc:timestamp,reported_cause:string,context_event_id:string",
    "demand_requests": "request_id:string,passenger_id:string,origin_stop_id:string,destination_stop_id:string,preferred_route_id:string,requested_at_utc:timestamp,request_available_at_utc:timestamp,desired_departure_utc:timestamp,service_date:date,resolution:string,decision_at_utc:timestamp,reason:string,resolution_available_at_utc:timestamp",
    "gps_events": "gps_event_id:string,trip_id:string,assignment_id:string,assignment_status:string,stop_event_id:string,service_date:date,observed_at_utc:timestamp,observation_index:int,latitude:decimal(10,7),longitude:decimal(10,7),distance_along_pattern_km:decimal(10,3),speed_kph:decimal(7,2),accuracy_m:decimal(8,2)",
    "passenger_counts": "count_id:string,stop_event_id:string,trip_id:string,arrival_assignment_id:string,departure_assignment_id:string,arrival_assignment_status:string,departure_assignment_status:string,service_date:date,counted_at_utc:timestamp,boardings:int,alightings:int,onboard_arrival:int,onboard_departure:int,replacement_event:boolean,transfer_out_count:int,transfer_in_count:int,measurement_method:string",
    "passenger_journeys": "journey_id:string,passenger_id:string,trip_id:string,request_id:string,request_link_status:string,ticket_id:string,origin_route_stop_id:string,destination_route_stop_id:string,boarding_stop_event_id:string,alighting_stop_event_id:string,service_date:date,boarded_at_utc:timestamp,alighted_at_utc:timestamp,passenger_count:int,movement_status:string",
    "passenger_transfer_events": "transfer_id:string,journey_id:string,replacement_stop_event_id:string,from_assignment_id:string,to_assignment_id:string,from_assignment_status:string,to_assignment_status:string,transfer_at_utc:timestamp,transfer_type:string,transfer_status:string",
    "passengers": "passenger_id:string,registered_at_utc:timestamp,passenger_type:string,home_zone:string,accessibility_need:boolean,valid_from:date,valid_to:date",
    "route_patterns": "pattern_id:string,route_id:string,direction_id:int,pattern_version:int,distance_km:decimal(10,3),valid_from:date,valid_to:date,published_at_utc:timestamp",
    "route_stops": "route_stop_id:string,pattern_id:string,route_id:string,stop_id:string,stop_sequence:int,distance_from_start_km:decimal(10,3),pickup_allowed:boolean,dropoff_allowed:boolean",
    "routes": "route_id:string,route_code:string,route_name:string,mode:string,service_type:string,social_service_required:boolean,opened_on:date,closed_on:date,route_status:string",
    "schedule_stop_times": "schedule_stop_time_id:string,schedule_id:string,route_stop_id:string,stop_sequence:int,arrival_offset_sec:int,departure_offset_sec:int",
    "schedules": "schedule_id:string,pattern_id:string,service_id:string,departure_offset_sec:int,valid_from:date,valid_to:date,published_at_utc:timestamp,schedule_version:string",
    "service_calendar": "service_id:string,service_name:string,timezone:string,valid_from:date,valid_to:date,monday:boolean,tuesday:boolean,wednesday:boolean,thursday:boolean,friday:boolean,saturday:boolean,sunday:boolean,calendar_day_type:string,published_at_utc:timestamp",
    "service_exceptions": "exception_id:string,service_id:string,exception_date:date,action:string,day_type_override:string,context_event_id:string,published_at_utc:timestamp",
    "stops": "stop_id:string,stop_code:string,stop_name:string,latitude:decimal(10,7),longitude:decimal(10,7),zone_id:string,stop_type:string,opened_on:date,closed_on:date,wheelchair_accessible:boolean",
    "tickets": "ticket_id:string,transaction_ref:string,passenger_id:string,trip_id:string,origin_route_stop_id:string,destination_route_stop_id:string,origin_stop_id:string,destination_stop_id:string,issued_at_utc:timestamp,service_date:date,fare_amount:decimal(12,2),currency:string,fare_product:string,payment_method:string,transaction_status:string",
    "trip_stop_events": "stop_event_id:string,trip_id:string,schedule_stop_time_id:string,route_stop_id:string,stop_sequence:int,service_date:date,arrival_assignment_id:string,departure_assignment_id:string,arrival_assignment_status:string,departure_assignment_status:string,actual_arrival_utc:timestamp,actual_departure_utc:timestamp,visit_status:string,outcome_available_at_utc:timestamp",
    "trip_vehicle_assignments": "assignment_id:string,trip_id:string,vehicle_id:string,assignment_kind:string,start_stop_sequence:string,end_stop_sequence:string,capacity_snapshot:int,capacity_reason:string,effective_start_utc:timestamp,effective_end_utc:timestamp,announced_at_utc:timestamp",
    "trips": "trip_id:string,operational_departure_id:string,plan_version:int,predecessor_trip_id:string,effective_from:timestamp,effective_to:timestamp,plan_status:string,route_id:string,pattern_id:string,schedule_id:string,service_id:string,service_date:date,instance_index:int,planned_vehicle_id:string,planned_vehicle_status:string,scheduled_start_utc:timestamp,scheduled_end_utc:timestamp,published_at_utc:timestamp,trip_status:string,cancellation_reason:string,actual_start_utc:timestamp,actual_end_utc:timestamp,outcome_available_at_utc:timestamp",
    "vehicles": "vehicle_id:string,vehicle_code:string,vehicle_type:string,seated_capacity:int,standing_capacity:int,nominal_capacity:int,commissioned_on:date,retired_on:date,operational_status:string",
}


def schema_spec(table: str) -> str:
    if table not in _TABLE_COLUMNS:
        raise KeyError(f"Unknown transport table: {table}")
    return _COMMON + _TABLE_COLUMNS[table]


def schema_for(table: str) -> Any:
    """Build a StructType lazily, keeping unit tests runnable without PySpark."""
    try:
        from pyspark.sql.types import (BooleanType, DateType, DecimalType, IntegerType,
                                       LongType, StringType, StructField, StructType,
                                       TimestampType)
    except ImportError as exc:
        raise RuntimeError("PySpark is required to build Spark schemas") from exc
    types = {
        "string": StringType(), "timestamp": TimestampType(), "date": DateType(),
        "boolean": BooleanType(), "int": IntegerType(), "long": LongType(),
        "decimal(10,7)": DecimalType(10, 7), "decimal(10,3)": DecimalType(10, 3),
        "decimal(7,2)": DecimalType(7, 2), "decimal(8,2)": DecimalType(8, 2),
        "decimal(12,2)": DecimalType(12, 2),
    }
    fields = []
    items = []
    current = []
    depth = 0
    for character in schema_spec(table):
        if character == "," and depth == 0:
            items.append("".join(current))
            current = []
            continue
        current.append(character)
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
    items.append("".join(current))
    for item in items:
        name, type_name = item.split(":", 1)
        fields.append(StructField(name, types[type_name], True))
    return StructType(fields)


def raw_schema_for(table: str) -> Any:
    """Keep source timestamp lexemes intact until the DQ typed projection."""
    from pyspark.sql.types import StringType, StructField, StructType, TimestampType

    return StructType([
        StructField(field.name,
                    StringType() if isinstance(field.dataType, TimestampType) else field.dataType,
                    field.nullable, field.metadata)
        for field in schema_for(table)
    ])

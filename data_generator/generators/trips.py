"""Stable operational departures, plan versions, and trip metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from ..config import GeneratorConfig
from ..ids import entity_id, operational_departure_id, trip_id
from .common import GenerationContext, add_seconds, iso_utc, parse_utc, utc_from_local


@dataclass
class TripSpec:
    index: int
    service_date: date
    route_index: int
    direction_id: int
    pattern: dict
    schedule: dict
    operational_id: str
    instance_index: int
    slot_index: int
    revision: bool = False
    old_pattern: Optional[dict] = None
    old_schedule: Optional[dict] = None
    cancelled: bool = False
    replacement: bool = False
    early: bool = False
    delayed: bool = False
    overcrowd: bool = False
    low_demand: bool = False
    bunching: bool = False
    bottleneck: bool = False
    unknown_vehicle: bool = False
    incomplete: bool = False
    skipped_stop: bool = False
    event_id: Optional[str] = None
    service_id: str = ""
    current_trip_id: str = ""
    old_trip_id: str = ""
    boarding_budget: Optional[int] = None
    allocated_replacement_id: str = ""
    duty_envelope_start: str = ""
    duty_envelope_end: str = ""
    current_row: dict = field(default_factory=dict)
    old_row: dict = field(default_factory=dict)


def _applicable_event_id(context_events, pattern, route_stops, scheduled_start):
    """Return a deterministic event that physically applies to this trip."""
    start = parse_utc(scheduled_start)
    first_stop_id = route_stops[0]["stop_id"] if route_stops else None
    applicable = []
    for event in context_events:
        if not (parse_utc(event["starts_at_utc"]) <= start <= parse_utc(event["ends_at_utc"])):
            continue
        if event["scope"] == "ROUTE" and event.get("route_id") != pattern["route_id"]:
            continue
        if event["scope"] == "STOP" and event.get("stop_id") != first_stop_id:
            continue
        applicable.append(event["context_event_id"])
    return min(applicable) if applicable else None


def _service_dates(config: GeneratorConfig) -> List[date]:
    # Four dates per month, including each month-end boundary fixture.
    result: List[date] = []
    current = config.history_start
    while current <= config.history_end:
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_last = next_month - timedelta(days=1)
        candidates = [date(current.year, current.month, 4), date(current.year, current.month, 14), date(current.year, current.month, 24), month_last]
        for candidate in candidates:
            if current <= candidate <= config.history_end and candidate not in result:
                result.append(candidate)
        current = next_month
    return result


def _choose_pattern(network, service_date: date, route_index: int, direction: int) -> Optional[dict]:
    candidates = [route for route in network.routes if date.fromisoformat(route["opened_on"]) <= service_date]
    if not candidates:
        return None
    route = candidates[route_index % len(candidates)]
    return network.active_pattern(route["route_id"], direction, service_date)


def _build_production_trip_specs(ctx: GenerationContext, network, service, context_events) -> List[TripSpec]:
    """Construct the approved production departure envelope.

    The full fact tables are still emitted one trip at a time by the caller;
    only compact immutable trip specifications are retained here.  This branch
    is explicit and is never selected by the smoke CLI unless requested.
    """
    config = ctx.config
    target = config.target_scale.get("operational_departures", 120_000)
    dates = list(config.dates())
    specs: List[TripSpec] = []
    compatible = {}
    for day_index, service_date in enumerate(dates):
        active_ids = {key for key in service.calendar_by_id if service.calendar_active(key, service_date)}
        active_routes = [route for route in network.routes if date.fromisoformat(route["opened_on"]) <= service_date]
        if not active_routes:
            continue
        # Spread the target over every complete service day rather than
        # multiplying a single month's demand by a constant.
        daily_target = (target + len(dates) - 1) // len(dates)
        for slot in range(daily_target):
            if len(specs) >= target:
                break
            index = len(specs)
            route = active_routes[(slot * 7 + day_index * 3) % len(active_routes)]
            direction = (slot + day_index) % 2
            pattern = network.active_pattern(route["route_id"], direction, service_date)
            if pattern is None:
                continue
            key = (pattern['pattern_id'], service_date)
            if key not in compatible:
                compatible[key] = service.compatible_schedules(*key, active_ids=active_ids)
            schedules = compatible[key]
            if not schedules:
                raise RuntimeError(f'no calendar-valid template for {key}; departure cannot be dropped')
            schedule = schedules[slot % len(schedules)]
            op_id = operational_departure_id(f"prod-{service_date.isoformat()}-{slot}", service_date.isoformat(), namespace=config.identity_namespace)
            old_pattern = None
            old_schedule = None
            revision = index % 20 == 0 and direction == 0
            if revision:
                candidates = [item for item in network.patterns_by_route_direction[(route["route_id"], direction)] if item["pattern_version"] < pattern["pattern_version"]]
                old_pattern = candidates[-1] if candidates else pattern
                old_candidates = service.compatible_schedules(old_pattern["pattern_id"], service_date, historical=True, active_ids=active_ids)
                old_schedule = old_candidates[slot % len(old_candidates)] if old_candidates else schedule
            scheduled_start = utc_from_local(service_date, int(schedule["departure_offset_sec"]))
            route_stops = network.route_stops_by_pattern[pattern["pattern_id"]]
            spec = TripSpec(
                index=index, service_date=service_date, route_index=network.routes.index(route), direction_id=direction,
                pattern=pattern, schedule=schedule, operational_id=op_id, instance_index=slot, slot_index=slot,
                revision=revision, old_pattern=old_pattern, old_schedule=old_schedule,
                cancelled=index % 50 == 49, replacement=index % 25 == 2, early=index % 17 == 3,
                delayed=index % 4 == 0, overcrowd=index % 19 == 5, low_demand=index % 10 == 1,
                bunching=index % 11 == 4, bottleneck=index % 13 == 7, unknown_vehicle=index == 5,
                incomplete=index == 7, skipped_stop=index == 8,
                event_id=_applicable_event_id(context_events, pattern, route_stops, scheduled_start),
                service_id=schedule["service_id"], current_trip_id=trip_id(op_id, 2 if revision else 1, namespace=config.identity_namespace),
                old_trip_id=trip_id(op_id, 1, namespace=config.identity_namespace) if revision else "",
            )
            specs.append(spec)
    ctx.evidence("production_trip_envelope", {"target_operational_departures": target, "built_specs": len(specs)})
    return specs


def build_trip_specs(ctx: GenerationContext, network, service, context_events) -> List[TripSpec]:
    config = ctx.config
    if not config.is_smoke:
        return _build_production_trip_specs(ctx, network, service, context_events)
    dates = _service_dates(config)
    specs: List[TripSpec] = []
    event_by_date = {row["starts_at_utc"][:10]: row["context_event_id"] for row in context_events}
    for date_index, service_date in enumerate(dates):
        # Two route/direction pairs and four daily slots give 288 compact
        # smoke departures across all 18 months (four dates per month).
        pair_specs = [(0, 0), (max(0, len(network.routes) // 2 - 1), 1), (1, 0), (max(1, len(network.routes) - 2), 1)]
        for slot_index, (route_seed, direction) in enumerate(pair_specs):
            route_index = (route_seed + date_index) % max(1, len(network.routes))
            pattern = _choose_pattern(network, service_date, route_index, direction)
            if pattern is None:
                # A newly opened route is not eligible before its opening date.
                pattern = _choose_pattern(network, service_date, 0, direction)
            if pattern is None:
                continue
            schedules = service.schedules_by_pattern.get(pattern["pattern_id"], [])
            if not schedules:
                continue
            event_id = event_by_date.get(service_date.isoformat())
            calendar_id = service.calendar_for_date(service_date, [event_id] if event_id else [])
            matching_schedules = [item for item in schedules if item.get("service_id") == calendar_id]
            if not matching_schedules:
                matching_schedules = schedules
            schedule = matching_schedules[slot_index % len(matching_schedules)]
            departure_token = f"{service_date.isoformat()}|{pattern['route_id']}|{direction}|{slot_index}"
            op_id = operational_departure_id(departure_token, service_date.isoformat(), namespace=config.identity_namespace)
            old_pattern = None
            old_schedule = None
            revision = False
            index = len(specs)
            cancelled = index % 29 == 11 or any(
                exception.get("exception_date") == service_date.isoformat() and exception.get("action") == "REMOVE"
                for exception in service.exceptions
            )
            # A deterministic 5% plan-version allowance, with a guaranteed
            # smoke fixture on the first eligible R001/direction-0 service and
            # a final cancelled revision fixture after the first split boundary.
            if (pattern["route_id"] == network.routes[0]["route_id"] and direction == 0 and service_date >= date(2025, 7, 1) and ((date_index + slot_index) % 5 == 0 or cancelled)) or (config.is_smoke and len(specs) == 2):
                revision = True
                old_candidates = [p for p in network.patterns_by_route_direction[(pattern["route_id"], direction)] if p["pattern_version"] < pattern["pattern_version"]]
                old_pattern = old_candidates[-1] if old_candidates else pattern
                old_schedules = service.schedules_by_pattern.get(old_pattern["pattern_id"], [])
                old_schedule = old_schedules[slot_index % len(old_schedules)] if old_schedules else schedule
            spec = TripSpec(
                index=index,
                service_date=service_date,
                route_index=route_index,
                direction_id=direction,
                pattern=pattern,
                schedule=schedule,
                operational_id=op_id,
                instance_index=slot_index,
                slot_index=slot_index,
                revision=revision,
                old_pattern=old_pattern,
                old_schedule=old_schedule,
                cancelled=cancelled,
                replacement=(index % 13 == 2),
                early=(index % 17 == 3),
                delayed=(index % 4 == 0),
                overcrowd=(index % 19 == 5),
                low_demand=(index % 10 == 1),
                bunching=(index % 11 == 4),
                bottleneck=(index % 13 == 7),
                unknown_vehicle=(index == 5),
                incomplete=(index == 7),
                skipped_stop=(index == 8),
                event_id=event_id,
            )
            # Ensure a trip selected on a route/date is actually served by the
            # selected recurring calendar; all synthetic schedule templates are
            # published before their first use.
            spec.service_id = schedule["service_id"]
            spec.current_trip_id = trip_id(op_id, 2 if revision else 1, namespace=config.identity_namespace)
            spec.old_trip_id = trip_id(op_id, 1, namespace=config.identity_namespace) if revision else ""
            specs.append(spec)
    # Explicitly ensure rare smoke fixtures survive any future date-list tweak.
    if config.is_smoke and specs:
        specs[0].delayed = True
        specs[1].replacement = True
        specs[2].early = True
        specs[3].overcrowd = True
        specs[4].low_demand = True
        specs[5].unknown_vehicle = True
        specs[6].incomplete = True
        specs[7].skipped_stop = True
        # Add a second replacement with an unresolved handover assignment so
        # G1 capability degradation is exercised independently of the known
        # assignment-attribution fixture above.
        unknown_replacement = next((spec for spec in specs if spec.replacement and spec.index != 1 and not spec.unknown_vehicle), None)
        if unknown_replacement is not None:
            unknown_replacement.unknown_vehicle = True
    ctx.evidence("trips", {
        "spec_count": len(specs),
        "revision_count": sum(1 for spec in specs if spec.revision),
        "cancelled_count": sum(1 for spec in specs if spec.cancelled),
        "replacement_count": sum(1 for spec in specs if spec.replacement),
        "boundary_trip_ids": [spec.operational_id for spec in specs if spec.service_date.day >= 28],
    })
    return specs


def materialize_trip_rows(ctx: GenerationContext, spec: TripSpec, network, service, vehicles) -> None:
    config = ctx.config
    current_schedule = spec.schedule
    pattern = spec.pattern
    current_route = network.route_by_id[pattern["route_id"]]
    start = utc_from_local(spec.service_date, int(current_schedule["departure_offset_sec"]))
    current_stop_times = service.stop_times_by_schedule[current_schedule["schedule_id"]]
    end = add_seconds(start, int(current_stop_times[-1]["departure_offset_sec"]))
    eligible_vehicles = [
        vehicle for vehicle in vehicles
        if vehicle.get("operational_status") == "AVAILABLE"
        and date.fromisoformat(vehicle["commissioned_on"]) <= spec.service_date
        and (vehicle.get("retired_on") is None or spec.service_date < date.fromisoformat(vehicle["retired_on"]))
    ]
    if not eligible_vehicles:
        raise RuntimeError(f"no eligible vehicle for {spec.service_date}")
    planned_vehicle = eligible_vehicles[(spec.index * 3 + spec.slot_index) % len(eligible_vehicles)]["vehicle_id"]
    if not config.is_smoke:
        from .vehicle_duties import reserve_trip
        planned_vehicle = reserve_trip(ctx, spec, network, service, vehicles)
    pattern_start = utc_from_local(date.fromisoformat(pattern["valid_from"]), 0)
    current_effective = max(current_schedule["published_at_utc"], pattern_start)
    if spec.revision:
        # Publish the revision before its effective boundary; never backdate a
        # correction to the service event.
        current_effective = utc_from_local(spec.service_date - timedelta(days=1), 0)
    current_published = add_seconds(current_effective, -60) if spec.revision else add_seconds(current_effective, 60)
    current_event = current_published
    row = ctx.base("trips", current_event, ingestion_time=add_seconds(current_effective, 120))
    row.update({
        "trip_id": spec.current_trip_id,
        "operational_departure_id": spec.operational_id,
        "plan_version": 2 if spec.revision else 1,
        "predecessor_trip_id": spec.old_trip_id if spec.revision else None,
        "effective_from": current_effective,
        "effective_to": None,
        "plan_status": "CURRENT",
        "route_id": current_route["route_id"],
        "pattern_id": pattern["pattern_id"],
        "schedule_id": current_schedule["schedule_id"],
        "service_id": spec.service_id,
        "service_date": spec.service_date.isoformat(),
        "instance_index": spec.instance_index,
        "planned_vehicle_id": planned_vehicle,
        "planned_vehicle_status": "KNOWN",
        "scheduled_start_utc": start,
        "scheduled_end_utc": end,
        "published_at_utc": current_published,
        "trip_status": "CANCELLED" if spec.cancelled else "COMPLETED",
        "cancellation_reason": "SYNTHETIC_DISRUPTION" if spec.cancelled else None,
        "actual_start_utc": None,
        "actual_end_utc": None,
        "outcome_available_at_utc": None,
    })
    spec.current_row = row
    if spec.revision:
        old_schedule = spec.old_schedule or current_schedule
        old_pattern = spec.old_pattern or pattern
        old_route = network.route_by_id[old_pattern["route_id"]]
        old_start = utc_from_local(spec.service_date, int(old_schedule["departure_offset_sec"]))
        old_times = service.stop_times_by_schedule[old_schedule["schedule_id"]]
        old_end = add_seconds(old_start, int(old_times[-1]["departure_offset_sec"]))
        old_pattern_start = utc_from_local(date.fromisoformat(old_pattern["valid_from"]), 0)
        old_effective = max(old_schedule["published_at_utc"], old_pattern_start)
        old_row = ctx.base("trips", old_effective, ingestion_time=add_seconds(old_effective, 60))
        old_row.update({
            "trip_id": spec.old_trip_id,
            "operational_departure_id": spec.operational_id,
            "plan_version": 1,
            "predecessor_trip_id": None,
            "effective_from": old_effective,
            "effective_to": current_effective,
            "plan_status": "SUPERSEDED",
            "route_id": old_route["route_id"],
            "pattern_id": old_pattern["pattern_id"],
            "schedule_id": old_schedule["schedule_id"],
            "service_id": old_schedule["service_id"],
            "service_date": spec.service_date.isoformat(),
            "instance_index": spec.instance_index,
            "planned_vehicle_id": planned_vehicle,
            "planned_vehicle_status": "KNOWN",
            "scheduled_start_utc": old_start,
            "scheduled_end_utc": old_end,
            "published_at_utc": add_seconds(old_effective, 30),
            "trip_status": "SCHEDULED",
            "cancellation_reason": None,
            "actual_start_utc": None,
            "actual_end_utc": None,
            "outcome_available_at_utc": None,
        })
        spec.old_row = old_row


def planned_assignment_rows(ctx: GenerationContext, spec: TripSpec, vehicles, network=None) -> List[dict]:
    """Return immutable planned duty rows for callers outside the main pipeline."""
    rows: List[dict] = []
    vehicle_by_id = {row["vehicle_id"]: row for row in vehicles}
    for trip_row, kind in ((spec.old_row, "OLD"), (spec.current_row, "CURRENT")):
        if not trip_row:
            continue
        vehicle_id = trip_row["planned_vehicle_id"]
        vehicle = vehicle_by_id[vehicle_id]
        start = trip_row["scheduled_start_utc"]
        end = trip_row["scheduled_end_utc"]
        stop_count = len(network.route_stops_by_pattern[trip_row["pattern_id"]]) if network is not None else 5
        assignment_id = entity_id("Assignment", {"trip_id": trip_row["trip_id"], "kind": "PLANNED", "segment": 1}, namespace=ctx.config.identity_namespace)
        announced = add_seconds(start, -3600)
        row = ctx.base("trip_vehicle_assignments", announced, ingestion_time=add_seconds(announced, 30))
        row.update({
            "assignment_id": assignment_id,
            "trip_id": trip_row["trip_id"],
            "vehicle_id": vehicle_id,
            "assignment_kind": "PLANNED",
            "start_stop_sequence": 1,
            "end_stop_sequence": stop_count,
            "capacity_snapshot": vehicle["nominal_capacity"],
            "capacity_reason": None,
            "effective_start_utc": start,
            "effective_end_utc": end,
            "announced_at_utc": announced,
        })
        rows.append(row)
    return rows


def split_for_date(config: GeneratorConfig, service_date: date) -> str:
    return config.split_for(service_date)

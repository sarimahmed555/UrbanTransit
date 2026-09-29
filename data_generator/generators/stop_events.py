"""Coherent operational simulation for trips, stops, counts, and assignments.

The simulation emits small per-trip bundles.  Callers can therefore stream
large fact tables without retaining millions of Python objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from ..ids import entity_id
from .common import GenerationContext, add_seconds, iso_utc, max_timestamps, parse_utc, refresh_availability, utc_from_local
from .delays import positive_delay_components
from .passenger_counts import validate_ordinary_count
from .trips import TripSpec


@dataclass
class TripBundle:
    trip_rows: List[dict] = field(default_factory=list)
    planned_assignments: List[dict] = field(default_factory=list)
    actual_assignments: List[dict] = field(default_factory=list)
    stop_events: List[dict] = field(default_factory=list)
    counts: List[dict] = field(default_factory=list)
    delays: List[dict] = field(default_factory=list)
    gps_events: List[dict] = field(default_factory=list)
    journeys: List[dict] = field(default_factory=list)
    tickets: List[dict] = field(default_factory=list)
    requests: List[dict] = field(default_factory=list)
    transfers: List[dict] = field(default_factory=list)
    special_fixtures: Dict[str, str] = field(default_factory=dict)


class PassengerAllocator:
    """Small deterministic allocator that avoids overlapping smoke rides."""

    def __init__(self, passenger_ids: List[str], busy_intervals: Optional[Dict[str, List[Tuple[str, str]]]] = None):
        self.passenger_ids = passenger_ids
        self.busy_intervals: Dict[str, List[Tuple[str, str]]] = busy_intervals if busy_intervals is not None else {}
        self.cursor = 0

    def choose(self, not_before: str, not_before_end: str) -> str:
        start = parse_utc(not_before)
        end = parse_utc(not_before_end)
        for _ in range(len(self.passenger_ids)):
            passenger_id = self.passenger_ids[self.cursor % len(self.passenger_ids)]
            self.cursor += 1
            intervals = self.busy_intervals.get(passenger_id, [])
            overlaps = any(start < parse_utc(existing_end) and parse_utc(existing_start) < end for existing_start, existing_end in intervals)
            if not overlaps:
                # Old intervals are no longer relevant once their end is before
                # this candidate's start; retaining only active/future windows
                # keeps the production map bounded.
                self.busy_intervals[passenger_id] = [
                    (existing_start, existing_end) for existing_start, existing_end in intervals
                    if parse_utc(existing_end) > start
                ] + [(not_before, not_before_end)]
                return passenger_id
        raise RuntimeError("synthetic passenger pool exhausted while preserving non-overlap")


def _context_event_applies(ctx: GenerationContext, spec: TripSpec, when: str,
                            route_id: Optional[str] = None, stop_id: Optional[str] = None) -> Optional[dict]:
    if not spec.event_id:
        return None
    event = ctx.scenario_evidence.get("context_event_map", {}).get(spec.event_id)
    if not event:
        return None
    observed = parse_utc(when)
    if observed < parse_utc(event["starts_at_utc"]) or observed > parse_utc(event["ends_at_utc"]):
        return None
    if event.get("scope") == "ROUTE" and route_id and event.get("route_id") != route_id:
        return None
    if event.get("scope") == "STOP" and stop_id and event.get("stop_id") != stop_id:
        return None
    return event


def _assignment_id(ctx: GenerationContext, trip_id_value: str, kind: str, segment: int) -> str:
    return entity_id("Assignment", {"trip_id": trip_id_value, "kind": kind, "segment": segment}, namespace=ctx.config.identity_namespace)


def _stop_event_id(ctx: GenerationContext, trip_id_value: str, sequence: int) -> str:
    return entity_id("TripStopEvent", {"trip_id": trip_id_value, "stop_sequence": sequence}, namespace=ctx.config.identity_namespace)


def _status_for_phase(known: bool, occurred: bool = True) -> str:
    if not occurred:
        return "NOT_APPLICABLE"
    return "KNOWN" if known else "UNKNOWN"


def _planned_only_events(ctx: GenerationContext, trip_row: dict, route_stop_rows: List[dict], schedule, service) -> List[dict]:
    if not trip_row:
        return []
    stop_times = service.stop_times_by_schedule[schedule["schedule_id"]]
    events: List[dict] = []
    for stop_time in stop_times:
        sequence = int(stop_time["stop_sequence"])
        route_stop = next(item for item in route_stop_rows if item["stop_sequence"] == sequence)
        event_time = trip_row["published_at_utc"]
        row = ctx.base("trip_stop_events", event_time, ingestion_time=add_seconds(event_time, 5))
        row.update({
            "stop_event_id": _stop_event_id(ctx, trip_row["trip_id"], sequence),
            "trip_id": trip_row["trip_id"],
            "schedule_stop_time_id": stop_time["schedule_stop_time_id"],
            "route_stop_id": route_stop["route_stop_id"],
            "stop_sequence": sequence,
            "service_date": trip_row["service_date"],
            "arrival_assignment_id": None,
            "departure_assignment_id": None,
            "arrival_assignment_status": "NOT_APPLICABLE",
            "departure_assignment_status": "NOT_APPLICABLE",
            "actual_arrival_utc": None,
            "actual_departure_utc": None,
            "visit_status": "SCHEDULED_ONLY",
            "outcome_available_at_utc": None,
        })
        events.append(row)
    return events


def _stop_coordinates(network, route_stop_rows: List[dict], sequence: int) -> Tuple[float, float, float]:
    route_stop = next(row for row in route_stop_rows if int(row["stop_sequence"]) == sequence)
    stop = network.stop_by_id[route_stop["stop_id"]]
    return float(stop["latitude"]), float(stop["longitude"]), float(route_stop["distance_from_start_km"])


def _choose_destination(ctx: GenerationContext, spec: TripSpec, origin_sequence: int,
                        allowed_sequences: List[int], rider_number: int,
                        replacement_sequence: Optional[int] = None) -> int:
    later = [sequence for sequence in allowed_sequences if sequence > origin_sequence]
    if not later:
        return allowed_sequences[-1] if allowed_sequences else origin_sequence
    if replacement_sequence is not None and origin_sequence < replacement_sequence:
        continuing = [sequence for sequence in later if sequence > replacement_sequence]
        if continuing:
            # Replacement smoke fixtures intentionally carry a visible group of
            # continuing riders; this also makes the transfer ledger reconcile.
            return continuing[rider_number % len(continuing)]
    # A deterministic triangular distribution favors medium-length rides.
    if rider_number % 4 == 0 and len(later) > 1:
        return later[min(1, len(later) - 1)]
    return later[ctx.index(len(later), spec.operational_id, origin_sequence, rider_number)]


def _build_actual_times(ctx: GenerationContext, spec: TripSpec, start: str, stop_times: List[dict],
                        route_stop_rows: List[dict], route_id: Optional[str] = None,
                        first_stop_id: Optional[str] = None) -> Tuple[List[dict], Optional[str], Optional[str]]:
    """Return observed event rows plus optional replacement handover time."""

    current_delay = 0
    if spec.delayed:
        current_delay = 180 + (spec.index % 4) * 90
    elif spec.early:
        current_delay = -75
    else:
        # Most ordinary services are on time or slightly early; positive delay
        # rows are concentrated in the explicit delay/event fixtures.
        current_delay = -50
    # Context effects are applied per observed stop below, after route/stop
    # scope and event-window checks; never attach a date-only event label.
    if _context_event_applies(ctx, spec, start, route_id, first_stop_id):
        current_delay += 180
    previous_departure: Optional[str] = None
    replacement_sequence = 5 if len(stop_times) >= 6 else max(2, len(stop_times) - 2)
    handover: Optional[str] = None
    result: List[dict] = []
    for stop_time in stop_times:
        sequence = int(stop_time["stop_sequence"])
        scheduled_arrival = add_seconds(start, int(stop_time["arrival_offset_sec"]))
        scheduled_departure = add_seconds(start, int(stop_time["departure_offset_sec"]))
        if spec.skipped_stop and sequence == 2:
            result.append({"stop_time": stop_time, "status": "SKIPPED", "arrival": None, "departure": None, "handover": None})
            continue
        if spec.incomplete and sequence == len(stop_times):
            arrival_deviation = current_delay
            arrival = add_seconds(scheduled_arrival, arrival_deviation)
            if previous_departure and parse_utc(arrival) < parse_utc(previous_departure) + timedelta(seconds=30):
                arrival = add_seconds(previous_departure, 30)
            result.append({"stop_time": stop_time, "status": "INCOMPLETE", "arrival": arrival, "departure": None, "handover": None})
            previous_departure = arrival
            continue
        if spec.replacement and sequence == replacement_sequence:
            arrival = add_seconds(scheduled_arrival, 300)
            departure = add_seconds(scheduled_departure, 120)
            if parse_utc(departure) <= parse_utc(arrival):
                departure = add_seconds(arrival, 90)
            handover = departure
        else:
            if sequence == 1:
                deviation = current_delay
                if spec.bunching:
                    # A valid early departure creates a measurable bunching
                    # fixture when compared with the next same-direction trip.
                    deviation -= 90
            else:
                deviation = current_delay + (sequence - 1) * (12 if spec.delayed else 0)
                if spec.early:
                    deviation = -75 + (sequence - 1) * 5
            arrival = add_seconds(scheduled_arrival, deviation)
            dwell = 25 + ((spec.index + sequence) % 4) * 10
            if spec.bottleneck and sequence == 2:
                dwell += 120
            departure = add_seconds(scheduled_departure, deviation)
            if parse_utc(departure) < parse_utc(arrival) + timedelta(seconds=dwell):
                departure = add_seconds(arrival, dwell)
        if previous_departure and parse_utc(arrival) < parse_utc(previous_departure) + timedelta(seconds=30):
            arrival = add_seconds(previous_departure, 30)
            if parse_utc(departure) < parse_utc(arrival) + timedelta(seconds=20):
                departure = add_seconds(arrival, 20)
        result.append({"stop_time": stop_time, "status": "OBSERVED", "arrival": arrival, "departure": departure, "handover": handover if sequence == replacement_sequence else None})
        previous_departure = departure
    return result, replacement_sequence if spec.replacement else None, handover


def demand_base(ctx, spec, current_row, route_stop_rows, *, record_scenarios=True,
                event_applies_override=None):
    """Shared demand formula for fact generation and row-free preflight."""
    base = 4 + (spec.index % 4)
    if spec.low_demand:
        base = 1 + (spec.index % 2)
    if spec.overcrowd:
        base = 24
    if spec.replacement:
        base = max(base, 8)
    if event_applies_override is None:
        event_at_trip = _context_event_applies(
            ctx, spec, current_row["scheduled_start_utc"],
            current_row.get("route_id"), route_stop_rows[0].get("stop_id"),
        )
    else:
        event_at_trip = spec.event_id if event_applies_override else None
    if event_at_trip:
        if ctx.config.is_smoke:
            base += 4
        else:
            # Special events model concentrated demand surges. Apply the
            # production multiplier before ordinary temporal effects.
            base = max(base + 4, int(round(base * 2.0)))
        if record_scenarios:
            ctx.scenario("event_demand_spike")
    # Seasonal, weekday/weekend, peak, direction, and isolated spike hooks are
    # deterministic and intentionally overlap rather than forming independent
    # Bernoulli labels.
    month_factor = {1: 0.88, 2: 0.92, 3: 0.98, 4: 1.05, 5: 1.10, 6: 1.15,
                    7: 1.05, 8: 1.00, 9: 1.08, 10: 1.12, 11: 0.98, 12: 0.90}.get(spec.service_date.month, 1.0)
    base = max(1, int(round(base * month_factor)))
    if spec.service_date.weekday() >= 5:
        base = max(1, int(round(base * 0.72)))
    if spec.direction_id == 1:
        base = max(1, int(round(base * 0.62)))
    if spec.slot_index in (0, 2):
        base += 2
    if spec.index % 37 == 0:
        base *= 2
        if record_scenarios:
            ctx.scenario("passenger_spikes")
    if spec.bunching and record_scenarios:
        ctx.scenario("vehicle_bunching")
    return base


def _counts_and_journeys(ctx: GenerationContext, spec: TripSpec, bundle: TripBundle, current_row: dict,
                          route_stop_rows: List[dict], observed: List[dict], replacement_sequence: Optional[int],
                          assignment_in: str, assignment_out: str, assignment_in_capacity: int,
                          assignment_out_capacity: int, unknown_stop_sequence: Optional[int],
                          passengers: List[str], allocator: Optional[PassengerAllocator] = None) -> None:
    allowed_sequences = [int(item["stop_time"]["stop_sequence"]) for item in observed if item["status"] == "OBSERVED"]
    replacement_item = observed_by_sequence(observed, replacement_sequence) if replacement_sequence is not None else None
    replacement_time = (replacement_item or {}).get("departure") or (replacement_item or {}).get("arrival")
    if spec.incomplete:
        allowed_sequences = [sequence for sequence in allowed_sequences if sequence != len(route_stop_rows)]
    if not allowed_sequences:
        return
    # Flow state is per-trip and intentionally small.  It maps each boarding to
    # its alighting sequence, allowing exact conservation and later transfer
    # ledger construction.
    riders: List[dict] = []
    all_riders: List[dict] = []
    allocator = allocator or PassengerAllocator(passengers)
    base = demand_base(ctx, spec, current_row, route_stop_rows)
    from .movement_budget import boarding_plan
    plan = boarding_plan(spec, base, allowed_sequences)
    next_rider_number = 0
    for item in observed:
        sequence = int(item["stop_time"]["stop_sequence"])
        if item["status"] != "OBSERVED":
            continue
        arriving = len(riders)
        alighting = sum(1 for rider in riders if rider["destination"] == sequence)
        boardings = plan.get(sequence, 0)
        continuing_before = arriving - alighting
        is_replacement = replacement_sequence is not None and sequence == replacement_sequence
        transfer_count = continuing_before if is_replacement else 0
        onboard_departure = arriving - alighting + boardings
        event_id = _stop_event_id(ctx, current_row["trip_id"], sequence)
        count_id = entity_id("PassengerCount", {"stop_event_id": event_id}, namespace=ctx.config.identity_namespace)
        known_phase = sequence != unknown_stop_sequence
        post_handover = replacement_sequence is not None and sequence > replacement_sequence
        arrival_assignment = assignment_out if post_handover else assignment_in
        departure_assignment = assignment_out if (is_replacement or post_handover) else assignment_in
        arrival_capacity = assignment_out_capacity if post_handover else assignment_in_capacity
        departure_capacity = assignment_out_capacity if (is_replacement or post_handover) else assignment_in_capacity
        quality = "VALID"
        unresolved = None
        if not known_phase:
            quality = "FLAGGED"
            unresolved = "UNKNOWN_VEHICLE"
        if known_phase and departure_capacity > 0 and onboard_departure > departure_capacity:
            quality = "FLAGGED"
            ctx.scenario("overcrowding")
        if known_phase and arrival_capacity > 0 and arriving > arrival_capacity:
            quality = "FLAGGED"
            ctx.scenario("overcrowding")
        count_event_time = item["departure"] or item["arrival"]
        count_row = ctx.base("passenger_counts", count_event_time, ingestion_time=add_seconds(count_event_time, 4), quality_status=quality, unresolved_reason=unresolved)
        count_row.update({
            "count_id": count_id,
            "stop_event_id": event_id,
            "trip_id": current_row["trip_id"],
            "arrival_assignment_id": arrival_assignment if known_phase else None,
            "departure_assignment_id": departure_assignment if known_phase else None,
            "arrival_assignment_status": _status_for_phase(known_phase),
            "departure_assignment_status": _status_for_phase(known_phase),
            "service_date": current_row["service_date"],
            "counted_at_utc": add_seconds(count_event_time, 4),
            "boardings": boardings,
            "alightings": alighting,
            "onboard_arrival": arriving,
            "onboard_departure": onboard_departure,
            "replacement_event": is_replacement,
            "transfer_out_count": transfer_count,
            "transfer_in_count": transfer_count,
            "measurement_method": "SIMULATED_SENSOR",
        })
        bundle.counts.append(count_row)
        if is_replacement:
            ctx.scenario("replacement")
            if transfer_count < 3:
                # The focused G1 fixture needs visible continuing riders.  Only
                # extend riders to an actually observed later stop; never create
                # a destination beyond the end of the trip or leave the audit
                # copy of the rider out of sync with the live flow state.
                future_sequences = [candidate for candidate in allowed_sequences if candidate > sequence]
                continuing_riders = [rider for rider in riders if rider["destination"] > sequence]
                needed = max(0, 3 - len(continuing_riders))
                candidates = [rider for rider in riders if rider["destination"] <= sequence]
                for offset, rider in enumerate(candidates[:needed]):
                    if not future_sequences:
                        break
                    new_destination = future_sequences[offset % len(future_sequences)]
                    rider["destination"] = new_destination
                    for audit_rider in all_riders:
                        if audit_rider.get("rider_number") == rider.get("rider_number"):
                            audit_rider["destination"] = new_destination
                            break
                # Recompute alighting/transfer state after moving a rider past
                # the handover.  The ordinary count equation remains exact.
                alighting = sum(1 for rider in riders if rider["destination"] == sequence)
                continuing_before = arriving - alighting
                transfer_count = continuing_before
                count_row["alightings"] = alighting
                count_row["transfer_out_count"] = transfer_count
                count_row["transfer_in_count"] = transfer_count
                count_row["onboard_departure"] = arriving - alighting + boardings
        valid_count, count_reason = validate_ordinary_count(
            onboard_arrival=arriving,
            alightings=alighting,
            boardings=boardings,
            onboard_departure=count_row["onboard_departure"],
            replacement_event=is_replacement,
            transfer_out_count=transfer_count,
            transfer_in_count=transfer_count,
        )
        if not valid_count:
            raise ValueError(f"generated impossible passenger count at {current_row['trip_id']}/{sequence}: {count_reason}")
        # Create ordinary boardings after the replacement transfer ledger is
        # known; transfer riders never enter this list as new customers.
        for _ in range(boardings):
            destination = _choose_destination(ctx, spec, sequence, allowed_sequences, next_rider_number, replacement_sequence)
            if destination <= sequence:
                destination = allowed_sequences[-1]
            destination_observation = observed_by_sequence(observed, destination)
            journey_end = (destination_observation or {}).get("arrival") or (destination_observation or {}).get("departure") or item["departure"] or item["arrival"]
            passenger_id = allocator.choose(item["arrival"], journey_end)
            rider = {"passenger_id": passenger_id, "origin": sequence, "destination": destination, "rider_number": next_rider_number}
            riders.append(rider)
            all_riders.append(dict(rider))
            next_rider_number += 1
        # Remove alighting riders only after their evidence has been captured.
        riders[:] = [rider for rider in riders if rider["destination"] != sequence]
    # Persist journey/ticket/request rows from the collected flow.  Reconstructing
    # from the per-trip rider list keeps counts and passenger-level evidence in
    # lockstep while retaining no global journey collection.
    # The actual rider list was reduced at each stop; create rows for all boardings
    # from the trip-local audit trail stored on the bundle.
    for rider in all_riders:
        origin = observed_by_sequence(observed, rider["origin"])
        destination = observed_by_sequence(observed, rider["destination"])
        if origin is None or destination is None or origin["status"] != "OBSERVED" or destination["status"] != "OBSERVED":
            continue
        boarded = origin["departure"] or origin["arrival"]
        alighted = destination["arrival"] or destination["departure"]
        if not boarded or not alighted or parse_utc(alighted) <= parse_utc(boarded):
            continue
        journey_natural = {"trip_id": current_row["trip_id"], "origin": rider["origin"], "rider": rider["rider_number"]}
        journey_id = entity_id("Journey", journey_natural, namespace=ctx.config.identity_namespace)
        ticket_id = entity_id("Ticket", journey_natural, namespace=ctx.config.identity_namespace)
        request_id = entity_id("DemandRequest", journey_natural, namespace=ctx.config.identity_namespace)
        missing_request = spec.index == 0 and rider["rider_number"] == 0
        missing_ticket = spec.index == 1 and rider["rider_number"] == 0
        request_status = "NOT_SUPPLIED" if missing_request else "OBSERVED"
        movement_status = "MISSING_TICKET" if missing_ticket else "OBSERVED"
        journey = ctx.base("passenger_journeys", boarded, ingestion_time=add_seconds(boarded, 8))
        journey.update({
            "journey_id": journey_id,
            "passenger_id": rider["passenger_id"],
            "trip_id": current_row["trip_id"],
            "request_id": None if missing_request else request_id,
            "request_link_status": request_status,
            "ticket_id": None if missing_ticket else ticket_id,
            "origin_route_stop_id": route_stop_rows[rider["origin"] - 1]["route_stop_id"],
            "destination_route_stop_id": route_stop_rows[rider["destination"] - 1]["route_stop_id"],
            "boarding_stop_event_id": _stop_event_id(ctx, current_row["trip_id"], rider["origin"]),
            "alighting_stop_event_id": _stop_event_id(ctx, current_row["trip_id"], rider["destination"]),
            "service_date": current_row["service_date"],
            "boarded_at_utc": boarded,
            "alighted_at_utc": alighted,
            "passenger_count": 1,
            "movement_status": movement_status,
        })
        refresh_availability(journey, event_time=alighted, available_at=add_seconds(alighted, 8), ingestion_time=add_seconds(alighted, 8))
        bundle.journeys.append(journey)
        if not missing_ticket:
            origin_stop = route_stop_rows[rider["origin"] - 1]["stop_id"]
            destination_stop = route_stop_rows[rider["destination"] - 1]["stop_id"]
            issued = add_seconds(boarded, -45)
            ticket = ctx.base("tickets", issued, ingestion_time=add_seconds(issued, 3))
            ticket.update({
                "ticket_id": ticket_id,
                "transaction_ref": f"TXN-{spec.operational_id[-12:]}-{rider['rider_number']:04d}",
                "passenger_id": rider["passenger_id"],
                "trip_id": current_row["trip_id"],
                "origin_route_stop_id": journey["origin_route_stop_id"],
                "destination_route_stop_id": journey["destination_route_stop_id"],
                "origin_stop_id": origin_stop,
                "destination_stop_id": destination_stop,
                "issued_at_utc": issued,
                "service_date": current_row["service_date"],
                "fare_amount": f"{25 + (rider['rider_number'] % 4) * 5:.2f}",
                "currency": "PKR",
                "fare_product": "SINGLE",
                "payment_method": "MOBILE" if rider["rider_number"] % 2 else "CARD",
                "transaction_status": "VALID",
            })
            bundle.tickets.append(ticket)
        if not missing_request:
            requested = add_seconds(boarded, -600)
            request = ctx.base("demand_requests", requested, ingestion_time=add_seconds(requested, 2))
            request.update({
                "request_id": request_id,
                "passenger_id": rider["passenger_id"],
                "origin_stop_id": route_stop_rows[rider["origin"] - 1]["stop_id"],
                "destination_stop_id": route_stop_rows[rider["destination"] - 1]["stop_id"],
                "preferred_route_id": current_row["route_id"],
                "requested_at_utc": requested,
                "request_available_at_utc": add_seconds(requested, 1),
                "desired_departure_utc": boarded,
                "service_date": current_row["service_date"],
                "resolution": "SERVED",
                "decision_at_utc": boarded,
                "reason": None,
                "resolution_available_at_utc": add_seconds(boarded, 8),
            })
            refresh_availability(request, event_time=boarded, available_at=add_seconds(boarded, 8), ingestion_time=add_seconds(boarded, 8))
            bundle.requests.append(request)
        if replacement_sequence is not None and rider["origin"] < replacement_sequence < rider["destination"]:
            transfer_known = unknown_stop_sequence != replacement_sequence
            transfer = ctx.base("passenger_transfer_events", replacement_time or origin["departure"] or origin["arrival"], ingestion_time=add_seconds(replacement_time or origin["departure"] or origin["arrival"], 9))
            transfer.update({
                "transfer_id": entity_id("Transfer", {"journey_id": journey_id, "replacement_sequence": replacement_sequence}, namespace=ctx.config.identity_namespace),
                "journey_id": journey_id,
                "replacement_stop_event_id": _stop_event_id(ctx, current_row["trip_id"], replacement_sequence),
                "from_assignment_id": assignment_in if transfer_known else None,
                "to_assignment_id": assignment_out if transfer_known else None,
                "from_assignment_status": "KNOWN" if transfer_known else "UNKNOWN",
                "to_assignment_status": "KNOWN" if transfer_known else "UNKNOWN",
                "transfer_at_utc": replacement_time or origin["departure"] or origin["arrival"],
                "transfer_type": "VEHICLE_REPLACEMENT",
                "transfer_status": "OBSERVED",
            })
            bundle.transfers.append(transfer)
    if spec.index == 0 and bundle.journeys:
        bundle.special_fixtures["missing_request_journey_id"] = bundle.journeys[0]["journey_id"]
    if spec.index == 1 and bundle.journeys:
        bundle.special_fixtures["missing_ticket_journey_id"] = bundle.journeys[0]["journey_id"]


def observed_by_sequence(observed: List[dict], sequence: int) -> Optional[dict]:
    for item in observed:
        if int(item["stop_time"]["stop_sequence"]) == sequence:
            return item
    return None


def _allocate_passenger(passenger_ids: List[str], rider_number: int, start: str, end: str) -> str:
    # This helper is replaced by the allocator closure installed by simulate_trip.
    return passenger_ids[rider_number % len(passenger_ids)]


def simulate_trip(ctx: GenerationContext, spec: TripSpec, network, service, vehicles, passenger_ids: List[str]) -> TripBundle:
    bundle = TripBundle()
    current = spec.current_row
    old = spec.old_row
    pattern = spec.pattern
    route_stop_rows = network.route_stops_by_pattern[pattern["pattern_id"]]
    # Route-stop rows are passed directly to the local simulation helpers.
    if old:
        old_pattern = spec.old_pattern or pattern
        old_route_stops = network.route_stops_by_pattern[old_pattern["pattern_id"]]
        bundle.stop_events.extend(_planned_only_events(ctx, old, old_route_stops, spec.old_schedule or spec.schedule, service))
    bundle.trip_rows.extend([row for row in (old, current) if row])
    bundle.planned_assignments.extend(_planned_assignments_for_rows(ctx, old, current, network, vehicles))

    if spec.cancelled:
        for stop_time in service.stop_times_by_schedule[spec.schedule["schedule_id"]]:
            sequence = int(stop_time["stop_sequence"])
            route_stop = route_stop_rows[sequence - 1]
            event_time = add_seconds(current["scheduled_start_utc"], int(stop_time["arrival_offset_sec"]))
            event = ctx.base("trip_stop_events", event_time, ingestion_time=add_seconds(event_time, 5), quality_status="VALID")
            event.update({
                "stop_event_id": _stop_event_id(ctx, current["trip_id"], sequence),
                "trip_id": current["trip_id"],
                "schedule_stop_time_id": stop_time["schedule_stop_time_id"],
                "route_stop_id": route_stop["route_stop_id"],
                "stop_sequence": sequence,
                "service_date": current["service_date"],
                "arrival_assignment_id": None,
                "departure_assignment_id": None,
                "arrival_assignment_status": "NOT_APPLICABLE",
                "departure_assignment_status": "NOT_APPLICABLE",
                "actual_arrival_utc": None,
                "actual_departure_utc": None,
                "visit_status": "CANCELLED",
                "outcome_available_at_utc": add_seconds(event_time, 10),
            })
            bundle.stop_events.append(event)
        ctx.scenario("cancellations")
        return bundle

    stop_times = service.stop_times_by_schedule[spec.schedule["schedule_id"]]
    observed, replacement_sequence, handover = _build_actual_times(
        ctx, spec, current["scheduled_start_utc"], stop_times, route_stop_rows,
        current.get("route_id"), route_stop_rows[0].get("stop_id") if route_stop_rows else None,
    )
    incoming_vehicle = next(row for row in vehicles if row["vehicle_id"] == current["planned_vehicle_id"])
    if spec.replacement:
        eligible_replacements = [
            vehicle for vehicle in vehicles
            if vehicle.get("operational_status") == "AVAILABLE"
            and vehicle["vehicle_id"] != incoming_vehicle["vehicle_id"]
            and vehicle.get("nominal_capacity") != incoming_vehicle.get("nominal_capacity")
            and date.fromisoformat(vehicle["commissioned_on"]) <= spec.service_date
            and (vehicle.get("retired_on") is None or spec.service_date < date.fromisoformat(vehicle["retired_on"]))
        ]
        if not eligible_replacements:
            raise RuntimeError(f"no eligible replacement vehicle for {spec.service_date}")
        if not ctx.config.is_smoke:
            outgoing_vehicle = next(vehicle for vehicle in eligible_replacements
                                    if vehicle["vehicle_id"] == spec.allocated_replacement_id)
        else:
            outgoing_vehicle = eligible_replacements[(spec.index + 1) % len(eligible_replacements)]
    else:
        outgoing_vehicle = incoming_vehicle
    # Actual duty segments are immutable snapshots.  A replacement has a
    # contiguous handover boundary; an ordinary trip has one duty.
    actual_start = next((item["arrival"] for item in observed if item["arrival"]), current["scheduled_start_utc"])
    actual_end = next((item["departure"] for item in reversed(observed) if item["departure"]), current["scheduled_end_utc"])
    if not ctx.config.is_smoke:
        timestamps = [value for item in observed for value in (item['arrival'], item['departure']) if value]
        if min(timestamps) < spec.duty_envelope_start or max(timestamps) > spec.duty_envelope_end:
            raise RuntimeError('actual timing exceeds reserved vehicle envelope; rerun production preflight')
    incoming_assignment = _assignment_id(ctx, current["trip_id"], "ACTUAL", 1)
    incoming_announced = add_seconds(actual_start, -1800)
    incoming_row = ctx.base("trip_vehicle_assignments", incoming_announced, ingestion_time=add_seconds(incoming_announced, 30))
    incoming_row.update({
        "assignment_id": incoming_assignment,
        "trip_id": current["trip_id"],
        "vehicle_id": incoming_vehicle["vehicle_id"],
        "assignment_kind": "ACTUAL",
        "start_stop_sequence": 1,
        "end_stop_sequence": replacement_sequence or len(route_stop_rows),
        "capacity_snapshot": incoming_vehicle["nominal_capacity"],
        "capacity_reason": None,
        "effective_start_utc": actual_start,
        "effective_end_utc": handover or actual_end,
        "announced_at_utc": add_seconds(actual_start, -1800),
    })
    bundle.actual_assignments.append(incoming_row)
    outgoing_assignment = incoming_assignment
    outgoing_capacity = incoming_vehicle["nominal_capacity"]
    if spec.replacement and handover:
        outgoing_assignment = _assignment_id(ctx, current["trip_id"], "ACTUAL", 2)
        outgoing_capacity = outgoing_vehicle["nominal_capacity"]
        outgoing_announced = add_seconds(handover, -900)
        outgoing_row = ctx.base("trip_vehicle_assignments", outgoing_announced, ingestion_time=add_seconds(outgoing_announced, 30))
        outgoing_row.update({
            "assignment_id": outgoing_assignment,
            "trip_id": current["trip_id"],
            "vehicle_id": outgoing_vehicle["vehicle_id"],
            "assignment_kind": "ACTUAL",
            "start_stop_sequence": replacement_sequence,
            "end_stop_sequence": len(route_stop_rows),
            "capacity_snapshot": outgoing_capacity,
            "capacity_reason": "VEHICLE_REPLACEMENT",
            "effective_start_utc": handover,
            "effective_end_utc": actual_end,
            "announced_at_utc": add_seconds(handover, -900),
        })
        bundle.actual_assignments.append(outgoing_row)
        ctx.scenario("vehicle_changes")
    unknown_stop_sequence = (replacement_sequence if spec.replacement else 2) if spec.unknown_vehicle else None
    if replacement_sequence is not None:
        bundle.special_fixtures["replacement_stop_sequence"] = replacement_sequence
    if spec.replacement and spec.unknown_vehicle and replacement_sequence is not None:
        ctx.scenario_evidence["g1_unknown_assignment_fixture"] = {
            "trip_id": spec.current_trip_id,
            "replacement_stop_sequence": replacement_sequence,
            "unknown_assignment_sequence": unknown_stop_sequence,
            "assignment_status": "UNKNOWN",
        }
    # Add actual stop events and phase assignments.
    for item in observed:
        stop_time = item["stop_time"]
        sequence = int(stop_time["stop_sequence"])
        route_stop = route_stop_rows[sequence - 1]
        event_time = item["arrival"] or item["departure"] or add_seconds(current["scheduled_start_utc"], int(stop_time["arrival_offset_sec"]))
        status = item["status"]
        known = sequence != unknown_stop_sequence
        is_replacement = replacement_sequence is not None and sequence == replacement_sequence
        post_handover = replacement_sequence is not None and sequence > replacement_sequence
        arrival_occurred = bool(item["arrival"]) and status in {"OBSERVED", "INCOMPLETE"}
        departure_occurred = bool(item["departure"]) and status == "OBSERVED"
        phase_default = outgoing_assignment if post_handover else incoming_assignment
        arrival_assignment = phase_default if arrival_occurred and known else None
        departure_phase_assignment = outgoing_assignment if (is_replacement or post_handover) else incoming_assignment
        departure_assignment = departure_phase_assignment if departure_occurred and known else None
        arrival_status = ("KNOWN" if known else "UNKNOWN") if arrival_occurred else "NOT_APPLICABLE"
        departure_status = ("KNOWN" if known else "UNKNOWN") if departure_occurred else "NOT_APPLICABLE"
        event = ctx.base("trip_stop_events", event_time, ingestion_time=add_seconds(event_time, 5), quality_status="VALID" if known or not (arrival_occurred or departure_occurred) else "FLAGGED", unresolved_reason=None if known or not (arrival_occurred or departure_occurred) else "UNKNOWN_VEHICLE")
        event.update({
            "stop_event_id": _stop_event_id(ctx, current["trip_id"], sequence),
            "trip_id": current["trip_id"],
            "schedule_stop_time_id": stop_time["schedule_stop_time_id"],
            "route_stop_id": route_stop["route_stop_id"],
            "stop_sequence": sequence,
            "service_date": current["service_date"],
            "arrival_assignment_id": arrival_assignment,
            "departure_assignment_id": departure_assignment,
            "arrival_assignment_status": arrival_status,
            "departure_assignment_status": departure_status,
            "actual_arrival_utc": item["arrival"],
            "actual_departure_utc": item["departure"],
            "visit_status": item["status"],
            "outcome_available_at_utc": add_seconds(item["departure"] or item["arrival"] or event_time, 8),
        })
        outcome_base = item["departure"] or item["arrival"] or event_time
        event["outcome_available_at_utc"] = add_seconds(outcome_base, 8)
        refresh_availability(event, event_time=outcome_base, available_at=event["outcome_available_at_utc"], ingestion_time=event["outcome_available_at_utc"])
        bundle.stop_events.append(event)
        if item["status"] != "OBSERVED":
            if item["status"] == "SKIPPED":
                ctx.scenario("skipped_stops")
            continue
        scheduled_arrival = add_seconds(current["scheduled_start_utc"], int(stop_time["arrival_offset_sec"]))
        scheduled_departure = add_seconds(current["scheduled_start_utc"], int(stop_time["departure_offset_sec"]))
        arrival_delay, departure_delay = positive_delay_components(
            parse_utc(item["arrival"]), parse_utc(scheduled_arrival),
            parse_utc(item["departure"]), parse_utc(scheduled_departure),
        )
        if arrival_delay or departure_delay:
            context_event = _context_event_applies(ctx, spec, item["departure"] or item["arrival"], current.get("route_id"), route_stop.get("stop_id"))
            delay = ctx.base("delays", item["departure"] or item["arrival"], ingestion_time=add_seconds(item["departure"] or item["arrival"], 10), quality_status="VALID" if known else "FLAGGED", unresolved_reason=None if known else "UNKNOWN_VEHICLE")
            delay.update({
                "delay_id": entity_id("Delay", {"stop_event_id": event["stop_event_id"]}, namespace=ctx.config.identity_namespace),
                "stop_event_id": event["stop_event_id"],
                "trip_id": current["trip_id"],
                "arrival_assignment_id": arrival_assignment,
                "departure_assignment_id": departure_assignment,
                "arrival_assignment_status": _status_for_phase(known),
                "departure_assignment_status": _status_for_phase(known),
                "service_date": current["service_date"],
                "arrival_delay_sec": arrival_delay,
                "departure_delay_sec": departure_delay,
                "recorded_at_utc": add_seconds(item["departure"] or item["arrival"], 10),
                "reported_cause": "SPECIAL_EVENT" if context_event else ("CONGESTION" if spec.delayed else "TRAFFIC_VARIATION"),
                "context_event_id": context_event.get("context_event_id") if context_event else None,
            })
            bundle.delays.append(delay)
            ctx.scenario("delays")
    # Generate the passenger flow and the linked journey/ticket/request streams.
    # A local allocator avoids overlaps without a global journey object store.
    allocator = PassengerAllocator(passenger_ids, ctx.passenger_busy_intervals)
    if not ctx.config.is_smoke:
        allocator.cursor = ctx.passenger_cursor
    _counts_and_journeys(ctx, spec, bundle, current, route_stop_rows, observed, replacement_sequence,
                          incoming_assignment, outgoing_assignment, incoming_vehicle["nominal_capacity"],
                          outgoing_capacity, unknown_stop_sequence, passenger_ids, allocator)
    if not ctx.config.is_smoke:
        ctx.passenger_cursor = allocator.cursor % len(passenger_ids)
    # Add GPS anchors after actual timing/assignments are known.
    _add_gps(ctx, spec, bundle, network, route_stop_rows, observed, incoming_assignment, outgoing_assignment, unknown_stop_sequence)
    observed_events = [item for item in observed if item["status"] == "OBSERVED"]
    if observed_events:
        current["actual_start_utc"] = observed_events[0]["arrival"]
        current["actual_end_utc"] = None if spec.incomplete else observed_events[-1]["departure"]
    else:
        current["actual_start_utc"] = actual_start
        current["actual_end_utc"] = None if spec.incomplete else actual_end
    current["trip_status"] = "PARTIAL" if spec.incomplete else "COMPLETED"
    outcome_base = current["actual_end_utc"] or current["actual_start_utc"] or current["scheduled_start_utc"]
    current["outcome_available_at_utc"] = add_seconds(outcome_base, 30)
    refresh_availability(current, event_time=current["actual_end_utc"] or current["actual_start_utc"] or current["scheduled_start_utc"], available_at=current["outcome_available_at_utc"], ingestion_time=current["outcome_available_at_utc"])
    if spec.early:
        ctx.scenario("early_arrivals")
    if spec.overcrowd:
        ctx.scenario("overcrowding")
    if spec.low_demand:
        ctx.scenario("low_demand")
    if spec.bunching:
        ctx.scenario("irregular_headways")
    if spec.bottleneck:
        ctx.scenario("stop_bottlenecks")
    return bundle


def _planned_assignments_for_rows(ctx: GenerationContext, old_row: Optional[dict], current_row: dict, network, vehicles) -> List[dict]:
    result: List[dict] = []
    vehicle_by_id = {row["vehicle_id"]: row for row in vehicles}
    for trip_row in (old_row, current_row):
        if not trip_row:
            continue
        pattern_id = trip_row["pattern_id"]
        stop_count = len(network.route_stops_by_pattern[pattern_id])
        vehicle_id = trip_row["planned_vehicle_id"]
        capacity = vehicle_by_id[vehicle_id]["nominal_capacity"]
        assignment_id = _assignment_id(ctx, trip_row["trip_id"], "PLANNED", 1)
        announced = add_seconds(trip_row["scheduled_start_utc"], -3600)
        row = ctx.base("trip_vehicle_assignments", announced, ingestion_time=add_seconds(announced, 30))
        row.update({
            "assignment_id": assignment_id,
            "trip_id": trip_row["trip_id"],
            "vehicle_id": vehicle_id,
            "assignment_kind": "PLANNED",
            "start_stop_sequence": 1,
            "end_stop_sequence": stop_count,
            "capacity_snapshot": capacity,
            "capacity_reason": None,
            "effective_start_utc": trip_row["scheduled_start_utc"],
            "effective_end_utc": trip_row["scheduled_end_utc"],
            "announced_at_utc": add_seconds(trip_row["scheduled_start_utc"], -3600),
        })
        result.append(row)
    return result


def _add_gps(ctx: GenerationContext, spec: TripSpec, bundle: TripBundle, network, route_stop_rows: List[dict],
             observed: List[dict], incoming_assignment: str, outgoing_assignment: str,
             unknown_stop_sequence: Optional[int]) -> None:
    observed_events = [item for item in observed if item["status"] == "OBSERVED"]
    if not observed_events:
        return
    phase_events = {row["stop_sequence"]: row for row in bundle.stop_events
                    if row["trip_id"] == spec.current_trip_id}
    anchors = [observed_events[0], observed_events[len(observed_events) // 2], observed_events[-1]]
    for index, item in enumerate(anchors):
        sequence = int(item["stop_time"]["stop_sequence"])
        lat, lon, distance = _stop_coordinates(network, route_stop_rows, sequence)
        observed_at = item["arrival"] if index == 0 else (item["departure"] or item["arrival"])
        assignment = phase_events[sequence]["arrival_assignment_id" if index == 0 else "departure_assignment_id"]
        known = sequence != unknown_stop_sequence
        gps_id = entity_id("GpsEvent", {"trip_id": spec.current_trip_id, "index": index, "kind": "anchor"}, namespace=ctx.config.identity_namespace)
        row = ctx.base("gps_events", observed_at, ingestion_time=add_seconds(observed_at, 3), quality_status="VALID" if known else "FLAGGED", unresolved_reason=None if known else "UNKNOWN_VEHICLE")
        row.update({
            "gps_event_id": gps_id,
            "trip_id": spec.current_trip_id,
            "assignment_id": assignment if known else None,
            "assignment_status": "KNOWN" if known else "UNKNOWN",
            "stop_event_id": _stop_event_id(ctx, spec.current_trip_id, sequence) if index in (0, 2) else None,
            "service_date": spec.service_date.isoformat(),
            "observed_at_utc": observed_at,
            "observation_index": index,
            "latitude": round(lat, 7),
            "longitude": round(lon, 7),
            "distance_along_pattern_km": round(distance, 3),
            "speed_kph": 24.0 + (spec.index % 5),
            "accuracy_m": 4.0 + (index % 3),
        })
        bundle.gps_events.append(row)
    if spec.replacement or spec.delayed or spec.bunching or spec.event_id:
        item = observed_events[min(2, len(observed_events) - 1)]
        sequence = int(item["stop_time"]["stop_sequence"])
        lat, lon, distance = _stop_coordinates(network, route_stop_rows, sequence)
        observed_at = item["departure"] or item["arrival"]
        known = sequence != unknown_stop_sequence
        index = 3
        row = ctx.base("gps_events", observed_at, ingestion_time=add_seconds(observed_at, 4), quality_status="VALID" if known else "FLAGGED", unresolved_reason=None if known else "UNKNOWN_VEHICLE")
        row.update({
            "gps_event_id": entity_id("GpsEvent", {"trip_id": spec.current_trip_id, "index": index, "kind": "targeted"}, namespace=ctx.config.identity_namespace),
            "trip_id": spec.current_trip_id,
            "assignment_id": phase_events[sequence]["departure_assignment_id"] if known else None,
            "assignment_status": "KNOWN" if known else "UNKNOWN",
            "stop_event_id": _stop_event_id(ctx, spec.current_trip_id, sequence),
            "service_date": spec.service_date.isoformat(),
            "observed_at_utc": observed_at,
            "observation_index": index,
            "latitude": round(lat + 0.0001, 7),
            "longitude": round(lon + 0.0001, 7),
            "distance_along_pattern_km": round(distance, 3),
            "speed_kph": 18.0 + (spec.index % 4),
            "accuracy_m": 6.0,
        })
        bundle.gps_events.append(row)
        ctx.scenario("gps_targeted_observations")
    if spec.unknown_vehicle:
        # A separate unknown-vehicle point retains trip/location evidence while
        # withholding vehicle attribution.
        item = observed_events[min(1, len(observed_events) - 1)]
        sequence = int(item["stop_time"]["stop_sequence"])
        lat, lon, distance = _stop_coordinates(network, route_stop_rows, sequence)
        observed_at = item["arrival"]
        row = ctx.base("gps_events", observed_at, ingestion_time=add_seconds(observed_at, 5), quality_status="FLAGGED", unresolved_reason="UNKNOWN_VEHICLE")
        row.update({
            "gps_event_id": entity_id("GpsEvent", {"trip_id": spec.current_trip_id, "index": 99, "kind": "unknown_vehicle"}, namespace=ctx.config.identity_namespace),
            "trip_id": spec.current_trip_id,
            "assignment_id": None,
            "assignment_status": "UNKNOWN",
            "stop_event_id": None,
            "service_date": spec.service_date.isoformat(),
            "observed_at_utc": observed_at,
            "observation_index": 99,
            "latitude": round(lat, 7),
            "longitude": round(lon, 7),
            "distance_along_pattern_km": round(distance, 3),
            "speed_kph": None,
            "accuracy_m": None,
        })
        bundle.gps_events.append(row)

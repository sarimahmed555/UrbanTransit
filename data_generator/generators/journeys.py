"""Journey-level helpers and sparse unserved-demand generation."""
from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

from ..ids import entity_id
from .common import GenerationContext, add_seconds, refresh_availability, utc_from_local


def iter_unserved_requests(ctx: GenerationContext, network, passenger_ids: List[str], service_dates: Iterable[date]) -> Iterable[dict]:
    """Emit a small, deterministic stream of intended but unresolved demand."""

    count = 0
    dates = list(service_dates)
    # Production target is approximately 100,000 unserved/abandoned requests,
    # independent of the 2.4M served journey population.  Repeated service
    # dates are cycled deterministically rather than multiplying by trip count.
    max_rows = None if ctx.config.is_smoke else 100_000
    if max_rows is not None and dates:
        # Two cases are emitted per date slot.  Cycle half as many dates so
        # the production unserved channel is exactly 100,000 rows rather than
        # accidentally doubling the documented target.
        date_slots = max_rows // 2
        dates = [dates[index % len(dates)] for index in range(date_slots)]
    elif max_rows is not None:
        dates = []
    for index, service_date in enumerate(dates):
        # Two sparse unserved/abandoned cases per month/date fixture; these are
        # independent of ticket sales and make the demand channel meaningful.
        for case in range(2):
            eligible = [stop for stop in network.stops if stop["opened_on"] <= service_date.isoformat() and (stop["closed_on"] is None or service_date.isoformat() < stop["closed_on"])]
            if not eligible:
                continue
            origin = eligible[(index * 3 + case) % len(eligible)]
            destination = eligible[(index * 3 + case + 5) % len(eligible)]
            if origin["stop_id"] == destination["stop_id"]:
                destination = eligible[(index + case + 2) % len(eligible)]
            passenger_id = passenger_ids[(index * 7 + case) % len(passenger_ids)]
            requested = utc_from_local(service_date, 8 * 3600 + case * 1800)
            row = ctx.base("demand_requests", requested, ingestion_time=add_seconds(requested, 2))
            row.update({
                "request_id": entity_id("DemandRequest", {"unserved": index, "case": case}, namespace=ctx.config.identity_namespace),
                "passenger_id": passenger_id,
                "origin_stop_id": origin["stop_id"],
                "destination_stop_id": destination["stop_id"],
                "preferred_route_id": network.routes[index % len(network.routes)]["route_id"],
                "requested_at_utc": requested,
                "request_available_at_utc": add_seconds(requested, 1),
                "desired_departure_utc": add_seconds(requested, 900),
                "service_date": service_date.isoformat(),
                "resolution": "UNSERVED" if case == 0 else "ABANDONED",
                "decision_at_utc": add_seconds(requested, 1200 if case == 0 else 2400),
                "reason": "CAPACITY" if case == 0 else "WAIT_LIMIT",
                "resolution_available_at_utc": add_seconds(requested, 1205 if case == 0 else 2405),
            })
            refresh_availability(row, event_time=row["decision_at_utc"], available_at=row["resolution_available_at_utc"], ingestion_time=row["resolution_available_at_utc"])
            count += 1
            yield row
    ctx.scenario("unserved_demand", count)


def build_unserved_requests(ctx, network, passenger_ids, service_dates) -> List[dict]:
    """Compatibility helper for small callers/tests; generation uses the iterator."""
    return list(iter_unserved_requests(ctx, network, passenger_ids, service_dates))


def canonical_movement_count(journeys: Iterable[dict], tickets: Iterable[dict]) -> Dict[str, int]:
    """Return the project movement-count view without adding ticket + journey."""

    journey_ids, journey_ticket_ids = set(), set()
    for row in journeys:
        if row.get('journey_id'):
            journey_ids.add(row['journey_id'])
        if row.get('ticket_id'):
            journey_ticket_ids.add(row['ticket_id'])
    ticket_ids = {row.get("ticket_id") for row in tickets if row.get("ticket_id")}
    overlap = len(journey_ticket_ids & ticket_ids)
    chosen_view = "Passenger_Journeys" if len(journey_ids) >= len(ticket_ids) else "Tickets"
    chosen_count = max(len(journey_ids), len(ticket_ids))
    return {
        "usable_journey_count": len(journey_ids),
        "usable_ticket_count": len(ticket_ids),
        "overlap_count": overlap,
        "chosen_view": chosen_view,
        "canonical_movement_count": chosen_count,
    }

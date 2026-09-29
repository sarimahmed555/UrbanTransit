"""Observable fictional context events used by the generator and smoke tests."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List

from ..config import GeneratorConfig
from ..ids import entity_id
from .common import GenerationContext, add_seconds, utc_from_local


def build_context_events(ctx: GenerationContext, network) -> List[dict]:
    config = ctx.config
    count = 6 if config.is_smoke else config.target_scale.get("context_events", 180)
    routes = network.routes
    stops = network.stops
    rows: List[dict] = []
    # The first six are stable smoke fixtures deliberately spread over splits.
    fixture_specs = [
        ("FESTIVAL", "Civic Festival", "ROUTE", 0, 0, "2025-07-24", 17, 22, True),
        ("SPORT", "Neighborhood Stadium Match", "STOP", 0, 5, "2025-10-24", 18, 23, True),
        ("WEATHER", "Monsoon Weather Advisory", "NETWORK", None, None, "2026-01-24", 6, 13, False),
        ("DISRUPTION", "Bridge Disruption", "ROUTE", 2, None, "2026-04-24", 7, 11, False),
        ("COMMUNITY_EVENT", "Transit Plaza Community Day", "STOP", 0, 9, "2026-05-24", 10, 16, True),
        ("FESTIVAL", "Summer Night Festival", "ROUTE", 1, None, "2026-06-24", 18, 23, True),
    ]
    for index in range(count):
        if index < len(fixture_specs):
            event_type, name, scope, route_index, stop_index, day, start_hour, end_hour, expected = fixture_specs[index]
        else:
            event_type = ("FESTIVAL", "SPORT", "WEATHER", "DISRUPTION", "COMMUNITY_EVENT")[index % 5]
            name = f"{event_type.title()} Synthetic Event {index + 1:04d}"
            scope = ("ROUTE", "STOP", "NETWORK")[index % 3]
            route_index = index % len(routes)
            stop_index = index % len(stops)
            service_day = config.history_start + timedelta(days=(index * 17) % ((config.history_end - config.history_start).days + 1))
            day = service_day.isoformat()
            start_hour = 7 + (index % 12)
            end_hour = min(23, start_hour + 3)
            expected = index % 3 != 0
        service_day = date.fromisoformat(day)
        route_id = routes[route_index % len(routes)]["route_id"] if scope == "ROUTE" else None
        stop_id = stops[stop_index % len(stops)]["stop_id"] if scope == "STOP" else None
        natural = {"event_index": index + 1, "date": day, "name": name}
        event_id = entity_id("ContextEvent", natural, namespace=config.identity_namespace)
        start = utc_from_local(service_day, start_hour * 3600)
        end = utc_from_local(service_day, end_hour * 3600)
        announced = add_seconds(start, -86400 if expected else -300)
        # The row's event_time is its publication/announcement event; the
        # future start/end fields remain the observable operating interval.
        event = ctx.base("context_events", announced, ingestion_time=add_seconds(announced, 1), value_available_at=add_seconds(announced, 1))
        event.update({
            "context_event_id": event_id,
            "event_type": event_type,
            "event_name": name,
            "scope": scope,
            "route_id": route_id,
            "stop_id": stop_id,
            "starts_at_utc": start,
            "ends_at_utc": end,
            "announced_at_utc": announced,
            "expected_in_advance": expected,
            "description": f"Synthetic observable {event_type.lower()} condition for controlled demand and disruption fixtures.",
        })
        rows.append(event)
    ctx.scenario("special_events", len(rows))
    ctx.evidence("context_event_map", {row["context_event_id"]: row for row in rows})
    ctx.evidence("context_events", {
        "count": len(rows),
        "event_ids": [row["context_event_id"] for row in rows],
        "event_types": sorted({row["event_type"] for row in rows}),
        "scoped_events": sum(1 for row in rows if row["scope"] != "NETWORK"),
    })
    return rows

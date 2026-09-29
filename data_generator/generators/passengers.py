"""Synthetic passenger dimension and private travel-profile state."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List

from ..ids import entity_id
from .common import GenerationContext, utc_from_local


@dataclass
class PassengerData:
    rows: List[dict]
    by_id: Dict[str, dict]
    profiles: Dict[str, dict]


def build_passengers(ctx: GenerationContext) -> PassengerData:
    config = ctx.config
    count = config.target_scale.get("passengers", 36 if config.is_smoke else 80_000)
    rows: List[dict] = []
    profiles: Dict[str, dict] = {}
    by_id: Dict[str, dict] = {}
    registered_base = config.history_start - timedelta(days=90)
    for index in range(count):
        natural = f"PASSENGER-{index + 1:06d}"
        passenger_id = entity_id("Passenger", natural, namespace=config.identity_namespace)
        registered = registered_base + timedelta(days=index % 45)
        passenger_type = ("STUDENT" if index % 5 == 0 else "SENIOR" if index % 11 == 0 else "ADULT")
        row = ctx.base("passengers", utc_from_local(registered, 0), ingestion_time=utc_from_local(registered, 60))
        row.update({
            "passenger_id": passenger_id,
            "registered_at_utc": utc_from_local(registered, 8 * 3600),
            "passenger_type": passenger_type,
            "home_zone": f"ZONE_{(index % 6) + 1}",
            "accessibility_need": index % 13 == 0,
            "valid_from": registered.isoformat(),
            "valid_to": None,
        })
        rows.append(row)
        by_id[passenger_id] = row
        # This is private generator state.  It is intentionally not exported
        # into transport feature rows.
        profiles[passenger_id] = {
            "weekday_factor": 0.65 + (index % 7) * 0.08,
            "weekend_factor": 0.55 + (index % 5) * 0.12,
            "peak_preference": (index % 3) / 2.0,
            "ride_distance_bias": index % 9,
        }
    ctx.evidence("passengers", {"count": len(rows), "active_first_month": len(rows)})
    return PassengerData(rows=rows, by_id=by_id, profiles=profiles)

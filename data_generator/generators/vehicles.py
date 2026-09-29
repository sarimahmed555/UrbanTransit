"""Synthetic fleet dimension generation."""
from __future__ import annotations

from datetime import date
from typing import Dict, List

from ..ids import entity_id
from .common import GenerationContext, utc_from_local


def build_vehicles(ctx: GenerationContext) -> List[dict]:
    config = ctx.config
    count = config.target_scale.get("vehicles", 10 if config.is_smoke else 320)
    rows: List[dict] = []
    for index in range(count):
        natural = f"VEHICLE-{index + 1:04d}"
        vehicle_id = entity_id("Vehicle", natural, namespace=config.identity_namespace)
        vehicle_type = ("MINIBUS" if index % 4 == 0 else "LARGE_BUS" if index % 3 == 0 else "STANDARD_BUS")
        seated = 18 + (index * 7) % 31
        standing = 0 if vehicle_type == "MINIBUS" else 15 + (index * 11) % 35
        # Vehicles are commissioned before the first service day, with a few
        # staggered additions for new service capacity.
        commissioned = config.history_start if index < max(1, count - 2) else date(2025, 7, 1)
        row = ctx.base("vehicles", utc_from_local(commissioned, 0), ingestion_time=utc_from_local(commissioned, 60))
        row.update({
            "vehicle_id": vehicle_id,
            "vehicle_code": f"V-{index + 1:04d}",
            "vehicle_type": vehicle_type,
            "seated_capacity": seated,
            "standing_capacity": standing,
            "nominal_capacity": seated + standing,
            "commissioned_on": commissioned.isoformat(),
            "retired_on": None,
            "operational_status": "AVAILABLE" if index < count - 1 else "MAINTENANCE",
        })
        rows.append(row)
    ctx.evidence("vehicles", {"count": len(rows), "capacity_min": min(row["nominal_capacity"] for row in rows), "capacity_max": max(row["nominal_capacity"] for row in rows)})
    return rows

"""Fictional route, stop, pattern, and ordered route-stop generation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
from typing import Dict, Iterable, List, Optional, Tuple

from ..config import GeneratorConfig
from ..ids import entity_id
from .common import GenerationContext, utc_from_local


@dataclass
class NetworkData:
    stops: List[dict]
    routes: List[dict]
    patterns: List[dict]
    route_stops: List[dict]
    stop_by_id: Dict[str, dict]
    route_by_id: Dict[str, dict]
    pattern_by_id: Dict[str, dict]
    route_stops_by_pattern: Dict[str, List[dict]]
    patterns_by_route_direction: Dict[Tuple[str, int], List[dict]]

    def active_pattern(self, route_id: str, direction_id: int, service_date: date) -> Optional[dict]:
        candidates = self.patterns_by_route_direction.get((route_id, direction_id), [])
        for pattern in candidates:
            start = date.fromisoformat(pattern["valid_from"])
            end = date.fromisoformat(pattern["valid_to"]) if pattern["valid_to"] else None
            if start <= service_date and (end is None or service_date < end):
                return pattern
        return None

    def stop_is_open(self, stop_id: str, service_date: date) -> bool:
        stop = self.stop_by_id[stop_id]
        start = date.fromisoformat(stop["opened_on"])
        end = date.fromisoformat(stop["closed_on"]) if stop["closed_on"] else None
        return start <= service_date and (end is None or service_date < end)


def _route_opening(index: int, config: GeneratorConfig) -> date:
    if config.is_smoke:
        if index < 4:
            return config.history_start
        if index == 4:
            return date(2025, 7, 1)
        return date(2026, 4, 1)
    if index < 100:
        return config.history_start
    if index < 110:
        return date(2025, 7, 1)
    return date(2026, 4, 1)


def _stop_opening(index: int, config: GeneratorConfig) -> date:
    if config.is_smoke:
        if index < 16:
            return config.history_start
        if index < 20:
            return date(2025, 7, 1)
        return date(2026, 4, 1)
    if index < 550:
        return config.history_start
    if index < 600:
        return date(2025, 7, 1)
    return date(2026, 4, 1)


def _select_stop_indices(route_index: int, direction: int, count: int, opening: date,
                          stops: List[dict], config: GeneratorConfig) -> List[int]:
    eligible = [index for index, stop in enumerate(stops) if date.fromisoformat(stop["opened_on"]) <= opening]
    if not eligible:
        eligible = list(range(min(5, len(stops))))
    if config.is_smoke:
        start = (route_index * 3 + direction * 2) % len(eligible)
        stride = 2
    else:
        # Spread every production pattern across the complete eligible stop
        # population. The former fixed step repeatedly selected a small subset
        # of stops, leaving valid later-opening stops unused by service.
        start = (route_index * 11 + direction * 17 + opening.toordinal()) % len(eligible)
        stride = max(1, len(eligible) // count)
    result: List[int] = []
    for step in range(count):
        # A connected synthetic path; repeated physical stops are allowed on a
        # loop, but sequence numbers remain unique.
        result.append(eligible[(start + step * stride) % len(eligible)])
    return result


def build_network(ctx: GenerationContext) -> NetworkData:
    config = ctx.config
    stop_count = config.target_scale.get("stops", 24 if config.is_smoke else 650)
    route_count = config.target_scale.get("routes", 6 if config.is_smoke else 120)
    target_patterns = config.target_scale.get("route_patterns", 13 if config.is_smoke else 320)

    stops: List[dict] = []
    for index in range(stop_count):
        natural = f"STOP-{index + 1:04d}"
        stop_id = entity_id("Stop", natural, namespace=config.identity_namespace)
        # Fictional coordinates around the declared Karachi-area extent.
        lat = 24.70 + (index % 12) * 0.012 + (index // 12) * 0.003
        lon = 66.95 + (index // 12) * 0.017 + (index % 5) * 0.004
        opened = _stop_opening(index, config)
        stop = ctx.base("stops", utc_from_local(opened, 0), ingestion_time=utc_from_local(opened, 60))
        stop.update({
            "stop_id": stop_id,
            "stop_code": f"ST-{index + 1:04d}",
            "stop_name": f"Transit Stop {index + 1:04d}",
            "latitude": round(lat, 7),
            "longitude": round(lon, 7),
            "zone_id": f"ZONE_{(index % 6) + 1}",
            "stop_type": "INTERCHANGE" if index % 11 == 0 else ("TERMINAL" if index % 13 == 0 else "LOCAL"),
            "opened_on": opened.isoformat(),
            "closed_on": None,
            "wheelchair_accessible": index % 7 != 0,
        })
        stops.append(stop)
    stop_by_id = {row["stop_id"]: row for row in stops}

    routes: List[dict] = []
    for index in range(route_count):
        natural = f"ROUTE-{index + 1:04d}"
        route_id = entity_id("Route", natural, namespace=config.identity_namespace)
        opened = _route_opening(index, config)
        service_type = ("SOCIAL" if index % 9 == 0 else "CORRIDOR" if index % 3 == 0 else "FEEDER")
        route = ctx.base("routes", utc_from_local(opened, 0), ingestion_time=utc_from_local(opened, 60))
        route.update({
            "route_id": route_id,
            "route_code": f"R{index + 1:03d}",
            "route_name": f"UrbanTransit {service_type.title()} {index + 1:03d}",
            "mode": "BUS" if index % 4 else "MINIBUS",
            "service_type": service_type,
            "social_service_required": service_type == "SOCIAL" or index % 8 == 0,
            "opened_on": opened.isoformat(),
            "closed_on": None,
            "route_status": "ACTIVE",
        })
        routes.append(route)
    route_by_id = {row["route_id"]: row for row in routes}

    # Each route/direction has a base pattern.  A bounded set receives a second
    # version, creating auditable old/new pattern snapshots.
    patterns: List[dict] = []
    route_stops: List[dict] = []
    patterns_by_route_direction: Dict[Tuple[str, int], List[dict]] = {}
    route_stops_by_pattern: Dict[str, List[dict]] = {}
    pattern_by_id: Dict[str, dict] = {}
    base_count = route_count * 2
    revision_count = max(0, target_patterns - base_count)
    revision_targets = {(index % route_count, index % 2) for index in range(revision_count)}

    for route_index, route in enumerate(routes):
        route_opening = date.fromisoformat(route["opened_on"])
        for direction in (0, 1):
            versions = [1]
            if (route_index, direction) in revision_targets and route_opening < date(2025, 7, 1):
                versions.append(2)
            for version in versions:
                valid_from = route_opening if version == 1 else date(2025, 7, 1)
                # A route opened after the revision boundary starts with its
                # current version rather than an impossible pre-opening row.
                if valid_from < route_opening:
                    valid_from = route_opening
                valid_to = None if version == 1 and len(versions) == 1 else (
                    date(2025, 7, 1) if version == 1 and valid_from < date(2025, 7, 1) else None
                )
                natural = {"route_code": route["route_code"], "direction_id": direction, "pattern_version": version}
                pattern_id = entity_id("RoutePattern", natural, namespace=config.identity_namespace)
                count = 6 if config.is_smoke else 16 + ((route_index + direction + version) % 9)
                selected = _select_stop_indices(route_index, direction, count, valid_from, stops, config)
                distances = [0.0]
                for sequence in range(1, count):
                    previous = stops[selected[sequence - 1]]
                    current = stops[selected[sequence]]
                    straight_km = math.sqrt(
                        ((float(current["latitude"]) - float(previous["latitude"])) * 111.0) ** 2
                        + ((float(current["longitude"]) - float(previous["longitude"])) * 104.0) ** 2
                    )
                    segment = max(
                        0.72 + ((route_index * 13 + direction * 7 + sequence * 3) % 9) * 0.08,
                        straight_km * 1.20,
                    )
                    distances.append(round(distances[-1] + segment, 3))
                pattern = ctx.base("route_patterns", utc_from_local(valid_from, 0), ingestion_time=utc_from_local(valid_from, 120))
                pattern.update({
                    "pattern_id": pattern_id,
                    "route_id": route["route_id"],
                    "direction_id": direction,
                    "pattern_version": version,
                    "distance_km": distances[-1],
                    "valid_from": valid_from.isoformat(),
                    "valid_to": valid_to.isoformat() if valid_to else None,
                    "published_at_utc": utc_from_local(valid_from - timedelta(days=30), 0),
                })
                patterns.append(pattern)
                patterns_by_route_direction.setdefault((route["route_id"], direction), []).append(pattern)
                pattern_by_id[pattern_id] = pattern
                local_route_stops: List[dict] = []
                for sequence, stop_index in enumerate(selected, start=1):
                    stop = stops[stop_index]
                    natural_rs = {"pattern_id": pattern_id, "stop_sequence": sequence}
                    route_stop_id = entity_id("RouteStop", natural_rs, namespace=config.identity_namespace)
                    route_stop = ctx.base("route_stops", pattern["published_at_utc"], ingestion_time=add_seconds_pub(pattern["published_at_utc"], 30))
                    route_stop.update({
                        "route_stop_id": route_stop_id,
                        "pattern_id": pattern_id,
                        "route_id": route["route_id"],
                        "stop_id": stop["stop_id"],
                        "stop_sequence": sequence,
                        "distance_from_start_km": distances[sequence - 1],
                        "pickup_allowed": True,
                        "dropoff_allowed": True,
                    })
                    route_stops.append(route_stop)
                    local_route_stops.append(route_stop)
                route_stops_by_pattern[pattern_id] = local_route_stops

    for key in patterns_by_route_direction:
        patterns_by_route_direction[key].sort(key=lambda item: (item["valid_from"], item["pattern_version"]))

    ctx.scenario("new_routes", sum(1 for row in routes if date.fromisoformat(row["opened_on"]) > config.history_start))
    ctx.scenario("new_stops", sum(1 for row in stops if date.fromisoformat(row["opened_on"]) > config.history_start))
    ctx.evidence("network", {
        "route_count": len(routes),
        "stop_count": len(stops),
        "pattern_count": len(patterns),
        "route_stop_count": len(route_stops),
        "new_route_ids": [row["route_id"] for row in routes if date.fromisoformat(row["opened_on"]) > config.history_start],
        "new_stop_ids": [row["stop_id"] for row in stops if date.fromisoformat(row["opened_on"]) > config.history_start],
    })
    return NetworkData(
        stops=stops,
        routes=routes,
        patterns=patterns,
        route_stops=route_stops,
        stop_by_id=stop_by_id,
        route_by_id=route_by_id,
        pattern_by_id=pattern_by_id,
        route_stops_by_pattern=route_stops_by_pattern,
        patterns_by_route_direction=patterns_by_route_direction,
    )


def add_seconds_pub(value: str, seconds: int) -> str:
    from .common import add_seconds
    return add_seconds(value, seconds)

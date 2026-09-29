"""Stable SHA-256-derived identifiers.

Persistent IDs never use Python's salted ``hash()``.  Business identities use a
versioned namespace plus a natural key; physical source-row identities include
the immutable file and ordinal so duplicate business keys remain auditable.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .config import IDENTITY_NAMESPACE


def _digest(namespace: str, entity_type: str, natural_key: Any) -> str:
    material = json.dumps(
        {"namespace": namespace, "entity_type": entity_type, "natural_key": natural_key},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def entity_id(entity_type: str, natural_key: Any, *, namespace: str = IDENTITY_NAMESPACE, prefix: str | None = None) -> str:
    """Return ``PREFIX`` plus a full 64-hex SHA-256 identity digest."""

    if prefix is None:
        prefix = {
            "Passenger": "P",
            "Ticket": "TKT",
            "Route": "R",
            "Stop": "S",
            "RoutePattern": "RP",
            "RouteStop": "RS",
            "Trip": "TR",
            "Schedule": "SC",
            "Vehicle": "V",
            "PassengerCount": "PC",
            "Delay": "DL",
            "GpsEvent": "GPS",
            "ServiceCalendar": "CAL",
            "ScheduleStopTime": "SST",
            "TripStopEvent": "TSE",
            "Assignment": "ASG",
            "ServiceException": "EXC",
            "Journey": "J",
            "DemandRequest": "REQ",
            "ContextEvent": "CTX",
            "Transfer": "XFER",
            "Source": "SRC",
        }.get(entity_type, entity_type[:3].upper())
    return f"{prefix}{_digest(namespace, entity_type, natural_key)}"


def operational_departure_id(departure_token: str, service_date: str, *, namespace: str = IDENTITY_NAMESPACE) -> str:
    return entity_id("OperationalDeparture", {"departure_token": departure_token, "service_date": service_date}, namespace=namespace, prefix="OPD")


def trip_id(operational_id: str, plan_version: int, *, namespace: str = IDENTITY_NAMESPACE) -> str:
    return entity_id("Trip", {"operational_departure_id": operational_id, "plan_version": plan_version}, namespace=namespace, prefix="TR")


def source_row_id(table_name: str, relative_path: str, ordinal: int, *, namespace: str = IDENTITY_NAMESPACE) -> str:
    return entity_id("SourceRow", {"table_name": table_name, "relative_path": relative_path, "ordinal": ordinal}, namespace=namespace, prefix="SRC")


def stable_hash(value: Any) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()

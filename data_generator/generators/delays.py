"""Timestamp-derived positive delay helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Tuple


def positive_delay_components(actual_arrival: datetime, scheduled_arrival: datetime,
                              actual_departure: datetime, scheduled_departure: datetime) -> Tuple[int, int]:
    """Return positive-only delay seconds; early deviations remain signed elsewhere."""
    arrival = max(0, int((actual_arrival - scheduled_arrival).total_seconds()))
    departure = max(0, int((actual_departure - scheduled_departure).total_seconds()))
    return arrival, departure

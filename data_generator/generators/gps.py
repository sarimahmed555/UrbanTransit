"""Simple deterministic GPS interpolation helpers."""
from __future__ import annotations

from typing import Tuple


def interpolate_coordinates(start: Tuple[float, float], end: Tuple[float, float], fraction: float) -> Tuple[float, float]:
    fraction = max(0.0, min(1.0, float(fraction)))
    return (start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction)

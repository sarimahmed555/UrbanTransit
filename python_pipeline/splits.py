"""Frozen chronological split and availability/leakage guards."""

from dataclasses import dataclass

import datetime as dt

from feature_contracts import CHRONOLOGICAL_SPLITS

@dataclass(frozen=True)
class Split:
    name: str
    start: dt.date
    end: dt.date

    def contains(self, value: dt.date) -> bool:
        return self.start <= value <= self.end


SPLITS = tuple(
    Split(name, dt.date.fromisoformat(start), dt.date.fromisoformat(end) - dt.timedelta(days=1))
    for name, (start, end) in CHRONOLOGICAL_SPLITS.items()
)


def assign_split(service_date) -> str:
    value = service_date.date() if hasattr(service_date, "date") else service_date
    if isinstance(value, str):
        value = dt.date.fromisoformat(value[:10])
    for split in SPLITS:
        if split.contains(value):
            return split.name
    raise ValueError(f"Date is outside the approved split envelope: {value}")


def assert_no_future_leakage(features, *, availability_column="value_available_at",
                             cutoff_column="feature_cutoff_at", target_column="target_available_at"):
    for column in (availability_column, cutoff_column):
        if column not in features.columns:
            raise KeyError(f"Missing leakage guard column: {column}")
    available = features[availability_column]
    cutoff = features[cutoff_column]
    if (available > cutoff).any():
        raise ValueError(f"Feature availability exceeds its as-of cutoff in {int((available > cutoff).sum())} row(s)")
    if target_column in features.columns and (features[target_column] < cutoff).any():
        raise ValueError("Target availability precedes feature cutoff; check target construction")
    return True

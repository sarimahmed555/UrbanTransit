"""Small, framework-neutral API models shared by routes and services."""

from dataclasses import dataclass, field
from typing import Any, Mapping


FILTER_NAMES = frozenset(
    {
        "dateRange",
        "startDate",
        "endDate",
        "serviceDay",
        "routeId",
        "stopId",
        "direction",
        "vehicleId",
        "serviceType",
        "period",
        "granularity",
        "datasetVersion",
    }
)


@dataclass(frozen=True)
class AnalyticsFilters:
    values: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "AnalyticsFilters":
        values = values or {}
        unknown = set(values) - FILTER_NAMES
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"Unsupported filter(s): {names}")
        return cls({key: value for key, value in values.items() if value not in (None, "")})


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: Mapping[str, Any]
    headers: Mapping[str, str] = field(default_factory=dict)


def response_meta(
    *,
    state: str,
    source: str,
    warnings: list[str] | None = None,
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"state": state, "source": source}
    if warnings:
        meta["warnings"] = warnings
    if filters:
        meta["filters"] = dict(filters)
    return meta

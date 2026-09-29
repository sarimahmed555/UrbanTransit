"""Shared execution contracts; importing this module does not require Spark."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from feature_contracts import CHRONOLOGICAL_SPLITS


TABLES = (
    "context_events",
    "delays",
    "demand_requests",
    "gps_events",
    "passenger_counts",
    "passenger_journeys",
    "passenger_transfer_events",
    "passengers",
    "route_patterns",
    "route_stops",
    "routes",
    "schedule_stop_times",
    "schedules",
    "service_calendar",
    "service_exceptions",
    "stops",
    "tickets",
    "trip_stop_events",
    "trip_vehicle_assignments",
    "trips",
    "vehicles",
)

SPLITS = CHRONOLOGICAL_SPLITS


def require_hdfs_uri(root: str) -> str:
    """Do not silently resolve a cluster output root on the local filesystem."""
    parsed = urlsplit(root)
    if parsed.scheme != "hdfs" or not parsed.netloc or not parsed.path.startswith("/"):
        raise ValueError("HDFS root must be an explicit hdfs://host:port/path URI")
    if parsed.query or parsed.fragment or any(c.isspace() for c in root):
        raise ValueError("Invalid HDFS root URI")
    return root.rstrip("/")


@dataclass(frozen=True)
class SparkPaths:
    """HDFS locations. Values are configurable; no local production path is assumed."""

    root: str = "/urbantransit"

    @property
    def raw(self) -> str:
        return f"{self.root}/raw"

    @property
    def staging(self) -> str:
        return f"{self.root}/staging"

    @property
    def curated(self) -> str:
        return f"{self.root}/curated"

    @property
    def features(self) -> str:
        return f"{self.root}/features"

    @property
    def analytics(self) -> str:
        return f"{self.root}/analytics"

    @property
    def models(self) -> str:
        return f"{self.root}/models"

    @property
    def evidence(self) -> str:
        return f"{self.root}/evidence"

    @property
    def checkpoints(self) -> str:
        return f"{self.root}/_checkpoints"


def validate_certified_input(dataset_root: str, certification_marker: str) -> Path:
    """Validate arguments without opening data files or scanning a dataset."""
    root = Path(dataset_root)
    if not root.is_absolute():
        raise ValueError("dataset_root must be an absolute path or URI")
    if "production-v1" in root.parts or str(root).endswith("/raw_data/production-v1"):
        raise ValueError("Refusing uncertified raw_data/production-v1; provide the certified package")
    marker = Path(certification_marker)
    if not marker.is_absolute():
        raise ValueError("certification_marker must be an absolute path")
    return root

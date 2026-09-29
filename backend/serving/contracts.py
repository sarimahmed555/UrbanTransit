"""Validated serving records; no database driver or authentication dependency."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
import re
import uuid


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
RESULT_KINDS = frozenset({
    "executiveSummary", "passengerDemand", "routesAndStops", "delayAnalysis",
    "occupancy", "demandForecast", "routeClusters", "passengerFlow",
    "whatIf", "recommendations", "dataQuality", "systemStatus",
    "pipelineStatus", "pipelineComparison", "report",
})
RESULT_STATUSES = frozenset({"SUCCEEDED", "FIXTURE_TESTED", "NOT_READY", "ESTIMATE"})
MODEL_STATUSES = frozenset({"SUCCEEDED", "FIXTURE_TESTED", "FAILED", "NOT_READY"})


def identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field_name} must be a nonempty stable identifier")
    return value


def nonempty_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError(f"{field_name} must be nonempty text of at most 2048 characters")
    return value


def sha256_digest(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def aware_timestamp(value: datetime | str, field_name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


def service_date(value: date | str, field_name: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a calendar date")
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO date") from exc
    if not isinstance(value, date):
        raise ValueError(f"{field_name} must be a calendar date")
    return value


def _json_object(value, field_name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    try:
        return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain finite JSON values") from exc


@dataclass(frozen=True)
class DatasetVersion:
    dataset_version: str
    certification_status: str
    source_artifact: str | None = None
    source_sha256: str | None = None
    registered_at: datetime | str | None = None

    def __post_init__(self):
        identifier(self.dataset_version, "dataset_version")
        if self.certification_status not in {"CERTIFIED", "FIXTURE", "PENDING"}:
            raise ValueError("Unsupported dataset certification_status")
        if self.source_sha256 is not None:
            sha256_digest(self.source_sha256, "source_sha256")
        if self.source_artifact is not None:
            nonempty_text(self.source_artifact, "source_artifact")
        if self.registered_at is not None:
            object.__setattr__(
                self, "registered_at", aware_timestamp(self.registered_at, "registered_at")
            )


@dataclass(frozen=True)
class RouteRecord:
    dataset_version: str
    route_id: str
    route_code: str
    route_name: str
    mode: str
    service_type: str
    social_service_required: bool

    def __post_init__(self):
        for name in ("dataset_version", "route_id", "route_code", "mode", "service_type"):
            identifier(getattr(self, name), name)
        nonempty_text(self.route_name, "route_name")
        if not isinstance(self.social_service_required, bool):
            raise ValueError("social_service_required must be boolean")


@dataclass(frozen=True)
class StopRecord:
    dataset_version: str
    stop_id: str
    stop_code: str
    stop_name: str
    latitude: float
    longitude: float

    def __post_init__(self):
        for name in ("dataset_version", "stop_id", "stop_code"):
            identifier(getattr(self, name), name)
        nonempty_text(self.stop_name, "stop_name")
        if (not isinstance(self.latitude, (int, float)) or isinstance(self.latitude, bool)
                or not math.isfinite(self.latitude) or not -90 <= self.latitude <= 90):
            raise ValueError("latitude must be finite and in [-90, 90]")
        if (not isinstance(self.longitude, (int, float)) or isinstance(self.longitude, bool)
                or not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180):
            raise ValueError("longitude must be finite and in [-180, 180]")


@dataclass(frozen=True)
class VehicleRecord:
    dataset_version: str
    vehicle_id: str
    vehicle_code: str
    vehicle_type: str
    nominal_capacity: int

    def __post_init__(self):
        for name in ("dataset_version", "vehicle_id", "vehicle_code", "vehicle_type"):
            identifier(getattr(self, name), name)
        if isinstance(self.nominal_capacity, bool) or not isinstance(self.nominal_capacity, int) or self.nominal_capacity <= 0:
            raise ValueError("nominal_capacity must be a positive integer")


@dataclass(frozen=True)
class RoutePatternRecord:
    dataset_version: str
    pattern_id: str
    route_id: str
    direction_id: int
    pattern_version: int
    distance_km: float
    valid_from: date | str
    valid_to: date | str | None
    published_at_utc: datetime | str

    def __post_init__(self):
        for name in ("dataset_version", "pattern_id", "route_id"):
            identifier(getattr(self, name), name)
        if isinstance(self.direction_id, bool) or not isinstance(self.direction_id, int) or self.direction_id < 0:
            raise ValueError("direction_id must be a nonnegative integer")
        if isinstance(self.pattern_version, bool) or not isinstance(self.pattern_version, int) or self.pattern_version < 1:
            raise ValueError("pattern_version must be a positive integer")
        if not isinstance(self.distance_km, (int, float)) or isinstance(self.distance_km, bool) or not math.isfinite(self.distance_km) or self.distance_km < 0:
            raise ValueError("distance_km must be finite and nonnegative")
        start = service_date(self.valid_from, "valid_from")
        end = service_date(self.valid_to, "valid_to") if self.valid_to is not None else None
        object.__setattr__(self, "valid_from", start)
        object.__setattr__(self, "valid_to", end)
        if end is not None and end <= start:
            raise ValueError("valid_to must be after valid_from")
        object.__setattr__(
            self, "published_at_utc", aware_timestamp(self.published_at_utc, "published_at_utc")
        )


@dataclass(frozen=True)
class RouteStopRecord:
    dataset_version: str
    route_stop_id: str
    pattern_id: str
    stop_id: str
    stop_sequence: int
    distance_from_start_km: float
    pickup_allowed: bool
    dropoff_allowed: bool

    def __post_init__(self):
        for name in ("dataset_version", "route_stop_id", "pattern_id", "stop_id"):
            identifier(getattr(self, name), name)
        if isinstance(self.stop_sequence, bool) or not isinstance(self.stop_sequence, int) or self.stop_sequence < 1:
            raise ValueError("stop_sequence must be a positive integer")
        if not isinstance(self.distance_from_start_km, (int, float)) or isinstance(self.distance_from_start_km, bool) or not math.isfinite(self.distance_from_start_km) or self.distance_from_start_km < 0:
            raise ValueError("distance_from_start_km must be finite and nonnegative")
        if not isinstance(self.pickup_allowed, bool) or not isinstance(self.dropoff_allowed, bool):
            raise ValueError("pickup/dropoff flags must be boolean")


@dataclass(frozen=True)
class ScheduleRecord:
    dataset_version: str
    schedule_id: str
    pattern_id: str
    service_id: str
    departure_offset_sec: int
    valid_from: date | str
    valid_to: date | str | None
    published_at_utc: datetime | str
    schedule_version: str

    def __post_init__(self):
        for name in ("dataset_version", "schedule_id", "pattern_id", "service_id", "schedule_version"):
            identifier(getattr(self, name), name)
        if isinstance(self.departure_offset_sec, bool) or not isinstance(self.departure_offset_sec, int) or self.departure_offset_sec < 0:
            raise ValueError("departure_offset_sec must be a nonnegative integer")
        start = service_date(self.valid_from, "valid_from")
        end = service_date(self.valid_to, "valid_to") if self.valid_to is not None else None
        object.__setattr__(self, "valid_from", start)
        object.__setattr__(self, "valid_to", end)
        if end is not None and end <= start:
            raise ValueError("valid_to must be after valid_from")
        object.__setattr__(
            self, "published_at_utc", aware_timestamp(self.published_at_utc, "published_at_utc")
        )


@dataclass(frozen=True)
class ScheduleStopTimeRecord:
    dataset_version: str
    schedule_stop_time_id: str
    schedule_id: str
    route_stop_id: str
    stop_sequence: int
    arrival_offset_sec: int
    departure_offset_sec: int

    def __post_init__(self):
        for name in (
            "dataset_version", "schedule_stop_time_id", "schedule_id", "route_stop_id",
        ):
            identifier(getattr(self, name), name)
        for name in ("stop_sequence", "arrival_offset_sec", "departure_offset_sec"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.stop_sequence == 0 or self.departure_offset_sec < self.arrival_offset_sec:
            raise ValueError("Stop sequence must be positive and departure cannot precede arrival")


@dataclass(frozen=True)
class TripRecord:
    dataset_version: str
    trip_id: str
    operational_departure_id: str
    plan_version: int
    route_id: str
    pattern_id: str
    schedule_id: str
    service_date: date | str
    scheduled_start_utc: datetime | str
    scheduled_end_utc: datetime | str
    trip_status: str

    def __post_init__(self):
        for name in (
            "dataset_version", "trip_id", "operational_departure_id",
            "route_id", "pattern_id", "schedule_id",
        ):
            identifier(getattr(self, name), name)
        if isinstance(self.plan_version, bool) or not isinstance(self.plan_version, int) or self.plan_version < 1:
            raise ValueError("plan_version must be a positive integer")
        if self.trip_status not in {"SCHEDULED", "COMPLETED", "CANCELLED", "PARTIAL"}:
            raise ValueError("Unsupported trip_status")
        object.__setattr__(self, "service_date", service_date(self.service_date, "service_date"))
        start = aware_timestamp(self.scheduled_start_utc, "scheduled_start_utc")
        end = aware_timestamp(self.scheduled_end_utc, "scheduled_end_utc")
        if end < start:
            raise ValueError("scheduled_end_utc cannot precede scheduled_start_utc")
        object.__setattr__(self, "scheduled_start_utc", start)
        object.__setattr__(self, "scheduled_end_utc", end)


@dataclass(frozen=True)
class FeatureVersion:
    dataset_version: str
    feature_version: str
    producer: str
    feature_manifest_uri: str
    manifest_sha256: str
    created_at: datetime | str

    def __post_init__(self):
        for name in ("dataset_version", "feature_version"):
            identifier(getattr(self, name), name)
        nonempty_text(self.feature_manifest_uri, "feature_manifest_uri")
        if self.producer not in {"python", "spark"}:
            raise ValueError("producer must be python or spark")
        sha256_digest(self.manifest_sha256, "manifest_sha256")
        object.__setattr__(self, "created_at", aware_timestamp(self.created_at, "created_at"))


@dataclass(frozen=True)
class ModelRun:
    model_run_id: uuid.UUID
    dataset_version: str
    feature_version: str
    task_name: str
    model_name: str
    model_version: str
    status: str
    generated_at: datetime | str
    source_artifact: str
    source_sha256: str
    metrics: dict
    actor_subject: str | None = None

    def __post_init__(self):
        if not isinstance(self.model_run_id, uuid.UUID):
            try:
                object.__setattr__(self, "model_run_id", uuid.UUID(str(self.model_run_id)))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("model_run_id must be a UUID") from exc
        for name in ("dataset_version", "feature_version", "task_name", "model_name", "model_version"):
            identifier(getattr(self, name), name)
        nonempty_text(self.source_artifact, "source_artifact")
        if self.status not in MODEL_STATUSES:
            raise ValueError("Unsupported model run status")
        sha256_digest(self.source_sha256, "source_sha256")
        object.__setattr__(self, "generated_at", aware_timestamp(self.generated_at, "generated_at"))
        object.__setattr__(self, "metrics", _json_object(self.metrics, "metrics"))
        if self.actor_subject is not None:
            identifier(self.actor_subject, "actor_subject")


@dataclass(frozen=True)
class ServingResult:
    result_id: uuid.UUID
    result_kind: str
    result_status: str
    dataset_version: str
    source_artifact: str
    source_sha256: str
    generated_at: datetime | str
    payload: dict
    feature_version: str | None = None
    model_run_id: uuid.UUID | None = None
    analytics_version: str | None = None
    window_start: datetime | str | None = None
    window_end: datetime | str | None = None
    route_id: str | None = None
    stop_id: str | None = None
    vehicle_id: str | None = None
    trip_id: str | None = None
    direction_id: int | None = None
    period: str | None = None
    scenario_type: str | None = None
    request_metadata: dict | None = None
    actor_subject: str | None = None

    def __post_init__(self):
        if not isinstance(self.result_id, uuid.UUID):
            try:
                object.__setattr__(self, "result_id", uuid.UUID(str(self.result_id)))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("result_id must be a UUID") from exc
        if self.result_kind not in RESULT_KINDS:
            raise ValueError("Unsupported result_kind")
        if self.result_status not in RESULT_STATUSES:
            raise ValueError("Unsupported result_status")
        identifier(self.dataset_version, "dataset_version")
        nonempty_text(self.source_artifact, "source_artifact")
        for name in ("feature_version", "analytics_version", "route_id", "stop_id",
                     "vehicle_id", "trip_id", "period", "scenario_type", "actor_subject"):
            value = getattr(self, name)
            if value is not None:
                identifier(value, name)
        sha256_digest(self.source_sha256, "source_sha256")
        object.__setattr__(self, "generated_at", aware_timestamp(self.generated_at, "generated_at"))
        for name in ("window_start", "window_end"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, aware_timestamp(value, name))
        if self.window_start and self.window_end and self.window_start >= self.window_end:
            raise ValueError("window_start must precede window_end")
        if self.direction_id is not None and (
            isinstance(self.direction_id, bool) or not isinstance(self.direction_id, int)
            or self.direction_id < 0
        ):
            raise ValueError("direction_id must be a nonnegative integer")
        object.__setattr__(self, "payload", _json_object(self.payload, "payload"))
        if self.request_metadata is not None:
            object.__setattr__(
                self, "request_metadata", _json_object(self.request_metadata, "request_metadata")
            )
        if self.result_kind == "whatIf" and not self.scenario_type:
            raise ValueError("whatIf results require scenario_type")
        if self.result_kind != "whatIf" and self.scenario_type is not None:
            raise ValueError("scenario_type is valid only for whatIf results")


@dataclass(frozen=True)
class ReportRecord:
    report_id: uuid.UUID
    result_id: uuid.UUID
    report_name: str
    media_type: str
    artifact_uri: str
    artifact_sha256: str
    generated_at: datetime | str

    def __post_init__(self):
        for name in ("report_id", "result_id"):
            value = getattr(self, name)
            if not isinstance(value, uuid.UUID):
                try:
                    object.__setattr__(self, name, uuid.UUID(str(value)))
                except (ValueError, TypeError, AttributeError) as exc:
                    raise ValueError(f"{name} must be a UUID") from exc
        nonempty_text(self.report_name, "report_name")
        nonempty_text(self.media_type, "media_type")
        nonempty_text(self.artifact_uri, "artifact_uri")
        sha256_digest(self.artifact_sha256, "artifact_sha256")
        object.__setattr__(self, "generated_at", aware_timestamp(self.generated_at, "generated_at"))

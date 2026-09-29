"""PostgreSQL serving repository with parameterized SQL and atomic writes."""

import json
from typing import Any, Mapping

from .contracts import (
    DatasetVersion,
    FeatureVersion,
    ModelRun,
    ReportRecord,
    RoutePatternRecord,
    RouteRecord,
    RouteStopRecord,
    ScheduleRecord,
    ServingResult,
    ScheduleStopTimeRecord,
    StopRecord,
    TripRecord,
    VehicleRecord,
)
from .database import PostgresDatabase


_FILTER_COLUMNS = {
    "datasetVersion": "dataset_version",
    "routeId": "route_id",
    "stopId": "stop_id",
    "vehicleId": "vehicle_id",
    "direction": "direction_id",
    "period": "period",
}


class PostgresServingRepository:
    """A PostgreSQL implementation of the backend artifact repository boundary.

    This stores compact serving projections and result summaries only. It never
    ingests source CSVs, raw passenger records, GPS events, or stop-event facts.
    """

    def __init__(self, database: PostgresDatabase):
        self.database = database

    @staticmethod
    def _insert(sql, values, database):
        with database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, values)
            finally:
                cursor.close()

    def register_dataset(self, record: DatasetVersion) -> None:
        self._insert(
            """
            INSERT INTO app_serving.dataset_versions
                (dataset_version, certification_status, source_artifact, source_sha256, registered_at)
            VALUES (%s, %s, %s, %s, COALESCE(%s, now()))
            """,
            (
                record.dataset_version, record.certification_status,
                record.source_artifact, record.source_sha256, record.registered_at,
            ),
            self.database,
        )

    def persist_route(self, record: RouteRecord) -> None:
        self._insert(
            """
            INSERT INTO app_serving.routes
                (dataset_version, route_id, route_code, route_name, mode, service_type,
                 social_service_required)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.dataset_version, record.route_id, record.route_code, record.route_name,
                record.mode, record.service_type, record.social_service_required,
            ),
            self.database,
        )

    def persist_stop(self, record: StopRecord) -> None:
        self._insert(
            """
            INSERT INTO app_serving.stops
                (dataset_version, stop_id, stop_code, stop_name, latitude, longitude)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                record.dataset_version, record.stop_id, record.stop_code, record.stop_name,
                record.latitude, record.longitude,
            ),
            self.database,
        )

    def persist_vehicle(self, record: VehicleRecord) -> None:
        self._insert(
            """
            INSERT INTO app_serving.vehicles
                (dataset_version, vehicle_id, vehicle_code, vehicle_type, nominal_capacity)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                record.dataset_version, record.vehicle_id, record.vehicle_code,
                record.vehicle_type, record.nominal_capacity,
            ),
            self.database,
        )

    def persist_route_pattern(self, record: RoutePatternRecord) -> None:
        self._insert(
            """
            INSERT INTO app_serving.route_patterns
                (dataset_version, pattern_id, route_id, direction_id, pattern_version,
                 distance_km, valid_from, valid_to, published_at_utc)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.dataset_version, record.pattern_id, record.route_id,
                record.direction_id, record.pattern_version, record.distance_km,
                record.valid_from, record.valid_to, record.published_at_utc,
            ),
            self.database,
        )

    def persist_route_stop(self, record: RouteStopRecord) -> None:
        self._insert(
            """
            INSERT INTO app_serving.route_stops
                (dataset_version, route_stop_id, pattern_id, stop_id, stop_sequence,
                 distance_from_start_km, pickup_allowed, dropoff_allowed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.dataset_version, record.route_stop_id, record.pattern_id,
                record.stop_id, record.stop_sequence, record.distance_from_start_km,
                record.pickup_allowed, record.dropoff_allowed,
            ),
            self.database,
        )

    def persist_schedule(self, record: ScheduleRecord) -> None:
        self._insert(
            """
            INSERT INTO app_serving.schedules
                (dataset_version, schedule_id, pattern_id, service_id, departure_offset_sec,
                 valid_from, valid_to, published_at_utc, schedule_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.dataset_version, record.schedule_id, record.pattern_id,
                record.service_id, record.departure_offset_sec, record.valid_from,
                record.valid_to, record.published_at_utc, record.schedule_version,
            ),
            self.database,
        )

    def persist_schedule_stop_time(self, record: ScheduleStopTimeRecord) -> None:
        self._insert(
            """
            INSERT INTO app_serving.schedule_stop_times
                (dataset_version, schedule_stop_time_id, schedule_id, route_stop_id,
                 stop_sequence, arrival_offset_sec, departure_offset_sec)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.dataset_version, record.schedule_stop_time_id, record.schedule_id,
                record.route_stop_id, record.stop_sequence, record.arrival_offset_sec,
                record.departure_offset_sec,
            ),
            self.database,
        )

    def persist_trip(self, record: TripRecord) -> None:
        self._insert(
            """
            INSERT INTO app_serving.trips
                (dataset_version, trip_id, operational_departure_id, plan_version,
                 route_id, pattern_id, schedule_id, service_date, scheduled_start_utc,
                 scheduled_end_utc, trip_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.dataset_version, record.trip_id, record.operational_departure_id,
                record.plan_version, record.route_id, record.pattern_id, record.schedule_id,
                record.service_date, record.scheduled_start_utc, record.scheduled_end_utc,
                record.trip_status,
            ),
            self.database,
        )

    def register_feature_version(self, record: FeatureVersion) -> None:
        self._insert(
            """
            INSERT INTO app_serving.feature_versions
                (dataset_version, feature_version, producer, feature_manifest_uri,
                 manifest_sha256, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                record.dataset_version, record.feature_version, record.producer,
                record.feature_manifest_uri, record.manifest_sha256, record.created_at,
            ),
            self.database,
        )

    def persist_model_run(self, record: ModelRun) -> None:
        self._insert(
            """
            INSERT INTO app_serving.model_runs
                (model_run_id, dataset_version, feature_version, task_name, model_name,
                 model_version, status, generated_at, source_artifact, source_sha256,
                 metrics, actor_subject)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                record.model_run_id, record.dataset_version, record.feature_version,
                record.task_name, record.model_name, record.model_version, record.status,
                record.generated_at, record.source_artifact, record.source_sha256,
                json.dumps(record.metrics, sort_keys=True, allow_nan=False), record.actor_subject,
            ),
            self.database,
        )

    def persist_result(self, record: ServingResult) -> None:
        self._insert(
            """
            INSERT INTO app_serving.serving_results
                (result_id, result_kind, result_status, dataset_version, feature_version,
                 model_run_id, analytics_version, source_artifact, source_sha256, generated_at,
                 window_start, window_end, route_id, stop_id, vehicle_id, trip_id,
                 direction_id, period, scenario_type, request_metadata, payload, actor_subject)
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s::jsonb, %s::jsonb, %s
            )
            """,
            (
                record.result_id, record.result_kind, record.result_status,
                record.dataset_version, record.feature_version, record.model_run_id,
                record.analytics_version, record.source_artifact, record.source_sha256,
                record.generated_at, record.window_start, record.window_end, record.route_id,
                record.stop_id, record.vehicle_id, record.trip_id, record.direction_id,
                record.period, record.scenario_type,
                json.dumps(record.request_metadata, sort_keys=True, allow_nan=False)
                if record.request_metadata is not None else None,
                json.dumps(record.payload, sort_keys=True, allow_nan=False), record.actor_subject,
            ),
            self.database,
        )

    def persist_report(self, record: ReportRecord) -> None:
        self._insert(
            """
            INSERT INTO app_serving.reports
                (report_id, result_id, report_name, media_type, artifact_uri,
                 artifact_sha256, generated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.report_id, record.result_id, record.report_name,
                record.media_type, record.artifact_uri, record.artifact_sha256,
                record.generated_at,
            ),
            self.database,
        )

    def get(
        self,
        capability: str,
        *,
        filters: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict | None:
        """Return the latest non-fixture result as a drop-in artifact adapter."""
        del body
        where = ["result_kind = %s", "result_status <> 'FIXTURE_TESTED'"]
        parameters: list[Any] = [capability]
        filters = filters or {}
        for key, value in filters.items():
            if key in {"startDate", "endDate"}:
                if key == "startDate":
                    where.append("(window_end IS NULL OR window_end >= %s::date)")
                else:
                    where.append("(window_start IS NULL OR window_start < (%s::date + interval '1 day'))")
                parameters.append(value)
            elif key == "dateRange":
                if not isinstance(value, Mapping) or set(value) - {"startDate", "endDate"}:
                    raise ValueError("dateRange must contain only startDate/endDate")
                if value.get("startDate"):
                    where.append("(window_end IS NULL OR window_end >= %s::date)")
                    parameters.append(value["startDate"])
                if value.get("endDate"):
                    where.append("(window_start IS NULL OR window_start < (%s::date + interval '1 day'))")
                    parameters.append(value["endDate"])
            elif key in _FILTER_COLUMNS:
                where.append(f"{_FILTER_COLUMNS[key]} = %s")
                parameters.append(value)
            elif key == "serviceDay":
                where.append("window_start::date = %s::date")
                parameters.append(value)
            elif key == "granularity":
                continue
            else:
                raise ValueError(f"Unsupported serving result filter: {key}")
        query = (
            "SELECT payload::text FROM app_serving.serving_results WHERE "
            + " AND ".join(where)
            + " ORDER BY generated_at DESC, result_id DESC LIMIT 1"
        )
        with self.database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(query, tuple(parameters))
                row = cursor.fetchone()
            finally:
                cursor.close()
        if row is None:
            return None
        payload = json.loads(row[0])
        if not isinstance(payload, dict):
            raise RuntimeError("Persisted serving result payload is not a JSON object")
        return payload

    def health(self) -> dict[str, str]:
        return self.database.readiness()

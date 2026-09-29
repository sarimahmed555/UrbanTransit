CREATE SCHEMA IF NOT EXISTS app_serving;

CREATE TABLE IF NOT EXISTS app_serving.schema_migrations (
    migration_id text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_serving.dataset_versions (
    dataset_version text PRIMARY KEY,
    certification_status text NOT NULL
        CHECK (certification_status IN ('CERTIFIED', 'FIXTURE', 'PENDING')),
    source_artifact text,
    source_sha256 char(64)
        CHECK (source_sha256 IS NULL OR source_sha256 ~ '^[a-f0-9]{64}$'),
    registered_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_serving.routes (
    dataset_version text NOT NULL REFERENCES app_serving.dataset_versions(dataset_version),
    route_id text NOT NULL,
    route_code text NOT NULL,
    route_name text NOT NULL,
    mode text NOT NULL,
    service_type text NOT NULL,
    social_service_required boolean NOT NULL,
    opened_on date,
    closed_on date,
    route_status text,
    PRIMARY KEY (dataset_version, route_id),
    UNIQUE (dataset_version, route_code),
    CHECK (closed_on IS NULL OR opened_on IS NULL OR closed_on > opened_on)
);

CREATE TABLE IF NOT EXISTS app_serving.stops (
    dataset_version text NOT NULL REFERENCES app_serving.dataset_versions(dataset_version),
    stop_id text NOT NULL,
    stop_code text NOT NULL,
    stop_name text NOT NULL,
    latitude numeric(10, 7) NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude numeric(10, 7) NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    zone_id text,
    stop_type text,
    opened_on date,
    closed_on date,
    wheelchair_accessible boolean,
    PRIMARY KEY (dataset_version, stop_id),
    UNIQUE (dataset_version, stop_code),
    CHECK (closed_on IS NULL OR opened_on IS NULL OR closed_on > opened_on)
);

CREATE TABLE IF NOT EXISTS app_serving.vehicles (
    dataset_version text NOT NULL REFERENCES app_serving.dataset_versions(dataset_version),
    vehicle_id text NOT NULL,
    vehicle_code text NOT NULL,
    vehicle_type text NOT NULL,
    nominal_capacity integer NOT NULL CHECK (nominal_capacity > 0),
    commissioned_on date,
    retired_on date,
    operational_status text,
    PRIMARY KEY (dataset_version, vehicle_id),
    UNIQUE (dataset_version, vehicle_code)
);

CREATE TABLE IF NOT EXISTS app_serving.route_patterns (
    dataset_version text NOT NULL,
    pattern_id text NOT NULL,
    route_id text NOT NULL,
    direction_id smallint NOT NULL CHECK (direction_id >= 0),
    pattern_version integer NOT NULL CHECK (pattern_version > 0),
    distance_km numeric(10, 3) NOT NULL CHECK (distance_km >= 0),
    valid_from date NOT NULL,
    valid_to date,
    published_at_utc timestamptz NOT NULL,
    PRIMARY KEY (dataset_version, pattern_id),
    UNIQUE (dataset_version, route_id, direction_id, pattern_version),
    UNIQUE (dataset_version, pattern_id, route_id),
    FOREIGN KEY (dataset_version, route_id)
        REFERENCES app_serving.routes(dataset_version, route_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS app_serving.route_stops (
    dataset_version text NOT NULL,
    route_stop_id text NOT NULL,
    pattern_id text NOT NULL,
    stop_id text NOT NULL,
    stop_sequence integer NOT NULL CHECK (stop_sequence > 0),
    distance_from_start_km numeric(10, 3) NOT NULL CHECK (distance_from_start_km >= 0),
    pickup_allowed boolean NOT NULL,
    dropoff_allowed boolean NOT NULL,
    PRIMARY KEY (dataset_version, route_stop_id),
    UNIQUE (dataset_version, pattern_id, stop_sequence),
    FOREIGN KEY (dataset_version, pattern_id)
        REFERENCES app_serving.route_patterns(dataset_version, pattern_id),
    FOREIGN KEY (dataset_version, stop_id)
        REFERENCES app_serving.stops(dataset_version, stop_id)
);

CREATE TABLE IF NOT EXISTS app_serving.schedules (
    dataset_version text NOT NULL,
    schedule_id text NOT NULL,
    pattern_id text NOT NULL,
    service_id text NOT NULL,
    departure_offset_sec integer NOT NULL CHECK (departure_offset_sec >= 0),
    valid_from date NOT NULL,
    valid_to date,
    published_at_utc timestamptz NOT NULL,
    schedule_version text NOT NULL,
    PRIMARY KEY (dataset_version, schedule_id),
    UNIQUE (dataset_version, schedule_id, pattern_id),
    FOREIGN KEY (dataset_version, pattern_id)
        REFERENCES app_serving.route_patterns(dataset_version, pattern_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS app_serving.schedule_stop_times (
    dataset_version text NOT NULL,
    schedule_stop_time_id text NOT NULL,
    schedule_id text NOT NULL,
    route_stop_id text NOT NULL,
    stop_sequence integer NOT NULL CHECK (stop_sequence > 0),
    arrival_offset_sec integer NOT NULL CHECK (arrival_offset_sec >= 0),
    departure_offset_sec integer NOT NULL CHECK (departure_offset_sec >= arrival_offset_sec),
    PRIMARY KEY (dataset_version, schedule_stop_time_id),
    UNIQUE (dataset_version, schedule_id, stop_sequence),
    UNIQUE (dataset_version, schedule_id, route_stop_id),
    FOREIGN KEY (dataset_version, schedule_id)
        REFERENCES app_serving.schedules(dataset_version, schedule_id),
    FOREIGN KEY (dataset_version, route_stop_id)
        REFERENCES app_serving.route_stops(dataset_version, route_stop_id)
);
CREATE INDEX IF NOT EXISTS ix_schedule_stop_times_schedule_sequence
    ON app_serving.schedule_stop_times (dataset_version, schedule_id, stop_sequence);

CREATE TABLE IF NOT EXISTS app_serving.trips (
    dataset_version text NOT NULL,
    trip_id text NOT NULL,
    operational_departure_id text NOT NULL,
    plan_version integer NOT NULL CHECK (plan_version > 0),
    route_id text NOT NULL,
    pattern_id text NOT NULL,
    schedule_id text NOT NULL,
    service_date date NOT NULL,
    scheduled_start_utc timestamptz NOT NULL,
    scheduled_end_utc timestamptz NOT NULL,
    trip_status text NOT NULL
        CHECK (trip_status IN ('SCHEDULED', 'COMPLETED', 'CANCELLED', 'PARTIAL')),
    PRIMARY KEY (dataset_version, trip_id),
    UNIQUE (dataset_version, operational_departure_id, plan_version),
    FOREIGN KEY (dataset_version, route_id)
        REFERENCES app_serving.routes(dataset_version, route_id),
    FOREIGN KEY (dataset_version, pattern_id)
        REFERENCES app_serving.route_patterns(dataset_version, pattern_id),
    FOREIGN KEY (dataset_version, schedule_id)
        REFERENCES app_serving.schedules(dataset_version, schedule_id),
    FOREIGN KEY (dataset_version, pattern_id, route_id)
        REFERENCES app_serving.route_patterns(dataset_version, pattern_id, route_id),
    FOREIGN KEY (dataset_version, schedule_id, pattern_id)
        REFERENCES app_serving.schedules(dataset_version, schedule_id, pattern_id),
    CHECK (scheduled_end_utc >= scheduled_start_utc)
);

CREATE INDEX IF NOT EXISTS ix_trips_service_date_route
    ON app_serving.trips (dataset_version, service_date, route_id);
CREATE INDEX IF NOT EXISTS ix_trips_operational_departure
    ON app_serving.trips (dataset_version, operational_departure_id);
CREATE INDEX IF NOT EXISTS ix_patterns_route_direction
    ON app_serving.route_patterns (dataset_version, route_id, direction_id);
CREATE INDEX IF NOT EXISTS ix_route_stops_stop_pattern
    ON app_serving.route_stops (dataset_version, stop_id, pattern_id);
CREATE INDEX IF NOT EXISTS ix_schedules_pattern_validity
    ON app_serving.schedules (dataset_version, pattern_id, valid_from, valid_to);

CREATE TABLE IF NOT EXISTS app_serving.feature_versions (
    dataset_version text NOT NULL REFERENCES app_serving.dataset_versions(dataset_version),
    feature_version text NOT NULL,
    producer text NOT NULL CHECK (producer IN ('python', 'spark')),
    feature_manifest_uri text NOT NULL,
    manifest_sha256 char(64) NOT NULL CHECK (manifest_sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (dataset_version, feature_version)
);

CREATE TABLE IF NOT EXISTS app_serving.model_runs (
    model_run_id uuid PRIMARY KEY,
    dataset_version text NOT NULL,
    feature_version text NOT NULL,
    task_name text NOT NULL,
    model_name text NOT NULL,
    model_version text NOT NULL,
    status text NOT NULL
        CHECK (status IN ('SUCCEEDED', 'FIXTURE_TESTED', 'FAILED', 'NOT_READY')),
    generated_at timestamptz NOT NULL,
    source_artifact text NOT NULL,
    source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[a-f0-9]{64}$'),
    metrics jsonb NOT NULL CHECK (jsonb_typeof(metrics) = 'object'),
    actor_subject text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model_run_id, dataset_version),
    FOREIGN KEY (dataset_version, feature_version)
        REFERENCES app_serving.feature_versions(dataset_version, feature_version)
);
CREATE INDEX IF NOT EXISTS ix_model_runs_task_generated
    ON app_serving.model_runs (task_name, generated_at DESC);
CREATE INDEX IF NOT EXISTS ix_model_runs_dataset_feature
    ON app_serving.model_runs (dataset_version, feature_version, generated_at DESC);

CREATE TABLE IF NOT EXISTS app_serving.serving_results (
    result_id uuid PRIMARY KEY,
    result_kind text NOT NULL CHECK (result_kind IN (
        'executiveSummary', 'passengerDemand', 'routesAndStops', 'delayAnalysis',
        'occupancy', 'demandForecast', 'routeClusters', 'passengerFlow',
        'whatIf', 'recommendations', 'dataQuality', 'systemStatus',
        'pipelineStatus', 'pipelineComparison', 'report'
    )),
    result_status text NOT NULL
        CHECK (result_status IN ('SUCCEEDED', 'FIXTURE_TESTED', 'NOT_READY', 'ESTIMATE')),
    dataset_version text NOT NULL REFERENCES app_serving.dataset_versions(dataset_version),
    feature_version text,
    model_run_id uuid,
    analytics_version text,
    source_artifact text NOT NULL,
    source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[a-f0-9]{64}$'),
    generated_at timestamptz NOT NULL,
    window_start timestamptz,
    window_end timestamptz,
    route_id text,
    stop_id text,
    vehicle_id text,
    trip_id text,
    direction_id smallint CHECK (direction_id IS NULL OR direction_id >= 0),
    period text,
    scenario_type text,
    request_metadata jsonb CHECK (
        request_metadata IS NULL OR jsonb_typeof(request_metadata) = 'object'
    ),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    actor_subject text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (window_end IS NULL OR window_start IS NULL OR window_end > window_start),
    CHECK ((result_kind = 'whatIf') = (scenario_type IS NOT NULL)),
    FOREIGN KEY (dataset_version, feature_version)
        REFERENCES app_serving.feature_versions(dataset_version, feature_version),
    FOREIGN KEY (model_run_id, dataset_version)
        REFERENCES app_serving.model_runs(model_run_id, dataset_version),
    FOREIGN KEY (dataset_version, route_id)
        REFERENCES app_serving.routes(dataset_version, route_id),
    FOREIGN KEY (dataset_version, stop_id)
        REFERENCES app_serving.stops(dataset_version, stop_id),
    FOREIGN KEY (dataset_version, vehicle_id)
        REFERENCES app_serving.vehicles(dataset_version, vehicle_id),
    FOREIGN KEY (dataset_version, trip_id)
        REFERENCES app_serving.trips(dataset_version, trip_id)
);

CREATE INDEX IF NOT EXISTS ix_serving_results_kind_status_time
    ON app_serving.serving_results (result_kind, result_status, generated_at DESC);
CREATE INDEX IF NOT EXISTS ix_serving_results_dataset_time
    ON app_serving.serving_results (dataset_version, generated_at DESC);
CREATE INDEX IF NOT EXISTS ix_serving_results_route_window
    ON app_serving.serving_results (dataset_version, route_id, window_start, window_end);
CREATE INDEX IF NOT EXISTS ix_serving_results_stop_window
    ON app_serving.serving_results (dataset_version, stop_id, window_start, window_end);
CREATE INDEX IF NOT EXISTS ix_serving_results_model_run
    ON app_serving.serving_results (model_run_id, generated_at DESC)
    WHERE model_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_serving_results_scenario
    ON app_serving.serving_results (scenario_type, generated_at DESC)
    WHERE scenario_type IS NOT NULL;

CREATE TABLE IF NOT EXISTS app_serving.reports (
    report_id uuid PRIMARY KEY,
    result_id uuid NOT NULL UNIQUE
        REFERENCES app_serving.serving_results(result_id) ON DELETE RESTRICT,
    report_name text NOT NULL,
    media_type text NOT NULL,
    artifact_uri text NOT NULL,
    artifact_sha256 char(64) NOT NULL CHECK (artifact_sha256 ~ '^[a-f0-9]{64}$'),
    generated_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

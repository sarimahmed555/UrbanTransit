"""Spark feature transformations matching the implementation-neutral contracts."""

from feature_contracts import (
    CHRONOLOGICAL_SPLITS,
    FEATURE_CONTRACTS,
    validate_severity_thresholds,
)


def _require_columns(frame, columns, name):
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise KeyError(f"{name} is missing required columns: {', '.join(missing)}")


def prepare_operation_features(tables):
    """Join the actual source tables into one stop-event feature projection."""
    from pyspark.sql import functions as F

    required = {
        "trip_stop_events", "trips", "route_patterns", "route_stops",
        "routes", "schedules", "schedule_stop_times", "passenger_counts",
        "trip_vehicle_assignments", "vehicles", "context_events",
    }
    missing = sorted(required - tables.keys())
    if missing:
        raise KeyError(f"Missing operation source table(s): {', '.join(missing)}")
    events = tables["trip_stop_events"]
    _require_columns(events, (
        "trip_id", "stop_event_id", "schedule_stop_time_id", "route_stop_id",
        "stop_sequence", "service_date", "actual_departure_utc",
        "outcome_available_at_utc", "value_available_at",
    ), "trip_stop_events")
    result = events.select(
        "trip_id", "stop_event_id", "schedule_stop_time_id", "route_stop_id",
        "stop_sequence", "service_date", "actual_departure_utc",
        "outcome_available_at_utc",
        F.col("value_available_at").alias("event_value_available_at"),
    )
    trips = tables["trips"]
    _require_columns(trips, (
        "trip_id", "operational_departure_id", "route_id", "pattern_id",
        "schedule_id", "scheduled_start_utc", "published_at_utc",
        "planned_vehicle_id", "planned_vehicle_status",
    ), "trips")
    trip_columns = [
        column for column in (
            "trip_id", "operational_departure_id", "route_id", "pattern_id",
            "schedule_id", "scheduled_start_utc", "published_at_utc",
            "planned_vehicle_id", "planned_vehicle_status",
        ) if column in trips.columns
    ]
    trip_columns = [
        F.col(column).alias(
            "trip_published_at_utc" if column == "published_at_utc" else column
        ) for column in trip_columns
    ]
    result = result.join(
        trips.select(*trip_columns), "trip_id", "left",
    )
    _require_columns(tables["route_patterns"], (
        "pattern_id", "direction_id", "distance_km", "published_at_utc",
    ), "route_patterns")
    pattern_columns = [
        column for column in ("pattern_id", "direction_id", "distance_km",
                              "published_at_utc")
        if column in tables["route_patterns"].columns
    ]
    pattern_columns = [
        F.col(column).alias(
            "pattern_published_at_utc" if column == "published_at_utc" else column
        ) for column in pattern_columns
    ]
    result = result.join(tables["route_patterns"].select(*pattern_columns), "pattern_id", "left")
    _require_columns(tables["routes"], ("route_id", "value_available_at"), "routes")
    route_columns = [
        column for column in ("route_id", "route_code", "mode", "service_type",
                              "social_service_required", "value_available_at")
        if column in tables["routes"].columns
    ]
    route_columns = [
        F.col(column).alias(
            "route_value_available_at" if column == "value_available_at" else column
        ) for column in route_columns
    ]
    result = result.join(tables["routes"].select(*route_columns), "route_id", "left")
    _require_columns(tables["route_stops"], (
        "route_stop_id", "stop_id", "value_available_at",
    ), "route_stops")
    result = result.join(
        tables["route_stops"].select(
            "route_stop_id", "stop_id",
            F.col("value_available_at").alias("route_stop_value_available_at"),
        ), "route_stop_id", "left"
    )
    _require_columns(tables["schedules"], ("schedule_id", "published_at_utc"), "schedules")
    result = result.join(
        tables["schedules"].select(
            "schedule_id",
            F.col("published_at_utc").alias("schedule_published_at_utc"),
        ), "schedule_id", "left",
    )
    _require_columns(
        tables["schedule_stop_times"],
        ("schedule_stop_time_id", "departure_offset_sec", "value_available_at"),
        "schedule_stop_times",
    )
    result = result.join(
        tables["schedule_stop_times"].select(
            "schedule_stop_time_id", "departure_offset_sec",
            F.col("value_available_at").alias("schedule_time_available_at"),
        ), "schedule_stop_time_id", "left",
    ).withColumn(
        "scheduled_departure_utc",
        F.expr(
            "scheduled_start_utc + make_interval(0, 0, 0, 0, 0, 0, "
            "departure_offset_sec)"
        ),
    ).withColumn(
        "known_schedule_available_at",
        F.when(
            F.col("trip_published_at_utc").isNull()
            | F.col("pattern_published_at_utc").isNull()
            | F.col("schedule_published_at_utc").isNull()
            | F.col("schedule_time_available_at").isNull()
            | F.col("route_value_available_at").isNull()
            | F.col("route_stop_value_available_at").isNull(),
            F.lit(None).cast("timestamp"),
        ).otherwise(
            F.greatest(
                "trip_published_at_utc", "pattern_published_at_utc",
                "schedule_published_at_utc", "schedule_time_available_at",
                "route_value_available_at", "route_stop_value_available_at",
            )
        ),
    )
    if "planned_vehicle_id" in result.columns:
        _require_columns(
            tables["vehicles"], ("vehicle_id", "vehicle_type", "value_available_at"),
            "vehicles",
        )
        result = result.join(
            tables["vehicles"].select(
                F.col("vehicle_id").alias("__planned_vehicle_id"),
                F.col("vehicle_type").alias("planned_vehicle_type"),
                F.col("value_available_at").alias("planned_vehicle_available_at"),
            ),
            F.col("planned_vehicle_id") == F.col("__planned_vehicle_id"),
            "left",
        ).drop("__planned_vehicle_id")
    counts = tables["passenger_counts"]
    count_columns = [
        column for column in (
            "stop_event_id", "onboard_departure", "boardings", "alightings",
            "departure_assignment_id",
        ) if column in counts.columns
    ]
    result = result.join(counts.select(*count_columns), "stop_event_id", "left")
    assignments = tables["trip_vehicle_assignments"]
    _require_columns(assignments, ("assignment_id", "capacity_snapshot"),
                     "trip_vehicle_assignments")
    result = result.join(
        assignments.select("assignment_id", "capacity_snapshot"),
        F.col("departure_assignment_id") == F.col("assignment_id"),
        "left",
    ).drop("assignment_id")
    if "delays" in tables:
        delays = tables["delays"]
        _require_columns(delays, ("stop_event_id", "value_available_at"), "delays")
        delay_columns = [
            column for column in ("stop_event_id", "departure_delay_sec", "value_available_at")
            if column in delays.columns
        ]
        delay_projection = delays.select(*[
            F.col(column).alias(
                "delay_value_available_at" if column == "value_available_at" else column
            ) for column in delay_columns
        ])
        result = result.join(delay_projection, "stop_event_id", "left")
    result = result.withColumn(
        "outcome_available_at_utc",
        F.coalesce("outcome_available_at_utc", "event_value_available_at"),
    )
    if "context_events" in tables:
        context = tables["context_events"].alias("context")
        operation = result.alias("operation")
        context_match = (
            (F.col("operation.route_id") == F.col("context.route_id"))
            | (F.col("operation.stop_id") == F.col("context.stop_id"))
        )
        known_at_departure = (
            (F.col("context.starts_at_utc") <= F.col("operation.scheduled_departure_utc"))
            & (F.col("context.ends_at_utc") >= F.col("operation.scheduled_departure_utc"))
            & (F.col("context.announced_at_utc") <= F.col("operation.scheduled_departure_utc"))
            & (F.col("context.value_available_at") <= F.col("operation.scheduled_departure_utc"))
        )
        counts = operation.join(
            context, context_match & known_at_departure, "inner"
        ).groupBy(F.col("operation.stop_event_id").alias("stop_event_id")).agg(
            F.countDistinct("context.context_event_id").alias("known_context_event_count")
        )
        result = result.join(counts, "stop_event_id", "left").withColumn(
            "known_context_event_count", F.coalesce("known_context_event_count", F.lit(0))
        )
    return result


def _calendar_features(frame):
    from pyspark.sql import functions as F

    service_day = F.to_date("service_date")
    return (
        frame.withColumn("scheduled_hour_utc", F.hour("scheduled_departure_utc"))
        .withColumn(
            "service_weekday",
            F.pmod(F.dayofweek(service_day) + F.lit(5), F.lit(7)) + F.lit(1),
        )
        .withColumn("is_weekend", F.col("service_weekday") >= 6)
        .withColumn("service_month", F.month(service_day))
    )


def _assert_known_schedule_as_of(frame):
    from pyspark.sql import functions as F

    _require_columns(
        frame, ("known_schedule_available_at", "scheduled_departure_utc"),
        "planned feature context",
    )
    invalid_condition = (
        F.col("known_schedule_available_at").isNull()
        | F.col("scheduled_departure_utc").isNull()
        | (F.col("known_schedule_available_at") > F.col("scheduled_departure_utc"))
    )
    if {"planned_vehicle_id", "planned_vehicle_available_at"} <= set(frame.columns):
        invalid_condition = invalid_condition | (
            F.col("planned_vehicle_id").isNotNull()
            & (
                F.col("planned_vehicle_available_at").isNull()
                | (F.col("planned_vehicle_available_at") > F.col("scheduled_departure_utc"))
            )
        )
    invalid = frame.filter(invalid_condition).limit(1).count()
    if invalid:
        raise ValueError("Planned feature context is unavailable at the scheduled cutoff")


def _past_only_history(frame, *, keys, time_column, target_column,
                       available_column, prefix):
    from functools import cmp_to_key

    from pyspark.sql import functions as F
    from pyspark.sql.types import StructField, StructType

    order_columns = [
        column for column in ("trip_id", "stop_event_id")
        if column in frame.columns
    ]
    row_id = "__history_row_id"
    source = frame.withColumn(row_id, F.monotonically_increasing_id())
    sorted_frame = source.repartition(
        *[F.col(column) for column in keys]
    ).sortWithinPartitions(
        *[F.col(column) for column in keys],
        F.col(time_column).asc_nulls_first(),
    )
    indexes = {
        field.name: index
        for index, field in enumerate(sorted_frame.schema.fields)
    }
    key_indexes = [indexes[column] for column in keys]
    time_index = indexes[time_column]
    available_index = indexes[available_column]
    target_index = indexes[target_column]
    order_indexes = [indexes[column] for column in order_columns]

    def compare_candidates(left, right):
        for left_value, right_value in zip(
            (left[0], *left[1]), (right[0], *right[1])
        ):
            if left_value is None:
                if right_value is not None:
                    return 1
            elif right_value is None:
                return -1
            elif left_value != right_value:
                return -1 if left_value > right_value else 1
        return 0

    def calculate_partition(rows):
        current_keys = None
        pending = []
        recent = []
        row = next(rows, None)
        while row is not None:
            group = tuple(row[index] for index in key_indexes)
            if group != current_keys:
                current_keys = group
                pending = []
                recent = []

            cutoff = row[time_index]
            batch = [row]
            row = next(rows, None)
            while row is not None:
                next_group = tuple(row[index] for index in key_indexes)
                if next_group != current_keys or row[time_index] != cutoff:
                    break
                batch.append(row)
                row = next(rows, None)

            if cutoff is not None:
                still_pending = []
                for available_at, candidate in pending:
                    if available_at <= cutoff:
                        recent.append(candidate)
                    else:
                        still_pending.append((available_at, candidate))
                pending = still_pending
                recent.sort(key=cmp_to_key(compare_candidates))
                del recent[7:]

            values = [candidate[2] for candidate in recent]
            lag = values[0] if values else None
            mean = sum(values) / len(values) if values else None
            for current in batch:
                yield (
                    *tuple(current),
                    lag,
                    mean,
                )

            for current in batch:
                event_time = current[time_index]
                available_at = current[available_index]
                target = current[target_index]
                if (
                    cutoff is not None
                    and event_time is not None
                    and available_at is not None
                    and target is not None
                ):
                    pending.append((
                        available_at,
                        (
                            event_time,
                            tuple(current[index] for index in order_indexes),
                            target,
                        ),
                    ))

    output_schema = StructType([
        *source.schema.fields,
        StructField(f"{prefix}_lag_1", frame.schema[target_column].dataType, True),
        StructField(
            f"{prefix}_mean_7",
            frame.select(F.avg(F.col(target_column)).alias("__history_avg_type"))
            .schema["__history_avg_type"].dataType,
            True,
        ),
    ])
    return frame.sparkSession.createDataFrame(
        sorted_frame.rdd.mapPartitions(calculate_partition),
        output_schema,
    ).drop(row_id)


def build_delay_features(operations, *, severity_thresholds_sec):
    """Create stop-event delay labels and schedule-time/history features."""
    from pyspark.sql import functions as F

    thresholds = validate_severity_thresholds(severity_thresholds_sec)
    _require_columns(operations, (
        "service_date", "route_id", "direction_id", "stop_id", "trip_id",
        "stop_event_id", "scheduled_departure_utc", "actual_departure_utc",
        "outcome_available_at_utc",
    ), "operations")
    _assert_known_schedule_as_of(operations)
    scheduled = F.col("scheduled_departure_utc")
    actual = F.col("actual_departure_utc")
    result = _calendar_features(operations)
    result = result.withColumn(
        "delay_sec",
        F.when(actual.isNull() | scheduled.isNull(), F.lit(None).cast("double"))
        .otherwise(F.greatest(F.lit(0.0), actual.cast("long") - scheduled.cast("long"))),
    )
    labels = ("ON_TIME", "MINOR_DELAY", "MODERATE_DELAY", "MAJOR_DELAY", "SEVERE_DELAY")
    severity = F.when(F.col("delay_sec") <= thresholds[0], labels[0])
    for index in range(1, len(thresholds)):
        severity = severity.when(F.col("delay_sec") <= thresholds[index], labels[index])
    result = (
        result.withColumn("delay_severity", severity.otherwise(labels[-1]))
        .withColumn("feature_cutoff_at", scheduled)
        .withColumn("target_available_at", F.col("outcome_available_at_utc"))
    )
    result = result.withColumn(
        "delay_severity",
        F.when(F.col("delay_sec").isNull(), F.lit(None).cast("string"))
        .otherwise(F.col("delay_severity")),
    )
    contract = FEATURE_CONTRACTS["delay_severity"]
    output_columns = [
        *contract.predictor_features, "service_date", "trip_id", "stop_event_id",
        "feature_cutoff_at", "known_schedule_available_at", "target_available_at",
        "delay_sec", "delay_severity",
    ]
    history_columns = [
        column for column in output_columns if column in result.columns
    ]
    result = _past_only_history(
        result.select(*history_columns),
        keys=["route_id", "direction_id", "stop_id"],
        time_column="feature_cutoff_at", target_column="delay_sec",
        available_column="target_available_at", prefix="historical_delay_sec",
    )
    return result.select(*[column for column in output_columns if column in result.columns])


def build_demand_forecast_features(trip_demand):
    """Add past-only demand history to one-trip demand targets."""
    from pyspark.sql import functions as F

    _require_columns(trip_demand, (
        "service_date", "route_id", "direction_id", "trip_id",
        "scheduled_departure_utc", "passenger_demand", "target_available_at",
    ), "trip_demand")
    _assert_known_schedule_as_of(trip_demand)
    result = _calendar_features(trip_demand).withColumn(
        "feature_cutoff_at", F.col("scheduled_departure_utc")
    )
    contract = FEATURE_CONTRACTS["demand_forecast"]
    output_columns = [
        *contract.predictor_features, "service_date", "trip_id",
        "operational_departure_id", "scheduled_departure_utc",
        "feature_cutoff_at", "known_schedule_available_at",
        "target_available_at", "passenger_demand",
    ]
    result = _past_only_history(
        result.select(*[column for column in output_columns if column in result.columns]),
        keys=["route_id", "direction_id", "scheduled_hour_utc", "service_weekday"],
        time_column="feature_cutoff_at", target_column="passenger_demand",
        available_column="target_available_at", prefix="historical_demand",
    )
    return result.select(*[column for column in output_columns if column in result.columns])


def aggregate_trip_demand(journeys, trips, route_patterns, schedules):
    """Aggregate observed passenger journeys into trip-level served-demand targets."""
    from pyspark.sql import functions as F

    _require_columns(journeys, ("trip_id", "passenger_count", "value_available_at"),
                     "passenger_journeys")
    _require_columns(trips, (
        "trip_id", "route_id", "pattern_id", "schedule_id", "scheduled_start_utc",
        "service_date", "published_at_utc",
    ), "trips")
    _require_columns(route_patterns, (
        "pattern_id", "direction_id", "published_at_utc",
    ), "route_patterns")
    _require_columns(schedules, ("schedule_id", "published_at_utc"), "schedules")
    demand = journeys.filter(F.col("trip_id").isNotNull()).groupBy("trip_id").agg(
        F.sum("passenger_count").alias("passenger_demand"),
        F.max("value_available_at").alias("target_available_at"),
    )
    demand = demand.join(
        trips.select(
            "trip_id", "route_id", "pattern_id", "service_date",
            "schedule_id", "scheduled_start_utc", "published_at_utc",
            "operational_departure_id",
        ), "trip_id", "inner",
    ).join(
        route_patterns.select(
            "pattern_id", "direction_id",
            F.col("published_at_utc").alias("pattern_published_at_utc"),
        ),
        "pattern_id", "left",
    ).join(
        schedules.select(
            "schedule_id",
            F.col("published_at_utc").alias("schedule_published_at_utc"),
        ), "schedule_id", "left",
    ).withColumn(
        "known_schedule_available_at",
        F.when(
            F.col("published_at_utc").isNull()
            | F.col("pattern_published_at_utc").isNull()
            | F.col("schedule_published_at_utc").isNull(),
            F.lit(None).cast("timestamp"),
        ).otherwise(
            F.greatest(
                "published_at_utc", "pattern_published_at_utc",
                "schedule_published_at_utc",
            )
        ),
    )
    return demand.withColumnRenamed("scheduled_start_utc", "scheduled_departure_utc")


def build_occupancy_features(operations):
    """Compute observed segment occupancy without inferring unknown capacity."""
    from pyspark.sql import functions as F

    _require_columns(operations, (
        "service_date", "stop_event_id", "onboard_departure", "capacity_snapshot",
    ), "operations")
    valid = (
        F.col("onboard_departure").isNotNull()
        & F.col("capacity_snapshot").isNotNull()
        & (F.col("capacity_snapshot") > 0)
    )
    return (
        operations.withColumn(
            "capacity_utilization",
            F.when(valid, F.col("onboard_departure") / F.col("capacity_snapshot")),
        )
        .withColumn(
            "occupancy_status",
            F.when(valid, F.lit("AVAILABLE")).otherwise(F.lit("UNAVAILABLE")),
        )
        .withColumn(
            "over_capacity",
            F.when(valid, F.col("onboard_departure") > F.col("capacity_snapshot")),
        )
    )


def build_headway_features(operations):
    """Calculate actual and scheduled headways at route/direction/physical-stop grain."""
    from pyspark.sql import Window, functions as F

    _require_columns(operations, (
        "service_date", "route_id", "direction_id", "stop_id", "trip_id",
        "stop_event_id", "actual_departure_utc", "scheduled_departure_utc",
    ), "operations")
    keys = ["route_id", "direction_id", "stop_id"]
    actual_window = Window.partitionBy(*keys).orderBy(
        F.col("actual_departure_utc").asc_nulls_last(),
        F.col("trip_id").asc(), F.col("stop_event_id").asc(),
    )
    scheduled_window = Window.partitionBy(*keys).orderBy(
        F.col("scheduled_departure_utc").asc_nulls_last(),
        F.col("trip_id").asc(), F.col("stop_event_id").asc(),
    )
    return (
        operations.withColumn(
            "__previous_actual", F.lag("actual_departure_utc").over(actual_window)
        )
        .withColumn(
            "actual_headway_sec",
            F.col("actual_departure_utc").cast("long")
            - F.col("__previous_actual").cast("long"),
        )
        .withColumn(
            "__previous_scheduled",
            F.lag("scheduled_departure_utc").over(scheduled_window),
        )
        .withColumn(
            "scheduled_headway_sec",
            F.col("scheduled_departure_utc").cast("long")
            - F.col("__previous_scheduled").cast("long"),
        )
        .withColumn(
            "headway_deviation_sec",
            F.col("actual_headway_sec") - F.col("scheduled_headway_sec"),
        )
        .drop("__previous_actual", "__previous_scheduled")
    )


def build_delay_analytics(operations):
    """Summarize measured schedule deviation by date, route, and direction."""
    from pyspark.sql import functions as F

    _require_columns(operations, (
        "service_date", "route_id", "direction_id", "scheduled_departure_utc",
        "actual_departure_utc",
    ), "operations")
    delay = F.greatest(
        F.lit(0.0),
        F.col("actual_departure_utc").cast("long")
        - F.col("scheduled_departure_utc").cast("long"),
    )
    valid = F.col("actual_departure_utc").isNotNull() & F.col("scheduled_departure_utc").isNotNull()
    work = operations.withColumn("delay_sec", F.when(valid, delay))
    return work.filter(F.col("delay_sec").isNotNull()).groupBy(
        "service_date", "route_id", "direction_id"
    ).agg(
        F.count("delay_sec").alias("observed_stop_events"),
        F.avg("delay_sec").alias("mean_delay_sec"),
        F.max("delay_sec").alias("max_delay_sec"),
        F.avg((F.col("delay_sec") > 0).cast("double")).alias("positive_delay_fraction"),
    )


def build_route_clustering_features(operations, route_dimensions=None, trip_demand=None):
    """Build training-window route/direction vectors; does not fit or emit clusters."""
    from pyspark.sql import functions as F

    _require_columns(operations, (
        "service_date", "route_id", "direction_id", "stop_id",
        "operational_departure_id", "onboard_departure", "capacity_snapshot",
        "scheduled_departure_utc", "actual_departure_utc", "distance_km",
    ), "operations")
    work = operations.filter(
        (F.col("service_date") >= F.lit("2025-01-01"))
        & (F.col("service_date") < F.lit("2026-01-01"))
    ).withColumn(
        "__utilization",
        F.when(
            F.col("capacity_snapshot") > 0,
            F.col("onboard_departure") / F.col("capacity_snapshot"),
        ),
    ).withColumn(
        "__delay_sec",
        F.when(
            F.col("actual_departure_utc").isNotNull()
            & F.col("scheduled_departure_utc").isNotNull(),
            F.greatest(
                F.lit(0.0),
                F.col("actual_departure_utc").cast("long")
                - F.col("scheduled_departure_utc").cast("long"),
            ),
        ),
    )
    work = build_headway_features(work).withColumn(
        "__abs_headway_deviation", F.abs("headway_deviation_sec")
    )
    result = work.groupBy("route_id", "direction_id").agg(
        F.count("stop_id").alias("training_stop_observations"),
        F.countDistinct("operational_departure_id").alias("training_departures"),
        F.avg("onboard_departure").alias("mean_observed_load"),
        F.avg("__utilization").alias("mean_capacity_utilization"),
        F.avg("__delay_sec").alias("mean_delay_sec"),
        F.avg("distance_km").alias("mean_route_distance_km"),
        F.avg("__abs_headway_deviation").alias("mean_abs_headway_deviation_sec"),
        F.countDistinct("stop_id").alias("distinct_stops"),
    )
    if trip_demand is not None:
        _require_columns(
            trip_demand, ("route_id", "direction_id", "trip_id", "service_date",
                          "passenger_demand"), "trip_demand",
        )
        demand_metrics = trip_demand.filter(
            (F.col("service_date") >= F.lit("2025-01-01"))
            & (F.col("service_date") < F.lit("2026-01-01"))
        ).groupBy("route_id", "direction_id").agg(
            F.countDistinct("trip_id").alias("supported_demand_trips"),
            F.avg("passenger_demand").alias("mean_served_demand_per_trip"),
        )
        result = result.join(demand_metrics, ["route_id", "direction_id"], "left")
    if route_dimensions is not None:
        _require_columns(route_dimensions, ("route_id",), "route_dimensions")
        result = result.join(route_dimensions, "route_id", "left")
    return result


def build_feature_frames(operations, trip_demand, *, severity_thresholds_sec,
                         route_dimensions=None, tasks=None):
    """Build independent task datasets from schema-backed enriched source projections."""
    builders = {
        "delay_severity": lambda: build_delay_features(
            operations, severity_thresholds_sec=severity_thresholds_sec
        ),
        "demand_forecast": lambda: build_demand_forecast_features(trip_demand),
        "occupancy": lambda: build_occupancy_features(operations),
        "headway": lambda: build_headway_features(operations),
        "delay_analytics": lambda: build_delay_analytics(operations),
        "route_clustering": lambda: build_route_clustering_features(
            operations, route_dimensions, trip_demand
        ),
    }
    selected = tuple(builders) if tasks is None else tuple(tasks)
    unknown = sorted(set(selected) - set(builders))
    if unknown:
        raise ValueError(f"Unknown feature task(s): {', '.join(unknown)}")
    return {task: builders[task]() for task in selected}


def build_feature_frames_from_tables(tables, *, severity_thresholds_sec, tasks=None):
    """Build every task projection directly from accepted schema-backed tables."""
    operations = prepare_operation_features(tables)
    selected = set(tasks) if tasks is not None else {
        "delay_severity", "demand_forecast", "occupancy", "headway",
        "delay_analytics", "route_clustering",
    }
    trip_demand = None
    if {"demand_forecast", "route_clustering"} & selected:
        trip_demand = aggregate_trip_demand(
            tables["passenger_journeys"], tables["trips"], tables["route_patterns"],
            tables["schedules"],
        )
    route_dimensions = tables["routes"].select(
        *[
            column for column in (
                "route_id", "route_code", "mode", "service_type",
                "social_service_required",
            ) if column in tables["routes"].columns
        ]
    )
    return build_feature_frames(
        operations, trip_demand,
        severity_thresholds_sec=severity_thresholds_sec,
        route_dimensions=route_dimensions,
        tasks=tasks,
    )


def chronological_split(frame, date_column="service_date"):
    from pyspark.sql import functions as F

    if date_column not in frame.columns:
        raise KeyError(f"Missing split date column: {date_column}")
    return {
        name: frame.filter(
            (F.col(date_column) >= F.lit(start))
            & (F.col(date_column) < F.lit(end))
        )
        for name, (start, end) in CHRONOLOGICAL_SPLITS.items()
    }


def write_feature_splits(
    feature_frame, output_root: str, logger, *, task="shared_features", splits=None
):
    selected_splits = (
        tuple(CHRONOLOGICAL_SPLITS)
        if splits is None else tuple(splits)
    )
    for split, current in chronological_split(feature_frame).items():
        if split not in selected_splits:
            continue
        output = f"{output_root}/task={task}/split={split}"
        with logger.stage(f"features_{task}_{split}", output) as record:
            current.write.mode("errorifexists").partitionBy("service_date").parquet(output)
            logger.record_count(record, current)
            record["schema"] = current.schema.jsonValue()
            date_min = current.agg({"service_date": "min"}).first()[0]
            date_max = current.agg({"service_date": "max"}).first()[0]
            record["date_min"] = date_min.isoformat() if date_min else None
            record["date_max"] = date_max.isoformat() if date_max else None

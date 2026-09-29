"""Independent Pandas feature transformations with explicit as-of semantics."""

from bisect import insort
import heapq

from feature_contracts import (
    CHRONOLOGICAL_SPLITS,
    FEATURE_CONTRACTS,
    validate_severity_thresholds,
)


def _pd():
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Pandas is required for feature engineering") from exc
    return pd


def passenger_demand(journeys, *, group_by=("trip_id",)):
    return journeys.groupby(list(group_by), dropna=False).size().rename("passenger_demand").reset_index()


def boarding_alighting(journeys, *, origin="origin_route_stop_id", destination="destination_route_stop_id"):
    pd = _pd()
    boardings = journeys.groupby(origin).size().rename("boardings")
    alightings = journeys.groupby(destination).size().rename("alightings")
    return pd.concat([boardings, alightings], axis=1).fillna(0).astype("int64").reset_index(names="route_stop_id")


def occupancy(counts, assignments, *, count_assignment="departure_assignment_id",
              capacity_column="capacity_snapshot"):
    joined = counts.merge(assignments[[ "assignment_id", capacity_column ]],
                          left_on=count_assignment, right_on="assignment_id", how="left", validate="many_to_one")
    valid = joined[capacity_column].notna() & (joined[capacity_column] > 0)
    joined["capacity_utilization"] = None
    joined.loc[valid, "capacity_utilization"] = (
        joined.loc[valid, "onboard_departure"] / joined.loc[valid, capacity_column]
    )
    joined["occupancy_status"] = "UNAVAILABLE"
    joined.loc[valid, "occupancy_status"] = "AVAILABLE"
    return joined


def temporal_features(frame, timestamp_column="event_time", service_date_column="service_date"):
    pd = _pd()
    result = frame.copy().reset_index(drop=True)
    timestamp = pd.to_datetime(result[timestamp_column], errors="coerce", utc=True)
    result["hour"] = timestamp.dt.hour
    result["weekday"] = timestamp.dt.dayofweek + 1
    result["is_weekend"] = result["weekday"] >= 6
    result["service_date"] = result.get(service_date_column, timestamp.dt.date)
    result["service_month"] = pd.to_datetime(
        result["service_date"], errors="coerce"
    ).dt.month
    return result


def _assert_known_schedule_as_of(frame):
    pd = _pd()
    _require_columns(
        frame, ("known_schedule_available_at", "scheduled_departure_utc"),
        "planned feature context",
    )
    available = pd.to_datetime(frame["known_schedule_available_at"], errors="coerce", utc=True)
    cutoff = pd.to_datetime(frame["scheduled_departure_utc"], errors="coerce", utc=True)
    invalid = available.isna() | cutoff.isna() | (available > cutoff)
    if {"planned_vehicle_id", "planned_vehicle_available_at"} <= set(frame.columns):
        planned_vehicle = frame["planned_vehicle_id"].notna()
        vehicle_available = pd.to_datetime(
            frame["planned_vehicle_available_at"], errors="coerce", utc=True
        )
        invalid |= planned_vehicle & (
            vehicle_available.isna() | (vehicle_available > cutoff)
        )
    if invalid.any():
        raise ValueError(
            f"Planned feature context is unavailable at the scheduled cutoff in "
            f"{int(invalid.sum())} row(s)"
        )


def headways(trips, *, route="route_id", direction="direction_id", stop="stop_sequence",
             departure="actual_departure_utc"):
    pd = _pd()
    result = trips.sort_values([route, direction, stop, departure]).copy()
    result["headway_sec"] = (
        pd.to_datetime(result[departure], utc=True) -
        pd.to_datetime(result[departure], utc=True).groupby(
            [result[route], result[direction], result[stop]]
        ).shift(1)
    ).dt.total_seconds()
    return result


def route_performance(counts, delays):
    loads = counts.groupby("trip_id", dropna=False)["onboard_departure"].max().rename("peak_onboard")
    delay = delays.groupby("trip_id", dropna=False)["delay_sec"].agg(["count", "mean"]).rename(
        columns={"count": "delay_records", "mean": "mean_delay_sec"}
    )
    return loads.to_frame().join(delay, how="outer").reset_index()


def _require_columns(frame, columns, name):
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise KeyError(f"{name} is missing required columns: {', '.join(missing)}")


def prepare_operation_features(tables):
    """Join schema-backed stop, schedule, capacity, and route context projections."""
    pd = _pd()
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
    _require_columns(tables["trips"], (
        "trip_id", "operational_departure_id", "route_id", "pattern_id",
        "schedule_id", "scheduled_start_utc", "published_at_utc",
        "planned_vehicle_id", "planned_vehicle_status",
    ), "trips")
    trip_context_columns = [
        column for column in (
            "trip_id", "operational_departure_id", "route_id", "pattern_id",
            "schedule_id", "scheduled_start_utc", "published_at_utc",
            "planned_vehicle_id", "planned_vehicle_status",
        ) if column in tables["trips"]
    ]
    _require_columns(tables["routes"], ("route_id", "value_available_at"), "routes")
    trip_context = tables["trips"][trip_context_columns].rename(
        columns={"published_at_utc": "trip_published_at_utc"}
    )
    result = events[[
        "trip_id", "stop_event_id", "schedule_stop_time_id", "route_stop_id",
        "stop_sequence", "service_date", "actual_departure_utc",
        "outcome_available_at_utc", "value_available_at",
    ]].rename(columns={"value_available_at": "event_value_available_at"})
    result = result.merge(
        trip_context,
        on="trip_id", how="left", validate="many_to_one",
    )
    _require_columns(
        tables["route_patterns"],
        ("pattern_id", "direction_id", "distance_km", "published_at_utc"),
        "route_patterns",
    )
    patterns = tables["route_patterns"][[
        "pattern_id", "direction_id", "distance_km", "published_at_utc",
    ]].rename(columns={"published_at_utc": "pattern_published_at_utc"})
    result = result.merge(patterns, on="pattern_id", how="left", validate="many_to_one")
    route_columns = [
        column for column in ("route_id", "route_code", "mode", "service_type",
                              "social_service_required", "value_available_at")
        if column in tables["routes"]
    ]
    routes = tables["routes"][route_columns].rename(
        columns={"value_available_at": "route_value_available_at"}
    )
    result = result.merge(
        routes,
        on="route_id", how="left", validate="many_to_one",
    )
    _require_columns(
        tables["route_stops"], ("route_stop_id", "stop_id", "value_available_at"),
        "route_stops",
    )
    stops = tables["route_stops"][[
        "route_stop_id", "stop_id", "value_available_at",
    ]].rename(columns={"value_available_at": "route_stop_value_available_at"})
    result = result.merge(stops, on="route_stop_id", how="left", validate="many_to_one")
    _require_columns(tables["schedules"], ("schedule_id", "published_at_utc"), "schedules")
    schedules = tables["schedules"][["schedule_id", "published_at_utc"]].rename(
        columns={"published_at_utc": "schedule_published_at_utc"}
    )
    result = result.merge(
        schedules, on="schedule_id", how="left", validate="many_to_one"
    )
    _require_columns(
        tables["schedule_stop_times"],
        ("schedule_stop_time_id", "departure_offset_sec", "value_available_at"),
        "schedule_stop_times",
    )
    offsets = tables["schedule_stop_times"][[
        "schedule_stop_time_id", "departure_offset_sec", "value_available_at",
    ]].rename(columns={"value_available_at": "schedule_time_available_at"})
    result = result.merge(
        offsets, on="schedule_stop_time_id", how="left", validate="many_to_one"
    )
    start = pd.to_datetime(result["scheduled_start_utc"], errors="coerce", utc=True)
    offset = pd.to_numeric(result["departure_offset_sec"], errors="coerce")
    result["scheduled_departure_utc"] = start + pd.to_timedelta(offset, unit="s")
    available_columns = (
        "trip_published_at_utc", "pattern_published_at_utc",
        "schedule_published_at_utc", "schedule_time_available_at",
        "route_value_available_at", "route_stop_value_available_at",
    )
    _require_columns(result, available_columns, "planned feature context")
    result["known_schedule_available_at"] = pd.concat(
        [
            pd.to_datetime(result[column], errors="coerce", utc=True)
            for column in available_columns
        ],
        axis=1,
    ).max(axis=1, skipna=False)
    if "planned_vehicle_id" in result:
        _require_columns(
            tables["vehicles"], ("vehicle_id", "vehicle_type", "value_available_at"),
            "vehicles",
        )
        result = result.merge(
            tables["vehicles"][[
                "vehicle_id", "vehicle_type", "value_available_at",
            ]].rename(columns={"value_available_at": "planned_vehicle_available_at"}),
            left_on="planned_vehicle_id", right_on="vehicle_id",
            how="left", validate="many_to_one",
        ).rename(columns={"vehicle_type": "planned_vehicle_type"}).drop(
            columns=["vehicle_id"]
        )

    counts = tables["passenger_counts"]
    count_columns = [
        column for column in (
            "stop_event_id", "onboard_departure", "boardings", "alightings",
            "departure_assignment_id",
        ) if column in counts
    ]
    result = result.merge(
        counts[count_columns],
        on="stop_event_id", how="left", validate="one_to_one",
    )
    _require_columns(tables["trip_vehicle_assignments"], (
        "assignment_id", "capacity_snapshot",
    ), "trip_vehicle_assignments")
    assignments = tables["trip_vehicle_assignments"][[
        "assignment_id", "capacity_snapshot",
    ]]
    result = result.merge(
        assignments, left_on="departure_assignment_id", right_on="assignment_id",
        how="left", validate="many_to_one",
    ).drop(columns=["assignment_id"])
    if "delays" in tables:
        delays = tables["delays"]
        _require_columns(delays, ("stop_event_id", "value_available_at"), "delays")
        delay_columns = [
            column for column in ("stop_event_id", "departure_delay_sec", "value_available_at")
            if column in delays
        ]
        delays = delays[delay_columns].rename(columns={
            "value_available_at": "delay_value_available_at",
        })
        result = result.merge(delays, on="stop_event_id", how="left", validate="one_to_one")
    result["outcome_available_at_utc"] = result["outcome_available_at_utc"].fillna(
        result["event_value_available_at"]
    )
    if "context_events" in tables:
        result = _add_known_context_counts(result, tables["context_events"])
    return result


def _add_known_context_counts(operations, context_events):
    pd = _pd()
    _require_columns(context_events, (
        "context_event_id", "route_id", "stop_id", "starts_at_utc", "ends_at_utc",
        "announced_at_utc", "value_available_at",
    ), "context_events")
    if context_events["context_event_id"].duplicated().any():
        raise ValueError("context_events must have unique context_event_id values")
    event_columns = [
        "context_event_id", "route_id", "stop_id", "starts_at_utc", "ends_at_utc",
        "announced_at_utc", "value_available_at",
    ]
    candidates = []
    route_events = context_events.loc[context_events["route_id"].notna(), event_columns]
    if not route_events.empty:
        candidates.append(
            operations[["stop_event_id", "route_id", "scheduled_departure_utc"]]
            .merge(route_events, on="route_id", how="inner", validate="many_to_many")
        )
    stop_events = context_events.loc[context_events["stop_id"].notna(), event_columns]
    if not stop_events.empty:
        candidates.append(
            operations[["stop_event_id", "stop_id", "scheduled_departure_utc"]]
            .merge(stop_events, on="stop_id", how="inner", validate="many_to_many")
        )
    if not candidates:
        result = operations.copy()
        result["known_context_event_count"] = 0
        return result
    matches = pd.concat(candidates, ignore_index=True)
    scheduled = pd.to_datetime(matches["scheduled_departure_utc"], errors="coerce", utc=True)
    start = pd.to_datetime(matches["starts_at_utc"], errors="coerce", utc=True)
    end = pd.to_datetime(matches["ends_at_utc"], errors="coerce", utc=True)
    announced = pd.to_datetime(matches["announced_at_utc"], errors="coerce", utc=True)
    available = pd.to_datetime(matches["value_available_at"], errors="coerce", utc=True)
    matches = matches.loc[
        (scheduled >= start) & (scheduled <= end)
        & (announced <= scheduled) & (available <= scheduled)
    ].drop_duplicates(["stop_event_id", "context_event_id"])
    counts = matches.groupby("stop_event_id").size().rename(
        "known_context_event_count"
    ).reset_index()
    result = operations.merge(
        counts, on="stop_event_id", how="left", validate="one_to_one"
    )
    result["known_context_event_count"] = (
        result["known_context_event_count"].fillna(0).astype("int64")
    )
    return result


def _calendar_features(frame, timestamp_column="scheduled_departure_utc"):
    pd = _pd()
    result = frame.copy()
    timestamp = pd.to_datetime(result[timestamp_column], errors="coerce", utc=True)
    service_date = pd.to_datetime(result["service_date"], errors="coerce")
    result["scheduled_hour_utc"] = timestamp.dt.hour
    result["service_weekday"] = service_date.dt.dayofweek + 1
    result["is_weekend"] = result["service_weekday"] >= 6
    result["service_month"] = service_date.dt.month
    return result


def _past_only_history(frame, *, keys, time_column, target_column,
                       available_column, prefix):
    """Lag and mean over up to seven previous available observations."""
    pd = _pd()
    result = frame.copy().reset_index(drop=True)
    lag_column, mean_column = f"{prefix}_lag_1", f"{prefix}_mean_7"
    result[lag_column] = float("nan")
    result[mean_column] = float("nan")
    result["__history_time"] = pd.to_datetime(result[time_column], errors="coerce", utc=True)
    result["__history_available"] = pd.to_datetime(
        result[available_column], errors="coerce", utc=True
    )
    result["__history_target"] = result[target_column]
    result["__history_original_index"] = range(len(result))
    ordering = [*keys, "__history_time"]
    ordering.extend(column for column in ("trip_id", "stop_event_id") if column in result)
    result = result.sort_values(ordering, kind="mergesort", na_position="last")

    for _, group in result.groupby(list(keys), dropna=False, sort=False):
        pending = []
        history = []
        cursor = 0
        columns = [
            "__history_time", "__history_available", "__history_target",
            "__history_original_index",
        ]
        records = list(group[columns].itertuples(index=False, name=None))
        for row_position, (cutoff, _, _, current_index) in enumerate(records):
            if pd.isna(cutoff):
                continue
            while cursor < row_position:
                previous_time, previous_available, previous_value, _ = records[cursor]
                if pd.isna(previous_time) or previous_time >= cutoff:
                    break
                if not pd.isna(previous_value) and not pd.isna(previous_available):
                    heapq.heappush(pending, (
                        previous_available.value, cursor, previous_time.value,
                        float(previous_value),
                    ))
                cursor += 1
            while pending and pending[0][0] <= cutoff.value:
                _, previous_position, previous_time, previous_value = heapq.heappop(pending)
                insort(history, (previous_time, previous_position, previous_value))
                if len(history) > 7:
                    del history[:-7]
            if history:
                result.at[current_index, lag_column] = history[-1][2]
                result.at[current_index, mean_column] = sum(
                    item[2] for item in history
                ) / len(history)
    return result.drop(columns=[
        "__history_time", "__history_available", "__history_target",
        "__history_original_index",
    ])


def build_delay_features(operations, *, severity_thresholds_sec):
    """Create stop-event labels and pre-departure features.

    Four increasing configurable boundaries in seconds define the five labels
    ON_TIME, MINOR_DELAY, MODERATE_DELAY, MAJOR_DELAY, and SEVERE_DELAY.
    """
    pd = _pd()
    thresholds = validate_severity_thresholds(severity_thresholds_sec)
    _require_columns(operations, (
        "service_date", "route_id", "direction_id", "stop_id", "trip_id",
        "stop_event_id", "scheduled_departure_utc", "actual_departure_utc",
        "outcome_available_at_utc",
    ), "operations")
    _assert_known_schedule_as_of(operations)
    result = _calendar_features(operations)
    actual = pd.to_datetime(result["actual_departure_utc"], errors="coerce", utc=True)
    scheduled = pd.to_datetime(result["scheduled_departure_utc"], errors="coerce", utc=True)
    result["delay_sec"] = (actual - scheduled).dt.total_seconds().clip(lower=0)
    labels = ["ON_TIME", "MINOR_DELAY", "MODERATE_DELAY", "MAJOR_DELAY", "SEVERE_DELAY"]
    result["delay_severity"] = pd.cut(
        result["delay_sec"], bins=[-float("inf"), *thresholds, float("inf")],
        labels=labels, right=True,
    ).astype("string")
    result["feature_cutoff_at"] = scheduled
    result["target_available_at"] = pd.to_datetime(
        result["outcome_available_at_utc"], errors="coerce", utc=True
    )
    result = _past_only_history(
        result, keys=["route_id", "direction_id", "stop_id"],
        time_column="feature_cutoff_at", target_column="delay_sec",
        available_column="target_available_at", prefix="historical_delay_sec",
    )
    contract = FEATURE_CONTRACTS["delay_severity"]
    output_columns = [
        *contract.predictor_features, "service_date", "trip_id", "stop_event_id",
        "feature_cutoff_at", "known_schedule_available_at", "target_available_at",
        "delay_sec", "delay_severity",
    ]
    return result[[column for column in output_columns if column in result.columns]]


def build_demand_forecast_features(trip_demand):
    """Add history-only features to one-trip demand targets."""
    _pd()
    _require_columns(trip_demand, (
        "service_date", "route_id", "direction_id", "trip_id",
        "scheduled_departure_utc", "passenger_demand", "target_available_at",
    ), "trip_demand")
    _assert_known_schedule_as_of(trip_demand)
    result = _calendar_features(trip_demand)
    result["feature_cutoff_at"] = result["scheduled_departure_utc"]
    result = _past_only_history(
        result,
        keys=["route_id", "direction_id", "scheduled_hour_utc", "service_weekday"],
        time_column="feature_cutoff_at", target_column="passenger_demand",
        available_column="target_available_at", prefix="historical_demand",
    )
    contract = FEATURE_CONTRACTS["demand_forecast"]
    output_columns = [
        *contract.predictor_features, "service_date", "trip_id",
        "operational_departure_id", "scheduled_departure_utc",
        "feature_cutoff_at", "known_schedule_available_at",
        "target_available_at", "passenger_demand",
    ]
    return result[[column for column in output_columns if column in result.columns]]


def aggregate_trip_demand(journeys, trips, route_patterns, schedules):
    """Aggregate observed passenger journeys into trip-level served-demand targets."""
    pd = _pd()
    _require_columns(journeys, (
        "trip_id", "passenger_count", "value_available_at",
    ), "passenger_journeys")
    _require_columns(trips, (
        "trip_id", "route_id", "pattern_id", "schedule_id", "scheduled_start_utc",
        "service_date", "published_at_utc",
    ), "trips")
    _require_columns(route_patterns, ("pattern_id", "direction_id"), "route_patterns")
    _require_columns(
        route_patterns, ("pattern_id", "published_at_utc"), "route_patterns"
    )
    _require_columns(schedules, ("schedule_id", "published_at_utc"), "schedules")
    journey_rows = journeys.loc[journeys["trip_id"].notna()].copy()
    journey_rows["passenger_count"] = pd.to_numeric(
        journey_rows["passenger_count"], errors="coerce"
    )
    demand = journey_rows.groupby("trip_id", dropna=False).agg(
        target_available_at=("value_available_at", "max")
    )
    demand["passenger_demand"] = journey_rows.groupby(
        "trip_id", dropna=False
    )["passenger_count"].sum(min_count=1)
    demand = demand.reset_index()
    trip_columns = [
        column for column in (
            "trip_id", "route_id", "pattern_id", "service_date",
            "schedule_id", "scheduled_start_utc", "published_at_utc",
            "operational_departure_id",
        ) if column in trips
    ]
    demand = demand.merge(
        trips[trip_columns], on="trip_id", how="inner", validate="one_to_one"
    )
    patterns = route_patterns[[
        "pattern_id", "direction_id", "published_at_utc",
    ]].rename(columns={"published_at_utc": "pattern_published_at_utc"})
    demand = demand.merge(
        patterns, on="pattern_id", how="left", validate="many_to_one"
    )
    demand = demand.merge(
        schedules[["schedule_id", "published_at_utc"]].rename(
            columns={"published_at_utc": "schedule_published_at_utc"}
        ),
        on="schedule_id", how="left", validate="many_to_one",
    )
    demand["known_schedule_available_at"] = pd.concat(
        [
            pd.to_datetime(demand["published_at_utc"], errors="coerce", utc=True),
            pd.to_datetime(demand["pattern_published_at_utc"], errors="coerce", utc=True),
            pd.to_datetime(demand["schedule_published_at_utc"], errors="coerce", utc=True),
        ],
        axis=1,
    ).max(axis=1, skipna=False)
    return demand.rename(columns={"scheduled_start_utc": "scheduled_departure_utc"})


def build_occupancy_features(operations):
    """Compute observed segment occupancy without inferring unknown capacity."""
    _require_columns(operations, (
        "service_date", "stop_event_id", "onboard_departure", "capacity_snapshot",
    ), "operations")
    result = operations.copy()
    pd = _pd()
    load = pd.to_numeric(result["onboard_departure"], errors="coerce")
    capacity = pd.to_numeric(result["capacity_snapshot"], errors="coerce")
    valid = load.notna() & capacity.notna() & (capacity > 0)
    result["capacity_utilization"] = float("nan")
    result.loc[valid, "capacity_utilization"] = load[valid] / capacity[valid]
    result["occupancy_status"] = "UNAVAILABLE"
    result.loc[valid, "occupancy_status"] = "AVAILABLE"
    result["over_capacity"] = pd.Series(pd.NA, index=result.index, dtype="boolean")
    result.loc[valid, "over_capacity"] = load[valid] > capacity[valid]
    return result


def build_headway_features(operations):
    """Calculate actual and scheduled headways at route/direction/physical-stop grain."""
    pd = _pd()
    _require_columns(operations, (
        "service_date", "route_id", "direction_id", "stop_id", "trip_id",
        "stop_event_id", "actual_departure_utc", "scheduled_departure_utc",
    ), "operations")
    result = operations.copy()
    keys = ["route_id", "direction_id", "stop_id"]
    result["__actual"] = pd.to_datetime(result["actual_departure_utc"], errors="coerce", utc=True)
    result["__scheduled"] = pd.to_datetime(
        result["scheduled_departure_utc"], errors="coerce", utc=True
    )
    result = result.sort_values(
        [*keys, "__actual", "trip_id", "stop_event_id"],
        kind="mergesort", na_position="last",
    )
    previous_actual = result.groupby(keys, dropna=False)["__actual"].shift(1)
    result["actual_headway_sec"] = (result["__actual"] - previous_actual).dt.total_seconds()
    scheduled = result.sort_values(
        [*keys, "__scheduled", "trip_id", "stop_event_id"],
        kind="mergesort", na_position="last",
    )
    previous_scheduled = scheduled.groupby(keys, dropna=False)["__scheduled"].shift(1)
    scheduled["scheduled_headway_sec"] = (
        scheduled["__scheduled"] - previous_scheduled
    ).dt.total_seconds()
    result["scheduled_headway_sec"] = scheduled["scheduled_headway_sec"].reindex(result.index)
    result["headway_deviation_sec"] = (
        result["actual_headway_sec"] - result["scheduled_headway_sec"]
    )
    return result.drop(columns=["__actual", "__scheduled"])


def build_delay_analytics(operations):
    """Summarize measured schedule deviation by date, route, and direction."""
    pd = _pd()
    _require_columns(operations, (
        "service_date", "route_id", "direction_id", "scheduled_departure_utc",
        "actual_departure_utc",
    ), "operations")
    actual = pd.to_datetime(operations["actual_departure_utc"], errors="coerce", utc=True)
    scheduled = pd.to_datetime(
        operations["scheduled_departure_utc"], errors="coerce", utc=True
    )
    work = operations.copy()
    work["delay_sec"] = (actual - scheduled).dt.total_seconds().clip(lower=0)
    work = work.loc[work["delay_sec"].notna()]
    keys = ["service_date", "route_id", "direction_id"]
    return work.groupby(keys, dropna=False).agg(
        observed_stop_events=("delay_sec", "count"),
        mean_delay_sec=("delay_sec", "mean"),
        max_delay_sec=("delay_sec", "max"),
        positive_delay_fraction=("delay_sec", lambda values: (values > 0).mean()),
    ).reset_index()


def build_route_clustering_features(operations, *, trip_demand=None, route_dimensions=None):
    """Build training-window route/direction vectors; does not fit or emit clusters."""
    pd = _pd()
    _require_columns(operations, (
        "service_date", "route_id", "direction_id", "stop_id",
        "operational_departure_id", "onboard_departure", "capacity_snapshot",
        "scheduled_departure_utc", "actual_departure_utc", "distance_km",
    ), "operations")
    work = operations.copy()
    dates = pd.to_datetime(work["service_date"], errors="coerce")
    work = work.loc[(dates >= "2025-01-01") & (dates < "2026-01-01")].copy()
    work["__load"] = pd.to_numeric(work["onboard_departure"], errors="coerce")
    work["__capacity"] = pd.to_numeric(work["capacity_snapshot"], errors="coerce")
    work["__utilization"] = work["__load"] / work["__capacity"].where(work["__capacity"] > 0)
    actual = pd.to_datetime(work["actual_departure_utc"], errors="coerce", utc=True)
    scheduled = pd.to_datetime(work["scheduled_departure_utc"], errors="coerce", utc=True)
    work["__delay"] = (actual - scheduled).dt.total_seconds().clip(lower=0)
    headway = build_headway_features(work)
    work["__headway_abs_deviation"] = headway["headway_deviation_sec"].abs()
    result = work.groupby(["route_id", "direction_id"], dropna=False).agg(
        training_stop_observations=("stop_id", "count"),
        training_departures=("operational_departure_id", "nunique"),
        mean_observed_load=("__load", "mean"),
        mean_capacity_utilization=("__utilization", "mean"),
        mean_delay_sec=("__delay", "mean"),
        mean_route_distance_km=("distance_km", "mean"),
        mean_abs_headway_deviation_sec=("__headway_abs_deviation", "mean"),
        distinct_stops=("stop_id", "nunique"),
    ).reset_index()
    if trip_demand is not None:
        _require_columns(
            trip_demand, ("route_id", "direction_id", "trip_id", "passenger_demand"),
            "trip_demand",
        )
        demand_dates = pd.to_datetime(trip_demand["service_date"], errors="coerce")
        training_demand = trip_demand.loc[
            (demand_dates >= "2025-01-01") & (demand_dates < "2026-01-01")
        ]
        demand_metrics = training_demand.groupby(
            ["route_id", "direction_id"], dropna=False
        ).agg(
            supported_demand_trips=("trip_id", "nunique"),
            mean_served_demand_per_trip=("passenger_demand", "mean"),
        ).reset_index()
        result = result.merge(
            demand_metrics, on=["route_id", "direction_id"], how="left",
            validate="one_to_one",
        )
    if route_dimensions is not None:
        _require_columns(route_dimensions, ("route_id",), "route_dimensions")
        result = result.merge(
            route_dimensions, on="route_id", how="left", validate="many_to_one"
        )
    return result


def build_feature_frames_from_tables(tables, *, severity_thresholds_sec):
    """Build all task outputs from accepted schema-backed table projections."""
    operations = prepare_operation_features(tables)
    trip_demand = aggregate_trip_demand(
        tables["passenger_journeys"], tables["trips"], tables["route_patterns"],
        tables["schedules"],
    )
    routes = tables["routes"]
    route_dimensions = routes[[
        column for column in (
            "route_id", "route_code", "mode", "service_type",
            "social_service_required",
        ) if column in routes
    ]]
    return build_feature_frames(
        operations, trip_demand,
        severity_thresholds_sec=severity_thresholds_sec,
        route_dimensions=route_dimensions,
    )


def build_feature_frames(operations, trip_demand, *, severity_thresholds_sec,
                         route_dimensions=None):
    """Build equivalent task outputs from prepared projections."""
    return {
        "delay_severity": build_delay_features(
            operations, severity_thresholds_sec=severity_thresholds_sec
        ),
        "demand_forecast": build_demand_forecast_features(trip_demand),
        "occupancy": build_occupancy_features(operations),
        "headway": build_headway_features(operations),
        "delay_analytics": build_delay_analytics(operations),
        "route_clustering": build_route_clustering_features(
            operations, trip_demand=trip_demand, route_dimensions=route_dimensions
        ),
    }


def assign_feature_splits(frame, *, date_column="service_date"):
    """Return disjoint half-open chronological splits using the shared contract."""
    _require_columns(frame, (date_column,), "frame")
    pd = _pd()
    dates = pd.to_datetime(frame[date_column], errors="coerce").dt.date
    result = {}
    for name, (start, end) in CHRONOLOGICAL_SPLITS.items():
        lower, upper = pd.Timestamp(start).date(), pd.Timestamp(end).date()
        result[name] = frame.loc[(dates >= lower) & (dates < upper)].copy()
    return result


def write_feature_frames(feature_frames, output_root):
    """Write task-scoped Parquet features without sharing Spark-generated data."""
    from pathlib import Path

    root = Path(output_root)
    required = set(FEATURE_CONTRACTS)
    missing = required - feature_frames.keys()
    if missing:
        raise KeyError(f"Missing feature task frame(s): {', '.join(sorted(missing))}")
    unknown = feature_frames.keys() - required
    if unknown:
        raise ValueError(f"Unknown feature task(s): {', '.join(sorted(unknown))}")
    for task, frame in feature_frames.items():
        if task == "route_clustering":
            target = root / f"task={task}" / "training_window=2025"
            target.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(target / "part-00000.parquet", index=False)
            continue
        for split, current in assign_feature_splits(frame).items():
            target = root / f"task={task}" / f"split={split}"
            target.mkdir(parents=True, exist_ok=True)
            current.to_parquet(target / "part-00000.parquet", index=False)


def run_feature_engineering(tables, output_root, *, severity_thresholds_sec):
    """Calculate and write the independent Python feature outputs."""
    feature_frames = build_feature_frames_from_tables(
        tables, severity_thresholds_sec=severity_thresholds_sec
    )
    write_feature_frames(feature_frames, output_root)

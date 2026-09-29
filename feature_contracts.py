"""Stable, implementation-neutral contracts for the two feature pipelines."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FeatureContract:
    name: str
    output_features: tuple[str, ...]
    predictor_features: tuple[str, ...]
    source_tables: tuple[str, ...]
    source_columns: tuple[str, ...]
    transformation: str
    grain: str
    target: str
    null_handling: str
    leakage_rule: str
    split_semantics: str


CHRONOLOGICAL_SPLITS = {
    "train": ("2025-01-01", "2026-01-01"),
    "validation": ("2026-01-01", "2026-04-01"),
    "test": ("2026-04-01", "2026-07-01"),
}

FEATURE_CONTRACTS = {
    "delay_severity": FeatureContract(
        name="delay_severity",
        output_features=("scheduled_hour_utc", "service_weekday", "is_weekend",
                         "service_month", "historical_delay_sec_lag_1",
                         "historical_delay_sec_mean_7", "planned_vehicle_status",
                         "planned_vehicle_type", "known_context_event_count",
                         "delay_sec", "delay_severity"),
        predictor_features=("route_id", "route_code", "mode", "service_type",
                            "pattern_id", "direction_id", "stop_id",
                            "stop_sequence", "distance_km", "planned_vehicle_status",
                            "planned_vehicle_type", "known_context_event_count",
                            "scheduled_departure_utc",
                            "scheduled_hour_utc", "service_weekday", "is_weekend",
                            "service_month", "historical_delay_sec_lag_1",
                            "historical_delay_sec_mean_7"),
        source_tables=("trips", "trip_stop_events", "route_patterns", "route_stops",
                       "routes", "schedules", "schedule_stop_times", "context_events",
                       "vehicles"),
        source_columns=("service_date", "route_id", "pattern_id", "direction_id",
                        "stop_id", "stop_sequence", "scheduled_start_utc",
                        "schedule_id", "published_at_utc", "departure_offset_sec",
                        "actual_departure_utc", "outcome_available_at_utc",
                        "value_available_at",
                        "planned_vehicle_id", "planned_vehicle_status", "vehicle_type",
                        "context_event_id", "event_type", "starts_at_utc", "ends_at_utc",
                        "announced_at_utc", "schedule_stop_time_id", "route_stop_id"),
        transformation="scheduled departure = trip scheduled_start_utc + stop departure_offset_sec; "
                       "delay_sec=max(0, actual-scheduled); five severity labels use four supplied "
                       "increasing boundaries; history is last available earlier stop outcome and "
                       "mean of up to seven available earlier outcomes",
        grain="one observed trip stop event",
        target="delay_severity, derived from max(0, actual departure - scheduled departure); "
               "four strictly increasing configurable second thresholds define five labels",
        null_handling="Missing scheduled or actual time leaves target null; historical features "
                      "use only non-null available outcomes; unknown planned vehicle remains "
                      "null, and context count is zero only when the source table is present",
        leakage_rule="Features are as of scheduled departure; current and later outcomes are labels "
                     "only. History is strictly earlier and value_available_at <= cutoff.",
        split_semantics="service_date; train 2025, validation 2026-Q1, test 2026-Q2",
    ),
    "demand_forecast": FeatureContract(
        name="demand_forecast",
        output_features=("scheduled_hour_utc", "service_weekday", "is_weekend",
                         "service_month", "historical_demand_lag_1",
                         "historical_demand_mean_7", "passenger_demand"),
        predictor_features=("route_id", "direction_id", "scheduled_hour_utc",
                            "service_weekday", "is_weekend", "service_month",
                            "historical_demand_lag_1", "historical_demand_mean_7"),
        source_tables=("passenger_journeys", "trips", "route_patterns", "schedules"),
        source_columns=("passenger_count", "trip_id", "route_id", "direction_id",
                        "service_date", "schedule_id", "scheduled_start_utc",
                        "published_at_utc", "boarded_at_utc", "value_available_at"),
        transformation="sum journey passenger_count per trip; lag latest earlier trip "
                       "demand and average up to seven latest earlier available trips in "
                       "same route/direction/UTC-hour/weekday group",
        grain="one trip / service date",
        target="passenger_demand, sum of journey passenger_count",
        null_handling="Missing demand remains null, not zero; lag/rolling values are null without "
                      "available history",
        leakage_rule="Prior demand only, ordered by scheduled time and stable trip_id; an outcome "
                      "must be available by the current scheduled-departure cutoff.",
        split_semantics="service_date; train 2025, validation 2026-Q1, test 2026-Q2",
    ),
    "occupancy": FeatureContract(
        name="occupancy",
        output_features=("capacity_utilization", "occupancy_status", "over_capacity"),
        predictor_features=(),
        source_tables=("passenger_counts", "trip_vehicle_assignments"),
        source_columns=("onboard_departure", "departure_assignment_id", "assignment_id",
                        "capacity_snapshot"),
        transformation="onboard_departure / departure assignment capacity_snapshot; preserve "
                       "ratios above 1 and flag over-capacity without clipping",
        grain="one observed stop event / departure segment",
        target="capacity_utilization = onboard_departure / capacity_snapshot",
        null_handling="Null load, missing assignment, or non-positive/missing capacity yields "
                      "null utilization and UNAVAILABLE status; never infer planned capacity",
        leakage_rule="Descriptive observed outcome only; do not use same-event load as a "
                      "pre-event predictor.",
        split_semantics="service_date; split using the associated trip stop event",
    ),
    "headway": FeatureContract(
        name="headway",
        output_features=("actual_headway_sec", "scheduled_headway_sec",
                         "headway_deviation_sec"),
        predictor_features=(),
        source_tables=("trip_stop_events", "trips", "route_patterns", "route_stops",
                       "schedule_stop_times"),
        source_columns=("actual_departure_utc", "scheduled_start_utc",
                        "departure_offset_sec", "route_id", "direction_id", "stop_id",
                        "stop_sequence", "trip_id", "stop_event_id"),
        transformation="timestamp differences from previous actual/scheduled departure "
                       "partitioned by route/direction/physical stop",
        grain="one observed stop event; prior departure at same route/direction/stop",
        target="actual_headway_sec and scheduled_headway_sec",
        null_handling="First departure has null headway; missing actual/scheduled time is not imputed",
        leakage_rule="Only the immediately preceding event under timestamp, trip_id, stop_event_id "
                     "ordering is used; current departure is descriptive, not a predictor.",
        split_semantics="service_date; history is chronological and never centered",
    ),
    "delay_analytics": FeatureContract(
        name="delay_analytics",
        output_features=("observed_stop_events", "mean_delay_sec", "max_delay_sec",
                         "positive_delay_fraction"),
        predictor_features=(),
        source_tables=("trip_stop_events", "trips", "route_patterns",
                       "schedule_stop_times"),
        source_columns=("service_date", "route_id", "direction_id", "actual_departure_utc",
                        "scheduled_start_utc", "departure_offset_sec"),
        transformation="service-date/route/direction aggregate of max(0, actual-scheduled) "
                       "over events with both timestamps",
        grain="service date / route / direction",
        target="observed stop-event delay metrics",
        null_handling="Only events with both actual and scheduled times contribute; no event is "
                      "silently imputed as on-time",
        leakage_rule="Descriptive outcomes; exclude aggregates from predictors unless recomputed "
                      "from prior, available records.",
        split_semantics="service_date; train 2025, validation 2026-Q1, test 2026-Q2",
    ),
    "route_clustering": FeatureContract(
        name="route_clustering",
        output_features=("training_stop_observations", "training_departures",
                         "mean_observed_load", "mean_capacity_utilization",
                         "mean_delay_sec", "mean_route_distance_km",
                         "mean_abs_headway_deviation_sec", "distinct_stops",
                         "supported_demand_trips", "mean_served_demand_per_trip"),
        predictor_features=("mean_observed_load", "mean_capacity_utilization",
                            "mean_delay_sec", "mean_route_distance_km",
                            "mean_abs_headway_deviation_sec", "distinct_stops",
                            "mean_served_demand_per_trip", "mode", "service_type",
                            "social_service_required"),
        source_tables=("routes", "route_patterns", "route_stops", "trips",
                       "trip_stop_events", "passenger_counts", "trip_vehicle_assignments",
                       "passenger_journeys"),
        source_columns=("route_id", "direction_id", "service_type", "mode",
                        "social_service_required", "distance_km", "stop_id",
                        "onboard_departure", "capacity_snapshot", "actual_departure_utc",
                        "passenger_count", "trip_id", "service_date"),
        transformation="aggregate route/direction counts, observed load, valid capacity "
                       "utilization, delay, stops, and journey demand using 2025 history only",
        grain="route / direction, aggregated on training-period history",
        target="unsupervised feature vector; no cluster label is generated here",
        null_handling="Unavailable metrics remain null with explicit support counts; social "
                      "service is context, not a low-load label",
        leakage_rule="Historical metrics use only 2025 training observations; no generator "
                      "archetype or validation/test outcome is used.",
        split_semantics="Fit inputs restricted to service_date 2025-01-01..2025-12-31",
    ),
}


def validate_severity_thresholds(values):
    thresholds = tuple(values)
    if len(thresholds) != 4 or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or value < 0
        for value in thresholds
    ) or any(left >= right for left, right in zip(thresholds, thresholds[1:])):
        raise ValueError(
            "severity thresholds must contain four increasing finite nonnegative values"
        )
    return thresholds

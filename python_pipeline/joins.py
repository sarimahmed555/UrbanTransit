"""Independent, cardinality-checked Pandas integration joins."""

JOIN_KEYS = {
    "tickets_passengers": ("passenger_id", "passenger_id"),
    "tickets_trips": ("trip_id", "trip_id"),
    "trips_routes": ("route_id", "route_id"),
    "trips_vehicles": ("planned_vehicle_id", "vehicle_id"),
    "trips_schedules": ("schedule_id", "schedule_id"),
    "routes_route_stops": ("route_id", "route_id"),
    "route_stops_stops": ("stop_id", "stop_id"),
    "trips_delays": ("trip_id", "trip_id"),
    "trips_passenger_counts": ("trip_id", "trip_id"),
    "stops_context": ("stop_id", "stop_id"),
}


def _pd():
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Pandas is required for integration joins") from exc
    return pd


def join_tables(left, right, *, left_on: str, right_on: str, how="left", validate="many_to_one",
                suffixes=("", "_right")):
    if left_on not in left.columns or right_on not in right.columns:
        raise KeyError(f"Join keys must exist: {left_on}, {right_on}")
    return left.merge(right, how=how, left_on=left_on, right_on=right_on,
                      validate=validate, suffixes=suffixes, indicator="_join_status")


def integrate(tables: dict[str, object]) -> dict[str, object]:
    """Build separately named projections, never a fact-to-fact cartesian product."""
    required = {"tickets", "passengers", "trips", "routes", "vehicles", "schedules",
                "route_stops", "stops", "delays", "passenger_counts"}
    missing = required - tables.keys()
    if missing:
        raise KeyError(f"Missing integration table(s): {', '.join(sorted(missing))}")
    return {
        "tickets_passengers": join_tables(tables["tickets"], tables["passengers"],
                                          left_on="passenger_id", right_on="passenger_id"),
        "tickets_trips": join_tables(tables["tickets"], tables["trips"],
                                      left_on="trip_id", right_on="trip_id"),
        "trips_routes": join_tables(tables["trips"], tables["routes"],
                                     left_on="route_id", right_on="route_id"),
        "trips_vehicles": join_tables(tables["trips"], tables["vehicles"],
                                       left_on="planned_vehicle_id", right_on="vehicle_id"),
        "trips_schedules": join_tables(tables["trips"], tables["schedules"],
                                        left_on="schedule_id", right_on="schedule_id"),
        "routes_route_stops": join_tables(tables["routes"], tables["route_stops"],
                                           left_on="route_id", right_on="route_id"),
        "route_stops_stops": join_tables(tables["route_stops"], tables["stops"],
                                          left_on="stop_id", right_on="stop_id"),
        "trips_delays": join_tables(tables["trips"], tables["delays"],
                                     left_on="trip_id", right_on="trip_id"),
        "trips_passenger_counts": join_tables(tables["trips"], tables["passenger_counts"],
                                               left_on="trip_id", right_on="trip_id"),
    }

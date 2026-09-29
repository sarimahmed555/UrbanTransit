"""Cardinality-aware Spark SQL integration and chronological feature base."""

from pyspark.sql import functions as F


def register_views(tables: dict, prefix: str = "uti_") -> None:
    for name, frame in tables.items():
        frame.createOrReplaceTempView(f"{prefix}{name}")


def build_integrated_views(spark, prefix: str = "uti_") -> dict:
    """Separate projections avoid fact-to-fact cartesian products."""
    return {
        "trip_context": spark.sql(f"""
            SELECT t.*, r.route_code, r.mode, p.direction_id,
                   c.context_event_id, c.event_type, c.value_available_at AS context_available_at
            FROM {prefix}trips t
            LEFT JOIN {prefix}routes r ON t.route_id = r.route_id
            LEFT JOIN {prefix}route_patterns p ON t.pattern_id = p.pattern_id
            LEFT JOIN {prefix}context_events c
              ON t.route_id = c.route_id
             AND t.scheduled_start_utc BETWEEN c.starts_at_utc AND c.ends_at_utc
             AND c.value_available_at <= t.scheduled_start_utc
        """),
        "trip_operations": spark.sql(f"""
            SELECT t.operational_departure_id, t.trip_id, t.service_date, t.route_id,
                   e.stop_event_id, e.route_stop_id, e.stop_sequence,
                   e.actual_arrival_utc, e.actual_departure_utc,
                   d.arrival_delay_sec, d.departure_delay_sec,
                   pc.boardings, pc.alightings, pc.onboard_departure
            FROM {prefix}trips t
            LEFT JOIN {prefix}trip_stop_events e ON t.trip_id = e.trip_id
            LEFT JOIN {prefix}delays d ON e.stop_event_id = d.stop_event_id
            LEFT JOIN {prefix}passenger_counts pc ON e.stop_event_id = pc.stop_event_id
        """),
        "demand_operations": spark.sql(f"""
            SELECT j.journey_id, j.passenger_id, j.trip_id, j.service_date,
                   j.origin_route_stop_id, j.destination_route_stop_id,
                   q.request_id, q.request_available_at_utc,
                   t.operational_departure_id, t.route_id
            FROM {prefix}passenger_journeys j
            LEFT JOIN {prefix}demand_requests q ON j.request_id = q.request_id
            LEFT JOIN {prefix}trips t ON j.trip_id = t.trip_id
        """),
    }


def chronological_split(frame, date_column="service_date"):
    return {
        split: frame.filter(
            (F.col(date_column) >= F.lit(start)) &
            (F.col(date_column) < F.lit(end))
        )
        for split, (start, end) in {
            "train": ("2025-01-01", "2026-01-01"),
            "validation": ("2026-01-01", "2026-04-01"),
            "test": ("2026-04-01", "2026-07-01"),
        }.items()
    }

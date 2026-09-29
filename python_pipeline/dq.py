"""Non-destructive data-quality detectors for the approved transport rules."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DQRule:
    code: str
    issue: str


RULES = {
    "missing_id": DQRule("DQ-MISSING-ID", "missing required identifier"),
    "invalid_stop": DQRule("DQ-INVALID-STOP", "invalid stop reference"),
    "duplicate_ticket": DQRule("DQ-DUPLICATE-TICKET", "duplicate ticket"),
    "duplicate_trip": DQRule("DQ-DUPLICATE-TRIP", "duplicate trip"),
    "negative_passenger_count": DQRule("DQ-NEGATIVE-COUNT", "negative passenger count"),
    "invalid_timestamp": DQRule("DQ-INVALID-TIMESTAMP", "invalid or impossible timestamp"),
    "departure_before_arrival": DQRule("DQ-DEPARTURE-BEFORE-ARRIVAL", "departure before arrival"),
    "capacity_violation": DQRule("DQ-CAPACITY", "capacity violation"),
    "invalid_delay": DQRule("DQ-INVALID-DELAY", "invalid delay"),
    "missing_vehicle": DQRule("DQ-MISSING-VEHICLE", "missing vehicle assignment"),
    "broken_stop_sequence": DQRule("DQ-STOP-SEQUENCE", "broken stop sequence"),
    "invalid_distance": DQRule("DQ-DISTANCE", "invalid distance"),
    "unknown_passenger": DQRule("DQ-UNKNOWN-PASSENGER", "unknown passenger"),
    "missing_trip": DQRule("DQ-MISSING-TRIP", "missing trip"),
}


def _pd():
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Pandas is required for DQ evaluation") from exc
    return pd


def _issues(frame, mask, rule: DQRule, *, correction: Any = None, status: str = "FLAGGED"):
    pd = _pd()
    rows = frame.loc[mask].copy()
    if rows.empty:
        return pd.DataFrame(columns=["source_row_id", "original_value", "issue", "rule", "correction", "status"])
    value_column = next((c for c in ("value", "id", "timestamp", "count", "delay", "distance") if c in rows), None)
    return pd.DataFrame(
        {
            "source_row_id": rows.get("source_row_id", rows.index.astype(str)).astype(str).to_list(),
            "original_value": rows[value_column].to_list() if value_column else [None] * len(rows),
            "issue": rule.issue,
            "rule": rule.code,
            "correction": correction,
            "status": status,
        }
    )


def missing_ids(frame, column: str, rule: DQRule = RULES["missing_id"]):
    return _issues(frame, frame[column].isna() | frame[column].astype("string").eq(""), rule)


def invalid_references(frame, column: str, valid_ids, rule: DQRule):
    return _issues(frame, frame[column].notna() & ~frame[column].isin(set(valid_ids)), rule)


def duplicate_keys(frame, column: str, rule: DQRule):
    return _issues(frame, frame[column].duplicated(keep=False), rule)


def numeric_violations(frame, column: str, predicate, rule: DQRule):
    return _issues(frame, predicate(frame[column]), rule)


def timestamp_violations(frame, column: str, *, minimum=None, maximum=None):
    pd = _pd()
    parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
    mask = parsed.isna()
    if minimum is not None:
        mask |= parsed < pd.Timestamp(minimum, tz="UTC")
    if maximum is not None:
        mask |= parsed > pd.Timestamp(maximum, tz="UTC")
    return _issues(frame, mask, RULES["invalid_timestamp"])


def departure_before_arrival(frame, arrival="actual_arrival_utc", departure="actual_departure_utc"):
    pd = _pd()
    arrival_values = pd.to_datetime(frame[arrival], errors="coerce", utc=True)
    departure_values = pd.to_datetime(frame[departure], errors="coerce", utc=True)
    return _issues(frame, arrival_values.notna() & departure_values.notna() & (departure_values < arrival_values),
                   RULES["departure_before_arrival"])


def missing_vehicle_assignments(frame, column="vehicle_id"):
    return _issues(frame, frame[column].isna() | frame[column].astype("string").eq(""),
                   RULES["missing_vehicle"])


def capacity_violations(frame, load="onboard_departure", capacity="capacity_snapshot"):
    return _issues(frame, frame[load].notna() & frame[capacity].notna() & (frame[load] > frame[capacity]),
                   RULES["capacity_violation"])


def broken_stop_sequences(frame, group="trip_id", sequence="stop_sequence"):
    expected = frame.groupby(group)[sequence].transform(
        lambda values: values.sort_values().diff().dropna().ne(1).any()
    )
    return _issues(frame, expected.fillna(False), RULES["broken_stop_sequence"])


def referential_rules(frame, *, known_stops=None, known_passengers=None, known_trips=None):
    results = []
    if "stop_id" in frame and known_stops is not None:
        results.append(invalid_references(frame, "stop_id", known_stops, RULES["invalid_stop"]))
    if "passenger_id" in frame and known_passengers is not None:
        results.append(invalid_references(frame, "passenger_id", known_passengers, RULES["unknown_passenger"]))
    if "trip_id" in frame and known_trips is not None:
        results.append(invalid_references(frame, "trip_id", known_trips, RULES["missing_trip"]))
    return results


def evaluate_rules(frame, *, known_stops=(), known_passengers=(), known_trips=()):
    """Run row-local and reference checks; return audit evidence without mutating input."""
    results = []
    for column in ("ticket_id", "trip_id", "passenger_id"):
        if column in frame:
            results.append(missing_ids(frame, column))
    for column, rule in (("ticket_id", RULES["duplicate_ticket"]), ("trip_id", RULES["duplicate_trip"])):
        if column in frame:
            results.append(duplicate_keys(frame, column, rule))
    if "passenger_count" in frame:
        results.append(numeric_violations(frame, "passenger_count", lambda s: s < 0, RULES["negative_passenger_count"]))
    if "delay_sec" in frame:
        results.append(numeric_violations(frame, "delay_sec", lambda s: s < 0, RULES["invalid_delay"]))
    if "distance_km" in frame:
        results.append(numeric_violations(frame, "distance_km", lambda s: s <= 0, RULES["invalid_distance"]))
    if "vehicle_id" in frame:
        results.append(missing_vehicle_assignments(frame))
    if {"onboard_departure", "capacity_snapshot"} <= set(frame):
        results.append(capacity_violations(frame))
    if {"trip_id", "stop_sequence"} <= set(frame):
        results.append(broken_stop_sequences(frame))
    for column in ("actual_arrival_utc", "actual_departure_utc", "event_time"):
        if column in frame:
            results.append(timestamp_violations(frame, column))
    if {"actual_arrival_utc", "actual_departure_utc"} <= set(frame):
        results.append(departure_before_arrival(frame))
    results.extend(referential_rules(frame, known_stops=known_stops, known_passengers=known_passengers,
                                     known_trips=known_trips))
    nonempty = [result for result in results if not result.empty]
    if not nonempty:
        return _pd().DataFrame(columns=["source_row_id", "original_value", "issue", "rule", "correction", "status"])
    return _pd().concat(nonempty, ignore_index=True)

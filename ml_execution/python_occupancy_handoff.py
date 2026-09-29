"""Derive bounded occupancy/crowding Python features from certified raw CSV."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import heapq
import json
import math
import sqlite3
import tempfile
from pathlib import Path

from feature_contracts import CHRONOLOGICAL_SPLITS
from ml_execution.contracts import PERIODS, contract_digest, load_features, validate_manifest
from ml_execution.python_delay_handoff import (
    SEED,
    _iso,
    _iter_rows,
    _table_files,
    _timestamp as _parse_timestamp,
)
from runtime_orchestration.certification import (
    load_certification_adapter,
    require_certified_package,
)

TASKS = {
    "occupancy_forecast": {
        "kind": "regression",
        "target": "occupancy_target",
        "features": [
            "scheduled_hour_utc", "service_weekday", "is_weekend", "service_month",
            "direction_id", "distance_km", "stop_sequence",
            "historical_occupancy_ratio_lag_1",
            "historical_onboard_departure_lag_1",
        ],
    },
    "crowding_risk": {
        "kind": "classification",
        "target": "crowding_target",
        "features": [
            "scheduled_hour_utc", "service_weekday", "is_weekend", "service_month",
            "direction_id", "distance_km", "stop_sequence",
            "historical_occupancy_ratio_lag_1",
            "historical_onboard_departure_lag_1",
        ],
    },
}
SOURCE_TABLES = (
    "trip_stop_events", "passenger_counts", "trip_vehicle_assignments",
    "trips", "route_patterns", "route_stops", "schedule_stop_times",
    "schedules", "routes",
)
SPLIT_START_UTC = {
    "train": "2024-12-31T19:00:00Z",
    "validation": "2025-12-31T19:00:00Z",
    "test": "2026-03-31T19:00:00Z",
}
SPLIT_END_UTC = {
    "train": "2025-12-31T19:00:00Z",
    "validation": "2026-03-31T19:00:00Z",
    "test": "2026-06-30T19:00:00Z",
}


def _fixture_exclusions(path: Path) -> dict[str, set[str]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    result = {table: set() for table in (*SOURCE_TABLES, "passenger_journeys")}
    for injection in manifest.get("injections", []):
        table = str(injection.get("table_name", "")).lower()
        source_row_id = injection.get("source_row_id")
        if table in result and isinstance(source_row_id, str) and source_row_id:
            result[table].add(source_row_id)
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _epoch(value: dt.datetime) -> int:
    return int(value.timestamp() * 1_000_000)


def _timestamp(value: str | None) -> dt.datetime | None:
    if value is None or value.strip().upper() in {"", "\\N", "NULL", "NONE"}:
        return None
    return _parse_timestamp(value)


def _from_epoch(value: int) -> str:
    return dt.datetime.fromtimestamp(value / 1_000_000, dt.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _split_for(service_date: str) -> str | None:
    return next(
        (name for name, (low, high) in CHRONOLOGICAL_SPLITS.items()
         if low <= service_date < high),
        None,
    )


def _unique(
    paths,
    table,
    key,
    columns,
    excluded,
    *,
    current_trips=False,
    accepted_statuses=("VALID",),
):
    values = {}
    for row in _iter_rows(paths):
        identity = row.get(key)
        if (
            row.get("quality_status") not in accepted_statuses
            or row.get("source_row_id") in excluded
            or not identity
            or (current_trips and row.get("plan_status") != "CURRENT")
        ):
            continue
        if identity in values:
            raise ValueError(f"Duplicate valid {table}.{key}: {identity}")
        values[identity] = {name: row.get(name) for name in (key, *columns)}
    if not values:
        raise ValueError(f"No valid source rows for {table}")
    return values


def _source_inventory(dataset_root, table_files, fixture_path):
    manifest_path = dataset_root / "metadata" / "generation_manifest.json"
    generation = json.loads(manifest_path.read_text(encoding="utf-8"))
    bound = {item["path"]: item for item in generation["files"]}
    inventory = []
    for table, paths in table_files.items():
        for path in paths:
            relative = path.relative_to(dataset_root).as_posix()
            item = bound.get(relative)
            if item is None or path.stat().st_size != item["bytes"]:
                raise ValueError(f"Source is not bound to the generation manifest: {relative}")
            inventory.append({
                "path": relative, "sha256": item["sha256"],
                "bytes": item["bytes"], "table": table,
            })
    relative = fixture_path.relative_to(dataset_root).as_posix()
    item = bound.get(relative)
    if item is None or fixture_path.stat().st_size != item["bytes"]:
        raise ValueError("Private fixture exclusion manifest is not generation-bound")
    inventory.append({
        "path": relative, "sha256": item["sha256"],
        "bytes": item["bytes"], "table": "metadata_fixture_exclusions",
    })
    return generation, inventory


def _write_package(
    *,
    output: Path,
    task_name: str,
    partition_rows: dict[str, list[dict]],
    eligible_counts: dict[str, int],
    source_inventory: list[dict],
    generation: dict,
    attestation,
    marker_hash: str,
    max_rows: int,
):
    spec = TASKS[task_name]
    output.mkdir(parents=True, exist_ok=False)
    partition_specs = {}
    for split, rows in partition_rows.items():
        rows.sort(key=lambda row: (
            row["service_date"], row["target_start"], row["case_id"]
        ))
        path = output / f"{split}.jsonl"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(
                    json.dumps(row, sort_keys=True, allow_nan=False, separators=(",", ":"))
                    + "\n"
                )
        partition_specs[split] = {
            "path": path.name,
            "sha256": _sha256(path),
            "eligible_rows": eligible_counts[split],
            "sample_rows": len(rows),
            "source_paths": [
                item["path"] for item in source_inventory
                if item["table"] in {"trip_stop_events", "passenger_counts"}
            ],
        }

    source_hashes = {item["path"]: item["sha256"] for item in source_inventory}
    fingerprint = hashlib.sha256(
        b"python-raw-occupancy-v1\x1f"
        + bytes.fromhex(_sha256(Path(__file__)))
        + bytes.fromhex(_sha256(Path(__file__).parents[1] / "python_pipeline/features.py"))
    ).hexdigest()
    manifest = {
        "schema_version": "1.0",
        "task_name": task_name,
        "dataset_version": attestation.dataset_version,
        "feature_version": f"python-raw-occupancy-{task_name}-{fingerprint}",
        "producer": "python",
        "grain": "one scheduled route-stop departure with a later observed occupancy outcome",
        "target_definition": (
            "Python-derived future onboard_departure / actual positive capacity_snapshot, "
            "or strict ratio > 1.0 for crowding; derived from certified raw events, counts "
            "and actual assignments. No Spark rows, features, or targets are consumed."
        ),
        "features": spec["features"],
        "target": "target",
        "partitions": partition_specs,
        "certification": {
            "status": "CERTIFIED",
            "evidence_path": "feature_certification.json",
            "scope": (
                "owner-approved production-v1.1 raw inputs, Python-owned as-of and "
                "past-only features, bounded package integrity and leakage validation"
            ),
        },
        "source_provenance": {
            "dataset_version": attestation.dataset_version,
            "producer": "python",
            "implementation": "ml_execution.python_occupancy_handoff",
            "owner_attestation_reference": attestation.evidence_reference,
            "owner_marker_sha256": marker_hash,
            "source_dataset_certification": "OWNER_APPROVED_PRODUCTION_V1_1",
            "source_run_id": generation["run_id"],
            "generation_manifest_sha256": _sha256(
                Path(__file__).parents[1] / "raw_data/production-v1.1/metadata/generation_manifest.json"
            ),
            "raw_input_policy": (
                "Reads only generation-manifest-bound raw CSV and private fixture exclusion "
                "metadata. No Spark Parquet, Spark features, Spark predictions, or other "
                "Python task outputs are read."
            ),
            "raw_input_sha256": source_hashes,
            "raw_input_file_count": len(source_inventory),
            "source_row_policy": (
                "Non-injected observed stop events with valid typed timestamps; source "
                "passenger counts retain VALID, ACCEPTED, and FLAGGED statuses to preserve "
                "measured overload targets produced by the existing feature pipeline. "
                "Exact ACTUAL departure assignments must be VALID and contain actual "
                "departure; schedule/route metadata must be available by cutoff."
            ),
            "history_policy": (
                "The immediately preceding scheduled same-route-stop event is used only "
                "when its real load and actual-assignment capacity are valid and its "
                "occupancy outcome was available strictly before the current cutoff. "
                "No later/current load, capacity, utilization, delay, or crowding value "
                "is a predictor; unavailable previous outcomes are not backfilled."
            ),
            "crowding_threshold": {
                "ratio": 1.0,
                "comparison": ">",
                "positive_class": "OVER_CAPACITY",
            },
            "split_boundaries": CHRONOLOGICAL_SPLITS,
            "eligible_rows": eligible_counts,
            "sample_rows": {split: len(rows) for split, rows in partition_rows.items()},
            "max_rows_per_task": max_rows,
        },
    }
    if task_name == "occupancy_forecast":
        manifest.update({
            "baseline_column": "historical_occupancy_ratio_lag_1",
            "baseline_type": "last_observation",
            "forecast_protocol": "rolling_origin_frozen_model",
            "forecast_horizon": (
                "scheduled cutoff to later observed departure occupancy, available "
                "only after event and actual assignment outcomes"
            ),
        })
    else:
        manifest.update({
            "class_labels": ["WITHIN_CAPACITY", "OVER_CAPACITY"],
            "thresholds": {
                "capacity_utilization_ratio": 1.0,
                "positive_class": "strictly greater",
            },
            "risk_probability_threshold": 0.5,
        })
    validate_manifest(manifest)
    certification = {
        "status": "CERTIFIED",
        "dataset_version": manifest["dataset_version"],
        "feature_version": manifest["feature_version"],
        "task_name": task_name,
        "producer": "python",
        "partitions": partition_specs,
        "contract_sha256": contract_digest(manifest),
        "certification_scope": manifest["certification"]["scope"],
        "source_provenance": manifest["source_provenance"],
    }
    (output / "feature_certification.json").write_text(
        json.dumps(certification, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    loaded, _, hashes = load_features(manifest_path, max_rows=max_rows)
    print(json.dumps({
        "status": "HANDOFF_VALIDATED",
        "task": loaded["task_name"],
        "manifest": str(manifest_path),
        "sample_rows": {split: len(rows) for split, rows in partition_rows.items()},
        "partition_sha256": hashes["partition_sha256"],
        "producer": loaded["producer"],
    }, sort_keys=True), flush=True)
    return manifest_path


def export_occupancy_handoffs(
    *,
    dataset_root: Path,
    marker_path: Path,
    adapter,
    output_root: Path,
    max_rows: int = 100_000,
):
    if max_rows < 3:
        raise ValueError("max_rows must allow each split")
    dataset_root, output_root = dataset_root.resolve(), output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"Refusing to overwrite Python occupancy packages: {output_root}")
    if output_root.is_relative_to(dataset_root) or "raw_data" in output_root.parts:
        raise ValueError("Occupancy output must remain outside the certified raw package")
    attestation, marker_hash = require_certified_package(
        dataset_root, marker_path, adapter
    )
    if attestation.dataset_version != "production-v1.1":
        raise ValueError("Python occupancy handoffs require production-v1.1")

    raw_root = dataset_root / "raw"
    table_files = {
        table: _table_files(raw_root, table)
        for table in SOURCE_TABLES
    }
    fixture_path = dataset_root / "metadata/private_injection_manifest.json"
    exclusions = _fixture_exclusions(fixture_path)
    generation, inventory = _source_inventory(dataset_root, table_files, fixture_path)
    trips = _unique(
        table_files["trips"], "trips", "trip_id",
        ("operational_departure_id", "plan_status", "route_id", "pattern_id",
         "schedule_id", "service_date", "scheduled_start_utc", "published_at_utc",
         "trip_status"),
        exclusions["trips"], current_trips=True,
    )
    patterns = _unique(
        table_files["route_patterns"], "route_patterns", "pattern_id",
        ("route_id", "direction_id", "distance_km", "published_at_utc"),
        exclusions["route_patterns"],
    )
    route_stops = _unique(
        table_files["route_stops"], "route_stops", "route_stop_id",
        ("stop_id", "value_available_at"),
        exclusions["route_stops"],
    )
    schedule_times = _unique(
        table_files["schedule_stop_times"], "schedule_stop_times",
        "schedule_stop_time_id",
        ("schedule_id", "route_stop_id", "departure_offset_sec", "stop_sequence",
         "value_available_at"),
        exclusions["schedule_stop_times"],
    )
    schedules = _unique(
        table_files["schedules"], "schedules", "schedule_id",
        ("pattern_id", "published_at_utc"),
        exclusions["schedules"],
    )
    routes = _unique(
        table_files["routes"], "routes", "route_id",
        ("value_available_at",),
        exclusions["routes"],
    )
    counts = _unique(
        table_files["passenger_counts"], "passenger_counts", "stop_event_id",
        ("trip_id", "departure_assignment_id", "onboard_departure"),
        exclusions["passenger_counts"],
        accepted_statuses=("VALID", "ACCEPTED", "FLAGGED"),
    )
    assignments = _unique(
        table_files["trip_vehicle_assignments"], "trip_vehicle_assignments",
        "assignment_id",
        ("trip_id", "assignment_kind", "capacity_snapshot", "effective_start_utc",
         "effective_end_utc", "announced_at_utc", "value_available_at",
         "correction_time"),
        exclusions["trip_vehicle_assignments"],
    )

    temporary = tempfile.NamedTemporaryFile(
        prefix="urbantransit-python-occupancy-", suffix=".sqlite",
        dir=Path(__file__).parents[1] / "reports/runtime", delete=False,
    )
    database_path = Path(temporary.name)
    temporary.close()
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute("""
        CREATE TABLE events (
            route_stop TEXT NOT NULL, scheduled INTEGER NOT NULL,
            trip_id TEXT NOT NULL, event_id TEXT NOT NULL, service_date TEXT NOT NULL,
            split TEXT NOT NULL, route_id TEXT NOT NULL, direction REAL NOT NULL,
            operational_id TEXT NOT NULL, actual INTEGER NOT NULL,
            target_available INTEGER, capacity REAL, load REAL, ratio REAL,
            distance REAL NOT NULL, sequence REAL NOT NULL, known INTEGER NOT NULL,
            occupancy_valid INTEGER NOT NULL
        )
    """)
    scanned = 0
    inserted = 0
    rejected = {}
    try:
        pending = []
        for event in _iter_rows(table_files["trip_stop_events"]):
            scanned += 1
            if scanned % 500_000 == 0:
                print(json.dumps({
                    "event_rows_scanned": scanned,
                    "timeline_rows_staged": inserted + len(pending),
                    "sqlite_bytes": database_path.stat().st_size,
                }), flush=True)
            if (
                event.get("quality_status") != "VALID"
                or event.get("source_row_id") in exclusions["trip_stop_events"]
                or event.get("visit_status") != "OBSERVED"
            ):
                continue
            trip = trips.get(event.get("trip_id"))
            stop_time = schedule_times.get(event.get("schedule_stop_time_id"))
            route_stop = route_stops.get(event.get("route_stop_id"))
            if not trip or not stop_time or not route_stop:
                rejected["missing_event_context"] = rejected.get("missing_event_context", 0) + 1
                continue
            pattern = patterns.get(trip.get("pattern_id"))
            schedule = schedules.get(trip.get("schedule_id"))
            route = routes.get(trip.get("route_id"))
            if not pattern or not schedule or not route:
                rejected["missing_trip_schedule_context"] = rejected.get(
                    "missing_trip_schedule_context", 0
                ) + 1
                continue
            if (
                pattern.get("route_id") != trip.get("route_id")
                or schedule.get("pattern_id") != trip.get("pattern_id")
                or stop_time.get("schedule_id") != trip.get("schedule_id")
                or stop_time.get("route_stop_id") != event.get("route_stop_id")
            ):
                rejected["inconsistent_schedule_join"] = rejected.get(
                    "inconsistent_schedule_join", 0
                ) + 1
                continue
            try:
                service_date = dt.date.fromisoformat(str(event["service_date"])[:10]).isoformat()
                scheduled_start = _timestamp(trip.get("scheduled_start_utc"))
                actual = _timestamp(event.get("actual_departure_utc"))
                offset = float(stop_time["departure_offset_sec"])
                direction = float(pattern["direction_id"])
                distance = float(pattern["distance_km"])
                sequence = float(event.get("stop_sequence"))
                known_times = [
                    _timestamp(trip.get("published_at_utc")),
                    _timestamp(pattern.get("published_at_utc")),
                    _timestamp(schedule.get("published_at_utc")),
                    _timestamp(stop_time.get("value_available_at")),
                    _timestamp(route_stop.get("value_available_at")),
                    _timestamp(route.get("value_available_at")),
                ]
            except (KeyError, TypeError, ValueError, OverflowError):
                rejected["invalid_schedule_value"] = rejected.get("invalid_schedule_value", 0) + 1
                continue
            if (
                scheduled_start is None or actual is None
                or any(value is None for value in known_times)
                or not all(math.isfinite(value) for value in (offset, direction, distance, sequence))
                or distance < 0
            ):
                rejected["invalid_schedule_value"] = rejected.get("invalid_schedule_value", 0) + 1
                continue
            split = _split_for(service_date)
            if split is None:
                continue
            scheduled = scheduled_start + dt.timedelta(seconds=offset)
            cutoff = _epoch(scheduled)
            known = _epoch(max(known_times))
            if known > cutoff:
                rejected["schedule_unavailable_at_cutoff"] = rejected.get(
                    "schedule_unavailable_at_cutoff", 0
                ) + 1
                continue

            count = counts.get(event.get("stop_event_id"))
            assignment = None
            load = capacity = ratio = None
            target_available = None
            occupancy_valid = 0
            if count and count.get("trip_id") == event.get("trip_id"):
                assignment = assignments.get(count.get("departure_assignment_id"))
            if (
                count and assignment
                and assignment.get("trip_id") == event.get("trip_id")
                and assignment.get("assignment_kind") == "ACTUAL"
            ):
                try:
                    load = float(count["onboard_departure"])
                    capacity = float(assignment["capacity_snapshot"])
                    effective_start = _timestamp(assignment.get("effective_start_utc"))
                    effective_end = _timestamp(assignment.get("effective_end_utc"))
                    assignment_available = [
                        _timestamp(assignment.get("announced_at_utc")),
                        _timestamp(assignment.get("value_available_at")),
                        _timestamp(assignment.get("correction_time")),
                    ]
                    event_available = [
                        _timestamp(event.get("value_available_at")),
                        _timestamp(event.get("outcome_available_at_utc")),
                    ]
                except (KeyError, TypeError, ValueError, OverflowError):
                    load = capacity = None
                else:
                    if (
                        assignment_available[0] is not None
                        and assignment_available[1] is not None
                        and event_available[0] is not None
                        and effective_start is not None and effective_end is not None
                        and math.isfinite(load) and load >= 0
                        and math.isfinite(capacity) and capacity > 0
                        and effective_start <= actual < effective_end
                    ):
                        all_availability = [
                            value for value in assignment_available + event_available
                            if value is not None
                        ]
                        target_available = _epoch(max(all_availability))
                        ratio = load / capacity
                        occupancy_valid = 1
            pending.append((
                event["route_stop_id"], cutoff, str(event["trip_id"]),
                str(event["stop_event_id"]), service_date, split,
                str(trip["route_id"]), direction,
                str(trip["operational_departure_id"]), _epoch(actual),
                target_available, capacity, load, ratio, distance, sequence, known,
                occupancy_valid,
            ))
            if len(pending) >= 20_000:
                connection.executemany(
                    "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    pending,
                )
                inserted += len(pending)
                connection.commit()
                pending.clear()
        if pending:
            connection.executemany(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                pending,
            )
            inserted += len(pending)
            connection.commit()
        connection.execute(
            "CREATE INDEX events_timeline ON events(route_stop, scheduled, trip_id, event_id)"
        )
        connection.commit()
        print(json.dumps({
            "event_rows_scanned": scanned,
            "timeline_rows_staged": inserted,
            "sqlite_bytes": database_path.stat().st_size,
            "invalid_source_counts": rejected,
        }), flush=True)

        eligible_counts = {split: 0 for split in PERIODS}
        stratum_counts = {(split, label): 0 for split in PERIODS for label in (0, 1)}
        diagnostics = {
            split: {name: 0 for name in (
                "rows", "valid_current", "same_stop_predecessor",
                "predecessor_actual_before_cutoff", "predecessor_available_by_cutoff",
                "current_actual_after_cutoff", "current_outcome_after_cutoff",
                "outcome_available_by_split_end", "schedule_available_by_cutoff",
                "eligible",
            )}
            for split in PERIODS
        }
        previous_key = None
        previous = None
        cursor = connection.execute("""
            SELECT route_stop, scheduled, trip_id, event_id, service_date, split,
                   route_id, direction, operational_id, actual, target_available,
                   capacity, load, ratio, distance, sequence, known, occupancy_valid
            FROM events INDEXED BY events_timeline
            ORDER BY route_stop, scheduled, trip_id, event_id
        """)
        while True:
            batch = cursor.fetchmany(50_000)
            if not batch:
                break
            for row in batch:
                route_stop, cutoff, trip_id, event_id, service_date, split, route_id, direction, operational_id, actual, available, capacity, load, ratio, distance, sequence, known, occupancy_valid = row
                group = route_stop
                if group != previous_key:
                    previous_key = group
                    previous = None
                diagnostic = diagnostics[split]
                diagnostic["rows"] += 1
                if occupancy_valid:
                    diagnostic["valid_current"] += 1
                if previous is not None:
                    diagnostic["same_stop_predecessor"] += 1
                if previous is not None and previous[0] is not None and previous[0] < cutoff:
                    diagnostic["predecessor_actual_before_cutoff"] += 1
                if (
                    previous is not None and previous[1] is not None
                    and previous[1] <= cutoff
                ):
                    diagnostic["predecessor_available_by_cutoff"] += 1
                if actual > cutoff:
                    diagnostic["current_actual_after_cutoff"] += 1
                if available is not None and available > cutoff:
                    diagnostic["current_outcome_after_cutoff"] += 1
                split_end = _epoch(dt.datetime.fromisoformat(
                    SPLIT_END_UTC[split].replace("Z", "+00:00")
                ))
                if available is not None and available <= split_end:
                    diagnostic["outcome_available_by_split_end"] += 1
                if known <= cutoff:
                    diagnostic["schedule_available_by_cutoff"] += 1
                if (
                    occupancy_valid and previous is not None
                    and previous[0] is not None and previous[0] < cutoff
                    and previous[1] is not None and previous[1] <= cutoff
                    and actual > cutoff and available is not None
                    and available > cutoff and available <= split_end
                    and known <= cutoff
                ):
                    eligible_counts[split] += 1
                    diagnostic["eligible"] += 1
                    stratum_counts[(split, int(ratio > 1.0))] += 1
                previous = (
                    actual if occupancy_valid else None,
                    available if occupancy_valid else None,
                    ratio if occupancy_valid else None,
                    load if occupancy_valid else None,
                )
        if any(eligible_counts[split] == 0 for split in PERIODS):
            raise ValueError(
                "No leakage-safe occupancy targets in all chronological splits: "
                f"{eligible_counts}; predicate_counts={diagnostics}"
            )
        if not all(stratum_counts[("train", label)] for label in (0, 1)):
            raise ValueError(f"Training window lacks both crowding classes: {stratum_counts}")

        total = sum(eligible_counts.values())
        budget = min(max_rows, total)
        split_quotas = {
            split: max(1, round(budget * eligible_counts[split] / total))
            for split in PERIODS
        }
        while sum(split_quotas.values()) > budget:
            largest = max(split_quotas, key=lambda split: (split_quotas[split], split))
            if split_quotas[largest] > 1:
                split_quotas[largest] -= 1
        while sum(split_quotas.values()) < budget:
            largest = max(PERIODS, key=lambda split: eligible_counts[split] - split_quotas[split])
            if split_quotas[largest] >= eligible_counts[largest]:
                break
            split_quotas[largest] += 1
        quotas = {
            split: {
                label: max(1, round(
                    split_quotas[split]
                    * stratum_counts[(split, label)]
                    / eligible_counts[split]
                ))
                for label in (0, 1)
            }
            for split in PERIODS
        }
        for split in PERIODS:
            while sum(quotas[split].values()) > split_quotas[split]:
                label = max(quotas[split], key=lambda key: (quotas[split][key], key))
                if quotas[split][label] > 1:
                    quotas[split][label] -= 1
            while sum(quotas[split].values()) < split_quotas[split]:
                label = max(
                    (0, 1),
                    key=lambda key: stratum_counts[(split, key)] - quotas[split][key],
                )
                if quotas[split][label] >= stratum_counts[(split, label)]:
                    break
                quotas[split][label] += 1
            for label in (0, 1):
                if quotas[split][label] > stratum_counts[(split, label)]:
                    quotas[split][label] = stratum_counts[(split, label)]

        heaps = {
            (split, label): []
            for split in PERIODS
            for label in (0, 1)
        }
        previous_key = None
        previous = None
        cursor = connection.execute("""
            SELECT route_stop, scheduled, trip_id, event_id, service_date, split,
                   route_id, direction, operational_id, actual, target_available,
                   capacity, load, ratio, distance, sequence, known, occupancy_valid
            FROM events INDEXED BY events_timeline
            ORDER BY route_stop, scheduled, trip_id, event_id
        """)
        while True:
            batch = cursor.fetchmany(50_000)
            if not batch:
                break
            for row in batch:
                route_stop, cutoff, trip_id, event_id, service_date, split, route_id, direction, operational_id, actual, available, capacity, load, ratio, distance, sequence, known, occupancy_valid = row
                if route_stop != previous_key:
                    previous_key = route_stop
                    previous = None
                if (
                    occupancy_valid
                    and previous is not None
                    and previous[0] is not None
                    and previous[0] < cutoff
                    and previous[1] is not None
                    and previous[1] <= cutoff
                    and actual > cutoff
                    and available is not None
                    and available > cutoff
                    and available <= _epoch(dt.datetime.fromisoformat(
                        SPLIT_END_UTC[split].replace("Z", "+00:00")
                    ))
                    and known <= cutoff
                ):
                    label = int(ratio > 1.0)
                    features = (
                        float(dt.datetime.fromtimestamp(
                            cutoff / 1_000_000, dt.timezone.utc
                        ).hour),
                        float(dt.date.fromisoformat(service_date).isoweekday()),
                        float(dt.date.fromisoformat(service_date).isoweekday() >= 6),
                        float(dt.date.fromisoformat(service_date).month),
                        float(direction),
                        float(distance),
                        float(sequence),
                        float(previous[2]),
                        float(previous[3]),
                    )
                    if not all(math.isfinite(value) for value in features):
                        raise ValueError(f"Nonfinite source predictor for {event_id}")
                    record = {
                        "case_id": f"{len(trip_id)}:{trip_id}{event_id}",
                        "service_date": service_date,
                        "target_start": _from_epoch(actual),
                        "target_end": _from_epoch(actual),
                        "feature_cutoff_at": _from_epoch(cutoff),
                        "value_available_at": _from_epoch(max(known, previous[1])),
                        "target_available_at": _from_epoch(available),
                        "operational_departure_id": operational_id,
                        "source_departure_ids": [operational_id],
                        "source_trip_id": trip_id,
                        "source_stop_event_id": event_id,
                        "route_id": route_id,
                        "route_stop_id": route_stop,
                        "occupancy_status": "AVAILABLE",
                        "capacity_snapshot": float(capacity),
                        "historical_occupancy_available_at": _from_epoch(previous[1]),
                        "feature_availability_policy": (
                            "Schedule/route/stop metadata is known by cutoff; predictor history "
                            "is the immediately preceding same-route-stop event only, with "
                            "actual and capacity outcome both available strictly before cutoff."
                        ),
                        **dict(zip(TASKS["occupancy_forecast"]["features"], features)),
                        "occupancy_target": float(ratio),
                        "crowding_target": label,
                    }
                    key = (split, label)
                    rank = int(hashlib.sha256(
                        f"{SEED}\x1f{split}\x1f{label}\x1f{event_id}".encode()
                    ).hexdigest(), 16)
                    heap = heaps[key]
                    entry = (-rank, event_id, record)
                    if len(heap) < quotas[split][label]:
                        heapq.heappush(heap, entry)
                    elif rank < -heap[0][0]:
                        heapq.heapreplace(heap, entry)
                previous = (
                    actual if occupancy_valid else None,
                    available if occupancy_valid else None,
                    ratio if occupancy_valid else None,
                    load if occupancy_valid else None,
                )

        output_root.mkdir(parents=True, exist_ok=False)
        manifests = {}
        for task_name, spec in TASKS.items():
            partition_rows = {}
            for split in PERIODS:
                sampled = [
                    entry[2]
                    for label in (0, 1)
                    for entry in heaps[(split, label)]
                ]
                rows = []
                for source in sampled:
                    target_value = (
                        source["crowding_target"]
                        if task_name == "crowding_risk"
                        else source["occupancy_target"]
                    )
                    row = {
                        key: value for key, value in source.items()
                        if key not in {"occupancy_target", "crowding_target"}
                    }
                    row["target"] = target_value
                    if task_name == "crowding_risk":
                        row["target_label"] = (
                            "OVER_CAPACITY" if target_value else "WITHIN_CAPACITY"
                        )
                    partition_rows[split] = partition_rows.get(split, []) + [row]
            manifests[task_name] = _write_package(
                output=output_root / task_name,
                task_name=task_name,
                partition_rows=partition_rows,
                eligible_counts=eligible_counts,
                source_inventory=inventory,
                generation=generation,
                attestation=attestation,
                marker_hash=marker_hash,
                max_rows=max_rows,
            )
        print(json.dumps({
            "status": "PYTHON_OCCUPANCY_HANDOFFS_VALIDATED",
            "manifests": {name: str(path) for name, path in manifests.items()},
            "source_rows_scanned": scanned,
            "timeline_rows_staged": inserted,
            "eligible_rows": eligible_counts,
            "class_counts": {
                split: {str(label): stratum_counts[(split, label)] for label in (0, 1)}
                for split in PERIODS
            },
            "sample_rows": {
                split: sum(len(heaps[(split, label)]) for label in (0, 1))
                for split in PERIODS
            },
            "sqlite_bytes": database_path.stat().st_size,
        }, sort_keys=True), flush=True)
        return manifests
    finally:
        connection.close()
        database_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument(
        "--certification-adapter",
        default="runtime_orchestration.production_v11_certification:ProductionV11Adapter",
    )
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--max-rows", type=int, default=100_000)
    args = parser.parse_args()
    adapter = load_certification_adapter(args.certification_adapter)
    export_occupancy_handoffs(
        dataset_root=Path(args.dataset_root),
        marker_path=Path(args.marker),
        adapter=adapter,
        output_root=args.output_root,
        max_rows=args.max_rows,
    )


if __name__ == "__main__":
    main()

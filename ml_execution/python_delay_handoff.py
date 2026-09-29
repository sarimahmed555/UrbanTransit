"""Create an independent Python delay handoff from certified raw source tables."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from feature_contracts import CHRONOLOGICAL_SPLITS, validate_severity_thresholds
from ml_execution.contracts import (
    PERIODS,
    contract_digest,
    load_features,
    validate_manifest,
)
from runtime_orchestration.certification import (
    load_certification_adapter,
    require_certified_package,
)

TASK = "delay_severity"
LABELS = ("ON_TIME", "MINOR_DELAY", "MODERATE_DELAY", "MAJOR_DELAY", "SEVERE_DELAY")
FEATURES = (
    "scheduled_hour_utc",
    "service_weekday",
    "is_weekend",
    "service_month",
    "direction_id",
    "distance_km",
)
SEED = 20260926
TABLES = (
    "trip_stop_events",
    "trips",
    "route_patterns",
    "route_stops",
    "schedule_stop_times",
    "schedules",
    "routes",
)
SPLIT_END_UTC = {
    "train": "2025-12-31T19:00:00Z",
    "validation": "2026-03-31T19:00:00Z",
    "test": "2026-06-30T19:00:00Z",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value: str | None) -> dt.datetime | None:
    if not value or value.strip().upper() in {"NULL", "NONE"}:
        return None
    result = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError(f"Source timestamp has no timezone: {value}")
    return result.astimezone(dt.timezone.utc)


def _table_files(raw_root: Path, table: str) -> list[Path]:
    directory = raw_root / table
    files = sorted(path for path in directory.glob("*.csv") if path.is_file())
    if not files:
        raise FileNotFoundError(f"No certified source CSV shards for {table}: {directory}")
    return files


def _iter_rows(paths: list[Path]):
    for path in paths:
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                raise ValueError(f"Source shard has no CSV header: {path}")
            for row in reader:
                yield row


def _read_unique(
    paths: list[Path],
    table: str,
    key: str,
    columns: tuple[str, ...],
    excluded_sources: set[str],
):
    result = {}
    for row in _iter_rows(paths):
        if (
            row.get("quality_status") != "VALID"
            or row.get("source_row_id") in excluded_sources
        ):
            continue
        identity = row.get(key)
        if not identity:
            continue
        if identity in result:
            raise ValueError(f"Duplicate valid {table}.{key}: {identity}")
        missing = [column for column in columns if column not in row]
        if missing:
            raise ValueError(f"{table} is missing source columns: {missing}")
        result[identity] = {
            column: row[column] for column in (key, *columns)
        }
    return result


def _build_context(
    raw_root: Path,
    file_map: dict[str, list[Path]],
    excluded_sources: dict[str, set[str]],
):
    trips = _read_unique(
        file_map["trips"], "trips", "trip_id",
        (
            "operational_departure_id", "plan_status", "route_id", "pattern_id",
            "schedule_id", "service_date", "scheduled_start_utc",
            "published_at_utc", "trip_status",
        ),
        excluded_sources["trips"],
    )
    patterns = _read_unique(
        file_map["route_patterns"], "route_patterns", "pattern_id",
        ("route_id", "direction_id", "distance_km", "published_at_utc"),
        excluded_sources["route_patterns"],
    )
    route_stops = _read_unique(
        file_map["route_stops"], "route_stops", "route_stop_id",
        ("stop_id", "value_available_at"),
        excluded_sources["route_stops"],
    )
    schedule_times = _read_unique(
        file_map["schedule_stop_times"], "schedule_stop_times",
        "schedule_stop_time_id",
        ("schedule_id", "route_stop_id", "departure_offset_sec", "value_available_at"),
        excluded_sources["schedule_stop_times"],
    )
    schedules = _read_unique(
        file_map["schedules"], "schedules", "schedule_id",
        ("pattern_id", "published_at_utc"),
        excluded_sources["schedules"],
    )
    routes = _read_unique(
        file_map["routes"], "routes", "route_id",
        ("value_available_at",),
        excluded_sources["routes"],
    )
    return trips, patterns, route_stops, schedule_times, schedules, routes


def _fixture_exclusions(path: Path) -> dict[str, set[str]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    result = {table: set() for table in TABLES}
    for injection in manifest.get("injections", []):
        table = str(injection.get("table_name", "")).lower()
        source_row_id = injection.get("source_row_id")
        if table in result and isinstance(source_row_id, str) and source_row_id:
            result[table].add(source_row_id)
    return result


def _row_features(event, context, thresholds):
    trips, patterns, route_stops, schedule_times, schedules, routes = context
    trip = trips.get(event.get("trip_id"))
    if (
        not trip
        or trip.get("plan_status") != "CURRENT"
        or trip.get("trip_status") == "CANCELLED"
        or not trip.get("operational_departure_id")
    ):
        return None
    pattern = patterns.get(trip.get("pattern_id"))
    stop_time = schedule_times.get(event.get("schedule_stop_time_id"))
    route_stop = route_stops.get(event.get("route_stop_id"))
    schedule = schedules.get(trip.get("schedule_id"))
    route = routes.get(trip.get("route_id"))
    if not all((pattern, stop_time, route_stop, schedule, route)):
        return None
    if (
        pattern.get("route_id") != trip.get("route_id")
        or schedule.get("pattern_id") != trip.get("pattern_id")
        or stop_time.get("schedule_id") != trip.get("schedule_id")
        or stop_time.get("route_stop_id") != event.get("route_stop_id")
    ):
        return None
    try:
        service_date = dt.date.fromisoformat(event["service_date"][:10])
        scheduled_start = _timestamp(trip["scheduled_start_utc"])
        offset = float(stop_time["departure_offset_sec"])
        direction = float(pattern["direction_id"])
        distance = float(pattern["distance_km"])
        actual = _timestamp(event.get("actual_departure_utc"))
        target_available = _timestamp(event.get("outcome_available_at_utc"))
        availability = [
            _timestamp(trip["published_at_utc"]),
            _timestamp(pattern["published_at_utc"]),
            _timestamp(schedule["published_at_utc"]),
            _timestamp(stop_time["value_available_at"]),
            _timestamp(route_stop["value_available_at"]),
            _timestamp(route["value_available_at"]),
        ]
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if (
        scheduled_start is None or actual is None or target_available is None
        or any(value is None for value in availability)
        or not math.isfinite(offset) or not math.isfinite(direction)
        or not math.isfinite(distance) or distance < 0
    ):
        return None
    split = next(
        (
            name for name, (lower, upper) in CHRONOLOGICAL_SPLITS.items()
            if lower <= service_date.isoformat() < upper
        ),
        None,
    )
    if split is None:
        return None
    scheduled = scheduled_start + dt.timedelta(seconds=offset)
    value_available = max(availability)
    if (
        value_available > scheduled
        or target_available < scheduled
        or target_available > dt.datetime.fromisoformat(
            SPLIT_END_UTC[split].replace("Z", "+00:00")
        )
    ):
        return None
    seconds = int(actual.timestamp()) - int(scheduled.timestamp())
    delay = max(0, seconds)
    label_index = next(
        (index for index, boundary in enumerate(thresholds) if delay <= boundary),
        len(thresholds),
    )
    label = LABELS[label_index]
    local_weekday = service_date.weekday() + 1
    feature_values = {
        "scheduled_hour_utc": float(scheduled.hour),
        "service_weekday": float(local_weekday),
        "is_weekend": float(local_weekday >= 6),
        "service_month": float(service_date.month),
        "direction_id": direction,
        "distance_km": distance,
    }
    if not all(math.isfinite(value) for value in feature_values.values()):
        return None
    return {
        "case_id": event["stop_event_id"],
        "source_stop_event_id": event["stop_event_id"],
        "operational_departure_id": trip["operational_departure_id"],
        "source_departure_ids": [trip["operational_departure_id"]],
        "service_date": service_date.isoformat(),
        "target_start": _iso(scheduled),
        "target_end": _iso(scheduled),
        "feature_cutoff_at": _iso(scheduled),
        "value_available_at": _iso(value_available),
        "target_available_at": _iso(target_available),
        "known_schedule_available_at": _iso(value_available),
        "target": label_index,
        "target_label": label,
        **feature_values,
        "__split": split,
        "__rank": hashlib.sha256(
            f"{SEED}\x1f{split}\x1f{label}\x1f{event['stop_event_id']}".encode()
        ).hexdigest(),
    }


def _allocate(counts: dict[str, int], budget: int, minimum: int = 1) -> dict[str, int]:
    budget = min(budget, sum(counts.values()))
    result = {key: min(value, minimum) for key, value in counts.items()}
    remaining = budget - sum(result.values())
    residual = {key: counts[key] - result[key] for key in counts}
    total = sum(residual.values())
    if remaining <= 0 or total <= 0:
        return result
    exact = {key: remaining * value / total for key, value in residual.items()}
    for key, value in exact.items():
        result[key] += min(value, math.floor(exact[key]))
    remaining = budget - sum(result.values())
    for key in sorted(
        counts, key=lambda item: (-(exact[item] - math.floor(exact[item])), item)
    ):
        if remaining <= 0:
            break
        if result[key] < counts[key]:
            result[key] += 1
            remaining -= 1
    return result


def _feature_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = (
        Path(__file__),
        root / "python_pipeline" / "features.py",
        root / "python_pipeline" / "splits.py",
        root / "feature_contracts.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def export_python_delay_handoff(
    *,
    dataset_root: Path,
    marker_path: Path,
    adapter,
    output: Path,
    max_rows: int = 100_000,
    severity_thresholds_sec=(60, 300, 600, 1800),
):
    dataset_root = dataset_root.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite Python handoff: {output}")
    if output.is_relative_to(dataset_root) or dataset_root.is_relative_to(output):
        raise ValueError("Handoff output must not overlap the certified raw package")
    if "raw_data" in output.parts:
        raise ValueError("Handoff output must not be written under raw_data")
    if max_rows < 3 * 5:
        raise ValueError("max_rows must allow every label in each chronological split")
    thresholds = validate_severity_thresholds(severity_thresholds_sec)
    attestation, marker_hash = require_certified_package(
        dataset_root, marker_path, adapter
    )
    if attestation.dataset_version != "production-v1.1":
        raise ValueError("Python delay handoff requires the approved production-v1.1 dataset")

    raw_root = dataset_root / "raw"
    file_map = {table: _table_files(raw_root, table) for table in TABLES}
    fixture_manifest_path = dataset_root / "metadata" / "private_injection_manifest.json"
    excluded_sources = _fixture_exclusions(fixture_manifest_path)
    generation = json.loads(
        (dataset_root / "metadata" / "generation_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_files = {item["path"]: item for item in generation["files"]}
    source_inventory = []
    for table, paths in file_map.items():
        for path in paths:
            relative = path.relative_to(dataset_root).as_posix()
            entry = manifest_files.get(relative)
            if entry is None or path.stat().st_size != entry["bytes"]:
                raise ValueError(f"Source file is not bound by generation manifest: {relative}")
            source_inventory.append({
                "path": relative,
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
                "table": table,
            })
    fixture_relative = fixture_manifest_path.relative_to(dataset_root).as_posix()
    fixture_entry = manifest_files.get(fixture_relative)
    if (
        fixture_entry is None
        or fixture_manifest_path.stat().st_size != fixture_entry["bytes"]
    ):
        raise ValueError("Private fixture-exclusion manifest is not bound by generation manifest")
    source_inventory.append({
        "path": fixture_relative,
        "sha256": fixture_entry["sha256"],
        "bytes": fixture_entry["bytes"],
        "table": "metadata_fixture_exclusions",
    })
    context = _build_context(raw_root, file_map, excluded_sources)

    counts = {split: {label: 0 for label in LABELS} for split in PERIODS}
    total_eligible = 0
    for event in _iter_rows(file_map["trip_stop_events"]):
        if (
            event.get("quality_status") != "VALID"
            or event.get("source_row_id") in excluded_sources["trip_stop_events"]
            or event.get("visit_status") != "OBSERVED"
        ):
            continue
        candidate = _row_features(event, context, thresholds)
        if candidate is None:
            continue
        counts[candidate["__split"]][candidate["target_label"]] += 1
        total_eligible += 1
    supported = [
        label for label in LABELS
        if all(counts[split][label] > 0 for split in PERIODS)
    ]
    if len(supported) < 2 or supported != list(LABELS[:len(supported)]):
        raise ValueError(f"Source labels lack stable chronological support: {counts}")
    supported_counts = {
        split: {label: counts[split][label] for label in supported}
        for split in PERIODS
    }
    split_totals = {
        split: sum(supported_counts[split].values())
        for split in PERIODS
    }
    split_budget = _allocate(split_totals, min(max_rows, sum(split_totals.values())))
    quotas = {
        split: _allocate(
            supported_counts[split],
            split_budget[split],
            minimum=1,
        )
        for split in PERIODS
    }
    heaps = {
        (split, label): []
        for split in PERIODS
        for label in supported
    }
    import heapq

    for event in _iter_rows(file_map["trip_stop_events"]):
        if (
            event.get("quality_status") != "VALID"
            or event.get("source_row_id") in excluded_sources["trip_stop_events"]
            or event.get("visit_status") != "OBSERVED"
        ):
            continue
        candidate = _row_features(event, context, thresholds)
        if candidate is None or candidate["target_label"] not in supported:
            continue
        key = (candidate["__split"], candidate["target_label"])
        capacity = quotas[key[0]][key[1]]
        rank = int(candidate["__rank"], 16)
        heap = heaps[key]
        entry = (-rank, candidate["case_id"], candidate)
        if len(heap) < capacity:
            heapq.heappush(heap, entry)
        elif rank < -heap[0][0]:
            heapq.heapreplace(heap, entry)

    output.mkdir(parents=True, exist_ok=False)
    partition_specs = {}
    observed_counts = {}
    for split in PERIODS:
        rows = [
            entry[2]
            for label in supported
            for entry in heaps[(split, label)]
        ]
        rows.sort(key=lambda row: (row["service_date"], row["target_start"], row["case_id"]))
        path = output / f"{split}.jsonl"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                row = {
                    key: value for key, value in row.items()
                    if not key.startswith("__")
                }
                stream.write(
                    json.dumps(row, sort_keys=True, allow_nan=False, separators=(",", ":"))
                    + "\n"
                )
        partition_specs[split] = {
            "path": path.name,
            "sha256": _sha256(path),
            "eligible_rows": sum(supported_counts[split].values()),
            "eligible_class_counts": supported_counts[split],
            "sample_rows": len(rows),
            "sample_class_counts": {
                label: quotas[split][label] for label in supported
            },
            "source_paths": [
                item["path"] for item in source_inventory
                if item["table"] == "trip_stop_events"
            ],
        }
        observed_counts[split] = len(rows)

    source_hashes = {
        item["path"]: item["sha256"] for item in source_inventory
    }
    feature_version = _feature_fingerprint()
    manifest = {
        "schema_version": "1.0",
        "task_name": TASK,
        "dataset_version": attestation.dataset_version,
        "feature_version": f"python-raw-delay-{feature_version}",
        "producer": "python",
        "grain": "one valid observed trip stop event derived from certified raw Python inputs",
        "target_definition": (
            "Python-derived max(0, actual departure minus schedule-known departure), "
            "encoded using configured delay-severity thresholds; no Spark feature rows "
            "or labels are consumed."
        ),
        "features": list(FEATURES),
        "target": "target",
        "class_labels": supported,
        "thresholds": {"delay_seconds": list(thresholds)},
        "partitions": partition_specs,
        "certification": {
            "status": "CERTIFIED",
            "evidence_path": "feature_certification.json",
            "scope": (
                "owner-approved production-v1.1 input, Python-owned source derivation, "
                "bounded package integrity, chronological and leakage validation"
            ),
        },
        "source_provenance": {
            "dataset_version": attestation.dataset_version,
            "source_dataset_certification": "OWNER_APPROVED_PRODUCTION_V1_1",
            "owner_attestation_reference": attestation.evidence_reference,
            "owner_marker_sha256": marker_hash,
            "producer": "python",
            "implementation": "ml_execution.python_delay_handoff",
            "python_feature_contract_fingerprint": feature_version,
            "raw_input_policy": (
                "Reads certified production-v1.1 raw CSV only; does not read Spark "
                "Parquet/features/predictions or Python pipeline outputs."
            ),
            "raw_input_sha256": source_hashes,
            "raw_input_file_count": len(source_inventory),
            "source_row_policy": (
                "Only quality_status=VALID observed event rows and CURRENT non-cancelled "
                "trip context with schedule, route, pattern and stop-time availability "
                "known by the scheduled departure; target outcome availability is at or "
                "after the event and no later than its fixed split boundary."
            ),
            "seed": SEED,
            "sampling_policy": (
                "Deterministic SHA-256 rank by split, source-derived label and stop_event_id; "
                "proportional chronological split/class quotas, no label or feature synthesis."
            ),
            "source_feature_version": "python_pipeline.features raw-contract semantics",
            "source_run_id": generation["run_id"],
            "source_manifest_sha256": _sha256(
                dataset_root / "metadata" / "generation_manifest.json"
            ),
            "source_row_count_eligible": total_eligible,
            "source_label_counts": supported_counts,
            "unsupported_labels": [
                label for label in LABELS if label not in supported
            ],
            "split_boundaries": CHRONOLOGICAL_SPLITS,
        },
    }
    validate_manifest(manifest)
    certification = {
        "status": "CERTIFIED",
        "dataset_version": manifest["dataset_version"],
        "feature_version": manifest["feature_version"],
        "task_name": manifest["task_name"],
        "producer": manifest["producer"],
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
    loaded, rows, hashes = load_features(manifest_path, max_rows=max_rows)
    result = {
        "status": "HANDOFF_VALIDATED",
        "manifest": str(manifest_path),
        "task": loaded["task_name"],
        "producer": loaded["producer"],
        "sample_rows": {split: len(rows[split]) for split in PERIODS},
        "eligible_rows": {
            split: sum(supported_counts[split].values()) for split in PERIODS
        },
        "class_labels": supported,
        "source_label_counts": supported_counts,
        "source_hash_count": len(source_hashes),
        "partition_hashes": hashes["partition_sha256"],
        "owner_marker_sha256": marker_hash,
        "source_manifest_sha256": manifest["source_provenance"]["source_manifest_sha256"],
    }
    print(json.dumps(result, sort_keys=True), flush=True)
    return manifest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument(
        "--certification-adapter",
        default="runtime_orchestration.production_v11_certification:ProductionV11Adapter",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-rows", type=int, default=100_000)
    args = parser.parse_args()
    adapter = load_certification_adapter(args.certification_adapter)
    export_python_delay_handoff(
        dataset_root=Path(args.dataset_root),
        marker_path=Path(args.marker),
        adapter=adapter,
        output=Path(args.output),
        max_rows=args.max_rows,
    )


if __name__ == "__main__":
    main()

"""Build an independent, bounded passenger-demand handoff from certified raw CSV."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import heapq
import json
import math
from pathlib import Path

from feature_contracts import CHRONOLOGICAL_SPLITS
from ml_execution.contracts import PERIODS, contract_digest, load_features, validate_manifest
from ml_execution.python_delay_handoff import SEED, _iso, _iter_rows, _table_files, _timestamp
from runtime_orchestration.certification import (
    load_certification_adapter,
    require_certified_package,
)

TASK = "passenger_demand"
TABLES = ("passenger_journeys", "trips", "route_patterns", "schedules")
FEATURES = (
    "scheduled_hour_utc",
    "service_weekday",
    "is_weekend",
    "service_month",
    "historical_demand_lag_1",
    "historical_demand_mean_7",
)


def _fixture_exclusions(path: Path) -> dict[str, set[str]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    result = {table: set() for table in TABLES}
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


def _unique_context(paths, table, key, columns, exclusions):
    values = {}
    for row in _iter_rows(paths):
        identity = row.get(key)
        if (
            row.get("quality_status") != "VALID"
            or row.get("source_row_id") in exclusions
            or not identity
        ):
            continue
        if table == "trips" and row.get("plan_status") != "CURRENT":
            continue
        if identity in values:
            raise ValueError(f"Duplicate valid {table}.{key}: {identity}")
        values[identity] = {name: row.get(name) for name in (key, *columns)}
    if not values:
        raise ValueError(f"No valid Python source context rows for {table}")
    return values


def _source_inventory(dataset_root, tables):
    generation_path = dataset_root / "metadata" / "generation_manifest.json"
    generation = json.loads(generation_path.read_text(encoding="utf-8"))
    bound = {item["path"]: item for item in generation["files"]}
    inventory = []
    for table, paths in tables.items():
        for path in paths:
            relative = path.relative_to(dataset_root).as_posix()
            entry = bound.get(relative)
            if (
                entry is None
                or path.stat().st_size != entry["bytes"]
                or _sha256(path) != entry["sha256"]
            ):
                raise ValueError(f"Raw Python source is not bound by its manifest: {relative}")
            inventory.append({
                "path": relative,
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
                "table": path.parents[0].name,
            })
    return generation, inventory


def _allocate(counts, budget):
    total = sum(counts.values())
    budget = min(budget, total)
    result = {name: min(1, count) for name, count in counts.items()}
    left = budget - sum(result.values())
    residual = {name: counts[name] - result[name] for name in counts}
    residual_total = sum(residual.values())
    if left > 0 and residual_total:
        exact = {name: left * count / residual_total for name, count in residual.items()}
        for name in counts:
            add = min(residual[name], math.floor(exact[name]))
            result[name] += add
        left = budget - sum(result.values())
        for name in sorted(
            counts, key=lambda key: (-(exact[key] - math.floor(exact[key])), key)
        ):
            if left and result[name] < counts[name]:
                result[name] += 1
                left -= 1
    return result


def export_demand_handoff(
    *,
    dataset_root: Path,
    marker_path: Path,
    adapter,
    output: Path,
    max_rows: int = 100_000,
):
    import pandas as pd
    from python_pipeline.features import build_demand_forecast_features

    dataset_root, output = dataset_root.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite Python demand handoff: {output}")
    if output.is_relative_to(dataset_root) or dataset_root.is_relative_to(output):
        raise ValueError("Demand output must not overlap the certified raw package")
    if "raw_data" in output.parts or max_rows < len(PERIODS):
        raise ValueError("Demand output path or bounded row limit is invalid")
    attestation, marker_hash = require_certified_package(
        dataset_root, marker_path, adapter
    )
    if attestation.dataset_version != "production-v1.1":
        raise ValueError("Python demand handoff requires production-v1.1")

    raw_root = dataset_root / "raw"
    table_files = {table: _table_files(raw_root, table) for table in TABLES}
    fixture_path = dataset_root / "metadata" / "private_injection_manifest.json"
    excluded = _fixture_exclusions(fixture_path)
    generation, inventory = _source_inventory(dataset_root, table_files)
    bound = {
        item["path"]: item
        for item in generation["files"]
    }
    fixture_relative = fixture_path.relative_to(dataset_root).as_posix()
    fixture_entry = bound.get(fixture_relative)
    if (
        fixture_entry is None
        or fixture_path.stat().st_size != fixture_entry["bytes"]
        or _sha256(fixture_path) != fixture_entry["sha256"]
    ):
        raise ValueError("Fixture-exclusion manifest is not bound by the generation manifest")
    inventory.append({
        "path": fixture_relative,
        "sha256": fixture_entry["sha256"],
        "bytes": fixture_entry["bytes"],
        "table": "metadata_fixture_exclusions",
    })

    trips = _unique_context(
        table_files["trips"], "trips", "trip_id",
        ("operational_departure_id", "plan_status", "route_id", "pattern_id",
         "schedule_id", "service_date", "scheduled_start_utc", "published_at_utc",
         "trip_status"),
        excluded["trips"],
    )
    patterns = _unique_context(
        table_files["route_patterns"], "route_patterns", "pattern_id",
        ("route_id", "direction_id", "published_at_utc"),
        excluded["route_patterns"],
    )
    schedules = _unique_context(
        table_files["schedules"], "schedules", "schedule_id",
        ("pattern_id", "published_at_utc"),
        excluded["schedules"],
    )

    journeys_by_trip = {}
    scanned = 0
    for journey in _iter_rows(table_files["passenger_journeys"]):
        scanned += 1
        if (
            journey.get("quality_status") != "VALID"
            or journey.get("source_row_id") in excluded["passenger_journeys"]
            or not journey.get("trip_id")
        ):
            continue
        try:
            count = float(journey["passenger_count"])
            available = _timestamp(journey.get("value_available_at"))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(count) or count < 0 or available is None:
            continue
        trip_id = journey["trip_id"]
        current = journeys_by_trip.get(trip_id)
        if current is None:
            journeys_by_trip[trip_id] = [count, available]
        else:
            current[0] += count
            if available > current[1]:
                current[1] = available
    if not journeys_by_trip:
        raise ValueError("No valid source journey targets were available")

    records = []
    excluded_counts = {
        "no_trip_context": 0,
        "no_schedule_context": 0,
        "outside_period_or_late_target": 0,
        "missing_source_values": 0,
    }
    for trip_id, target_row in journeys_by_trip.items():
        trip = trips.get(trip_id)
        if not trip:
            excluded_counts["no_trip_context"] += 1
            continue
        pattern, schedule = (
            patterns.get(trip.get("pattern_id")),
            schedules.get(trip.get("schedule_id")),
        )
        if not pattern or not schedule:
            excluded_counts["no_schedule_context"] += 1
            continue
        if (
            pattern.get("route_id") != trip.get("route_id")
            or schedule.get("pattern_id") != trip.get("pattern_id")
        ):
            excluded_counts["no_schedule_context"] += 1
            continue
        try:
            service_date = dt.date.fromisoformat(str(trip["service_date"])[:10])
            scheduled = _timestamp(trip.get("scheduled_start_utc"))
            published = [
                _timestamp(trip.get("published_at_utc")),
                _timestamp(pattern.get("published_at_utc")),
                _timestamp(schedule.get("published_at_utc")),
            ]
            direction = float(pattern["direction_id"])
        except (KeyError, TypeError, ValueError, OverflowError):
            excluded_counts["missing_source_values"] += 1
            continue
        if (
            scheduled is None
            or any(value is None for value in published)
            or not math.isfinite(direction)
        ):
            excluded_counts["missing_source_values"] += 1
            continue
        split = next(
            (name for name, (lo, hi) in CHRONOLOGICAL_SPLITS.items()
             if lo <= service_date.isoformat() < hi),
            None,
        )
        if split is None:
            continue
        target, target_available = target_row
        known_at = max(published)
        boundary = dt.datetime.fromisoformat(
            {
                "train": "2025-12-31T19:00:00+00:00",
                "validation": "2026-03-31T19:00:00+00:00",
                "test": "2026-06-30T19:00:00+00:00",
            }[split]
        )
        if (
            known_at > scheduled
            or target_available < scheduled
            or target_available > boundary
            or not trip.get("operational_departure_id")
        ):
            excluded_counts["outside_period_or_late_target"] += 1
            continue
        records.append({
            "service_date": service_date.isoformat(),
            "route_id": trip["route_id"],
            "direction_id": direction,
            "trip_id": trip_id,
            "operational_departure_id": trip["operational_departure_id"],
            "scheduled_departure_utc": _iso(scheduled),
            "known_schedule_available_at": _iso(known_at),
            "target_available_at": _iso(target_available),
            "passenger_demand": target,
            "__split": split,
        })
        if len(records) % 100_000 == 0:
            print(json.dumps({
                "journey_rows_scanned": scanned,
                "trip_targets_built": len(records),
            }), flush=True)

    if not records:
        raise ValueError("No chronology- and availability-eligible demand targets")
    frame = pd.DataFrame.from_records(records)
    featured = build_demand_forecast_features(frame)
    featured["__split"] = frame.set_index("trip_id").loc[
        featured["trip_id"], "__split"
    ].to_numpy()
    eligible = {}
    for split in PERIODS:
        frame_split = featured.loc[featured["__split"] == split].copy()
        frame_split = frame_split.dropna(subset=list(FEATURES))
        if frame_split.empty:
            raise ValueError(f"No complete as-of demand cases in {split}")
        eligible[split] = frame_split.to_dict(orient="records")
        print(json.dumps({
            "split": split,
            "source_trip_targets": int((featured["__split"] == split).sum()),
            "eligible_rows": len(eligible[split]),
            "service_date_min": min(row["service_date"] for row in eligible[split]),
            "service_date_max": max(row["service_date"] for row in eligible[split]),
        }), flush=True)

    quotas = _allocate(
        {split: len(rows) for split, rows in eligible.items()},
        min(max_rows, sum(len(rows) for rows in eligible.values())),
    )
    selected = {}
    for split, rows in eligible.items():
        ranked = sorted(
            rows,
            key=lambda row: hashlib.sha256(
                f"{SEED}\x1f{split}\x1f{row['trip_id']}".encode()
            ).digest(),
        )
        selected[split] = ranked[:quotas[split]]

    output.mkdir(parents=True, exist_ok=False)
    partition_specs = {}
    for split, rows in selected.items():
        rows.sort(key=lambda row: (
            str(row["service_date"]),
            str(row["scheduled_departure_utc"]),
            str(row["trip_id"]),
        ))
        path = output / f"{split}.jsonl"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            for source in rows:
                target_start = str(source["scheduled_departure_utc"])
                target_end = str(source["target_available_at"])
                record = {
                    "case_id": str(source["trip_id"]),
                    "service_date": str(source["service_date"]),
                    "target_start": target_start,
                    "target_end": target_end,
                    "feature_cutoff_at": str(source["feature_cutoff_at"]),
                    "value_available_at": str(source["known_schedule_available_at"]),
                    "target_available_at": target_end,
                    "operational_departure_id": str(source["operational_departure_id"]),
                    "source_trip_id": str(source["trip_id"]),
                    "source_departure_ids": [str(source["operational_departure_id"])],
                    "known_schedule_available_at": str(source["known_schedule_available_at"]),
                    "feature_availability_policy": (
                        "Python independently sums valid passenger_journeys by current "
                        "trip_id. Only route, direction, schedule and trip context published "
                        "by scheduled departure is used. Past demand is produced by the "
                        "existing Python past-only history implementation and becomes "
                        "available only when each earlier outcome was available by cutoff."
                    ),
                    **{name: float(source[name]) for name in FEATURES},
                    "passenger_demand": float(source["passenger_demand"]),
                    "target": float(source["passenger_demand"]),
                }
                stream.write(
                    json.dumps(record, sort_keys=True, allow_nan=False, separators=(",", ":"))
                    + "\n"
                )
        partition_specs[split] = {
            "path": path.name,
            "sha256": _sha256(path),
            "eligible_rows": len(eligible[split]),
            "sample_rows": len(rows),
            "source_paths": [
                item["path"] for item in inventory if item["table"] == "passenger_journeys"
            ],
        }

    source_hashes = {item["path"]: item["sha256"] for item in inventory}
    feature_version = hashlib.sha256(
        b"python-demand-raw-v1\x1f"
        + bytes.fromhex(_sha256(Path(__file__)))
        + bytes.fromhex(_sha256(Path(__file__).parents[1] / "python_pipeline/features.py"))
    ).hexdigest()
    manifest = {
        "schema_version": "1.0",
        "task_name": TASK,
        "dataset_version": attestation.dataset_version,
        "feature_version": f"python-raw-demand-{feature_version}",
        "producer": "python",
        "grain": "one current scheduled trip with valid observed passenger-journey demand",
        "target_definition": (
            "Python sum of valid source passenger_journeys.passenger_count by current "
            "trip_id; no Spark rows, features or targets are consumed."
        ),
        "features": list(FEATURES),
        "target": "target",
        "baseline_column": "historical_demand_lag_1",
        "baseline_type": "last_observation",
        "forecast_protocol": "rolling_origin_frozen_model",
        "forecast_horizon": "scheduled trip departure to final available journey evidence",
        "partitions": partition_specs,
        "certification": {
            "status": "CERTIFIED",
            "evidence_path": "feature_certification.json",
            "scope": (
                "owner-approved production-v1.1 raw source, Python-owned target and "
                "history derivation, bounded package integrity and chronology validation"
            ),
        },
        "source_provenance": {
            "dataset_version": attestation.dataset_version,
            "producer": "python",
            "implementation": "ml_execution.python_demand_handoff",
            "owner_attestation_reference": attestation.evidence_reference,
            "owner_marker_sha256": marker_hash,
            "source_dataset_certification": "OWNER_APPROVED_PRODUCTION_V1_1",
            "raw_input_policy": (
                "Certified production-v1.1 raw CSV only; no Spark features/predictions "
                "or other Python task outputs are read."
            ),
            "raw_input_sha256": source_hashes,
            "raw_input_file_count": len(inventory),
            "generation_manifest_sha256": _sha256(
                dataset_root / "metadata" / "generation_manifest.json"
            ),
            "source_run_id": generation["run_id"],
            "journey_rows_scanned": scanned,
            "trip_targets_after_context": len(records),
            "eligible_rows": {split: len(rows) for split, rows in eligible.items()},
            "sample_rows": quotas,
            "excluded_trip_targets": excluded_counts,
            "source_row_policy": (
                "Only quality_status=VALID, non-injected journey rows with finite, "
                "nonnegative passenger_count and availability, current non-injected "
                "trip plans, and source schedule context known by prediction time."
            ),
            "sampling_policy": (
                "Deterministic SHA-256 rank by fixed split and trip_id; proportional "
                "chronological quotas; no target or predictor synthesis."
            ),
            "split_boundaries": CHRONOLOGICAL_SPLITS,
            "max_rows": max_rows,
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
    loaded, _, hashes = load_features(manifest_path, max_rows=max_rows)
    print(json.dumps({
        "status": "HANDOFF_VALIDATED",
        "manifest": str(manifest_path),
        "sample_rows": quotas,
        "partition_sha256": hashes["partition_sha256"],
        "source_file_count": len(source_hashes),
        "owner_marker_sha256": marker_hash,
        "producer": loaded["producer"],
    }, sort_keys=True), flush=True)
    return manifest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument(
        "--certification-adapter",
        default="runtime_orchestration.production_v11_certification:ProductionV11Adapter",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-rows", type=int, default=100_000)
    args = parser.parse_args()
    adapter = load_certification_adapter(args.certification_adapter)
    export_demand_handoff(
        dataset_root=Path(args.dataset_root),
        marker_path=Path(args.marker),
        adapter=adapter,
        output=args.output,
        max_rows=args.max_rows,
    )


if __name__ == "__main__":
    main()

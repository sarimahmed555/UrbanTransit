"""Bounded executor and lightweight verification for the corrected dataset.

The executor links every unchanged raw shard from the frozen parent and replaces
only the shards named by the resolved closure.  Each replacement is written to an
exclusive temporary file inside the target tree, fsynced, and then atomically
renamed over the hard-linked directory entry, so the parent inode is never opened
for writing and ``production-v1`` stays byte-for-byte unchanged.

All metadata published in the corrected generation manifest is deterministic:
the declared correction epoch is a configuration constant and the wall-clock
materialization record is written to a runtime log that is deliberately not part
of the hashed manifest, so re-running the same correction configuration produces
byte-identical metadata.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import signal
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .bounded_remediation import (
    CANONICAL_MOVEMENTS, NULL, PLANNED_USED_STOPS, PLANNED_USED_VEHICLES, RAW_META_FIELDS,
    REQUIRED_USED_STOPS, REQUIRED_USED_VEHICLES, RESERVE_BYTES, SOURCE_VERSION, TARGET_VERSION,
    UTC, _classify_event_trips, _digest, _fields, _plus, _read_table, _scan_rows,
    load_production_config, write_shard,
)
from .config import canonical_json

# Declared, deterministic correction epoch.  It is part of the correction
# configuration, so the corrected metadata is byte-reproducible.
REMEDIATION_EPOCH = "2026-09-26T00:00:00Z"
CORRECTION_VERSION = "production-v1.1-correction-v1"
RUNTIME_LOG = "metadata/remediation_run_log.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _free(path: Path) -> int:
    return shutil.disk_usage(path).free


def correction_configuration(plan: Dict[str, Any]) -> Dict[str, Any]:
    """The deterministic correction configuration published with the version."""

    return {
        "correction_version": CORRECTION_VERSION,
        "remediation_epoch_utc": REMEDIATION_EPOCH,
        "seed": 20260924,
        "identity_namespace": "urbantransit-iq/synthetic/v2",
        "parent_dataset_version": SOURCE_VERSION,
        "corrected_dataset_version": TARGET_VERSION,
        "rules": {
            "event_exposure": "trips._applicable_event_id evaluates every physically applicable "
                              "context event; the superseded monthly selector exposed no operated trip",
            "movement_reapportionment": "unchanged largest-remainder boarding_weights/boarding_plan "
                                        "with spec.boarding_budget from allocate_movements; the fixed "
                                        "2,400,000 canonical movement budget is conserved",
            "bundle_movement": "complete journey/ticket/served-request bundles move between "
                               "departures; identities, source_row_id, raw_file and row_ordinal are "
                               "preserved and only corrected business values change",
            "stop_coverage": "observed route-stop positions are re-pointed at already opened stops "
                             "that no observed event used; no covered stop loses its last observed "
                             "position and no opening date is backdated",
            "vehicle_coverage": "actual assignments on whole-trip duties move to unused AVAILABLE "
                                "vehicles of identical nominal capacity; assignment identity, timing, "
                                "capacity and duty span are preserved",
            "selection_order": "stable business-key order with deterministic tie-breaks",
        },
        "targets": {
            "canonical_movements": CANONICAL_MOVEMENTS,
            "required_used_stops": REQUIRED_USED_STOPS,
            "planned_used_stops": PLANNED_USED_STOPS,
            "required_used_vehicles": REQUIRED_USED_VEHICLES,
            "planned_used_vehicles": PLANNED_USED_VEHICLES,
            "later_opening_stop_minimum": 50,
        },
        "storage": {
            "strategy": "hard-link unchanged shards; atomically replace only resolved changed shards",
            "reserve_bytes": RESERVE_BYTES,
            "closure_bytes": plan["closure_bytes"],
            "closure_files": len(plan["closure"]),
        },
        "deliberate_non_propagation": {
            "gps_event_coordinates": "GPS_Events carry no stop foreign key in the raw contract and no "
                                    "validator rule binds a ping coordinate to a route-stop position. "
                                    "Propagating the re-pointed stop coordinates would rewrite every "
                                    "gps_events CSV and JSONL mirror, which the measured storage "
                                    "inequality cannot absorb alongside the mandatory closure. "
                                    "Recorded as a declared residual, not silently dropped.",
        },
    }


# --------------------------------------------------------------------------- #
# Materialization
# --------------------------------------------------------------------------- #

INCOMPLETE_MARKER = "MATERIALIZATION_INCOMPLETE"
OWNERSHIP_MARKER = "MATERIALIZATION_OWNER"


class StorageAbort(RuntimeError):
    """The measured closure would breach the declared free-space reserve."""


def materialize(source: Path, target: Path, plan: Dict[str, Any], *, log=print,
                journal: Optional[Path] = None) -> Dict[str, Any]:
    source = source.resolve()
    target = target.resolve()
    started = _now()
    target_existed = target.exists()
    resume = target_existed
    if target_existed and journal is None:
        raise FileExistsError(
            f"{target} already exists and no progress journal was supplied; refusing to overlay "
            "a materialized or partially materialized version")
    if source.stat().st_dev != target.parent.stat().st_dev:
        raise OSError("hard-link remediation requires one filesystem for hard links")
    if plan["source_dataset_version"] != SOURCE_VERSION or plan["target_dataset_version"] != TARGET_VERSION:
        raise ValueError("plan does not describe this version pair")

    before = _free(source)

    def _stop(signum, _frame):
        # An external signal must never destroy completed shards: the journal makes
        # the run resumable, so leave the staged tree exactly as it stands.
        log(f"materialize: received signal {signum}; staged tree kept for resume")
        os._exit(143)

    for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(name, _stop)
        except (ValueError, OSError):
            pass

    done: Set[str] = set()
    if journal is not None and journal.exists():
        # The progress record is append-only and is always re-validated against the
        # tree: a shard counts as done only when its staged file exists and no longer
        # shares the parent inode, which is exactly the state an atomic rename leaves
        # behind.  Losing the tree therefore costs nothing but the shards it held.
        recorded = {line.strip() for line in journal.read_text().splitlines() if line.strip()}
        for entry in plan["closure"]:
            if entry["path"] not in recorded:
                continue
            mirror = target / entry["path"]
            origin = source / entry["path"]
            if mirror.exists() and origin.stat().st_ino != mirror.stat().st_ino:
                done.add(entry["path"])
    if target.exists():
        for leftover in target.rglob(".*remediation-tmp"):
            leftover.unlink(missing_ok=True)
            log(f"materialize: discarded stale staging file {leftover.relative_to(target)}")
        for required in ("metadata", "validation", ".uti_dataset_output"):
            if not (target / required).exists():
                raise RuntimeError(f"staged target is missing {required}; refusing to guess")
        linked = sum(1 for path in (target / "raw").rglob("*") if path.is_file())
        log(f"materialize: resuming with {len(done)}/{len(plan['closure'])} shards already replaced")
    else:
        if journal is not None:
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.touch(exist_ok=True)
        log(f"materialize: linking {source.name} -> {target.name} (free {before / 2**30:.2f} GiB)")
        target.mkdir(parents=True)
        linked = 0
        for path in sorted(q for q in (source / "raw").rglob("*") if q.is_file()):
            destination = target / "raw" / path.relative_to(source / "raw")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.link(path, destination)
            linked += 1
        shutil.copytree(source / "metadata", target / "metadata")
        shutil.copy2(source / ".uti_dataset_output", target / ".uti_dataset_output")
        (target / "validation").mkdir(exist_ok=True)
        (target / INCOMPLETE_MARKER).write_text(
            "This tree is a partially materialized corrected version. The corrected generation "
            "manifest is published only when materialization completes.\n", encoding="utf-8")
        # Ownership token: a concurrent executor must never be able to remove this
        # staged tree, so abort cleanup is only permitted for the tree's creator.
        (target / OWNERSHIP_MARKER).write_text(f"{os.getpid()} {source}\n", encoding="utf-8")
    created_here = not target_existed

    replaced: List[Dict[str, Any]] = []
    remaining = sum(entry["bytes"] for entry in plan["closure"] if entry["path"] not in done)
    already = plan["closure_bytes"] - remaining
    if before - remaining < RESERVE_BYTES:
        raise StorageAbort(
            f"storage inequality failed: free={before} remaining_closure={remaining} "
            f"already_materialized={already} reserve={RESERVE_BYTES}")
    log(f"materialize: storage inequality satisfied: free={before / 2**30:.2f} GiB "
        f"remaining={remaining / 2**30:.2f} GiB already_written={already / 2**30:.2f} GiB "
        f"reserve={RESERVE_BYTES / 2**30:.2f} GiB")
    try:
        for entry in plan["closure"]:
            if entry["path"] in done:
                replaced.append({"path": entry["path"], "table": entry["table"], "shard": entry["shard"],
                                 "changed_rows": entry["changed_rows"], "bytes": entry["bytes"]})
                continue
            if _free(source) - remaining < RESERVE_BYTES:
                raise StorageAbort(
                    f"aborting before {entry['path']}: free space would fall below the declared reserve")
            table_shard = source / entry["path"]
            target_shard = target / entry["path"]
            rows = write_shard(table_shard, target_shard, entry["table"],
                               plan["updates_by_shard"][entry["table"]][entry["shard"]])
            remaining -= entry["bytes"]
            replaced.append({"path": entry["path"], "table": entry["table"], "shard": entry["shard"],
                             "changed_rows": rows, "bytes": entry["bytes"]})
            if journal is not None:
                with journal.open("a", encoding="utf-8") as handle:
                    handle.write(entry["path"] + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            log(f"materialize: replaced {entry['path']} ({rows} rows, "
                f"{entry['bytes'] / 2**30:.3f} GiB) free={_free(source) / 2**30:.2f} GiB")
        metadata = _publish_metadata(source, target, plan, replaced, log)
        (target / INCOMPLETE_MARKER).unlink(missing_ok=True)
        (target / OWNERSHIP_MARKER).unlink(missing_ok=True)
    except StorageAbort as error:
        # A storage-inequality abort is a hard stop. The staged tree is removed only
        # when this invocation created it, so a concurrent executor's work is never
        # destroyed by another process's abort.
        for leftover in target.rglob(".*remediation-tmp"):
            leftover.unlink(missing_ok=True)
        if not created_here:
            shutil.rmtree(target, ignore_errors=True)
            log("materialize: aborted and removed the partially created target tree")
        else:
            log("materialize: aborted; staged tree kept because it was not created by this run")
        raise error
    except BaseException:
        for leftover in target.rglob(".*remediation-tmp"):
            leftover.unlink(missing_ok=True)
        log("materialize: failed; staged tree kept for resume")
        raise

    after = _free(source)
    return {
        "corrected_dataset_path": str(target),
        "resumed": resume,
        "hard_linked_files": linked - len(replaced),
        "newly_materialized_files": len(replaced),
        "total_raw_files": linked,
        "changed_rows": sum(item["changed_rows"] for item in replaced),
        "changed_files": replaced,
        "disk_free_before_bytes": before,
        "disk_free_after_bytes": after,
        "incremental_disk_bytes": before - after,
        "disk_free_remaining_bytes": after,
        "materialization_started_utc": started,
        "materialization_finished_utc": _now(),
        "metadata": metadata,
    }


def _publish_metadata(source: Path, target: Path, plan: Dict[str, Any],
                      replaced: List[Dict[str, Any]], log) -> Dict[str, Any]:
    """Publish corrected metadata, manifest, provenance and the change ledger."""

    meta = target / "metadata"
    changed_paths = {item["path"]: item for item in replaced}
    row_counts = {item["path"]: item["changed_rows"] for item in replaced}

    # Files whose corrected bytes differ from the parent declaration.
    generation = json.loads((meta / "generation_manifest.json").read_text())
    source_manifest = json.loads((source / "metadata" / "generation_manifest.json").read_text())
    declared = {item["path"]: item for item in source_manifest["files"]}

    # 1. Corrected movement budget evidence.
    budget = json.loads((meta / "production_movement_budget.json").read_text())
    budget.update({
        "canonical_movements": CANONICAL_MOVEMENTS,
        "flexible_trip_count": plan["movement"]["repaired_plan"]["flexible_trip_count"],
        "protected_movements": plan["movement"]["repaired_plan"]["protected_movements"],
        "rule": plan["movement"]["repaired_plan"]["rule"],
        "correction_version": CORRECTION_VERSION,
        "parent_dataset_version": SOURCE_VERSION,
        "event_exposure_rule": "physically applicable context events per operated departure",
    })
    (meta / "production_movement_budget.json").write_text(
        json.dumps(budget, indent=2, sort_keys=True) + "\n")

    # 2. Dataset version labels in inherited metadata.
    for name in ("scenario_manifest.json", "split_manifest.json"):
        inherited = json.loads((source / "metadata" / name).read_text())
        payload = json.loads((meta / name).read_text())
        payload["dataset_version"] = TARGET_VERSION
        if name == "split_manifest.json":
            payload["split_version"] = f"{TARGET_VERSION}-split-v1"
            payload["parent_split_version"] = inherited["split_version"]
            payload["membership_note"] = (
                "Chronological split boundaries, grouping rule, membership and purged cases are "
                "inherited unchanged from the parent version; only the version label changed.")
        (meta / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    # 3. Remediation artifacts.
    configuration = correction_configuration(plan)
    ledger = _change_ledger(plan)
    (meta / "remediation_change_ledger.json").write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    (meta / "remediation_correction_config.json").write_text(
        json.dumps(configuration, indent=2, sort_keys=True) + "\n")

    config = load_production_config(source, TARGET_VERSION)
    object.__setattr__(config, "_effective_timestamp", REMEDIATION_EPOCH)
    used = dict(source_manifest["configuration_used"])
    used["dataset_version"] = TARGET_VERSION
    used["generation_timestamp_utc"] = REMEDIATION_EPOCH

    provenance = {
        "corrected_dataset_version": TARGET_VERSION,
        "parent_dataset_version": SOURCE_VERSION,
        "parent_dataset_path": str(source),
        "corrected_dataset_path": str(target),
        "parent_run_id": source_manifest["run_id"],
        "parent_generation_timestamp_utc": source_manifest["generation_timestamp_utc"],
        "parent_configuration_hash": source_manifest["configuration_hash"],
        "corrected_run_id": config.run_id,
        "corrected_configuration_hash": config.config_hash,
        "remediation_epoch_utc": REMEDIATION_EPOCH,
        "correction_version": CORRECTION_VERSION,
        "source_id": source_manifest["source_ids"][0],
        "parent_immutable": True,
        "parent_write_policy": "hard-linked unchanged shards; changed shards replaced atomically "
                               "through exclusive temporary files; parent inodes never opened for writing",
        "identity_policy": "source_row_id, raw_file and row_ordinal are preserved on every corrected "
                           "row, so source, provenance and DQ fixture identities are unchanged",
        "changed_files": [item["path"] for item in replaced],
        "changed_rows": sum(row_counts.values()),
        "inherited_files": len(source_manifest["files"]) - len(replaced),
        "declared_versus_measured_metadata": _metadata_discrepancy(source, meta, source_manifest),
    }
    remediation = {
        "correction_version": CORRECTION_VERSION,
        "remediation_epoch_utc": REMEDIATION_EPOCH,
        "parent_dataset_version": SOURCE_VERSION,
        "parent_run_id": source_manifest["run_id"],
        "certification_status": "REQUIRES_REVALIDATION",
        "corrections": {
            "event_demand": {
                "status": "MATERIALIZED",
                "method": "deterministic re-apportionment of the fixed 2,400,000 movement budget "
                          "using the unchanged largest-remainder rule with repaired event exposure",
                "event_trips": plan["movement"]["event_trips"],
                "event_movements": plan["movement"]["event_movements"],
                "event_mean_movements": plan["movement"]["event_mean_movements"],
                "baseline_trips": plan["movement"]["baseline_trips"],
                "baseline_movements": plan["movement"]["baseline_movements"],
                "baseline_mean_movements": plan["movement"]["baseline_mean_movements"],
                "moved_journey_bundles": plan["movement"]["moved_journeys"],
            },
            "stop_coverage": {
                "status": "MATERIALIZED",
                "method": "deterministic re-pointing of observed route-stop positions at already "
                          "opened stops that no observed event used",
                "used_stops_before": plan["stops"]["used_stops_before"],
                "used_stops_after": plan["stops"]["used_stops_after"],
                "remapped_positions": plan["stops"]["remapped_positions"],
                "later_opening_stops": plan["stops"]["later_opening_cohort"],
            },
            "vehicle_coverage": {
                "status": "MATERIALIZED",
                "method": "deterministic substitution of whole-trip actual assignments onto unused "
                          "AVAILABLE vehicles of identical nominal capacity",
                "used_vehicles_before": plan["vehicles"]["used_vehicles_before"],
                "used_vehicles_after": plan["vehicles"]["used_vehicles_after"],
                "substitutions": len(plan["vehicles"]["substitutions"]),
            },
        },
        "change_ledger": "metadata/remediation_change_ledger.json",
        "correction_configuration": "metadata/remediation_correction_config.json",
        "provenance": "metadata/remediation_provenance.json",
    }
    (meta / "remediation_provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    (meta / "remediation_manifest.json").write_text(
        json.dumps(remediation, indent=2, sort_keys=True) + "\n")

    # 4. Manifest file inventory.  Raw shards are corrected in place, so their row
    #    counts are inherited; only metadata is re-measured.
    files: List[Dict[str, Any]] = []
    for item in source_manifest["files"]:
        path = item["path"]
        candidate = target / path
        if not candidate.exists():
            raise FileNotFoundError(candidate)
        rows = _count_rows(candidate, item["format"]) if path.startswith("metadata/") else item["rows"]
        files.append({
            "bytes": candidate.stat().st_size,
            "format": item["format"],
            "partition": item["partition"],
            "path": path,
            "rows": rows,
            "sha256": _digest(candidate),
            "table": item["table"],
        })
    for name in ("metadata/remediation_change_ledger.json",
                 "metadata/remediation_correction_config.json",
                 "metadata/remediation_manifest.json",
                 "metadata/remediation_provenance.json"):
        path = target / name
        if not path.exists():
            raise FileNotFoundError(path)
        files.append({"bytes": path.stat().st_size, "format": "json", "partition": "all",
                      "path": name, "rows": 1, "sha256": _digest(path), "table": "metadata"})
    files.sort(key=lambda item: item["path"])
    provenance["inherited_files"] = len(files) - len(replaced) - 4
    (meta / "remediation_provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    for item in files:
        if item["path"] == "metadata/remediation_provenance.json":
            item["bytes"] = (target / item["path"]).stat().st_size
            item["sha256"] = _digest(target / item["path"])

    body = dict(source_manifest)
    body["dataset_version"] = TARGET_VERSION
    body["configuration_used"] = used
    body["configuration_hash"] = config.config_hash
    body["run_id"] = config.run_id
    body["generation_timestamp_utc"] = REMEDIATION_EPOCH
    body["generation_duration_seconds"] = None
    body["runtime_fields_excluded_from_deterministic_hash"] = ["generation_duration_seconds"]
    body["files"] = files
    body["parent_dataset_version"] = SOURCE_VERSION
    body["remediation"] = remediation
    body["remediation_observed_statistics"] = {
        "canonical_movement_count": CANONICAL_MOVEMENTS,
        "used_vehicles": plan["vehicles"]["used_vehicles_after"],
        "used_stops": plan["stops"]["used_stops_after"],
        "event_trips": plan["movement"]["event_trips"],
        "event_mean_movements": plan["movement"]["event_mean_movements"],
        "baseline_mean_movements": plan["movement"]["baseline_mean_movements"],
    }
    body.pop("manifest_sha256", None)
    body.pop("deterministic_content_sha256", None)
    deterministic = {k: v for k, v in body.items()
                     if k not in {"generation_duration_seconds",
                                  "runtime_fields_excluded_from_deterministic_hash",
                                  "deterministic_content_sha256"}}
    body["deterministic_content_sha256"] = hashlib.sha256(
        canonical_json(deterministic).encode()).hexdigest()
    body["manifest_sha256"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    (meta / "generation_manifest.json").write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")

    log("materialize: published corrected metadata, manifest, provenance and change ledger")
    return {"files": len(files), "changed_files": len(replaced)}


def _count_rows(path: Path, fmt: str) -> int:
    if path.suffix == ".csv":
        with path.open(newline="") as handle:
            return max(0, sum(1 for _ in handle) - 1)
    if path.suffix == ".jsonl":
        with path.open() as handle:
            return sum(1 for line in handle if line.strip())
    return 1


def _metadata_discrepancy(source: Path, meta: Path, source_manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Record the pre-existing parent metadata hash mismatch without hiding it."""

    declared = next(item for item in source_manifest["files"]
                    if item["path"] == "metadata/private_injection_manifest.json")
    return {
        "path": declared["path"],
        "parent_declared": {"bytes": declared["bytes"], "sha256": declared["sha256"]},
        "parent_measured": {
            "bytes": (source / declared["path"]).stat().st_size,
            "sha256": _digest(source / declared["path"]),
        },
        "corrected_declared": {
            "bytes": (meta / "private_injection_manifest.json").stat().st_size,
            "sha256": _digest(meta / "private_injection_manifest.json"),
        },
        "note": "The parent generation manifest recorded a stale hash for the private injection "
                "manifest; the corrected version declares the measured hash and preserves the "
                "parent's declared and measured values side by side. The parent's own file was "
                "not modified.",
    }


def _change_ledger(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic row-level change inventory."""

    tables: Dict[str, Any] = {}
    for table, rows in plan["updates"].items():
        columns: Counter = Counter()
        for changes in rows.values():
            columns.update(changes.keys())
        tables[table] = {
            "changed_rows": len(rows),
            "changed_columns": dict(sorted(columns.items())),
        }
    return {
        "correction_version": CORRECTION_VERSION,
        "parent_dataset_version": SOURCE_VERSION,
        "corrected_dataset_version": TARGET_VERSION,
        "remediation_epoch_utc": REMEDIATION_EPOCH,
        "row_identity_policy": "source_row_id preserved; row_ordinal and raw_file preserved; "
                               "raw_record_text and raw_bytes_sha256 regenerated",
        "tables": tables,
        "stop_repointing": plan["stops"]["mappings"],
        "vehicle_substitution": plan["vehicles"]["substitutions"],
        "journey_bundle_moves": [{
            "journey_id": m["journey_id"], "ticket_id": m["ticket_id"], "request_id": m["request_id"],
            "passenger_id": m["passenger_id"], "from_trip_id": m["from_trip"],
            "to_trip_id": m["to_trip"], "board_sequence": m["board_sequence"],
            "alight_sequence": m["alight_sequence"],
            "origin_route_stop_id": m["origin_route_stop_id"],
            "destination_route_stop_id": m["destination_route_stop_id"],
            "boarded_at_utc": m["boarded_at_utc"], "alighted_at_utc": m["alighted_at_utc"],
            "service_date": m["service_date"],
        } for m in plan["moves"]],
    }

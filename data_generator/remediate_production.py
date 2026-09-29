"""Create a storage-safe immutable remediation version.

The command never edits ``production-v1`` and never copies its large raw
files. It creates a copy-on-write version using hard links for immutable raw
shards plus a small, explicit remediation manifest. Fact-row repairs remain
blocked until their coordinated overlay executor and revalidation are run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

from .config import canonical_json


DQ01_SOURCE = "SRC5a4ea46c63779235d49afc1a4c2ee74a9db2145007acf184ff15af5bceb9b281"
DQ01_JOURNEY = "Ja8ffc9f96431ae9ceae992ed6bcbf31be12a318e5d224f1d38788369c4fca4b8"


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _refresh_metadata_evidence(target: Path, source: Path) -> dict:
    """Refresh copied metadata evidence without changing raw data."""
    generation_path = target / "metadata" / "generation_manifest.json"
    private_path = target / "metadata" / "private_injection_manifest.json"
    generation = json.loads(generation_path.read_text())
    private = json.loads(private_path.read_text())
    source_generation = json.loads(
        (source / "metadata" / "generation_manifest.json").read_text()
    )
    source_entry = next(
        item for item in source_generation["files"]
        if item["path"] == "metadata/private_injection_manifest.json"
    )
    measured = {
        "path": "metadata/private_injection_manifest.json",
        "rows": 1,
        "bytes": private_path.stat().st_size,
        "sha256": _digest(private_path),
    }
    for item in generation["files"]:
        if item["path"] == measured["path"]:
            item.update(measured)
            break
    else:
        raise ValueError("generation manifest has no private injection entry")

    body = dict(generation)
    body.pop("manifest_sha256")
    deterministic = {
        key: value for key, value in body.items()
        if key not in {
            "generation_duration_seconds",
            "runtime_fields_excluded_from_deterministic_hash",
            "deterministic_content_sha256",
        }
    }
    generation["deterministic_content_sha256"] = hashlib.sha256(
        canonical_json(deterministic).encode()
    ).hexdigest()
    self_hash_body = dict(generation)
    self_hash_body.pop("manifest_sha256")
    generation["manifest_sha256"] = hashlib.sha256(
        canonical_json(self_hash_body).encode()
    ).hexdigest()
    generation_path.write_text(json.dumps(generation, indent=2, sort_keys=True) + "\n")
    return {
        "path": measured["path"],
        "source_declared": dict(source_entry),
        "source_measured": {
            "bytes": (source / measured["path"]).stat().st_size,
            "sha256": _digest(source / measured["path"]),
        },
        "corrected_declared": measured,
        "reason": "DQ01 metadata correction changed the copied private injection manifest",
    }


def _hardlink_tree(source: Path, target: Path) -> int:
    count = 0
    for path in source.rglob("*"):
        destination = target / path.relative_to(source)
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.link(path, destination)
            count += 1
    return count


def create_version(source: Path, target: Path, *, source_version="production-v1",
                   target_version="production-v1.1") -> dict:
    source = source.resolve()
    target = target.resolve()
    if source == target or source.name != source_version:
        raise ValueError("source must be the immutable production-v1 directory")
    if target.exists():
        raise FileExistsError(target)
    if source.stat().st_dev != target.parent.stat().st_dev:
        raise OSError("hard-link remediation requires one filesystem")

    target.mkdir(parents=True)
    raw_links = _hardlink_tree(source / "raw", target / "raw")
    shutil.copytree(source / "metadata", target / "metadata")

    manifest_path = target / "metadata" / "private_injection_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    injections = manifest.setdefault("injections", [])
    if not any(item.get("rule_id") == "DQ01" for item in injections):
        injections.append({
            "injection_id": "INJ-DQ01-MISSING-TICKET",
            "scenario_id": "C01",
            "table_name": "passenger_journeys",
            "source_row_id": DQ01_SOURCE,
            "business_key": DQ01_JOURNEY,
            "rule_id": "DQ01",
            "field_path": "ticket_id",
            "original_value": None,
            "mutation_spec": "physical ticket omitted while journey is retained",
            "expected_disposition": "ACCEPTED_FLAGGED",
            "usable_for": ["movement", "od", "timing", "boarding"],
            "seed": 20260924,
        })
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    artifact_refresh = _refresh_metadata_evidence(target, source)

    repair = {
        "repair_version": "production-v1.1-repair-v1",
        "dataset_version": target_version,
        "source_dataset_version": source_version,
        "source_dataset_path": str(source),
        "raw_storage": "hard-linked immutable shards; production-v1 remains untouched",
        "dq01": {
            "source_row_id": DQ01_SOURCE,
            "journey_id": DQ01_JOURNEY,
            "physical_row_preserved": True,
            "manifest_only": True,
        },
        "artifact_refresh": artifact_refresh,
        "coverage_repair": {
            "required_used_stops": 600,
            "required_used_vehicles": 300,
            "current_used_stops": 392,
            "current_used_vehicles": 229,
            "method": "deterministic post-opening stop-event and assignment overlay",
            "status": "PLANNED_NOT_MATERIALIZED",
        },
        "event_demand_repair": {
            "method": "deterministic matched-control event-window overlay",
            "baseline_mean": 20.418823589551348,
            "event_mean": 14.871681415929203,
            "status": "PLANNED_NOT_MATERIALIZED",
        },
        "certification_status": "REQUIRES_REVALIDATION",
    }
    (target / "metadata" / "remediation_manifest.json").write_text(
        json.dumps(repair, indent=2, sort_keys=True) + "\n"
    )
    provenance = {
        "dataset_version": target_version,
        "source_dataset_version": source_version,
        "raw_links": raw_links,
        "source_manifest_unchanged": True,
        "repair_manifest": "metadata/remediation_manifest.json",
        "production_v1_immutable": True,
    }
    (target / "metadata" / "remediation_provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    print(json.dumps(create_version(args.source, args.target), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

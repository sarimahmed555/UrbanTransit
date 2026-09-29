"""Owner-attested certification adapter for the verified production-v1.1 package."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

from data_generator.config import canonical_json
from .certification import CertificationAttestation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_VERSION = "production-v1.1"
CORRECTION_VERSION = "production-v1.1-correction-v1"
EXPECTED_RUN_ID = "RUN498f509222a2382c01b5c865f2a7174404f0d3a676128f4d01078ea8968ff8e9"
EXPECTED_PARENT_RUN_ID = "RUNdab8aef622d520438a679f11fa23729bfa12b502315b451359a8cef0620b31c6"
MARKER_RELATIVE_PATH = Path(
    "reports/dataset_certification/production-v1.1-owner-attestation.json"
)
APPROVAL_STATEMENT = (
    "I approve production-v1.1 (correction production-v1.1-correction-v1; "
    "run RUN498f509222a2382c01b5c865f2a7174404f0d3a676128f4d01078ea8968ff8e9), "
    "derived from production-v1, for downstream independent Python feature "
    "engineering and ML use, based on the bound v1.1 generation/remediation "
    "provenance and reports/production_v11_bounded_verification.json with 42/42 "
    "checks passing. I acknowledge that this is project-generated synthetic data; "
    "the earlier production-v1 validation failed and its reuse report is not a "
    "passing v1.1 validation; this scoped bounded verification does not reconcile "
    "GPS coordinates to stops (the raw GPS contract has no stop foreign key) or "
    "constitute external or real-world validation. This attestation does not "
    "modify the dataset files."
)

EVIDENCE_FILES = {
    "generation_manifest": "raw_data/production-v1.1/metadata/generation_manifest.json",
    "remediation_manifest": "raw_data/production-v1.1/metadata/remediation_manifest.json",
    "remediation_provenance": "raw_data/production-v1.1/metadata/remediation_provenance.json",
    "change_ledger": "raw_data/production-v1.1/metadata/remediation_change_ledger.json",
    "correction_config": "raw_data/production-v1.1/metadata/remediation_correction_config.json",
    "source_manifest": "raw_data/production-v1.1/metadata/source_manifest.json",
    "split_manifest": "raw_data/production-v1.1/metadata/split_manifest.json",
    "verification_report": "reports/production_v11_bounded_verification.json",
    "verification_log": "reports/production_v11_bounded_verification.log",
}

EXPECTED_CHECKS = frozenset({
    "production_v1_integrity_unchanged",
    "parent_only_preexisting_metadata_declaration",
    "hard_link_and_replace_split",
    "no_second_physical_copy",
    "manifest_hash_consistency",
    "manifest_self_hash",
    "manifest_deterministic_hash",
    "corrected_version_lineage",
    "operated_departures",
    "operational_stop_coverage",
    "stop_opening_and_event_timing_consistency",
    "stop_event_chronology",
    "operational_vehicle_coverage",
    "vehicle_capacity_snapshot_consistency",
    "vehicle_capacity_lifecycle",
    "vehicle_duty_nonoverlap",
    "exact_canonical_movements",
    "journey_stop_event_trip_link",
    "journey_sequence_and_route_stop_link",
    "journey_timing_derivation",
    "passenger_journey_nonoverlap",
    "condition_event_demand_increase",
    "movement_plan_mismatches_zero",
    "cancelled_trip_budget_invariant",
    "operated_trip_budget_invariant",
    "passenger_count_conservation",
    "journey_boarding_alighting_counts",
    "passenger_count_event_uniqueness",
    "load_continuity",
    "terminal_load_zero",
    "passenger_count_sequence_order",
    "overcrowding_flagging",
    "ticket_journey_business_context",
    "request_journey_business_context",
    "all_dq_families",
    "exact_duplicate_ticket_copies",
    "dq_fixture_sources_complete",
    "protected_row_envelope_integrity",
    "physical_duplicate_ledger",
    "dq_reconciliation_totals",
    "corrected_version_provenance",
    "change_ledger_consistency",
})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable certification evidence: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Certification evidence must be a JSON object: {path}")
    return value


class ProductionV11Adapter:
    """Bind explicit owner approval to immutable package and verifier hashes."""

    def __init__(self, project_root: str | Path | None = None):
        self.project_root = Path(project_root or PROJECT_ROOT).resolve()

    def certify(self, dataset_root: Path, marker_path: Path) -> CertificationAttestation:
        root = Path(dataset_root).resolve()
        marker = Path(marker_path).resolve()
        expected_root = (self.project_root / "raw_data" / DATASET_VERSION).resolve()
        expected_marker = (self.project_root / MARKER_RELATIVE_PATH).resolve()
        if root != expected_root:
            raise ValueError(f"Expected certified package at {expected_root}")
        if marker != expected_marker:
            raise ValueError(f"Expected owner attestation at {expected_marker}")
        if not root.is_dir() or not (root / "raw").is_dir():
            raise ValueError("Production-v1.1 package/raw directory is missing")

        evidence_paths = {
            key: (self.project_root / relative).resolve()
            for key, relative in EVIDENCE_FILES.items()
        }
        evidence_hashes = {key: _sha256(path) for key, path in evidence_paths.items()}
        attestation = _read_json(marker)
        if attestation.get("schema_version") != "1.0":
            raise ValueError("Unsupported owner-attestation schema")
        if attestation.get("status") != "CERTIFIED":
            raise ValueError("Owner attestation status must be CERTIFIED")
        if attestation.get("dataset_version") != DATASET_VERSION:
            raise ValueError("Owner attestation dataset version mismatch")
        if attestation.get("correction_version") != CORRECTION_VERSION:
            raise ValueError("Owner attestation correction version mismatch")
        if attestation.get("run_id") != EXPECTED_RUN_ID:
            raise ValueError("Owner attestation run ID mismatch")
        if attestation.get("approval_statement") != APPROVAL_STATEMENT:
            raise ValueError("Owner approval does not match the reviewed statement")
        if attestation.get("approval_channel") != "explicit interactive dataset-owner approval":
            raise ValueError("Owner approval channel is missing or invalid")
        if not isinstance(attestation.get("approved_by"), str) or not attestation["approved_by"].strip():
            raise ValueError("Owner approver must be identified")
        try:
            approved_at = datetime.fromisoformat(
                str(attestation.get("approved_at_utc", "")).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError("Owner approval timestamp must be ISO-8601 with timezone") from exc
        if approved_at.tzinfo is None:
            raise ValueError("Owner approval timestamp must include a timezone")
        if attestation.get("evidence_sha256") != evidence_hashes:
            raise ValueError("Owner attestation is not bound to the current evidence hashes")

        manifest_path = root / "metadata" / "generation_manifest.json"
        manifest = _read_json(manifest_path)
        if manifest.get("dataset_version") != DATASET_VERSION:
            raise ValueError("Generation manifest dataset version mismatch")
        if manifest.get("run_id") != EXPECTED_RUN_ID:
            raise ValueError("Generation manifest run ID mismatch")
        manifest_body = dict(manifest)
        declared_self_hash = manifest_body.pop("manifest_sha256", None)
        if hashlib.sha256(canonical_json(manifest_body).encode("utf-8")).hexdigest() != declared_self_hash:
            raise ValueError("Generation manifest self-hash mismatch")

        entries = manifest.get("files")
        if not isinstance(entries, list) or len(entries) != 613:
            raise ValueError("Expected all 613 manifest-bound package artifacts")
        seen = set()
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError("Malformed generation-manifest file entry")
            relative = Path(str(item.get("path", "")))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError("Unsafe path in generation manifest")
            candidate = root / relative
            if candidate.is_symlink():
                raise ValueError(f"Symlinked generation-manifest artifact rejected: {relative}")
            path = candidate.resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"Manifest-bound artifact missing or unsafe: {relative}")
            key = relative.as_posix()
            if key in seen:
                raise ValueError(f"Duplicate generation-manifest path: {key}")
            seen.add(key)
            if path.stat().st_size != item.get("bytes") or _sha256(path) != item.get("sha256"):
                raise ValueError(f"Generation-manifest artifact hash mismatch: {key}")

        remediation = _read_json(root / "metadata" / "remediation_manifest.json")
        provenance = _read_json(root / "metadata" / "remediation_provenance.json")
        if remediation.get("certification_status") != "REQUIRES_REVALIDATION":
            raise ValueError("Unexpected remediation certification state")
        if remediation.get("correction_version") != CORRECTION_VERSION:
            raise ValueError("Remediation manifest correction version mismatch")
        if remediation.get("parent_dataset_version") != "production-v1":
            raise ValueError("Remediation manifest parent version mismatch")
        if remediation.get("parent_run_id") != EXPECTED_PARENT_RUN_ID:
            raise ValueError("Remediation manifest parent run ID mismatch")
        if provenance.get("corrected_dataset_version") != DATASET_VERSION:
            raise ValueError("Remediation provenance dataset version mismatch")
        if provenance.get("corrected_run_id") != EXPECTED_RUN_ID:
            raise ValueError("Remediation provenance run ID mismatch")
        if provenance.get("parent_dataset_version") != "production-v1":
            raise ValueError("Remediation provenance parent version mismatch")
        if provenance.get("parent_run_id") != EXPECTED_PARENT_RUN_ID:
            raise ValueError("Remediation provenance parent run ID mismatch")

        report = _read_json(evidence_paths["verification_report"])
        checks = report.get("checks")
        if (
            report.get("passed") is not True
            or report.get("failed") != []
            or not isinstance(checks, list)
            or len(checks) != 42
            or {item.get("name") for item in checks if isinstance(item, dict)} != EXPECTED_CHECKS
            or any(item.get("passed") is not True for item in checks)
        ):
            raise ValueError("The bound v1.1 bounded-verification report did not pass 42/42 checks")
        lineage = next(item for item in checks if item.get("name") == "corrected_version_lineage")
        if lineage.get("corrected_run_id") != EXPECTED_RUN_ID:
            raise ValueError("Bounded-verification report refers to a different corrected run")

        return CertificationAttestation(
            dataset_version=DATASET_VERSION,
            evidence_reference=(
                f"{MARKER_RELATIVE_PATH.as_posix()}#sha256={_sha256(marker)}"
            ),
        )

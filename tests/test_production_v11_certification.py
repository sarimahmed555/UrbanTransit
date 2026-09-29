import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from data_generator.config import canonical_json
from runtime_orchestration.certification import CertificationAttestation
from runtime_orchestration.production_v11_certification import (
    APPROVAL_STATEMENT,
    DATASET_VERSION,
    EVIDENCE_FILES,
    EXPECTED_PARENT_RUN_ID,
    EXPECTED_CHECKS,
    EXPECTED_RUN_ID,
    MARKER_RELATIVE_PATH,
    ProductionV11Adapter,
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProductionV11CertificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.root = self.project / "raw_data" / DATASET_VERSION
        (self.root / "raw").mkdir(parents=True)
        metadata = self.root / "metadata"
        metadata.mkdir()

        entries = []
        for index in range(613):
            relative = f"raw/test/part-{index:03d}.csv"
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"row-{index}\n", encoding="utf-8")
            entries.append({
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })

        manifest = {
            "dataset_version": DATASET_VERSION,
            "run_id": EXPECTED_RUN_ID,
            "files": entries,
        }
        manifest["manifest_sha256"] = hashlib.sha256(
            canonical_json(manifest).encode("utf-8")
        ).hexdigest()
        (metadata / "generation_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (metadata / "remediation_manifest.json").write_text(json.dumps({
            "certification_status": "REQUIRES_REVALIDATION",
            "corrected_dataset_version": DATASET_VERSION,
            "correction_version": "production-v1.1-correction-v1",
            "parent_dataset_version": "production-v1",
            "parent_run_id": EXPECTED_PARENT_RUN_ID,
        }), encoding="utf-8")
        (metadata / "remediation_provenance.json").write_text(json.dumps({
            "corrected_dataset_version": DATASET_VERSION,
            "corrected_run_id": EXPECTED_RUN_ID,
            "parent_dataset_version": "production-v1",
            "parent_run_id": EXPECTED_PARENT_RUN_ID,
        }), encoding="utf-8")

        self.report_path = self.project / EVIDENCE_FILES["verification_report"]
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        checks = [
            {
                "name": name,
                "passed": True,
                **(
                    {"corrected_run_id": EXPECTED_RUN_ID}
                    if name == "corrected_version_lineage"
                    else {}
                ),
            }
            for name in sorted(EXPECTED_CHECKS)
        ]
        self.report_path.write_text(json.dumps({
            "passed": True,
            "failed": [],
            "checks": checks,
        }), encoding="utf-8")

        for relative in EVIDENCE_FILES.values():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("evidence\n", encoding="utf-8")

        self.marker = self.project / MARKER_RELATIVE_PATH
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self.attestation = {
            "schema_version": "1.0",
            "status": "CERTIFIED",
            "dataset_version": DATASET_VERSION,
            "correction_version": "production-v1.1-correction-v1",
            "run_id": EXPECTED_RUN_ID,
            "approval_statement": APPROVAL_STATEMENT,
            "approval_channel": "explicit interactive dataset-owner approval",
            "approved_by": "Dataset owner",
            "approved_at_utc": "2026-09-28T13:30:00Z",
            "evidence_sha256": {
                key: _sha256(self.project / relative)
                for key, relative in EVIDENCE_FILES.items()
            },
        }
        self.marker.write_text(json.dumps(self.attestation), encoding="utf-8")
        self.adapter = ProductionV11Adapter(self.project)

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_owner_attestation_bound_to_all_manifest_and_verifier_artifacts(self):
        result = self.adapter.certify(self.root, self.marker)
        self.assertIsInstance(result, CertificationAttestation)
        self.assertEqual(result.dataset_version, DATASET_VERSION)
        self.assertIn("sha256=", result.evidence_reference)

    def test_rejects_modified_manifest_bound_package_file(self):
        path = self.root / "raw/test/part-000.csv"
        path.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "artifact hash mismatch"):
            self.adapter.certify(self.root, self.marker)

    def test_rejects_owner_marker_bound_to_stale_verification_report(self):
        self.report_path.write_text('{"passed": false, "failed": ["check"], "checks": []}')
        with self.assertRaisesRegex(ValueError, "current evidence hashes"):
            self.adapter.certify(self.root, self.marker)

    def test_rejects_changed_approval_statement(self):
        self.attestation["approval_statement"] += " Approved."
        self.marker.write_text(json.dumps(self.attestation), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reviewed statement"):
            self.adapter.certify(self.root, self.marker)


if __name__ == "__main__":
    unittest.main()

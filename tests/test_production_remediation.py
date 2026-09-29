"""Storage-safe remediation contract tests using tiny synthetic trees."""

import json
import tempfile
import unittest
import hashlib
from pathlib import Path

from data_generator.remediate_production import create_version
from data_generator.validate_production import canonical_json


class ProductionRemediationTests(unittest.TestCase):
    def test_creates_hardlinked_immutable_version_and_dq01_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "production-v1"
            (source / "raw" / "trips").mkdir(parents=True)
            (source / "metadata").mkdir()
            raw = source / "raw" / "trips" / "part-000.csv"
            raw.write_text("trip_id\nT1\n")
            (source / "metadata" / "private_injection_manifest.json").write_text(
                json.dumps({"injections": []})
            )
            private = (source / "metadata" / "private_injection_manifest.json").read_bytes()
            (source / "metadata" / "generation_manifest.json").write_text(
                json.dumps({
                    "dataset_version": "production-v1",
                    "files": [{
                        "path": "metadata/private_injection_manifest.json",
                        "rows": 1,
                        "bytes": len(private),
                        "sha256": hashlib.sha256(private).hexdigest(),
                    }],
                    "actual_row_counts": {},
                    "deterministic_content_sha256": "",
                    "manifest_sha256": "",
                })
            )
            target = root / "production-v1.1"
            result = create_version(source, target)
            copied = target / "raw" / "trips" / "part-000.csv"
            self.assertEqual(result["raw_links"], 1)
            self.assertEqual(raw.stat().st_ino, copied.stat().st_ino)
            manifest = json.loads(
                (target / "metadata" / "private_injection_manifest.json").read_text()
            )
            self.assertEqual(manifest["injections"][0]["rule_id"], "DQ01")
            self.assertTrue(json.loads(
                (target / "metadata" / "remediation_provenance.json").read_text()
            )["production_v1_immutable"])
            provenance = json.loads(
                (target / "metadata" / "remediation_manifest.json").read_text()
            )
            refresh = provenance["artifact_refresh"]
            self.assertEqual(
                refresh["source_declared"]["bytes"],
                (source / "metadata" / "private_injection_manifest.json").stat().st_size,
            )
            self.assertEqual(
                refresh["corrected_declared"]["sha256"],
                hashlib.sha256(
                    (target / "metadata" / "private_injection_manifest.json").read_bytes()
                ).hexdigest(),
            )
            generation = json.loads(
                (target / "metadata" / "generation_manifest.json").read_text()
            )
            entry = next(
                item for item in generation["files"]
                if item["path"] == "metadata/private_injection_manifest.json"
            )
            self.assertEqual(entry["bytes"], refresh["corrected_declared"]["bytes"])
            body = dict(generation)
            manifest_hash = body.pop("manifest_sha256")
            self.assertEqual(
                hashlib.sha256(canonical_json(body).encode()).hexdigest(),
                manifest_hash,
            )


if __name__ == "__main__":
    unittest.main()

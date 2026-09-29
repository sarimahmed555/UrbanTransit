"""Fixture tests for manifest-bound remediation cache identity."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from data_generator.bounded_remediation import _source_fingerprint


class BoundedIntegrityTests(unittest.TestCase):
    def test_cache_fingerprint_changes_with_manifest_and_shard_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            table = source / "raw" / "trips"
            table.mkdir(parents=True)
            shard = table / "part-000.csv"
            original = b"trip_id\nT1\n"
            shard.write_bytes(original)
            manifest_path = source / "metadata" / "generation_manifest.json"
            manifest_path.parent.mkdir()
            entry = {
                "path": "raw/trips/part-000.csv",
                "bytes": len(original),
                "sha256": hashlib.sha256(original).hexdigest(),
            }
            manifest_path.write_text(
                json.dumps({"files": [entry]}), encoding="utf-8"
            )

            before = _source_fingerprint(source)
            shard.write_bytes(b"trip_id\nT2\n")
            entry["bytes"] = shard.stat().st_size
            entry["sha256"] = hashlib.sha256(shard.read_bytes()).hexdigest()
            manifest_path.write_text(
                json.dumps({"files": [entry]}), encoding="utf-8"
            )
            after = _source_fingerprint(source)
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()

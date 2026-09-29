"""Contract tests for the bounded remediation on tiny synthetic trees.

These tests never touch ``production-v1``.  They pin the storage-safety and
determinism properties that the bounded materializer must hold regardless of
scale: unchanged shards are hard links, changed shards are new inodes reached by
atomic replacement, the parent is byte-for-byte unchanged, and row identity plus
raw envelopes are preserved.
"""
import csv
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_generator.bounded_executor import (
    INCOMPLETE_MARKER,
    OWNERSHIP_MARKER,
    REMEDIATION_EPOCH,
    RESERVE_BYTES,
    SOURCE_VERSION,
    TARGET_VERSION,
    _change_ledger,
    correction_configuration,
    materialize,
)
from data_generator.bounded_remediation import RAW_META_FIELDS, rebuild_row, write_shard
from data_generator.config import canonical_json


def _envelope(fields, ordinal, raw_file, business):
    payload = {name: value for name, value in business.items() if name not in RAW_META_FIELDS}
    text = canonical_json(payload)
    row = dict(business)
    row.update({
        "raw_file": raw_file,
        "row_ordinal": str(ordinal),
        "raw_bytes_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "raw_record_text": text,
        "parse_status": "UNPARSED_RAW",
    })
    return [row.get(name, r"\N") for name in fields]


class BoundedRemediationTests(unittest.TestCase):
    def _tree(self, root: Path):
        from data_generator.schemas import column_names
        source = root / "production-v1"
        (source / "raw" / "route_stops").mkdir(parents=True)
        (source / "metadata").mkdir(parents=True)
        fields = list(column_names("route_stops", raw=True))
        business = {
            "dataset_version": "production-v1", "source_id": "SRC1", "source_row_id": "SRCA",
            "event_time": "2024-12-01T19:00:00.000000Z",
            "value_available_at": "2024-12-01T19:00:30.000000Z", "correction_time": r"\N",
            "ingestion_time": "2024-12-01T19:00:30.000000Z", "quality_status": "VALID",
            "unresolved_reason": r"\N", "route_stop_id": "RS1", "pattern_id": "RP1",
            "route_id": "R1", "stop_id": "OLD", "stop_sequence": "1",
            "distance_from_start_km": "0.0", "pickup_allowed": "true", "dropoff_allowed": "true",
        }
        with (source / "raw" / "route_stops" / "part-000.csv").open("w", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(fields)
            writer.writerow(_envelope(fields, 1, "raw/route_stops/part-000.csv", business))
        return source, fields, business

    def test_changed_shard_replaces_inode_and_leaves_parent_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, fields, business = self._tree(root)
            origin = source / "raw" / "route_stops" / "part-000.csv"
            before_bytes = origin.read_bytes()
            before_hash = hashlib.sha256(before_bytes).hexdigest()
            before_ino = origin.stat().st_ino
            target = root / "production-v1.1"
            (target / "raw" / "route_stops").mkdir(parents=True)
            mirror = target / "raw" / "route_stops" / "part-000.csv"
            os.link(origin, mirror)
            self.assertEqual(origin.stat().st_ino, mirror.stat().st_ino)

            applied = write_shard(origin, mirror, "route_stops", {"SRCA": {"stop_id": "NEW"}})
            self.assertEqual(applied, 1)
            self.assertNotEqual(origin.stat().st_ino, mirror.stat().st_ino)
            self.assertEqual(origin.read_bytes(), before_bytes)
            self.assertEqual(hashlib.sha256(origin.read_bytes()).hexdigest(), before_hash)
            with mirror.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["stop_id"], "NEW")
            self.assertEqual(rows[0]["source_row_id"], "SRCA")
            self.assertEqual(rows[0]["raw_file"], "raw/route_stops/part-000.csv")
            self.assertEqual(rows[0]["row_ordinal"], "1")
            payload = json.loads(rows[0]["raw_record_text"])
            self.assertEqual(payload["stop_id"], "NEW")
            self.assertEqual(payload["source_row_id"], "SRCA")
            self.assertEqual(
                hashlib.sha256(rows[0]["raw_record_text"].encode()).hexdigest(),
                rows[0]["raw_bytes_sha256"])
            self.assertEqual(before_ino, origin.stat().st_ino)

    def test_unchanged_rows_keep_their_exact_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, fields, business = self._tree(root)
            shard = source / "raw" / "route_stops" / "part-000.csv"
            second = dict(business, source_row_id="SRCB", route_stop_id="RS2", stop_sequence="2",
                          stop_id="KEEP")
            with shard.open("a", newline="") as handle:
                csv.writer(handle, lineterminator="\n").writerow(
                    _envelope(fields, 2, "raw/route_stops/part-000.csv", second))
            original_lines = shard.read_text().splitlines(keepends=True)
            target = root / "production-v1.1"
            (target / "raw" / "route_stops").mkdir(parents=True)
            mirror = target / "raw" / "route_stops" / "part-000.csv"
            os.link(shard, mirror)
            applied = write_shard(shard, mirror, "route_stops", {"SRCA": {"stop_id": "NEW"}})
            self.assertEqual(applied, 1)
            rewritten = mirror.read_text().splitlines(keepends=True)
            self.assertEqual(len(rewritten), len(original_lines))
            self.assertEqual(rewritten[0], original_lines[0])
            self.assertEqual(rewritten[2], original_lines[2])
            self.assertNotEqual(rewritten[1], original_lines[1])

    def test_rebuild_row_rejects_unknown_columns(self):
        with self.assertRaises(KeyError):
            rebuild_row("route_stops", [""] * 24, {"not_a_column": "x"})

    def test_write_shard_refuses_to_drop_a_planned_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = self._tree(root)
            target = root / "production-v1.1"
            (target / "raw" / "route_stops").mkdir(parents=True)
            mirror = target / "raw" / "route_stops" / "part-000.csv"
            os.link(source / "raw" / "route_stops" / "part-000.csv", mirror)
            with self.assertRaises(ValueError):
                write_shard(source / "raw" / "route_stops" / "part-000.csv", mirror,
                            "route_stops", {"MISSING": {"stop_id": "NEW"}})
            self.assertFalse(list(mirror.parent.glob(".*tmp*")))
            self.assertEqual(mirror.stat().st_ino,
                             (source / "raw" / "route_stops" / "part-000.csv").stat().st_ino)

    def test_correction_configuration_is_deterministic(self):
        plan = {"closure_bytes": 123, "closure": [1]}
        first = correction_configuration(plan)
        second = correction_configuration(plan)
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(first["remediation_epoch_utc"], REMEDIATION_EPOCH)
        self.assertEqual(first["parent_dataset_version"], "production-v1")
        self.assertEqual(first["corrected_dataset_version"], "production-v1.1")
        self.assertEqual(first["targets"]["canonical_movements"], 2_400_000)
        self.assertIn("gps_event_coordinates", first["deliberate_non_propagation"])

    def test_change_ledger_counts_column_names_not_mixed_replacement_values(self):
        plan = {
            "updates": {
                "route_stops": {
                    "SRC1": {"stop_id": "NEW_STOP", "stop_sequence": 2},
                    "SRC2": {"stop_id": 17, "pickup_allowed": False},
                },
            },
            "stops": {"mappings": []},
            "vehicles": {"substitutions": []},
            "moves": [],
        }

        ledger = _change_ledger(plan)

        self.assertEqual(ledger["tables"]["route_stops"], {
            "changed_rows": 2,
            "changed_columns": {
                "pickup_allowed": 1,
                "stop_id": 2,
                "stop_sequence": 1,
            },
        })

    def test_fully_journaled_resume_skips_shards_and_publishes_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "production-v1"
            target = root / "production-v1.1"
            table_source = source / "raw" / "route_stops"
            table_target = target / "raw" / "route_stops"
            table_source.mkdir(parents=True)
            table_target.mkdir(parents=True)
            (target / "metadata").mkdir()
            (target / "validation").mkdir()
            (target / ".uti_dataset_output").write_text("staged\n")
            (target / INCOMPLETE_MARKER).write_text("resume required\n")
            (target / OWNERSHIP_MARKER).write_text("test\n")

            closure = []
            journal = root / "materialization.journal"
            with journal.open("w", encoding="utf-8") as handle:
                for index in range(395):
                    shard = f"part-{index:03}.csv"
                    relative = f"raw/route_stops/{shard}"
                    (table_source / shard).write_text(f"source {index}\n")
                    (table_target / shard).write_text(f"materialized {index}\n")
                    closure.append({
                        "path": relative,
                        "table": "route_stops",
                        "shard": shard,
                        "changed_rows": 1,
                        "bytes": 20,
                    })
                    handle.write(relative + "\n")

            plan = {
                "source_dataset_version": SOURCE_VERSION,
                "target_dataset_version": TARGET_VERSION,
                "closure": closure,
                "closure_bytes": sum(item["bytes"] for item in closure),
            }

            with (
                patch("data_generator.bounded_executor.signal.signal"),
                patch("data_generator.bounded_executor._free",
                      return_value=RESERVE_BYTES + plan["closure_bytes"] + 1),
                patch("data_generator.bounded_executor.write_shard") as write_shard,
                patch("data_generator.bounded_executor._publish_metadata",
                      return_value={"files": 1, "changed_files": 395}) as publish,
            ):
                result = materialize(source, target, plan, journal=journal, log=lambda _: None)

            write_shard.assert_not_called()
            publish.assert_called_once()
            self.assertEqual(len(publish.call_args.args[3]), 395)
            self.assertTrue(result["resumed"])
            self.assertFalse((target / INCOMPLETE_MARKER).exists())
            self.assertFalse((target / OWNERSHIP_MARKER).exists())


if __name__ == "__main__":
    unittest.main()

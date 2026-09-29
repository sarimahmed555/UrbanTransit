"""Standard-library tests for deterministic generation and smoke acceptance."""
from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from data_generator.config import default_smoke, production
from data_generator.generate import generate_dataset
from data_generator.ids import entity_id
from data_generator.production_gate import compare_production_counts
from data_generator.validation import leakage_checks, relationship_checks, count_checks, temporal_checks, transport_checks, schema_checks
from data_generator.validate import validate_dataset


class DataGeneratorTests(unittest.TestCase):
    def test_ids_are_stable_and_namespace_aware(self) -> None:
        first = entity_id("Passenger", "PASSENGER-000001")
        second = entity_id("Passenger", "PASSENGER-000001")
        changed = entity_id("Passenger", "PASSENGER-000002")
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertEqual(len(first.split("P", 1)[1]), 64)

    def test_production_is_explicitly_guarded(self) -> None:
        with self.assertRaises(PermissionError):
            production("/tmp/should-not-be-written").ensure_valid()
        self.assertEqual(production("/tmp/should-not-be-written", allow_production=True).profile, "production")

    def test_logical_configuration_is_output_path_invariant(self) -> None:
        with tempfile.TemporaryDirectory(prefix="uti-config-a-") as first_tmp, tempfile.TemporaryDirectory(prefix="uti-config-b-") as second_tmp:
            first = default_smoke(Path(first_tmp) / "one", seed=123, timestamp="2026-09-24T00:00:00Z")
            second = default_smoke(Path(second_tmp) / "two", seed=123, timestamp="2026-09-24T00:00:00Z")
            self.assertEqual(first.config_hash, second.config_hash)
            self.assertEqual(first.run_id, second.run_id)

    def test_production_unserved_target_is_bounded(self) -> None:
        from data_generator.generators.common import GenerationContext
        from data_generator.generators.journeys import build_unserved_requests

        config = production("/tmp/uti-unserved-target", allow_production=True, timestamp="2026-09-24T00:00:00Z")
        network = SimpleNamespace(
            stops=[
                {"stop_id": "S1", "opened_on": "2025-01-01", "closed_on": None},
                {"stop_id": "S2", "opened_on": "2025-01-01", "closed_on": None},
            ],
            routes=[{"route_id": "R1"}],
        )
        rows = build_unserved_requests(GenerationContext(config), network, ["P1", "P2"], [date(2025, 1, 1)])
        self.assertEqual(len(rows), 100_000)
        self.assertEqual(len({row["request_id"] for row in rows}), 100_000)

    def test_production_gate_reports_missing_or_wrong_targets(self) -> None:
        passed = compare_production_counts({"passengers": 80_000}, {"passengers": 80_000})
        failed = compare_production_counts({"passengers": 79_999}, {"passengers": 80_000})
        self.assertTrue(passed["passed"])
        self.assertFalse(failed["passed"])
        self.assertFalse(failed["comparisons"]["passengers"]["passed"])

    def test_core_flow_and_replacement_contract(self) -> None:
        # A small direct simulation catches conservation regressions without
        # requiring a generated directory.
        from data_generator.generators.common import GenerationContext
        from data_generator.generators.context import build_context_events
        from data_generator.generators.network import build_network
        from data_generator.generators.passengers import build_passengers
        from data_generator.generators.service import build_service
        from data_generator.generators.stop_events import simulate_trip
        from data_generator.generators.trips import build_trip_specs, materialize_trip_rows
        from data_generator.generators.vehicles import build_vehicles

        config = default_smoke("/tmp/core-flow-check", timestamp="2026-01-01T00:00:00Z")
        ctx = GenerationContext(config)
        network = build_network(ctx)
        context = build_context_events(ctx, network)
        service = build_service(ctx, network, context)
        vehicles = build_vehicles(ctx)
        passengers = build_passengers(ctx)
        specs = build_trip_specs(ctx, network, service, context)
        replacement_spec = next(spec for spec in specs if spec.replacement)
        materialize_trip_rows(ctx, replacement_spec, network, service, vehicles)
        bundle = simulate_trip(ctx, replacement_spec, network, service, vehicles, list(passengers.by_id))
        self.assertGreaterEqual(len(bundle.transfers), 3)
        for count in bundle.counts:
            self.assertEqual(int(count["onboard_departure"]), int(count["onboard_arrival"]) - int(count["alightings"]) + int(count["boardings"]))
            self.assertGreaterEqual(int(count["transfer_out_count"]), 0)
            self.assertEqual(count["transfer_out_count"], count["transfer_in_count"])
        replacement_count = next(count for count in bundle.counts if count["replacement_event"])
        self.assertEqual(int(replacement_count["transfer_out_count"]), len(bundle.transfers))

    def test_smoke_generation_and_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="uti-smoke-test-") as temporary:
            root = Path(temporary) / "smoke"
            config = default_smoke(root, seed=424242, force=True, timestamp="2026-09-24T00:00:00Z")
            generate_dataset(config)
            report = validate_dataset(root)
            self.assertEqual(report.failed_count, 0, [check.as_dict() for check in report.checks if not check.passed])
            self.assertGreaterEqual(report.passed_count, 190)
            self.assertTrue((root / "raw/passenger_journeys/part-000.csv").exists())
            injection_manifest = json.loads((root / "metadata/private_injection_manifest.json").read_text())
            self.assertEqual(len({item["rule_id"] for item in injection_manifest["injections"] if item["rule_id"].startswith("DQ")}), 16)
            self.assertTrue((root / "metadata/g1_unknown_assignment_fixture.json").exists())
            self.assertTrue((root / "metadata/g5_unresolved_core_fixture.json").exists())
            with (root / "raw/gps_events/part-000.jsonl").open() as handle:
                gps_row = json.loads(next(line for line in handle if line.strip()))
            self.assertIsInstance(gps_row["observation_index"], int)
            self.assertTrue(gps_row["raw_file"].endswith(".csv"))
            with (root / "metadata/value_revisions.csv").open() as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 2)
            with (root / "metadata/prediction_cases.csv").open() as handle:
                self.assertTrue(any(row["purged"] == "true" for row in csv.DictReader(handle)))

    def test_same_seed_reproduces_raw_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="uti-determinism-a-") as first_tmp, tempfile.TemporaryDirectory(prefix="uti-determinism-b-") as second_tmp:
            first = Path(first_tmp) / "smoke"
            second = Path(second_tmp) / "smoke"
            generate_dataset(default_smoke(first, seed=777, force=True, timestamp="2026-09-24T00:00:00Z"))
            generate_dataset(default_smoke(second, seed=777, force=True, timestamp="2026-09-24T00:00:00Z"))
            raw_files = sorted(path.relative_to(first) for path in (first / "raw").rglob("*") if path.is_file())
            self.assertTrue(raw_files)
            for relative in raw_files:
                left = (first / relative).read_bytes()
                right = (second / relative).read_bytes()
                self.assertEqual(hashlib.sha256(left).hexdigest(), hashlib.sha256(right).hexdigest(), str(relative))
            first_manifest = json.loads((first / "metadata/generation_manifest.json").read_text())
            second_manifest = json.loads((second / "metadata/generation_manifest.json").read_text())
            self.assertEqual(first_manifest["configuration_hash"], second_manifest["configuration_hash"])
            self.assertEqual(first_manifest["run_id"], second_manifest["run_id"])
            self.assertEqual(first_manifest["deterministic_content_sha256"], second_manifest["deterministic_content_sha256"])


if __name__ == "__main__":
    unittest.main()

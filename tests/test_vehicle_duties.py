"""Focused fleet safety regressions, including the integrated replacement path."""
import unittest
from datetime import date
from data_generator.generators.vehicle_duties import VehicleDutyAllocator


def vehicle(key, **kwargs):
    return dict(vehicle_id=key, operational_status=kwargs.get('status', 'AVAILABLE'),
                commissioned_on=kwargs.get('commissioned', '2025-01-01'),
                retired_on=kwargs.get('retired'), nominal_capacity=kwargs.get('capacity', 40))


class VehicleDutyTests(unittest.TestCase):
    day = date(2025, 1, 1)

    def test_overlap_is_rejected(self):
        allocator = VehicleDutyAllocator([vehicle('a')])
        allocator.allocate(self.day, '2025-01-01T06:00:00Z', '2025-01-01T08:00:00Z')
        with self.assertRaises(RuntimeError):
            allocator.allocate(self.day, '2025-01-01T07:00:00Z', '2025-01-01T09:00:00Z')

    def test_buffer_boundary_and_out_of_order_intervals(self):
        allocator = VehicleDutyAllocator([vehicle('a')])
        allocator.allocate(self.day, '2025-01-01T06:00:00Z', '2025-01-01T08:00:00Z')
        with self.assertRaises(RuntimeError):
            allocator.allocate(self.day, '2025-01-01T08:59:59Z', '2025-01-01T10:00:00Z')
        allocator.allocate(self.day, '2025-01-01T09:00:00Z', '2025-01-01T10:00:00Z')
        with self.assertRaises(RuntimeError):
            allocator.allocate(self.day, '2025-01-01T04:00:00Z', '2025-01-01T05:00:01Z')

    def test_availability(self):
        allocator = VehicleDutyAllocator([vehicle('a', status='MAINTENANCE'),
            vehicle('b', commissioned='2025-07-01'), vehicle('c', retired='2025-01-01'), vehicle('d')])
        self.assertEqual(allocator.allocate(self.day, '2025-01-01T06:00:00Z', '2025-01-01T08:00:00Z')['vehicle_id'], 'd')

    def test_replacement_and_determinism(self):
        def allocate(fleet):
            allocator = VehicleDutyAllocator(fleet)
            args = self.day, '2025-01-01T06:00:00Z', '2025-01-01T08:00:00Z'
            incoming = allocator.allocate(*args)
            outgoing = allocator.allocate(*args, exclude=(incoming['vehicle_id'],), different_capacity=incoming['nominal_capacity'])
            return incoming['vehicle_id'], outgoing['vehicle_id']
        fleet = [vehicle('a'), vehicle('b'), vehicle('c', capacity=60)]
        self.assertEqual(allocate(fleet), ('a', 'c'))
        self.assertEqual(allocate(fleet), allocate(list(reversed(fleet))))

    def test_integrated_production_replacement_phases(self):
        from data_generator.config import default_smoke
        from dataclasses import replace
        from data_generator.generators.common import GenerationContext
        from data_generator.generators.network import build_network
        from data_generator.generators.context import build_context_events
        from data_generator.generators.service import build_service
        from data_generator.generators.trips import build_trip_specs, materialize_trip_rows
        from data_generator.generators.vehicles import build_vehicles
        from data_generator.generators.stop_events import simulate_trip
        ctx = GenerationContext(default_smoke())
        network = build_network(ctx)
        events = build_context_events(ctx, network)
        service = build_service(ctx, network, events)
        fleet = build_vehicles(ctx)
        spec = next(s for s in build_trip_specs(ctx, network, service, events) if s.replacement)
        ctx.config = replace(ctx.config, profile='production')
        materialize_trip_rows(ctx, spec, network, service, fleet)
        bundle = simulate_trip(ctx, spec, network, service, fleet, [f'P{i}' for i in range(1000)])
        first, second = bundle.actual_assignments
        self.assertNotEqual(first['vehicle_id'], second['vehicle_id'])
        self.assertEqual(first['effective_end_utc'], second['effective_start_utc'])
        handover = next(row for row in bundle.stop_events if row['stop_sequence'] == first['end_stop_sequence'])
        self.assertEqual(handover['arrival_assignment_id'], first['assignment_id'])
        self.assertEqual(handover['departure_assignment_id'], second['assignment_id'])
        for assignment in bundle.actual_assignments:
            from data_generator.generators.common import parse_utc
            self.assertTrue(any(a <= parse_utc(assignment['effective_start_utc']) and parse_utc(assignment['effective_end_utc']) <= b
                                for a,b in ctx.vehicle_duties.reservations[assignment['vehicle_id']]))

    def test_production_fixture_sources_survive_dimension_capture(self):
        from data_generator.config import production
        from data_generator.generators.common import GenerationContext
        from data_generator.generate import _write_rows
        from types import SimpleNamespace
        ctx = GenerationContext(production(allow_production=True))
        manager = SimpleNamespace(table_writer=lambda table: SimpleNamespace(write=lambda row: 'source-' + str(row)))
        _write_rows(ctx, manager, 'passengers', ({'passenger_id': str(i)} for i in range(11000)))
        _write_rows(ctx, manager, 'passenger_counts', [{'count_id': 'fixture'}])
        self.assertIsNotNone(ctx.source_for('passenger_counts', 'fixture'))
        self.assertEqual(len(ctx.source_refs), 1001)

    def test_failed_preflight_precedes_output_creation(self):
        from unittest.mock import patch
        from data_generator.config import production
        from data_generator.generate import generate_dataset
        with patch('data_generator.preflight.run_preflight', return_value={'passed': False, 'checks': {'calendar': False}}), patch('data_generator.generate.OutputManager') as manager:
            with self.assertRaisesRegex(RuntimeError, 'calendar'):
                generate_dataset(production(allow_production=True))
            manager.assert_not_called()

    def test_production_rejects_in_memory_validation(self):
        from unittest.mock import patch
        from pathlib import Path
        from data_generator.validate import validate_dataset
        with patch.object(Path, 'read_text', return_value='{"configuration_used":{"profile":"production"}}'):
            with self.assertRaisesRegex(ValueError, 'smoke-only'):
                validate_dataset(Path('raw_data/unused'))

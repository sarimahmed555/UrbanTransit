"""GPS vehicle attribution must follow the observed handover phase."""
import unittest
from dataclasses import replace
from data_generator.config import default_smoke
from data_generator.generators.common import GenerationContext
from data_generator.generators.network import build_network
from data_generator.generators.context import build_context_events
from data_generator.generators.service import build_service
from data_generator.generators.trips import build_trip_specs, materialize_trip_rows
from data_generator.generators.vehicles import build_vehicles
from data_generator.generators.stop_events import simulate_trip


class GpsReplacementTests(unittest.TestCase):
    def test_all_gps_points_belong_to_active_vehicle_phase(self):
        for profile in ('smoke', 'production'):
            with self.subTest(profile=profile):
                ctx = GenerationContext(default_smoke())
                network = build_network(ctx)
                events = build_context_events(ctx, network)
                service = build_service(ctx, network, events)
                fleet = build_vehicles(ctx)
                spec = next(s for s in build_trip_specs(ctx, network, service, events) if s.replacement)
                ctx.config = replace(ctx.config, profile=profile)
                materialize_trip_rows(ctx, spec, network, service, fleet)
                bundle = simulate_trip(ctx, spec, network, service, fleet, [f'P{i}' for i in range(1000)])
                assignments = {r['assignment_id']: r for r in bundle.actual_assignments}
                stop_events = {r['stop_event_id']: r for r in bundle.stop_events}
                for gps in bundle.gps_events:
                    if gps['assignment_id'] is None:
                        continue
                    assignment = assignments[gps['assignment_id']]
                    self.assertLessEqual(assignment['effective_start_utc'], gps['observed_at_utc'])
                    self.assertLessEqual(gps['observed_at_utc'], assignment['effective_end_utc'])
                    if gps['stop_event_id']:
                        event = stop_events[gps['stop_event_id']]
                        phase = 'arrival' if gps['observation_index'] == 0 else 'departure'
                        self.assertEqual(gps['assignment_id'], event[f'{phase}_assignment_id'])

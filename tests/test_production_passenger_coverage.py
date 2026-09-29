"""Production allocation must cover the population across separate trips."""
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


class PassengerCoverageTests(unittest.TestCase):
    def test_separate_trips_cover_population_deterministically(self):
        def run():
            ctx = GenerationContext(default_smoke(timestamp='2026-09-24T00:00:00Z'))
            network = build_network(ctx)
            events = build_context_events(ctx, network)
            service = build_service(ctx, network, events)
            fleet = build_vehicles(ctx)
            specs = build_trip_specs(ctx, network, service, events)
            ctx.config = replace(ctx.config, profile='production')
            passengers = [f'P{i}' for i in range(1000)]
            rides = []
            for spec in sorted(specs, key=lambda s: (s.service_date, int(s.schedule['departure_offset_sec']), s.slot_index, s.index)):
                materialize_trip_rows(ctx, spec, network, service, fleet)
                rides.extend(simulate_trip(ctx, spec, network, service, fleet, passengers).journeys)
            self.assertEqual(len({r['passenger_id'] for r in rides}), 1000)
            intervals = {}
            for row in rides:
                intervals.setdefault(row['passenger_id'], []).append((row['boarded_at_utc'], row['alighted_at_utc']))
            for values in intervals.values():
                values.sort()
                self.assertTrue(all(a[1] <= b[0] for a, b in zip(values, values[1:])))
            return [(r['journey_id'], r['passenger_id']) for r in rides]
        self.assertEqual(run(), run())

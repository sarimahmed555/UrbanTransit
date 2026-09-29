"""Focused metadata/streaming regressions; never generate production fact tables."""
import csv
import hashlib
import io
import tempfile
import unittest
from collections import defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from data_generator.config import production, default_smoke
from data_generator.generators.common import GenerationContext
from data_generator.generators.network import build_network
from data_generator.generators.context import build_context_events
from data_generator.generators.service import build_service
from data_generator.generators.trips import build_trip_specs, materialize_trip_rows
from data_generator.generators.vehicles import build_vehicles
from data_generator.generators.movement_budget import allocate_movements, metadata_demand, boarding_plan, boarding_weights
from data_generator.generators.ticket_duplicates import TicketDuplicateInjector, duplicate_budget
from data_generator.generators.stop_events import simulate_trip, demand_base
from data_generator.generators.journeys import canonical_movement_count, iter_unserved_requests
from data_generator.writers import OutputManager


class ProductionBlockerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = production(allow_production=True, timestamp='2026-09-24T00:00:00Z')
        cls.ctx = GenerationContext(cls.config)
        cls.network = build_network(cls.ctx)
        cls.events = build_context_events(cls.ctx, cls.network)
        cls.service = build_service(cls.ctx, cls.network, cls.events)
        cls.vehicles = build_vehicles(cls.ctx)
        cls.specs = build_trip_specs(cls.ctx, cls.network, cls.service, cls.events)
        cls.summary = allocate_movements(cls.ctx, cls.specs, cls.network, cls.service)

    def test_all_calendar_dates_and_departure_targets(self):
        self.assertEqual(len(self.specs), 120000)
        self.assertEqual(sum(not s.cancelled for s in self.specs), 117600)
        self.assertEqual(len(self.service.schedules), 2600)
        exceptions = defaultdict(set)
        for row in self.service.exceptions:
            exceptions[row['service_id'], row['exception_date']].add(row['action'])
        flags = ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')
        for spec in self.specs:
            day = str(spec.service_date)
            calendar = self.service.calendar_by_id[spec.service_id]
            actions = exceptions[spec.service_id, day]
            self.assertNotIn('REMOVE', actions)
            self.assertTrue('ADD' in actions or calendar[flags[spec.service_date.weekday()]])
            self.assertLessEqual(spec.schedule['valid_from'], day)
            self.assertTrue(spec.schedule['valid_to'] is None or day < spec.schedule['valid_to'])
            self.assertEqual(spec.service_id, spec.schedule['service_id'])

    def test_remove_and_add_override_flags(self):
        pattern = self.network.patterns[0]['pattern_id']
        weekday = next(c['service_id'] for c in self.service.calendars if c['calendar_day_type'] == 'WEEKDAY')
        monday, sunday = date(2025, 1, 6), date(2025, 1, 5)
        service = replace(self.service, exceptions=[
            {'service_id':weekday,'exception_date':str(monday),'action':'REMOVE'},
            {'service_id':weekday,'exception_date':str(sunday),'action':'ADD'}])
        self.assertFalse(service.calendar_active(weekday, monday))
        self.assertTrue(service.calendar_active(weekday, sunday))
        self.assertTrue(service.compatible_schedules(pattern, monday))
        self.assertTrue(all(s['service_id'] != weekday for s in service.compatible_schedules(pattern, monday)))
        self.assertTrue(any(s['service_id'] == weekday for s in service.compatible_schedules(pattern, sunday)))
        conflicting = replace(service, exceptions=service.exceptions + [
            {'service_id':weekday,'exception_date':str(sunday),'action':'REMOVE'}])
        self.assertFalse(conflicting.calendar_active(weekday, sunday))

    def test_planning_determinism_and_operational_identity(self):
        from data_generator.ids import operational_departure_id
        again = build_trip_specs(GenerationContext(self.config), self.network, self.service, self.events)
        for left, right in zip(self.specs, again):
            self.assertEqual((left.operational_id, left.schedule['schedule_id'], left.current_trip_id),
                             (right.operational_id, right.schedule['schedule_id'], right.current_trip_id))
            self.assertEqual(left.operational_id, operational_departure_id(
                f'prod-{left.service_date.isoformat()}-{left.slot_index}', str(left.service_date),
                namespace=self.config.identity_namespace))

    def test_exact_allocation_and_deterministic_order_independence(self):
        before = {s.operational_id:s.boarding_budget for s in self.specs}
        self.assertEqual(sum(before.values()), 2400000)
        self.assertTrue(all(s.boarding_budget == 0 for s in self.specs if s.cancelled))
        self.assertTrue(all(s.boarding_budget > 0 for s in self.specs if not s.cancelled))
        allocate_movements(GenerationContext(self.config), list(reversed(self.specs)), self.network, self.service)
        self.assertEqual(before, {s.operational_id:s.boarding_budget for s in self.specs})

    def test_stop_allocation_and_demand_patterns(self):
        total = 0
        groups = {name:defaultdict(list) for name in ('month','direction','weekend','route','peak')}
        for spec in self.specs:
            if spec.cancelled:
                continue
            base, sequences = metadata_demand(self.ctx, spec, self.network, self.service)
            plan = boarding_plan(spec, base, sequences)
            self.assertEqual(sum(plan.values()), spec.boarding_budget)
            self.assertEqual(plan[sequences[-1]], 0)
            self.assertTrue(all(i in sequences and (n == 0 or i < sequences[-1]) for i,n in plan.items()))
            if spec.overcrowd or spec.low_demand or spec.replacement:
                self.assertEqual(spec.boarding_budget, sum(boarding_weights(spec, base, sequences)))
            total += sum(plan.values())
            for name,key in [('month',spec.service_date.month), ('direction',spec.direction_id),
                             ('weekend',spec.service_date.weekday() >= 5), ('route',spec.route_index),
                             ('peak',7*3600 <= spec.schedule['departure_offset_sec'] < 10*3600)]:
                groups[name][key].append(spec.boarding_budget)
        self.assertEqual(total, 2400000)
        for name,values in groups.items():
            means = [sum(v)/len(v) for v in values.values()]
            self.assertGreater(len(means), 1, name)
            self.assertGreater(max(means)-min(means), 0.1, name)
        self.assertGreater(len({s.boarding_budget for s in self.specs}), 20)

    def test_event_demand_hook_survives(self):
        spec = next(s for s in self.specs if not s.cancelled and not s.overcrowd)
        context = GenerationContext(self.config)
        row = {'scheduled_start_utc':'2025-01-01T02:00:00.000000Z','route_id':spec.pattern['route_id']}
        stops = self.network.route_stops_by_pattern[spec.pattern['pattern_id']]
        baseline = demand_base(context, spec, row, stops, record_scenarios=False)
        event_spec = replace(spec, event_id='focused-event')
        context.scenario_evidence['context_event_map'] = {'focused-event':{
            'starts_at_utc':'2025-01-01T00:00:00Z','ends_at_utc':'2025-01-02T00:00:00Z','scope':'CITY'}}
        increased = demand_base(context, event_spec, row, stops, record_scenarios=False)
        self.assertGreater(increased, baseline)

    def test_small_integrated_production_flows_and_fixtures(self):
        # Emit only 60 trip-local bundles in memory; no production files.
        chosen = sorted(self.specs[:60], key=lambda s:(s.service_date,s.schedule['departure_offset_sec'],s.index))
        context = GenerationContext(self.config)
        context.scenario_evidence['context_event_map'] = self.ctx.scenario_evidence['context_event_map']
        missing_request = missing_ticket = overloaded = transfers = 0
        for original in chosen:
            spec = replace(original)
            materialize_trip_rows(context, spec, self.network, self.service, self.vehicles)
            bundle = simulate_trip(context, spec, self.network, self.service, self.vehicles, [f'P{i}' for i in range(10000)])
            self.assertEqual(len(bundle.journeys), spec.boarding_budget)
            self.assertEqual(sum(r['boardings'] for r in bundle.counts), spec.boarding_budget)
            for count in bundle.counts:
                self.assertEqual(count['onboard_departure'], count['onboard_arrival'] - count['alightings'] + count['boardings'])
            valid_events = {r['stop_event_id'] for r in bundle.stop_events if r['visit_status']=='OBSERVED'}
            for journey in bundle.journeys:
                self.assertIn(journey['boarding_stop_event_id'], valid_events)
                self.assertIn(journey['alighting_stop_event_id'], valid_events)
                self.assertLess(journey['boarded_at_utc'], journey['alighted_at_utc'])
            missing_request += len(bundle.journeys)-len(bundle.requests)
            missing_ticket += len(bundle.journeys)-len(bundle.tickets)
            transfers += len(bundle.transfers)
            capacities = {a['assignment_id']:a['capacity_snapshot'] for a in bundle.actual_assignments}
            overloaded += sum(r['onboard_departure'] > capacities.get(r['departure_assignment_id'], 100000) for r in bundle.counts)
        self.assertEqual(missing_request, 1)
        self.assertEqual(missing_ticket, 1)
        self.assertGreater(transfers, 0)
        self.assertGreater(overloaded, 0)

    def test_unserved_budget_stream_and_request_reconciliation(self):
        context = GenerationContext(self.config)
        network = SimpleNamespace(stops=[{'stop_id':'S1','opened_on':'2025-01-01','closed_on':None},
                                         {'stop_id':'S2','opened_on':'2025-01-01','closed_on':None}],
                                  routes=[{'route_id':'R1'}])
        rows = iter_unserved_requests(context, network, ['P1','P2'], [date(2025,1,1)])
        self.assertIs(iter(rows), rows)
        self.assertEqual(sum(1 for _ in rows), 100000)
        self.assertEqual(2400000 - 1 + 100000, 2499999)

    def test_canonical_count_ignores_ticket_representations(self):
        journeys = [{'journey_id':'J1','ticket_id':'T1'},{'journey_id':'J2','ticket_id':None}]
        result = canonical_movement_count(iter(journeys), iter([{'ticket_id':'T1'}]*12001))
        self.assertEqual(result['canonical_movement_count'], 2)
        self.assertEqual(result['overlap_count'], 1)

    def test_duplicate_config_and_bounded_stream(self):
        self.assertEqual(duplicate_budget(self.config), {'total':12000,'controlled_dq04':1,'streamed':11999})
        audits = []
        injector = TicketDuplicateInjector(self.config, lambda row: audits.append(row) if len(audits)<2 else None)
        class Writer:
            total = 0
            def write(self, row):
                self.total += 1
                return f'copy-{self.total}'
        writer = Writer()
        for i in range(12010):
            injector.observe({'ticket_id':f'T{i}'}, f'original-{i}', writer)
        self.assertEqual(writer.total + 1, 12000)
        self.assertEqual(injector.finish()['streamed_emitted'], 11999)
        self.assertEqual(audits[0]['survivor_source_row_id'], 'original-0')
        self.assertNotEqual(audits[0]['source_row_id'], audits[0]['survivor_source_row_id'])
        self.assertEqual(audits[0]['canonical_movement_increment'], 0)
        self.assertEqual(duplicate_budget(replace(self.config, inject_quality_defects=False))['streamed'], 12000)

    def test_duplicate_writer_identity_rotation_and_determinism(self):
        # Small target exercises the actual CSV writer and physical provenance.
        def emit(root):
            config = replace(default_smoke(root, timestamp='2026-09-24T00:00:00Z'),
                             profile='production', target_scale={'raw_duplicate_ticket_copies':4}, chunk_rows=2)
            # Smoke manager avoids the production free-space guard in this unit fixture.
            manager = OutputManager(replace(config, profile='smoke'))
            writer = manager.table_writer('tickets')
            audit = []
            injector = TicketDuplicateInjector(config, audit.append)
            for i in range(5):
                row = {'ticket_id':f'T{i}','transaction_ref':f'TXN-{i}'}
                source = writer.write(row)
                injector.observe(row, source, writer)
            self.assertEqual(injector.finish()['streamed_emitted'], 3)
            manager.close()
            rows = []
            for path in sorted((root/'raw/tickets').glob('*.csv')):
                with path.open() as handle:
                    rows.extend(csv.DictReader(handle))
            by_source = {row['source_row_id']:row for row in rows}
            self.assertEqual(len(by_source), 8)
            for entry in audit:
                original, duplicate = by_source[entry['survivor_source_row_id']], by_source[entry['source_row_id']]
                self.assertEqual(original['ticket_id'], duplicate['ticket_id'])
                self.assertEqual(original['raw_record_text'], duplicate['raw_record_text'])
                self.assertEqual(original['raw_bytes_sha256'], duplicate['raw_bytes_sha256'])
            return rows,audit
        with tempfile.TemporaryDirectory(dir='sample_data', prefix='focused-duplicates-') as tmp:
            self.assertEqual(emit(Path(tmp)/'a'), emit(Path(tmp)/'b'))

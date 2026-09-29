"""Read-only production metadata simulation. Never writes production facts.

Run: python3 -m data_generator.preflight
Numbers here are pre-generation projections, not accepted production counts.
"""
import json
import hashlib
import shutil
from collections import Counter
from pathlib import Path

from .config import production, PRODUCTION_TARGETS
from .generators.common import GenerationContext
from .generators.network import build_network
from .generators.context import build_context_events
from .generators.service import build_service
from .generators.trips import build_trip_specs
from .generators.vehicles import build_vehicles
from .generators.vehicle_duties import reserve_trip, BUFFER_SECONDS
from .generators.movement_budget import allocate_movements, metadata_demand, boarding_plan
from .generators.ticket_duplicates import duplicate_budget
from .generators.common import utc_from_local


def run_preflight(config=None):
    config = config or production(allow_production=True, timestamp='2026-09-24T00:00:00Z')
    config.ensure_valid()
    ctx = GenerationContext(config)
    network = build_network(ctx)
    events = build_context_events(ctx, network)
    service = build_service(ctx, network, events)
    vehicles = build_vehicles(ctx)
    specs = build_trip_specs(ctx, network, service, events)
    movement_summary = allocate_movements(ctx, specs, network, service)
    specs.sort(key=lambda s: (s.service_date, int(s.schedule['departure_offset_sec']), s.slot_index, s.index))
    counts = Counter(routes=len(network.routes), stops=len(network.stops), vehicles=len(vehicles),
                     route_patterns=len(network.patterns), route_stops=len(network.route_stops),
                     service_calendars=len(service.calendars), service_exceptions=len(service.exceptions),
                     schedules=len(service.schedules), schedule_stop_times=len(service.stop_times),
                     context_events=len(events), operational_departures=len(specs))
    counts['passengers'] = config.target_scale['passengers']
    duplicates = duplicate_budget(config)
    counts['raw_duplicate_ticket_copies'] = duplicates['controlled_dq04'] + duplicates['streamed']
    checks = {}
    checks['history_and_splits'] = (str(config.history_start) == '2025-01-01' and str(config.history_end) == '2026-06-30'
        and config.split_boundaries() == {'train_start':'2025-01-01','train_end_exclusive':'2026-01-01',
        'validation_start':'2026-01-01','validation_end_exclusive':'2026-04-01',
        'test_start':'2026-04-01','test_end_exclusive':'2026-07-01'})
    months = {s.service_date.strftime('%Y-%m') for s in specs}
    checks['eighteen_service_months'] = len(months) == 18
    checks['fleet_target'] = len(vehicles) == 320
    invalid_calendar = 0
    delay_lower_bound = 0
    fleet_error = None
    allocation_hash = hashlib.sha256()
    calendar_states = {}
    for spec in specs:
        n = len(service.stop_times_by_schedule[spec.schedule['schedule_id']])
        counts['trip_plan_rows'] += 1 + spec.revision
        counts['planned_stop_events'] += n
        if spec.revision:
            counts['planned_stop_events'] += len(service.stop_times_by_schedule[(spec.old_schedule or spec.schedule)['schedule_id']])
        counts['trip_vehicle_assignments'] += 1 + spec.revision
        if not spec.cancelled:
            base, sequences = metadata_demand(ctx, spec, network, service)
            boardings = boarding_plan(spec, base, sequences)
            movements = sum(boardings.values())
            counts['gps_events'] += 3 + bool(spec.replacement or spec.delayed or spec.bunching or spec.event_id) + spec.unknown_vehicle
            if spec.replacement:
                counts['passenger_transfer_events'] += sum(amount for i, amount in boardings.items() if i < 5)
            if spec.delayed:
                delay_lower_bound += len(sequences)
            counts['passenger_journeys'] += movements
            counts['tickets'] += movements - (spec.index == 1)
            counts['demand_requests'] += movements - (spec.index == 0)
            counts['operated_departures'] += 1
            counts['trip_vehicle_assignments'] += 1 + spec.replacement
            counts['observed_stop_events'] += n - spec.incomplete - spec.skipped_stop
            counts['passenger_counts'] += n - spec.incomplete - spec.skipped_stop
        calendar_key = (spec.service_id, spec.service_date)
        if calendar_key not in calendar_states:
            calendar_states[calendar_key] = service.calendar_active(*calendar_key)
        invalid_calendar += not calendar_states[calendar_key]
        if fleet_error is None:
            try:
                vehicle = reserve_trip(ctx, spec, network, service, vehicles)
                allocation_hash.update(f'{spec.operational_id}|{vehicle}|{spec.allocated_replacement_id}\n'.encode())
            except RuntimeError as exc:
                fleet_error = str(exc)
    counts['demand_requests'] += 100000
    checks['vehicle_duty_feasibility'] = fleet_error is None
    checks['calendar_feasibility'] = invalid_calendar == 0
    comparisons = {key: {'projected': value, 'target': PRODUCTION_TARGETS.get(key, 320),
                         'difference': value - PRODUCTION_TARGETS.get(key, 320)} for key, value in sorted(counts.items())}
    # Approximate design counts are reported as differences, not exact failures.
    checks['operational_departure_target'] = counts['operational_departures'] == 120000
    checks['operated_departure_target'] = counts['operated_departures'] == 117600
    # Exact projections use the same allocation as the fact emitter.
    checks['movement_target'] = counts['passenger_journeys'] == 2400000
    checks['request_budget'] = counts['demand_requests'] == 2400000 - 1 + 100000
    checks['duplicate_ticket_budget'] = counts['raw_duplicate_ticket_copies'] == 12000
    smoke = Path('sample_data/smoke')
    manifest = json.loads((smoke/'metadata/generation_manifest.json').read_text())
    estimates = {}
    aliases = {'trips':'trip_plan_rows','service_calendar':'service_calendars'}
    for artifact in manifest['files']:
        if not artifact['path'].startswith('raw/') or not artifact['rows']:
            continue
        table = artifact['path'].split('/')[1]
        target = PRODUCTION_TARGETS.get(aliases.get(table, table), 320 if table == 'vehicles' else 0)
        if table == 'trip_stop_events':
            target = PRODUCTION_TARGETS['planned_stop_events']
        estimates[artifact['path']] = round(artifact['bytes'] / artifact['rows'] * target)
    raw_bytes = sum(estimates.values())
    free = shutil.disk_usage(Path.cwd()).free
    checks['raw_generation_disk_headroom'] = free >= 55 * 1024**3
    return {'kind':'PRE_GENERATION_ONLY', 'passed':all(checks.values()),
            'passed_count':sum(checks.values()), 'failed_count':sum(not v for v in checks.values()),
            'checks':checks, 'history':[str(config.history_start),str(config.history_end)],
            'splits':config.split_boundaries(), 'months':len(months), 'projections':comparisons,
            'unestimated_targets':{k:v for k,v in PRODUCTION_TARGETS.items() if k not in counts},
            'invalid_calendar_departures':invalid_calendar,
            'movement_allocation':movement_summary,
            'duplicate_allocation':duplicates,
            'representation_rules':{'missing_ticket':1,'missing_request':1,'quarantined_extra_journeys':1,
                'quarantined_extra_tickets':5,'expected_raw_journeys':2400001,'expected_raw_tickets':2412004,
                'request_rows':2499999,'canonical_movements':2400000},
            'delay_row_bounds_before_fixtures':{'lower':delay_lower_bound,'upper':counts['observed_stop_events'],'target':300000},
            'vehicle_allocator':{'buffer_seconds':BUFFER_SECONDS,'error':fleet_error,
                'peak_reserved_vehicles':ctx.vehicle_duties.peak_reservations,
                'assignment_sha256':allocation_hash.hexdigest()},
            'resources':{'raw_target_estimate_bytes':raw_bytes,'free_bytes':free,
                'planning_raw_upper_bytes':round(raw_bytes*1.5),
                'temporary_generation_bytes':round(raw_bytes*0.1),
                'raw_generation_disk_guard_bytes':55*1024**3,
                'validation_working_disk_bytes':round(raw_bytes*0.25),
                'two_intermediate_copies_bytes':raw_bytes*2,
                'safety_margin_bytes':10*1024**3,
                'planning_total_bytes':round(raw_bytes*3.85)+10*1024**3},
            'post_generation_required':['measured counts and unique canonical movements','raw checksums and DQ reconciliation',
                'independent accepted/quarantine cleaning','bounded production validation','full as-of ML leakage checks',
                'HDFS/Spark/Parquet evidence'],
            'limitations':['Raw bytes extrapolate smoke bytes/row at approved targets, not measured production.',
                'Approximate targets must not be interpreted as already passed production count gates.']}


def main():
    result = run_preflight()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

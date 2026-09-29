"""Bounded physical raw validation using a compact SQLite projection.

This is generator acceptance evidence, not an independent cleaning pipeline.
The explicit injection oracle only identifies intentional challenge rows.
Raw files are never modified. SQLite stores business columns, not raw envelopes.
"""
import argparse
import csv
import hashlib
import json
import logging
import shutil
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from .config import canonical_json
from .schemas import TABLE_COLUMNS, all_columns, column_names

LOG = logging.getLogger(__name__)
PK = {t: columns[0].name for t, columns in TABLE_COLUMNS.items()}
ALIASES = {'planned_vehicle_id': 'vehicles', 'arrival_assignment_id': 'trip_vehicle_assignments',
 'departure_assignment_id': 'trip_vehicle_assignments', 'from_assignment_id': 'trip_vehicle_assignments',
 'to_assignment_id': 'trip_vehicle_assignments', 'predecessor_trip_id': 'trips',
 'origin_route_stop_id': 'route_stops', 'destination_route_stop_id': 'route_stops',
 'origin_stop_id': 'stops', 'destination_stop_id': 'stops', 'preferred_route_id': 'routes',
 'boarding_stop_event_id': 'trip_stop_events', 'alighting_stop_event_id': 'trip_stop_events',
 'replacement_stop_event_id': 'trip_stop_events'}

VEHICLE_CAPACITY_LIFECYCLE_SQL = (
    "SELECT COUNT(*) FROM trip_vehicle_assignments a "
    "JOIN vehicles v USING(vehicle_id) JOIN trips t USING(trip_id) "
    "WHERE a.capacity_snapshot<=0 OR a.capacity_snapshot!=v.nominal_capacity "
    "OR CAST(a.start_stop_sequence AS INTEGER)>CAST(a.end_stop_sequence AS INTEGER) "
    "OR (a.assignment_kind='ACTUAL' AND "
    "(v.operational_status!='AVAILABLE' OR t.service_date<v.commissioned_on "
    "OR (v.retired_on IS NOT NULL AND t.service_date>=v.retired_on)))"
)

DELAY_EVENT_SCOPE_SQL = (
    "SELECT COUNT(*) FROM delays d JOIN context_events c USING(context_event_id) "
    "JOIN trip_stop_events e USING(stop_event_id) JOIN trips t ON t.trip_id=e.trip_id "
    "JOIN route_stops r ON r.route_stop_id=e.route_stop_id "
    "WHERE d.recorded_at_utc<c.starts_at_utc OR d.recorded_at_utc>c.ends_at_utc "
    "OR (c.scope='ROUTE' AND c.route_id!=t.route_id) "
    "OR (c.scope='STOP' AND c.stop_id!=r.stop_id)"
)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load(root, db, report):
    manifest = json.loads((root/'metadata/generation_manifest.json').read_text())
    injections = json.loads((root/'metadata/private_injection_manifest.json').read_text())['injections']
    exclusions = {r['source_row_id'] for r in injections if r['expected_disposition'] != 'ACCEPTED_FLAGGED'}
    duplicate_path = root/'metadata/production_ticket_duplicates.csv'
    duplicates = list(csv.DictReader(duplicate_path.open(newline=''))) if duplicate_path.exists() else []
    duplicate_sources = {r['source_row_id'] for r in duplicates}
    duplicate_survivors = {r['survivor_source_row_id'] for r in duplicates}
    duplicate_hashes = {}
    duplicate_identity_sources = duplicate_sources | duplicate_survivors
    exclusions.update(r['source_row_id'] for r in duplicates)
    expected_sources = {r['source_row_id'] for r in injections} | {r['source_row_id'] for r in duplicates}
    found_sources = set()
    fixtures = {}
    report['raw_counts'], report['clean_counts'], report['files'] = {}, {}, {}
    report['duplicates_ledger_count'] = len(duplicates)
    report['checks'] = []
    for table, columns in TABLE_COLUMNS.items():
        LOG.info('Loading %s', table)
        names = [c.name for c in columns] + ['source_row_id', 'quality_status']
        declarations = all_columns(table, raw=False)
        definitions = ','.join('"%s" %s' % (c.name, 'INTEGER' if c.type == 'INT' else 'REAL' if c.type.startswith('DEC') else 'TEXT') for c in columns)
        db.execute(f'CREATE TABLE {table} ({definitions}, source_row_id TEXT, quality_status TEXT)')
        sql = f'INSERT INTO {table} VALUES ({",".join("?" for _ in names)})'
        raw_count = clean_count = 0
        bad_types = bad_headers = bad_envelopes = 0
        for path in sorted((root/'raw'/table).glob('*.csv')):
            batch = []
            rows = 0
            with path.open(newline='') as f:
                reader = csv.DictReader(f)
                bad_headers += reader.fieldnames != column_names(table)
                for row in reader:
                    rows += 1
                    sid = row['source_row_id']
                    if sid in expected_sources:
                        found_sources.add(sid)
                        if sid not in duplicate_sources:
                            fixtures[sid] = row
                    if table == 'tickets' and sid in duplicate_identity_sources:
                        duplicate_hashes[sid] = (row['ticket_id'], row['raw_bytes_sha256'])
                    bad_envelopes += (row['raw_file'] != str(path.relative_to(root)) or int(row['row_ordinal']) != rows or hashlib.sha256(row['raw_record_text'].encode()).hexdigest() != row['raw_bytes_sha256'])
                    if sid in exclusions:
                        continue
                    for c in declarations:
                        v = row[c.name]
                        try:
                            if v == r'\N':
                                if not c.nullable:
                                    bad_types += 1
                            elif c.type == 'TS':
                                datetime.fromisoformat(v.replace('Z', '+00:00'))
                            elif c.type == 'DATE':
                                date.fromisoformat(v)
                            elif c.type == 'INT':
                                int(v)
                            elif c.type.startswith('DEC'):
                                float(v)
                            elif c.type == 'BOOL' and v not in ('true', 'false'):
                                bad_types += 1
                        except ValueError:
                            bad_types += 1
                    batch.append(tuple(None if row[n] == r'\N' else row[n] for n in names))
                    clean_count += 1
                    if len(batch) == 2000:
                        db.executemany(sql, batch)
                        batch.clear()
                db.executemany(sql, batch)
            raw_count += rows
            report['files'][str(path.relative_to(root))] = {'rows': rows, 'bytes': path.stat().st_size, 'sha256': digest(path)}
            db.commit()
        report['raw_counts'][table] = raw_count
        report['clean_counts'][table] = clean_count
        for label, bad in [('schema', bad_types), ('header', bad_headers), ('envelope', bad_envelopes)]:
            report['checks'].append({'name': f'{label}:{table}', 'passed': bad == 0, 'bad': bad})
        db.execute(f'CREATE INDEX pk_{table} ON {table} ({PK[table]})')
        db.commit()
    report['checks'].append({'name':'physical_duplicate_ledger', 'passed':all(duplicate_hashes.get(d['source_row_id']) == duplicate_hashes.get(d['survivor_source_row_id']) == (d['ticket_id'], duplicate_hashes.get(d['source_row_id'], (None,None))[1]) for d in duplicates)})
    report['fixture_rows'] = fixtures
    report['fixture_sources_complete'] = found_sources == expected_sources
    report['manifest'] = manifest
    report['injections'] = injections
    report['duplicate_ledger'] = duplicates
    return report


def validate(db, root, report):
    checks = report['checks']
    def check(name, passed, **evidence):
        checks.append(dict(name=name, passed=bool(passed), **evidence))
        LOG.info('%s %s', 'PASS' if passed else 'FAIL', name)
    def scalar(sql):
        return db.execute(sql).fetchone()[0]
    def zero(name, sql):
        bad = scalar(sql)
        check(name, bad == 0, bad=bad)
    def evidence(name, sql):
        cur = db.execute(sql)
        report.setdefault('evidence', {})[name] = [dict(zip([d[0] for d in cur.description], r)) for r in cur]
        return report['evidence'][name]
    counts = report['clean_counts']
    for t in TABLE_COLUMNS:
        zero(f'pk_unique:{t}', f'SELECT COUNT(*) FROM (SELECT {PK[t]} FROM {t} GROUP BY {PK[t]} HAVING COUNT(*) != 1 OR {PK[t]} IS NULL)')
    targets = {v: k for k, v in PK.items()} | ALIASES
    for t, columns in TABLE_COLUMNS.items():
        for c in columns:
            parent = targets.get(c.name)
            if parent and c.name != PK[t]:
                zero(f'fk:{t}.{c.name}', f'SELECT COUNT(*) FROM {t} a WHERE a.{c.name} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {parent} b WHERE b.{PK[parent]}=a.{c.name})')
    for t, minimum in [('passenger_journeys',2000000), ('passenger_counts',500000), ('routes',100), ('stops',500), ('vehicles',250), ('passengers',50000), ('delays',250000)]:
        check(f'srs_minimum:{t}', counts[t] >= minimum, actual=counts[t], minimum=minimum)
    check('multiple_calendars_schedules', counts['service_calendar'] > 1 and counts['schedules'] > 1)
    check('exact_canonical_movements', counts['passenger_journeys'] == 2400000, actual=counts['passenger_journeys'])
    check('exact_demand_requests', counts['demand_requests'] == 2499999, actual=counts['demand_requests'])
    evidence('request_resolutions', 'SELECT resolution, COUNT(*) rows FROM demand_requests GROUP BY resolution')
    zero('served_request_budget', "SELECT ABS(COUNT(*)-2399999) FROM demand_requests WHERE resolution='SERVED'")
    zero('missing_optional_channels', 'SELECT ABS(SUM(ticket_id IS NULL)-1)+ABS(SUM(request_id IS NULL)-1) FROM passenger_journeys')
    zero('ticket_journey_overlap', 'SELECT ABS(COUNT(*)-2399999) FROM passenger_journeys j JOIN tickets t USING(ticket_id)')
    zero('journeys_on_current_operated_trips', "SELECT COUNT(*) FROM passenger_journeys j JOIN trips t USING(trip_id) WHERE t.plan_status!='CURRENT' OR t.trip_status='CANCELLED'")
    zero('operational_departures', 'SELECT ABS(COUNT(DISTINCT operational_departure_id)-120000) FROM trips')
    zero('operated_departures', "SELECT ABS(COUNT(*)-117600) FROM trips WHERE plan_status='CURRENT' AND trip_status!='CANCELLED'")
    zero('one_current_plan', "SELECT COUNT(*) FROM (SELECT operational_departure_id FROM trips GROUP BY operational_departure_id HAVING SUM(plan_status='CURRENT')!=1 OR MIN(plan_version)!=1 OR MAX(plan_version)!=COUNT(*))")
    zero('plan_intervals', 'SELECT COUNT(*) FROM trips t JOIN trips p ON t.predecessor_trip_id=p.trip_id WHERE p.effective_to!=t.effective_from OR p.operational_departure_id!=t.operational_departure_id')
    zero('trip_schedule_pattern', 'SELECT COUNT(*) FROM trips t JOIN schedules s USING(schedule_id) JOIN route_patterns p ON p.pattern_id=t.pattern_id WHERE s.pattern_id!=t.pattern_id OR s.service_id!=t.service_id OR p.route_id!=t.route_id')
    zero('current_plan_dates', "SELECT COUNT(*) FROM trips t JOIN schedules s USING(schedule_id) JOIN route_patterns p ON p.pattern_id=t.pattern_id JOIN routes r ON r.route_id=t.route_id WHERE t.plan_status='CURRENT' AND (t.service_date<s.valid_from OR t.service_date>=s.valid_to OR t.service_date<p.valid_from OR t.service_date>=p.valid_to OR t.service_date<r.opened_on)")
    zero('route_stop_sequence', 'SELECT COUNT(*) FROM (SELECT pattern_id FROM route_stops GROUP BY pattern_id HAVING MIN(stop_sequence)!=1 OR MAX(stop_sequence)!=COUNT(*) OR COUNT(DISTINCT stop_sequence)!=COUNT(*))')
    zero('route_stop_parents', 'SELECT COUNT(*) FROM route_stops r JOIN route_patterns p USING(pattern_id) WHERE r.route_id!=p.route_id')
    zero('route_stop_distance_order', 'SELECT COUNT(*) FROM (SELECT distance_from_start_km d, LAG(distance_from_start_km) OVER(PARTITION BY pattern_id ORDER BY stop_sequence) prev FROM route_stops) WHERE d<0 OR d<=prev')
    zero('schedule_stop_order', 'SELECT COUNT(*) FROM (SELECT schedule_id FROM schedule_stop_times GROUP BY schedule_id HAVING MIN(stop_sequence)!=1 OR MAX(stop_sequence)!=COUNT(*) OR COUNT(DISTINCT stop_sequence)!=COUNT(*))')
    zero('schedule_stop_offsets', 'SELECT COUNT(*) FROM schedule_stop_times s JOIN schedules c USING(schedule_id) JOIN route_stops r USING(route_stop_id) WHERE s.arrival_offset_sec>s.departure_offset_sec OR s.arrival_offset_sec<0 OR s.stop_sequence!=r.stop_sequence OR c.pattern_id!=r.pattern_id')
    calendars = {r['service_id']: r for r in map(dict, db.execute('SELECT * FROM service_calendar'))}
    exceptions = {}
    for r in db.execute('SELECT * FROM service_exceptions'):
        key=(r['service_id'],r['exception_date'])
        if exceptions.get(key)!='REMOVE': exceptions[key]=r['action']
    bad=0
    for t in db.execute('SELECT service_id, service_date FROM trips'):
        c=calendars[t['service_id']]; day=t['service_date']; action=exceptions.get((t['service_id'],day))
        active=c[['monday','tuesday','wednesday','thursday','friday','saturday','sunday'][datetime.fromisoformat(day).weekday()]]=='true'
        if action: active=action=='ADD'
        bad += not (active and c['valid_from']<=day<c['valid_to'])
    check('calendar_validity', bad==0, bad=bad)
    evidence('history', 'SELECT MIN(service_date) first, MAX(service_date) last, COUNT(DISTINCT substr(service_date,1,7)) months FROM trips')
    check('history_18_months', report['evidence']['history'][0]=={'first':'2025-01-01','last':'2026-06-30','months':18})
    # All large joins use indexed business keys. Additional indexes serve flow aggregation.
    for t, c in [('trip_stop_events','trip_id, stop_sequence'), ('passenger_counts','stop_event_id'), ('passenger_journeys','boarding_stop_event_id'), ('passenger_journeys','alighting_stop_event_id'), ('passenger_transfer_events','replacement_stop_event_id')]:
        name=t+'_'+c.replace(', ','_')
        db.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {t} ({c})')
    db.commit()
    zero('event_pattern_schedule_sequence', 'SELECT COUNT(*) FROM trip_stop_events e JOIN trips t USING(trip_id) JOIN route_stops r USING(route_stop_id) JOIN schedule_stop_times s USING(schedule_stop_time_id) WHERE r.pattern_id!=t.pattern_id OR e.stop_sequence!=r.stop_sequence OR s.route_stop_id!=e.route_stop_id OR s.schedule_id!=t.schedule_id')
    zero('event_timing', "SELECT COUNT(*) FROM trip_stop_events WHERE visit_status='OBSERVED' AND (actual_arrival_utc IS NULL OR actual_departure_utc IS NULL OR actual_departure_utc<actual_arrival_utc)")
    zero('event_chronology', "SELECT COUNT(*) FROM (SELECT actual_arrival_utc a, LAG(actual_departure_utc) OVER(PARTITION BY trip_id ORDER BY stop_sequence) p FROM trip_stop_events WHERE visit_status='OBSERVED') WHERE a<p")
    zero('cancellation_and_supersession', "SELECT COUNT(*) FROM trip_stop_events e JOIN trips t USING(trip_id) WHERE (t.trip_status='CANCELLED' OR t.plan_status!='CURRENT') AND (e.actual_arrival_utc IS NOT NULL OR e.actual_departure_utc IS NOT NULL)")
    zero('journey_od_timing', "SELECT COUNT(*) FROM passenger_journeys j JOIN trip_stop_events b ON b.stop_event_id=j.boarding_stop_event_id JOIN trip_stop_events a ON a.stop_event_id=j.alighting_stop_event_id WHERE b.trip_id!=j.trip_id OR a.trip_id!=j.trip_id OR b.route_stop_id!=j.origin_route_stop_id OR a.route_stop_id!=j.destination_route_stop_id OR b.stop_sequence>=a.stop_sequence OR j.boarded_at_utc!=b.actual_departure_utc OR j.alighted_at_utc!=a.actual_arrival_utc OR j.boarded_at_utc>=j.alighted_at_utc OR j.passenger_count!=1")
    zero('counts_conservation', 'SELECT COUNT(*) FROM passenger_counts WHERE MIN(boardings,alightings,onboard_arrival,onboard_departure)<0 OR alightings>onboard_arrival OR onboard_departure!=onboard_arrival-alightings+boardings OR transfer_in_count!=transfer_out_count')
    zero('counts_event_context', "SELECT COUNT(*) FROM passenger_counts c JOIN trip_stop_events e USING(stop_event_id) WHERE c.trip_id!=e.trip_id OR e.visit_status!='OBSERVED' OR c.arrival_assignment_id IS NOT e.arrival_assignment_id OR c.departure_assignment_id IS NOT e.departure_assignment_id")
    zero('counts_unique_event', 'SELECT COUNT(*) FROM (SELECT stop_event_id FROM passenger_counts GROUP BY stop_event_id HAVING COUNT(*)!=1)')
    zero('journey_boarding_counts', 'SELECT COUNT(*) FROM passenger_counts c WHERE c.boardings!=(SELECT COUNT(*) FROM passenger_journeys j WHERE j.boarding_stop_event_id=c.stop_event_id)')
    zero('journey_alighting_counts', 'SELECT COUNT(*) FROM passenger_counts c WHERE c.alightings!=(SELECT COUNT(*) FROM passenger_journeys j WHERE j.alighting_stop_event_id=c.stop_event_id)')
    zero('load_continuity', 'SELECT COUNT(*) FROM (SELECT c.onboard_arrival a, LAG(c.onboard_departure,1,0) OVER(PARTITION BY c.trip_id ORDER BY e.stop_sequence) p FROM passenger_counts c JOIN trip_stop_events e USING(stop_event_id)) WHERE a!=p')
    zero('terminal_load', 'SELECT COUNT(*) FROM (SELECT c.onboard_departure d, ROW_NUMBER() OVER(PARTITION BY c.trip_id ORDER BY e.stop_sequence DESC) n FROM passenger_counts c JOIN trip_stop_events e USING(stop_event_id)) WHERE n=1 AND d!=0')
    zero('vehicle_capacity_lifecycle', VEHICLE_CAPACITY_LIFECYCLE_SQL)
    zero('vehicle_duty_nonoverlap', "SELECT COUNT(*) FROM (SELECT effective_start_utc s, MAX(effective_end_utc) OVER(PARTITION BY vehicle_id ORDER BY effective_start_utc ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) p FROM trip_vehicle_assignments WHERE assignment_kind='ACTUAL') WHERE s<p")
    zero('passenger_nonoverlap', 'SELECT COUNT(*) FROM (SELECT boarded_at_utc s, MAX(alighted_at_utc) OVER(PARTITION BY passenger_id ORDER BY boarded_at_utc ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) p FROM passenger_journeys) WHERE s<p')
    for table in ['trip_stop_events','passenger_counts','delays']:
        for phase in ['arrival','departure']:
            zero(f'{table}_{phase}_assignment_status', f"SELECT COUNT(*) FROM {table} WHERE ({phase}_assignment_status='KNOWN')!=({phase}_assignment_id IS NOT NULL)")
    zero('replacement_phase_semantics', "SELECT COUNT(*) FROM trip_stop_events e JOIN trip_vehicle_assignments a ON a.assignment_id=e.arrival_assignment_id JOIN trip_vehicle_assignments d ON d.assignment_id=e.departure_assignment_id WHERE a.trip_id!=e.trip_id OR d.trip_id!=e.trip_id OR a.assignment_kind!='ACTUAL' OR d.assignment_kind!='ACTUAL' OR e.actual_arrival_utc<a.effective_start_utc OR e.actual_arrival_utc>a.effective_end_utc OR e.actual_departure_utc<d.effective_start_utc OR e.actual_departure_utc>d.effective_end_utc OR (a.assignment_id!=d.assignment_id AND (a.vehicle_id=d.vehicle_id OR a.effective_end_utc!=d.effective_start_utc))")
    zero('replacement_transfer_counts', 'SELECT COUNT(*) FROM passenger_counts c WHERE c.transfer_out_count!=(SELECT COUNT(*) FROM passenger_transfer_events x WHERE x.replacement_stop_event_id=c.stop_event_id)')
    zero('transfer_journey_context', 'SELECT COUNT(*) FROM passenger_transfer_events x JOIN passenger_journeys j USING(journey_id) JOIN trip_stop_events e ON e.stop_event_id=x.replacement_stop_event_id WHERE e.trip_id!=j.trip_id OR x.from_assignment_id IS NOT e.arrival_assignment_id OR x.to_assignment_id IS NOT e.departure_assignment_id OR x.transfer_at_utc<j.boarded_at_utc OR x.transfer_at_utc>j.alighted_at_utc')
    zero('delay_timestamp_derivation', "SELECT COUNT(*) FROM delays d JOIN trip_stop_events e USING(stop_event_id) JOIN trips t ON t.trip_id=e.trip_id JOIN schedule_stop_times s USING(schedule_stop_time_id) WHERE d.trip_id!=e.trip_id OR d.arrival_assignment_id IS NOT e.arrival_assignment_id OR d.departure_assignment_id IS NOT e.departure_assignment_id OR d.arrival_delay_sec!=MAX(0,CAST(strftime('%s',e.actual_arrival_utc) AS INTEGER)-CAST(strftime('%s',t.scheduled_start_utc) AS INTEGER)-s.arrival_offset_sec) OR d.departure_delay_sec!=MAX(0,CAST(strftime('%s',e.actual_departure_utc) AS INTEGER)-CAST(strftime('%s',t.scheduled_start_utc) AS INTEGER)-s.departure_offset_sec) OR MAX(d.arrival_delay_sec,d.departure_delay_sec)<=0")
    zero('gps_context', 'SELECT COUNT(*) FROM gps_events g JOIN trip_stop_events e USING(stop_event_id) WHERE g.trip_id!=e.trip_id OR g.latitude NOT BETWEEN -90 AND 90 OR g.longitude NOT BETWEEN -180 AND 180')
    zero('delay_event_scope', DELAY_EVENT_SCOPE_SQL)
    zero('ticket_journey_business_context', 'SELECT COUNT(*) FROM passenger_journeys j JOIN tickets t USING(ticket_id) WHERE j.passenger_id!=t.passenger_id OR j.trip_id!=t.trip_id OR j.origin_route_stop_id!=t.origin_route_stop_id OR j.destination_route_stop_id!=t.destination_route_stop_id OR j.service_date!=t.service_date OR t.issued_at_utc>j.boarded_at_utc')
    zero('request_journey_business_context', "SELECT COUNT(*) FROM passenger_journeys j JOIN demand_requests d USING(request_id) JOIN route_stops o ON o.route_stop_id=j.origin_route_stop_id JOIN route_stops a ON a.route_stop_id=j.destination_route_stop_id WHERE j.passenger_id!=d.passenger_id OR d.origin_stop_id!=o.stop_id OR d.destination_stop_id!=a.stop_id OR d.resolution!='SERVED' OR d.desired_departure_utc!=j.boarded_at_utc")
    zero('gps_assignment_context', "SELECT COUNT(*) FROM gps_events g LEFT JOIN trip_vehicle_assignments a USING(assignment_id) WHERE (g.assignment_status='KNOWN')!=(g.assignment_id IS NOT NULL) OR (g.assignment_id IS NOT NULL AND (a.trip_id!=g.trip_id OR a.assignment_kind!='ACTUAL' OR g.observed_at_utc<a.effective_start_utc OR g.observed_at_utc>a.effective_end_utc))")
    zero('overcrowding_flagging', "SELECT COUNT(*) FROM passenger_counts c JOIN trip_vehicle_assignments a ON a.assignment_id=c.arrival_assignment_id JOIN trip_vehicle_assignments d ON d.assignment_id=c.departure_assignment_id WHERE (c.onboard_arrival>a.capacity_snapshot OR c.onboard_departure>d.capacity_snapshot) AND c.quality_status!='FLAGGED'")
    evidence('occupancy', 'SELECT MAX(1.0*c.onboard_departure/a.capacity_snapshot) max_departure_occupancy, AVG(1.0*c.onboard_departure/a.capacity_snapshot) mean_departure_occupancy, SUM(c.onboard_departure>a.capacity_snapshot) overloaded FROM passenger_counts c JOIN trip_vehicle_assignments a ON a.assignment_id=c.departure_assignment_id')
    evidence('observed_service', 'SELECT visit_status, COUNT(*) rows FROM trip_stop_events GROUP BY visit_status')
    evidence('trip_statuses', 'SELECT plan_status,trip_status, COUNT(*) rows FROM trips GROUP BY plan_status,trip_status')
    evidence('used_stops_vehicles', "SELECT (SELECT COUNT(DISTINCT r.stop_id) FROM trip_stop_events e JOIN route_stops r USING(route_stop_id) WHERE e.visit_status='OBSERVED') stops, (SELECT COUNT(DISTINCT vehicle_id) FROM trip_vehicle_assignments WHERE assignment_kind='ACTUAL') vehicles")
    check('used_stops_vehicles_project_floors',report['evidence']['used_stops_vehicles'][0]['stops']>=600 and report['evidence']['used_stops_vehicles'][0]['vehicles']>=300)
    # Conditions are measured from physical records, never scenario flags.
    evidence('conditions', "SELECT (SELECT COUNT(*) FROM trips WHERE plan_status='CURRENT' AND trip_status='CANCELLED') cancellations, (SELECT COUNT(*) FROM trip_stop_events e JOIN trips t USING(trip_id) JOIN schedule_stop_times s USING(schedule_stop_time_id) WHERE strftime('%s',e.actual_arrival_utc)-strftime('%s',t.scheduled_start_utc)<s.arrival_offset_sec) early_arrivals, (SELECT COUNT(*) FROM delays) positive_delays, (SELECT COUNT(*) FROM passenger_counts WHERE replacement_event='true') replacements, (SELECT COUNT(*) FROM passenger_counts c JOIN trip_vehicle_assignments a ON a.assignment_id=c.departure_assignment_id WHERE c.onboard_departure>a.capacity_snapshot) overcrowded_observations, (SELECT COUNT(*) FROM trip_stop_events WHERE strftime('%s',actual_departure_utc)-strftime('%s',actual_arrival_utc)>=120) bottleneck_visits")
    for k,v in report['evidence']['conditions'][0].items(): check('condition:'+k, v>0, count=v)
    db.execute('CREATE TEMP TABLE trip_demand AS SELECT trip_id, COUNT(*) movements FROM passenger_journeys GROUP BY trip_id')
    db.execute('CREATE INDEX demand_trip ON trip_demand(trip_id)')
    evidence('used_entities', 'SELECT COUNT(DISTINCT j.passenger_id) passengers, COUNT(DISTINCT t.route_id) routes, COUNT(DISTINCT t.schedule_id) schedules FROM passenger_journeys j JOIN trips t USING(trip_id)')
    check('used_passengers_project_floor',report['evidence']['used_entities'][0]['passengers']>=75000)
    for label,expr in [('weekday',"CASE WHEN strftime('%w',t.service_date) IN ('0','6') THEN 'weekend' ELSE 'weekday' END"), ('peak',"CASE WHEN CAST(strftime('%H',t.scheduled_start_utc,'+5 hours') AS INT) BETWEEN 7 AND 9 OR CAST(strftime('%H',t.scheduled_start_utc,'+5 hours') AS INT) BETWEEN 16 AND 19 THEN 'peak' ELSE 'off_peak' END"), ('season',"substr(t.service_date,1,7)"), ('direction','p.direction_id')]:
        data=evidence(label, f'SELECT {expr} category, COUNT(*) trips, SUM(d.movements) movements, AVG(d.movements) mean_movements FROM trip_demand d JOIN trips t USING(trip_id) JOIN route_patterns p USING(pattern_id) GROUP BY category')
        check('condition:'+label, len(data)>1 and len({round(r['mean_movements'],4) for r in data})>1)
    evidence('low_and_spike_demand', 'SELECT MIN(movements) minimum, MAX(movements) maximum, SUM(movements<=5) low_demand_trips, SUM(movements>=50) high_demand_trips FROM trip_demand')
    check('condition:low_demand_and_spikes', report['evidence']['low_and_spike_demand'][0]['low_demand_trips']>0 and report['evidence']['low_and_spike_demand'][0]['high_demand_trips']>0)
    evidence('event_demand', "SELECT CASE WHEN EXISTS (SELECT 1 FROM context_events c JOIN route_stops r ON r.pattern_id=t.pattern_id AND r.stop_sequence=1 WHERE t.scheduled_start_utc BETWEEN c.starts_at_utc AND c.ends_at_utc AND (c.scope='NETWORK' OR c.scope='ROUTE' AND c.route_id=t.route_id OR c.scope='STOP' AND c.stop_id=r.stop_id)) THEN 'event' ELSE 'baseline' END category, COUNT(*) trips, AVG(d.movements) mean_movements FROM trip_demand d JOIN trips t USING(trip_id) GROUP BY category")
    check('condition:event_demand', len(report['evidence']['event_demand'])==2)
    event_means={r['category']:r['mean_movements'] for r in report['evidence']['event_demand']}
    check('condition:event_demand_increase',event_means.get('event',0)>event_means.get('baseline',0))
    peak_means={r['category']:r['mean_movements'] for r in report['evidence']['peak']}
    check('condition:peak_demand_increase',peak_means.get('peak',0)>peak_means.get('off_peak',float('inf')))
    evidence('headways', "WITH h AS (SELECT strftime('%s',e.actual_departure_utc)-LAG(strftime('%s',e.actual_departure_utc)) OVER w gap, ABS(strftime('%s',t.scheduled_start_utc)-LAG(strftime('%s',t.scheduled_start_utc)) OVER w) planned FROM trip_stop_events e JOIN trips t USING(trip_id) JOIN route_patterns p USING(pattern_id) WHERE e.stop_sequence=1 AND e.visit_status='OBSERVED' WINDOW w AS (PARTITION BY t.service_date,t.route_id,p.direction_id ORDER BY e.actual_departure_utc)) SELECT SUM((planned>0 AND gap<0.25*planned) OR (planned=0 AND gap<300)) bunching_pairs, SUM(gap<=0 OR (planned>0 AND ABS(gap-planned)>MAX(300,0.5*planned))) irregular_pairs FROM h")
    check('condition:headways', all(v>0 for v in report['evidence']['headways'][0].values()))
    evidence('new_entities', "SELECT (SELECT COUNT(*) FROM routes WHERE opened_on>'2025-01-01') routes, (SELECT COUNT(*) FROM stops WHERE opened_on>'2025-01-01') stops, (SELECT COUNT(*) FROM schedules WHERE valid_from>'2025-01-01') schedules")
    check('condition:new_entities', all(v>0 for v in report['evidence']['new_entities'][0].values()))
    zero('stop_opening_consistency', 'SELECT COUNT(*) FROM trip_stop_events e JOIN route_stops r USING(route_stop_id) JOIN stops s USING(stop_id) WHERE e.service_date<s.opened_on')
    validate_dq(db, report, check)
    validate_artifacts(root, report, check)
    report['passed_count']=sum(c['passed'] for c in checks)
    report['failed_count']=sum(not c['passed'] for c in checks)
    report['passed']=report['failed_count']==0
    return report


def validate_dq(db, report, check):
    """Verify the claimed defect on each physical sparse fixture."""
    def missing(v): return v in (None, r'\N', '')
    def absent(table, key, value):
        return db.execute(f'SELECT 1 FROM {table} WHERE {key}=?', (value,)).fetchone() is None
    for injection in report['injections']:
        row=report['fixture_rows'].get(injection['source_row_id'])
        rule=injection['rule_id']
        ok=False
        if row:
            table=injection['table_name']
            if rule in ('DQ04','DQ05'):
                survivor=db.execute(f'SELECT * FROM {table} WHERE {PK[table]}=?', (row[PK[table]],)).fetchone()
                ok=bool(survivor) and all((None if row[c.name]==r'\N' else str(row[c.name])) == (None if survivor[c.name] is None else str(survivor[c.name])) for c in TABLE_COLUMNS[table] if not c.type.startswith('DEC'))
            elif rule=='DQ01': ok=missing(row['ticket_id']) and row['movement_status']=='MISSING_TICKET'
            elif rule=='DQ02': ok=missing(row['route_id'])
            elif rule=='DQ03': ok=absent('stops','stop_id',row['origin_stop_id'])
            elif rule=='DQ06': ok=int(row['boardings'])<0
            elif rule=='DQ07':
                try: datetime.fromisoformat(row['issued_at_utc'].replace('Z','+00:00'))
                except ValueError: ok=True
            elif rule=='DQ08': ok=row['actual_arrival_utc']<'2025-01-01'
            elif rule=='DQ09': ok=row['actual_departure_utc']<row['actual_arrival_utc']
            elif rule=='DQ10':
                assignment = db.execute(
                    'SELECT capacity_snapshot FROM trip_vehicle_assignments WHERE assignment_id=?',
                    (row['departure_assignment_id'],),
                ).fetchone()
                ok = bool(assignment) and int(row['onboard_departure']) > int(assignment['capacity_snapshot'])
            elif rule=='DQ11': ok=min(int(row['arrival_delay_sec']),int(row['departure_delay_sec']))<0
            elif rule=='DQ12': ok=missing(row['vehicle_id'])
            elif rule=='DQ13': ok=db.execute('SELECT 1 FROM route_stops WHERE pattern_id=? AND stop_sequence=?', (row['pattern_id'],int(row['stop_sequence']))).fetchone() is not None
            elif rule=='DQ14': ok=float(row['distance_km'])<=0
            elif rule=='DQ15': ok=absent('passengers','passenger_id',row['passenger_id'])
            elif rule in ('DQ16','G5_CORE_TRIP'): ok=absent('trips','trip_id',row['trip_id'])
            elif rule=='C01': ok=missing(row[injection['field_path']])
        check('physical_fixture:'+rule,ok,source_row_id=injection['source_row_id'])


def validate_artifacts(root, report, check):
    m=report['manifest']
    for file in m['files']:
        p=root/file['path']
        # Raw hashes are the expensive physical-load evidence reused here.
        # Metadata may have been repaired since that load; never trust its cache.
        measured=report['files'].get(file['path']) if file['path'].startswith('raw/') else None
        if measured is None:
            rows=1
            if p.suffix=='.csv':
                with p.open(newline='') as f: rows=sum(1 for _ in csv.reader(f))-1
            elif p.suffix=='.jsonl':
                with p.open() as f: rows=sum(1 for line in f if json.loads(line) is not None)
            measured={'rows':rows,'bytes':p.stat().st_size,'sha256':digest(p)}
            report['files'][file['path']]=measured
        check('artifact:'+file['path'], all(measured[k]==file[k] for k in ('rows','bytes','sha256')), measured=measured)
    check('manifest_table_counts',m['actual_row_counts']==report['raw_counts'])
    body=dict(m); hash_value=body.pop('manifest_sha256')
    check('manifest_self_hash',hashlib.sha256(canonical_json(body).encode()).hexdigest()==hash_value)
    deterministic={k:v for k,v in body.items() if k not in {'generation_duration_seconds','runtime_fields_excluded_from_deterministic_hash','deterministic_content_sha256'}}
    check('manifest_deterministic_hash', hashlib.sha256(canonical_json(deterministic).encode()).hexdigest()==body['deterministic_content_sha256'])
    check('fixture_sources_complete',report['fixture_sources_complete'])
    # Physically present appended copies are excluded only by exact source identity.
    dq=json.loads((root/'metadata/dq_reconciliation.json').read_text())['tables']
    for t, values in dq.items():
        total=sum(v for k,v in values.items() if k.endswith('_count') and k not in ('raw_count','affected_unique_count'))
        expected_clean=values['accepted_unchanged_count']+values['accepted_corrected_count']+values['accepted_flagged_count']
        check('dq_reconciliation:'+t,total==report['raw_counts'][t]==values['raw_count'] and expected_clean==report['clean_counts'][t], expected=values, measured_clean=report['clean_counts'][t])
    check('exact_duplicate_ticket_copies',report['duplicates_ledger_count']+sum(i['rule_id']=='DQ04' for i in report['injections'])==12000)
    check('all_dq_families',set(f'DQ{i:02}' for i in range(1,17))<=set(i['rule_id'] for i in report['injections']))
    report['resource_snapshot']={'free_bytes':shutil.disk_usage(root).free,'dataset_bytes':sum(p.stat().st_size for p in root.rglob('*') if p.is_file()),'file_count':sum(p.is_file() for p in root.rglob('*'))}
    report['limitations']=['Oracle-assisted raw acceptance validation; independent Spark/Python cleaning and model leakage validation remain future work.', 'Canonical movements use Passenger_Journeys once; Tickets and JSONL mirrors are not additional movements.']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--reuse',action='store_true')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    cache=args.report.with_suffix('.load.json')
    if args.database.exists() and not args.reuse: raise FileExistsError(args.database)
    # Reuse must never create or rebuild a missing projection, nor modify it.
    db=sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro', uri=True) if args.reuse else sqlite3.connect(args.database)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=OFF'); db.execute('PRAGMA synchronous=OFF')
    db.execute('PRAGMA cache_size=-131072'); db.execute('PRAGMA temp_store=FILE')
    report=json.loads(cache.read_text()) if args.reuse else load(args.root,db,{})
    if not args.reuse: cache.write_text(json.dumps(report))
    report=validate(db,args.root,report)
    # Keep detailed fixtures in load evidence, not in the public summary.
    for key in ('fixture_rows','duplicate_ledger','injections','manifest'): report.pop(key,None)
    args.report.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:report[k] for k in ('passed','passed_count','failed_count','raw_counts')}))
    db.close()
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())

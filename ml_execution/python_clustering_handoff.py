"""Independent temporal route profiles from certified CSV, staged on disk.

One route/direction/period is loaded at a time for the existing Pandas headway
transformation. No Spark feature, label, prediction or fitted object is read.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import sqlite3
from pathlib import Path

from ml_execution.contracts import PERIODS, contract_digest, load_features
from ml_execution.python_delay_handoff import _iter_rows, _table_files, _iso
from ml_execution.python_occupancy_handoff import (
    SOURCE_TABLES, SPLIT_START_UTC, SPLIT_END_UTC, _fixture_exclusions,
    _unique, _timestamp, _split_for, _sha256, _source_inventory,
)
from runtime_orchestration.certification import load_certification_adapter, require_certified_package

FEATURES = ['mean_observed_load', 'mean_capacity_utilization', 'mean_delay_sec',
            'mean_route_distance_km', 'mean_abs_headway_deviation_sec',
            'distinct_stops', 'mean_served_demand_per_trip']


def route_profile(frame, demand):
    """Same operational definitions as the shared Python feature implementation."""
    from python_pipeline.features import build_headway_features
    import pandas as pd
    work = build_headway_features(frame)
    delay = (pd.to_datetime(work.actual_departure_utc, utc=True) -
             pd.to_datetime(work.scheduled_departure_utc, utc=True)).dt.total_seconds().clip(lower=0)
    values = dict(zip(FEATURES, [
        work.onboard_departure.mean(),
        (work.onboard_departure / work.capacity_snapshot).mean(),
        delay.mean(), work.distance_km.mean(),
        work.headway_deviation_sec.abs().mean(), work.stop_id.nunique(), demand,
    ]))
    return {k:float(v) for k,v in values.items()}


def export_profiles(dataset_root, marker, output):
    import pandas as pd
    root, output = Path(dataset_root).resolve(), Path(output).resolve()
    if output.exists() or 'raw_data' in output.parts:
        raise ValueError('Requires a new output outside raw_data')
    adapter = load_certification_adapter('runtime_orchestration.production_v11_certification:ProductionV11Adapter')
    attestation, marker_hash = require_certified_package(root, Path(marker), adapter)
    print(json.dumps({'status':'SOURCE_CERTIFICATION_VERIFIED','dataset':attestation.dataset_version}),flush=True)
    tables = {t:_table_files(root/'raw',t) for t in (*SOURCE_TABLES,'passenger_journeys')}
    fixture = root/'metadata/private_injection_manifest.json'
    excluded = _fixture_exclusions(fixture)
    generation, inventory = _source_inventory(root,tables,fixture)
    trips = _unique(tables['trips'],'trips','trip_id',
        ('operational_departure_id','route_id','pattern_id','schedule_id','service_date','scheduled_start_utc','published_at_utc'),
        excluded['trips'],current_trips=True)
    patterns = _unique(tables['route_patterns'],'route_patterns','pattern_id',
        ('route_id','direction_id','distance_km','published_at_utc'),excluded['route_patterns'])
    stops = _unique(tables['route_stops'],'route_stops','route_stop_id',('stop_id','value_available_at'),excluded['route_stops'])
    times = _unique(tables['schedule_stop_times'],'schedule_stop_times','schedule_stop_time_id',
        ('schedule_id','route_stop_id','departure_offset_sec','value_available_at'),excluded['schedule_stop_times'])
    schedules = _unique(tables['schedules'],'schedules','schedule_id',('pattern_id','published_at_utc'),excluded['schedules'])
    routes = _unique(tables['routes'],'routes','route_id',('value_available_at',),excluded['routes'])
    assignments = _unique(tables['trip_vehicle_assignments'],'trip_vehicle_assignments','assignment_id',
        ('trip_id','assignment_kind','capacity_snapshot','effective_start_utc','effective_end_utc','announced_at_utc','value_available_at','correction_time'),excluded['trip_vehicle_assignments'])
    output.mkdir(parents=True,exist_ok=False)
    # Retained staging supports recovery and an audit of all included observations.
    db=sqlite3.connect(output/'source_staging.sqlite')
    db.execute('PRAGMA temp_store=FILE')
    db.execute('CREATE TABLE counts(event TEXT PRIMARY KEY, trip TEXT, assignment TEXT, load REAL, available TEXT)')
    batch=[]
    for row in _iter_rows(tables['passenger_counts']):
        if row.get('quality_status') not in ('VALID','ACCEPTED','FLAGGED') or row.get('source_row_id') in excluded['passenger_counts']: continue
        try: load=float(row['onboard_departure'])
        except (ValueError,TypeError,KeyError): continue
        if not math.isfinite(load) or load<0: continue
        try:
            available = _timestamp(row.get('value_available_at'))
            correction = _timestamp(row.get('correction_time'))
        except (ValueError, TypeError):
            continue
        if available is None:
            continue
        available = max(available, correction) if correction else available
        batch.append((row['stop_event_id'],row['trip_id'],row['departure_assignment_id'],load,_iso(available)))
        if len(batch)>=20000:
            db.executemany('INSERT INTO counts VALUES (?,?,?,?,?)',batch); db.commit(); batch.clear()
    db.executemany('INSERT INTO counts VALUES (?,?,?,?,?)',batch); db.commit()
    db.execute('CREATE TABLE events(split TEXT, route_id TEXT, direction_id REAL, stop_id TEXT, trip_id TEXT, stop_event_id TEXT PRIMARY KEY, service_date TEXT, scheduled_departure_utc TEXT, actual_departure_utc TEXT, onboard_departure REAL, capacity_snapshot REAL, distance_km REAL, operational_departure_id TEXT)')
    included=0; batch=[]
    availability_audit = {"checked": 0, "count_later_than_event_and_assignment": 0, "examples": []}
    for n,event in enumerate(_iter_rows(tables['trip_stop_events']),1):
        if n%500000==0: print(json.dumps({'events_scanned':n,'included':included}),flush=True)
        if event.get('quality_status')!='VALID' or event.get('source_row_id') in excluded['trip_stop_events'] or event.get('visit_status')!='OBSERVED': continue
        trip=trips.get(event.get('trip_id')); timing=times.get(event.get('schedule_stop_time_id')); stop=stops.get(event.get('route_stop_id'))
        if not trip or not timing or not stop: continue
        pattern=patterns.get(trip['pattern_id']); schedule=schedules.get(trip['schedule_id']); route=routes.get(trip['route_id'])
        if not pattern or not schedule or not route: continue
        if pattern['route_id']!=trip['route_id'] or schedule['pattern_id']!=trip['pattern_id'] or timing['schedule_id']!=trip['schedule_id'] or timing['route_stop_id']!=event['route_stop_id']: continue
        split=_split_for(event['service_date'])
        if split is None: continue
        count=db.execute('SELECT trip,assignment,load,available FROM counts WHERE event=?',(event['stop_event_id'],)).fetchone()
        if not count or count[0]!=event['trip_id']: continue
        assignment=assignments.get(count[1])
        if not assignment or assignment['trip_id']!=event['trip_id'] or assignment['assignment_kind']!='ACTUAL': continue
        try:
            actual=_timestamp(event.get('actual_departure_utc'))
            scheduled=_timestamp(trip['scheduled_start_utc'])+dt.timedelta(seconds=float(timing['departure_offset_sec']))
            cap=float(assignment['capacity_snapshot']); distance=float(pattern['distance_km']); direction=float(pattern['direction_id'])
            start=_timestamp(assignment['effective_start_utc']); end=_timestamp(assignment['effective_end_utc'])
            available=[_timestamp(event.get('value_available_at')), _timestamp(assignment.get('value_available_at')),
                       _timestamp(assignment.get('announced_at_utc')), _timestamp(count[3])]
            optional=[_timestamp(event.get('outcome_available_at_utc')), _timestamp(assignment.get('correction_time')),
                      _timestamp(event.get('correction_time'))]
            known=[_timestamp(trip['published_at_utc']),_timestamp(pattern['published_at_utc']),_timestamp(schedule['published_at_utc']),
                   _timestamp(timing['value_available_at']),_timestamp(stop['value_available_at']),_timestamp(route['value_available_at'])]
            low,high=_timestamp(SPLIT_START_UTC[split]),_timestamp(SPLIT_END_UTC[split])
            if any(x is None for x in [actual,start,end,*available,*known]): continue
            if not all(math.isfinite(x) for x in (cap,distance,direction)) or cap<=0 or distance<0: continue
            if not start<=actual<end or not low<=scheduled<high or not low<=actual<high: continue
            if max(known)>scheduled or max(available+[x for x in optional if x is not None])>high: continue
            event_assignment_available = max(available[:3] + [x for x in optional if x is not None])
            availability_audit['checked'] += 1
            if available[3] > event_assignment_available:
                availability_audit['count_later_than_event_and_assignment'] += 1
                if len(availability_audit['examples']) < 10:
                    availability_audit['examples'].append({'event':event['stop_event_id'], 'count':_iso(available[3]), 'event_assignment':_iso(event_assignment_available)})
        except (ValueError,TypeError,KeyError,OverflowError): continue
        batch.append((split,trip['route_id'],direction,stop['stop_id'],event['trip_id'],event['stop_event_id'],event['service_date'],_iso(scheduled),_iso(actual),count[2],cap,distance,trip['operational_departure_id']))
        included+=1
        if len(batch)>=20000:
            db.executemany('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',batch); db.commit(); batch.clear()
    db.executemany('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',batch); db.commit()
    db.execute('CREATE INDEX event_profile ON events(split,route_id,direction_id)')
    journeys={}
    for row in _iter_rows(tables['passenger_journeys']):
        if row.get('quality_status')!='VALID' or row.get('source_row_id') in excluded['passenger_journeys']: continue
        try: count=float(row['passenger_count']); available=_timestamp(row.get('value_available_at'))
        except (ValueError,TypeError,KeyError): continue
        if not math.isfinite(count) or count<0 or available is None: continue
        current=journeys.setdefault(row['trip_id'],[0,available]); current[0]+=count; current[1]=max(current[1],available)
    demand={}
    for trip_id,(count,available) in journeys.items():
        trip=trips.get(trip_id)
        if not trip: continue
        pattern=patterns.get(trip['pattern_id']); schedule=schedules.get(trip['schedule_id']); split=_split_for(trip['service_date'])
        if not pattern or not schedule or split is None: continue
        if pattern['route_id']!=trip['route_id'] or schedule['pattern_id']!=trip['pattern_id']: continue
        try:
            scheduled=_timestamp(trip['scheduled_start_utc'])
            known=[_timestamp(trip['published_at_utc']),_timestamp(pattern['published_at_utc']),_timestamp(schedule['published_at_utc'])]
            if scheduled is None or any(x is None for x in known): continue
            if not _timestamp(SPLIT_START_UTC[split])<=scheduled<_timestamp(SPLIT_END_UTC[split]): continue
            if not scheduled<=available<=_timestamp(SPLIT_END_UTC[split]) or max(known)>scheduled: continue
            key=(split,trip['route_id'],float(pattern['direction_id']))
        except (TypeError,ValueError): continue
        agg=demand.setdefault(key,[0,0]); agg[0]+=count; agg[1]+=1
    partitions={}; excluded_profiles=[]
    for split in PERIODS:
        records=[]
        keys=db.execute('SELECT DISTINCT route_id,direction_id FROM events WHERE split=? ORDER BY route_id,direction_id',(split,)).fetchall()
        for route,direction in keys:
            supported=demand.get((split,route,direction))
            if not supported: excluded_profiles.append([split,route,direction,'no supported journey targets']); continue
            frame=pd.read_sql_query('SELECT * FROM events WHERE split=? AND route_id=? AND direction_id=?',db,params=(split,route,direction))
            values=route_profile(frame,supported[0]/supported[1])
            if not all(math.isfinite(v) for v in values.values()): excluded_profiles.append([split,route,direction,'nonfinite profile']); continue
            records.append({'case_id':f'{split}:{route}:{int(direction)}','route_id':route,'direction_id':direction,
                'service_date':PERIODS[split]['start'],'target_start':SPLIT_START_UTC[split],'target_end':SPLIT_END_UTC[split],
                'feature_cutoff_at':SPLIT_END_UTC[split],'value_available_at':SPLIT_END_UTC[split],'target_available_at':SPLIT_END_UTC[split],
                'source_departure_ids':sorted(frame.operational_departure_id.unique().tolist()),
                'observed_events':len(frame),'supported_demand_trips':supported[1],**values})
        p=output/f'{split}.jsonl'
        with p.open('x') as f:
            for row in records: f.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
        partitions[split]={'path':p.name,'sha256':_sha256(p),'sample_rows':len(records)}
    db.close()
    manifest={'schema_version':'1.0','task_name':'route_clustering','dataset_version':attestation.dataset_version,
        'feature_version':'python-raw-temporal-route-'+_sha256(Path(__file__)), 'producer':'python',
        'grain':'one route/direction profile per chronological period','target_definition':'Unsupervised temporal operational profile; train-fit, validation-select, test held out. No generated archetype labels.',
        'features':FEATURES,'partitions':partitions,'certification':{'status':'CERTIFIED','evidence_path':'feature_certification.json'},
        'source_provenance':{'owner_marker_sha256':marker_hash,'owner_attestation_reference':attestation.evidence_reference,
            'source_run_id':generation['run_id'],'raw_input_sha256':{x['path']:x['sha256'] for x in inventory},
            'implementation_sha256':_sha256(Path(__file__)),'python_features_sha256':_sha256(Path('python_pipeline/features.py')),
            'helper_sha256':{name:_sha256(Path(name)) for name in ['ml_execution/python_delay_handoff.py','ml_execution/python_occupancy_handoff.py','ml_execution/contracts.py']},
            'included_stop_events':included,'excluded_profiles':excluded_profiles,'count_availability_audit':availability_audit,
            'policy':'Non-injected valid observed events; real flagged overload counts retained; exact actual assignments. All outcomes available by period end; complete physical-stop headway timelines within each route/direction/period. All available served trip targets; no Spark input.',
            'limitations':['Synthetic dataset; GPS coordinates are not reconciled to stops (no raw stop foreign key).','Profiles exclude missing/invalid capacity or outcomes and nonfinite aggregates. Temporal changes may yield weak clustering.']}}
    cert={k:manifest[k] for k in ('dataset_version','task_name','feature_version','producer','partitions','source_provenance')}
    cert.update(status='CERTIFIED',contract_sha256=contract_digest(manifest))
    for name,value in [('manifest.json',manifest),('feature_certification.json',cert)]:
        with (output/name).open('x') as f: json.dump(value,f,indent=2,allow_nan=False)
    _,parts,_=load_features(output/'manifest.json')
    print(json.dumps({'status':'HANDOFF_VALIDATED','manifest':str(output/'manifest.json'),'profiles':{k:len(v) for k,v in parts.items()}}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-root',required=True,type=Path); parser.add_argument('--marker',required=True,type=Path); parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(); export_profiles(args.dataset_root,args.marker,args.output)

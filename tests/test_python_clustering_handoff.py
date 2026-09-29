import unittest
import pandas as pd
from ml_execution.python_clustering_handoff import route_profile

class RouteProfileTests(unittest.TestCase):
    def test_physical_stop_headway_and_overload_are_preserved(self):
        frame=pd.DataFrame([
            {'service_date':'2025-01-02','route_id':'R','direction_id':0,'stop_id':'S','trip_id':'A','stop_event_id':'1','actual_departure_utc':'2025-01-02T01:01:00Z','scheduled_departure_utc':'2025-01-02T01:00:00Z','onboard_departure':12,'capacity_snapshot':10,'distance_km':5},
            {'service_date':'2025-01-02','route_id':'R','direction_id':0,'stop_id':'S','trip_id':'B','stop_event_id':'2','actual_departure_utc':'2025-01-02T01:13:00Z','scheduled_departure_utc':'2025-01-02T01:10:00Z','onboard_departure':8,'capacity_snapshot':10,'distance_km':5},
            {'service_date':'2025-01-02','route_id':'R','direction_id':0,'stop_id':'OTHER','trip_id':'B','stop_event_id':'3','actual_departure_utc':'2025-01-02T01:15:00Z','scheduled_departure_utc':'2025-01-02T01:15:00Z','onboard_departure':10,'capacity_snapshot':10,'distance_km':5},
        ])
        result=route_profile(frame,17)
        self.assertEqual(result['mean_observed_load'],10)
        self.assertEqual(result['mean_capacity_utilization'],1)
        self.assertEqual(result['mean_delay_sec'],80)
        self.assertEqual(result['mean_abs_headway_deviation_sec'],120)
        self.assertEqual(result['distinct_stops'],2)
        self.assertEqual(result['mean_served_demand_per_trip'],17)

    def test_raw_temporal_profiles_exclude_outcomes_available_after_period(self):
        import csv
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from ml_execution.python_clustering_handoff import export_profiles
        from ml_execution.contracts import load_features
        tables={name:[] for name in ['trips','route_patterns','route_stops','schedule_stop_times','schedules','routes','trip_vehicle_assignments','trip_stop_events','passenger_counts','passenger_journeys']}
        def add(table,**row):
            row.update(quality_status='VALID',source_row_id=f'{table}-{len(tables[table])}')
            tables[table].append(row)
        known='2024-01-01T00:00:00Z'
        for route in range(5):
            r=f'R{route}'
            add('routes',route_id=r,value_available_at=known)
            add('route_patterns',pattern_id=r,route_id=r,direction_id=0,distance_km=5+route,published_at_utc=known)
            add('schedules',schedule_id=r,pattern_id=r,published_at_utc=known)
            add('route_stops',route_stop_id=r,stop_id=r,value_available_at=known)
            add('schedule_stop_times',schedule_stop_time_id=r,schedule_id=r,route_stop_id=r,departure_offset_sec=0,value_available_at=known)
            for split,date in [('train','2025-05-01'),('validation','2026-02-01'),('test','2026-05-01')]:
                for index in range(2):
                    trip=f'{split}-{r}-{index}'; scheduled=f'{date}T01:{index*10:02d}:00Z'; actual=f'{date}T01:{index*10+1:02d}:00Z'
                    add('trips',trip_id=trip,operational_departure_id=trip,route_id=r,pattern_id=r,schedule_id=r,service_date=date,scheduled_start_utc=scheduled,published_at_utc=known,plan_status='CURRENT')
                    add('trip_vehicle_assignments',assignment_id=trip,trip_id=trip,assignment_kind='ACTUAL',capacity_snapshot=10,effective_start_utc=f'{date}T00:00:00Z',effective_end_utc=f'{date}T23:00:00Z',announced_at_utc=known,value_available_at=known,correction_time='\\N')
                    add('trip_stop_events',stop_event_id=trip,trip_id=trip,schedule_stop_time_id=r,route_stop_id=r,service_date=date,actual_departure_utc=actual,visit_status='OBSERVED',value_available_at=actual,outcome_available_at_utc=actual,correction_time='\\N')
                    available='2026-07-02T00:00:00Z' if split=='test' and route==4 else actual
                    add('passenger_counts',stop_event_id=trip,trip_id=trip,departure_assignment_id=trip,onboard_departure=12,value_available_at=available,correction_time='\\N')
                    add('passenger_journeys',trip_id=trip,passenger_count=17,value_available_at=actual)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'dataset'; output=Path(temp)/'profiles'
            for table,rows in tables.items():
                directory=root/'raw'/table; directory.mkdir(parents=True)
                with (directory/'part-000.csv').open('w') as f:
                    writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
            (root/'metadata').mkdir(); (root/'metadata/private_injection_manifest.json').write_text(json.dumps({'injections':[]}))
            att=SimpleNamespace(dataset_version='production-v1.1',evidence_reference='fixture-only')
            with patch('ml_execution.python_clustering_handoff.require_certified_package',return_value=(att,'fixture-only')), patch('ml_execution.python_clustering_handoff._source_inventory',return_value=({'run_id':'fixture'},[])):
                export_profiles(root,Path(temp)/'marker',output)
            _,parts,_=load_features(output/'manifest.json')
            self.assertEqual({k:len(v) for k,v in parts.items()},{'train':5,'validation':5,'test':4})
            self.assertTrue(all(row['mean_capacity_utilization']==1.2 for rows in parts.values() for row in rows))
            self.assertEqual(parts['train'][0]['mean_served_demand_per_trip'],17)

if __name__=='__main__': unittest.main()

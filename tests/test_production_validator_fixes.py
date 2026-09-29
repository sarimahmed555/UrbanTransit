"""Regression tests for the three proven production-validator defects."""

import hashlib
import json
import tempfile
from pathlib import Path

import sqlite3
import unittest

from data_generator.validate_production import (
    DELAY_EVENT_SCOPE_SQL,
    VEHICLE_CAPACITY_LIFECYCLE_SQL,
    validate_dq,
    validate_artifacts,
)


class ProductionValidatorFixTests(unittest.TestCase):
    def test_vehicle_sequences_are_numeric(self):
        db = sqlite3.connect(":memory:")
        db.executescript(
            """
            CREATE TABLE vehicles(vehicle_id TEXT, nominal_capacity INTEGER,
                                  operational_status TEXT, commissioned_on TEXT, retired_on TEXT);
            CREATE TABLE trips(trip_id TEXT, service_date TEXT);
            CREATE TABLE trip_vehicle_assignments(
                vehicle_id TEXT, trip_id TEXT, capacity_snapshot INTEGER,
                start_stop_sequence TEXT, end_stop_sequence TEXT, assignment_kind TEXT
            );
            INSERT INTO vehicles VALUES ('v', 40, 'AVAILABLE', '2025-01-01', NULL);
            INSERT INTO trips VALUES ('t', '2025-01-01');
            INSERT INTO trip_vehicle_assignments VALUES ('v','t',40,'2','10','ACTUAL');
            """
        )
        self.assertEqual(db.execute(VEHICLE_CAPACITY_LIFECYCLE_SQL).fetchone()[0], 0)
        db.execute("UPDATE trip_vehicle_assignments SET start_stop_sequence='10', end_stop_sequence='2'")
        self.assertEqual(db.execute(VEHICLE_CAPACITY_LIFECYCLE_SQL).fetchone()[0], 1)

    def test_delay_scope_uses_recorded_observation_for_early_arrival(self):
        db = sqlite3.connect(":memory:")
        db.executescript(
            """
            CREATE TABLE context_events(context_event_id TEXT, starts_at_utc TEXT,
                ends_at_utc TEXT, scope TEXT, route_id TEXT, stop_id TEXT);
            CREATE TABLE delays(delay_id TEXT, context_event_id TEXT, stop_event_id TEXT,
                recorded_at_utc TEXT);
            CREATE TABLE trip_stop_events(stop_event_id TEXT, trip_id TEXT,
                route_stop_id TEXT, actual_arrival_utc TEXT);
            CREATE TABLE trips(trip_id TEXT, route_id TEXT);
            CREATE TABLE route_stops(route_stop_id TEXT, stop_id TEXT);
            INSERT INTO context_events VALUES ('c','2025-05-13T02:00:00Z',
                '2025-05-13T05:00:00Z','ROUTE','r',NULL);
            INSERT INTO trips VALUES ('t','r');
            INSERT INTO route_stops VALUES ('rs','s');
            INSERT INTO trip_stop_events VALUES ('e','t','rs','2025-05-13T01:59:28Z');
            INSERT INTO delays VALUES ('d','c','e','2025-05-13T02:00:13Z');
            """
        )
        self.assertEqual(db.execute(DELAY_EVENT_SCOPE_SQL).fetchone()[0], 0)
        db.execute("UPDATE delays SET recorded_at_utc='2025-05-13T05:00:01Z'")
        self.assertEqual(db.execute(DELAY_EVENT_SCOPE_SQL).fetchone()[0], 1)

    def test_dq10_fixture_is_capacity_violation_not_conservation_failure(self):
        db = sqlite3.connect(":memory:")
        db.executescript(
            """
            CREATE TABLE passenger_counts(onboard_arrival INTEGER,
                onboard_departure INTEGER, alightings INTEGER, boardings INTEGER,
                departure_assignment_id TEXT);
            CREATE TABLE trip_vehicle_assignments(assignment_id TEXT, capacity_snapshot INTEGER);
            INSERT INTO trip_vehicle_assignments VALUES ('a',66);
            INSERT INTO passenger_counts VALUES (9999,10000,0,1,'a');
            """
        )
        db.row_factory = sqlite3.Row
        row = dict(db.execute("SELECT * FROM passenger_counts").fetchone())
        self.assertEqual(row['onboard_departure'],
                         row['onboard_arrival'] - row['alightings'] + row['boardings'])
        report = {'injections': [{'rule_id': 'DQ10', 'source_row_id': 'fixture',
                                  'table_name': 'passenger_counts'}],
                  'fixture_rows': {'fixture': row}}
        results = []
        validate_dq(db, report, lambda name, passed, **kw: results.append(passed))
        self.assertEqual(results, [True])
        row['onboard_departure'] = 66
        results.clear()
        validate_dq(db, report, lambda name, passed, **kw: results.append(passed))
        self.assertEqual(results, [False])

    def test_existing_dq01_reference_is_validated_without_mutating_row(self):
        row = {'ticket_id': None, 'movement_status': 'MISSING_TICKET'}
        report = {'injections': [{'rule_id': 'DQ01', 'source_row_id': 'existing',
                                  'table_name': 'passenger_journeys'}],
                  'fixture_rows': {'existing': row}}
        results = []
        validate_dq(sqlite3.connect(':memory:'), report,
                    lambda name, passed, **kw: results.append(passed))
        self.assertEqual(results, [True])
        self.assertEqual(row, {'ticket_id': None, 'movement_status': 'MISSING_TICKET'})


    def test_metadata_hash_is_remeasured_during_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'metadata').mkdir()
            (root / 'metadata/private_injection_manifest.json').write_text('{}')
            (root / 'metadata/dq_reconciliation.json').write_text('{"tables": {}}')
            expected = {'path': 'metadata/private_injection_manifest.json',
                        'rows': 1, 'bytes': 2,
                        'sha256': hashlib.sha256(b'{}').hexdigest()}
            manifest = {'files': [expected], 'actual_row_counts': {},
                        'manifest_sha256': '', 'deterministic_content_sha256': ''}
            report = {'manifest': manifest, 'files': {expected['path']: dict(expected)},
                      'raw_counts': {}, 'clean_counts': {}, 'fixture_sources_complete': True,
                      'duplicates_ledger_count': 12000, 'injections': []}
            (root / expected['path']).write_text('{"changed": true}')
            results = {}
            validate_artifacts(root, report,
                               lambda name, passed, **kw: results.update({name: passed}))
            self.assertFalse(results['artifact:' + expected['path']])


if __name__ == "__main__":
    unittest.main()

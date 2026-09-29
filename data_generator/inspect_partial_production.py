"""Read-only inventory and targeted integrity evidence for an incomplete run.

Usage: python3 -m data_generator.inspect_partial_production ROOT REPORT
This does not certify an incomplete dataset or repair generated files.
"""
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path


def inspect(root):
    counts, errors, inventory = {}, [], []
    for table in sorted((root / 'raw').iterdir()):
        count = 0
        for path in sorted(table.glob('*.csv')):
            rows = 0
            with path.open(newline='') as f:
                for row in csv.DictReader(f):
                    rows += 1
                    if None in row or any(v is None for v in row.values()):
                        errors.append(str(path))
            count += rows
            h = hashlib.sha256()
            with path.open('rb') as f:
                for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
                    h.update(block)
            inventory.append({'path': str(path.relative_to(root)), 'rows': rows,
                              'bytes': path.stat().st_size, 'sha256': h.hexdigest()})
        counts[table.name] = count

    def rows(table):
        for path in sorted((root/'raw'/table).glob('*.csv')):
            with path.open(newline='') as f:
                yield from csv.DictReader(f)

    assignments = {r['assignment_id']: r for r in rows('trip_vehicle_assignments')}
    bad, examples = 0, []
    for gps in rows('gps_events'):
        a = assignments.get(gps['assignment_id'])
        if a and not a['effective_start_utc'] <= gps['observed_at_utc'] <= a['effective_end_utc']:
            bad += 1
            if len(examples) < 3:
                examples.append({k: gps[k] for k in ['gps_event_id', 'trip_id', 'assignment_id', 'observed_at_utc', 'stop_event_id']} |
                                {k: a[k] for k in ['start_stop_sequence', 'effective_start_utc', 'effective_end_utc']})
    ticket_ids = Counter(r['ticket_id'] for r in rows('tickets'))
    trips = list(rows('trips'))
    files = [p for p in root.rglob('*') if p.is_file()]
    return {'status': 'INTERRUPTED_FOR_CONFIRMED_INTEGRITY_DEFECT', 'raw_counts': counts,
            'csv_errors': errors[:10], 'gps_assignment_time_violations': bad, 'examples': examples,
            'checks': [{'name': 'complete_csv_record_shapes', 'passed': not errors},
                       {'name': 'gps_assignment_time_integrity', 'passed': bad == 0}],
            'passed_count': int(not errors) + int(bad == 0),
            'failed_count': int(bool(errors)) + int(bad != 0),
            'partial_operational_departures': len({r['operational_departure_id'] for r in trips}),
            'partial_operated_current_trips': sum(r['plan_status'] == 'CURRENT' and r['trip_status'] != 'CANCELLED' for r in trips),
            'partial_duplicate_ticket_copies': sum(n-1 for n in ticket_ids.values()),
            'partial_service_dates': [min(r['service_date'] for r in trips), max(r['service_date'] for r in trips)],
            'dataset_bytes': sum(p.stat().st_size for p in files), 'file_count': len(files),
            'remaining_disk_bytes': shutil.disk_usage(root).free,
            'manifest_exists': (root/'metadata/generation_manifest.json').exists(),
            'physical_csv_inventory': inventory,
            'limitations': ['Partial raw inventory only; no complete production acceptance validation or DQ reconciliation is possible.',
                            'SQLite projection validator requires a completed generation manifest.',
                            'No resume/checkpoint support in the generator; restart requires fresh output or explicit overwrite of marked output.']}


if __name__ == '__main__':
    result = inspect(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('physical_csv_inventory', 'examples')}, indent=2))

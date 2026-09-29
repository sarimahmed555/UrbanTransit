"""Bounded, read-only views of owner-bound production-v1.1 metadata.

This is an inventory of the generated project dataset, not operational analytics.
No raw fact scan, generator scenario count, Spark execution or inferred geometry.
"""
import csv
import hashlib
import io
import json
import math
from pathlib import Path

from runtime_orchestration.production_v11_certification import (
    APPROVAL_STATEMENT, DATASET_VERSION, EVIDENCE_FILES, EXPECTED_RUN_ID,
    MARKER_RELATIVE_PATH,
)

CATALOG_FIELDS = {
    'routes': ('route_id', 'route_code', 'route_name', 'mode', 'service_type', 'route_status', 'opened_on', 'closed_on'),
    'stops': ('stop_id', 'stop_code', 'stop_name', 'latitude', 'longitude', 'opened_on', 'closed_on'),
    'vehicles': ('vehicle_id', 'vehicle_type', 'seated_capacity', 'standing_capacity', 'nominal_capacity', 'operational_status'),
    'route_stops': ('route_stop_id', 'pattern_id', 'route_id', 'stop_id', 'stop_sequence'),
}


class EvidenceCatalog:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _read(self, relative, *, limit=12_000_000, expected=None):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or not path.is_file() or path.stat().st_size > limit:
            raise RuntimeError('Bounded inventory artifact unavailable')
        raw = path.read_bytes()
        if expected and hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError('Inventory evidence hash mismatch')
        return raw

    def _bound(self):
        owner = json.loads(self._read(MARKER_RELATIVE_PATH, limit=100_000))
        if (owner.get('status') != 'CERTIFIED' or owner.get('dataset_version') != DATASET_VERSION
                or owner.get('run_id') != EXPECTED_RUN_ID or owner.get('approval_statement') != APPROVAL_STATEMENT):
            raise RuntimeError('Owner-bound inventory unavailable')
        documents = {}
        for key in ('generation_manifest', 'verification_report'):
            expected = owner.get('evidence_sha256', {}).get(key)
            if not isinstance(expected, str) or len(expected) != 64:
                raise RuntimeError('Owner evidence hash missing')
            documents[key] = json.loads(self._read(EVIDENCE_FILES[key], expected=expected))
        manifest, verification = documents['generation_manifest'], documents['verification_report']
        if manifest.get('dataset_version') != DATASET_VERSION or manifest.get('run_id') != EXPECTED_RUN_ID:
            raise RuntimeError('Inventory dataset mismatch')
        if verification.get('failed') != [] or not all(c.get('passed') is True for c in verification['checks']):
            raise RuntimeError('Bounded verification did not pass')
        return owner, manifest, verification

    def _envelope(self, data, owner):
        return {'status': 'ready', 'data': data, 'meta': {
            'source': 'owner_bound_dataset_inventory', 'dataset_version': DATASET_VERSION,
            'freshness': 'frozen', 'generated_at': owner['approved_at_utc'],
            'provenance': str(MARKER_RELATIVE_PATH),
            'source_hashes': {k: owner['evidence_sha256'][k] for k in ('generation_manifest', 'verification_report')},
        }}

    def summary(self):
        owner, manifest, verification = self._bound()
        # Explicitly exclude generator scenario_counts/condition_coverage from analytics.
        return self._envelope({
            'dataset_version': DATASET_VERSION,
            'history_start': manifest['history_start'], 'history_end': manifest['history_end'],
            'timezone': manifest['timezone'],
            'inventory': [{'table': name, 'raw_rows': count} for name, count in sorted(manifest['actual_row_counts'].items())],
            'verification_checks': verification['checks'],
            'limitations': [
                'Project-generated synthetic dataset; counts describe raw inventory, not live riders or cleaned analytical totals.',
                'Bounded verification is the existing owner-approved scope, not a new certification or a full SRS completion claim.',
                'Generator scenario counts are not measured detection results and are intentionally excluded.',
                'GPS coordinates have not been reconciled to stops; route paths and hotspots are unavailable.',
            ],
        }, owner)

    def catalog(self, kind, *, query='', route_id='', limit=100, offset=0):
        if kind not in CATALOG_FIELDS:
            raise KeyError('Unknown catalog')
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError('Invalid catalog pagination')
        if not isinstance(query, str) or len(query) > 128 or not isinstance(route_id, str) or len(route_id) > 128:
            raise ValueError('Invalid catalog filter')
        if route_id and kind != 'route_stops':
            raise ValueError('Route filter applies only to route stop membership')
        owner, manifest, _ = self._bound()
        entries = [e for e in manifest['files'] if e['table'] == kind and e['format'] == 'csv']
        if not entries:
            raise RuntimeError('Catalog has no manifest-bound CSV')
        rows, excluded = [], 0
        for entry in entries:
            raw = self._read(Path('raw_data') / DATASET_VERSION / entry['path'], expected=entry['sha256'])
            source_rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8'))))
            if len(source_rows) != entry['rows']:
                raise RuntimeError('Catalog row count mismatch')
            for row in source_rows:
                if row.get('dataset_version') not in (DATASET_VERSION, manifest.get('parent_dataset_version')):
                    raise RuntimeError('Catalog row dataset mismatch')
                if row.get('quality_status') not in ('VALID', 'ACCEPTED', 'CLEAN') or row.get('parse_status') not in ('PARSED', 'UNPARSED_RAW'):
                    excluded += 1
                    continue
                public = {key: row.get(key) or None for key in CATALOG_FIELDS[kind]}
                if kind == 'stops':
                    try:
                        lat, lng = float(public['latitude']), float(public['longitude'])
                        if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
                            raise ValueError
                    except (TypeError, ValueError):
                        excluded += 1
                        continue
                    public.update(latitude=lat, longitude=lng)
                if route_id and public.get('route_id') != route_id:
                    continue
                if query.casefold() not in ' '.join(str(v or '') for v in public.values()).casefold():
                    continue
                rows.append(public)
        return self._envelope({'kind': kind, 'rows': rows[offset:offset+limit], 'total': len(rows),
            'offset': offset, 'limit': limit, 'excluded_rows': excluded,
            'applied_filters': {'query': query, 'route_id': route_id},
            'source_hashes': {e['path']: e['sha256'] for e in entries},
            'scope': 'Static catalog of valid project dataset records; no performance or live service claim.'}, owner)

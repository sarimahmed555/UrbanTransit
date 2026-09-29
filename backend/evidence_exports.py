"""Read-only, provenance-preserving exports from the existing evidence repository."""
import csv
import io
import json
from .evidence import COMPARISON_PATH, MAX_PREDICTION_ARTIFACT_BYTES, TASKS, _sha256
from runtime_orchestration.compare import _read_test_predictions, _timestamp


def comparison_cases(repository):
    frozen = repository.comparison()
    policy = frozen['data']['comparison_policy']
    sources = {}
    for engine in ('python', 'spark'):
        _, audit, run = repository._verified_run('delay_severity', engine)
        path = repository._path(run['artifact_paths']['predictions'])
        expected = audit['artifact_sha256']['predictions'].get(path.relative_to(repository.root).as_posix())
        if not expected or path.stat().st_size > MAX_PREDICTION_ARTIFACT_BYTES or _sha256(path) != expected:
            raise RuntimeError('Comparison prediction hash mismatch')
        sources[engine] = _read_test_predictions(path, expected_engine=engine,
            dataset_version='production-v1.1', task_name='delay_severity',
            feature_version=run['feature_version'], comparison_policy=policy)
    rows = []
    for case in sorted(sources['python'].keys() & sources['spark'].keys()):
        python, spark = sources['python'][case], sources['spark'][case]
        if any(python.get(k) != spark.get(k) for k in ('dataset_version','task_name','service_date','actual')):
            raise RuntimeError('Comparison truth or cohort mismatch')
        for key in ('target_start','target_end'):
            if _timestamp(python.get(key)) != _timestamp(spark.get(key), correction_hours=5):
                raise RuntimeError('Comparison time alignment mismatch')
        same = python['prediction'] == spark['prediction']
        # The stored Spark identity includes its original trip ID; no raw join needed.
        raw_id = spark['case_id']; separator = raw_id.index(':'); length = int(raw_id[:separator])
        rows.append({'case_id': case, 'trip_id': raw_id[separator+1:separator+1+length],
            'service_date': python['service_date'], 'actual': python['actual'],
            'python_prediction': python['prediction'], 'spark_prediction': spark['prediction'],
            'truth_match': True, 'prediction_match': same,
            'delta_spark_minus_python': spark['prediction']-python['prediction'],
            'explanation': 'Predictions agree.' if same else 'Different independently selected models predicted different classes; causal attribution is not established.'})
    summary = frozen['data']
    if len(rows) != summary['shared_test_cases'] or sum(r['prediction_match'] for r in rows) != summary['prediction_agreements']:
        raise RuntimeError('Comparison rows differ from frozen summary')
    if {r['case_id'] for r in rows if not r['prediction_match']} != {r['case_id'] for r in summary['mismatches']}:
        raise RuntimeError('Comparison disagreement identities differ')
    return {'status':'ready', 'data':{'summary':summary, 'rows':rows}, 'meta':frozen['meta']}


def report(repository, name):
    if name == 'delay-comparison':
        return comparison_cases(repository)
    if name in TASKS:
        result = repository.task(name, prediction_limit=50)
        rows = []
        for engine, pipeline in result['data']['pipelines'].items():
            for sample in pipeline['prediction_sample']:
                rows.append({'task_name': name, 'pipeline':engine, 'model':pipeline['selected_model'], **sample})
        return {**result, 'data':{**result['data'], 'rows':rows,
            'export_scope':'First 50 persisted held-out cases per pipeline; metrics describe the full original held-out cohort.'}}
    raise KeyError('Evidence report unavailable')


def serialize_report(payload, format):
    if format == 'json':
        return json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2), 'application/json'
    if format != 'csv':
        raise ValueError('Unsupported report format')
    rows = payload['data']['rows']
    # Repeat essential provenance for a CSV that can be interpreted independently.
    fields = list(rows[0]) if rows else []
    provenance = {'dataset_version':'production-v1.1', 'source':payload['meta']['source'],
        'freshness':'frozen', 'scope':payload['data'].get('export_scope','All shared unseen delay cases; original frozen agreement unchanged'),
        'provenance_json':json.dumps(payload['meta'],sort_keys=True)}
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields+list(provenance)); writer.writeheader()
    def safe(value):
        # Neutralize spreadsheet formulas in strings, without changing numeric values.
        return "'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value
    for row in rows:
        writer.writerow({k:safe(v) for k,v in {**row,**provenance}.items()})
    return output.getvalue(), 'text/csv'

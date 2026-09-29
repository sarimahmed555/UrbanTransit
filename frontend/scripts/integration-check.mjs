import { strict as assert } from 'node:assert';
import { createApiClient } from '../src/api/client.js';
import { renderPage } from '../src/pages/index.js';

const calls = [];
const client = createApiClient({
  baseUrl: 'http://127.0.0.1:8000',
  fetcher: async (url) => {
    const parsed = new URL(url);
    calls.push(parsed.pathname + parsed.search);
    return {
      ok: true,
      json: async () => ({
        status: 'ready',
        data: { task_name: 'passenger_demand', pipelines: {} },
        meta: { source: 'audited_result_artifacts' },
      }),
    };
  },
});
assert.equal(client.contracts.evidenceTask.state, 'connected');
const registrationCalls = [];
const registrationClient = createApiClient({
  baseUrl: 'http://127.0.0.1:8000',
  fetcher: async (url, options) => {
    registrationCalls.push({ path: new URL(url).pathname, body: JSON.parse(options.body) });
    return { ok: true, status: 201, json: async () => ({ status: 'created', username: 'new.user@example.com' }) };
  },
});
const registration = await registrationClient.register('new.user@example.com', 'correct-horse-battery-staple');
assert.equal(registration.state, 'ready');
assert.deepEqual(registrationCalls, [{
  path: '/api/v1/auth/register',
  body: { username: 'new.user@example.com', password: 'correct-horse-battery-staple' },
}]);
const response = await client.evidenceTask('passenger_demand');
assert.equal(response.state, 'ready');
assert.equal(response.data.task_name, 'passenger_demand');
assert.deepEqual(calls, ['/api/v1/evidence/tasks/passenger_demand']);
await client.evidenceTask('delay_severity', { predictionLimit: 5 });
assert.equal(calls[1], '/api/v1/evidence/tasks/delay_severity?prediction_limit=5');

const failedClient = createApiClient({
  baseUrl: 'http://127.0.0.1:8000',
  fetcher: async () => { throw new Error('backend unavailable'); },
});
const failure = await failedClient.evidenceTask('passenger_demand');
assert.equal(failure.state, 'error');
assert.equal(failure.data, null, 'API failure must not be replaced with demo data');

const demandMarkup = renderPage('forecasting', 'api', {
  __productionEvidence: {
    state: 'ready',
    meta: { source: 'audited_result_artifacts' },
    data: {
      kind: 'task',
      task: {
        task_name: 'passenger_demand',
        limitations: ['Test evidence is at scheduled-trip grain.'],
        pipelines: {
          python: {
            selected_model: 'random_forest',
            model_type: 'regression',
            test_metrics: { mae: 10.8938, sample_count: 16767 },
            sample_counts: { test: 16767 },
            baseline_vs_selected: { metric: 'mae', baseline: 16.5179, selected: 10.8938 },
            acceptance: { passed: true },
            prediction_sample: [{
              case_id: 'TR-real-test-case',
              target_start: '2026-04-01T12:00:00Z',
              actual: 19,
              prediction: 20.25,
            }],
          },
        },
      },
    },
  },
});
assert.match(demandMarkup, /10\.8938/);
assert.match(demandMarkup, /Persisted held-out prediction sample/);
assert.match(demandMarkup, /20\.25/);
assert.match(demandMarkup, /scheduled-trip grain/);
assert.doesNotMatch(demandMarkup, /128\.6k|18,420|87\.4%/);

const comparisonMarkup = renderPage('model-comparison', 'api', {
  productionEvidence: {
    state: 'ready',
    meta: { source: 'verified_pipeline_comparison' },
    data: {
      kind: 'comparison',
      tasks: [],
      comparison: {
        shared_test_cases: 2429,
        truths_matched: 2429,
        prediction_agreements: 2422,
        prediction_disagreements: 7,
        agreement_rate: 0.9971181556195965,
        mismatches: [{
          case_id: 'hashed-case',
          service_date: '2026-04-30',
          actual: 2,
          python_prediction: 0,
          spark_prediction: 2,
          truth_match: true,
        }],
      },
    },
  },
});
assert.match(comparisonMarkup, /99\.7118% agreement/);
assert.match(comparisonMarkup, /2,429 shared held-out cases/);
assert.match(comparisonMarkup, /Actual truth/);
assert.match(comparisonMarkup, /Truth alignment/);

function taskEvidence(taskName) {
  const classification = ['delay_severity', 'crowding_risk'].includes(taskName);
  return {
    task_name: taskName,
    pipelines: {
      python: {
        selected_model: 'decision_tree',
        model_type: classification ? 'classification' : 'regression',
        test_metrics: classification
          ? { accuracy: 0.99, macro_f1: 0.5, sample_count: 100 }
          : { mae: 0.0685, sample_count: 100 },
        sample_counts: { test: 100 },
      },
    },
  };
}

for (const [page, tasks] of [
  ['delay-prediction', [taskEvidence('delay_severity')]],
  ['occupancy-forecast', [taskEvidence('occupancy_forecast'), taskEvidence('crowding_risk')]],
]) {
  const markup = renderPage(page, 'api', {
    productionEvidence: {
      state: 'ready',
      meta: { source: 'audited_result_artifacts' },
      data: { kind: 'tasks', tasks },
    },
  });
  assert.match(markup, /Historical evaluation evidence available/);
  assert.doesNotMatch(markup, /Awaiting processed data/);
  for (const task of tasks) assert.ok(markup.includes(task.task_name), `${page} did not show ${task.task_name}`);
}

console.log('Frontend evidence integration checks passed.');

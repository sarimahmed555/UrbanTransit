import { button, panel, notice } from '../components/ui.js';
import { pageFrame } from './shared.js';
import { resultPanel } from '../components/result-state.js';

/** Fields match backend/app.py::_validate_what_if and SCENARIOS. No execution or estimates. */
export function renderWhatIf() {
  return pageFrame({
    data: { eyebrow: 'Scenario planning', title: 'Explore a service change.', description: 'Compare an estimated service change with a certified baseline.' },
    mode: 'api', updated: 'No baseline selected',
    children: `${notice({ title: 'Awaiting a certified baseline', description: 'Scenario execution becomes available after baseline evidence and the scenario service are connected.' })}
      <div class="dashboard-grid dashboard-grid--split">
      ${panel({ title: 'Scenario setup', description: 'Select a baseline before changing service parameters.', body: `<form class="scenario-form" aria-describedby="scenario-status"><fieldset disabled><legend>Baseline evidence</legend><label class="scenario-field">Source<input name="source_id" placeholder="Awaiting source" /></label><label class="scenario-field">Evidence reference<input name="pointer" placeholder="Awaiting baseline reference" /></label><label class="scenario-field">Route<input name="route_id" placeholder="Awaiting route catalog" /></label><label class="scenario-field">Direction<input name="direction_id" placeholder="Awaiting baseline direction" /></label></fieldset><fieldset disabled><legend>Proposed change</legend><label class="scenario-field">Scenario type<select name="scenario_type"><option value="increase_frequency">Increase frequency</option><option value="decrease_frequency">Decrease frequency</option><option value="add_vehicle">Add vehicle</option><option value="change_vehicle_capacity">Change vehicle capacity</option><option value="shift_trip_start_time">Shift trip start time</option><option value="remove_low_demand_trip">Remove low-demand trip</option><option value="add_new_stop">Add new stop</option><option value="increase_predicted_demand">Increase predicted demand</option></select></label><label class="scenario-field">Proposed trip count<input type="number" name="trip_count" min="1" step="1" placeholder="Select baseline first" /></label></fieldset><p id="scenario-status" class="muted">No estimate has been generated. Controls unlock when a baseline is available.</p>${button({ label: 'Run estimate unavailable', disabled: true, variant: 'primary' })}</form>` })}
      ${panel({ title: 'Reading scenario results', description: 'Estimates require context.', body: `<ul class="scenario-guidance"><li>Use one route and direction with a common service window.</li><li>Baseline and scenario metrics are estimates, with assumptions supplied by the service.</li><li>Review served and unserved demand together.</li><li>Check capacity constraints and supporting evidence before making a service decision.</li></ul>` })}
      </div>
      ${resultPanel({ title: 'Baseline versus scenario', description: 'Estimated outcomes, changes and supporting evidence', columns: [{ label: 'Measure', key: 'measure' }, { label: 'Baseline', key: 'baseline' }, { label: 'Scenario', key: 'scenario' }, { label: 'Change', key: 'change' }] })}`,
  });
}

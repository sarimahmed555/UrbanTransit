/**
 * Synthetic UI fixtures for visual development only.
 *
 * IMPORTANT: every value in this module is deliberately labelled demo/mock.
 * These values are not production measurements, dataset statistics, model
 * outputs, or evidence of pipeline completion. Replace this module with API
 * view models when the analytics service is available.
 */

export const DEMO_META = Object.freeze({
  mode: 'demo',
  label: 'DEMO / MOCK',
  shortLabel: 'DEMO',
  source: 'Synthetic UI fixture',
  disclaimer: 'Preview values are illustrative only and are not production results.',
});

export const DEMO_FILTER_OPTIONS = Object.freeze({
  dateRanges: ['Last 7 days', 'Last 30 days', 'Quarter to date', 'Full history'],
  serviceDays: ['All service days', 'Weekdays', 'Weekends', 'Holidays'],
  routes: ['All routes', 'R-101 · Central Loop', 'R-107 · Riverside', 'R-214 · Airport Link'],
  stops: ['All stops', 'Central Hub', 'Riverside Market', 'Airport Terminal'],
  granularities: ['Hour', 'Day', 'Week'],
});

export const demoData = Object.freeze({
  meta: DEMO_META,
  executive: {
    eyebrow: 'Network pulse',
    title: 'See the network at a glance.',
    description:
      'A decision-ready view of demand, service health, and the next best question to investigate.',
    lastUpdated: 'Demo refresh · 09:42 local',
    kpis: [
      { label: 'Ridership today', value: '18,420', unit: 'rides', delta: '+6.8%', direction: 'up', icon: 'users', tone: 'teal', note: 'vs. previous service day' },
      { label: 'On-time performance', value: '87.4%', unit: 'of stop visits', delta: '+1.2 pts', direction: 'up', icon: 'check', tone: 'blue', note: 'demo punctuality view' },
      { label: 'Average load factor', value: '64%', unit: 'capacity', delta: '+4.1 pts', direction: 'up', icon: 'gauge', tone: 'amber', note: 'weighted route view' },
      { label: 'Open insights', value: '12', unit: 'prioritized', delta: '4 high', direction: 'attention', icon: 'spark', tone: 'coral', note: 'evidence queue' },
    ],
    trend: {
      labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
      values: [68, 74, 79, 83, 91, 62, 57],
      comparison: [61, 65, 68, 73, 76, 58, 52],
      label: 'Daily boardings',
      unit: 'index',
    },
    health: [
      { label: 'Demand coverage', value: 'Good', detail: '5 routes need a closer look', status: 'watch', icon: 'pulse' },
      { label: 'Service reliability', value: 'Stable', detail: '2 corridors above watch threshold', status: 'good', icon: 'check' },
      { label: 'Crowding exposure', value: 'Elevated', detail: 'Morning peak on 3 corridors', status: 'attention', icon: 'layers' },
      { label: 'Data readiness', value: 'Awaiting API', detail: 'Preview fixtures are isolated', status: 'pending', icon: 'database' },
    ],
    priority: [
      { rank: '01', title: 'Protect peak capacity on the central corridor', detail: 'Review recurring load and frequency together before changing service.', tag: 'Capacity', tone: 'coral' },
      { rank: '02', title: 'Investigate a persistent delay pocket', detail: 'Compare stop-level travel time with the current schedule baseline.', tag: 'Reliability', tone: 'amber' },
      { rank: '03', title: 'Test a shoulder-period frequency adjustment', detail: 'Run a what-if estimate before committing an operational change.', tag: 'Scenario', tone: 'teal' },
    ],
  },
  demand: {
    kpis: [
      { label: 'Total boardings', value: '128.6k', unit: 'demo period', delta: '+4.8%', direction: 'up', icon: 'users', tone: 'teal' },
      { label: 'Peak window', value: '08:00–09:00', unit: 'demo insight', delta: 'Highest', direction: 'attention', icon: 'clock', tone: 'amber' },
      { label: 'Avg. riders / trip', value: '21.8', unit: 'demo trips', delta: '+1.3', direction: 'up', icon: 'trend', tone: 'blue' },
      { label: 'Unresolved requests', value: '—', unit: 'API required', delta: 'Not supplied', direction: 'pending', icon: 'help', tone: 'slate' },
    ],
    trend: {
      labels: ['00', '03', '06', '09', '12', '15', '18', '21'],
      values: [18, 12, 41, 96, 66, 58, 83, 47],
      comparison: [15, 11, 36, 81, 60, 52, 74, 43],
      label: 'Ridership by time of day',
      unit: 'demo index',
    },
    peakBars: [
      { label: 'Morning peak', value: 96, detail: '08:00–09:00' },
      { label: 'Midday', value: 66, detail: '12:00–13:00' },
      { label: 'Evening peak', value: 83, detail: '18:00–19:00' },
      { label: 'Off-peak', value: 41, detail: '03:00–04:00' },
    ],
    routeDemand: [
      { rank: 1, name: 'Central Loop', code: 'R-101', boardings: '32,480', trend: '+8.4%', status: 'High demand', tone: 'coral' },
      { rank: 2, name: 'Riverside Connector', code: 'R-107', boardings: '27,910', trend: '+4.1%', status: 'High demand', tone: 'amber' },
      { rank: 3, name: 'Airport Link', code: 'R-214', boardings: '21,360', trend: '+2.7%', status: 'Growing', tone: 'teal' },
      { rank: 4, name: 'Civic District', code: 'R-118', boardings: '18,940', trend: '-1.3%', status: 'Steady', tone: 'blue' },
      { rank: 5, name: 'University Loop', code: 'R-126', boardings: '16,220', trend: '+6.2%', status: 'Growing', tone: 'teal' },
    ],
    stopDemand: [
      { rank: 1, name: 'Central Hub', code: 'S-014', boardings: '8,420', alightings: '7,980', peak: '08:15' },
      { rank: 2, name: 'Riverside Market', code: 'S-087', boardings: '5,870', alightings: '6,220', peak: '08:30' },
      { rank: 3, name: 'Airport Terminal', code: 'S-203', boardings: '4,960', alightings: '3,140', peak: '17:45' },
      { rank: 4, name: 'University Gate', code: 'S-121', boardings: '4,210', alightings: '4,480', peak: '16:30' },
    ],
    heatmap: [
      [12, 18, 32, 48, 72, 86, 64, 42],
      [16, 25, 45, 67, 89, 94, 73, 51],
      [10, 21, 39, 58, 76, 81, 59, 38],
      [8, 17, 28, 46, 61, 69, 48, 31],
      [6, 12, 22, 35, 44, 52, 36, 24],
    ],
  },
  routesStops: {
    kpis: [
      { label: 'Active routes', value: '24', unit: 'demo network', delta: 'Preview', direction: 'neutral', icon: 'route', tone: 'teal' },
      { label: 'Mapped stops', value: '186', unit: 'demo network', delta: 'Preview', direction: 'neutral', icon: 'map', tone: 'blue' },
      { label: 'Avg. route reliability', value: '87.4%', unit: 'demo view', delta: '+1.2 pts', direction: 'up', icon: 'check', tone: 'teal' },
      { label: 'Map provider', value: '—', unit: 'not connected', delta: 'Interface ready', direction: 'pending', icon: 'layers', tone: 'slate' },
    ],
    routePerformance: [
      { route: 'R-101', name: 'Central Loop', service: 'Corridor', reliability: '82%', load: '82%', delay: '4m 12s', status: 'Watch', tone: 'coral' },
      { route: 'R-107', name: 'Riverside Connector', service: 'Corridor', reliability: '88%', load: '74%', delay: '2m 48s', status: 'Stable', tone: 'teal' },
      { route: 'R-118', name: 'Civic District', service: 'Feeder', reliability: '93%', load: '46%', delay: '1m 36s', status: 'Stable', tone: 'teal' },
      { route: 'R-126', name: 'University Loop', service: 'Feeder', reliability: '90%', load: '58%', delay: '2m 04s', status: 'Stable', tone: 'teal' },
      { route: 'R-214', name: 'Airport Link', service: 'Express', reliability: '79%', load: '68%', delay: '6m 21s', status: 'Watch', tone: 'amber' },
    ],
    stopRanking: [
      { name: 'Central Hub', zone: 'Central', boardings: '8,420', alightings: '7,980', dwell: '00:42', status: 'High activity', tone: 'coral' },
      { name: 'Riverside Market', zone: 'Riverside', boardings: '5,870', alightings: '6,220', dwell: '00:36', status: 'High activity', tone: 'amber' },
      { name: 'Airport Terminal', zone: 'Airport', boardings: '4,960', alightings: '3,140', dwell: '00:28', status: 'High activity', tone: 'blue' },
      { name: 'University Gate', zone: 'University', boardings: '4,210', alightings: '4,480', dwell: '00:31', status: 'Steady', tone: 'teal' },
    ],
  },
  delays: {
    kpis: [
      { label: 'On-time performance', value: '87.4%', unit: 'demo view', delta: '+1.2 pts', direction: 'up', icon: 'check', tone: 'teal' },
      { label: 'Median positive delay', value: '3m 18s', unit: 'demo events', delta: '−0m 24s', direction: 'up', icon: 'clock', tone: 'blue' },
      { label: 'Late stop visits', value: '12.6%', unit: 'demo visits', delta: '−0.8 pts', direction: 'up', icon: 'pulse', tone: 'amber' },
      { label: 'Worst affected corridor', value: 'R-214', unit: 'demo route', delta: 'Watch', direction: 'attention', icon: 'route', tone: 'coral' },
    ],
    distribution: {
      labels: ['0–2m', '2–4m', '4–6m', '6–8m', '8–10m', '10m+'],
      values: [42, 27, 16, 8, 4, 3],
      label: 'Positive delay distribution',
      unit: 'demo share',
    },
    trend: {
      labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
      values: [61, 64, 67, 72, 75, 58, 54],
      comparison: [67, 68, 70, 76, 78, 63, 59],
      label: 'Punctuality trend',
      unit: 'demo percent',
    },
    routeReliability: [
      { route: 'R-214', name: 'Airport Link', onTime: '79%', median: '6m 21s', variance: 'High', status: 'Watch', tone: 'coral' },
      { route: 'R-101', name: 'Central Loop', onTime: '82%', median: '4m 12s', variance: 'Medium', status: 'Watch', tone: 'amber' },
      { route: 'R-107', name: 'Riverside Connector', onTime: '88%', median: '2m 48s', variance: 'Medium', status: 'Stable', tone: 'teal' },
      { route: 'R-126', name: 'University Loop', onTime: '90%', median: '2m 04s', variance: 'Low', status: 'Stable', tone: 'blue' },
      { route: 'R-118', name: 'Civic District', onTime: '93%', median: '1m 36s', variance: 'Low', status: 'Stable', tone: 'teal' },
    ],
    timePattern: [
      { label: '06–09', value: 78, tone: 'coral' },
      { label: '09–12', value: 42, tone: 'teal' },
      { label: '12–15', value: 36, tone: 'blue' },
      { label: '15–18', value: 61, tone: 'amber' },
      { label: '18–21', value: 84, tone: 'coral' },
    ],
  },
  occupancy: {
    kpis: [
      { label: 'Average load factor', value: '64%', unit: 'demo capacity', delta: '+4.1 pts', direction: 'up', icon: 'gauge', tone: 'teal' },
      { label: 'Overcrowded trips', value: '3.8%', unit: 'demo trips', delta: '−0.6 pts', direction: 'up', icon: 'layers', tone: 'coral' },
      { label: 'Critical exposure', value: '14', unit: 'demo segments', delta: 'Needs review', direction: 'attention', icon: 'alert', tone: 'amber' },
      { label: 'Capacity coverage', value: '91%', unit: 'demo segments', delta: 'Known', direction: 'neutral', icon: 'database', tone: 'blue' },
    ],
    bands: [
      { label: 'Low', value: 28, color: 'teal' },
      { label: 'Moderate', value: 39, color: 'blue' },
      { label: 'High', value: 24, color: 'amber' },
      { label: 'Overcrowded', value: 7, color: 'coral' },
      { label: 'Critical', value: 2, color: 'purple' },
    ],
    trend: {
      labels: ['06', '07', '08', '09', '10', '11', '16', '17', '18', '19'],
      values: [58, 78, 94, 81, 64, 52, 71, 88, 97, 82],
      comparison: [55, 69, 81, 76, 61, 49, 65, 76, 85, 77],
      label: 'Peak load factor by hour',
      unit: 'demo percent',
    },
    persistent: [
      { route: 'R-101', direction: 'Inbound', window: 'Weekday · 08:00–09:00', load: '112%', duration: '38m', stops: '5', status: 'Persistent', tone: 'coral' },
      { route: 'R-214', direction: 'Outbound', window: 'Weekday · 17:00–19:00', load: '106%', duration: '1h 12m', stops: '4', status: 'Persistent', tone: 'coral' },
      { route: 'R-107', direction: 'Inbound', window: 'Weekday · 08:00–09:00', load: '101%', duration: '24m', stops: '3', status: 'Recurring', tone: 'amber' },
      { route: 'R-126', direction: 'Inbound', window: 'Weekday · 16:00–18:00', load: '97%', duration: '—', stops: '—', status: 'Near limit', tone: 'blue' },
    ],
  },
  forecasting: {
    kpis: [
      { label: 'Forecast cases', value: '—', unit: 'API required', delta: 'Not supplied', direction: 'pending', icon: 'database', tone: 'slate' },
      { label: 'Model status', value: 'Awaiting API', unit: 'contract only', delta: 'No claims', direction: 'pending', icon: 'trend', tone: 'blue' },
      { label: 'Evaluation split', value: 'Locked', unit: 'design contract', delta: 'Awaiting results', direction: 'neutral', icon: 'shield', tone: 'teal' },
      { label: 'Baseline comparison', value: '—', unit: 'API required', delta: 'Not supplied', direction: 'pending', icon: 'pulse', tone: 'amber' },
    ],
    forecast: {
      labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug'],
      actual: [48, 53, 58, 64, 62, 70, 76, 82],
      forecast: [null, null, null, 64, 63, 70, 77, 84],
      lower: [null, null, null, 58, 57, 63, 69, 75],
      upper: [null, null, null, 70, 69, 77, 85, 93],
      label: 'Forecast versus actual',
      unit: 'demo index',
    },
    horizon: [
      { label: '1 hour', value: 'Awaiting API', status: 'pending' },
      { label: '6 hours', value: 'Awaiting API', status: 'pending' },
      { label: '24 hours', value: 'Awaiting API', status: 'pending' },
      { label: '7 days', value: 'Awaiting API', status: 'pending' },
    ],
    readiness: [
      { label: 'Chronological split', value: 'Contract ready', status: 'good' },
      { label: 'Baseline comparison', value: 'API required', status: 'pending' },
      { label: 'Leakage evidence', value: 'API required', status: 'pending' },
      { label: 'Unseen case cohort', value: 'API required', status: 'pending' },
    ],
  },
  clustering: {
    kpis: [
      { label: 'Route clusters', value: '—', unit: 'API required', delta: 'Not supplied', direction: 'pending', icon: 'cluster', tone: 'slate' },
      { label: 'Routes covered', value: 'Awaiting API', unit: 'contract only', delta: 'No claims', direction: 'pending', icon: 'route', tone: 'blue' },
      { label: 'Feature window', value: 'Historical', unit: 'design contract', delta: 'Past only', direction: 'neutral', icon: 'clock', tone: 'teal' },
      { label: 'Explanation view', value: 'Ready', unit: 'UI interface', delta: 'Awaiting results', direction: 'neutral', icon: 'spark', tone: 'amber' },
    ],
    points: [
      { x: 0.22, y: 0.74, label: 'Corridor', cluster: 'High-load corridors', color: 'coral' },
      { x: 0.34, y: 0.67, label: 'Corridor', cluster: 'High-load corridors', color: 'coral' },
      { x: 0.29, y: 0.58, label: 'Feeder', cluster: 'Growing feeders', color: 'teal' },
      { x: 0.48, y: 0.43, label: 'Feeder', cluster: 'Growing feeders', color: 'teal' },
      { x: 0.61, y: 0.29, label: 'Social', cluster: 'Coverage-first', color: 'amber' },
      { x: 0.72, y: 0.23, label: 'Social', cluster: 'Coverage-first', color: 'amber' },
      { x: 0.84, y: 0.58, label: 'Express', cluster: 'Premium corridors', color: 'blue' },
    ],
    clusters: [
      { name: 'High-load corridors', routes: 'Awaiting API', signal: 'Demand + load', color: 'coral', action: 'Review capacity first' },
      { name: 'Growing feeders', routes: 'Awaiting API', signal: 'Demand growth', color: 'teal', action: 'Test frequency' },
      { name: 'Coverage-first', routes: 'Awaiting API', signal: 'Low load + access', color: 'amber', action: 'Protect coverage' },
      { name: 'Premium corridors', routes: 'Awaiting API', signal: 'Distance + reliability', color: 'blue', action: 'Protect experience' },
    ],
    assignments: [
      { route: 'R-101', name: 'Central Loop', cluster: 'Awaiting API', confidence: '—', rationale: 'API result required' },
      { route: 'R-107', name: 'Riverside Connector', cluster: 'Awaiting API', confidence: '—', rationale: 'API result required' },
      { route: 'R-118', name: 'Civic District', cluster: 'Awaiting API', confidence: '—', rationale: 'API result required' },
    ],
  },
  passengerFlow: {
    kpis: [
      { label: 'Completed OD pairs', value: '—', unit: 'API required', delta: 'Not supplied', direction: 'pending', icon: 'flow', tone: 'slate' },
      { label: 'Top corridor', value: 'Awaiting API', unit: 'contract only', delta: 'No claims', direction: 'pending', icon: 'route', tone: 'blue' },
      { label: 'Directional split', value: 'Awaiting API', unit: 'contract only', delta: 'No claims', direction: 'pending', icon: 'pulse', tone: 'teal' },
      { label: 'Unresolved endpoints', value: '—', unit: 'API required', delta: 'Report explicitly', direction: 'pending', icon: 'alert', tone: 'amber' },
    ],
    matrix: [
      [0, 12, 18, 31, 46, 58, 44, 28, 17],
      [14, 0, 22, 37, 52, 64, 51, 34, 21],
      [19, 23, 0, 41, 57, 69, 55, 38, 24],
      [26, 29, 38, 0, 63, 72, 61, 43, 27],
      [33, 37, 49, 58, 0, 76, 68, 51, 32],
      [41, 46, 58, 66, 73, 0, 74, 59, 38],
      [34, 39, 51, 59, 67, 72, 0, 54, 35],
      [22, 28, 39, 46, 55, 61, 53, 0, 29],
      [15, 20, 27, 33, 42, 49, 43, 29, 0],
    ],
    topPairs: [
      { origin: 'Central Hub', destination: 'Airport Terminal', route: 'R-214', count: 'Awaiting API', share: '—' },
      { origin: 'Riverside Market', destination: 'Central Hub', route: 'R-107', count: 'Awaiting API', share: '—' },
      { origin: 'University Gate', destination: 'Civic District', route: 'R-126', count: 'Awaiting API', share: '—' },
    ],
    directional: [
      { label: 'Inbound', value: 'Awaiting API', note: 'Direction contract ready' },
      { label: 'Outbound', value: 'Awaiting API', note: 'Direction contract ready' },
      { label: 'Cross-network', value: 'Awaiting API', note: 'OD contract ready' },
    ],
  },
  whatIf: {
    baseline: [
      { label: 'Boardings served', value: '—', note: 'Awaiting baseline API' },
      { label: 'Average wait', value: '—', note: 'Awaiting baseline API' },
      { label: 'Peak load factor', value: '—', note: 'Awaiting baseline API' },
      { label: 'Coverage retained', value: '—', note: 'Awaiting baseline API' },
    ],
    scenario: [
      { label: 'Boardings served', value: '—', note: 'Scenario estimate pending' },
      { label: 'Average wait', value: '—', note: 'Scenario estimate pending' },
      { label: 'Peak load factor', value: '—', note: 'Scenario estimate pending' },
      { label: 'Coverage retained', value: '—', note: 'Scenario estimate pending' },
    ],
    assumptions: [
      'Clone a known schedule, capacity, route-stop, and demand state.',
      'Label every result as an estimate; never overwrite the baseline.',
      'Show unsupported capabilities when a required input is unavailable.',
    ],
  },
  recommendations: {
    summary: [
      { label: 'Evidence-backed actions', value: '12', unit: 'demo queue', tone: 'coral' },
      { label: 'High priority', value: '4', unit: 'demo queue', tone: 'amber' },
      { label: 'Awaiting evidence', value: '8', unit: 'demo queue', tone: 'slate' },
    ],
    insights: [
      { title: 'Capacity is concentrated in a repeatable morning window', detail: 'A recurring pattern appears on the central corridor in the demo fixture. Confirm with the analytics API before action.', type: 'Capacity', tone: 'coral', confidence: 'Demo insight' },
      { title: 'Delay exposure is route and stop specific', detail: 'The UI has a slot for a stop-level evidence panel and an attached source window.', type: 'Reliability', tone: 'amber', confidence: 'Demo insight' },
      { title: 'A scenario test can separate demand from schedule mismatch', detail: 'Use the what-if workspace to compare frequency, capacity, and timing assumptions.', type: 'Scenario', tone: 'teal', confidence: 'Demo insight' },
    ],
    recommendations: [
      { title: 'Review peak frequency on R-101', owner: 'Network planning', evidence: 'Awaiting API evidence', impact: 'High', status: 'Review', tone: 'coral' },
      { title: 'Validate delay pocket around S-087', owner: 'Service delivery', evidence: 'Awaiting API evidence', impact: 'Medium', status: 'Investigate', tone: 'amber' },
      { title: 'Protect coverage on social routes', owner: 'Network planning', evidence: 'Awaiting API evidence', impact: 'Required', status: 'Protect', tone: 'teal' },
    ],
  },
  dataQuality: {
    kpis: [
      { label: 'Overall readiness', value: 'Awaiting API', unit: 'no claims', delta: 'Contract ready', direction: 'pending', icon: 'shield', tone: 'slate' },
      { label: 'Raw records', value: '—', unit: 'API required', delta: 'Not supplied', direction: 'pending', icon: 'database', tone: 'blue' },
      { label: 'Issue families', value: '16', unit: 'design families', delta: 'UI catalog ready', direction: 'neutral', icon: 'alert', tone: 'amber' },
      { label: 'Lineage coverage', value: '—', unit: 'API required', delta: 'Not supplied', direction: 'pending', icon: 'route', tone: 'teal' },
    ],
    severity: [
      { label: 'Blocking', value: '—', status: 'pending' },
      { label: 'High', value: '—', status: 'pending' },
      { label: 'Medium', value: '—', status: 'pending' },
      { label: 'Low', value: '—', status: 'pending' },
    ],
    issueFamilies: [
      { code: 'DQ-01', name: 'Missing ticket records', stage: 'Quality', status: 'Awaiting result', tone: 'pending' },
      { code: 'DQ-02', name: 'Missing route identifiers', stage: 'Quality', status: 'Awaiting result', tone: 'pending' },
      { code: 'DQ-03', name: 'Invalid stop identifiers', stage: 'Quality', status: 'Awaiting result', tone: 'pending' },
      { code: 'DQ-04', name: 'Duplicate business keys', stage: 'Quality', status: 'Awaiting result', tone: 'pending' },
      { code: 'DQ-05', name: 'Invalid count observations', stage: 'Quality', status: 'Awaiting result', tone: 'pending' },
      { code: 'DQ-06', name: 'Invalid timestamps / timing', stage: 'Quality', status: 'Awaiting result', tone: 'pending' },
      { code: 'DQ-07', name: 'Capacity metadata', stage: 'Quality', status: 'Awaiting result', tone: 'pending' },
      { code: 'DQ-08', name: 'Invalid delay values', stage: 'Quality', status: 'Awaiting result', tone: 'pending' },
    ],
    reconciliation: [
      { label: 'Raw → staging', value: 'Awaiting API', status: 'pending' },
      { label: 'Staging → accepted', value: 'Awaiting API', status: 'pending' },
      { label: 'Run/table totals', value: 'Awaiting API', status: 'pending' },
      { label: 'Split manifest', value: 'Awaiting API', status: 'pending' },
    ],
  },
  system: {
    pipeline: [
      { stage: 'Source snapshot', detail: 'Version and manifest', status: 'Awaiting API', tone: 'pending', icon: 'database' },
      { stage: 'Validation & quality', detail: 'Issues and reconciliation', status: 'Awaiting API', tone: 'pending', icon: 'shield' },
      { stage: 'Feature preparation', detail: 'Past-only feature contract', status: 'Awaiting API', tone: 'pending', icon: 'sliders' },
      { stage: 'Analytics service', detail: 'Frontend contract boundary', status: 'Not connected', tone: 'pending', icon: 'activity' },
      { stage: 'Presentation layer', detail: 'This dashboard foundation', status: 'Ready for wiring', tone: 'good', icon: 'grid' },
    ],
    sources: [
      { name: 'Analytics API', detail: 'Proposed capability contracts', status: 'Not connected', tone: 'pending' },
      { name: 'System status API', detail: 'Pipeline and freshness metadata', status: 'Contract only', tone: 'pending' },
      { name: 'Map adapter', detail: 'Optional provider boundary', status: 'Interface ready', tone: 'good' },
    ],
    events: [
      { time: '—', title: 'Awaiting pipeline event', detail: 'No production runtime is connected to this UI.', tone: 'pending' },
      { time: '—', title: 'Awaiting DQ reconciliation', detail: 'Quality summaries will appear when the service responds.', tone: 'pending' },
      { time: '—', title: 'Frontend foundation ready', detail: 'Demo fixtures remain isolated from API contracts.', tone: 'good' },
    ],
  },
});

const DATA_KEY_BY_PAGE = Object.freeze({
  'routes-stops': 'routesStops',
  'what-if': 'whatIf',
  'passenger-flow': 'passengerFlow',
  'data-quality': 'dataQuality',
});

export function getDemoPageData(pageId) {
  const dataKey = DATA_KEY_BY_PAGE[pageId] ?? pageId;
  return demoData[dataKey] ?? demoData.executive;
}

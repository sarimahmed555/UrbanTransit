export const NAV_GROUPS = [
  {
    label: 'Overview',
    items: [
      {
        id: 'executive',
        label: 'Executive Overview',
        shortLabel: 'Executive',
        icon: 'grid',
        description: 'Network pulse and priority signals',
      },
    ],
  },
  {
    label: 'Understand the network',
    items: [
      {
        id: 'demand',
        label: 'Passenger Demand',
        shortLabel: 'Demand',
        icon: 'pulse',
        description: 'Ridership, peaks, routes, and stops',
      },
      {
        id: 'routes-stops',
        label: 'Routes & Stops',
        shortLabel: 'Routes & stops',
        icon: 'route',
        description: 'Network structure and performance',
      },
      {
        id: 'passenger-flow',
        label: 'OD / Passenger Flow',
        shortLabel: 'OD / flow',
        icon: 'flow',
        description: 'Origin, destination, and stop-to-stop flows',
      },
    ],
  },
  {
    label: 'Operational intelligence',
    items: [
      {
        id: 'delays',
        label: 'Delay Analysis',
        shortLabel: 'Delays',
        icon: 'clock',
        description: 'Punctuality and delay distributions',
      },
      {
        id: 'occupancy',
        label: 'Crowding Risk / Occupancy',
        shortLabel: 'Occupancy',
        icon: 'layers',
        description: 'Load, capacity, and crowding risk',
      },
      {
        id: 'forecasting',
        label: 'Demand Forecasting',
        shortLabel: 'Forecasting',
        icon: 'trend',
        description: 'Forecast versus actual view',
      },
    ],
  },
  {
    label: 'Decision support',
    items: [
      {
        id: 'clustering',
        label: 'Route Clustering',
        shortLabel: 'Clustering',
        icon: 'cluster',
        description: 'Behavioral route groupings',
      },
      {
        id: 'what-if',
        label: 'What-If Analysis',
        shortLabel: 'What-if',
        icon: 'sliders',
        description: 'Scenario planning and estimates',
      },
      {
        id: 'recommendations',
        label: 'Recommendations / Insights',
        shortLabel: 'Insights',
        icon: 'spark',
        description: 'Evidence-backed action queue',
      },
    ],
  },
  {
    label: 'Trust and operations',
    items: [
      {
        id: 'data-quality',
        label: 'Data Quality',
        shortLabel: 'Data quality',
        icon: 'shield',
        description: 'Validation, lineage, and issue status',
      },
      {
        id: 'system',
        label: 'System / Evidence Status',
        shortLabel: 'System status',
        icon: 'activity',
        description: 'Pipeline health and API readiness',
      },
    ],
  },
];

// Additional product surfaces preserve every existing route identifier.
NAV_GROUPS[1].items.push({ id: 'peak-hours', label: 'Peak-Hour Analysis', icon: 'clock', description: 'Demand peaks by service window' });
NAV_GROUPS[2].items.push(
  { id: 'underutilization', label: 'Underutilization', icon: 'layers', description: 'Spare capacity and low-demand services' },
  { id: 'delay-prediction', label: 'Delay Prediction', icon: 'trend', description: 'Predicted delays and observed outcomes' },
  { id: 'occupancy-forecast', label: 'Occupancy Forecast', icon: 'trend', description: 'Future crowding and capacity pressure' },
);
NAV_GROUPS[1].items.push({ id: 'network-map', label: 'Transit Network Map', icon: 'map', description: 'Verified route geometry and stops' });
NAV_GROUPS[3].items.push(
  { id: 'passenger-clustering', label: 'Passenger Clustering', icon: 'cluster', description: 'Aggregate passenger travel patterns' },
  { id: 'model-comparison', label: 'Model Comparison', icon: 'chart', description: 'Spark MLlib and Python scikit-learn test unseen evaluation' },
);
NAV_GROUPS[4].items.push({ id: 'reports', label: 'Reports', icon: 'download', description: 'Certified reports and exports' });
NAV_GROUPS.push({ label: 'Account', items: [
  { id: 'profile', label: 'Profile & Session', icon: 'shield', description: 'Account permissions and logout' },
  { id: 'login', label: 'Terminal Entry', icon: 'grid', description: 'Workspace authentication' },
] });

export const ALL_NAV_ITEMS = NAV_GROUPS.flatMap((group) => group.items);

export const PAGE_BY_ID = Object.fromEntries(
  ALL_NAV_ITEMS.map((item) => [item.id, item]),
);

PAGE_BY_ID.home = { id: 'home', label: 'Home', description: 'UrbanTransit IQ landing page' };

export const DEFAULT_PAGE_ID = 'executive';

export function isPageId(value) {
  return Object.prototype.hasOwnProperty.call(PAGE_BY_ID, value);
}

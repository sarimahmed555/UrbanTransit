const ICON_PATHS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.2"/><rect x="14" y="3" width="7" height="7" rx="1.2"/><rect x="3" y="14" width="7" height="7" rx="1.2"/><rect x="14" y="14" width="7" height="7" rx="1.2"/>',
  pulse: '<path d="M3 12h3l2.2-6 4.1 12 2.2-6H21"/>',
  route: '<circle cx="5" cy="18" r="2"/><circle cx="19" cy="6" r="2"/><path d="M7 18h3a4 4 0 0 0 4-4v-4a4 4 0 0 1 4-4h-1"/>',
  flow: '<path d="M4 7h5l3 5 3-5h5"/><path d="M4 17h5l3-5 3 5h5"/><path d="m17 5 3 2-3 2M17 15l3 2-3 2"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.2 2"/>',
  layers: '<path d="m12 3 8 4-8 4-8-4 8-4Z"/><path d="m4 12 8 4 8-4M4 17l8 4 8-4"/>',
  trend: '<path d="M4 17 9 12l3.2 3.2L20 7.5"/><path d="M15 7.5H20v5"/>',
  cluster: '<circle cx="7" cy="7" r="2.3"/><circle cx="17" cy="8" r="2.3"/><circle cx="12" cy="17" r="2.3"/><path d="m8.8 8.4 2 6M15.8 9.5l-2.6 5.5M9.4 6.4l5.2.5"/>',
  sliders: '<path d="M4 6h7M15 6h5M4 12h3M11 12h9M4 18h9M17 18h3"/><circle cx="13" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="15" cy="18" r="2"/>',
  spark: '<path d="m12 3 1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5L12 3Z"/><path d="m19 16 .6 2.4L22 19l-2.4.6L19 22l-.6-2.4L16 19l2.4-.6L19 16Z"/>',
  shield: '<path d="M12 3 19 6v5c0 4.5-3 8.1-7 10-4-1.9-7-5.5-7-10V6l7-3Z"/><path d="m8.5 12 2.2 2.2 4.8-5"/>',
  activity: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
  search: '<circle cx="10.8" cy="10.8" r="6.3"/><path d="m16 16 4.2 4.2"/>',
  bell: '<path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4"/>',
  chevron: '<path d="m8 10 4 4 4-4"/>',
  chevronRight: '<path d="m9 5 7 7-7 7"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
  filter: '<path d="M4 5h16l-6.2 7.1v5.3l-3.6 1.8v-7.1L4 5Z"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.8-4L3 10"/><path d="M3 5v5h5M4 13a8 8 0 0 0 14.8 4L21 14"/><path d="M21 19v-5h-5"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  database: '<ellipse cx="12" cy="5.5" rx="7.5" ry="3"/><path d="M4.5 5.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6M4.5 11.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6"/>',
  users: '<circle cx="9" cy="8" r="3"/><path d="M3.5 20a5.5 5.5 0 0 1 11 0M16 5.5a3 3 0 0 1 0 5.8M17 14.5a5.3 5.3 0 0 1 3.5 5"/>',
  check: '<path d="m5 12 4.2 4.2L19 6.5"/>',
  gauge: '<path d="M4.5 16a8 8 0 1 1 15 0"/><path d="m12 12 3.4-3.4M6 19h12"/>',
  alert: '<path d="M12 3 2.8 19h18.4L12 3Z"/><path d="M12 9v4M12 16.5v.1"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.6 2.6 0 1 1 4.4 1.9c-1.3 1.1-1.9 1.5-1.9 3M12 17v.1"/>',
  map: '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6Z"/><path d="M9 3v15M15 6v15"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5M4 20h16"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/>',
  arrowUp: '<path d="m6 15 6-6 6 6"/>',
  arrowDown: '<path d="m6 9 6 6 6-6"/>',
  more: '<circle cx="5" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 10.5v5M12 7.5v.1"/>',
  play: '<path d="m9 6 9 6-9 6V6Z"/>',
  circle: '<circle cx="12" cy="12" r="7.5"/>',
  checkCircle: '<circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/>',
  warning: '<path d="M12 3 2.8 19h18.4L12 3Z"/><path d="M12 9v4M12 16.5v.1"/>',
  lock: '<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  plug: '<path d="M9 3v5M15 3v5M7 8h10v2a5 5 0 0 1-10 0V8ZM12 15v6"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M9 4v16M15 4v16"/>',
  wand: '<path d="m15 4 5 5M13.5 5.5 4 15l5 5 9.5-9.5"/><path d="M5 4v4M3 6h4M19 16v4M17 18h4"/>',
  chart: '<path d="M4 19V5M4 19h16"/><path d="m7 15 3-4 3 2 5-7"/>',
};

/**
 * Render a small inline SVG icon without an external icon dependency.
 * @param {keyof typeof ICON_PATHS|string} name
 * @param {number} size
 * @param {string} className
 */
export function icon(name, size = 18, className = '') {
  const path = ICON_PATHS[name] ?? ICON_PATHS.circle;
  return `<svg class="icon ${className}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
}

export function hasIcon(name) {
  return Boolean(ICON_PATHS[name]);
}

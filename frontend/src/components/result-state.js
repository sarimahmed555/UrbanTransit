import { chartState, escapeHtml, panel, table } from './ui.js';

/** Presentation boundary only. No fetching, calculation, fixtures or endpoint assumptions. */
export function resultPanel({ title, description = '', result = null, columns = [] }) {
  const state = result?.state || 'not_configured';
  const rows = state === 'ready' && Array.isArray(result?.rows) ? result.rows : [];
  const body = (state === 'ready' || state === 'empty' || state === 'not_configured') && columns.length
    ? table({ columns, rows, mode: 'api', dataReady: true, emptyTitle: state === 'empty' ? 'No results for this selection' : 'Awaiting processed data' })
    : chartState({ state, title: state === 'error' ? 'Results could not be loaded' : state === 'empty' ? 'No results for this selection' : 'Awaiting processed data', description: state === 'error' ? 'Please try again when the service is available.' : 'Results will appear after a verified source is connected.' });
  const freshness = result?.asOf ? `Updated ${escapeHtml(result.asOf)}` : 'Freshness unavailable';
  return panel({ title, description, body: `<div aria-live="polite" aria-busy="${state === 'loading'}">${body}</div><div class="result-provenance">${freshness}<span>${escapeHtml(result?.source || 'Source not connected')}</span></div>` });
}

import { PAGE_CAPABILITIES, operationalPanel } from './components/operational-evidence.js';
import { createApiClient } from './api/client.js';
import { renderShell, renderMobileOverlay } from './components/layout.js';
import { button, errorState, escapeHtml, filterBar } from './components/ui.js';
import { icon } from './components/icons.js';
import { DEFAULT_PAGE_ID, isPageId, PAGE_BY_ID } from './navigation.js';
import { renderPage } from './pages/index.js';
import { DEMO_FILTER_OPTIONS } from './data/demo-data.js';

const STORAGE_KEY = 'urbanTransitIq.dashboardMode.v3';
const root = document.querySelector('[data-app-shell]');

export const apiClient = createApiClient({
  baseUrl: globalThis.__URBANTRANSIT_API_BASE_URL__
    || document.querySelector('meta[name="urbantransit-api-base-url"]')?.content
    || 'http://127.0.0.1:8000',
  fetcher: globalThis.fetch?.bind(globalThis),
});

const state = {
  activePage: readPageFromHash(),
  demoMode: false,
  localPreview: false,
  mode: readMode(),
  apiConnection: 'checking',
  searchValue: '',
  refreshing: false,
  filterOpen: false,
  sidebarOpen: false,
  sidebarCollapsed: false,
  demoBannerVisible: true,
  apiViewModels: {},
  evidenceRequests: {},
  operationalResponses: {},
  operationalFilters: {},
  operationalRequests: {},
  mapSelection: null,
  session: null,
  authentication: null,
  sessionTimer: null,
  signingIn: false,
  accountMode: 'sign-in',
  authMessage: '',
  toast: null,
  toastTimer: null,
};

export async function mountApp() {
  if (!root) return;
  state.localPreview = isLoopbackHost();
  const configuration = await apiClient.configuration();
  state.demoMode = state.localPreview && configuration.demoMode;
  state.activePage = readPageFromHash();
  bindEvents();
  if (!state.localPreview && state.activePage !== 'login') {
    state.activePage = 'login';
    globalThis.history?.replaceState?.(null, '', `${globalThis.location.pathname}${globalThis.location.search}#/login`);
  }
  render();
  checkApiConnection();
  if (!state.localPreview && apiClient.authenticationTransport) apiClient.authenticationStatus().then(r=>{ state.authentication=r.state==='ready'?r.data.data:{state:'NOT_READY'}; render(); });
  if ((state.localPreview || state.session) && state.activePage !== 'login' && state.activePage !== 'home') {
    loadPageEvidence(state.activePage);
    loadOperationalPage(state.activePage);
  }
  globalThis.urbanTransitApi = apiClient;
  globalThis.urbanTransitApp = { state, render, apiClient, setApiViewModel };
}

function bindEvents() {
  window.addEventListener?.('hashchange', () => {
    let requestedPage = readPageFromHash();
    if (!state.localPreview && requestedPage !== 'login' && !state.session) {
      state.activePage = 'login';
      globalThis.history?.replaceState?.(null, '', `${globalThis.location.pathname}${globalThis.location.search}#/login`);
      state.authMessage = 'Sign in to access the Executive Dashboard.';
    } else {
      state.activePage = requestedPage;
    }
    state.sidebarOpen = false;
    state.filterOpen = false;
    render();
    if ((state.localPreview || state.session) && state.activePage !== 'login' && state.activePage !== 'home') {
      loadPageEvidence(state.activePage);
      loadOperationalPage(state.activePage);
    }
    focusMain();
  });

  document.addEventListener('click', handleClick);
  document.addEventListener('submit', handleSubmit);
  document.addEventListener('change', handleChange);
  document.addEventListener('input', handleInput);
  document.addEventListener('keydown', handleKeydown);
}

function handleClick(event) {
  const actionElement = event.target.closest('[data-action]');
  if (!actionElement) return;
  const action = actionElement.dataset.action;
  if (!action) return;

  if (action === 'show-create-account' || action === 'show-sign-in') {
    state.accountMode = action === 'show-create-account' ? 'create-account' : 'sign-in';
    state.authMessage = '';
    render();
    return;
  }

  if (action === 'toggle-password') {
    const input = document.querySelector('#login-password');
    if (input) {
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      actionElement.textContent = visible ? 'Hide' : 'Show';
      actionElement.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
      actionElement.setAttribute('aria-pressed', String(visible));
    }
    return;
  }

  if (action === 'download-evidence') {
    actionElement.disabled=true;
    apiClient.downloadEvidence(actionElement.dataset.report, actionElement.dataset.format).then(blob => {
      const url=URL.createObjectURL(blob); const link=document.createElement('a');
      link.href=url; link.download=`${actionElement.dataset.report}-production-v1.1.${actionElement.dataset.format}`;
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(()=>URL.revokeObjectURL(url),1000);
    }).catch(()=>showToast('Verified report could not be downloaded. No replacement report was generated.','error')).finally(()=>{actionElement.disabled=false;});
    return;
  }
  if (action === 'reset-evidence-filters') {
    const vm=state.apiViewModels[state.activePage]; const evidence=vm?.productionEvidence || vm?.__productionEvidence;
    if(evidence) evidence.filters={}; render(); return;
  }
  if (action === 'catalog-previous' || action === 'catalog-next') {
    const current=state.apiViewModels['routes-stops']?.__catalog?.data;
    if(current) loadCatalog({kind:current.kind, ...current.applied_filters, offset:Math.max(0,current.offset+(action==='catalog-next'?100:-100))});
    return;
  }
  if (action === 'sign-out') {
    apiClient.logout().then(result=>{
      state.session=null; clearTimeout(state.sessionTimer); state.apiViewModels={}; state.operationalResponses={}; state.operationalRequests={};
      state.authMessage=result.state==='ready'?'Signed out. Server session revoked.':'Local session cleared. Server revocation could not be confirmed; the server session will expire.';
      navigateTo('login'); render();
    }); return;
  }
  if (action === 'toggle-sidebar') {
    if (globalThis.innerWidth <= 1100) state.sidebarOpen = !state.sidebarOpen;
    else state.sidebarCollapsed = !state.sidebarCollapsed;
    render();
    return;
  }
  if (action === 'open-sidebar') {
    state.sidebarOpen = true;
    render();
    return;
  }
  if (action === 'close-sidebar') {
    state.sidebarOpen = false;
    render();
    return;
  }
  if (action === 'toggle-filters') {
    state.filterOpen = !state.filterOpen;
    render();
    return;
  }
  if (action === 'dismiss-demo-banner') {
    state.demoBannerVisible = false;
    render();
    return;
  }
  if (action === 'close-toast') {
    state.toast = null;
    clearTimeout(state.toastTimer);
    render();
    return;
  }
  if (action === 'return-home') {
    navigateTo(DEFAULT_PAGE_ID);
    return;
  }
  if (action === 'refresh') {
    runRefresh();
    return;
  }
  if (action === 'notifications') {
    showToast('Notifications are not connected. Event status is unavailable.', 'info');
    return;
  }
  if (action === 'workspace-menu') {
    showToast('Workspace controls are prepared for the future operating configuration.', 'info');
    return;
  }
  if (action === 'open-about') {
    showToast('Frozen production-v1.1 evaluations. Operational feeds and authentication are not configured.', 'info');
    return;
  }
  if (action === 'scenario-operation') {
    const group = actionElement.closest('.segmented-control');
    group?.querySelectorAll('[data-action="scenario-operation"]').forEach((item) => item.classList.toggle('is-active', item === actionElement));
    showToast('Scenario operation selected. A baseline is required before estimates can run.', 'info');
    return;
  }
  if (action === 'map-select-route' || action === 'map-select-stop') {
    selectMapEntity(actionElement);
    return;
  }
  if (action === 'map-zoom-in' || action === 'map-zoom-out' || action === 'map-settings') {
    showToast(action === 'map-settings' ? 'Map layer controls are an adapter boundary; no live provider is loaded.' : 'Map zoom is ready for a future geometry adapter.', 'info');
    return;
  }
  if (action.startsWith('navigate-')) {
    const target = action.replace('navigate-', '');
    navigateTo(target);
    return;
  }
  if (action.startsWith('open-') || action.startsWith('export-') || action === 'apply-filters' || action === 'apply-od-filters' || action === 'run-what-if' || action === 'load-what-if-baseline') {
    const labels = {
      'run-what-if': 'Scenario execution waits for an analytics baseline and estimate service.',
      'load-what-if-baseline': 'Baseline loading waits for the analytics service contract.',
      'apply-filters': 'Filter context staged. API responses will preserve these filters when connected.',
      'apply-od-filters': 'OD filter context staged for the future passenger-flow response.',
      'open-insight': 'Evidence drawer is prepared; API evidence is not connected.',
      'open-thresholds': 'Threshold configuration is an analytics contract boundary.',
      'open-capacity-rules': 'Capacity rules will be supplied with the occupancy response.',
      'open-model-card': 'Model card and metrics are intentionally hidden until real results exist.',
      'open-route-performance': 'Route performance details are ready for an API response.',
      'open-stop-analysis': 'Stop analysis details are ready for an API response.',
      'open-reliability': 'Reliability details are ready for an API response.',
      'open-issues': 'Issue-level DQ detail is ready for an API response.',
      'open-action-queue': 'Action queue detail is ready for an API response.',
      'open-flow-detail': 'OD detail is ready for an API response.',
      'open-case-explorer': 'Forecast case detail is ready for an API response.',
      'open-cluster-assignments': 'Cluster assignment detail is ready for an API response.',
      'open-crowding-evidence': 'Crowding evidence detail is ready for an API response.',
      'open-what-if-audit': 'Scenario audit detail is ready for an API response.',
      'open-evidence-policy': 'Evidence policy is part of the future recommendation contract.',
      'open-rule-catalog': 'Rule catalog detail is ready for a quality API response.',
      'open-contracts': 'Contract paths are proposals only; no backend completion is implied.',
      'open-logs': 'Runtime log navigation is reserved for the pipeline service.',
    };
    showToast(labels[action] ?? `${humanize(action)} is prepared for the analytics service.`, 'info');
  }
}

/**
 * Map interaction is limited to identification: selecting a route or stop
 * records which entity the operator is looking at. It cannot create, move or
 * infer geometry, and the selection is cleared whenever the mode changes so a
 * demo pick can never survive into API mode.
 */
function selectMapEntity(element) {
  const kind = element?.dataset?.entityKind;
  const id = element?.dataset?.entityId;
  if ((kind !== 'route' && kind !== 'stop') || !id) return;
  const current = state.mapSelection;
  state.mapSelection = current?.kind === kind && current?.id === id ? null : { kind, id };
  render();
}

function handleChange(event) {
  const element = event.target.closest('[data-action="source-mode"]');
  if (element) {
    const nextMode = 'api';
    state.mode = nextMode;
    // A selection made in one mode must never be shown in the other.
    state.mapSelection = null;
    persistMode(nextMode);
    render();
    if (nextMode === 'api') {
      checkApiConnection();
      loadPageEvidence(state.activePage);
    }
    showToast(nextMode === 'demo' ? 'Demo preview enabled. Synthetic fixtures are clearly marked.' : 'Live evidence mode selected. Checking the verified result service.', 'success');
  }
}

function handleInput(event) {
  if (event.target.matches('[data-action="global-search"]')) {
    state.searchValue = event.target.value;
  }
}

function handleKeydown(event) {
  if (event.key === 'Escape') {
    if (state.sidebarOpen || state.filterOpen) {
      state.sidebarOpen = false;
      state.filterOpen = false;
      render();
    }
    return;
  }
  if (event.key === 'Enter' && event.target.matches('[data-action="global-search"]')) {
    event.preventDefault();
    searchViews(state.searchValue);
  }
}

function renderLoginAccountMode() {
  if(typeof root.querySelector!=='function') return;
  const form=root.querySelector('.auth-form');
  if(!form) return;
  const title=root.querySelector('#login-title');
  const description=root.querySelector('.terminal-description');
  if(state.accountMode!=='create-account') {
    if(!root.querySelector('[data-action="show-create-account"]')) {
      form.insertAdjacentHTML('afterend','<button type="button" class="button button--outline entry-action" data-action="show-create-account">CREATE ACCOUNT</button>');
    }
    return;
  }
  const authState=state.authentication?.state || 'NOT_CONFIGURED';
  const availability=!apiClient.authenticationTransport
    ? 'Account creation requires the configured HTTPS API.'
    : authState==='NOT_CONFIGURED'
      ? 'Authentication PostgreSQL is not configured. Set UTIQ_AUTH_DATABASE_DSN and prepare the app_auth schema.'
      : authState!=='READY' ? 'Authentication service is not configured or ready.' : '';
  if(title) title.textContent='Create account';
  if(description) description.textContent='Create an account for dashboard access.';
  form.dataset.form='create-account';
  form.innerHTML=`<label for="register-identity">Identity<input id="register-identity" name="username" placeholder="Email or username" required minlength="3" maxlength="128" autocomplete="username" autocapitalize="none" /></label><label for="register-password">Password<input id="register-password" name="password" type="password" placeholder="At least 12 characters" required minlength="12" maxlength="1024" autocomplete="new-password" /></label><label for="register-password-confirm">Confirm password<input id="register-password-confirm" name="confirmPassword" type="password" placeholder="Re-enter your password" required minlength="12" maxlength="1024" autocomplete="new-password" /></label>${state.authMessage ? `<p role="alert" class="auth-message">${escapeHtml(state.authMessage)}</p>` : ''}${availability ? `<p role="status" class="auth-availability">${escapeHtml(availability)}</p>` : ''}<button type="submit" class="button button--primary entry-action" ${!apiClient.authenticationTransport || state.signingIn ? 'disabled' : ''}>${state.signingIn ? 'Creating account…' : 'CREATE ACCOUNT'}</button><button type="button" class="button button--outline entry-action" data-action="show-sign-in">BACK TO SIGN IN</button>`;
}

function render() {
  if (!root) return;
  if (!state.localPreview && state.activePage === 'home') state.activePage = 'login';
  try {
    const pageMarkup = renderPage(
      state.activePage,
      state.mode,
      { ...(state.apiViewModels[state.activePage] || {}), __session: state.session, __authentication: { ...(state.authentication || {}), secureTransport:apiClient.authenticationTransport, signingIn:state.signingIn, message:state.authMessage } },
      state.mapSelection,
      state.apiConnection,
    );
    const globalFilters = state.filterOpen ? renderGlobalFilters() : '';
    const operationalMarkup=operationalPanel(state.operationalResponses[state.activePage], PAGE_CAPABILITIES[state.activePage], state.operationalFilters[state.activePage]);
    const content = `${operationalMarkup}${state.mode === 'demo' && state.demoBannerVisible ? renderDemoBanner() : ''}${globalFilters}${pageMarkup}`;
  if (state.activePage === 'login' || state.activePage === 'home') { root.innerHTML = pageMarkup; if (state.activePage === 'login') renderLoginAccountMode(); document.title = 'TransitVerse Intelligence · UrbanTransit IQ'; return; }
    root.innerHTML = `${renderShell({ activePage: state.activePage, mode: state.mode, demoMode: state.demoMode, apiConnection: state.apiConnection, content, searchValue: state.searchValue, refreshing: state.refreshing, filterOpen: state.filterOpen, sidebarOpen: state.sidebarOpen, sidebarCollapsed: state.sidebarCollapsed })}${renderMobileOverlay(state.sidebarOpen)}${state.toast ? renderToast(state.toast) : ''}`;
    document.title = `${PAGE_BY_ID[state.activePage]?.label ?? 'Dashboard'} · UrbanTransit IQ`;
  } catch (error) {
    root.innerHTML = `${renderShell({ activePage: state.activePage, mode: state.mode, content: errorState({ title: 'Dashboard view could not render', description: 'The frontend caught a rendering error. Reload or return to the executive view.', action: button({ label: 'Return home', action: 'return-home', variant: 'primary', size: 'sm' }) }) })}`;
    console.error('UrbanTransit IQ render error', error);
  }
}

async function checkApiConnection() {
  const response = await apiClient.health();
  state.apiConnection = response.state === 'ready'
    && response.data?.dataset_version === 'production-v1.1'
    ? 'connected'
    : 'unavailable';
  if(!apiClient.authenticationTransport) state.authentication={state:'NOT_CONFIGURED'};
  render();
  return state.apiConnection;
}

function renderGlobalFilters() {
  return `<div class="global-filter-drawer"><div class="global-filter-drawer__head"><div><span class="eyebrow">Workspace context</span><h2>Global filters</h2><p>These controls are shared by the page contracts and can be connected to URL state later.</p></div>${button({ label: 'Close filters', action: 'toggle-filters', iconName: 'close', variant: 'outline', size: 'sm' })}</div>${filterBar({ mode: state.mode, filters: [
    { key: 'global-date', label: 'Date range', icon: 'calendar', options: DEMO_FILTER_OPTIONS.dateRanges },
    { key: 'global-day', label: 'Service day', icon: 'pulse', options: DEMO_FILTER_OPTIONS.serviceDays },
    { key: 'global-route', label: 'Route scope', icon: 'route', options: DEMO_FILTER_OPTIONS.routes, demoOptions: true },
    { key: 'global-source', label: 'Source snapshot', icon: 'database', options: ['Latest available', 'Selected snapshot', 'Awaiting API'] },
  ], actions: button({ label: 'Apply workspace filters', action: 'apply-filters', iconName: 'check', variant: 'primary', size: 'sm' }) })}</div>`;
}

function renderDemoBanner() {
  return `<div class="demo-banner" role="status"><span class="demo-banner__icon">${icon('spark', 15)}</span><span><strong>Demo preview</strong> · Synthetic UI fixtures are visible for layout development. Values are not production results and will be replaced by API responses.</span><button type="button" class="demo-banner__dismiss" data-action="dismiss-demo-banner" aria-label="Dismiss demo notice">${icon('close', 15)}</button></div>`;
}

function renderToast(toast) {
  return `<div class="toast toast--${escapeTone(toast.tone)}" role="status"><span class="toast__icon">${icon(toast.tone === 'success' ? 'checkCircle' : 'info', 17)}</span><span>${escapeHtml(toast.message)}</span><button type="button" data-action="close-toast" aria-label="Dismiss notification">${icon('close', 14)}</button></div>`;
}

function showToast(message, tone = 'info') {
  state.toast = { message, tone };
  clearTimeout(state.toastTimer);
  render();
  state.toastTimer = setTimeout(() => {
    state.toast = null;
    render();
  }, 4200);
}

function runRefresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  render();
  if (state.mode === 'demo') {
    state.refreshing = false;
    render();
    showToast('Demo preview refreshed locally. No analytics job was run.', 'info');
    return;
  }
  Promise.all([
    checkApiConnection(),
    loadPageEvidence(state.activePage, { force: true }),
  ]).finally(() => {
    state.refreshing = false;
    render();
  });
}

const TASK_FOR_PAGE = Object.freeze({
  demand: ['passenger_demand'],
  forecasting: ['passenger_demand'],
  delays: ['delay_severity'],
  'delay-prediction': ['delay_severity'],
  occupancy: ['occupancy_forecast', 'crowding_risk'],
  'occupancy-forecast': ['occupancy_forecast', 'crowding_risk'],
  clustering: ['route_clustering'],
});

async function loadPageEvidence(pageId, { force = false } = {}) {
  if (state.mode !== 'api') return;
  if (['routes-stops','network-map','data-quality','reports'].includes(pageId)) { await loadSupportingEvidence(pageId); return; }
  const supported = pageId === 'executive'
    || pageId === 'system'
    || pageId === 'model-comparison'
    || pageId === 'delays'
    || pageId === 'delay-prediction'
    || Object.hasOwn(TASK_FOR_PAGE, pageId);
  if (!supported) return;
  const requestId = (state.evidenceRequests[pageId] || 0) + 1;
  state.evidenceRequests[pageId] = requestId;
  const existingEvidence = ['delay-prediction', 'occupancy-forecast', 'model-comparison']
    .includes(pageId)
    ? state.apiViewModels[pageId]?.productionEvidence
    : state.apiViewModels[pageId]?.__productionEvidence;
  if (!force && existingEvidence?.state === 'ready') return;
  setPageEvidence(pageId, { state: 'loading', data: null, meta: { source: 'audited result artifacts' } });
  if (state.activePage === pageId) render();

  const wrap = (response) => ({
    state: response.state,
    data: response.data,
    meta: response.meta,
    error: response.error,
  });
  const loadTasks = async (taskNames) => {
    const responses = await Promise.all(
      taskNames.map((name) => apiClient.evidenceTask(name, { predictionLimit: 50 })),
    );
    const failed = responses.find((item) => item.state !== 'ready');
    if (failed) return wrap(failed);
    return {
      state: 'ready',
      data: { kind: 'tasks', tasks: responses.map((item) => item.data) },
      meta: { source: 'audited_result_artifacts' },
    };
  };

  let evidence;
  if (pageId === 'executive') {
    const [status, tasks] = await Promise.all([apiClient.evidenceStatus(), apiClient.evidenceTasks()]);
    const failed = [status, tasks].find((item) => item.state !== 'ready');
    evidence = failed
      ? wrap(failed)
      : {
        state: 'ready',
        data: { kind: 'summary', summary: status.data, tasks: tasks.data },
        meta: { source: 'audited_result_artifacts' },
      };
  } else if (pageId === 'system') {
    const status = await apiClient.evidenceStatus();
    evidence = { ...wrap(status), data: { kind: 'summary', summary: status.data, tasks: [] } };
  } else if (pageId === 'model-comparison') {
    const [tasks, comparison] = await Promise.all([apiClient.evidenceTasks(), apiClient.delayComparison()]);
    const failed = [tasks, comparison].find((item) => item.state !== 'ready');
    evidence = failed
      ? wrap(failed)
      : {
        state: 'ready',
        data: { kind: 'comparison', tasks: tasks.data, comparison: comparison.data },
        meta: { source: 'verified_pipeline_comparison' },
      };
  } else if (pageId === 'delays' || pageId === 'delay-prediction') {
    const [task, status] = await Promise.all([
      apiClient.evidenceTask('delay_severity', { predictionLimit: 50 }),
      apiClient.evidenceStatus(),
    ]);
    evidence = task.state !== 'ready'
      ? wrap(task)
      : {
        state: 'ready',
        data: { kind: 'task', task: task.data, summary: status.data },
        meta: task.meta,
      };
  } else if (TASK_FOR_PAGE[pageId]) {
    evidence = await loadTasks(TASK_FOR_PAGE[pageId]);
  } else {
    return;
  }

  if (state.evidenceRequests[pageId] !== requestId) return;
  setPageEvidence(pageId, evidence);
  if (pageId === 'executive' || pageId === 'system') await loadSupportingEvidence(pageId);
  if (state.activePage === pageId) render();
}

function setPageEvidence(pageId, evidence) {
  if (['delay-prediction', 'occupancy-forecast', 'model-comparison'].includes(pageId)) {
    state.apiViewModels[pageId] = { ...state.apiViewModels[pageId], productionEvidence: evidence };
  } else {
    state.apiViewModels[pageId] = { ...state.apiViewModels[pageId], __productionEvidence: evidence };
  }
}

function searchViews(query) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return;
  const match = Object.values(PAGE_BY_ID).find((page) => `${page.label} ${page.description}`.toLowerCase().includes(normalized));
  if (match) {
    navigateTo(match.id);
  } else {
    showToast(`No view matched “${query}”. Try a page name such as Demand or Data Quality.`, 'info');
  }
}

function navigateTo(pageId) {
  if (!isPageId(pageId)) pageId = DEFAULT_PAGE_ID;
  if (!state.localPreview && pageId !== 'login' && !state.session) pageId = 'login';
  if (window.location.hash !== `#/${pageId}`) {
    window.location.hash = `#/${pageId}`;
  } else {
    state.activePage = pageId;
    render();
    focusMain();
  }
  state.sidebarOpen = false;
  state.filterOpen = false;
}

export function setApiViewModel(pageId, viewModel) {
  if (!PAGE_BY_ID[pageId]) return false;
  state.apiViewModels[pageId] = viewModel;
  render();
  return true;
}

function readPageFromHash() {
  const value = globalThis.location?.hash?.replace(/^#\/?/, '') || 'home';
  return isPageId(value) ? value : DEFAULT_PAGE_ID;
}

function isLoopbackHost() {
  const hostname = String(globalThis.location?.hostname || '').toLowerCase();
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]' || hostname === '::1';
}

function readMode() { return 'api'; }

function persistMode(mode) {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, mode);
  } catch {
    // Storage is optional; the in-memory state remains authoritative.
  }
}

function focusMain() {
  requestAnimationFrame(() => {
    document.querySelector('#main-content')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: 'instant' });
  });
}

function humanize(value) {
  return value.replaceAll('-', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function escapeTone(value) {
  return value === 'success' ? 'success' : value === 'error' ? 'error' : 'info';
}

async function loadCatalog({kind='routes',query='',route_id='',offset=0}={}) {
  state.apiViewModels['routes-stops']={__catalog:{state:'loading'}};
  if(state.activePage==='routes-stops') render();
  const response=await apiClient.evidenceCatalog(kind,{query,route_id,offset,limit:100});
  state.apiViewModels['routes-stops']={__catalog:response};
  if(state.activePage==='routes-stops') render();
}

async function loadSupportingEvidence(pageId) {
  if(pageId==='routes-stops') { await loadCatalog(); return; }
  if(pageId==='reports') {
    const response=await apiClient.evidenceTasks();
    state.apiViewModels[pageId]={__reportTasks:response.state==='ready'?response.data:null};
  } else if(pageId==='network-map') {
    const response=await apiClient.evidenceCatalog('stops',{limit:1000});
    state.apiViewModels[pageId]=response.state==='ready' ? {mapGeometry:{available:true,source:response.meta.source,asOf:response.meta.generated_at,datasetVersion:response.meta.dataset_version,routes:[],stops:response.data.rows.map(row=>({id:row.stop_id,name:row.stop_name,latitude:row.latitude,longitude:row.longitude})),scope:'Manifest-verified project stop coordinates only. No route polylines, GPS reconciliation, live service or hotspot values are represented.'}} : {state:response.state};
  } else {
    const response=await apiClient.evidenceDataset();
    state.apiViewModels[pageId]={...state.apiViewModels[pageId],__dataset:response};
  }
  if(state.activePage===pageId) render();
}

async function handleSubmit(event) {
  const form=event.target;
  if(!form.matches('[data-form]')) return;
  event.preventDefault();
  const values=Object.fromEntries(new FormData(form));
  if(form.dataset.form==='operational-filter') {
    if(values.startDate && values.endDate && values.startDate>values.endDate) { showToast('Invalid date range.','error'); return; }
    state.operationalFilters[state.activePage]=values; await loadOperationalPage(state.activePage);
  } else if(form.dataset.form==='evidence-filter') {
    if(values.start && values.end && values.start>values.end) { showToast('Start date must be on or before end date.','error'); return; }
    const vm=state.apiViewModels[state.activePage]; const evidence=vm?.productionEvidence || vm?.__productionEvidence;
    if(evidence) evidence.filters=values; render();
  } else if(form.dataset.form==='catalog-filter') {
    if(values.kind!=='route_stops' && values.route_id) { showToast('Route ID applies to route stop membership. Select that catalog or clear Route ID.','error'); return; }
    await loadCatalog(values);
  } else if((form.dataset.form==='sign-in' || form.dataset.form==='create-account') && !state.signingIn) {
    const creatingAccount=form.dataset.form==='create-account';
    if(creatingAccount && values.password!==values.confirmPassword) {
      state.authMessage='Passwords do not match.';
      render();
      return;
    }
    state.signingIn=true; state.authMessage='';
    const username=values.username;
    const password=values.password;
    form.reset(); render();
    let result=creatingAccount
      ? await apiClient.register(username,password)
      : await apiClient.login(username,password);
    if(creatingAccount && result.state==='ready') {
      result=await apiClient.login(username,password);
    }
    state.signingIn=false;
    if(result.state==='ready') {
      state.accountMode='sign-in';
      state.session=result.data; state.authMessage='';
      clearTimeout(state.sessionTimer);
      state.sessionTimer=setTimeout(()=>{ apiClient.clearSession(); state.session=null; state.apiViewModels={}; state.operationalResponses={}; state.operationalRequests={}; state.authMessage='Session expired. Sign in again.'; navigateTo('login'); render(); },Math.max(0,result.data.session_expires_at*1000-Date.now()));
      loadOperationalPage(state.activePage);
      navigateTo('executive');
      loadPageEvidence('executive');
      loadOperationalPage('executive');
    } else state.authMessage=result.error?.message || 'Authentication unavailable.';
    render();
  }
}

async function loadOperationalPage(pageId) {
  const capability=PAGE_CAPABILITIES[pageId];
  if((!state.localPreview && !state.session) || !capability) return;
  const session=state.session;
  const request=(state.operationalRequests[pageId] || 0)+1; state.operationalRequests[pageId]=request;
  const response=await apiClient.request(capability,state.operationalFilters[pageId] || {});
  if((!state.localPreview && state.session!==session) || state.operationalRequests[pageId]!==request) return;
  state.operationalResponses[pageId]=response;
  if(state.activePage===pageId) render();
}

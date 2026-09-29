import { verifiedReports } from './verified-context.js';
import { normalizeNetworkGeometry, projectNetworkGeometry } from '../api/map-adapter.js';
import { pageFrame } from './shared.js';
import { resultPanel } from '../components/result-state.js';
import { button, escapeHtml, panel, notice } from '../components/ui.js';
import { networkMap } from '../components/map.js';

export const PRODUCT_AREAS = Object.freeze({
  home: { title: 'UrbanTransit IQ', description: 'TransitVerse intelligence' },
  'peak-hours': { title: 'Peak-hour analysis', description: 'Understand recurring demand peaks across service windows.', columns: ['Service window', 'Route', 'Passenger demand', 'Source period'] },
  underutilization: { title: 'Underutilization', description: 'Review services with sustained spare capacity alongside their demand context.', columns: ['Route', 'Service window', 'Occupancy', 'Evidence'] },
  'delay-prediction': { title: 'Delay prediction', description: 'Inspect predicted delays alongside observed outcomes and evaluation evidence.', columns: ['Route / trip', 'Prediction time', 'Predicted delay', 'Observed delay', 'Model version'] },
  'occupancy-forecast': { title: 'Occupancy forecast', description: 'Review future capacity pressure, forecast horizons and uncertainty.', columns: ['Route / segment', 'Forecast horizon', 'Predicted occupancy', 'Uncertainty', 'Model version'] },
  'passenger-clustering': { title: 'Passenger clustering', description: 'Explore passenger travel patterns when privacy-safe aggregate results are available.', columns: ['Cluster', 'Travel pattern', 'Aggregate size', 'Evidence'] },
  'model-comparison': { title: 'Model comparison', description: 'Compare Spark MLlib and Python / scikit-learn using matching tasks and evaluation cohorts.', columns: ['Task', 'Pipeline', 'Model', 'Metric / unit', 'Validation', 'Test / unseen', 'Evidence'] },
  'network-map': { title: 'Transit network', description: 'Explore verified routes and stops in their geographic context.' },
  reports: { title: 'Reports', description: 'Review available report categories and prepare an export when certified results are connected.' },
  login: { title: 'Welcome to TransitVerse', description: 'Sign in to your UrbanTransit IQ workspace.' },
  profile: { title: 'Your workspace', description: 'Account and session information.' },
});

const REPORTS = ['Passenger demand', 'Route performance', 'Delay', 'Occupancy', 'Stop performance', 'Forecast', 'Route clustering', 'Recommendations', 'Pipeline comparison', 'Data quality', 'Executive summary', 'Passenger flow'];

function landing() {
  return `<main id="main-content" class="landing" tabindex="-1">
    <header class="landing-header"><a class="landing-brand" href="#/home">UrbanTransit IQ<span>Transport intelligence platform</span></a><a class="landing-dashboard" href="#/executive">DASHBOARD ↗</a></header>
    <section class="landing-content" aria-labelledby="landing-title">
      <p class="landing-kicker">TRANSITVERSE INTELLIGENCE</p>
      <h1 id="landing-title">The digital brain of a city's<br>transport network.</h1>
      <p class="landing-description">Big Data and Data Science intelligence for passenger flow, route performance, delays, demand, occupancy and transport operations.</p>
      <div class="transit-visual" aria-label="Live network model">
        <div class="transit-visual__caption"><span>LIVE NETWORK MODEL</span><span>DATA → INTELLIGENCE</span></div>
        <svg class="transit-network" viewBox="0 0 720 220" role="img" aria-label="Cyan city transport routes connected through data hubs"><g class="transit-network__grid"><path d="M0 35H720M0 75H720M0 115H720M0 155H720M0 195H720M60 0V220M160 0V220M260 0V220M360 0V220M460 0V220M560 0V220M660 0V220"/></g><g class="transit-network__routes"><path class="route route--dim" d="M28 174 128 174 194 126 292 126 365 62 475 62 548 112 690 112"/><path class="route route--main" d="M30 62 130 62 205 114 306 114 378 174 486 174 560 122 690 122"/><path class="route route--signal" d="M30 62 130 62 205 114 306 114 378 174 486 174 560 122 690 122"/><path class="route route--signal route--signal-alt" d="M28 174 128 174 194 126 292 126 365 62 475 62 548 112 690 112"/><path class="route route--alt" d="M128 174V62M292 126 306 114M475 62 486 174M560 112V122"/></g><g class="transit-network__nodes"><circle class="node-glow" cx="130" cy="62" r="22"/><circle class="node" cx="130" cy="62" r="4"/><circle class="node-glow node-glow--slow" cx="306" cy="114" r="26"/><circle class="node node--active" cx="306" cy="114" r="5"/><circle class="node" cx="475" cy="62" r="4"/><circle class="node" cx="486" cy="174" r="4"/><circle class="node" cx="560" cy="122" r="4"/></g><g class="transit-network__labels"><text x="31" y="48">CITY MOVEMENT</text><text x="272" y="94">DATA HUB</text><text x="553" y="151">INTELLIGENCE</text><text x="598" y="100">ROUTES / STOPS</text></g></svg>
        <div class="architecture" aria-label="HDFS to Spark to MLlib and Python to intelligence"><strong>HDFS</strong><i>→</i><strong>SPARK</strong><i>→</i><strong>MLlib + Python</strong><i>→</i><strong>INTELLIGENCE</strong></div>
      </div>
    </section>
  </main>`;
}

function reports() {
  return `<div class="report-grid">${REPORTS.map(label => panel({ title: label, description: 'Awaiting certified report data', body: `<p class="muted">CSV and JSON export</p>${button({ label: 'Download unavailable', disabled: true, variant: 'outline', size: 'sm' })}` })).join('')}</div>`;
}

function login(authentication = {}) {
  const secureTransport = authentication.secureTransport === true;
  const state = authentication.state || 'NOT_CONFIGURED';
  const availability = !secureTransport
    ? 'Sign-in requires the configured HTTPS API.'
    : state === 'READY'
      ? 'Use an account provisioned by your administrator. No default account is provided.'
      : state === 'NOT_CONFIGURED'
        ? 'Authentication PostgreSQL is not configured. Set UTIQ_AUTH_DATABASE_DSN and prepare the app_auth schema before signing in.'
      : 'Authentication service is not configured or ready.';
  return `<main id="main-content" class="entry entry--login" tabindex="-1"><section class="entry-story" aria-labelledby="entry-title"><a class="entry-brand" href="#/login">UrbanTransit <b>IQ</b><span>Transport intelligence platform</span></a><p class="entry-kicker">TRANSITVERSE INTELLIGENCE</p><h1 id="entry-title">The digital brain of a city's <span>transport network.</span></h1><p class="entry-intro">Big Data and Data Science intelligence for passenger flow, route performance, delays, demand, occupancy and transport operations.</p><div class="transit-visual" aria-hidden="true"><div class="transit-visual__caption"><span>LIVE NETWORK MODEL</span><span>DATA → INTELLIGENCE</span></div><svg class="transit-network" viewBox="0 0 720 290" role="presentation" focusable="false"><g class="transit-network__grid"><path d="M0 40H720M0 90H720M0 140H720M0 190H720M0 240H720M60 0V290M160 0V290M260 0V290M360 0V290M460 0V290M560 0V290M660 0V290"/></g><g class="transit-network__routes"><path class="route route--dim" d="M28 224 128 224 194 156 292 156 365 82 475 82 548 142 690 142"/><path class="route route--main" d="M30 80 130 80 205 144 306 144 378 212 486 212 560 158 690 158"/><path class="route route--signal" d="M30 80 130 80 205 144 306 144 378 212 486 212 560 158 690 158"/><path class="route route--signal route--signal-alt" d="M28 224 128 224 194 156 292 156 365 82 475 82 548 142 690 142"/><path class="route route--alt" d="M128 224 128 80M292 156 306 144M475 82 486 212M560 142 560 158"/></g><g class="transit-network__nodes"><circle class="node-glow" cx="130" cy="80" r="25"/><circle class="node" cx="130" cy="80" r="4"/><circle class="node-glow node-glow--slow" cx="306" cy="144" r="30"/><circle class="node node--active" cx="306" cy="144" r="5"/><circle class="node" cx="475" cy="82" r="4"/><circle class="node" cx="486" cy="212" r="4"/><circle class="node" cx="560" cy="158" r="4"/></g><g class="transit-network__labels"><text x="31" y="62">CITY MOVEMENT</text><text x="272" y="121">DATA HUB</text><text x="553" y="190">INTELLIGENCE</text><text x="598" y="128">ROUTES / STOPS</text></g></svg><div class="architecture" aria-label="HDFS feeds Spark and MLlib; Python and scikit-learn feed transport intelligence"><div><small>DISTRIBUTED DATA</small><strong>HDFS</strong><span>Transport network data</span></div><i>→</i><div><small>PROCESSING</small><strong>SPARK</strong><span>Big Data intelligence</span></div><i>→</i><div><small>MODEL LAYER</small><strong>MLlib + Python</strong><span>scikit-learn</span></div><i class="architecture__merge">↘</i><div class="architecture__result"><small>TRANSPORT OUTCOMES</small><strong>INTELLIGENCE</strong></div></div></div></section><section class="terminal login-card" aria-labelledby="login-title"><div class="terminal-top"><span>URBANTRANSIT IQ</span><span>SECURE TERMINAL ACCESS</span></div><div class="terminal-body"><span class="terminal-symbol" aria-hidden="true">UT</span><p class="terminal-eyebrow">AUTHORIZED PERSONNEL</p><h2 id="login-title">Sign in</h2><p class="terminal-description">Authenticated access to UrbanTransit IQ.</p><form class="auth-form" data-form="sign-in"><label for="login-identity">Identity<input id="login-identity" name="username" placeholder="Email or username" required autocomplete="off" /></label><label for="login-password">Password / Access Key<input id="login-password" name="password" type="password" placeholder="Enter your password" required autocomplete="current-password" /></label>${authentication.message ? `<p role="alert" class="auth-message">${escapeHtml(authentication.message)}</p>` : ''}${!secureTransport || state !== 'READY' ? `<p role="status" class="auth-availability">${escapeHtml(availability)}</p>` : ''}<button type="submit" class="button button--primary entry-action" ${!secureTransport || authentication.signingIn ? 'disabled' : ''}>${authentication.signingIn ? 'Signing in…' : 'SIGN IN'}</button></form><div class="login-security-note"><span class="security-indicator" aria-hidden="true"></span><span>Authenticated access to UrbanTransit IQ</span></div></div></section></main>`;
}

export function renderProductArea(pageId, result = null, selection = null) {
  const meta = PRODUCT_AREAS[pageId];
  const productionEvidence = result?.productionEvidence || null;
  let children;
  if (pageId === 'home') return landing();
  if (pageId === 'login') return login(result?.__authentication);
  else if (pageId === 'profile' && result?.__session) children = panel({title:'Authenticated session',description:'Identity and permissions returned by the existing authentication API.',body:`<p>${escapeHtml(result.__session.username)} · ${escapeHtml(result.__session.role)}</p><p class="muted">Expires ${escapeHtml(new Date(result.__session.session_expires_at*1000).toISOString())}</p><ul>${result.__session.permissions.map(p=>`<li>${escapeHtml(p)}</li>`).join('')}</ul>${button({label:'Sign out',action:'sign-out',variant:'outline'})}`});
  else if (pageId === 'profile') children = panel({ title: 'No active session', body: `<p class="muted">Account details and permissions will appear after sign-in is connected.</p><div class="page-actions"><a class="button button--primary button--sm" href="#/login">Sign in</a>${button({ label: 'Sign out', disabled: true, variant: 'outline', size: 'sm' })}</div>` });
  else if (pageId === 'reports') children = Array.isArray(result?.__reportTasks) ? verifiedReports(result.__reportTasks) : reports();
  else if (pageId === 'network-map') {
    const normalized = normalizeNetworkGeometry(result?.mapGeometry);
    const state = ['loading', 'error'].includes(result?.state) ? result.state : normalized.state;
    const geometry = { ...normalized, projection: state === 'ready' ? projectNetworkGeometry(normalized.routes, normalized.stops) : null };
    children = `${result?.mapGeometry?.scope ? notice({title:'Static stop-coordinate view',description:result.mapGeometry.scope}) : ''}${networkMap({ mode: 'api', title: 'Network overview', state, geometry, selection })}`;
  }
  else if (pageId === 'model-comparison' && productionEvidence?.state === 'ready') {
    children = notice({
      title: 'Comparison scope',
      description: 'Only the delay task has persisted case-level Spark/Python alignment. Other task metrics are shown as separate held-out results, not paired-case comparisons.',
      tone: 'info',
    });
  } else if (productionEvidence?.state === 'ready' && ['delay-prediction', 'occupancy-forecast'].includes(pageId)) {
    children = notice({
      title: 'Historical evaluation evidence available',
      description: pageId === 'delay-prediction'
        ? 'The audited model results above include held-out evaluation evidence. A live delay prediction service and operational predictions are not available.'
        : 'The audited model results above include held-out occupancy and crowding evaluations. A live forecasting service and operational forecasts are not available.',
      tone: 'info',
    });
  }
  else children = `${pageId === 'model-comparison' ? notice({ title: 'Comparable evidence required', description: 'Metrics need matching targets, units, split definitions and unseen test cases. No winning model has been selected.' }) : ''}${resultPanel({ title: meta.title, description: 'Results and supporting evidence', result, columns: meta.columns.map(label => ({ label, key: label })) })}${pageId === 'model-comparison' ? `<div class="dashboard-grid dashboard-grid--split">${resultPanel({ title: 'Spark MLlib', description: 'Model runs, evaluation metrics and provenance' })}${resultPanel({ title: 'Python / scikit-learn', description: 'Independent evaluation and comparison evidence' })}</div>` : ''}`;
  const updated = productionEvidence?.state === 'ready'
    ? `Audited evidence · ${productionEvidence.meta?.generated_at || 'timestamp unavailable'}`
    : 'Awaiting API evidence';
  return pageFrame({ data: { ...meta, eyebrow: 'TransitVerse intelligence', __productionEvidence: productionEvidence, __apiConnection: result?.__apiConnection }, mode: 'api', updated, children });
}

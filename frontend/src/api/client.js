import { API_CONTRACTS, getContract } from './contracts.js';
import { toQueryParams } from './filters.js';

const DEFAULT_TIMEOUT_MS = 15000;

/**
 * Creates the shared frontend API adapter for application contracts and
 * audited read-only evidence endpoints.
 *
 * The adapter never substitutes demo fixtures when an API request fails.
 * @param {{baseUrl?: string, fetcher?: typeof fetch, timeoutMs?: number}} options
 */
export function createApiClient({ baseUrl = '', fetcher, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const transport = typeof fetcher === 'function' ? fetcher : globalThis.fetch?.bind(globalThis);
  const configured = Boolean(baseUrl && transport);
  let accessToken = null;
  let session = null;
  const apiUrl = configured ? new URL(baseUrl) : null;
  const authenticationTransport = configured && (
    apiUrl.protocol === 'https:'
    || (apiUrl.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(apiUrl.hostname))
  );

  async function requestPath(path) {
    if (!configured) {
      return {
        state: 'not_configured',
        data: null,
        meta: { source: 'api', warnings: ['Backend base URL or Fetch API is unavailable.'] },
      };
    }

    const url = new URL(path, ensureTrailingSlash(baseUrl));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await transport(url, {
        method: 'GET',
        signal: controller.signal,
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`Evidence request failed with HTTP ${response.status}`);
      }
      const payload = await response.json();
      const state = payload?.status === 'ready' ? 'ready' : 'not_configured';
      return {
        state,
        data: payload?.data ?? null,
        meta: payload?.meta ?? { source: 'api' },
      };
    } catch (error) {
      return {
        state: 'error',
        data: null,
        meta: { source: 'api' },
        error: error instanceof Error ? error : new Error('Unknown API error'),
      };
    } finally {
      clearTimeout(timer);
    }
  }

  async function configuration() {
    if (!configured) return { demoMode: false };
    try {
      const response = await transport(new URL('/api/config', ensureTrailingSlash(baseUrl)), {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) return { demoMode: false };
      return { demoMode: (await response.json())?.demo_mode === true };
    } catch {
      return { demoMode: false };
    }
  }

  function unavailable(contractName) {
    const contract = getContract(contractName);
    return {
      state: 'not_configured',
      data: null,
      meta: {
        source: 'api',
        warnings: [
          `Frontend contract only: ${contract.method} ${contract.path} is not connected.`,
        ],
      },
    };
  }

  async function request(contractName, params = {}) {
    if (!configured) return unavailable(contractName);

    const contract = getContract(contractName);
    const url = new URL(contract.path, ensureTrailingSlash(baseUrl));
    const isPost = contract.method === 'POST';
    if (!isPost) {
      Object.entries(toQueryParams(params)).forEach(([key, value]) => {
        url.searchParams.set(key, String(value));
      });
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await transport(url, {
        method: contract.method,
        signal: controller.signal,
        headers: {
          Accept: 'application/json',
          ...(isPost ? { 'Content-Type': 'application/json' } : {}),
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        ...(isPost ? { body: JSON.stringify(params) } : {}),
      });
      if (!response.ok) {
        throw new Error(`Analytics request failed with HTTP ${response.status}`);
      }
      const data = await response.json();
      return {
        state: data?.status === 'ready' ? 'ready' : 'not_configured',
        data,
        meta: { source: 'api' },
      };
    } catch (error) {
      return {
        state: 'error',
        data: null,
        meta: { source: 'api' },
        error: error instanceof Error ? error : new Error('Unknown API error'),
      };
    } finally {
      clearTimeout(timer);
    }
  }

  async function authRequest(path, method = 'GET', body = null) {
    if (!authenticationTransport) return {state:'not_configured', error:{message:'Authentication requires the configured HTTPS API.'}};
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await transport(new URL(path, ensureTrailingSlash(baseUrl)), {
        method, signal:controller.signal, cache:'no-store', redirect:'error',
        headers:{Accept:'application/json', ...(body ? {'Content-Type':'application/json'} : {}), ...(accessToken ? {Authorization:`Bearer ${accessToken}`} : {})},
        ...(body ? {body:JSON.stringify(body)} : {}),
      });
      if (!response.ok) {
        if (response.status === 401) { accessToken=null; session=null; }
        return {state:response.status===503?'not_configured':'error',error:{message:response.status===429?'Too many attempts. Try again later.':response.status===403?'Your account does not have permission.':response.status===409?'Account could not be created; sign in or try another username.':'Authentication unavailable or credentials invalid.'}};
      }
      return {state:'ready',data:await response.json()};
    } catch { return {state:'error',error:{message:'Authentication service could not be reached securely.'}}; }
    finally { clearTimeout(timer); }
  }

  async function login(username, password) {
    accessToken=null; session=null;
    const result = await authRequest('/api/v1/auth/login','POST',{username,password});
    if(result.state!=='ready') return result;
    if(typeof result.data?.access_token!=='string') return {state:'error',error:{message:'Invalid authentication response.'}};
    accessToken=result.data.access_token;
    const identity=await authRequest('/api/v1/auth/me');
    if(identity.state==='ready') session=identity.data;
    else accessToken=null;
    return identity;
  }

  async function register(username, password) {
    return authRequest('/api/v1/auth/register','POST',{username,password});
  }

  async function logout() {
    const result=await authRequest('/api/v1/auth/logout','POST');
    accessToken=null; session=null;
    return result;
  }

  async function downloadEvidence(name, format='json') {
    if(!configured || !['json','csv'].includes(format)) throw new Error('Evidence export unavailable');
    const controller=new AbortController(); const timer=setTimeout(()=>controller.abort(),timeoutMs);
    try {
      const path=`/api/v1/evidence/reports/${encodeURIComponent(name)}/download?format=${format}`;
      const response=await transport(new URL(path,ensureTrailingSlash(baseUrl)),{signal:controller.signal,headers:{Accept:format==='json'?'application/json':'text/csv'}});
      if(!response.ok) throw new Error('Verified report unavailable');
      return await response.blob();
    } finally { clearTimeout(timer); }
  }

  return {
    configured,
    configuration,
    authenticationTransport, login, register, logout,
    authenticationStatus: () => authRequest('/api/v1/auth/status'),
    clearSession: () => { accessToken=null; session=null; },
    currentUser: async () => { const response=await authRequest('/api/v1/auth/me'); if(response.state==='ready') session=response.data; return response; },
    getSession: () => session,
    downloadEvidence,
    evidenceDataset: () => requestPath('/api/v1/evidence/dataset'),
    evidenceCatalog: (kind, params={}) => requestPath(`/api/v1/evidence/catalog/${encodeURIComponent(kind)}?${new URLSearchParams(params)}`),
    comparisonCases: () => requestPath('/api/v1/evidence/comparison/delay/cases'),
    contracts: API_CONTRACTS,
    request,
    get: (contractName, params) => request(contractName, params),
    executiveSummary: (params) => request('executiveSummary', params),
    passengerDemand: (params) => request('passengerDemand', params),
    routesAndStops: (params) => request('routesAndStops', params),
    delayAnalysis: (params) => request('delayAnalysis', params),
    occupancy: (params) => request('occupancy', params),
    demandForecast: (params) => request('demandForecast', params),
    routeClusters: (params) => request('routeClusters', params),
    passengerFlow: (params) => request('passengerFlow', params),
    whatIf: (body) => request('whatIf', body),
    recommendations: (params) => request('recommendations', params),
    dataQuality: (params) => request('dataQuality', params),
    systemStatus: (params) => request('systemStatus', params),
    health: () => requestPath('/api/health'),
    evidenceStatus: () => requestPath(getContract('evidenceStatus').path),
    evidenceTasks: () => requestPath(getContract('evidenceTasks').path),
    evidenceTask: (taskName, { predictionLimit = 0 } = {}) => {
      const query = predictionLimit ? `?prediction_limit=${encodeURIComponent(predictionLimit)}` : '';
      const path = getContract('evidenceTask').path.replace(
        '{taskName}',
        encodeURIComponent(taskName),
      );
      return requestPath(`${path}${query}`);
    },
    delayComparison: () => requestPath(getContract('delayComparison').path),
  };
}

function ensureTrailingSlash(value) {
  return value.endsWith('/') ? value : `${value}/`;
}

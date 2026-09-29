import { panel, table, escapeHtml, notice, kpiGrid } from './ui.js';
import { hasViewModelShape } from '../api/view-models.js';

// Existing authenticated analytics contracts; no guessed endpoints or field values.
export const PAGE_CAPABILITIES = Object.freeze({
  executive:'executiveSummary', demand:'passengerDemand', 'routes-stops':'routesAndStops',
  'passenger-flow':'passengerFlow', 'peak-hours':'passengerDemand', delays:'delayAnalysis',
  occupancy:'occupancy', underutilization:'occupancy', forecasting:'demandForecast',
  'occupancy-forecast':'occupancy', 'delay-prediction':'delayAnalysis', clustering:'routeClusters',
  recommendations:'recommendations', 'data-quality':'dataQuality', system:'systemStatus',
});

export function operationalPanel(response, capability, filters={}) {
  if(!response) return '';
  const controls=`<form class="filter-bar" data-form="operational-filter">${[['startDate','From','date'],['endDate','Through','date'],['routeId','Route ID','text'],['stopId','Stop ID','text'],['vehicleId','Vehicle ID','text'],['direction','Direction','text']].map(([key,label,type])=>`<label class="field">${label}<input name="${key}" type="${type}" value="${escapeHtml(filters[key]||'')}" /></label>`).join('')}<button class="button button--outline button--sm">Apply analytical filters</button></form>`;
  const data=response.data;
  if(response.state!=='ready' || !hasViewModelShape(data,capability) || data.dataset_version!=='production-v1.1') return panel({title:'Authenticated operational analytics',description:'No certified result for this selection',body:controls+notice({title:'Evidence unavailable',description:'The serving API has not supplied a ready production-v1.1 result with the required fields. Frozen model evidence remains separate.'})});
  const sections=Object.entries(data).filter(([key,val])=>key!=='kpis' && Array.isArray(val));
  return panel({title:'Certified operational analytics',description:`${data.dataset_version} · ${data.generated_at}`,body:controls+(Array.isArray(data.kpis)?kpiGrid(data.kpis,'api',{dataReady:true}):'')+sections.map(([key,rows])=>{
    const objects=rows.filter(r=>r && typeof r==='object' && !Array.isArray(r));
    const keys=[...new Set(objects.flatMap(Object.keys))];
    return `<h3 class="evidence-subheading">${escapeHtml(key)}</h3>${table({columns:keys.map(k=>({label:k,key:k})),rows:objects.map(row=>Object.fromEntries(Object.entries(row).map(([k,v])=>[k,typeof v==='object'?JSON.stringify(v):v]))),mode:'api',dataReady:true})}`;
  }).join('')+`<details class="metric-detail"><summary>Result provenance and applied filters</summary><pre>${escapeHtml(JSON.stringify(data.meta,null,2))}</pre></details>`});
}

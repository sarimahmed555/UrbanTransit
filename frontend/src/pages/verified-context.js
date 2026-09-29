import { escapeHtml, panel, table, notice } from '../components/ui.js';
import { exportButtons } from '../components/evidence-analysis.js';
import { pageFrame } from './shared.js';

export function inventoryPanel(response) {
  if (response?.state !== 'ready') return '';
  const data=response.data;
  return panel({title:'Verified dataset inventory',description:`${data.history_start} – ${data.history_end} · ${data.timezone} · frozen project-generated data`,body:`<p class="muted">Raw record counts include the documented data-quality defects. These are dataset inventory counts, not active passengers, live vehicles or cleaned service KPIs.</p><details class="metric-detail"><summary>Inspect source table counts and provenance</summary>${table({columns:[{label:'Source table',key:'table'},{label:'Raw rows',key:'raw_rows'}],rows:data.inventory,mode:'api',dataReady:true})}<pre>${escapeHtml(JSON.stringify(response.meta,null,2))}</pre></details>`});
}

export function renderVerifiedContext(pageId, data) {
  const response = pageId === 'data-quality' ? data.__dataset : data.__catalog;
  if (!response) return null;
  const titles = {'data-quality':'Data quality','routes-stops':'Routes & stops'};
  if (response.state !== 'ready') return pageFrame({data:{title:titles[pageId],description:'Verified dataset evidence',__apiConnection:data.__apiConnection},mode:'api',children:notice({title:response.state==='loading'?'Loading verified evidence':'Evidence unavailable',description:'A valid owner-bound response is required. No fixture values are substituted.'})});
  let children;
  if(pageId==='data-quality') {
    const checks=response.data.verification_checks.map(c=>({name:c.name,result:c.passed?'Passed':'Failed',detail:JSON.stringify(Object.fromEntries(Object.entries(c).filter(([k])=>!['name','passed'].includes(k))))}));
    children=inventoryPanel(response)+panel({title:'Owner-bound verification checks',description:'Existing bounded verification results. These do not claim a new data-quality execution or live anomaly detection.',body:table({columns:[{label:'Check',key:'name'},{label:'Result',key:'result'},{label:'Recorded evidence',key:'detail'}],rows:checks,mode:'api',dataReady:true})});
  } else {
    const catalog=response.data;
    const columns=catalog.kind==='stops' ? ['stop_code','stop_name','latitude','longitude','opened_on'] : catalog.kind==='vehicles' ? ['vehicle_id','vehicle_type','nominal_capacity','operational_status'] : catalog.kind==='route_stops' ? ['route_id','pattern_id','stop_id','stop_sequence'] : ['route_code','route_name','mode','service_type','route_status'];
    children=panel({title:'Verified source catalog',description:catalog.scope,body:`<form class="filter-bar" data-form="catalog-filter"><label class="field">Catalog<select name="kind">${['routes','stops','vehicles','route_stops'].map(k=>`<option value="${k}" ${catalog.kind===k?'selected':''}>${k.replaceAll('_',' ')}</option>`).join('')}</select></label><label class="field">Search<input name="query" maxlength="128" value="${escapeHtml(catalog.applied_filters.query)}" /></label><label class="field">Route ID (route stops only)<input name="route_id" maxlength="128" value="${escapeHtml(catalog.applied_filters.route_id)}" /></label><button class="button button--outline button--sm" type="submit">Apply catalog filters</button></form><p class="api-note">${catalog.total} matching records · ${catalog.excluded_rows} invalid or flagged rows excluded · showing ${catalog.offset + (catalog.rows.length?1:0)}–${catalog.offset+catalog.rows.length}</p>${table({columns:columns.map(k=>({label:k.replaceAll('_',' '),key:k})),rows:catalog.rows,mode:'api',dataReady:true})}<div class="page-actions"><button class="button button--outline button--sm" data-action="catalog-previous" ${catalog.offset===0?'disabled':''}>Previous</button><button class="button button--outline button--sm" data-action="catalog-next" ${catalog.offset+catalog.rows.length>=catalog.total?'disabled':''}>Next</button></div><details class="metric-detail"><summary>Catalog provenance</summary><pre>${escapeHtml(JSON.stringify(response.meta,null,2))}</pre><pre>${escapeHtml(JSON.stringify(catalog.source_hashes,null,2))}</pre></details>`})+notice({title:'Performance analytics unavailable',description:'Catalog identities do not establish route ranking, reliability, bottlenecks, demand or route geometry. Those findings require verified analytical outputs.'});
  }
  return pageFrame({data:{title:titles[pageId],description:'Owner-bound production-v1.1 evidence',__apiConnection:data.__apiConnection},mode:'api',children});
}

export function verifiedReports(tasks) {
  return `<div class="report-grid">${tasks.map(task=>panel({title:task.task_name.replaceAll('_',' '),description:'Frozen model evaluation and bounded held-out sample; not a full operational report.',body:exportButtons(task.task_name)})).join('')}${panel({title:'Spark / Python delay comparison',description:'All shared unseen cases, original truth/predictions, differences, limitations and provenance.',body:exportButtons('delay-comparison')})}</div>${notice({title:'Operational reports remain unavailable',description:'Route performance, stop performance, passenger flows, recommendations and the complete transport-intelligence report require certified operational aggregates.'})}`;
}

import { barChart, lineChart } from './charts.js';
import { chartFrame, escapeHtml, table } from './ui.js';

export function evidenceControls(filters = {}) {
  return `<form class="filter-bar analytics-toolbar" data-form="evidence-filter" aria-label="Filter bounded prediction samples"><div class="analytics-toolbar__fields"><label class="analytics-field">Pipeline<select name="pipeline"><option value="">Both pipelines</option><option value="spark" ${filters.pipeline === 'spark' ? 'selected' : ''}>Spark MLlib</option><option value="python" ${filters.pipeline === 'python' ? 'selected' : ''}>Python</option></select></label><label class="analytics-field">Case contains<input name="query" maxlength="128" value="${escapeHtml(filters.query || '')}" /></label><label class="analytics-field">Target from<input name="start" type="date" value="${escapeHtml(filters.start || '')}" /></label><label class="analytics-field">Target through<input name="end" type="date" value="${escapeHtml(filters.end || '')}" /></label></div><div class="analytics-toolbar__actions"><button type="submit" class="button button--primary button--sm">Apply filters</button><button type="button" class="button button--outline button--sm" data-action="reset-evidence-filters">Reset</button></div><p class="analytics-toolbar__note">Filters apply to loaded bounded prediction samples and charts. Full-cohort metrics and frozen comparison totals remain unchanged.</p></form>`;
}

export function filterSample(rows = [], filters = {}) {
  return rows.filter(row => {
    const day = typeof row.target_start === 'string' ? row.target_start.slice(0,10) : '';
    return String(row.case_id || '').toLowerCase().includes((filters.query || '').toLowerCase())
      && (!filters.start || (day && day >= filters.start)) && (!filters.end || (day && day <= filters.end));
  });
}

export function exportButtons(name) {
  return `<div class="analytics-export-actions" aria-label="Download ${escapeHtml(name)} evidence">${['json','csv'].map(format => `<button type="button" class="button button--outline button--sm" data-action="download-evidence" data-report="${escapeHtml(name)}" data-format="${format}">Download ${format.toUpperCase()}</button>`).join('')}</div>`;
}

export function evidenceVisualizations(tasks, filters = {}) {
  return tasks.map(task => {
    const pipelines = Object.entries(task.pipelines || {}).filter(([engine]) => !filters.pipeline || filters.pipeline === engine);
    return `<section class="evidence-visual-section" aria-label="${escapeHtml(task.task_name)} evaluation visualizations"><div class="evidence-visual-heading"><h3 class="evidence-subheading">${escapeHtml(task.task_name.replaceAll('_',' '))} · Evaluation charts</h3>${exportButtons(task.task_name)}</div>${pipelines.map(([engine, result]) => {
      const title = engine === 'spark' ? 'Spark MLlib' : 'Python';
      const metric = result.test_metrics || {};
      const sample = filterSample(result.prediction_sample, filters);
      let charts = '';
      if (result.model_type === 'regression') {
        const valid = sample.filter(row => typeof row.actual === 'number' && typeof row.prediction === 'number');
        charts += chartFrame({title:`${title} · Actual versus predicted`, subtitle:`${valid.length} loaded held-out cases. Case order, not a continuous time series or future forecast.`, source:'api', body:lineChart({labels:valid.map((_,i)=>String(i+1)),series:[{label:'Actual',values:valid.map(r=>r.actual),color:'teal'},{label:'Predicted',values:valid.map(r=>r.prediction),color:'blue'}],ariaLabel:`${title} bounded held-out actual versus predicted`,formatValue:v=>Number(v.toFixed(3))})});
        charts += chartFrame({title:`${title} · Prediction error`, subtitle:'Predicted minus actual, on the same displayed cases; negative values mean underprediction.', source:'api', body:lineChart({labels:valid.map((_,i)=>String(i+1)),series:[{label:'Error',values:valid.map(r=>r.prediction-r.actual),color:'blue'},{label:'Zero error',values:valid.map(()=>0),color:'slate',dashed:true}],ariaLabel:`${title} bounded held-out residuals`,formatValue:v=>Number(v.toFixed(3))})});
        const baseline=result.baseline_vs_selected;
        if (baseline && Number.isFinite(baseline.baseline) && Number.isFinite(baseline.selected)) charts+=chartFrame({title:`${title} · Baseline / selected model`,subtitle:`Full held-out cohort · ${baseline.metric}; lower MAE is better.`,source:'api',body:barChart({labels:['Baseline','Selected'],values:[baseline.baseline,baseline.selected],colors:['slate','blue'],ariaLabel:`${title} original held-out baseline comparison`})});
      }
      if (Array.isArray(metric.confusion_matrix) && Array.isArray(metric.class_labels)) {
        charts += chartFrame({title:`${title} · Confusion matrix`, subtitle:'Full held-out cohort. Rows: actual class; columns: predicted class.',source:'api',body:table({columns:[{label:'Actual / predicted',key:'actual'},...metric.class_labels.map((label,i)=>({label,key:`p${i}`}))],rows:metric.confusion_matrix.map((row,i)=>({actual:metric.class_labels[i],...Object.fromEntries(row.map((v,j)=>[`p${j}`,v]))})),mode:'api',dataReady:true})});
        if (metric.per_class?.length) charts+=chartFrame({title:`${title} · Recall by class`,subtitle:'Full held-out cohort; zero recall remains visible in the table below.',source:'api',body:barChart({labels:metric.per_class.map(r=>r.label),values:metric.per_class.map(r=>r.recall),colors:metric.per_class.map(()=>'blue'),ariaLabel:`${title} per class recall`})+table({columns:[{label:'Class',key:'label'},{label:'Precision',key:'precision'},{label:'Recall',key:'recall'},{label:'F1',key:'f1'},{label:'Support',key:'support'}],rows:metric.per_class,mode:'api',dataReady:true})});
      }
      if(result.model_type==='clustering' && sample.length) {
        const counts=new Map(); for(const row of sample) counts.set(String(row.prediction),(counts.get(String(row.prediction))||0)+1);
        charts+=chartFrame({title:`${title} · Sample cluster assignments`,subtitle:`${sample.length} loaded cases only. Cluster IDs are arbitrary and are not matched across pipelines.`,source:'api',body:barChart({labels:[...counts.keys()].map(k=>`Cluster ${k}`),values:[...counts.values()],colors:[...counts].map(()=>'blue'),ariaLabel:`${title} bounded assignment counts`})});
      }
      return `<div class="dashboard-grid dashboard-grid--split">${charts}</div><details class="metric-detail"><summary>${title} · Model selection, version and provenance</summary><pre>${escapeHtml(JSON.stringify({model:result.selected_model,run:result.model_run_id,feature_version:result.feature_version,result_sha256:result.result_sha256,candidates:result.candidate_models,selection_metric:result.selection_metric,selection_rationale:result.selection_rationale,validation_metrics:result.validation_metrics,periods:result.periods,provenance:result.provenance},null,2))}</pre></details>`;
    }).join('')}</section>`;
  }).join('');
}

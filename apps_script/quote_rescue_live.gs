/** Independent, bounded live validation. Loading this file never starts a trigger. */
const QRV1 = Object.freeze({
 repo:'lzwislam-collab/doge-data-collector',handler:'runQuoteRescueLiveValidation',
 approved:'PR54:16a7eeef4b284f5ba62e4742f73f0be6e50792e3',
 maxPolls:12,maxWallMs:65*60000,maxBodyMs:30*60000,maxExecutionMs:6*60000,maxFetches:37,
 stateKey:'QRV1_STATE',startedKey:'QRV1_STARTED'
});
function quoteRescueLiveSave_(properties,state) {
 const text=JSON.stringify(state);
 if(encodeURIComponent(text).replace(/%[0-9A-F]{2}|./g,'x').length>=8000)throw new Error('QRV1_STATE_SIZE');
 properties.setProperty(QRV1.stateKey,text);
}
function quoteRescueLiveLoad_(properties) {
 const raw=properties.getProperty(QRV1.stateKey);if(!raw)throw new Error('QRV1_NOT_STARTED');
 const s=JSON.parse(raw);
 if(!['RUNNING','POLL_RESERVED','DISPATCH_RESERVED','STOPPED'].includes(s.phase)
    ||!Number.isInteger(s.polls)||s.polls<0||s.polls>QRV1.maxPolls
    ||!Number.isInteger(s.dispatch_attempts)||s.dispatch_attempts<0||s.dispatch_attempts>1
    ||!Number.isInteger(s.fetch_budget_reserved)||s.fetch_budget_reserved<0||s.fetch_budget_reserved>QRV1.maxFetches
    ||!Number.isFinite(Date.parse(s.started_at_utc))||!Number.isFinite(Date.parse(s.deadline_utc))
    ||!Number.isFinite(s.body_ms)||s.body_ms<0)throw new Error('QRV1_INVALID_STATE');
 return s;
}
function quoteRescueLiveStopTriggers_() {
 ScriptApp.getProjectTriggers().filter(t=>t.getHandlerFunction()===QRV1.handler).forEach(t=>ScriptApp.deleteTrigger(t));
}
function quoteRescueLiveRequest_(url,method,properties,record,payload) {
 const rawBase='https://raw.githubusercontent.com/'+QRV1.repo+'/main/data/direction_v42/';
 const apiBase='https://api.github.com/repos/'+QRV1.repo+'/actions/';
 const allowed=method==='get'?[rawBase+'forward_registry.csv',rawBase+'forward_scores.csv',apiBase+'runs?branch=main&per_page=30']:
  method==='post'?[apiBase+'workflows/quote_only_rescue.yml/dispatches']:[];
 if(!allowed.includes(url))throw new Error('QRV1_URL_NOT_ALLOWED');
 const headers={'Accept':'application/vnd.github+json','Cache-Control':'no-cache','X-GitHub-Api-Version':'2026-03-10'};
 if(url.startsWith('https://api.github.com/')) {
  const token=properties.getProperty(method==='post'?'GH_QUOTE_RESCUE_DISPATCH_TOKEN':'GH_ACTIONS_READ_TOKEN');
  if(!token||!token.trim())throw new Error('QRV1_CREDENTIAL_MISSING');
  headers.Authorization='Bearer '+token.trim();
 }
 const options={method:method,muteHttpExceptions:true,followRedirects:false,headers:headers};
 if(method==='post'){options.contentType='application/json';options.payload=JSON.stringify(payload);}
 const started=Date.now();
 if(record.deadline_ms!==undefined&&(started>=record.deadline_ms||started-Date.parse(record.started_at_utc)>=record.remaining_body_ms))throw new Error('QRV1_BUDGET_CLOSED');
 if(method==='post'&&record.witness_deadline_ms!==undefined&&started>record.witness_deadline_ms)throw new Error('QRV1_TARGET_WINDOW_CLOSED');
 if(method==='post')record.dispatch_calls++;
 record.fetch_calls++;
 const response=UrlFetchApp.fetch(url,options);record.request_ms.push(Date.now()-started);
 const code=response.getResponseCode();
 if(method==='get'&&code!==200)throw new Error('QRV1_READ_HTTP_'+code);
 const responseHeaders=response.getAllHeaders();
 if(url.startsWith('https://api.github.com/')) {
  record.rate_limit={};Object.keys(responseHeaders).filter(k=>/^x-ratelimit-(limit|remaining|reset)$/i.test(k)).forEach(k=>record.rate_limit[k]=String(responseHeaders[k]));
 }
 return {code:code,text:response.getContentText('UTF-8')};
}
function quoteRescueLiveCsv_(text,required) {
 const rows=Utilities.parseCsv(text.trim());const header=rows.shift();
 if(!header||new Set(header).size!==header.length||required.some(k=>!header.includes(k)))throw new Error('QRV1_CSV_SCHEMA');
 return rows.filter(r=>r.some(x=>x!=='')).map(fields=>{
  if(fields.length!==header.length)throw new Error('QRV1_CSV_ROW');
  const row={};header.forEach((name,i)=>row[name]=fields[i]);return row;
 });
}
function quoteRescueLiveRead_(properties,record) {
 const base='https://raw.githubusercontent.com/'+QRV1.repo+'/main/data/direction_v42/';
 const registry=quoteRescueLiveCsv_(quoteRescueLiveRequest_(base+'forward_registry.csv','get',properties,record).text,['forecast_id','issued_at_utc','target_utc']);
 const scores=quoteRescueLiveCsv_(quoteRescueLiveRequest_(base+'forward_scores.csv','get',properties,record).text,['forecast_id','status']);
 const runs=JSON.parse(quoteRescueLiveRequest_('https://api.github.com/repos/'+QRV1.repo+'/actions/runs?branch=main&per_page=30','get',properties,record).text).workflow_runs;
 if(!Array.isArray(runs))throw new Error('QRV1_RUN_SCHEMA');
 return {registry:registry,scores:scores,runs:runs};
}
function quoteRescueLiveDecide_(data,now) {
 const seen=new Set(),scored=new Set(),due=[];
 data.scores.forEach(r=>{if(!r.forecast_id||scored.has(r.forecast_id)||!r.status)throw new Error('QRV1_SCORE_SCHEMA');scored.add(r.forecast_id);});
 data.registry.forEach(r=>{
  const target=Date.parse(r.target_utc),issued=Date.parse(r.issued_at_utc);
  if(!r.forecast_id||r.forecast_id.length>200||seen.has(r.forecast_id)||!Number.isFinite(target)||!Number.isFinite(issued)||issued>now)throw new Error('QRV1_REGISTRY_SCHEMA');
  seen.add(r.forecast_id);if(!scored.has(r.forecast_id)&&target<=now&&now<=target+20*60000)due.push(r.forecast_id);
 });
 if([...scored].some(id=>!seen.has(id)))return {decision:'SKIP_INCONSISTENT_READ',due:[]};
 if(!due.length)return {decision:'SKIP_NO_OPEN_TARGET',due:[]};
 const paths=['.github/workflows/okx_hourly_extreme_history.yml','.github/workflows/quote_only_rescue.yml'];
 if(data.runs.some(r=>paths.includes(r.path)&&r.head_branch==='main'&&['queued','pending','requested','waiting','in_progress'].includes(r.status)))return {decision:'SKIP_EXISTING_RUN',due:due};
 // This advisory run list is bounded. The shared server-side lock and fresh
 // main checkout remain authoritative even if an old queued run is omitted.
 return {decision:'REQUEST_QUOTE_ONLY_WORKFLOW',due:due};
}
function startQuoteRescueLiveValidation() {
 const lock=LockService.getScriptLock();if(!lock.tryLock(0))throw new Error('QRV1_BUSY');
 try {
  const p=PropertiesService.getScriptProperties();
  if(p.getProperty('QRV1_VALIDATION_READY')!==QRV1.approved)throw new Error('QRV1_DEPLOYMENT_NOT_REVIEWED');
  if(p.getProperty(QRV1.startedKey)||p.getProperty(QRV1.stateKey)||ScriptApp.getProjectTriggers().some(t=>t.getHandlerFunction()===QRV1.handler))throw new Error('QRV1_ALREADY_STARTED');
  for(const key of ['GH_ACTIONS_READ_TOKEN','GH_QUOTE_RESCUE_DISPATCH_TOKEN'])if(!(p.getProperty(key)||'').trim())throw new Error('QRV1_CREDENTIAL_MISSING');
  const now=Date.now();const state={phase:'RUNNING',decision:'STARTED',started_at_utc:new Date(now).toISOString(),deadline_utc:new Date(now+QRV1.maxWallMs).toISOString(),polls:0,fetch_budget_reserved:0,fetch_calls:0,dispatch_calls:0,dispatch_attempts:0,body_ms:0,body_budget_reserved_ms:0};
  quoteRescueLiveSave_(p,state);p.setProperty(QRV1.startedKey,state.started_at_utc);
  try{ScriptApp.newTrigger(QRV1.handler).timeBased().everyMinutes(5).create();}
  catch(e){state.phase='STOPPED';state.decision='TRIGGER_CREATE_ERROR';quoteRescueLiveStopTriggers_();quoteRescueLiveSave_(p,state);throw new Error('QRV1_TRIGGER_CREATE_ERROR');}
  console.log(JSON.stringify({kind:'LIVE_VALIDATION_STARTED',state:state}));
 }finally{lock.releaseLock();}
}
function runQuoteRescueLiveValidation(event) {
 if(!event||!event.triggerUid)throw new Error('QRV1_TIMED_EVENT_REQUIRED');
 const lock=LockService.getScriptLock();if(!lock.tryLock(0)){console.log(JSON.stringify({kind:'LIVE_VALIDATION',decision:'SKIP_OVERLAP'}));return;}
 const started=Date.now(),p=PropertiesService.getScriptProperties();let state=null;
 const record={kind:'LIVE_VALIDATION',started_at_utc:new Date(started).toISOString(),fetch_calls:0,dispatch_calls:0,request_ms:[]};
 try {
  state=quoteRescueLiveLoad_(p);
  if(state.phase==='POLL_RESERVED'||state.phase==='DISPATCH_RESERVED'){
   state.interrupted_execution=true;state.body_ms+=state.body_budget_reserved_ms||QRV1.maxExecutionMs;
   state.body_budget_reserved_ms=0;state.phase='STOPPED';state.decision='STOP_INTERRUPTED_EXECUTION';return;
  }
  if(state.phase!=='RUNNING'||state.dispatch_attempts||state.polls>=QRV1.maxPolls||started+QRV1.maxExecutionMs>Date.parse(state.deadline_utc)||state.body_ms+QRV1.maxExecutionMs>QRV1.maxBodyMs||state.fetch_budget_reserved+3>QRV1.maxFetches){state.phase='STOPPED';return;}
  record.deadline_ms=Date.parse(state.deadline_utc);record.remaining_body_ms=QRV1.maxBodyMs-state.body_ms;
  state.polls++;state.fetch_budget_reserved+=3;state.phase='POLL_RESERVED';state.body_budget_reserved_ms=QRV1.maxExecutionMs;quoteRescueLiveSave_(p,state);
  const data=quoteRescueLiveRead_(p,record);
  state.phase='RUNNING';
  if(Date.now()>=Date.parse(state.deadline_utc)){state.phase='STOPPED';state.decision='STOP_DEADLINE';return;}
  const verdict=quoteRescueLiveDecide_(data,Date.now());state.decision=verdict.decision;
  state.due_count=verdict.due.length;state.due_sample=verdict.due.slice(0,4).map(id=>id.slice(0,100));
  if(verdict.decision==='REQUEST_QUOTE_ONLY_WORKFLOW') {
   if(state.fetch_budget_reserved+1>QRV1.maxFetches)throw new Error('QRV1_FETCH_BUDGET');
   // Durable intent precedes POST. Crash/timeout/ambiguous HTTP results must
   // never cause an automatic second attempt or run a different workflow.
   state.phase='DISPATCH_RESERVED';state.dispatch_attempts=1;state.fetch_budget_reserved++;
   record.witness_deadline_ms=Math.max(...data.registry.filter(r=>verdict.due.slice(0,100).includes(r.forecast_id)).map(r=>Date.parse(r.target_utc)+20*60000));
   state.dispatch_reserved_at_utc=new Date().toISOString();quoteRescueLiveSave_(p,state);
   const response=quoteRescueLiveRequest_('https://api.github.com/repos/'+QRV1.repo+'/actions/workflows/quote_only_rescue.yml/dispatches','post',p,record,{ref:'main',inputs:{expected_ids_json:JSON.stringify(verdict.due.slice(0,100))}});
   state.decision='DISPATCH_RESULT_UNKNOWN';
   if(response.code===204)state.decision='DISPATCH_ACCEPTED_AWAIT_EVIDENCE';
   else if(response.code===200){try{const id=JSON.parse(response.text).workflow_run_id;if(Number.isSafeInteger(id)&&id>0){state.run_id=id;state.decision='DISPATCH_ACCEPTED_AWAIT_EVIDENCE';}}catch(e){/* Never log an untrusted response body. */}}
   else state.decision='DISPATCH_HTTP_'+response.code;
   state.phase='STOPPED';
  }
 }catch(e){
  const code=/^QRV1_[A-Z0-9_]+$/.test(e.message||'')?e.message:'QRV1_UNEXPECTED_ERROR';
  record.error_code=code;if(state){state.phase='STOPPED';state.decision=code;}
 }finally{
  record.elapsed_ms=Date.now()-started;
  if(state){state.body_ms+=record.elapsed_ms;state.body_budget_reserved_ms=0;state.fetch_calls+=record.fetch_calls;state.dispatch_calls+=record.dispatch_calls;
   if(state.polls>=QRV1.maxPolls||state.body_ms>=QRV1.maxBodyMs)state.phase='STOPPED';
  }
  if(!state||state.phase!=='RUNNING'){try{quoteRescueLiveStopTriggers_();}catch(e){record.trigger_cleanup_error=true;}}
  if(state){try{quoteRescueLiveSave_(p,state);}catch(e){record.persistence_error=true;try{quoteRescueLiveStopTriggers_();}catch(e2){record.trigger_cleanup_error=true;}}}
  record.state=state;console.log(JSON.stringify(record));lock.releaseLock();
 }
}
function previewQuoteRescueLiveRead() {
 const record={kind:'LIVE_READ_ONLY_PREVIEW',fetch_calls:0,dispatch_calls:0,request_ms:[]};const started=Date.now();
 try{const data=quoteRescueLiveRead_(PropertiesService.getScriptProperties(),record);const result=quoteRescueLiveDecide_(data,Date.now());record.decision=result.decision;record.due_count=result.due.length;}
 catch(e){record.error_code=/^QRV1_[A-Z0-9_]+$/.test(e.message||'')?e.message:'QRV1_UNEXPECTED_ERROR';}
 record.elapsed_ms=Date.now()-started;console.log(JSON.stringify(record));
}
function showQuoteRescueLiveValidation() {
 console.log(JSON.stringify(quoteRescueLiveLoad_(PropertiesService.getScriptProperties())));
}
function stopQuoteRescueLiveValidation() {
 const lock=LockService.getScriptLock();if(!lock.tryLock(0))throw new Error('QRV1_BUSY');
 try{quoteRescueLiveStopTriggers_();const p=PropertiesService.getScriptProperties();if(p.getProperty(QRV1.stateKey)){const s=quoteRescueLiveLoad_(p);s.phase='STOPPED';s.decision='STOPPED_EXPLICITLY';quoteRescueLiveSave_(p,s);}}
 finally{lock.releaseLock();}
}

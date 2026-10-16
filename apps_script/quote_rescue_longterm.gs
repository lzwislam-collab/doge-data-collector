/** Independent long-term caller. Loading never starts timers or sends email.
 * Requires the unchanged quote_rescue_live.gs CSV/decision helpers.
 * All counters are local, conservative budgets, not account quota measurements.
 */
const QLT1=Object.freeze({repo:'lzwislam-collab/doge-data-collector',owner:'lzwislam@gmail.com',
 handler:'runQuoteRescueLongTerm',watchdog:'watchQuoteRescueLongTerm',stateKey:'QLT1_STATE',
 approved:'QLT1_REVIEW_20261016',maxFetch:1200,maxDispatch:48,maxBody:30*60000,
 reserveBody:6*60000,maxTargets:8,staleMs:15*60000,maxMail:3});
function qltSave_(p,s){
 const text=JSON.stringify(s);
 if(encodeURIComponent(text).replace(/%[0-9A-F]{2}|./g,'x').length>=7800)throw new Error('QLT1_STATE_TOO_LARGE');
 p.setProperty(QLT1.stateKey,text);
}
function qltLoad_(p){
 const s=JSON.parse(p.getProperty(QLT1.stateKey)||'null');
 if(!s||s.version!==1||!['RUNNING','WAIT_RUN','POLL_RESERVED','DISPATCH_RESERVED','STOPPED'].includes(s.phase)
  ||!Number.isFinite(s.started_ms)||!Number.isFinite(s.last_clock_ms)||!Number.isFinite(s.last_poll_ms)
  ||!Array.isArray(s.budget)||s.budget.length>26||!Array.isArray(s.attempted)||s.attempted.length>32
  ||!Number.isFinite(s.reserved_body_ms)||s.reserved_body_ms<0)throw new Error('QLT1_INVALID_STATE');
 const hours=new Set();s.budget.forEach(b=>{if(!Array.isArray(b)||b.length!==5||b.some(n=>!Number.isSafeInteger(n)||n<0)||hours.has(b[0]))throw new Error('QLT1_INVALID_BUDGET');hours.add(b[0]);});
 s.attempted.forEach(a=>{if(!Array.isArray(a)||a.length!==2||!(/^[0-9a-f]{64}$/).test(a[0])||!Number.isFinite(a[1]))throw new Error('QLT1_INVALID_ATTEMPT');});
 if(s.inflight&&(!Number.isSafeInteger(s.inflight.id)||s.inflight.id<=0||!Array.isArray(s.inflight.targets)||!s.inflight.targets.length||s.inflight.targets.length>QLT1.maxTargets||!Number.isFinite(s.inflight.requested_ms)
  ||s.inflight.targets.some(t=>!t||typeof t.id!=='string'||t.id.length>200||!Number.isFinite(t.target_ms))))throw new Error('QLT1_INVALID_RUN');
 if(s.phase==='WAIT_RUN'&&!s.inflight)throw new Error('QLT1_INVALID_RUN');
 return s;
}
function qltHour_(s,now){
 const hour=Math.floor(now/3600000);
 // Charge by execution-start hour; a six-minute execution can cross into
 // the next hour. Retain that extra hour too (at most 26 hours), so no later
 // request disappears while it is still inside the last 24 hours.
 s.budget=s.budget.filter(b=>b[0]>=hour-25);
 let row=s.budget.find(b=>b[0]===hour);if(!row){row=[hour,0,0,0,0];s.budget.push(row);}return row;
}
function qltTotal_(s,column){return s.budget.reduce((n,b)=>n+b[column],0);}
function qltOwned_(){return ScriptApp.getProjectTriggers().filter(t=>[QLT1.handler,QLT1.watchdog].includes(t.getHandlerFunction()));}
function qltDelete_(){let failed=false;qltOwned_().forEach(t=>{try{ScriptApp.deleteTrigger(t);}catch(e){failed=true;}});if(failed)throw new Error('QLT1_TRIGGER_DELETE_ERROR');}
function qltError_(e){return /^QLT1_[A-Z0-9_]+$/.test(e&&e.message||'')?e.message:'QLT1_UNEXPECTED_ERROR';}
function qltNotify_(p,s,code,includedInExecution){
 if(s.notice&&['RESERVED','UNCERTAIN'].includes(s.notice.status))return false;
 if(s.notice&&s.notice.key===code)return s.notice.status==='SENT';
 const now=Date.now(),row=qltHour_(s,now);
 if(qltTotal_(s,4)>=QLT1.maxMail){s.notice={key:code,status:'NO_QUOTA'};qltSave_(p,s);return false;}
 let ownReservation=false;
 if(!includedInExecution){
  if(qltTotal_(s,3)+QLT1.reserveBody>QLT1.maxBody){s.notice={key:code,status:'NO_RUNTIME_BUDGET'};qltSave_(p,s);return false;}
  const reserve=JSON.parse(p.getProperty('QLT1_STOP_NOTICE_BUDGET')||'null');
  if(!reserve||reserve.status!=='AVAILABLE'||reserve.reserved_ms!==QLT1.reserveBody){s.notice={key:code,status:'NO_RUNTIME_BUDGET'};qltSave_(p,s);return false;}
  p.setProperty('QLT1_STOP_NOTICE_BUDGET',JSON.stringify({status:'RESERVED',reserved_ms:QLT1.reserveBody}));
  row[3]+=QLT1.reserveBody;ownReservation=true;qltSave_(p,s);
 }
 row[4]++;s.notice={key:code,status:'RESERVED'};qltSave_(p,s);
 let status='SENT';
 try{if(MailApp.getRemainingDailyQuota()<1)status='NO_QUOTA';else MailApp.sendEmail(QLT1.owner,'DOGE 独立补采提醒：'+code,
  '独立补采状态：'+s.phase+'\n原因：'+code+'\nUTC：'+new Date(now).toISOString()+'\n'
  +(s.inflight?'运行：https://github.com/'+QLT1.repo+'/actions/runs/'+s.inflight.id+'\n':'')
  +'异常停止后不会自动恢复。正常采集、冻结规则和旧证据未修改。\n此邮件不代表真实补采证据已经验收。');}
 catch(e){status='UNCERTAIN';}
 s.notice.status=status;
 if(ownReservation){row[3]+=Math.max(Date.now()-now,1000)-QLT1.reserveBody;p.setProperty('QLT1_STOP_NOTICE_BUDGET',JSON.stringify({status:status,reserved_ms:0}));}
 qltSave_(p,s);return status==='SENT';
}
function qltEmergency_(p,code){
 // Independent one-shot outbox preserves corrupt original state. Never resend
 // when an email result or persistence result is unknown.
 try{if(p.getProperty('QLT1_EMERGENCY_MAIL'))return;
  let durable=null;try{durable=JSON.parse(p.getProperty(QLT1.stateKey)||'null');}catch(e){/* Preserve unreadable state. */}
  if(durable&&durable.notice&&(['RESERVED','UNCERTAIN'].includes(durable.notice.status)||(durable.phase==='STOPPED'&&durable.notice.status==='SENT')))return;
  if(durable&&Array.isArray(durable.budget)&&durable.budget.every(b=>Array.isArray(b)&&Number.isFinite(b[3]))
   &&durable.budget.reduce((n,b)=>n+b[3],0)+QLT1.reserveBody>QLT1.maxBody)return;
  const reserve=JSON.parse(p.getProperty('QLT1_STOP_NOTICE_BUDGET')||'null');
  if(!reserve||reserve.status!=='AVAILABLE'||reserve.reserved_ms!==QLT1.reserveBody)return;
  p.setProperty('QLT1_STOP_NOTICE_BUDGET',JSON.stringify({status:'RESERVED',reserved_ms:QLT1.reserveBody}));
  p.setProperty('QLT1_EMERGENCY_MAIL','RESERVED');
  if(MailApp.getRemainingDailyQuota()<1)return;
  MailApp.sendEmail(QLT1.owner,'DOGE 独立补采已停止：'+code,'独立补采因 '+code+' 停止。原始状态保留，请检查项目执行记录；不会自动重试或恢复。正常程序及冻结规则未修改。');
  p.setProperty('QLT1_EMERGENCY_MAIL','SENT');
 }catch(e){console.log(JSON.stringify({kind:'LONGTERM_EMERGENCY',notification:'UNCONFIRMED',code:code}));}
}
function qltStop_(p,s,code,includedInExecution){
 s.phase='STOPPED';s.decision=code;
 let cleanup=false;try{qltDelete_();}catch(e){cleanup=true;}
 if(cleanup)s.cleanup_error=true;
 qltSave_(p,s);qltNotify_(p,s,code,!!includedInExecution);
}
function qltCredentials_(p,s,starting){
 if(p.getProperty('QLT1_DEPLOYMENT_READY')!==QLT1.approved||QRV1.approved!=='PR54:16a7eeef4b284f5ba62e4742f73f0be6e50792e3')throw new Error('QLT1_NOT_REVIEWED');
 if(p.getProperty('QLT1_MAIL_ENABLED')!=='true')throw new Error('QLT1_MAIL_NOT_ENABLED');
 let nearest=Infinity;
 for(const kind of ['READ','DISPATCH']){
  const token=p.getProperty(kind==='READ'?'GH_ACTIONS_READ_TOKEN':'GH_QUOTE_RESCUE_DISPATCH_TOKEN');
  const expiry=Date.parse(p.getProperty('QLT1_'+kind+'_TOKEN_EXPIRES_UTC')||'');
  if(!token||!token.trim()||!Number.isFinite(expiry))throw new Error('QLT1_CREDENTIAL_MISSING');
  nearest=Math.min(nearest,expiry);
 }
 const now=Date.now();if(nearest-now<=(starting?24*3600000:QLT1.reserveBody))throw new Error('QLT1_CREDENTIAL_EXPIRING');
 if(!starting&&nearest-now<=24*3600000&&!s.expiry_warning_sent){s.expiry_warning_sent=true;qltSave_(p,s);if(!qltNotify_(p,s,'CREDENTIAL_EXPIRING_WITHIN_24H',true))throw new Error('QLT1_NOTIFICATION_FAILED');}
}
function qltRequest_(p,s,r,url,method,payload){
 const raw='https://raw.githubusercontent.com/'+QLT1.repo+'/main/data/direction_v42/';
 const api='https://api.github.com/repos/'+QLT1.repo+'/actions/';
 const allowed=method==='get'?[raw+'forward_registry.csv',raw+'forward_scores.csv',api+'runs?branch=main&per_page=30'].concat(s.inflight?[api+'runs/'+s.inflight.id]:[]):method==='post'?[api+'workflows/quote_only_rescue.yml/dispatches']:[];
 if(!allowed.includes(url))throw new Error('QLT1_URL_NOT_ALLOWED');
 const now=Date.now();if(now>=r.deadline_ms)throw new Error('QLT1_EXECUTION_BUDGET');
 if(method==='post'&&now>r.witness_deadline_ms)throw new Error('QLT1_TARGET_WINDOW_CLOSED');
 if(r.fetch_calls>=r.reserved_fetch)throw new Error('QLT1_REQUEST_RESERVATION');
 const headers={Accept:'application/vnd.github+json','Cache-Control':'no-cache','X-GitHub-Api-Version':'2026-03-10'};
 if(url.startsWith('https://api.github.com/')){
  const token=p.getProperty(method==='post'?'GH_QUOTE_RESCUE_DISPATCH_TOKEN':'GH_ACTIONS_READ_TOKEN');
  if(!(token||'').trim())throw new Error('QLT1_CREDENTIAL_MISSING');headers.Authorization='Bearer '+token.trim();
 }
 const options={method:method,headers:headers,muteHttpExceptions:true,followRedirects:false};
 if(method==='post'){options.contentType='application/json';options.payload=JSON.stringify(payload);}
 r.fetch_calls++;if(method==='post')r.dispatch_calls++;
 const response=UrlFetchApp.fetch(url,options),code=response.getResponseCode();r.request_ms.push(Date.now()-now);
 if(method==='get'&&code!==200)throw new Error('QLT1_READ_HTTP_'+code);
 const all=response.getAllHeaders();
 for(const key of Object.keys(all)){if(key.toLowerCase()==='x-ratelimit-remaining'&&Number.isFinite(Number(all[key]))){r.api_remaining=Number(all[key]);if(method==='get'&&r.api_remaining<100)throw new Error('QLT1_API_QUOTA_LOW');}}
 return {code:code,text:response.getContentText('UTF-8')};
}
function qltHash_(id){return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,id,Utilities.Charset.UTF_8).map(b=>(b&255).toString(16).padStart(2,'0')).join('');}
function qltRead_(p,s,r){
 const raw='https://raw.githubusercontent.com/'+QLT1.repo+'/main/data/direction_v42/';
 const data={registry:quoteRescueLiveCsv_(qltRequest_(p,s,r,raw+'forward_registry.csv','get').text,['forecast_id','issued_at_utc','target_utc']),
  scores:quoteRescueLiveCsv_(qltRequest_(p,s,r,raw+'forward_scores.csv','get').text,['forecast_id','status']),
  runs:JSON.parse(qltRequest_(p,s,r,'https://api.github.com/repos/'+QLT1.repo+'/actions/runs?branch=main&per_page=30','get').text).workflow_runs};
 if(data.scores.some(row=>!['scored','unscorable_missing_target_quote'].includes(row.status)))throw new Error('QLT1_SCORE_STATUS');
 return data;
}
function qltMonitorRun_(p,s,r){
 const active=s.inflight;
 const run=JSON.parse(qltRequest_(p,s,r,'https://api.github.com/repos/'+QLT1.repo+'/actions/runs/'+active.id,'get').text);
 if(run.id!==active.id||run.path!=='.github/workflows/quote_only_rescue.yml'||run.event!=='workflow_dispatch'||run.head_branch!=='main'
  ||!Number.isFinite(Date.parse(run.created_at))||Date.parse(run.created_at)<active.requested_ms-2000)throw new Error('QLT1_RUN_IDENTITY');
 if(run.status!=='completed'){
  if(!['queued','pending','requested','waiting','in_progress'].includes(run.status))throw new Error('QLT1_RUN_STATUS');
  if(Date.now()-active.requested_ms>10*60000||active.targets.every(t=>Date.now()>t.target_ms+20*60000))throw new Error('QLT1_RUN_TIMEOUT');
  s.phase='WAIT_RUN';s.decision='AWAIT_RUN_COMPLETION';return;
 }
 if(run.conclusion!=='success')throw new Error('QLT1_RUN_FAILED');
 const raw='https://raw.githubusercontent.com/'+QLT1.repo+'/main/data/direction_v42/forward_scores.csv';
 const scores=quoteRescueLiveCsv_(qltRequest_(p,s,r,raw,'get').text,['forecast_id','status','observed_at_utc','target_quote_utc']);
 for(const t of active.targets){
  const matches=scores.filter(row=>row.forecast_id===t.id),score=matches[0];
  if(matches.length!==1||score.status!=='scored'||!Number.isFinite(Date.parse(score.target_quote_utc))||!Number.isFinite(Date.parse(score.observed_at_utc))
   ||Date.parse(score.target_quote_utc)<t.target_ms||Date.parse(score.target_quote_utc)>t.target_ms+25*60000
   ||Date.parse(score.observed_at_utc)>t.target_ms+25*60000)throw new Error('QLT1_RESULT_NOT_SCORED');
 }
 s.last_completed_run_id=active.id;s.inflight=null;s.phase='RUNNING';s.decision='RUN_FINISHED_SCORES_PRESENT';
 // No email or raw-binding attestation: a green run plus CSV is only a health
 // check. The unchanged server entry owns full frozen-evidence validation.
}
function qltPoll_(p,s,r){
 if(s.inflight){qltMonitorRun_(p,s,r);return;}
 const data=qltRead_(p,s,r);if(!Array.isArray(data.runs))throw new Error('QLT1_RUN_SCHEMA');
 const now=Date.now(),verdict=quoteRescueLiveDecide_(data,now);
 if(verdict.decision==='SKIP_INCONSISTENT_READ')throw new Error('QLT1_INCONSISTENT_READ');
 s.phase='RUNNING';s.decision=verdict.decision;
 if(verdict.decision!=='REQUEST_QUOTE_ONLY_WORKFLOW')return;
 s.attempted=s.attempted.filter(a=>a[1]>=now);
 const due=verdict.due.filter(id=>!s.attempted.some(a=>a[0]===qltHash_(id)));
 if(!due.length){s.decision='SKIP_ALREADY_REQUESTED';return;}
 if(due.length>QLT1.maxTargets)throw new Error('QLT1_TOO_MANY_TARGETS');
 if(qltTotal_(s,2)>=QLT1.maxDispatch)throw new Error('QLT1_DISPATCH_LIMIT');
 const targets=due.map(id=>({id:id,target_ms:Date.parse(data.registry.find(row=>row.forecast_id===id).target_utc)}));
 targets.forEach(t=>s.attempted.push([qltHash_(t.id),t.target_ms+25*60000]));
 if(s.attempted.length>32)throw new Error('QLT1_ATTEMPT_LIMIT');
 s.phase='DISPATCH_RESERVED';qltHour_(s,now)[2]++;s.dispatch_reserved_ms=now;
 r.witness_deadline_ms=Math.min(...targets.map(t=>t.target_ms+20*60000));
 qltSave_(p,s);
 const response=qltRequest_(p,s,r,'https://api.github.com/repos/'+QLT1.repo+'/actions/workflows/quote_only_rescue.yml/dispatches','post',
  {ref:'main',inputs:{expected_ids_json:JSON.stringify(due)}});
 let id=null;if(response.code===200){try{id=JSON.parse(response.text).workflow_run_id;}catch(e){/* Untrusted body never logged. */}}
 if(!Number.isSafeInteger(id)||id<=0)throw new Error('QLT1_DISPATCH_UNKNOWN');
 s.inflight={id:id,requested_ms:now,targets:targets};s.phase='WAIT_RUN';s.decision='DISPATCH_ACCEPTED_AWAIT_RUN';
}
function qltExecute_(event,isWatch){
 if(!event||!event.triggerUid)throw new Error('QLT1_TIMED_EVENT_REQUIRED');
 const lock=LockService.getScriptLock();if(!lock.tryLock(0))return;
 const p=PropertiesService.getScriptProperties(),started=Date.now();let s=null,admitted=false,reserveHour=null;
 const r={kind:'LONGTERM',watchdog:isWatch,started_at_utc:new Date(started).toISOString(),fetch_calls:0,dispatch_calls:0,request_ms:[],reserved_fetch:0,deadline_ms:started+QLT1.reserveBody};
 try{
  s=qltLoad_(p);if(s.phase==='STOPPED'){try{qltDelete_();delete s.cleanup_error;}catch(e){s.cleanup_error=true;r.trigger_cleanup_error=true;}return;}
  if(started<s.last_clock_ms)throw new Error('QLT1_CLOCK_REVERSED');
  if(['POLL_RESERVED','DISPATCH_RESERVED'].includes(s.phase))throw new Error('QLT1_INTERRUPTED_EXECUTION');
  const row=qltHour_(s,started);
  r.reserved_fetch=isWatch?0:s.inflight?2:4;
  // Keep another full execution available for the one-shot stop/emergency
  // notice, including when normal poll admission is denied.
  if(qltTotal_(s,1)+r.reserved_fetch>QLT1.maxFetch||qltTotal_(s,3)+2*QLT1.reserveBody>QLT1.maxBody)throw new Error('QLT1_ROLLING_BUDGET');
  reserveHour=row[0];row[1]+=r.reserved_fetch;row[3]+=QLT1.reserveBody;s.reserved_body_ms=QLT1.reserveBody;s.phase='POLL_RESERVED';s.last_clock_ms=started;qltSave_(p,s);admitted=true;
  qltCredentials_(p,s,false);
  if(isWatch){
   if(!qltOwned_().some(t=>t.getHandlerFunction()===QLT1.handler)||started-s.last_poll_ms>QLT1.staleMs)throw new Error('QLT1_MAIN_TIMER_STALE');
   s.phase=s.inflight?'WAIT_RUN':'RUNNING';s.decision='HEALTHY';
  }else{s.last_poll_ms=started;qltPoll_(p,s,r);}
 }catch(e){
  r.error_code=qltError_(e);
  try{if(s)qltStop_(p,s,r.error_code,admitted);else{qltDelete_();qltEmergency_(p,r.error_code);}}
  catch(e2){r.persistence_or_cleanup_error=true;try{qltDelete_();}catch(e3){r.trigger_cleanup_error=true;}qltEmergency_(p,'QLT1_PERSISTENCE_ERROR');}
 }finally{
  r.elapsed_ms=Date.now()-started;
  if(s){
   if(admitted){const row=s.budget.find(b=>b[0]===reserveHour);if(row){row[1]-=r.reserved_fetch-r.fetch_calls;row[3]+=Math.max(r.elapsed_ms,isWatch?1000:3000)-QLT1.reserveBody;}s.reserved_body_ms=0;}
   try{qltSave_(p,s);}catch(e){r.persistence_error=true;try{qltDelete_();}catch(e2){r.trigger_cleanup_error=true;}qltEmergency_(p,'QLT1_PERSISTENCE_ERROR');}
   r.phase=s.phase;r.decision=s.decision;r.run_id=s.inflight?s.inflight.id:s.last_completed_run_id||null;
   r.budget_fetch=qltTotal_(s,1);r.budget_dispatch=qltTotal_(s,2);r.budget_body_ms=qltTotal_(s,3);
  }
  console.log(JSON.stringify(r));lock.releaseLock();
 }
}
function runQuoteRescueLongTerm(event){qltExecute_(event,false);}
function watchQuoteRescueLongTerm(event){qltExecute_(event,true);}
function startQuoteRescueLongTerm(){
 const lock=LockService.getScriptLock();if(!lock.tryLock(0))throw new Error('QLT1_BUSY');
 try{
  const p=PropertiesService.getScriptProperties();
  if(p.getProperty(QLT1.stateKey)||p.getProperty('QLT1_STARTED')||qltOwned_().length)throw new Error('QLT1_ALREADY_STARTED');
  if(ScriptApp.getProjectTriggers().some(t=>t.getHandlerFunction()===QRV1.handler))throw new Error('QLT1_VALIDATION_STILL_RUNNING');
  qltCredentials_(p,null,true);if(MailApp.getRemainingDailyQuota()<1)throw new Error('QLT1_MAIL_NO_QUOTA');
  const now=Date.now(),s={version:1,phase:'RUNNING',decision:'STARTED',started_ms:now,last_clock_ms:now,last_poll_ms:now,budget:[],attempted:[],inflight:null,reserved_body_ms:0,notice:null};
  qltSave_(p,s);p.setProperty('QLT1_STARTED',new Date(now).toISOString());
  p.setProperty('QLT1_STOP_NOTICE_BUDGET',JSON.stringify({status:'AVAILABLE',reserved_ms:QLT1.reserveBody}));
  try{ScriptApp.newTrigger(QLT1.handler).timeBased().everyMinutes(5).create();ScriptApp.newTrigger(QLT1.watchdog).timeBased().everyMinutes(15).create();}
  catch(e){qltStop_(p,s,'QLT1_TRIGGER_CREATE_ERROR');throw new Error('QLT1_TRIGGER_CREATE_ERROR');}
  console.log(JSON.stringify({kind:'LONGTERM_STARTED',started_at_utc:new Date(now).toISOString(),timers:2}));
 }finally{lock.releaseLock();}
}
function showQuoteRescueLongTerm(){console.log(JSON.stringify(qltLoad_(PropertiesService.getScriptProperties())));}
function stopQuoteRescueLongTerm(){
 const lock=LockService.getScriptLock();if(!lock.tryLock(0))throw new Error('QLT1_BUSY');
 try{const p=PropertiesService.getScriptProperties(),s=qltLoad_(p);qltStop_(p,s,'QLT1_STOPPED_EXPLICITLY');}finally{lock.releaseLock();}
}
function testQuoteRescueLongTermNotification(){
 const lock=LockService.getScriptLock();if(!lock.tryLock(0))throw new Error('QLT1_BUSY');
 try{const p=PropertiesService.getScriptProperties();
  if(p.getProperty('QLT1_MAIL_TEST'))throw new Error('QLT1_MAIL_TEST_ALREADY_ATTEMPTED');
  if(p.getProperty('QLT1_MAIL_ENABLED')!=='true'||MailApp.getRemainingDailyQuota()<1)throw new Error('QLT1_MAIL_NOT_READY');
  p.setProperty('QLT1_MAIL_TEST','RESERVED');
  MailApp.sendEmail(QLT1.owner,'DOGE 独立补采：通知测试','这是一封长期补采异常通知测试邮件。尚未启动长期补采；正常程序、冻结规则和旧证据未改动。');
  p.setProperty('QLT1_MAIL_TEST','SENT');console.log(JSON.stringify({kind:'LONGTERM_MAIL_TEST',status:'SENT'}));
 }finally{lock.releaseLock();}
}

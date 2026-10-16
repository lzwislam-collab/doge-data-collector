/* Synthetic Apps Script environment; no external network or real credentials. */
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const path=require('path').join(__dirname,'quote_rescue_live.gs');
const source=fs.existsSync(path)?fs.readFileSync(path,'utf8'):'';
const epoch=Date.parse('2026-10-16T04:00:00Z');
const repo='lzwislam-collab/doge-data-collector';
const handler='runQuoteRescueLiveValidation';
const approved='PR54:16a7eeef4b284f5ba62e4742f73f0be6e50792e3';
let passed=0;
function test(name,fn){try{fn();passed++;console.log('PASS '+name);}catch(e){console.error('FAIL '+name);throw e;}}
function fixture(options={}) {
 let now=epoch,locked=false;
 const props={GH_ACTIONS_READ_TOKEN:'test-read-secret',GH_QUOTE_RESCUE_DISPATCH_TOKEN:'test-dispatch-secret',QRV1_VALIDATION_READY:approved};
 const requests=[],logs=[],triggers=[{getHandlerFunction:()=> 'normal_unrelated_trigger'}];
 const table=rows=>rows.map(r=>r.join(',')).join('\n')+'\n';
 let registry=table([['forecast_id','issued_at_utc','target_utc'],['due','2026-10-16T02:50:00Z','2026-10-16T03:50:00Z']]);
 let scores='forecast_id,status\n';let runs=[];
 const store={getProperty:k=>props[k]??null,setProperty:(k,v)=>{options.onStore?.(k,v,{advance:ms=>{now+=ms;}});if(options.failStore?.(k,v,requests))throw new Error('storage failure');props[k]=v;}};
 class Clock extends Date {constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
 const context=vm.createContext({Date:Clock,console:{log:x=>logs.push(JSON.parse(x))},Utilities:{parseCsv:text=>text.trim().split(/\r?\n/).map(line=>line.split(','))},
  LockService:{getScriptLock:()=>({tryLock:()=>{if(locked)return false;locked=true;return true;},releaseLock:()=>{locked=false;}})},
  PropertiesService:{getScriptProperties:()=>store},
  ScriptApp:{getProjectTriggers:()=>triggers.slice(),deleteTrigger:t=>triggers.splice(triggers.indexOf(t),1),newTrigger:name=>({timeBased:()=>({everyMinutes:m=>({create:()=>{assert.equal(m,5);const t={getHandlerFunction:()=>name};triggers.push(t);return t;}})})})},
  UrlFetchApp:{fetch:(url,opts)=>{requests.push({url,opts});options.onFetch?.(url,opts,{advance:ms=>{now+=ms;}});
   if(options.throwFetch)throw new Error('network exception '+props.GH_QUOTE_RESCUE_DISPATCH_TOKEN);
   const isPost=opts.method==='post';const code=isPost?(options.postCode??204):(options.readCode??200);
   const body=isPost?(options.postBody??''):url.includes('registry.csv')?registry:url.includes('scores.csv')?scores:JSON.stringify({workflow_runs:runs});
   return {getResponseCode:()=>code,getContentText:()=>body,getAllHeaders:()=>({'x-ratelimit-remaining':'4990','x-ratelimit-limit':'5000'})};}}
 });
 vm.runInContext(source,context);
 const state=()=>JSON.parse(props.QRV1_STATE||'null');
 return {c:context,props,requests,logs,triggers,state,advance:ms=>{now+=ms;},setRegistry:v=>{registry=v;},setScores:v=>{scores=v;},setRuns:v=>{runs=v;},setLocked:v=>{locked=v;},start:()=>context.startQuoteRescueLiveValidation(),tick:()=>context.runQuoteRescueLiveValidation({triggerUid:'synthetic'}),liveTriggers:()=>triggers.filter(t=>t.getHandlerFunction()===handler)};
}
test('live entry exists but loading source performs no work',()=>{const f=fixture();assert.equal(typeof f.c.runQuoteRescueLiveValidation,'function');assert.equal(f.requests.length,0);assert(!f.props.QRV1_STATE);});
test('start requires explicit deployed and reviewed readiness marker',()=>{const f=fixture();delete f.props.QRV1_VALIDATION_READY;assert.throws(f.start);assert.equal(f.liveTriggers().length,0);assert.equal(f.requests.length,0);});
test('missing dispatch credential cannot silently reuse read credential',()=>{const f=fixture();delete f.props.GH_QUOTE_RESCUE_DISPATCH_TOKEN;assert.throws(f.start);assert.equal(f.liveTriggers().length,0);});
test('start once preserves trial evidence and unrelated trigger',()=>{const f=fixture();f.props.DRY_TRIAL_STATE='old-failed-evidence';f.start();assert.equal(f.liveTriggers().length,1);assert.equal(f.props.DRY_TRIAL_STATE,'old-failed-evidence');assert.throws(f.start);assert.equal(f.liveTriggers().length,1);});
test('manual live handler call cannot consume sample or dispatch',()=>{const f=fixture();f.start();assert.throws(()=>f.c.runQuoteRescueLiveValidation());assert.equal(f.state().polls,0);assert.equal(f.requests.length,0);});
test('only new quote-only workflow main may receive one POST then own trigger stops',()=>{const f=fixture();f.start();f.tick();const post=f.requests.filter(r=>r.opts.method==='post');assert.equal(post.length,1);assert.equal(post[0].url,'https://api.github.com/repos/'+repo+'/actions/workflows/quote_only_rescue.yml/dispatches');const data=JSON.parse(post[0].opts.payload);assert.equal(data.ref,'main');assert.deepEqual(JSON.parse(data.inputs.expected_ids_json),['due']);assert.equal(f.state().phase,'STOPPED');assert.equal(f.state().dispatch_attempts,1);assert.equal(f.state().decision,'DISPATCH_ACCEPTED_AWAIT_EVIDENCE');assert.equal(f.liveTriggers().length,0);f.tick();assert.equal(f.requests.length,4);assert.equal(f.triggers.length,1);});
test('GETs retain read token and only POST uses separate dispatch token',()=>{const f=fixture();f.start();f.tick();assert.equal(f.requests.length,4);for(const {url,opts} of f.requests){assert.equal(opts.followRedirects,false);assert.equal(opts.headers.Authorization,opts.method==='post'?'Bearer test-dispatch-secret':url.startsWith('https://api.github.com/')?'Bearer test-read-secret':undefined);}assert(!JSON.stringify(f.logs).includes('secret'));assert(!f.props.QRV1_STATE.includes('secret'));});
test('new GitHub 200 response retains only validated numeric run ID',()=>{const f=fixture({postCode:200,postBody:JSON.stringify({workflow_run_id:12345,html_url:'ignored'})});f.start();f.tick();assert.equal(f.state().run_id,12345);assert.equal(f.state().decision,'DISPATCH_ACCEPTED_AWAIT_EVIDENCE');});
test('malformed success response is ambiguous and never retried',()=>{const f=fixture({postCode:200,postBody:'unexpected-secret'});f.start();f.tick();assert.equal(f.state().decision,'DISPATCH_RESULT_UNKNOWN');f.tick();assert.equal(f.requests.length,4);assert(!JSON.stringify(f.logs).includes('unexpected-secret'));});
test('denied POST stops without widening permissions or repeating',()=>{const f=fixture({postCode:403});f.start();f.tick();assert.equal(f.state().decision,'DISPATCH_HTTP_403');f.tick();assert.equal(f.requests.length,4);assert.equal(f.liveTriggers().length,0);});
test('network exception is redacted and stops without retrying',()=>{const f=fixture({throwFetch:true});f.start();f.tick();assert.equal(f.liveTriggers().length,0);assert.equal(f.requests.length,1);assert(!JSON.stringify(f.logs).includes('secret'));});
test('existing missing score is never requested',()=>{const f=fixture();f.setScores('forecast_id,status\ndue,unscorable_missing_target_quote\n');f.start();f.tick();assert.equal(f.state().decision,'SKIP_NO_OPEN_TARGET');assert.equal(f.requests.length,3);});
for(const name of ['okx_hourly_extreme_history','quote_only_rescue'])test('active '+name+' run prevents duplicate request',()=>{const f=fixture();f.setRuns([{path:'.github/workflows/'+name+'.yml',head_branch:'main',status:'in_progress'}]);f.start();f.tick();assert.equal(f.state().decision,'SKIP_EXISTING_RUN');assert.equal(f.requests.length,3);});
test('time is rechecked after reads and old targets are skipped',()=>{const f=fixture({onFetch:(url,opts,api)=>{if(url.includes('actions/runs'))api.advance(11*60000);}});f.start();f.tick();assert.equal(f.state().decision,'SKIP_NO_OPEN_TARGET');assert.equal(f.requests.length,3);});
test('inconsistent or malformed input fails closed',()=>{const f=fixture();f.setScores('forecast_id,status\nunknown,scored\n');f.start();f.tick();assert.notEqual(f.state().decision,'DISPATCH_ACCEPTED_AWAIT_EVIDENCE');assert.equal(f.requests.length,3);});
test('12 genuine event polls without open targets auto-stop',()=>{const f=fixture();f.setScores('forecast_id,status\ndue,scored\n');f.start();for(let i=0;i<12;i++){f.advance(i===0?60000:5*60000);f.tick();}assert.equal(f.state().polls,12);assert.equal(f.state().fetch_calls,36);assert.equal(f.state().dispatch_attempts,0);assert.equal(f.liveTriggers().length,0);assert.equal(f.triggers.length,1);});
test('expired validation deadline stops before any request',()=>{const f=fixture();f.start();f.advance(66*60000);f.tick();assert.equal(f.requests.length,0);assert.equal(f.liveTriggers().length,0);});
test('late poll cannot start unless full execution fits wall deadline',()=>{const f=fixture();f.start();f.advance(64*60000);f.tick();assert.equal(f.requests.length,0);assert.equal(f.liveTriggers().length,0);});
test('script lock rejects overlap without requests',()=>{const f=fixture();f.start();f.setLocked(true);f.tick();assert.equal(f.requests.length,0);assert.equal(f.state().polls,0);});
test('dispatch reservation must persist before HTTP POST',()=>{const f=fixture({failStore:(k,v)=>k==='QRV1_STATE'&&JSON.parse(v).phase==='DISPATCH_RESERVED'});f.start();f.tick();assert.equal(f.requests.filter(r=>r.opts.method==='post').length,0);assert.equal(f.liveTriggers().length,0);});
test('reservation persistence delay cannot dispatch beyond the target reserve',()=>{const f=fixture({onStore:(k,v,api)=>{if(k==='QRV1_STATE'&&JSON.parse(v).phase==='DISPATCH_RESERVED')api.advance(11*60000);}});f.start();f.tick();assert.equal(f.requests.length,3);assert.equal(f.liveTriggers().length,0);});
test('full execution runtime must fit remaining body budget before reading',()=>{const f=fixture({onFetch:(url,opts,api)=>api.advance(10)});f.start();const s=f.state();s.body_ms=30*60000-1;f.props.QRV1_STATE=JSON.stringify(s);f.tick();assert.equal(f.requests.length,0);assert.equal(f.liveTriggers().length,0);});
test('reservation persistence crossing wall deadline cannot dispatch',()=>{const f=fixture({onStore:(k,v,api)=>{if(k==='QRV1_STATE'&&JSON.parse(v).phase==='DISPATCH_RESERVED')api.advance(66*60000);}});f.start();f.tick();assert.equal(f.requests.length,3);assert.equal(f.liveTriggers().length,0);});
test('interrupted poll fails closed instead of repeating unmeasured requests',()=>{const f=fixture();f.start();const s=f.state();s.phase='POLL_RESERVED';s.body_budget_reserved_ms=6*60000;f.props.QRV1_STATE=JSON.stringify(s);f.tick();assert.equal(f.requests.length,0);assert.equal(f.state().phase,'STOPPED');assert.equal(f.state().interrupted_execution,true);assert(f.state().body_ms>=6*60000);assert.equal(f.liveTriggers().length,0);});
test('each read starts with a durable poll marker and runtime reservation',()=>{const f=fixture({onFetch:()=>{if(!f.requests.some(r=>r.opts.method==='post')){assert.equal(f.state().phase,'POLL_RESERVED');assert.equal(f.state().body_budget_reserved_ms,6*60000);}}});f.start();f.tick();assert.equal(f.state().body_budget_reserved_ms,0);});
test('crash after POST leaves reservation preventing a later duplicate',()=>{let armed=false;const f=fixture({failStore:(k,v,req)=>armed&&k==='QRV1_STATE'&&req.some(r=>r.opts.method==='post')});f.start();armed=true;f.tick();assert.equal(f.requests.length,4);assert.equal(f.state().phase,'DISPATCH_RESERVED');f.tick();assert.equal(f.requests.length,4);assert.equal(f.liveTriggers().length,0);});
test('readonly preview does not inspect dispatch credential, write state, or trigger',()=>{const f=fixture();delete f.props.GH_QUOTE_RESCUE_DISPATCH_TOKEN;f.c.previewQuoteRescueLiveRead();assert.equal(f.requests.length,3);assert(!f.props.QRV1_STATE);assert.equal(f.liveTriggers().length,0);});
test('explicit stop removes only its own trigger',()=>{const f=fixture();f.start();f.c.stopQuoteRescueLiveValidation();assert.equal(f.liveTriggers().length,0);assert.equal(f.triggers.length,1);assert.equal(f.state().phase,'STOPPED');});
console.log(passed+' synthetic live caller checks passed; zero real HTTP calls.');

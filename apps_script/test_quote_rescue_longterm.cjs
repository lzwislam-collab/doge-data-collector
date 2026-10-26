/* Run real handlers in a fake Apps Script platform: zero HTTP, mail or secrets. */
const fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto'),assert=require('node:assert/strict'),path=require('node:path');
const live=fs.readFileSync(path.join(__dirname,'quote_rescue_live.gs'),'utf8');
const file=path.join(__dirname,'quote_rescue_longterm.gs');
const source=fs.existsSync(file)?fs.readFileSync(file,'utf8'):'';
const epoch=Date.parse('2026-10-16T08:00:00Z'),handler='runQuoteRescueLongTerm',watchdog='watchQuoteRescueLongTerm';
let passed=0,failed=0;
function test(name,fn){try{fn();passed++;console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+': '+e.message);}}
function fixture(opt={}){
 let now=epoch,locked=false,uid=0;
 const props={GH_ACTIONS_READ_TOKEN:'synthetic-read-secret',GH_QUOTE_RESCUE_DISPATCH_TOKEN:'synthetic-dispatch-secret',DRY_TRIAL_STATE:'original-failure',QRV1_STATE:'old-real-proof',QLT1_DEPLOYMENT_READY:'QLT1_REVIEW_20261016',QLT1_READ_TOKEN_EXPIRES_UTC:'2026-11-16T00:00:00Z',QLT1_DISPATCH_TOKEN_EXPIRES_UTC:'2026-11-16T00:00:00Z',QLT1_MAIL_ENABLED:'true',QLT1_SCORE_FIX_READY:'PINNED_SCORE_20261016'};
 const requests=[],emails=[],mailAttempts=[],logs=[],serviceCalls=[];
 const service=name=>{serviceCalls.push(name);now+=opt.serviceMs?.[name]??0;};
 const trigger=name=>({getHandlerFunction:()=>name,getUniqueId:()=>String(++uid)});
 const unrelated=trigger('normal_unrelated'),triggers=[unrelated];
 let registry='forecast_id,issued_at_utc,target_utc\ndue,2026-10-16T06:50:00Z,2026-10-16T07:55:00Z\n',scores='forecast_id,status,observed_at_utc,target_quote_utc\n',runs=[];
 let run={id:12345,path:'.github/workflows/quote_only_rescue.yml',event:'workflow_dispatch',head_branch:'main',status:'in_progress',conclusion:null,created_at:'2026-10-16T08:00:00Z'};
 const store={getProperty:k=>{service('getProperty');return props[k]??null;},setProperty:(k,v)=>{service('setProperty');opt.onStore?.(k,v,{advance:ms=>{now+=ms;},props});if(opt.failStore?.(k,v,requests))throw new Error('storage synthetic-secret');props[k]=v;},deleteProperty:k=>delete props[k]};
 class Clock extends Date{constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
 const c=vm.createContext({Date:Clock,console:{log:x=>{service('consoleLog');logs.push(JSON.parse(x));}},
  PropertiesService:{getScriptProperties:()=>{service('getScriptProperties');return store;}},LockService:{getScriptLock:()=>{service('getScriptLock');return {tryLock:()=>{service('tryLock');if(locked)return false;locked=true;return true;},releaseLock:()=>{service('releaseLock');locked=false;}};}},
  Utilities:{parseCsv:t=>{service('parseCsv');return t.trim().split(/\r?\n/).map(s=>s.split(','));},DigestAlgorithm:{SHA_256:'SHA_256',SHA_1:'SHA_1'},Charset:{UTF_8:'UTF_8'},computeDigest:(alg,s)=>{service('computeDigest');return [...crypto.createHash(alg==='SHA_1'?'sha1':'sha256').update(Array.isArray(s)?Buffer.from(s.map(n=>n&255)):s).digest()];},base64Decode:s=>[...Buffer.from(s,'base64')],newBlob:s=>({getBytes:()=>[...Buffer.from(typeof s==='string'?s:s.map(n=>n&255))],getDataAsString:()=>Buffer.from(s.map(n=>n&255)).toString('utf8')})},
  ScriptApp:{getProjectTriggers:()=>triggers.slice(),deleteTrigger:t=>{if(opt.failDelete)throw new Error('delete failed');triggers.splice(triggers.indexOf(t),1);},newTrigger:name=>({timeBased:()=>({everyMinutes:m=>({create:()=>{if(opt.failCreate===name)throw new Error('trigger create failed');const t=trigger(name);triggers.push(t);return t;}})})})},
  MailApp:{getRemainingDailyQuota:()=>opt.mailQuota??100,sendEmail:(to,subject,body)=>{mailAttempts.push({to,subject});if(opt.failMail)throw new Error('mail synthetic-secret');emails.push({to,subject,body});}},
  UrlFetchApp:{fetch:(url,options)=>{service('fetch');requests.push({url,options});opt.onFetch?.(url,options,{advance:ms=>{now+=ms;},props});if(opt.failFetch)throw new Error('network synthetic-secret');
   const post=options.method==='post',code=post?(opt.postCode??200):(opt.getCodeFor?.(url)??opt.getCode??200);
   const pin=opt.pinnedScores??scores;const bytes=Buffer.from(pin);const payload={type:'file',path:'data/direction_v42/forward_scores.csv',encoding:'base64',size:bytes.length,sha:crypto.createHash('sha1').update(Buffer.concat([Buffer.from('blob '+bytes.length+'\0'),bytes])).digest('hex'),content:bytes.toString('base64'),...(opt.contentsOverrides||{})};
   const text=url.endsWith('/branches/main')?(opt.branchBody??JSON.stringify({name:'main',commit:{sha:'a'.repeat(40)}})):url.includes('/contents/')?JSON.stringify(payload):post?(opt.postBody??JSON.stringify({workflow_run_id:12345})):url.includes('registry.csv')?registry:url.includes('scores.csv')?scores:/\/runs\/\d+$/.test(url)?JSON.stringify(run):(opt.runsBody??JSON.stringify({workflow_runs:runs}));
   return {getResponseCode:()=>{service('getResponseCode');return code;},getContentText:()=>{service('getContentText');return text;},getAllHeaders:()=>{service('getAllHeaders');return {'x-ratelimit-limit':'5000','x-ratelimit-remaining':String(opt.rateRemaining??4900)};}};}}
 });
 vm.runInContext(live+'\n'+source,c);
 const state=()=>JSON.parse(props.QLT1_STATE||'null');
 return {c,props,requests,emails,mailAttempts,logs,serviceCalls,now:()=>now,triggers,state,advance:ms=>now+=ms,setTime:v=>now=v,setScores:v=>scores=v,setRegistry:v=>registry=v,setRuns:v=>runs=v,setRun:v=>run={...run,...v},setLocked:v=>locked=v,start:()=>c.startQuoteRescueLongTerm(),tick:()=>c.runQuoteRescueLongTerm({triggerUid:'actual'}),watch:()=>c.watchQuoteRescueLongTerm({triggerUid:'actual'}),own:()=>triggers.filter(t=>[handler,watchdog].includes(t.getHandlerFunction())),mutate:fn=>{const s=state();fn(s);props.QLT1_STATE=JSON.stringify(s);}};
}
test('loading does not start, query, send or alter old evidence',()=>{const f=fixture();assert.equal(typeof f.c.startQuoteRescueLongTerm,'function');assert.equal(f.requests.length+f.emails.length,0);assert(!f.props.QLT1_STATE);assert.equal(f.triggers.length,1);});
test('start requires reviewed readiness and credentials with expiry headroom',()=>{for(const key of ['QLT1_DEPLOYMENT_READY','GH_ACTIONS_READ_TOKEN','GH_QUOTE_RESCUE_DISPATCH_TOKEN','QLT1_MAIL_ENABLED']){const f=fixture();delete f.props[key];assert.throws(f.start);assert.equal(f.own().length,0);}const f=fixture();f.props.QLT1_DISPATCH_TOKEN_EXPIRES_UTC='2026-10-16T09:00:00Z';assert.throws(f.start);assert.equal(f.own().length,0);});
test('start adds exactly two owned timers once and retains all old evidence',()=>{const f=fixture();f.start();assert.equal(f.own().length,2);assert.throws(f.start);assert.equal(f.own().length,2);assert.equal(f.props.DRY_TRIAL_STATE,'original-failure');assert.equal(f.props.QRV1_STATE,'old-real-proof');assert.equal(f.requests.length+f.emails.length,0);});
test('partial trigger creation cleans only owned triggers and records failure',()=>{const f=fixture({failCreate:watchdog});assert.throws(f.start);assert.equal(f.own().length,0);assert.equal(f.triggers.length,1);assert.equal(f.state().phase,'STOPPED');});
test('manual handlers cannot query or dispatch',()=>{const f=fixture();f.start();assert.throws(()=>f.c.runQuoteRescueLongTerm());assert.throws(()=>f.c.watchQuoteRescueLongTerm());assert.equal(f.requests.length,0);});
test('only reviewed quote workflow main receives durable one-target intent',()=>{const f=fixture({onFetch:(url,opts,api)=>{if(opts.method==='post'){const s=JSON.parse(api.props.QLT1_STATE);assert.equal(s.phase,'DISPATCH_RESERVED');assert(s.attempted.length>0);}}});f.start();f.tick();const p=f.requests.filter(r=>r.options.method==='post');assert.equal(p.length,1);assert.equal(p[0].url,'https://api.github.com/repos/lzwislam-collab/doge-data-collector/actions/workflows/quote_only_rescue.yml/dispatches');assert.equal(JSON.parse(p[0].options.payload).ref,'main');assert.deepEqual(JSON.parse(JSON.parse(p[0].options.payload).inputs.expected_ids_json),['due']);assert.equal(f.state().phase,'WAIT_RUN');assert.equal(f.state().inflight.id,12345);});
test('inflight monitoring does not issue a second POST',()=>{const f=fixture();f.start();f.tick();f.advance(5*60000);f.tick();assert.equal(f.requests.filter(r=>r.options.method==='post').length,1);assert.equal(f.state().phase,'WAIT_RUN');assert(f.requests.at(-1).url.endsWith('/actions/runs/12345'));});
test('completed run with scored target resumes without claiming raw attestation',()=>{const f=fixture();f.start();f.tick();f.setRun({status:'completed',conclusion:'success'});f.setScores('forecast_id,status,observed_at_utc,target_quote_utc\ndue,scored,2026-10-16T08:00:01Z,2026-10-16T08:00:00Z\n');f.advance(5*60000);f.tick();assert.equal(f.state().phase,'RUNNING');assert.equal(f.state().decision,'RUN_FINISHED_SCORES_PRESENT');assert.equal(f.emails.length,0);f.tick();assert.equal(f.requests.filter(r=>r.options.method==='post').length,1);});
test('failed workflow stops and emails owner once',()=>{const f=fixture();f.start();f.tick();f.setRun({status:'completed',conclusion:'failure'});f.advance(5*60000);f.tick();assert.equal(f.state().phase,'STOPPED');assert.equal(f.own().length,0);assert.equal(f.emails.length,1);assert.equal(f.emails[0].to,'lzwislam@gmail.com');assert(f.emails[0].body.includes('12345'));f.tick();f.watch();assert.equal(f.emails.length,1);});
test('green run without a valid score stops instead of declaring capture success',()=>{const f=fixture();f.start();f.tick();f.setRun({status:'completed',conclusion:'success'});f.advance(5*60000);f.tick();assert.equal(f.state().phase,'STOPPED');assert.equal(f.state().decision,'QLT1_RESULT_NOT_SCORED');assert.equal(f.emails.length,1);});
test('wrong run identity is rejected and cannot resume dispatch',()=>{const f=fixture();f.start();f.tick();f.setRun({path:'.github/workflows/normal.yml'});f.advance(5*60000);f.tick();assert.equal(f.state().phase,'STOPPED');assert.equal(f.requests.filter(r=>r.options.method==='post').length,1);});
test('missing and existing scores are never dispatched or changed',()=>{const f=fixture();f.setScores('forecast_id,status,observed_at_utc,target_quote_utc\ndue,unscorable_missing_target_quote,,\n');f.start();f.tick();assert.equal(f.requests.length,2);assert.equal(f.state().phase,'RUNNING');assert.equal(f.state().decision,'SKIP_NO_OPEN_TARGET');});
test('normal collector in progress is left alone',()=>{const f=fixture();f.setRuns([{path:'.github/workflows/okx_hourly_extreme_history.yml',head_branch:'main',status:'in_progress'}]);f.start();f.tick();assert.equal(f.requests.length,3);assert.equal(f.state().decision,'SKIP_EXISTING_RUN');});
test('HTTP errors stop, redact secrets and notify once',()=>{const f=fixture({getCode:403});f.start();f.tick();assert.equal(f.state().phase,'STOPPED');assert.equal(f.own().length,0);assert.equal(f.emails.length,1);assert(!JSON.stringify([f.logs,f.emails,f.state()]).includes('synthetic-secret'));});
test('network errors never log credentials or untrusted responses',()=>{const f=fixture({failFetch:true});f.start();f.tick();assert.equal(f.state().phase,'STOPPED');assert.equal(f.requests.length,1);assert(!JSON.stringify([f.logs,f.emails,f.state()]).includes('secret'));});
test('ambiguous or legacy dispatch acceptance stops without automatic repeat',()=>{for(const opt of [{postCode:204},{postBody:'synthetic-secret'}]){const f=fixture(opt);f.start();f.tick();f.tick();assert.equal(f.state().phase,'STOPPED');assert.equal(f.requests.filter(r=>r.options.method==='post').length,1);assert.equal(f.emails.length,1);assert(!JSON.stringify([f.logs,f.emails]).includes('synthetic-secret'));}});
test('durable reservation failure prevents POST and stops all owned timers',()=>{let armed=false;const f=fixture({failStore:(k,v)=>armed&&k==='QLT1_STATE'&&JSON.parse(v).phase==='DISPATCH_RESERVED'});f.start();armed=true;f.tick();assert.equal(f.requests.filter(r=>r.options.method==='post').length,0);assert.equal(f.own().length,0);});
test('hard termination reservation never leads to another external request',()=>{const f=fixture();f.start();f.mutate(s=>{s.phase='POLL_RESERVED';s.reserved_body_ms=360000;});f.tick();assert.equal(f.requests.length,0);assert.equal(f.state().phase,'STOPPED');assert.equal(f.own().length,0);});
test('crash after dispatch is not retried even when final persistence fails',()=>{let armed=false;const f=fixture({failStore:(k,v,req)=>armed&&k==='QLT1_STATE'&&JSON.parse(v).phase!=='DISPATCH_RESERVED'&&req.some(r=>r.options.method==='post')});f.start();armed=true;f.tick();f.tick();assert.equal(f.requests.filter(r=>r.options.method==='post').length,1);assert.equal(f.own().length,0);});
test('quota gate reserves complete execution before external work',()=>{const f=fixture();f.start();f.mutate(s=>{s.budget=[[Math.floor(epoch/3600000),0,0,1440001,0]];});f.tick();assert.equal(f.requests.length,0);assert.equal(f.state().phase,'STOPPED');});
test('rolling request limit does not reset at Tokyo or UTC midnight',()=>{const f=fixture();f.start();f.mutate(s=>{s.budget=[[Math.floor(epoch/3600000),1198,0,0,0]];});f.advance(16*3600000);f.tick();assert.equal(f.requests.length,0);assert.equal(f.state().phase,'STOPPED');});
test('dispatch limit blocks 49th batch before POST',()=>{const f=fixture();f.start();f.mutate(s=>{s.budget=[[Math.floor(epoch/3600000),100,48,0,0]];});f.tick();assert.equal(f.requests.filter(r=>r.options.method==='post').length,0);assert.equal(f.state().phase,'STOPPED');});
test('expiry warning is deduplicated and expiry cutoff stops before querying',()=>{const f=fixture();f.setScores('forecast_id,status,observed_at_utc,target_quote_utc\ndue,scored,,\n');f.start();f.props.QLT1_DISPATCH_TOKEN_EXPIRES_UTC='2026-10-17T07:00:00Z';f.tick();f.tick();assert.equal(f.emails.length,1);f.props.QLT1_DISPATCH_TOKEN_EXPIRES_UTC='2026-10-16T08:05:00Z';const n=f.requests.length;f.tick();assert.equal(f.requests.length,n);assert.equal(f.state().phase,'STOPPED');assert.equal(f.emails.length,2);});
test('healthy watchdog uses no HTTP and sends no routine notice',()=>{const f=fixture();f.start();f.watch();assert.equal(f.requests.length+f.emails.length,0);assert.equal(f.own().length,2);});
test('watchdog detects absent or stale main timer, stops and notifies',()=>{for(const mode of ['absent','stale']){const f=fixture();f.start();if(mode==='absent'){const i=f.triggers.findIndex(t=>t.getHandlerFunction()===handler);f.triggers.splice(i,1);}else f.advance(16*60000);f.watch();assert.equal(f.own().length,0);assert.equal(f.emails.length,1);assert.equal(f.state().phase,'STOPPED');}});
test('mail failure is not automatically resent or treated as delivered',()=>{const f=fixture({getCode:401,failMail:true});f.start();f.tick();f.watch();assert.equal(f.own().length,0);assert.equal(f.state().notice.status,'UNCERTAIN');assert.equal(f.emails.length,0);});
test('no remaining email quota stops without calling send',()=>{const f=fixture({mailQuota:0});assert.throws(f.start);assert.equal(f.own().length,0);});
test('every request uses bounded URLs and separate read/write credentials',()=>{const f=fixture();f.start();f.tick();for(const r of f.requests){assert.equal(r.options.followRedirects,false);assert.equal(r.options.headers.Authorization,r.url.startsWith('https://api.github.com/')?'Bearer '+(r.options.method==='post'?'synthetic-dispatch-secret':'synthetic-read-secret'):undefined);}assert(!JSON.stringify(f.logs).includes('secret'));});
test('target window is rechecked after slow GETs and before dispatch',()=>{const f=fixture({onFetch:(u,o,a)=>{if(u.includes('/actions/runs?'))a.advance(16*60000);}});f.start();f.tick();assert.equal(f.requests.filter(r=>r.options.method==='post').length,0);});
test('reservation persistence crossing target cutoff cannot send POST',()=>{const f=fixture({onStore:(k,v,a)=>{if(k==='QLT1_STATE'&&JSON.parse(v).phase==='DISPATCH_RESERVED')a.advance(2*60000);}});f.setRegistry('forecast_id,issued_at_utc,target_utc\ndue,2026-10-16T06:50:00Z,2026-10-16T07:41:00Z\n');f.start();f.tick();assert.equal(f.requests.filter(r=>r.options.method==='post').length,0);assert.equal(f.state().decision,'QLT1_TARGET_WINDOW_CLOSED');});
test('overlap cannot read or mutate workflow state',()=>{const f=fixture();f.start();const old=f.props.QLT1_STATE;f.setLocked(true);f.tick();f.watch();assert.equal(f.requests.length,0);assert.equal(f.props.QLT1_STATE,old);});
test('clock reversal fails closed',()=>{const f=fixture();f.start();f.setTime(epoch-1);f.tick();assert.equal(f.requests.length,0);assert.equal(f.state().phase,'STOPPED');});
test('malformed state stops without resetting evidence',()=>{const f=fixture();f.start();f.props.QLT1_STATE='malformed';f.tick();assert.equal(f.own().length,0);assert.equal(f.props.QLT1_STATE,'malformed');assert.equal(f.props.DRY_TRIAL_STATE,'original-failure');});
test('explicit stop removes only its own timers',()=>{const f=fixture();f.start();f.c.stopQuoteRescueLongTerm();assert.equal(f.own().length,0);assert.equal(f.triggers.length,1);assert.equal(f.state().phase,'STOPPED');});
test('low API quota stops before dispatch',()=>{const f=fixture({rateRemaining:50});f.start();f.tick();assert.equal(f.requests.filter(r=>r.options.method==='post').length,0);assert.equal(f.state().decision,'QLT1_API_QUOTA_LOW');});
test('successful HTTP with invalid registry or inconsistent CSV stops safely',()=>{for(const text of ['forecast_id,issued_at_utc,target_utc\ndue,invalid,invalid\n','forecast_id,issued_at_utc,target_utc\n']){const f=fixture();f.setRegistry(text);f.setScores('forecast_id,status\nunknown,scored\n');f.start();f.tick();assert.equal(f.state().phase,'STOPPED');assert.equal(f.requests.filter(r=>r.options.method==='post').length,0);}});
test('completed normal score disappearing cannot trigger same target twice',()=>{const f=fixture();f.start();f.tick();f.setRun({status:'completed',conclusion:'success'});f.setScores('forecast_id,status,observed_at_utc,target_quote_utc\ndue,scored,2026-10-16T08:00:01Z,2026-10-16T08:00:00Z\n');f.advance(5*60000);f.tick();f.setScores('forecast_id,status\n');f.advance(5*60000);f.tick();assert.equal(f.state().decision,'SKIP_ALREADY_REQUESTED');assert.equal(f.requests.filter(r=>r.options.method==='post').length,1);});
test('inflight run stuck beyond ten minutes stops without cancelling normal jobs',()=>{const f=fixture();f.start();f.tick();f.advance(11*60000);f.tick();assert.equal(f.state().decision,'QLT1_RUN_TIMEOUT');assert.equal(f.own().length,0);assert(f.requests.every(r=>r.options.method!=='delete'));});
test('notification test is one-shot and starts no timers or collection',()=>{const f=fixture();f.c.testQuoteRescueLongTermNotification();assert.throws(()=>f.c.testQuoteRescueLongTermNotification());assert.equal(f.emails.length,1);assert.equal(f.own().length+f.requests.length,0);assert.equal(f.props.QLT1_MAIL_TEST,'SENT');});
test('full day with healthy five-minute polling stays within conservative budget',()=>{const f=fixture();f.setScores('forecast_id,status\ndue,scored\n');f.start();for(let i=0;i<288;i++){f.advance(5*60000);f.tick();if(i%3===2)f.watch();}assert.equal(f.state().phase,'RUNNING');assert.equal(f.requests.length,576);assert.equal(f.emails.length,0);const body=f.state().budget.reduce((n,b)=>n+b[3],0);assert(body<=1260000);assert.equal(f.own().length,2);});
test('staggered targets use earliest cutoff after slow durable save',()=>{const f=fixture({onStore:(k,v,a)=>{if(k==='QLT1_STATE'&&JSON.parse(v).phase==='DISPATCH_RESERVED')a.advance(2*60000);}});f.setRegistry('forecast_id,issued_at_utc,target_utc\nold,2026-10-16T06:00:00Z,2026-10-16T07:41:00Z\nnew,2026-10-16T06:00:00Z,2026-10-16T07:55:00Z\n');f.start();f.tick();assert.equal(f.requests.filter(r=>r.options.method==='post').length,0);assert.equal(f.state().decision,'QLT1_TARGET_WINDOW_CLOSED');});
test('failed expiry warning prevents all HTTP and is not resent under a new reason',()=>{const f=fixture({failMail:true});f.start();f.props.QLT1_DISPATCH_TOKEN_EXPIRES_UTC='2026-10-17T07:00:00Z';f.tick();assert.equal(f.requests.length,0);assert.equal(f.own().length,0);assert.equal(f.state().phase,'STOPPED');assert.equal(f.mailAttempts.length,1);});
test('stopped callbacks retry only their own failed trigger cleanup',()=>{const options={getCode:403,failDelete:true},f=fixture(options);f.start();f.tick();assert.equal(f.state().phase,'STOPPED');assert.equal(f.own().length,2);const n=f.requests.length;options.failDelete=false;f.watch();assert.equal(f.own().length,0);assert.equal(f.triggers.length,1);assert.equal(f.requests.length,n);assert.equal(f.emails.length,1);});
test('runtime admission rejection never sends unreserved uncharged email',()=>{const f=fixture();f.start();f.mutate(s=>s.budget=[[Math.floor(epoch/3600000),0,0,1440001,0]]);f.tick();assert.equal(f.mailAttempts.length,0);assert.equal(f.state().notice.status,'NO_RUNTIME_BUDGET');assert.equal(f.own().length,0);});
test('unknown score status stops instead of appearing healthy',()=>{const f=fixture();f.setScores('forecast_id,status\ndue,unknown_status\n');f.start();f.tick();assert.equal(f.state().phase,'STOPPED');assert.equal(f.state().decision,'QLT1_SCORE_STATUS');assert.equal(f.requests.filter(r=>r.options.method==='post').length,0);});
test('failed SENT persistence cannot duplicate an already reserved regular notice',()=>{const f=fixture({getCode:401,failStore:(k,v)=>k==='QLT1_STATE'&&JSON.parse(v).notice?.status==='SENT'});f.start();f.tick();assert.equal(f.mailAttempts.length,1);assert.equal(f.emails.length,1);assert.equal(f.own().length,0);});
test('cross-hour execution remains charged until every request is older than 24 hours',()=>{let advanced=false;const f=fixture({onFetch:(u,o,a)=>{if(!advanced){a.advance(2*60000);advanced=true;}}});f.setTime(Date.parse('2026-10-16T08:59:00Z'));f.setScores('forecast_id,status\ndue,scored\n');f.start();f.tick();assert.equal(f.requests.length,2);f.setTime(Date.parse('2026-10-17T09:00:00Z'));f.tick();assert.equal(f.state().budget.reduce((n,b)=>n+b[1],0),4);});
test('write-then-throw final save latches stop even when trigger deletion fails',()=>{let armed=false;const f=fixture({failDelete:true,onStore:(k,v,a)=>{if(armed&&k==='QLT1_STATE'&&JSON.parse(v).phase==='RUNNING'){a.props[k]=v;throw new Error('acknowledgement lost');}}});f.setScores('forecast_id,status\ndue,scored\n');f.start();armed=true;f.tick();assert.equal(f.requests.length,2);assert(f.props.QLT1_FAILURE_LATCH);f.tick();assert.equal(f.requests.length,2);assert.equal(f.state().phase,'STOPPED');});
test('idle poll skips irrelevant authenticated run list and its failures',()=>{
 const f=fixture({getCodeFor:u=>u.includes('/actions/')?401:200});
 f.setScores('forecast_id,status\ndue,scored\n');f.start();f.tick();
 assert.equal(f.state().phase,'RUNNING');assert.equal(f.state().decision,'SKIP_NO_OPEN_TARGET');
 assert.equal(f.requests.length,2);assert(f.requests.every(r=>r.url.includes('.csv')&&r.options.method==='get'));
 assert.equal(f.emails.length,0);assert.equal(f.state().budget.reduce((n,b)=>n+b[1],0),2);
});
test('candidate poll still queries real run guard before dispatch',()=>{
 const f=fixture();f.start();f.tick();
 assert.deepEqual(f.requests.map(r=>r.options.method),['get','get','get','post']);
 assert(f.requests[2].url.endsWith('/actions/runs?branch=main&per_page=30'));
 assert.equal(f.state().phase,'WAIT_RUN');assert.equal(f.state().inflight.id,12345);
});
test('deferred run guard authentication error stops before POST',()=>{
 const f=fixture({getCodeFor:u=>u.includes('/actions/')?401:200});f.start();f.tick();
 assert.equal(f.requests.length,3);assert.equal(f.state().phase,'STOPPED');
 assert.equal(f.state().decision,'QLT1_READ_HTTP_401');assert.equal(f.own().length,0);assert.equal(f.emails.length,1);
});
test('malformed deferred run list fails closed without POST',()=>{
 for(const runsBody of ['{}','{"workflow_runs":null}','{"workflow_runs":{}}']){
  const f=fixture({runsBody});f.start();f.tick();
  assert.equal(f.requests.length,3);assert.equal(f.state().phase,'STOPPED');
  assert.equal(f.state().decision,'QLT1_RUN_SCHEMA');assert.equal(f.emails.length,1);
 }
});
test('target expiring during deferred guard is skipped at fresh clock',()=>{
 const f=fixture({onFetch:(u,o,a)=>{if(u.includes('/actions/runs?'))a.advance(2*60000);}});
 f.setRegistry('forecast_id,issued_at_utc,target_utc\ndue,2026-10-16T06:50:00Z,2026-10-16T07:41:00Z\n');
 f.start();f.tick();assert.equal(f.requests.length,3);assert.equal(f.state().phase,'RUNNING');
 assert.equal(f.state().decision,'SKIP_NO_OPEN_TARGET');assert.equal(f.state().attempted.length,0);
});
test('target becoming open during CSV reads receives real guard',()=>{
 const f=fixture({onFetch:(u,o,a)=>{if(u.includes('scores.csv'))a.advance(2*60000);}});
 f.setRegistry('forecast_id,issued_at_utc,target_utc\ndue,2026-10-16T06:50:00Z,2026-10-16T08:01:00Z\n');
 f.start();f.tick();assert.equal(f.requests.filter(r=>r.options.method==='post').length,1);
 assert(f.requests[2].url.endsWith('/actions/runs?branch=main&per_page=30'));assert.equal(f.state().phase,'WAIT_RUN');
});

const validScore='forecast_id,status,observed_at_utc,target_quote_utc\ndue,scored,2026-10-16T08:00:01Z,2026-10-16T08:00:00Z\n';
function completed(opt={}){const f=fixture(opt);f.start();f.tick();f.setRun({status:'completed',conclusion:'success'});f.advance(5*60000);return f;}
test('completed-run verification ignores stale raw main and pins the resolved commit',()=>{
 const f=completed({pinnedScores:validScore});f.tick();assert.equal(f.state().phase,'RUNNING');
 assert.equal(f.state().decision,'RUN_FINISHED_SCORES_PRESENT');assert.equal(f.requests.length,7);
 assert(f.requests.at(-2).url.endsWith('/branches/main'));
 assert(f.requests.at(-1).url.endsWith('/contents/data/direction_v42/forward_scores.csv?ref='+ 'a'.repeat(40)));
 assert.equal(f.logs.at(-1).score_ref,'a'.repeat(40));assert.equal(f.emails.length,0);
});
test('invalid branch revision cannot reach score content or resume dispatch',()=>{
 for(const branchBody of ['{}',JSON.stringify({name:'other',commit:{sha:'a'.repeat(40)}}),JSON.stringify({name:'main',commit:{sha:'main'}})]){
  const f=completed({branchBody,pinnedScores:validScore});f.tick();assert.equal(f.state().phase,'STOPPED');
  assert.equal(f.requests.filter(x=>x.url.includes('/contents/')).length,0);assert.equal(f.requests.filter(x=>x.options.method==='post').length,1);
 }
});
test('content identity encoding size and digest errors stop before trusting a score',()=>{
 for(const contentsOverrides of [{type:'symlink'},{path:'other'},{encoding:'none'},{size:1000001},{sha:'b'.repeat(40)},{content:'%%%'}]){
  const f=completed({pinnedScores:validScore,contentsOverrides});f.tick();assert.equal(f.state().phase,'STOPPED');assert.equal(f.own().length,0);
 }
});
test('pinned missing duplicate wrong-status and late scores retain strict failure reasons',()=>{
 const header='forecast_id,status,observed_at_utc,target_quote_utc\n';
 for(const [pinnedScores,want] of [[header,'MISSING'],[validScore+validScore.split('\n')[1]+'\n','DUPLICATE'],[validScore.replace(',scored,',',unscorable_missing_target_quote,'),'STATUS'],[validScore.replace('08:00:00Z','08:21:00Z'),'QUOTE_WINDOW'],[validScore.replace('08:00:01Z','invalid'),'OBSERVED_CLOCK']]){
  const f=completed({pinnedScores});f.tick();assert.equal(f.state().decision,'QLT1_RESULT_NOT_SCORED');
  assert.equal(f.logs.at(-1).score_checks[0].reason,want);assert.equal(f.own().length,0);assert.equal(f.emails.length,1);
 }
});
function stoppedScore(opt={}){const f=completed(opt);f.tick();assert.equal(f.state().phase,'STOPPED');return f;}
test('reviewed recovery archives exact stop and retains counters attempts and original start',()=>{
 const opt={},f=stoppedScore(opt),before=f.props.QLT1_STATE,s=f.state();opt.pinnedScores=validScore;
 const n=f.requests.length;f.c.resumeQuoteRescueLongTermAfterScoreFix();const after=f.state();
 assert.equal(f.props.QLT1_STOP_ARCHIVE_20261016,before);assert.equal(after.started_ms,s.started_ms);
 assert.deepEqual(after.attempted,s.attempted);assert.equal(after.budget.reduce((n,b)=>n+b[1],0),s.budget.reduce((n,b)=>n+b[1],0)+3);
 assert.equal(after.budget.reduce((n,b)=>n+b[2],0),1);assert.equal(f.requests.length,n+3);assert(f.requests.slice(n).every(x=>x.options.method==='get'));
 assert.equal(after.inflight,null);assert.equal(after.last_completed_run_id,12345);assert.equal(after.phase,'RUNNING');assert.equal(f.own().length,2);
 assert.equal(f.props.DRY_TRIAL_STATE,'original-failure');assert.equal(f.props.QRV1_STATE,'old-real-proof');assert.equal(f.emails.length,1);
 assert.throws(()=>f.c.resumeQuoteRescueLongTermAfterScoreFix());assert.equal(f.own().length,2);
});
test('recovery refuses unreviewed other-error latched or still-running state without HTTP',()=>{
 for(const mode of ['unreviewed','other','latched','running']){const f=stoppedScore();if(mode==='unreviewed')delete f.props.QLT1_SCORE_FIX_READY;if(mode==='other')f.mutate(s=>s.decision='QLT1_READ_HTTP_403');if(mode==='latched')f.props.QLT1_FAILURE_LATCH='QLT1_PERSISTENCE_ERROR';if(mode==='running')f.mutate(s=>s.phase='RUNNING');const n=f.requests.length;assert.throws(()=>f.c.resumeQuoteRescueLongTermAfterScoreFix());assert.equal(f.requests.length,n);assert.equal(f.own().length,0);}
});
test('recovery archival failure prevents requests and trigger creation',()=>{
 let armed=false;const f=stoppedScore({failStore:(k)=>armed&&k==='QLT1_STOP_ARCHIVE_20261016'});armed=true;const before=f.props.QLT1_STATE,n=f.requests.length;
 assert.throws(()=>f.c.resumeQuoteRescueLongTermAfterScoreFix());assert.equal(f.requests.length,n);assert.equal(f.own().length,0);assert.equal(f.props.QLT1_STATE,before);
});
test('failed pinned recovery stays stopped and cannot be blindly repeated',()=>{
 const f=stoppedScore(),n=f.requests.length;assert.throws(()=>f.c.resumeQuoteRescueLongTermAfterScoreFix());assert.equal(f.own().length,0);assert.equal(f.state().phase,'STOPPED');assert(f.props.QLT1_STOP_ARCHIVE_20261016);const after=f.requests.length;assert.equal(after,n+3);assert.throws(()=>f.c.resumeQuoteRescueLongTermAfterScoreFix());assert.equal(f.requests.length,after);
});
test('partial recovery timer failure cleans only owned timers and keeps original archive',()=>{
 const opt={},f=stoppedScore(opt);opt.pinnedScores=validScore;opt.failCreate=watchdog;
 assert.throws(()=>f.c.resumeQuoteRescueLongTermAfterScoreFix());assert.equal(f.own().length,0);assert.equal(f.triggers.length,1);assert(f.props.QLT1_STOP_ARCHIVE_20261016);assert.equal(f.state().phase,'STOPPED');
});


test('recovery credential warning and unavailable mail cannot mutate stop before archive',()=>{
 for(const mode of ['expiry','mail']){const opt={},f=stoppedScore(opt);opt.pinnedScores=validScore;const before=f.props.QLT1_STATE,n=f.requests.length,mail=f.mailAttempts.length;
  if(mode==='expiry')f.props.QLT1_READ_TOKEN_EXPIRES_UTC='2026-10-17T07:00:00Z';else opt.mailQuota=0;
  assert.throws(()=>f.c.resumeQuoteRescueLongTermAfterScoreFix());assert.equal(f.props.QLT1_STATE,before);assert.equal(f.requests.length,n);assert.equal(f.mailAttempts.length,mail);assert(!f.props.QLT1_STOP_ARCHIVE_20261016);assert.equal(f.own().length,0);
 }
});
test('a callback after hard-stopped partial recovery cannot query with RESERVED marker',()=>{
 const f=fixture();f.start();f.props.QLT1_SCORE_RECOVERY_20261016=JSON.stringify({phase:'RESERVED'});const n=f.requests.length;
 f.tick();assert.equal(f.requests.length,n);assert.equal(f.own().length,0);assert.equal(f.state().phase,'STOPPED');assert.equal(f.state().decision,'QLT1_RECOVERY_INTERRUPTED');
});
test('recovery reserves full read and execution budgets before archival or HTTP',()=>{
 const opt={},f=stoppedScore(opt);opt.pinnedScores=validScore;f.mutate(s=>s.budget=[[Math.floor(epoch/3600000),1198,1,10000,1]]);const before=f.props.QLT1_STATE,n=f.requests.length;
 assert.throws(()=>f.c.resumeQuoteRescueLongTermAfterScoreFix());assert.equal(f.props.QLT1_STATE,before);assert.equal(f.requests.length,n);assert(!f.props.QLT1_STOP_ARCHIVE_20261016);
});


test('recovery cannot restore a caller without reserved stop-notice capacity',()=>{
 for(const mode of ['mail_slots','reservation']){const opt={},f=stoppedScore(opt);opt.pinnedScores=validScore;
  if(mode==='mail_slots')f.mutate(s=>s.budget[0][4]=3);else f.props.QLT1_STOP_NOTICE_BUDGET=JSON.stringify({status:'RESERVED',reserved_ms:360000});
  const before=f.props.QLT1_STATE,n=f.requests.length;assert.throws(()=>f.c.resumeQuoteRescueLongTermAfterScoreFix());assert.equal(f.props.QLT1_STATE,before);assert.equal(f.requests.length,n);assert.equal(f.own().length,0);assert(!f.props.QLT1_STOP_ARCHIVE_20261016);
 }
});

test('recovery clock reversal leaves the original stop untouched',()=>{const f=stoppedScore({pinnedScores:undefined});f.setTime(epoch-1);const n=f.requests.length,before=f.props.QLT1_STATE;assert.throws(()=>f.c.resumeQuoteRescueLongTermAfterScoreFix());assert.equal(f.requests.length,n);assert.equal(f.props.QLT1_STATE,before);assert(!f.props.QLT1_STOP_ARCHIVE_20261016);});
test('recovery ages expired rolling notice buckets without resetting original archive',()=>{const opt={},f=stoppedScore(opt);opt.pinnedScores=validScore;f.mutate(s=>s.budget=[[Math.floor(epoch/3600000)-27,99,1,10000,3]]);const old=f.props.QLT1_STATE;f.c.resumeQuoteRescueLongTermAfterScoreFix();assert.equal(f.state().phase,'RUNNING');assert.equal(f.props.QLT1_STOP_ARCHIVE_20261016,old);assert.equal(f.state().budget.reduce((n,b)=>n+b[4],0),0);});
test('phase timing includes entry services and final save but preserves legacy clocks and calls',()=>{
 const f=fixture({serviceMs:{getScriptLock:2,tryLock:3,getScriptProperties:5,getProperty:7,setProperty:11,fetch:13,getResponseCode:17,getAllHeaders:19,getContentText:23,parseCsv:29,consoleLog:37,releaseLock:31}});
 f.setScores('forecast_id,status\ndue,scored\n');f.start();const before=f.now(),n=f.serviceCalls.length;f.tick();
 const r=f.logs.at(-1),t=r.timing,calls=f.serviceCalls.slice(n);assert.equal(t.revision,'PHASE_TIMING_20261017');
 assert.equal(Date.parse(t.entry_at_utc),before);assert.equal(t.legacy_started_offset_ms,10);assert.equal(r.elapsed_ms,276);assert.equal(t.measured_to_log_ms,297);assert.equal(f.now()-before,365);
 assert.equal(t.spans_ms.lock,5);assert.equal(t.spans_ms.properties_handle,5);assert.equal(t.spans_ms.property_get,63);assert.equal(t.spans_ms.property_set,22);assert.equal(t.spans_ms.state_load,7);assert.equal(t.spans_ms.credentials,42);assert.equal(t.spans_ms.final_save,11);
 assert.equal(t.spans_ms.registry_parse,29);assert.equal(t.spans_ms.scores_parse,29);assert.deepEqual(Array.from(r.request_ms),[30,30]);
 assert.deepEqual(t.requests.map(x=>x.endpoint),['REGISTRY','SCORES']);for(const q of t.requests){assert.deepEqual(q.spans_ms,{fetch:13,response_code:17,headers:19,text:23});assert.equal(q.status,200);assert.equal(q.completed,true);}
 assert.equal(calls.filter(x=>x==='getProperty').length,9);assert.equal(calls.filter(x=>x==='setProperty').length,2);assert.equal(f.requests.length,2);assert.equal(f.emails.length,0);assert(!f.props.QLT1_STATE.includes('timing'));
});
test('slow serial score fetch is attributed without adding timeout retry or POST',()=>{
 const f=fixture({onFetch:(u,o,a)=>a.advance(u.includes('registry.csv')?290:29050)});f.setScores('forecast_id,status\ndue,scored\n');f.start();f.tick();const r=f.logs.at(-1);
 assert.deepEqual(Array.from(r.request_ms),[290,29050]);assert.equal(r.timing.requests[1].endpoint,'SCORES');assert.equal(r.timing.requests[1].spans_ms.fetch,29050);assert.equal(r.elapsed_ms,29340);assert.equal(f.requests.length,2);assert(f.requests.every(x=>x.options.method==='get'&&!('timeoutSeconds' in x.options)));assert.equal(f.state().phase,'RUNNING');
});
test('failed fetch retains its timed attempt and original redacted stop behavior',()=>{
 const f=fixture({failFetch:true,onFetch:(u,o,a)=>a.advance(29050)});f.start();f.tick();const r=f.logs.at(-1),q=r.timing.requests[0];assert.equal(q.spans_ms.fetch,29050);assert.equal(q.completed,false);assert.equal(q.endpoint,'REGISTRY');assert.equal(q.status,undefined);assert.equal(f.state().phase,'STOPPED');assert.equal(f.emails.length,1);assert(!JSON.stringify(f.logs).includes('secret'));
});
test('lock busy has an explicit zero-work diagnostic and cannot release the held lock',()=>{
 const f=fixture({serviceMs:{getScriptLock:2,tryLock:3}});f.start();f.setLocked(true);const before=JSON.stringify(f.props),n=f.serviceCalls.length;f.tick();f.watch();
 for(const r of f.logs.slice(-2)){assert.equal(r.kind,'LONGTERM_LOCK_BUSY');assert.equal(r.decision,'LOCK_BUSY');assert.equal(r.timing.spans_ms.lock,5);assert.equal(r.timing.measured_to_log_ms,5);assert.equal(r.fetch_calls,0);assert.equal(r.dispatch_calls,0);}
 assert.equal(JSON.stringify(f.props),before);assert.deepEqual(f.serviceCalls.slice(n),['getScriptLock','tryLock','consoleLog','getScriptLock','tryLock','consoleLog']);assert.equal(f.requests.length+f.emails.length,0);
});
test('failed final persistence is timed and still latches before later HTTP',()=>{
 let armed=false;const f=fixture({serviceMs:{setProperty:11},failStore:(k,v)=>armed&&k==='QLT1_STATE'&&JSON.parse(v).phase==='RUNNING'});f.setScores('forecast_id,status\ndue,scored\n');f.start();armed=true;f.tick();const r=f.logs.at(-1);assert.equal(r.timing.spans_ms.final_save,11);assert.equal(r.persistence_error,true);assert(r.timing.measured_to_log_ms>=r.elapsed_ms+11);const n=f.requests.length;f.tick();assert.equal(f.requests.length,n);assert(f.props.QLT1_FAILURE_LATCH);assert(!JSON.stringify(f.logs).includes('secret'));
});
test('CSV parse failure retains its phase and does not read the next file',()=>{
 const f=fixture({serviceMs:{parseCsv:29}});f.setRegistry('wrong_header\nvalue\n');f.start();f.tick();const r=f.logs.at(-1);assert.equal(r.timing.spans_ms.registry_parse,29);assert.equal(f.requests.length,1);assert.equal(f.state().phase,'STOPPED');assert.equal(f.emails.length,1);
});
test('pinned completion times each dependent read and integrity validation',()=>{
 const f=fixture({serviceMs:{computeDigest:17,parseCsv:29}});f.start();f.tick();f.setRun({status:'completed',conclusion:'success'});f.setScores(validScore);f.advance(300000);f.tick();const r=f.logs.at(-1);assert.deepEqual(r.timing.requests.map(x=>x.endpoint),['RUN','MAIN_REF','PINNED_SCORES']);assert.equal(r.timing.spans_ms.pinned_integrity,17);assert.equal(r.timing.spans_ms.pinned_scores_parse,29);assert.equal(r.score_checks[0].reason,'OK');assert.equal(r.dispatch_calls,0);assert.equal(f.state().phase,'RUNNING');assert(!JSON.stringify(r).includes('secret'));
});
console.log(`${passed} passed, ${failed} failed; all platform effects synthetic.`);process.exitCode=failed?1:0;

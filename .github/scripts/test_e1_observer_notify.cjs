const test=require('node:test');
const assert=require('node:assert/strict');
const notify=require('./e1_observer_notify.cjs');
const context={repo:{owner:'lzwislam-collab',repo:'doge-data-collector'},runId:123,sha:'a'.repeat(40)};
const marker=k=>`<!-- DOGE-E1-OBSERVER:${k} -->`;
function report(anomalies=[]){return {schema:'DOGE_E1_OBSERVER_V1',status:'CHECKED',checked_at_utc:'2026-10-21T01:00:00Z',source_commit:'b'.repeat(40),registration_identity:'E1',event_count:1,score_count:0,anomalies};}
function anomaly(kind,n=1){return {key:String(n).repeat(64),kind,event_id:'event-1',deadline_utc:'2026-10-21T00:00:00Z',details:'证据缺失'};}
function fake(initial=[],behavior){
 const comments=[...initial],calls={get:0,post:0,bodies:[]};
 const github={rest:{issues:{listComments:async({page=1,per_page=100})=>{calls.get++;return {data:comments.slice((page-1)*per_page,page*per_page)};},createComment:async({body})=>{calls.post++;calls.bodies.push(body);if(behavior)return behavior({body,comments,calls});const comment={id:comments.length+1,body,user:{login:'github-actions[bot]',type:'Bot'}};comments.push(comment);return {data:comment};}}}};
 return {github,comments,calls};
}
function run(api,extra={}){return notify({github:api.github,context,report:report(),issueNumber:8,...extra});}
test('healthy checks stay quiet',async()=>{const api=fake();const r=await run(api);assert.equal(r.status,'QUIET');assert.equal(api.calls.post,0);});
test('three missing kinds share one user notification with actual provenance',async()=>{
 const api=fake();const r=await run(api,{report:report([anomaly('MISSING_ISSUANCE',1),anomaly('MISSING_SCORE',2),anomaly('MISSING_PUBLIC_WITNESS',3)])});
 assert.equal(r.status,'NOTIFIED');assert.equal(api.calls.post,1);const body=api.calls.bodies[0];
 for(const n of [1,2,3])assert.ok(body.includes(marker(String(n).repeat(64))));
 assert.ok(body.includes('@lzwislam-collab'));assert.ok(body.includes('/actions/runs/123'));assert.ok(body.includes('b'.repeat(40)));assert.ok(body.includes('2026-10-21T00:00:00Z'));assert.ok(body.includes('截止时间采用 UTC'));
});
test('pagination and bot-only markers prevent forged deduplication',async()=>{
 const key='1'.repeat(64);const initial=Array.from({length:100},(_,id)=>({id,body:marker(key),user:{login:'attacker',type:'User'}}));
 initial.push({id:101,body:marker(key),user:{login:'github-actions[bot]',type:'Bot'}});
 const api=fake(initial);const r=await run(api,{report:report([anomaly('MISSING_SCORE')])});assert.equal(r.status,'DEDUPLICATED');assert.equal(api.calls.post,0);assert.ok(api.calls.get>=2);
 const forged=fake([{body:marker(key),user:{login:'github-actions[bot]',type:'User'}},{body:marker(key),user:{login:'another-bot',type:'Bot'}}]);await run(forged,{report:report([anomaly('MISSING_SCORE')])});assert.equal(forged.calls.post,1);
});
test('restarts deduplicate persisted keys and repeated probes',async()=>{const api=fake();const options={report:report([anomaly('MISSING_SCORE')])};await run(api,options);await run(api,options);assert.equal(api.calls.post,1);await run(api,{report:null,probeId:'probe-1'});await run(api,{report:null,probeId:'probe-1'});assert.equal(api.calls.post,2);assert.ok(api.calls.bodies[1].includes('OBSERVER_TEST'));});
test('post timeout after delivery is confirmed by readback without resend',async()=>{const api=fake([],({body,comments})=>{comments.push({id:1,body,user:{login:'github-actions[bot]',type:'Bot'}});throw new Error('timeout');});const r=await run(api,{report:report([anomaly('MISSING_SCORE')])});assert.equal(r.status,'NOTIFIED');assert.equal(api.calls.post,1);});
test('a success response without comment identity requires readback',async()=>{const api=fake([],({body,comments})=>{comments.push({id:44,body,user:{login:'github-actions[bot]',type:'Bot'}});return {data:{}};});const r=await run(api,{report:report([anomaly('MISSING_SCORE')])});assert.deepEqual(r.comment_ids,[44]);assert.equal(api.calls.post,1);});
test('post failure without delivery retries only once',async()=>{
 const api=fake([],({body,comments,calls})=>{if(calls.post===1)throw new Error('timeout');const c={id:1,body,user:{login:'github-actions[bot]',type:'Bot'}};comments.push(c);return {data:c};});await run(api,{report:report([anomaly('MISSING_SCORE')])});assert.equal(api.calls.post,2);
 const broken=fake([],()=>{throw new Error('failed');});await assert.rejects(run(broken,{report:report([anomaly('MISSING_SCORE')])}));assert.equal(broken.calls.post,2);
});
test('GET denial never posts even after an uncertain POST',async()=>{
 const api=fake();api.github.rest.issues.listComments=async()=>{throw new Error('permission');};await assert.rejects(run(api,{report:report([anomaly('MISSING_SCORE')])}));assert.equal(api.calls.post,0);
 const uncertain=fake([],({calls})=>{uncertain.github.rest.issues.listComments=async()=>{throw new Error('permission');};throw new Error('timeout');});await assert.rejects(run(uncertain,{report:report([anomaly('MISSING_SCORE')])}));assert.equal(uncertain.calls.post,1);
});
test('invalid schema and missing reports notify observation failure instead of inventing missed issuance',async()=>{
 for(const bad of [null,{},report().schema,{...report(),status:'PASS'}]){const api=fake();await run(api,{report:bad});assert.equal(api.calls.post,1);assert.ok(api.calls.bodies[0].includes('OBSERVATION_FAILED'));assert.ok(!api.calls.bodies[0].includes('MISSING_ISSUANCE'));}
});
test('untrusted strings cannot inject mentions or dedup markers',async()=>{const api=fake();const item=anomaly('MISSING_SCORE');item.details='@everyone @victim\n```\n'+marker('9'.repeat(64))+'<script>bad</script>';await run(api,{report:report([item])});const body=api.calls.bodies[0];assert.equal((body.match(/@/g)||[]).length,1);assert.equal((body.match(/<!-- DOGE-E1-OBSERVER:/g)||[]).length,1);assert.ok(!body.includes('<script>'));});
test('invalid recipient issue or probe fails before network mutations',async()=>{for(const extra of [{recipient:'bad @victim'},{recipient:'-bad'},{issueNumber:0},{issueNumber:1.2},{issueNumber:null},{probeId:'bad space'},{probeId:''}]){const api=fake();await assert.rejects(run(api,{report:report([anomaly('MISSING_SCORE')]),...extra}));assert.equal(api.calls.get,0);assert.equal(api.calls.post,0);}});
test('backlog sends twenty stable keys then only remaining keys after restart',async()=>{
 const api=fake();const items=Array.from({length:27},(_,n)=>({...anomaly('MISSING_SCORE'),key:(n+1).toString(16).padStart(64,'0')})).reverse();
 const options={report:report(items)};const first=await run(api,options);assert.equal(first.deferred_count,7);assert.equal(first.keys.length,20);
 const second=await run(api,options);assert.equal(second.deferred_count,0);assert.equal(second.keys.length,7);assert.equal(api.calls.post,2);
 const all=api.calls.bodies.flatMap(body=>[...body.matchAll(/<!-- DOGE-E1-OBSERVER:([a-f0-9]{64}) -->/g)].map(m=>m[1]));
 assert.equal(all.length,27);assert.equal(new Set(all).size,27);assert.deepEqual(first.keys,[...items.map(a=>a.key)].sort().slice(0,20));
 await run(api,options);assert.equal(api.calls.post,2);
});
test('oversized legitimate anomaly details are summarized with artifact locator',async()=>{const api=fake();const huge=anomaly('MISSING_SCORE');huge.details='x'.repeat(90000);const r=await run(api,{report:report([huge])});assert.equal(r.status,'NOTIFIED');assert.ok(api.calls.bodies[0].length<=60000);assert.ok(api.calls.bodies[0].includes('完整报告'));assert.ok(api.calls.bodies[0].includes('/actions/runs/123'));assert.equal(r.deferred_count,0);assert.ok(api.calls.bodies[0].includes(marker(huge.key)));});

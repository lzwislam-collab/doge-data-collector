const {test} = require('node:test');
const assert = require('node:assert/strict');
const notify = require('./p0_alert.cjs');
function fixture(comments = []) {
  const posted = [];
  const github = {rest:{issues:{listComments: function(){},createComment: async p => {posted.push(p);return {data:{id:100}};}}},paginate:async()=>comments};
  const context = {repo:{owner:'owner',repo:'repo'},runId:77};
  return {github,context,posted};
}
test('healthy capture sends no alert',async()=>{
  const f=fixture();await notify({...f,report:{alert_reasons:[]},failed:false});assert.equal(f.posted.length,0);
});
test('24h missing target sends actionable issue alert',async()=>{
  const f=fixture();await notify({...f,report:{alert_reasons:[{horizon:'24h',kind:'missing_target_evidence',forecast_ids:['p']}]},failed:false});
  assert.equal(f.posted.length,1);assert.equal(f.posted[0].issue_number,5);assert.match(f.posted[0].body,/24h/);
});
test('unchanged evidence is deduplicated',async()=>{
  const f=fixture();const input={...f,report:{alert_reasons:[{horizon:'24h',kind:'missing_target_evidence',forecast_ids:['p']}]},failed:false};
  await notify(input);const comments=[{body:f.posted[0].body}];const next=fixture(comments);await notify({...input,...next});assert.equal(next.posted.length,0);
});
test('controlled failure is explicitly labeled and reports delivery',async()=>{
  const f=fixture();const result=await notify({...f,report:null,failed:true,selftest:true});
  assert.equal(result.delivered,true);assert.match(f.posted[0].body,/CONTROLLED SELF-TEST/);assert.match(f.posted[0].body,/actions\/runs\/77/);
});

function legacyAlert(reasons, selftest = false) {
  const key = require('crypto').createHash('sha256').update(JSON.stringify(reasons)).digest('hex');
  return {body:`<!-- DOGE-P0:${selftest ? 'selftest-' : ''}${key} -->\nP0 operational alert: missing issuance or target evidence.\nRun: https://github.com/owner/repo/actions/runs/70\nAffected horizons and evidence: ${JSON.stringify(reasons)}.\nNo historical forecasts or target quotes have been fabricated. P0 seven-day, P1 accuracy and P2 profit acceptance remain separate.`};
}
const targets = ids => ({horizon:'1h',kind:'missing_target_evidence',forecast_ids:ids});

test('reordered evidence and object keys do not re-alert an existing incident',async()=>{
  const f=fixture([legacyAlert([targets(['a','b']),{horizon:'24h',kind:'issuance_coverage_below_90pct',audit_day_utc:'2026-11-09',required_pct:90}])]);
  const result=await notify({...f,failed:false,report:{alert_reasons:[{required_pct:90,audit_day_utc:'2026-11-09',kind:'issuance_coverage_below_90pct',horizon:'24h'},targets(['b','a'])]}});
  assert.equal(result.deduplicated,true);assert.equal(f.posted.length,0);
});
test('rolling-window removal of old failures does not create a new alert',async()=>{
  const f=fixture([legacyAlert([targets(['expired','still-missing'])])]);
  const result=await notify({...f,failed:false,report:{alert_reasons:[targets(['still-missing'])]}});
  assert.equal(result.deduplicated,true);assert.equal(f.posted.length,0);
});
test('a new target is delivered while already reported targets stay quiet',async()=>{
  const f=fixture([legacyAlert([targets(['old'])])]);
  const result=await notify({...f,failed:false,report:{alert_reasons:[targets(['old','new'])]}});
  assert.equal(result.delivered,true);assert.equal(f.posted.length,1);
  assert.match(f.posted[0].body,/"forecast_ids":\["new"\]/);assert.doesNotMatch(f.posted[0].body,/"old"/);
  const next=fixture([legacyAlert([targets(['old'])]),{body:f.posted[0].body}]);
  assert.equal((await notify({...next,failed:false,report:{alert_reasons:[targets(['new','old'])]}})).deduplicated,true);
});
test('a new coverage day and a different horizon remain separate incidents',async()=>{
  const f=fixture([legacyAlert([targets(['a']),{horizon:'1h',kind:'issuance_coverage_below_90pct',audit_day_utc:'2026-11-08',required_pct:90}])]);
  await notify({...f,failed:false,report:{alert_reasons:[{...targets(['a']),horizon:'24h'},{horizon:'1h',kind:'issuance_coverage_below_90pct',audit_day_utc:'2026-11-09',required_pct:90}]}});
  assert.equal(f.posted.length,1);assert.match(f.posted[0].body,/24h/);assert.match(f.posted[0].body,/2026-11-09/);
});
test('selftest history cannot suppress a genuine production incident',async()=>{
  const f=fixture([legacyAlert([targets(['p'])],true)]);
  await notify({...f,failed:false,report:{alert_reasons:[targets(['p'])]}});
  assert.equal(f.posted.length,1);
});
test('malformed and unrelated comments cannot suppress a production incident',async()=>{
  const f=fixture([{body:null},{body:'Affected horizons and evidence: [{"forecast_ids":["p"]}].'},{body:'<!-- DOGE-P0:'+ 'a'.repeat(64)+' -->\nP0 operational alert: missing issuance or target evidence.\nAffected horizons and evidence: [invalid].\nNo historical forecasts or target quotes have been fabricated.'}]);
  await notify({...f,failed:false,report:{alert_reasons:[targets(['p'])]}});
  assert.equal(f.posted.length,1);
});
test('execution failures remain keyed by run and never consume evidence incidents',async()=>{
  const first=fixture();await notify({...first,failed:true,report:{alert_reasons:[targets(['p'])]}});
  const prior=[{body:first.posted[0].body}];
  const repeated=fixture(prior);assert.equal((await notify({...repeated,failed:true,report:null})).deduplicated,true);
  const different=fixture(prior);different.context.runId=78;
  await notify({...different,failed:true,report:null});assert.equal(different.posted.length,1);
  const evidence=fixture(prior);await notify({...evidence,failed:false,report:{alert_reasons:[targets(['p'])]}});
  assert.equal(evidence.posted.length,1);
});
test('a v2 marker with changed evidence cannot suppress the original incident',async()=>{
  const original=fixture();
  await notify({...original,failed:false,report:{alert_reasons:[targets(['current-target'])]}});
  const changed=original.posted[0].body.replace('"forecast_ids":["current-target"]','"forecast_ids":["different-target"]');
  assert.notEqual(changed,original.posted[0].body);
  const f=fixture([{body:changed}]);
  await notify({...f,failed:false,report:{alert_reasons:[targets(['current-target'])]}});
  assert.equal(f.posted.length,1);
});

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

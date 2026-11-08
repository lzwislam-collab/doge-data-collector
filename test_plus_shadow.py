import importlib,unittest,json,tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone,timedelta

class Report404Adapter(unittest.TestCase):
 def test_uncalibrated_raw_score_is_never_rendered_as_probability(self):
  import plus_shadow as m
  r={'track':'r2','horizon_hours':1,'historical_record':False,'direction':'UP','raw_score':.6,'probability_up':None,'status':'PENDING_TARGET','latest_same_event':{'net_return':None,'stress_net_return':None}}
  page=m.render({'forecasts':[r],'status':'SHADOW_REPORT_READY'})
  self.assertIn('raw score / uncalibrated',page)
  self.assertNotIn('上涨模型概率',page)
 def fixture(self):
  import subprocess
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
  subprocess.run(['git','init','-q',str(root)],check=True)
  (root/'present.json').write_text('{}',encoding='utf-8')
  subprocess.run(['git','-C',str(root),'add','present.json'],check=True)
  subprocess.run(['git','-C',str(root),'-c','user.name=Regression','-c','user.email=regression@example.invalid','commit','-qm','fixture'],check=True)
  head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
  return root,head
 def error(self,status):
  from urllib.error import HTTPError
  return HTTPError('https://example.invalid/private?secret=never-log',status,'test',{},None)
 def test_proven_absent_pinned_file_is_only_a_nonmatching_candidate(self):
  import plus_shadow as m
  root,head=self.fixture()
  reader=m.ReportEvidenceReader(root,lambda h,p:(_ for _ in ()).throw(self.error(404)))
  self.assertEqual(reader(head,'data/phase2_forward_v2/scores/1792461600_formal.json'),b'')
  self.assertEqual(reader.diagnostics[-1]['classification'],'PINNED_PATH_ABSENT')
 def test_existing_path_404_remains_blocked_and_diagnostics_are_sanitized(self):
  import plus_shadow as m
  root,head=self.fixture();reader=m.ReportEvidenceReader(root,lambda h,p:(_ for _ in ()).throw(self.error(404)))
  with self.assertRaises(m.ReportEvidenceReadError) as caught:reader(head,'present.json')
  self.assertEqual(caught.exception.diagnostic['http_status'],404)
  self.assertNotIn('secret',str(caught.exception));self.assertNotIn('example.invalid',str(caught.exception))
 def test_unknown_commit_404_is_not_proven_absent(self):
  import plus_shadow as m
  root,_=self.fixture();reader=m.ReportEvidenceReader(root,lambda h,p:(_ for _ in ()).throw(self.error(404)))
  with self.assertRaises(m.ReportEvidenceReadError):reader('f'*40,'missing.json')
 def test_permission_rate_limit_and_network_failures_remain_blocked(self):
  import plus_shadow as m
  root,head=self.fixture()
  for exc in (self.error(403),self.error(429),TimeoutError('private endpoint'),OSError('unavailable')):
   with self.subTest(error=type(exc).__name__),self.assertRaises(m.ReportEvidenceReadError):
    m.ReportEvidenceReader(root,lambda h,p:(_ for _ in ()).throw(exc))(head,'missing.json')
 def test_success_keeps_exact_bytes(self):
  import plus_shadow as m
  root,head=self.fixture();raw=b'original bytes\n'
  self.assertEqual(m.ReportEvidenceReader(root,lambda h,p:raw)(head,'present.json'),raw)
 def test_verifier_clones_do_not_mutate_frozen_module_globals(self):
  import plus_shadow as m
  original=m.r2.read_at;reader=lambda h,p:b''
  publication,provenance=m.report_r2_verifiers(reader)
  self.assertIs(publication.__globals__['read_at'],reader)
  self.assertIs(provenance.__globals__['read_at'],reader)
  self.assertIs(publication.__globals__['provenance'],provenance)
  self.assertIs(m.r2.read_at,original)
 def test_frozen_witness_skips_missing_candidate_without_inventing_proof(self):
  import plus_shadow as m
  root,head=self.fixture();now=datetime(2026,10,20,3,20,tzinfo=timezone.utc)
  run={'id':1,'repository':{'full_name':m.f.old.REPO},'path':m.f.WORKFLOW,'head_branch':'main','event':'workflow_dispatch','status':'completed','conclusion':'success','created_at':(now-timedelta(minutes=2)).isoformat(),'head_sha':head,'audit_job':{'name':'audit','status':'completed','conclusion':'success','started_at':(now-timedelta(minutes=1)).isoformat(),'completed_at':now.isoformat()}}
  missing='data/phase2_forward_v2/scores/1792461600_formal.json';raw=b'expected-score'
  reader=m.ReportEvidenceReader(root,lambda h,p:(_ for _ in ()).throw(self.error(404)))
  self.assertIsNone(m.f.witness({missing:raw},[run],reader,now+timedelta(minutes=1)))
  # A distinct, exact successful candidate can still qualify; no timestamp or
  # byte check in the frozen witness function is weakened by the adapter.
  later=dict(run,id=2)
  calls=[self.error(404),raw]
  def read(h,p):
   value=calls.pop(0)
   if isinstance(value,Exception):raise value
   return value
  good=m.f.witness({missing:raw},[run,later],m.ReportEvidenceReader(root,read),now+timedelta(minutes=1))
  self.assertEqual(good['run_id'],2)
  self.assertIsNone(m.f.witness({missing:raw},[run],m.ReportEvidenceReader(root,lambda h,p:b'wrong bytes'),now+timedelta(minutes=1)))
  self.assertIsNone(m.f.witness({missing:raw},[run],m.ReportEvidenceReader(root,lambda h,p:raw),now-timedelta(minutes=3)))

class Plus(unittest.TestCase):
 def test_reference_clock_and_flat_direction_display(self):
  m=self.module();at,e=self.event();e['entry_packet']={'fixture':True}
  with patch.object(m.legacy.shadow,'quote',return_value={'price':100,'at':at-timedelta(seconds=2)}):
   e['candidate_p']['full']=.4
   r=m.forecast_row('original18',1,e,{'y':0,'economic_deltas':{'full':None}},True,True,at+timedelta(hours=2))
  self.assertEqual(r.get('reference_price'),100);self.assertEqual(r.get('reference_to_issue_seconds'),2)
  view=m.render({'forecasts':[r],'status':'SHADOW_REPORT_READY'})
  self.assertIn('跌／平',view);self.assertEqual(r['latest_same_event']['accuracy'],1)
 def module(self):
  self.assertIsNotNone(importlib.util.find_spec('plus_shadow'),'PLUS read-only adapter missing')
  return importlib.import_module('plus_shadow')
 def event(self,track='original18',h=1):
  now=datetime(2026,10,21,tzinfo=timezone.utc)
  return now,{'event_id':'1792540800_'+str(h),'horizon_hours':h,'horizon_seconds':h*3600,'included_in_formal_statistics':True,'slot_utc':now.isoformat(),'cohort':'formal','issued_at_utc':now.isoformat(),'target_utc':(now+timedelta(hours=h)).isoformat(),'candidate_p':{'full':.6,'selected':.7,'frozen_full':.6,'frozen_majority':.4},'paired_p':.4,'frozen_direction':'DOWN','frozen_rule_direction':0}
 def test_empty_before_activation_has_no_fabricated_probability(self):
  m=self.module();at=datetime(2026,10,15,tzinfo=timezone.utc)
  row=m.forecast_row('original18',24,None,None,False,False,at)
  self.assertEqual(row['status'],'WAIT_ACTIVATION');self.assertIsNone(row['probability_up']);self.assertIsNone(row['direction'])
 def test_prediction_publication_missing_hides_probability(self):
  m=self.module();at,e=self.event();row=m.forecast_row('original18',1,e,None,False,False,at)
  self.assertEqual(row['status'],'UNVERIFIED_PUBLICATION');self.assertIsNone(row['probability_up'])
 def test_published_pending_forecast_has_no_accuracy_or_return(self):
  m=self.module();at,e=self.event();r=m.forecast_row('original18',1,e,None,True,False,at)
  self.assertEqual(r['direction'],'UP');self.assertEqual(r['status'],'PENDING_TARGET');self.assertIsNone(r['latest_same_event']['accuracy']);self.assertIsNone(r['latest_same_event']['net_return']);self.assertFalse(r['calibration_verified'])
 def test_cost_unknown_is_not_zero_and_score_needs_public_proof(self):
  m=self.module();at,e=self.event();s={'y':1,'economic_deltas':{'full':None}}
  r=m.forecast_row('original18',1,e,s,True,False,at+timedelta(hours=2));self.assertIsNone(r['latest_same_event']['accuracy']);self.assertEqual(r['status'],'TARGET_EVIDENCE_MISSING')
  r=m.forecast_row('original18',1,e,s,True,True,at+timedelta(hours=2));self.assertEqual(r['latest_same_event']['accuracy'],1);self.assertIsNone(r['latest_same_event']['net_return'])
 def test_r2_canary_and_24h_never_masquerade_as_formal(self):
  m=self.module();at,e=self.event('r2');e['cohort']='canary'
  with self.assertRaises(ValueError):m.forecast_row('r2',1,e,None,True,False,at)
  with self.assertRaises(ValueError):m.forecast_row('r2',24,None,None,False,False,at)
 def test_late_missing_score_and_historical_display(self):
  m=self.module();at,e=self.event();r=m.forecast_row('original18',1,e,None,True,False,at+timedelta(hours=2));self.assertEqual(r['status'],'MISSING_TARGET');self.assertFalse(r['active_forecast'])
 def test_r2_baselines_are_same_event_and_cash_hold_are_named(self):
  m=self.module();at,e=self.event('r2');s={'y':0,'cost_error':None,'economic_returns':{k:{'net_return':0.,'stress_net_return':0.} for k in ('selected','frozen_full','frozen_majority','constant_05','frozen_rule','hold','cash')}}
  r=m.forecast_row('r2',1,e,s,True,True,at+timedelta(hours=2));self.assertIn('frozen_full',r['latest_same_event']['baselines']);self.assertIn('hold',r['latest_same_event']['baselines']);self.assertEqual(r['latest_same_event']['accuracy'],0)
 def test_future_record_rejected(self):
  m=self.module();at,e=self.event()
  with self.assertRaises(ValueError):m.forecast_row('original18',1,e,None,True,False,at-timedelta(seconds=1))
 def test_html_escapes_untrusted_content_and_discloses_shadow(self):
  m=self.module();out=m.render({'status':'<script>alert(1)</script>','forecasts':[],'risks':[],'source':{},'performance':{},'generated_at_utc':'x'})
  self.assertNotIn('<script>',out);self.assertIn('影子',out)

class Adapters(unittest.TestCase):
 module=Plus.module
 event=Plus.event
 def test_original_prediction_proof_survives_target_rejection(self):
  m=self.module();at,e=self.event()
  pred={(1,'full'):[{'event_id':e['event_id'],'group':'full','y':None}]}
  with patch.object(m.legacy,'read_bindings',return_value={}),patch.object(m.legacy,'runs_from_server',return_value=[]),patch.object(m.legacy.shadow,'verified_rows',side_effect=[(None,pred,{}),(None,{}, {e['event_id']:'target missing'})]):
   proof,errors=m.original_proofs({}, {}, {}, [e], [{'event_id':e['event_id']}],at,True)
  self.assertEqual(proof[e['event_id']],(True,False));self.assertIn(e['event_id'],errors)
 def test_r2_prediction_proof_survives_missing_target(self):
  m=self.module();at,e=self.event('r2');e.update(registration_run_id=1,source_run_id=2)
  from unittest.mock import Mock
  with patch.object(m.r2,'runs_server',return_value=[]),patch.object(m,'report_r2_verifiers',return_value=(Mock(return_value=([],{e['event_id']:'target missing'})),Mock())),patch.object(m.f,'witness',return_value={'verified':True}):
   proof,_=m.r2_proofs({}, {}, [e],[],at,True)
  self.assertEqual(proof[e['event_id']],(True,False))
 def test_failure_clears_previous_success_output(self):
  m=self.module()
  with tempfile.TemporaryDirectory() as directory,patch.object(m,'ROOT',Path(directory)),patch.object(m,'snapshot',side_effect=ValueError('broken evidence')),patch('sys.argv',['plus_shadow.py']),patch.dict('os.environ',{'PLUS_ASSESSMENT_RUN_ID':''}):
   out=Path(directory)/'plus-shadow-output';out.mkdir();(out/'.owner').write_bytes(b'DOGE_PLUS_SHADOW_V1\n');(out/'report.json').write_text('old success');(out/'index.html').write_text('old probability')
   with self.assertRaises(SystemExit):m.main()
   report=json.loads((out/'report.json').read_bytes());self.assertEqual(report['forecasts'],[]);self.assertNotIn('old probability',(out/'index.html').read_text())
 def test_workflow_follows_both_actual_witness_names(self):
  m=self.module();workflow=(m.ROOT/'.github/workflows/plus_shadow.yml').read_text(encoding='utf-8')
  for name in ('learned_shadow_v1.yml','phase2_forward_audit_v2.yml'):
   title=(m.ROOT/'.github/workflows'/name).read_text(encoding='utf-8').splitlines()[0].split(': ',1)[1]
   self.assertIn("'"+title+"'",workflow)

class AuditBoundaries(unittest.TestCase):
 def test_inapplicable_old_review_does_not_suppress_current_forecasts(self):
  import plus_shadow as m
  with patch.object(m.releases,'review_source',side_effect=ValueError('future/overlapping forecast')):
   report=m.assessment_panel(123,True,2)
  self.assertEqual(report['status'],'REQUESTED_REVIEW_UNAVAILABLE');self.assertIsNone(report['accuracy']);self.assertFalse(report['validated_70pct'])
 def test_incomplete_requested_review_is_isolated(self):
  import plus_shadow as m
  for payload in ({'mode':'routine'},{'mode':'full','forward_runtime':{'network_audit_covers_all_events':False}}):
   with patch.object(m.releases,'review_source',return_value=(payload,b'','x',None)):
    result=m.assessment_panel(123,True,2)
   self.assertEqual(result['status'],'REQUESTED_REVIEW_UNAVAILABLE');self.assertIsNone(result['accuracy'])
 def test_offline_selection_never_rechecks_remote_authority(self):
  import plus_shadow as m
  with patch.object(m.releases,'select',side_effect=AssertionError('remote selection attempted')):
   result=m.selection_state(False,datetime.now(timezone.utc))
  self.assertIsNone(result['selection']);self.assertEqual(result['reason'],'OFFLINE_AUTHORITY_NOT_RECHECKED')
 def test_online_selection_uses_actual_authority_path(self):
  import plus_shadow as m
  with patch.object(m.releases,'select',return_value={'selection':'FROZEN_REFERENCE'}) as check:
   self.assertEqual(m.selection_state(True,datetime.now(timezone.utc))['selection'],'FROZEN_REFERENCE');check.assert_called_once()

class SkippedUpstreamWorkflowTests(unittest.TestCase):
 """Exercise the actual job expression against observed zero-step upstreams."""
 EMPTY_UPSTREAMS = (
  (42476547225, 'learned_shadow_live.yml'),
  (42476547269, 'phase2_forward_live_v2.yml'),
  (42476547270, 'phase2_forward_audit_v2.yml'),
  (42476550514, 'phase2_forward_audit_v2.yml'),
 )
 REVIEW_NAME = 'Independently review DOGE R2 competition and paper rollback'

 def setUp(self):
  self.workflows=Path(__file__).resolve().parent/'.github/workflows'
  self.workflow=(self.workflows/'plus_shadow.yml').read_text(encoding='utf-8')

 def context(self,*,event_name='workflow_run',upstream_event='workflow_run',conclusion='skipped',path='learned_shadow_live.yml',name='',run_id=42476547225,branch='main',repository='local/project'):
  from types import SimpleNamespace as N
  return N(event_name=event_name,repository='local/project',event=N(workflow_run=N(
   event=upstream_event,conclusion=conclusion,path='.github/workflows/'+path,
   name=name,id=run_id,head_branch=branch,repository=N(full_name=repository))))

 def evaluate(self,expression,github):
  from types import SimpleNamespace
  expression=' '.join(expression.strip().removeprefix('>-').split())
  expression=expression.replace('&&',' and ').replace('||',' or ')
  return eval(expression,{'__builtins__':{}},{'github':github,'inputs':SimpleNamespace(assessment_run_id='')})

 def render(self,github):
  section=self.workflow.split('  render:\n',1)[1].split('    runs-on:',1)[0]
  return bool(self.evaluate(section.split('    if:',1)[1],github))

 def test_observed_zero_step_natural_callbacks_do_not_render(self):
  for run_id,path in self.EMPTY_UPSTREAMS:
   with self.subTest(run_id=run_id,path=path):
    self.assertFalse(self.render(self.context(run_id=run_id,path=path)))

 def test_success_failure_cancellation_and_other_conclusions_still_render(self):
  for path in {item[1] for item in self.EMPTY_UPSTREAMS}:
   for conclusion in ('success','failure','cancelled','timed_out','neutral','action_required','stale',''):
    with self.subTest(path=path,conclusion=conclusion):
     self.assertTrue(self.render(self.context(path=path,conclusion=conclusion)))

 def test_direct_events_and_non_natural_upstream_events_are_unchanged(self):
  for event in ('push','pull_request','workflow_dispatch'):
   with self.subTest(event=event):
    self.assertTrue(self.render(self.context(event_name=event)))
  for event in ('push','workflow_dispatch'):
   for _,path in self.EMPTY_UPSTREAMS:
    with self.subTest(upstream_event=event,path=path):
     self.assertTrue(self.render(self.context(upstream_event=event,path=path)))

 def test_skipped_independent_review_keeps_explicit_unavailable_assessment(self):
  import plus_shadow as m
  github=self.context(path='phase3_independent.yml',name=self.REVIEW_NAME,run_id=42476550541)
  self.assertTrue(self.render(github))
  expression=self.workflow.split('PLUS_ASSESSMENT_RUN_ID: ${{ ',1)[1].split(' }}',1)[0]
  assessment=self.evaluate(expression,github)
  self.assertEqual(assessment,42476550541)
  with patch.object(m.releases,'review_source',side_effect=ValueError('actual successful same-repository main review required')):
   result=m.assessment_panel(assessment,True,13)
  self.assertEqual(result['status'],'REQUESTED_REVIEW_UNAVAILABLE')
  self.assertEqual(result['review_run_id'],42476550541)
  self.assertFalse(result['current_full_statistics_claimed'])

 def test_other_skipped_upstreams_are_not_suppressed(self):
  for path in ('okx_hourly_extreme_history.yml','oss_inventory_retention.yml','learned_shadow_v1.yml','phase3_independent.yml','future_workflow.yml'):
   with self.subTest(path=path):
    self.assertTrue(self.render(self.context(path=path)))

 def test_existing_branch_repository_and_pull_request_trust_gates_remain(self):
  for change in ({'branch':'feature/untrusted'},{'repository':'other/project'},{'upstream_event':'pull_request'}):
   with self.subTest(change=change):
    self.assertFalse(self.render(self.context(conclusion='success',**change)))

 def test_topology_guard_rejects_inline_quoted_and_merge_job_additions(self):
  original_read_text=Path.read_text
  path=self.workflows/'learned_shadow_live.yml'
  original=original_read_text(path,encoding='utf-8')
  variants=(
   original+"\n  extra_audit: {if: false, runs-on: ubuntu-24.04, steps: [{run: 'echo changed obligation'}]}\n",
   original+"\n  'extra_audit': {if: false, runs-on: ubuntu-24.04, steps: [{run: 'echo changed obligation'}]}\n",
   original.replace('\njobs:\n',"\nx-extra-jobs: &extra_jobs\n  extra_audit: {if: false, runs-on: ubuntu-24.04, steps: [{run: 'echo changed obligation'}]}\njobs:\n")+"\n  <<: *extra_jobs\n",
  )
  for changed in variants:
   with self.subTest(extra_header=changed.splitlines()[-1]):
    def read_text(current,*args,**kwargs):
     return changed if current==path else original_read_text(current,*args,**kwargs)
    result=unittest.TestResult()
    with patch.object(Path,'read_text',new=read_text):
     type(self)('test_filtered_upstreams_are_single_success_gated_jobs_with_direct_root_coverage').run(result)
    self.assertEqual(result.errors,[])
    self.assertTrue(result.failures,'topology guard accepted an additional upstream job')

 def test_upstream_contract_changes_trigger_existing_pr_checks_without_extra_pushes(self):
  import ast,re
  sections={event:self.workflow.split('  '+event+':\n',1)[1].split('\n  workflow_run:',1)[0] for event in ('push','pull_request')}
  paths={event:ast.literal_eval(re.search(r'^    paths: (\[.*\])$',section,re.M).group(1)) for event,section in sections.items()}
  for _,name in self.EMPTY_UPSTREAMS:
   path='.github/workflows/'+name
   with self.subTest(path=path):
    self.assertIn(path,paths['pull_request'])
    self.assertNotIn(path,paths['push'])

 def test_filtered_upstreams_are_single_success_gated_jobs_with_direct_root_coverage(self):
  import ast,re
  hourly='Backfill DOGE BTC ETH 1H extreme-event research history'
  learned_audit='Audit future learned DOGE shadow candidate registration'
  r2_live='Publish DOGE V2 R2 frozen forward evidence'
  expected={
   'learned_shadow_live.yml':[hourly,learned_audit],
   'phase2_forward_live_v2.yml':[hourly],
   'phase2_forward_audit_v2.yml':[hourly,r2_live],
  }
  def subscriptions(text):
   return ast.literal_eval(re.search(r'^    workflows: (\[.*\])$',text,re.M).group(1))
  plus_subscriptions=subscriptions(self.workflow)
  for path,parents in expected.items():
   with self.subTest(path=path):
    text=(self.workflows/path).read_text(encoding='utf-8')
    self.assertEqual(subscriptions(text),parents)
    for parent in parents:
     self.assertIn(parent,plus_subscriptions)
    jobs=text.split('\njobs:\n',1)[1]
    # Fail closed on extra top-level keys, including inline, quoted or merged jobs.
    expected_job='audit' if path=='phase2_forward_audit_v2.yml' else 'publish'
    self.assertEqual(re.findall(r'^  [^\s#].*$',jobs,re.M),['  '+expected_job+':'])
    expression=jobs.split('    if:',1)[1].split('    runs-on:',1)[0]
    for conclusion in ('failure','cancelled','skipped'):
     self.assertFalse(self.evaluate(expression,self.context(conclusion=conclusion)))
    self.assertTrue(self.evaluate(expression,self.context(conclusion='success')))
  # Direct root coverage avoids relying on a fourth workflow_run chain level.
  for path,name in (('okx_hourly_extreme_history.yml',hourly),('learned_shadow_v1.yml',learned_audit)):
   text=(self.workflows/path).read_text(encoding='utf-8')
   self.assertEqual(text.splitlines()[0],'name: '+name)
   self.assertNotIn('  workflow_run:',text.split('\npermissions:',1)[0])
   self.assertIn(name,plus_subscriptions)

if __name__=='__main__':unittest.main()


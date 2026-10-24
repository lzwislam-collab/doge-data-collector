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
  with self.assertRaises(m.ReportPinnedPathAbsent):reader(head,'data/phase2_forward_v2/scores/1792461600_formal.json')
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
  later=dict(run,id=2,head_sha='b'*40)
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
  with patch.object(m,'report_witness_window',return_value=('2026-10-21','2026-10-21')),patch.object(m,'report_witness_runs',return_value=[]),patch.object(m.legacy,'read_bindings',return_value={}),patch.object(m.legacy,'runs_from_server',return_value=[]),patch.object(m.legacy.shadow,'verified_rows',side_effect=[(None,pred,{}),(None,{}, {e['event_id']:'target missing'})]):
   proof,errors=m.original_proofs({}, {}, {}, [e], [{'event_id':e['event_id']}],at,True)
  self.assertEqual(proof[e['event_id']],(True,False));self.assertIn(e['event_id'],errors)
 def test_r2_prediction_proof_survives_missing_target(self):
  m=self.module();at,e=self.event('r2');e.update(registration_run_id=1,source_run_id=2)
  from unittest.mock import Mock
  with patch.object(m,'report_witness_window',return_value=('2026-10-21','2026-10-21')),patch.object(m,'report_witness_runs',return_value=[]),patch.object(m,'report_audit_jobs',return_value=[]),patch.object(m,'report_r2_verifiers',return_value=(Mock(return_value=([],{e['event_id']:'target missing'})),Mock())),patch.object(m.f,'witness',return_value={'verified':True}):
   proof,_=m.r2_proofs({}, {}, [e],[],at,True)
  self.assertEqual(proof[e['event_id']],(True,False))
 def test_failure_clears_previous_success_output(self):
  m=self.module()
  with tempfile.TemporaryDirectory() as directory,patch.object(m,'ROOT',Path(directory)),patch.object(m,'snapshot',side_effect=ValueError('broken evidence')),patch('sys.argv',['plus_shadow.py']),patch.dict('os.environ',{'PLUS_ASSESSMENT_RUN_ID':''}):
   out=Path(directory)/'plus-shadow-output';out.mkdir();(out/'.owner').write_bytes(b'DOGE_PLUS_SHADOW_V1\n');(out/'report.json').write_text('old success');(out/'index.html').write_text('old probability')
   with self.assertRaises(SystemExit):m.main()
   report=json.loads((out/'report.json').read_bytes());self.assertEqual(report['forecasts'],[]);self.assertNotIn('old probability',(out/'index.html').read_text(encoding='utf-8'))
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

if __name__=='__main__':unittest.main()


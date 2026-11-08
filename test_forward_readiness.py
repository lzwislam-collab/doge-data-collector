import importlib,copy,unittest,csv,io,json,os,tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
import phase2_forward_v2 as f

class Readiness(unittest.TestCase):
 def test_grace_never_hides_an_older_hour_missing(self):
  a=self.engine();now=datetime(2026,10,15,0,5,tzinfo=timezone.utc)
  v=a.health([],[],now,now.replace(minute=0)-timedelta(hours=2),[])
  self.assertIn('LATEST_CLOSED_BAR_MISSING',v['errors'])
  v=a.health([],[],now,now.replace(minute=0)-timedelta(hours=1),[])
  self.assertNotIn('LATEST_CLOSED_BAR_MISSING',v['errors'])
 def test_history_receipt_before_close_and_nonfinite_volume_rejected(self):
  a=self.engine()
  raw=(a.ROOT/a.r.SOURCE).read_bytes();rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
  now=max(f.utc(row['first_ingested_utc']) for row in rows)+timedelta(hours=1)
  a.source_state(raw,now)
  for key,value in (('first_ingested_utc',datetime.fromtimestamp(int(rows[0]['open_utc_ms'])/1000,timezone.utc).isoformat()),('volume_base','nan')):
   bad=copy.deepcopy(rows);bad[0][key]=value;buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(bad)
   with self.assertRaises(ValueError):a.source_state(buf.getvalue().encode(),now)
 def engine(self):
  self.assertIsNotNone(importlib.util.find_spec('forward_readiness'),'operational monitor missing')
  return importlib.import_module('forward_readiness')
 def test_before_activation_zero_formal_is_not_missing(self):
  a=self.engine();now=datetime(2026,10,14,12,tzinfo=timezone.utc)
  v=a.health([],[],now,now,[]);self.assertEqual(v['missing_issuance'],0);self.assertFalse(v['errors'])
 def test_matured_missing_issuance_and_score_are_errors(self):
  a=self.engine();start=f.utc(f.policy()['effective_from_utc']);now=start+timedelta(minutes=90)
  v=a.health([],[],now,now,[]);self.assertEqual(v['missing_issuance'],1);self.assertIn('MISSING_ISSUANCE',v['errors'])
  e={'event_id':str(int(start.timestamp()))+'_formal','cohort':'formal','slot_utc':start.isoformat(),'target_utc':(start+timedelta(hours=1)).isoformat()}
  v=a.health([e],[],now,now,[]);self.assertEqual(v['missing_targets'],1);self.assertIn('MISSING_TARGET',v['errors'])
 def test_due_score_near_deadline_is_urgent_not_backfilled(self):
  a=self.engine();start=f.utc(f.policy()['effective_from_utc']);e={'event_id':'1_formal','cohort':'formal','slot_utc':start.isoformat(),'target_utc':(start+timedelta(hours=1)).isoformat()}
  v=a.health([e],[],start+timedelta(minutes=80),start+timedelta(hours=1),[])
  self.assertIn('TARGET_DEADLINE_NEAR',v['errors']);self.assertEqual(v['due_targets'],['1_formal'])
 def test_source_last_closed_hour_not_merely_internal_gap(self):
  a=self.engine();now=datetime(2026,10,14,12,30,tzinfo=timezone.utc)
  v=a.health([],[],now,now-timedelta(hours=1,minutes=30),[])
  self.assertIn('LATEST_CLOSED_BAR_MISSING',v['errors'])
 def event(self):
  return {'id':1,'run_attempt':1,'repository':{'full_name':f.old.REPO},'head_repository':{'full_name':f.old.REPO},
   'head_branch':'main','head_sha':'a'*40,'event':'schedule','status':'completed','conclusion':'failure','path':'.github/workflows/okx_hourly_extreme_history.yml'}
 def test_auxiliary_failure_can_trigger_reverification_when_core_saved(self):
  a=self.engine();run=self.event();jobs=[{'steps':[{'name':'Fail closed before writes on full-history or frozen protocol mutation','conclusion':'success'},{'name':'Persist data and diagnostics','conclusion':'success'}]}]
  self.assertTrue(a.trusted_completion(run,jobs));jobs[0]['steps'][1]['conclusion']='failure';self.assertFalse(a.trusted_completion(run,jobs))
 def test_untrusted_or_changed_attempt_completion_rejected(self):
  a=self.engine();event=self.event()
  for field,value in (('head_branch','feature'),('event','pull_request'),('head_repository',{'full_name':'other/repo'})):
   bad=copy.deepcopy(event);bad[field]=value;self.assertFalse(a.trusted_completion(bad,[]))
  with self.assertRaises(ValueError):a.match_event(event,{**event,'run_attempt':2})
 def test_existing_collector_completion_is_only_trusted_after_success(self):
  a=self.engine();run=self.event();run['path']='.github/workflows/collect.yml';run['conclusion']='success'
  self.assertTrue(a.trusted_completion(run,[]))
  run['conclusion']='failure';self.assertFalse(a.trusted_completion(run,[]))
  run['conclusion']='success';run['head_branch']='feature';self.assertFalse(a.trusted_completion(run,[]))

 def test_no_cycle_when_no_current_issue_or_target_needed(self):
  a=self.engine();now=datetime(2026,10,14,tzinfo=timezone.utc)
  self.assertEqual(a.actions([],[],now,False,False),[])
  start=f.utc(f.policy()['effective_from_utc']);e={'event_id':str(int(start.timestamp()))+'_formal','cohort':'formal','slot_utc':start.isoformat(),'target_utc':(start+timedelta(hours=1)).isoformat()}
  self.assertEqual(a.actions([e],[],start+timedelta(minutes=10),True,True),[])

class CurrentReadiness(unittest.TestCase):
 def setUp(self):
  self.a=importlib.import_module('forward_readiness');self.p=f.policy();self.start=f.utc(self.p['effective_from_utc'])
 def event(self,hours,target_hours=None,cohort='formal'):
  slot=self.start+timedelta(hours=hours)
  return {'event_id':str(int(slot.timestamp()))+'_'+cohort,'cohort':cohort,'slot_utc':slot.isoformat(),
   'target_utc':(self.start+timedelta(hours=target_hours if target_hours is not None else hours+1)).isoformat()}
 def view(self,events,scores,hours,qualified=()):
  now=self.start+timedelta(hours=hours)
  return self.a.health(events,scores,now,now.replace(minute=0,second=0,microsecond=0),qualified)
 def test_healthy_latest_window_does_not_erase_or_fail_for_old_issuance_gap(self):
  e=self.event(2);v=self.view([e],[{'event_id':e['event_id']}],3.5,[e])
  self.assertEqual(v['missing_issuance_ids'],['1792454400_formal'])
  self.assertIn('MISSING_ISSUANCE',v['errors'])
  self.assertEqual(v.get('runtime_errors',v['errors']),[])
 def test_latest_closed_issue_is_required_even_when_new_window_opens(self):
  old=self.event(0)
  for hours in (2.75,4):
   with self.subTest(hours=hours):
    v=self.view([old],[{'event_id':old['event_id']}],hours,[old])
    self.assertIn('MISSING_ISSUANCE',v.get('runtime_errors',[]))
    self.assertEqual(v['runtime_missing_issuance_ids'],['1792461600_formal'])
 def test_older_missing_target_stays_historical_after_latest_target_is_verified(self):
  old,e=self.event(0),self.event(2);v=self.view([old,e],[{'event_id':e['event_id']}],3.5,[e])
  self.assertEqual(v['missing_target_ids'],[old['event_id']])
  self.assertIn('MISSING_TARGET',v['errors']);self.assertEqual(v.get('runtime_errors',v['errors']),[])
 def test_young_event_cannot_hide_the_latest_matured_missing_target(self):
  old,young=self.event(0),self.event(2);v=self.view([old,young],[],2.1)
  self.assertEqual(v.get('runtime_missing_target_ids'),[old['event_id']])
  self.assertIn('MISSING_TARGET',v['runtime_errors'])
  self.assertEqual({e['event_id'] for e in self.a.publication_events([old,young],self.start+timedelta(hours=2.1),self.p)},
   {old['event_id'],young['event_id']})
 def test_matured_score_needs_online_public_evidence_and_canary_cannot_replace_it(self):
  old,young=self.event(0),self.event(2,cohort='canary')
  v=self.view([old,young],[{'event_id':old['event_id']},{'event_id':young['event_id']}],2.1,[young])
  self.assertIn('PUBLIC_DEADLINE_EVIDENCE_MISSING',v.get('runtime_errors',[]))
  self.assertEqual(v['runtime_missing_public_witness_ids'],[old['event_id']])
 def test_target_deadline_exact_boundary_fails_and_near_deadline_remains_urgent(self):
  e=self.event(0)
  near=self.view([e],[],80/60)
  self.assertIn('TARGET_DEADLINE_NEAR',near.get('runtime_errors',[]))
  due=self.view([e],[],85/60)
  self.assertIn('MISSING_TARGET',due.get('runtime_errors',[]))
 def test_before_activation_and_missing_bar_keep_original_boundaries(self):
  now=self.start-timedelta(hours=1)
  v=self.a.health([],[],now,now,[]);self.assertEqual(v.get('runtime_errors'),[])
  v=self.a.health([],[],now,now-timedelta(hours=2),[])
  self.assertIn('LATEST_CLOSED_BAR_MISSING',v['runtime_errors'])
 def legacy(self):
  return {'effective_from_utc':self.start.isoformat(),'horizons_hours':[1,24],
   'slot_steps_hours':{'1':2,'24':25},'max_issue_delay_minutes':45,'target_window_minutes':25}
 def legacy_event(self,hours,horizon):
  e=self.event(hours,hours+horizon);e['event_id']=str(int((self.start+timedelta(hours=hours)).timestamp()))+'_'+str(horizon)
  e['horizon_hours']=horizon;return e
 def test_original18_latest_issuance_uses_45_minutes_not_statistical_maturity(self):
  v=self.a.legacy_runtime_health(self.legacy(),[],[],self.start+timedelta(minutes=45))
  self.assertEqual(set(v['missing_issuance_ids']),{'1792454400_1','1792454400_24'})
 def test_original18_each_horizon_retains_its_latest_missing_target(self):
  e1,e24=self.legacy_event(26,1),self.legacy_event(0,24)
  v=self.a.legacy_runtime_health(self.legacy(),[e1,e24],[{'event_id':e1['event_id']}],self.start+timedelta(hours=27.5))
  self.assertEqual(v['missing_target_ids'],[e24['event_id']]);self.assertIn('ORIGINAL18_MISSING_TARGET',v['errors'])
 def test_original18_healthy_latest_targets_do_not_retry_older_missing_targets(self):
  old1,old24=self.legacy_event(0,1),self.legacy_event(0,24)
  e1,e24=self.legacy_event(48,1),self.legacy_event(25,24)
  v=self.a.legacy_runtime_health(self.legacy(),[old1,old24,e24,e1],
   [{'event_id':e1['event_id']},{'event_id':e24['event_id']}],self.start+timedelta(hours=49.5))
  self.assertEqual(v['errors'],[]);self.assertEqual(v['missing_target_ids'],[])
 def test_runtime_source_gap_fails_but_original_hour_grace_is_preserved(self):
  source={'latest73_ready':False,'frame_errors':['missing latest73 source bars'],
   'latest_closed_utc':self.start.isoformat(),'asset_rows':{'DOGE-USDT':73,'BTC-USDT':73,'ETH-USDT':1}}
  self.assertEqual(self.a.source_runtime_errors(source,self.start+timedelta(hours=1,minutes=5)),[])
  self.assertIn('SOURCE_FRAME_NOT_READY',self.a.source_runtime_errors(source,self.start+timedelta(hours=1,minutes=15)))
  source['frame_errors']=['empty volume basis']
  self.assertIn('SOURCE_FRAME_NOT_READY',self.a.source_runtime_errors(source,self.start+timedelta(hours=1,minutes=5)))
 def test_cli_preserves_historical_output_but_exports_separate_current_gate(self):
  result={'status':'OPERATIONS_ERRORS_PRESERVED','errors':['MISSING_TARGET'],'dispatches':[],
   'runtime_errors':[],'runtime_status':'CURRENT_WINDOW_CLEAR_WITH_HISTORICAL_GAPS'}
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/'outputs'
   with patch.object(self.a,'ROOT',Path(tmp)),patch.object(self.a,'run',return_value=result),patch('sys.argv',['forward_readiness.py','audit']),patch.dict(os.environ,{'GITHUB_OUTPUT':str(out)}):
    self.a.main()
   self.assertIn('has_errors=true\n',out.read_text());self.assertIn('has_runtime_errors=false\n',out.read_text())
   self.assertEqual(json.loads((Path(tmp)/'forward-readiness-output/readiness.json').read_text())['errors'],['MISSING_TARGET'])
 def test_cli_missing_runtime_contract_never_defaults_to_success(self):
  result={'status':'OPERATIONS_ERRORS_PRESERVED','errors':['MISSING_TARGET'],'dispatches':[]}
  with tempfile.TemporaryDirectory() as tmp:
   with patch.object(self.a,'ROOT',Path(tmp)),patch.object(self.a,'run',return_value=result),patch('sys.argv',['forward_readiness.py','audit']),patch.dict(os.environ,{'GITHUB_OUTPUT':str(Path(tmp)/'outputs')}):
    with self.assertRaises(KeyError):self.a.main()
   self.assertIn('KeyError',json.loads((Path(tmp)/'forward-readiness-output/failure.json').read_text())['error'])

if __name__=='__main__':unittest.main()

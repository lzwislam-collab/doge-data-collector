import importlib,copy,unittest,csv,io
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
 def test_no_cycle_when_no_current_issue_or_target_needed(self):
  a=self.engine();now=datetime(2026,10,14,tzinfo=timezone.utc)
  self.assertEqual(a.actions([],[],now,False,False),[])
  start=f.utc(f.policy()['effective_from_utc']);e={'event_id':str(int(start.timestamp()))+'_formal','cohort':'formal','slot_utc':start.isoformat(),'target_utc':(start+timedelta(hours=1)).isoformat()}
  self.assertEqual(a.actions([e],[],start+timedelta(minutes=10),True,True),[])

if __name__=='__main__':unittest.main()

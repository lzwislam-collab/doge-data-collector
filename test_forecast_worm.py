import unittest,json,hashlib,io
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import forecast_worm as w
import contextlib,tempfile
from pathlib import Path
from unittest.mock import patch
NOW=datetime(2026,10,15,12,tzinfo=timezone.utc)
RAW=json.dumps({'event_id':'1792065600_1','issued_at_utc':NOW.isoformat(),'target_utc':(NOW+timedelta(hours=1)).isoformat(),'horizon_hours':1}).encode()
class Body(io.BytesIO):
 def close(self): super().close()
class Bucket:
 def __init__(self): self.objects={};self.bad=False;self.late=False
 def put_object(self,key,raw,headers):
  self.objects[key]=raw
  return SimpleNamespace(headers={'x-oss-version-id':'version1'},request_id='put1',status=200)
 def get_object(self,key,params):
  r=Body(b'bad' if self.bad else self.objects[key]);r.headers={'x-oss-version-id':'version1','date':'Thu, 15 Oct 2026 12:00:01 GMT','last-modified':'Thu, 15 Oct 2026 12:00:00 GMT','x-oss-server-side-encryption':'AES256'}
  if self.late:r.headers['date']='Thu, 15 Oct 2026 13:00:01 GMT'
  r.request_id='get1';return r
class Tests(unittest.TestCase):
 def event(self,issued,horizon=1,track='original18'):
  e={'event_id':str(int(issued.timestamp()))+'_'+(str(horizon) if track=='original18' else 'formal'),'issued_at_utc':issued.isoformat(),'target_utc':(issued+timedelta(hours=horizon)).isoformat()}
  if track=='original18':e['horizon_hours']=horizon
  else:e.update(cohort='formal',horizon_seconds=horizon*3600,included_in_formal_statistics=True)
  return e
 def receipt(self,e,track):
  raw=json.dumps(e).encode();issued=w.utc(e['issued_at_utc']);digest=w.sha(raw)
  return {'status':'PRE_TARGET_WORM_VERIFIED','track':track,'event_id':e['event_id'],'issued_at_utc':e['issued_at_utc'],'target_utc':e['target_utc'],'sha256':digest,'byte_count':len(raw),'bucket':w.inventory.BUCKET,'endpoint':w.inventory.ENDPOINT,'key':w.PREFIX+track+'/'+e['event_id']+'/'+digest+'.json','version_id':'version1','readback_request_id':'get1','server_last_modified_utc':issued.isoformat(),'server_response_date_utc':(issued+timedelta(seconds=1)).isoformat(),'verified_at_utc':(issued+timedelta(seconds=2)).isoformat(),'calculated_retain_until_utc':(issued+timedelta(days=732)).isoformat(),'historical_backfill':False}
 def run_events(self,rows,*,retain_error=None):
  class Clock(datetime):
   @classmethod
   def now(cls,tz=None):return NOW+timedelta(days=4)
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp)
   for track,e,receipt in rows:
    base='data/learned_shadow_v1/events' if track=='original18' else 'data/phase2_forward_v2/events'
    path=root/base/(e['event_id']+'.json');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(json.dumps(e).encode())
    if receipt is not None:
     dest=root/'audit/forecast_worm'/track/(e['event_id']+'.json');dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(receipt))
   bucket=Bucket()
   with contextlib.ExitStack() as stack:
    for p in [patch.object(w,'ROOT',root),patch.object(w,'verify_frozen'),patch.object(w,'connection',return_value=bucket),patch.object(w.inventory,'check_bucket',return_value={'state':'Locked','retention_days':732,'worm_id':'worm'}),patch.object(w,'datetime',Clock),patch.dict(w.os.environ,{'EXTEND_RETENTION_DAYS':'0','ENGINEERING_PROBE':'false'}),contextlib.redirect_stdout(io.StringIO())]:stack.enter_context(p)
    if retain_error is not None:stack.enter_context(patch.object(w,'retain',side_effect=retain_error))
    failed=w.run()
   report=json.loads((root/'forecast-worm-output/result.json').read_bytes())
   return failed,report,bucket
 def test_older_missing_receipt_is_history_only_after_same_horizon_bound_receipt(self):
  old=self.event(NOW);new=self.event(NOW+timedelta(hours=2))
  failed,r,b=self.run_events([('original18',old,None),('original18',new,self.receipt(new,'original18'))])
  self.assertFalse(failed);self.assertEqual(r['status'],'BLOCKED_PRE_TARGET_WORM')
  self.assertEqual(r['errors'],r['historical_errors']);self.assertEqual(r['runtime_errors'],[])
  self.assertEqual(r['expired_unproven'],['original18/'+old['event_id']]);self.assertFalse(b.objects)
 def test_latest_missing_receipt_stays_current_even_if_old(self):
  old=self.event(NOW);latest=self.event(NOW+timedelta(hours=2))
  failed,r,b=self.run_events([('original18',old,self.receipt(old,'original18')),('original18',latest,None)])
  self.assertTrue(failed);self.assertEqual(r['errors'],r['runtime_errors']);self.assertEqual(r['historical_errors'],[]);self.assertFalse(b.objects)
 def test_newer_missing_receipt_does_not_clear_older_missing_receipt(self):
  old=self.event(NOW);new=self.event(NOW+timedelta(hours=2))
  failed,r,_=self.run_events([('original18',old,None),('original18',new,None)])
  self.assertTrue(failed);self.assertEqual(len(r['runtime_errors']),2);self.assertEqual(r['historical_errors'],[])
 def test_receipt_from_other_horizon_cannot_clear_current_obligation(self):
  old=self.event(NOW);other=self.event(NOW+timedelta(hours=2),24)
  failed,r,_=self.run_events([('original18',old,None),('original18',other,self.receipt(other,'original18'))])
  self.assertTrue(failed);self.assertEqual(r['errors'],r['runtime_errors']);self.assertEqual(r['historical_errors'],[])
 def test_receipt_from_other_track_cannot_clear_current_obligation(self):
  old=self.event(NOW);other=self.event(NOW+timedelta(hours=2),track='r2')
  failed,r,_=self.run_events([('original18',old,None),('r2',other,self.receipt(other,'r2'))])
  self.assertTrue(failed);self.assertEqual(r['errors'],r['runtime_errors'])
 def test_r2_uses_original_horizon_seconds(self):
  old=self.event(NOW,track='r2');new=self.event(NOW+timedelta(hours=2),track='r2')
  failed,r,_=self.run_events([('r2',old,None),('r2',new,self.receipt(new,'r2'))])
  self.assertFalse(failed);self.assertEqual(r['errors'],r['historical_errors'])
 def test_other_r2_horizon_cannot_close_missed_hourly_obligation(self):
  old=self.event(NOW,track='r2');other=self.event(NOW+timedelta(hours=2),24,'r2')
  failed,r,_=self.run_events([('r2',old,None),('r2',other,self.receipt(other,'r2'))])
  self.assertTrue(failed);self.assertEqual(r['historical_errors'],[])
 def test_future_receipt_cannot_close_latest_issued_obligation(self):
  old=self.event(NOW);future=self.event(NOW+timedelta(days=5))
  failed,r,_=self.run_events([('original18',old,None),('original18',future,self.receipt(future,'original18'))])
  self.assertTrue(failed);self.assertEqual(r['historical_errors'],[])
  self.assertEqual(r['current_obligations'][0]['event_id'],old['event_id'])
 def test_future_receipt_times_cannot_close_current_custody(self):
  checked=NOW+timedelta(days=4)
  old=self.event(NOW,24);new=self.event(checked-timedelta(hours=1),24)
  for fields in [('verified_at_utc',),('server_response_date_utc','verified_at_utc'),('server_last_modified_utc','server_response_date_utc','verified_at_utc')]:
   with self.subTest(fields=fields):
    receipt=self.receipt(new,'original18')
    for i,field in enumerate(fields):receipt[field]=(checked+timedelta(hours=1,seconds=i)).isoformat()
    failed,r,b=self.run_events([('original18',old,None),('original18',new,receipt)])
    self.assertTrue(failed);self.assertEqual(r['historical_errors'],[]);self.assertFalse(b.objects)
    self.assertTrue(any(e['status']=='BLOCKED_EXISTING_RECEIPT_BINDING' for e in r['runtime_errors']))
 def test_incomplete_or_changed_receipt_never_supersedes_missing_event(self):
  old=self.event(NOW);new=self.event(NOW+timedelta(hours=2))
  for field,bad in [('sha256','bad'),('event_id','wrong'),('track','r2'),('status','WAIT'),('issued_at_utc',old['issued_at_utc']),('target_utc',old['target_utc']),('version_id','null'),('server_response_date_utc',new['target_utc']),('verified_at_utc',new['target_utc']),('key','wrong'),('readback_request_id','')]:
   with self.subTest(field=field):
    receipt=self.receipt(new,'original18');receipt[field]=bad
    failed,r,_=self.run_events([('original18',old,None),('original18',new,receipt)])
    self.assertTrue(failed);self.assertEqual(r['historical_errors'],[])
    self.assertTrue(any(e['event_id']==old['event_id'] for e in r['runtime_errors']))
 def test_active_upload_failure_stays_runtime_failure(self):
  old=self.event(NOW);new=self.event(NOW+timedelta(hours=2));active=self.event(NOW+timedelta(days=4))
  failed,r,_=self.run_events([('original18',old,None),('original18',new,self.receipt(new,'original18')),('original18',active,None)],retain_error=RuntimeError('provider unavailable'))
  self.assertTrue(failed);self.assertEqual(len(r['historical_errors']),1)
  self.assertEqual(r['runtime_errors'][0]['event_id'],active['event_id']);self.assertEqual(r['runtime_errors'][0]['type'],'RuntimeError')
 def test_pre_target_pinned_readback(self):
  b=Bucket();r=w.retain(b,RAW,'original18',{'state':'Locked','retention_days':732,'worm_id':'worm'},NOW)
  self.assertEqual(r['status'],'PRE_TARGET_WORM_VERIFIED');self.assertEqual(r['sha256'],hashlib.sha256(RAW).hexdigest());self.assertIn('/original18/',r['key']);self.assertEqual(r['version_id'],'version1')
 def test_subsecond_issuance_accepts_oss_second_precision(self):
  e=json.loads(RAW);issued=NOW+timedelta(microseconds=123456);e['issued_at_utc']=issued.isoformat();e['target_utc']=(issued+timedelta(hours=1)).isoformat()
  r=w.retain(Bucket(),json.dumps(e).encode(),'original18',{'state':'Locked','retention_days':732,'worm_id':'worm'},issued)
  self.assertEqual(r['status'],'PRE_TARGET_WORM_VERIFIED')
 def test_old_object_extension_changes_horizon_not_historical_proof(self):
  b=Bucket();b.objects['old']=RAW
  prior={'key':'old','version_id':'version1','readback_sha256':hashlib.sha256(RAW).hexdigest(),'byte_count':len(RAW)}
  self.assertFalse(w.read_old_version(b,prior,{'retention_days':730})['fixed_horizon_satisfied'])
  extended=w.read_old_version(b,prior,{'retention_days':732})
  self.assertTrue(extended['fixed_horizon_satisfied'])
  self.assertFalse(extended['historical_pre_target_retention_verified'])
  self.assertFalse(extended['object_rewritten'])
 def test_report_live_rechecks_exact_original_version(self):
  import tempfile
  from pathlib import Path
  from unittest.mock import patch
  lock={'state':'Locked','retention_days':732,'worm_id':'worm'};bucket=Bucket();receipt=w.retain(bucket,RAW,'original18',lock,NOW)
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);event=root/'data/learned_shadow_v1/events/1792065600_1.json';event.parent.mkdir(parents=True);event.write_bytes(RAW)
   proof=root/'audit/forecast_worm/original18/1792065600_1.json';proof.parent.mkdir(parents=True);proof.write_text(json.dumps(receipt))
   with patch.dict(w.os.environ,{'ALIBABA_CLOUD_SECURITY_TOKEN':'unit-token'}),patch.object(w,'connection',return_value=bucket),patch.object(w.inventory,'check_bucket',return_value=lock):
    panel=w.report_panel(root,[{'track':'original18','event_id':'1792065600_1'}],True)
    self.assertTrue(panel['external_immutable_retention_verified'])
    bucket.bad=True
    self.assertFalse(w.report_panel(root,[{'track':'original18','event_id':'1792065600_1'}],True)['external_immutable_retention_verified'])
 def test_missed_formal_deadline_stays_blocked_without_backfill(self):
  import tempfile
  from pathlib import Path
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);path=root/'data/learned_shadow_v1/events/1_1.json';path.parent.mkdir(parents=True)
   path.write_text(json.dumps({'event_id':'1_1','issued_at_utc':'2000-01-01T00:00:00Z','target_utc':'2000-01-01T01:00:00Z','horizon_hours':1}))
   with patch.object(w,'ROOT',root),patch.object(w,'verify_frozen'),patch.object(w,'connection',return_value=Bucket()),patch.object(w.inventory,'check_bucket',return_value={'state':'Locked','retention_days':732,'worm_id':'worm'}),patch.dict(w.os.environ,{'EXTEND_RETENTION_DAYS':'0','ENGINEERING_PROBE':'false'}):
    self.assertTrue(w.run())
   self.assertFalse((root/'audit/forecast_worm/original18/1_1.json').exists())
 def test_history_never_retroactively_approved(self):
  with self.assertRaises(ValueError):w.retain(Bucket(),RAW,'original18',{'retention_days':732},NOW+timedelta(hours=1))
 def test_corrupt_full_readback_rejected(self):
  b=Bucket();b.bad=True
  with self.assertRaises(ValueError):w.retain(b,RAW,'original18',{'state':'Locked','retention_days':732,'worm_id':'worm'},NOW)
 def test_server_completion_after_target_rejected(self):
  b=Bucket();b.late=True
  with self.assertRaises(ValueError):w.retain(b,RAW,'original18',{'state':'Locked','retention_days':732,'worm_id':'worm'},NOW)
 def test_fixed_horizon_not_relaxed(self):
  with self.assertRaises(ValueError):w.retain(Bucket(),RAW,'original18',{'state':'Locked','retention_days':730,'worm_id':'worm'},NOW)
 def test_canary_cannot_enter_r2(self):
  e=json.loads(RAW);e.update(cohort='canary',horizon_seconds=3600)
  with self.assertRaises(ValueError):w.retain(Bucket(),json.dumps(e).encode(),'r2',{'state':'Locked','retention_days':732,'worm_id':'worm'},NOW)
 def test_legacy_never_accepted(self):
  with self.assertRaises(ValueError):w.retain(Bucket(),RAW,'legacy',{'retention_days':732},NOW)
if __name__=='__main__':unittest.main()

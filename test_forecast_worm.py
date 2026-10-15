import unittest,json,hashlib,io
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import forecast_worm as w
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

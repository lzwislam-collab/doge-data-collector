import importlib,copy,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
import phase2_forward_v2 as f
from unittest.mock import patch

class Runtime(unittest.TestCase):
 def engine(self):
  self.assertIsNotNone(importlib.util.find_spec('phase3_runtime'),'independent registration runtime not implemented')
  return importlib.import_module('phase3_runtime')
 def witness(self):
  a=self.engine();at=datetime(2026,10,14,11,tzinfo=timezone.utc);head='a'*40;files={'a':b'fixed'}
  run={'id':1,'repository':{'full_name':f.old.REPO},'path':a.WORKFLOW,'head_branch':'main','event':'push',
   'head_sha':head,'status':'completed','conclusion':'success','created_at':'2026-10-14T10:00:00Z',
   'audit_job':{'name':'independent','status':'completed','conclusion':'success','started_at':'2026-10-14T10:01:00Z','completed_at':'2026-10-14T10:03:00Z'}}
  return a,at,files,run
 def test_exact_main_assessment_publication_before_effective_and_first_forecast(self):
  a,at,files,run=self.witness();w=a.registration_witness(files,[run],lambda h,p:b'fixed',at,[])
  self.assertEqual(w['run_id'],1)
  self.assertIsNone(a.registration_witness(files,[run],lambda h,p:b'changed',at,[]))
 def test_branch_failed_or_late_registration_cannot_qualify(self):
  a,at,files,run=self.witness()
  for key,value in (('head_branch','feature'),('conclusion','failure'),('path',f.WORKFLOW)):
   r=copy.deepcopy(run);r[key]=value;self.assertIsNone(a.registration_witness(files,[r],lambda h,p:b'fixed',at,[]))
  events=[{'cohort':'formal','issued_at_utc':'2026-10-14T10:02:00Z'}]
  self.assertIsNone(a.registration_witness(files,[run],lambda h,p:b'fixed',at,events))
 def test_registration_requires_exact_file_set_and_hashes(self):
  a=self.engine()
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):a.verify_files(Path(d),{'sha256_lf':{}})
 def test_routine_never_claims_full_forecast_qualification(self):
  a=self.engine();self.assertFalse(a.full_scope('routine',{'network_audit_covers_all_events':True}))
  self.assertTrue(a.full_scope('full',{'network_audit_covers_all_events':True}))
  self.assertFalse(a.full_scope('full',{'network_audit_covers_all_events':False}))
 def test_new_registered_modules_cannot_change_original_r2_package(self):
  a=self.engine();self.assertFalse(set(a.NEW_FILES)&set(__import__('phase2_forward_runtime_v2').FIXED))
 def test_regular_registered_code_uses_hash_guard_not_append_ledger_prefix(self):
  a=self.engine()
  with patch.object(a.forward,'guard'),patch.object(a.forward.evidence_append_guard,'guard',side_effect=AssertionError('code is not an append ledger')):
   a.guard()
 def test_rolling_audit_verifies_actual_published_archive_layout(self):
  a=self.engine()
  try:result=a.rolling_audit()
  except FileNotFoundError:self.fail('published archive must be verified without assuming uncommitted artifact layout')
  self.assertEqual(result['verified_models'],1056);self.assertTrue(result['archive_verified'])
  self.assertFalse(result['historical_results_authorize_promotion'])
 def test_registration_search_remains_before_window_after_many_daily_runs(self):
  a=self.engine();requests=[]
  def api(path):requests.append(path);return {'workflow_runs':[]}
  with patch.object(a.forward.frozen,'api',side_effect=api):a.registration_runs([])
  self.assertIn('created=',requests[0]);self.assertIn('2026-10-20',requests[0])

if __name__=='__main__':unittest.main()

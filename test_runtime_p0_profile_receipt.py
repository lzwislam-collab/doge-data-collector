"""The whole CLI gate must prove actual fresh bounded P0 transport ran."""
import importlib.util,json,unittest
from pathlib import Path
HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('p0_profile_under_test',HERE/'runtime_audit_pipeline_profile.py');profile=importlib.util.module_from_spec(spec);spec.loader.exec_module(profile)
class P0ProfileReceiptTests(unittest.TestCase):
 def setUp(self):
  self.base={'kind':'runtime_publication_state_validation','mode':'native_batch','status':'ok','validation_status':'ok','native_batch_calls':1}
  self.p0={'kind':'runtime_p0_inventory_transport','implementation':'p0_fresh_inventory_window4_v1','mode':'fresh_bounded_inventory','status':'ok','installed':True,'module_sha256':'b'*64,'window':4,'native_calls':1,'fallback_calls':0,'real_gets':30,'days':29}
 def check(self,*rows):return profile.validate_native_diagnostics('\n'.join(json.dumps(r) for r in rows),'p0_operational_audit.py',expected_p0_sha256='b'*64)
 def test_exact_one_successful_actual_helper_receipt_is_required(self):
  result=self.check(self.base,self.p0);self.assertEqual(len(result),2);self.assertIn(self.p0,result)
  for rows in ((self.base,),(self.base,self.p0,self.p0)):
   with self.assertRaises(profile.ProfileError):self.check(*rows)
 def test_unknown_implementation_fallback_no_native_and_unbounded_values_fail(self):
  for updates in ({'implementation':'unknown'},{'fallback_calls':1},{'native_calls':0},{'installed':False},{'status':'error'},{'module_sha256':'c'*64},{'real_gets':True},{'days':2**32},{'window':5},{'secret':'PRIVATE_TOKEN'}):
   with self.subTest(updates=updates),self.assertRaises(profile.ProfileError):self.check(self.base,dict(self.p0,**updates))

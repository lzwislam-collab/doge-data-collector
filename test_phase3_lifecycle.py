import copy,importlib,unittest
from datetime import datetime,timezone,timedelta

class Lifecycle(unittest.TestCase):
 def engine(self):
  self.assertIsNotNone(importlib.util.find_spec('phase3_lifecycle'),'paper lifecycle not implemented')
  return importlib.import_module('phase3_lifecycle')
 def data(self):
  a=self.engine();reg=a.registry();at=datetime(2026,10,20,tzinfo=timezone.utc);s=a.genesis(reg)
  return a,reg,at,s
 def test_shadow_has_no_production_side_effect_and_hash_chain(self):
  a,reg,at,s=self.data();t=a.transition(s,'start_shadow',reg,at,{})
  self.assertEqual(t['status'],'SHADOW');self.assertEqual(t['previous_state_sha256'],s['state_sha256'])
  self.assertFalse(t['production_pointer_changed']);self.assertFalse(t['real_orders_authorized'])
 def test_approval_flags_alone_cannot_activate(self):
  a,reg,at,s=self.data();s=a.transition(s,'start_shadow',reg,at,{})
  with self.assertRaises(ValueError):a.transition(s,'paper_activate',reg,at,{'approved':True})
 def test_complete_paper_review_requires_storage_and_exact_version(self):
  a,reg,at,s=self.data();s=a.transition(s,'start_shadow',reg,at,{})
  ev=a.synthetic_review_fixture(reg)
  s=a.transition(s,'independent_review',reg,at,ev);self.assertEqual(s['status'],'REVIEW_READY')
  for key in ('external_immutable_retention_verified','reviewer_attestation','model_identity'):
   bad=copy.deepcopy(ev);bad[key]=False if key.endswith('verified') else 'wrong'
   with self.assertRaises(ValueError):a.transition(s,'paper_activate',reg,at,bad)
  t=a.transition(s,'paper_activate',reg,at,ev);self.assertEqual(t['status'],'PAPER_ACTIVE')
  self.assertFalse(t['production_pointer_changed'])
 def test_expiry_requires_fail_closed_and_rolls_back_exact_frozen_package(self):
  a,reg,at,s=self.data();ev=a.synthetic_review_fixture(reg)
  for command in ('start_shadow','independent_review','paper_activate'):s=a.transition(s,command,reg,at,ev)
  expired=datetime(2027,2,12,tzinfo=timezone.utc);t=a.transition(s,'health_check',reg,expired,{'integrity_ok':True})
  self.assertEqual(t['status'],'ROLLED_BACK');self.assertEqual(t['paper_pointer'],reg['frozen_fallback_package_sha256'])
 def test_tampering_repetition_and_unsafe_registry_rejected(self):
  a,reg,at,s=self.data();bad=copy.deepcopy(s);bad['status']='PAPER_ACTIVE'
  with self.assertRaises(ValueError):a.transition(bad,'health_check',reg,at,{})
  s=a.transition(s,'start_shadow',reg,at,{})
  with self.assertRaises(ValueError):a.transition(s,'start_shadow',reg,at,{})
  bad=copy.deepcopy(reg);bad['frozen_fallback_package_sha256']='0'*64
  with self.assertRaises(ValueError):a.transition(s,'manual_stop',bad,at,{})
 def test_shadow_missing_integrity_evidence_fails_closed(self):
  a,reg,at,s=self.data();s=a.transition(s,'start_shadow',reg,at,{})
  self.assertEqual(a.transition(s,'health_check',reg,at,{})['status'],'REJECTED')
 def test_synthetic_drill_is_explicit_and_not_a_real_approval(self):
  a,reg,at,s=self.data();drill=a.drill(reg);self.assertTrue(drill['synthetic']);self.assertFalse(drill['actual_production_switch'])
  self.assertEqual(drill['final_status'],'ROLLED_BACK')

if __name__=='__main__':unittest.main()

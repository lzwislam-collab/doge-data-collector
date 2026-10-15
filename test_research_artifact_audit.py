import copy,importlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

class ResearchAudit(unittest.TestCase):
 def module(self):return importlib.import_module('research_artifact_audit')
 def fixture(self):
  flags={k:False for k in ('production_promotion_authorized','real_orders_authorized','validated_70pct','historical_pit_validated','historical_holdout_pristine','cost_net_return_validated','test_labels_used_for_selection')}
  fold={'fold':0,'selected_candidate':'a','models':{'a':{}},'validation':{'a':{'n':1,'brier':.1}},'windows':{'train':{'start_index':0,'end_index':2}},'events':[{'selected_candidate':'a','selected_setting':'a','selected_p':.7,'candidate_p':{'a':.7},'y':1}]}
  result={**flags,'status':'HISTORICAL_RESEARCH_ONLY','horizons':{'1':{'folds':[fold]}}}
  registry={'production_promotion_authorized':False,'real_orders_authorized':False,'status':'RESEARCH_ONLY','future_preregistration_required':True,'candidate_models':[{'horizon_hours':1,'fold':0,'hypothesis_id':'a','status':'RESEARCH_ONLY','production_promotion_authorized':False,'training_window':fold['windows']['train']}]}
  return result,registry
 def test_metric_replay_accepts_only_serialization_roundoff(self):
  m=self.module();self.assertTrue(m.metric_equal({'v':.123456789012},{'v':.123456789013}));self.assertFalse(m.metric_equal({'v':.12},{'v':.13}));self.assertFalse(m.metric_equal(False,0))
 def test_v2_identity_does_not_depend_on_draft_file_existence(self):
  m=self.module();self.assertTrue(m.is_v2({'identity':{'code_sha256_lf':{'phase2_research_v2.py':'abc'}}}));self.assertFalse(m.is_v2({'identity':{'code_sha256_lf':{'phase2_research.py':'abc'}}}))
 def test_v2_extra_metrics_replayed_from_saved_same_events(self):
  m=self.module();e={'y':1,'selected_p':.6,'majority_p':.5,'selected_setting':'0_raw','candidate_p':{'0_raw__full':.6},'regime':'UP_HIGH_VOL','event_id':'x'}
  for group in m.v1.library.GROUPS:e['candidate_p']['0_raw__drop_'+group]=.5
  hr={'nonoverlap_events':[e],'folds':[{'fold':0,'events':[e]}]}
  expected=m.v2_metrics(hr);self.assertIn('factor_stability',expected);self.assertEqual(expected['selected_probability_metrics']['n'],1)
  hr.update(expected);m.check_v2_metrics(hr);hr['selected_probability_metrics']['brier']=.9
  with self.assertRaises(ValueError):m.check_v2_metrics(hr)
 def test_valid_research_is_still_only_research(self):
  m=self.module();r,g=self.fixture();self.assertTrue(m.check_semantics(r,g))
 def test_rehashed_authorization_claims_rejected_at_every_level(self):
  m=self.module()
  for target,key,value in [('research','real_orders_authorized',True),('research','validated_70pct',True),('research','historical_pit_validated',True),('registry','real_orders_authorized',True),('candidate','production_promotion_authorized',True),('candidate','status','PRODUCTION_APPROVED')]:
   r,g=self.fixture();obj={'research':r,'registry':g,'candidate':g['candidate_models'][0]}[target];obj[key]=value
   with self.subTest(target=target,key=key),self.assertRaises(ValueError):m.check_semantics(r,g)
 def test_selected_probability_and_training_record_cannot_drift(self):
  m=self.module();r,g=self.fixture();r['horizons']['1']['folds'][0]['events'][0]['selected_p']=.9
  with self.assertRaises(ValueError):m.check_semantics(r,g)
  r,g=self.fixture();g['candidate_models'][0]['training_window']={'start_index':5}
  with self.assertRaises(ValueError):m.check_semantics(r,g)
 def test_phase1_output_must_be_new_and_outside_asset_namespaces(self):
  m=self.module()
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'existing').mkdir();(root/'existing/research.json').write_text('keep')
   for out in (root/'existing',root/'data/new-output',root/'research/new-output',root):
    with self.subTest(out=out),self.assertRaises(ValueError):m.fresh_output(root,out)
   self.assertEqual(m.fresh_output(root,root/'phase1-output'),root/'phase1-output')

if __name__=='__main__':unittest.main()

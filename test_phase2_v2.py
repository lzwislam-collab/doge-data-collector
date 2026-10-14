import copy,importlib.util,json,tempfile,unittest
from pathlib import Path
import numpy as np
import test_phase2_research as fixtures

@unittest.skipUnless(importlib.util.find_spec('sklearn'),'V2 pinned training runtime not installed')
class V2Tests(unittest.TestCase):
 def module(self):
  self.assertIsNotNone(importlib.util.find_spec('phase2_research_v2'),'V2 must exist')
  import phase2_research_v2 as m
  return m
 def frame(self):return fixtures.Phase2ResearchTests().frame(550)
 def policy(self):
  p=fixtures.Phase2ResearchTests().policy();p.update(calibration_bars=30,max_fits=1200,tree_estimators=5,tree_min_leaf=5,
   min_validation_gain=.002,min_regime_validation_n=20,max_regime_harm=.01)
  return p
 def test_88_fixed_candidates_and_budget(self):self.assertEqual(len(self.module().family()),88)
 def test_purged_inner_split(self):
  m=self.module();f=m.library.rows(self.frame(),24,'candle_close_research');fit,cal=m.inner([r for r in f[100:200] if r],24,self.policy())
  self.assertLess(fit[-1]['label_available_ms']+2*3600000,cal[0]['anchor_ms']);self.assertEqual(len(cal),30)
 def test_calibration_does_not_change_base_model(self):
  m=self.module();x=[[1.],[2.],[3.],[4.]];b=m.models.fit_base(m.SETTINGS[0],x,[0,1,0,1],self.policy());old=copy.deepcopy(b)
  m.models.calibrate(b,[[5.],[6.],[7.]],[1,1,1],self.policy());self.assertEqual(b,old)
 def test_tree_json_matches_official_sklearn_probability(self):
  m=self.module();from sklearn.ensemble import GradientBoostingClassifier
  x=np.column_stack([np.arange(120)/120,np.sin(np.arange(120))]);y=(np.arange(120)%3==0).astype(int);p=self.policy()
  official=GradientBoostingClassifier(n_estimators=5,max_depth=2,min_samples_leaf=5,learning_rate=.05,subsample=1,random_state=p['seed'],n_iter_no_change=None).fit(x,y)
  saved=m.models.fit_base(m.SETTINGS[2],x,y,p);z=np.vstack([x,[[100,-100]]])
  self.assertTrue(np.allclose(m.models.predict_base(saved,z),official.predict_proba(z)[:,1],atol=2e-12,rtol=0))
 def test_single_class_tree_is_valid_constant_model(self):
  m=self.module();b=m.models.fit_base(m.SETTINGS[2],[[1.],[2.]],[1,1],self.policy())
  self.assertTrue(all(0<p<1 for p in m.models.predict_base(b,[[3.],[4.]])))
 def test_probability_metrics_include_logloss_and_same_event_baseline(self):
  m=self.module();v=m.probability_metrics([0,1],[.5,.5]);self.assertEqual(v['brier'],.25);self.assertAlmostEqual(v['logloss'],np.log(2));self.assertEqual(v['n'],2)
 def test_test_mutation_does_not_change_model_calibration_or_choice(self):
  m=self.module();f=self.frame();a=m.experiment(f,self.policy());fold=a['horizons']['1']['folds'][0]
  start=fold['windows']['test']['start_index']+1;f['DOGE-USDT']['close'][start:]=[2]*(550-start)
  b=m.experiment(f,self.policy())['horizons']['1']['folds'][0]
  self.assertEqual(fold['models'],b['models']);self.assertEqual(fold['selected_candidate'],b['selected_candidate']);self.assertEqual(fold['validation'],b['validation'])
 def test_all_probabilities_and_variants_have_same_events(self):
  m=self.module();r=m.experiment(self.frame(),self.policy());self.assertFalse(r['production_promotion_authorized'])
  for hr in r['horizons'].values():
   for f in hr['folds']:
    for e in f['events']:self.assertEqual(len(e['candidate_p']),88);self.assertTrue(all(0<=v<=1 for v in e['candidate_p'].values()))
   self.assertEqual(hr['probability_baselines']['half']['n'],hr['nonoverlap_n']);self.assertEqual(hr['probability_baselines']['half']['brier'],.25)
   self.assertEqual(len(hr['factor_stability']),5)
 def test_budget_rejected_before_fitting(self):
  p=self.policy();p['max_fits']=1
  with self.assertRaises(ValueError):self.module().experiment(self.frame(),p)
 def test_future_draft_ignores_test_scores(self):
  m=self.module();p=self.policy();p['min_regime_validation_n']=1
  f={'fold':0,'selected_candidate':'x','models':{'x':{'test':1}},'validation':{'x':{'n':10,'brier':.1}},
     'validation_probability_baselines':{'half':{'brier':.25},'majority':{'brier':.2}},
     'validation_regimes':{s:{'x':{'n':2,'brier':.1}} for s in m.STATES},'events':[]}
  a=m.protocol.prepare({'horizons':{'1':{'folds':[f]}}},p);f['events']=[{'y':0,'selected_p':.999}]
  self.assertEqual(a,m.protocol.prepare({'horizons':{'1':{'folds':[f]}}},p));self.assertFalse(a['auto_issuance_authorized'])
 def test_weak_or_missing_regime_cannot_enter_future_draft(self):
  m=self.module();f={'fold':0,'selected_candidate':'x','models':{'x':{}},'validation':{'x':{'n':10,'brier':.251}},
    'validation_probability_baselines':{'half':{'brier':.25},'majority':{'brier':.2}},'validation_regimes':{}}
  r=m.protocol.prepare({'horizons':{'1':{'folds':[f]}}},self.policy());self.assertEqual(r['candidates'],[]);self.assertFalse(r['production_promotion_authorized'])
 def test_missing_receipts_stay_wait(self):
  m=self.module();f=self.frame();p=self.policy();p['availability_mode']='receipt_time';f['DOGE-USDT']['seen']=[f['times'][-1]+7200000]*550
  r=m.experiment(f,p);self.assertEqual(r['actual_fits'],0);self.assertEqual(m.protocol.prepare(r,p)['candidates'],[])
 def test_tree_float32_input_is_compared_to_exact_double_threshold(self):
  m=self.module();x=float(np.nextafter(np.float32(.5),np.float32(1)))
  tree={'left':[1,-1,-1],'right':[2,-1,-1],'feature':[0,-2,-2],
        'threshold_hex':[float(.50000003).hex(),float(-2).hex(),float(-2).hex()],
        'value_hex':[float(0).hex(),float(-2).hex(),float(2).hex()]}
  saved={'kind':'gradient_boosting','feature_count':1,'initial_logit_hex':float(0).hex(),'learning_rate_hex':float(1).hex(),'trees':[tree]}
  self.assertGreater(m.models.predict_base(saved,[[x]])[0],.5)
 def test_invalid_fractional_training_label_is_rejected(self):
  m=self.module()
  with self.assertRaises(ValueError):m.models.fit_base(m.SETTINGS[2],[[1.],[2.]],[.2,1],self.policy())
 def test_future_draft_verification_requires_semantics_not_only_manifest_hash(self):
  m=self.module()
  verify=getattr(m,'verify_output',m.v1.verify_output)
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp);fixtures.Phase2ResearchTests().output_fixture(out)
   research=json.loads((out/'research.json').read_bytes());research['policy']=self.policy()
   fold=research['horizons']['1']['folds'][0];fold.update(validation_probability_baselines={'half':{'brier':.25},'majority':{'brier':.2}},validation_regimes={})
   (out/'research.json').write_bytes(m.encode(research));receipt=json.loads((out/'receipt.json').read_bytes());receipt['result_sha256']=m.sha((out/'research.json').read_bytes())
   (out/'receipt.json').write_bytes(m.encode(receipt))
   draft=m.protocol.prepare(research,research['policy']);draft['experiment_id']=research['experiment_id'];(out/'future_protocol_draft.json').write_bytes(m.encode(draft))
   def manifest():
    (out/'manifest.json').write_bytes(m.encode({'experiment_id':research['experiment_id'],'objects':{p.relative_to(out).as_posix():m.sha(p.read_bytes()) for p in out.rglob('*') if p.is_file() and p.name!='manifest.json'}}))
   manifest();self.assertTrue(verify(out))
   draft['auto_issuance_authorized']=True;(out/'future_protocol_draft.json').write_bytes(m.encode(draft));manifest()
   with self.assertRaises(ValueError):verify(out)
   (out/'future_protocol_draft.json').unlink();manifest()
   with self.assertRaises(ValueError):verify(out)

if __name__=='__main__':unittest.main()

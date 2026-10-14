import copy, importlib, math, unittest
from datetime import timedelta
import phase2_forward_v2 as f

class Independent(unittest.TestCase):
 def engine(self):
  self.assertIsNotNone(importlib.util.find_spec('phase3_independent'), 'independent assessor not implemented')
  return importlib.import_module('phase3_independent')
 def cohort(self):
  a=self.engine();p=a.policy();start=f.utc(f.policy()['effective_from_utc']);events=[];scores=[];q=[]
  for i in range(1080):
   slot=start+timedelta(seconds=i*7200);y=i%2;key=str(int(slot.timestamp()))+'_formal';at=slot+timedelta(minutes=5)
   events.append({'event_id':key,'cohort':'formal','included_in_formal_statistics':True,'slot_utc':slot.isoformat(),
    'issued_at_utc':at.isoformat(),'target_utc':(at+timedelta(hours=1)).isoformat(),'horizon_seconds':3600,
    'protocol_id':f.policy()['protocol_id'],'model_identity':p['model_identity'],'regime':a.STATES[i%4],
    'candidate_p':{'selected':.99 if y else .01,'frozen_full':.5,'constant_05':.5,'frozen_majority':.5},'frozen_rule_direction':1-y})
   costs={k:{'net_return':.02 if k=='selected' else 0.,'stress_net_return':.01 if k=='selected' else 0.} for k in ('selected',*a.COST_BASELINES)}
   scores.append({'event_id':key,'y':y,'included_in_formal_statistics':True,'economic_returns':costs,'cost_error':None})
   q.append({'event_id':key,'formal_eligible':True,'cohort':'formal','cost_available':True})
  return a,p,events,scores,q,f.utc(f.policy()['fixed_review_utc'][0])
 def test_bound_known_value_and_dependency_estimand(self):
  a=self.engine();self.assertAlmostEqual(a.lower_bound([1]*100,0,1,.05),1-math.sqrt(math.log(20)/200))
  with self.assertRaises(ValueError):a.lower_bound([float('nan')],0,1,.05)
 def test_engineering_cohort_never_enters_denominator(self):
  a,p,e,s,q,at=self.cohort();e.append({'event_id':'1_canary','cohort':'canary'});s.append({'event_id':'1_canary'})
  v=a.evaluate(p,e,s,q,at,True,True);self.assertEqual(v['scheduled_events'],1080);self.assertEqual(v['qualified_events'],1080)
  self.assertFalse(v['production_promotion_authorized']);self.assertTrue(v['statistical_gate_passed'])
 def test_missing_prediction_uses_worst_score_not_smaller_denominator(self):
  a,p,e,s,q,at=self.cohort();v=a.evaluate(p,e[1:],s[1:],q[1:],at,True,True)
  self.assertEqual(v['scheduled_events'],1080);self.assertEqual(v['qualified_events'],1079)
  self.assertEqual(v['missing'][0]['reason'],'MISSING_ISSUANCE');self.assertFalse(v['statistical_gate_passed'])
 def test_missing_score_or_public_witness_blocks(self):
  a,p,e,s,q,at=self.cohort()
  for ss,qq in ((s[1:],q),(s,q[1:])):
   v=a.evaluate(p,e,ss,qq,at,True,True);self.assertEqual(v['qualified_events'],1079);self.assertFalse(v['statistical_gate_passed'])
 def test_partial_network_audit_and_early_or_late_look_cannot_approve(self):
  a,p,e,s,q,at=self.cohort()
  for now,full,reg in ((at,False,True),(at,True,False),(at-timedelta(minutes=1),True,True),(at+timedelta(minutes=31),True,True)):
   self.assertFalse(a.evaluate(p,e,s,q,now,full,reg)['statistical_gate_passed'])
 def test_duplicate_off_grid_future_and_overlap_rejected(self):
  a,p,e,s,q,at=self.cohort()
  for kind in ('duplicate','grid','future','overlap'):
   bad=copy.deepcopy(e)
   if kind=='duplicate':bad.append(bad[0])
   if kind=='grid':bad[0]['slot_utc']=(f.utc(bad[0]['slot_utc'])+timedelta(seconds=1)).isoformat()
   if kind=='future':bad[0]['issued_at_utc']=(at+timedelta(days=1)).isoformat()
   if kind=='overlap':bad[0]['target_utc']=bad[1]['target_utc']
   with self.assertRaises(ValueError,msg=kind):a.evaluate(p,bad,s,q,at,True,True)
 def test_cost_absence_is_not_deleted_and_raw_mean_not_claimed_as_ci(self):
  a,p,e,s,q,at=self.cohort();s[0]['cost_error']='depth absent';s[0]['economic_returns']={}
  v=a.evaluate(p,e,s,q,at,True,True);self.assertEqual(v['cost_missing'],1);self.assertFalse(v['statistical_gate_passed'])
  self.assertFalse(v['raw_return_mean_confidence_interval_proved'])
 def test_regime_bound_uses_full_predictable_mask(self):
  a,p,e,s,q,at=self.cohort();v=a.evaluate(p,e,s,q,at,True,True)
  for r in v['regimes'].values():self.assertEqual(r['scheduled_denominator'],1080);self.assertEqual(r['n'],270)
 def test_model_policy_and_cohort_mutation_rejected(self):
  a,p,e,s,q,at=self.cohort();bad=copy.deepcopy(e);bad[0]['model_identity']='0'*64
  with self.assertRaises(ValueError):a.evaluate(p,bad,s,q,at,True,True)
  altered=copy.deepcopy(p);altered['minimum_accuracy_lower_bound']=.5
  with self.assertRaises(ValueError):a.evaluate(altered,e,s,q,at,True,True)
 def test_three_day_blocks_are_descriptive_not_independent_sample_claim(self):
  a,p,e,s,q,at=self.cohort();v=a.evaluate(p,e,s,q,at,True,True)
  self.assertEqual(len(v['calendar_blocks']),30);self.assertFalse(v['calendar_blocks_assumed_independent'])
 def test_late_run_identifies_missed_fixed_review(self):
  a,p,e,s,q,at=self.cohort();self.assertEqual(a.evaluate(p,e,s,q,at+timedelta(minutes=31),True,True)['status'],'MISSED_FIXED_REVIEW')

if __name__=='__main__':unittest.main()

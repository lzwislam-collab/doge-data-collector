"""Additional regression checks outside the immutable registered file set."""
import unittest
from datetime import timedelta
import test_phase3_independent as independent_fixture
import test_learned_shadow_v1 as shadow_fixture

class CohortRegressions(unittest.TestCase):
 def test_foreign_events_cannot_fill_formal_slot(self):
  a,p,e,s,q,at=independent_fixture.Independent().cohort()
  for foreign in ({'event_id':e[0]['event_id'],'cohort':'research'},
    {'event_id':e[0]['event_id'],'horizon_hours':1,'candidate_p':{'full':.99}},
    {**e[0],'protocol_id':'historical_backtest'}):
   with self.assertRaises((ValueError,KeyError)):a.evaluate(p,[foreign]+e[1:],s,q,at,True,True)
 def test_high_accuracy_canary_cannot_fill_missing_formal_issuance(self):
  a,p,e,s,q,at=independent_fixture.Independent().cohort();bad={**e[0],'cohort':'canary','event_id':'1_canary'}
  v=a.evaluate(p,[bad]+e[1:],[{**s[0],'event_id':'1_canary'}]+s[1:],[{'event_id':'1_canary','formal_eligible':True,'cohort':'canary','cost_available':True}]+q[1:],at,True,True)
  self.assertEqual(v['qualified_events'],1079);self.assertEqual(v['scheduled_events'],1080)
  self.assertFalse(v['statistical_gate_passed'])
 def test_equal_target_is_down_at_up_probability_boundary(self):
  fixture=shadow_fixture.Shadow();m=fixture.module();event=fixture.event()
  scored=m.score(event,fixture.packet(m.utc(event['target_utc']),100),fixture.policy())
  self.assertEqual(scored['y'],0)
  import plus_shadow
  for probability in (.5,.500001):
   event['candidate_p']['full']=probability
   row=plus_shadow.forecast_row('original18',1,event,scored,True,True,m.utc(event['target_utc'])+timedelta(minutes=1))
   self.assertEqual(row['direction'],'UP');self.assertEqual(row['latest_same_event']['accuracy'],0)

if __name__=='__main__':unittest.main()

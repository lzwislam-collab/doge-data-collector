import unittest
from datetime import datetime,timedelta,timezone
import plus_shadow as view

class Scorecard(unittest.TestCase):
 def build(self,events=(),scores=(),proofs=None,h=24):
  import plus_scorecard as m
  p={'effective_from_utc':'2026-10-16T00:00:00Z','slot_steps_hours':{'1':2,'24':25},'max_issue_delay_minutes':45,'target_window_minutes':25}
  return m.cohort('original18',h,p,list(events),list(scores),proofs or {},datetime(2026,10,19,tzinfo=timezone.utc),view.forecast_row)
 def event(self,i):
  at=datetime(2026,10,16,tzinfo=timezone.utc)+timedelta(hours=i*25)
  return {'event_id':str(int(at.timestamp()))+'_24','slot_utc':at.isoformat(),'issued_at_utc':at.isoformat(),'target_utc':(at+timedelta(hours=24)).isoformat(),'horizon_hours':24,'candidate_p':{'full':.6},'paired_p':.4,'frozen_direction':'DOWN'}
 def test_two_wrong_24h_are_zero_of_two(self):
  events=[self.event(i) for i in range(2)]
  scores=[{'event_id':e['event_id'],'y':0,'economic_deltas':{'full':None}} for e in events]
  c=self.build(events,scores,{e['event_id']:(True,True) for e in events})
  self.assertEqual((c['hits'],c['fixed_denominator'],c['accuracy_fixed']), (0,2,0.))
  self.assertEqual(c['score_label'],'0/2');self.assertEqual(c['qualified_scored'],2)
 def test_missing_slots_remain_in_denominator(self):
  c=self.build();self.assertEqual(c['fixed_denominator'],2);self.assertEqual(c['missing_issuance'],2)
  self.assertEqual(c['score_label'],'0/2');self.assertEqual(c['qualified_scored'],0)
  self.assertTrue(all(r['probability_up'] is None for r in c['rows']))
 def test_unverified_target_never_scores(self):
  e=self.event(0);c=self.build([e],[{'event_id':e['event_id'],'y':1}],{e['event_id']:(True,False)})
  self.assertEqual(c['hits'],0);self.assertEqual(c['missing_evaluation'],1)
 def test_duplicate_and_bad_slot_fail_closed(self):
  e=self.event(0)
  with self.assertRaises(ValueError):self.build([e,e])
  e['slot_utc']='2026-10-16T01:00:00Z'
  with self.assertRaises(ValueError):self.build([e])
 def test_probability_never_called_calibrated(self):
  e=self.event(0);c=self.build([e],proofs={e['event_id']:(True,False)})
  self.assertFalse(c['rows'][0]['calibration_verified'])
 def test_zero_of_two_visible_in_both_reports(self):
  import plus_scorecard,report_evidence
  c=self.build();report={'status':'SHADOW_REPORT_READY','forecasts':[],'scorecard':{'cohorts':[c]}}
  self.assertIn('0/2',view.render(report))
  self.assertIn('0/2',report_evidence.plus_sections(report)['F10'])
 def test_r2_canary_and_orphan_are_rejected(self):
  e=self.event(0)
  with self.assertRaises(ValueError):self.build([], [{'event_id':e['event_id']}])
 def test_naive_clock_is_rejected(self):
  e=self.event(0);e['issued_at_utc']='2026-10-16T00:00:00'
  with self.assertRaises(ValueError):self.build([e])

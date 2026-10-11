#!/usr/bin/env python3
"""Calibration causal integrity and delayed-registration tests."""
import math,unittest,hashlib
from datetime import datetime,timezone,timedelta,date
from research_interval_calibration import candidate,pit_distribution,pit_of_matured_forecast,assess
from score_v41 import score_row
from forecast_v41 import MODEL

class PitCausalityTests(unittest.TestCase):
 def setUp(self):
  self.prices=[.08*math.exp(.00015*i+.045*math.sin(i/17)+.035*math.sin(i/3)) for i in range(1600)]
 def test_calibration_only_past_matured_labels(self):
  anchor=1320
  for h in (1,7,30):
   a=candidate(self.prices,anchor,h,{})
   later=self.prices[:]
   for i in range(anchor+1,len(later)):later[i]*=3
   b=candidate(later,anchor,h,{})
   self.assertEqual(a,b)
 def test_calibration_sample_range(self):
  sample=pit_distribution(self.prices,1300,30,{})
  self.assertEqual(sample['calibration_n'],365)
  self.assertGreaterEqual(sample['p10_pit'],0)
  self.assertLessEqual(sample['p90_pit'],1)
  self.assertLessEqual(sample['p10_pit'],sample['p90_pit'])
 def test_walkforward_reports_overlap(self):
  a=assess(self.prices,30)
  self.assertGreater(a['all_overlapping']['n'],a['nonoverlap']['n'])
  self.assertFalse(a['automatic_promotion'])
  self.assertFalse(a['model_edge_validated'])

class RegistrationCutoffTests(unittest.TestCase):
 def row(self,registered):
  day=date(2026,10,10);a=.08
  return dict(forecast_id='x',registered_utc=registered,anchor_date_utc=str(day),
   anchor_close=str(a),target_date_utc=str(day+timedelta(days=7)),
   horizon_days='7',model_id=MODEL,p10='.07',p50='.08',p90='.09',
   baseline_p10='.06',baseline_p50='.08',baseline_p90='.10',
   historical_up_frequency='.5',baseline_up_frequency='.5',
   input_digest=hashlib.sha256((str(day)+':'+format(a,'.12g')).encode()).hexdigest())
 def test_registration_119_minutes_allowed(self):
  d={date(2026,10,10):.08}
  a,_=score_row(self.row('2026-10-11T01:59:00+00:00'),d,datetime(2026,10,11,12,tzinfo=timezone.utc))
  self.assertEqual(a,'pending')
 def test_registration_after_120_minutes_rejected(self):
  d={date(2026,10,10):.08}
  a,m=score_row(self.row('2026-10-11T02:01:00+00:00'),d,datetime(2026,10,11,12,tzinfo=timezone.utc))
  self.assertEqual(a,'quarantined');self.assertIn('too_late',m)
 def test_registration_before_close_rejected(self):
  a,_=score_row(self.row('2026-10-10T23:59:00+00:00'),{},datetime(2026,10,11,12,tzinfo=timezone.utc))
  self.assertEqual(a,'quarantined')

if __name__=='__main__':unittest.main(verbosity=2)

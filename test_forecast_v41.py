#!/usr/bin/env python3
"""V4.1 deterministic synthetic tests. No writes to production files."""
import hashlib,math,unittest
from datetime import date,datetime,timezone,timedelta
from forecast_v41 import quantile,prediction,walk_forward,interval_score,MODEL
from score_v41 import score_row

class DistributionTests(unittest.TestCase):
 def setUp(self):
  self.prices=[0.08*math.exp(.0006*i+.075*math.sin(i/12)) for i in range(1200)]
 def test_quantile_interpolates(self):
  self.assertEqual(quantile([0,1,2,3,4],.5),2)
  self.assertEqual(quantile([0,1,2,3,4],.1),.4)
 def test_no_future_price_leakage(self):
  origin=1100
  a=prediction(self.prices,origin,30)
  changed=self.prices[:]
  for i in range(origin+1,len(changed)):changed[i]*=7
  b=prediction(changed,origin,30)
  self.assertEqual(a,b)
 def test_matured_sample_is_bounded(self):
  a=prediction(self.prices,1100,30)
  self.assertEqual(a['n'],730)
  self.assertLessEqual(a['p10_r'],a['p50_r'])
  self.assertLessEqual(a['p50_r'],a['p90_r'])
 def test_backtest_nonoverlap(self):
  r=walk_forward(self.prices,30)
  self.assertGreater(r['all_anchors_overlapping']['n'],r['nonoverlap_fixed_start_phase']['n'])
  self.assertGreater(r['nonoverlap_fixed_start_phase']['n'],0)
  self.assertTrue(math.isfinite(r['all_anchors_overlapping']['rolling']['mean_interval_score_log_pct']))
 def test_interval_score_penalizes_miss(self):
  self.assertAlmostEqual(interval_score(-.1,.1,0),.2)
  self.assertGreater(interval_score(-.1,.1,.5),.2)

class ScoringTests(unittest.TestCase):
 def fixture(self,h=1,registered='2026-10-11T00:51:00+00:00'):
  origin=date(2026,10,10);anchor=0.08
  return {'forecast_id':'id','anchor_date_utc':str(origin),'target_date_utc':str(origin+timedelta(days=h)),
    'registered_utc':registered,'horizon_days':str(h),'anchor_close':str(anchor),
    'model_id':MODEL,'p10':'0.07','p50':'0.08','p90':'0.10',
    'baseline_p10':'0.06','baseline_p50':'0.08','baseline_p90':'0.11',
    'historical_up_frequency':'.48','baseline_up_frequency':'.49',
    'input_digest':hashlib.sha256((str(origin)+':'+format(anchor,'.12g')).encode()).hexdigest()}
 def test_valid_target_maturity(self):
  origin=date(2026,10,10);target=origin+timedelta(days=1)
  r=self.fixture();d={origin:.08,target:.09}
  self.assertEqual(score_row(r,d,datetime(2026,10,11,12,tzinfo=timezone.utc))[0],'pending')
  state,v=score_row(r,d,datetime(2026,10,12,tzinfo=timezone.utc))
  self.assertEqual(state,'scored');self.assertEqual(v['realized_up'],1)
 def test_retrospective_rejected(self):
  state,reason=score_row(self.fixture(1,'2026-10-12T00:01:00+00:00'),{},datetime(2026,10,13,tzinfo=timezone.utc))
  self.assertEqual(state,'quarantined')
 def test_missing_intermediate_day(self):
  origin=date(2026,10,10)
  state,reason=score_row(self.fixture(7),{origin:.08,origin+timedelta(days=7):.09},datetime(2026,10,18,tzinfo=timezone.utc))
  self.assertEqual(state,'quarantined')
  self.assertIn('intermediate',reason)
 def test_input_digest_tampering(self):
  r=self.fixture();r['input_digest']='tampered'
  state,_=score_row(r,{},datetime(2026,10,12,tzinfo=timezone.utc))
  self.assertEqual(state,'quarantined')
 def test_invalid_bands(self):
  r=self.fixture();r['p10']='0.12'
  state,_=score_row(r,{},datetime(2026,10,12,tzinfo=timezone.utc))
  self.assertEqual(state,'quarantined')

if __name__=='__main__':unittest.main(verbosity=2)

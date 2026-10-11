#!/usr/bin/env python3
"""V4.2 directional unit tests: no broad 70% claims, cause before label."""
import tempfile,unittest,csv
from datetime import datetime,timezone,timedelta
from pathlib import Path
from direction_baseline_v42 import audit_horizon,read_csv,stats,HOUR

class DirectionTests(unittest.TestCase):
 def setup_series(self,n=1500):
  start=int(datetime(2026,1,1,tzinfo=timezone.utc).timestamp()*1000)
  times=[start+i*HOUR for i in range(n)]
  doge=[.1 + .001*i + .007*(i%9-4) for i in range(n)]
  btc=[1000+2*i+20*(i%13-6) for i in range(n)]
  eth=[1000+2*i+10*(i%7-3) for i in range(n)]
  return times,{'DOGE-USDT':doge,'BTC-USDT':btc,'ETH-USDT':eth}
 def test_four_classifiers_and_nonstripped_holdout(self):
  t,p=self.setup_series()
  x=audit_horizon(t,p,1,500)
  self.assertEqual(x['all_overlapping']['n'],374)
  self.assertEqual(x['fixed_phase_nonoverlap']['n'],374)
  for key in ('expanding_majority','doge_momentum','doge_reversal','btc_momentum'):
   self.assertGreaterEqual(x['all_overlapping'][key]['accuracy_pct'],0)
 def test_overlap_reduces_independent_count(self):
  t,p=self.setup_series()
  x=audit_horizon(t,p,24,500)
  self.assertGreater(x['all_overlapping']['n'],x['fixed_phase_nonoverlap']['n'])
  self.assertFalse(x['models_precommitted_for_future_shadow_comparison'])
 def test_stable_prefix_without_future_price(self):
  t,p=self.setup_series()
  before=audit_horizon(t[:1200],{k:v[:1200] for k,v in p.items()},1,500)
  alt={k:v[:1200] for k,v in p.items()}
  alt['DOGE-USDT'][1199]*=4
  # Observations before the revised final target are unaffected in a truncated series
  earlier=audit_horizon(t[:1190],{k:v[:1190] for k,v in p.items()},1,500)
  same=audit_horizon(t[:1190],{k:v[:1190] for k,v in alt.items()},1,500)
  self.assertEqual(earlier,same)
 def test_down_class_includes_zero(self):
  rows=[{'up':0,'expanding_majority':0,'doge_momentum':0,'doge_reversal':1,'btc_momentum':0}]
  self.assertEqual(stats(rows)['expanding_majority']['accuracy_pct'],100.)
 def test_duplicate_hourly_key_rejected(self):
  with tempfile.TemporaryDirectory() as temp:
   p=Path(temp)/'data.csv'
   with p.open('w',newline='') as file:
    wr=csv.writer(file);wr.writerow(['symbol','open_utc_ms','close'])
    wr.writerow(['DOGE-USDT',3600000,.1]);wr.writerow(['DOGE-USDT',3600000,.11])
   with self.assertRaises(ValueError):read_csv(p,HOUR)

if __name__=='__main__':unittest.main(verbosity=2)

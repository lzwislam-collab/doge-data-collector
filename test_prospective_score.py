#!/usr/bin/env python3
"""Synthetic point-in-time scoring tests; never modify production DOGE data."""
import unittest
from datetime import date,timedelta
from prospective_score import score_record
class ScoringQA(unittest.TestCase):
 def make(self,p='0.48',reg='2026-10-11T00:51:40+00:00',h=1):
  return {'registered_utc':reg,'anchor_date_utc':'2026-10-10','target_date_utc':str(date(2026,10,10)+timedelta(days=h)),'horizon_days':str(h),'p_up':p,'anchor_close':'0.08','model_id':'doge_laplace_v1','forecast_id':'x'}
 def test_valid_before_target_close(self):
  a,b=score_record(self.make(),{date(2026,10,10):.08,date(2026,10,11):.09},date(2026,10,12))
  self.assertEqual(a,'scored');self.assertEqual(b['realized_up'],1)
 def test_future_not_scored(self):
  a,_=score_record(self.make(),{date(2026,10,10):.08,date(2026,10,11):.09},date(2026,10,11));self.assertEqual(a,'pending')
 def test_stale_anchor(self):
  a,_=score_record(self.make(reg='2026-10-12T00:00:00+00:00'),{},date(2026,10,13));self.assertEqual(a,'quarantined')
 def test_not_yet_registered_target_not_scored(self):
  a,_=score_record(self.make(reg='2026-10-11T23:59:00+00:00'),{date(2026,10,10):.08,date(2026,10,11):.09},date(2026,10,12));self.assertEqual(a,'scored')
 def test_bad_probability(self):
  a,_=score_record(self.make(p='nan'),{},date(2026,10,12));self.assertEqual(a,'quarantined')
 def test_missing_intermediate_daily_bar(self):
  a,b=score_record(self.make(h=7),{date(2026,10,10):.08,date(2026,10,17):.09},date(2026,10,18));self.assertEqual(a,'quarantined');self.assertEqual(b,'missing_intermediate_daily_bar')
 def test_wrong_anchor_close(self):
  a,b=score_record(self.make(),{date(2026,10,10):.07,date(2026,10,11):.09},date(2026,10,12));self.assertEqual(a,'quarantined')
if __name__=='__main__':unittest.main()

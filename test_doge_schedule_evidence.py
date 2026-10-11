#!/usr/bin/env python3
"""Offline tests for limited OKX liquidation listening-window eligibility."""
import unittest
from doge_schedule_evidence import snapshot_status_eligible

class ScheduleEvidenceTests(unittest.TestCase):
 def sample(self,**kw):
  x={'status':'partial','not_market_wide':True,'historical_backfill':False,
     'subscription_coverage_pct':96.2,
     'data_quality':{'subscribed':True,'subscription_ack_count':1,
                     'errors':[],'parse_errors':0}}
  x.update(kw)
  return x
 def test_healthy_limited_okx_partial_is_eligible(self):
  self.assertTrue(snapshot_status_eligible('liquidations_okx',self.sample(),('ok',)))
 def test_low_subscription_is_not_eligible(self):
  self.assertFalse(snapshot_status_eligible('liquidations_okx',
    self.sample(subscription_coverage_pct=45),('ok',)))
 def test_parse_error_is_not_eligible(self):
  self.assertFalse(snapshot_status_eligible('liquidations_okx',
    self.sample(data_quality={'subscribed':True,'subscription_ack_count':1,
                              'errors':[],'parse_errors':2}),('ok',)))
 def test_source_error_is_not_eligible(self):
  self.assertFalse(snapshot_status_eligible('liquidations_okx',
    self.sample(data_quality={'subscribed':True,'subscription_ack_count':1,
                              'errors':['socket fault'],'parse_errors':0}),('ok',)))
 def test_unverified_market_wide_claim_rejected(self):
  self.assertFalse(snapshot_status_eligible('liquidations_okx',
    self.sample(not_market_wide=False),('ok',)))
 def test_other_partial_source_not_implicitly_allowed(self):
  self.assertFalse(snapshot_status_eligible('liquidity_okx',self.sample(),('ok',)))
 def test_normal_ok_still_accepted(self):
  self.assertTrue(snapshot_status_eligible('liquidations_okx',self.sample(status='ok'),('ok',)))
 def test_missing_coverage_is_not_accepted(self):
  self.assertFalse(snapshot_status_eligible('liquidations_okx',
    self.sample(subscription_coverage_pct=None),('ok',)))
if __name__=='__main__':
 unittest.main(verbosity=2)

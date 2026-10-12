#!/usr/bin/env python3
"""Offline tests for limited OKX liquidation listening-window eligibility."""
import unittest
import tempfile
import os
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from doge_schedule_evidence import report_quality, rolling_integrity
from doge_schedule_evidence import snapshot_status_eligible,verify_rolling_link

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

class RollingActionLinkTests(unittest.TestCase):
 def sample(self):
  local={'eligible_chain_metadata_present':True,
   'audit_snapshot_utc':'2026-10-12T03:10:20+00:00',
   'provenance_metadata':{'upstream_whale_run_id':'100',
    'audit_workflow_run_id':'200',
    'upstream_whale_run_created_at_utc':'2026-10-12T03:07:00Z'}}
  up={'id':100,'status':'completed','conclusion':'success','event':'schedule',
      'path':'.github/workflows/whale_blocks.yml@main',
      'created_at':'2026-10-12T03:07:00Z','updated_at':'2026-10-12T03:10:00Z'}
  down={'id':200,'status':'completed','conclusion':'success','event':'workflow_run',
      'path':'.github/workflows/doge_rolling_utxo.yml@main',
      'created_at':'2026-10-12T03:10:10Z','updated_at':'2026-10-12T03:10:40Z'}
  return local,up,down

 def test_verified_only_with_real_matching_action_ids(self):
  local,up,down=self.sample()
  loader=lambda url: up if url.endswith('/100') else down
  result=verify_rolling_link(local,loader)
  self.assertTrue(result['verified'])
  self.assertEqual(result['upstream_schedule_run_id'],100)

 def test_skipped_workflow_run_not_success(self):
  local,up,down=self.sample()
  down['conclusion']='skipped'
  result=verify_rolling_link(local,lambda url:up if url.endswith('/100') else down)
  self.assertFalse(result['verified'])

 def test_wrong_downstream_event_not_success(self):
  local,up,down=self.sample()
  down['event']='push'
  self.assertFalse(verify_rolling_link(local,lambda url:up if url.endswith('/100') else down)['verified'])


class QualityAlertIntegrationTests(unittest.TestCase):
 sample=RollingActionLinkTests.sample
 def test_alert_fails_and_writes_existing_actions_summary(self):
  obj={'status':'all_evidence_matched','stage1_rolling_utxo':{
   'current_chain_automation_verified':True,
   'data_quality':{'alert_required':True,'alerts':['BACKLOG_OVER_30H'],
                   'coverage_pct':50,'backlog_events':1}}}
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'summary.md'
   captured=io.StringIO()
   with patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':str(path)}), redirect_stdout(captured):
    self.assertTrue(report_quality(obj))
   self.assertIn('::error',captured.getvalue())
   self.assertIn('BACKLOG_OVER_30H',path.read_text())
   self.assertIn('coverage_pct',path.read_text())

 def test_normal_backlog_does_not_fail(self):
  with patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':''}):
   self.assertFalse(report_quality({'status':'all_evidence_matched',
    'stage1_rolling_utxo':{'data_quality':{'alert_required':False,'alerts':[]}}}))

 def test_unverified_chain_alerts_even_with_healthy_local_data(self):
  with patch('doge_schedule_evidence.monitor_quality',return_value={'alerts':[],
              'alert_required':False}), \
       patch('doge_schedule_evidence.audit_rolling_local',return_value={
              'current_unprocessed_whale_events':0}), \
       patch('doge_schedule_evidence.verify_rolling_link',return_value={
              'verified':False,'reason':'run failed'}):
   result=rolling_integrity()
  self.assertIn('UNVERIFIED_SCHEDULED_CHAIN',result['data_quality']['alerts'])
  self.assertTrue(result['data_quality']['alert_required'])

 def test_upstream_run_identity_cannot_be_spoofed(self):
  local,up,down=self.sample()
  up['id']=101
  self.assertFalse(verify_rolling_link(local,lambda url:up if url.endswith('/100') else down)['verified'])

 def test_upstream_created_at_must_match_saved_provenance(self):
  local,up,down=self.sample()
  local['provenance_metadata']['upstream_whale_run_created_at_utc']='2026-10-12T03:09:00Z'
  self.assertFalse(verify_rolling_link(local,lambda url:up if url.endswith('/100') else down)['verified'])

 def test_downstream_timestamps_must_follow_upstream(self):
  local,up,down=self.sample()
  down['created_at']='2026-10-12T03:01:00Z'
  self.assertFalse(verify_rolling_link(local,lambda url:up if url.endswith('/100') else down)['verified'])

if __name__=='__main__':
 unittest.main(verbosity=2)

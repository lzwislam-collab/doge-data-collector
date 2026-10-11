#!/usr/bin/env python3
"""Deterministic issuance and score-denominator tests; no external exchange requests."""
import csv
import json
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from stage5_forward_quality import evaluate

FROZEN=datetime(2026,10,12,0,0,tzinfo=timezone.utc)
REG=['forecast_id','model_id','issued_at_utc','source_hour_start_utc','horizon_hours','target_utc']
SCORE=['forecast_id','status','realized_up','majority_correct','doge_reversal_correct']
MINUTE=['forecast_id','status','realized_up','majority_correct','doge_reversal_correct','evaluation_cohort']


def save(path,columns,rows):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='',encoding='utf-8') as file:
  w=csv.DictWriter(file,fieldnames=columns);w.writeheader();w.writerows(rows)


class IssuanceReliability(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.root=Path(self.tmp.name)
  dir=self.root/'data/stage5'
  dir.mkdir(parents=True)
  self.now=datetime(2026,10,12,4,46,tzinfo=timezone.utc)
  (dir/'pre_registration.json').write_text(json.dumps({
   'status':'frozen_protocol','protocol_id':'TEST_FROZEN',
   'model_id':'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',
   'primary_horizons_hours':[1,24],
   'minimum_nonoverlapping_valid_scores_per_horizon':100,
   'effective_from_utc':FROZEN.isoformat()}))
  (dir/'minute_evaluation_protocol.json').write_text(json.dumps({
   'status':'frozen_protocol','protocol_id':'TEST_FROZEN_MINUTE',
   'forecast_source_model_id':'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',
   'primary_horizons_hours':[1,24],
   'minimum_nonoverlap_scored_per_horizon':100,
   'effective_from_utc':FROZEN.isoformat()}))
  self.reg=[]
  for hour in (0,1):
   issued=FROZEN+timedelta(hours=hour,minutes=10)
   self.reg.append({'forecast_id':'h'+str(hour),'model_id':'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',
     'issued_at_utc':issued.isoformat(),
     'source_hour_start_utc':(FROZEN+timedelta(hours=hour)).isoformat(),
     'horizon_hours':'1','target_utc':(issued+timedelta(hours=1)).isoformat()})
  self.quote=[
   {'forecast_id':'h0','status':'scored','realized_up':'UP','majority_correct':'0','doge_reversal_correct':'1'},
   {'forecast_id':'h1','status':'unscorable_missing_target_quote','realized_up':'','majority_correct':'','doge_reversal_correct':''}]
  self.minute=[
   {'forecast_id':'h0','status':'scored_from_confirmed_1m_close','realized_up':'UP',
    'majority_correct':'0','doge_reversal_correct':'1','evaluation_cohort':'preregistered_forward_shadow'}]
  self.write()

 def tearDown(self):self.tmp.cleanup()

 def write(self):
  save(self.root/'data/direction_v42/forward_registry.csv',REG,self.reg)
  save(self.root/'data/direction_v42/forward_scores.csv',SCORE,self.quote)
  save(self.root/'data/stage5/minute_shadow/minute_scores.csv',MINUTE,self.minute)

 def test_missing_issued_windows_count_in_denominator(self):
  result=evaluate(self.root,self.now)
  h=result['horizons']['1h']
  self.assertEqual(h['expected_expired_issue_windows'],5)
  self.assertEqual(h['issued_windows'],2)
  self.assertEqual(h['missing_issuance_windows'],3)
  self.assertEqual(h['issuance_coverage_pct'],40)
  self.assertEqual(h['quote_scoring']['coverage_pct'],50)
  self.assertEqual(h['quote_scoring']['missed_or_unscorable'],1)
  self.assertEqual(h['minute_scoring']['coverage_pct'],50)
  self.assertEqual(h['minute_scoring']['missing'],1)
  self.assertFalse(h['primary_model_prospectively_proven_70pct'])
  self.assertEqual(result['horizons']['24h']['missing_issuance_windows'],1)

 def test_before_freeze_windows_do_not_exist(self):
  r=evaluate(self.root,FROZEN-timedelta(minutes=1))
  self.assertEqual(r['horizons']['1h']['expected_expired_issue_windows'],0)
  self.assertEqual(r['horizons']['1h']['issuance_coverage_pct'],None)

 def test_open_window_does_not_inflate_denominator(self):
  result=evaluate(self.root,FROZEN+timedelta(hours=1,minutes=40))
  self.assertEqual(result['horizons']['1h']['expected_expired_issue_windows'],1)

 def test_no_cherry_picking_when_same_bin_has_two_forecasts(self):
  second=dict(self.reg[0],forecast_id='later',
     issued_at_utc=(FROZEN+timedelta(minutes=20)).isoformat(),
     target_utc=(FROZEN+timedelta(hours=1,minutes=20)).isoformat())
  self.reg.append(second)
  self.quote.append({'forecast_id':'later','status':'scored','realized_up':'DOWN',
                     'majority_correct':'1','doge_reversal_correct':'1'})
  self.write()
  result=evaluate(self.root,self.now)
  h=result['horizons']['1h']
  self.assertEqual(h['issued_windows'],2)
  self.assertEqual(h['extra_registrations_in_same_bin'],1)
  self.assertEqual(h['quote_scoring']['successfully_scored'],1)

 def test_wrong_source_hour_is_rejected(self):
  self.reg[0]['source_hour_start_utc']=(FROZEN-timedelta(hours=1)).isoformat()
  self.write()
  with self.assertRaisesRegex(ValueError,'source feature hour'):
   evaluate(self.root,self.now)

 def test_issued_after_deadline_is_rejected(self):
  self.reg[0]['issued_at_utc']=(FROZEN+timedelta(minutes=48)).isoformat()
  self.reg[0]['target_utc']=(FROZEN+timedelta(hours=1,minutes=48)).isoformat()
  self.write()
  with self.assertRaisesRegex(ValueError,'late/out-of-bin'):
   evaluate(self.root,self.now)

 def test_orphan_score_is_rejected(self):
  self.minute.append({'forecast_id':'orphan','status':'scored_from_confirmed_1m_close',
                       'realized_up':'UP','majority_correct':'0','doge_reversal_correct':'1',
                       'evaluation_cohort':'preregistered_forward_shadow'})
  self.write()
  with self.assertRaisesRegex(ValueError,'orphan'):
   evaluate(self.root,self.now)

 def test_no_70pct_claim_from_single_perfect_hit(self):
  r=evaluate(self.root,self.now)
  self.assertEqual(r['horizons']['1h']['minute_scoring']['primary_model']['accuracy_pct'],100)
  self.assertFalse(r['not_verified_70pct'] is False)
  self.assertEqual(r['claim_permission'],'NO_VALIDATED_EDGE')


if __name__=='__main__':unittest.main(verbosity=2)

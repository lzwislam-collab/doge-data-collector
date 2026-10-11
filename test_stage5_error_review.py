#!/usr/bin/env python3
"""Deterministic tests for Stage5 error attribution and frozen score integrity."""
import csv,json,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from stage5_error_review import evaluate
from stage5_70pct_audit import verify_error_review_hashes

F=['forecast_id','model_id','issued_at_utc','source_quote_utc','reference_price','horizon_hours',
   'target_utc','majority_direction','doge_reversal_direction','btc_momentum_direction']
S=['forecast_id','status','observed_at_utc','target_quote_utc','target_price','realized_up',
   'majority_correct','doge_reversal_correct','btc_momentum_correct']
M=['forecast_id','status','evaluated_at_utc','target_utc','bar_open_utc','bar_closed_at_utc',
   'retrieved_at_utc','minute_close_usdt','realized_up','majority_correct','doge_reversal_correct',
   'btc_momentum_correct','evaluation_cohort','protocol_id']
MODEL='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1'
NOW=datetime(2026,10,11,12,0,tzinfo=timezone.utc)

def save(path,header,rows):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='',encoding='utf-8') as fh:
  w=csv.DictWriter(fh,fieldnames=header);w.writeheader();w.writerows(rows)

class ErrorReviewTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.root=Path(self.tmp.name)
  folder=self.root/'data/stage5'
  folder.mkdir(parents=True)
  (folder/'pre_registration.json').write_text(json.dumps({
   'status':'frozen_protocol','model_id':MODEL,'effective_from_utc':'2026-10-12T00:00:00+00:00'}))
  (folder/'minute_evaluation_protocol.json').write_text(json.dumps({
   'status':'frozen_protocol','forecast_source_model_id':MODEL,
   'effective_from_utc':'2026-10-12T00:00:00+00:00','protocol_id':'TEST_MINUTE'}))
  self.reg=[
   dict(forecast_id='wrong',model_id=MODEL,
    issued_at_utc='2026-10-11T08:10:00+00:00',
    source_quote_utc='2026-10-11T08:09:59+00:00',
    reference_price='0.10',horizon_hours='1',
    target_utc='2026-10-11T09:10:00+00:00',
    majority_direction='DOWN',doge_reversal_direction='UP',btc_momentum_direction='UP'),
   dict(forecast_id='pending24',model_id=MODEL,
    issued_at_utc='2026-10-11T10:00:00+00:00',
    source_quote_utc='2026-10-11T09:59:59+00:00',reference_price='0.09',
    horizon_hours='24',target_utc='2026-10-12T10:00:00+00:00',
    majority_direction='DOWN',doge_reversal_direction='UP',btc_momentum_direction='UP')]
  self.scored=[dict(forecast_id='wrong',status='scored',
    observed_at_utc='2026-10-11T09:12:00+00:00',
    target_quote_utc='2026-10-11T09:11:00+00:00',target_price='0.09',
    realized_up='DOWN',majority_correct='1',doge_reversal_correct='0',
    btc_momentum_correct='0')]
  self.minute=[]
  self.write()

 def tearDown(self):self.tmp.cleanup()

 def write(self):
  save(self.root/'data/direction_v42/forward_registry.csv',F,self.reg)
  save(self.root/'data/direction_v42/forward_scores.csv',S,self.scored)
  save(self.root/'data/stage5/minute_shadow/minute_scores.csv',M,self.minute)

 def empty(self):return {}

 def test_wrong_and_pending_stay_separate(self):
  r=evaluate(self.root,NOW,self.empty())
  self.assertEqual(r['status'],'ok')
  self.assertEqual(r['cohort_statistics']['original_ticker_1h_pre_freeze_diagnostic']['wrong'],1)
  self.assertEqual(r['cohort_statistics']['original_ticker_24h_pre_freeze_diagnostic']['pending'],1)
  self.assertEqual(r['24h_watch'][0]['status'],'pending')
  self.assertFalse(r['automatic_model_change_authorized'])
  self.assertFalse(r['validated_70pct'])

 def test_wrong_market_context_is_correlation_only(self):
  def ms(h):return int(datetime(2026,10,11,h,tzinfo=timezone.utc).timestamp()*1000)
  fake={
   'BTC-USDT':{ms(7):(.99,NOW),ms(8):(.97,NOW)},
   'ETH-USDT':{ms(7):(.99,NOW),ms(8):(.97,NOW)},
   'DOGE-USDT':{ms(7):(.10,NOW),ms(8):(.09,NOW)}}
  r=evaluate(self.root,NOW,fake)
  first=r['errors_and_coverage_examples'][0]
  self.assertIn('btc_eth_co_movement_correlated',first['explanatory_factors'])
  self.assertIn('doge_underperformed_btc',first['explanatory_factors'])
  self.assertEqual(first['context']['state'],'retrospective_association_only')
  self.assertNotIn('causal_proof',first)
  self.assertTrue(r['retrospective_correlation_not_proven_cause'])

 def test_early_issue_context_and_late_target_collection_are_not_mixed(self):
  def ms(h):return int(datetime(2026,10,11,h,tzinfo=timezone.utc).timestamp()*1000)
  issue_before=datetime(2026,10,11,8,0,tzinfo=timezone.utc)
  observed_after=datetime(2026,10,11,9,30,tzinfo=timezone.utc)
  fake={sym:{ms(7):(.10,issue_before),ms(8):(.09,observed_after)}
        for sym in ('BTC-USDT','ETH-USDT','DOGE-USDT')}
  r=evaluate(self.root,NOW,fake)
  c=r['errors_and_coverage_examples'][0]['context']
  self.assertFalse(c['anchor_first_seen_after_issue'])
  self.assertTrue(c['outcome_first_seen_after_target'])
  self.assertFalse(c['ex_ante_market_features_verified'])
  self.assertEqual(c['source_first_seen_utc_by_symbol']['BTC-USDT']['target_context'],
                   observed_after.isoformat())

 def test_review_hashes_detect_unchanged_count_but_changed_prediction(self):
  r=evaluate(self.root,NOW,{})
  self.assertTrue(verify_error_review_hashes(self.root,r))
  self.reg[0]['majority_direction']='UP'
  self.write()
  with self.assertRaisesRegex(ValueError,'source hash mismatch'):
   verify_error_review_hashes(self.root,r)

 def test_review_hashes_detect_scoring_mutation_without_count_change(self):
  r=evaluate(self.root,NOW,{})
  self.assertTrue(verify_error_review_hashes(self.root,r))
  self.scored[0]['target_price']='0.091'
  self.write()
  with self.assertRaisesRegex(ValueError,'source hash mismatch'):
   verify_error_review_hashes(self.root,r)

 def test_hashes_detect_minute_record_tampering(self):
  r=evaluate(self.root,NOW,{})
  self.assertTrue(verify_error_review_hashes(self.root,r))
  path=self.root/'data/stage5/minute_shadow/minute_scores.csv'
  path.write_text(path.read_text()+'\\n',encoding='utf-8')
  with self.assertRaisesRegex(ValueError,'source hash mismatch'):
   verify_error_review_hashes(self.root,r)

 def test_source_quote_missing_is_coverage_not_direction(self):
  self.scored[0]=dict(forecast_id='wrong',status='unscorable_missing_target_quote',
   observed_at_utc='2026-10-11T10:00:00+00:00',target_quote_utc='',
   target_price='',realized_up='',majority_correct='',doge_reversal_correct='',btc_momentum_correct='')
  self.write()
  r=evaluate(self.root,NOW,{})
  summary=r['cohort_statistics']['original_ticker_1h_pre_freeze_diagnostic']
  self.assertEqual(summary['wrong'],0)
  self.assertEqual(summary['unscorable'],1)
  self.assertEqual(r['errors_and_coverage_examples'][0]['category'],'observation_coverage_failure')

 def test_score_flag_cannot_be_falsified(self):
  self.scored[0]['doge_reversal_correct']='1'
  self.write()
  with self.assertRaisesRegex(ValueError,'mismatched quote correct flag'):
   evaluate(self.root,NOW,{})

 def test_scoring_not_before_target(self):
  self.scored[0]['target_quote_utc']='2026-10-11T09:09:59+00:00'
  self.write()
  with self.assertRaisesRegex(ValueError,'not inside score window'):
   evaluate(self.root,NOW,{})

 def test_no_retrospective_price_fill_for_pending_forecast(self):
  r=evaluate(self.root,NOW,{})
  w=r['24h_watch'][0]
  self.assertIsNone(r['cohort_statistics']['original_ticker_24h_pre_freeze_diagnostic']['accuracy_pct'])
  self.assertEqual(w['status'],'pending')
  self.assertEqual(w['original_direction'],'UP')

 def test_correct_score_is_not_error(self):
  self.scored[0].update(target_price='0.11',realized_up='UP',
    majority_correct='0',doge_reversal_correct='1',btc_momentum_correct='1')
  self.write()
  r=evaluate(self.root,NOW,{})
  self.assertEqual(r['cohort_statistics']['original_ticker_1h_pre_freeze_diagnostic']['correct'],1)
  self.assertFalse(r['errors_and_coverage_examples'])

 def test_minute_score_is_separate_hindsight_diagnostic(self):
  self.minute=[dict(forecast_id='wrong',status='scored_from_confirmed_1m_close',
   evaluated_at_utc='2026-10-11T10:05:00+00:00',
   target_utc='2026-10-11T09:10:00+00:00',
   bar_open_utc='2026-10-11T09:10:00+00:00',
   bar_closed_at_utc='2026-10-11T09:11:00+00:00',
   retrieved_at_utc='2026-10-11T10:04:00+00:00',minute_close_usdt='0.11',
   realized_up='UP',majority_correct='0',doge_reversal_correct='1',
   btc_momentum_correct='1',evaluation_cohort='pre_freeze_diagnostic_only',
   protocol_id='TEST_MINUTE')]
  self.write()
  r=evaluate(self.root,NOW,{})
  self.assertEqual(r['cohort_statistics']['original_ticker_1h_pre_freeze_diagnostic']['wrong'],1)
  self.assertEqual(r['cohort_statistics']['confirmed_1m_close_1h_pre_freeze_diagnostic']['correct'],1)
  self.assertTrue(r['pre_freeze_diagnostic_excluded_from_70pct_validation'])

 def test_wrong_minute_cohort_rejected(self):
  self.minute=[dict(forecast_id='wrong',status='scored_from_confirmed_1m_close',
   evaluated_at_utc='2026-10-11T10:05:00+00:00',target_utc='2026-10-11T09:10:00+00:00',
   bar_open_utc='2026-10-11T09:10:00+00:00',bar_closed_at_utc='2026-10-11T09:11:00+00:00',
   retrieved_at_utc='2026-10-11T10:04:00+00:00',minute_close_usdt='0.11',
   realized_up='UP',majority_correct='0',doge_reversal_correct='1',
   btc_momentum_correct='1',evaluation_cohort='preregistered_forward_shadow',protocol_id='TEST_MINUTE')]
  self.write()
  with self.assertRaisesRegex(ValueError,'wrong minute scoring cohort'):
   evaluate(self.root,NOW,{})

if __name__=='__main__':unittest.main(verbosity=2)

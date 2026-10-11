#!/usr/bin/env python3
"""Fail-closed, deterministic tests for daily DOGE prediction evolution ledger."""
import csv,json,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from spot_plus_daily_evolution import inspect,run,read_ledger

AT=datetime(2026,10,11,13,0,tzinfo=timezone.utc)

def dump(root,path,data):
 p=root/path;p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')

class TestDailyEvolution(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name)
  self.day={
    'locked_bins_with_issued_predictions':0,
    'resolved':0,'scored':0,'primary_correct':0,'primary_accuracy_pct':None,
    'wilson95_lower_pct_descriptive_only':None,'coverage_pct':None,
    'baseline_majority_accuracy_pct':None,'up_recall_pct':None,
    'down_recall_pct':None,'eligible_for_70pct_claim':False}
  self.stage={'status':'ok','generated_at_utc':'2026-10-11T12:19:08+00:00',
    'claim_permission':'NO_VALIDATED_EDGE','prospective_70pct_validated':False,
    'preregistered_forward_trial':{'protocol_frozen':True,
     'no_prefreeze_predictions_counted':True,
     'horizons':{'24h':self.day}}}
  self.plus={'status':'ok','generated_at_utc':'2026-10-11T12:19:10+00:00',
    'source_daily_last_available_at_utc':'2026-10-11T00:00:00+00:00',
    'profit_edge_vs_hold_verified':False,'direction_accuracy_70pct_verified':False,
    'live_trading_authorized':False,'dca_rules_changed':False,
    'five_pillar_evidence_audit':{'quality_audit_age_hours':3.4,'pillar_count':5,
     'original_36_items':{'item_level_current_mapping_reconciled':False},
     'pillars':{s:{'sources':{}} for s in ('1','2','3','4','5')}},
    'historical_spot_paper':{'buy_and_hold':{'net_total_return_pct':-30.66},
      'cash':{'net_total_return_pct':0},
      'candidate':{'net_total_return_pct':-46.85}}}
  self.save()

 def tearDown(self):self.t.cleanup()

 def save(self):
  dump(self.root,'data/stage5/spot_plus/latest.json',self.plus)
  dump(self.root,'data/stage5/latest.json',self.stage)

 def test_first_ledger_and_same_day_are_idempotent(self):
  a=run(self.root,AT);b=run(self.root,AT+timedelta(minutes=4))
  self.assertEqual(a['ledger_rows'],1)
  self.assertTrue(a['new_ledger_row'])
  self.assertEqual(b['ledger_rows'],1)
  self.assertFalse(b['new_ledger_row'])
  self.assertEqual(len(read_ledger(self.root/'data/stage5/spot_plus/evolution_log.csv')),1)
  self.assertIn('70%有效优势', (self.root/'reports/DOGE_SPOT_PLUS_DAILY.md').read_text())

 def test_new_day_has_hash_link_and_no_backdating(self):
  run(self.root,AT)
  next_day=AT+timedelta(days=1)
  self.plus['generated_at_utc']=(next_day-timedelta(minutes=30)).isoformat()
  self.stage['generated_at_utc']=(next_day-timedelta(minutes=29)).isoformat()
  self.save()
  a=run(self.root,next_day)
  self.assertEqual(a['ledger_rows'],2)
  rows=read_ledger(self.root/'data/stage5/spot_plus/evolution_log.csv')
  self.assertEqual(rows[-1]['previous_digest'],rows[0]['row_digest'])
  self.assertEqual(a['change_since_previous_daily_record']['new_24h_predictions'],0)
  self.assertEqual(a['change_since_previous_daily_record']['new_24h_scored'],0)
  # Replay with contemporaneous sources: prove ledger rejects backdates itself.
  self.plus['generated_at_utc']='2026-10-11T12:19:10+00:00'
  self.stage['generated_at_utc']='2026-10-11T12:19:08+00:00'
  self.save()
  with self.assertRaisesRegex(ValueError,'backdate'):
   run(self.root,AT)

 def test_failed_counter_regression_does_not_write_new_ledger(self):
  self.day.update(locked_bins_with_issued_predictions=5,resolved=3,scored=3,
    primary_correct=1,primary_accuracy_pct=33.33)
  self.save()
  run(self.root,AT)
  next_day=AT+timedelta(days=1)
  self.plus['generated_at_utc']=(next_day-timedelta(minutes=30)).isoformat()
  self.stage['generated_at_utc']=(next_day-timedelta(minutes=29)).isoformat()
  self.day.update(locked_bins_with_issued_predictions=3,resolved=2,scored=2,
    primary_correct=1,primary_accuracy_pct=50.0)
  self.save()
  with self.assertRaisesRegex(ValueError,'counters regressed'):
   run(self.root,next_day)
  self.assertEqual(len(read_ledger(self.root/'data/stage5/spot_plus/evolution_log.csv')),1)

 def test_warning_text_names_stale_global_quality_audit(self):
  report=run(self.root,AT)
  self.assertIn('GLOBAL_QUALITY_AUDIT:older_than_3h',
                report['source_health']['warning_sources'])
  md=(self.root/'reports/DOGE_SPOT_PLUS_DAILY.md').read_text()
  self.assertIn('GLOBAL_QUALITY_AUDIT:older_than_3h',md)
  self.assertIn('尚无有效样本',md)
  self.assertNotIn('尚无有效样本%',md)

 def test_mutating_existing_ledger_cell_is_detected(self):
  run(self.root,AT)
  p=self.root/'data/stage5/spot_plus/evolution_log.csv'
  s=p.read_text();p.write_text(s.replace('-30.66','99.90'))
  with self.assertRaisesRegex(ValueError,'hash chain'):
   read_ledger(p)

 def test_missing_source_model_or_5_pillars_blocks(self):
  del self.plus['five_pillar_evidence_audit']['pillars']['5']
  self.save()
  with self.assertRaisesRegex(ValueError,'five-pillar'):
   inspect(self.root,AT)

 def test_missing_target_quote_blocks_progress_before_100_forecasts(self):
  self.plus['five_pillar_evidence_audit']['quality_audit_age_hours']=0.1
  self.stage['forward_live']={'horizons':{'1h':{'resolved':8,'scored':3,
    'unscorable':5,'resolved_quote_coverage_pct':37.5}}}
  self.save()
  result=inspect(self.root,AT)
  health=result['original_ticker_1h_collection_diagnostic']
  self.assertEqual(health['matured'],8)
  self.assertEqual(health['unscorable'],5)
  self.assertEqual(health['quote_coverage_pct'],37.5)
  self.assertEqual(result['evolution_priority'],'P0_REPAIR_ORIGINAL_TARGET_TICKER_QUOTE_COVERAGE')
  self.assertFalse(result['accuracy_70pct_validated'])
  run(self.root,AT)
  self.assertIn('报价覆盖率 37.5%',(self.root/'reports/DOGE_SPOT_PLUS_DAILY.md').read_text())

 def test_tampered_quote_coverage_fails_closed(self):
  self.plus['five_pillar_evidence_audit']['quality_audit_age_hours']=0.1
  self.stage['forward_live']={'horizons':{'1h':{'resolved':8,'scored':3,
    'unscorable':5,'resolved_quote_coverage_pct':90.0}}}
  self.save()
  with self.assertRaisesRegex(ValueError,'coverage mismatch'):
   inspect(self.root,AT)

 def test_missing_quote_counter_cannot_be_ignored(self):
  self.stage['forward_live']={'horizons':{'1h':{'resolved':8,'scored':8,
    'unscorable':5,'resolved_quote_coverage_pct':100.0}}}
  self.save()
  with self.assertRaisesRegex(ValueError,'coverage accounting mismatch'):
   inspect(self.root,AT)

 def test_realtime_warning_prevents_fake_green(self):
  x=inspect(self.root,AT)
  self.assertEqual(x['source_health']['grade'],'WARN')
  self.assertTrue(x['source_health']['global_quality_audit_stale_over_3h'])
  self.assertFalse(x['real_spot_orders_authorized'])

 def test_invalid_future_global_audit_blocks(self):
  self.plus['generated_at_utc']=(AT+timedelta(hours=3)).isoformat()
  self.save()
  with self.assertRaisesRegex(ValueError,'future'):
   inspect(self.root,AT)

 def test_forecast_accuracy_must_match_actual_correct_count(self):
  self.day.update(locked_bins_with_issued_predictions=4,resolved=4,scored=4,
    primary_correct=1,primary_accuracy_pct=90.0)
  self.save()
  with self.assertRaisesRegex(ValueError,'not supported'):
   inspect(self.root,AT)

 def test_70pct_flag_does_not_auto_authorize(self):
  self.stage['prospective_70pct_validated']=True
  self.save()
  with self.assertRaisesRegex(ValueError,'global validation'):
   inspect(self.root,AT)

 def test_nonzero_score_is_not_pooled_with_historical_simulation(self):
  self.day.update(locked_bins_with_issued_predictions=7,resolved=5,scored=4,
    primary_correct=3,primary_accuracy_pct=75.0,
    wilson95_lower_pct_descriptive_only=30.0,coverage_pct=80.0)
  self.save()
  d=inspect(self.root,AT)
  self.assertEqual(d['prospective_24h']['accuracy_pct'],75.0)
  self.assertFalse(d['accuracy_70pct_validated'])
  self.assertEqual(d['evolution_priority'],'P0_VERIFY_TIMESTAMP_FRESHNESS_AND_MISSING_SOURCE')

 def test_reports_do_not_change_original_36_or_dca(self):
  x=run(self.root,AT)
  self.assertFalse(x['all_five_pillars_in_prediction_weights'])
  self.assertFalse(x['model_change_authorized'])
  self.assertEqual(x['original_36_status']['item_level_current_mapping_reconciled'],False)
  self.assertIn('不更改DOGE定投', (self.root/'reports/DOGE_SPOT_PLUS_DAILY.md').read_text())

if __name__=='__main__':
 unittest.main(verbosity=2)

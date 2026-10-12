#!/usr/bin/env python3
"""Offline failure-path tests for the Stage 5 predictive accuracy audit."""
import csv
import tempfile
import unittest
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone
from stage5_70pct_audit import wilson_lower, audited_fixed_phase, inspect_forward, audit_locked_forward_trial

FCOLS=['forecast_id','model_id','issued_at_utc','source_quote_utc','reference_price',
       'horizon_hours','target_utc','majority_direction',
       'doge_reversal_direction','btc_momentum_direction']
SCOLS=['forecast_id','status','observed_at_utc','target_quote_utc','target_price',
       'realized_up','majority_correct','doge_reversal_correct','btc_momentum_correct']


def save(path, cols, data):
    with path.open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=cols)
        writer.writeheader()
        writer.writerows(data)


class AccuracyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.dir=self.root/'data/direction_v42'
        self.dir.mkdir(parents=True)
        self.forecast={
            'forecast_id':'id1','model_id':'v1',
            'issued_at_utc':'2026-10-11T03:01:00+00:00',
            'source_quote_utc':'2026-10-11T03:00:59+00:00',
            'reference_price':'0.07','horizon_hours':'1',
            'target_utc':'2026-10-11T04:01:00+00:00',
            'majority_direction':'UP','doge_reversal_direction':'DOWN',
            'btc_momentum_direction':'UP'
        }
        self.score={
            'forecast_id':'id1','status':'scored','observed_at_utc':'2026-10-11T04:01:05+00:00',
            'target_quote_utc':'2026-10-11T04:01:03+00:00','target_price':'0.08',
            'realized_up':'UP','majority_correct':'1','doge_reversal_correct':'0',
            'btc_momentum_correct':'1'
        }

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, forecasts=None, scores=None):
        save(self.dir/'forward_registry.csv',FCOLS,
             forecasts if forecasts is not None else [self.forecast])
        save(self.dir/'forward_scores.csv',SCOLS,
             scores if scores is not None else [self.score])

    def test_wilson_small_70percent_not_proven(self):
        bound=wilson_lower(14,20)
        self.assertTrue(.45 < bound < .55)
        self.assertLess(wilson_lower(1,1), .30)

    def test_prospective_one_scored_is_not_accuracy_proof(self):
        self.write()
        report=inspect_forward(self.root)
        one=report['horizons']['1h']
        self.assertEqual(one['scored'],1)
        self.assertEqual(one['resolved_quote_coverage_pct'],100)
        self.assertFalse(one['model_results']['majority']['eligible_70pct_claim'])

    def test_orphan_scored_forecast_rejected(self):
        self.write(scores=[dict(self.score,forecast_id='orphan')])
        with self.assertRaisesRegex(ValueError,'orphan'):
            inspect_forward(self.root)

    def test_quote_before_target_rejected(self):
        self.write(scores=[dict(self.score,target_quote_utc='2026-10-11T04:00:59+00:00')])
        with self.assertRaisesRegex(ValueError,'outside'):
            inspect_forward(self.root)

    def test_retroactively_changed_direction_flag_rejected(self):
        self.write(scores=[dict(self.score,majority_correct='0')])
        with self.assertRaisesRegex(ValueError,'score flag'):
            inspect_forward(self.root)

    def test_duplicate_forecast_rejected(self):
        self.write(forecasts=[self.forecast,self.forecast])
        with self.assertRaisesRegex(ValueError,'duplicate'):
            inspect_forward(self.root)

    def test_historical_fixed_phase_70_is_exploratory(self):
        frame={}
        for horizon in ('1h','24h','7d','30d'):
            n,k=(20,14) if horizon=='30d' else (100,53)
            entry={'n':n}
            for model in ('expanding_majority','doge_momentum','doge_reversal','btc_momentum'):
                entry[model]={'correct':k,'accuracy_pct':round(100*k/n,2)}
            frame[horizon]={'fixed_phase_nonoverlap':entry}
        result=audited_fixed_phase({'validated_accuracy_70pct':False,'horizons':frame})
        high=result['highest_observed_accuracy']
        self.assertEqual(high['accuracy_pct'],70)
        self.assertEqual(high['n_nonoverlap'],20)
        self.assertFalse(high['may_claim_prospective_70pct'])
        self.assertLess(high['wilson95_lower_pct_descriptive_only'],70)



    def make_lock(self):
        shutil.copyfile(Path(__file__).resolve().parent/'direction_forward_v42.py',self.root/'direction_forward_v42.py')
        folder=self.root/'data/stage5'
        folder.mkdir(parents=True,exist_ok=True)
        lock={'status':'frozen_protocol','protocol_id':'DOGE_V42_DIRECTION_SHADOW_LOCK_20261012',
         'model_id':'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',
         'locked_source_file':'direction_forward_v42.py',
         'locked_source_git_blob_sha':'368abc641db6d5ea6496ac4db624554161034665',
         'primary_model':'doge_reversal','comparison_model':'majority',
         'primary_horizons_hours':[1,24],'eligible_target_quote_window_minutes':25,
         'issue_quote_max_age_seconds':120,'effective_from_utc':'2026-10-12T00:00:00+00:00',
         'minimum_nonoverlapping_valid_scores_per_horizon':100,
         'minimum_resolved_quote_coverage_fraction':0.9,
         'minimum_wilson_95pct_lower_accuracy_fraction':0.7,
         'minimum_observation_span_days':30}
        (folder/'pre_registration.json').write_text(json.dumps(lock))
        return lock

    def test_trial_excludes_all_prelock_predictions(self):
        self.make_lock()
        self.write()
        study=audit_locked_forward_trial(self.root)
        self.assertEqual(study['horizons']['1h']['locked_bins_with_issued_predictions'],0)
        self.assertEqual(study['horizons']['1h']['scored'],0)
        self.assertFalse(study['horizons']['1h']['eligible_for_70pct_claim'])

    def test_trial_freeze_detects_changed_model_script(self):
        self.make_lock()
        self.write()
        (self.root/'direction_forward_v42.py').write_text('modification')
        with self.assertRaisesRegex(ValueError,'frozen V4.2 source changed'):
            audit_locked_forward_trial(self.root)

    def test_trial_postlock_pending_is_not_backfilled(self):
        self.make_lock()
        new=dict(self.forecast,issued_at_utc='2026-10-12T01:01:00+00:00',
            source_quote_utc='2026-10-12T01:00:59+00:00',
            target_utc='2026-10-12T02:01:00+00:00',
            model_id='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',
            forecast_id='id-postlock')
        self.write(forecasts=[self.forecast,new],scores=[self.score])
        hour=audit_locked_forward_trial(self.root, datetime(2026,10,12,1,30,tzinfo=timezone.utc))['horizons']['1h']
        self.assertEqual(hour['locked_bins_with_issued_predictions'],1)
        self.assertEqual(hour['pending'],1)
        self.assertEqual(hour['scored'],0)

    def test_matured_unscored_forecast_reduces_coverage_even_if_not_in_scores_csv(self):
        self.make_lock()
        first=dict(self.forecast, model_id='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',
            forecast_id='registered-one',
            issued_at_utc='2026-10-12T00:10:00+00:00',
            source_quote_utc='2026-10-12T00:09:59+00:00',
            target_utc='2026-10-12T01:10:00+00:00')
        second=dict(first,forecast_id='registered-two',
            issued_at_utc='2026-10-12T01:10:00+00:00',
            source_quote_utc='2026-10-12T01:09:59+00:00',
            target_utc='2026-10-12T02:10:00+00:00')
        one=dict(self.score,forecast_id='registered-one',
            observed_at_utc='2026-10-12T01:11:00+00:00',
            target_quote_utc='2026-10-12T01:10:05+00:00')
        self.write(forecasts=[first,second],scores=[one])
        now=datetime(2026,10,12,3,0,tzinfo=timezone.utc)
        h=audit_locked_forward_trial(self.root,now)['horizons']['1h']
        self.assertEqual(h['matured_denom_including_missing_scores'],2)
        self.assertEqual(h['scored'],1)
        self.assertEqual(h['overdue_missing_score_rows'],1)
        self.assertEqual(h['coverage_pct'],50.0)
        self.assertEqual(h['pending'],0)
        self.assertFalse(h['coverage_target_met'])
        self.assertFalse(h['eligible_for_70pct_claim'])

    def test_not_yet_matured_forecast_remains_pending_not_coverage_failure(self):
        self.make_lock()
        first=dict(self.forecast, model_id='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',
            forecast_id='registered-one', issued_at_utc='2026-10-12T00:10:00+00:00',
            source_quote_utc='2026-10-12T00:09:59+00:00',
            target_utc='2026-10-12T01:10:00+00:00')
        second=dict(first,forecast_id='registered-two',
            issued_at_utc='2026-10-12T01:10:00+00:00',
            source_quote_utc='2026-10-12T01:09:59+00:00',
            target_utc='2026-10-12T02:10:00+00:00')
        one=dict(self.score,forecast_id='registered-one',
            observed_at_utc='2026-10-12T01:11:00+00:00',
            target_quote_utc='2026-10-12T01:10:05+00:00')
        self.write(forecasts=[first,second],scores=[one])
        now=datetime(2026,10,12,2,20,tzinfo=timezone.utc)
        h=audit_locked_forward_trial(self.root,now)['horizons']['1h']
        self.assertEqual(h['matured_denom_including_missing_scores'],1)
        self.assertEqual(h['overdue_missing_score_rows'],0)
        self.assertEqual(h['pending'],1)
        self.assertEqual(h['coverage_pct'],100.0)

    def test_trial_does_not_select_same_bin_winner(self):
        self.make_lock()
        first=dict(self.forecast,issued_at_utc='2026-10-12T01:01:00+00:00',
            source_quote_utc='2026-10-12T01:00:59+00:00',
            target_utc='2026-10-12T02:01:00+00:00',
            model_id='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',forecast_id='early')
        second=dict(first,issued_at_utc='2026-10-12T01:10:00+00:00',
            source_quote_utc='2026-10-12T01:09:59+00:00',
            target_utc='2026-10-12T02:10:00+00:00',forecast_id='late')
        score=dict(self.score,forecast_id='late',
            observed_at_utc='2026-10-12T02:10:05+00:00',
            target_quote_utc='2026-10-12T02:10:03+00:00')
        self.write(forecasts=[first,second],scores=[score])
        hour=audit_locked_forward_trial(self.root, datetime(2026,10,12,3,0,tzinfo=timezone.utc))['horizons']['1h']
        self.assertEqual(hour['locked_bins_with_issued_predictions'],1)
        self.assertEqual(hour['scored'],0)
        self.assertEqual(hour['pending'],0)
        self.assertEqual(hour['overdue_missing_score_rows'],1)

if __name__=='__main__':
    unittest.main(verbosity=2)

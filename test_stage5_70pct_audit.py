#!/usr/bin/env python3
"""Offline failure-path tests for the Stage 5 predictive accuracy audit."""
import csv
import tempfile
import unittest
from pathlib import Path
from stage5_70pct_audit import wilson_lower, audited_fixed_phase, inspect_forward

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


if __name__=='__main__':
    unittest.main(verbosity=2)

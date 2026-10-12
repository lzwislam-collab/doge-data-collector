import math
import unittest
from datetime import datetime, timedelta, timezone
import forward_diagnostics as d


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 15, tzinfo=timezone.utc)

    def forecast(self, fid='a', p=.8, hours=1):
        issued = self.now - timedelta(hours=2)
        return {'forecast_id': fid, 'model_id': 'shadow', 'p_up': str(p),
                'direction': 'UP', 'horizon_hours': str(hours),
                'issued_at_utc': issued.isoformat(),
                'target_utc': (issued+timedelta(hours=hours)).isoformat(),
                'btc_momentum': 'UP', 'training_last_label_utc': issued.isoformat(),
                'walkforward_holdout_brier': '.1'}

    def score(self, f, label='UP'):
        return {'forecast_id': f['forecast_id'], 'status': 'scored',
                'target_quote_utc': f['target_utc'], 'realized_up': label,
                'brier': str((float(f['p_up'])-int(label=='UP'))**2)}

    def test_empty_calibration_is_wait_and_null(self):
        out=d.calibration([], {}, self.now, self.now-timedelta(days=1))
        self.assertEqual(out['status'], 'WAIT_FUTURE_DATA')
        self.assertIsNone(out['brier_mean'])

    def test_missing_outcome_keeps_denominator_and_worst_case(self):
        f=self.forecast(); g=self.forecast('b'); g['issued_at_utc']=(self.now-timedelta(hours=4)).isoformat(); g['target_utc']=(self.now-timedelta(hours=3)).isoformat()
        g['training_last_label_utc']=g['issued_at_utc']
        out=d.calibration([f,g], {'a':self.score(f)}, self.now, self.now-timedelta(days=1))
        self.assertEqual((out['due'],out['scored'],out['missing']), (2,1,1))
        self.assertEqual(out['accuracy_if_missing_wrong_pct'],50)
        self.assertAlmostEqual(out['brier_mean'],.04)
        self.assertAlmostEqual(out['brier_half_probability_baseline'],.25)

    def test_overlap_is_reserved_even_when_outcome_missing(self):
        f=self.forecast(); g=dict(f,forecast_id='b')
        out=d.calibration([f,g], {'b':self.score(g)}, self.now, self.now-timedelta(days=1))
        self.assertEqual((out['selected'],out['overlap_excluded'],out['scored']), (1,1,0))

    def test_future_training_label_rejected(self):
        f=self.forecast(); f['training_last_label_utc']=self.now.isoformat()
        with self.assertRaisesRegex(ValueError,'training label'): d.calibration([f],{},self.now,self.now-timedelta(days=1))

    def test_invalid_probability_rejected(self):
        for p in (math.nan,math.inf,-.1,1.1):
            with self.assertRaises(ValueError): d.calibration([self.forecast(p=p)],{},self.now,self.now-timedelta(days=1))

    def test_forged_brier_or_late_quote_rejected(self):
        f=self.forecast(); s=self.score(f); s['brier']='0'
        with self.assertRaisesRegex(ValueError,'Brier'): d.calibration([f],{'a':s},self.now,self.now-timedelta(days=1))
        s=self.score(f); s['target_quote_utc']=self.now.isoformat()
        with self.assertRaisesRegex(ValueError,'target window'): d.calibration([f],{'a':s},self.now,self.now-timedelta(days=1))

    def test_deadline_urgent_and_expired_never_removed(self):
        f=self.forecast(); f['target_utc']=(self.now-timedelta(minutes=20)).isoformat()
        out=d.deadlines([f],{},self.now)
        self.assertEqual(out[0]['state'],'DUE_URGENT')
        self.assertEqual(out[0]['seconds_left'],300)
        out=d.deadlines([f],{},self.now+timedelta(minutes=6))
        self.assertEqual(out[0]['state'],'MISSED_WINDOW')

    def test_duplicate_forecast_rejected(self):
        f=self.forecast()
        with self.assertRaisesRegex(ValueError,'duplicate'): d.deadlines([f,f],{},self.now)

    def test_exit_economics_uses_ask_bid_and_cost_stress(self):
        out=d.cost_scenario(100,101,100,10,5)
        expected=(101*(1-.0005)*(1-.001)/(100*(1+.0005)*(1+.001))-1)*100
        self.assertAlmostEqual(out['net_return_pct'],expected)
        self.assertLess(d.cost_scenario(100,101,100,20,10)['net_return_pct'],expected)

    def test_down_is_cash_and_hold_comparator_still_loses_costs(self):
        records=[{'quote':{'quote_utc':self.now.isoformat()},'observations':[
            {'forecast_id':'a','kind':'entry_quote','ask':100,'bid':99,'forecast':{'doge_reversal_direction':'DOWN'}}]},
            {'quote':{'quote_utc':(self.now+timedelta(hours=1)).isoformat()},'observations':[
            {'forecast_id':'a','kind':'exit_quote','ask':102,'bid':101,'forecast':{'doge_reversal_direction':'DOWN'}}]}]
        out=d.execution_costs(records)['intervals'][0]
        self.assertEqual(out['strategy_net_return_pct'],0)
        self.assertIsNotNone(out['hold_net_return_pct'])
        self.assertFalse(out['profit_validated'])

    def test_gaps_and_missing_exits_block_execution_claim(self):
        records=[{'quote':{'quote_utc':self.now.isoformat()},'observations':[
            {'forecast_id':'a','kind':'entry_quote','ask':100,'bid':99,'forecast':{'doge_reversal_direction':'UP'},'path_gap':True}]}]
        out=d.execution_costs(records)
        self.assertEqual(out['intervals'][0]['status'],'WAIT_EXIT')
        self.assertIsNone(out['intervals'][0]['strategy_net_return_pct'])
        self.assertFalse(out['continuous_drawdown_verified'])

    def test_horizons_must_not_be_pooled(self):
        with self.assertRaisesRegex(ValueError,'horizons'): d.calibration([self.forecast(),self.forecast('b',hours=24)],{},self.now,self.now-timedelta(days=1))


if __name__=='__main__': unittest.main()

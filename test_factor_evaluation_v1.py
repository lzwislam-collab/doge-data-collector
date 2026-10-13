"""Synthetic fixtures exercise review logic, never demonstrate market edge."""
import copy
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone

try:
    import factor_evaluation_v1 as evaluation
except ModuleNotFoundError:
    evaluation = None

START = datetime(2026, 10, 15, tzinfo=timezone.utc)


def policy():
    return dict(protocol_id='TEST', effective_from_utc=START.isoformat(),
                review_dates_utc=[(START+timedelta(days=120)).isoformat()],
                horizons_hours=[1,24], groups=['FOUR_HOUR'],
                minimum_samples=100, minimum_days=90, minimum_coverage=.9,
                minimum_discordant=30, minimum_regime_samples=20,
                block_days=3, minimum_blocks=30, bootstrap_replicates=2048,
                bootstrap_seed=731, familywise_alpha=.05, family_size=1,
                minimum_bootstrap_tail=20, accuracy_margin=.01,
                brier_margin=.002, net_return_margin=.0002,
                max_issue_delay_minutes=45, target_window_minutes=25)


def rows(sign=1, days=120):
    result=[]
    for day in range(days):
        issued=START+timedelta(days=day,minutes=1)
        y=day%2
        good=.9 if y else .1
        bad=1-good
        result.append(dict(event_id=str(day), horizon_hours=1,
            issued_at_utc=issued.isoformat(), target_utc=(issued+timedelta(hours=1)).isoformat(),
            group='FOUR_HOUR', candidate_p=good if sign>0 else bad,
            paired_p=bad if sign>0 else good,
            candidate_direction='UP' if (good if sign>0 else bad)>=.5 else 'DOWN',
            paired_direction='UP' if (bad if sign>0 else good)>=.5 else 'DOWN',
            frozen_direction='UP' if (bad if sign>0 else good)>=.5 else 'DOWN',
            y=y, regime='UP' if day%2 else 'DOWN', paper_action='NO_TRADE',
            economic_deltas={k:.01*sign for k in evaluation.ECONOMIC_KEYS} if evaluation else {},
            rolling_validation_verified=True))
    return result


class FactorEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(evaluation, 'objective evaluator is not implemented')

    def test_early_review_cannot_promote_even_perfect_results(self):
        r=evaluation.evaluate(policy(),rows(days=119),START+timedelta(days=119),registered=True)
        self.assertEqual(r['status'],'WAIT_EVIDENCE')
        self.assertIn('not_fixed_review_date',r['reasons'])

    def test_unregistered_perfect_results_cannot_promote(self):
        r=evaluation.evaluate(policy(),rows(),START+timedelta(days=120),registered=False)
        self.assertEqual(r['status'],'WAIT_EVIDENCE')

    def test_positive_and_negative_evidence_are_symmetric_review_only(self):
        # One issued forecast each 24h in a 2h planned schedule -> low coverage.
        p=policy();p['slot_step_hours']=24
        for sign,status in [(1,'REVIEW_PROMOTION'),(-1,'REVIEW_RETIREMENT')]:
            with self.subTest(sign=sign):
                r=evaluation.evaluate(p,rows(sign),START+timedelta(days=120),registered=True)
                self.assertEqual(r['status'],status)
                self.assertFalse(r['production_allowed'])
                self.assertFalse(r['real_orders_authorized'])

    def test_missing_issuance_stays_in_denominator(self):
        r=evaluation.evaluate(policy(),rows(),START+timedelta(days=120),registered=True)
        # Last 22:00 slot can issue at 22:45; its 25m target window closes
        # after midnight. It is pending for every candidate, not yet due.
        self.assertEqual(r['coverage']['expected'],1439)
        self.assertEqual(r['coverage']['issued'],120)
        self.assertEqual(r['status'],'WAIT_EVIDENCE')

    def test_no_trade_still_counts_for_accuracy(self):
        p=policy();p['slot_step_hours']=24
        r=evaluation.evaluate(p,rows(),START+timedelta(days=120),registered=True)
        self.assertEqual(r['metrics']['candidate_accuracy'],1)
        self.assertEqual(r['metrics']['no_trade_n'],120)

    def test_duplicate_event_and_horizon_pooling_rejected(self):
        for mutation in ('duplicate','horizon'):
            data=rows()
            if mutation=='duplicate':data.append(copy.deepcopy(data[0]))
            else:data[0]['horizon_hours']=24
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                evaluation.evaluate(policy(),data,START+timedelta(days=120),registered=True)

    def test_future_label_and_invalid_probability_rejected(self):
        for field,value in [('y',2),('candidate_p',float('nan')),
                            ('target_utc',(START+timedelta(hours=2)).isoformat())]:
            data=rows();data[0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                evaluation.evaluate(policy(),data,START+timedelta(days=120),registered=True)

    def test_missing_cost_and_missing_rolling_proof_block_review(self):
        p=policy();p['slot_step_hours']=24
        for field,value in [('economic_deltas',None),('rolling_validation_verified',False)]:
            data=rows();data[0][field]=value
            r=evaluation.evaluate(p,data,START+timedelta(days=120),registered=True)
            self.assertEqual(r['status'],'WAIT_EVIDENCE')

    def test_negative_predictive_gain_can_retire_despite_missing_book(self):
        p=policy();p['slot_step_hours']=24;data=rows(-1);data[0]['economic_deltas']=None
        r=evaluation.evaluate(p,data,START+timedelta(days=120),registered=True)
        self.assertEqual(r['status'],'REVIEW_RETIREMENT')
        self.assertIn('incomplete_same_event_cost_evidence',r['promotion_blockers'])

    def test_pre_activation_events_cannot_be_reused(self):
        p=policy();p['slot_step_hours']=24
        data=rows();data[0]['issued_at_utc']=(START-timedelta(days=1)).isoformat()
        data[0]['target_utc']=(START-timedelta(days=1)+timedelta(hours=1)).isoformat()
        with self.assertRaises(ValueError):
            evaluation.evaluate(p,data,START+timedelta(days=120),registered=True)

    def test_bootstrap_is_reproducible_and_preserves_calendar_gaps(self):
        data=[(START+timedelta(days=i)).date().isoformat() for i in (0,1,9,10)]
        x=evaluation.block_interval(data,[1,1,-1,-1],policy())
        self.assertEqual(x,evaluation.block_interval(data,[1,1,-1,-1],policy()))
        self.assertEqual(x['calendar_days'],11)
        self.assertLessEqual(x['lower'],0)
        self.assertGreaterEqual(x['upper'],0)

    def test_multiplicity_tails_and_regime_shortage_block_review(self):
        p=policy();p['slot_step_hours']=24;p['family_size']=288
        r=evaluation.evaluate(p,rows(),START+timedelta(days=120),registered=True)
        self.assertIn('insufficient_bootstrap_tail_resolution',r['reasons'])
        p=policy();p['slot_step_hours']=24;data=rows()
        for item in data:item['regime']='UP'
        r=evaluation.evaluate(p,data,START+timedelta(days=120),registered=True)
        self.assertIn('insufficient_exante_regime_coverage',r['reasons'])

    def test_missed_review_is_not_unlimited_sequential_testing(self):
        r=evaluation.evaluate(policy(),rows(),START+timedelta(days=121),registered=True)
        self.assertIn('not_fixed_review_date',r['reasons'])


if __name__=='__main__':unittest.main()

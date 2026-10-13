import hashlib
import json
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch

import p2_locked_forward_v1 as f
import p2_paired_execution_v3_1 as engine

E=datetime(2026,10,14,tzinfo=timezone.utc)


def issue(h,index,verified=True,action='NO_TRADE'):
    when=E+timedelta(hours=index,minutes=7)
    return {'forecast_id':f'{h}h_{index}','issued_at_utc':when.isoformat(),
            'target_utc':(when+timedelta(hours=h)).isoformat(),
            'horizon_hours':h,'paired_audit_verified':verified,
            'source_verified':verified,'paper_action':action}


def book(minutes):
    when=E+timedelta(minutes=minutes)
    return {'quote':{'quote_utc':when.isoformat()},'captured_at_utc':when.isoformat()}


def replay_result():
    def p():
        return {
            'completed_due_intervals':0,'path_gap_count':0,'missing_mark_count':0,
            'blocked_forecast_id':None,'blocked_reason':None,
            'paper_long_completed':0,'no_trade_completed':0,
            'strategy':{'prefix_net_return_pct':None,'sampled_drawdown_pct':0},
            'hold':{'prefix_net_return_pct':None},
            'double_cost':{'prefix_net_return_pct':None},
            'double_cost_hold':{'prefix_net_return_pct':None},
        }
    return {'1h':p(),'24h':p()}


class FixedForwardTests(unittest.TestCase):
    def policy(self,root):
        return json.loads((root/f.PROTOCOL).read_text())

    def test_before_activation_never_reads_or_scores_past(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/f.PROTOCOL).parent.mkdir(parents=True)
            source=Path(__file__).resolve().parent/f.PROTOCOL
            (root/f.PROTOCOL).write_bytes(source.read_bytes())
            with patch.object(engine,'guard',return_value={}):
                r=f.run(root,E-timedelta(minutes=1))
            self.assertEqual(r['status'],'WAIT_ACTIVATION')
            self.assertEqual(r['portfolios'],{})
            self.assertFalse(r['economic_edge_validated'])

    def test_protocol_freeze_rejects_changed_start_and_gap(self):
        p=json.loads((Path(__file__).resolve().parent/f.PROTOCOL).read_text())
        for change in ({'effective_from_utc':'2026-10-13T08:00:00Z'},
                       {'max_gap_seconds':3600},{'no_auto_reset':False},
                       {'locked_code_git_blob_sha':'garbage'}):
            bad=dict(p,**change)
            with self.assertRaises(ValueError):
                f.verify_protocol(Path('/ignored'),E,bad)

    def test_source_denominator_retains_missing_issuance_and_missing_pair(self):
        items=[issue(1,0),issue(1,2,False),issue(24,0)]
        fake=replay_result()
        with patch.object(engine,'replay',return_value=fake):
            a=f.assess(items,[],[book(m) for m in (1,11,21,31,41,51,61,71,81,91,101,111,121,131,141)],E+timedelta(hours=3,minutes=15),E,{})
        one=a['portfolios']['1h']
        self.assertEqual(one['expected_closed_hourly_issue_slots'],3)
        self.assertEqual(one['actual_issued_slots'],2)
        self.assertEqual(one['verified_paired_source_slots'],1)
        self.assertEqual(one['missing_issue_slots'],1)
        self.assertEqual(one['fixed_precommitted_nonoverlap_selected'],2)
        self.assertFalse(one['eligible_for_independent_paper_review_only'])

    def test_gaps_never_reset_on_later_clean_books(self):
        rows=[book(m) for m in (1,11,52,62,72,82,92)]
        with patch.object(engine,'replay',return_value=replay_result()):
            r=f.assess([issue(1,0),issue(24,0)],[],rows,E+timedelta(minutes=93),E,{})
        self.assertEqual(r['gap_count_including_open_tail'],1)
        self.assertEqual(r['gap_records'][0]['kind'],'missing_book_path')
        self.assertFalse(r['portfolios']['1h']['eligible_for_independent_paper_review_only'])
        self.assertFalse(r['economic_edge_validated'])

    def test_stale_tail_and_no_books_visible_not_success(self):
        with patch.object(engine,'replay',return_value=replay_result()):
            result=f.assess([issue(1,0)],[],[],E+timedelta(minutes=45),E,{})
        self.assertGreaterEqual(result['gap_count_including_open_tail'],1)
        self.assertEqual(result['portfolios']['1h']['original_due_book_coverage_pct'],None)
        self.assertFalse(result['economic_edge_validated'])

    def test_uncertified_positive_gain_still_never_claims_profit(self):
        p=replay_result()
        for horizon in ('1h','24h'):
            p[horizon]['completed_due_intervals']=101
            p[horizon]['strategy']['prefix_net_return_pct']=8
            p[horizon]['hold']['prefix_net_return_pct']=4
            p[horizon]['double_cost']['prefix_net_return_pct']=7
            p[horizon]['double_cost_hold']['prefix_net_return_pct']=3
        items=[issue(h,i*26 if h==24 else i*2) for h in (1,24) for i in range(101)]
        # This is a fake synthetic test that must never authorize actual economic claims.
        with patch.object(engine,'replay',return_value=p):
            r=f.assess(items,[],[book(1)],E+timedelta(days=200),E,{})
        self.assertFalse(r['economic_edge_validated'])
        self.assertFalse(r['allow_model_promotion'])
        self.assertTrue(r['gap_count_including_open_tail']>0)

    def test_invalid_future_books_are_rejected(self):
        with patch.object(engine,'replay',return_value=replay_result()):
            with self.assertRaises(ValueError):
                f.assess([],[],[book(22),book(20)],E+timedelta(hours=1),E,{})


if __name__=='__main__':unittest.main()

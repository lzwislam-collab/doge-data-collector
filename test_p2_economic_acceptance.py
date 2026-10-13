import copy
import unittest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
import p2_paired_execution_v3_1 as engine

try:
    from p2_economic_acceptance import evaluate
except ImportError:
    evaluate = None


NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def mature_sources():
    rows={}
    for h in (1,24):
        for i in range(100):
            issued=NOW-timedelta(hours=(101-i)*(h+1));fid=str(h)+'-'+str(i)
            rows[fid]={'forecast_id':fid,'horizon_hours':h,'issued_at_utc':issued.isoformat(),
                'target_utc':(issued+timedelta(hours=h)).isoformat(),
                'observed_public_utc':(issued+timedelta(seconds=5)).isoformat()}
    return rows


def mature_report():
    portfolios={}
    for h in (1,24):
        items=[dict(row,paper_action='PAPER_LONG' if row['forecast_id'].endswith('-0') else 'NO_TRADE',
            source_verified=True,pair_sha256='a'*64) for row in mature_sources().values() if row['horizon_hours']==h]
        frame=engine.Frame(items)
        for i,item in enumerate(items):
            for at,price in ((datetime.fromisoformat(item['issued_at_utc'])+timedelta(minutes=1),100 if i==0 else 105),
                    (datetime.fromisoformat(item['target_utc'])+timedelta(minutes=1),110 if i==0 else 105)):
                book={'requested_at_utc':(at-timedelta(seconds=1)).isoformat(),'captured_at_utc':at.isoformat(),
                    'quote':{'quote_utc':at.isoformat(),'spread_bps':0},'asks':[[price,100000]],'bids':[[price,100000]],'book_sha256':at.isoformat()}
                frame.on_book(book)
        p=frame.summary(NOW)
        p.update(observation_days=110,selected_source_intervals=100,verified_selected_source_intervals=100,
            expected_closed_source_hour_slots=2640,original_source_issue_slots=2640,verified_pair_slots=2640,
            missing_original_or_verified_pair_slots=0,path_gap_count=0,missing_mark_count=0,latest_mark_fresh=True,
            last_valid_mark_utc=NOW.isoformat(),tail_path_gap=False,tail_gap_seconds=0)
        portfolios[str(h)+'h']=p
    return {'status':'ok','version_id':'DOGE_P2_PAIRED_EXECUTION_V3_1_20261013_0000','audited_at_utc':NOW.isoformat(),
        'real_orders_authorized':False,'actual_account_fee_verified':False,'continuous_drawdown_verified':False,'portfolios':portfolios}


class EconomicAcceptanceTests(unittest.TestCase):
    def audit(self, report):
        self.assertIsNotNone(evaluate, 'pure supplementary economic evaluator is missing')
        return evaluate(report, NOW, sources=mature_sources())

    def test_mature_paper_review_retains_all_denominators_and_limits(self):
        report = mature_report(); before = copy.deepcopy(report)
        out = self.audit(report)
        self.assertTrue(out['eligible_for_independent_paper_review_only'])
        self.assertFalse(out['real_orders_authorized']); self.assertFalse(out['auto_promotion_authorized'])
        self.assertFalse(out['economic_acceptance_passed'])
        self.assertEqual(out['portfolios']['1h']['coverage']['expected_closed_source_hour_slots'],2640)
        self.assertGreater(out['portfolios']['1h']['comparison']['strategy_minus_hold_pct_points'],0)
        self.assertEqual(report,before)

    def test_either_immature_horizon_cannot_pass(self):
        for horizon in ('1h','24h'):
            report=mature_report(); report['portfolios'][horizon]['observation_days']=29.99
            self.assertFalse(self.audit(report)['eligible_for_independent_paper_review_only'])

    def test_missing_or_nonfinite_economics_are_not_zero(self):
        for missing in (None, float('nan'), float('inf')):
            report=mature_report(); report['portfolios']['1h']['strategy']['prefix_net_return_pct']=missing
            out=self.audit(report)
            self.assertFalse(out['eligible_for_independent_paper_review_only'])
            self.assertIsNone(out['portfolios']['1h']['comparison']['strategy_minus_hold_pct_points'])

    def test_stale_future_or_wrong_version_report_blocks(self):
        for change in ({'audited_at_utc':(NOW-timedelta(seconds=901)).isoformat()},
                       {'audited_at_utc':(NOW+timedelta(seconds=1)).isoformat()}, {'version_id':'V3'}):
            report=mature_report();report.update(change)
            self.assertEqual(self.audit(report)['status'],'BLOCKED')

    def test_gaps_missing_fee_and_denominator_defects_block(self):
        for field,value in [('tail_path_gap',True),('path_gap_count',1),('missing_mark_count',1),
                ('expected_closed_source_hour_slots',None),('verified_pair_slots',2641),('completed_due_intervals',101)]:
            report=mature_report();report['portfolios']['1h'][field]=value
            self.assertFalse(self.audit(report)['eligible_for_independent_paper_review_only'])
        report=mature_report();report['portfolios']['1h']['strategy']['recorded_trade_cost_components_usdt']={}
        self.assertFalse(self.audit(report)['eligible_for_independent_paper_review_only'])

    def test_missing_horizon_or_forged_comparison_blocks(self):
        report=mature_report();del report['portfolios']['24h']
        self.assertFalse(self.audit(report)['eligible_for_independent_paper_review_only'])

    def test_duplicate_id_bad_entry_and_missing_slippage_cannot_pass(self):
        for defect in ('duplicate','future_source','entry_after_exit','late_entry','late_exit','overlap','wrong_horizon','missing_slip','ledger_cost','bad_action'):
            report=mature_report();p=report['portfolios']['1h'];rows=p['ledger'];sources=mature_sources()
            if defect=='duplicate':rows[1]['forecast_id']=rows[0]['forecast_id']
            elif defect=='future_source':sources[rows[0]['forecast_id']]['issued_at_utc']=(NOW+timedelta(hours=1)).isoformat()
            elif defect=='entry_after_exit':rows[0]['entry_quote_utc']=rows[0]['exit_quote_utc']
            elif defect=='late_entry':rows[0]['entry_quote_utc']=(datetime.fromisoformat(sources[rows[0]['forecast_id']]['observed_public_utc'])+timedelta(seconds=601)).isoformat()
            elif defect=='late_exit':rows[0]['exit_quote_utc']=(datetime.fromisoformat(sources[rows[0]['forecast_id']]['target_utc'])+timedelta(minutes=26)).isoformat()
            elif defect=='overlap':sources[rows[1]['forecast_id']]['issued_at_utc']=sources[rows[0]['forecast_id']]['target_utc']
            elif defect=='wrong_horizon':sources[rows[0]['forecast_id']]['horizon_hours']=24
            elif defect=='missing_slip':del p['strategy']['recorded_trade_cost_components_usdt']['entry_extra_slippage_usdt']
            elif defect=='ledger_cost':rows[0]['entry_legs']['strategy']['costs']['buy_fee_usdt']+=1
            elif defect=='bad_action':rows[0]['paper_action']='BUY'
            self.assertFalse(evaluate(report,NOW,sources=sources)['eligible_for_independent_paper_review_only'],defect)

    def test_mature_ledger_without_source_timing_cannot_pass(self):
        self.assertFalse(evaluate(mature_report(),NOW)['eligible_for_independent_paper_review_only'])

    def test_existing_immature_report_remains_wait_without_new_fields(self):
        root=Path(__file__).resolve().parent
        report=json.loads((root/'data/p2_paired_execution_v3_1/latest.json').read_text(encoding='utf-8'))
        self.assertEqual(evaluate(report,datetime.fromisoformat(report['audited_at_utc']))['status'],'WAIT')

    def test_future_ledger_or_double_cost_gain_cannot_pass(self):
        report=mature_report();report['portfolios']['1h']['ledger'][-1]['exit_quote_utc']=(NOW+timedelta(seconds=1)).isoformat()
        self.assertFalse(self.audit(report)['eligible_for_independent_paper_review_only'])
        report=mature_report();p=report['portfolios']['1h'];p['double_cost']['prefix_net_return_pct']=11
        p['double_cost']['prefix_liquidation_equity_usdt']=1110;p['ledger'][-1]['equity_usdt']['double_cost']=1110
        self.assertFalse(self.audit(report)['eligible_for_independent_paper_review_only'])

    def test_malformed_clock_and_impossible_due_denominator_fail_closed(self):
        report=mature_report();report['audited_at_utc']=123
        self.assertEqual(self.audit(report)['status'],'BLOCKED')
        report=mature_report();report['portfolios']['1h']['matured_due_intervals']=101
        self.assertFalse(self.audit(report)['eligible_for_independent_paper_review_only'])

    def test_cli_missing_input_preserves_failure_and_immature_input_waits(self):
        import p2_economic_acceptance as audit
        self.assertTrue(hasattr(audit,'main'),'safe CLI missing')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.assertEqual(audit.main(root,NOW),1)
            output=root/'data/p2_economic_acceptance_v1/latest.json'
            self.assertEqual(json.loads(output.read_text())['status'],'BLOCKED')
            path=root/'data/p2_paired_execution_v3_1/latest.json';path.parent.mkdir(parents=True)
            report=mature_report();report['portfolios']['1h']['observation_days']=0
            path.write_text(json.dumps(report))
            self.assertEqual(audit.main(root,NOW),0)
            saved=json.loads(output.read_text());self.assertEqual(saved['status'],'WAIT')
            self.assertFalse(saved['economic_acceptance_passed'])
            report['portfolios']['1h']['strategy']['recorded_trade_cost_components_usdt']['buy_fee_usdt']=float('nan')
            path.write_text(json.dumps(report))
            self.assertEqual(audit.main(root,NOW),0)
            saved=json.loads(output.read_text());self.assertFalse(saved['eligible_for_independent_paper_review_only'])
            self.assertIsNone(saved['portfolios']['1h']['account_facts']['strategy']['recorded_trade_cost_components_usdt']['buy_fee_usdt'])
        report=mature_report();report['portfolios']['1h']['ledger'][-1]['equity_usdt']['hold']=999
        self.assertFalse(self.audit(report)['eligible_for_independent_paper_review_only'])

    def test_actual_engine_flat_roundtrip_loses_and_double_cost_is_lower(self):
        nominal=engine.buy([[100,10000]],1000,.001,.0005,0)
        stressed=engine.buy([[100,10000]],1000,.002,.001,0)
        normal_exit=engine.sell([[100,10000]],nominal['quantity'],.001,.0005,0)
        stress_exit=engine.sell([[100,10000]],stressed['quantity'],.002,.001,0)
        self.assertAlmostEqual(normal_exit['cash_usdt'],997.004495,places=5)
        self.assertLess(stress_exit['cash_usdt'],normal_exit['cash_usdt'])
        self.assertAlmostEqual(nominal['gross_usdt']+sum(nominal['costs'].values()),1000)
        self.assertAlmostEqual(normal_exit['gross_usdt']-sum(normal_exit['costs'].values()),normal_exit['cash_usdt'])

    def test_actual_engine_hold_buys_once_across_two_intervals(self):
        def item(n):
            at=NOW+timedelta(hours=n)
            return {'forecast_id':str(n),'issued_at_utc':at.isoformat(),'target_utc':(at+timedelta(hours=1)).isoformat(),
                    'observed_public_utc':(at+timedelta(seconds=5)).isoformat(),'source_verified':True,
                    'horizon_hours':1,'paper_action':'PAPER_LONG','pair_sha256':'a'*64}
        def book(minutes):
            at=NOW+timedelta(minutes=minutes)
            return {'requested_at_utc':(at-timedelta(seconds=1)).isoformat(),'captured_at_utc':at.isoformat(),
                'quote':{'quote_utc':at.isoformat(),'spread_bps':0},'asks':[[100,10000]],'bids':[[100,10000]],'book_sha256':str(minutes)}
        p=engine.replay([item(0),item(2)],[book(1),book(61),book(121),book(181)],NOW+timedelta(minutes=186))['1h']
        self.assertEqual(p['hold']['buy_legs'],1);self.assertEqual(p['strategy']['buy_legs'],2)
        self.assertEqual(p['hold']['sell_legs'],0)


if __name__=='__main__':unittest.main()

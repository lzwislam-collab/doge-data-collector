import json,tempfile,unittest,hashlib
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import p2_paired_execution_v3 as p
import test_p2_paired_execution_v3 as fixtures

class RepairTests(unittest.TestCase):
    def setUp(self):self.helper=fixtures.PaperTests();self.helper.setUp();self.t=self.helper.t

    def test_complete_hourly_sources_not_penalized_by_execution_reservation(self):
        rows=[]
        for h in (1,24):
            group=[dict(self.helper.item(str(h)+'_'+str(i),offset=i,h=h),paired_audit_verified=True) for i in range(120)]
            chosen={x['forecast_id'] for x in p.reserve(group)}
            for item in group:item['source_verified']=item['forecast_id'] in chosen
            rows+=group
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(p,'guard',return_value={'effective_from_utc':self.t.isoformat(),'protocol_id':'test'}),patch.object(p,'history_guard',return_value=[]),patch.object(p,'source_items',return_value=rows),patch.object(p,'datetime') as clock:
                clock.now.return_value=self.t+timedelta(hours=120,minutes=44)
                r=p.run(Path(tmp),capture=False)
        for h in ('1h','24h'):
            self.assertEqual(r['portfolios'][h]['paired_issue_coverage_pct'],100)
            self.assertEqual(r['portfolios'][h]['selected_source_verification_pct'],100)

    def test_tail_gap_between_last_book_and_audit_is_reported(self):
        r=self.helper.replay([self.helper.item()],[self.helper.book(m) for m in (1,20,40,61)],600)['1h']
        self.assertEqual(r['path_gap_count'],1)
        self.assertEqual(r['tail_gap_seconds'],32340)
        self.assertFalse(r['latest_mark_fresh'])

    def test_fresh_tail_and_repeated_summary_do_not_create_gaps(self):
        frame=p.Frame([self.helper.item()])
        for m in (1,20,40,61):frame.on_book(self.helper.book(m))
        self.assertEqual(frame.summary(self.t+timedelta(minutes=80))['path_gap_count'],0)
        a=frame.summary(self.t+timedelta(minutes=600));b=frame.summary(self.t+timedelta(minutes=600))
        self.assertEqual(a['path_gap_count'],b['path_gap_count'])

    def test_missing_hourly_pair_remains_in_all_source_denominator(self):
        rows=[dict(self.helper.item(str(i),offset=i),paired_audit_verified=i!=1) for i in range(4)]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(p,'guard',return_value={'effective_from_utc':self.t.isoformat(),'protocol_id':'test'}),patch.object(p,'history_guard',return_value=[]),patch.object(p,'source_items',return_value=rows),patch.object(p,'datetime') as clock:
                clock.now.return_value=self.t+timedelta(hours=4,minutes=44)
                r=p.run(Path(tmp),capture=False)['portfolios']['1h']
        self.assertEqual(r['expected_closed_source_hour_slots'],4)
        self.assertEqual(r['paired_issue_coverage_pct'],75)
        self.assertEqual(r['missing_original_or_verified_pair_slots'],1)

    def test_source_audit_covers_unreserved_ids_but_rejected_pairs_do_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);now=self.t+timedelta(hours=4)
            original={str(i):self.helper.item(str(i),offset=i) for i in range(4)}
            pairs=[{'production':row,'decision_recorded_at_utc':row['issued_at_utc'],
                'record_sha256':str(i),'decision':{'paper_action':'NO_TRADE'}} for i,row in enumerate(original.values())]
            proofs={fid:{'publication':{'head_sha':'h','observed_public_utc':row['observed_public_utc']}} for fid,row in original.items()}
            audit={'protocol_id':'paired','registered_pairs':4,'evaluated_at_utc':now.isoformat(),
                'rejected_or_missing':{'3':'missing_public_witness'},'input_registry_sha256':hashlib.sha256(b'registry').hexdigest(),'status':'ok'}
            for path,body in [('data/direction_v42/forward_registry.csv','registry'),(p.quote_source.BINDINGS,'bindings'),
                ('data/p0_operations/latest.json',json.dumps({'status':'ok','audited_at_utc':now.isoformat(),'forecast_proofs':proofs})),
                ('data/paired_forward_v1/audit_latest.json',json.dumps(audit))]:
                target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(body,encoding='utf-8')
            with patch.object(p.source,'guard',return_value={'protocol_id':'paired'}),patch.object(p.source,'history_guard',return_value=pairs),patch.object(p.frozen,'read_rows',side_effect=lambda path,cols: original if str(path).endswith('forward_registry.csv') else {}),patch.object(p.paired_audit,'verify_pair',return_value=True):
                items=p.source_items(root,now,self.t)
            byid={i['forecast_id']:i for i in items}
            self.assertTrue(byid['1']['paired_audit_verified'])
            self.assertFalse(byid['1']['source_verified'])
            self.assertFalse(byid['3']['paired_audit_verified'])

    def test_failed_mark_does_not_refresh_tail_age(self):
        frame=p.Frame([self.helper.item()])
        frame.on_book(self.helper.book(1))
        frame.on_book(self.helper.book(61))
        broken=self.helper.book(600,depth=0)
        frame.on_book(broken)
        r=frame.summary(self.t+timedelta(minutes=600))
        self.assertEqual(r['tail_gap_seconds'],32340)
        self.assertFalse(r['latest_mark_fresh'])
        self.assertGreater(r['missing_mark_count'],0)

    def test_v3_has_independent_namespace_and_complete_statistical_dependency(self):
        import p2_paired_execution_v2 as old
        self.assertNotEqual(p.BASE,old.BASE)
        self.assertNotEqual(p.POLICY,old.POLICY)
        self.assertNotEqual(p.SYSTEM,old.SYSTEM)
        self.assertIn('p1_model_competition.py',p.CODE_FILES)

    def test_tail_or_stale_mark_blocks_review_even_when_other_gates_pass(self):
        rows=[dict(self.helper.item(str(i),offset=i),paired_audit_verified=True) for i in range(720)]
        chosen={i['forecast_id'] for i in p.reserve(rows)}
        for item in rows:item['source_verified']=item['forecast_id'] in chosen
        for fresh,gaps,expected in [(True,0,True),(False,0,False),(True,1,False)]:
            portfolio={'matured_due_intervals':100,'completed_due_intervals':100,'blocked_forecast_id':None,
                'path_gap_count':gaps,'missing_mark_count':0,'latest_mark_fresh':fresh,
                'excess_over_hold_pct_points':1,'excess_over_cash_pct_points':1,
                'double_cost_excess_over_hold_pct_points':1,'double_cost':{'prefix_net_return_pct':1}}
            with tempfile.TemporaryDirectory() as tmp:
                with patch.object(p,'guard',return_value={'effective_from_utc':self.t.isoformat(),'protocol_id':'test'}),patch.object(p,'history_guard',return_value=[]),patch.object(p,'source_items',return_value=rows),patch.object(p,'replay',return_value={'1h':portfolio}),patch.object(p,'datetime') as clock:
                    clock.now.return_value=self.t+timedelta(days=30,minutes=44)
                    result=p.run(Path(tmp),capture=False)
            self.assertEqual(result['portfolios']['1h']['eligible_for_independent_paper_review_only'],expected)
            self.assertFalse(result['exact_real_profit_validated'])
            self.assertFalse(result['real_orders_authorized'])

if __name__=='__main__':unittest.main()

import copy, importlib.util, unittest
from datetime import datetime, timedelta, timezone
import test_minimal_market_candidate as fixtures

class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('minimal_market_forward'), 'forward module missing')
        import minimal_market_forward as m
        self.m=m; self.at=datetime(2026,10,13,2,tzinfo=timezone.utc)
        f=fixtures.CandidateTests();f.setUp()
        self.prepared=m.candidate.prepare(f.rows,self.at)
        self.prepared['starting_ids']=[]
        self.original={'forecast_id':'new','issued_at_utc':(self.at+timedelta(seconds=2)).isoformat(),
            'target_utc':(self.at+timedelta(hours=1,seconds=2)).isoformat(),
            'horizon_hours':'1','source_hour_start_utc':self.at.isoformat()}

    def test_real_new_original_receives_fixed_prepared_directions_only(self):
        x=self.m.make_context(self.original,self.prepared,self.at+timedelta(seconds=3),self.at)
        self.assertEqual(x['directions']['DOGE_REVERSAL_1H'],'DOWN')
        self.assertFalse(x['real_orders_authorized']);self.assertIsNone(x['p_up'])

    def test_old_original_never_backfilled(self):
        self.prepared['starting_ids']=['new']
        with self.assertRaisesRegex(ValueError,'old'):self.m.make_context(self.original,self.prepared,self.at+timedelta(seconds=3),self.at)

    def test_late_future_preparation_and_wrong_source_cutoff_rejected(self):
        for change in ('late','future','cutoff','target'):
            p=copy.deepcopy(self.prepared);r=dict(self.original);now=self.at+timedelta(seconds=3)
            if change=='late':now=self.at+timedelta(seconds=33)
            if change=='future':p['prepared_at_utc']=(self.at+timedelta(seconds=4)).isoformat()
            if change=='cutoff':r['source_hour_start_utc']=(self.at-timedelta(hours=1)).isoformat()
            if change=='target':r['target_utc']=(self.at+timedelta(hours=2)).isoformat()
            with self.assertRaises(ValueError):self.m.make_context(r,p,now,self.at)

    def test_pre_activation_original_rejected(self):
        with self.assertRaises(ValueError):self.m.make_context(self.original,self.prepared,self.at+timedelta(seconds=3),self.at+timedelta(hours=1))

    def test_unsafe_original_id_cannot_become_a_record_path(self):
        self.original['forecast_id']='../escape'
        with self.assertRaises(ValueError):self.m.make_context(self.original,self.prepared,self.at+timedelta(seconds=3),self.at)

    def test_unavailable_features_keep_original_with_missing_directions(self):
        self.prepared={'status':'WAIT_SOURCE','prepared_at_utc':self.at.isoformat(),'starting_ids':[], 'directions':{}}
        x=self.m.make_context(self.original,self.prepared,self.at+timedelta(seconds=3),self.at)
        self.assertTrue(all(v is None for v in x['directions'].values()))
        self.assertEqual(x['original'],self.original)

    def test_missing_feature_cannot_hide_an_injected_direction(self):
        self.prepared={'status':'WAIT_SOURCE','prepared_at_utc':self.at.isoformat(),'starting_ids':[],
                       'directions':{'DOGE_REVERSAL_1H':'UP'}}
        with self.assertRaises(ValueError):self.m.make_context(self.original,self.prepared,self.at+timedelta(seconds=3),self.at)

    def test_registration_manifest_alone_cannot_witness_future_code(self):
        import hashlib
        policy=b'policy';code=b'precommitted code\n'
        registry={'protocol_sha256':hashlib.sha256(policy).hexdigest(),
                  'code_sha256_lf':{'new.py':hashlib.sha256(code).hexdigest()}}
        self.m.verify_registration_bytes(registry,policy,lambda p:code)
        with self.assertRaises(ValueError):self.m.verify_registration_bytes(registry,policy,lambda p:b'future code')

    def test_missing_context_remains_in_due_denominator(self):
        r=dict(self.original,primary_direction='UP',doge_reversal_direction='DOWN',btc_momentum_direction='UP', majority_direction='DOWN')
        out=self.m.compare([r],{}, {},{},self.at+timedelta(hours=2))
        x=out['DOGE_REVERSAL_1H_1h']
        self.assertEqual(x['due_reserved'],1);self.assertEqual(x['qualified'],0);self.assertEqual(x['missing'],1)
        self.assertIsNone(x['accuracy_pct']);self.assertEqual(x['accuracy_missing_as_wrong_pct'],0)
        self.assertEqual(x['missing_reason_counts']['missing_context'],1)

    def test_no_trade_included_and_probability_not_fabricated(self):
        r=dict(self.original,primary_direction='UP',doge_reversal_direction='DOWN',btc_momentum_direction='UP',majority_direction='DOWN')
        context=self.m.make_context(r,self.prepared,self.at+timedelta(seconds=3),self.at)
        out=self.m.compare([r],{'new':context},{'new':{'realized_up':'DOWN','status':'scored'}},{'new':{}},self.at+timedelta(hours=2))
        x=out['DOGE_REVERSAL_1H_1h']
        self.assertEqual(x['qualified'],1);self.assertEqual(x['accuracy_pct'],100)
        self.assertEqual(x['no_trade_in_accuracy'],1);self.assertIsNone(x['brier'])

if __name__=='__main__':unittest.main()

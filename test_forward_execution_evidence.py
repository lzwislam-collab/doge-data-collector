import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    import forward_execution_evidence as f
except ModuleNotFoundError:
    f = None

T = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
def book(t=T, bid='0.10', ask='0.101', size='20000'):
    return {'code':'0','data':[{'ts':str(int(t.timestamp()*1000)), 'bids':[[bid,size,'0','1']], 'asks':[[ask,size,'0','1']]}]}
def forecasts(direction='UP'):
    return [{'forecast_id':'f','observed_public_utc':(T-timedelta(seconds=5)).isoformat(), 'target_utc':(T+timedelta(hours=1)).isoformat(), 'model_id':'frozen-primary','doge_reversal_direction':direction,'majority_direction':direction, 'publication':{'run_id':'1','head_sha':'a'*40}}]

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(f, 'supplementary execution evidence module must exist')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'evidence.jsonl'
        self.assertTrue(hasattr(f,'activate'), 'persistent activation required')
        f.activate(self.path,T-timedelta(seconds=10))
    def capture(self, t=T, payload=None, fs=None):
        return f.capture(self.path, fs or forecasts(), payload or book(t), t, t+timedelta(seconds=1))
    def test_post_publication_entry_is_ask_reference_not_fill(self):
        r=self.capture(); o=r['observations'][0]
        self.assertEqual(o['kind'],'entry_quote'); self.assertEqual(o['ask'],0.101)
        self.assertFalse(r['actual_fill']); self.assertEqual(len(f.read_chain(self.path)),1)
    def test_first_entry_cannot_be_backfilled_after_target(self):
        r=self.capture(T+timedelta(hours=2))
        self.assertEqual(r['observations'][0]['kind'],'missed_entry')
        self.assertNotIn('paper_equity_usdt',r['observations'][0])
    def test_pre_publication_quote_rejected(self):
        with self.assertRaises(ValueError): self.capture(payload=book(T-timedelta(seconds=6)))
        self.assertFalse(self.path.exists())
    def test_stale_future_crossed_nonfinite_or_insufficient_depth_rejected(self):
        for b in [book(T-timedelta(seconds=31)),book(T+timedelta(seconds=2)),book(bid='0.102'),book(ask='NaN'),book(size='1')]:
            with self.subTest(b=b), self.assertRaises(ValueError): self.capture(payload=b)
        self.assertFalse(self.path.exists())
    def test_duplicate_or_backward_snapshot_rejected(self):
        self.capture()
        with self.assertRaises(ValueError): self.capture()
        with self.assertRaises(ValueError): self.capture(T-timedelta(seconds=1))
    def test_exit_window_and_gaps_sampled_drawdown(self):
        self.capture(); t=T+timedelta(minutes=30)
        r=self.capture(t,book(t,bid='0.09',ask='0.091'))
        o=r['observations'][0]; self.assertEqual(o['kind'],'bid_mark')
        self.assertTrue(o['path_gap']); self.assertGreater(o['sampled_drawdown_pct'],9)
        r=self.capture(T+timedelta(hours=1)); self.assertEqual(r['observations'][0]['kind'],'exit_quote')
    def test_late_exit_never_becomes_exit_quote(self):
        self.capture(); r=self.capture(T+timedelta(hours=1,minutes=26))
        self.assertEqual(r['observations'][0]['kind'],'missed_exit')
    def test_down_signal_never_creates_short_or_long_position(self):
        r=self.capture(fs=forecasts('DOWN'))
        self.assertNotIn('paper_equity_usdt',r['observations'][0])
    def test_hash_tampering_fails_closed(self):
        self.capture(); self.path.write_text(self.path.read_text().replace('0.101','0.102'))
        with self.assertRaises(ValueError): f.read_chain(self.path)
    def test_depth_checks_all_levels_not_top_size_times_depth(self):
        b=book(size='1'); b['data'][0]['asks'].append(['0.102','20000','0','1']); b['data'][0]['bids'].append(['0.099','20000','0','1'])
        self.assertGreater(self.capture(payload=b)['quote']['ask_depth_notional_usdt'],2000)
        b['data'][0]['asks'].append(['0.100','1','0','1'])
        with self.assertRaises(ValueError): self.capture(T+timedelta(seconds=2),b)

class PublicationTests(unittest.TestCase):
    def test_registry_quote_must_match_verified_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            row={'forecast_id':'f','issued_at_utc':T.isoformat(),'source_quote_utc':T.isoformat(),'target_utc':(T+timedelta(hours=1)).isoformat(),'majority_direction':'UP'}
            import csv, json
            with (root/'registry.csv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
            proof={'issued_at_utc':T.isoformat(),'source_quote_utc':(T-timedelta(seconds=1)).isoformat(),'publication':{'forecast_id':'f','observed_public_utc':(T+timedelta(seconds=1)).isoformat(),'run_id':'1','head_sha':'a'*40,'run_url':'https://github.com/owner/repo/actions/runs/1'}}
            (root/'audit.json').write_text(json.dumps({'status':'ok','frozen_implementation_verified':True,'audited_at_utc':T.isoformat(),'input_head':'a'*40,'monitor_code_sha256':__import__('hashlib').sha256(Path(f.__file__).with_name('p0_operational_audit.py').read_bytes()).hexdigest(),'forecast_proofs':{'f':proof}}))
            with self.assertRaises(ValueError): f.load_forecasts(root/'audit.json',root/'registry.csv',T+timedelta(seconds=2))



class ReviewCorrections(unittest.TestCase):
    setUp=EvidenceTests.setUp
    capture=EvidenceTests.capture
    def test_frozen_primary_direction_overrules_majority(self):
        fs=forecasts('DOWN'); fs[0]['majority_direction']='UP'
        self.assertNotIn('paper_equity_usdt',self.capture(fs=fs)['observations'][0])
    def test_pre_activation_publication_is_permanently_excluded(self):
        fs=forecasts(); fs[0]['observed_public_utc']=(T-timedelta(seconds=20)).isoformat()
        self.assertEqual(self.capture(fs=fs)['observations'][0]['kind'],'legacy_excluded')
    def test_sweep_uses_multiple_levels_and_is_indicative(self):
        b=book(size='1'); b['data'][0]['asks'].append(['0.102','20000','0','1']); b['data'][0]['bids'].append(['0.099','20000','0','1'])
        o=self.capture(payload=b)['observations'][0]
        self.assertGreater(o['indicative_entry_vwap'],o['ask'])
        self.assertLess(o['indicative_bid_vwap'],o['bid'])
        self.assertFalse(o['actual_fill'])
    def test_activation_cannot_be_changed(self):
        with self.assertRaises(ValueError): f.activate(self.path,T)
    def test_model_identity_change_fails_closed(self):
        self.capture(); fs=forecasts(); fs[0]['model_id']='changed'
        with self.assertRaises(ValueError): self.capture(T+timedelta(seconds=5),fs=fs)



class AuditFreshnessTests(unittest.TestCase):
    def test_stale_or_future_audit_and_invalid_hash_fail_closed(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'registry.csv').write_text('forecast_id\n')
            good={'status':'ok','frozen_implementation_verified':True,'audited_at_utc':T.isoformat(),'input_head':'a'*40,'monitor_code_sha256':__import__('hashlib').sha256(Path(f.__file__).with_name('p0_operational_audit.py').read_bytes()).hexdigest(),'forecast_proofs':{}}
            for update in [{'audited_at_utc':(T-timedelta(hours=7)).isoformat()},{'audited_at_utc':(T+timedelta(seconds=1)).isoformat()},{'input_head':'z'*40},{'status':'blocked'}]:
                with self.subTest(update=update):
                    (root/'audit.json').write_text(json.dumps(dict(good,**update)))
                    with self.assertRaises(ValueError): f.load_forecasts(root/'audit.json',root/'registry.csv',T)
            (root/'audit.json').write_text(json.dumps(good))
            self.assertEqual(f.load_forecasts(root/'audit.json',root/'registry.csv',T),[])



class AuditCodeBindingTests(unittest.TestCase):
    def test_well_formed_wrong_monitor_code_hash_rejected(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'registry.csv').write_text('forecast_id\n')
            (root/'audit.json').write_text(json.dumps({'status':'ok','frozen_implementation_verified':True,'audited_at_utc':T.isoformat(),'input_head':'a'*40,'monitor_code_sha256':'b'*64,'forecast_proofs':{}}))
            with self.assertRaises(ValueError): f.load_forecasts(root/'audit.json',root/'registry.csv',T)

if __name__=='__main__': unittest.main()

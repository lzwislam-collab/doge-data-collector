import hashlib
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import p2_paired_execution_v2 as p
import p2_execution_registration as registration


class RecordsTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def snapshot(self):
        now=datetime.now(timezone.utc)
        raw={'code':'0','data':[{'ts':str(int(now.timestamp()*1000)),'bids':[['100','1000','0','1']],'asks':[['100.01','1000','0','1']]}]}
        instrument={'code':'0','data':[{'instId':'DOGE-USDT','instType':'SPOT','state':'live','lotSz':'.01','minSz':'.01','tickSz':'.01'}]}
        return {'schema_version':1,'requested_at_utc':(now-timedelta(seconds=1)).isoformat(),
                'captured_at_utc':now.isoformat(),'raw_response_utf8':json.dumps(raw),
                'instrument_raw_response_utf8':json.dumps(instrument),'instrument':p.instrument_rules(instrument),
                'instrument_observed_at_utc':(now-timedelta(seconds=2)).isoformat()}

    def test_raw_book_and_event_hashes_match(self):
        row=p.append_event(self.root,self.snapshot(),[])
        events=p.read_events(self.root);self.assertEqual(events,[row])
        self.assertEqual(len(list(p.event_books(self.root,events))),1)
        path=self.root/row['snapshot_path'];path.write_text('changed')
        with self.assertRaisesRegex(ValueError,'hash'):list(p.event_books(self.root,events))

    def test_modified_event_and_duplicate_quote_rejected(self):
        snap=self.snapshot();p.append_event(self.root,snap,[])
        with self.assertRaisesRegex(ValueError,'nonmonotonic'):p.append_event(self.root,snap,[])
        path=self.root/p.EVENTS;path.write_text(path.read_text().replace('false','true'))
        with self.assertRaisesRegex(ValueError,'chain'):p.read_events(self.root)

    def test_deleted_committed_tail_blocked(self):
        row=p.append_event(self.root,self.snapshot(),[]);path=self.root/p.EVENTS;body=path.read_bytes();path.write_text('')
        patch_body=b'diff --git a/books.jsonl b/books.jsonl\n@@ -0,0 +1 @@\n+'+body
        with patch.object(p.source.publication,'command',side_effect=[b'commit\n',patch_body]):
            with self.assertRaisesRegex(ValueError,'tail'):p.history_guard(self.root)

    def test_no_snapshot_path_escape(self):
        row=p.append_event(self.root,self.snapshot(),[]);row=dict(row,snapshot_path='../outside.json')
        with self.assertRaisesRegex(ValueError,'namespace'):list(p.event_books(self.root,[row]))

    def make_registration(self):
        for f in p.CODE_FILES:(self.root/f).write_bytes((p.ROOT/f).read_bytes())
        policy=json.loads((p.ROOT/p.POLICY).read_text());policy['effective_from_utc']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        path=self.root/p.POLICY;path.parent.mkdir(parents=True);path.write_bytes((json.dumps(policy)+'\n').encode())
        return {'engineering_pass':True,'independent_review_pass':True,'tests_passed':16,'tests_failed':0,
            'code_sha256_lf':p.code_hashes(self.root),'protocol_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'real_orders_authorized':False,'audited_at_utc':datetime.now(timezone.utc).isoformat()}

    def test_audited_exact_version_registered_and_guarded(self):
        audit=self.make_registration();registration.register(self.root,audit)
        self.assertEqual(p.guard(self.root)['status'],'frozen_future_paired_spot_paper')
        (self.root/'p2_paired_execution_v2.py').write_text('changed')
        with self.assertRaisesRegex(ValueError,'integrity'):p.guard(self.root)

    def test_missing_review_cannot_register(self):
        audit=self.make_registration();audit['independent_review_pass']=False
        with self.assertRaisesRegex(ValueError,'audit'):registration.register(self.root,audit)

    def test_before_activation_does_not_import_old_books_or_returns(self):
        policy={'effective_from_utc':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),'protocol_id':'future'}
        with patch.object(p,'guard',return_value=policy),patch.object(p,'history_guard',return_value=[]),patch.object(p,'source_items',return_value=[]),patch.object(p,'fetch_book',side_effect=AssertionError('must not fetch formal book')):
            result=p.run(self.root)
        self.assertEqual(result['activation_state'],'WAIT_ACTIVATION')
        self.assertIsNone(result['portfolios']['1h']['strategy']['prefix_net_return_pct'])


if __name__=='__main__':unittest.main()

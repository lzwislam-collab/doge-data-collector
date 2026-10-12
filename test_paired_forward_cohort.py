import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from datetime import datetime,timedelta,timezone
import paired_forward_cohort as p


class CohortTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.now=datetime(2026,10,12,17,tzinfo=timezone.utc)

    def tearDown(self):self.temp.cleanup()

    def row(self):
        return {'forecast_id':'original','model_id':'frozen','issued_at_utc':self.now.isoformat(),
                'target_utc':(self.now+timedelta(hours=1)).isoformat(),'source_hour_start_utc':self.now.isoformat(),
                'source_quote_utc':self.now.isoformat(),'reference_price':'.1','horizon_hours':'1',
                'majority_direction':'UP','doge_reversal_direction':'DOWN','btc_momentum_direction':'UP'}

    def prepared(self):
        return {'prepared_at_utc':(self.now-timedelta(seconds=1)).isoformat(),
                'source_close_utc':self.now.isoformat(),'source_sha256':'a'*64,
                'candidates':{'1':{'p_up':.7,'training_last_label_ms':int(self.now.timestamp()*1000),
                    'calibration_n':150,'validation_n':150,'validation_selected_n':40,'validation_brier':.2,
                    'selected_return_lower_proxy':.01,'doge_return_24h':.01,'btc_return_24h':.01,
                    'frozen_features':{'reversal':'DOWN','majority':'UP','btc_momentum':'UP'}}},
                'book':None,'book_error':'unavailable'}

    def test_same_quote_target_and_full_old_prediction_are_preserved(self):
        f=self.row();r=p.make_pair(f,self.prepared(),'b'*64,self.now)
        self.assertEqual(r['production'],f)
        self.assertEqual(r['challenger']['direction'],'UP')
        self.assertEqual(r['decision']['paper_action'],'NO_TRADE')

    def test_prepared_after_issuance_cannot_inherit_earlier_clock(self):
        prep=self.prepared();prep['prepared_at_utc']=(self.now+timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError,'prepared'):p.make_pair(self.row(),prep,'b'*64,self.now)

    def test_mismatched_old_features_rejected(self):
        f=self.row();f['doge_reversal_direction']='UP'
        with self.assertRaisesRegex(ValueError,'frozen feature'):p.make_pair(f,self.prepared(),'b'*64,self.now)

    def test_decision_after_fixed_deadline_rejected(self):
        with self.assertRaisesRegex(ValueError,'decision clock'):p.make_pair(self.row(),self.prepared(),'b'*64,self.now+timedelta(seconds=31))

    def test_future_training_label_rejected(self):
        prep=self.prepared();prep['candidates']['1']['training_last_label_ms']+=3600000
        with self.assertRaisesRegex(ValueError,'training'):p.make_pair(self.row(),prep,'b'*64,self.now)

    def test_append_chain_duplicate_and_tamper(self):
        row=p.make_pair(self.row(),self.prepared(),'b'*64,self.now)
        path=self.root/'pairs.jsonl';p.append(path,[row]);self.assertEqual(len(p.read_chain(path)),1)
        with self.assertRaisesRegex(ValueError,'duplicate'):p.append(path,[row])
        path.write_text(path.read_text().replace('NO_TRADE','PAPER_LONG'))
        with self.assertRaisesRegex(ValueError,'hash'):p.read_chain(path)

    def test_deleted_committed_tail_is_rejected(self):
        row=p.make_pair(self.row(),self.prepared(),'b'*64,self.now)
        path=self.root/p.REG;p.append(path,[row]);body=path.read_bytes();path.write_text('')
        history_patch=b'diff --git a/pairs.jsonl b/pairs.jsonl\n@@ -0,0 +1 @@\n+'+body
        with patch.object(p.publication,'command',side_effect=[b'commit\n',history_patch]):
            with self.assertRaisesRegex(ValueError,'removed'):p.history_guard(self.root)

    def test_preparation_failure_does_not_suppress_production_issue(self):
        path=self.root/'data/hourly_research/ohlc.csv';path.parent.mkdir(parents=True);path.write_text('source')
        events=[]
        def fail_prepare(root):events.append('prepare');raise ValueError('unavailable fit')
        def production(*args,**kw):events.append('production');return {'status':'ok','attempts':1}
        with patch.object(p,'guard',return_value={'effective_from_utc':'2026-01-01T00:00:00+00:00'}),patch.object(p,'history_guard',return_value=[]),patch.object(p.frozen,'read_csv',return_value=([0],{})),patch.object(p,'prepare',side_effect=fail_prepare),patch.object(p.retry,'capture',side_effect=production),patch.object(p.frozen,'read_rows',side_effect=[{}, {'original':self.row()}]):
            code=p.issue(self.root)
        self.assertEqual(events,['prepare','production']);self.assertEqual(code,1)
        out=json.loads((self.root/p.BASE/'issue_latest.json').read_text())
        self.assertEqual(out['production_new'],1);self.assertEqual(out['paired_new'],0)

    def test_new_system_integrity_fault_does_not_suppress_frozen_issue(self):
        called=[]
        def production(*args,**kw):called.append('original');return {'status':'ok','attempts':1}
        with patch.object(p,'guard',side_effect=ValueError('paired pointer tampered')),patch.object(p.retry,'capture',side_effect=production),patch.object(p.frozen,'read_rows',side_effect=[{},{}]):
            self.assertEqual(p.issue(self.root),1)
        self.assertEqual(called,['original'])

    def test_real_git_history_allows_only_exact_tail_appends(self):
        p.publication.command(self.root,'init')
        p.publication.command(self.root,'config','user.name','test')
        p.publication.command(self.root,'config','user.email','test@example.invalid')
        row=p.make_pair(self.row(),self.prepared(),'b'*64,self.now);p.append(self.root/p.REG,[row])
        p.publication.command(self.root,'add','.')
        p.publication.command(self.root,'commit','-m','first')
        second=dict(row,production=dict(row['production'],forecast_id='second'));p.append(self.root/p.REG,[second])
        p.publication.command(self.root,'add','.')
        p.publication.command(self.root,'commit','-m','second')
        self.assertEqual(len(p.history_guard(self.root)),2)
        path=self.root/p.REG;path.write_text(path.read_text().replace('NO_TRADE','PAPER_LONG'))
        with self.assertRaisesRegex(ValueError,'hash'):p.history_guard(self.root)


if __name__=='__main__':unittest.main()

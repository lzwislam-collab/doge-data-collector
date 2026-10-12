import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
import feature_ablation as a
class AblationTests(unittest.TestCase):
    def test_fixed_nudge_not_claimed_calibrated_or_authorized(self):
        x=a.variant(.5,2)
        self.assertGreater(x['p_up_raw_proxy'],.5)
        self.assertFalse(x['probability_calibration_validated'])
        self.assertFalse(x['real_orders_authorized'])
    def test_missing_feature_never_becomes_zero_or_issued_forecast(self):
        x=a.variant(.55,None)
        self.assertEqual(x['status'],'WAIT_FEATURE')
        self.assertIsNone(x['direction'])
    def test_source_revision_not_visible_before_first_seen(self):
        t=datetime(2026,10,13,tzinfo=timezone.utc)
        rows=[{'source_id':'X','observation_key':'d','value':10,'usable_from_utc':t.isoformat()},
              {'source_id':'X','observation_key':'d','value':20,'usable_from_utc':(t+timedelta(days=1)).isoformat()}]
        self.assertEqual(a.asof_rows(rows,t)[('X','d')]['value'],10)
    def test_reservation_does_not_drop_missing_features(self):
        rows=[{'issued_at_utc':'2026-10-13T02:00:00+00:00','forecast_id':'a','target_utc':'2026-10-13T03:00:00+00:00','qualified':False},
              {'issued_at_utc':'2026-10-13T03:00:00+00:00','forecast_id':'b','target_utc':'2026-10-13T04:00:00+00:00','qualified':True}]
        self.assertEqual([x['forecast_id'] for x in a.paired_audit.nonoverlap(rows)],['a'])
    def bind_fixture(self,root,late=False):
        t=datetime(2026,10,13,2,tzinfo=timezone.utc)
        prep={'prepared_at_utc':t.isoformat(),'feature_cutoff_utc':t.isoformat(),'starting_ids':['old'],
            'features':{g:{'value':None if g=='DXY' else 1,'evidence':[]} for g in a.GROUPS}}
        path=root/a.BASE/'pending_prepare.json';path.parent.mkdir(parents=True);path.write_text(json.dumps(prep))
        row={'forecast_id':'new','horizon_hours':'1','issued_at_utc':(t+timedelta(seconds=5)).isoformat()}
        pair={'production':row,'record_sha256':'pair','feature_cutoff_utc':t.isoformat(),'challenger':{'p_up':.51,'direction':'UP'}}
        with patch.object(a,'guard',return_value={'effective_from_utc':t.isoformat()}),patch.object(a,'chain',return_value=[]),patch.object(a.cohort.frozen,'read_rows',return_value={'new':row,'old':dict(row,forecast_id='old')}),patch.object(a.cohort,'history_guard',return_value=[pair]),patch.object(a.cohort.publication,'command') as stage,patch.object(a,'datetime') as clock:
            clock.now.return_value=t+timedelta(seconds=40 if late else 6)
            result=a.bind(root)
        return result,stage
    def test_bind_only_new_original_preserves_missing_feature_and_stages_before_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);r,stage=self.bind_fixture(root)
            self.assertEqual(r['new_contexts'],1);stage.assert_called_once()
            row=json.loads((root/a.BASE/'contexts.jsonl').read_text())
            self.assertEqual(row['forecast_id'],'new')
            self.assertEqual(row['variants']['DXY']['status'],'WAIT_FEATURE')
    def test_late_bind_cannot_issue_feature_prediction(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'late'):self.bind_fixture(Path(temp),True)
    def test_pre_activation_bind_is_preparation_only(self):
        t=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(a,'guard',return_value={'effective_from_utc':(t+timedelta(days=1)).isoformat()}),patch.object(a,'chain',return_value=[]),patch.object(a.cohort.publication,'command') as stage:
                r=a.bind(Path(temp))
            self.assertEqual(r['status'],'WAIT_ACTIVATION');self.assertEqual(r['new_contexts'],0)
            self.assertFalse((Path(temp)/a.BASE/'contexts.jsonl').exists())
    def test_reachable_committed_ledger_deletion_is_rejected(self):
        import evidence_append_guard as g
        with patch.object(g.subprocess,'run') as run:
            run.side_effect=[type('R',(),{'stdout':b'commit\n'})(),type('R',(),{'stdout':b'@@ -1 +0 @@\n-old\n'})()]
            with self.assertRaisesRegex(ValueError,'deleted'):g.guard(Path('.'),'x.jsonl',b'')
if __name__=='__main__':unittest.main()

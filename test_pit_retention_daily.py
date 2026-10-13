import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import pit_retention_preparation as prep
import source_evidence_snapshot as snapshots

NOW=datetime(2026,10,13,4,tzinfo=timezone.utc)
class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def fixture(self):
        with patch.object(snapshots,'clock_utc',return_value=NOW):
            return snapshots.capture(self.root,storage_namespace=snapshots.PLUS_STORAGE_NAMESPACE)
    def test_latest_uses_verified_actual_chain_tip_and_rejects_stale_inventory(self):
        record=self.fixture()
        self.assertTrue(hasattr(prep,'latest_manifest'),'verified tip selection missing')
        self.assertEqual(prep.latest_manifest(self.root,NOW),self.root/record['manifest_path'])
        with self.assertRaises(ValueError):prep.latest_manifest(self.root,NOW+timedelta(minutes=16))
    def test_absent_inventory_fails_instead_of_constructing_a_fake_capture(self):
        self.assertTrue(hasattr(prep,'latest_manifest'),'verified tip selection missing')
        with self.assertRaises(ValueError):prep.latest_manifest(self.root,NOW)
    def test_daily_repeat_skips_and_next_day_or_code_change_regenerates(self):
        self.assertTrue(hasattr(prep,'prepare_daily'),'daily preparation missing')
        self.fixture()
        count=[]
        def make(root,manifest,now):
            count.append(now)
            source=root/'data/input';source.write_bytes(b'fixture')
            return prep.prepare_bundle(root,{'data/input':prep.digest(b'fixture')},now)[1]
        with patch.dict('os.environ',{'GITHUB_RUN_ID':'123'}),patch.object(prep,'prepare_existing',side_effect=make),patch.object(prep,'implementation_hash',return_value='b'*64):
            first=prep.prepare_daily(self.root,NOW)
            self.assertTrue(first['generated_new_bundle'])
            self.assertTrue(hasattr(prep,'record_artifact'),'post-upload completion marker missing')
            prep.record_artifact(self.root,'456','a'*64,'123',NOW)
            second=prep.prepare_daily(self.root,NOW+timedelta(minutes=10))
            self.assertEqual(second['status'],'SKIP_ALREADY_PREPARED_TODAY');self.assertFalse(second['generated_new_bundle'])
            self.assertEqual(len(count),1)
            # Date change requires a new fresh source inventory.
            with patch.object(snapshots,'clock_utc',return_value=NOW+timedelta(days=1)):
                snapshots.capture(self.root,storage_namespace=snapshots.PLUS_STORAGE_NAMESPACE)
            prep.prepare_daily(self.root,NOW+timedelta(days=1));self.assertEqual(len(count),2)
        with patch.object(prep,'prepare_existing',side_effect=make),patch.object(prep,'implementation_hash',return_value='c'*64):
            prep.prepare_daily(self.root,NOW+timedelta(days=1));self.assertEqual(len(count),3)
    def test_failure_replaces_run_receipt_and_does_not_report_prior_bundle_as_new(self):
        self.assertTrue(hasattr(prep,'prepare_daily'),'daily preparation missing')
        prep.write_latest(self.root,{'status':'NOT_CONFIGURED','prepared_at_utc':NOW.isoformat()})
        with self.assertRaises(ValueError):prep.prepare_daily(self.root,NOW)
        receipt=json.loads((self.root/prep.BASE/'run_latest.json').read_bytes())
        self.assertEqual(receipt['status'],'BLOCKED');self.assertFalse(receipt['generated_new_bundle'])
        self.assertFalse(receipt['independent_retention_verified'])
    def test_failed_upload_does_not_suppress_same_day_recovery(self):
        self.assertTrue(hasattr(prep,'record_artifact'),'post-upload completion marker missing')
        self.fixture();count=[]
        def make(root,manifest,now):
            count.append(now);source=root/'data/input';source.write_bytes(b'fixture')
            return prep.prepare_bundle(root,{'data/input':prep.digest(b'fixture')},now)[1]
        with patch.dict('os.environ',{'GITHUB_RUN_ID':'123'}),patch.object(prep,'prepare_existing',side_effect=make):
            prep.prepare_daily(self.root,NOW)
            # Preparation was persisted but upload failed, no completion marker.
            second=prep.prepare_daily(self.root,NOW+timedelta(minutes=1))
            self.assertTrue(second['generated_new_bundle']);self.assertEqual(len(count),2)
    def test_pending_receipt_exists_before_slow_work_and_can_be_finalized(self):
        self.assertTrue(hasattr(prep,'record_failure'),'timeout finalizer missing')
        def slow(root,now):
            receipt=json.loads((self.root/prep.BASE/'run_latest.json').read_bytes())
            self.assertEqual(receipt['status'],'PENDING');raise TimeoutError('slow')
        with patch.object(prep,'latest_manifest',side_effect=slow):
            with self.assertRaises(TimeoutError):prep.prepare_daily(self.root,NOW)
        prep.record_failure(self.root,NOW)
        receipt=json.loads((self.root/prep.BASE/'run_latest.json').read_bytes())
        self.assertEqual(receipt['status'],'BLOCKED');self.assertFalse(receipt['generated_new_bundle'])

class WorkflowTests(unittest.TestCase):
    def test_preparation_runs_after_capture_before_persist_and_no_new_cron(self):
        root=Path(__file__).parent
        text=(root/'.github/workflows/okx_hourly_extreme_history.yml').read_text(encoding='utf-8')
        self.assertIn('python pit_retention_preparation.py prepare --latest --daily',text)
        start=text.index('python pit_retention_preparation.py prepare --latest --daily')
        self.assertLess(text.index('id: supplemental_evidence'),start)
        self.assertLess(start,text.index('id: persist'))
        self.assertIn('data/pit_retention_preparation_v1/run_latest.json',text)
        self.assertIn("steps.pit_preparation.outputs.generated == 'true'",text)
        self.assertIn("steps.pit_preparation.outcome == 'failure'",text)
        self.assertGreater(text.index('record-artifact --artifact-id'),text.index('id: pit_artifact'))
        self.assertLess(text.index('record-failure'),text.index('id: persist'))
        self.assertEqual(text.count('cron:'),1)
        self.assertNotIn('git add -f data/pit_retention_preparation_v1\n',text)

if __name__=='__main__':unittest.main()

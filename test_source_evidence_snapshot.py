import importlib
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('source_evidence_snapshot')
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, stamp='2026-10-10T12:00:00+00:00', value=1):
        p = self.root / 'data/onchain/metrics_latest.json'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'collected_at_utc': stamp, 'status': 'ok', 'value': value}))

    def capture(self, cutoff=None):
        with patch.object(self.module, 'clock_utc', return_value=self.now):
            return self.module.capture(self.root, cutoff=cutoff)

    def test_namespaces_isolate_availability_and_chain(self):
        self.source()
        legacy = self.capture()
        self.now += timedelta(hours=1)
        namespace = 'data/plus_evidence_v1_0/sources'
        with patch.object(self.module, 'clock_utc', return_value=self.now):
            fresh = self.module.capture(self.root, storage_namespace=namespace)
            again = self.module.capture(self.root, storage_namespace=namespace)
        self.assertIsNone(fresh['previous_manifest_path'])
        self.assertEqual(fresh['sources']['onchain']['first_seen_utc'], self.now.isoformat())
        self.assertNotEqual(legacy['sources']['onchain']['archive_path'], fresh['sources']['onchain']['archive_path'])
        self.assertEqual(again['previous_manifest_path'], fresh['manifest_path'])
        self.assertEqual(fresh['version_id'], 'DOGE_PLUS_EVIDENCE_V1_0_20261012')
        self.assertEqual(fresh['storage_namespace'], namespace)
        (self.root/fresh['sources']['onchain']['archive_path']).write_bytes(b'forged')
        with patch.object(self.module, 'clock_utc', return_value=self.now):
            with self.assertRaisesRegex(ValueError, 'hash'):
                self.module.capture(self.root, storage_namespace=namespace)

    def test_new_namespace_archives_execution_with_legacy_context_and_missing_predecessor(self):
        namespace = 'data/plus_evidence_v1_0/sources'
        execution = self.root/'data/plus_evidence_v1_0/execution/observations.jsonl'
        execution.parent.mkdir(parents=True)
        execution.write_bytes(b'new execution')
        legacy = self.root/'data/forward_execution_evidence/observations.jsonl'
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'legacy execution')
        with patch.object(self.module, 'clock_utc', return_value=self.now):
            first = self.module.capture(self.root, storage_namespace=namespace)
            self.module.capture(self.root, storage_namespace=namespace)
            items = first['supporting_evidence']
            self.assertEqual(items[execution.relative_to(self.root).as_posix()]['evidence_role'], 'current_version_execution')
            self.assertEqual(items[legacy.relative_to(self.root).as_posix()]['evidence_role'], 'archived_context')
            (self.root/first['manifest_path']).unlink()
            with self.assertRaisesRegex(ValueError, 'predecessor'):
                self.module.capture(self.root, storage_namespace=namespace)

    def test_unsafe_namespace_rejected(self):
        for namespace in ('../escape', '/absolute', 'data/../escape', 'data\\..\\escape', 'C:/escape'):
            with self.assertRaises(ValueError):
                self.module.capture(self.root, storage_namespace=namespace)

    def test_historical_observation_never_backdates_availability(self):
        self.source()
        record = self.capture(datetime(2026, 10, 11, tzinfo=timezone.utc))
        item = record['sources']['onchain']
        self.assertEqual(item['first_seen_utc'], '2026-10-12T12:00:00+00:00')
        self.assertFalse(item['available_as_of_cutoff'])
        self.assertFalse(record['trained_weights_authorized'])

    def test_versions_preserve_bytes_and_first_seen(self):
        self.source()
        one = self.capture()
        self.now += timedelta(hours=1)
        two = self.capture()
        self.assertEqual(one['sources']['onchain']['first_seen_utc'], two['sources']['onchain']['first_seen_utc'])
        self.source(value=2)
        three = self.capture()
        self.assertNotEqual(one['sources']['onchain']['sha256'], three['sources']['onchain']['sha256'])
        self.assertEqual(three['sources']['onchain']['first_seen_utc'], self.now.isoformat())
        self.assertEqual(len(list((self.root/'data/evidence_snapshot/manifests').glob('*.json'))), 3)

    def test_missing_future_invalid_and_stale_are_visible_per_feed(self):
        self.source()
        one = self.capture()
        self.assertEqual(one['sources']['onchain']['state'], 'stale')
        self.assertEqual(one['sources']['macro']['state'], 'missing')
        self.assertEqual(one['pillar_count'], 5)
        self.source(stamp='2026-10-13T12:00:00+00:00')
        self.assertEqual(self.capture()['sources']['onchain']['state'], 'future')
        self.source(stamp='2026-10-12T12:00:00')
        self.assertEqual(self.capture()['sources']['onchain']['state'], 'invalid')

    def test_tampered_archive_refuses_new_snapshot(self):
        self.source()
        one = self.capture()
        blob = self.root / one['sources']['onchain']['archive_path']
        blob.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.capture()

    def test_deleted_predecessor_manifest_blocks_extension(self):
        self.source()
        first = self.capture()
        self.now += timedelta(hours=1)
        self.capture()
        (self.root/first['manifest_path']).unlink()
        with self.assertRaisesRegex(ValueError, 'predecessor'):
            self.capture()

    def test_quote_support_missing_is_visible_and_archive_tamper_blocks_capture(self):
        quote = self.root/'data/quote_evidence_v2/snapshots/aa/a.json'
        quote.parent.mkdir(parents=True)
        quote.write_bytes(b'original quote bytes')
        one = self.capture()
        key = 'data/quote_evidence_v2/snapshots/aa/a.json'
        item = one['supporting_evidence'][key]
        self.assertEqual((self.root/item['archive_path']).read_bytes(), b'original quote bytes')
        self.assertNotIn('source_utc', item)
        quote.unlink()
        self.assertEqual(self.capture()['supporting_evidence'][key]['state'], 'missing')
        (self.root/item['archive_path']).write_bytes(b'forged quote')
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.capture()


if __name__ == '__main__':
    unittest.main()

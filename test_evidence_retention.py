import hashlib
import importlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone, timedelta


class S3Fixture:
    def __init__(self, body=b'proof', mode='COMPLIANCE', version='v1', enabled=True):
        self.body, self.mode, self.version, self.enabled = body, mode, version, enabled

    def get_object_lock_configuration(self, **kwargs):
        return {'ObjectLockConfiguration': {'ObjectLockEnabled': 'Enabled' if self.enabled else 'Disabled'}}

    def get_object_retention(self, **kwargs):
        return {'Retention': {'Mode': self.mode, 'RetainUntilDate': datetime(2027, 1, 1, tzinfo=timezone.utc)}}

    def get_object(self, **kwargs):
        if kwargs != {'Bucket': 'bucket', 'Key': 'key', 'VersionId': 'v1'}:
            raise ValueError('wrong pinned object version')
        return {'VersionId': self.version, 'Body': io.BytesIO(self.body)}


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('evidence_retention')
        self.now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        self.spec = {'provider': 'aws_s3_object_lock', 'bucket': 'bucket', 'key': 'key', 'version_id': 'v1'}
        self.sha = hashlib.sha256(b'proof').hexdigest()

    def test_new_namespace_export_autodetection_and_ancestry(self):
        import source_evidence_snapshot as snapshot
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            namespace = 'data/plus_evidence_v1_0/sources'
            with patch.object(snapshot, 'clock_utc', return_value=self.now):
                first = snapshot.capture(root, storage_namespace=namespace)
                second = snapshot.capture(root, storage_namespace=namespace)
            output, result = self.module.export_manifest(root, root/second['manifest_path'])
            self.assertTrue(output.is_relative_to(root/namespace/'retention_exports'))
            self.assertFalse(result['independent_retention_verified'])
            self.assertEqual(result['storage_namespace'], namespace)
            self.assertIn(first['manifest_path'], {o['local_path'] for o in result['objects']})
            with self.assertRaises(ValueError):
                self.module.export_manifest(root, root/second['manifest_path'], storage_namespace='data/evidence_snapshot')
            (root/first['manifest_path']).write_bytes(b'forged')
            with self.assertRaisesRegex(ValueError, 'hash'):
                self.module.export_manifest(root, root/second['manifest_path'])

    def test_local_hash_and_flag_never_prove_worm(self):
        result = self.module.verify_receipt({'worm': True, 'sha256': self.sha}, self.sha, now=self.now)
        self.assertEqual(result['status'], 'NOT_CONFIGURED')
        self.assertFalse(result['independent_retention_verified'])

    def test_readback_and_retention_are_required(self):
        for fixture in [S3Fixture(body=b'forged'), S3Fixture(mode='GOVERNANCE'), S3Fixture(version='null'), S3Fixture(enabled=False)]:
            result = self.module.verify_receipt(self.spec, self.sha, client=fixture, now=self.now)
            self.assertNotEqual(result['status'], 'VERIFIED')
        result = self.module.verify_receipt(self.spec, self.sha, client=S3Fixture(), now=self.now)
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(result['readback_sha256'], self.sha)

    def test_expired_retention_rejected(self):
        result = self.module.verify_receipt(self.spec, self.sha, client=S3Fixture(), now=self.now+timedelta(days=200))
        self.assertNotEqual(result['status'], 'VERIFIED')

    def test_one_manifest_receipt_cannot_protect_support_or_ancestry(self):
        import source_evidence_snapshot as snapshot
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            quote = root/'data/quote_evidence_v2/bindings.csv'
            quote.parent.mkdir(parents=True)
            quote.write_bytes(b'proof')
            with patch.object(snapshot, 'clock_utc', return_value=self.now):
                first = snapshot.capture(root)
                second = snapshot.capture(root)
            manifest = root/second['manifest_path']
            config = {'objects': {second['manifest_path']: self.spec}}
            fixture = S3Fixture(body=manifest.read_bytes())
            _, exported = self.module.export_manifest(root, manifest, config, fixture)
            self.assertEqual(exported['status'], 'NOT_VERIFIED')
            self.assertFalse(exported['independent_retention_verified'])
            paths = {o['local_path'] for o in exported['objects']}
            self.assertIn(first['manifest_path'], paths)
            self.assertIn(second['supporting_evidence']['data/quote_evidence_v2/bindings.csv']['archive_path'], paths)


if __name__ == '__main__':
    unittest.main()

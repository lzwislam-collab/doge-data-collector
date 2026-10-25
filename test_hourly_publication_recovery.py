"""Fresh optional-source recovery with a real local Git remote."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import publication_recovery as publication

H = 3600000


def source(hour):
    return ('symbol,open_utc_ms\n' + ''.join(
        f'{symbol},{hour}\n' for symbol in ('DOGE-USDT', 'BTC-USDT', 'ETH-USDT'))).encode()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'writer'
        self.remote = Path(self.tmp.name) / 'remote.git'
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        self.root.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.registry = self.root / publication.REG
        self.registry.parent.mkdir(parents=True)
        self.registry.write_bytes(b'forecast_id\noriginal\n')
        self.data = self.root / publication.SOURCE
        self.data.parent.mkdir(parents=True)
        self.data.write_bytes(source(3 * H))
        self.git('add', '.')
        self.git('commit', '-m', 'fixture original publication')
        self.git('push', 'origin', 'main')
        self.output = self.root / 'output'

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True).stdout

    def publisher(self, root):
        if self.git('diff', '--cached', '--name-only').strip():
            self.git('commit', '-m', 'fixture genuine refreshed source')
            self.git('push', 'origin', 'main')
        self.git('fetch', 'origin', 'main')
        return {'status': 'verified_remote_readback', 'remote_head': self.git('rev-parse', 'FETCH_HEAD').decode().strip()}

    def run_recovery(self, refresh=None, publisher=None, now=5 * H + 1000):
        module = importlib.import_module('hourly_publication_recovery')
        def fresh(root):
            self.data.write_bytes(source(4 * H))
        with patch.dict(os.environ, {'GITHUB_OUTPUT': str(self.output)}), \
             patch.object(publication.time, 'time', return_value=now / 1000):
            return module.recover(self.root, refresh or fresh, publisher or self.publisher)

    def test_boundary_rejection_is_recovered_only_with_fresh_exact_remote_bytes(self):
        from prospective_witness_budget import assert_source_fresh
        with self.assertRaisesRegex(ValueError, 'reserve'):
            assert_source_fresh(self.data.read_bytes(), 5 * H - 65000)
        result = self.run_recovery()
        self.assertTrue(result['recovered'])
        self.assertEqual(self.git('show', 'FETCH_HEAD:' + publication.SOURCE), source(4 * H))
        self.assertEqual(self.registry.read_bytes(), b'forecast_id\noriginal\n')
        self.assertFalse(result['publication']['new_predictions'])
        self.assertEqual(self.output.read_text(), 'recovered=false\nrecovered=true\n')
        receipt = json.loads((self.root / result['receipt_path']).read_text())
        self.assertTrue(receipt['recovered'])

    def test_unpublished_original_blocks_refresh_and_publication(self):
        self.registry.write_bytes(b'forecast_id\noriginal\nunpublished\n')
        def forbidden(root):
            self.fail('must reject before refresh or publication')
        with self.assertRaisesRegex(ValueError, 'unpublished'):
            self.run_recovery(refresh=forbidden, publisher=forbidden)
        self.assertEqual(self.output.read_text(), 'recovered=false\n')

    def test_same_boundary_guard_remains_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'source recovery not verified'):
            self.run_recovery(now=6 * H - 1000)
        self.assertEqual(self.output.read_text(), 'recovered=false\n')

    def test_stale_refresh_cannot_claim_recovery(self):
        with self.assertRaisesRegex(ValueError, 'source recovery not verified'):
            self.run_recovery(refresh=lambda root: None)
        self.assertNotIn('recovered=true', self.output.read_text())

    def test_refresh_error_never_calls_publisher(self):
        def broken(root):
            raise TimeoutError('real refresh unavailable')
        def forbidden(root):
            self.fail('no publication after refresh failure')
        with self.assertRaises(TimeoutError):
            self.run_recovery(refresh=broken, publisher=forbidden)
        self.assertEqual(self.output.read_text(), 'recovered=false\n')

    def test_remote_mismatch_keeps_recovery_false(self):
        def wrong(root):
            self.git('fetch', 'origin', 'main')
            return {'status': 'verified_remote_readback'}
        with self.assertRaisesRegex(ValueError, 'source recovery not verified'):
            self.run_recovery(publisher=wrong)
        self.assertEqual(self.output.read_text(), 'recovered=false\n')

    def test_core_publisher_failure_propagates(self):
        def broken(root):
            raise ValueError('publisher failed')
        with self.assertRaisesRegex(ValueError, 'publisher failed'):
            self.run_recovery(publisher=broken)
        self.assertEqual(self.output.read_text(), 'recovered=false\n')

    def test_receipt_failure_does_not_emit_recovery_success(self):
        module = importlib.import_module('hourly_publication_recovery')
        with patch.object(module, 'write_receipt', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.run_recovery()
        self.assertEqual(self.output.read_text(), 'recovered=false\n')

    def test_failed_attempt_has_durable_receipt_and_restores_output_environment(self):
        with self.assertRaisesRegex(ValueError, 'source recovery not verified'):
            self.run_recovery(refresh=lambda root: None)
        receipts = list((self.root / 'data/hourly_publication_recovery_v1').glob('*.json'))
        self.assertEqual(len(receipts), 1)
        result = json.loads(receipts[0].read_text())
        self.assertFalse(result['recovered'])
        self.assertIn('source recovery not verified', result['error'])


if __name__ == '__main__':
    unittest.main()

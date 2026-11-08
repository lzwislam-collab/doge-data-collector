"""Keep every real publication boundary while replacing only validator I/O.

Temporary local Git remotes exercise commits, rebase retries and exact readback.
The frozen data fixtures stay isolated at named validator boundaries.
"""
import json
import subprocess
import unittest
from unittest.mock import patch

import test_runtime_publication_boundary as boundary

runtime = boundary.runtime
publisher = boundary.publisher
PAIR_PATH = boundary.PAIR_PATH


class RuntimePublicationValidatorBoundaryTests(unittest.TestCase):
    git = boundary.PublicationBoundary.git
    fixture_validate = boundary.PublicationBoundary.fixture_validate
    result = boundary.PublicationBoundary.result
    record = boundary.PublicationBoundary.record

    def setUp(self):
        boundary.PublicationBoundary.setUp(self)
        self.calls = {'state': [], 'paired': []}
        self.failure = None
        for kind, name in [('state', 'validate_publication_state'),
                           ('paired', 'validate_paired_history')]:
            def validate(root, kind=kind):
                self.fixture_validate(root)
                self.calls[kind].append(self.git('rev-parse', 'HEAD').stdout.strip())
                if self.failure == (kind, len(self.calls[kind])):
                    raise ValueError('fixture rejects complete ' + kind + ' validation')
            item = patch.object(runtime, name, validate, create=True)
            item.start()
            self.addCleanup(item.stop)

    def test_new_validators_check_preflight_data_and_receipt_without_dirty_remote(self):
        result = runtime.finish(self.root, self.adapter, self.result())
        self.assertFalse(result['fatal'])
        self.assertEqual(len(self.calls['state']), 3)
        self.assertEqual(len(self.calls['paired']), 3)
        self.assertEqual(self.calls['state'], self.calls['paired'])
        self.assertEqual(self.calls['state'][0], self.initial)
        self.assertEqual(self.calls['state'][-1], self.adapter.public_head)
        self.assertEqual(self.git('status', '--porcelain', '--untracked-files=no').stdout, '')
        remote = self.git('--git-dir', str(self.remote), 'show', 'refs/heads/main:' + PAIR_PATH).stdout
        self.assertEqual(json.loads(remote), json.loads((self.root / PAIR_PATH).read_text()))

    def test_preflight_history_rejection_prevents_any_publication(self):
        self.failure = ('paired', 1)
        result = runtime.finish(self.root, self.adapter, self.result())
        self.assertTrue(result['fatal'])
        self.assertEqual(self.git('--git-dir', str(self.remote), 'rev-parse', 'refs/heads/main').stdout.strip(), self.initial)
        self.assertNotIn('fatal=false', self.output.read_text())

    def test_post_rebase_state_rejection_prevents_data_push(self):
        self.failure = ('state', 2)
        result = runtime.finish(self.root, self.adapter, self.result())
        self.assertTrue(result['fatal'])
        self.assertEqual(len(self.calls['state']), 2)
        self.assertEqual(len(self.calls['paired']), 1)
        self.assertEqual(self.git('--git-dir', str(self.remote), 'rev-parse', 'refs/heads/main').stdout.strip(), self.initial)
        self.assertNotIn('fatal=false', self.output.read_text())

    def test_receipt_rejection_preserves_data_but_never_reports_safe_completion(self):
        self.failure = ('paired', 3)
        result = runtime.finish(self.root, self.adapter, self.result())
        self.assertTrue(result['fatal'])
        self.assertEqual(len(self.calls['paired']), 3)
        remote = self.git('--git-dir', str(self.remote), 'rev-parse', 'refs/heads/main').stdout.strip()
        self.assertNotEqual(remote, self.initial)
        self.assertNotEqual(remote, self.git('rev-parse', 'HEAD').stdout.strip())
        self.assertNotIn('fatal=false', self.output.read_text())

    def test_retry_executes_complete_fresh_validators_again(self):
        original = publisher.command
        refused = []
        def command(root, *args):
            if args[:1] == ('push',) and not refused:
                refused.append(True)
                raise subprocess.CalledProcessError(1, ['git', *args])
            return original(root, *args)
        with patch.object(publisher, 'command', command), patch.object(publisher.time, 'sleep', lambda seconds: None):
            result = runtime.finish(self.root, self.adapter, self.result())
        self.assertFalse(result['fatal'])
        self.assertEqual(len(self.calls['state']), 4)
        self.assertEqual(len(self.calls['paired']), 4)
        self.assertEqual(self.calls['state'], self.calls['paired'])
        self.assertTrue(self.output.read_text().endswith('fatal=false\n'))


if __name__ == '__main__':
    unittest.main()

"""Actual LiveAdapter/publisher Git boundary with synthetic local validation fixtures.

Frozen data validators are fixture boundaries, not production acceptance. No live
constructor, witness, forecast, OSS, network or production Git remote is invoked.
"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SOURCE = Path(os.environ.get("RUNTIME_SOURCE", HERE/"runtime_deadline_service.py")).resolve()
spec = importlib.util.spec_from_file_location('runtime_boundary_under_test', SOURCE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
import publish_forward_evidence as publisher
import paired_forward_cohort as pair

RUNTIME_PATH = 'data/runtime_deadline_v1/latest.json'
PAIR_PATH = 'data/paired_forward_v1/publication_latest.json'
CASES = []


class PublicationBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='doge-pr83-local-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base/'runner'
        self.remote = self.base/'remote.git'
        self.output = self.base/'runtime.output'
        self.root.mkdir()
        self.env = patch.dict(os.environ, {
            'GITHUB_OUTPUT': str(self.output), 'GIT_TERMINAL_PROMPT': '0',
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_COUNT': '4',
            'GIT_CONFIG_KEY_0': 'protocol.allow', 'GIT_CONFIG_VALUE_0': 'never',
            'GIT_CONFIG_KEY_1': 'protocol.file.allow', 'GIT_CONFIG_VALUE_1': 'always',
            'GIT_CONFIG_KEY_2': 'protocol.ext.allow', 'GIT_CONFIG_VALUE_2': 'never',
            'GIT_CONFIG_KEY_3': 'core.autocrlf', 'GIT_CONFIG_VALUE_3': 'false'})
        self.env.start()
        self.addCleanup(self.env.stop)
        fixtures = {
            RUNTIME_PATH: '{"fatal": false, "status": "CAPTURE_LOOP_COMPLETED"}\n',
            PAIR_PATH: '{"status": "fixture_old_receipt"}\n',
            'data/direction_v42/forward_registry.csv': 'forecast_id\n',
            'data/direction_v42/forward_scores.csv': 'forecast_id\n',
            'data/direction_v42/forward_latest.json': '{}\n',
            'data/quote_evidence_v2/bindings.csv': 'forecast_id\n'}
        for name, text in fixtures.items():
            target = self.root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf-8')
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'offline fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.hooksPath', 'disabled-hooks')
        self.git('add', '--', 'data')
        self.git('commit', '-qm', 'synthetic baseline; no forecasts')
        self.git('init', '--bare', '-q', str(self.remote))
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-u', 'origin', 'main')
        self.initial = self.git('rev-parse', 'HEAD').stdout.strip()
        self.adapter = runtime.LiveAdapter.__new__(runtime.LiveAdapter)
        self.adapter.root = self.root
        self.adapter.core_witness_pending = False
        self.adapter.last_witness_head = None
        # These named production validation dependencies require real frozen data.
        # Keep the actual action/staging/publish/verify/readback implementation.
        self.patches = [patch.object(runtime, 'validate_p2', self.fixture_validate),
                        patch.object(publisher, 'validate_state', self.fixture_validate),
                        patch.object(pair, 'guard', self.fixture_validate),
                        patch.object(pair, 'history_guard', self.fixture_validate)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.record)

    def fixture_validate(self, root):
        if Path(root).resolve() != self.root.resolve():
            raise AssertionError('fixture validation cannot run outside its temporary root')
        if (self.root/'data/direction_v42/forward_registry.csv').read_text() != 'forecast_id\n':
            raise ValueError('fixture does not permit issuing forecasts')

    def git(self, *args, check=True, cwd=None):
        return subprocess.run(['git', '-C', str(cwd or self.root), *args],
                              capture_output=True, text=True, check=check)

    def result(self):
        return {'fatal': False, 'status': 'CAPTURE_LOOP_COMPLETED',
                'archive_window_budget': {'duration_seconds': 2400,
                                         'archive_reserve_seconds': 1800}}

    def record(self):
        CASES.append({'test': self.id(), 'initial_head': self.initial,
                      'local_head': self.git('rev-parse', 'HEAD').stdout.strip(),
                      'remote_head': self.git('--git-dir', str(self.remote), 'rev-parse',
                                              'refs/heads/main').stdout.strip(),
                      'tracked_status': self.git('status', '--porcelain',
                                                 '--untracked-files=no').stdout,
                      'output': self.output.read_text() if self.output.exists() else None})

    def test_live_publication_leaves_no_tracked_dirty_receipt_and_exact_remote_bytes(self):
        result = runtime.finish(self.root, self.adapter, self.result())
        self.assertFalse(result['fatal'])
        self.assertEqual(self.git('status', '--porcelain', '--untracked-files=no').stdout, '')
        for path in (RUNTIME_PATH, PAIR_PATH):
            readback = subprocess.run(['git', '--git-dir', str(self.remote), 'show',
                                      'refs/heads/main:'+path], capture_output=True, check=True).stdout
            self.assertEqual(readback, (self.root/path).read_bytes())
        self.assertEqual(json.loads((self.root/RUNTIME_PATH).read_text()), self.result())
        receipt = json.loads((self.root/PAIR_PATH).read_text())
        self.assertEqual(receipt['status'], 'verified_remote_readback')
        self.assertEqual(self.git('merge-base', '--is-ancestor', receipt['remote_head'],
                                  self.adapter.public_head, check=False).returncode, 0)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), self.adapter.public_head)
        self.assertEqual(self.git('pull', '--rebase', 'origin', 'main', check=False).returncode, 0)

    def test_unchanged_receipt_publisher_supports_divergent_local_and_remote_rebase(self):
        runtime.finish(self.root, self.adapter, self.result())
        peer = self.base/'peer'
        self.git('clone', '-q', '--branch', 'main', str(self.remote), str(peer))
        self.git('config', 'user.name', 'offline peer', cwd=peer)
        self.git('config', 'user.email', 'peer@example.invalid', cwd=peer)
        (peer/'remote-note.txt').write_text('independent remote fixture\n')
        self.git('add', '--', 'remote-note.txt', cwd=peer)
        self.git('commit', '-qm', 'remote fixture note', cwd=peer)
        self.git('push', 'origin', 'main', cwd=peer)
        (self.root/'local-note.txt').write_text('independent local fixture\n')
        self.git('add', '--', 'local-note.txt')
        self.git('commit', '-qm', 'local fixture note')
        before = self.git('rev-parse', 'HEAD').stdout.strip()
        pull = self.git('pull', '--rebase', 'origin', 'main', check=False)
        self.assertEqual(pull.returncode, 0, pull.stderr)
        self.assertNotEqual(self.git('rev-parse', 'HEAD').stdout.strip(), before)
        self.assertEqual(self.git('status', '--porcelain', '--untracked-files=no').stdout, '')
        self.assertTrue((self.root/'local-note.txt').exists())
        self.assertTrue((self.root/'remote-note.txt').exists())

    def test_publication_failure_never_emits_safe_output(self):
        def reject(*args, **kwargs):
            raise OSError('offline publication refusal')
        with patch.object(publisher, 'publish', reject):
            result = runtime.finish(self.root, self.adapter, self.result())
        self.assertTrue(result['fatal'])
        self.assertNotIn('fatal=false', self.output.read_text())
        self.assertEqual(self.git('--git-dir', str(self.remote), 'rev-parse',
                                  'refs/heads/main').stdout.strip(), self.initial)

    def test_receipt_readback_failure_never_emits_safe_output(self):
        verify = publisher.verify_readback
        def reject_receipt(root, head, expected):
            if PAIR_PATH in expected and json.loads(expected[PAIR_PATH]).get('status') == 'verified_remote_readback':
                return False
            return verify(root, head, expected)
        with patch.object(publisher, 'verify_readback', reject_receipt):
            result = runtime.finish(self.root, self.adapter, self.result())
        self.assertTrue(result['fatal'])
        self.assertNotIn('fatal=false', self.output.read_text())

    def test_original_validation_rejection_does_not_publish_or_signal_safe(self):
        def reject(root):
            raise ValueError('fixture validation refusal')
        with patch.object(pair, 'guard', reject):
            result = runtime.finish(self.root, self.adapter, self.result())
        self.assertTrue(result['fatal'])
        self.assertEqual(self.git('--git-dir', str(self.remote), 'rev-parse',
                                  'refs/heads/main').stdout.strip(), self.initial)
        self.assertNotIn('fatal=false', self.output.read_text())


if __name__ == '__main__':
    def no_network(*args, **kwargs):
        raise AssertionError('network forbidden in offline PR83 fixtures')
    with patch.object(socket.socket, 'connect', no_network), \
         patch.object(socket.socket, 'connect_ex', no_network), \
         patch.object(socket, 'getaddrinfo', no_network), \
         patch.object(publisher.time, 'sleep', lambda seconds: None):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(PublicationBoundary)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if os.environ.get('CASE_LOG'):
            Path(os.environ['CASE_LOG']).write_text(json.dumps(CASES, indent=2)+'\n', encoding='utf-8')
        raise SystemExit(not result.wasSuccessful())

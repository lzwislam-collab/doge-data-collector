import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

import audited_forward_refresh as refresh


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime.now(timezone.utc)
        for p in (refresh.REG, refresh.SCORES):
            (self.root / p).parent.mkdir(parents=True, exist_ok=True)
            (self.root / p).write_bytes(b'original\n')
        self.calls = 0

    def runner(self, errors=(), mismatch=False, future=False):
        def run(*args, **kwargs):
            error = errors[self.calls] if self.calls < len(errors) else None
            self.calls += 1
            stamp = self.now + timedelta(minutes=1) if future else datetime.now(timezone.utc)
            v1 = dict(schema_version=1, status='ok', audited_at_utc=stamp.isoformat(),
                      full_reachable_history_checked=True,
                      input_commit_sha='a' * 40, protocol_id='v1',
                      protocol_sha256='b' * 64,
                      registry_sha256=hashlib.sha256(b'original\n').hexdigest(),
                      scores_sha256=hashlib.sha256(b'original\n').hexdigest())
            if mismatch: v1['scores_sha256'] = '0' * 64
            if error: v1 = dict(status='blocked', audited_at_utc=stamp.isoformat(), error=error)
            v2 = dict(schema_version=2, status='ok', audited_at_utc=stamp.isoformat(),
                      locked_implementation_checked=True, protocol_id='v2',
                      official_score_source='V2 only; V1 and frozen collector metrics are legacy exploratory diagnostics')
            for path, value in ((refresh.V1, v1), (refresh.V2, v2)):
                (self.root / path).parent.mkdir(parents=True, exist_ok=True)
                (self.root / path).write_text(json.dumps(value))
            return subprocess.CompletedProcess(args[0], 0, 'ok', '')
        return run

    def test_legacy_transient_retry_preserves_both_attempts(self):
        out = refresh.refresh(self.root, runner=self.runner(['HTTP Error 502: Bad Gateway']), sleeper=lambda _: None)
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(self.calls, 2)
        archives = list((self.root / refresh.ARCHIVE).glob('*.json'))
        self.assertEqual(len(archives), 2)
        self.assertEqual(json.loads(archives[0].read_text())['v1']['status'], 'blocked')

    def test_nontransient_never_retried(self):
        with self.assertRaises(ValueError):
            refresh.refresh(self.root, runner=self.runner(['frozen model modified']), sleeper=lambda _: None)
        self.assertEqual(self.calls, 1)

    def test_hash_mismatch_never_retried(self):
        with self.assertRaises(ValueError):
            refresh.refresh(self.root, runner=self.runner(mismatch=True), sleeper=lambda _: None)
        self.assertEqual(self.calls, 1)

    def test_future_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            refresh.refresh(self.root, runner=self.runner(future=True), sleeper=lambda _: None)

    def test_transient_attempts_are_bounded(self):
        waits = []
        with self.assertRaises(ValueError):
            refresh.refresh(self.root, runner=self.runner(['HTTP Error 429: busy'] * 3), sleeper=waits.append)
        self.assertEqual(self.calls, 3)
        self.assertEqual(waits, [2, 4])
        self.assertEqual(len(list((self.root / refresh.ARCHIVE).glob('*.json'))), 3)

    def test_v2_integrity_failure_overrides_legacy_transport_error(self):
        original = self.runner(['HTTP Error 502: gateway'])
        def run(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.root / refresh.V2).write_text(json.dumps(dict(status='blocked',
                audited_at_utc=datetime.now(timezone.utc).isoformat(), error='frozen V2 implementation modified')))
            return result
        with self.assertRaises(ValueError):
            refresh.refresh(self.root, runner=run, sleeper=lambda _: None)
        self.assertEqual(self.calls, 1)

    def test_success_does_not_claim_external_immutability(self):
        out = refresh.refresh(self.root, runner=self.runner(), sleeper=lambda _: None)
        self.assertFalse(out['external_immutable_storage_configured'])
        self.assertEqual(out['official_score_source'], 'V2 only')

    def test_timeout_retries_bounded_and_never_accepts_stale_success(self):
        successful = self.runner()
        def run(*args, **kwargs):
            self.assertEqual(kwargs['timeout'], 60)
            successful(*args, **kwargs)
            raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
        waits = []
        with self.assertRaises(ValueError):
            refresh.refresh(self.root, runner=run, sleeper=waits.append)
        self.assertEqual(self.calls, 3)
        self.assertEqual(waits, [2, 4])
        receipts = [json.loads(p.read_text()) for p in (self.root / refresh.ARCHIVE).glob('*.json')]
        self.assertEqual(len(receipts), 3)
        self.assertTrue(all(r['status'] == 'blocked' for r in receipts))


if __name__ == '__main__': unittest.main()

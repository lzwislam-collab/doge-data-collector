import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
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
        raw_outputs = []
        original_runner = self.runner(['HTTP Error 502: Bad Gateway'])

        def run(*args, **kwargs):
            result = original_runner(*args, **kwargs)
            raw_outputs.append(tuple((self.root / path).read_text(encoding='utf-8')
                                     for path in (refresh.V1, refresh.V2)))
            return result

        out = refresh.refresh(self.root, runner=run, sleeper=lambda _: None)
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(self.calls, 2)
        archives = list((self.root / refresh.ARCHIVE).glob('*.json'))
        self.assertEqual(len(archives), 2)
        receipts = sorted((json.loads(path.read_text(encoding='utf-8')) for path in archives),
                          key=lambda receipt: receipt['attempt'])
        self.assertEqual([receipt['attempt'] for receipt in receipts], [1, 2])
        self.assertEqual([receipt['status'] for receipt in receipts], ['blocked', 'ok'])
        self.assertEqual([receipt['v1']['status'] for receipt in receipts], ['blocked', 'ok'])
        self.assertEqual(receipts[0]['v1']['error'], 'HTTP Error 502: Bad Gateway')
        for receipt, (raw_v1, raw_v2) in zip(receipts, raw_outputs):
            self.assertEqual(receipt['original_v1_json'], raw_v1)
            self.assertEqual(receipt['original_v2_json'], raw_v2)
            self.assertEqual(json.loads(receipt['original_v1_json']), receipt['v1'])
            self.assertEqual(json.loads(receipt['original_v2_json']), receipt['v2'])
            self.assertEqual(receipt['v2']['status'], 'ok')

    def test_nontransient_never_retried(self):
        with self.assertRaises(ValueError):
            refresh.refresh(self.root, runner=self.runner(['frozen model modified']), sleeper=lambda _: None)
        self.assertEqual(self.calls, 1)

    def test_legacy_transient_retry_evidence_with_reverse_glob_order(self):
        original_glob = Path.glob

        def reverse_glob(path, pattern):
            return iter(sorted(original_glob(path, pattern), reverse=True))

        # Filesystem enumeration order is unspecified, including on Linux CI.
        with patch.object(Path, 'glob', reverse_glob):
            self.test_legacy_transient_retry_preserves_both_attempts()

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

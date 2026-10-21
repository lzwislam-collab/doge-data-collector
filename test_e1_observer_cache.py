import copy
import hashlib
import io
import json
import unittest
import zipfile
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

import e1_observer_cache as cache


NOW = datetime(2026, 10, 21, 4, tzinfo=timezone.utc)
BEFORE = NOW - timedelta(hours=1)
AFTER = BEFORE - timedelta(hours=1)
FP = {'registration_identity': 'a' * 64, 'sha256_lf': {'observer.py': 'b' * 64}}
PROOF = {'run_id': 9, 'head_sha': 'c' * 40,
         'job_started_at_utc': (BEFORE - timedelta(minutes=5)).isoformat(),
         'job_completed_at_utc': (BEFORE - timedelta(minutes=4)).isoformat()}


def archive(payload, filename='witness-cache.json'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as stream:
        if payload is not None:
            stream.writestr(filename, json.dumps(payload))
        else:
            stream.writestr('report.json', '{}')
    return out.getvalue()


class PositiveCacheTests(unittest.TestCase):
    def test_exact_closed_positive_reuses_but_changed_inputs_or_new_stage_do_not(self):
        c = cache.PositiveCache(FP)
        c.remember({'event': b'actual'}, {'code': b'frozen'}, BEFORE, AFTER, PROOF, NOW)
        restored = cache.PositiveCache.from_payload(c.payload(), FP, NOW)
        self.assertEqual(restored.get({'event': b'actual'}, {'code': b'frozen'}, BEFORE, AFTER, NOW), PROOF)
        for required, files, before, after in [({'event': b'changed'}, {'code': b'frozen'}, BEFORE, AFTER),
                ({'event': b'actual'}, {'code': b'changed'}, BEFORE, AFTER),
                ({'event': b'actual'}, {'code': b'frozen'}, BEFORE + timedelta(seconds=1), AFTER),
                ({'event': b'actual'}, {'code': b'frozen'}, NOW + timedelta(seconds=1), AFTER)]:
            self.assertIsNone(restored.get(required, files, before, after, NOW))

    def test_no_negative_or_unclosed_window_is_persisted(self):
        c = cache.PositiveCache(FP)
        c.remember({'event': b'x'}, {}, BEFORE, AFTER, None, NOW)
        c.remember({'event': b'x'}, {}, NOW + timedelta(hours=1), AFTER, PROOF, NOW)
        self.assertEqual(c.payload()['proofs'], {})

    def test_malformed_proof_fails_closed_and_old_fingerprint_is_cold(self):
        c = cache.PositiveCache(FP)
        c.remember({'event': b'x'}, {}, BEFORE, AFTER, PROOF, NOW)
        payload = c.payload(); payload['proofs'][next(iter(payload['proofs']))]['run_id'] = True
        with self.assertRaises(cache.CacheError):
            cache.PositiveCache.from_payload(payload, FP, NOW)
        old = copy.deepcopy(FP); old['sha256_lf']['observer.py'] = 'd' * 64
        self.assertIsNone(cache.PositiveCache.from_payload(c.payload(), old, NOW))

    def test_proof_format_cannot_carry_future_clock_or_extra_fields(self):
        for change in [{'job_completed_at_utc': (NOW + timedelta(seconds=1)).isoformat()},
                       {'head_sha': 'not-a-sha'}, {'extra': 'untrusted'}]:
            proof = {**PROOF, **change}
            with self.assertRaises(cache.CacheError):
                cache.validate_proof(proof, NOW)

    def test_real_code_fingerprint_normalizes_lf_and_changes_with_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in cache.CODE:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'first\r\nsecond\r\n')
            original = cache.fingerprint(root, 'a' * 64)
            for name in cache.CODE:
                (root / name).write_bytes(b'first\nsecond\n')
            self.assertEqual(cache.fingerprint(root, 'a' * 64), original)
            (root / cache.CODE[0]).write_bytes(b'new observer code\n')
            self.assertNotEqual(cache.fingerprint(root, 'a' * 64), original)


class LoaderTests(unittest.TestCase):
    def fixture(self, payload=None, event='workflow_run', conclusion='success', job='success', bad_digest=False,
                repository='owner/repo'):
        body = archive(payload)
        run = {'id': 12, 'repository': {'full_name': repository}, 'path': cache.WORKFLOW,
               'head_branch': 'main', 'event': event, 'status': 'completed',
               'conclusion': conclusion, 'pull_requests': [], 'run_attempt': 1,
               'head_sha': 'e' * 40}
        calls = []; downloads = []
        def api(path):
            calls.append(path)
            if '/workflows/' in path:
                return {'workflow_runs': [run]}
            if '/jobs' in path:
                return {'jobs': [{'name': 'observe', 'status': 'completed', 'conclusion': job}], 'total_count': 1}
            return {'artifacts': [{'id': 99, 'name': 'e1-observer-12-1', 'expired': False,
                                  'digest': 'sha256:' + ('0' * 64 if bad_digest else hashlib.sha256(body).hexdigest())}]}
        engine = SimpleNamespace(api=api, parent=SimpleNamespace(old=SimpleNamespace(REPO='owner/repo')))
        def download(url):
            downloads.append(url); return body
        return engine, download, calls, downloads

    def test_trusted_successful_artifact_loads_and_probe_without_file_is_cold(self):
        payload = cache.PositiveCache(FP).payload()
        engine, download, calls, downloads = self.fixture(payload)
        self.assertEqual(cache.load_previous(engine, FP, NOW, download=download).payload(), payload)
        self.assertEqual(len(downloads), 1)
        engine, download, calls, downloads = self.fixture(None)
        self.assertEqual(cache.load_previous(engine, FP, NOW, download=download).payload()['proofs'], {})

    def test_foreign_pr_or_failed_observe_are_not_downloaded(self):
        for changes in [{'event': 'pull_request'}, {'conclusion': 'failure'}, {'job': 'failure'},
                        {'repository': 'foreign/repo'}]:
            engine, download, calls, downloads = self.fixture(cache.PositiveCache(FP).payload(), **changes)
            cache.load_previous(engine, FP, NOW, download=download)
            self.assertEqual(downloads, [])

    def test_bad_digest_and_malformed_payload_are_observation_failures(self):
        for payload, bad in [(cache.PositiveCache(FP).payload(), True), ({'malformed': True}, False)]:
            engine, download, calls, downloads = self.fixture(payload, bad_digest=bad)
            with self.assertRaises(cache.CacheError):
                cache.load_previous(engine, FP, NOW, download=download)

    def test_no_prior_run_is_cold_without_download(self):
        engine = SimpleNamespace(api=lambda path: {'workflow_runs': []},
                                 parent=SimpleNamespace(old=SimpleNamespace(REPO='owner/repo')))
        self.assertEqual(cache.load_previous(engine, FP, NOW).payload()['proofs'], {})

    def test_duplicate_zip_cache_members_are_rejected(self):
        payload = cache.PositiveCache(FP).payload()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            z.writestr('witness-cache.json', json.dumps(payload))
            z.writestr('sub/witness-cache.json', json.dumps(payload))
        with self.assertRaises(cache.CacheError):
            cache.unpack(stream.getvalue(), FP, NOW)


if __name__ == '__main__':
    unittest.main()

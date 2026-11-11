"""New engineering transport preserves original qualification and actual identity."""
from contextlib import nullcontext
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
from urllib.error import HTTPError

import phase2_forward_runtime_v2 as r
import phase2_forward_v2 as f
import forward_readiness as readiness
import test_r2_witness_presence as fixtures

HERE = Path(__file__).resolve().parent
VERSION = 'DOGE_R2_ENGINEERING_TRANSPORT_V1'
MANIFEST = 'config/r2_engineering_transport_v1.json'
AUDIT = '.github/workflows/r2_engineering_audit.yml'
LIVE = '.github/workflows/r2_engineering_live.yml'
TRANSPORT = {
    'r2_engineering_runtime.py', 'test_r2_engineering_runtime.py',
    'r2_witness_presence.py', 'test_r2_witness_presence.py',
    'r2_engineering_readiness.py', 'r2_engineering_dispatch.py',
    'test_r2_engineering_dispatch.py', 'forward_readiness.py',
    'engineering_run_receipt.py', 'learned_audit_handoff.py',
    'test_learned_audit_handoff.py', 'r2_engineering_routing.py',
    'test_r2_engineering_routing.py', 'runtime_forward_bridge.py', AUDIT, LIVE,
}


def digest(raw):
    return hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()


class EngineeringRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.absent, cls.old_present = fixtures.retained_fixture('r2-engineering')
        for name in r.FIXED | {f.REGISTRATION}:
            target = cls.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((r.ROOT / name).read_bytes().replace(b'\r\n', b'\n'))
        hashes = {}
        for name in TRANSPORT:
            target = cls.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(('retained transport fixture: ' + name + '\n').encode())
            hashes[name] = digest(target.read_bytes())
        cls.manifest = {'schema': VERSION, 'engineering_version': VERSION,
                        'audit_workflow': AUDIT, 'live_workflow': LIVE,
                        'original_registration_path': f.REGISTRATION,
                        'original_registration_sha256_lf': digest((cls.root / f.REGISTRATION).read_bytes()),
                        'engineering_files_sha256_lf': hashes,
                        'paper_only': True}
        cls.raw = (json.dumps(cls.manifest, indent=2, sort_keys=True) + '\n').encode()
        (cls.root / MANIFEST).write_bytes(cls.raw)
        fixtures.git(cls.root, 'add', '-A')
        fixtures.git(cls.root, 'commit', '-qm', 'retained compatible engineering source')
        cls.head = fixtures.git(cls.root, 'rev-parse', 'HEAD').decode().strip()
        path = HERE / 'r2_engineering_runtime.py'
        if path.exists():
            spec = importlib.util.spec_from_file_location('r2_engineering_subject', path)
            cls.subject = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.subject)
        else:
            cls.subject = types.SimpleNamespace(witness=f.witness, context=nullcontext,
                run=r.run, main=r.main, verify_transport=lambda: (cls.manifest, digest(cls.raw)))

    def setUp(self):
        for patch in (mock.patch.object(self.subject, 'ROOT', self.root, create=True),
                      mock.patch.object(r, 'ROOT', self.root),
                      mock.patch.object(readiness, 'ROOT', self.root),
                      mock.patch.object(r, 'urlopen', side_effect=self.http404)):
            patch.start()
            self.addCleanup(patch.stop)
        self.original_witness, self.original_workflow = f.witness, f.WORKFLOW
        self.original_reader, self.original_runs = r.read_at, r.runs_server
        for patch in (mock.patch.object(self.subject, '_identity_cache', OrderedDict(), create=True),
                      mock.patch.object(self.subject, '_identity_cache_bytes', 0, create=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def http404(self, request, **kwargs):
        raise HTTPError(request.full_url, 404, 'Not Found', {}, None)

    def row(self, head=None, number=20, workflow=AUDIT):
        row = fixtures.runrow(head or self.head, number)
        row.update(path=workflow, run_attempt=2, head_repository={'full_name': f.old.REPO})
        return row

    def witness(self, rows, before=fixtures.BEFORE, after=fixtures.AFTER):
        return self.subject.witness({fixtures.SCORE: fixtures.EXPECTED}, rows,
                                    r.read_at, before, after)

    def upgraded_context(self):
        """A real local transport upgrade; retained candidate Git bytes stay old."""
        from contextlib import contextmanager
        @contextmanager
        def context():
            target=self.root/'r2_engineering_readiness.py'
            original=target.read_bytes()
            upgraded=dict(self.manifest,engineering_files_sha256_lf=dict(self.manifest['engineering_files_sha256_lf']))
            try:
                target.write_bytes(original+b'upgraded engineering entry\n')
                upgraded['engineering_files_sha256_lf']['r2_engineering_readiness.py']=digest(target.read_bytes())
                (self.root/MANIFEST).write_bytes((json.dumps(upgraded,indent=2,sort_keys=True)+'\n').encode())
                with self.subject.context():yield
            finally:
                target.write_bytes(original)
                (self.root/MANIFEST).write_bytes(self.raw)
        return context()

    def test_registered_predecessor_witness_survives_engineering_upgrade_with_actual_metadata(self):
        row=self.row()
        with mock.patch.object(self.subject,'PUBLISHED_PREDECESSOR_MANIFESTS',frozenset({digest(self.raw)}),create=True),self.upgraded_context():
            proof=self.witness([row])
        self.assertIsNotNone(proof,'upgrading engineering code invalidated a registered old public witness')
        self.assertEqual(proof['engineering_manifest_sha256'],digest(self.raw))
        self.assertEqual(proof['head_sha'],self.head)
        self.assertEqual(proof['run_id'],row['id'])
        self.assertEqual(proof['run_attempt'],row['run_attempt'])
        self.assertEqual(row['status'],'completed')
        self.assertEqual(row['conclusion'],'success')

    def test_unknown_predecessor_and_changed_predecessor_source_remain_rejected(self):
        row=self.row()
        with mock.patch.object(self.subject,'PUBLISHED_PREDECESSOR_MANIFESTS',frozenset(),create=True),self.upgraded_context():
            self.assertIsNone(self.witness([row]))
        with mock.patch.object(self.subject,'PUBLISHED_PREDECESSOR_MANIFESTS',frozenset({digest(self.raw)}),create=True),self.upgraded_context():
            original=self.subject.read_identity_at
            def changed(head,path):
                value=original(head,path)
                return value+b'wrong code\n' if path=='r2_engineering_readiness.py' else value
            with mock.patch.object(self.subject,'_identity_cache',OrderedDict()),mock.patch.object(self.subject,'_identity_cache_bytes',0),mock.patch.object(self.subject,'read_identity_at',side_effect=changed):
                self.assertIsNone(self.witness([row]))

    def test_predecessor_keeps_original_deadline_and_server_failure_rejection(self):
        row=self.row()
        with mock.patch.object(self.subject,'PUBLISHED_PREDECESSOR_MANIFESTS',frozenset({digest(self.raw)}),create=True),self.upgraded_context():
            self.assertIsNone(self.witness([row],before=datetime(2026,11,9,17,41,tzinfo=timezone.utc)))
            failed=dict(row,conclusion='failure')
            self.assertIsNone(self.witness([failed]))

    def test_approved_predecessor_never_allows_changed_local_execution(self):
        target=self.root/'r2_engineering_readiness.py'
        original=target.read_bytes()
        try:
            target.write_bytes(original+b'wrong local execution\n')
            with mock.patch.object(self.subject,'PUBLISHED_PREDECESSOR_MANIFESTS',frozenset({digest(self.raw)}),create=True):
                with self.assertRaises(ValueError):self.subject.verify_transport()
        finally:target.write_bytes(original)

    def test_new_workflow_qualifies_only_with_actual_identity_and_version_proof(self):
        row = self.row()
        result = self.witness([row])
        self.assertIsNotNone(result)
        self.assertEqual(result['run_id'], 20)
        self.assertEqual(result['head_sha'], self.head)
        self.assertEqual(result['workflow_path'], AUDIT)
        self.assertEqual(result['engineering_version'], VERSION)
        self.assertEqual(result['engineering_manifest_sha256'], digest(self.raw))
        self.assertEqual(result['run_attempt'], 2)
        self.assertEqual(row['path'], AUDIT)
        self.assertEqual(f.WORKFLOW, self.original_workflow)

    def test_old_workflow_proof_is_unchanged_and_not_relabelled(self):
        row = self.row(self.old_present, 1, self.original_workflow)
        self.assertEqual(self.witness([row]), {'run_id': 1, 'head_sha': self.old_present,
            'job_completed_at_utc': '2026-11-09T17:42:00+00:00'})

    def test_other_workflow_never_enters_compatibility_path(self):
        self.assertIsNone(self.witness([self.row(workflow='.github/workflows/other.yml')]))

    def test_new_audit_after_original_deadline_cannot_repair_historical_gap(self):
        self.assertIsNone(self.witness([self.row()],
            before=datetime(2026, 11, 9, 17, 41, tzinfo=timezone.utc)))

    def test_new_workflow_still_requires_exact_requested_score_bytes(self):
        result = self.subject.witness({fixtures.SCORE: b'different bytes'}, [self.row()],
                                     r.read_at, fixtures.BEFORE, fixtures.AFTER)
        self.assertIsNone(result)

    def test_present_score_404_and_identity_transport_errors_are_never_nonmatches(self):
        error = HTTPError('https://raw.githubusercontent.com/fixture', 404, 'Not Found', {}, None)
        with mock.patch.object(r, 'read_at', side_effect=error):
            with self.assertRaises(HTTPError) as caught:
                self.witness([self.row()])
        self.assertIs(caught.exception, error)
        for error in (HTTPError('https://raw.githubusercontent.com/fixture', 403, 'Forbidden', {}, None),
                      TimeoutError('identity timeout')):
            with self.subTest(error=repr(error)):
                # The score-404 case above verified and cached identity bytes.
                # Each injected transport fault must model an uncached read.
                with mock.patch.object(self.subject, '_identity_cache', OrderedDict()), \
                     mock.patch.object(self.subject, '_identity_cache_bytes', 0), \
                     mock.patch.object(self.subject, 'read_identity_at', side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        self.witness([self.row()])
                    self.assertIs(caught.exception, error)

    def test_identity_reader_rejects_nonallowlisted_paths(self):
        with self.assertRaises(ValueError):
            self.subject.read_identity_at(self.head, '../escape')

    def test_old_absent_candidate_then_valid_new_candidate_uses_real_frozen_loop(self):
        with self.subject.context():
            result = f.witness({fixtures.SCORE: fixtures.EXPECTED},
                [self.row(self.absent, 1, self.original_workflow), self.row()],
                r.read_at, fixtures.BEFORE, fixtures.AFTER)
        self.assertIsNotNone(result)
        self.assertEqual(result['run_id'], 20)
        self.assertIs(f.witness, self.original_witness)
        self.assertIs(r.read_at, self.original_reader)

    def test_transport_guard_rejects_changed_new_source_and_original_registration(self):
        self.subject.verify_transport()
        for name in ('r2_engineering_readiness.py', f.REGISTRATION):
            target = self.root / name
            raw = target.read_bytes()
            try:
                target.write_bytes(raw + b'changed bytes\n')
                with self.subTest(path=name), self.assertRaises(ValueError):
                    self.subject.verify_transport()
            finally:
                target.write_bytes(raw)

    def test_transport_guard_rejects_changed_original_registered_model(self):
        self.subject.verify_transport()
        target = self.root / f.MODEL
        raw = target.read_bytes()
        try:
            target.write_bytes(raw + b'changed model\n')
            with self.assertRaises(ValueError):
                self.subject.verify_transport()
        finally:
            target.write_bytes(raw)

    def test_compatible_identity_mismatch_at_exact_candidate_head_is_nonmatch(self):
        self.assertIsNotNone(self.witness([self.row()]))
        target = self.root / 'r2_engineering_readiness.py'
        raw = target.read_bytes()
        try:
            target.write_bytes(raw + b'candidate differs\n')
            fixtures.git(self.root, 'add', '-A')
            fixtures.git(self.root, 'commit', '-qm', 'retain incompatible candidate tree')
            incompatible = fixtures.git(self.root, 'rev-parse', 'HEAD').decode().strip()
        finally:
            target.write_bytes(raw)
        self.assertIsNone(self.witness([self.row(incompatible)]))
        self.assertEqual(self.witness([self.row(incompatible), self.row()])['head_sha'], self.head)

    def test_repeated_qualification_reads_immutable_identity_bytes_once_per_head_path(self):
        read = self.subject.read_identity_at
        requests = []
        def tracked(head, path):
            requests.append((head, path))
            return read(head, path)
        with mock.patch.object(self.subject, 'read_identity_at', side_effect=tracked):
            for unused in range(3):
                result = self.witness([self.row()])
                self.assertEqual(result['engineering_version'], VERSION)
        self.assertEqual(len(requests), len(set(requests)))
        self.assertEqual({head for head, path in requests}, {self.head})

    def test_cached_raw_tampering_is_rehashed_instead_of_reusing_eligibility(self):
        self.assertIsNotNone(self.witness([self.row()]))
        key = (self.head, 'r2_engineering_readiness.py')
        self.assertIn(key, self.subject._identity_cache)
        # Simulate corrupted cached bytes after a valid qualification. The
        # verifier must recalculate SHA for this use, never cache a PASS result.
        self.subject._identity_cache[key] = b'wrong immutable source bytes'
        self.assertIsNone(self.witness([self.row()]))

    def test_failed_identity_read_is_never_cached_and_retry_keeps_original_error(self):
        read = getattr(self.subject, '_cached_identity_at', self.subject.read_identity_at)
        failure = TimeoutError('transient transport failure')
        with mock.patch.object(self.subject, 'read_identity_at', side_effect=[failure, self.raw]) as raw:
            # Resolve the wrapper after the patch when RED has no cache wrapper.
            read = getattr(self.subject, '_cached_identity_at', self.subject.read_identity_at)
            with self.assertRaises(TimeoutError) as caught:
                read(self.head, MANIFEST)
            self.assertIs(caught.exception, failure)
            self.assertEqual(read(self.head, MANIFEST), self.raw)
            self.assertEqual(read(self.head, MANIFEST), self.raw)
        self.assertEqual(raw.call_count, 2)

    def test_identity_cache_enforces_total_byte_bound_and_lru_eviction(self):
        read = getattr(self.subject, '_cached_identity_at', self.subject.read_identity_at)
        paths = [MANIFEST, 'r2_engineering_runtime.py', 'r2_engineering_readiness.py']
        calls = []
        def raw(head, path):
            calls.append(path)
            return {paths[0]: b'abc', paths[1]: b'def', paths[2]: b'ghi'}[path]
        with mock.patch.object(self.subject, 'IDENTITY_CACHE_MAX_BYTES', 6, create=True), \
             mock.patch.object(self.subject, 'IDENTITY_CACHE_MAX_ENTRIES', 256, create=True), \
             mock.patch.object(self.subject, 'read_identity_at', side_effect=raw):
            read = getattr(self.subject, '_cached_identity_at', self.subject.read_identity_at)
            for path in [paths[0], paths[1], paths[0], paths[2], paths[1]]:
                self.assertEqual(len(read(self.head, path)), 3)
        self.assertEqual(calls, [paths[0], paths[1], paths[2], paths[1]])
        self.assertLessEqual(self.subject._identity_cache_bytes, 6)

    def test_identity_cache_enforces_entry_bound_independently_of_byte_bound(self):
        paths = [MANIFEST, 'r2_engineering_runtime.py', 'r2_engineering_readiness.py']
        calls = []
        def raw(head, path):
            calls.append(path)
            return b'x'
        with mock.patch.object(self.subject, 'IDENTITY_CACHE_MAX_BYTES', 100, create=True), \
             mock.patch.object(self.subject, 'IDENTITY_CACHE_MAX_ENTRIES', 2, create=True), \
             mock.patch.object(self.subject, 'read_identity_at', side_effect=raw):
            read = getattr(self.subject, '_cached_identity_at', self.subject.read_identity_at)
            for path in [*paths, paths[0]]:
                self.assertEqual(read(self.head, path), b'x')
        self.assertEqual(calls, [*paths, paths[0]])
        self.assertLessEqual(len(self.subject._identity_cache), 2)

    def test_original_metadata_guards_remain_required_for_new_workflow(self):
        for key, value in [('head_branch', 'other'), ('event', 'schedule'),
                           ('status', 'in_progress'), ('conclusion', 'failure'),
                           ('repository', {'full_name': 'other/repo'}),
                           ('head_repository', {'full_name': 'fork/repo'})]:
            row = self.row()
            row[key] = value
            with self.subTest(key=key):
                self.assertIsNone(self.witness([row]))

    def test_context_restores_all_globals_after_error_and_cancellation(self):
        original_run = r.run
        for failure in (RuntimeError('guard'), SystemExit(2), KeyboardInterrupt()):
            with self.subTest(failure=repr(failure)), self.assertRaises(type(failure)):
                with self.subject.context():
                    raise failure
            self.assertIs(f.witness, self.original_witness)
            self.assertEqual(f.WORKFLOW, self.original_workflow)
            self.assertIs(r.read_at, self.original_reader)
            self.assertIs(r.runs_server, self.original_runs)
            self.assertIs(r.run, original_run)

    def test_original_runtime_result_records_explicit_engineering_version(self):
        original = {'status': 'FORWARD_PAPER_ONLY', 'new_events': [], 'new_scores': []}
        with mock.patch.object(r, 'run', return_value=original) as runner:
            result = self.subject.run('audit')
            self.assertIs(r.run, runner)
        self.assertEqual(result['status'], 'FORWARD_PAPER_ONLY')
        self.assertEqual(result['engineering_version'], VERSION)
        self.assertEqual(result['engineering_manifest_sha256'], digest(self.raw))
        self.assertEqual(result['compatible_audit_workflow'], AUDIT)
        self.assertNotIn('engineering_version', original)

    def test_readiness_shim_records_explicit_version_and_preserves_current_status(self):
        path = HERE / 'r2_engineering_readiness.py'
        if path.exists():
            spec = importlib.util.spec_from_file_location('r2_engineering_readiness_subject', path)
            shim = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(shim)
            import r2_engineering_runtime as engine
        else:
            shim = types.SimpleNamespace(run=lambda mode: readiness.run(mode))
            engine = self.subject
        current = {'runtime_status': 'CURRENT_WINDOW_CLEAR_WITH_HISTORICAL_GAPS', 'errors': ['OLD_GAP']}
        with mock.patch.object(readiness, 'run', return_value=current), \
             mock.patch.object(engine, 'verify_transport', return_value=(self.manifest, digest(self.raw))):
            result = shim.run('audit')
        self.assertEqual(result['runtime_status'], 'CURRENT_WINDOW_CLEAR_WITH_HISTORICAL_GAPS')
        self.assertEqual(result['errors'], ['OLD_GAP'])
        self.assertEqual(result.get('engineering_version'), VERSION)
        self.assertNotIn('engineering_version', current)

    def test_local_retry_validation_preserves_initial_publisher_dispatch_evidence(self):
        output = self.root / 'phase2-forward-output/runtime.json'
        output.parent.mkdir(exist_ok=True)
        published = b'{"new_events":["1794240000_formal"],"request_witness":true}\n'
        output.write_bytes(published)
        with mock.patch.object(r, 'guard', return_value=(f.policy(),
                {'trial_model_identity': 'retained fixture'}, {}, {})), \
             mock.patch.object(r, 'records', return_value=[]):
            result = self.subject.run('local')
        self.assertEqual(result['engineering_version'], VERSION)
        self.assertEqual(output.read_bytes(), published)

    def test_pending_prediction_and_target_request_only_open_original_windows(self):
        event = {'event_id': '1794240000_formal', 'issued_at_utc': '2026-11-09T17:00:00Z',
                 'target_utc': '2026-11-09T18:00:00Z', 'cohort': 'formal'}
        rejected = {'rejected': {event['event_id']: 'MISSING_PREDICTION_PUBLIC_WITNESS'}}
        function = getattr(self.subject, 'pending_witness', lambda *args: [])
        prediction = function(rejected, [event], [], f.utc('2026-11-09T17:59:59Z'), f.policy())
        self.assertEqual(prediction, [{'event_id': event['event_id'], 'kind': 'prediction'}])
        self.assertEqual(function(rejected, [event], [], f.utc(event['target_utc']), f.policy()), [])
        target_rejected = {'rejected': {event['event_id']: 'MISSING_TARGET_PUBLIC_WITNESS'}}
        scores = [{'event_id': event['event_id']}]
        self.assertEqual(function(target_rejected, [event], scores,
            f.utc('2026-11-09T18:24:59Z'), f.policy()),
            [{'event_id': event['event_id'], 'kind': 'target'}])
        self.assertEqual(function(target_rejected, [event], scores,
            f.utc('2026-11-09T18:25:00Z'), f.policy()), [])
        self.assertEqual(function(target_rejected, [event], [],
            f.utc('2026-11-09T18:10:00Z'), f.policy()), [])
        self.assertEqual(function({'rejected': {event['event_id']: 'MISSING_REAL_TARGET'}},
            [event], scores, f.utc('2026-11-09T18:10:00Z'), f.policy()), [])

    def test_publish_pending_flag_preserves_original_result_and_raw_records(self):
        now = f.utc('2026-11-09T18:10:00Z')
        event = {'event_id': '1794240000_formal', 'issued_at_utc': '2026-11-09T17:00:00Z',
                 'target_utc': '2026-11-09T18:00:00Z', 'cohort': 'formal'}
        score = {'event_id': event['event_id']}
        original = {'status': 'FORWARD_PAPER_ONLY', 'request_witness': False,
                    'new_events': [], 'new_scores': [], 'new_target_attempts': [],
                    'rejected': {event['event_id']: 'MISSING_TARGET_PUBLIC_WITNESS'}}
        snapshots = [json.dumps(item, sort_keys=True) for item in (original, event, score)]
        with mock.patch.object(r, 'run', return_value=original), \
             mock.patch.object(r, 'records', side_effect=lambda kind: [event] if kind == 'events' else [score]), \
             mock.patch.object(self.subject, 'datetime', create=True) as clock:
            clock.now.return_value = now
            result = self.subject.run('publish')
        self.assertEqual(result.get('engineering_pending_witness'),
                         [{'event_id': event['event_id'], 'kind': 'target'}])
        self.assertTrue(result['request_witness'])
        for key in ('status', 'new_events', 'new_scores', 'new_target_attempts', 'rejected'):
            self.assertEqual(result[key], original[key])
        self.assertEqual([json.dumps(item, sort_keys=True) for item in (original, event, score)], snapshots)

    def test_discovery_uses_actual_attempt_jobs_and_preserves_old_rows(self):
        old = self.row(self.old_present, 1, self.original_workflow)
        fresh = self.row()
        requests = []
        def api(path):
            requests.append(path)
            if path.startswith('/actions/workflows/r2_engineering_audit.yml/runs?'):
                return {'workflow_runs': [fresh]}
            if path == '/actions/runs/20':
                return fresh
            if path == '/actions/runs/20/attempts/2/jobs?per_page=100&page=1':
                return {'jobs': [fresh['audit_job']]}
            raise AssertionError('unexpected server request ' + path)
        with mock.patch.object(self.subject, '_ORIGINAL_RUNS_SERVER', return_value=[old], create=True), \
             mock.patch.object(r.frozen, 'api', side_effect=api):
            rows = self.subject.runs_server([20])
        self.assertEqual(rows, [old, fresh])
        self.assertIn('/actions/runs/20/attempts/2/jobs?per_page=100&page=1', requests)
        self.assertEqual(requests.count('/actions/runs/20'), 2)

    def test_discovery_rejects_attempt_change_during_job_lookup(self):
        fresh = self.row()
        reads = []
        def api(path):
            if path.startswith('/actions/workflows/r2_engineering_audit.yml/runs?'):
                return {'workflow_runs': [fresh]}
            if '/attempts/2/jobs?' in path:
                return {'jobs': [fresh['audit_job']]}
            if path == '/actions/runs/20':
                reads.append(path)
                return fresh if len(reads) == 1 else {**fresh, 'run_attempt': 3}
            raise AssertionError(path)
        with mock.patch.object(self.subject, '_ORIGINAL_RUNS_SERVER', return_value=[], create=True), \
             mock.patch.object(r.frozen, 'api', side_effect=api):
            with self.assertRaises(ValueError):
                self.subject.runs_server()


if __name__ == '__main__':
    unittest.main()

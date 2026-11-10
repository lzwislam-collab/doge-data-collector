"""Outer writer orchestration and unmodified frozen witness causality."""
import copy
import importlib
import json
import os
import subprocess
import hashlib
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import learned_shadow_runtime as frozen
import test_learned_shadow_v1 as fixtures


class FrozenCausality(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.Shadow()
        self.e = self.fx.event()
        self.p = self.fx.policy()
        self.issued = frozen.shadow.utc(self.e['issued_at_utc'])
        self.target = frozen.shadow.utc(self.e['target_utc'])

    def audit(self, rid, head, start, end, path=None):
        return {'id': rid, 'repository': {'full_name': frozen.shadow.REPO},
                'path': path or frozen.shadow.WORKFLOW, 'event': 'workflow_dispatch',
                'head_sha': head, 'status': 'completed', 'conclusion': 'success',
                'created_at': start.isoformat(), 'updated_at': end.isoformat()}

    def test_normal_parent_cannot_witness_but_real_original_audit_can(self):
        raw = b'exact committed source bytes'
        before = self.issued - timedelta(minutes=1)
        normal = self.audit(1, 'a'*40, before-timedelta(minutes=1), before,
                            '.github/workflows/okx_hourly_extreme_history.yml')
        source_audit = self.audit(2, 'b'*40, before-timedelta(minutes=1), before)
        read = lambda head, path: raw
        self.assertIsNone(frozen.source_witness(raw, [normal], read, self.issued))
        self.assertEqual(frozen.source_witness(raw, [source_audit], read, self.issued)['run_id'], 2)
        self.assertIsNone(frozen.source_witness(raw, [source_audit], lambda *a: b'changed', self.issued))
        source_audit['updated_at'] = self.issued.isoformat()
        self.assertIsNone(frozen.source_witness(raw, [source_audit], read, self.issued))

    def test_actual_frozen_score_and_publication_gates_accept_only_causal_audits(self):
        score = frozen.shadow.score(self.e, self.fx.packet(self.target+timedelta(seconds=1), 101), self.p)
        pred = frozen.BASE+'/events/'+self.e['event_id']+'.json'
        target = frozen.BASE+'/scores/'+self.e['event_id']+'.json'
        stored = {('c'*40, pred): frozen.shadow.encode(self.e),
                  ('d'*40, target): frozen.shadow.encode(score)}
        read = lambda head, path: stored.get((head, path), b'')
        runs = [self.audit(3, 'c'*40, self.issued, self.issued+timedelta(minutes=1)),
                self.audit(4, 'd'*40, self.target+timedelta(seconds=1), self.target+timedelta(minutes=2))]
        good = frozen.publication_state(self.p, [self.e], {self.e['event_id']: score},
                                        runs, read, self.target+timedelta(minutes=3))
        self.assertEqual(good['bindings'][self.e['event_id']+'_prediction']['run_id'], 3)
        self.assertEqual(good['bindings'][self.e['event_id']+'_target']['run_id'], 4)
        bad = copy.deepcopy(runs)
        bad[0]['created_at'] = (self.issued-timedelta(seconds=1)).isoformat()
        bad[1]['updated_at'] = self.target.isoformat()
        rejected = frozen.publication_state(self.p, [self.e], {self.e['event_id']: score},
                                            bad, read, self.target+timedelta(minutes=3))
        self.assertEqual(rejected['bindings'], {})
        self.assertTrue(rejected['request_witness'])
        with self.assertRaises(ValueError):
            frozen.shadow.score(self.e, self.fx.packet(self.target-timedelta(seconds=1)), self.p)
        with self.assertRaises(ValueError):
            frozen.shadow.issue(self.p, self.fx.package(), self.fx.frame(self.issued),
                                self.fx.packet(self.issued), self.issued+timedelta(minutes=41), 1)


class SequentialOriginal18(unittest.TestCase):
    def setUp(self):
        self.m = importlib.import_module('runtime_forward_bridge')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fx = fixtures.Shadow()
        self.at = datetime(2026, 10, 16, 0, 5, tzinfo=timezone.utc)
        self.state = {'policy': self.fx.policy(), 'events': [], 'scores': {}, 'bindings': {}}
        self.env = patch.dict(os.environ, {'GITHUB_REF': 'refs/heads/main',
                              'GITHUB_REPOSITORY': frozen.shadow.REPO})
        self.env.start()
        self.addCleanup(self.env.stop)

    def result(self, **extra):
        return {'status': 'READY_PUBLICATION_ONLY', 'errors': {}, 'new_events': [],
                'new_scores': [], 'request_witness': False, **extra}

    def call(self, producer, at=None):
        return self.m.capture_original18(self.root, producer, clock=lambda: at or self.at,
                                         state_reader=lambda root: self.state)

    def write(self, rel, value):
        path = self.root/rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(frozen.shadow.encode(value))

    def test_current_frozen_plan_runs_one_bounded_sequential_producer(self):
        calls = []
        event = self.fx.event()
        def produce(timeout):
            calls.append(timeout)
            self.write(frozen.BASE+'/events/'+event['event_id']+'.json', event)
            return json.dumps(self.result(new_events=[event['event_id']], request_witness=True))
        out = self.call(produce)
        self.assertEqual(calls, [self.m.CAPTURE_TIMEOUT_SECONDS])
        self.assertEqual(out['changed_paths'], [frozen.BASE+'/events/'+event['event_id']+'.json'])
        self.assertTrue(out['requires_publication'])
        self.assertFalse(out['errors'])

    def test_expired_window_does_not_run_producer_or_backfill(self):
        calls = []
        out = self.call(lambda timeout: calls.append(timeout), self.at+timedelta(minutes=41))
        self.assertEqual(calls, [])
        self.assertEqual(out['status'], 'SKIP_NO_CURRENT_OBLIGATION')

    def test_partial_producer_error_stays_error_with_valid_new_evidence_pending(self):
        event = self.fx.event()
        def produce(timeout):
            self.write(frozen.BASE+'/events/'+event['event_id']+'.json', event)
            return json.dumps(self.result(new_events=[event['event_id']], request_witness=True,
                                          errors={'24': 'ValueError: unavailable quote'}))
        out = self.call(produce)
        self.assertEqual(out['status'], 'CAPTURE_ERRORS_PRESERVED')
        self.assertTrue(out['errors'])
        self.assertTrue(out['requires_publication'])

    def test_existing_journal_overwrite_is_rejected(self):
        path = frozen.BASE+'/events/1792108800_1.json'
        self.write(path, {'original': True})
        def produce(timeout):
            self.write(path, {'mutated': True})
            return json.dumps(self.result())
        with self.assertRaises(ValueError):
            self.call(produce)

    def test_process_timeout_is_propagated_without_success_or_retry(self):
        failure = subprocess.TimeoutExpired(['frozen'], 90)
        def produce(timeout):
            raise failure
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.call(produce)
        self.assertIs(caught.exception, failure)

    def test_waiting_exact_source_also_requests_original_audit_after_public_readback(self):
        out = self.call(lambda timeout: json.dumps(self.result(status='WAIT_EXACT_SOURCE_WITNESS', request_witness=True)))
        posted = []
        state = {}
        publication = {'status': 'verified_remote_readback', 'head': 'a'*40, 'verified_paths': []}
        dispatch = lambda name: posted.append(name) or {'workflow': name, 'status': 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED'}
        first = self.m.request_original18_witness(out, publication, state, dispatch=dispatch)
        second = self.m.request_original18_witness(out, publication, state, dispatch=dispatch)
        self.assertEqual(posted, ['learned_shadow_v1.yml'])
        self.assertEqual(first['status'], 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')
        self.assertEqual(second['status'], 'ALREADY_REQUESTED_THIS_PUBLIC_HEAD')

    def test_new_evidence_cannot_request_witness_before_exact_path_publication(self):
        capture = {'request_witness': True, 'changed_paths': [frozen.BASE+'/events/1792108800_1.json']}
        for publication in ({'status': 'push_succeeded', 'head': 'a'*40},
                            {'status': 'verified_remote_readback', 'head': 'a'*40, 'verified_paths': []}):
            with self.subTest(publication=publication), self.assertRaises(ValueError):
                self.m.request_original18_witness(capture, publication, {}, dispatch=lambda name: self.fail('must not post'))

    def test_active_audit_does_not_claim_new_witness_or_mark_accepted(self):
        out = {'request_witness': True, 'changed_paths': []}
        state = {}
        result = self.m.request_original18_witness(out, {'status': 'verified_remote_readback', 'head': 'a'*40},
                 state, dispatch=lambda name: {'workflow': name, 'status': 'ALREADY_PENDING'})
        self.assertEqual(result['status'], 'ALREADY_PENDING')
        self.assertEqual(state, {})

    def test_default_audit_path_uses_one_bounded_client_for_five_queue_reads_and_post(self):
        class Client:
            def __init__(self):
                self.gets, self.posts = [], []
            def api(self, path):
                self.gets.append(path)
                return {'total_count': 0, 'workflow_runs': []}
            def request(self, path, payload):
                self.posts.append((path, payload))
                return 204
        client = Client()
        self.m.request_original18_witness({'request_witness': True, 'changed_paths': []},
            {'status': 'verified_remote_readback', 'head': 'a'*40}, {}, client=client)
        self.assertEqual(len(client.gets), 5)
        self.assertEqual(client.posts, [('/actions/workflows/learned_shadow_v1.yml/dispatches', {'ref': 'main'})])

    def test_unknown_default_post_consumes_context_without_claiming_acceptance(self):
        class Client:
            def __init__(self):
                self.posts = 0
            def api(self, path):
                return {'total_count': 0, 'workflow_runs': []}
            def request(self, path, payload):
                self.posts += 1
                raise TimeoutError('unknown POST outcome')
        client, state = Client(), {}
        args = ({'request_witness': True, 'changed_paths': []},
                {'status': 'verified_remote_readback', 'head': 'a'*40}, state)
        with self.assertRaises(TimeoutError):
            self.m.request_original18_witness(*args, client=client)
        retry = self.m.request_original18_witness(*args, client=client)
        self.assertEqual(retry['status'], 'UNKNOWN_PREVIOUS_AUDIT_DISPATCH_NOT_RETRIED')
        self.assertNotIn('original18_audit_requested_head', state)
        self.assertEqual(client.posts, 1)

    def source_queue(self):
        source = self.root/frozen.bundle.SOURCE
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'new committed source\n')
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.DEVNULL)
        git('init')
        git('config', 'user.name', 'Fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        git('add', '.')
        git('commit', '-m', 'public source fixture')
        head = git('rev-parse', 'HEAD').decode().strip()
        blob = git('rev-parse', 'HEAD:'+frozen.bundle.SOURCE).decode().strip()
        repo = frozen.shadow.REPO
        class Client:
            def __init__(self): self.posts = []; self.gets = []; self.post_error = None
            def api(self, path):
                from urllib.parse import urlsplit, parse_qs
                self.gets.append(path)
                parts = urlsplit(path); query = parse_qs(parts.query)
                row = {'id': 42, 'head_sha': 'c'*40, 'head_branch': 'main',
                       'path': '.github/workflows/learned_shadow_v1.yml', 'status': 'in_progress',
                       'event': 'workflow_dispatch', 'repository': {'full_name': repo},
                       'head_repository': {'full_name': repo}}
                if parts.path.startswith('/actions/workflows/'):
                    return ({'total_count': 1, 'workflow_runs': [row]} if query['status'] == ['in_progress']
                            else {'total_count': 0, 'workflow_runs': []})
                if parts.path == '/actions/runs/42': return row
                if parts.path == '/contents/'+frozen.bundle.SOURCE:
                    current = query['ref'] == [head]
                    return {'type': 'file', 'path': frozen.bundle.SOURCE,
                            'sha': blob if current else 'd'*40, 'size': 21 if current else 9}
                raise AssertionError('unexpected source queue read '+path)
            def request(self, path, payload):
                self.posts.append((path, payload))
                if self.post_error: raise self.post_error
                return 204
        root_patch = patch.object(frozen, 'ROOT', self.root)
        root_patch.start(); self.addCleanup(root_patch.stop)
        return source, {'status': 'verified_remote_readback', 'head': head, 'verified_paths': []}, Client()

    def test_exact_source_wait_queues_correct_source_once_behind_old_source(self):
        source, publication, client = self.source_queue()
        capture = {'request_witness': True, 'changed_paths': [], 'runtime_status': 'WAIT_EXACT_SOURCE_WITNESS'}
        state = {}
        first = self.m.request_original18_witness(capture, publication, state, client=client)
        second = self.m.request_original18_witness(capture, publication, state, client=client)
        self.assertEqual(first['status'], 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')
        self.assertEqual(second['status'], 'ALREADY_REQUESTED_THIS_PUBLIC_HEAD')
        self.assertEqual(len(client.posts), 1)
        self.assertFalse(first['exact_head_witness_verified'])

    def test_exact_source_wait_rejects_uncommitted_source_before_any_post(self):
        source, publication, client = self.source_queue()
        source.write_bytes(b'unpublished source')
        with self.assertRaises(ValueError):
            self.m.request_original18_witness({'request_witness': True, 'changed_paths': [],
                'runtime_status': 'WAIT_EXACT_SOURCE_WITNESS'}, publication, {}, client=client)
        self.assertFalse(client.posts)

    def test_queued_source_unknown_post_is_not_retried(self):
        source, publication, client = self.source_queue()
        client.post_error = TimeoutError('unknown accepted POST')
        capture = {'request_witness': True, 'changed_paths': [], 'runtime_status': 'WAIT_EXACT_SOURCE_WITNESS'}
        state = {}
        with self.assertRaises(TimeoutError):
            self.m.request_original18_witness(capture, publication, state, client=client)
        result = self.m.request_original18_witness(capture, publication, state, client=client)
        self.assertEqual(result['status'], 'UNKNOWN_PREVIOUS_AUDIT_DISPATCH_NOT_RETRIED')
        self.assertEqual(len(client.posts), 1)

    def test_source_modified_during_queue_reads_never_consumes_post_intent(self):
        source, publication, client = self.source_queue()
        original_api = client.api
        def change_after_read(path):
            value = original_api(path)
            if path.startswith('/contents/'):
                source.write_bytes(b'changed during remote source read')
            return value
        client.api = change_after_read
        state = {}
        with self.assertRaises(ValueError):
            self.m.request_original18_witness({'request_witness': True, 'changed_paths': [],
                'runtime_status': 'WAIT_EXACT_SOURCE_WITNESS'}, publication, state, client=client)
        self.assertFalse(client.posts)
        self.assertEqual(state, {})

    def test_remote_descendant_head_with_same_source_can_queue_original_local_source(self):
        source, publication, client = self.source_queue()
        local_head = publication['head']
        publication['head'] = 'e'*40
        original_api = client.api
        def descendant(path):
            return original_api(path.replace('ref='+'e'*40, 'ref='+local_head))
        client.api = descendant
        result = self.m.request_original18_witness({'request_witness': True, 'changed_paths': [],
            'runtime_status': 'WAIT_EXACT_SOURCE_WITNESS'}, publication, {}, client=client)
        self.assertEqual(result['status'], 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')
        self.assertEqual(len(client.posts), 1)

    def test_remote_descendant_with_different_source_never_queues_for_local_source(self):
        source, publication, client = self.source_queue()
        publication['head'] = 'e'*40
        with self.assertRaises(ValueError):
            self.m.request_original18_witness({'request_witness': True, 'changed_paths': [],
                'runtime_status': 'WAIT_EXACT_SOURCE_WITNESS'}, publication, {}, client=client)
        self.assertFalse(client.posts)

    def test_local_head_changed_during_source_reads_never_consumes_post_intent(self):
        source, publication, client = self.source_queue()
        original_api = client.api
        changed = []
        def advance_after_read(path):
            value = original_api(path)
            if path.startswith('/contents/') and not changed:
                subprocess.check_output(['git', 'commit', '--allow-empty', '-m', 'new local head'],
                                        cwd=self.root, stderr=subprocess.DEVNULL)
                changed.append(True)
            return value
        client.api = advance_after_read
        state = {}
        with self.assertRaises(ValueError):
            self.m.request_original18_witness({'request_witness': True, 'changed_paths': [],
                'runtime_status': 'WAIT_EXACT_SOURCE_WITNESS'}, publication, state, client=client)
        self.assertFalse(client.posts)
        self.assertEqual(state, {})


class Original18Validation(unittest.TestCase):
    """Cache the full immutable model guard, never the actual Git journal guard."""
    def setUp(self):
        self.m = importlib.import_module('runtime_forward_bridge')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fx = fixtures.Shadow()
        self.git('init')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        training = b'actual immutable fixture training source\n'
        source = self.root/frozen.bundle.SOURCE
        source.parent.mkdir(parents=True)
        source.write_bytes(training)
        self.git('add', '.')
        self.git('commit', '-m', 'training fixture')
        commit = self.git('rev-parse', 'HEAD').decode().strip()
        self.policy = self.fx.policy()
        self.package = {**self.fx.package(), 'training_source_commit': commit,
                        'training_input_sha256': hashlib.sha256(training).hexdigest()}
        for name in frozen.shadow.REGISTERED_FILES:
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('immutable fixture '+name+'\n').encode())
        (self.root/frozen.shadow.POLICY).write_bytes(frozen.shadow.encode(self.policy))
        (self.root/frozen.shadow.PACKAGE).write_bytes(frozen.shadow.encode(self.package))
        runtime = {}
        for name in ('learned_shadow_runtime.py', 'test_learned_shadow_runtime.py',
                     '.github/workflows/learned_shadow_live.yml'):
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('runtime fixture '+name+'\n').encode())
            runtime[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {'sha256_lf': runtime, 'model_package_sha256': frozen.shadow.digest(self.package),
                    'effective_from_utc': self.policy['effective_from_utc'],
                    'publication_only_not_independent_worm': True}
        (self.root/frozen.MANIFEST).write_bytes(frozen.shadow.encode(manifest))
        self.bundle_result = (self.policy, self.package, {})
        self.m._BUNDLE_CACHE.clear()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.DEVNULL)

    def event(self):
        event = self.fx.event()
        path = self.root/frozen.BASE/'events'/(event['event_id']+'.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(frozen.shadow.encode(event))
        self.git('add', '.')
        self.git('commit', '-m', 'actual event fixture')
        return path

    def test_full_guard_cache_is_bound_to_root_and_returns_deep_copies(self):
        with patch.object(frozen.bundle, 'guard', return_value=self.bundle_result) as full:
            first = self.m._checked_bundle(self.root)
            first[0]['groups'].append('caller mutation')
            second = self.m._checked_bundle(self.root)
            self.assertNotIn('caller mutation', second[0]['groups'])
            self.assertEqual(full.call_count, 1)
            with tempfile.TemporaryDirectory() as other:
                shutil.copytree(self.root, other, dirs_exist_ok=True)
                self.m._checked_bundle(Path(other))
            self.assertEqual(full.call_count, 2)

    def test_registered_runtime_and_manifest_drift_invalidate_cached_success(self):
        names = ('learned_shadow_v1.py', 'learned_shadow_runtime.py', frozen.MANIFEST)
        for name in names:
            with self.subTest(name=name):
                self.m._BUNDLE_CACHE.clear()
                path = self.root/name
                before = path.read_bytes()
                with patch.object(frozen.bundle, 'guard', return_value=self.bundle_result) as full:
                    self.m._checked_bundle(self.root)
                    path.write_bytes(before+b'\n')
                    try:
                        self.m._checked_bundle(self.root)
                    except ValueError:
                        pass  # Runtime manifest mismatch is rejected before cache use.
                    else:
                        self.assertEqual(full.call_count, 2)
                path.write_bytes(before)

    def test_training_git_bytes_or_missing_model_never_reuse_success(self):
        with patch.object(frozen.bundle, 'guard', return_value=self.bundle_result):
            self.m._checked_bundle(self.root)
            with patch.object(frozen.bundle, 'git', return_value=b'changed training source'):
                with self.assertRaises(ValueError):
                    self.m._checked_bundle(self.root)
            (self.root/frozen.shadow.PACKAGE).unlink()
            with self.assertRaises(FileNotFoundError):
                self.m._checked_bundle(self.root)
            self.assertEqual(self.m._BUNDLE_CACHE, {})

    def test_full_guard_failure_and_mid_guard_input_change_are_not_cached(self):
        with patch.object(frozen.bundle, 'guard', side_effect=ValueError('training mismatch')):
            with self.assertRaises(ValueError):
                self.m._checked_bundle(self.root)
        self.assertEqual(self.m._BUNDLE_CACHE, {})
        def changing(root):
            path = self.root/'learned_shadow_v1.py'
            path.write_bytes(path.read_bytes()+b'changed during guard')
            return self.bundle_result
        with patch.object(frozen.bundle, 'guard', side_effect=changing):
            with self.assertRaises(ValueError):
                self.m._checked_bundle(self.root)
        self.assertEqual(self.m._BUNDLE_CACHE, {})

    def test_every_validation_checks_committed_deletion_even_on_model_cache_hit(self):
        path = self.event()
        with patch.object(frozen.bundle, 'guard', return_value=self.bundle_result) as full:
            self.m.validate_original18(self.root)
            path.unlink()
            with self.assertRaises(ValueError):
                self.m.validate_original18(self.root)
            self.assertEqual(full.call_count, 1)

    def test_every_validation_checks_old_byte_mutation_on_model_cache_hit(self):
        path = self.event()
        with patch.object(frozen.bundle, 'guard', return_value=self.bundle_result) as full:
            self.m.validate_original18(self.root)
            event = json.loads(path.read_bytes())
            event['reference_price'] += 1
            path.write_bytes(frozen.shadow.encode(event))
            with self.assertRaises(ValueError):
                self.m.validate_original18(self.root)
            self.assertEqual(full.call_count, 1)


if __name__ == '__main__':
    unittest.main()

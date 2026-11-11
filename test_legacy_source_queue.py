"""Strict legacy queue compatibility; no real Git writes or network requests."""
import io
import json
import os
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import runtime_forward_bridge as bridge
import runtime_r2_bridge as core
import runtime_custody_bridge as transport

WORKFLOW = 'learned_shadow_v1.yml'
FIRST = '/actions/workflows/'+WORKFLOW+'/runs?branch=main&status=pending&per_page=1'
HEAD, OLD, BLOB = 'a'*40, 'b'*40, 'c'*40


def row(**changes):
    return {'id': 42, 'head_branch': 'main', 'path': '.github/workflows/'+WORKFLOW,
            'status': 'in_progress', 'event': 'workflow_dispatch', 'head_sha': OLD,
            'repository': {'full_name': bridge.original.shadow.REPO},
            'head_repository': {'full_name': bridge.original.shadow.REPO}, **changes}


def empty():
    return {'workflow_runs': [], 'total_count': 0}


class Client:
    def __init__(self, first=(), running=False):
        self.first = list(first)
        self.running = running
        self.gets, self.posts = [], []
        self.spent = 0.0
        self.post_error = None
        self.source_wrong = False
        self.hook = lambda path: None

    def api(self, path):
        self.gets.append(path)
        self.hook(path)
        query = parse_qs(urlsplit(path).query)
        if path == FIRST and self.first:
            value = self.first.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        if path.startswith('/actions/workflows/'):
            return {'total_count': 1, 'workflow_runs': [row()]} if (
                self.running and query['status'] == ['in_progress']) else empty()
        if path == '/actions/runs/42':
            return row()
        if path.startswith('/contents/'+bridge.original.bundle.SOURCE+'?'):
            current = query['ref'] == [HEAD]
            return {'type': 'file', 'path': bridge.original.bundle.SOURCE,
                    'sha': BLOB if current and not self.source_wrong else 'd'*40,
                    'size': 12 if current else 8}
        raise AssertionError(path)

    def request(self, path, payload=None):
        self.posts.append((path, payload))
        if self.post_error:
            raise self.post_error
        return 204


class LegacySourceQueueTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'GITHUB_REF': 'refs/heads/main',
            'GITHUB_REPOSITORY': bridge.original.shadow.REPO})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.capture = {'request_witness': True, 'changed_paths': [],
                        'runtime_status': 'WAIT_EXACT_SOURCE_WITNESS'}
        self.publication = {'status': 'verified_remote_readback', 'head': HEAD,
                            'verified_paths': []}
        self.source = {'head': HEAD, 'local_head': HEAD, 'sha': BLOB, 'size': 12}
        self.state = {}

    def call(self, client, *, source=None, capture=None):
        with patch.object(bridge, '_committed_source', side_effect=source or (lambda pub: dict(self.source))), \
                redirect_stderr(io.StringIO()):
            return bridge.request_original18_witness(capture or self.capture,
                self.publication, self.state, client=client)

    def test_legal_mismatch_rereads_same_url_then_empty_and_posts_once(self):
        client = Client([{'total_count': 1, 'workflow_runs': []}, empty()])
        out = self.call(client)
        self.assertEqual(out['status'], 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')
        self.assertEqual(client.gets[:2], [FIRST, FIRST])
        self.assertEqual(client.gets.count(FIRST), 3)  # original fresh second census survives
        self.assertEqual(len(client.posts), 1)
        self.assertFalse(out['exact_head_witness_verified'])
        self.assertEqual(self.state['original18_audit_attempted_head'], HEAD)

    def test_legal_mismatch_then_active_never_consumes_intent(self):
        client = Client([{'total_count': 0, 'workflow_runs': [row(status='queued')]},
                         {'total_count': 1, 'workflow_runs': [row(status='queued')]}])
        out = self.call(client)
        self.assertEqual(out['status'], 'ALREADY_PENDING')
        self.assertEqual(client.gets, [FIRST, FIRST])
        self.assertEqual(client.posts, [])
        self.assertEqual(self.state, {})

    def test_bad_schema_or_identity_never_authorizes_second_read(self):
        bad = [None, [], {'total_count': True, 'workflow_runs': []},
               {'total_count': -1, 'workflow_runs': []},
               {'total_count': 1, 'workflow_runs': 'not a list'},
               {'total_count': 1, 'workflow_runs': [row(), row()]},
               {'total_count': 0, 'workflow_runs': ['bad row']}]
        for changes in ({'id': True}, {'id': 0}, {'id': '42'}, {'head_branch': 'feature'},
                        {'path': '.github/workflows/other.yml'}, {'path': None},
                        {'status': 'unknown'}):
            bad.append({'total_count': 0, 'workflow_runs': [row(**changes)]})
        for value in bad:
            with self.subTest(value=value):
                self.state.clear()
                client = Client([value, empty()])
                with self.assertRaises((ValueError, TypeError, KeyError)):
                    self.call(client)
                self.assertEqual(client.gets, [FIRST])
                self.assertFalse(client.posts)
                self.assertEqual(self.state, {})

    def test_provider_errors_propagate_without_reread_or_intent(self):
        errors = [TimeoutError('budget'), HTTPError('https://example.invalid', 403, 'quota', {}, None),
                  RuntimeError('unknown provider result')]
        for error in errors:
            with self.subTest(kind=type(error).__name__):
                client = Client([error, empty()])
                with self.assertRaises(type(error)) as caught:
                    self.call(client)
                self.assertIs(caught.exception, error)
                self.assertEqual(client.gets, [FIRST])
                self.assertEqual(self.state, {})
                self.assertFalse(client.posts)

    def test_persistent_disagreement_is_exactly_two_reads_without_outer_retry(self):
        for runtime_status in ('WAIT_EXACT_SOURCE_WITNESS', 'READY_PUBLICATION_ONLY'):
            with self.subTest(runtime_status=runtime_status):
                client = Client([{'total_count': 1, 'workflow_runs': []}]*4)
                capture = {**self.capture, 'runtime_status': runtime_status}
                with self.assertRaises(ValueError) as caught:
                    self.call(client, capture=capture)
                self.assertNotIsInstance(caught.exception, core._QueueSnapshotChanged)
                self.assertEqual(client.gets, [FIRST, FIRST])
                self.assertEqual(self.state, {})
                self.assertFalse(client.posts)

    def test_original_running_identity_and_repeated_fresh_censuses_still_run(self):
        client = Client([{'total_count': 1, 'workflow_runs': []}, empty()], running=True)
        self.call(client)
        self.assertEqual(client.gets.count('/actions/runs/42'), 1)
        self.assertEqual(sum(p.startswith('/contents/') for p in client.gets), 2)
        self.assertEqual(client.gets.count(FIRST), 3)  # initial reread plus original final census
        self.assertEqual(len(client.posts), 1)

    def test_original_remote_source_guard_rejects_after_reread_without_intent(self):
        client = Client([{'total_count': 1, 'workflow_runs': []}, empty()])
        client.source_wrong = True
        with self.assertRaisesRegex(ValueError, 'public source does not match'):
            self.call(client)
        self.assertEqual(self.state, {})
        self.assertFalse(client.posts)

    def test_original_local_source_revalidation_remains_immediately_before_intent(self):
        client = Client([{'total_count': 1, 'workflow_runs': []}, empty()])
        calls = []
        def source(publication):
            calls.append(1)
            self.assertEqual(self.state, {})
            return dict(self.source) if len(calls) == 1 else {**self.source, 'local_head': OLD}
        with self.assertRaisesRegex(ValueError, 'committed source changed'):
            self.call(client, source=source)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.state, {})
        self.assertFalse(client.posts)

    def test_unknown_post_is_consumed_and_never_retried(self):
        client = Client([{'total_count': 1, 'workflow_runs': []}, empty()])
        client.post_error = TimeoutError('unknown POST result')
        with self.assertRaises(TimeoutError):
            self.call(client)
        reads = list(client.gets)
        self.assertEqual(self.state, {'original18_audit_attempted_head': HEAD})
        retry = self.call(client)
        self.assertEqual(retry['status'], 'UNKNOWN_PREVIOUS_AUDIT_DISPATCH_NOT_RETRIED')
        self.assertEqual(client.gets, reads)
        self.assertEqual(len(client.posts), 1)

    def test_completed_race_still_defers_and_custom_dispatch_stays_untouched(self):
        client = Client([{'total_count': 0, 'workflow_runs': [row(status='completed')]},
                         {'total_count': 1, 'workflow_runs': [row(status='completed')]}])
        self.assertEqual(self.call(client)['status'], 'ALREADY_PENDING')
        self.assertFalse(client.posts)
        self.assertEqual(self.state, {})
        with patch.object(bridge, '_committed_source', side_effect=AssertionError('custom path changed')):
            calls = []
            out = bridge.request_original18_witness(self.capture, self.publication, {},
                dispatch=lambda name: calls.append(name) or {'workflow': name, 'status': 'ALREADY_PENDING'})
        self.assertEqual(calls, [WORKFLOW])
        self.assertEqual(out['status'], 'ALREADY_PENDING')

    def test_reread_uses_same_original_transport_budget(self):
        client = transport.GitHubClient()
        client.spent = 39.5
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read1(self, limit):
                value, self.body = self.body, b''
                return value
        first = Response()
        first.body = json.dumps({'total_count': 1, 'workflow_runs': []}).encode()
        second = Response()
        second.body = b''
        now = [0.0]
        def opened(request, timeout):
            # Both requests consume the same spent account; the second crosses its own remainder.
            if opened.calls:
                self.assertAlmostEqual(timeout, .3)
                now[0] += .4
                response = second
            else:
                self.assertAlmostEqual(timeout, .5)
                now[0] += .2
                response = first
            opened.calls += 1
            return response
        opened.calls = 0
        with patch.object(transport, 'urlopen', side_effect=opened), \
                patch.object(transport.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.dict(os.environ, {'GITHUB_TOKEN': 'test-only'}):
            with self.assertRaises(TimeoutError):
                self.call(client)
        self.assertEqual(opened.calls, 2)
        self.assertGreaterEqual(client.spent, 40)
        self.assertEqual(self.state, {})

    def test_default_client_is_created_once_and_preserves_source_guards(self):
        client = Client([{'total_count': 1, 'workflow_runs': []}, empty()])
        with patch.object(transport, 'GitHubClient', return_value=client) as create:
            self.call(None)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(sum(p.startswith('/contents/') for p in client.gets), 1)
        self.assertEqual(len(client.posts), 1)

    def test_unrelated_urls_delegate_without_queue_retry(self):
        client = Client()
        proxy = bridge._Original18QueueClient(client)
        self.assertIs(proxy._client, client)
        for path in (FIRST+'&unexpected=1', FIRST.replace('branch=main', 'branch=feature'),
                     FIRST.replace('pending', 'completed'),
                     FIRST.replace(WORKFLOW, 'phase2_forward_audit_v2.yml')):
            value = {'total_count': 1, 'workflow_runs': []}
            with patch.object(client, 'api', return_value=value) as api:
                self.assertIs(proxy.api(path), value)
                api.assert_called_once_with(path)


if __name__ == '__main__':
    unittest.main()

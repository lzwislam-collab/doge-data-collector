"""Recovery guards exercise real trust/queue/transport code with no live POST."""
import base64
import copy
import importlib
import json
import os
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

REPO = 'lzwislam-collab/doge-data-collector'
SOURCE = '.github/workflows/okx_hourly_extreme_history.yml'
AT = datetime(2026, 11, 10, 12, 20, tzinfo=timezone.utc)
ENV = {'GITHUB_REPOSITORY': REPO, 'GITHUB_REF': 'refs/heads/main',
       'GITHUB_EVENT_NAME': 'workflow_run'}


def parent():
    return {'id': 91, 'run_attempt': 1, 'head_sha': 'a'*40, 'path': SOURCE,
            'repository': {'full_name': REPO}, 'head_repository': {'full_name': REPO},
            'head_branch': 'main', 'event': 'schedule', 'status': 'completed',
            'conclusion': 'failure', 'pull_requests': []}


class Remote:
    """Exact API boundary fixture; unexpected requests fail instead of going live."""
    def __init__(self):
        self.now = AT
        self.spent = 0.0
        self.run = parent()
        self.head = 'b'*40
        self.snapshot = {'status': 'ok', 'collected_at_utc': '2026-11-10T08:20:47.231689+00:00'}
        self.jobs = [{'id': 81, 'run_id': 91, 'status': 'completed', 'conclusion': 'failure',
                      'steps': [{'name': name, 'status': 'completed', 'conclusion': 'success', 'number': n}
                                for n, name in enumerate((
                                    'Fail closed before writes on full-history or frozen protocol mutation',
                                    'Persist data and diagnostics'), 1)]}]
        self.active = None
        self.active_by_workflow = {}
        self.transitioned_completed = False
        self.prior = None
        self.calls = []
        self.posts = []
        self.queue_views = 0
        self.snapshots = 0
        self.head_reads = 0
        self.change = None
        self.bad_queue = None
        self.bad_attempt = None
        self.bad_jobs = None
        self.job_pages = None
        self.bad_file = None
        self.post_error = None

    def collect(self, **changes):
        return {'id': 92, 'run_attempt': 1, 'head_sha': 'b'*40,
                'repository': {'full_name': REPO}, 'head_repository': {'full_name': REPO},
                'head_branch': 'main', 'path': '.github/workflows/collect.yml',
                'status': 'completed', 'event': 'workflow_dispatch',
                'created_at': '2026-11-10T12:01:00Z', **changes}

    def api(self, path):
        self.calls.append(path)
        self.spent += .1
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        if parsed.path == '/actions/runs/91':
            value = copy.deepcopy(self.run)
        elif parsed.path == '/actions/runs/91/attempts/1/jobs':
            if self.job_pages is not None:
                value = copy.deepcopy(self.job_pages[int(query['page'][0])-1])
            else:
                value = copy.deepcopy(self.bad_jobs if self.bad_jobs is not None else {
                    'total_count': len(self.jobs), 'jobs': self.jobs})
        elif path == '/git/ref/heads/main':
            self.head_reads += 1
            value = {'object': {'type': 'commit', 'sha': self.head}}
        elif parsed.path == '/contents/data/latest.json':
            self.snapshots += 1
            raw = self.snapshot if isinstance(self.snapshot, bytes) else json.dumps(self.snapshot).encode()
            import runtime_custody_bridge as transport
            value = {'type': 'file', 'path': 'data/latest.json', 'encoding': 'base64',
                     'sha': transport.git_blob(raw), 'size': len(raw),
                     'content': base64.b64encode(raw).decode()}
            if self.bad_file:
                value.update(self.bad_file)
            if query != {'ref': [self.head]}:
                raise AssertionError('snapshot must be pinned to current main')
        elif parsed.path in ('/actions/workflows/collect.yml/runs',
                              '/actions/workflows/okx_hourly_extreme_history.yml/runs',
                              '/actions/workflows/doge_history.yml/runs'):
            workflow = parsed.path.split('/')[-2]
            if query.get('branch') != ['main'] or query.get('per_page') != ['1']:
                raise AssertionError('collector listing must be bounded and main-only')
            if 'status' in query:
                if workflow == 'collect.yml' and query['status'] == ['pending']:
                    self.queue_views += 1
                active = self.active if workflow == 'collect.yml' else self.active_by_workflow.get(workflow)
                if active is not None and not (
                        active['status'] == query['status'][0]
                        or self.transitioned_completed and active['status'] == 'completed'):
                    active = None
                value = copy.deepcopy(self.bad_queue if self.bad_queue is not None else {
                    'total_count': int(active is not None),
                    'workflow_runs': [] if active is None else [active]})
            else:
                if query != {'branch': ['main'], 'event': ['workflow_dispatch'],
                             'created': ['>=2026-11-10T12:00:00Z'], 'per_page': ['1']}:
                    raise AssertionError('dedupe must include all current-hour dispatch outcomes')
                value = copy.deepcopy(self.bad_attempt if self.bad_attempt is not None else {
                    'total_count': int(self.prior is not None),
                    'workflow_runs': [] if self.prior is None else [self.prior]})
        else:
            raise AssertionError('unapproved request '+path)
        if self.change:
            self.change(self, path)
        return value

    def request(self, path, payload=None):
        if payload is None:
            return self.api(path)
        if self.spent >= 40:
            raise TimeoutError('shared transport budget exhausted')
        self.posts.append((path, payload))
        if self.post_error:
            raise self.post_error
        return 204


class Recovery(unittest.TestCase):
    def setUp(self):
        self.remote = Remote()
        self.event = {'action': 'completed', 'repository': {'full_name': REPO}, 'workflow_run': parent()}

    def engine(self):
        self.assertIsNotNone(importlib.util.find_spec('market_snapshot_recovery'),
                             'stale required market snapshot has no bounded existing recovery path')
        return importlib.import_module('market_snapshot_recovery')

    def call(self, event=None, env=None):
        return self.engine().recover(self.event if event is None else event,
                                     client=self.remote, clock=lambda: self.remote.now,
                                     environ=ENV if env is None else env)

    def blocked(self, error=ValueError):
        with self.assertRaises(error):
            self.call()
        self.assertEqual(self.remote.posts, [])

    def test_stale_required_market_dispatches_existing_collect_once_without_freshness_claim(self):
        result = self.call()
        self.assertEqual(self.remote.posts, [('/actions/workflows/collect.yml/dispatches', {'ref': 'main'})])
        self.assertEqual(result['status'], 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')
        self.assertFalse(result['snapshot_freshness_verified'])
        self.assertEqual(self.remote.snapshots, 2)

    def test_original_three_hour_boundary_is_fresh_and_future_is_invalid(self):
        self.remote.snapshot['collected_at_utc'] = '2026-11-10T09:20:00Z'
        self.assertEqual(self.call()['status'], 'SKIP_MARKET_FRESH')
        self.assertEqual(self.remote.posts, [])
        self.remote.snapshot['collected_at_utc'] = '2026-11-10T12:20:00.000001Z'
        self.blocked()

    def test_audit_push_pr_foreign_main_and_wrong_event_never_dispatch(self):
        for field, value in [('GITHUB_EVENT_NAME', 'push'), ('GITHUB_EVENT_NAME', 'pull_request'),
                             ('GITHUB_EVENT_NAME', 'workflow_dispatch'), ('GITHUB_REPOSITORY', 'foreign/repo'),
                             ('GITHUB_REF', 'refs/heads/feature')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.call(env={**ENV, field: value})
        for field, value in [('action', 'requested'), ('repository', {'full_name': 'foreign/repo'})]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.call(event={**self.event, field: value})
        self.assertEqual(self.remote.posts, [])

    def test_other_source_untrusted_and_changed_parent_identity_never_dispatch(self):
        for field, value in [('id', True), ('run_attempt', 2), ('head_sha', 'c'*40),
                             ('path', '.github/workflows/collect.yml'), ('head_branch', 'feature'),
                             ('event', 'pull_request'), ('head_repository', {'full_name': 'foreign/repo'})]:
            with self.subTest(field=field):
                saved = self.remote.run
                self.remote.run = {**saved, field: value}
                self.blocked()
                self.remote.run = saved

    def test_forged_pr_source_event_cannot_borrow_real_trusted_parent_identity(self):
        self.event['workflow_run']['event'] = 'pull_request'
        self.blocked()

    def test_complete_parent_job_pagination_is_required(self):
        first = [dict(self.remote.jobs[0], id=1000+n, steps=[]) for n in range(100)]
        self.remote.job_pages = [{'total_count': 101, 'jobs': first},
                                 {'total_count': 101, 'jobs': self.remote.jobs}]
        self.assertEqual(self.call()['status'], 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')
        self.assertEqual(len(self.remote.posts), 1)
        self.remote.posts.clear()
        self.remote.job_pages[1]['total_count'] = 102
        self.blocked()

    def test_backwards_or_non_utc_actual_clock_blocks(self):
        self.remote.change = lambda r, p: setattr(r, 'now', AT-timedelta(minutes=1)) if r.snapshots == 2 else None
        self.blocked()
        self.remote.change = None
        self.remote.now = AT.replace(tzinfo=None)
        self.blocked()

    def test_guard_or_persist_failed_cancelled_missing_or_duplicated_blocks(self):
        saved = copy.deepcopy(self.remote.jobs)
        for conclusion in ('failure', 'cancelled', 'skipped'):
            for n in (0, 1):
                with self.subTest(conclusion=conclusion, n=n):
                    self.remote.jobs = copy.deepcopy(saved)
                    self.remote.jobs[0]['steps'][n]['conclusion'] = conclusion
                    self.blocked()
        self.remote.jobs = copy.deepcopy(saved)
        self.remote.jobs[0]['steps'].append(copy.deepcopy(saved[0]['steps'][0]))
        self.blocked()

    def test_incomplete_foreign_or_running_actual_jobs_block(self):
        for value in ({'total_count': 2, 'jobs': self.remote.jobs},
                      {'total_count': 0, 'jobs': []}, {'total_count': True, 'jobs': self.remote.jobs}):
            self.remote.bad_jobs = value
            self.blocked()
        self.remote.bad_jobs = None
        for field, value in [('run_id', 999), ('status', 'in_progress'), ('steps', {})]:
            saved = self.remote.jobs[0].copy()
            self.remote.jobs[0][field] = value
            self.blocked()
            self.remote.jobs[0] = saved

    def test_all_five_active_states_and_transitioned_completed_block(self):
        states = ('pending', 'queued', 'requested', 'waiting', 'in_progress')
        for n, state in enumerate(states):
            with self.subTest(state=state):
                self.remote.calls.clear()
                self.remote.active = self.remote.collect(status=state)
                self.assertEqual(self.call()['status'], 'DEFERRED_ACTIVE_COLLECT')
                self.assertEqual(self.remote.posts, [])
                seen = [parse_qs(urlsplit(p).query)['status'][0] for p in self.remote.calls
                        if p.startswith('/actions/workflows/collect.yml/runs?') and 'status=' in p]
                self.assertEqual(seen, list(states[:n+1]))

    def test_transitioned_completed_row_conservatively_defers(self):
        self.remote.active = self.remote.collect(status='completed')
        self.remote.transitioned_completed = True
        self.assertEqual(self.call()['status'], 'DEFERRED_ACTIVE_COLLECT')
        self.assertEqual(self.remote.posts, [])

    def test_workflow_gate_accepts_bare_and_ref_suffix_only_for_main_hourly_completion(self):
        lines = (Path(__file__).parent/'.github/workflows/doge_engineering_completion.yml').read_text().splitlines()
        title = '      - name: Recover stale required market snapshot from trusted hourly completion'
        expression = lines[lines.index(title)+1].strip().removeprefix('if: ')
        expression = expression.replace('&&', 'and').replace('||', 'or')
        def accepts(path, ref='refs/heads/main', event_name='workflow_run'):
            github = types.SimpleNamespace(ref=ref, event_name=event_name,
                event=types.SimpleNamespace(workflow_run=types.SimpleNamespace(path=path)))
            return eval(expression, {'__builtins__': {}, 'github': github,
                                     'startsWith': lambda text, prefix: text.startswith(prefix)})
        self.assertTrue(accepts(SOURCE))
        self.assertTrue(accepts(SOURCE+'@refs/heads/main'))
        self.assertFalse(accepts(SOURCE+'x@refs/heads/main'))
        self.assertFalse(accepts('.github/workflows/collect.yml'))
        self.assertFalse(accepts(SOURCE, ref='refs/heads/feature'))
        for event_name in ('push', 'pull_request', 'workflow_dispatch'):
            self.assertFalse(accepts(SOURCE, event_name=event_name))

    def test_queue_count_page_disagreement_has_at_most_two_views_then_blocks(self):
        self.remote.bad_queue = {'total_count': 1, 'workflow_runs': []}
        self.blocked()
        self.assertEqual(self.remote.queue_views, 2)

    def test_other_necessary_collectors_active_in_any_state_block(self):
        for workflow in ('okx_hourly_extreme_history.yml', 'doge_history.yml'):
            states = ('pending', 'queued', 'requested', 'waiting', 'in_progress')
            for n, state in enumerate(states):
                with self.subTest(workflow=workflow, state=state):
                    self.remote.calls.clear()
                    self.remote.active_by_workflow = {workflow: self.remote.collect(
                        path='.github/workflows/'+workflow, status=state)}
                    result = self.call()
                    self.assertEqual(result['status'], 'DEFERRED_ACTIVE_COLLECT')
                    self.assertEqual(result['active_workflow'], workflow)
                    self.assertEqual(self.remote.posts, [])
                    seen = [parse_qs(urlsplit(p).query)['status'][0] for p in self.remote.calls
                            if p.startswith('/actions/workflows/'+workflow+'/runs?') and 'status=' in p]
                    self.assertEqual(seen, list(states[:n+1]))

    def test_other_necessary_collector_arriving_in_final_view_blocks(self):
        def change(remote, path):
            if remote.snapshots == 2:
                remote.active_by_workflow['doge_history.yml'] = remote.collect(
                    path='.github/workflows/doge_history.yml', status='in_progress')
        self.remote.change = change
        self.assertEqual(self.call()['status'], 'DEFERRED_ACTIVE_COLLECT')
        self.assertEqual(self.remote.posts, [])

    def test_queue_foreign_identity_invalid_schema_or_count_blocks(self):
        for row in (self.remote.collect(status='queued', repository={'full_name': 'foreign/repo'}),
                    self.remote.collect(status='queued', path='.github/workflows/wrong.yml'),
                    self.remote.collect(status='queued', id=True), self.remote.collect(status='queued', head_branch='feature')):
            self.remote.active = row
            self.blocked()
        self.remote.active = None
        for bad in ({'total_count': True, 'workflow_runs': []},
                    {'total_count': 0, 'workflow_runs': {}}, {'total_count': -1, 'workflow_runs': []}):
            self.remote.bad_queue = bad
            self.blocked()

    def test_any_current_hour_dispatch_attempt_blocks_including_failed_and_cancelled(self):
        for outcome in ('success', 'failure', 'cancelled', 'timed_out', 'skipped', None):
            self.remote.prior = self.remote.collect(conclusion=outcome)
            self.assertEqual(self.call()['status'], 'ALREADY_ATTEMPTED_THIS_UTC_HOUR')
            self.assertEqual(self.remote.posts, [])

    def test_invalid_or_incomplete_attempt_listing_blocks(self):
        for row in (self.remote.collect(event='schedule'),
                    self.remote.collect(created_at='2026-11-10T11:59:59Z'),
                    self.remote.collect(created_at='2026-11-10T12:21:00Z'),
                    self.remote.collect(head_repository={'full_name': 'foreign/repo'})):
            self.remote.prior = row
            self.blocked()
        self.remote.prior = None
        self.remote.bad_attempt = {'total_count': 1, 'workflow_runs': []}
        self.blocked()

    def test_malformed_duplicate_nonfinite_timezone_or_status_snapshot_blocks(self):
        for snapshot in (b'{"status":"ok","status":"ok"}', b'{bad',
                         {'status': 'ok', 'collected_at_utc': '2026-11-10T08:00:00'},
                         {'status': 'ok', 'collected_at_utc': '2026-11-10T09:00:00+01:00'},
                         {'status': 'error', 'collected_at_utc': '2026-11-10T08:00:00Z'},
                         {'status': 'ok', 'collected_at_utc': '2026-11-10T08:00:00Z', 'invalid': float('nan')}):
            self.remote.snapshot = snapshot
            self.blocked()

    def test_blob_hash_size_path_and_encoding_mismatch_block(self):
        for bad in ({'sha': '0'*40}, {'size': 1}, {'path': 'foreign.json'}, {'encoding': 'none'}):
            self.remote.bad_file = bad
            self.blocked()

    def test_newly_fresh_snapshot_blocks_before_post(self):
        def change(remote, path):
            if remote.snapshots == 1:
                remote.snapshot = {'status': 'ok', 'collected_at_utc': '2026-11-10T12:19:00Z'}
        self.remote.change = change
        self.assertEqual(self.call()['status'], 'SKIP_MARKET_FRESH')
        self.assertEqual(self.remote.posts, [])

    def test_active_collect_arrives_during_final_snapshot_read_blocks(self):
        self.remote.change = lambda r, p: setattr(r, 'active', r.collect(status='queued')) if r.snapshots == 2 else None
        self.assertEqual(self.call()['status'], 'DEFERRED_ACTIVE_COLLECT')
        self.assertEqual(self.remote.posts, [])

    def test_dispatch_attempt_arrives_during_final_queue_read_blocks(self):
        self.remote.change = lambda r, p: setattr(r, 'prior', r.collect()) if r.queue_views == 2 else None
        self.assertEqual(self.call()['status'], 'ALREADY_ATTEMPTED_THIS_UTC_HOUR')
        self.assertEqual(self.remote.posts, [])

    def test_hour_rollover_during_final_reads_blocks_without_new_hour_query(self):
        self.remote.change = lambda r, p: setattr(r, 'now', AT.replace(hour=13)) if r.snapshots == 2 else None
        self.assertEqual(self.call()['status'], 'DEFERRED_UTC_HOUR_CHANGED')
        self.assertEqual(self.remote.posts, [])

    def test_main_change_during_final_queue_blocks(self):
        self.remote.change = lambda r, p: setattr(r, 'head', 'c'*40) if r.queue_views == 2 else None
        self.assertEqual(self.call()['status'], 'DEFERRED_MAIN_CHANGED')
        self.assertEqual(self.remote.posts, [])

    def test_shared_budget_exhausted_during_any_read_blocks(self):
        self.remote.change = lambda r, p: setattr(r, 'spent', 40.0) if r.snapshots == 2 else None
        self.blocked(TimeoutError)

    def test_original_real_transport_accumulates_all_gets_without_resetting_budget(self):
        m = self.engine()
        seconds, requests = [0.0], []
        remote = self.remote
        class Response:
            status = 200
            def __init__(self, value):
                self.raw = json.dumps(value).encode()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read1(self, size):
                raw, self.raw = self.raw, b''
                if raw:
                    seconds[0] += 1.5
                return raw
        def open_response(request, timeout):
            requests.append((request.get_method(), timeout))
            if request.get_method() != 'GET':
                raise AssertionError('expired shared transport must not POST')
            path = request.full_url.removeprefix('https://api.github.com/repos/'+REPO)
            return Response(remote.api(path))
        client = m.transport.GitHubClient()
        with patch.object(m.transport, 'urlopen', side_effect=open_response), \
                patch.object(m.transport.time, 'monotonic', side_effect=lambda: seconds[0]), \
                patch.dict(os.environ, {'GITHUB_TOKEN': 'fixture-only-no-live-token'}):
            with self.assertRaises(TimeoutError):
                m.recover(self.event, client=client, clock=lambda: AT, environ=ENV)
        self.assertGreaterEqual(client.spent, 40)
        self.assertGreater(len(requests), 1)
        self.assertTrue(all(method == 'GET' for method, timeout in requests))
        self.assertLess(requests[-1][1], requests[0][1])
        self.assertEqual(remote.posts, [])

    def test_unknown_post_is_preserved_and_never_retried_within_callback(self):
        self.remote.post_error = TimeoutError('POST outcome unknown')
        with self.assertRaisesRegex(TimeoutError, 'POST outcome unknown'):
            self.call()
        self.assertEqual(len(self.remote.posts), 1)


if __name__ == '__main__':
    unittest.main()

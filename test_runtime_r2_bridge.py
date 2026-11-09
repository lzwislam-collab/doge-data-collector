import copy
import base64
import hashlib
import importlib
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
import phase2_forward_runtime_v2 as frozen
import test_phase2_forward_v2 as frames
import test_runtime_custody_bridge as remote_fixture


class Remote(remote_fixture.Remote):
    def __init__(self, now):
        super().__init__()
        self.clock = now
        self.runs = []
        self.reads = {}
        self.files['config/phase2_forward_v2.json'] = frozen.f.encode(frozen.f.policy())
        self.files['config/phase2_forward_registration_v2.json'] = remote_fixture.enc({
            'sha256_lf': {k: remote_fixture.sha(v) for k, v in self.files.items()}})
        self.source = self.csv(frames.Forward().frame(now))
        self.files[frozen.SOURCE] = self.source
        self.add_event('canary', datetime(2026, 10, 20, 0, 1, tzinfo=timezone.utc))

    def csv(self, frame):
        lines = ['symbol,open_utc_ms,close,volume_base,first_ingested_utc,source\n']
        for symbol in ('DOGE-USDT', 'BTC-USDT'):
            for i, stamp in enumerate(frame['times']):
                seen = datetime.fromtimestamp(frame[symbol]['seen'][i]/1000, timezone.utc).isoformat()
                lines.append(f'{symbol},{stamp},{frame[symbol]["close"][i]},{frame[symbol]["volume"][i]},{seen},OKX history-candles 1H confirmed\n')
        return ''.join(lines).encode()

    def add_event(self, cohort, issued):
        p = frozen.f.policy()
        slot = issued if cohort == 'canary' else issued.replace(minute=0, second=0, microsecond=0)
        horizon = p['canary_horizon_seconds'] if cohort == 'canary' else p['horizon_seconds']
        key = str(int(slot.timestamp()))+'_'+cohort
        event = {'event_id': key, 'cohort': cohort, 'protocol_id': p['protocol_id'],
                 'policy_sha256': frozen.f.digest(p), 'issued_at_utc': issued.isoformat(),
                 'slot_utc': slot.isoformat(), 'target_utc': (issued+timedelta(seconds=horizon)).isoformat(),
                 'horizon_seconds': horizon, 'included_in_formal_statistics': cohort == 'formal',
                 'registration_run_id': 1, 'source_run_id': 2}
        path = frozen.f.BASE+'/events/'+key+'.json'
        self.files[path] = frozen.f.encode(event)
        return path, event

    def audit(self, rid, head, start, end):
        row = {'id': rid, 'head_sha': head, 'head_branch': 'main',
               'repository': {'full_name': frozen.f.old.REPO}, 'path': frozen.f.WORKFLOW,
               'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
               'created_at': start.isoformat(), 'audit_job': {'id': rid*10, 'run_id': rid,
               'name': 'audit', 'status': 'completed', 'conclusion': 'success',
               'started_at': start.isoformat(), 'completed_at': end.isoformat()}}
        self.runs.append(row)
        return row

    def api(self, path):
        u = urlsplit(path)
        q = parse_qs(u.query)
        if u.path == '/actions/workflows/phase2_forward_audit_v2.yml/runs' and not q.get('status'):
            return {'workflow_runs': self.runs}
        if u.path.startswith('/actions/runs/'):
            rid = int(u.path.split('/')[3])
            row = next((row for row in self.runs if row['id'] == rid), None)
            if row is None:
                row = self.audit(rid, format(rid, '040x'), self.clock-timedelta(days=1),
                                 self.clock-timedelta(hours=23))
            return {'jobs': [row['audit_job']]} if u.path.endswith('/jobs') else row
        if u.path == '/contents/data/hourly_research':
            self.assert_ref(q)
            return [self.meta(frozen.SOURCE, self.files[frozen.SOURCE])]
        return super().api(path)

    def request(self, path, payload=None):
        if payload is None:
            return self.api(path)
        self.posts.append((path, payload))
        return 204


class Routing(unittest.TestCase):
    def setUp(self):
        self.m = importlib.import_module('runtime_r2_bridge')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 9, 14, 10, tzinfo=timezone.utc)
        self.remote = Remote(self.now)
        source = self.root/frozen.SOURCE
        source.parent.mkdir(parents=True)
        source.write_bytes(self.remote.source)
        self.state = {}
        self.publication = {'status': 'verified_remote_readback', 'head': '1'*40}
        self.env = patch.dict(os.environ, {'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': frozen.f.old.REPO})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.fixed = patch.object(self.m, '_fixed_files', return_value={'registered': b'fixed original bytes'})
        self.fixed.start()
        self.addCleanup(self.fixed.stop)
        self.reader = patch.object(self.m, '_server_bytes', side_effect=lambda root, client, state, head, path: self.remote.reads.get((head, path), b''))
        self.reader.start()
        self.addCleanup(self.reader.stop)

    def call(self):
        return self.m.route_r2(self.root, self.publication, self.state,
                              clock=lambda: self.remote.clock, client=self.remote)

    def source_witness(self):
        self.remote.audit(2, 'b'*40, self.now-timedelta(minutes=2), self.now-timedelta(minutes=1))
        self.remote.reads[('b'*40, frozen.SOURCE)] = self.remote.source

    def test_missing_source_requests_real_audit_once_without_pretending_issuer_ran(self):
        first = self.call()
        second = self.call()
        self.assertEqual(len(self.remote.posts), 1)

        self.assertTrue(self.remote.posts[0][0].endswith('/phase2_forward_audit_v2.yml/dispatches'))
        self.assertEqual(first['status'], 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')
        self.assertEqual(second['status'], 'ALREADY_ATTEMPTED_THIS_R2_CONTEXT')
        self.assertFalse(first['witness_or_forecast_verified'])

    def test_real_source_witness_enables_original_publish_payload(self):
        self.source_witness()
        result = self.call()
        self.assertEqual(result['purpose'], 'issue')
        self.assertEqual(self.remote.posts[0][1], {'ref': 'main', 'inputs': {'mode': 'publish'}})

    def test_past_issue_window_noop_preserves_miss(self):
        self.remote.clock = self.now.replace(minute=45)
        self.assertEqual(self.call()['status'], 'SKIP_NO_CURRENT_R2_OBLIGATION')
        self.assertFalse(self.remote.posts)

    def test_no_or_multiple_existing_canaries_never_dispatches_target_mode(self):
        self.remote.clock = self.now.replace(hour=15)
        self.remote.add_event('formal', self.now.replace(minute=5))
        canary = next(path for path in self.remote.files if path.endswith('_canary.json'))
        del self.remote.files[canary]
        with self.assertRaises(ValueError):
            self.call()
        self.assertFalse(self.remote.posts)
        self.remote.add_event('canary', datetime(2026, 10, 20, 0, 1, tzinfo=timezone.utc))
        self.remote.add_event('canary', datetime(2026, 10, 20, 0, 2, tzinfo=timezone.utc))
        with self.assertRaises(ValueError):
            self.call()
        self.assertFalse(self.remote.posts)

    def test_true_prediction_witness_and_unique_old_canary_route_target_without_new_issue(self):
        self.remote.clock = self.now.replace(hour=15)
        path, event = self.remote.add_event('formal', self.now.replace(minute=5))
        self.remote.audit(3, 'c'*40, self.now.replace(minute=6), self.now.replace(minute=8))
        self.remote.reads[('c'*40, 'registered')] = b'fixed original bytes'
        self.remote.reads[('c'*40, path)] = self.remote.files[path]
        result = self.call()
        self.assertEqual(result['purpose'], 'target')
        self.assertEqual(self.remote.posts[0][1], {'ref': 'main', 'inputs': {'mode': 'canary'}})
        self.assertEqual(result['existing_canary_count'], 1)

    def test_missing_pretarget_witness_never_creates_late_proof_or_target_job(self):
        self.remote.clock = self.now.replace(hour=15)
        self.remote.add_event('formal', self.now.replace(minute=5))
        result = self.call()
        self.assertEqual(result['status'], 'WAIT_MISSING_ORIGINAL_PREDICTION_WITNESS')
        self.assertFalse(self.remote.posts)

    def test_active_publisher_is_deferred_and_unknown_queue_fails_closed(self):
        self.source_witness()
        for state in self.m.STATES:
            with self.subTest(state=state):
                self.remote.active = {'id': 41, 'head_branch': 'main', 'path': '.github/workflows/phase2_forward_live_v2.yml', 'status': state}
                self.assertEqual(self.call()['status'], 'DEFERRED_ACTIVE_R2_WORKFLOW')
                self.assertFalse(self.remote.posts)
        self.remote.active = None
        self.remote.bad_queue = {'total_count': 1, 'workflow_runs': []}
        with self.assertRaises(ValueError):
            self.call()
        self.assertFalse(self.remote.posts)

    def test_unpublished_source_and_unknown_post_outcome_do_not_retry(self):
        (self.root/frozen.SOURCE).write_bytes(self.remote.source+b'changed')
        self.assertEqual(self.call()['status'], 'WAIT_SOURCE_PUBLICATION')
        self.assertFalse(self.remote.posts)
        (self.root/frozen.SOURCE).write_bytes(self.remote.source)
        def unknown(path, payload=None):
            if payload is None:
                return self.remote.api(path)
            self.remote.posts.append((path, payload))
            raise TimeoutError('POST outcome unknown')
        self.remote.request = unknown
        with self.assertRaises(TimeoutError):
            self.call()
        self.assertEqual(self.call()['status'], 'ALREADY_ATTEMPTED_THIS_R2_CONTEXT')
        self.assertEqual(len(self.remote.posts), 1)

    def test_window_closing_during_queue_checks_prevents_post(self):
        self.source_witness()
        real = self.remote.api
        def advancing(path):
            result = real(path)
            if '/actions/workflows/phase2_forward_live_v2.yml/runs?' in path:
                self.remote.clock = self.now.replace(hour=15)
            return result
        self.remote.api = advancing
        self.assertEqual(self.call()['status'], 'DEFERRED_R2_STATE_CHANGED_BEFORE_POST')
        self.assertFalse(self.remote.posts)

    def test_transport_or_corrupt_witness_bytes_are_not_misclassified_as_missing_proof(self):
        self.source_witness()
        for error in (TimeoutError('source read budget'), ValueError('blob mismatch')):
            with self.subTest(error=type(error).__name__), \
                    patch.object(self.m, '_server_bytes', side_effect=error):
                with self.assertRaises(type(error)):
                    self.call()
                self.assertFalse(self.remote.posts)


    def test_final_proof_io_closing_issue_or_source_audit_window_prevents_post(self):
        for witnessed in (False, True):
            with self.subTest(witnessed=witnessed):
                self.state.clear()
                self.remote.runs.clear()
                self.remote.posts.clear()
                self.remote.clock = self.now.replace(minute=44, second=50)
                if witnessed:
                    self.source_witness()
                real = self.remote.api
                calls = [0]
                def advancing(path):
                    result = real(path)
                    if path.startswith('/actions/workflows/'+self.m.AUDIT+'/runs?') and 'status=' not in path:
                        calls[0] += 1
                        if calls[0] == 2:
                            self.remote.clock = self.now.replace(minute=45, second=10)
                    return result
                self.remote.api = advancing
                result = self.call()
                self.remote.api = real
                self.assertEqual(calls[0], 2)
                self.assertEqual(result['status'], 'DEFERRED_R2_WINDOW_CLOSED_BEFORE_POST')
                self.assertFalse(self.remote.posts)
                self.assertFalse(self.state.get('r2_dispatch_attempts'))

    def test_final_proof_io_closing_target_or_prediction_window_prevents_post(self):
        for purpose in ('target', 'prediction_witness', 'binding'):
            with self.subTest(purpose=purpose):
                self.state.clear()
                self.remote.runs.clear()
                self.remote.posts.clear()
                path, event = self.remote.add_event('formal', self.now.replace(minute=5))
                self.remote.clock = self.now.replace(hour=15, minute=29 if purpose == 'target' else 4, second=50)
                if purpose != 'prediction_witness':
                    self.remote.audit(3, 'c'*40, self.now.replace(minute=6), self.now.replace(minute=8))
                    self.remote.reads[('c'*40, 'registered')] = b'fixed original bytes'
                    self.remote.reads[('c'*40, path)] = self.remote.files[path]
                real = self.remote.api
                calls = [0]
                def advancing(url):
                    result = real(url)
                    if url.startswith('/actions/workflows/'+self.m.AUDIT+'/runs?') and 'status=' not in url:
                        calls[0] += 1
                        if calls[0] == 2:
                            self.remote.clock = self.now.replace(hour=15, minute=30 if purpose == 'target' else 5, second=10)
                    return result
                self.remote.api = advancing
                result = self.call()
                self.remote.api = real
                self.assertEqual(calls[0], 2)
                self.assertEqual(result['status'], 'DEFERRED_R2_WINDOW_CLOSED_BEFORE_POST')
                self.assertFalse(self.remote.posts)
                self.assertFalse(self.state.get('r2_dispatch_attempts'))

    def test_remote_only_source_uses_pinned_public_bytes_without_parent_claim(self):
        self.publication = None
        self.source_witness()
        self.remote.reads[(self.remote.head, frozen.SOURCE)] = self.remote.source
        (self.root/frozen.SOURCE).write_bytes(b'unpublished local source')
        result = self.call()
        self.assertEqual(result['purpose'], 'issue')
        self.assertTrue(result['remote_pinned_state_only'])
        self.assertFalse(result['parent_publication_verified'])
        self.assertFalse(result['witness_or_forecast_verified'])
        self.assertEqual(self.remote.posts[0][1], {'ref': 'main', 'inputs': {'mode': 'publish'}})

    def test_remote_only_target_still_requires_actual_original_witness(self):
        self.publication = None
        self.remote.clock = self.now.replace(hour=15)
        path, event = self.remote.add_event('formal', self.now.replace(minute=5))
        with patch.object(self.m, '_remote_fixed_files', return_value={'registered': b'fixed original bytes'}):
            self.assertEqual(self.call()['status'], 'WAIT_MISSING_ORIGINAL_PREDICTION_WITNESS')
            self.assertFalse(self.remote.posts)
            self.remote.audit(3, 'c'*40, self.now.replace(minute=6), self.now.replace(minute=8))
            self.remote.reads[('c'*40, 'registered')] = b'fixed original bytes'
            self.remote.reads[('c'*40, path)] = self.remote.files[path]
            result = self.call()
        self.assertEqual(result['purpose'], 'target')
        self.assertTrue(result['remote_pinned_state_only'])
        self.assertFalse(result['parent_publication_verified'])
        self.assertEqual(self.remote.posts[0][1], {'ref': 'main', 'inputs': {'mode': 'canary'}})

    def test_explicit_malformed_publication_still_rejected(self):
        for publication in ({}, {'status': 'pushed', 'head': 'a'*40},
                            {'status': 'verified_remote_readback', 'head': 'bad'}, False):
            with self.subTest(publication=publication):
                self.publication = publication
                with self.assertRaises(ValueError):
                    self.call()
                self.assertFalse(self.remote.posts)


class WitnessBytes(unittest.TestCase):
    def test_missing_pinned_file_is_not_negatively_cached_after_provider_recovers(self):
        from urllib.error import HTTPError
        m = importlib.import_module('runtime_r2_bridge')
        raw = b'recovered exact bytes'
        class Client:
            calls = 0
            def api(self, path):
                self.calls += 1
                if self.calls == 1:
                    raise HTTPError(path, 404, 'transient', {}, None)
                return {'type': 'file', 'path': frozen.SOURCE, 'sha': remote_fixture.blob(raw),
                        'size': len(raw), 'encoding': 'base64', 'content': base64.b64encode(raw).decode()}
        client, state = Client(), {}
        self.assertEqual(m._server_bytes(Path('.'), client, state, 'a'*40, frozen.SOURCE), b'')
        self.assertEqual(m._server_bytes(Path('.'), client, state, 'a'*40, frozen.SOURCE), raw)
        self.assertEqual(m._server_bytes(Path('.'), client, state, 'a'*40, frozen.SOURCE), raw)
        self.assertEqual(client.calls, 2)

    def test_remote_manifest_checks_every_local_registered_byte_with_frozen_validator(self):
        m = importlib.import_module('runtime_r2_bridge')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path in frozen.FIXED:
                target = root/path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(('fixture for '+path+'\n').encode())
            expected = {path: frozen.frozen.file_bytes(root, path) for path in frozen.FIXED}
            registration = frozen.f.encode({'sha256_lf': {p: frozen.f.old.sha(raw) for p, raw in expected.items()}})
            with patch.object(m, '_server_bytes', return_value=registration):
                result = m._remote_fixed_files(root, object(), {}, 'a'*40)
                self.assertEqual(result, {**expected, frozen.f.REGISTRATION: registration})
                changed = root/next(iter(frozen.FIXED))
                changed.write_bytes(changed.read_bytes()+b'changed')
                with self.assertRaisesRegex(ValueError, 'registered source changed'):
                    m._remote_fixed_files(root, object(), {}, 'a'*40)

    def test_actual_transport_decode_and_blob_hash_including_large_file_encoding(self):
        m = importlib.import_module('runtime_r2_bridge')
        raw = b'actual source bytes\r\n'
        sha = remote_fixture.blob(raw)
        class Client:
            def __init__(self, large=False):
                self.large = large
                self.calls = []
            def api(self, path):
                self.calls.append(path)
                if path.startswith('/git/blobs/'):
                    return {'sha': sha, 'size': len(raw), 'encoding': 'base64', 'content': base64.b64encode(raw).decode()}
                return {'type': 'file', 'path': frozen.SOURCE, 'sha': sha, 'size': len(raw),
                        'encoding': 'none' if self.large else 'base64',
                        'content': '' if self.large else base64.b64encode(raw).decode()}
        for large in (False, True):
            with self.subTest(large=large):
                client, state = Client(large), {}
                self.assertEqual(m._server_bytes(Path('.'), client, state, 'a'*40, frozen.SOURCE), raw)
                self.assertEqual(m._server_bytes(Path('.'), client, state, 'a'*40, frozen.SOURCE), raw)
                self.assertEqual(len(client.calls), 2 if large else 1)
        client = Client()
        good = client.api
        client.api = lambda path: {**good(path), 'sha': '0'*40}
        with self.assertRaises(ValueError):
            m._server_bytes(Path('.'), client, {}, 'a'*40, frozen.SOURCE)



class FrozenNoNewCanaryBranch(unittest.TestCase):
    """Run body, event replay, clocks, scoring and append are actual frozen code.

    The guard return selects genuine stored models as a fixture; this test does
    not replay full model training. Actual source/prediction publication
    predicates are not mocked. Full immutable guard also requires repository CI.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p = frozen.f.policy()
        self.model = json.loads((Path(__file__).parent/frozen.f.MODEL).read_bytes())
        self.package = json.loads((Path(__file__).parent/frozen.f.old.PACKAGE).read_bytes())
        dest = self.root/frozen.f.old.PACKAGE
        dest.parent.mkdir(parents=True)
        dest.write_bytes(frozen.f.encode(self.package))
        self.fx = importlib.import_module('test_learned_shadow_v1').Shadow()
        self.issued = datetime(2026, 10, 20, 0, 5, tzinfo=timezone.utc)
        self.now = self.issued+timedelta(hours=1, seconds=2)
        self.fixed = {'registered_original_fixture': b'original registered bytes'}
        self.events = []
        self.source = Remote(self.issued).csv(frames.Forward().frame(self.issued))
        self.stored = {}
        self.runs = []
        self.stack = importlib.import_module('contextlib').ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(frozen, 'ROOT', self.root))
        self.stack.enter_context(patch.object(frozen.f, 'ROOT', self.root))
        for cohort, issued in (('canary', self.issued.replace(minute=1)), ('formal', self.issued)):
            frame = frames.Forward().frame(issued)
            bx = frozen.f.old.causal_features(frozen.f.benchmark_frame(frame), issued)
            benchmark = self.package['models']['1']['full']
            bp = frozen.prior.predict(benchmark, [[bx[j] for j in benchmark['columns']]])[0]
            event = frozen.f.issue(self.p, self.model, frame, self.packet(issued), issued, bp, cohort)
            event.update(input_source_commit='b'*40, input_source_sha256=hashlib.sha256(self.source).hexdigest(),
                         registration_run_id=1, source_run_id=2)
            self.events.append(event)
            path = self.root/frozen.f.BASE/'events'/(event['event_id']+'.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(frozen.f.encode(event))
            self.stored[('c'*40, path.relative_to(self.root).as_posix())] = path.read_bytes()
        self.formal = self.events[-1]
        self.audit(1, 'a'*40, self.issued.replace(hour=0, minute=0)-timedelta(minutes=3),
                   self.issued.replace(hour=0, minute=0)-timedelta(minutes=1))
        self.audit(2, 'b'*40, self.issued.replace(minute=0, second=1), self.issued.replace(minute=0, second=30))
        self.audit(3, 'c'*40, self.issued+timedelta(minutes=1), self.issued+timedelta(minutes=3))
        for head in ('a'*40, 'c'*40):
            self.stored.update({(head, name): raw for name, raw in self.fixed.items()})
        self.stored[('b'*40, frozen.SOURCE)] = self.source
        for args in (('init',), ('config', 'user.name', 'Fixture'),
                     ('config', 'user.email', 'fixture@example.invalid'), ('add', '.'),
                     ('commit', '-m', 'immutable issued fixture')):
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)
        self.stack.enter_context(patch.object(frozen, 'guard', return_value=(self.p, self.model, self.package, self.fixed)))
        self.stack.enter_context(patch.object(frozen, 'runs_server', side_effect=lambda *a: self.runs))
        self.stack.enter_context(patch.object(frozen, 'read_at', side_effect=lambda head, name: self.stored.get((head, name), b'')))
        self.stack.enter_context(patch.dict(os.environ, {'GITHUB_REF': 'refs/heads/main'}))
        outer = self
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return outer.now
        self.stack.enter_context(patch.object(frozen, 'datetime', Clock))
        frozen.source_frame.cache_clear()

    def packet(self, at, price=100):
        packet = self.fx.packet(at, price)
        packet['capture_errors'] = {}
        packet['raw_base64'] = {name: base64.b64encode(raw.encode()).decode()
                                for name, raw in packet['raw_text'].items()}
        return packet

    def audit(self, rid, head, start, end):
        self.runs.append({'id': rid, 'head_sha': head, 'head_branch': 'main',
            'repository': {'full_name': frozen.f.old.REPO}, 'path': frozen.f.WORKFLOW,
            'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
            'created_at': start.isoformat(), 'audit_job': {'name': 'audit', 'status': 'completed',
                'conclusion': 'success', 'started_at': start.isoformat(), 'completed_at': end.isoformat()}})

    def test_actual_no_new_canary_branch_scores_formal_and_only_replays_old_issuance(self):
        with patch.object(frozen, 'capture_target', return_value=self.packet(self.now-timedelta(seconds=1), 101)), \
                patch.object(frozen.f, 'issue', wraps=frozen.f.issue) as issue:
            result = frozen.run('canary')
        self.assertEqual(result['new_scores'], [self.formal['event_id']])
        self.assertEqual(result['new_events'], [])
        self.assertEqual(result['errors'], {})
        self.assertEqual(issue.call_count, len(self.events))
        self.assertEqual({call.args[4] for call in issue.call_args_list},
                         {frozen.f.utc(event['issued_at_utc']) for event in self.events})
        self.assertEqual(len(frozen.records('events')), 2)
        self.assertEqual(sum(event['cohort'] == 'canary' for event in frozen.records('events')), 1)
        score = frozen.records('scores')[0]
        path = frozen.f.BASE+'/scores/'+score['event_id']+'.json'
        self.stored[('d'*40, path)] = frozen.f.encode(score)
        self.stored.update({('d'*40, name): raw for name, raw in self.fixed.items()})
        self.audit(4, 'd'*40, self.now, self.now+timedelta(minutes=2))
        qualified, _ = frozen.publication(self.p, [self.formal], [score], self.fixed, self.runs)
        self.assertEqual([row['event_id'] for row in qualified], [self.formal['event_id']])
        with patch.object(frozen, 'capture_target') as capture:
            self.assertEqual(frozen.run('canary')['new_scores'], [])
        capture.assert_not_called()

    def test_actual_missing_prediction_witness_or_expired_target_never_scores(self):
        key = ('c'*40, frozen.f.BASE+'/events/'+self.formal['event_id']+'.json')
        saved = self.stored.pop(key)
        with patch.object(frozen, 'capture_target') as capture:
            self.assertEqual(frozen.run('canary')['new_scores'], [])
        capture.assert_not_called()
        self.stored[key] = saved
        self.now = frozen.f.deadline(self.formal, self.p)
        with patch.object(frozen, 'capture_target') as capture:
            self.assertEqual(frozen.run('canary')['new_scores'], [])
        capture.assert_not_called()

    def test_actual_capture_failure_is_preserved_not_converted_to_success(self):
        with patch.object(frozen, 'capture_target', side_effect=OSError('actual HTTP failed')):
            result = frozen.run('canary')
        self.assertEqual(result['new_scores'], [])
        self.assertIn(self.formal['event_id'], result['errors'])


if __name__ == '__main__':
    unittest.main()

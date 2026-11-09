"""Existing-writer safety when adding prospective issuance and custody hooks."""
import copy
import os
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import runtime_deadline_service as service


NOW = datetime(2026, 11, 9, 14, tzinfo=timezone.utc)
EVENT = 'data/learned_shadow_v1/events/1794232800_1.json'
INTENT = 'data/runtime_deadline_v1/custody_dispatch_attempts/example.json'


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class Adapter:
    def __init__(self, clock, *, smoke=False, fail=None, stop_file=None, open_during_book=False):
        self.clock, self.smoke, self.fail = clock, smoke, fail
        self.stop_file, self.open_during_book = stop_file, open_during_book
        self.calls, self.scored = [], False
        self.last_qualified_book = {'book_sha256': 'a' * 64}

    def state(self):
        return ([{'forecast_id': 'existing', 'horizon_hours': '1',
                  'source_hour_start_utc': NOW.isoformat(),
                  'target_utc': (NOW + timedelta(seconds=2 if self.open_during_book else 3600)).isoformat()}],
                {'existing': {}} if self.scored else {})

    def can_stop(self):
        return True

    def action(self, name):
        self.calls.append(name)
        if self.fail and self.fail[0] == name:
            raise self.fail[1]
        if name == 'source_audit':
            return {'status': 'pending' if self.smoke else 'ready'}
        if name == 'book':
            if self.stop_file:
                self.stop_file.touch()
            if self.open_during_book:
                self.clock.sleep(5)
            return {'mode': 'smoke' if self.smoke else 'capture',
                    'formal_eligibility': not self.smoke}
        if name == 'watch':
            self.scored = True
        if name == 'original18_capture':
            return {'changed_paths': [EVENT], 'requires_publication': True,
                    'request_witness': True, 'errors': {}}
        if name == 'custody_prepare':
            return {'requires_publication': True, 'candidate_count': 1}
        if name == 'publish':
            return {'status': 'verified_remote_readback', 'head': 'b' * 40,
                    'verified_paths': [EVENT, INTENT]}
        return {'status': 'ok'}


class ProspectiveIntegration(unittest.TestCase):
    def cycle(self, adapter, clock):
        return service.cycle(adapter, {'_prospective_enabled': True}, clock)

    def test_new_journals_and_intents_share_the_required_publication_before_post(self):
        clock = Clock()
        adapter = Adapter(clock, smoke=True)
        result = self.cycle(adapter, clock)
        self.assertFalse(result['fatal'])
        self.assertNotIn('preserve_smoke', adapter.calls)
        self.assertLess(adapter.calls.index('original18_capture'), adapter.calls.index('publish'))
        self.assertLess(adapter.calls.index('custody_prepare'), adapter.calls.index('publish'))
        for name in ('original18_witness', 'r2_dispatch', 'custody_dispatch'):
            self.assertLess(adapter.calls.index('publish'), adapter.calls.index(name))
        self.assertEqual(adapter.calls.count('publish'), 1)

    def test_publication_failure_never_dispatches_unread_journals_or_custody(self):
        clock = Clock()
        adapter = Adapter(clock, fail=('publish', ValueError('remote readback failed')))
        result = self.cycle(adapter, clock)
        self.assertTrue(result['fatal'])
        self.assertIn('original18_capture', adapter.calls)
        for name in ('original18_witness', 'r2_dispatch', 'custody_dispatch'):
            self.assertNotIn(name, adapter.calls)

    def test_interrupted_original_writer_stops_before_any_next_writer(self):
        clock = Clock()
        adapter = Adapter(clock, fail=('original18_capture', subprocess.TimeoutExpired('producer', 90)))
        with self.assertRaises(subprocess.TimeoutExpired):
            self.cycle(adapter, clock)
        self.assertNotIn('book', adapter.calls)
        self.assertNotIn('publish', adapter.calls)

    def test_unreadable_original_writer_result_is_fatal_before_other_mutation(self):
        clock = Clock()
        adapter = Adapter(clock, fail=('original18_capture', ValueError('invalid producer result')))
        result = self.cycle(adapter, clock)
        self.assertTrue(result['fatal'])
        self.assertNotIn('book', adapter.calls)
        self.assertNotIn('publish', adapter.calls)

    def test_operational_post_error_is_recorded_and_fresh_due_core_quote_still_runs(self):
        clock = Clock()
        adapter = Adapter(clock, fail=('r2_dispatch', OSError('POST unavailable')),
                          open_during_book=True)
        result = self.cycle(adapter, clock)
        self.assertTrue(any(row['stage'] == 'r2_dispatch' for row in result['errors']))
        self.assertIn('watch', adapter.calls)
        self.assertTrue(adapter.scored)

    def test_default_cycle_keeps_original_action_contract(self):
        clock = Clock()
        adapter = Adapter(clock)
        result = service.cycle(adapter, {}, clock)
        self.assertFalse(result['fatal'])
        self.assertFalse(set(adapter.calls) & {'original18_capture', 'custody_prepare',
                                              'original18_witness', 'r2_dispatch', 'custody_dispatch'})

    def test_archive_stop_never_enables_bridge_even_if_foreground_flag_leaks(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DOGE_PROSPECTIVE_RUNTIME_BRIDGE': '1'}):
            clock = Clock()
            stop = Path(folder) / 'stop'
            adapter = Adapter(clock, stop_file=stop)
            result = service.run(folder, adapter, duration_seconds=60, clock=clock,
                                 sleep=clock.sleep, stop_file=stop, require_qualified_stop=True)
            self.assertTrue(result['qualified_stop_satisfied'])
            self.assertFalse(set(adapter.calls) & {'original18_capture', 'custody_prepare',
                                                  'original18_witness', 'r2_dispatch', 'custody_dispatch'})

    def test_foreground_run_enables_bridge_and_retains_qualified_stop_contract(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DOGE_PROSPECTIVE_RUNTIME_BRIDGE': '1'}):
            clock = Clock()
            adapter = Adapter(clock)
            result = service.run(folder, adapter, duration_seconds=1, clock=clock, sleep=clock.sleep)
            self.assertFalse(result['fatal'])
            self.assertIn('original18_capture', adapter.calls)

    def test_existing_process_owner_bounds_child_and_isolates_parent_output(self):
        calls = []
        module = types.ModuleType('runtime_forward_bridge')
        def capture(root, producer, **kwargs):
            value = producer(90)
            self.assertEqual(value, '{"current":true}')
            return {'errors': {}, 'requires_publication': False, 'request_witness': False}
        module.capture_original18 = capture
        def command(args, **kwargs):
            calls.append((args, kwargs))
            self.assertTrue(Path(kwargs['env']['GITHUB_OUTPUT']).parent.is_dir())
            self.assertNotEqual(kwargs['env']['GITHUB_OUTPUT'], 'parent-output')
            return 'diagnostic line\n{"current":true}\n'
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {'runtime_forward_bridge': module}), \
                patch.dict(os.environ, {'GITHUB_OUTPUT': 'parent-output'}), \
                patch.object(service.runtime_process, 'command', side_effect=command):
            adapter = service.LiveAdapter.__new__(service.LiveAdapter)
            adapter.root = Path(folder)
            adapter.prospective_enabled = True
            result = adapter.action('original18_capture')
        self.assertEqual(calls[0][0], (sys.executable, 'learned_shadow_runtime.py', 'publish'))
        self.assertEqual(calls[0][1]['timeout'], 90)
        self.assertEqual(calls[0][1]['env']['OPENBLAS_NUM_THREADS'], '1')
        self.assertEqual(calls[0][1]['env']['OMP_NUM_THREADS'], '1')
        self.assertEqual(result['errors'], {})

    def test_archive_adapter_does_not_import_or_call_any_prospective_helper(self):
        adapter = service.LiveAdapter.__new__(service.LiveAdapter)
        # Legacy callers may allocate the publisher without new optional fields.
        module = types.ModuleType('runtime_forward_bridge')
        module.capture_original18 = lambda *_: self.fail('archive helper called')
        with patch.dict(sys.modules, {'runtime_forward_bridge': module}):
            for name in ('original18_capture', 'original18_witness', 'custody_prepare',
                         'r2_dispatch', 'custody_dispatch'):
                self.assertEqual(adapter.action(name)['status'], 'DISABLED_OUTSIDE_FOREGROUND')

    def test_direct_archive_publisher_constructor_ignores_leaked_foreground_flag(self):
        worker = types.ModuleType('runtime_audit_worker')
        worker.Auditor = lambda root: object()
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {'runtime_audit_worker': worker}), \
                patch.dict(os.environ, {'DOGE_PROSPECTIVE_RUNTIME_BRIDGE': '1',
                                        'GITHUB_REF': 'refs/heads/main',
                                        'GITHUB_REPOSITORY': service.REPO}):
            adapter = service.LiveAdapter(folder)
            self.assertFalse(adapter.prospective_enabled)

    def test_proven_read_only_custody_outage_keeps_capture_and_records_degradation(self):
        clock = Clock()
        adapter = Adapter(clock, open_during_book=True)
        action = adapter.action
        def observed(name):
            if name == 'custody_prepare':
                adapter.calls.append(name)
                return {'status': 'OBSERVATION_UNAVAILABLE', 'requires_publication': False,
                        'errors': {'observation': 'HTTP 503 before any local mutation'}}
            return action(name)
        adapter.action = observed
        result = self.cycle(adapter, clock)
        self.assertFalse(result['fatal'])
        self.assertIn('book', adapter.calls)
        self.assertIn('watch', adapter.calls)
        self.assertTrue(any(row['stage'] == 'custody_prepare' for row in result['errors']))

    def test_unknown_intent_write_error_remains_fatal(self):
        clock = Clock()
        adapter = Adapter(clock, fail=('custody_prepare', OSError('intent write failed')))
        result = self.cycle(adapter, clock)
        self.assertTrue(result['fatal'])
        self.assertNotIn('book', adapter.calls)

    def test_safe_read_outage_clears_old_dispatch_candidates(self):
        module = types.ModuleType('runtime_custody_bridge')
        class ObservationUnavailable(OSError):
            pass
        module.ObservationUnavailable = ObservationUnavailable
        def unavailable(root, **kwargs):
            raise ObservationUnavailable('HTTP 503 before writes')
        module.prepare = unavailable
        adapter = service.LiveAdapter.__new__(service.LiveAdapter)
        adapter.root = Path('.')
        adapter.prospective_enabled = True
        adapter.prospective_client = types.SimpleNamespace(api=lambda path: None)
        adapter.custody_prepared = {'candidates': ['stale-prior-cycle']}
        with patch.dict(sys.modules, {'runtime_custody_bridge': module}):
            result = adapter.action('custody_prepare')
        self.assertIsNone(adapter.custody_prepared)
        self.assertFalse(result['requires_publication'])
        self.assertTrue(result['errors'])

    def test_emergency_successful_publication_does_not_abandon_prepared_intent(self):
        clock = Clock()
        adapter = Adapter(clock, fail=('source_audit', OSError('audit transport failed')))
        result = self.cycle(adapter, clock)
        self.assertFalse(result['fatal'])
        self.assertIn('publish', adapter.calls)
        for name in ('original18_witness', 'custody_dispatch'):
            self.assertIn(name, adapter.calls)
            self.assertLess(adapter.calls.index('publish'), adapter.calls.index(name))

    def test_a_later_fresh_target_publish_does_not_repeat_same_cycle_dispatch(self):
        clock = Clock()
        adapter = Adapter(clock, open_during_book=True)
        result = self.cycle(adapter, clock)
        self.assertFalse(result['fatal'])
        self.assertEqual(adapter.calls.count('publish'), 2)
        for name in ('original18_witness', 'r2_dispatch', 'custody_dispatch'):
            self.assertEqual(adapter.calls.count(name), 1)

    def test_all_optional_http_work_shares_one_cycle_transport_budget(self):
        custody = types.ModuleType('runtime_custody_bridge')
        forward = types.ModuleType('runtime_forward_bridge')
        owners = []
        class Client:
            def api(self, path): pass
            def post(self, payload, timeout): pass
        custody.GitHubClient = Client
        custody.prepare = lambda root, **kw: (owners.append(kw['api'].__self__) or
                                             {'report': {}, 'candidates': []})
        custody.after_publish = lambda root, prepared, **kw: (owners.extend(
            (kw['api'].__self__, kw['post'].__self__)) or {'status': 'ok'})
        forward.request_original18_witness = lambda *args, **kw: (owners.append(kw['client']) or {})
        forward.route_r2 = lambda *args, **kw: (owners.append(kw['client']) or {})
        adapter = service.LiveAdapter.__new__(service.LiveAdapter)
        adapter.root = Path('.')
        adapter.prospective_enabled = True
        adapter.prospective_state = {}
        adapter.last_verified_publication = {'status': 'verified_remote_readback'}
        with patch.dict(sys.modules, {'runtime_custody_bridge': custody,
                                     'runtime_forward_bridge': forward}):
            adapter.begin_prospective_cycle()
            first = adapter.prospective_client
            for name in ('custody_prepare', 'original18_witness', 'r2_dispatch', 'custody_dispatch'):
                adapter.action(name)
            self.assertTrue(all(owner is first for owner in owners))
            adapter.begin_prospective_cycle()
            self.assertIsNot(adapter.prospective_client, first)
            self.assertIsNone(adapter.custody_prepared)

    def test_due_core_quote_is_persisted_before_auxiliary_client_setup(self):
        clock = Clock()
        adapter = Adapter(clock)
        original_state = adapter.state
        def state():
            rows, scores = original_state()
            rows[0]['target_utc'] = NOW.isoformat()
            return rows, scores
        adapter.state = state
        adapter.begin_prospective_cycle = lambda: adapter.calls.append('bridge_begin')
        self.cycle(adapter, clock)
        self.assertLess(adapter.calls.index('watch'), adapter.calls.index('bridge_begin'))
        self.assertLess(adapter.calls.index('publish'), adapter.calls.index('bridge_begin'))
        self.assertLess(adapter.calls.index('bridge_begin'), adapter.calls.index('r2_dispatch'))

    def test_original_state_and_full_guard_git_reads_are_owned_and_bounded(self):
        calls = []
        def command(args, **kwargs):
            calls.append((args, kwargs))
            if 'original18_state' in args[2]:
                Path(args[3]).write_text('{"policy":{},"events":[],"scores":{},"bindings":{}}', encoding='utf-8')
                return 'ORIGINAL18_STATE_READY\n'
            return 'ORIGINAL18_FULL_GUARD_VERIFIED\n'
        with tempfile.TemporaryDirectory() as folder, patch.object(service.runtime_process, 'command', side_effect=command):
            adapter = service.LiveAdapter.__new__(service.LiveAdapter)
            adapter.root = Path(folder)
            self.assertEqual(adapter.original18_state_owned()['events'], [])
            self.assertEqual(adapter.validate_original18_owned(Path(folder))['status'], 'verified')
        self.assertEqual([kw['timeout'] for _, kw in calls], [60, service.COMMAND_TIMEOUT_SECONDS])
        self.assertTrue(all(kw['env']['OPENBLAS_NUM_THREADS'] == '1' for _, kw in calls))

    def test_full_guard_timeout_is_not_a_cached_success(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(service.runtime_process, 'command',
                side_effect=subprocess.TimeoutExpired('read-only validation', service.COMMAND_TIMEOUT_SECONDS)):
            adapter = service.LiveAdapter.__new__(service.LiveAdapter)
            adapter.root = Path(folder)
            with self.assertRaises(subprocess.TimeoutExpired):
                adapter.validate_original18_owned(Path(folder))


    def test_public_at_most_once_custody_precedes_other_http_consumers(self):
        clock = Clock()
        adapter = Adapter(clock)
        self.cycle(adapter, clock)
        self.assertLess(adapter.calls.index('custody_dispatch'), adapter.calls.index('original18_witness'))
        self.assertLess(adapter.calls.index('custody_dispatch'), adapter.calls.index('r2_dispatch'))

    def test_remote_only_r2_survives_safe_smoke_deferral(self):
        clock = Clock()
        adapter = Adapter(clock, smoke=True)
        original = adapter.action
        def action(name):
            if name == 'original18_capture':
                adapter.calls.append(name)
                return {'errors': {}, 'requires_publication': False, 'request_witness': False}
            if name == 'custody_prepare':
                adapter.calls.append(name)
                return {'requires_publication': False, 'candidate_count': 0}
            return original(name)
        adapter.action = action
        result = self.cycle(adapter, clock)
        self.assertFalse(result['fatal'])
        self.assertIn('preserve_smoke', adapter.calls)
        self.assertNotIn('publish', adapter.calls)
        self.assertEqual(adapter.calls.count('r2_dispatch'), 1)
        self.assertNotIn('original18_witness', adapter.calls)
        self.assertNotIn('custody_dispatch', adapter.calls)


    def test_r2_never_reuses_a_prior_cycle_parent_publication(self):
        module = types.ModuleType('runtime_forward_bridge')
        contexts = []
        module.route_r2 = lambda root, publication, state, **kw: (contexts.append(publication) or {})
        adapter = service.LiveAdapter.__new__(service.LiveAdapter)
        adapter.root = Path('.')
        adapter.prospective_enabled = True
        adapter.last_verified_publication = {'status': 'verified_remote_readback', 'head': 'a'*40}
        adapter.prospective_state = {}
        adapter.prospective_client = object()
        with patch.dict(sys.modules, {'runtime_forward_bridge': module}):
            adapter.action('r2_dispatch')
        self.assertEqual(contexts, [None])


if __name__ == '__main__':
    unittest.main()

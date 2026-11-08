"""Behavior tests for the offline parallel experiment, not performance evidence."""
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

import test_runtime_publication_profile as safety

SCRIPT = safety.SCRIPT


class ParallelProfileTests(unittest.TestCase):
    setUp = safety.ProfilerSafetyTests.setUp
    git = safety.ProfilerSafetyTests.git
    commit = safety.ProfilerSafetyTests.commit
    fixture = safety.ProfilerSafetyTests.fixture

    def require_feature(self):
        self.assertIn('parallel_validation_experiment',
                      inspect.signature(self.driver.run_profile).parameters,
                      'parallel diagnostic execution is not implemented')

    def experiment(self, functions, readback=None):
        self.require_feature()
        fixed = self.fixture()
        checkout = self.base / 'isolated'
        self.driver.prepare_checkout(self.source, fixed, checkout)
        report = self.driver.initial_report(fixed, parallel_validation_experiment=True)
        output = self.base / 'parallel.json'
        names = ['preflight_validate_p2', 'preflight_validate_state',
                 'preflight_pair_guard', 'preflight_pair_history_guard']
        snapshot = lambda: {'core': (checkout / 'data/direction_v42/forward_registry.csv').read_bytes()}
        return fixed, checkout, report, output, list(zip(names, functions)), snapshot

    def execute(self, prepared, readback=lambda: None):
        fixed, checkout, report, output, validators, snapshot = prepared
        return self.driver.parallel_experiment(checkout, self.source, fixed, report,
                                               output, validators, readback, snapshot)

    def test_real_barrier_proves_overlap_and_second_group_keeps_original_order(self):
        # Serializing either group or reordering B breaks the barrier/order assertions.
        barrier = threading.Barrier(2, timeout=2)
        counts = [0] * 4
        events = []
        def validator(index):
            def run():
                counts[index] += 1
                if counts[index] == 2:
                    if index < 2:
                        barrier.wait()
                        time.sleep(.08)
                    events.append(index)
            return run
        prepared = self.experiment([validator(i) for i in range(4)])
        self.execute(prepared)
        trials = prepared[2]['parallel_validation_experiment']['trials']
        self.assertEqual([t['name'] for t in trials], ['serial_before', 'parallel', 'serial_after'])
        self.assertEqual([t['status'] for t in trials], ['completed'] * 3)
        self.assertEqual(counts, [3, 3, 3, 3])
        self.assertEqual([i for i in events if i != 0], [1, 2, 3])
        self.assertLess(trials[1]['validation_wall_seconds'], trials[1]['phase_seconds_sum'])
        self.assertEqual(len(trials[1]['groups']), 2)
        self.assertTrue(all(g['wall_seconds'] >= .08 for g in trials[1]['groups']))
        self.assertEqual([len(t['phases']) for t in trials], [5, 5, 5])

    def test_dual_failure_waits_for_slow_group_and_raises_original_first_exception(self):
        # Returning on first completed future leaves B alive; completion order must not choose B's error.
        counts = [0, 0]
        barrier = threading.Barrier(2, timeout=2)
        slow_finished = threading.Event()
        original = ValueError('first validator failure')
        def p2():
            counts[0] += 1
            if counts[0] == 2:
                barrier.wait()
                time.sleep(.15)
                slow_finished.set()
                raise original
        def state():
            counts[1] += 1
            if counts[1] == 2:
                barrier.wait()
                raise KeyError('later validator failure')
        prepared = self.experiment([p2, state, lambda: None, lambda: None])
        with self.assertRaises(ValueError) as caught:
            self.execute(prepared)
        self.assertIs(caught.exception, original)
        self.assertTrue(slow_finished.is_set())
        self.assertEqual([t['status'] for t in prepared[2]['parallel_validation_experiment']['trials']],
                         ['completed', 'error'])
        self.assertEqual(counts, [2, 2])

    def test_failed_final_report_does_not_replace_original_validation_exception(self):
        # Disk failure during final diagnostics must not replace the validator error/cause.
        original = ValueError('validation failed first')
        def fail():
            raise original
        prepared = self.experiment([fail, lambda: None, lambda: None, lambda: None])
        real_write = self.driver.write_report
        def write(output, report):
            if report['parallel_validation_experiment']['trials'][-1]['status'] == 'error':
                raise OSError('diagnostic disk failure')
            return real_write(output, report)
        with mock.patch.object(self.driver, 'write_report', side_effect=write):
            with self.assertRaises(Exception) as caught:
                self.execute(prepared)
        self.assertIs(caught.exception, original)

    def test_group_entry_clock_failure_cannot_skip_p2_and_report_success(self):
        # An exception before a group's first validator must be caught and fail closed.
        counts = [0] * 4
        def validator(index):
            return lambda: counts.__setitem__(index, counts[index] + 1)
        prepared = self.experiment([validator(i) for i in range(4)])
        real_counter = time.perf_counter
        injected = threading.Event()
        uncaught = []
        def counter():
            if threading.current_thread() is not threading.main_thread() and not injected.is_set():
                injected.set()
                raise OSError('group startup clock')
            return real_counter()
        error = None
        with mock.patch.object(self.driver.time, 'perf_counter', side_effect=counter), \
                mock.patch.object(threading, 'excepthook', side_effect=lambda args: uncaught.append(args.exc_value)):
            try:
                self.execute(prepared)
            except BaseException as exc:
                error = exc
        self.assertIsInstance(error, OSError)
        self.assertEqual(uncaught, [])
        experiment = prepared[2]['parallel_validation_experiment']
        self.assertEqual(experiment['status'], 'error')
        self.assertEqual([t['status'] for t in experiment['trials']], ['completed', 'error'])
        self.assertTrue(all(g['status'] != 'running' for g in experiment['trials'][-1]['groups']))
        self.assertLess(counts[0], 3)

    def test_real_validator_error_takes_priority_over_other_group_diagnostic_error(self):
        # A successful P2's report failure cannot replace the actual state validator failure.
        counts = [0, 0]
        barrier = threading.Barrier(2, timeout=2)
        p2_thread = []
        original = ValueError('real state validation failed')
        def p2():
            counts[0] += 1
            if counts[0] == 2:
                p2_thread.append(threading.get_ident())
                barrier.wait()
        def state():
            counts[1] += 1
            if counts[1] == 2:
                barrier.wait()
                raise original
        prepared = self.experiment([p2, state, lambda: None, lambda: None])
        real_write = self.driver.write_report
        injected = []
        def write(output, report):
            trial = report['parallel_validation_experiment']['trials'][-1]
            completed_p2 = any(p['stage'] == 'preflight_validate_p2' and p['status'] == 'ok'
                               for p in trial['phases'])
            if p2_thread == [threading.get_ident()] and completed_p2 and not injected:
                injected.append(True)
                raise OSError('P2 final report')
            return real_write(output, report)
        with mock.patch.object(self.driver, 'write_report', side_effect=write):
            with self.assertRaises(Exception) as caught:
                self.execute(prepared)
        self.assertEqual(injected, [True])
        self.assertIs(caught.exception, original)
        self.assertEqual(prepared[2]['parallel_validation_experiment']['status'], 'error')

    def test_join_interruption_finishes_all_readers_before_state_checks_or_raise(self):
        # An interrupted join must not permit post-trial input checks while readers are alive.
        counts = [0, 0]
        barrier = threading.Barrier(2, timeout=2)
        active = threading.Event()
        readers = []
        done = [threading.Event(), threading.Event()]
        def validator(index):
            def run():
                counts[index] += 1
                if counts[index] == 2:
                    readers.append(threading.current_thread())
                    barrier.wait()
                    active.set()
                    time.sleep(.25)
                    done[index].set()
            return run
        prepared = self.experiment([validator(0), validator(1), lambda: None, lambda: None])
        fixed, checkout, report, output, validators, snapshot = prepared
        unsafe_checks = []
        def checked_snapshot():
            if active.is_set() and not all(event.is_set() for event in done):
                unsafe_checks.append(True)
            return snapshot()
        prepared = fixed, checkout, report, output, validators, checked_snapshot
        real_join = threading.Thread.join
        interrupted = []
        original = RuntimeError('one interrupted join')
        def join(thread, *args, **kwargs):
            if not interrupted:
                self.assertTrue(active.wait(2))
                interrupted.append(True)
                raise original
            return real_join(thread, *args, **kwargs)
        error = None
        stopped_at_return = False
        try:
            with mock.patch.object(threading.Thread, 'join', new=join):
                try:
                    self.execute(prepared)
                except BaseException as exc:
                    error = exc
                stopped_at_return = all(not thread.is_alive() for thread in readers)
        finally:
            for thread in readers:
                real_join(thread, timeout=2)
        self.assertIs(error, original)
        self.assertTrue(stopped_at_return)
        self.assertEqual(unsafe_checks, [])
        self.assertEqual([t['status'] for t in report['parallel_validation_experiment']['trials']],
                         ['completed', 'error'])

    def test_start_raises_after_launch_still_finishes_reader_before_state_check(self):
        # start() can be interrupted after launch but before returning to registration.
        calls = []
        active = threading.Event()
        release = threading.Event()
        done = threading.Event()
        readers = []
        def p2():
            calls.append(True)
            if len(calls) == 2:
                readers.append(threading.current_thread())
                active.set()
                release.wait(3)
                done.set()
        prepared = self.experiment([p2, lambda: None, lambda: None, lambda: None])
        fixed, checkout, report, output, validators, snapshot = prepared
        unsafe_checks = []
        def checked_snapshot():
            if active.is_set() and not done.is_set():
                unsafe_checks.append(True)
            return snapshot()
        prepared = fixed, checkout, report, output, validators, checked_snapshot
        real_start, real_join = threading.Thread.start, threading.Thread.join
        original = RuntimeError('start interrupted after actual launch')
        injected = []
        timer = None
        def start(thread):
            nonlocal timer
            real_start(thread)
            if not injected:
                self.assertTrue(active.wait(2))
                injected.append(True)
                timer = threading.Timer(.2, release.set)
                real_start(timer)
                raise original
        error = None
        stopped_at_return = False
        try:
            with mock.patch.object(threading.Thread, 'start', new=start):
                try:
                    self.execute(prepared)
                except BaseException as exc:
                    error = exc
                stopped_at_return = all(not thread.is_alive() for thread in readers)
        finally:
            release.set()
            for thread in readers:
                real_join(thread, timeout=2)
            if timer is not None:
                real_join(timer, timeout=2)
        self.assertIs(error, original)
        self.assertTrue(stopped_at_return)
        self.assertEqual(unsafe_checks, [])
        self.assertEqual([t['status'] for t in report['parallel_validation_experiment']['trials']],
                         ['completed', 'error'])

    def test_equal_count_reordered_history_is_rejected_after_first_trial(self):
        # Comparing only history lengths would accept this changed ordered input.
        prepared = self.experiment([lambda: None] * 4)
        first = {p: ['1' * 40, '2' * 40] for p in self.driver.HISTORY_PATHS}
        changed = {p: list(reversed(v)) for p, v in first.items()}
        with mock.patch.object(self.driver, 'history_oids', side_effect=[first, first, changed]):
            with self.assertRaisesRegex(ValueError, 'ordered history'):
                self.execute(prepared)
        self.assertFalse(prepared[2]['parallel_validation_experiment'].get('history_sequences_equal', False))

    def test_core_change_after_readback_cannot_start_next_trial(self):
        # Omitting the post-trial bytes check permits a changed input into the next trial.
        prepared = self.experiment([lambda: None] * 4)
        target = prepared[1] / 'data/direction_v42/forward_registry.csv'
        with self.assertRaisesRegex(ValueError, 'input|core'):
            self.execute(prepared, lambda: target.write_text('changed'))
        self.assertEqual(len(prepared[2]['parallel_validation_experiment']['trials']), 1)

    def test_original_source_change_is_rejected_at_trial_boundary(self):
        prepared = self.experiment([lambda: None] * 4)
        target = self.source / 'runtime_deadline_service.py'
        with self.assertRaisesRegex(ValueError, 'source'):
            self.execute(prepared, lambda: target.write_text('changed source'))
        self.assertEqual(len(prepared[2]['parallel_validation_experiment']['trials']), 1)

    def test_worker_executes_three_full_trials_and_preserves_source(self):
        self.require_feature()
        fixed = self.fixture()
        output = self.base / 'parallel.json'
        before = self.git(self.source, 'status', '--porcelain')
        code = self.driver.run_profile(self.source, fixed, output, 15,
                                       parallel_validation_experiment=True)
        report = json.loads(output.read_text())
        self.assertEqual(code, 0, report)
        self.assertEqual(report['parallel_validation_experiment']['status'], 'completed')
        trials = report['parallel_validation_experiment']['trials']
        self.assertEqual([len(t['phases']) for t in trials], [5, 5, 5])
        self.assertEqual(report['core_inputs_before'], report['core_inputs_after'])
        self.assertTrue(report['tracked_worktree_clean'])
        self.assertEqual(self.git(self.source, 'status', '--porcelain'), before)
        self.assertFalse(report['production_publication_verified'])

    def test_experiment_modes_are_mutually_exclusive_in_api_and_cli(self):
        self.require_feature()
        fixed = self.fixture()
        with self.assertRaises(ValueError):
            self.driver.run_profile(self.source, fixed, self.base / 'bad.json', 15,
                                     index_experiment=True, parallel_validation_experiment=True)
        result = subprocess.run([sys.executable, str(SCRIPT), '--source-sha', fixed,
                                 '--output', str(self.base / 'bad.json'), '--index-experiment',
                                 '--parallel-validation-experiment'], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'not allowed with argument', result.stderr)

    @unittest.skipUnless(os.name == 'posix', 'process group cleanup requires POSIX')
    def test_parallel_timeout_kills_descendant_and_preserves_completed_trial(self):
        # A timed-out trial must retain the completed baseline and kill its forked child.
        import fcntl
        self.require_feature()
        self.fixture()
        held = self.base / 'child.lock'
        ready = self.base / 'ready'
        runtime = (
            'import os, time, signal, fcntl\n'
            'def validate_p2(root):\n'
            "    p = root / '.calls'\n"
            "    n = int(p.read_text()) + 1 if p.exists() else 1\n"
            '    p.write_text(str(n))\n'
            '    if n == 2:\n'
            '        pid = os.fork()\n'
            '        if pid == 0:\n'
            f"            held = open({str(held)!r}, 'w')\n"
            '            fcntl.flock(held, fcntl.LOCK_EX)\n'
            '            signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
            f"            open({str(ready)!r}, 'w').write(str(os.getpid()))\n"
            '            while True: time.sleep(.02)\n'
            '        time.sleep(30)\n')
        (self.source / 'runtime_deadline_service.py').write_text(runtime)
        fixed = self.commit('parallel timeout descendant fixture')
        output = self.base / 'parallel.json'
        self.assertNotEqual(self.driver.run_profile(self.source, fixed, output, 2,
                             parallel_validation_experiment=True), 0)
        report = json.loads(output.read_text())
        self.assertEqual(report['status'], 'timeout')
        self.assertEqual([t['status'] for t in report['parallel_validation_experiment']['trials']],
                         ['completed', 'timeout'])
        self.assertTrue(ready.exists())
        with held.open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


if __name__ == '__main__':
    unittest.main()

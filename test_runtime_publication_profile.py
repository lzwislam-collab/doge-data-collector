"""Offline safety tests for the profiler; fixtures are not performance evidence."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name('runtime_publication_profile.py')


class ProfilerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'the read-only profiling entry point must exist')
        spec = importlib.util.spec_from_file_location('profile_test_driver', SCRIPT)
        self.driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.driver)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.git(self.source, 'init', '-q')
        self.git(self.source, 'config', 'user.name', 'Offline test')
        self.git(self.source, 'config', 'user.email', 'offline@example.invalid')

    def git(self, root, *args):
        return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True,
                              text=True, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}).stdout.strip()

    def commit(self, message):
        self.git(self.source, 'add', '.')
        self.git(self.source, 'commit', '-qm', message)
        return self.git(self.source, 'rev-parse', 'HEAD')

    def fixture(self, validator='return None'):
        files = {
            'runtime_deadline_service.py': 'def validate_p2(root):\n    ' + validator + '\n',
            'paired_forward_cohort.py': 'def guard(root): return {}\ndef history_guard(root): return []\n',
            'publish_forward_evidence.py':
                "import subprocess\nPATHS=('data/direction_v42/forward_registry.csv',)\n"
                "def validate_state(root): return None\n"
                "def verify_readback(root, head, expected):\n"
                "    return all(subprocess.check_output(['git','show',head+':'+p],cwd=root)==b for p,b in expected.items())\n",
            'quote_evidence_v2.py': "BASE='data/quote_evidence_v2'\n",
            'data/direction_v42/forward_registry.csv': 'forecast_id\noriginal\n',
            'data/quote_evidence_v2/snapshots/ab/abc.json': '{"raw":"original"}\n',
        }
        for path, content in files.items():
            target = self.source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return self.commit('fixed fixture inputs')

    def test_fixed_commit_checkout_does_not_use_or_change_dirty_source_data(self):
        fixed = self.fixture()
        data = self.source / 'data/direction_v42/forward_registry.csv'
        data.write_text('forecast_id\nlater\n')
        self.commit('later source state')
        data.write_text('forecast_id\ndirty\n')
        before = self.git(self.source, 'status', '--porcelain')
        copy = self.base / 'isolated'
        self.driver.prepare_checkout(self.source, fixed, copy)
        self.assertEqual((copy / 'data/direction_v42/forward_registry.csv').read_text(), 'forecast_id\noriginal\n')
        self.assertEqual(self.git(copy, 'rev-parse', 'HEAD'), fixed)
        self.assertEqual(self.git(copy, 'rev-parse', '--is-shallow-repository'), 'false')
        self.assertEqual(data.read_text(), 'forecast_id\ndirty\n')
        self.assertEqual(self.git(self.source, 'status', '--porcelain'), before)

    def test_shallow_source_is_rejected_without_fetching_history(self):
        self.fixture()
        shallow = self.base / 'shallow'
        self.git(self.base, 'clone', '-q', '--depth', '1', self.source.as_uri(), str(shallow))
        fixed = self.git(shallow, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'full git history required'):
            self.driver.prepare_checkout(shallow, fixed, self.base / 'isolated')

    def test_git_http_transport_is_rejected_before_connection(self):
        result = subprocess.run(['git', 'ls-remote', 'https://127.0.0.1:9/unreachable'],
                                env=self.driver.safe_environment(), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("transport 'https' not allowed", result.stderr)

    def test_python_socket_and_dns_are_rejected_in_worker_policy(self):
        code = ("import importlib.util,socket;"
                f"s=importlib.util.spec_from_file_location('p',{str(SCRIPT)!r});"
                "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.lock_down_worker();"
                "\nfor call in [lambda:socket.create_connection(('127.0.0.1',9)),lambda:socket.getaddrinfo('localhost',9)]:"
                "\n try: call()"
                "\n except PermissionError: pass"
                "\n else: raise SystemExit(7)")
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_read_only_profile_preserves_source_and_reports_exact_core_inputs(self):
        fixed = self.fixture()
        before = self.git(self.source, 'status', '--porcelain')
        output = self.base / 'profile.json'
        code = self.driver.run_profile(self.source, fixed, output, 15)
        report = json.loads(output.read_text())
        self.assertEqual(code, 0, report)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['core_inputs_before']['files'], 2)
        self.assertEqual(report['core_inputs_before'], report['core_inputs_after'])
        self.assertTrue(report['tracked_worktree_clean'])
        self.assertFalse(report['production_network_time_measured'])
        self.assertFalse(report['production_publication_verified'])
        self.assertEqual(len(report['phases']), 5)
        self.assertTrue(all(p['status'] == 'ok' for p in report['phases']))
        self.assertEqual(self.git(self.source, 'status', '--porcelain'), before)
        self.assertEqual(self.git(self.source, 'rev-parse', 'HEAD'), fixed)

    def test_mutated_core_input_fails_and_original_source_remains_unchanged(self):
        fixed = self.fixture("(root/'data/direction_v42/forward_registry.csv').write_text('changed')")
        output = self.base / 'profile.json'
        code = self.driver.run_profile(self.source, fixed, output, 15)
        report = json.loads(output.read_text())
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['phases'][-1]['stage'], 'core_exact_readback')
        self.assertEqual(report['phases'][-1]['status'], 'error')
        self.assertEqual((self.source/'data/direction_v42/forward_registry.csv').read_text(), 'forecast_id\noriginal\n')

    def test_timeout_keeps_completed_phase_diagnostics(self):
        self.fixture()
        (self.source / 'paired_forward_cohort.py').write_text(
            "def guard(root): __import__('time').sleep(30)\ndef history_guard(root): return []\n")
        fixed = self.commit('slow paired guard fixture')
        output = self.base / 'profile.json'
        code = self.driver.run_profile(self.source, fixed, output, 1)
        report = json.loads(output.read_text())
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'timeout')
        self.assertEqual([p['status'] for p in report['phases']], ['ok', 'ok', 'timeout'])
        self.assertEqual(report['phases'][-1]['stage'], 'preflight_pair_guard')
        self.assertFalse(report['production_publication_verified'])

    @unittest.skipUnless(os.name == 'posix', 'Ubuntu process-group cleanup')
    def test_timeout_kills_descendant_which_ignores_sigterm(self):
        import fcntl
        ready = self.base / 'child-ready.json'
        parent_info = self.base / 'parent.json'
        held_lock = self.base / 'child.lock'
        heartbeat = self.base / 'heartbeat'
        child = (
            "import signal,time,os,json,pathlib,fcntl;"
            f"held=open({str(held_lock)!r},'w');fcntl.flock(held,fcntl.LOCK_EX);"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            f"pathlib.Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid(),'pgid':os.getpgid(0)}}));"
            f"beats=open({str(heartbeat)!r},'ab',buffering=0)"
            "\nwhile True: beats.write(b'x');time.sleep(.02)")
        parent = ("import subprocess,sys,time,pathlib,os,json;"
                  f"p=subprocess.Popen([sys.executable,'-c',{child!r}]);"
                  f"pathlib.Path({str(parent_info)!r}).write_text(json.dumps({{'pid':os.getpid(),'pgid':os.getpgid(0),'child':p.pid}}));"
                  "time.sleep(60)")
        result = self.driver.run_bounded([sys.executable, '-c', parent], self.driver.safe_environment(), 1)
        self.assertTrue(result['timed_out'])
        child_state = json.loads(ready.read_text())
        parent_state = json.loads(parent_info.read_text())
        self.assertEqual(parent_state['pgid'], parent_state['pid'])
        self.assertEqual(child_state['pgid'], parent_state['pgid'])
        self.assertEqual(child_state['pid'], parent_state['child'])
        self.assertGreaterEqual(len(heartbeat.read_bytes()), 2)
        # A live descendant holds this lock even when /proc is mounted from a
        # different PID namespace. Death releases it without requiring reaping.
        deadline = time.monotonic() + 2
        with held_lock.open('r+') as probe:
            while True:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        self.fail('diagnostic descendant retained its lifetime lock after timeout')
                    time.sleep(.02)
        stopped = heartbeat.read_bytes()
        for _ in range(2):
            time.sleep(.06)
            self.assertEqual(heartbeat.read_bytes(), stopped)

    def test_output_cannot_overwrite_original_checkout(self):
        fixed = self.fixture()
        original = (self.source / 'runtime_deadline_service.py').read_bytes()
        with self.assertRaises(ValueError):
            self.driver.run_profile(self.source, fixed, self.source / 'runtime_deadline_service.py', 15)
        self.assertEqual((self.source / 'runtime_deadline_service.py').read_bytes(), original)

    def test_isolated_checkout_cannot_be_created_inside_original_repo(self):
        fixed = self.fixture()
        with self.assertRaises(ValueError):
            self.driver.prepare_checkout(self.source, fixed, self.source / 'nested-copy')
        self.assertFalse((self.source / 'nested-copy').exists())

    def test_internal_worker_cannot_bypass_output_isolation(self):
        fixed = self.fixture()
        output = self.source / 'runtime_deadline_service.py'
        original = output.read_bytes()
        workspace = self.base / 'workspace'
        workspace.mkdir()
        result = subprocess.run([sys.executable, '-I', '-B', str(SCRIPT), '--worker',
                                 '--source-repo', str(self.source), '--source-sha', fixed,
                                 '--output', str(output), '--workspace', str(workspace)],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), original)

    def test_measured_runtime_is_current_code_without_modifying_fixed_data(self):
        fixed = self.fixture()
        runtime = self.source / 'runtime_deadline_service.py'
        runtime.write_text("def validate_p2(root): raise ValueError('current maintenance runtime')\n")
        expected_hash = hashlib.sha256(runtime.read_bytes()).hexdigest()
        output = self.base / 'profile.json'
        self.assertNotEqual(self.driver.run_profile(self.source, fixed, output, 15), 0)
        report = json.loads(output.read_text())
        self.assertEqual(report['runtime_code_sha256'], expected_hash)
        self.assertEqual(report['phases'][0]['status'], 'error')
        self.assertEqual(report['phases'][0]['stage'], 'preflight_validate_p2')
        self.assertEqual(hashlib.sha256(runtime.read_bytes()).hexdigest(), expected_hash)

    def test_clock_failure_preserves_validator_exception_identity_and_cause(self):
        original = ValueError('original frozen validator error')
        cause = KeyError('original cause')
        def fail():
            raise original from cause
        report = {'phases': []}
        with mock.patch.object(self.driver.time, 'perf_counter', side_effect=[1.0, OSError('clock unavailable')]):
            with self.assertRaises(ValueError) as caught:
                self.driver.phase(report, self.base / 'partial.json', 'validator', fail)
        self.assertIs(caught.exception, original)
        self.assertIs(caught.exception.__cause__, cause)
        self.assertEqual(report['phases'][0]['status'], 'error')
        self.assertEqual(report['diagnostic_errors'][0]['operation'], 'clock')

    def test_report_failure_preserves_validator_exception_identity_and_cause(self):
        original = ValueError('original frozen validator error')
        cause = KeyError('original cause')
        def fail():
            raise original from cause
        report = {'phases': []}
        with mock.patch.object(self.driver, 'write_report', side_effect=[None, OSError('report unavailable')]):
            with self.assertRaises(ValueError) as caught:
                self.driver.phase(report, self.base / 'partial.json', 'validator', fail)
        self.assertIs(caught.exception, original)
        self.assertIs(caught.exception.__cause__, cause)
        self.assertEqual(report['diagnostic_errors'][0]['operation'], 'report')

    def test_successful_validator_cannot_make_failed_diagnostics_green(self):
        observed = []
        report = {'phases': []}
        with mock.patch.object(self.driver, 'write_report', side_effect=[None, OSError('report unavailable')]):
            with self.assertRaises(OSError):
                self.driver.phase(report, self.base / 'partial.json', 'validator', lambda: observed.append('ran'))
        self.assertEqual(observed, ['ran'])


if __name__ == '__main__':
    unittest.main()

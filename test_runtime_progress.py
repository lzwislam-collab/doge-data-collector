"""Offline progress logging preserves runtime behavior and survives interruption."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import archive_capture_bridge as bridge
import runtime_deadline_service as runtime

FLAG = 'DOGE_ARCHIVE_RUNTIME_PROGRESS'
CYCLE_ID = '1a' * 16


class Clock:
    def __init__(self): self.calls = 0
    def __call__(self):
        self.calls += 1
        return datetime(2026, 11, 8, 10, tzinfo=timezone.utc)


class Adapter:
    def __init__(self, publish=None): self.calls, self.publish = [], publish
    def state(self): return [], {}
    def action(self, name):
        self.calls.append(name)
        if name == 'publish' and self.publish is not None: return self.publish()
        return {'status': 'ok'}


class RuntimeProgressTests(unittest.TestCase):
    def enabled(self): return patch.dict(os.environ, {FLAG: '1'})

    def rows(self, stream):
        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertTrue(rows, 'opted-in runtime emitted no progress records')
        return rows

    def test_default_and_non_opt_in_values_leave_stdout_empty(self):
        for value in ('', '0', 'true'):
            with self.subTest(value=value), patch.dict(os.environ, {FLAG: value}), redirect_stdout(io.StringIO()) as output:
                result = runtime.PublicationProfile().call('preflight_validate_p2', lambda: 0)
                receipt = runtime.cycle(Adapter(), {}, Clock())
            self.assertEqual(result, 0)
            self.assertFalse(receipt['fatal'])
            self.assertEqual(output.getvalue(), '')

    def test_begin_is_flushed_before_operation_and_end_after_return(self):
        with tempfile.TemporaryDirectory() as name, self.enabled():
            path = Path(name) / 'runtime.log'
            with path.open('w', buffering=65536) as output, redirect_stdout(output):
                def operation():
                    rows = [json.loads(line) for line in path.read_text().splitlines()]
                    self.assertEqual([row['event'] for row in rows], ['begin'])
                    self.assertEqual(rows[0]['stage'], 'preflight_validate_p2')
                    return 'unchanged'
                self.assertEqual(runtime.PublicationProfile().call('preflight_validate_p2', operation), 'unchanged')
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual([row['event'] for row in rows], ['begin', 'end'])
                self.assertEqual(rows[-1]['status'], 'ok')
                self.assertEqual(datetime.fromisoformat(rows[0]['at_utc']).utcoffset().total_seconds(), 0)

    def test_false_identity_and_arguments_are_preserved(self):
        for value, status in [(False, 'error'), (0, 'ok'), (None, 'ok')]:
            calls = []
            def operation(arg, *, key):
                calls.append((arg, key))
                return value
            with self.subTest(value=value), self.enabled(), redirect_stdout(io.StringIO()) as output:
                profile = runtime.PublicationProfile()
                result = profile.call('receipt_readback', operation, 'private-input', key='private-key')
            self.assertIs(result, value)
            self.assertEqual(calls, [('private-input', 'private-key')])
            self.assertEqual(profile.snapshot('ok')['phases'][0]['status'], status)
            self.assertEqual(self.rows(output)[-1]['status'], status)
            self.assertNotIn('private', output.getvalue())
            self.assertNotIn('error_type', self.rows(output)[-1])

    def test_original_exception_object_and_cause_survive_progress(self):
        for error in (ValueError('private-message'), KeyboardInterrupt('private-message'), SystemExit(71)):
            cause = RuntimeError('private-cause')
            error.__cause__ = cause
            calls = []
            def operation():
                calls.append(1)
                raise error
            with self.subTest(error=type(error).__name__), self.enabled(), redirect_stdout(io.StringIO()) as output:
                try:
                    runtime.PublicationProfile().call('preflight_validate_p2', operation)
                except BaseException as caught:
                    self.assertIs(caught, error)
                    self.assertIs(caught.__cause__, cause)
                else:
                    self.fail('original exception was swallowed')
            self.assertEqual(calls, [1])
            row = self.rows(output)[-1]
            self.assertEqual(row['event'], 'end')
            self.assertEqual(row['status'], 'error')
            self.assertEqual(row['error_type'], type(error).__name__)
            self.assertNotIn('private', output.getvalue())

    def test_unknown_stage_and_custom_exception_name_never_leak(self):
        secret_error = type('TOKEN_private_exception_name', (Exception,), {})
        with self.enabled(), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runtime.PublicationProfile().call('private-stage', lambda: 'private-result'), 'private-result')
            self.assertEqual(output.getvalue(), '')
            with self.assertRaises(secret_error):
                runtime.PublicationProfile().call('preflight_validate_state', lambda: (_ for _ in ()).throw(secret_error('private-payload')))
        self.assertNotIn('private', output.getvalue())
        self.assertEqual(self.rows(output)[-1]['error_type'], 'Exception')

    def test_nested_phases_order_and_repeated_invocations(self):
        with self.enabled(), redirect_stdout(io.StringIO()) as output:
            profile = runtime.PublicationProfile()
            def operation():
                profile.call('data_post_rebase_validate_p2', lambda: None)
                profile.call('data_post_rebase_validate_p2', lambda: None)
            profile.call('data_publisher_total', operation)
        rows = self.rows(output)
        self.assertEqual([(r['stage'], r['event'], r['invocation']) for r in rows], [
            ('data_publisher_total', 'begin', 1),
            ('data_post_rebase_validate_p2', 'begin', 1),
            ('data_post_rebase_validate_p2', 'end', 1),
            ('data_post_rebase_validate_p2', 'begin', 2),
            ('data_post_rebase_validate_p2', 'end', 2),
            ('data_publisher_total', 'end', 1)])

    def test_cycle_context_clears_and_clock_calls_and_receipt_do_not_change(self):
        observations = []
        for enabled in ('0', '1'):
            clock, adapter = Clock(), Adapter()
            with patch.dict(os.environ, {FLAG: enabled}), patch.object(runtime.uuid, 'uuid4', return_value=SimpleNamespace(hex=CYCLE_ID)), redirect_stdout(io.StringIO()) as output:
                receipt = runtime.cycle(adapter, {}, clock)
            observations.append((receipt, adapter.calls, clock.calls))
            if enabled == '1':
                rows = self.rows(output)
                self.assertTrue(rows)
                self.assertTrue(all(r.get('cycle_id') == CYCLE_ID for r in rows))
        self.assertEqual(observations[0], observations[1])
        with self.enabled(), redirect_stdout(io.StringIO()) as output:
            runtime.cycle(Adapter(lambda: runtime.PublicationProfile().call('preflight_validate_p2', lambda: {'status': 'ok'})), {}, Clock())
            runtime.PublicationProfile().call('receipt_readback', lambda: True)
        rows = self.rows(output)
        nested = [r for r in rows if r['stage'] == 'preflight_validate_p2']
        self.assertTrue(nested)
        self.assertTrue(all(len(r['cycle_id']) == 32 for r in nested))
        self.assertIsNone(rows[-1].get('cycle_id'))

    def test_invalid_cycle_identifier_is_not_logged(self):
        with self.enabled(), patch.object(runtime.uuid, 'uuid4', return_value=SimpleNamespace(hex='private-id')), redirect_stdout(io.StringIO()) as output:
            runtime.cycle(Adapter(), {}, Clock())
        self.assertTrue(self.rows(output))
        self.assertNotIn('private-id', output.getvalue())
        self.assertTrue(all(r.get('cycle_id') is None for r in self.rows(output)))

    def test_diagnostic_io_clock_json_and_helper_failures_are_isolated(self):
        class BrokenWrite(io.StringIO):
            def write(self, value): raise OSError('private-write-failure')
        class BrokenFlush(io.StringIO):
            def flush(self): raise OSError('private-flush-failure')
        failures = [
            lambda: redirect_stdout(BrokenWrite()),
            lambda: redirect_stdout(BrokenFlush()),
            lambda: patch.object(runtime, 'datetime', SimpleNamespace(now=lambda *_: (_ for _ in ()).throw(OSError('clock unavailable')))),
            lambda: patch.object(runtime.json, 'dumps', side_effect=OSError('json unavailable')),
            lambda: patch.object(runtime, 'emit_runtime_progress', side_effect=OSError('helper unavailable'), create=True)]
        for failure in failures:
            with self.subTest(failure=failure), self.enabled(), redirect_stdout(io.StringIO()), failure():
                calls, sentinel = [], object()
                def operation():
                    calls.append(1)
                    return sentinel
                self.assertIs(runtime.PublicationProfile().call('preflight_validate_p2', operation), sentinel)
                self.assertEqual(calls, [1])
                original = ValueError('original')
                with self.assertRaises(ValueError) as caught:
                    runtime.PublicationProfile().call('preflight_validate_p2', lambda: (_ for _ in ()).throw(original))
                self.assertIs(caught.exception, original)

    def test_blocked_child_leaves_begin_on_disk_without_fabricated_end(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            code = "import time; from pathlib import Path; import runtime_deadline_service as r; r.PublicationProfile().call('preflight_validate_p2', lambda: time.sleep(30)); Path('completed.json').write_text('done')"
            env = dict(os.environ, **{FLAG: '1', 'PYTHONPATH': str(Path(runtime.__file__).parent)})
            path = root / 'runtime.log'
            with path.open('wb') as stream:
                process = subprocess.Popen([sys.executable, '-c', code], cwd=root, env=env, stdout=stream, stderr=stream)
                try:
                    deadline = time.monotonic() + 5
                    while not path.read_bytes() and time.monotonic() < deadline and process.poll() is None:
                        time.sleep(.02)
                    self.assertIsNone(process.poll(), 'child failed before blocked operation')
                    rows = [json.loads(line) for line in path.read_text().splitlines()]
                    self.assertEqual([r['event'] for r in rows], ['begin'])
                finally:
                    process.terminate()
                    process.wait(timeout=5)
            self.assertFalse((root/'completed.json').exists())
            self.assertEqual([json.loads(line)['event'] for line in path.read_text().splitlines()], ['begin'])
            self.assertNotIn('fatal=false', path.read_text())

    def test_worker_enables_only_child_environment_and_preserves_argv_and_exit(self):
        trusted = {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': runtime.REPO,
                   FLAG: '0', 'DOGE_ARCHIVE_QUALIFIED_DRAIN': '1', 'GITHUB_OUTPUT': 'parent-outputs', 'FIXTURE_ENV': 'retained'}
        with tempfile.TemporaryDirectory() as name, patch.dict(os.environ, trusted):
            root = Path(name); control = root/'control'; control.mkdir()
            (root/'runtime_deadline_service.py').write_text("import json, os, sys\nfrom pathlib import Path\nPath('seen.json').write_text(json.dumps({'argv':sys.argv[1:], 'progress':os.environ.get('DOGE_ARCHIVE_RUNTIME_PROGRESS'), 'output':os.environ.get('GITHUB_OUTPUT'), 'extra':os.environ.get('FIXTURE_ENV')}))\nsys.exit(7)\n")
            bridge.worker(root, control)
            seen = json.loads((root/'seen.json').read_text())
            self.assertEqual(seen, {'argv': ['--duration-seconds', '600', '--stop-file', str(control/'stop'), '--require-qualified-stop'],
                                   'progress': '1', 'output': 'parent-outputs', 'extra': 'retained'})
            self.assertEqual(os.environ[FLAG], '0')
            self.assertEqual(json.loads((control/'completed.json').read_text())['exit_code'], 7)


if __name__ == '__main__': unittest.main()

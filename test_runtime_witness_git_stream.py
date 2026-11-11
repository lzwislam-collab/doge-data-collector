"""Fresh real Git reads only; no business main, model, quote or HTTP calls."""
import ast
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
import unittest
import uuid
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PATH = 'data/direction_v42/forward_registry.csv'
POLICY = 'data/stage5/forward_validation_protocol.json'
HELPER = HERE/'runtime_witness_git_stream.py'
if HELPER.exists():
    spec = importlib.util.spec_from_file_location('runtime_witness_git_stream', HELPER)
    stream = importlib.util.module_from_spec(spec); spec.loader.exec_module(stream)
else:
    stream = None


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True).stdout


def definitions(path, module, names):
    namespace = module if isinstance(module, dict) else module.__dict__
    code = compile(path.read_bytes(), str(path), 'exec', dont_inherit=True)
    for child in code.co_consts:
        if isinstance(child, types.CodeType) and child.co_name in names:
            namespace[child.co_name] = types.FunctionType(child, namespace)


class FreshWitnessTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(stream, 'fresh witness stream is not implemented')
        # Git is protected permanent evidence, never a disposable temp fixture.
        self.root = HERE.parent/'witness-stream-git-fixtures'/uuid.uuid4().hex
        self.root.mkdir(parents=True)
        git(self.root, 'init', '-q'); git(self.root, 'config', 'user.name', 'fixture')
        git(self.root, 'config', 'user.email', 'fixture@example.invalid')
        git(self.root, 'config', 'core.autocrlf', 'false')
        self.raw = ' \tα\r\nβ\rγ\n '.encode('utf-8')
        for relative, raw in ((PATH, self.raw), (POLICY, b'  {"fixed":true}\r\n')):
            path = self.root/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        git(self.root, 'add', '.'); git(self.root, 'commit', '-qm', 'raw source fixture')
        self.head = git(self.root, 'rev-parse', 'HEAD').decode().strip()
        for filename in ('forward_validation.py', 'forward_validation_v2.py', 'p0_operational_audit.py'):
            shutil.copyfile(HERE/filename, self.root/filename)
        self.base = types.ModuleType('forward_validation'); self.base.__file__ = str(self.root/'forward_validation.py')
        self.base.subprocess = subprocess; self.base.REG = PATH; self.base.PROTOCOL = POLICY
        definitions(self.root/'forward_validation.py', self.base, {'git', 'remote_witnesses'})
        self.base.remote_witnesses.__defaults__ = (False,)
        self.original = self.base.git
        self.namespace = {'__file__': str(self.root/'forward_validation_v2.py'), 'base': self.base}
        definitions(self.root/'forward_validation_v2.py', self.namespace, {'main'})

    def install(self, **kwargs):
        return stream.installed(self.root, self.namespace, **kwargs)

    def read(self, path=PATH):
        return self.base.git(self.root, 'show', self.head+':'+path)

    def test_repeated_original_reads_are_fresh_and_preserve_text_semantics(self):
        with self.install() as report:
            self.assertTrue(report['installed'])
            for path, expected in ((PATH, 'α\nβ\nγ'), (POLICY, '{"fixed":true}'), (PATH, 'α\nβ\nγ')):
                self.assertEqual(self.read(path), expected)
                self.assertEqual(self.read(path), self.original(self.root, 'show', self.head+':'+path))
            self.assertEqual(report['requests'], 6); self.assertEqual(report['verified_reads'], 6)
            self.assertEqual(report['processes'], 1); self.assertEqual(report['fallback_calls'], 0)
        self.assertIs(self.base.git, self.original)
        self.assertTrue(report['reaped']); self.assertTrue(report['reader_joined'])

    def test_other_git_operations_preserve_original_output_and_never_request_blob(self):
        with self.install() as report:
            for args in (('show', 'HEAD:'+PATH), ('merge-base', '--is-ancestor', self.head, 'HEAD'),
                         ('show', '-s', '--format=%cI', self.head), ('log', '--format=%H', '--', PATH)):
                self.assertEqual(self.base.git(self.root, *args), self.original(self.root, *args))
            self.assertEqual(report['requests'], 0)

    def test_missing_blob_replays_identical_original_error_then_disables_stream(self):
        missing = '0'*40+':'+PATH
        try: self.original(self.root, 'show', missing)
        except subprocess.CalledProcessError as expected:
            with self.install() as report:
                with self.assertRaises(subprocess.CalledProcessError) as got:
                    self.base.git(self.root, 'show', missing)
                self.assertEqual((got.exception.cmd, got.exception.returncode, got.exception.stdout, got.exception.stderr),
                                 (expected.cmd, expected.returncode, expected.stdout, expected.stderr))
                self.assertEqual(self.read(), 'α\nβ\nγ')
                self.assertTrue(report['disabled']); self.assertEqual(report['fallback_calls'], 2)
                self.assertEqual(report['requests'], 1)
            self.assertTrue(report['reaped']); self.assertTrue(report['reader_joined'])

    def test_valid_commit_without_registry_replays_original_git_failure(self):
        git(self.root, 'rm', '--', PATH); git(self.root, 'commit', '-qm', 'fixture registry deletion')
        self.head = git(self.root, 'rev-parse', 'HEAD').decode().strip()
        with self.assertRaises(subprocess.CalledProcessError) as expected:
            self.original(self.root, 'show', self.head+':'+PATH)
        with self.install() as report:
            with self.assertRaises(subprocess.CalledProcessError) as got: self.read()
            self.assertEqual((got.exception.cmd, got.exception.returncode, got.exception.stdout, got.exception.stderr),
                             (expected.exception.cmd, expected.exception.returncode, expected.exception.stdout, expected.exception.stderr))
            self.assertEqual(report['requests'], 1); self.assertEqual(report['fallback_calls'], 1)
            self.assertTrue(report['disabled']); self.assertTrue(report['reaped']); self.assertTrue(report['reader_joined'])

    def test_legal_keyword_root_preserves_original_error_surface(self):
        with self.assertRaises(subprocess.CalledProcessError) as expected:
            self.original(root=self.root)
        with self.install() as report:
            with self.assertRaises(Exception) as got: self.base.git(root=self.root)
            self.assertIsInstance(got.exception, subprocess.CalledProcessError)
            self.assertEqual((got.exception.cmd, got.exception.returncode, got.exception.stdout, got.exception.stderr),
                             (expected.exception.cmd, expected.exception.returncode, expected.exception.stdout, expected.exception.stderr))
            self.assertEqual(report['requests'], 0)

    def test_changed_git_view_falls_back_before_any_native_read(self):
        with self.install() as report:
            git(self.root, 'config', 'core.commentchar', ';')
            self.assertEqual(self.read(), 'α\nβ\nγ')
            self.assertTrue(report['disabled']); self.assertEqual(report['requests'], 0)
            self.assertEqual(report['fallback_calls'], 1)

    def test_unknown_compiled_function_or_script_never_installs(self):
        for namespace in (dict(self.namespace, main=lambda: True), dict(self.namespace, __file__=str(self.root/'other.py'))):
            with stream.installed(self.root, namespace) as report:
                self.assertFalse(report['installed']); self.assertIs(self.base.git, self.original)
        with patch.object(self.base, 'git', lambda *args: 'unknown'):
            with self.install() as report: self.assertFalse(report['installed'])

    def test_invalid_utf8_preserves_original_decode_error_and_disables_transport(self):
        (self.root/PATH).write_bytes(b'\xff\r\n')
        git(self.root, 'add', PATH); git(self.root, 'commit', '-qm', 'invalid UTF8 fixture')
        self.head = git(self.root, 'rev-parse', 'HEAD').decode().strip()
        # Windows' subprocess reader thread may surface a different main-thread
        # exception from POSIX. Preserve this platform's real original result.
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(Exception) as expected:
                self.original(self.root, 'show', self.head+':'+PATH)
        with self.install() as report, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(type(expected.exception)) as got: self.read()
            self.assertEqual(got.exception.args, expected.exception.args)
            self.assertTrue(report['disabled']); self.assertEqual(report['fallback_calls'], 1)
        self.assertTrue(report['reaped']); self.assertTrue(report['reader_joined'])

    def test_stream_failure_closes_owned_process_before_original_fallback(self):
        with self.install() as report, patch.object(stream.transport._Batch, 'read', side_effect=TimeoutError('read deadline')):
            self.assertEqual(self.read(), 'α\nβ\nγ')
            self.assertTrue(report['disabled']); self.assertEqual(report['fallback_calls'], 1)
            self.assertTrue(report['reaped']); self.assertTrue(report['reader_joined'])

    def test_p0_uses_same_transport_and_restores_on_caller_error(self):
        namespace = {'__file__': str(self.root/'p0_operational_audit.py'), 'base': self.base}
        definitions(self.root/'p0_operational_audit.py', namespace, {'main'})
        error = ValueError('original validator failure')
        with self.assertRaises(ValueError) as got:
            with stream.installed(self.root, namespace) as report:
                self.assertEqual(self.read(), 'α\nβ\nγ'); raise error
        self.assertIs(got.exception, error); self.assertIs(self.base.git, self.original)
        self.assertTrue(report['reaped']); self.assertTrue(report['reader_joined'])

    def test_cli_hook_installs_only_fwd_p0_and_reports_fresh_requests(self):
        spec = importlib.util.spec_from_file_location('witness_launcher_test', HERE/'runtime_audit_cli.py')
        launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
        self.assertTrue(hasattr(launcher, '_witness_stream_hook'), 'FWD/P0 stream launcher hook is absent')
        error = io.StringIO()
        with patch.object(launcher, '_backend', side_effect=lambda root,name:
                stream if name == 'runtime_witness_git_stream' else stream.transport), contextlib.redirect_stderr(error):
            with launcher._witness_stream_hook(self.root, 'forward_validation_v2.py', self.namespace):
                self.assertEqual(self.read(), 'α\nβ\nγ')
        row = json.loads(error.getvalue())
        self.assertEqual(row['kind'], 'runtime_witness_git_stream')
        self.assertEqual(row['requests'], 1); self.assertEqual(row['verified_reads'], 1)
        self.assertEqual(row['fallback_calls'], 0)
        with patch.object(launcher, '_backend', side_effect=AssertionError('must not import')):
            with launcher._witness_stream_hook(self.root, 'paired_cohort_audit.py', self.namespace): pass
        self.assertIs(self.base.git, self.original)

    def test_cli_fallback_is_diagnostic_and_never_reported_as_native_ok(self):
        spec = importlib.util.spec_from_file_location('witness_launcher_fallback_test', HERE/'runtime_audit_cli.py')
        launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
        error = io.StringIO()
        with patch.object(launcher, '_backend', side_effect=lambda root,name:
                stream if name == 'runtime_witness_git_stream' else stream.transport), \
                patch.object(stream.transport, '_View', side_effect=ValueError('unknown Git view')), \
                contextlib.redirect_stderr(error):
            with launcher._witness_stream_hook(self.root, 'forward_validation_v2.py', self.namespace):
                self.assertEqual(self.read(), 'α\nβ\nγ')
        row = json.loads(error.getvalue())
        self.assertFalse(row['installed']); self.assertEqual(row['status'], 'fallback')
        self.assertEqual(row['verified_reads'], 0)


if __name__ == '__main__': unittest.main()

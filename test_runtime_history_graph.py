"""Real temporary Git semantics, including merges, dirty files and failure paths."""
import importlib
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

GIT = shutil.which('git')


class HistoryGraph(unittest.TestCase):
    def setUp(self):
        self.m = importlib.import_module('runtime_history_graph')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'repo'
        self.root.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.write('model.py', 'frozen = True\n')
        self.write('events/one.json', '{"v":1}\n')
        self.write('delete later.txt', 'original\n')
        self.commit('initial')
        self.first = self.git('rev-parse', 'HEAD').strip()
        self.git('checkout', '-q', '-b', 'side')
        self.write('events/side.json', '{"side":true}\n')
        self.commit('side')
        self.git('checkout', '-q', 'main')
        for n in range(3):
            self.write('events/one.json', '{"v":'+str(n+2)+'}\n')
            self.write('events/two words.json', '{"n":'+str(n)+'}\n')
            self.commit('main '+str(n))
        self.git('mv', 'delete later.txt', 'renamed file.txt')
        self.commit('rename')
        self.git('merge', '-q', '--no-ff', 'side', '-m', 'merge side')
        self.git('tag', '-a', 'snapshot', '-m', 'fixture tag')
        self.history_paths = sorted(set(self.git('log', '--all', '--format=', '--name-only').splitlines())-{''})
        self.write('model.py', 'frozen = True\n# pre-existing unstaged\n')
        self.write('events/one.json', '{"staged":true}\n')
        self.git('add', 'events/one.json')
        self.write('events/one.json', '{"staged":true,"unstaged":true}\n')
        self.git('rm', '-q', 'renamed file.txt')
        self.write('untracked.keep', 'untouched\n')

    def git(self, *args, cwd=None):
        result = subprocess.run([GIT, '--no-optional-locks', *args], cwd=cwd or self.root,
                                check=True, capture_output=True, text=True, encoding='utf-8', timeout=15)
        return result.stdout

    def write(self, name, text):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)

    def state(self):
        return {'head': self.git('rev-parse', 'HEAD'),
                'refs': self.git('for-each-ref', '--sort=refname', '--format=%(refname) %(objectname)'),
                'status': self.git('status', '--porcelain=v1', '-z', '--untracked-files=all'),
                'diff': self.git('diff', '--binary', '--no-ext-diff', '--no-textconv', 'HEAD'),
                'cached_diff': self.git('diff', '--cached', '--binary', '--no-ext-diff', '--no-textconv', 'HEAD'),
                'index': (self.root/'.git/index').read_bytes(),
                'config': (self.root/'.git/config').read_bytes(),
                'worktree': {p.relative_to(self.root).as_posix(): p.read_bytes()
                             for p in self.root.rglob('*') if p.is_file() and '.git' not in p.relative_to(self.root).parts}}

    def histories(self, enabled):
        prefix = ('-c', 'core.commitGraph='+('true' if enabled else 'false'))
        values = {}
        for path in self.history_paths:
            values[path] = (
                self.git(*prefix, 'log', '--all', '--full-history', '--format=%H %P', '-p', '--', path),
                self.git(*prefix, 'log', '--format=%H', '--follow', '--', path),
                self.git(*prefix, 'diff', '--binary', '--no-ext-diff', '--no-textconv', self.first, 'HEAD', '--', path))
        values['namespace'] = self.git(*prefix, 'log', '--format=', '--name-only', '--', 'events')
        return values

    def test_real_index_preserves_every_history_and_preexisting_dirty_state(self):
        before, histories = self.state(), self.histories(False)
        with patch.object(self.m.runtime_process, 'command', wraps=self.m.runtime_process.command) as owned:
            result = self.m.prepare(self.root)
        self.assertEqual(result['status'], 'LOCAL_GIT_HISTORY_INDEX_VERIFIED')
        self.assertTrue(result['bloom_filters_present'])
        self.assertFalse(result['frozen_guards_skipped'])
        self.assertFalse(result['production_performance_verified'])
        self.assertEqual(before, self.state())
        self.assertEqual(histories, self.histories(True))
        self.assertTrue(all(0 < call.kwargs['timeout'] <= 60 for call in owned.call_args_list))
        self.assertTrue(all(call.kwargs['env']['GIT_OPTIONAL_LOCKS'] == '0' for call in owned.call_args_list))

    def test_replaces_existing_index_without_bloom_and_is_repeatable(self):
        self.git('commit-graph', 'write', '--reachable', '--no-changed-paths')
        before = self.state()
        first = self.m.prepare(self.root)
        second = self.m.prepare(self.root)
        self.assertTrue(first['bloom_filters_present'])
        self.assertTrue(second['bloom_filters_present'])
        self.assertEqual(before, self.state())
        self.git('commit-graph', 'verify', '--no-progress')

    def test_disabled_index_or_bloom_consumer_cannot_claim_ready(self):
        for key, value in [('core.commitGraph', 'false'), ('commitGraph.changedPathsVersion', '0'),
                           ('commitGraph.readChangedPaths', 'false')]:
            with self.subTest(key=key):
                self.git('config', key, value)
                before = self.state()
                with self.assertRaises(ValueError):
                    self.m.prepare(self.root)
                self.assertEqual(before, self.state())
                self.git('config', '--unset', key)
        self.git('config', 'commitGraph.readChangedPaths', 'false')
        self.git('config', 'commitGraph.changedPathsVersion', '1')
        self.assertTrue(self.m.prepare(self.root)['bloom_filters_present'])

    def test_explicit_unsupported_bloom_version_never_reaches_write(self):
        original = self.state()
        config_path = self.root/'.git/config'
        original_config = config_path.read_bytes()
        for value in ('-999', '3', ''):
            with self.subTest(value=value):
                self.git('config', 'commitGraph.changedPathsVersion', value)
                configured = config_path.read_bytes()
                try:
                    with patch.object(self.m.runtime_process, 'command', wraps=self.m.runtime_process.command) as owned:
                        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                            self.m.prepare(self.root)
                    self.assertFalse(any('write' in call.args[0] for call in owned.call_args_list))
                    self.assertEqual(configured, config_path.read_bytes())
                finally:
                    config_path.write_bytes(original_config)
                self.assertEqual(original, self.state())

    def test_shallow_shared_or_linked_checkout_rejected_without_mutation(self):
        for mode in ('shallow', 'shared', 'linked'):
            with self.subTest(mode=mode):
                destination = Path(self.temp.name)/mode
                if mode == 'shallow':
                    self.git('clone', '-q', '--depth=1', self.root.as_uri(), str(destination))
                elif mode == 'shared':
                    self.git('clone', '-q', '--shared', str(self.root), str(destination))
                else:
                    self.git('worktree', 'add', '-q', '--detach', str(destination), 'HEAD')
                before = self.state()
                with self.assertRaises(ValueError):
                    self.m.prepare(destination)
                self.assertEqual(before, self.state())

    def test_git_redirection_and_replace_or_graft_history_rejected(self):
        before = self.state()
        with patch.dict(os.environ, {'GIT_OBJECT_DIRECTORY': str(self.root/'.git/objects')}):
            with self.assertRaises(ValueError):
                self.m.prepare(self.root)
        self.git('update-ref', 'refs/replace/'+self.first, 'HEAD')
        with self.assertRaises(ValueError):
            self.m.prepare(self.root)
        self.git('update-ref', '-d', 'refs/replace/'+self.first)
        graft = self.root/'.git/info/grafts'
        graft.write_text(self.first+'\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.m.prepare(self.root)
        graft.unlink()
        self.assertEqual(before, self.state())

    def test_write_noop_or_failure_never_claims_success(self):
        real = self.m.runtime_process.command
        before = self.state()
        for error in (None, subprocess.CalledProcessError(7, ['git', 'commit-graph', 'write'])):
            def command(args, **kwargs):
                if 'commit-graph' in args and 'write' in args:
                    if error is not None:
                        raise error
                    return ''
                return real(args, **kwargs)
            with self.subTest(error=error), self.assertRaises((ValueError, subprocess.CalledProcessError)):
                self.m.prepare(self.root, command=command)
            self.assertEqual(before, self.state())

    def test_actual_owned_timeout_propagates_with_reaped_child(self):
        real = self.m.runtime_process.command
        before = self.state()
        def command(args, **kwargs):
            if 'commit-graph' in args and 'write' in args:
                return real([sys.executable, '-c', 'import time; time.sleep(5)'],
                            cwd=kwargs['cwd'], env=kwargs['env'], timeout=.05)
            return real(args, **kwargs)
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.m.prepare(self.root, command=command)
        self.assertTrue(caught.exception.process_cleanup['leader_reaped'])
        self.assertEqual(before, self.state())

    def test_real_verify_rejects_corrupt_written_graph(self):
        real = self.m.runtime_process.command
        def command(args, **kwargs):
            result = real(args, **kwargs)
            if 'commit-graph' in args and 'write' in args:
                path = next((self.root/'.git/objects/info/commit-graphs').glob('graph-*.graph'))
                raw = bytearray(path.read_bytes())
                raw[0] ^= 1
                path.chmod(stat.S_IREAD | stat.S_IWRITE)
                path.write_bytes(raw)
            return result
        before = self.state()
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            self.m.prepare(self.root, command=command)
        self.assertEqual(before, self.state())

    def test_overall_budget_exhaustion_or_concurrent_state_change_fails_closed(self):
        real = self.m.runtime_process.command
        elapsed = [0]
        timeouts = []
        def command(args, **kwargs):
            timeouts.append(kwargs['timeout'])
            result = real(args, **kwargs)
            if 'commit-graph' in args and 'write' in args:
                elapsed[0] = 99
            elif 'commit-graph' in args and 'verify' in args:
                elapsed[0] = 101
            return result
        with self.assertRaises(TimeoutError):
            self.m.prepare(self.root, command=command, monotonic=lambda: elapsed[0])
        self.assertLessEqual(timeouts[-1], 1)
        def changed(args, **kwargs):
            result = real(args, **kwargs)
            if 'commit-graph' in args and 'verify' in args:
                self.write('model.py', 'changed by another writer\n')
            return result
        with self.assertRaises(ValueError):
            self.m.prepare(self.root, command=changed)

    def test_cli_uses_existing_cancel_scope_and_never_labels_cancellation_success(self):
        import signal
        output = io.StringIO()
        before = self.state()
        with patch.object(self.m, 'prepare', side_effect=self.m.runtime_process.RuntimeCancelled(signal.SIGTERM)), \
                patch.object(self.m.runtime_process, 'cancellation_signals',
                             wraps=self.m.runtime_process.cancellation_signals) as scope, redirect_stdout(output):
            code = self.m.main(['--root', str(self.root)])
        self.assertEqual(scope.call_count, 1)
        self.assertEqual(code, 128+signal.SIGTERM)
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'LOCAL_GIT_HISTORY_INDEX_FAILED')
        self.assertTrue(result['cancelled'])
        self.assertEqual(before, self.state())

    def test_cli_failure_emits_json_and_nonzero(self):
        with tempfile.TemporaryDirectory() as empty:
            env = dict(os.environ)
            env['PYTHONPATH'] = os.pathsep.join(sys.path)
            result = subprocess.run([sys.executable, str(Path(self.m.__file__)), '--root', empty],
                                    capture_output=True, text=True, encoding='utf-8', env=env, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            body = json.loads(result.stdout)
            self.assertEqual(body['status'], 'LOCAL_GIT_HISTORY_INDEX_FAILED')
            self.assertNotIn('LOCAL_GIT_HISTORY_INDEX_VERIFIED', result.stdout)


if __name__ == '__main__':
    unittest.main()

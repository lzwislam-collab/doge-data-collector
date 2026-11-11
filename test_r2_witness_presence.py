"""Candidate-only R2 adapter checks; all Git fixtures are permanently retained."""
from contextlib import nullcontext
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError
from uuid import uuid4

import phase2_forward_runtime_v2 as r
import phase2_forward_v2 as f
import forward_readiness as readiness

HERE = Path(__file__).resolve().parent
# CI may declare an absolute artifact root and retain/upload it. The fallback is
# a named retained directory, never an automatically deleted temporary Git repo.
_DEFAULT_FIXTURE_HOME = (
    Path(r'C:\Users\lzwis\.codex\.chatgpt-projects\g-p-6abbcaea551c8191860ef46a58b88c09\p0-1-current-20261111\adapter-test-fixtures')
    if os.name == 'nt' else Path(tempfile.gettempdir()) / 'doge-r2-adapter-retained-fixtures')
FIXTURE_HOME = Path(os.environ.get('DOGE_R2_ADAPTER_TEST_FIXTURE_ROOT', str(_DEFAULT_FIXTURE_HOME))).expanduser()
if not FIXTURE_HOME.is_absolute():
    raise ValueError('DOGE_R2_ADAPTER_TEST_FIXTURE_ROOT must be absolute')
SCORE = f.BASE + '/scores/1794240000_formal.json'
EVENT = f.BASE + '/events/1794240000_formal.json'
EXPECTED = b'{"real_target":true}\n'
BEFORE = datetime(2026, 11, 9, 18, 2, tzinfo=timezone.utc)
AFTER = datetime(2026, 11, 9, 17, 37, tzinfo=timezone.utc)


def git(root, *args):
    return subprocess.run(['git', '-c', 'safe.directory=*', *args], cwd=root,
                          check=True, capture_output=True).stdout


def retained_fixture(label):
    # These are protected evidence, never TemporaryDirectory or teardown targets.
    FIXTURE_HOME.mkdir(parents=True, exist_ok=True)
    marker = FIXTURE_HOME / '.cleanup-protect'
    if not marker.exists():
        marker.write_text('Permanent adapter Git test evidence.\n', encoding='utf-8')
    root = FIXTURE_HOME / (label + '-' + uuid4().hex)
    root.mkdir()
    (root / '.cleanup-protect').write_text('Permanent Git fixture; retain all objects and refs.\n', encoding='utf-8')
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'R2 isolated fixture')
    git(root, 'config', 'user.email', 'r2-fixture@example.invalid')
    git(root, 'config', 'core.autocrlf', 'false')
    git(root, 'config', 'core.longpaths', 'true')
    (root / 'known.txt').write_bytes(b'candidate predates event and score\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'immutable pre-publication candidate')
    absent = git(root, 'rev-parse', 'HEAD').decode().strip()
    for path in (SCORE, EVENT):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(EXPECTED)
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'exact event and score are present')
    present = git(root, 'rev-parse', 'HEAD').decode().strip()
    (root / 'fixture-evidence.json').write_text(json.dumps({'absent': absent, 'present': present,
        'paths': [SCORE, EVENT], 'permanently_retained': True}, sort_keys=True) + '\n', encoding='utf-8')
    return root, absent, present


def runrow(head, number):
    return {'id': number, 'repository': {'full_name': f.old.REPO}, 'path': f.WORKFLOW,
            'head_branch': 'main', 'event': 'workflow_dispatch', 'status': 'completed',
            'conclusion': 'success', 'head_sha': head, 'created_at': '2026-11-09T17:38:00Z',
            'audit_job': {'name': 'audit', 'status': 'completed', 'conclusion': 'success',
                         'started_at': '2026-11-09T17:39:00Z', 'completed_at': '2026-11-09T17:42:00Z'}}


class R2WitnessPresence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.absent, cls.present = retained_fixture('r2-presence')
        path = HERE / 'r2_witness_presence.py'
        if path.exists():
            spec = importlib.util.spec_from_file_location('r2_presence_subject', path)
            cls.subject = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.subject)
        else:
            # RED executes the actual stock behavior before the adapter exists.
            cls.subject = types.SimpleNamespace(witness_presence=nullcontext, run=r.run, main=r.main)

    def setUp(self):
        self.patches = [mock.patch.object(r, 'ROOT', self.root),
                        mock.patch.object(readiness, 'ROOT', self.root),
                        mock.patch.object(r, 'urlopen', side_effect=self.not_found)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.original = r.read_at
        self.http_errors = []

    def not_found(self, request, **kwargs):
        # Network is forbidden: preserve the original local Git read and replace
        # only its external HTTP boundary with the observed missing-object 404.
        error = HTTPError(request.full_url, 404, 'Not Found', {}, None)
        self.http_errors.append(error)
        raise error

    def witness(self, runs, path=SCORE):
        return f.witness({path: EXPECTED}, runs, r.read_at, BEFORE, AFTER)

    def test_stock_reader_reproduces_absent_score_http404_abort(self):
        with self.assertRaises(HTTPError) as caught:
            self.witness([runrow(self.absent, 1), runrow(self.present, 2)])
        self.assertEqual(caught.exception.code, 404)
        self.assertIs(r.read_at, self.original)

    def test_real_witness_skips_missing_old_tree_and_selects_later_exact_bytes(self):
        with self.subject.witness_presence():
            result = self.witness([runrow(self.absent, 1), runrow(self.present, 2)])
        self.assertEqual(result, {'run_id': 2, 'head_sha': self.present,
                                 'job_completed_at_utc': '2026-11-09T17:42:00+00:00'})
        self.assertIs(r.read_at, self.original)

    def test_all_proven_absent_candidates_remain_no_witness(self):
        with self.subject.witness_presence():
            self.assertIsNone(self.witness([runrow(self.absent, 1)]))
        self.assertIs(r.read_at, self.original)

    def test_event_namespace_uses_the_same_exact_absence_rule(self):
        with self.subject.witness_presence():
            result = self.witness([runrow(self.absent, 1), runrow(self.present, 2)], EVENT)
        self.assertEqual(result['run_id'], 2)

    def test_conversion_keeps_typed_value_error_and_original_404_cause(self):
        with self.subject.witness_presence():
            with self.assertRaises(ValueError) as caught:
                r.read_at(self.absent, SCORE)
        self.assertIsInstance(caught.exception, readiness.WitnessObjectAbsent)
        self.assertIs(caught.exception.__cause__, self.http_errors[-1])

    def test_later_candidate_with_different_bytes_does_not_qualify(self):
        with self.subject.witness_presence():
            result = f.witness({SCORE: b'wrong expected bytes'},
                [runrow(self.absent, 1), runrow(self.present, 2)], r.read_at, BEFORE, AFTER)
        self.assertIsNone(result)

    def test_fixed_source_missing_is_not_classified_as_candidate_absence(self):
        with self.subject.witness_presence():
            with self.assertRaises(HTTPError):
                r.read_at(self.absent, r.SOURCE)

    def test_unavailable_commit_never_certifies_absence(self):
        with self.subject.witness_presence():
            with self.assertRaises(HTTPError) as caught:
                self.witness([runrow('f' * 40, 1)])
        self.assertIs(caught.exception, self.http_errors[-1])

    def test_present_tree_entry_with_404_keeps_original_error(self):
        error = HTTPError('https://raw.githubusercontent.com/fixture', 404, 'Not Found', {}, None)
        with mock.patch.object(r, 'read_at', side_effect=error) as reader:
            with self.subject.witness_presence():
                with self.assertRaises(HTTPError) as caught:
                    self.witness([runrow(self.present, 1)])
            self.assertIs(r.read_at, reader)
        self.assertIs(caught.exception, error)

    def test_replace_ref_cannot_disguise_present_immutable_tree(self):
        root, absent, present = retained_fixture('replace-ref')
        git(root, 'replace', present, absent)
        error = HTTPError('https://raw.githubusercontent.com/fixture', 404, 'Not Found', {}, None)
        with mock.patch.object(readiness, 'ROOT', root), mock.patch.object(r, 'read_at', side_effect=error):
            with self.subject.witness_presence():
                with self.assertRaises(HTTPError) as caught:
                    r.read_at(present, SCORE)
        self.assertIs(caught.exception, error)

    def test_malformed_head_and_paths_preserve_reader_validation(self):
        for head, path in [('bad head', SCORE), (self.absent, SCORE + '.extra'),
                           (self.absent, SCORE.replace('1794240000_formal', '../escape')),
                           (self.absent, SCORE.replace('/scores/', '/attempts/'))]:
            with self.subTest(head=head, path=path), self.subject.witness_presence():
                with self.assertRaises(ValueError) as caught:
                    r.read_at(head, path)
                self.assertNotIsInstance(caught.exception, readiness.WitnessObjectAbsent)
        self.assertEqual(self.http_errors, [])

    def test_non404_transport_failures_propagate_same_error(self):
        for error in (HTTPError('https://raw.githubusercontent.com/fixture', 403, 'Forbidden', {}, None),
                      HTTPError('https://raw.githubusercontent.com/fixture', 429, 'Limited', {}, None),
                      TimeoutError('transport timeout'), URLError('transport reset')):
            with self.subTest(error=repr(error)), mock.patch.object(r, 'urlopen', side_effect=error):
                with self.subject.witness_presence():
                    with self.assertRaises(type(error)) as caught:
                        self.witness([runrow(self.absent, 1)])
                self.assertIs(caught.exception, error)
                self.assertIs(r.read_at, self.original)

    def test_failed_git_presence_proof_preserves_original_404(self):
        error = HTTPError('https://raw.githubusercontent.com/fixture', 404, 'Not Found', {}, None)
        ok = subprocess.CompletedProcess(['git'], 0, b'', b'')
        bad = subprocess.CompletedProcess(['git'], 128, b'', b'cannot read tree')
        for replies in ([bad], [ok, bad]):
            with self.subTest(replies=len(replies)), mock.patch.object(r, 'read_at', side_effect=error):
                with mock.patch.object(readiness.subprocess, 'run', side_effect=replies):
                    with self.subject.witness_presence():
                        with self.assertRaises(HTTPError) as caught:
                            self.witness([runrow(self.absent, 1)])
                self.assertIs(caught.exception, error)
        for error_probe in (OSError('Git unavailable'), subprocess.TimeoutExpired(['git'], 5)):
            with self.subTest(error=repr(error_probe)), mock.patch.object(r, 'read_at', side_effect=error):
                with mock.patch.object(readiness.subprocess, 'run', side_effect=error_probe):
                    with self.subject.witness_presence():
                        with self.assertRaises(HTTPError) as caught:
                            self.witness([runrow(self.absent, 1)])
                self.assertIs(caught.exception, error)

    def test_original_identity_and_time_guards_still_reject_candidates(self):
        mutations = [('head_branch', 'other'), ('event', 'schedule'), ('conclusion', 'failure'),
                     ('status', 'in_progress'), ('repository', {'full_name': 'other/repo'}),
                     ('path', '.github/workflows/untrusted.yml')]
        for key, value in mutations:
            row = runrow(self.present, 1)
            row[key] = value
            with self.subTest(key=key), self.subject.witness_presence():
                self.assertIsNone(self.witness([row]))
        for start, finish in [('2026-11-09T17:36:00Z', '2026-11-09T17:42:00Z'),
                              ('2026-11-09T17:39:00Z', '2026-11-09T18:02:00Z')]:
            row = runrow(self.present, 1)
            row['audit_job'].update(started_at=start, completed_at=finish)
            with self.subTest(start=start, finish=finish), self.subject.witness_presence():
                self.assertIsNone(self.witness([row]))

    def test_scope_restores_original_reader_on_all_exit_types(self):
        for failure in (None, RuntimeError('original runtime failure'), SystemExit(3), KeyboardInterrupt()):
            with self.subTest(failure=repr(failure)):
                def invoke():
                    with self.subject.witness_presence():
                        self.assertIsNone(self.witness([runrow(self.absent, 1)]))
                        if failure is not None:
                            raise failure
                if failure is None:
                    invoke()
                else:
                    with self.assertRaises(type(failure)) as caught:
                        invoke()
                    self.assertIs(caught.exception, failure)
                self.assertIs(r.read_at, self.original)
        with self.assertRaises(HTTPError):
            r.read_at(self.absent, SCORE)

    def test_nested_scopes_restore_enclosing_then_original_reader(self):
        with self.subject.witness_presence():
            outer = r.read_at
            with self.subject.witness_presence():
                self.assertIsNone(self.witness([runrow(self.absent, 1)]))
            self.assertIs(r.read_at, outer)
        self.assertIs(r.read_at, self.original)

    def test_runner_invokes_original_run_and_does_not_bypass_guard_failure(self):
        failure = ValueError('original frozen guard rejected bytes')
        with mock.patch.object(r, 'guard', side_effect=failure):
            with self.assertRaises(ValueError) as caught:
                self.subject.run('audit')
        self.assertIs(caught.exception, failure)
        self.assertIs(r.read_at, self.original)

    def test_runner_scopes_real_witness_progression_and_preserves_return(self):
        def original_run(mode):
            return {'mode': mode, 'witness': self.witness([runrow(self.absent, 1), runrow(self.present, 2)])}
        with mock.patch.object(r, 'run', side_effect=original_run):
            result = self.subject.run('audit_all')
        self.assertEqual(result['mode'], 'audit_all')
        self.assertEqual(result['witness']['run_id'], 2)
        self.assertIs(r.read_at, self.original)

    def test_main_invokes_original_cli_and_writes_original_result(self):
        cli_root = self.root / ('cli-' + uuid4().hex)
        cli_root.mkdir()
        def original_run(mode):
            return {'mode': mode, 'witness': self.witness([runrow(self.absent, 1), runrow(self.present, 2)]),
                    'request_witness': False, 'errors': {}, 'new_events': [], 'new_scores': [],
                    'new_target_attempts': []}
        with mock.patch.object(r, 'ROOT', cli_root), mock.patch.object(r, 'run', side_effect=original_run):
            # Candidate object reads need the retained Git fixture, while the
            # original CLI output has its own retained directory.
            def fixture_read(head, path):
                with mock.patch.object(r, 'ROOT', self.root):
                    return self.original(head, path)
            with mock.patch.object(r, 'read_at', side_effect=fixture_read) as reader:
                with mock.patch.object(sys, 'argv', ['r2_witness_presence.py', 'audit']), \
                     mock.patch.dict(r.os.environ, {'GITHUB_OUTPUT': ''}), \
                     mock.patch('sys.stdout', new_callable=io.StringIO):
                    self.subject.main()
                self.assertIs(r.read_at, reader)
        result = json.loads((cli_root / 'phase2-forward-output/runtime.json').read_bytes())
        self.assertEqual(result['mode'], 'audit')
        self.assertEqual(result['witness']['run_id'], 2)

    def test_original_cli_argument_rejection_restores_reader(self):
        with mock.patch.object(sys, 'argv', ['r2_witness_presence.py', 'not-a-mode']), \
             mock.patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as caught:
                self.subject.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIs(r.read_at, self.original)


if __name__ == '__main__':
    unittest.main()

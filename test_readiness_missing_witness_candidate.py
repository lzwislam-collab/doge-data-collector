"""An authenticated absent candidate object is not a publication witness."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError
from unittest import mock

HERE = Path(__file__).parent
REFERENCE = HERE / 'reference' if (HERE / 'reference').is_dir() else HERE
SCORE = 'data/phase2_forward_v2/scores/1794240000_formal.json'
EXPECTED = b'{"real_target":true}\n'


class MissingWitnessCandidate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        (self.root / 'known.txt').write_bytes(b'known immutable commit')
        self.git('add', '-A')
        self.git('commit', '-qm', 'source predates actual target')
        self.absent = self.git('rev-parse', 'HEAD').decode().strip()
        target = self.root / SCORE
        target.parent.mkdir(parents=True)
        target.write_bytes(EXPECTED)
        self.git('add', '-A')
        self.git('commit', '-qm', 'real target score now exists')
        self.present = self.git('rev-parse', 'HEAD').decode().strip()
        self.f = types.ModuleType('phase2_forward_v2')
        self.f.BASE = 'data/phase2_forward_v2'
        self.f.WORKFLOW = '.github/workflows/phase2_forward_audit_v2.yml'
        self.f.old = types.SimpleNamespace(REPO='lzwislam-collab/doge-data-collector')
        self.f.utc = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00'))
        original = REFERENCE / 'phase2_forward_v2.py'
        body = original.read_bytes()
        tree = ast.parse(body)
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'witness']
        self.assertEqual(len(functions), 1)
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(original), 'exec'), self.f.__dict__)
        self.r = types.ModuleType('phase2_forward_runtime_v2')
        self.calls = []
        self.errors = []

        def read_at(head, path):
            self.calls.append((head, path))
            try:
                return subprocess.check_output(['git', 'show', head + ':' + path], cwd=self.root,
                                                stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                error = HTTPError('https://raw.githubusercontent.com/repo/' + head + '/' + path,
                                  404, 'Not Found', {}, None)
                self.errors.append(error)
                raise error

        self.reader = read_at
        self.r.read_at = read_at
        self.before = datetime(2026, 11, 9, 18, 2, tzinfo=timezone.utc)
        self.after = datetime(2026, 11, 9, 17, 37, tzinfo=timezone.utc)
        self.r.publication = lambda runs: self.f.witness({SCORE: EXPECTED}, runs,
                                                       self.r.read_at, self.before, self.after)
        modules = mock.patch.dict(sys.modules, {'phase2_forward_v2': self.f,
            'phase2_forward_runtime_v2': self.r,
            'engineering_run_receipt': types.ModuleType('engineering_run_receipt')})
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location('missing_witness_subject', HERE / 'forward_readiness.py')
        self.subject = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.subject)
        self.subject.ROOT = self.root

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True).stdout

    def runrow(self, head, number):
        return {'id': number, 'repository': {'full_name': self.f.old.REPO}, 'path': self.f.WORKFLOW,
                'head_branch': 'main', 'event': 'workflow_dispatch', 'status': 'completed',
                'conclusion': 'success', 'head_sha': head, 'created_at': '2026-11-09T17:38:00Z',
                'audit_job': {'name': 'audit', 'status': 'completed', 'conclusion': 'success',
                    'started_at': '2026-11-09T17:39:00Z', 'completed_at': '2026-11-09T17:42:00Z'}}

    def publication(self, runs):
        # Baseline executes the exact failing original path; candidate wraps
        # only this invocation, retaining the unchanged frozen witness body.
        function = getattr(self.subject, 'publication_with_presence', self.r.publication)
        return function(runs)

    def test_real_immutable_missing_score_skips_old_candidate_then_selects_exact_valid_witness(self):
        result = self.publication([self.runrow(self.absent, 1), self.runrow(self.present, 2)])
        self.assertEqual(result, {'run_id': 2, 'head_sha': self.present,
                                 'job_completed_at_utc': '2026-11-09T17:42:00+00:00'})
        self.assertEqual(self.calls, [(self.absent, SCORE), (self.present, SCORE)])
        self.assertIs(self.r.read_at, self.reader)

    def test_all_authenticated_missing_candidates_produce_no_witness(self):
        self.assertIsNone(self.publication([self.runrow(self.absent, 1)]))
        self.assertIs(self.r.read_at, self.reader)

    def test_only_authenticated_absence_has_typed_value_error_and_original_cause(self):
        self.r.publication = lambda ignored: self.r.read_at(self.absent, SCORE)
        with self.assertRaises(self.subject.WitnessObjectAbsent) as caught:
            self.publication([])
        self.assertIsInstance(caught.exception, ValueError)
        self.assertIs(caught.exception.__cause__, self.errors[-1])
        self.assertIs(self.r.read_at, self.reader)

    def test_outside_this_publication_invocation_original_reader_still_raises_404(self):
        self.assertIsNone(self.publication([self.runrow(self.absent, 1)]))
        with self.assertRaises(HTTPError):
            self.r.read_at(self.absent, SCORE)

    def test_same_missing_object_in_event_namespace_is_also_only_a_nonmatch(self):
        path = SCORE.replace('/scores/', '/events/')
        self.r.publication = lambda runs: self.f.witness({path: EXPECTED}, runs,
                                                       self.r.read_at, self.before, self.after)
        self.assertIsNone(self.publication([self.runrow(self.absent, 1)]))

    def test_present_tree_entry_with_404_preserves_original_error_object(self):
        error = HTTPError('redacted', 404, 'Not Found', {}, None)
        self.r.read_at = mock.Mock(side_effect=error)
        original = self.r.read_at
        with self.assertRaises(HTTPError) as caught:
            self.publication([self.runrow(self.present, 1)])
        self.assertIs(caught.exception, error)
        self.assertIs(self.r.read_at, original)

    def test_replace_ref_cannot_turn_known_immutable_tree_entry_into_absence(self):
        self.git('replace', self.present, self.absent)
        self.assertTrue(self.git('--no-replace-objects', 'ls-tree', '--full-tree', '-z',
                                 self.present, '--', SCORE))
        self.assertEqual(self.git('ls-tree', '--full-tree', '-z', self.present, '--', SCORE), b'')
        error = HTTPError('redacted', 404, 'Not Found', {}, None)
        self.r.read_at = mock.Mock(side_effect=error)
        with self.assertRaises(HTTPError) as caught:
            self.publication([self.runrow(self.present, 1)])
        self.assertIs(caught.exception, error)

    def test_unknown_commit_or_invalid_dynamic_path_preserves_original_404(self):
        error = HTTPError('redacted', 404, 'Not Found', {}, None)
        self.r.read_at = mock.Mock(side_effect=error)
        for head, path in [('f' * 40, SCORE), (self.absent, 'config/fixed.json'),
                           (self.absent, SCORE.replace('1794240000_formal', '../escape')),
                           (self.absent, SCORE + '.extra'), ('bad head', SCORE)]:
            with self.subTest(head=head, path=path):
                self.r.publication = lambda runs, h=head, p=path: self.r.read_at(h, p)
                with self.assertRaises(HTTPError) as caught:
                    self.publication([])
                self.assertIs(caught.exception, error)

    def test_all_other_transport_and_integrity_errors_propagate_unchanged(self):
        for error in (HTTPError('redacted', 403, 'Forbidden', {}, None),
                      HTTPError('redacted', 429, 'Limited', {}, None),
                      HTTPError('redacted', 503, 'Unavailable', {}, None),
                      TimeoutError('transport timeout'), ConnectionError('transport reset'),
                      RuntimeError('blob integrity mismatch')):
            with self.subTest(error=error):
                self.r.read_at = mock.Mock(side_effect=error)
                original = self.r.read_at
                with self.assertRaises(type(error)) as caught:
                    self.publication([self.runrow(self.absent, 1)])
                self.assertIs(caught.exception, error)
                self.assertIs(self.r.read_at, original)

    def test_failed_presence_proof_preserves_original_404(self):
        error = HTTPError('redacted', 404, 'Not Found', {}, None)
        self.r.read_at = mock.Mock(side_effect=error)
        for failure in (OSError('Git unavailable'), subprocess.TimeoutExpired(['git'], 5)):
            with self.subTest(error=failure):
                # Original reader is already independently controlled above.
                with mock.patch.object(subprocess, 'run', side_effect=failure):
                    with self.assertRaises(HTTPError) as caught:
                        self.publication([self.runrow(self.absent, 1)])
                self.assertIs(caught.exception, error)

    def test_nonzero_commit_or_tree_lookup_never_certifies_absence(self):
        error = HTTPError('redacted', 404, 'Not Found', {}, None)
        self.r.read_at = mock.Mock(side_effect=error)
        ok = subprocess.CompletedProcess(['git'], 0, b'', b'')
        failed = subprocess.CompletedProcess(['git'], 128, b'', b'not authenticated')
        for replies in ([failed], [ok, failed]):
            with self.subTest(replies=len(replies)), mock.patch.object(subprocess, 'run', side_effect=replies):
                with self.assertRaises(HTTPError) as caught:
                    self.publication([self.runrow(self.absent, 1)])
                self.assertIs(caught.exception, error)

    def test_presence_proof_is_readonly_exact_commit_path_and_bounded(self):
        original = subprocess.run
        probes = []
        def run(args, **kwargs):
            if args[:4] == ['git', '--no-replace-objects', '-c', 'safe.directory=*'] and args[4] in ('cat-file', 'ls-tree'):
                probes.append((args, kwargs))
            return original(args, **kwargs)
        with mock.patch.object(subprocess, 'run', side_effect=run):
            self.assertIsNone(self.publication([self.runrow(self.absent, 1)]))
        self.assertEqual(len(probes), 2)
        self.assertEqual(probes[0][0][-2:], ['-e', self.absent + '^{commit}'])
        self.assertEqual(probes[1][0][-5:], ['--full-tree', '-z', self.absent, '--', SCORE])
        for args, kwargs in probes:
            self.assertEqual(kwargs['timeout'], 5)
            self.assertEqual(kwargs['cwd'], self.root)
            self.assertTrue(kwargs['capture_output'])
            self.assertNotIn('input', kwargs)

    def test_original_server_clock_and_identity_predicates_are_not_bypassed(self):
        for key, value in [('head_branch', 'other'), ('event', 'schedule'),
                            ('conclusion', 'failure'), ('status', 'in_progress')]:
            row = self.runrow(self.present, 1)
            row[key] = value
            self.assertIsNone(self.publication([row]))
        for start, finish in [('2026-11-09T17:36:00Z', '2026-11-09T17:42:00Z'),
                              ('2026-11-09T17:39:00Z', '2026-11-09T18:02:00Z')]:
            row = self.runrow(self.present, 1)
            row['audit_job'].update(started_at=start, completed_at=finish)
            self.assertIsNone(self.publication([row]))

    def test_context_restores_reader_on_success_exception_and_cancellation(self):
        for failure in (None, ValueError('publication failure'), SystemExit(3), KeyboardInterrupt()):
            with self.subTest(failure=repr(failure)):
                original = self.r.read_at
                def call(*args):
                    if failure is not None:
                        raise failure
                    return 'unchanged return'
                self.r.publication = call
                if failure is None:
                    self.assertEqual(self.publication([]), 'unchanged return')
                else:
                    with self.assertRaises(type(failure)) as caught:
                        self.publication([])
                    self.assertIs(caught.exception, failure)
                self.assertIs(self.r.read_at, original)

    def test_no_negative_cache_survives_this_publication_call(self):
        # Same exact head/path absence must be proven again on a new invocation.
        original = subprocess.run
        calls = []
        def run(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)
        with mock.patch.object(subprocess, 'run', side_effect=run):
            self.assertIsNone(self.publication([self.runrow(self.absent, 1)]))
            first = len(calls)
            self.assertIsNone(self.publication([self.runrow(self.absent, 1)]))
        self.assertEqual(len(calls), first * 2)


if __name__ == '__main__':
    unittest.main()

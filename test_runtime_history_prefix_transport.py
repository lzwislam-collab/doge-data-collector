"""Call-local CSV reuse must equal the unmodified complete history validator."""
import ast
import csv
import hashlib
import importlib.util
import io
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

HERE = Path(__file__).parent
REFERENCE = HERE / 'reference' if (HERE / 'reference').is_dir() else HERE
COLUMNS = ['forecast_id', 'value']
RELATIVE = 'data/test.csv'


def normalize(body):
    return body.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').strip()


def records(body):
    return {row['forecast_id']: row for row in csv.DictReader(io.StringIO(normalize(body)))}


class PrefixHistoryTests(unittest.TestCase):
    def setUp(self):
        self.base = types.ModuleType('forward_validation')
        self.base.__file__ = str(REFERENCE / 'forward_validation.py')
        self.base.__dict__.update(csv=csv, io=io, subprocess=subprocess,
                                  datetime=datetime, timezone=timezone)
        tree = ast.parse(Path(self.base.__file__).read_bytes())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name in ('git', 'history_receipts')]
        self.assertEqual({n.name for n in functions}, {'git', 'history_receipts'})
        exec(compile(ast.Module(body=functions, type_ignores=[]), self.base.__file__, 'exec'),
             self.base.__dict__)
        publisher = types.ModuleType('publish_forward_evidence')
        publisher.__file__ = str(REFERENCE / 'publish_forward_evidence.py')
        self.modules = mock.patch.dict(sys.modules, {'forward_validation': self.base,
                'publish_forward_evidence': publisher})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location('prefix_subject', HERE / 'runtime_publication_state.py')
        self.subject = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.subject)
        self.assertIsNone(self.subject._dependency_reason())

    def outcome(self, function, *args):
        try:
            return ('ok', function(*args))
        except Exception as error:
            return ('error', type(error), str(error))

    def compare(self, bodies, current=None, stamps=None, late_bad_frame=False, parsed=None):
        bodies = [b.encode('utf-8') if isinstance(b, str) else b for b in bodies]
        revisions = [format(i + 1, '040x') for i in range(len(bodies))]
        by_revision = dict(zip(revisions, bodies))
        stamps = stamps or ['2026-11-09T00:%02d:%02d+00:00' % (i // 60, i % 60)
                            for i in range(len(bodies))]
        by_stamp = dict(zip(revisions, stamps))
        current = records(bodies[-1]) if current is None else current

        def git(root, *args):
            if args == ('rev-parse', '--is-shallow-repository'):
                return 'false'
            if args[:1] == ('log',):
                return '\n'.join(revisions)
            if len(args) == 2 and args[0] == 'show':
                return normalize(by_revision[args[1].split(':')[0]])
            if args[:3] == ('show', '-s', '--format=%cI'):
                return by_stamp[args[3]]
            raise AssertionError(args)

        def oid(body):
            return hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()

        by_oid = {oid(body): body for body in bodies}

        def check(root, chunk, relative):
            return ''.join('%s blob %d %s\n' % (oid(by_revision[r]), len(by_revision[r]), r)
                           for r in chunk).encode()

        calls = []

        def batch(root, objects):
            calls.append(list(objects))
            if late_bad_frame and len(calls) == 2:
                return b'corrupt native frame'
            return b''.join((o + ' blob ' + str(len(by_oid[o])) + '\n').encode() + by_oid[o] + b'\n'
                            for o in objects)

        with mock.patch.object(self.base, 'git', side_effect=git):
            expected = self.outcome(self.base.history_receipts, HERE, RELATIVE, COLUMNS, current)
            with mock.patch.object(self.subject, '_log_output', return_value=''.join(
                    r + '\0' + by_stamp[r] + '\n' for r in revisions).encode()), \
                 mock.patch.object(self.subject, '_batch_check_output', side_effect=check), \
                 mock.patch.object(self.subject, '_batch_output', side_effect=batch), \
                 mock.patch.object(self.subject, 'BATCH_SIZE', 1):
                original = self.subject.csv.DictReader

                def reader(stream, *args, **kwargs):
                    if parsed is not None:
                        parsed.append((stream.getvalue(), kwargs.copy()))
                    return original(stream, *args, **kwargs)

                with mock.patch.object(self.subject.csv, 'DictReader', side_effect=reader):
                    diagnostics = {}
                    actual = self.outcome(self.subject.history_receipts, HERE, RELATIVE, COLUMNS,
                                          current, diagnostics)
            self.assertEqual(actual, expected)
        return actual, diagnostics, calls

    def test_append_parses_only_new_tail_after_strict_closed_prefix(self):
        parsed = []
        result, report, calls = self.compare(['forecast_id,value\n0,a',
            'forecast_id,value\n0,a\n1,b', 'forecast_id,value\n0,a\n1,b\n2,c'], parsed=parsed)
        self.assertEqual(result[0], 'ok')
        self.assertEqual(report['mode'], 'native_batch')
        self.assertEqual(len(calls), 3)
        self.assertFalse(any(body == 'forecast_id,value\n0,a\n1,b' for body, kw in parsed))
        self.assertTrue(any(body == '1,b' for body, kw in parsed))

    def test_identical_normalized_body_is_not_reparsed(self):
        parsed = []
        self.compare(['forecast_id,value\r\n0,a\r\n', '\n forecast_id,value\n0,a \n'], parsed=parsed)
        self.assertEqual(sum(body == 'forecast_id,value\n0,a' for body, kw in parsed), 1)

    def test_reformat_semantically_identical_uses_full_parse(self):
        parsed = []
        self.compare(['forecast_id,value\n0,a', 'forecast_id,value\n"0","a"'], parsed=parsed)
        self.assertTrue(any(body == 'forecast_id,value\n"0","a"' for body, kw in parsed))

    def test_equivalence_normalization_multiline_blank_extra_missing_fields(self):
        cases = [
            ['forecast_id,value', 'forecast_id,value\n0,a'],
            ['forecast_id,value\r\n0,a\r\n', 'forecast_id,value\r0,a\r1,b\r'],
            ['forecast_id,value\n0,"a\nb"', 'forecast_id,value\n0,"a\nb"\n1,"c\nd"'],
            ['forecast_id,value\n0,a', 'forecast_id,value\n0,a\n\n1,b\n\n'],
            ['forecast_id,value\n0,a', 'forecast_id,value\n0,a\n1,b,c'],
            ['forecast_id,value\n0,a', 'forecast_id,value\n0,a\n1'],
            ['forecast_id,value\n0,a ', 'forecast_id,value\n0,a\n1,b '],
            ['forecast_id,value\n0,""', 'forecast_id,value\n0,""\n1, "b"'],
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(self.compare(case)[0][0], 'ok')

    def test_unclosed_quote_prefix_must_never_be_reused(self):
        # Default CSV accepts the original truncated field. The next revision
        # extends that same field, so treating the old EOF as a record is wrong.
        old = 'forecast_id,value\n0,"open'
        new = old + '\ncontinued"\n1,b'
        result, report, _ = self.compare([old, new])
        self.assertEqual(result[0], 'error')
        self.assertEqual(result[2], 'committed record revised: ' + RELATIVE)

    def test_strict_rejection_does_not_add_a_new_validation_error(self):
        for first in ('forecast_id,value\n0,"a" trailing', 'forecast_id,value\n0,"unclosed',
                      'forecast_id,value\n0,a"unquoted'):
            with self.subTest(first=first):
                self.assertEqual(self.compare([first, first])[0][0], 'ok')

    def test_strict_only_rejected_body_remains_on_complete_nonstrict_parser(self):
        first = 'forecast_id,value\n0,"a" trailing'
        parsed = []
        self.compare([first, first, first + '\n1,b'], parsed=parsed)
        self.assertEqual(sum(body == first for body, kw in parsed), 2)
        self.assertTrue(any(body == first + '\n1,b' for body, kw in parsed))

    def test_unclosed_new_tail_disables_reuse_for_following_revision(self):
        first = 'forecast_id,value\n0,a'
        second = first + '\n1,"open'
        third = second + '\ncontinued"'
        self.assertEqual(self.compare([first, second, third])[0][0], 'error')

    def test_duplicate_ids_in_tail_or_prior_are_still_exact_errors(self):
        for tail in ('0,b', '1,b\n1,b', ',empty'):
            with self.subTest(tail=tail):
                result = self.compare(['forecast_id,value\n0,a', 'forecast_id,value\n0,a\n' + tail])[0]
                self.assertEqual(result[0], 'error')
                self.assertEqual(result[2], 'duplicate historical ID')

    def test_schema_revision_deletion_and_edit_then_restore_fail_at_same_revision(self):
        cases = [
            ['forecast_id,value\n0,a', 'value,forecast_id\na,0'],
            ['forecast_id,value\n0,a\n1,b', 'forecast_id,value\n0,a', 'forecast_id,value\n0,a\n1,b'],
            ['forecast_id,value\n0,a', 'forecast_id,value\n0,b', 'forecast_id,value\n0,a'],
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(self.compare(case)[0][0], 'error')

    def test_current_revision_check_and_uncommitted_append_preserved(self):
        history = ['forecast_id,value\n0,a', 'forecast_id,value\n0,a\n1,b']
        self.assertEqual(self.compare(history, current=records(b'forecast_id,value\n0,a\n1,b\n2,c'))[0][0], 'ok')
        for current in ({}, {'0': {'forecast_id': '0', 'value': 'changed'}}):
            self.assertEqual(self.compare(history, current=current)[0][2], 'current record revised: ' + RELATIVE)

    def test_late_bad_native_frame_replays_complete_original(self):
        result, report, calls = self.compare(['forecast_id,value\n0,a', 'forecast_id,value\n0,a\n1,b'],
                                             late_bad_frame=True)
        self.assertEqual(result[0], 'ok')
        self.assertEqual(report['mode'], 'legacy_full_fallback')
        self.assertEqual(len(calls), 2)

    def test_invalid_commit_time_replays_original_and_preserves_exception(self):
        result, report, _ = self.compare(['forecast_id,value\n0,a'], stamps=['invalid'])
        self.assertEqual(result[0], 'error')
        self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_cache_is_local_to_call_and_no_previous_receipts_survive(self):
        first = self.compare(['forecast_id,value\n0,a'])[0][1]
        second = self.compare(['forecast_id,value\n9,z'])[0][1]
        self.assertEqual(set(first), {'0'})
        self.assertEqual(set(second), {'9'})
        self.assertEqual(self.compare(['forecast_id,value\n0,changed'])[0][0], 'ok')

    def test_many_commits_keep_every_original_receipt_and_blob_validation(self):
        history = []
        body = 'forecast_id,value'
        for fid in range(130):
            body += '\n%d,value%d' % (fid, fid)
            history.append(body)
        result, report, calls = self.compare(history)
        self.assertEqual(result[0], 'ok')
        self.assertEqual(len(result[1]), 130)
        self.assertEqual(report['attempted_commit_count'], 130)
        self.assertEqual(len(calls), 130)

    def test_adversarial_random_csv_differential(self):
        rng = random.Random(20261110)
        fields = ['a', '', 'a,b', 'a\nb', 'a"b', ' leading', 'trailing ', '\n', '"']
        for sample in range(35):
            stream = io.StringIO()
            writer = csv.writer(stream, lineterminator='\n')
            writer.writerow(COLUMNS)
            history = []
            for fid in range(4):
                writer.writerow([str(fid), rng.choice(fields)])
                history.append(stream.getvalue())
            with self.subTest(sample=sample):
                self.compare(history)

    def test_nonstrict_malformed_quotes_and_appended_fragments_differential(self):
        rng = random.Random(1794268800)
        for sample in range(100):
            body = 'forecast_id,value\n0,' + ''.join(rng.choice('ab," ') for _ in range(9))
            history = [body]
            for fid in range(1, 4):
                body += '\n%d,' % fid + ''.join(rng.choice('ab," ') for _ in range(9))
                history.append(body)
            with self.subTest(sample=sample):
                self.compare(history)


class RealGitPrefixTests(unittest.TestCase):
    setUp = PrefixHistoryTests.setUp

    def test_true_git_full_history_first_receipts_exact_and_objects_all_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*args):
                return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True).stdout
            git('init', '-q')
            git('config', 'user.name', 'fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'core.autocrlf', 'false')
            path = root / RELATIVE
            path.parent.mkdir()
            body = 'forecast_id,value'
            for fid in range(4):
                body += '\n%d,value%d' % (fid, fid)
                path.write_bytes(body.encode())
                git('add', RELATIVE)
                git('commit', '-qm', 'real prefix append')
            current = records(body.encode())
            expected = self.base.history_receipts(root, RELATIVE, COLUMNS, current)
            report = {}
            original = self.subject._bodies
            with mock.patch.object(self.subject, '_bodies', wraps=original) as verified:
                actual = self.subject.history_receipts(root, RELATIVE, COLUMNS, current, report)
            self.assertEqual(actual, expected)
            self.assertEqual(report['attempted_commit_count'], 4)
            self.assertEqual(report['mode'], 'native_batch')
            self.assertEqual(sum(len(call.args[1]) for call in verified.call_args_list), 4)


if __name__ == '__main__':
    unittest.main()

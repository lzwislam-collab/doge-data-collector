"""Real Git differential checks against the unchanged frozen P2 verifier."""
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import p2_paired_execution_v3_1 as frozen
import runtime_deadline_service as runtime

SCRIPT = Path(__file__).with_name('runtime_p2_history.py')


class RuntimeP2HistoryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'Independent full-history verifier is not implemented')
        spec = importlib.util.spec_from_file_location('runtime_p2_history_test_subject', SCRIPT)
        self.subject = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.subject)
        self.temp = tempfile.TemporaryDirectory(prefix='runtime-p2-history-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'History Test')
        self.git('config', 'user.email', 'history@example.invalid')
        self.path = self.root / frozen.EVENTS
        self.path.parent.mkdir(parents=True)

    def git(self, *args, check=True):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, check=check).stdout

    def row(self, number, previous=None, value='original'):
        row = {'quote_utc': (datetime(2026, 11, 8, tzinfo=timezone.utc)
                             + timedelta(seconds=number)).isoformat(),
               'previous_sha256': previous, 'value': value}
        row['record_sha256'] = hashlib.sha256(json.dumps(
            row, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        return row

    def write(self, rows):
        self.path.write_text(''.join(json.dumps(row, sort_keys=True, separators=(',', ':'))
                                     + '\n' for row in rows), encoding='utf-8')

    def commit(self, rows=None, message='event'):
        if rows is not None:
            self.write(rows)
        self.git('add', '-A')
        self.git('commit', '--allow-empty', '-qm', message)
        return self.git('rev-parse', 'HEAD').decode().strip()

    def run_new(self):
        diagnostics = {}
        result = self.subject.history_guard(self.root, engine=frozen, diagnostics=diagnostics)
        return result, diagnostics

    def same_error(self, expected):
        with self.assertRaises(Exception) as original:
            frozen.history_guard(self.root)
        self.assertIn(expected, str(original.exception))
        diagnostics = {}
        with self.assertRaises(type(original.exception)) as candidate:
            self.subject.history_guard(self.root, engine=frozen, diagnostics=diagnostics)
        self.assertEqual(str(candidate.exception), str(original.exception))
        return diagnostics

    def test_complete_commits_and_uncommitted_append_match_original(self):
        rows = []
        for number in range(3):
            rows.append(self.row(number, rows[-1]['record_sha256'] if rows else None))
            self.commit(rows)
        rows.append(self.row(3, rows[-1]['record_sha256']))
        self.write(rows)
        result, report = self.run_new()
        self.assertEqual(result, rows)
        self.assertEqual(result, frozen.history_guard(self.root))
        self.assertEqual(report['mode'], 'native_batch')
        self.assertEqual(report['attempted_commit_count'], 3)
        self.assertEqual(report['status'], 'ok')

    def test_no_committed_path_allows_original_valid_uncommitted_chain(self):
        self.commit()
        rows = [self.row(0)]
        self.write(rows)
        result, report = self.run_new()
        self.assertEqual(result, frozen.history_guard(self.root))
        self.assertEqual(result, rows)
        self.assertEqual(report['attempted_commit_count'], 0)

    def test_deleted_history_then_restored_current_tail_is_still_rejected(self):
        row = self.row(0)
        self.commit([row])
        self.commit([])
        self.commit([row])
        self.same_error('historic tail removed/revised')

    def test_modified_history_then_restored_current_bytes_is_still_rejected(self):
        original = self.row(0)
        self.commit([original])
        self.commit([self.row(0, value='tampered')])
        self.commit([original])
        self.same_error('historic tail removed/revised')

    def test_bad_current_hash_wins_before_history_git_failure(self):
        row = self.row(0)
        self.commit([row])
        row['value'] = 'bad hash'
        self.write([row])
        shutil.rmtree(self.root / '.git')
        self.same_error('P2 event hash chain changed')

    def test_bad_historic_hash_wins_before_later_removal(self):
        first = self.row(0)
        self.commit([first])
        bad = self.row(1, first['record_sha256'])
        bad['record_sha256'] = '0' * 64
        self.commit([first, bad])
        self.commit([first])
        self.same_error('P2 historic event hash mismatch')

    def test_bad_historic_previous_link_is_not_hidden_by_restoration(self):
        first = self.row(0)
        self.commit([first])
        self.commit([first, self.row(1, '0' * 64)])
        self.commit([first])
        self.same_error('P2 historic event hash mismatch')

    def test_combined_merge_patch_keeps_original_hunk_rejection(self):
        first = self.row(0)
        self.commit([first])
        self.git('checkout', '-qb', 'side')
        (self.root / 'side').write_text('side')
        self.commit()
        self.git('checkout', '-q', 'main')
        (self.root / 'main').write_text('main')
        self.commit()
        self.git('merge', '--no-ff', '--no-commit', 'side')
        merge = self.commit([first, self.row(1, first['record_sha256'])])
        patch = self.git('show', '--format=', '--unified=0', '--no-renames', merge, '--', frozen.EVENTS)
        self.assertIn(b'@@@', patch)
        self.same_error('P2 historic tail removed/revised')

    def test_explicit_empty_patch_uses_real_original_show(self):
        first = self.commit([self.row(0)])
        empty = self.commit()
        report = {'native_batch_calls': 0, 'empty_fallback_show_calls': 0}
        got = list(self.subject._native_patches(self.root, [first, empty], frozen, report))
        expected = [self.git('show', '--format=', '--unified=0', '--no-renames', oid,
                             '--', frozen.EVENTS) for oid in [first, empty]]
        self.assertEqual(got, expected)
        self.assertEqual(got[-1], b'')
        self.assertEqual(report['empty_fallback_show_calls'], 1)

    def test_native_git_failure_performs_visible_complete_legacy_fallback(self):
        rows = [self.row(0)]
        self.commit(rows)
        with mock.patch.object(self.subject, '_batch_output', side_effect=OSError('native failed')):
            result, report = self.run_new()
        self.assertEqual(result, rows)
        self.assertEqual(report['mode'], 'legacy_full_fallback')
        self.assertTrue(report['fallback_reason'])
        self.assertEqual(report['status'], 'ok')

    def test_legacy_fallback_still_rejects_restored_historical_tampering(self):
        original = self.row(0)
        self.commit([original])
        self.commit([])
        self.commit([original])
        with mock.patch.object(self.subject, '_batch_output', side_effect=OSError('native failed')):
            report = self.same_error('historic tail removed/revised')
        self.assertEqual(report['mode'], 'legacy_full_fallback')
        self.assertEqual(report['status'], 'error')

    def test_truncated_native_patch_falls_back_instead_of_accepting_partial_transport(self):
        rows = [self.row(0)]
        self.commit(rows)
        real_batch = self.subject._batch_output
        def truncate(*args):
            return real_batch(*args)[:-1]
        with mock.patch.object(self.subject, '_batch_output', side_effect=truncate):
            result, report = self.run_new()
        self.assertEqual(result, rows)
        self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_reused_diagnostics_do_not_report_a_prior_call_fallback(self):
        self.commit([self.row(0)])
        report = {}
        with mock.patch.object(self.subject, '_batch_output', side_effect=OSError('native failed')):
            self.subject.history_guard(self.root, engine=frozen, diagnostics=report)
        self.assertIn('fallback_reason', report)
        self.subject.history_guard(self.root, engine=frozen, diagnostics=report)
        self.assertEqual(report['mode'], 'native_batch')
        self.assertNotIn('fallback_reason', report)

    def test_reordered_or_missing_nonempty_frames_trigger_full_fallback(self):
        first = self.row(0)
        rows = [first, self.row(1, first['record_sha256'])]
        self.commit(rows[:1])
        self.commit(rows)
        real_batch = self.subject._batch_output
        for mode in ('reverse', 'missing'):
            def corrupt(root, oids, marker, engine):
                body = real_batch(root, oids, marker, engine)
                blocks = body.split(marker)[1:]
                blocks = list(reversed(blocks)) if mode == 'reverse' else blocks[1:]
                return b''.join(marker + block for block in blocks)
            with self.subTest(mode=mode), mock.patch.object(self.subject, '_batch_output', side_effect=corrupt):
                result, report = self.run_new()
            self.assertEqual(result, rows)
            self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_next_call_reads_complete_fresh_rebased_history(self):
        first = self.row(0)
        self.commit([first])
        self.git('checkout', '-qb', 'feature')
        rows = [first, self.row(1, first['record_sha256'])]
        self.commit(rows)
        _, before = self.run_new()
        self.git('checkout', '-q', 'main')
        (self.root / 'unrelated').write_text('new base')
        self.commit()
        self.git('checkout', '-q', 'feature')
        self.git('rebase', 'main')
        result, after = self.run_new()
        self.assertEqual(result, rows)
        self.assertEqual(result, frozen.history_guard(self.root))
        self.assertEqual(after['attempted_commit_count'], 2)
        self.assertNotEqual(before['ordered_oids_sha256'], after['ordered_oids_sha256'])

    def append_commits(self, count):
        rows = []
        for number in range(count):
            rows.append(self.row(number, rows[-1]['record_sha256'] if rows else None))
            self.commit(rows)
        return rows

    def test_real_129_commit_boundary_matches_every_original_patch_and_result(self):
        h = self
        rows = self.append_commits(129)
        oids = h.git('log', '--reverse', '--format=%H', '--', frozen.EVENTS).decode().splitlines()
        report = {}
        batched = list(h.subject._native_patches(h.root, oids, frozen, report))
        original = [h.git('show', '--format=', '--unified=0', '--no-renames', oid,
                          '--', frozen.EVENTS) for oid in oids]
        self.assertEqual(batched, original)
        self.assertEqual(report['native_batch_calls'], 2)
        actual, diag = h.run_new()
        self.assertEqual(actual, rows)
        self.assertEqual(actual, frozen.history_guard(h.root))
        self.assertEqual(diag['attempted_commit_count'], 129)
        self.assertEqual(diag['native_batch_calls'], 2)

    def test_second_batch_failure_restarts_current_and_entire_legacy_history(self):
        h = self
        rows = self.append_commits(3)
        real_batch = h.subject._batch_output
        calls = []
        def fail_second(root, oids, marker, engine):
            calls.append(list(oids))
            if len(calls) == 2:
                raise OSError('review second batch read failure')
            return real_batch(root, oids, marker, engine)
        with mock.patch.object(h.subject, 'BATCH_SIZE', 2), \
             mock.patch.object(h.subject, '_batch_output', side_effect=fail_second), \
             mock.patch.object(frozen, 'read_events', wraps=frozen.read_events) as fresh, \
             mock.patch.object(frozen, 'history_guard', wraps=frozen.history_guard) as legacy:
            actual, report = h.run_new()
        self.assertEqual(actual, rows)
        self.assertEqual(fresh.call_count, 2)
        legacy.assert_called_once_with(h.root)
        self.assertEqual(report['mode'], 'legacy_full_fallback')
        self.assertEqual(report['native_batch_calls'], 2)
        self.assertEqual(report['status'], 'ok')

    def test_current_valid_truncation_is_rejected_at_original_prefix_gate(self):
        h = self
        rows = self.append_commits(2)
        h.write(rows[:1])
        report = h.same_error('P2 committed event tail removed/changed')
        self.assertEqual(report['mode'], 'native_batch')
        self.assertEqual(report['status'], 'error')

    def test_runtime_keeps_guard_all_snapshot_checks_none_return_and_identity(self):
        for name in (*frozen.CODE_FILES, frozen.POLICY, frozen.SYSTEM,
                     'system/current_p2_execution_shadow_v3_1.json'):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(frozen.ROOT / name, target)
        self.commit()
        import test_p2_execution_records_v3_1 as fixtures
        helper = fixtures.RecordsTests()
        for index in range(2):
            snapshot = helper.snapshot()
            moment = datetime(2026, 11, 8, tzinfo=timezone.utc) + timedelta(seconds=index)
            snapshot['captured_at_utc'] = moment.isoformat()
            snapshot['requested_at_utc'] = (moment-timedelta(seconds=1)).isoformat()
            snapshot['instrument_observed_at_utc'] = (moment-timedelta(seconds=2)).isoformat()
            raw = json.loads(snapshot['raw_response_utf8'])
            raw['data'][0]['ts'] = str(int(moment.timestamp()*1000))
            snapshot['raw_response_utf8'] = json.dumps(raw)
            last = frozen.append_event(self.root, snapshot, [])
            self.commit()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertIsNone(runtime.validate_p2(self.root))
        report = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(report['kind'], 'runtime_p2_history_validation')
        self.assertEqual(report['mode'], 'native_batch')
        self.assertEqual(report['validation_status'], 'ok')
        self.assertEqual(report['module_sha256'], hashlib.sha256(SCRIPT.read_bytes()).hexdigest())
        (self.root / last['snapshot_path']).write_bytes(b'corrupt second snapshot')
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'P2 raw book snapshot hash mismatch'):
                runtime.validate_p2(self.root)
        (self.root / 'p2_paired_execution_v3_1.py').write_text('changed producer')
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'P2 execution system/code integrity mismatch'):
                runtime.validate_p2(self.root)


if __name__ == '__main__':
    unittest.main()

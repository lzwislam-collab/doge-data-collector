"""Real Git differential tests for the unchanged paired history contract."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import paired_forward_cohort as frozen


SCRIPT = Path(__file__).with_name('runtime_paired_history.py')


class RuntimePairedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'Native paired history adapter is not implemented')
        spec = importlib.util.spec_from_file_location('paired_history_test_subject', SCRIPT)
        self.subject = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.subject)
        temporary = tempfile.TemporaryDirectory(prefix='runtime-paired-history-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git('init', '-q', '-b', 'main')
        self.path = self.root / frozen.REG
        self.path.parent.mkdir(parents=True)

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME='Paired History Test',
                   GIT_AUTHOR_EMAIL='paired@example.invalid',
                   GIT_COMMITTER_NAME='Paired History Test',
                   GIT_COMMITTER_EMAIL='paired@example.invalid')
        return subprocess.run(['git', *args], cwd=self.root, env=env,
                              check=True, capture_output=True).stdout

    def row(self, number, previous=None, value='original', fid=None):
        row = {'production': {'forecast_id': fid or f'forecast-{number:03d}'},
               'previous_sha256': previous, 'value': value}
        row['record_sha256'] = hashlib.sha256(json.dumps(
            row, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        return row

    def write(self, rows):
        self.path.write_text(''.join(json.dumps(row, sort_keys=True, separators=(',', ':'))
                                     + '\n' for row in rows), encoding='utf-8')
        for row in rows:
            record = self.path.parent / 'records' / (row['production']['forecast_id'] + '.json')
            record.parent.mkdir(exist_ok=True)
            record.write_text(json.dumps(row), encoding='utf-8')

    def commit(self, rows=None):
        if rows is not None:
            self.write(rows)
        self.git('add', '-A')
        self.git('commit', '--allow-empty', '-qm', 'paired evidence')
        return self.git('rev-parse', 'HEAD').decode().strip()

    def append_commits(self, count):
        rows = []
        for number in range(count):
            rows.append(self.row(number, rows[-1]['record_sha256'] if rows else None))
            self.commit(rows)
        return rows

    def run_new(self, cohort=frozen):
        report = {}
        result = self.subject.history_guard(self.root, cohort=cohort, diagnostics=report)
        return result, report

    def same_error(self, expected):
        with self.assertRaises(Exception) as original:
            frozen.history_guard(self.root)
        self.assertIn(expected, str(original.exception))
        report = {}
        with self.assertRaises(type(original.exception)) as candidate:
            self.subject.history_guard(self.root, cohort=frozen, diagnostics=report)
        self.assertEqual(str(candidate.exception), str(original.exception))
        self.assertEqual(report['status'], 'error')
        return report

    def test_complete_commits_and_uncommitted_append_match_original(self):
        rows = self.append_commits(3)
        rows.append(self.row(3, rows[-1]['record_sha256']))
        self.write(rows)
        actual, report = self.run_new()
        self.assertEqual(actual, rows)
        self.assertEqual(actual, frozen.history_guard(self.root))
        self.assertEqual(report['mode'], 'native_batch')
        self.assertEqual(report['attempted_commit_count'], 3)
        self.assertEqual(report['status'], 'ok')

    def test_uncommitted_chain_without_path_history_keeps_individual_checks(self):
        self.commit()
        rows = [self.row(0)]
        self.write(rows)
        actual, report = self.run_new()
        self.assertEqual(actual, rows)
        self.assertEqual(actual, frozen.history_guard(self.root))
        self.assertEqual(report['attempted_commit_count'], 0)
        (self.path.parent / 'records' / 'forecast-000.json').unlink()
        self.same_error('individual paired record absent/changed')

    def test_deleted_history_restored_to_original_current_bytes_is_rejected(self):
        row = self.row(0)
        self.commit([row])
        self.commit([])
        self.commit([row])
        self.same_error('historic paired chain removed/changed away from append boundary')

    def test_modified_history_restored_to_original_current_bytes_is_rejected(self):
        row = self.row(0)
        self.commit([row])
        self.commit([self.row(0, value='tampered')])
        self.commit([row])
        self.same_error('historic paired chain removed/changed away from append boundary')

    def test_invalid_current_hash_wins_before_history_git_failure(self):
        row = self.row(0)
        self.commit([row])
        row['value'] = 'invalid hash'
        self.write([row])
        shutil.rmtree(self.root / '.git')
        self.same_error('paired chain hash mismatch')

    def test_duplicate_current_forecast_wins_before_history_git_failure(self):
        first = self.row(0)
        second = self.row(1, first['record_sha256'], fid='forecast-000')
        self.write([first, second])
        shutil.rmtree(self.root / '.git')
        self.same_error('duplicate paired original ID')

    def test_bad_historical_hash_is_not_hidden_by_valid_current_restoration(self):
        first = self.row(0)
        self.commit([first])
        bad = self.row(1, first['record_sha256'])
        bad['record_sha256'] = '0' * 64
        self.commit([first, bad])
        self.commit([first])
        self.same_error('historical appended paired hash/duplicate mismatch')

    def test_bad_historical_previous_hash_is_not_hidden_by_restoration(self):
        first = self.row(0)
        self.commit([first])
        self.commit([first, self.row(1, '0' * 64)])
        self.commit([first])
        self.same_error('historical appended paired hash/duplicate mismatch')

    def test_historical_duplicate_forecast_is_not_hidden_by_restoration(self):
        first = self.row(0)
        self.commit([first])
        self.commit([first, self.row(1, first['record_sha256'], fid='forecast-000')])
        self.commit([first])
        self.same_error('historical appended paired hash/duplicate mismatch')

    def test_current_prefix_truncation_is_rejected(self):
        rows = self.append_commits(2)
        self.write(rows[:1])
        self.same_error('committed paired records removed/changed')

    def test_missing_individual_record_is_rejected(self):
        self.append_commits(2)
        (self.path.parent / 'records' / 'forecast-001.json').unlink()
        self.same_error('individual paired record absent/changed')

    def test_changed_individual_record_is_rejected(self):
        self.append_commits(2)
        (self.path.parent / 'records' / 'forecast-000.json').write_text('{}')
        self.same_error('individual paired record absent/changed')

    def test_combined_merge_preserves_original_unknown_hunk_error(self):
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
        patch = self.git('show', '--format=', '--unified=0', '--no-renames', merge, '--', frozen.REG)
        self.assertIn(b'@@@', patch)
        self.same_error('unknown paired history patch')

    def test_explicit_empty_patch_matches_original_single_show(self):
        first = self.commit([self.row(0)])
        empty = self.commit()
        report = {}
        actual = list(self.subject._native_patches(self.root, [first, empty], frozen, report))
        original = [self.git('show', '--format=', '--unified=0', '--no-renames', oid,
                             '--', frozen.REG) for oid in (first, empty)]
        self.assertEqual(actual, original)
        self.assertEqual(actual[-1], b'')
        self.assertEqual(report['empty_fallback_show_calls'], 1)

    def test_real_129_commit_boundary_matches_all_original_patch_bytes_and_records(self):
        rows = self.append_commits(129)
        oids = self.git('log', '--reverse', '--format=%H', '--', frozen.REG).decode().splitlines()
        report = {}
        actual = list(self.subject._native_patches(self.root, oids, frozen, report))
        original = [self.git('show', '--format=', '--unified=0', '--no-renames', oid,
                             '--', frozen.REG) for oid in oids]
        self.assertEqual(actual, original)
        self.assertEqual(len(actual), 129)
        self.assertEqual(report['native_batch_calls'], 2)
        result, report = self.run_new()
        self.assertEqual(result, rows)
        self.assertEqual(result, frozen.history_guard(self.root))
        self.assertEqual(report['attempted_commit_count'], 129)
        self.assertEqual(report['native_batch_calls'], 2)

    def test_second_batch_failure_restarts_current_read_and_every_original_commit(self):
        rows = self.append_commits(129)
        ordered = self.git('log', '--reverse', '--format=%H', '--', frozen.REG).decode().splitlines()
        real_batch = self.subject._batch_output
        real_command = frozen.publication.command
        real_read = frozen.read_chain
        batches = []
        legacy_oids = []
        reads = []
        def fail_second(root, oids, marker, cohort):
            batches.append(list(oids))
            if len(batches) == 2:
                raise OSError('second native batch failed')
            return real_batch(root, oids, marker, cohort)
        def trace_command(root, *args):
            if args[:4] == ('show', '--format=', '--unified=0', '--no-renames'):
                legacy_oids.append(args[4])
            return real_command(root, *args)
        def trace_read(path):
            reads.append(path)
            return real_read(path)
        with mock.patch.object(self.subject, '_batch_output', side_effect=fail_second), \
             mock.patch.object(frozen.publication, 'command', side_effect=trace_command), \
             mock.patch.object(frozen, 'read_chain', side_effect=trace_read):
            result, report = self.run_new()
        self.assertEqual(result, rows)
        self.assertEqual(legacy_oids, ordered)
        self.assertEqual(reads, [self.path, self.path])
        self.assertEqual(report['mode'], 'legacy_full_fallback')
        self.assertEqual(report['native_batch_calls'], 2)

    def test_native_read_failure_full_fallback_still_rejects_restored_tampering(self):
        row = self.row(0)
        self.commit([row])
        self.commit([])
        self.commit([row])
        with mock.patch.object(self.subject, '_batch_output', side_effect=OSError('native failed')):
            report = self.same_error('historic paired chain removed/changed away from append boundary')
        self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_bad_frames_fall_back_to_complete_original_validation(self):
        rows = self.append_commits(2)
        real_batch = self.subject._batch_output
        for kind in ('truncated', 'reversed', 'missing_nonempty', 'nul_payload'):
            def corrupt(root, oids, marker, cohort):
                body = real_batch(root, oids, marker, cohort)
                if kind == 'truncated':
                    return body[:-1]
                if kind == 'nul_payload':
                    return body.replace(b'diff --git', b'\0diff --git', 1)
                blocks = body.split(marker)[1:]
                blocks = list(reversed(blocks)) if kind == 'reversed' else blocks[1:]
                return b''.join(marker + block for block in blocks)
            with self.subTest(kind=kind), mock.patch.object(self.subject, '_batch_output', side_effect=corrupt):
                result, report = self.run_new()
                self.assertEqual(result, rows)
                self.assertEqual(result, frozen.history_guard(self.root))
                self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_dependency_identity_change_uses_original_full_fallback(self):
        rows = self.append_commits(2)
        for attribute in ('self', 'publication', 'depth'):
            changed = types.SimpleNamespace(**vars(frozen))
            dependency = frozen if attribute == 'self' else getattr(frozen, attribute)
            copy = self.root / (attribute + '.py')
            copy.write_bytes(Path(dependency.__file__).read_bytes() + b'\n# changed identity\n')
            if attribute == 'self':
                changed.__file__ = str(copy)
            else:
                replacement = types.SimpleNamespace(**vars(dependency))
                replacement.__file__ = str(copy)
                setattr(changed, attribute, replacement)
            with self.subTest(attribute=attribute):
                result, report = self.run_new(changed)
                self.assertEqual(result, rows)
                self.assertEqual(report['mode'], 'legacy_full_fallback')
                self.assertEqual(report['fallback_reason'], 'dependency_identity_changed')

    def test_fresh_rebased_history_is_not_served_from_prior_validation(self):
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

    def test_reused_diagnostics_do_not_keep_previous_fallback(self):
        rows = self.append_commits(1)
        report = {}
        with mock.patch.object(self.subject, '_batch_output', side_effect=OSError('native failed')):
            self.subject.history_guard(self.root, cohort=frozen, diagnostics=report)
        self.assertIn('fallback_reason', report)
        result = self.subject.history_guard(self.root, cohort=frozen, diagnostics=report)
        self.assertEqual(result, rows)
        self.assertEqual(report['mode'], 'native_batch')
        self.assertNotIn('fallback_reason', report)

    def test_emitted_diagnostics_are_bounded_metadata_with_actual_module_identity(self):
        self.append_commits(1)
        _, report = self.run_new()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.subject.emit_diagnostics(report, validation_succeeded=True)
        row = json.loads(output.getvalue())
        self.assertEqual(row['kind'], 'runtime_paired_history_validation')
        self.assertEqual(row['mode'], 'native_batch')
        self.assertEqual(row['validation_status'], 'ok')
        self.assertEqual(row['module_sha256'], hashlib.sha256(SCRIPT.read_bytes()).hexdigest())
        self.assertNotIn('forecast-000', output.getvalue())


if __name__ == '__main__':
    unittest.main()

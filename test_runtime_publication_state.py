"""Real Git differential tests for the independent live publication verifier.

Frozen validators execute unchanged. Only malformed native transport and module
source identity are injected; all ledgers, commits and original errors are real.
"""
import contextlib
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import direction_forward_v42 as frozen
import forward_validation as original
import forward_validation_v2 as v2
import publish_forward_evidence as publisher
import quote_evidence_v2 as evidence


SCRIPT = Path(__file__).with_name('runtime_publication_state.py')
RELATIVE = 'data/test_history.csv'
COLUMNS = ['forecast_id', 'value']
STAMP = datetime(2026, 10, 10, 8, tzinfo=timezone(timedelta(hours=8)))


class _GitFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='publication-history-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'work'
        self.root.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'History Test')
        self.git('config', 'user.email', 'history@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.path = self.root / RELATIVE
        self.path.parent.mkdir(parents=True)
        self.sequence = 0
        self._subject = None

    @property
    def subject(self):
        if self._subject is None:
            self.assertTrue(SCRIPT.is_file(), 'Independent publication state verifier is not implemented')
            spec = importlib.util.spec_from_file_location('publication_state_under_test', SCRIPT)
            self._subject = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self._subject)
        return self._subject

    def git(self, *args, env=None, check=True):
        return subprocess.run(['git', *args], cwd=self.root, env=env,
                              check=check, capture_output=True).stdout

    def rows(self, *values):
        return {str(i): {'forecast_id': str(i), 'value': value}
                for i, value in enumerate(values)}

    def write(self, rows, path=None, columns=COLUMNS, newline='\n'):
        target = path or self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        body = io.StringIO(newline='')
        writer = csv.DictWriter(body, fieldnames=columns, lineterminator=newline)
        writer.writeheader()
        writer.writerows(rows.values())
        target.write_bytes(body.getvalue().encode('utf-8'))

    def commit(self, rows=None, stamp=None, message='history'):
        if rows is not None:
            self.write(rows)
        at = stamp or STAMP + timedelta(minutes=self.sequence)
        self.sequence += 1
        env = dict(os.environ, GIT_AUTHOR_DATE=at.isoformat(), GIT_COMMITTER_DATE=at.isoformat())
        self.git('add', '-A')
        self.git('commit', '--allow-empty', '-qm', message, env=env)
        return self.git('rev-parse', 'HEAD').decode().strip()

    def candidate(self, current, relative=RELATIVE, columns=COLUMNS, diagnostics=None):
        report = {} if diagnostics is None else diagnostics
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.subject.history_receipts(self.root, relative, columns, current,
                                                   diagnostics=report)
        return result, report

    def same_error(self, current, fragment, relative=RELATIVE, columns=COLUMNS):
        with self.assertRaises(Exception) as before:
            original.history_receipts(self.root, relative, columns, current)
        self.assertIn(fragment, str(before.exception))
        report = {}
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(type(before.exception)) as after:
            self.subject.history_receipts(self.root, relative, columns, current, diagnostics=report)
        self.assertEqual(str(after.exception), str(before.exception))
        if isinstance(before.exception, subprocess.CalledProcessError):
            self.assertEqual(after.exception.cmd, before.exception.cmd)
            self.assertEqual(after.exception.returncode, before.exception.returncode)
        self.assertEqual(report['status'], 'error')
        return report


class PublicationHistoryTests(_GitFixture):
    def test_every_receipt_and_uncommitted_append_matches_original(self):
        first = self.commit(self.rows('first'))
        second = self.commit(self.rows('first', 'second'))
        current = self.rows('first', 'second', 'uncommitted')
        self.write(current)
        want = original.history_receipts(self.root, RELATIVE, COLUMNS, current)
        got, report = self.candidate(current)
        self.assertEqual(got, want)
        self.assertEqual(got, {
            '0': {'commit_sha': first, 'committed_at_utc': '2026-10-10T00:00:00+00:00'},
            '1': {'commit_sha': second, 'committed_at_utc': '2026-10-10T00:01:00+00:00'}})
        self.assertEqual(report['mode'], 'native_batch')
        self.assertEqual(report['attempted_commit_count'], 2)

    def test_129_real_revisions_cross_chunk_and_keep_all_first_receipts(self):
        current = {}
        commits = []
        for index in range(129):
            current[str(index)] = {'forecast_id': str(index), 'value': 'v' + str(index)}
            commits.append(self.commit(current))
        want = original.history_receipts(self.root, RELATIVE, COLUMNS, current)
        real_run = subprocess.run
        calls = []
        def counted(args, **kwargs):
            calls.append(args)
            return real_run(args, **kwargs)
        with mock.patch.object(subprocess, 'run', side_effect=counted):
            got, report = self.candidate(current)
        self.assertEqual(got, want)
        self.assertEqual(len(got), 129)
        for index, commit in enumerate(commits):
            self.assertEqual(got[str(index)], {'commit_sha': commit,
                'committed_at_utc': (STAMP + timedelta(minutes=index)).astimezone(timezone.utc).isoformat()})
        self.assertEqual(report['attempted_commit_count'], 129)
        self.assertEqual(report['native_batch_calls'], 2)
        self.assertEqual(report['mode'], 'native_batch')
        self.assertLessEqual(len(calls), 8, '129 revisions must not spawn two Git reads per revision')
        self.assertFalse(any(args[1] == 'show' for args in calls))

    def test_no_committed_path_allows_valid_uncommitted_rows(self):
        self.commit()
        current = self.rows('uncommitted')
        self.write(current)
        got, report = self.candidate(current)
        self.assertEqual(got, original.history_receipts(self.root, RELATIVE, COLUMNS, current))
        self.assertEqual(got, {})
        self.assertEqual(report['attempted_commit_count'], 0)

    def test_historical_row_removal_then_restoration_still_rejected(self):
        current = self.rows('original')
        self.commit(current)
        self.commit({})
        self.commit(current)
        self.same_error(current, 'committed record revised')

    def test_historical_edit_then_restoration_still_rejected(self):
        current = self.rows('original')
        self.commit(current)
        self.commit(self.rows('tampered'))
        self.commit(current)
        self.same_error(current, 'committed record revised')

    def test_deleted_historical_file_then_restoration_keeps_original_git_error(self):
        current = self.rows('original')
        self.commit(current)
        self.path.unlink()
        self.commit()
        self.commit(current)
        report = self.same_error(current, 'git')
        self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_historic_schema_change_then_restore_is_not_hidden(self):
        self.commit(self.rows('first'))
        self.path.write_bytes(b'forecast_id,changed\n0,first\n')
        self.commit()
        self.commit(self.rows('first'))
        self.same_error(self.rows('first'), 'historic schema revised')

    def test_duplicate_historical_id_is_rejected_before_prefix_check(self):
        self.commit(self.rows('first'))
        self.path.write_bytes(b'forecast_id,value\n0,changed\n0,again\n')
        self.commit()
        self.same_error(self.rows('first'), 'duplicate historical ID')

    def test_empty_historical_id_preserves_original_duplicate_error(self):
        self.path.write_bytes(b'forecast_id,value\n,empty\n')
        self.commit()
        self.same_error({}, 'duplicate historical ID')

    def test_invalid_utf8_matches_original_exception(self):
        self.path.write_bytes(b'forecast_id,value\n0,\xff\n')
        self.commit()
        self.same_error({}, 'utf-8')

    def test_universal_newlines_strip_and_quoted_csv_match_original(self):
        for newline in ('\r\n', '\r', '\n'):
            with self.subTest(newline=repr(newline)):
                relative = 'data/newline-' + str(len(newline)) + '-' + str(ord(newline[0])) + '.csv'
                path = self.root / relative
                path.write_bytes(('forecast_id,value' + newline + '0,"a' + newline + 'b"' + newline).encode())
                self.commit()
                current = self.rows('a\nb')
                got, report = self.candidate(current, relative=relative)
                self.assertEqual(got, original.history_receipts(self.root, relative, COLUMNS, current))
                self.assertEqual(report['mode'], 'native_batch')

    def test_current_changed_record_rejected_after_full_history(self):
        self.commit(self.rows('first'))
        self.same_error(self.rows('changed'), 'current record revised')

    def test_current_missing_record_rejected_after_full_history(self):
        self.commit(self.rows('first'))
        self.same_error({}, 'current record revised')

    def test_shallow_repository_rejected_with_original_precedence(self):
        self.commit(self.rows('first'))
        self.commit(self.rows('first', 'second'))
        clone = Path(self.temp.name) / 'shallow'
        self.git('clone', '-q', '--depth=1', self.root.as_uri(), str(clone))
        self.root = clone
        self.same_error({}, 'full git history required')

    def test_earlier_schema_error_wins_over_later_deleted_blob(self):
        self.path.write_bytes(b'wrong,header\na,b\n')
        self.commit()
        self.path.unlink()
        self.commit()
        self.same_error({}, 'historic schema revised')

    def test_earlier_utf8_error_wins_over_later_missing_blob(self):
        self.path.write_bytes(b'forecast_id,value\n0,\xff\n')
        self.commit()
        self.path.unlink()
        self.commit()
        self.same_error({}, 'utf-8')

    def test_native_process_failure_runs_whole_original_history(self):
        current = self.rows('first')
        self.commit(current)
        with mock.patch.object(self.subject, '_batch_output', side_effect=OSError('native unavailable')):
            got, report = self.candidate(current)
        self.assertEqual(got, original.history_receipts(self.root, RELATIVE, COLUMNS, current))
        self.assertEqual(report['mode'], 'legacy_full_fallback')
        self.assertTrue(report['fallback_reason'])

    def test_fallback_rechecks_historical_tampering_from_first_commit(self):
        current = self.rows('first')
        self.commit(current)
        self.commit({})
        self.commit(current)
        with mock.patch.object(self.subject, '_batch_output', side_effect=OSError('native unavailable')):
            report = self.same_error(current, 'committed record revised')
        self.assertEqual(report['mode'], 'legacy_full_fallback')

    @staticmethod
    def frames(body):
        frames = []
        offset = 0
        while offset < len(body):
            end = body.index(b'\n', offset)
            size = int(body[offset:end].split()[2])
            stop = end + 1 + size + 1
            frames.append(body[offset:stop])
            offset = stop
        return frames

    def test_incomplete_reordered_corrupt_or_extra_batch_falls_back_completely(self):
        self.commit(self.rows('first'))
        current = self.rows('first', 'second')
        self.commit(current)
        expected = original.history_receipts(self.root, RELATIVE, COLUMNS, current)
        real = self.subject._batch_output
        for fault in ('truncated', 'reordered', 'missing', 'corrupt', 'extra'):
            def damaged(*args):
                raw = real(*args)
                blocks = self.frames(raw)
                if fault == 'truncated': return raw[:-1]
                if fault == 'reordered': return b''.join(reversed(blocks))
                if fault == 'missing': return blocks[0]
                if fault == 'corrupt': return raw.replace(b'first', b'FIRST', 1)
                return raw + b'unexpected\n'
            with self.subTest(fault=fault), mock.patch.object(self.subject, '_batch_output', side_effect=damaged):
                got, report = self.candidate(current)
            self.assertEqual(got, expected)
            self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_reordered_object_resolution_cannot_reassign_commit_receipts(self):
        self.commit(self.rows('first'))
        current = self.rows('first', 'second')
        self.commit(current)
        real = self.subject._batch_check_output
        def reordered(*args):
            return b''.join(reversed(real(*args).splitlines(keepends=True)))
        with mock.patch.object(self.subject, '_batch_check_output', side_effect=reordered):
            got, report = self.candidate(current)
        self.assertEqual(got, original.history_receipts(self.root, RELATIVE, COLUMNS, current))
        self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_malformed_log_metadata_falls_back_without_new_validation_rules(self):
        current = self.rows('first')
        self.commit(current)
        real = self.subject._log_output
        def damaged(*args):
            return real(*args).replace(b'2026-10-10T08:00:00+08:00', b'not-a-time')
        with mock.patch.object(self.subject, '_log_output', side_effect=damaged):
            got, report = self.candidate(current)
        self.assertEqual(got, original.history_receipts(self.root, RELATIVE, COLUMNS, current))
        self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_non_git_naive_timestamp_cannot_be_accepted_as_commit_receipt(self):
        current = self.rows('first')
        self.commit(current)
        real = self.subject._log_output
        def missing_zone(*args):
            return real(*args).replace(b'2026-10-10T08:00:00+08:00', b'2026-10-10T08:00:00')
        with mock.patch.object(self.subject, '_log_output', side_effect=missing_zone):
            got, report = self.candidate(current)
        self.assertEqual(got, original.history_receipts(self.root, RELATIVE, COLUMNS, current))
        self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_cross_call_new_rebased_history_is_not_cached(self):
        first = self.commit(self.rows('first'))
        current = self.rows('first', 'second')
        self.commit(current)
        before, _ = self.candidate(current)
        self.git('checkout', '-qb', 'upstream', first)
        (self.root / 'upstream.txt').write_text('new parent\n')
        self.commit()
        self.git('checkout', '-q', 'main')
        self.git('rebase', 'upstream')
        after, report = self.candidate(current)
        self.assertEqual(after, original.history_receipts(self.root, RELATIVE, COLUMNS, current))
        self.assertNotEqual(before['1']['commit_sha'], after['1']['commit_sha'])
        self.assertEqual(before['0'], after['0'])
        self.assertEqual(report['attempted_commit_count'], 2)
        self.commit(self.rows('altered', 'second'))
        self.commit(current)
        self.same_error(current, 'committed record revised')

    def test_reused_diagnostics_do_not_keep_old_fallback(self):
        current = self.rows('first')
        self.commit(current)
        report = {}
        with mock.patch.object(self.subject, '_batch_output', side_effect=OSError('native unavailable')):
            self.candidate(current, diagnostics=report)
        self.assertIn('fallback_reason', report)
        self.candidate(current, diagnostics=report)
        self.assertEqual(report['mode'], 'native_batch')
        self.assertNotIn('fallback_reason', report)

    def test_non_linear_merge_history_matches_original_rejection(self):
        first = self.commit(self.rows('first'))
        self.git('checkout', '-qb', 'side')
        self.commit(self.rows('first', 'side'))
        self.git('checkout', '-q', 'main')
        self.commit(self.rows('first', 'main'))
        merge = subprocess.run(['git', 'merge', '--no-ff', '--no-commit', 'side'],
                               cwd=self.root, capture_output=True)
        self.assertEqual(merge.returncode, 1)
        current = self.rows('first', 'main', 'side')
        self.commit(current, message='resolve merge')
        self.same_error(current, 'committed record revised')

    def test_plain_merge_preserves_all_original_first_receipts(self):
        first = self.commit(self.rows('first'))
        self.git('checkout', '-qb', 'side')
        (self.root / 'side.txt').write_text('side')
        self.commit()
        self.git('checkout', '-q', 'main')
        current = self.rows('first', 'main')
        self.commit(current)
        self.git('merge', '--no-ff', '--no-commit', 'side')
        self.commit(message='merge unchanged ledger')
        got, report = self.candidate(current)
        self.assertEqual(got, original.history_receipts(self.root, RELATIVE, COLUMNS, current))
        self.assertEqual(got['0']['commit_sha'], first)
        self.assertEqual(report['mode'], 'native_batch')

    def test_git_blob_replacement_keeps_original_content_and_error_behavior(self):
        self.commit(self.rows('first'))
        old_blob = self.git('rev-parse', 'HEAD:' + RELATIVE).decode().strip()
        self.write(self.rows('replacement'))
        replacement_blob = self.git('hash-object', '-w', str(self.path)).decode().strip()
        self.git('replace', old_blob, replacement_blob)
        current = self.rows('replacement')
        want = original.history_receipts(self.root, RELATIVE, COLUMNS, current)
        got, report = self.candidate(current)
        self.assertEqual(got, want)
        self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_diagnostic_container_failure_cannot_change_original_result(self):
        class BrokenReport(dict):
            def update(self, *args, **kwargs):
                raise OSError('diagnostic unavailable')
        current = self.rows('first')
        self.commit(current)
        got, _ = self.candidate(current, diagnostics=BrokenReport())
        self.assertEqual(got, original.history_receipts(self.root, RELATIVE, COLUMNS, current))

    def test_changed_entire_dependency_file_uses_original_with_visible_reason(self):
        current = self.rows('first')
        self.commit(current)
        for module in (publisher, original):
            changed = Path(self.temp.name) / (module.__name__ + '.py')
            changed.write_bytes(Path(module.__file__).read_bytes() + b'\n# changed dependency\n')
            with self.subTest(module=module.__name__), mock.patch.object(module, '__file__', str(changed)):
                output = io.StringIO()
                report = {}
                with contextlib.redirect_stdout(output):
                    got = self.subject.history_receipts(self.root, RELATIVE, COLUMNS, current, diagnostics=report)
                    self.subject.emit_diagnostics(report, True)
                self.assertEqual(got, original.history_receipts(self.root, RELATIVE, COLUMNS, current))
                self.assertEqual(report['mode'], 'legacy_full_fallback')
                self.assertIn('dependency', report['fallback_reason'])
                self.assertIn('legacy_full_fallback', output.getvalue())


class PublicationStateTests(_GitFixture):
    """The complete public function retains original validation/error order."""

    def seed_state(self):
        pins = {}
        for name in sorted(v2.REQUIRED_CODE):
            body = Path(__file__).with_name(name).read_bytes()
            (self.root / name).write_bytes(body)
            pins[name] = hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()
        protocol = {'status': 'frozen_protocol', 'protocol_id': 'PUBLICATION_TEST',
            'effective_from_utc': '2026-10-01T00:00:00+00:00', 'model_id': frozen.MODEL,
            'primary_horizons_hours': [1, 24], 'locked_code_git_blobs': pins,
            'minimum_quote_and_issuance_coverage_fraction': .9,
            'minimum_nonoverlap_valid_scores': 100}
        path = self.root / v2.PROTOCOL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(protocol), encoding='utf-8')
        issued = STAMP.astimezone(timezone.utc)
        row = {'forecast_id': 'new-1', 'model_id': frozen.MODEL,
            'issued_at_utc': issued.isoformat(), 'source_quote_utc': issued.isoformat(),
            'source_hour_start_utc': issued.replace(minute=0).isoformat(),
            'reference_price': '.11', 'horizon_hours': '1',
            'target_utc': (issued + timedelta(hours=1)).isoformat(), 'majority_p_up': '.6',
            'majority_direction': 'UP', 'doge_reversal_direction': 'UP',
            'btc_momentum_direction': 'UP', 'feature_sha256': 'a' * 64}
        raw = json.dumps({'code': '0', 'data': [{'instId': 'DOGE-USDT', 'last': '.11',
                                               'ts': str(int(issued.timestamp() * 1000))}]})
        qid = evidence.save_quote(self.root, raw, issued)
        bindings = {'new-1|ISSUE': {'forecast_id': 'new-1|ISSUE', 'prediction_id': 'new-1',
                                  'kind': 'ISSUE', 'quote_id': qid}}
        self.write({'new-1': row}, self.root / original.REG, frozen.FCOLS)
        self.write({}, self.root / original.SCORES, frozen.SCOLS)
        self.write(bindings, self.root / evidence.BINDINGS, evidence.BCOLS)
        self.commit()
        return row, bindings

    def state_outcome(self, candidate):
        report = {}
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                if candidate:
                    succeeded = False
                    try:
                        value = self.subject.validate_state(self.root, diagnostics=report)
                        succeeded = True
                    finally:
                        self.subject.emit_diagnostics(report, succeeded)
                else:
                    value = publisher.validate_state(self.root)
            return ('ok', value), report, output.getvalue()
        except Exception as exc:
            return (type(exc), str(exc)), report, output.getvalue()

    def assert_same_state_error(self, fragment):
        want, _, _ = self.state_outcome(False)
        self.assertNotEqual(want[0], 'ok')
        self.assertIn(fragment, want[1])
        got, report, output = self.state_outcome(True)
        self.assertEqual(got, want)
        self.assertEqual(report['status'], 'error')
        self.assertTrue(output.strip())
        return report

    def test_full_actual_protocol_quotes_and_history_equal_original(self):
        self.seed_state()
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
                  if p.is_file() and '.git' not in p.parts}
        want, _, _ = self.state_outcome(False)
        got, report, output = self.state_outcome(True)
        self.assertEqual(want, ('ok', None))
        self.assertEqual(got, want)
        self.assertEqual(report['mode'], 'native_batch')
        self.assertEqual(report['status'], 'ok')
        self.assertEqual([x['relative'] for x in report['histories']],
                         [original.REG, original.SCORES, evidence.BINDINGS])
        self.assertIn('runtime_publication_state_validation', output)
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
                 if p.is_file() and '.git' not in p.parts}
        self.assertEqual(after, before)

    def test_invalid_protocol_precedes_bad_current_csv(self):
        self.seed_state()
        (self.root / v2.PROTOCOL).write_text('{}')
        (self.root / original.REG).write_text('bad\ncsv\n')
        self.assert_same_state_error('invalid V2 protocol')

    def test_changed_frozen_code_precedes_current_rows_and_history(self):
        self.seed_state()
        (self.root / 'direction_baseline_v42.py').write_text('changed\n')
        (self.root / original.REG).write_text('bad\ncsv\n')
        self.assert_same_state_error('frozen V2 implementation modified')

    def test_all_current_rows_are_read_before_score_and_history_validation(self):
        row, _ = self.seed_state()
        row['reference_price'] = '-1'
        self.write({'new-1': row}, self.root / original.REG, frozen.FCOLS)
        (self.root / evidence.BINDINGS).write_text('bad\n')
        self.assert_same_state_error('registry schema changed bindings.csv')

    def test_invalid_score_semantics_precede_historical_revision_error(self):
        row, _ = self.seed_state()
        row['reference_price'] = '-1'
        self.write({'new-1': row}, self.root / original.REG, frozen.FCOLS)
        self.commit()
        self.assert_same_state_error('invalid price')

    def test_registry_history_failure_precedes_later_binding_failure(self):
        row, bindings = self.seed_state()
        row['reference_price'] = '.12'
        self.write({'new-1': row}, self.root / original.REG, frozen.FCOLS)
        self.commit()
        self.assert_same_state_error('committed record revised: ' + original.REG)

    def test_original_quote_binding_validation_runs_after_all_histories(self):
        self.seed_state()
        snapshots = list((self.root / evidence.BASE / 'snapshots').glob('*/*.json'))
        self.assertEqual(len(snapshots), 1)
        snapshots[0].unlink()
        self.assert_same_state_error('raw quote missing')

    def test_dependency_change_falls_back_to_entire_original_validation(self):
        self.seed_state()
        (self.root / v2.PROTOCOL).write_text('{}')
        changed = Path(self.temp.name) / 'publisher_changed.py'
        changed.write_bytes(Path(publisher.__file__).read_bytes() + b'\n# unrelated original function changed\n')
        with mock.patch.object(publisher, '__file__', str(changed)):
            report = self.assert_same_state_error('invalid V2 protocol')
        self.assertEqual(report['mode'], 'legacy_full_fallback')

    def test_native_transport_fallback_still_runs_original_full_validation(self):
        self.seed_state()
        real = self.subject._batch_output
        def truncated(*args): return real(*args)[:-1]
        with mock.patch.object(self.subject, '_batch_output', side_effect=truncated):
            got, report, output = self.state_outcome(True)
        self.assertEqual(got, self.state_outcome(False)[0])
        self.assertTrue(any(x['mode'] == 'legacy_full_fallback' for x in report['histories']))
        self.assertIn('legacy_full_fallback', output)

    def test_diagnostic_serialization_failure_cannot_replace_validation_error(self):
        self.seed_state()
        (self.root / original.REG).write_text('broken\n')
        want, _, _ = self.state_outcome(False)
        with mock.patch.object(self.subject.json, 'dumps', side_effect=OSError('diagnostic unavailable')):
            got, report, output = self.state_outcome(True)
        self.assertEqual(got, want)
        self.assertEqual(report['status'], 'error')
        self.assertEqual(output, '')

    def test_diagnostic_broken_pipe_cannot_turn_valid_publication_into_failure(self):
        self.seed_state()
        with mock.patch('builtins.print', side_effect=OSError('broken pipe')):
            got, report, output = self.state_outcome(True)
        self.assertEqual(got, ('ok', None))
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(output, '')


if __name__ == '__main__':
    unittest.main()

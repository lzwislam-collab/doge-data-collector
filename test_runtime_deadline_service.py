"""Future capture deadlines, serial execution and honest receipts."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    import runtime_deadline_service as runtime
except ImportError:
    runtime = None

START = datetime(2026, 10, 25, 10, tzinfo=timezone.utc)


class Clock:
    def __init__(self): self.at = START
    def __call__(self): return self.at
    def sleep(self, seconds): self.at += timedelta(seconds=seconds)


class Adapter:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.fail = set()
        self.registry = []
        self.scores = {}
        self.duration = {}
    def state(self): return self.registry, self.scores
    def action(self, name):
        self.calls.append(name)
        self.clock.sleep(self.duration.get(name, 0))
        if name in self.fail: raise RuntimeError(name + ' failed')
        return {'status': 'ok'}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runtime, 'deadline runtime implementation missing')
        self.clock = Clock()
        self.adapter = Adapter(self.clock)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
    def row(self, fid='due', target=START, hour=START-timedelta(hours=1)):
        return {'forecast_id': fid, 'target_utc': target.isoformat(),
                'source_hour_start_utc': hour.isoformat(), 'horizon_hours': '1'}
    def run_cycle(self):
        return runtime.cycle(self.adapter, {}, self.clock)
    def test_due_target_is_serviced_before_research_or_issuance(self):
        self.adapter.registry = [self.row()]
        result = self.run_cycle()
        self.assertLess(self.adapter.calls.index('watch'), self.adapter.calls.index('issue'))
        self.assertEqual(result['errors'], [])
    def test_future_target_does_not_trigger_watch(self):
        self.adapter.registry = [self.row(target=START+timedelta(hours=1))]
        self.run_cycle()
        self.assertNotIn('watch', self.adapter.calls)
    def test_expired_target_does_not_trigger_reconstruction(self):
        self.adapter.registry = [self.row(target=START-timedelta(minutes=26))]
        result = self.run_cycle()
        self.assertNotIn('watch', self.adapter.calls)
        self.assertEqual(result['expired_unscored_ids'], ['due'])
    def test_existing_missing_score_is_not_reopened(self):
        self.adapter.registry = [self.row()]
        self.adapter.scores = {'due': {'status': 'unscorable_missing_target_quote'}}
        self.run_cycle()
        self.assertNotIn('watch', self.adapter.calls)
    def test_issued_hour_is_not_reissued(self):
        self.adapter.registry = [self.row(hour=START)]
        self.run_cycle()
        self.assertNotIn('issue', self.adapter.calls)
    def test_late_start_waits_for_new_hour_without_relaxing_deadline(self):
        self.clock.sleep(41*60)
        result = self.run_cycle()
        self.assertNotIn('issue', self.adapter.calls)
        self.assertEqual(result['issuance'], 'SKIP_RESERVE_EXHAUSTED')
    def test_preparation_crossing_deadline_prevents_issuer_invocation(self):
        self.clock.sleep(39*60)
        self.adapter.duration['prepare'] = 121
        result = self.run_cycle()
        self.assertNotIn('issue', self.adapter.calls)
        self.assertEqual(result['issuance'], 'SKIP_RESERVE_EXHAUSTED')
    def test_preparation_crossing_hour_is_not_used_for_new_hour(self):
        self.adapter.duration['prepare'] = 3601
        result = self.run_cycle()
        self.assertNotIn('issue', self.adapter.calls)
        self.assertEqual(result['issuance'], 'SKIP_SOURCE_HOUR_CHANGED')
    def test_failed_capture_still_attempts_publication_before_return(self):
        self.adapter.registry = [self.row()]
        self.adapter.fail = {'watch'}
        result = self.run_cycle()
        self.assertEqual(self.adapter.calls[:2], ['watch', 'publish'])
        self.assertTrue(any(x['stage'] == 'watch' for x in result['errors']))
    def test_failed_publication_stops_further_mutations(self):
        self.adapter.registry = [self.row()]
        self.adapter.fail = {'publish'}
        result = self.run_cycle()
        self.assertNotIn('issue', self.adapter.calls)
        self.assertNotIn('book', self.adapter.calls)
        self.assertTrue(result['fatal'])
    def test_source_audit_failure_prevents_unverified_book_capture(self):
        self.adapter.fail = {'source_audit'}
        result = self.run_cycle()
        self.assertNotIn('book', self.adapter.calls)
        self.assertTrue(result['errors'])
    def test_depth_capture_precedes_long_research(self):
        self.run_cycle()
        self.assertIn('book', self.adapter.calls)
        self.assertNotIn('research', self.adapter.calls)
    def test_failed_issue_is_published_but_never_reported_as_issued(self):
        self.adapter.fail = {'issue'}
        result = self.run_cycle()
        self.assertEqual(result['issuance'], 'FAILED')
        self.assertIn('publish', self.adapter.calls)
        self.assertTrue(result['errors'])
    def test_loop_preserves_old_receipts_and_never_claims_economic_success(self):
        out = runtime.run(self.root, self.adapter, duration_seconds=121,
                          clock=self.clock, sleep=self.clock.sleep)
        receipts = list((self.root/'data/runtime_deadline_v1/receipts').glob('*.json'))
        self.assertEqual(len(receipts), 3)
        self.assertFalse(out['economic_acceptance_passed'])
        self.assertFalse(out['historical_gaps_repaired'])
        self.assertEqual(out['cycles'], 3)
    def test_cycle_overrun_is_reported_without_overlapping_or_catchup(self):
        self.adapter.duration['source_audit'] = 400
        out = runtime.run(self.root, self.adapter, duration_seconds=500,
                          clock=self.clock, sleep=self.clock.sleep)
        self.assertGreater(out['maximum_cycle_seconds'], 300)
        self.assertFalse(out['cadence_target_met'])
    def test_receipt_collision_cannot_overwrite_old_evidence(self):
        runtime.save_receipt(self.root, {'observed_at_utc': START.isoformat(), 'id': 'same'})
        with self.assertRaises(FileExistsError):
            runtime.save_receipt(self.root, {'observed_at_utc': START.isoformat(), 'id': 'same'})
    def test_live_adapter_refuses_non_main_before_running_commands(self):
        with patch.dict('os.environ', {'GITHUB_REF': 'refs/heads/fix',
                                      'GITHUB_REPOSITORY': 'lzwislam-collab/doge-data-collector'}):
            with self.assertRaisesRegex(ValueError, 'main'):
                runtime.LiveAdapter(self.root)
    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError):
            runtime.open_targets([], {}, datetime(2026, 10, 25))
    def test_target_opening_during_preparation_is_captured_before_source_audit(self):
        self.adapter.registry = [self.row(target=START+timedelta(seconds=30))]
        self.adapter.duration['prepare'] = 40
        self.run_cycle()
        self.assertLess(self.adapter.calls.index('watch'), self.adapter.calls.index('source_audit'))
    def test_failed_partial_issuance_invalidates_previous_source_audit(self):
        state = {'source_audited_at': START}
        self.adapter.fail = {'issue'}
        runtime.cycle(self.adapter, state, self.clock)
        self.assertIn('source_audit', self.adapter.calls)
    def test_book_publication_without_new_core_evidence_does_not_dispatch_witness(self):
        with patch.dict('os.environ', {'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': runtime.REPO}):
            live = runtime.LiveAdapter(self.root)
        live.public_head = 'a'*40
        live.core_witness_pending = False
        self.assertEqual(live.action('witness')['status'], 'no_new_core_evidence')
    def test_last_five_minutes_of_frozen_target_window_are_not_lost(self):
        self.adapter.registry = [self.row()]
        self.clock.sleep(24*60)
        self.run_cycle()
        self.assertEqual(self.adapter.calls[0], 'watch')
    def test_fatal_result_is_not_published_again_by_finalization(self):
        runtime.finish(self.root, self.adapter, {'fatal': True, 'status': 'DEGRADED'})
        self.assertNotIn('publish', self.adapter.calls)
    def test_receipt_write_failure_leaves_explicit_unsafe_output(self):
        output = self.root/'outputs.txt'
        with patch.dict('os.environ', {'GITHUB_OUTPUT': str(output)}):
            with patch.object(Path, 'write_text', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    runtime.finish(self.root, self.adapter, {'fatal': True, 'status': 'DEGRADED'})
        self.assertEqual(output.read_text(), 'fatal=true\n')
    def test_corrupt_p2_partial_append_is_rejected_before_git_commit(self):
        import subprocess
        import p2_execution_registration_v3_1 as registration
        import test_p2_execution_records_v3_1 as fixtures
        fixture = fixtures.RecordsTests()
        fixture.root = self.root
        audit = fixture.make_registration()
        registration.register(self.root, audit)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        subprocess.run(['git', '-c', 'user.name=test', '-c', 'user.email=test@example.com',
                        'commit', '--allow-empty', '-qm', 'fixture'], cwd=self.root, check=True)
        path = self.root/'data/p2_paired_execution_v3_1/books.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"partial":', encoding='utf-8')
        with self.assertRaises(ValueError): runtime.validate_p2(self.root)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.adapter = Adapter(self.clock)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fail_publication(self, exc):
        original = self.adapter.action
        def action(name):
            if name == 'publish':
                self.adapter.calls.append(name)
                raise exc
            return original(name)
        self.adapter.action = action

    def cycle_failure(self, exc):
        self.fail_publication(exc)
        result = runtime.cycle(self.adapter, {}, self.clock)
        self.assertTrue(result['fatal'])
        self.assertNotIn('book', self.adapter.calls)
        self.assertNotIn('witness', self.adapter.calls)
        self.assertEqual(self.adapter.calls.count('publish'), 1)
        self.assertFalse(result['economic_acceptance_passed'])
        return result

    def test_called_process_metadata_survives_in_durable_fatal_receipt(self):
        self.fail_publication(subprocess.CalledProcessError(
            1, ['git', 'push'], output='rejected update', stderr=b'non-fast-forward'))
        result = runtime.run(self.root, self.adapter, 60, self.clock, self.clock.sleep)
        self.assertTrue(result['fatal'])
        receipts = list((self.root/runtime.BASE/'receipts').glob('*.json'))
        self.assertEqual(len(receipts), 1)
        entry = json.loads(receipts[0].read_text())['errors'][0]
        self.assertIn('returncode', entry)
        self.assertEqual(entry['returncode'], 1)
        self.assertEqual(entry['stdout'], 'rejected update')
        self.assertEqual(entry['stderr'], 'non-fast-forward')
        self.assertIn('CalledProcessError:', entry['error'])
        self.assertEqual(self.adapter.calls, ['prepare', 'issue', 'publish'])

    def test_real_offline_child_failure_keeps_code_and_redacts_captured_bytes(self):
        original = self.adapter.action
        def action(name):
            if name == 'publish':
                self.adapter.calls.append(name)
                return subprocess.run([sys.executable, '-I', '-c',
                    "import sys; print('remote: non-fast-forward'); "
                    "print('token=fake-child-secret', file=sys.stderr); sys.exit(17)"],
                    cwd=self.root, check=True, capture_output=True)
            return original(name)
        self.adapter.action = action
        result = runtime.run(self.root, self.adapter, 60, self.clock, self.clock.sleep)
        self.assertTrue(result['fatal'])
        receipt = next((self.root/runtime.BASE/'receipts').glob('*.json'))
        entry = json.loads(receipt.read_text())['errors'][0]
        self.assertEqual(entry['returncode'], 17)
        self.assertIn('non-fast-forward', entry['stdout'])
        self.assertNotIn('fake-child-secret', receipt.read_text())
        self.assertNotIn('book', self.adapter.calls)

    def test_absent_output_is_omitted_and_empty_output_is_preserved(self):
        for output, stderr, present in [(None, None, False), ('', b'', True)]:
            with self.subTest(present=present):
                self.adapter = Adapter(self.clock)
                entry = self.cycle_failure(subprocess.CalledProcessError(
                    -15, ['git', 'push'], output=output, stderr=stderr))['errors'][0]
                self.assertIn('returncode', entry)
                self.assertEqual(entry['returncode'], -15)
                self.assertEqual('stdout' in entry, present)
                self.assertEqual('stderr' in entry, present)
                if present:
                    self.assertEqual(entry['stdout'], '')
                    self.assertEqual(entry['stderr'], '')

    def test_error_and_both_streams_hide_environment_secrets_and_signed_urls(self):
        secret = 'fake-env-secret.[+](value)'
        url = 'https://fake-user:fake-pass@example.invalid/archive?sig=fake-signature#fragment'
        exc = subprocess.CalledProcessError(1, ['git', 'push', url, secret],
            output='remote: '+url, stderr='fatal: '+secret)
        with patch.dict(os.environ, {'DIAGNOSTIC_TEST_TOKEN': secret}):
            result = self.cycle_failure(exc)
        body = json.dumps(result)
        for sensitive in (secret, 'fake-user', 'fake-pass', 'fake-signature', 'fragment'):
            self.assertNotIn(sensitive, body)
        self.assertIn('[REDACTED]', body)

    def test_credential_forms_are_redacted_without_erasing_plain_failure_reason(self):
        examples = [
            'Authorization: Bearer fake-bearer-value',
            'Authorization: Basic ZmFrZS1jcmVkZW50aWFs',
            '{"password": "fake-password-value"}',
            '{"api_key":"fake-api-value"}',
            'access_token=fake-token-value',
            'Cookie: session=fake-cookie-value',
            'ghp_'+('a'*36),
            '-----BEGIN PRIVATE KEY-----\nfake-private-material\n-----END PRIVATE KEY-----',
        ]
        for value in examples:
            with self.subTest(form=value[:18]):
                self.adapter = Adapter(self.clock)
                entry = self.cycle_failure(subprocess.CalledProcessError(
                    1, ['git', 'push'], output=value, stderr='non-fast-forward'))['errors'][0]
                self.assertIn('stdout', entry)
                self.assertIn('[REDACTED]', entry['stdout'])
                self.assertNotIn(value, entry['stdout'])
                self.assertNotIn('fake-', entry['stdout'])
                self.assertEqual(entry['stderr'], 'non-fast-forward')

    def test_url_quotes_cannot_leave_userinfo_or_path_fragments_in_receipts(self):
        url = "https://fake-user:fake-pass'private@example.invalid/private-path"
        entry = self.cycle_failure(subprocess.CalledProcessError(
            1, ['git', 'push', url], output=url, stderr=url))['errors'][0]
        for name in ('error', 'stdout', 'stderr'):
            self.assertNotIn('private', entry[name])
            self.assertNotIn('fake-pass', entry[name])
            self.assertIn('[REDACTED]', entry[name])

    def test_escaped_known_secrets_are_removed_from_argv_repr_and_json_strings(self):
        for secret in ('fake\\backslash-secret', 'fake\nnewline-secret',
                       'fake\tunicode-密钥', 'fake\'"quotes-secret'):
            with self.subTest(form=repr(secret[:5])):
                self.adapter = Adapter(self.clock)
                with patch.dict(os.environ, {'DIAGNOSTIC_TEST_TOKEN': secret}):
                    entry = self.cycle_failure(subprocess.CalledProcessError(
                        1, ['git', 'push', secret], output=json.dumps(secret),
                        stderr=secret))['errors'][0]
                for name in ('error', 'stdout', 'stderr'):
                    self.assertIn('[REDACTED]', entry[name])
                    self.assertNotIn('fake', entry[name])

    def test_known_short_credentials_are_not_exempt_from_redaction(self):
        with patch.dict(os.environ, {'DIAGNOSTIC_TEST_TOKEN': 'abc'}):
            entry = self.cycle_failure(subprocess.CalledProcessError(
                1, ['git', 'push', 'abc'], output='abc', stderr='abc'))['errors'][0]
        for name in ('error', 'stdout', 'stderr'):
            self.assertNotIn('abc', entry[name])
            self.assertIn('[REDACTED]', entry[name])

    def test_nested_json_credential_fields_are_redacted(self):
        value = r'{\"password\":\"fake-nested-password\"}'
        entry = self.cycle_failure(subprocess.CalledProcessError(
            1, ['git', 'push'], output=value, stderr=value))['errors'][0]
        self.assertNotIn('fake-nested-password', entry['stdout'])
        self.assertNotIn('fake-nested-password', entry['stderr'])
        self.assertIn('[REDACTED]', entry['stdout'])

    def test_invalid_utf8_and_unicode_output_remain_json_serializable(self):
        entry = self.cycle_failure(subprocess.CalledProcessError(
            2, 'git push', output=b'failed\xff', stderr='拒绝更新'))['errors'][0]
        self.assertIn('stdout', entry)
        self.assertEqual(entry['stdout'], 'failed\ufffd')
        self.assertEqual(entry['stderr'], '拒绝更新')
        json.dumps(entry, allow_nan=False)

    def test_output_is_sanitized_before_truncation_and_each_field_is_bounded(self):
        value = 'x'*980+' token='+('s'*2500)
        entry = self.cycle_failure(subprocess.CalledProcessError(
            1, ['git', 'push'], output=value, stderr='y'*4000))['errors'][0]
        self.assertIn('stdout', entry)
        self.assertNotIn('s'*10, entry['stdout'])
        self.assertLessEqual(len(entry['stdout']), 1000)
        self.assertLessEqual(len(entry['stderr']), 1000)
        self.assertTrue(entry['stderr'].endswith('[truncated]'))
        self.assertLessEqual(len(entry['error']), len('CalledProcessError: ')+500)

    def test_oversize_diagnostics_are_omitted_without_changing_failure(self):
        entry = self.cycle_failure(subprocess.CalledProcessError(
            1, 'git push', output=b'x'*17000, stderr='密'*6000))['errors'][0]
        self.assertIn('stdout', entry)
        self.assertEqual(entry['stdout'], '[omitted: diagnostic input exceeds limit]')
        self.assertEqual(entry['stderr'], '[omitted: diagnostic input exceeds limit]')

    def test_plain_exceptions_keep_original_summary_without_fabricated_streams(self):
        entry = self.cycle_failure(RuntimeError('publish failed'))['errors'][0]
        self.assertEqual(entry['error'], 'RuntimeError: publish failed')
        for name in ('returncode', 'stdout', 'stderr'):
            self.assertNotIn(name, entry)

    def test_non_subprocess_error_summary_is_also_redacted(self):
        entry = self.cycle_failure(RuntimeError('password=fake-summary-secret'))['errors'][0]
        self.assertNotIn('fake-summary-secret', entry['error'])
        self.assertTrue(entry['error'].startswith('RuntimeError: '))

    def test_unprintable_exception_cannot_break_fatal_handling(self):
        class Unprintable(subprocess.CalledProcessError):
            def __str__(self): raise ValueError('unprintable')
        result = self.cycle_failure(Unprintable(1, ['git', 'push']))
        self.assertIn('[diagnostic unavailable]', result['errors'][0]['error'])

    def test_final_publication_failure_keeps_unsafe_and_sanitized_evidence(self):
        self.fail_publication(subprocess.CalledProcessError(
            1, 'git push', output='remote refused', stderr='token=fake-final-secret'))
        output = self.root/'outputs.txt'
        with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
            result = runtime.finish(self.root, self.adapter,
                {'fatal': False, 'status': 'CAPTURE_LOOP_COMPLETED'})
        self.assertTrue(result['fatal'])
        self.assertEqual(result['status'], 'DEGRADED')
        self.assertNotIn('fatal=false', output.read_text())
        saved = json.loads((self.root/runtime.BASE/'latest.json').read_text())
        self.assertIn('final_publication_diagnostics', saved)
        self.assertEqual(saved['final_publication_diagnostics']['returncode'], 1)
        self.assertEqual(saved['final_publication_diagnostics']['stdout'], 'remote refused')
        self.assertNotIn('fake-final-secret', json.dumps(saved))

    def test_startup_failure_keeps_unsafe_without_calling_real_producers(self):
        exc = subprocess.CalledProcessError(7, 'fixture', output='offline failure',
                                          stderr='secret=fake-startup-secret')
        closed = []
        def fail(*args): raise exc
        fake = SimpleNamespace(python=fail,
            auditor=SimpleNamespace(close=lambda: closed.append(True)))
        output = self.root/'outputs.txt'
        printed = io.StringIO()
        with patch.object(runtime, 'LiveAdapter', return_value=fake), \
             patch.object(runtime, '__file__', str(self.root/'runtime_deadline_service.py')), \
             patch.object(sys, 'argv', ['runtime_deadline_service.py']), \
             patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}), redirect_stdout(printed):
            code = runtime.main()
        self.assertEqual(code, 1)
        self.assertEqual(closed, [True])
        self.assertNotIn('fatal=false', output.read_text())
        result = json.loads(printed.getvalue())
        self.assertTrue(result['fatal'])
        self.assertIn('returncode', result)
        self.assertEqual(result['returncode'], 7)
        self.assertEqual(result['stdout'], 'offline failure')
        self.assertNotIn('fake-startup-secret', printed.getvalue())


if __name__ == '__main__': unittest.main()

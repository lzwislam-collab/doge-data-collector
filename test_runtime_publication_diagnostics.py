"""Publication timings retain the actual local Git and rejection boundaries.

Only synthetic validation fixtures are used. Python networking is denied and
the existing PublicationBoundary fixture permits only file-protocol Git remotes.
"""
import json
import math
import socket
import subprocess
import unittest
from datetime import timedelta
from unittest.mock import patch

import test_runtime_publication_boundary as boundary

runtime = boundary.runtime
publisher = boundary.publisher
pair = boundary.pair

EXPECTED_STAGES = [
    'preflight_validate_p2', 'preflight_validate_state', 'preflight_pair_guard',
    'preflight_pair_history_guard', 'data_publisher_total',
    'data_post_rebase_validate_state', 'data_post_rebase_pair_guard',
    'data_post_rebase_pair_history_guard', 'data_post_rebase_validate_p2',
    'supplementary_readback', 'receipt_publisher_total',
    'receipt_post_rebase_validate_state', 'receipt_post_rebase_pair_guard',
    'receipt_post_rebase_pair_history_guard', 'receipt_post_rebase_validate_p2',
    'receipt_readback']


class PublicationDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = boundary.PublicationBoundary('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture

        def no_network(*args, **kwargs):
            raise AssertionError('network forbidden in publication diagnostics tests')

        for owner, name, replacement in (
                (socket.socket, 'connect', no_network),
                (socket.socket, 'connect_ex', no_network),
                (socket, 'getaddrinfo', no_network),
                (publisher.time, 'sleep', lambda seconds: None)):
            guard = patch.object(owner, name, replacement)
            guard.start()
            self.addCleanup(guard.stop)
        (self.f.root/boundary.RUNTIME_PATH).write_text(json.dumps(self.f.result())+'\n')

    def profile(self, result):
        self.assertIn('publication_profile', result)
        profile = result['publication_profile']
        self.assertEqual(profile['schema_version'], 1)
        self.assertEqual(profile['clock'], 'monotonic')
        self.assertEqual(profile['scope'], 'nested_publisher_totals_include_callbacks_do_not_sum')
        self.assertLessEqual(len(profile['phases']), 64)
        for row in profile['phases']:
            self.assertIn(row['stage'], EXPECTED_STAGES)
            self.assertIn(row['status'], ('ok', 'error'))
            self.assertGreaterEqual(row['invocation'], 1)
            if 'seconds' in row:
                self.assertTrue(math.isfinite(row['seconds']))
                self.assertGreaterEqual(row['seconds'], 0)
        json.dumps(profile, allow_nan=False)
        return profile

    def peer_change(self, path, body):
        peer = self.f.base/'peer'
        self.f.git('clone', '-q', '--branch', 'main', str(self.f.remote), str(peer))
        self.f.git('config', 'user.name', 'offline peer', cwd=peer)
        self.f.git('config', 'user.email', 'peer@example.invalid', cwd=peer)
        (peer/path).write_text(body)
        self.f.git('add', '--', path, cwd=peer)
        self.f.git('commit', '-qm', 'independent fixture change', cwd=peer)
        self.f.git('push', 'origin', 'main', cwd=peer)
        return self.f.git('rev-parse', 'HEAD', cwd=peer).stdout.strip()

    def test_real_publication_profiles_all_phases_without_dirty_receipt(self):
        result = self.f.adapter.action('publish')
        profile = self.profile(result)
        self.assertEqual(profile['status'], 'ok')
        self.assertEqual([p['stage'] for p in profile['phases']], EXPECTED_STAGES)
        self.assertTrue(all(p['status'] == 'ok' and p['invocation'] == 1
                            and 'seconds' in p for p in profile['phases']))
        self.assertEqual(profile['timing_errors'], 0)
        self.assertFalse(profile['truncated'])
        for prefix in ('data', 'receipt'):
            total = next(p['seconds'] for p in profile['phases']
                         if p['stage'] == prefix+'_publisher_total')
            callbacks = sum(p['seconds'] for p in profile['phases']
                            if p['stage'].startswith(prefix+'_post_rebase_'))
            self.assertGreaterEqual(total, callbacks)
        self.assertEqual(self.f.git('status', '--porcelain', '--untracked-files=no').stdout, '')
        for path in (boundary.RUNTIME_PATH, boundary.PAIR_PATH):
            body = subprocess.run(['git', '--git-dir', str(self.f.remote), 'show',
                                   'refs/heads/main:'+path], capture_output=True, check=True).stdout
            self.assertEqual(body, (self.f.root/path).read_bytes())
        receipt = json.loads((self.f.root/boundary.PAIR_PATH).read_text())
        self.assertNotIn('publication_profile', receipt)
        self.assertEqual(self.f.git('merge-base', '--is-ancestor', receipt['remote_head'],
                                    result['head'], check=False).returncode, 0)

    def test_preflight_rejection_preserves_exception_chain_and_precommit_gate(self):
        cause = OSError('original cause')
        refusal = ValueError('fixture preflight refusal')
        refusal.__cause__ = cause
        with patch.object(pair, 'guard', side_effect=refusal):
            with self.assertRaises(ValueError) as caught:
                self.f.adapter.action('publish')
        self.assertIs(caught.exception, refusal)
        self.assertIs(caught.exception.__cause__, cause)
        profile = self.profile(runtime.exception_details(caught.exception))
        self.assertEqual(profile['status'], 'error')
        self.assertEqual([p['stage'] for p in profile['phases']], EXPECTED_STAGES[:3])
        self.assertEqual(profile['phases'][-1]['status'], 'error')
        self.assertEqual(self.f.git('rev-parse', 'HEAD').stdout.strip(), self.f.initial)
        self.assertEqual(self.f.git('--git-dir', str(self.f.remote), 'rev-parse',
                                    'refs/heads/main').stdout.strip(), self.f.initial)

    def test_actual_divergent_rebase_retains_callback_and_remote_changes(self):
        peer_head = self.peer_change('remote-note.txt', 'independent fixture\n')
        result = self.f.adapter.action('publish')
        profile = self.profile(result)
        self.assertEqual([p['stage'] for p in profile['phases']], EXPECTED_STAGES)
        self.assertEqual(self.f.git('merge-base', '--is-ancestor', peer_head,
                                    result['head'], check=False).returncode, 0)
        self.assertEqual((self.f.root/'remote-note.txt').read_text(), 'independent fixture\n')
        self.assertEqual(self.f.git('status', '--porcelain', '--untracked-files=no').stdout, '')

    def test_actual_rebase_bad_evidence_is_rejected_before_push(self):
        peer_head = self.peer_change('data/direction_v42/forward_registry.csv',
                                     'forecast_id\nunacceptable-fixture\n')
        with self.assertRaisesRegex(ValueError, 'fixture does not permit') as caught:
            self.f.adapter.action('publish')
        profile = self.profile(runtime.exception_details(caught.exception))
        self.assertEqual([p['stage'] for p in profile['phases']], EXPECTED_STAGES[:6])
        self.assertEqual([p['status'] for p in profile['phases'][-2:]], ['error', 'error'])
        self.assertEqual(self.f.git('--git-dir', str(self.f.remote), 'rev-parse',
                                    'refs/heads/main').stdout.strip(), peer_head)

    def test_each_existing_push_retry_callback_is_measured(self):
        command = publisher.command
        failed_once = []

        def reject_first_push(root, *args):
            if args[0] == 'push' and not failed_once:
                failed_once.append(True)
                raise subprocess.CalledProcessError(1, ['git', 'push'], stderr=b'offline retry fixture')
            return command(root, *args)

        with patch.object(publisher, 'command', reject_first_push):
            result = self.f.adapter.action('publish')
        profile = self.profile(result)
        self.assertEqual(profile['status'], 'ok')
        for suffix in ('validate_state', 'pair_guard', 'pair_history_guard', 'validate_p2'):
            self.assertEqual([p['invocation'] for p in profile['phases']
                              if p['stage'] == 'data_post_rebase_'+suffix], [1, 2])
        self.assertEqual(self.f.git('--git-dir', str(self.f.remote), 'rev-parse',
                                    'refs/heads/main').stdout.strip(), result['head'])

    def test_supplementary_rejection_profile_is_saved_in_existing_cycle_receipt(self):
        from test_runtime_deadline_service import Adapter, Clock
        clock = Clock()
        adapter = Adapter(clock)
        adapter.registry = [{'forecast_id': 'already-issued', 'horizon_hours': '1',
                             'source_hour_start_utc': clock().isoformat(),
                             'target_utc': (clock()+timedelta(hours=1)).isoformat()}]
        other_action = adapter.action
        adapter.action = lambda name: self.f.adapter.action(name) if name == 'publish' else other_action(name)
        verify = publisher.verify_readback

        def reject_supplementary(root, head, expected):
            return False if boundary.RUNTIME_PATH in expected else verify(root, head, expected)

        with patch.object(publisher, 'verify_readback', reject_supplementary):
            result = runtime.run(self.f.root, adapter, 1, clock, clock.sleep)
        self.assertTrue(result['fatal'])
        self.assertEqual(result['errors'], 1)
        receipts = list((self.f.root/runtime.BASE/'receipts').glob('*.json'))
        self.assertEqual(len(receipts), 1)
        failure = json.loads(receipts[0].read_text())['errors'][0]
        self.assertIn('supplementary evidence remote readback mismatch', failure['error'])
        profile = self.profile(failure)
        self.assertEqual(profile['status'], 'error')
        self.assertEqual(profile['phases'][-1]['stage'], 'supplementary_readback')
        self.assertEqual(profile['phases'][-1]['status'], 'error')
        self.assertNotIn('receipt_publisher_total', [p['stage'] for p in profile['phases']])

    def test_final_receipt_rejection_profile_is_saved_without_safe_output(self):
        verify = publisher.verify_readback

        def reject_receipt(root, head, expected):
            if (boundary.PAIR_PATH in expected and
                    json.loads(expected[boundary.PAIR_PATH]).get('status') == 'verified_remote_readback'):
                return False
            return verify(root, head, expected)

        with patch.object(publisher, 'verify_readback', reject_receipt):
            result = runtime.finish(self.f.root, self.f.adapter, self.f.result())
        self.assertTrue(result['fatal'])
        self.assertNotIn('fatal=false', self.f.output.read_text())
        saved = json.loads((self.f.root/boundary.RUNTIME_PATH).read_text())
        self.assertIn('final_publication_diagnostics', saved)
        profile = self.profile(saved['final_publication_diagnostics'])
        self.assertEqual(profile['phases'][-1]['stage'], 'receipt_readback')
        self.assertEqual(profile['phases'][-1]['status'], 'error')
        self.assertEqual(profile['status'], 'error')

    def test_clock_failure_does_not_mask_publication_or_original_rejection(self):
        with patch.object(runtime.time, 'monotonic', side_effect=OSError('diagnostic clock failed')):
            result = self.f.adapter.action('publish')
            profile = self.profile(result)
            self.assertEqual(profile['status'], 'ok')
            self.assertGreater(profile['timing_errors'], 0)
            self.assertTrue(all('seconds' not in p for p in profile['phases']))
            refusal = ValueError('original validation refusal')
            with patch.object(pair, 'guard', side_effect=refusal):
                with self.assertRaises(ValueError) as caught:
                    self.f.adapter.action('publish')
        self.assertIs(caught.exception, refusal)
        self.assertEqual(self.profile(runtime.exception_details(refusal))['status'], 'error')

    def test_exception_profile_is_bounded_and_cannot_include_arbitrary_metadata(self):
        refusal = ValueError('original validation refusal')
        phases = [{'stage': 'preflight_validate_p2', 'invocation': 1,
                   'status': 'ok', 'seconds': .1, 'argv': 'secret-argv'} for _ in range(100)]
        phases[0]['stage'] = 'https://secret.invalid/private?sig=secret-url'
        phases[1]['seconds'] = float('nan')
        phases[2]['seconds'] = float('inf')
        phases[3]['invocation'] = True
        refusal.publication_profile = {'schema_version': 1, 'clock': 'monotonic',
            'scope': 'nested_publisher_totals_include_callbacks_do_not_sum', 'status': 'error',
            'phases': phases, 'truncated': False, 'timing_errors': 0, 'env': 'secret-env'}
        profile = self.profile(runtime.exception_details(refusal))
        self.assertTrue(profile['truncated'])
        self.assertGreater(profile['timing_errors'], 0)
        serialized = json.dumps(profile, allow_nan=False)
        self.assertNotIn('secret-', serialized)
        self.assertNotIn('argv', serialized)
        self.assertNotIn('env', serialized)
        self.assertLess(len(serialized), 16384)
        refusal.publication_profile = profile
        self.assertEqual(runtime.exception_details(refusal)['publication_profile'], profile)

    def test_profile_serialization_failure_keeps_original_success_and_failure(self):
        refusal = ValueError('original preflight refusal')
        cause = OSError('original cause')
        refusal.__cause__ = cause
        with patch.object(runtime, 'safe_publication_profile', side_effect=RuntimeError('diagnostic failure')):
            result = self.f.adapter.action('publish')
            self.assertEqual(result['status'], 'verified_remote_readback')
            self.assertEqual(result['publication_profile']['phases'], [])
            self.assertTrue(result['publication_profile']['truncated'])
            with patch.object(pair, 'guard', side_effect=refusal):
                with self.assertRaises(ValueError) as caught:
                    self.f.adapter.action('publish')
        self.assertIs(caught.exception, refusal)
        self.assertIs(caught.exception.__cause__, cause)
        self.assertEqual(self.profile(runtime.exception_details(refusal))['status'], 'error')

    def test_diagnostic_cap_never_skips_later_validator_or_hides_its_rejection(self):
        calls = []
        refusal = ValueError('original validator after diagnostic cap')

        def validator(root):
            calls.append(root)
            self.f.fixture_validate(root)
            if len(calls) == 71: raise refusal

        with self.assertRaises(ValueError) as caught:
            with runtime.PublicationProfile() as profile:
                for _ in range(71):
                    profile.call('preflight_pair_guard', validator, self.f.root)
        self.assertIs(caught.exception, refusal)
        self.assertEqual(len(calls), 71)
        saved = self.profile(runtime.exception_details(refusal))
        self.assertEqual(len(saved['phases']), 64)
        self.assertEqual(saved['status'], 'error')
        self.assertTrue(saved['truncated'])


if __name__ == '__main__':
    unittest.main()

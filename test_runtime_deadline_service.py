"""Future capture deadlines, serial execution and honest receipts."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


if __name__ == '__main__': unittest.main()

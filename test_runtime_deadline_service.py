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
    def test_workflow_budget_reaches_capture_loop_and_saved_final_receipt(self):
        import archive_window_budget as budget
        clock = self.clock
        class ObservedDatetime(datetime):
            @classmethod
            def now(cls, tz=None): return clock()
        rows = [{'forecast_id': 'day', 'horizon_hours': '24',
                 'issued_at_utc': (START-timedelta(minutes=1)).isoformat(),
                 'target_utc': (START+timedelta(days=1)).isoformat()}]
        # Keep the real planner, loop, target-window checks and receipt writes.
        # Frozen-source loading and all exchange/Git commands are offline seams.
        def from_root(root, now, maximum, reserve, **options):
            return budget.plan(rows, now, maximum, reserve, **options)
        self.adapter.python = lambda *args: None
        self.adapter.command = lambda *args: None
        self.adapter.auditor = SimpleNamespace(close=lambda: None)
        self.adapter.registry = [self.row(target=START-timedelta(minutes=24))]
        output = self.root/'outputs.txt'
        printed = io.StringIO()
        arguments = ['runtime_deadline_service.py', '--duration-seconds', '2400',
                     '--archive-reserve-seconds', '1800',
                     '--workflow-started-at', '2026-10-25T09:56:32+00:00',
                     '--workflow-timeout-seconds', '3900']
        with patch.object(runtime, 'LiveAdapter', return_value=self.adapter), \
             patch.object(runtime, '__file__', str(self.root/'runtime_deadline_service.py')), \
             patch.object(runtime, 'datetime', ObservedDatetime), \
             patch.object(runtime.time, 'sleep', self.clock.sleep), \
             patch.object(budget, 'from_root', from_root), \
             patch.object(sys, 'argv', arguments), \
             patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}), redirect_stdout(printed):
            try:
                code = runtime.main()
            except SystemExit as exc:
                self.fail('workflow budget was not accepted by the runtime: '+str(exc))
        self.assertEqual(code, 0)
        saved = json.loads((self.root/runtime.BASE/'latest.json').read_text())
        self.assertEqual(saved['archive_window_budget']['duration_seconds'], 1292)
        self.assertEqual(saved['ended_at_utc'], (START+timedelta(seconds=1292)).isoformat())
        self.assertEqual(self.adapter.calls[0], 'watch')
        self.assertEqual(output.read_text().splitlines()[-1], 'fatal=false')
        self.assertFalse(saved['historical_gaps_repaired'])
    def test_cycle_overrun_is_reported_without_overlapping_or_catchup(self):
        self.adapter.duration['source_audit'] = 400
        out = runtime.run(self.root, self.adapter, duration_seconds=500,
                          clock=self.clock, sleep=self.clock.sleep)
        self.assertGreater(out['maximum_cycle_seconds'], 300)
        self.assertFalse(out['cadence_target_met'])
    def exhausted_main(self, *, integrity_error=False):
        import archive_window_budget as budget
        clock = self.clock
        class ObservedDatetime(datetime):
            @classmethod
            def now(cls, tz=None): return clock()
        def from_root(root, now, maximum, reserve, **options):
            return budget.plan([], now, maximum, reserve, **options)
        def integrity(*args):
            if integrity_error: raise ValueError('integrity rejected')
        self.adapter.python = integrity
        self.adapter.command = lambda *args: None
        self.adapter.auditor = SimpleNamespace(close=lambda: None)
        output = self.root/'outputs.txt'
        arguments = ['runtime_deadline_service.py', '--duration-seconds', '2400',
                     '--archive-reserve-seconds', '1800', '--workflow-started-at',
                     (START-timedelta(seconds=1500)).isoformat(),
                     '--workflow-timeout-seconds', '3900']
        with patch.object(runtime, 'LiveAdapter', return_value=self.adapter), \
             patch.object(runtime, '__file__', str(self.root/'runtime_deadline_service.py')), \
             patch.object(runtime, 'datetime', ObservedDatetime), \
             patch.object(budget, 'from_root', from_root), \
             patch.object(sys, 'argv', arguments), \
             patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}), \
             redirect_stdout(io.StringIO()):
            code = runtime.main()
        saved = json.loads((self.root/runtime.BASE/'latest.json').read_text())
        self.assertEqual(code, 1)
        self.assertTrue(saved['fatal'])
        self.assertEqual(saved['status'], 'DEGRADED')
        self.assertEqual(output.read_text().splitlines()[-1], 'fatal=true')
        return saved
    def test_exhausted_budget_services_due_target_once_without_new_work(self):
        self.adapter.registry = [self.row(target=START-timedelta(minutes=24))]
        saved = self.exhausted_main()
        self.assertEqual(self.adapter.calls, ['watch', 'publish'])
        self.assertEqual(saved['cycles'], 0)
        self.assertEqual(saved['fatal_reason'], 'WORKFLOW_BUDGET_EXHAUSTED')
        self.assertEqual(saved['archive_window_budget']['duration_seconds'], 0)
        receipt = saved['due_only_receipt']
        self.assertEqual(receipt['open_target_ids'], ['due'])
        self.assertEqual(receipt['errors'], [])
        self.assertFalse(receipt['historical_backfill'])
        paths = list((self.root/runtime.BASE/'receipts').glob('*.json'))
        self.assertEqual(len(paths), 1)
        self.assertEqual(json.loads(paths[0].read_text()), receipt)
    def test_exhausted_budget_without_open_targets_does_no_network_work(self):
        self.adapter.registry = [self.row('expired', START-timedelta(minutes=26)),
                                 self.row('future', START+timedelta(seconds=1)),
                                 self.row('scored', START)]
        self.adapter.scores = {'scored': {'status': 'scored'}}
        saved = self.exhausted_main()
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(saved['due_only_receipt']['open_target_ids'], [])
        self.assertEqual(saved['cycles'], 0)
    def test_exhausted_capture_failure_still_publishes_valid_partial_evidence(self):
        self.adapter.registry = [self.row()]
        self.adapter.fail = {'watch'}
        saved = self.exhausted_main()
        self.assertEqual(self.adapter.calls, ['watch', 'publish'])
        self.assertEqual([e['stage'] for e in saved['due_only_receipt']['errors']], ['watch'])
    def test_exhausted_publication_failure_is_not_retried_or_cleared(self):
        self.adapter.registry = [self.row()]
        self.adapter.fail = {'publish'}
        saved = self.exhausted_main()
        self.assertEqual(self.adapter.calls, ['watch', 'publish'])
        self.assertEqual([e['stage'] for e in saved['due_only_receipt']['errors']], ['publish'])
    def test_integrity_failure_never_enters_exhausted_due_only_path(self):
        self.adapter.registry = [self.row()]
        saved = self.exhausted_main(integrity_error=True)
        self.assertEqual(self.adapter.calls, [])
        self.assertNotIn('due_only_receipt', saved)
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


class PendingSmokeReplayAdapter(Adapter):
    """Offline action timings from run 42458999547's 09:00 UTC cycle."""
    def __init__(self, clock, root):
        super().__init__(clock)
        self.root = root
        self.started = clock()
        self.book_result = {'status': 'command_completed', 'mode': 'smoke',
                            'formal_eligibility': False}
        self.audit_status = 'pending'
        self.prepared_path = root/'data/prepared_source_context.json'
        self.published_prepared = []
        self.raw_paths = []
        self.published_raw = []
        self.fail_publish_number = None
    def action(self, name):
        self.calls.append(name)
        number = self.calls.count(name)
        seconds = {'prepare': 4.971565, 'issue': 26.722580,
                   'source_audit': 0.019398, 'book': 0.515468}.get(name, 0)
        if name == 'publish': seconds = 181.037305 if number == 1 else 182.766045
        if name == 'witness': seconds = 1.124913 if number == 1 else 0.024160
        seconds = self.duration.get(name, seconds)
        self.clock.sleep(seconds)
        if name == 'prepare':
            self.prepared_path.parent.mkdir(parents=True, exist_ok=True)
            self.prepared_path.write_bytes(b'{"prepared_source_context":"current_hour"}\n')
        if name == 'book':
            import p2_paired_execution_v3_1 as engine
            qid, relative = engine.save_book(self.root, {'synthetic_raw_book': number}, engineering=True)
            path = self.root/relative
            (self.root/engine.BASE/'engineering_smoke_latest.json').write_text(json.dumps(
                {'status': 'ok', 'book_sha256': qid, 'snapshot_path': relative}))
            self.smoke_qid = qid
            self.raw_paths.append(path)
        if name in self.fail or (name == 'publish' and number == self.fail_publish_number):
            raise RuntimeError(name + ' failed')
        if name == 'issue':
            self.registry.append({'forecast_id': 'issued', 'horizon_hours': '1',
                'source_hour_start_utc': self.started.isoformat(),
                'target_utc': (self.started+timedelta(hours=1)).isoformat()})
        if name == 'watch':
            for fid in runtime.open_targets(self.registry, self.scores, self.clock()):
                self.scores[fid] = {'status': 'scored'}
        if name == 'publish':
            self.published_prepared.append(self.prepared_path.read_bytes()
                                           if self.prepared_path.exists() else None)
            self.published_raw.append({p.name: p.read_bytes() for p in self.raw_paths})
            return {'status': 'verified_remote_readback'}
        if name == 'witness': return {'status': 'requested_only'}
        if name == 'source_audit': return {'status': self.audit_status}
        if name == 'book': return self.book_result
        if name == 'preserve_smoke': return runtime.preserve_smoke_book(self.root, self.smoke_qid)
        return {'status': 'command_completed'}


class DeferredSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.case = 0
        self.make_adapter()
    def make_adapter(self, already_issued=False):
        self.case += 1
        self.clock = Clock()
        self.clock.at = datetime(2026, 11, 8, 9, tzinfo=timezone.utc)
        self.start = self.clock()
        self.root = Path(self.tmp.name)/f'case-{self.case}'
        self.adapter = PendingSmokeReplayAdapter(self.clock, self.root)
        if already_issued:
            self.adapter.registry = [{'forecast_id': 'old', 'horizon_hours': '1',
                'source_hour_start_utc': self.start.isoformat(),
                'target_utc': (self.start+timedelta(hours=1)).isoformat()}]
    def add_target(self, seconds):
        self.adapter.registry.append({'forecast_id': 'due', 'horizon_hours': '1',
            'source_hour_start_utc': (self.start-timedelta(hours=1)).isoformat(),
            'target_utc': (self.start+timedelta(seconds=seconds)).isoformat()})
    def cycle(self, state=None):
        return runtime.cycle(self.adapter, {} if state is None else state, self.clock)
    def test_recorded_duplicate_publication_cycle_defers_only_smoke(self):
        result = self.cycle()
        self.assertEqual(self.adapter.calls.count('publish'), 1)
        self.assertAlmostEqual((self.clock()-self.start).total_seconds(), 214.391229, places=6)
        self.assertTrue(result.get('this_cycle_smoke_publication_deferred', False))
        self.assertTrue(result.get('this_cycle_deferred_smoke_pending_at_end', False))
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.adapter.published_raw, [{}])
        self.assertTrue(self.adapter.raw_paths[0].read_bytes())
    def test_next_ready_tick_publishes_all_samples_retained_while_audit_pending(self):
        state = {}
        first = self.cycle(state)
        raw = self.adapter.raw_paths[0].read_bytes()
        self.assertTrue(first.get('this_cycle_deferred_smoke_pending_at_end', False))
        second = self.cycle(state)
        self.assertTrue(second.get('this_cycle_smoke_publication_deferred', False))
        self.assertEqual(self.adapter.calls.count('publish'), 1)
        self.adapter.audit_status = 'ready'
        third = self.cycle(state)
        self.assertFalse(third.get('this_cycle_smoke_publication_deferred', False))
        self.assertEqual(self.adapter.calls.count('publish'), 2)
        self.assertEqual(self.adapter.published_raw[-1][self.adapter.raw_paths[0].name], raw)
        self.assertEqual(len(self.adapter.published_raw[-1]), 3)
        self.assertEqual(self.adapter.raw_paths[0].read_bytes(), raw)
    def test_pending_smoke_without_prior_core_publication_is_only_locally_retained(self):
        self.make_adapter(already_issued=True)
        self.adapter.duration['publish'] = 240
        result = self.cycle()
        self.assertEqual(result['issuance'], 'ALREADY_ISSUED')
        self.assertNotIn('publish', self.adapter.calls)
        self.assertTrue(result['this_cycle_deferred_smoke_pending_at_end'])
        kept = next(a['result'] for a in result['actions'] if a['stage'] == 'preserve_smoke')
        self.assertEqual((self.root/kept['artifact_path']).read_bytes(), self.adapter.raw_paths[0].read_bytes())
        self.assertFalse(kept['remote_readback_verified'])
        self.assertFalse(kept['worm_verified'])
        self.assertEqual(self.adapter.published_raw, [])
    def test_preparation_crossing_reserve_or_hour_must_publish_written_context(self):
        for seconds, issuance in ((4.971565, 'SKIP_RESERVE_EXHAUSTED'),
                                  (1202, 'SKIP_SOURCE_HOUR_CHANGED')):
            with self.subTest(prepare_seconds=seconds, issuance=issuance):
                self.make_adapter()
                self.clock.at = self.start+timedelta(minutes=39, seconds=59)
                self.adapter.duration['prepare'] = seconds
                result = self.cycle()
                self.assertEqual(result['issuance'], issuance)
                self.assertEqual(result['errors'], [])
                self.assertEqual(self.adapter.calls, ['prepare', 'source_audit', 'book',
                                                      'publish', 'witness'])
                self.assertEqual(self.adapter.published_prepared,
                                 [self.adapter.prepared_path.read_bytes()])
                self.assertEqual(self.adapter.published_raw[-1][self.adapter.raw_paths[0].name],
                                 self.adapter.raw_paths[0].read_bytes())
                self.assertFalse(result['this_cycle_smoke_publication_deferred'])
                self.assertFalse(result['this_cycle_deferred_smoke_pending_at_end'])
    def test_pending_smoke_does_not_put_a_240_second_publish_before_newly_due_target(self):
        self.make_adapter(already_issued=True)
        self.adapter.duration['publish'] = 240
        self.add_target(0.25)  # Becomes due during book, after the pre-book target check.
        result = self.cycle()
        watch = next(a for a in result['actions'] if a['stage'] == 'watch')
        self.assertEqual(watch['started_at_utc'], (self.start+timedelta(seconds=0.534866)).isoformat())
        self.assertEqual(self.adapter.calls, ['source_audit', 'book', 'preserve_smoke',
                                              'watch', 'publish', 'witness'])
        self.assertIn('due', self.adapter.scores)
        self.assertTrue(result['this_cycle_smoke_publication_deferred'])
        self.assertFalse(result['this_cycle_deferred_smoke_pending_at_end'])
        self.assertTrue(self.adapter.published_raw[-1])
        self.assertEqual(result['errors'], [])
    def test_finish_flushes_smoke_deferred_without_any_prior_core_publication(self):
        self.make_adapter(already_issued=True)
        result = runtime.run(self.root, self.adapter, 1, self.clock, self.clock.sleep)
        self.assertNotIn('publish', self.adapter.calls)
        raw = self.adapter.raw_paths[0].read_bytes()
        finished = runtime.finish(self.root, self.adapter, result)
        self.assertFalse(finished['fatal'])
        self.assertEqual(self.adapter.calls.count('publish'), 1)
        self.assertEqual(self.adapter.published_raw[-1][self.adapter.raw_paths[0].name], raw)
    def test_existing_finish_publishes_pending_raw_without_resetting_clock(self):
        result = runtime.run(self.root, self.adapter, 1, self.clock, self.clock.sleep)
        raw = self.adapter.raw_paths[0].read_bytes()
        self.assertEqual(self.adapter.calls.count('publish'), 1)
        self.assertAlmostEqual(result['maximum_cycle_seconds'], 214.391229, places=6)
        self.assertTrue(result['cadence_target_met'])
        finished = runtime.finish(self.root, self.adapter, result)
        self.assertFalse(finished['fatal'])
        self.assertEqual(self.adapter.published_raw[-1][self.adapter.raw_paths[0].name], raw)
        self.assertEqual(self.adapter.raw_paths[0].read_bytes(), raw)
        self.assertAlmostEqual((self.clock()-self.start).total_seconds(), 397.157274, places=6)
    def test_target_opening_during_smoke_is_immediately_published_and_pending_cleared(self):
        self.add_target(214)  # After the pre-book check, before book completes.
        result = runtime.run(self.root, self.adapter, 1, self.clock, self.clock.sleep)
        receipt = json.loads(next((self.root/runtime.BASE/'receipts').glob('*.json')).read_text())
        self.assertEqual(self.adapter.calls[-3:], ['watch', 'publish', 'witness'])
        self.assertTrue(receipt.get('this_cycle_smoke_publication_deferred', False))
        self.assertFalse(receipt.get('this_cycle_deferred_smoke_pending_at_end', False))
        self.assertIn('due', self.adapter.scores)
        self.assertTrue(self.adapter.published_raw[-1])
        self.assertAlmostEqual(result['maximum_cycle_seconds'], 397.181434, places=6)
        self.assertFalse(result['cadence_target_met'])
        self.assertEqual(result['status'], 'DEGRADED')
    def test_failed_due_publication_keeps_pending_and_fatal(self):
        self.add_target(214)
        self.adapter.fail_publish_number = 2
        result = self.cycle()
        self.assertTrue(result['fatal'])
        self.assertTrue(result.get('this_cycle_deferred_smoke_pending_at_end', False))
        self.assertEqual(result['errors'][-1]['stage'], 'publish')
        self.assertTrue(self.adapter.raw_paths[0].exists())
        copy = self.root/runtime.BASE/'deferred_smoke_books'/self.adapter.smoke_qid[:2]/(self.adapter.smoke_qid+'.json')
        self.assertEqual(copy.read_bytes(), self.adapter.raw_paths[0].read_bytes())
        count = self.adapter.calls.count('publish')
        runtime.finish(self.root, self.adapter, result)
        self.assertEqual(self.adapter.calls.count('publish'), count)
        self.assertTrue(copy.exists())
    def test_next_tick_fatal_keeps_first_raw_in_existing_artifact_scope(self):
        state = {}
        self.cycle(state)
        raw = self.adapter.raw_paths[0].read_bytes()
        qid = self.adapter.smoke_qid
        self.adapter.fail_publish_number = 2
        self.adapter.audit_status = 'ready'
        result = self.cycle(state)
        self.assertTrue(result['fatal'])
        runtime.finish(self.root, self.adapter, result)
        copy = self.root/runtime.BASE/'deferred_smoke_books'/qid[:2]/(qid+'.json')
        self.assertEqual(copy.read_bytes(), raw)
    def test_failed_finish_keeps_raw_copy_and_explicit_failure(self):
        for already_issued in (False, True):
            with self.subTest(already_issued=already_issued):
                self.make_adapter(already_issued=already_issued)
                result = runtime.run(self.root, self.adapter, 1, self.clock, self.clock.sleep)
                raw = self.adapter.raw_paths[0].read_bytes()
                qid = self.adapter.smoke_qid
                self.adapter.fail_publish_number = self.adapter.calls.count('publish')+1
                finished = runtime.finish(self.root, self.adapter, result)
                self.assertTrue(finished['fatal'])
                self.assertEqual(finished['status'], 'DEGRADED')
                self.assertIn('final_publication_error', finished)
                copy = self.root/runtime.BASE/'deferred_smoke_books'/qid[:2]/(qid+'.json')
                self.assertTrue(copy.exists(), 'failed finish lost the retained local smoke copy')
                self.assertEqual(copy.read_bytes(), raw)
    def test_raw_preservation_failure_uses_original_immediate_publication(self):
        for already_issued in (False, True):
            with self.subTest(already_issued=already_issued):
                self.make_adapter(already_issued=already_issued)
                with patch.object(runtime, 'preserve_smoke_book', side_effect=OSError('disk full')):
                    result = self.cycle()
                self.assertEqual(self.adapter.calls.count('publish'), 1 if already_issued else 2)
                self.assertFalse(result.get('this_cycle_smoke_publication_deferred', False))
                self.assertTrue(any(e['stage'] == 'preserve_smoke' and 'disk full' in e['error']
                                    for e in result['errors']))
                self.assertTrue(self.adapter.published_raw[-1])
    def test_partial_copy_write_leaves_no_truncated_sha_file_or_temporary(self):
        actual_temporary = tempfile.NamedTemporaryFile
        actual_open = Path.open
        partial_paths = []
        class PartialWriter:
            def __init__(self, stream): self.stream = stream
            def __getattr__(self, name): return getattr(self.stream, name)
            def __enter__(self): return self
            def __exit__(self, *args): return self.stream.__exit__(*args)
            def write(self, body):
                self.stream.write(body[:7])
                self.stream.flush()
                path = Path(self.stream.name)
                partial_paths.append((path, path.read_bytes()))
                raise OSError('injected failure after a real partial write')
        def temporary(*args, **kwargs): return PartialWriter(actual_temporary(*args, **kwargs))
        def direct_open(path, mode='r', *args, **kwargs):
            stream = actual_open(path, mode, *args, **kwargs)
            return PartialWriter(stream) if mode == 'xb' and 'deferred_smoke_books' in path.parts else stream
        with patch.object(tempfile, 'NamedTemporaryFile', side_effect=temporary), \
                patch.object(Path, 'open', direct_open):
            result = self.cycle()
        self.assertEqual(self.adapter.calls.count('publish'), 2)
        self.assertEqual(result['errors'][-1]['stage'], 'preserve_smoke')
        self.assertEqual(len(partial_paths), 1)
        self.assertEqual(len(partial_paths[0][1]), 7)
        self.assertFalse(partial_paths[0][0].exists())
        qid = self.adapter.smoke_qid
        target = self.root/runtime.BASE/'deferred_smoke_books'/qid[:2]/(qid+'.json')
        self.assertFalse(target.exists())
        self.assertEqual(self.adapter.published_raw[-1][qid+'.json'], self.adapter.raw_paths[0].read_bytes())
        self.assertFalse(result.get('this_cycle_smoke_publication_deferred', False))
    def test_real_smoke_bytes_are_bound_to_current_stdout_and_latest(self):
        import p2_paired_execution_v3_1 as engine
        qid, relative = engine.save_book(self.root, {'synthetic_raw_book': 'binding'}, engineering=True)
        latest = self.root/engine.BASE/'engineering_smoke_latest.json'
        latest.write_text(json.dumps({'status': 'ok', 'book_sha256': qid, 'snapshot_path': relative}))
        with patch.dict(os.environ, {'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': runtime.REPO}):
            live = runtime.LiveAdapter(self.root)
        live.source_ready = lambda: False
        live.python = lambda *args: json.dumps({'status': 'ok', 'book_sha256': qid})
        live.action('book')
        kept = live.action('preserve_smoke')
        self.assertEqual((self.root/kept['artifact_path']).read_bytes(), (self.root/relative).read_bytes())
        self.assertFalse(kept['remote_readback_verified'])
        self.assertFalse(kept['worm_verified'])
        self.assertEqual(live.action('preserve_smoke'), kept)
        latest.write_text(json.dumps({'status': 'ok', 'book_sha256': 'a'*64, 'snapshot_path': relative}))
        with self.assertRaises(ValueError): live.action('preserve_smoke')
    def test_raw_hash_or_existing_copy_mismatch_is_never_overwritten(self):
        import p2_paired_execution_v3_1 as engine
        qid, relative = engine.save_book(self.root, {'synthetic_raw_book': 'tamper'}, engineering=True)
        latest = self.root/engine.BASE/'engineering_smoke_latest.json'
        latest.write_text(json.dumps({'status': 'ok', 'book_sha256': qid, 'snapshot_path': relative}))
        kept = runtime.preserve_smoke_book(self.root, qid)
        copy = self.root/kept['artifact_path']
        copy.write_bytes(b'existing mismatch')
        with self.assertRaises(ValueError): runtime.preserve_smoke_book(self.root, qid)
        self.assertEqual(copy.read_bytes(), b'existing mismatch')
        (self.root/relative).write_bytes(b'changed original')
        with self.assertRaises(ValueError): runtime.preserve_smoke_book(self.root, qid)
        self.assertEqual(copy.read_bytes(), b'existing mismatch')
    def test_formal_capture_or_nonexplicit_smoke_proof_is_never_deferred(self):
        cases = ({'mode': 'capture', 'formal_eligibility': True},
                     {'mode': 'capture', 'formal_eligibility': False},
                     {'mode': 'smoke', 'formal_eligibility': True},
                     {'mode': 'smoke'}, {'formal_eligibility': False},
                     {'mode': 'smoke', 'formal_eligibility': 0})
        for already_issued in (False, True):
            for book in cases:
                with self.subTest(book=book, already_issued=already_issued):
                    self.make_adapter(already_issued=already_issued)
                    self.adapter.book_result = book
                    result = self.cycle()
                    self.assertEqual(self.adapter.calls.count('publish'), 1 if already_issued else 2)
                    self.assertFalse(result.get('this_cycle_smoke_publication_deferred', False))
    def test_book_or_witness_error_prevents_deferral(self):
        for failed in ('book', 'witness'):
            with self.subTest(failed=failed):
                self.make_adapter()
                self.adapter.fail = {failed}
                result = self.cycle()
                self.assertEqual(self.adapter.calls.count('publish'), 2)
                self.assertFalse(result.get('this_cycle_smoke_publication_deferred', False))
                self.assertTrue(result['errors'])
                self.assertTrue(self.adapter.published_raw[-1])
    def test_earlier_watch_error_prevents_deferral_after_successful_issuance(self):
        self.add_target(0)
        self.adapter.fail = {'watch'}
        result = self.cycle()
        self.assertGreaterEqual(self.adapter.calls.count('publish'), 3)
        self.assertFalse(result.get('this_cycle_smoke_publication_deferred', False))
        self.assertTrue(result['errors'])
    def test_nonpending_audit_cannot_defer_with_or_without_new_issuance(self):
        for already_issued in (False, True):
            with self.subTest(already_issued=already_issued):
                self.make_adapter(already_issued=already_issued)
                self.adapter.audit_status = 'ready'
                result = self.cycle()
                self.assertEqual(self.adapter.calls.count('publish'), 1 if already_issued else 2)
                self.assertFalse(result.get('this_cycle_smoke_publication_deferred', False))
                self.assertTrue(self.adapter.published_raw[-1])
    def test_no_issue_cycle_errors_keep_the_original_immediate_publish_path(self):
        for failed in ('source_audit', 'book'):
            with self.subTest(failed=failed):
                self.make_adapter(already_issued=True)
                self.adapter.fail = {failed}
                result = self.cycle()
                self.assertEqual(self.adapter.calls.count('publish'), 1)
                self.assertNotIn('preserve_smoke', self.adapter.calls)
                self.assertFalse(result['this_cycle_smoke_publication_deferred'])
                self.assertTrue(any(e['stage'] == failed for e in result['errors']))


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

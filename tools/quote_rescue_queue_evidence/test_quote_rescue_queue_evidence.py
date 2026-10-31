"""Offline contract tests; all UNIT_TEST_* data are synthetic, never forecasts."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sample():
    return {
        'run': {'id': 123, 'run_attempt': 1, 'event': 'workflow_dispatch',
                'path': '.github/workflows/quote_only_rescue.yml', 'head_branch': 'main',
                'head_sha': 'a' * 40, 'status': 'completed', 'conclusion': 'success',
                'created_at': '2026-10-19T15:41:57Z',
                'run_started_at': '2026-10-19T15:41:57Z'},
        'jobs': {'total_count': 2, 'jobs': [
            {'id': 456, 'run_id': 123, 'run_attempt': 1, 'name': 'rescue',
             'status': 'completed', 'conclusion': 'success',
             'created_at': '2026-10-19T15:41:57Z',
             'started_at': '2026-10-19T15:49:44Z',
             'completed_at': '2026-10-19T15:57:12Z'},
            {'id': 457, 'run_id': 123, 'run_attempt': 1, 'name': 'verify',
             'conclusion': 'skipped', 'created_at': '2026-10-19T15:41:57Z',
             'started_at': '2026-10-19T15:41:57Z'}]},
        'result': {'status': 'verified_window_quote_rescue', 'scored_ids': ['UNIT_TEST_ID'],
                   'quotes': [{'forecast_id': 'UNIT_TEST_ID',
                               'target_utc': '2026-10-19T15:39:00Z',
                               'target_quote_utc': '2026-10-19T15:50:20Z',
                               'observed_at_utc': '2026-10-19T15:50:21Z'}],
                   'capture': {'events': [{'attempt': 1,
                                          'started_at_utc': '2026-10-19T15:50:20Z'}]},
                   'publication': {'remote_head': 'b' * 40,
                                   'verified_at_utc': '2026-10-19T15:50:40Z'}}}


def prior_receipt(e):
    q = e['result']['quotes'][0]
    return {'run_id': 123, 'status': 'VERIFIED_NATURAL_WINDOW_RESCUE_NEW_CALLER',
            'publication': copy.deepcopy(e['result']['publication']),
            'old_rows_registry_snapshots_frozen_code_unchanged': True,
            'targets': [{'forecast_id': q['forecast_id'], 'target_utc': q['target_utc'],
                         'quote_utc': q['target_quote_utc'],
                         'remote_verified_at_utc': e['result']['publication']['verified_at_utc'],
                         'issue_target_binding_and_score_verified': True}]}


class QueueEvidenceTests(unittest.TestCase):
    def setUp(self):
        path = HERE / 'quote_rescue_queue_evidence.py'
        self.assertTrue(path.exists(), 'queue evidence implementation is absent')
        spec = importlib.util.spec_from_file_location('queue_evidence_under_test', path)
        self.q = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.q)

    def build(self, evidence=None, **kwargs):
        return self.q.build_queue_evidence(evidence or sample(), **kwargs)

    def test_job_clock_records_467_seconds_instead_of_run_started_clock(self):
        r = self.build()
        self.assertEqual(r['segments_seconds']['job_created_to_started'], 467)
        self.assertEqual(r['job']['id'], 456)
        self.assertEqual(r['queue_cause'], 'UNKNOWN')

    def test_dispatch_missing_is_unknown_not_copied_from_creation(self):
        r = self.build()
        self.assertIsNone(r['clocks_utc']['dispatch_requested_at_utc'])
        self.assertIsNone(r['segments_seconds']['dispatch_to_run_created'])

    def test_explicit_matching_caller_clock_supplies_dispatch_segment(self):
        r = self.build(caller_record={'run_id': 123, 'run_attempt': 1,
                                     'dispatch_requested_at_utc': '2026-10-19T15:41:50Z'})
        self.assertEqual(r['segments_seconds']['dispatch_to_run_created'], 7)

    def test_cross_run_or_attempt_caller_rejected(self):
        for key, value in [('run_id', 124), ('run_attempt', 2)]:
            caller = {'run_id': 123, 'run_attempt': 1,
                      'dispatch_requested_at_utc': '2026-10-19T15:41:50Z'}
            caller[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.build(caller_record=caller)

    def test_normal_workflow_is_rejected(self):
        e = sample(); e['run']['path'] = '.github/workflows/okx_hourly_extreme_history.yml'
        with self.assertRaises(ValueError): self.build(e)

    def test_non_main_and_non_dispatch_are_rejected(self):
        for key, value in [('head_branch', 'feature'), ('event', 'push')]:
            e = sample(); e['run'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.build(e)

    def test_job_other_run_cannot_be_used(self):
        e = sample(); e['jobs']['jobs'][0]['run_id'] = 999
        with self.assertRaises(ValueError): self.build(e)

    def test_multiple_same_attempt_rescue_jobs_are_ambiguous(self):
        e = sample(); other = copy.deepcopy(e['jobs']['jobs'][0]); other['id'] = 999
        e['jobs']['jobs'].append(other)
        with self.assertRaises(ValueError): self.build(e)

    def test_previous_attempt_does_not_supply_current_attempt_clocks(self):
        e = sample(); e['run']['run_attempt'] = 2
        r = self.build(e)
        self.assertIsNone(r['job'])
        self.assertIsNone(r['segments_seconds']['job_created_to_started'])

    def test_missing_job_attempt_is_not_assumed(self):
        e = sample(); del e['jobs']['jobs'][0]['run_attempt']
        self.assertIsNone(self.build(e)['job'])

    def test_pending_job_preserves_null_start_and_no_result(self):
        e = sample(); e['run']['status'] = 'queued'; e['run']['conclusion'] = None
        e.pop('result'); job = e['jobs']['jobs'][0]
        job.update(status='queued', conclusion=None, started_at=None, completed_at=None)
        r = self.build(e)
        self.assertIsNone(r['segments_seconds']['job_created_to_started'])
        self.assertEqual(r['targets'], [])
        self.assertEqual(r['prior_validation']['status'], 'NOT_PROVIDED')

    def test_failed_run_is_diagnostic_not_promoted_to_rescue_success(self):
        e = sample(); e.pop('result'); e['run']['conclusion'] = 'failure'
        r = self.build(e)
        self.assertEqual(r['run']['conclusion'], 'failure')
        self.assertEqual(r['scope'], 'QUEUE_EVIDENCE_ONLY')
        self.assertFalse(r['rescue_acceptance_evaluated'])

    def test_negative_interval_is_invalid_not_clamped_or_absolute(self):
        e = sample(); e['jobs']['jobs'][0]['started_at'] = '2026-10-19T15:40:00Z'
        r = self.build(e)
        self.assertIsNone(r['segments_seconds']['job_created_to_started'])
        self.assertIn('INVALID_ORDER:job_created_to_started', r['issues'])

    def test_non_utc_or_bad_timestamp_stays_unknown_with_issue(self):
        for stamp in ['2026-10-20T00:49:44+09:00', 'not-a-time', '2026-10-19T15:49:44']:
            e = sample(); e['jobs']['jobs'][0]['started_at'] = stamp
            r = self.build(e)
            self.assertIsNone(r['segments_seconds']['job_created_to_started'])
            self.assertTrue(any(x.startswith('INVALID_TIMESTAMP:job_started_at_utc') for x in r['issues']))

    def test_equal_clocks_produce_zero(self):
        e = sample(); e['jobs']['jobs'][0]['started_at'] = '2026-10-19T15:41:57Z'
        self.assertEqual(self.build(e)['segments_seconds']['job_created_to_started'], 0)

    def test_capture_and_publication_segments_use_actual_observation(self):
        r = self.build(); t = r['targets'][0]
        self.assertEqual(r['segments_seconds']['job_started_to_capture_attempt'], 36)
        self.assertEqual(t['segments_seconds']['capture_attempt_to_observed'], 1)
        self.assertEqual(t['segments_seconds']['observed_to_remote_verified'], 19)
        self.assertEqual(t['capture_cutoff_utc'], '2026-10-19T15:59:00+00:00')
        self.assertEqual(t['quote_deadline_utc'], '2026-10-19T16:04:00+00:00')

    def test_unknown_capture_start_is_not_inferred_from_quote(self):
        e = sample(); e['result']['capture']['events'] = []
        r = self.build(e)
        self.assertIsNone(r['clocks_utc']['capture_attempt_started_at_utc'])
        self.assertIsNone(r['targets'][0]['segments_seconds']['capture_attempt_to_observed'])

    def test_old_success_receipt_is_attached_without_replaying_or_mutating(self):
        e = sample(); receipt = prior_receipt(e); saved = copy.deepcopy((e, receipt))
        r = self.build(e, assessment=receipt)
        self.assertEqual(r['prior_validation']['status'], 'ATTACHED_PRIOR_VERIFIED_RECEIPT')
        self.assertFalse(r['prior_validation']['replayed'])
        self.assertFalse(r['performance_acceptance_evaluated'])
        self.assertEqual((e, receipt), saved)

    def test_mismatched_receipt_run_publication_or_target_is_rejected(self):
        for kind in ['run', 'head', 'target', 'binding', 'old_rows']:
            e = sample(); a = prior_receipt(e)
            if kind == 'run': a['run_id'] = 999
            if kind == 'head': a['publication']['remote_head'] = 'c' * 40
            if kind == 'target': a['targets'][0]['quote_utc'] = '2026-10-19T15:40:00Z'
            if kind == 'binding': a['targets'][0]['issue_target_binding_and_score_verified'] = False
            if kind == 'old_rows': a['old_rows_registry_snapshots_frozen_code_unchanged'] = False
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.build(e, assessment=a)

    def test_fresh_main_publication_parent_need_not_equal_trigger_head(self):
        e = sample(); e['commit'] = {'sha': 'b' * 40, 'parents': [{'sha': 'c' * 40}]}
        r = self.build(e, assessment=prior_receipt(e))
        self.assertEqual(r['prior_validation']['status'], 'ATTACHED_PRIOR_VERIFIED_RECEIPT')

    def test_legacy_receipt_cannot_be_attached_to_a_rerun_attempt(self):
        e = sample(); a = prior_receipt(e); e['run']['run_attempt'] = 2
        with self.assertRaises(ValueError): self.build(e, assessment=a)

    def test_explicit_prior_attempt_must_match(self):
        e = sample(); a = prior_receipt(e); a['run_attempt'] = 2
        with self.assertRaises(ValueError): self.build(e, assessment=a)

    def test_skipped_rescue_job_does_not_fabricate_runner_execution(self):
        e = sample(); e.pop('result')
        e['jobs']['jobs'][0].update(conclusion='skipped', started_at='2026-10-19T15:41:57Z')
        r = self.build(e)
        self.assertIsNone(r['clocks_utc']['job_started_at_utc'])
        self.assertIsNone(r['segments_seconds']['job_created_to_started'])

    def test_job_trigger_head_mismatch_rejected_without_parent_head_requirement(self):
        e = sample(); e['jobs']['jobs'][0]['head_sha'] = 'd' * 40
        with self.assertRaises(ValueError): self.build(e)

    def test_missing_or_invalid_association_clocks_never_match_each_other(self):
        for kind in ['publication', 'target', 'quote', 'observed', 'invalid_utc']:
            e = sample(); a = prior_receipt(e)
            if kind == 'publication':
                e['result']['publication'].pop('verified_at_utc')
                a['publication'].pop('verified_at_utc')
                a['targets'][0].pop('remote_verified_at_utc')
            if kind == 'target':
                e['result']['quotes'][0].pop('target_utc'); a['targets'][0].pop('target_utc')
            if kind == 'quote':
                e['result']['quotes'][0].pop('target_quote_utc'); a['targets'][0].pop('quote_utc')
            if kind == 'observed': e['result']['quotes'][0].pop('observed_at_utc')
            if kind == 'invalid_utc':
                e['result']['quotes'][0]['target_utc'] = '2026-10-19T15:39:00'
                a['targets'][0]['target_utc'] = '2026-10-19T15:39:00'
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.build(e, assessment=a)

    def test_success_receipt_requires_executed_successful_same_attempt_job(self):
        for kind in ['skipped', 'failure', 'missing', 'pending']:
            e = sample(); a = prior_receipt(e)
            if kind == 'missing': e['jobs'] = {'total_count': 0, 'jobs': []}
            elif kind == 'pending': e['jobs']['jobs'][0]['status'] = 'queued'
            else: e['jobs']['jobs'][0]['conclusion'] = kind
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.build(e, assessment=a)

    def test_rerun_unbound_result_cannot_supply_old_capture_or_target_clocks(self):
        e = sample(); e['run']['run_attempt'] = 2
        e['jobs']['jobs'][0].update(run_attempt=2, created_at='2026-10-20T15:41:57Z',
                                  started_at='2026-10-20T15:49:44Z', completed_at='2026-10-20T15:57:12Z')
        r = self.build(e)
        self.assertEqual(r['segments_seconds']['job_created_to_started'], 467)
        self.assertIsNone(r['clocks_utc']['capture_attempt_started_at_utc'])
        self.assertIsNone(r['clocks_utc']['remote_verified_at_utc'])
        self.assertEqual(r['targets'], [])
        self.assertIn('RESULT_ATTEMPT_UNPROVEN', r['issues'])

    def test_capture_after_job_completion_is_an_explicit_chronology_issue(self):
        e = sample(); e['jobs']['jobs'][0]['completed_at'] = '2026-10-19T15:49:45Z'
        r = self.build(e)
        self.assertIn('INVALID_ORDER:capture_attempt_to_job_completed', r['issues'])

    def test_success_receipt_rejects_execution_interval_contradiction(self):
        e = sample(); a = prior_receipt(e)
        e['jobs']['jobs'][0]['completed_at'] = '2026-10-19T15:49:45Z'
        with self.assertRaises(ValueError): self.build(e, assessment=a)

    def test_explicit_jobs_response_requires_total_count_for_uniqueness(self):
        e = sample(); e['jobs'].pop('total_count')
        with self.assertRaises(ValueError): self.build(e)

    def test_absent_jobs_response_is_unknown_not_a_complete_empty_page(self):
        e = sample(); e.pop('jobs')
        r = self.build(e)
        self.assertIsNone(r['job'])
        self.assertIn('JOBS_RESPONSE_UNAVAILABLE', r['issues'])

    def test_late_data_is_flagged_without_hiding_or_rewriting(self):
        e = sample(); e['result']['quotes'][0]['target_utc'] = '2026-10-19T15:19:25Z'
        r = self.build(e); t = r['targets'][0]
        self.assertFalse(t['observed_by_capture_cutoff'])
        self.assertFalse(t['remote_verified_by_quote_deadline'])
        self.assertEqual(t['observed_at_utc'], '2026-10-19T15:50:21+00:00')

    def test_empty_now_concurrency_snapshot_never_proves_historical_no_lock(self):
        r = self.build(concurrency_snapshot={'run_id': 123, 'run_attempt': 1,
                       'captured_at_utc': '2026-10-20T00:00:00Z',
                       'response': {'total_count': 0, 'concurrency_groups': []}})
        self.assertEqual(r['queue_cause'], 'UNKNOWN')
        self.assertFalse(r['concurrency']['historical_cause_proven'])
        self.assertEqual(r['concurrency']['captured_at_utc'], '2026-10-20T00:00:00+00:00')

    def test_wrong_snapshot_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build(concurrency_snapshot={'run_id': 999, 'run_attempt': 1,
                       'captured_at_utc': '2026-10-20T00:00:00Z', 'response': {}})

    def test_cli_writes_new_appendix_and_never_overwrites_input_or_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            e = Path(folder) / 'input.json'; out = Path(folder) / 'queue.json'
            e.write_text(json.dumps(sample()), encoding='utf-8'); before = e.read_bytes()
            self.assertEqual(self.q.main(['--evidence', str(e), '--output', str(out)]), 0)
            self.assertEqual(e.read_bytes(), before)
            first = out.read_bytes()
            with self.assertRaises(FileExistsError):
                self.q.main(['--evidence', str(e), '--output', str(out)])
            self.assertEqual(out.read_bytes(), first)
            with self.assertRaises(FileExistsError):
                self.q.main(['--evidence', str(e), '--output', str(e)])
            self.assertEqual(e.read_bytes(), before)
            self.assertEqual(json.loads(first)['input_sha256']['evidence'], __import__('hashlib').sha256(before).hexdigest())

    def test_archived_natural_run_reuses_saved_single_run_chain_without_overwriting(self):
        # Exact metadata-only projection of the already verified archived run.
        # It is embedded so repository tests need no private/local evidence files.
        e = {'run': {'id': 39024893378,
                 'run_attempt': 1,
                 'event': 'workflow_dispatch',
                 'path': '.github/workflows/quote_only_rescue.yml',
                 'head_branch': 'main',
                 'head_sha': '284db3e994d80ee092c9bc16ee84da8404172034',
                 'status': 'completed',
                 'conclusion': 'success',
                 'created_at': '2026-10-17T10:26:22Z',
                 'run_started_at': '2026-10-17T10:26:22Z',
                 'html_url': 'https://github.com/lzwislam-collab/doge-data-collector/actions/runs/39024893378'},
         'jobs': {'total_count': 2,
                  'jobs': [{'id': 117373836776,
                            'run_id': 39024893378,
                            'run_attempt': 1,
                            'name': 'rescue',
                            'status': 'completed',
                            'conclusion': 'success',
                            'created_at': '2026-10-17T10:26:23Z',
                            'started_at': '2026-10-17T10:26:30Z',
                            'completed_at': '2026-10-17T10:27:12Z',
                            'head_sha': '284db3e994d80ee092c9bc16ee84da8404172034'},
                           {'id': 117373837445,
                            'run_id': 39024893378,
                            'run_attempt': 1,
                            'name': 'verify',
                            'status': 'completed',
                            'conclusion': 'skipped',
                            'created_at': '2026-10-17T10:26:23Z',
                            'started_at': '2026-10-17T10:26:23Z',
                            'completed_at': '2026-10-17T10:26:23Z',
                            'head_sha': '284db3e994d80ee092c9bc16ee84da8404172034'}]},
         'result': {'status': 'verified_window_quote_rescue',
                    'scored_ids': ['DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1792141200000_24h',
                                   'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1792224000000_1h'],
                    'quotes': [{'forecast_id': 'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1792141200000_24h',
                                'quote_id': 'b333e25f04f9dc3f3f228162b101bb1c01f4bb748f0e75f7c769982a4f9b8cb2',
                                'target_utc': '2026-10-17T10:23:07.606747+00:00',
                                'target_quote_utc': '2026-10-17T10:26:53.564000+00:00',
                                'observed_at_utc': '2026-10-17T10:26:54.045126+00:00'},
                               {'forecast_id': 'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1792224000000_1h',
                                'quote_id': 'b333e25f04f9dc3f3f228162b101bb1c01f4bb748f0e75f7c769982a4f9b8cb2',
                                'target_utc': '2026-10-17T10:20:43.552351+00:00',
                                'target_quote_utc': '2026-10-17T10:26:53.564000+00:00',
                                'observed_at_utc': '2026-10-17T10:26:54.045126+00:00'}],
                    'capture': {'status': 'ok',
                                'attempts': 1,
                                'events': [{'attempt': 1,
                                            'started_at_utc': '2026-10-17T10:26:53.858892+00:00',
                                            'exit_code': 0}]},
                    'publication': {'status': 'verified_remote_readback',
                                    'commit_sha': 'b758331c2a674ee32fc74a01c0ab5f0b5fc79d8c',
                                    'remote_head': 'b758331c2a674ee32fc74a01c0ab5f0b5fc79d8c',
                                    'new_predictions': False,
                                    'new_ids': [],
                                    'verified_files': 744,
                                    'verified_at_utc': '2026-10-17T10:27:08.010126+00:00',
                                    'registry_sha256': '8fcdf8a08716f526ee6abd96aaff27a6047e257f1a518dd345368898890eba70'},
                    'remote_readback_within_window': True,
                    'formal_prediction_issuance': False,
                    'real_orders_authorized': False,
                    'historical_backfill': False}}
        a = {'status': 'VERIFIED_NATURAL_WINDOW_RESCUE_NEW_CALLER',
         'run_id': 39024893378,
         'run_url': 'https://github.com/lzwislam-collab/doge-data-collector/actions/runs/39024893378',
         'publication': {'status': 'verified_remote_readback',
                         'commit_sha': 'b758331c2a674ee32fc74a01c0ab5f0b5fc79d8c',
                         'remote_head': 'b758331c2a674ee32fc74a01c0ab5f0b5fc79d8c',
                         'new_predictions': False,
                         'new_ids': [],
                         'verified_files': 744,
                         'verified_at_utc': '2026-10-17T10:27:08.010126+00:00',
                         'registry_sha256': '8fcdf8a08716f526ee6abd96aaff27a6047e257f1a518dd345368898890eba70'},
         'targets': [{'forecast_id': 'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1792141200000_24h',
                      'horizon_hours': 24,
                      'target_utc': '2026-10-17T10:23:07.606747+00:00',
                      'quote_utc': '2026-10-17T10:26:53.564000+00:00',
                      'remote_verified_at_utc': '2026-10-17T10:27:08.010126+00:00',
                      'seconds_target_to_remote': 240.403379,
                      'price': 0.08024,
                      'issue_target_binding_and_score_verified': True},
                     {'forecast_id': 'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1792224000000_1h',
                      'horizon_hours': 1,
                      'target_utc': '2026-10-17T10:20:43.552351+00:00',
                      'quote_utc': '2026-10-17T10:26:53.564000+00:00',
                      'remote_verified_at_utc': '2026-10-17T10:27:08.010126+00:00',
                      'seconds_target_to_remote': 384.457775,
                      'price': 0.08024,
                      'issue_target_binding_and_score_verified': True}],
         'raw_quote_sha256': 'b333e25f04f9dc3f3f228162b101bb1c01f4bb748f0e75f7c769982a4f9b8cb2',
         'old_rows_registry_snapshots_frozen_code_unchanged': True,
         'performance_retest_acceptance_not_implied': True}
        before = copy.deepcopy((e, a))
        r = self.q.build_queue_evidence(e, assessment=a)
        self.assertEqual(r['run']['id'], 39024893378)
        self.assertEqual(r['segments_seconds']['run_created_to_job_created'], 1)
        self.assertEqual(r['segments_seconds']['job_created_to_started'], 7)
        self.assertEqual(len(r['targets']), 2)
        self.assertEqual(r['prior_validation']['status'], 'ATTACHED_PRIOR_VERIFIED_RECEIPT')
        self.assertEqual((e, a), before)


if __name__ == '__main__':
    unittest.main()

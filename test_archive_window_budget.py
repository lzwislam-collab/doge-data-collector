from datetime import datetime, timezone
import importlib
import unittest


def row(fid, issued, target, horizon):
    return dict(forecast_id=fid, issued_at_utc=issued, target_utc=target, horizon_hours=str(horizon))


class BudgetTests(unittest.TestCase):
    def test_long_target_reserves_tail_from_actual_job_start(self):
        module = importlib.import_module('archive_window_budget')
        rows = [row('day', '2026-11-08T01:45:33+00:00',
                    '2026-11-09T01:45:33+00:00', 24)]
        # Recorded run 42433449972: job 01:43:36, capture step 01:47:04.
        # A 24H target previously kept all 2,400 seconds and starved the tail.
        result = module.plan(rows, datetime(2026, 11, 8, 1, 47, 4, tzinfo=timezone.utc),
                             2400, 1800,
                             workflow_started_at='2026-11-08T01:43:36+00:00',
                             workflow_timeout_seconds=3900)
        self.assertEqual(result['duration_seconds'], 1292)
        self.assertEqual(result['selected_forecast_id'], 'day')
        self.assertEqual(result['workflow_budget']['elapsed_seconds'], 208)
        self.assertTrue(result['workflow_budget']['soft_limit_only'])

    def test_observed_capture_overrun_still_leaves_the_archive_reserve(self):
        module = importlib.import_module('archive_window_budget')
        rows = [row('day', '2026-11-08T01:45:33+00:00',
                    '2026-11-09T01:45:33+00:00', 24)]
        result = module.plan(rows, datetime(2026, 11, 8, 1, 47, 4, tzinfo=timezone.utc),
                             2400, 1800,
                             workflow_started_at='2026-11-08T01:43:36+00:00',
                             workflow_timeout_seconds=3900)
        # The real capture step lasted 46m40s for a nominal 40m loop.
        # The recorded 400-second setup/cycle/final-publication excess must fit.
        projected_total = 208 + result['duration_seconds'] + 400 + 1800
        self.assertLessEqual(projected_total, 3900)

    def test_job_budget_does_not_extend_an_earlier_frozen_target_reserve(self):
        module = importlib.import_module('archive_window_budget')
        rows = [row('one', '2026-11-08T01:00:00+00:00',
                    '2026-11-08T02:00:00+00:00', 1)]
        result = module.plan(rows, datetime(2026, 11, 8, 1, 47, 4, tzinfo=timezone.utc),
                             2400, 1800,
                             workflow_started_at='2026-11-08T01:43:36+00:00',
                             workflow_timeout_seconds=3900)
        self.assertEqual(result['duration_seconds'], 1)
        self.assertEqual(result['selected_forecast_id'], 'one')

    def test_exhausted_job_budget_refuses_another_capture_loop(self):
        module = importlib.import_module('archive_window_budget')
        with self.assertRaisesRegex(TimeoutError, 'tail'):
            module.plan([], datetime(2026, 11, 8, 2, 15, tzinfo=timezone.utc),
                        2400, 1800,
                        workflow_started_at='2026-11-08T01:43:36+00:00',
                        workflow_timeout_seconds=3900)

    def test_missing_or_future_job_start_is_not_treated_as_zero_elapsed(self):
        module = importlib.import_module('archive_window_budget')
        for started in ('', '2026-11-08T02:00:00+00:00', '2026-11-08T01:43:36'):
            with self.subTest(started=started), self.assertRaises(ValueError):
                module.plan([], datetime(2026, 11, 8, 1, 47, 4, tzinfo=timezone.utc),
                            2400, 1800, workflow_started_at=started,
                            workflow_timeout_seconds=3900)

    def test_workflow_budget_requires_both_clock_and_timeout(self):
        module = importlib.import_module('archive_window_budget')
        now = datetime(2026, 11, 8, 1, 47, 4, tzinfo=timezone.utc)
        for options in ({'workflow_started_at': '2026-11-08T01:43:36+00:00'},
                        {'workflow_timeout_seconds': 3900}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                module.plan([], now, 2400, 1800, **options)

    def test_recorded_failure_reserves_tail_before_last_active_target(self):
        module = importlib.import_module('archive_window_budget')
        rows = [row('one', '2026-10-26T00:00:41.167026+00:00', '2026-10-26T01:00:41.167026+00:00', 1),
                row('excluded', '2026-10-26T01:01:45.704191+00:00', '2026-10-26T02:01:45.704191+00:00', 1),
                row('day', '2026-10-25T01:09:29.563822+00:00', '2026-10-26T01:09:29.563822+00:00', 24)]
        now = datetime(2026, 10, 26, 0, 48, 1, tzinfo=timezone.utc)
        result = module.plan(rows, now, 2400, 1800)
        self.assertEqual(result['duration_seconds'], 1)
        self.assertEqual(result['selected_forecast_id'], 'day')
        # Recorded pre-archive tail was approximately 20 minutes.
        self.assertLess(now.timestamp() + result['duration_seconds'] + 1200,
                        datetime.fromisoformat(rows[2]['target_utc']).timestamp())
        self.assertGreater(now.timestamp() + 2400 + 1200,
                           datetime.fromisoformat(rows[2]['target_utc']).timestamp())

    def test_no_active_reserved_interval_does_not_reselect_overlapping_original(self):
        module = importlib.import_module('archive_window_budget')
        rows = [row('one', '2026-10-26T00:00:41+00:00', '2026-10-26T01:00:41+00:00', 1),
                row('excluded', '2026-10-26T01:01:45+00:00', '2026-10-26T02:01:45+00:00', 1)]
        result = module.plan(rows, datetime(2026, 10, 26, 1, 43, tzinfo=timezone.utc), 2400, 1800)
        self.assertEqual(result['duration_seconds'], 2400)
        self.assertIsNone(result['selected_forecast_id'])

    def test_long_active_interval_keeps_existing_duration(self):
        module = importlib.import_module('archive_window_budget')
        result = module.plan([row('day', '2026-10-26T00:00:00+00:00', '2026-10-27T00:00:00+00:00', 24)],
                             datetime(2026, 10, 26, 1, tzinfo=timezone.utc), 2400, 1800)
        self.assertEqual(result['duration_seconds'], 2400)

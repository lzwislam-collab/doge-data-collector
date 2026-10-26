from datetime import datetime, timezone
import importlib
import unittest


def row(fid, issued, target, horizon):
    return dict(forecast_id=fid, issued_at_utc=issued, target_utc=target, horizon_hours=str(horizon))


class BudgetTests(unittest.TestCase):
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

"""Offline regression for the archive-window exhaustion in run 40248370100."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import archive_window_budget as budget


def row(fid, issued, target, horizon=1):
    return dict(forecast_id=fid, issued_at_utc=issued,
                target_utc=target, horizon_hours=str(horizon))


ROWS = [
    row('one', '2026-10-26T00:00:41.167026+00:00', '2026-10-26T01:00:41.167026+00:00'),
    row('excluded', '2026-10-26T01:01:45.704191+00:00', '2026-10-26T02:01:45.704191+00:00'),
    row('day', '2026-10-25T01:09:29.563822+00:00', '2026-10-26T01:09:29.563822+00:00', 24),
]


class ArchiveWindowBudgetTests(unittest.TestCase):
    def test_recorded_foreground_must_yield_before_archive_window_is_lost(self):
        now = datetime(2026, 10, 26, 0, 48, 1, tzinfo=timezone.utc)
        result = budget.plan(ROWS, now, 2400, 1800)
        self.assertEqual(result['duration_seconds'], 1)
        self.assertEqual(result['selected_forecast_id'], 'day')
        # Approximately 14m foreground tail + 6m upload in the recorded run.
        target = datetime.fromisoformat(ROWS[2]['target_utc'])
        self.assertLess(now + timedelta(seconds=result['duration_seconds'] + 1200), target)
        self.assertGreater(now + timedelta(seconds=2400 + 1200), target)
        self.assertFalse(result['formal_eligibility_claimed'])

    def test_expired_prefix_does_not_reselect_overlapping_excluded_forecast(self):
        result = budget.plan(ROWS, datetime(2026, 10, 26, 1, 43, tzinfo=timezone.utc), 2400, 1800)
        self.assertIsNone(result['selected_forecast_id'])
        self.assertEqual(result['duration_seconds'], 2400)

    def test_complete_prefix_must_be_reserved_before_time_filter(self):
        now = datetime(2026, 10, 26, 1, 43, tzinfo=timezone.utc)
        self.assertEqual([r['forecast_id'] for r in budget.engine.reserve(ROWS[:2])], ['one'])
        self.assertIsNone(budget.plan(ROWS[:2], now, 2400, 1800)['selected_forecast_id'])

    def test_horizons_are_reserved_independently(self):
        now = datetime(2026, 10, 26, 0, 48, 1, tzinfo=timezone.utc)
        self.assertEqual(budget.plan(ROWS, now, 2400, 1800)['selected_forecast_id'], 'day')

    def test_distant_target_does_not_extend_foreground_above_existing_maximum(self):
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        rows = [row('day', now.isoformat(), (now+timedelta(days=1)).isoformat(), 24)]
        self.assertEqual(budget.plan(rows, now, 2400, 1800)['duration_seconds'], 2400)

    def test_reserve_subtracted_at_exact_boundary(self):
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        rows = [row('one', now.isoformat(), (now+timedelta(seconds=1801)).isoformat())]
        self.assertEqual(budget.plan(rows, now, 2400, 1800)['duration_seconds'], 1)

    def test_target_exactly_now_is_no_longer_active(self):
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        rows = [row('one', (now-timedelta(hours=1)).isoformat(), now.isoformat())]
        self.assertIsNone(budget.plan(rows, now, 2400, 1800)['selected_forecast_id'])

    def test_future_issue_is_not_active(self):
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        rows = [row('future', (now+timedelta(minutes=1)).isoformat(), (now+timedelta(hours=1)).isoformat())]
        self.assertIsNone(budget.plan(rows, now, 2400, 1800)['selected_forecast_id'])

    def test_other_horizons_do_not_supply_a_replacement_capture_window(self):
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        rows = [row('week', now.isoformat(), (now+timedelta(days=7)).isoformat(), 168)]
        self.assertIsNone(budget.plan(rows, now, 2400, 1800)['selected_forecast_id'])

    def test_empty_prefix_is_not_fabricated_into_a_formal_source(self):
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        result = budget.plan([], now, 2400, 1800)
        self.assertIsNone(result['selected_target_utc'])
        self.assertFalse(result['formal_eligibility_claimed'])

    def test_invalid_foreground_bound_fails_closed(self):
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        for maximum in (0, -1, 2401, float('nan'), float('inf')):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                budget.plan(ROWS, now, maximum, 1800)

    def test_reserve_cannot_be_reduced_by_configuration(self):
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        for reserve in (0, -1, 1799, 1801):
            with self.subTest(reserve=reserve), self.assertRaises(ValueError):
                budget.plan(ROWS, now, 2400, reserve)

    def test_from_root_keeps_post_activation_history_before_reservation(self):
        now = datetime(2026, 10, 26, 1, 43, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with patch.object(budget.engine, 'guard', return_value={'effective_from_utc':'2026-10-13T00:00:00+00:00'}), patch.object(budget.engine.frozen, 'read_rows', return_value={r['forecast_id']:r for r in ROWS}):
                self.assertIsNone(budget.from_root(root, now, 2400, 1800)['selected_forecast_id'])


if __name__ == '__main__':
    unittest.main()

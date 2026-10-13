"""Regression tests for independent P2 path diagnostics; synthetic evidence is never live."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import json
import p2_path_quality as audit

T = datetime(2026, 10, 13, tzinfo=timezone.utc)


def event(minute, serial):
    qt = T + timedelta(minutes=minute)
    return {
        "quote_utc": qt.isoformat(),
        "captured_at_utc": (qt + timedelta(milliseconds=90)).isoformat(),
        "book_sha256": format(serial, "064x"),
    }


class PathQualityTests(unittest.TestCase):
    def test_three_realistic_intervals_are_retained(self):
        # Fill intervening genuine observations so only the three stated gaps remain.
        minutes = [2.6, 38.97]
        minutes.extend(range(58, 398, 20))
        minutes.extend([396.11, 427.83, 456.69, 480.36, 510.79, 536.85])
        minutes = sorted(set(minutes))
        rows = []
        for serial, minute in enumerate(minutes, 1):
            qt = T + timedelta(minutes=minute)
            rows.append({"quote_utc": qt.isoformat(),
                         "captured_at_utc": (qt + timedelta(milliseconds=100)).isoformat(),
                         "book_sha256": format(serial, "064x")})
        result = audit.evaluate(rows, T + timedelta(minutes=540))
        self.assertEqual(result["historical_gap_count"], 3)
        self.assertFalse(result["p2_economic_acceptance"])
        self.assertTrue(result["no_historical_reconstruction"])
        self.assertEqual(result["historical_gaps"][-1]["next_book_index"], len(rows) - 2)

    def test_closed_clean_observations_do_not_rewrite_before_gap(self):
        rows = [event(1, 1), event(41, 2), event(51, 3), event(61, 4)]
        result = audit.evaluate(rows, T + timedelta(minutes=63))
        self.assertEqual(result["historical_gap_count"], 1)
        self.assertEqual(result["recent_gap_free_seconds"], 20 * 60)
        self.assertEqual(result["last_gap_ends_at_utc"], event(41, 2)["quote_utc"])
        self.assertEqual(result["status"], "GAPS_PRESERVED_NEW_SEGMENT_OBSERVED")

    def test_stale_tail_is_not_accepted(self):
        result = audit.evaluate([event(1, 1), event(11, 2)],
                                T + timedelta(hours=1))
        self.assertEqual(result["status"], "WAIT_STALE_TAIL")
        self.assertFalse(result["latest_mark_fresh"])

    def test_unordered_duplicate_future_and_naive_rejected(self):
        with self.assertRaisesRegex(ValueError, "nonmonotonic"):
            audit.evaluate([event(10, 1), event(9, 2)], T + timedelta(minutes=30))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            audit.evaluate([event(1, 1), event(2, 1)], T + timedelta(minutes=30))
        with self.assertRaisesRegex(ValueError, "future"):
            audit.evaluate([event(31, 1)], T + timedelta(minutes=30))
        with self.assertRaisesRegex(ValueError, "naive"):
            audit.clock("2026-10-13T00:01:00")

    def test_no_source_is_wait_not_zero_missing(self):
        result = audit.evaluate([], T)
        self.assertEqual(result["status"], "WAIT_NO_SOURCE_BOOKS")
        self.assertIsNone(result["recent_gap_free_seconds"])

    def test_main_uses_genuine_guard_and_never_changes_ledger(self):
        rows = [event(1, 1), event(11, 2)]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            existing = root / "data/p2_paired_execution_v3_1/latest.json"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"original immutable report")
            with patch.object(audit.engine, "history_guard", return_value=rows):
                rc = audit.main(root, T + timedelta(minutes=13))
            self.assertEqual(rc, 0)
            result = json.loads((root / audit.OUTPUT).read_text())
            self.assertEqual(result["status"], "NO_OBSERVED_SOURCE_GAPS")
            self.assertEqual(existing.read_bytes(), b"original immutable report")


if __name__ == "__main__":
    unittest.main()

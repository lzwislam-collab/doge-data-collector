"""Protect the observed collector workload without adding scheduled work."""
import re
import unittest
from pathlib import Path

WORKFLOW=Path(__file__).resolve().parent/'.github/workflows/okx_hourly_extreme_history.yml'

class HourlyTimeoutRecovery(unittest.TestCase):
    def test_budget_covers_observed_real_run_and_bounded_cleanup_margin(self):
        text=WORKFLOW.read_text(encoding='utf8')
        job=text.split('  hourly-history:',1)[1].split('    steps:',1)[0]
        minutes=int(re.search(r'timeout-minutes:\s*(\d+)',job).group(1))
        # Run 39693555382 lasted 508s and GitHub annotated an 8m timeout.
        # Its source/persist succeeded; the 1.32GB retained evidence upload
        # exhausted the budget. Retain all evidence and give 120s margin.
        self.assertGreaterEqual(minutes*60,508+120)
        self.assertLessEqual(minutes,15)

    def test_existing_single_schedule_and_serialization_are_preserved(self):
        text=WORKFLOW.read_text(encoding='utf8')
        schedules=re.findall(r"cron:\s*'([^']+)'",text)
        self.assertEqual(schedules,['7,17,27,37,47,57 * * * *'])
        self.assertIn('cancel-in-progress: false',text)
        self.assertIn('group: okx-hourly-extreme-history',text)
        self.assertIn('run: python forward_validation_v2.py --integrity-only',text)
        self.assertIn('Preserve new-version evidence separately from frozen forecast artifacts',text)
        self.assertIn('data/plus_evidence_v1_0/',text)

if __name__=='__main__':unittest.main()

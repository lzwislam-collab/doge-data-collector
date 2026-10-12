#!/usr/bin/env python3
"""Regression guard: production evidence capture cannot be blocked by a test fixture.

The frozen protocol and quote-binding integrity check MUST execute first.
The scheduled source capture and prospective registration MUST happen before
non-production unit tests; tests still fail the workflow for independent alerting.
No additional cron or modification of frozen model implementations is needed.
"""
import unittest
from pathlib import Path

WORKFLOW=Path(__file__).resolve().parent/'.github/workflows/okx_hourly_extreme_history.yml'

class TestOperationalOrder(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.text=WORKFLOW.read_text(encoding='utf-8')
  cls.lines=cls.text.splitlines()

 def offset(self,fragment):
  found=[i for i,line in enumerate(self.lines) if fragment in line]
  self.assertEqual(len(found),1,'missing/duplicate operational step: '+fragment)
  return found[0]

 def test_frozen_integrity_check_precedes_all_quote_source_writes(self):
  a=self.offset('Fail closed before writes on full-history or frozen protocol mutation')
  b=self.offset('Score genuine due target quotes before long collection steps')
  c=self.offset('Backfill confirmed UTC 1H data with source-ingestion timestamps')
  d=self.offset('Issue and score DOGE UP/DOWN shadow forecasts from live ticker quote')
  self.assertLess(a,b)
  self.assertLess(a,c)
  self.assertLess(a,d)

 def test_time_sensitive_scoring_and_issuance_precede_fixture_tests(self):
  a=self.offset('Score genuine due target quotes before long collection steps')
  b=self.offset('Issue and score DOGE UP/DOWN shadow forecasts from live ticker quote')
  t=self.offset('- name: Test hourly point-in-time registry causal restrictions after time-sensitive evidence capture')
  self.assertLess(a,t)
  self.assertLess(b,t)

 def test_nonproduction_test_failure_still_fails_job_and_evidence_is_saved(self):
  t=self.offset('- name: Test hourly point-in-time registry causal restrictions after time-sensitive evidence capture')
  d=self.offset('- name: Persist data and diagnostics')
  e=self.offset('- name: Preserve per-run forecast and score evidence snapshot')
  self.assertLess(t,d)
  self.assertLess(d,e)
  test_step='\n'.join(self.lines[t:d])
  self.assertIn('run: python -m unittest -v',test_step)
  self.assertIn('continue-on-error: true',test_step)
  self.assertIn('if: always()', '\n'.join(self.lines[d:d+3]))
  self.assertIn('if: always()', '\n'.join(self.lines[e:e+3]))

 def test_single_quote_poll_cadence_reserves_at_least_15_minutes_for_delay(self):
  crons=[line for line in self.lines if 'cron:' in line]
  self.assertEqual(len(crons),1)
  minutes=sorted(int(x) for x in crons[0].split("'")[1].split()[0].split(','))
  gaps=[b-a for a,b in zip(minutes,minutes[1:])]+[60+minutes[0]-minutes[-1]]
  self.assertLessEqual(max(gaps),10,'25 minute target grace requires at least 15 minute delay reserve')
  self.assertIn('cancel-in-progress: false',self.text)
  self.assertIn('ref: main # read current data after the shared concurrency lock is acquired',self.text)

 def test_fast_candle_and_issuance_precede_long_backfill(self):
  quick=self.offset('Fast-refresh confirmed 1H bars before prediction issuance')
  issue=self.offset('Issue and score DOGE UP/DOWN shadow forecasts from live ticker quote')
  heavy=self.offset('Backfill confirmed UTC 1H data with source-ingestion timestamps')
  self.assertLess(quick,issue)
  self.assertLess(issue,heavy)
  self.assertIn('DOGE_HOURLY_PAGE_BUDGET=0 python okx_hourly_research_history.py',
                '\n'.join(self.lines[quick:issue]))
  self.assertIn("steps.fast_hourly.outcome != 'success'",self.text)

 def test_direction_attempt_not_suppressed_by_backfill_failure(self):
  # A historical backfill outage must not silently skip an independent forecast.
  a=self.offset('Issue and score DOGE UP/DOWN shadow forecasts from live ticker quote')
  b=self.offset('Verify benign late-registration skip without changing frozen deadline')
  direction='\n'.join(self.lines[a:b])
  self.assertIn('run: python quote_capture_retry.py direction',direction)
  self.assertNotIn("if: steps.collect.outcome == 'success'",direction)
  late='\n'.join(self.lines[b:self.offset('Backfill confirmed UTC 1H data with source-ingestion timestamps')])
  self.assertIn("if: steps.direction.outcome != 'success'",late)
  self.assertNotIn("steps.collect.outcome == 'success'",late)

 def test_v2_frozen_code_validation_preserved(self):
  self.assertIn('run: python forward_validation_v2.py --integrity-only',self.text)
  self.assertIn('run: python quote_capture_retry.py direction',self.text)
  self.assertIn('run: python forward_validation_v2.py',self.text)
  self.assertIn('test_forward_validation_v2',self.text)

if __name__=='__main__':
 unittest.main(verbosity=2)

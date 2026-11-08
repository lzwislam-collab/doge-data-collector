"""Qualification failure never emits a safe final output or publishes again."""
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import runtime_deadline_service as runtime
from test_runtime_deadline_service import Adapter, Clock, START


class QualificationFailureTests(unittest.TestCase):
    def test_zero_cycle_errors_cannot_hide_a_required_qualification_failure(self):
        clock = Clock()
        adapter = Adapter(clock)
        adapter.can_stop = lambda: False
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            stop = root / 'stop'
            stop.touch()
            result = runtime.run(root, adapter, 121, clock, clock.sleep,
                                 stop_file=stop, require_qualified_stop=True)
            self.assertEqual(result['errors'], 0)
            self.assertEqual(result['status'], 'DEGRADED')
            self.assertFalse(result['qualified_stop_satisfied'])
            self.assertTrue(result['fatal'])
            self.assertEqual(result['fatal_reason'], 'REQUIRED_QUALIFIED_STOP_NOT_SATISFIED')
            adapter.calls.clear()
            output = root / 'runtime.outputs'
            with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
                runtime.finish(root, adapter, result)
            self.assertEqual(adapter.calls, [])
            self.assertEqual(output.read_text(), 'fatal=true\nfatal=true\n')
            self.assertTrue(json.loads((root / runtime.BASE / 'latest.json').read_text())['fatal'])

    def test_finalization_rejects_missing_or_false_required_qualification(self):
        for value in [None, False]:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                adapter = Adapter(Clock())
                result = {'fatal': False, 'status': 'CAPTURE_LOOP_COMPLETED',
                          'qualified_stop_required': True}
                if value is not None:
                    result['qualified_stop_satisfied'] = value
                output = root / 'outputs'
                with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
                    result = runtime.finish(root, adapter, result)
                self.assertTrue(result['fatal'])
                self.assertEqual(result['status'], 'DEGRADED')
                self.assertEqual(adapter.calls, [])
                self.assertNotIn('fatal=false', output.read_text())

    def test_qualified_success_still_publishes_before_final_safe_output(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            adapter = Adapter(Clock())
            result = {'fatal': False, 'status': 'CAPTURE_LOOP_COMPLETED',
                      'qualified_stop_required': True, 'qualified_stop_satisfied': True}
            output = root / 'outputs'
            with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
                result = runtime.finish(root, adapter, result)
            self.assertEqual(adapter.calls, ['publish'])
            self.assertFalse(result['fatal'])
            self.assertEqual(output.read_text(), 'fatal=true\nfatal=false\n')

    def test_legacy_service_without_qualified_stop_keeps_final_publication(self):
        with tempfile.TemporaryDirectory() as name:
            adapter = Adapter(Clock())
            result = runtime.finish(Path(name), adapter, {'fatal': False, 'status': 'CAPTURE_LOOP_COMPLETED'})
            self.assertFalse(result['fatal'])
            self.assertEqual(adapter.calls, ['publish'])


class CaptureContextTests(unittest.TestCase):
    def capture(self, row=None, ready=True):
        import p2_paired_execution_v3_1 as engine
        with tempfile.TemporaryDirectory() as name, patch.dict(os.environ, {
                'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': runtime.REPO}):
            adapter = runtime.LiveAdapter(Path(name))
            # The exchange command and frozen-ledger readers are I/O boundaries;
            # runtime.action itself and its diagnostic/qualification logic run.
            with patch.object(adapter, 'python'), patch.object(adapter, 'source_ready', return_value=ready), \
                 patch.object(engine, 'read_events', return_value=[row] if row else []), \
                 patch.object(engine, 'event_books', return_value=iter([{'requested_at_utc': START.isoformat()}])):
                result = adapter.action('book')
            return result

    def test_empty_frozen_reservation_context_is_distinct_from_source_rejection(self):
        result = self.capture({'source_items': []})
        self.assertFalse(result['formal_eligibility'])
        self.assertIn('reserved_source_count', result)
        self.assertEqual(result['reserved_source_count'], 0)
        self.assertEqual(result['source_context_status'], 'NO_RESERVED_SOURCE_CONTEXT')

    def test_unqualified_reserved_source_is_not_promoted_by_diagnostics(self):
        result = self.capture({'source_items': [{'source_verified': False}]})
        self.assertFalse(result['formal_eligibility'])
        self.assertIn('reserved_source_count', result)
        self.assertEqual(result['reserved_source_count'], 1)
        self.assertEqual(result['source_context_status'], 'RESERVED_SOURCE_NOT_QUALIFIED')

    def test_qualified_formal_capture_has_explicit_source_context(self):
        result = self.capture({'quote_utc': START.isoformat(), 'captured_at_utc': START.isoformat(),
                               'source_items': [{'source_verified': True, 'paired_audit_verified': True,
                                                 'source_error': None,
                                                 'observed_public_utc': (START-timedelta(minutes=1)).isoformat(),
                                                 'target_utc': (START+timedelta(hours=1)).isoformat()}]})
        self.assertTrue(result['formal_eligibility'])
        self.assertIn('source_context_status', result)
        self.assertEqual(result['source_context_status'], 'QUALIFIED_FORMAL_BOOK')

    def test_audit_wait_cannot_be_confused_with_an_empty_formal_context(self):
        result = self.capture(ready=False)
        self.assertFalse(result['formal_eligibility'])
        self.assertIn('reserved_source_count', result)
        self.assertIsNone(result['reserved_source_count'])
        self.assertEqual(result['source_context_status'], 'AUDIT_NOT_READY')


if __name__ == '__main__':
    unittest.main()

"""Quality alerts use existing cadence; ordinary daily-window backlog is expected."""
import json
import unittest
from datetime import timedelta
import test_stage1_rolling_integrity as fixture
from test_stage1_rolling_integrity import NOW
import stage1_rolling_integrity as integrity


class QualityTests(unittest.TestCase):
    setUp = fixture.StageOneRollingAuditTests.setUp
    tearDown = fixture.StageOneRollingAuditTests.tearDown
    def write(self):
        fixture.StageOneRollingAuditTests.write(self)
        enriched = []
        for row in self.audited:
            row = dict(row)
            row.update(input_addresses_matched='0', output_addresses_matched='0',
                por_input_amount_doge='0', por_output_amount_doge='0',
                por_address_set_delta_doge='0', fee_doge='1')
            if row['balance_reconciled'] == 'false':
                for key in integrity.AMOUNT_FIELDS:
                    row[key] = ''
            enriched.append(row)
        fixture.save(self.root, integrity.UTXO, fixture.UT + list(integrity.AMOUNT_FIELDS)
                     + ['input_addresses_matched', 'output_addresses_matched'], enriched)
    def quality(self, now=NOW):
        return integrity.monitor_quality(self.root, now)

    def test_coverage_uses_current_source_and_separates_missing(self):
        q = self.quality()
        self.assertEqual(q['coverage_pct'], 50.0)
        self.assertEqual(q['backlog_events'], 1)
        self.assertEqual(q['missing_files'], [])
        self.assertEqual(q['missing_required_fields'], 0)
        self.assertFalse(q['alert_required'])

    def test_old_pending_alerts_after_30h(self):
        q = self.quality(NOW + timedelta(hours=31))
        self.assertTrue(q['alert_required'])
        self.assertIn('BACKLOG_OVER_30H', q['alerts'])
        self.assertIn('STALE_UTXO_OVER_30H', q['alerts'])

    def test_duplicate_retains_metrics_and_alerts(self):
        self.source.append(dict(self.source[0]))
        self.write()
        q = self.quality()
        self.assertEqual(q['duplicate_source_rows'], 1)
        self.assertEqual(q['source_unique_events'], 2)
        self.assertIn('INVALID_LOCAL_DATA', q['alerts'])

    def test_missing_file_is_not_zero_backlog(self):
        (self.root / integrity.UTXO).unlink()
        q = self.quality()
        self.assertIn(integrity.UTXO, q['missing_files'])
        self.assertTrue(q['alert_required'])
        self.assertIsNone(q['coverage_pct'])

    def test_unreconciled_and_economic_ambiguity_are_separate(self):
        self.audited[0].update(balance_reconciled='false', issues='FEE_MISMATCH',
            classification='BOTH_SIDES_MIXED_OR_SELF_RETURN')
        self.report['full_utxo_reconciled_events'] = 0
        self.write()
        q = self.quality()
        self.assertEqual(q['unreconciled_events'], 1)
        self.assertEqual(q['economic_ambiguity_events'], 1)
        self.assertEqual(q['pending_reconciliation_events'], 2)
        self.assertIn('UNRECONCILED_UTXO', q['alerts'])

    def test_missing_amount_fields_detected_on_real_schema(self):
        # Minimal legacy fixtures remain valid for integrity but not amount coverage.
        from test_stage1_rolling_integrity import save, UT
        self.write()
        self.audited[0]['por_input_amount_doge'] = ''
        save(self.root, integrity.UTXO, UT + ['por_input_amount_doge'], self.audited)
        q = self.quality()
        self.assertGreater(q['missing_required_fields'], 0)
        self.assertTrue(q['alert_required'])

    def test_failed_snapshot_keeps_backlog_visible(self):
        self.report['status'] = 'error'
        self.write()
        q = self.quality()
        self.assertEqual(q['backlog_events'], 1)
        self.assertTrue(q['alert_required'])

    def test_future_timestamp_rejected(self):
        self.whale['ended_at_utc'] = (NOW + timedelta(hours=1)).isoformat()
        self.write()
        self.assertTrue(self.quality()['alert_required'])

    def test_source_nonfinite_amount_alerts(self):
        self.source[0]['amount_doge'] = 'NaN'
        self.write()
        q = self.quality()
        self.assertEqual(q['invalid_amount_events'], 1)
        self.assertIn('INVALID_AMOUNTS', q['alerts'])

    def test_source_imprecision_alerts(self):
        self.source[0]['amount_doge'] = '1000000.000000001'
        self.write()
        self.assertIn('INVALID_AMOUNTS', self.quality()['alerts'])


if __name__ == '__main__':
    unittest.main()

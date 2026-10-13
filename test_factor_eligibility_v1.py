import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('factor_eligibility_v1'), 'eligibility gate missing')
        import factor_eligibility_v1 as gate
        self.gate = gate
        self.at = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
        self.policy = {'features': [{'id': 'OI', 'decision': 'RESEARCH', 'max_age_seconds': 1800}]}
        self.row = {'feature_id': 'OI', 'first_seen_utc': '2026-10-13T11:59:00+00:00',
                    'usable_from_utc': '2026-10-13T11:59:00+00:00', 'value': 100,
                    'raw_verified': True, 'public_pre_target_witness_verified': True,
                    'source_coverage_fraction': .95}

    def test_unknown_and_monitor_and_stopped_cannot_enter_research(self):
        for decision in ('MONITOR', 'STOP', 'ADOPT_GOVERNANCE'):
            self.policy['features'][0]['decision'] = decision
            self.assertFalse(self.gate.qualify(self.policy, self.row, self.at)['research_allowed'])
        self.row['feature_id'] = 'INVENTED'
        self.assertFalse(self.gate.qualify(self.policy, self.row, self.at)['research_allowed'])

    def test_receipt_and_public_witness_required_independently(self):
        self.assertTrue(self.gate.qualify(self.policy, self.row, self.at)['research_allowed'])
        for key in ('raw_verified', 'public_pre_target_witness_verified'):
            row = dict(self.row, **{key: False})
            self.assertFalse(self.gate.qualify(self.policy, row, self.at)['research_allowed'])
        self.assertFalse(self.gate.qualify(self.policy, self.row, self.at)['production_allowed'])

    def test_future_receipt_stale_missing_and_nan_fail_closed(self):
        for change in ({'first_seen_utc': '2026-10-13T12:01:00+00:00'},
                       {'usable_from_utc': '2026-10-13T12:01:00+00:00'},
                       {'first_seen_utc': '2026-10-13T10:00:00+00:00'},
                       {'value': None}, {'value': float('nan')},
                       {'source_coverage_fraction': .89}, {'raw_verified': 'true'}):
            self.assertFalse(self.gate.qualify(self.policy, dict(self.row, **change), self.at)['research_allowed'])

    def test_availability_cannot_precede_receipt(self):
        self.row['usable_from_utc'] = '2026-10-13T11:58:00+00:00'
        self.assertFalse(self.gate.qualify(self.policy, self.row, self.at)['research_allowed'])

    def test_indicative_funding_expires_at_settlement_even_if_receipt_is_fresh(self):
        self.policy['features'][0]['id'] = 'FUNDING'
        self.row.update(feature_id='FUNDING', funding_time_utc='2026-10-13T12:00:00+00:00',
                        rate_type='indicative_current_period')
        self.assertFalse(self.gate.qualify(self.policy, self.row, self.at)['research_allowed'])
        self.row['funding_time_utc'] = '2026-10-13T12:01:00+00:00'
        self.assertTrue(self.gate.qualify(self.policy, self.row, self.at)['research_allowed'])


class DerivativeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('derivatives_pit_v1'), 'raw derivative ledger missing')
        import derivatives_pit_v1 as d
        self.d = d
        self.seen = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
        self.requested = self.seen - timedelta(seconds=2)
        self.ms = int(self.seen.timestamp()*1000)

    def raw(self, kind='OI', **changes):
        row = {'instId': 'DOGE-USDT-SWAP', 'ts': str(self.ms-1000),
               'oi': '100', 'oiCcy': '1000', 'oiUsd': '85'} if kind == 'OI' else {
               'instId': 'DOGE-USDT-SWAP', 'ts': str(self.ms-1000), 'fundingRate': '-0.0001',
               'fundingTime': str(self.ms+4*3600000), 'nextFundingTime': str(self.ms+12*3600000),
               'method': 'current_period', 'settFundingRate': '0.0002'}
        row.update(changes)
        return json.dumps({'code': '0', 'data': [row]}).encode()

    def test_future_settlement_is_not_future_availability(self):
        row = self.d.parse('FUNDING', self.raw('FUNDING'), self.requested, self.seen)
        self.assertEqual(row['value'], -0.0001)
        self.assertEqual(row['usable_from_utc'], '2026-10-13T12:00:00+00:00')
        self.assertEqual(row['rate_type'], 'indicative_current_period')
        self.assertFalse(row['realized_funding_cost_verified'])

    def test_oi_units_not_renamed_or_price_changes_confused_with_contract_changes(self):
        row = self.d.parse('OI', self.raw(), self.requested, self.seen)
        self.assertEqual(row['contracts'], 100)
        self.assertEqual(row['coin'], 1000)
        self.assertEqual(row['usd'], 85)
        self.assertEqual(row['value'], 1000)

    def test_wrong_instrument_invalid_negative_oi_stale_future_clock_rejected(self):
        for changes in ({'instId': 'BTC-USDT-SWAP'}, {'oi': '-1'}, {'oiUsd': 'NaN'},
                        {'ts': str(self.ms+1)}, {'ts': str(self.ms-1800001)}):
            with self.assertRaises(ValueError):
                self.d.parse('OI', self.raw(**changes), self.requested, self.seen)
        with self.assertRaises(ValueError):
            self.d.parse('FUNDING', self.raw('FUNDING', fundingTime=str(self.ms-1)), self.requested, self.seen)

    def test_archive_replays_raw_and_preserves_earlier_captures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.d.append(root, 'OI', self.raw(), self.requested, self.seen)
            before = (root/self.d.LEDGER).read_bytes()
            self.d.append(root, 'OI', self.raw(), self.requested, self.seen)
            self.assertEqual((root/self.d.LEDGER).read_bytes(), before)
            self.d.append(root, 'OI', self.raw(oiCcy='1200', ts=str(self.ms+3599000)), self.requested+timedelta(hours=1), self.seen+timedelta(hours=1))
            self.assertTrue((root/self.d.LEDGER).read_bytes().startswith(before))
            rows = self.d.read(root)
            self.assertEqual([r['coin'] for r in rows], [1000, 1200])
            self.assertFalse(rows[0]['public_pre_target_witness_verified'])
            rawpath = root/rows[0]['raw_path']
            rawpath.write_bytes(b'{}')
            with self.assertRaises(ValueError): self.d.read(root)

    def test_changed_value_even_with_rehashed_chain_rejected_by_raw_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = self.d.append(root, 'OI', self.raw(), self.requested, self.seen)
            row['value'] = 999
            row.pop('record_sha256')
            row['record_sha256'] = self.d.digest(row)
            (root/self.d.LEDGER).write_text(self.d.canon(row)+'\n', encoding='utf-8')
            with self.assertRaises(ValueError): self.d.read(root)

    def test_failed_source_cannot_be_zero_or_full_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def fetch(kind):
                if kind == 'FUNDING': raise OSError('unavailable')
                return self.raw(), self.requested, self.seen
            report = self.d.collect(root, fetch=fetch)
            self.assertEqual(report['status'], 'PARTIAL')
            self.assertEqual(len(self.d.read(root)), 1)
            self.assertEqual(report['sources']['FUNDING']['status'], 'BLOCKED')
            self.assertNotIn('value', report['sources']['FUNDING'])

    def test_gap_and_duplicate_hours_do_not_create_coverage(self):
        rows = [{'kind': 'OI', 'first_seen_utc': self.seen.isoformat()}]*30
        self.assertEqual(self.d.coverage(rows, 'OI', self.seen+timedelta(hours=1)), 1/24)

    def test_rejected_schema_retains_raw_and_receipt_without_eligible_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = self.raw(oi='-1')
            report = self.d.collect(root, fetch=lambda kind: (raw, self.requested, self.seen))
            self.assertEqual(report['status'], 'BLOCKED')
            self.assertEqual(self.d.read(root), [])
            self.assertTrue((root/'data/derivatives_pit_v1/failures.jsonl').exists(), 'failed response receipt must be retained')
            failures = self.d.read_failures(root)
            self.assertEqual(len(failures), 2)
            self.assertEqual((root/failures[0]['raw_path']).read_bytes(), raw)
            self.assertNotIn('value', failures[0])


if __name__ == '__main__': unittest.main()

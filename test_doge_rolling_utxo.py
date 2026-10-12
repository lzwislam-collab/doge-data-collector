"""Offline regression tests for the existing rolling audit, never dispatch Actions."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
import doge_rolling_utxo as core


class RollingDrainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.csv'
        self.out = self.root / 'events.csv'
        self.latest = self.root / 'latest.json'
        self.por = self.root / 'por.csv'
        with self.por.open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['address', 'network'])
            w.writerows((f'address{i}', 'DOGE') for i in range(40134))
        self.ids = [f'{i:064x}' for i in range(1, 82)]
        self.write_source(self.ids)
        self.paths = patch.multiple(core, ROOT=self.root, SOURCE=self.source,
                                   OUT=self.out, LATEST=self.latest, POR=self.por)
        self.paths.start()
        self.original_fetch = core.fetch
        self.fetch = patch.object(core, 'fetch', side_effect=self.good_tx)
        self.mock = self.fetch.start()

    def tearDown(self):
        self.fetch.stop()
        self.paths.stop()
        self.tmp.cleanup()

    def write_source(self, ids):
        with self.source.open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['txid', 'block_height', 'amount_doge', 'headline',
                        'observed_at_utc', 'verification'])
            w.writerows((i, 10, '1000000', '', '2026-10-12T03:00:00Z',
                         'confirmed-depth>=6') for i in ids)

    def good_tx(self, txid, **kwargs):
        return {'transaction': {'txid': txid, 'block_height': 10, 'confirmations': 6,
            'inputs': [{'address': 'other', 'value': '3'}],
            'outputs': [{'address': 'other', 'value': '2'}],
            'inputs_value': '3', 'outputs_value': '2', 'fee': '1'}}

    def report(self):
        return json.loads(self.latest.read_text())

    def test_drains_more_than_old_40_and_rerun_is_idempotent(self):
        self.assertEqual(core.run(), 0)
        self.assertEqual(self.report()['audited_unique_events'], 81)
        self.assertEqual(self.report()['events_pending'], 0)
        self.mock.reset_mock()
        self.assertEqual(core.run(), 0)
        self.mock.assert_not_called()

    def test_one_failed_transaction_does_not_starve_later_events(self):
        def fetch(txid, **kwargs):
            if txid == self.ids[0]:
                raise TimeoutError('provider timeout')
            return self.good_tx(txid)
        self.mock.side_effect = fetch
        self.assertEqual(core.run(), 1)
        self.assertEqual(self.report()['audited_unique_events'], 80)
        self.assertEqual(self.report()['events_pending'], 1)
        self.assertTrue(self.out.exists())
        self.mock.side_effect = self.good_tx
        self.assertEqual(core.run(), 0)
        self.assertEqual(self.report()['events_pending'], 0)

    def test_unbalanced_row_is_pending_not_complete(self):
        self.write_source(self.ids[:1])
        def fetch(txid, **kwargs):
            obj = self.good_tx(txid)
            obj['transaction']['fee'] = '2'
            return obj
        self.mock.side_effect = fetch
        self.assertEqual(core.run(), 1)
        self.assertEqual(self.report()['events_pending'], 0)
        self.assertEqual(self.report()['events_pending_reconciliation'], 1)
        self.assertEqual(self.report()['status'], 'partial_backlog')
        self.assertIsNone(self.report()['observed_historical_por_address_set_delta_doge_covered_whale_events_only'])

    def test_wrong_api_txid_is_rejected_and_successes_preserved(self):
        def fetch(txid, **kwargs):
            obj = self.good_tx(txid)
            if txid == self.ids[0]:
                obj['transaction']['txid'] = self.ids[1]
            return obj
        self.mock.side_effect = fetch
        self.assertEqual(core.run(), 1)
        self.assertEqual(self.report()['audited_unique_events'], 80)

    def test_budget_stop_preserves_partial_output(self):
        with patch.object(core, 'MAX_RUN_SECONDS', 0):
            self.assertEqual(core.run(), 0)
        self.mock.assert_not_called()
        self.assertEqual(self.report()['events_pending'], 81)

    def test_real_provider_hash_field_is_accepted(self):
        self.write_source(self.ids[:1])
        def fetch(txid, **kwargs):
            obj = self.good_tx(txid)
            obj['transaction']['hash'] = obj['transaction'].pop('txid')
            return obj
        self.mock.side_effect = fetch
        self.assertEqual(core.run(), 0)
        self.assertEqual(self.report()['events_pending'], 0)

    def test_retry_cursor_prevents_cross_run_starvation(self):
        self.write_source(self.ids[:3])
        def fetch(txid, **kwargs):
            if txid == self.ids[0]:
                raise TimeoutError('persistent failure')
            return self.good_tx(txid)
        self.mock.side_effect = fetch
        with patch.object(core, 'MAX_PER_RUN', 1):
            self.assertEqual(core.run(), 1)
            self.assertEqual(core.run(), 0)
            self.assertEqual(self.report()['audited_unique_events'], 1)
            self.assertEqual(core.run(), 0)
            self.assertEqual(self.report()['audited_unique_events'], 2)
            self.assertEqual(core.run(), 1)

    def test_bad_source_preserves_csv_and_por_manifest(self):
        core.run()
        before = self.out.read_bytes()
        digest = self.report()['por_source_sha256']
        self.write_source([self.ids[0], self.ids[0]])
        self.assertEqual(core.run(), 1)
        self.assertEqual(before, self.out.read_bytes())
        self.assertEqual(self.report()['por_source_sha256'], digest)

    def test_request_deadline_does_not_start_late_network_call(self):
        with patch.object(core.time, 'monotonic', return_value=10), \
                patch.object(core.urllib.request, 'urlopen') as request:
            with self.assertRaises(TimeoutError):
                self.original_fetch(self.ids[0], deadline=9)
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()

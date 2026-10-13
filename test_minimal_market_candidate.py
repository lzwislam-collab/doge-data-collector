import copy
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('minimal_market_candidate'), 'isolated PIT candidate module missing')
        import minimal_market_candidate as m
        self.m = m
        self.at = datetime(2026, 10, 13, 2, tzinfo=timezone.utc)
        self.rows = []
        for symbol in ('DOGE-USDT', 'BTC-USDT'):
            for i in range(25):
                opened = self.at - timedelta(hours=25-i)
                self.rows.append(dict(symbol=symbol, open_utc_ms=str(int(opened.timestamp()*1000)),
                    close=str(100+i), volume_base=str(10+i),
                    first_ingested_utc=(opened+timedelta(hours=1)).isoformat(),
                    source='OKX history-candles 1H confirmed'))

    def test_causal_candidates_are_preparation_not_scored_predictions(self):
        x = self.m.prepare(self.rows, self.at)
        self.assertEqual(x['status'], 'PREPARED_NOT_ISSUED')
        self.assertEqual(x['directions']['DOGE_REVERSAL_1H'], 'DOWN')
        self.assertEqual(x['directions']['BTC_MOMENTUM_1H'], 'UP')
        self.assertFalse(x['real_orders_authorized'])
        self.assertIsNone(x['p_up'])

    def test_later_ingested_history_cannot_fill_old_forecast_features(self):
        self.rows[-1]['first_ingested_utc'] = (self.at+timedelta(seconds=1)).isoformat()
        self.assertEqual(self.m.prepare(self.rows, self.at)['status'], 'WAIT_SOURCE')

    def test_missing_hour_does_not_become_consecutive_return(self):
        del self.rows[10]
        self.assertEqual(self.m.prepare(self.rows, self.at)['status'], 'WAIT_SOURCE')

    def test_duplicate_bar_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.m.prepare(self.rows+[self.rows[0]], self.at)

    def test_future_closed_bar_never_enters_features(self):
        row = dict(self.rows[-1], open_utc_ms=str(int(self.at.timestamp()*1000)),
                   first_ingested_utc=(self.at+timedelta(hours=1)).isoformat())
        self.assertEqual(self.m.prepare(self.rows+[row], self.at), self.m.prepare(self.rows, self.at))

    def test_stale_window_abstains(self):
        self.assertEqual(self.m.prepare(self.rows, self.at+timedelta(hours=2))['status'], 'WAIT_SOURCE')

    def test_invalid_values_rejected(self):
        for key, value in [('close', 'nan'), ('volume_base', '-1'), ('open_utc_ms', '1')]:
            rows = copy.deepcopy(self.rows); rows[-1][key] = value
            with self.assertRaises(ValueError): self.m.prepare(rows, self.at)

    def test_missing_or_false_source_cannot_qualify(self):
        self.rows[-1]['source'] = 'unconfirmed'
        with self.assertRaisesRegex(ValueError, 'source'): self.m.prepare(self.rows, self.at)

    def test_confirmed_bar_cannot_be_ingested_before_close(self):
        self.rows[-1]['first_ingested_utc'] = '2000-01-01T00:00:00+00:00'
        with self.assertRaisesRegex(ValueError, 'close'): self.m.prepare(self.rows, self.at)

    def test_preparation_does_not_alias_caller_rows(self):
        x = self.m.prepare(self.rows, self.at)
        before = copy.deepcopy(x)
        self.rows[-1]['close'] = '42'
        self.assertEqual(x, before)

    def test_extreme_finite_prices_do_not_create_nonfinite_features(self):
        self.rows[-2]['close'] = '1e-308'; self.rows[-1]['close'] = '1e308'
        x = self.m.prepare(self.rows, self.at)
        import math
        self.assertTrue(all(v is None or math.isfinite(v) for f in x['features'].values() for v in f.values()))

    def test_feature_availability_requires_first_seen_and_ordered_request(self):
        r = dict(source_id='X', observation_key='d', value=1, source_url='https://example.test',
            raw_sha256='a'*64, raw_path='raw/a', requested_at_utc=self.at.isoformat(),
            first_seen_utc=self.at.isoformat(), usable_from_utc=self.at.isoformat())
        self.assertTrue(self.m.available(r, self.at, 3600))
        r['first_seen_utc'] = (self.at+timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError, 'availability'): self.m.available(r, self.at, 3600)

    def test_missing_provenance_and_staleness_fail_closed(self):
        with self.assertRaises(ValueError): self.m.available({'usable_from_utc':self.at.isoformat()}, self.at, 3600)
        r = dict(source_id='X', observation_key='d', value=1, source_url='https://example.test',
            raw_sha256='a'*64, raw_path='raw/a', requested_at_utc=self.at.isoformat(),
            first_seen_utc=self.at.isoformat(), usable_from_utc=self.at.isoformat())
        self.assertFalse(self.m.available(r, self.at+timedelta(hours=2), 3600))

    def test_purge_removes_labels_touching_next_partition(self):
        rows = [dict(anchor=self.at+timedelta(hours=i), label_end=self.at+timedelta(hours=i+2)) for i in range(10)]
        split = self.m.purged_split(rows, self.at+timedelta(hours=5), self.at+timedelta(hours=8), 3600)
        self.assertEqual([r['anchor'] for r in split['train']], [self.at, self.at+timedelta(hours=1)])
        self.assertEqual(split['calibration'], [])
        self.assertEqual(len(split['validation']), 2)

if __name__ == '__main__': unittest.main()

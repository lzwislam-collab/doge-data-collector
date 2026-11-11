"""Auxiliary refresh preserves captured availability and repairs a rolled-off ETF gap.

All collector calls here use an in-memory ROOT, CSV boundary and HTTP fixture.
No production files, live requests, temporary directories or Git are created.
"""
import copy
import csv
import io
import json
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import macro_collector as macro
import etf_flows as etf


START = '2026-11-11T12:00:00+00:00'
BTC_READ = '2026-11-11T12:00:05+00:00'
BTC_ALL_READ = '2026-11-11T12:00:10+00:00'
ETH_READ = '2026-11-11T12:00:15+00:00'
ETH_ALL_READ = '2026-11-11T12:00:20+00:00'
OLD = '2026-10-27T06:05:04+00:00'
BTC_URL = 'https://farside.co.uk/btc/'
ETH_URL = 'https://farside.co.uk/eth/'
BTC_ALL = 'https://farside.co.uk/bitcoin-etf-flow-all-data/'
ETH_ALL = 'https://farside.co.uk/ethereum-etf-flow-all-data/'


class MemoryRoot:
    def mkdir(self, **kwargs):
        pass

    def __truediv__(self, name):
        return Path(name)


def csv_rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def html(rows):
    return ('<table><tr><th>Date</th><th>Fund</th><th>Total</th></tr>' +
            ''.join('<tr>' + ''.join('<td>' + str(cell) + '</td>' for cell in row) +
                    '</tr>' for row in rows) + '</table>').encode()


def etf_row(asset, day, total, revision_count='0'):
    return {'asset': asset, 'trade_date_us': day, 'total_usd_m': total,
            'reported_funds': '1', 'funds_with_values': '1',
            'source_url': BTC_URL if asset == 'BTC' else ETH_URL,
            'first_seen_utc': OLD, 'last_checked_utc': OLD,
            'revision_count': revision_count}


def exercise(module, responses, prior, revisions, series=None):
    """Replace only external IO and clock boundaries; run the real merge."""
    clock = {'utc': START}
    saved = {}

    class Response:
        def __init__(self, body, received):
            self.body = body
            self.received = received

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            clock['utc'] = self.received
            return self.body[:size]

    def get(request, **kwargs):
        value = responses[request.full_url]
        if isinstance(value, Exception):
            raise value
        return Response(*value)

    def load(path, fields):
        return copy.deepcopy(revisions if path.name == 'revisions.csv' else prior)

    def save(path, content):
        saved[path.name] = content

    with ExitStack() as stack:
        stack.enter_context(patch.object(module, 'ROOT', MemoryRoot()))
        stack.enter_context(patch.object(module, 'load' if module is macro else 'load_csv', load))
        stack.enter_context(patch.object(module, 'save_atomic' if module is macro else 'atomic', save))
        stack.enter_context(patch.object(module, 'now' if module is macro else 'utc', lambda: clock['utc']))
        stack.enter_context(patch.object(module.urllib.request, 'urlopen', get))
        if series is not None:
            stack.enter_context(patch.object(module, 'SERIES', series))
        with redirect_stdout(io.StringIO()):
            result = module.main()
    return result, saved


class MacroAvailabilityTests(unittest.TestCase):
    @staticmethod
    def fred(sid):
        return ('observation_date,' + sid + '\n' +
                ''.join(f'2025-{month:02d}-01,{100 + month}\n' for month in range(1, 13)) +
                '2026-01-01,113\n').encode()

    def test_new_and_revised_values_become_known_after_their_http_response(self):
        # A pre-fetch run stamp would backdate both a new row and this revision.
        prior = [{'series_id': 'CPIAUCSL', 'observation_date': '2025-01-01',
                  'value': '99', 'unit': 'index_1982_84_100', 'frequency': 'monthly',
                  'source_url': 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL',
                  'first_seen_utc': OLD, 'last_checked_utc': OLD, 'revision_count': '1'}]
        previous_revision = {'series_id': 'CPIAUCSL', 'observation_date': '2025-01-01',
                             'old_value': '98', 'new_value': '99', 'observed_at_utc': OLD,
                             'source_url': prior[0]['source_url']}
        url = prior[0]['source_url']
        result, saved = exercise(macro, {url: (self.fred('CPIAUCSL'), BTC_READ)},
                                 prior, [previous_revision],
                                 {'CPIAUCSL': macro.SERIES['CPIAUCSL']})
        self.assertEqual(result, 0)
        rows = {row['observation_date']: row for row in csv_rows(saved['observations.csv'])}
        self.assertEqual(rows['2026-01-01']['first_seen_utc'], BTC_READ)
        self.assertEqual(rows['2026-01-01']['last_checked_utc'], BTC_READ)
        self.assertEqual(rows['2025-01-01']['first_seen_utc'], OLD)
        self.assertEqual(rows['2025-01-01']['last_checked_utc'], BTC_READ)
        self.assertEqual(rows['2025-01-01']['value'], '101')
        self.assertEqual(rows['2025-01-01']['revision_count'], '2')
        revised = csv_rows(saved['revisions.csv'])
        self.assertEqual(revised[0], previous_revision)
        self.assertEqual(revised[1]['observed_at_utc'], BTC_READ)

    def test_each_series_uses_its_own_successful_response_time(self):
        responses = {}
        for sid, received in [('CPIAUCSL', BTC_READ), ('CPILFESL', ETH_READ)]:
            responses['https://fred.stlouisfed.org/graph/fredgraph.csv?id=' + sid] = (self.fred(sid), received)
        result, saved = exercise(macro, responses, [], [],
                                 {sid: macro.SERIES[sid] for sid in ('CPIAUCSL', 'CPILFESL')})
        self.assertEqual(result, 0)
        rows = csv_rows(saved['observations.csv'])
        self.assertEqual({row['first_seen_utc'] for row in rows if row['series_id'] == 'CPIAUCSL'}, {BTC_READ})
        self.assertEqual({row['first_seen_utc'] for row in rows if row['series_id'] == 'CPILFESL'}, {ETH_READ})

    def test_later_series_failure_does_not_publish_partial_history(self):
        responses = {'https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL':
                     (self.fred('CPIAUCSL'), BTC_READ),
                     'https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPILFESL':
                     OSError('source unavailable')}
        result, saved = exercise(macro, responses, [], [],
                                 {sid: macro.SERIES[sid] for sid in ('CPIAUCSL', 'CPILFESL')})
        self.assertEqual(result, 1)
        self.assertNotIn('observations.csv', saved)
        self.assertNotIn('revisions.csv', saved)
        self.assertEqual(json.loads(saved['latest.json'])['status'], 'error')


class EtfAvailabilityTests(unittest.TestCase):
    def test_receipt_time_preserves_first_seen_and_appends_a_revision(self):
        # A corrected old total must not be assigned the collector start time.
        prior = [etf_row('BTC', '2026-10-23', '-140.0', '1'),
                 etf_row('ETH', '2026-10-23', '4.4')]
        previous = {'asset': 'BTC', 'trade_date_us': '2026-10-23',
                    'old_total_usd_m': '-145.0', 'new_total_usd_m': '-140.0',
                    'observed_at_utc': OLD, 'source_url': BTC_URL}
        responses = {BTC_URL: (html([['23 Oct 2026', '-137.9', '-137.9'],
                                     ['26 Oct 2026', '110.3', '110.3'],
                                     ['11 Nov 2026', '-', '0.0']]), BTC_READ),
                     ETH_URL: (html([['23 Oct 2026', '4.4', '4.4'],
                                     ['26 Oct 2026', '-88.6', '-88.6'],
                                     ['11 Nov 2026', '-', '0.0']]), ETH_READ)}
        result, saved = exercise(etf, responses, prior, [previous])
        self.assertEqual(result, 0)
        rows = {(r['asset'], r['trade_date_us']): r for r in csv_rows(saved['daily_flows.csv'])}
        self.assertEqual(rows['BTC', '2026-10-26']['first_seen_utc'], BTC_READ)
        self.assertEqual(rows['ETH', '2026-10-26']['first_seen_utc'], ETH_READ)
        self.assertEqual(rows['BTC', '2026-10-23']['first_seen_utc'], OLD)
        self.assertEqual(rows['BTC', '2026-10-23']['last_checked_utc'], BTC_READ)
        self.assertEqual(rows['BTC', '2026-10-23']['revision_count'], '2')
        revisions = csv_rows(saved['revisions.csv'])
        self.assertEqual(revisions[0], previous)
        self.assertEqual(revisions[1]['observed_at_utc'], BTC_READ)
        self.assertNotIn(('BTC', '2026-11-11'), rows)
        self.assertNotIn(('ETH', '2026-11-11'), rows)

    @staticmethod
    def gap_responses():
        return {BTC_URL: (html([['23 Oct 2026', '-137.9', '-137.9'],
                                ['26 Oct 2026', '110.3', '110.3']]), BTC_READ),
                ETH_URL: (html([['23 Oct 2026', '4.4', '4.4'],
                                ['26 Oct 2026', '-88.6', '-88.6']]), ETH_READ),
                BTC_ALL: (html([['11 Jan 2024', '655.3', '655.3'],
                                ['14 Oct 2026', '-261.4', '-261.4'],
                                ['15 Oct 2026', '5.1', '5.1'],
                                ['16 Oct 2026', '-82.4', '-82.4'],
                                ['19 Oct 2026', '526.7', '526.7'],
                                ['20 Oct 2026', '23.2', '23.2'],
                                ['21 Oct 2026', '524.8', '524.8'],
                                ['22 Oct 2026', '-635.6', '-635.6'],
                                ['23 Oct 2026', '-137.9', '-137.9'],
                                ['26 Oct 2026', '110.3', '110.3']]), BTC_ALL_READ),
                ETH_ALL: (html([['23 Jul 2024', '106.6', '106.6'],
                                ['14 Oct 2026', '-129.5', '-129.5'],
                                ['15 Oct 2026', '-60.3', '-60.3'],
                                ['16 Oct 2026', '-69.7', '-69.7'],
                                ['19 Oct 2026', '-103.3', '-103.3'],
                                ['20 Oct 2026', '-26.1', '-26.1'],
                                ['21 Oct 2026', '14.9', '14.9'],
                                ['22 Oct 2026', '-110.6', '-110.6'],
                                ['23 Oct 2026', '4.4', '4.4'],
                                ['26 Oct 2026', '-88.6', '-88.6']]), ETH_ALL_READ)}

    def test_rolled_off_gap_is_recovered_from_the_actual_full_page(self):
        prior = [etf_row('BTC', '2026-10-14', '-261.4'),
                 etf_row('ETH', '2026-10-14', '-129.5')]
        result, saved = exercise(etf, self.gap_responses(), prior, [])
        self.assertEqual(result, 0)
        rows = {(r['asset'], r['trade_date_us']): r for r in csv_rows(saved['daily_flows.csv'])}
        self.assertEqual(len(rows), 18)  # 2 existing + 2 * (6 gap dates + 2 rolling dates).
        for asset, received, source in [('BTC', BTC_ALL_READ, BTC_ALL), ('ETH', ETH_ALL_READ, ETH_ALL)]:
            self.assertIn((asset, '2026-10-15'), rows)
            self.assertEqual(rows[asset, '2026-10-15']['first_seen_utc'], received)
            self.assertEqual(rows[asset, '2026-10-15']['last_checked_utc'], received)
            self.assertEqual(rows[asset, '2026-10-15']['source_url'], source)
            self.assertEqual(rows[asset, '2026-10-14']['first_seen_utc'], OLD)
        self.assertEqual(rows['BTC', '2026-10-23']['first_seen_utc'], BTC_READ)
        self.assertEqual(rows['ETH', '2026-10-23']['first_seen_utc'], ETH_READ)
        self.assertNotIn(('BTC', '2024-01-11'), rows)
        self.assertNotIn(('ETH', '2024-07-23'), rows)

    def test_disagreeing_full_page_cannot_publish_any_history(self):
        responses = self.gap_responses()
        responses[BTC_ALL] = (html([['14 Oct 2026', '-261.4', '-261.4'],
                                    ['15 Oct 2026', '5.1', '5.1'],
                                    ['23 Oct 2026', '999.0', '999.0'],
                                    ['26 Oct 2026', '110.3', '110.3']]), BTC_ALL_READ)
        result, saved = exercise(etf, responses,
                                 [etf_row('BTC', '2026-10-14', '-261.4'),
                                  etf_row('ETH', '2026-10-14', '-129.5')], [])
        self.assertEqual(result, 1)
        self.assertNotIn('daily_flows.csv', saved)
        self.assertNotIn('revisions.csv', saved)

    def test_full_page_without_the_saved_anchor_cannot_claim_continuity(self):
        responses = self.gap_responses()
        responses[BTC_ALL] = (html([['15 Oct 2026', '5.1', '5.1'],
                                    ['23 Oct 2026', '-137.9', '-137.9'],
                                    ['26 Oct 2026', '110.3', '110.3']]), BTC_ALL_READ)
        result, saved = exercise(etf, responses,
                                 [etf_row('BTC', '2026-10-14', '-261.4'),
                                  etf_row('ETH', '2026-10-14', '-129.5')], [])
        self.assertEqual(result, 1)
        self.assertNotIn('daily_flows.csv', saved)

    def test_second_asset_failure_does_not_publish_the_first_asset(self):
        result, saved = exercise(etf,
                                 {BTC_URL: (html([['23 Oct 2026', '1.0', '1.0']]), BTC_READ),
                                  ETH_URL: OSError('source unavailable')}, [], [])
        self.assertEqual(result, 1)
        self.assertNotIn('daily_flows.csv', saved)
        self.assertNotIn('revisions.csv', saved)
        self.assertEqual(json.loads(saved['latest.json'])['status'], 'error')


if __name__ == '__main__':
    unittest.main(verbosity=2)


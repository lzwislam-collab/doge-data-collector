"""Confirmed-block source identity, durable resume, and honest run-end backlog."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import doge_whale_blocks as scanner


@contextmanager
def fixture_directory():
    keep = os.environ.get('DOGE_TEST_FIXTURE_ROOT')
    if keep:
        root = Path(keep)
        root.mkdir(parents=True, exist_ok=True)
        yield Path(tempfile.mkdtemp(prefix='whale-', dir=root))
    else:
        with tempfile.TemporaryDirectory() as directory:
            yield Path(directory)


def block(height=11, txids=None):
    return {'height': height, 'hash': format(height, '064x'),
            'previous_block_hash': format(height - 1, '064x') if height else None,
            'time': 1794398000 + height, 'confirmations': 8, 'is_orphan': False,
            'num_txs': len(txids or ['a' * 64]), 'txs': txids or ['a' * 64]}


def transaction(txid, parent=None, amount='2000000'):
    parent = parent or block()
    return {'transaction': {'hash': txid, 'block_height': parent['height'],
            'block_hash': parent['hash'], 'time': parent['time'], 'confirmations': 8,
            'inputs': [{'index': 0}], 'inputs_n': 1,
            'outputs': [{'index': 0}], 'outputs_n': 1},
            'explain': {'sent': amount, 'headline': 'current provider headline'}}


class Clock:
    def __init__(self):
        self.now = 10000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Provider:
    def __init__(self, txids=None, tips=None):
        self.parent = block(txids=txids)
        self.tips = iter(tips or [17, 17])
        self.calls = []
        self.tx_overrides = {}

    def __call__(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path == '/network':
            tick = next(self.tips)
            if isinstance(tick, Exception):
                raise tick
            return {'info': {'block_count': tick}}
        if path.startswith('/block/'):
            height = int(path.rsplit('/', 1)[1])
            return self.parent if height == 11 else block(height)
        txid = path.rsplit('/', 1)[1]
        value = self.tx_overrides.get(txid, transaction(txid, self.parent))
        if isinstance(value, Exception):
            raise value
        return value


class ConfirmedIdentityTests(unittest.TestCase):
    def test_block_requires_real_identity_complete_list_and_confirmations(self):
        cases = [('height', None), ('height', 12), ('hash', 'bad'),
                 ('previous_block_hash', 'bad'), ('num_txs', 2),
                 ('num_txs', True), ('is_orphan', True), ('confirmations', 5),
                 ('time', None)]
        for key, value in cases:
            with self.subTest(field=key, value=value):
                data = block()
                data[key] = value
                with patch.object(scanner, 'request', return_value=data):
                    with self.assertRaises(ValueError):
                        scanner.parse_block(11)

    def test_transaction_must_belong_to_this_exact_confirmed_block(self):
        cases = [('hash', 'b' * 64), ('block_height', 12),
                 ('block_hash', 'c' * 64), ('confirmations', 5),
                 ('time', 1), ('inputs_n', 2), ('outputs_n', 2)]
        for key, value in cases:
            with self.subTest(field=key):
                data = transaction('a' * 64)
                data['transaction'][key] = value
                with patch.object(scanner, 'request', return_value=data):
                    with self.assertRaises(ValueError):
                        scanner.explain('a' * 64, block())

    def test_valid_transaction_preserves_provider_decimal_metric(self):
        with patch.object(scanner, 'request',
                          return_value=transaction('a' * 64, amount='988515.05640000')):
            amount, _ = scanner.explain('a' * 64, block())
        self.assertEqual(amount, Decimal('988515.05640000'))


class EndTipAndResumeTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture_directory()
        self.root = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.checkpoint = {'schema_version': 1, 'last_completed_height': 10}
        (self.root / 'checkpoint.json').write_text(json.dumps(self.checkpoint))
        self.addCleanup(patch.stopall)
        patch.object(scanner, 'OUT', self.root).start()
        patch.object(scanner, 'MAX_BLOCKS', 1).start()

    def summary(self):
        return json.loads((self.root / 'latest.json').read_text(encoding='utf-8'))

    def test_backlog_uses_fresh_end_tip_even_when_initial_target_was_completed(self):
        provider = Provider(tips=[17, 18])
        with patch.object(scanner, 'request', side_effect=provider):
            self.assertEqual(scanner.main(), 0)
        result = self.summary()
        self.assertEqual(result['confirmed_tip_at_run'], 12)
        self.assertEqual(result['backlog_blocks_at_run_end'], 1)
        self.assertTrue(result['run_end_tip_verified'])
        self.assertEqual(provider.calls[-1][0], '/network')

    def test_end_tip_failure_cannot_reuse_old_zero_backlog(self):
        provider = Provider(tips=[17, TimeoutError('end tip unavailable')])
        with patch.object(scanner, 'request', side_effect=provider):
            self.assertEqual(scanner.main(), 1)
        result = self.summary()
        self.assertIsNone(result['backlog_blocks_at_run_end'])
        self.assertFalse(result['run_end_tip_verified'])
        self.assertEqual(result['status'], 'error')

    def test_missing_checkpoint_cannot_seed_a_near_tip_replacement(self):
        (self.root / 'checkpoint.json').unlink()
        provider = Provider()
        with patch.object(scanner, 'request', side_effect=provider):
            self.assertEqual(scanner.main(), 1)
        self.assertFalse((self.root / 'checkpoint.json').exists())
        self.assertFalse(any(path.startswith('/block/') for path, _ in provider.calls))

    def test_previous_hash_mismatch_preserves_checkpoint(self):
        provider = Provider()
        provider.parent['previous_block_hash'] = 'f' * 64
        with patch.object(scanner, 'request', side_effect=provider):
            self.assertEqual(scanner.main(), 1)
        self.assertEqual(json.loads((self.root / 'checkpoint.json').read_text()), self.checkpoint)

    def test_legacy_pending_is_identity_checked_without_rewriting_first_seen_or_value(self):
        a, b = 'a' * 64, 'b' * 64
        old = {'amount_doge': '2000000.00000000', 'headline': 'original wording',
               'observed_at_utc': '2026-10-15T10:31:36+00:00'}
        pending = {'schema_version': 1, 'height': 11, 'txids': [a, b],
                   'verified': {a: dict(old)}}
        (self.root / 'pending_block.json').write_text(json.dumps(pending))
        provider = Provider(txids=[a, b])
        provider.tx_overrides[b] = TimeoutError('interrupted')
        with patch.object(scanner, 'request', side_effect=provider):
            self.assertEqual(scanner.main(), 1)
        saved = json.loads((self.root / 'pending_block.json').read_text())
        for key, value in old.items():
            self.assertEqual(saved['verified'][a][key], value)
        self.assertIn('identity', saved['verified'][a])
        self.assertIn('/tx/' + a, [path for path, _ in provider.calls])
        self.assertEqual(json.loads((self.root / 'checkpoint.json').read_text()), self.checkpoint)
        resumed = Provider(txids=[a, b])
        with patch.object(scanner, 'request', side_effect=resumed):
            self.assertEqual(scanner.main(), 0)
        self.assertNotIn('/tx/' + a, [path for path, _ in resumed.calls])
        stored = scanner.read_events()[a]
        self.assertEqual(stored['observed_at_utc'], old['observed_at_utc'])
        self.assertEqual(stored['headline'], old['headline'])
        self.assertEqual(Decimal(stored['amount_doge']), Decimal(old['amount_doge']))

    def test_legacy_pending_amount_conflict_does_not_replace_old_observation(self):
        a = 'a' * 64
        pending = {'schema_version': 1, 'height': 11, 'txids': [a],
                   'verified': {a: {'amount_doge': '3000000', 'headline': 'old',
                                   'observed_at_utc': '2026-10-15T10:31:36+00:00'}}}
        original = json.dumps(pending).encode()
        (self.root / 'pending_block.json').write_bytes(original)
        with patch.object(scanner, 'request', side_effect=Provider()):
            self.assertEqual(scanner.main(), 1)
        self.assertEqual((self.root / 'pending_block.json').read_bytes(), original)
        self.assertEqual(json.loads((self.root / 'checkpoint.json').read_text()), self.checkpoint)

    def test_changed_pending_list_fails_closed_without_discarding_old_cache(self):
        a, b = 'a' * 64, 'b' * 64
        pending = {'schema_version': 1, 'height': 11, 'txids': [b],
                   'verified': {b: {'amount_doge': '1', 'headline': 'old',
                                   'observed_at_utc': '2026-10-15T10:31:36+00:00'}}}
        original = json.dumps(pending).encode()
        (self.root / 'pending_block.json').write_bytes(original)
        with patch.object(scanner, 'request', side_effect=Provider(txids=[a])):
            self.assertEqual(scanner.main(), 1)
        self.assertEqual((self.root / 'pending_block.json').read_bytes(), original)

    def test_checkpoint_write_interruption_recovers_without_replacing_first_seen(self):
        provider = Provider()
        real_atomic = scanner.atomic

        def interrupted(path, text):
            if path.name == 'checkpoint.json':
                raise OSError('checkpoint write interrupted')
            return real_atomic(path, text)

        with patch.object(scanner, 'request', side_effect=provider), \
             patch.object(scanner, 'atomic', side_effect=interrupted):
            self.assertEqual(scanner.main(), 1)
        self.assertEqual(self.summary()['last_completed_height'], 10)
        self.assertEqual(json.loads((self.root / 'checkpoint.json').read_text()), self.checkpoint)
        before = (self.root / 'events.csv').read_bytes()
        resumed = Provider()
        with patch.object(scanner, 'request', side_effect=resumed):
            self.assertEqual(scanner.main(), 0)
        self.assertEqual((self.root / 'events.csv').read_bytes(), before)
        self.assertNotIn('/tx/' + 'a' * 64, [path for path, _ in resumed.calls])
        self.assertEqual(json.loads((self.root / 'checkpoint.json').read_text())['last_completed_height'], 11)

    def test_events_write_failure_keeps_checkpoint_and_all_pending_observations(self):
        real_atomic = scanner.atomic
        def interrupted(path, text):
            if path.name == 'events.csv':
                raise OSError('events write interrupted')
            return real_atomic(path, text)
        with patch.object(scanner, 'request', side_effect=Provider()), \
             patch.object(scanner, 'atomic', side_effect=interrupted):
            self.assertEqual(scanner.main(), 1)
        self.assertEqual(json.loads((self.root/'checkpoint.json').read_text()), self.checkpoint)
        self.assertFalse((self.root/'events.csv').exists())
        pending = json.loads((self.root/'pending_block.json').read_text())
        first_seen = pending['verified']['a' * 64]['observed_at_utc']
        resumed = Provider()
        with patch.object(scanner, 'request', side_effect=resumed):
            self.assertEqual(scanner.main(), 0)
        self.assertFalse(any(path.startswith('/tx/') for path, _ in resumed.calls))
        self.assertEqual(scanner.read_events()['a' * 64]['observed_at_utc'], first_seen)

    def test_pending_cleanup_failure_after_checkpoint_can_resume_the_next_block(self):
        real_unlink = Path.unlink
        def interrupted(path, *args, **kwargs):
            if path.name == 'pending_block.json':
                raise OSError('pending cleanup interrupted')
            return real_unlink(path, *args, **kwargs)
        with patch.object(scanner, 'request', side_effect=Provider()), \
             patch.object(Path, 'unlink', interrupted):
            self.assertEqual(scanner.main(), 1)
        self.assertEqual(json.loads((self.root/'checkpoint.json').read_text())['last_completed_height'], 11)
        original = scanner.read_events()['a' * 64]
        provider = Provider(tips=[18, 18])
        next_block = block(12, ['b' * 64])
        def resumed(path, **kwargs):
            if path == '/block/12':
                return next_block
            if path == '/tx/' + 'b' * 64:
                return transaction('b' * 64, next_block)
            return provider(path, **kwargs)
        with patch.object(scanner, 'request', side_effect=resumed):
            self.assertEqual(scanner.main(), 0)
        self.assertEqual(json.loads((self.root/'checkpoint.json').read_text())['last_completed_height'], 12)
        self.assertEqual(scanner.read_events()['a' * 64], original)
        self.assertEqual(len(scanner.read_events()), 2)

    def test_checkpoint_hash_change_is_not_silently_adopted(self):
        state = {**self.checkpoint, 'last_completed_block_hash': 'f' * 64}
        saved = json.dumps(state).encode()
        (self.root/'checkpoint.json').write_bytes(saved)
        with patch.object(scanner, 'request', side_effect=Provider()):
            self.assertEqual(scanner.main(), 1)
        self.assertEqual((self.root/'checkpoint.json').read_bytes(), saved)
        self.assertFalse((self.root/'events.csv').exists())


    def test_end_tip_read_reserves_time_for_publication(self):
        provider = Provider()
        with patch.object(scanner, 'request', side_effect=provider):
            scanner.main()
        kwargs = provider.calls[-1][1]
        self.assertEqual(kwargs.get('attempts'), 1)
        self.assertLessEqual(kwargs.get('timeout', 1000), 10)
        self.assertIn('deadline', kwargs)


class RequestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.addCleanup(patch.stopall)
        patch.object(scanner.time, 'monotonic', self.clock.monotonic).start()
        patch.object(scanner.time, 'sleep', self.clock.sleep).start()
        patch.object(scanner, '_last_request_time', 0).start()
        if hasattr(scanner, '_retry_not_before'):
            patch.object(scanner, '_retry_not_before', 0).start()

    def response(self):
        response = io.BytesIO(b'{"status":"success","data":{"ok":true}}')
        return response

    def test_retry_after_is_not_shortened_and_global_spacing_is_retained(self):
        error = urllib.error.HTTPError('url', 429, 'limited', {'Retry-After': '45'}, None)
        with patch.object(scanner.urllib.request, 'urlopen',
                          side_effect=[error, self.response()]):
            scanner.request('/network', attempts=2, deadline=self.clock.now + 100)
        self.assertIn(45, self.clock.sleeps)

    def test_retry_cannot_sleep_past_deadline(self):
        error = urllib.error.HTTPError('url', 429, 'limited', {'Retry-After': '45'}, None)
        with patch.object(scanner.urllib.request, 'urlopen', side_effect=error) as open_url:
            with self.assertRaises(Exception) as raised:
                scanner.request('/network', attempts=2, deadline=self.clock.now + 25)
        self.assertIs(raised.exception, error)
        self.assertEqual(open_url.call_count, 1)
        self.assertFalse(self.clock.sleeps)

    def test_overslept_rate_limit_wait_cannot_start_http_after_deadline(self):
        scanner._last_request_time = self.clock.now
        def oversleep(seconds):
            self.clock.now += seconds + 5
        with patch.object(scanner.time, 'sleep', oversleep), \
             patch.object(scanner.urllib.request, 'urlopen', return_value=self.response()) as open_url:
            with self.assertRaises(TimeoutError):
                scanner.request('/network', attempts=1, deadline=self.clock.now + 1)
        open_url.assert_not_called()

    def test_slow_response_past_deadline_cannot_be_accepted_as_fresh(self):
        clock = self.clock
        class SlowResponse(io.BytesIO):
            def read(self, *args, **kwargs):
                raw = super().read(*args, **kwargs)
                clock.now += 22
                return raw
        with patch.object(scanner.urllib.request, 'urlopen',
                          return_value=SlowResponse(b'{"status":"success","data":{"ok":true}}')):
            with self.assertRaises(TimeoutError):
                scanner.request('/network', attempts=1, deadline=clock.now + 21)

    def test_late_provider_failure_envelopes_remain_data_errors(self):
        clock = self.clock
        class LateFailure(io.BytesIO):
            def read(self, *args, **kwargs):
                raw = super().read(*args, **kwargs)
                clock.now += 22
                return raw
        for payload in ({'status':'error','data':{'message':'provider declared failure'}},
                        {'status':'success','data':[]}):
            with self.subTest(payload=payload):
                with patch.object(scanner.urllib.request, 'urlopen',
                                  return_value=LateFailure(json.dumps(payload).encode())):
                    with self.assertRaises(Exception) as raised:
                        scanner.request('/network', attempts=1, deadline=clock.now + 21)
                self.assertIsInstance(raised.exception, ValueError)
                self.assertIn('invalid API envelope', str(raised.exception))


    def test_all_requests_keep_original_global_start_interval(self):
        starts = []

        def response(*args, **kwargs):
            starts.append(self.clock.now)
            return self.response()

        with patch.object(scanner.urllib.request, 'urlopen', side_effect=response):
            scanner.request('/network')
            scanner.request('/network')
        self.assertGreaterEqual(starts[1] - starts[0], scanner.MIN_REQUEST_INTERVAL - 1e-9)


# Exact official response bodies captured 2026-11-11 12:06 UTC; original
# HTTP metadata and SHA256 remain in chain-diagnosis/current-window-cost/.
CAPTURED_NETWORK = json.loads("{\n  \"data\": {\n    \"info\": {\n      \"best_block_hash\": \"5feedabb8e890a7e47d1a2190b7db5d1385faf50f47bde0e9bc2865ff1b05082\",\n      \"block_count\": 6453131,\n      \"hashrate\": \"2351419498137581\",\n      \"mempool\": {\n        \"blocks\": [\n          {\n            \"block_num\": 1,\n            \"max_fee_rate\": 444445.0,\n            \"median_fee_rate\": 1837.0,\n            \"min_fee_rate\": 1003.0,\n            \"min_fee_rate_possible\": 100.0,\n            \"num_txs\": 13,\n            \"size\": 13640\n          },\n          {\n            \"block_num\": 2,\n            \"max_fee_rate\": 0.0,\n            \"median_fee_rate\": 0.0,\n            \"min_fee_rate\": 0.0,\n            \"min_fee_rate_possible\": 100.0,\n            \"num_txs\": 0,\n            \"size\": 0\n          },\n          {\n            \"block_num\": 3,\n            \"max_fee_rate\": 0.0,\n            \"median_fee_rate\": 0.0,\n            \"min_fee_rate\": 0.0,\n            \"min_fee_rate_possible\": 100.0,\n            \"num_txs\": 0,\n            \"size\": 0\n          },\n          {\n            \"block_num\": 4,\n            \"max_fee_rate\": 0.0,\n            \"median_fee_rate\": 0.0,\n            \"min_fee_rate\": 0.0,\n            \"min_fee_rate_possible\": 100.0,\n            \"num_txs\": 0,\n            \"size\": 0\n          },\n          {\n            \"block_num\": 5,\n            \"max_fee_rate\": 0.0,\n            \"median_fee_rate\": 0.0,\n            \"min_fee_rate\": 0.0,\n            \"min_fee_rate_possible\": 100.0,\n            \"num_txs\": 0,\n            \"size\": 0\n          },\n          {\n            \"block_num\": 6,\n            \"max_fee_rate\": 0.0,\n            \"median_fee_rate\": 0.0,\n            \"min_fee_rate\": 0.0,\n            \"min_fee_rate_possible\": 100.0,\n            \"num_txs\": 0,\n            \"size\": 0\n          }\n        ],\n        \"loading\": false,\n        \"mempool_size\": 13640,\n        \"mempool_txs\": 13,\n        \"quote_targets\": {\n          \"fast\": 20176991.0,\n          \"normal\": 5038314.0,\n          \"slow\": 183585.0\n        },\n        \"updated_at\": 1794398765\n      }\n    },\n    \"network\": \"DOGE\",\n    \"price_usd\": 0.06809631\n  },\n  \"status\": \"success\"\n}\n")
CAPTURED_BLOCK = json.loads("{\n  \"data\": {\n    \"auxpow\": {\n      \"parent_chain\": \"LTC\",\n      \"parent_hash\": \"25b353f9138f5cfbbee13aeee0295e67c9e1fba9ca0bca8795e92ccc591cf495\",\n      \"parent_height\": null,\n      \"parent_in_chain\": null,\n      \"parent_prev_hash\": \"b7731a1e1fcadefafd8ae6a169116e562f2216521e1bba8c2c22f8b91187ff9b\"\n    },\n    \"bits\": \"196c2b0a\",\n    \"confirmations\": 9,\n    \"difficulty\": \"39705799.70214868\",\n    \"fees\": \"2.91519032\",\n    \"hash\": \"5b4053890b48547bb5f693973002939ab7c14778d910173195ec22d5560dddba\",\n    \"height\": 6453123,\n    \"is_orphan\": false,\n    \"merkleroot\": \"aa71fc89ef0982e0dbf316909535dc34b7ed73c9720d0c47d25fa56c815cf0eb\",\n    \"next_block_hash\": \"578f286b08f7e17823f47d5ebadb9c4842914976a027bdea0577dd0135c9c354\",\n    \"nonce\": 0,\n    \"num_txs\": 27,\n    \"previous_block_hash\": \"8494a85b7fb1696f55abcfc23ce4c007c8081728957c85f7e8171fb470992b1e\",\n    \"price\": {\n      \"currency\": \"USD\",\n      \"value\": \"0.06810900\"\n    },\n    \"reward\": \"10000.00000000\",\n    \"size\": 21647,\n    \"time\": 1794398057,\n    \"txs\": [\n      \"f88edab090c4da2e6a7688ca818b75be5501dd37891321771a7b40b917c12932\",\n      \"9e1512a4568118ebb2424bac4393fe348a68f7cb9e208034b5ac16a653aa006f\",\n      \"7530a2c527e94b0b6597d1a302db295eab7278911c60f179d5788ea48ef1062e\",\n      \"50eb9dee6d595157fd7b3fb0d3abfbd5f2785a64c8901d2e1de0a90c04799a82\",\n      \"c4a71eac8f08e73d0808547a3141daf57d91887b9d4bc0af1c66e2da33a3462a\",\n      \"3a64ba5a6bbbc4bdb494bd12efd50575f25f16a7237d37420d289c304b79de37\",\n      \"05eccb1e38e4e78b4bbff232edc563b6d14656c206f6a512ff7f677db94e15f8\",\n      \"9ef9aeda77f4da6d4070fbe25ed67933088bb55b18e35486b053db33fee86315\",\n      \"a71d8f4f1710a39f3a0a8bbac5809a15668be52d13902ac96dfdd6ade279f090\",\n      \"54136935dd5d0aa7a682d1eb71a2e5536b5ed7c52f569c36a622eeb9a27c0b87\",\n      \"638afdec88dbb31c621d3a82e50124a6a903fad4cd1ea851aa2b1120ee7c4aaa\",\n      \"bf8ea416c4793c64d5ebd9c6526b6e6fdebea02f970fa80ff6549b6e28c5512a\",\n      \"9f9a92b8472921b87d3b7243df93b75bef4cd007ef7fb3e49e7c4872d931905c\",\n      \"d94f8912762987fab947330c3bd3744d9128095e526692bd083ecbfaa0cb0fac\",\n      \"4d4f7c946ab6c1b9905ed8abf408ece3da28ab9a3825046dc8dbbe68fa05182a\",\n      \"01c6383b88abfa601764992b7f896c81979fffd2d83c7a1b11d5c1d7052465dd\",\n      \"5151616884e3033315fcc6ba1b0bef9c9fa0ef3c55e8fb81983edfedcaf179c1\",\n      \"40e8b02b7d2034f96b8305c8d5748cde8660f31d00d61369a0b417dae376d50d\",\n      \"b5291987288e471da8b902053f9be10c3c47c0b9c7108b39e58145a5ae9721fe\",\n      \"e612588503474df7d638b08962984f5ea3f5407fe424753c2005ea333527704c\",\n      \"030df98c2c31129c481ee02fe2447e216bff8430c6ab1c39ffefa6681b523d51\",\n      \"1ed531d44dcd8b6be50660545e020f7510af74d906f792dbc5f4067c220ee97a\",\n      \"213caf7e3bb4657f137898627705bd13ec7f208980b9b7e3402f6cdac1848b0e\",\n      \"e20c62cb40ddc1d225c556859c49e3d952d14ccc1946836c21f865465d35b308\",\n      \"0dea1d09e0cb1ccbb86603870ac7d0a0d87d64b58e24a7d0fb658cab2f95d207\",\n      \"310d59f3ca4f66de9bc43267320286f952c7dce713fe9f3cc718c6bf400976ae\",\n      \"d7daff73d2bed57fe851d922e279d02dfc4dfc83cfcb664440400a500c2c8145\"\n    ],\n    \"value_out\": \"136304.43508876\",\n    \"version\": 6422788,\n    \"weight\": 86588\n  },\n  \"status\": \"success\"\n}\n")
CAPTURED_TRANSACTION = json.loads("{\n  \"data\": {\n    \"explain\": {\n      \"change\": \"0.00000000\",\n      \"change_outputs\": [],\n      \"detail\": \"This is the miner's reward: brand-new coins plus the fees from every transaction in the block.\",\n      \"fee\": \"0.00000000\",\n      \"headline\": \"DTZSTXecLmSXpRGSfht4tAMyqra1wsL7xb collected the 10,002.92 DOGE block reward\",\n      \"sent\": \"10002.91519032\"\n    },\n    \"transaction\": {\n      \"block_hash\": \"5b4053890b48547bb5f693973002939ab7c14778d910173195ec22d5560dddba\",\n      \"block_height\": 6453123,\n      \"confirmations\": 9,\n      \"fee\": \"0.00000000\",\n      \"hash\": \"f88edab090c4da2e6a7688ca818b75be5501dd37891321771a7b40b917c12932\",\n      \"inputs\": [\n        {\n          \"address\": null,\n          \"index\": 0,\n          \"previous_output\": null,\n          \"value\": \"0.00000000\"\n        }\n      ],\n      \"inputs_n\": 1,\n      \"inputs_value\": \"0.00000000\",\n      \"locktime\": 0,\n      \"outputs\": [\n        {\n          \"address\": \"DTZSTXecLmSXpRGSfht4tAMyqra1wsL7xb\",\n          \"index\": 0,\n          \"is_change\": null,\n          \"spent\": null,\n          \"type\": \"pubkeyhash\",\n          \"value\": \"10002.91519032\"\n        }\n      ],\n      \"outputs_n\": 1,\n      \"outputs_value\": \"10002.91519032\",\n      \"price\": {\n        \"currency\": \"USD\",\n        \"value\": \"0.06810900\"\n      },\n      \"size\": 91,\n      \"time\": 1794398057,\n      \"version\": 1\n    },\n    \"warnings\": []\n  },\n  \"status\": \"success\"\n}\n")


class CapturedProviderReplayTests(unittest.TestCase):
    def test_real_network_block_and_nested_transaction_shapes(self):
        clock = Clock()
        calls = []
        def recorded_response(request, **kwargs):
            url = request.full_url
            calls.append((url, kwargs))
            data = (CAPTURED_NETWORK if url.endswith('/network') else
                    CAPTURED_BLOCK if '/block/' in url else CAPTURED_TRANSACTION)
            return io.BytesIO(json.dumps(data).encode('utf-8'))
        with patch.object(scanner.time, 'monotonic', clock.monotonic), \
             patch.object(scanner.time, 'sleep', clock.sleep), \
             patch.object(scanner, '_last_request_time', 0), \
             patch.object(scanner, '_retry_not_before', 0), \
             patch.object(scanner.urllib.request, 'urlopen', side_effect=recorded_response):
            source_block = scanner.parse_block(6453123)
            self.assertEqual(len(source_block['txids']), 27)
            txid = CAPTURED_TRANSACTION['data']['transaction']['hash']
            amount, _ = scanner.explain(txid, source_block)
            self.assertEqual(amount, Decimal('10002.91519032'))
            end_tip = scanner.get_tip(attempts=1, timeout=10, deadline=clock.now + 30)
        self.assertEqual(end_tip, 6453131)
        self.assertEqual(end_tip - scanner.CONFIRMATIONS, 6453125)
        self.assertEqual(calls[-1][0], scanner.API + '/network')
        self.assertLessEqual(calls[-1][1]['timeout'], 10)


class NormalBudgetTailTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture_directory()
        self.root = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.clock = Clock()
        self.start = self.clock.now
        self.checkpoint = {'last_completed_height': 10}
        self.pending = {'schema_version': 1, 'height': 11, 'txids': ['a' * 64],
                        'verified': {'a' * 64: {'amount_doge': '2000000', 'headline': 'legacy',
                                               'observed_at_utc': '2026-10-15T10:31:36+00:00'}}}
        (self.root/'checkpoint.json').write_text(json.dumps(self.checkpoint))
        self.pending_raw = json.dumps(self.pending).encode()
        (self.root/'pending_block.json').write_bytes(self.pending_raw)
        self.addCleanup(patch.stopall)
        patch.object(scanner, 'OUT', self.root).start()
        patch.object(scanner, 'RUN_SECONDS', 780).start()
        patch.object(scanner, 'MAX_BLOCKS', 1).start()
        patch.object(scanner.time, 'monotonic', self.clock.monotonic).start()
        patch.object(scanner.time, 'sleep', self.clock.sleep).start()
        patch.object(scanner, '_last_request_time', 0).start()
        patch.object(scanner, '_retry_not_before', 0).start()
        patch.object(scanner, 'get_tip', return_value=17).start()

    def source_block(self, height, **kwargs):
        value = block(height)
        value['txids'] = value['txs']
        return value

    def assert_original_state(self):
        self.assertEqual(json.loads((self.root/'checkpoint.json').read_text()), self.checkpoint)
        self.assertEqual((self.root/'pending_block.json').read_bytes(), self.pending_raw)

    def summary(self):
        return json.loads((self.root/'latest.json').read_bytes())

    def test_last_point_three_seconds_stops_normally_without_any_http(self):
        def near_deadline(height, **kwargs):
            value = self.source_block(height)
            if height == 11:
                self.clock.now = self.start + 660 - .3
                scanner._last_request_time = self.clock.now
            return value
        with patch.object(scanner, 'parse_block', side_effect=near_deadline), \
             patch.object(scanner.urllib.request, 'urlopen') as network:
            self.assertEqual(scanner.main(), 0)
        network.assert_not_called()
        self.assert_original_state()
        latest = self.summary()
        self.assertEqual(latest['errors'], [])
        self.assertEqual(latest['scan_stop_reason'], 'time_budget_in_block')
        self.assertEqual(latest['incomplete_block_not_committed'], 11)
        self.assertEqual(latest['backlog_blocks_at_run_end'], 1)

    def test_new_block_needs_a_full_request_slot_before_it_starts(self):
        seen = []
        def near_deadline(height, **kwargs):
            seen.append(height)
            value = self.source_block(height)
            if height == 10:
                self.clock.now = self.start + 660 - .3
            return value
        with patch.object(scanner, 'parse_block', side_effect=near_deadline):
            self.assertEqual(scanner.main(), 0)
        self.assertEqual(seen, [10])
        self.assert_original_state()
        self.assertEqual(self.summary()['scan_stop_reason'], 'time_budget_before_block')

    def test_real_provider_errors_stay_errors_when_retry_budget_runs_out(self):
        cases = [urllib.error.HTTPError('url', 429, 'provider rate limited', {'Retry-After':'45'}, None),
                 urllib.error.HTTPError('url', 500, 'provider server failed', {}, None),
                 TimeoutError('real provider timeout'),
                 urllib.error.URLError('real provider connection failed')]
        for error in cases:
            with self.subTest(provider_error=str(error)):
                self.clock.now = self.start
                scanner._retry_not_before = 0
                scanner._last_request_time = 0
                def near_deadline(height, **kwargs):
                    value = self.source_block(height)
                    if height == 11:
                        self.clock.now = self.start + 660 - 25
                    return value
                def provider_failure(*args, **kwargs):
                    self.clock.now += 24.9
                    raise error
                with patch.object(scanner, 'parse_block', side_effect=near_deadline), \
                     patch.object(scanner.urllib.request, 'urlopen', side_effect=provider_failure):
                    self.assertEqual(scanner.main(), 1)
                self.assert_original_state()
                self.assertEqual(self.summary()['scan_stop_reason'], 'error')
                self.assertTrue(any(str(error) in value for value in self.summary()['errors']),
                                self.summary()['errors'])

    def test_valid_complete_response_after_local_deadline_is_normal_incomplete_progress(self):
        def near_deadline(height, **kwargs):
            value = self.source_block(height)
            if height == 11:
                self.clock.now = self.start + 660 - 21
            return value
        clock = self.clock
        class SlowResponse(io.BytesIO):
            def read(self, *args, **kwargs):
                raw = super().read(*args, **kwargs)
                clock.now += 22
                return raw
        body = json.dumps({'status':'success', 'data':transaction('a' * 64)}).encode()
        with patch.object(scanner, 'parse_block', side_effect=near_deadline), \
             patch.object(scanner.urllib.request, 'urlopen', return_value=SlowResponse(body)):
            self.assertEqual(scanner.main(), 0)
        self.assert_original_state()
        self.assertEqual(self.summary()['errors'], [])
        self.assertEqual(self.summary()['scan_stop_reason'], 'time_budget_in_block')
        self.assertEqual(self.summary()['backlog_blocks_at_run_end'], 1)

    def test_end_tip_local_budget_failure_stays_error_and_cannot_reuse_zero(self):
        failure_type = getattr(scanner, 'BudgetExhausted', TimeoutError)
        def tip(**kwargs):
            if kwargs.get('attempts') == 1:
                raise failure_type('end tip has no budget')
            return 17
        with patch.object(scanner, 'get_tip', side_effect=tip), \
             patch.object(scanner, 'parse_block', side_effect=self.source_block), \
             patch.object(scanner, 'explain', return_value=(Decimal('2000000'), 'legacy')):
            self.assertEqual(scanner.main(), 1)
        latest = self.summary()
        self.assertEqual(latest['last_completed_height'], 11)
        self.assertIsNone(latest['backlog_blocks_at_run_end'])
        self.assertIsNone(latest['confirmed_tip_at_run'])
        self.assertFalse(latest['run_end_tip_verified'])
        self.assertTrue(latest['errors'])



if __name__ == '__main__':
    unittest.main()


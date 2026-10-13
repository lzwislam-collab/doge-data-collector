"""Isolated causal market-only preparation; no issuance, training, orders or IO.

Availability metadata is a necessary condition, not independently witnessed PIT.
Caller must verify raw bytes, ledger chain and public pre-target witness separately.
"""
import hashlib
import json
import math
import copy
from datetime import datetime, timedelta, timezone

HOUR_MS = 3600000
SYMBOLS = ('DOGE-USDT', 'BTC-USDT')
SOURCE = 'OKX history-candles 1H confirmed'


def utc(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timezone required')
    return stamp.astimezone(timezone.utc)


def clock(value):
    if value.tzinfo is None:
        raise ValueError('timezone required')
    return value.astimezone(timezone.utc)


def number(value, minimum=0):
    value = float(value)
    if not math.isfinite(value) or value < minimum:
        raise ValueError('invalid market value')
    return value


def available(row, at, max_age_seconds):
    """Validate metadata chronology; never substitutes for raw/public evidence."""
    at = clock(at)
    if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError('invalid freshness limit')
    required = ('source_id', 'observation_key', 'source_url', 'raw_sha256', 'raw_path',
                'requested_at_utc', 'first_seen_utc', 'usable_from_utc')
    if any(not row.get(k) for k in required):
        raise ValueError('missing provenance')
    if not row['source_url'].startswith('https://') or len(row['raw_sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['raw_sha256']):
        raise ValueError('invalid provenance')
    number(row['value'], -float('inf'))
    requested, seen, usable = (utc(row[k]) for k in required[-3:])
    if not requested <= seen <= usable:
        raise ValueError('availability chronology invalid')
    if row.get('published_at_utc') and utc(row['published_at_utc']) > seen:
        raise ValueError('publication after receipt')
    return usable <= at and (at-seen).total_seconds() <= max_age_seconds


def prepare(rows, at):
    """Fixed 25 confirmed, contiguous, first-ingested bars for both assets.

    Historical downloads are unavailable before their real ingestion timestamp.
    No probability calibration or selected-trade advantage is inferred.
    """
    at = clock(at)
    cutoff = int(at.timestamp()*1000)
    by_symbol = {s: {} for s in SYMBOLS}
    keys = set()
    for row in rows:
        symbol = row['symbol']
        if symbol not in by_symbol:
            continue
        opened = int(row['open_utc_ms'])
        if opened < 0 or opened % HOUR_MS:
            raise ValueError('unaligned bar timestamp')
        key = symbol, opened
        if key in keys:
            raise ValueError('duplicate market bar')
        keys.add(key)
        if row.get('source') != SOURCE:
            raise ValueError('unconfirmed source')
        close = number(row['close'])
        if close == 0:
            raise ValueError('zero close')
        number(row['volume_base'])
        seen = utc(row['first_ingested_utc'])
        if int(seen.timestamp()*1000) < opened+HOUR_MS:
            raise ValueError('confirmed ingestion before bar close')
        if opened+HOUR_MS <= cutoff and seen <= at:
            by_symbol[symbol][opened] = copy.deepcopy(row)
    result = {'schema_version':1, 'candidate_id':'MARKET_ONLY_PREPARATION_V1',
              'prepared_at_utc':at.isoformat(), 'status':'WAIT_SOURCE', 'directions':{},
              'p_up':None, 'paper_action':'NO_TRADE', 'real_orders_authorized':False,
              'prediction_issued':False, 'probability_calibration_validated':False,
              'raw_response_and_public_witness_verified':False}
    windows = {}
    for symbol, bars in by_symbol.items():
        # Require the most recent completed hour; do not silently use an older
        # close when that hour was ingested after the preparation cutoff.
        last = cutoff//HOUR_MS*HOUR_MS-HOUR_MS
        wanted = [last-i*HOUR_MS for i in range(24, -1, -1)]
        if any(i not in bars for i in wanted):
            result['reason'] = 'missing_or_not_yet_available_'+symbol
            return result
        windows[symbol] = [bars[i] for i in wanted]
    features = {}
    for symbol, bars in windows.items():
        prices = [float(r['close']) for r in bars]
        returns = [math.log(b)-math.log(a) for a,b in zip(prices, prices[1:])]
        mean = sum(returns)/len(returns)
        volumes = [float(r['volume_base']) for r in bars]
        volume_mean = math.fsum(v/24 for v in volumes[:-1])
        features[symbol] = {'return_1h': returns[-1], 'return_24h': math.log(prices[-1])-math.log(prices[0]),
            'realized_volatility_24h':math.sqrt(sum((r-mean)**2 for r in returns)/len(returns)),
            'volume_ratio_1h_to_prior24h':volumes[-1]/volume_mean if volume_mean > 0 else None}
        for value in features[symbol].values():
            if value is not None: number(value, -float('inf'))
    def direction(value):
        return 'UP' if value > 0 else 'DOWN' if value < 0 else None
    raw = json.dumps(windows, sort_keys=True, separators=(',', ':')).encode()
    result.update(status='PREPARED_NOT_ISSUED', features=features,
        feature_input_sha256=hashlib.sha256(raw).hexdigest(), input_rows=windows,
        directions={'DOGE_MOMENTUM_1H':direction(features['DOGE-USDT']['return_1h']),
                    'DOGE_REVERSAL_1H':direction(-features['DOGE-USDT']['return_1h']),
                    'BTC_MOMENTUM_1H':direction(features['BTC-USDT']['return_1h'])})
    return result


def purged_split(rows, calibration_from, validation_from, embargo_seconds):
    """Chronological research partitions, purged at label end plus embargo.

    This helper creates no fitted model and does not bless retrospective PIT.
    """
    calibration_from, validation_from = clock(calibration_from), clock(validation_from)
    if calibration_from >= validation_from or not math.isfinite(embargo_seconds) or embargo_seconds < 0:
        raise ValueError('invalid split boundaries')
    ordered = sorted(rows, key=lambda r:clock(r['anchor']))
    if len({clock(r['anchor']) for r in ordered}) != len(ordered):
        raise ValueError('duplicate split anchor')
    out = {'train':[], 'calibration':[], 'validation':[]}
    for row in ordered:
        anchor, end = clock(row['anchor']), clock(row['label_end'])
        if end <= anchor:
            raise ValueError('invalid label end')
        end += timedelta(seconds=embargo_seconds)
        if anchor < calibration_from:
            if end < calibration_from: out['train'].append(row)
        elif anchor < validation_from:
            if end < validation_from: out['calibration'].append(row)
        else:
            out['validation'].append(row)
    return out

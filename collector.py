#!/usr/bin/env python3
"""DOGE research collector v2.0. No private keys; source-labelled metrics; no fake data."""
import csv
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = 'https://www.okx.com'
SYMBOLS = ('BTC-USDT', 'ETH-USDT', 'DOGE-USDT')
OUT = Path(__file__).resolve().parent / 'data'
OUT.mkdir(exist_ok=True)
FIELDS = ['collected_at_ms', 'symbol', 'ticker_ts_ms', 'last', 'bid', 'ask',
          'volume_24h_base', 'volume_24h_quote', 'closed_hour_start_ms',
          'hour_open', 'hour_high', 'hour_low', 'hour_close', 'hour_volume_base']


def request_json(url):
    req = urllib.request.Request(url, headers={
        'Accept': 'application/json', 'Cache-Control': 'no-cache',
        'User-Agent': 'doge-research-collector/2.0'})
    with urllib.request.urlopen(req, timeout=18) as response:
        return json.load(response)


def okx(path, **kwargs):
    obj = request_json(BASE + path + '?' + urllib.parse.urlencode(kwargs))
    if obj.get('code') != '0' or not obj.get('data'):
        raise ValueError('OKX API failure: ' + str(obj.get('code')) + ' ' + str(obj.get('msg')))
    return obj['data']


def pos(x):
    val = float(x)
    if not 0 < val < float('inf'):
        raise ValueError('Invalid positive value: ' + repr(x))
    return val


def try_metric(name, fn, faults):
    try:
        return fn()
    except (Exception,) as exc:
        faults[name] = str(exc)[:300]
        return None


def spot(symbol):
    failures = []
    for endpoint in ('/api/v5/market/ticker', '/api/v5/market/tickers', '/api/v5/market/ticker'):
        try:
            params = {'instType': 'SPOT'} if endpoint.endswith('/tickers') else {'instId': symbol}
            tick = next(x for x in okx(endpoint, **params) if x.get('instId') == symbol)
            ts = int(tick['ts'])
            now = int(time.time() * 1000)
            if not 0 <= now - ts <= 600000:
                raise ValueError('Stale ticker')
            bid, ask, last = (pos(tick[k]) for k in ('bidPx', 'askPx', 'last'))
            if bid > ask:
                raise ValueError('Crossed bid/ask')
            return {'symbol': symbol, 'ticker_ts_ms': ts, 'last': tick['last'],
                    'bid': tick['bidPx'], 'ask': tick['askPx'],
                    'volume_24h_base': tick['vol24h'],
                    'volume_24h_quote': tick['volCcy24h']}
        except Exception as exc:
            failures.append(str(exc))
            time.sleep(.5)
    raise ValueError('; '.join(failures))


def candles(symbol, bar, limit=100):
    raw = okx('/api/v5/market/history-candles', instId=symbol, bar=bar, limit=str(limit))
    now = int(time.time()*1000)
    step = 3600000 if bar == '1H' else 86400000
    result = {}
    for c in raw:
        if len(c) < 9 or str(c[8]) != '1':
            continue
        stamp = int(c[0])
        if stamp < 0 or stamp + step > now + 60000:
            continue
        o,h,l,cl = (pos(x) for x in c[1:5])
        if h < max(o,cl,l) or l > min(o,cl,h) or float(c[5]) < 0:
            continue
        result[stamp] = c
    if not result:
        raise ValueError('No valid confirmed ' + bar + ' candles')
    return result


def derivative(symbol):
    inst = symbol + '-SWAP'
    oi = okx('/api/v5/public/open-interest', instType='SWAP', instId=inst)[0]
    funding = okx('/api/v5/public/funding-rate', instId=inst)[0]
    now = int(time.time()*1000)
    oi_ts = int(oi['ts'])
    if not 0 <= now - oi_ts <= 1800000:
        raise ValueError('Stale open interest')
    return {'source': 'OKX public API', 'instrument': inst,
            'open_interest_contracts': oi.get('oi'), 'open_interest_ccy': oi.get('oiCcy'),
            'open_interest_usd': oi.get('oiUsd'), 'open_interest_ts_ms': oi_ts,
            'funding_rate': funding.get('fundingRate'),
            'funding_time_ms': int(funding.get('fundingTime') or 0),
            'next_funding_time_ms': int(funding.get('nextFundingTime') or 0)}


def orderbook(symbol):
    x = okx('/api/v5/market/books', instId=symbol, sz='20')[0]
    now = int(time.time()*1000)
    ts = int(x['ts'])
    if not 0 <= now-ts <= 600000:
        raise ValueError('Stale orderbook')
    bids = [(pos(b[0]), pos(b[1])) for b in x['bids']]
    asks = [(pos(a[0]), pos(a[1])) for a in x['asks']]
    if not bids or not asks or bids[0][0] > asks[0][0]:
        raise ValueError('Invalid orderbook')
    bv = sum(p*q for p,q in bids)
    av = sum(p*q for p,q in asks)
    mid = (bids[0][0]+asks[0][0])/2
    return {'source':'OKX public API','timestamp_ms':ts,
            'spread_bps':(asks[0][0]-bids[0][0])/mid*10000,
            'bid_notional_usdt_top20':bv,'ask_notional_usdt_top20':av,
            'imbalance_top20':(bv-av)/(bv+av),
            'note':'20-level snapshot, not executed trade flow or market-wide liquidity'}


def fear_greed():
    obj = request_json('https://api.alternative.me/fng/?limit=1')
    d = obj['data'][0]
    return {'source':'alternative.me','scope':'broad crypto market, not DOGE',
            'value':int(d['value']),'classification':d['value_classification'],
            'timestamp_s':int(d['timestamp'])}


def technical(daily):
    prices = [float(daily[t][4]) for t in sorted(daily)]
    if len(prices) < 15:
        return {'status':'insufficient_history','available_days':len(prices)}
    returns = [prices[i]/prices[i-1]-1 for i in range(1,len(prices))]
    return {'status':'ok','available_days':len(prices),
            'return_7d':prices[-1]/prices[-8]-1,
            'return_14d':prices[-1]/prices[-15]-1,
            'sma_7d':sum(prices[-7:])/7,
            'sma_14d':sum(prices[-14:])/14,
            'mean_absolute_daily_return_14d':sum(abs(x) for x in returns[-14:])/14,
            'note':'Descriptive statistics, not a calibrated price prediction'}


def write_hourly(history, collected_ms):
    """Preserve established CSV schema; backfill confirmed candles in API rolling window."""
    path = OUT / 'hourly.csv'
    prior = {}
    if path.exists():
        with path.open(newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != FIELDS:
                raise ValueError('CSV schema mismatch, manual migration required')
            for row in reader:
                key = (row['symbol'], int(row['closed_hour_start_ms']))
                if key in prior:
                    raise ValueError('Existing duplicate historical key: '+str(key))
                prior[key] = row
    for symbol, bars in history.items():
        for stamp, c in bars.items():
            key = (symbol,stamp)
            if key not in prior:
                # Hour-end snapshot data cannot be reconstructed for missed hours.
                # Such values are explicitly blank, rather than fabricated.
                prior[key] = dict(zip(FIELDS, [str(collected_ms), symbol, '', '', '', '', '', '',
                                               str(stamp), c[1], c[2], c[3], c[4], c[5]]))
    tmp = path.with_suffix('.csv.tmp')
    with tmp.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(prior[k] for k in sorted(prior, key=lambda a:(a[1],a[0])))
    os.replace(tmp,path)
    report = {}
    for symbol in SYMBOLS:
        series = sorted(t for s,t in prior if s==symbol)
        holes = sum(max(0,(b-a)//3600000-1) for a,b in zip(series,series[1:]))
        report[symbol] = {'rows':len(series), 'missing_hours_between_first_last':holes,
                          'earliest_ms':series[0] if series else None,
                          'latest_ms':series[-1] if series else None}
    return report


def atomic_json(path, obj):
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    os.replace(tmp,path)


def run():
    started = int(time.time()*1000)
    errors, markets, hours = {}, {}, {}
    extra = {'derivatives':{},'orderbook':{},'daily_technical':{},'market_sentiment':None}
    for symbol in SYMBOLS:
        ticker = try_metric('spot:'+symbol,lambda s=symbol:spot(s),errors)
        h = try_metric('hourly:'+symbol,lambda s=symbol:candles(s,'1H'),errors)
        d = try_metric('daily:'+symbol,lambda s=symbol:candles(s,'1Dutc'),errors)
        if ticker and h:
            c = h[max(h)]
            ticker.update({'closed_hour_start_ms':max(h),'hour_open':c[1],
                           'hour_high':c[2],'hour_low':c[3],
                           'hour_close':c[4],'hour_volume_base':c[5]})
            markets[symbol] = ticker
        if h: hours[symbol]=h
        if d: extra['daily_technical'][symbol]=technical(d)
        v = try_metric('derivatives:'+symbol,lambda s=symbol:derivative(s),errors)
        if v: extra['derivatives'][symbol]=v
        v = try_metric('orderbook:'+symbol,lambda s=symbol:orderbook(s),errors)
        if v: extra['orderbook'][symbol]=v
    extra['market_sentiment'] = try_metric('market_sentiment',fear_greed,errors)
    quality = try_metric('history_write',lambda:write_hourly(hours,started),errors)
    result = {'schema_version':3,'source':'OKX public API; third-party sentiment separately labelled',
              'collected_at_utc':datetime.now(timezone.utc).isoformat(),
              'collected_at_ms':started,
              'status':'ok' if len(markets)==3 and not errors else ('partial' if markets else 'unavailable'),
              'markets':markets,'extensions':extra,
              'data_quality':{'hourly':quality,'errors':errors,
                  'notes':['1H repair limited to latest ~100 confirmed candles',
                           'Historical missed tick snapshots cannot be reconstructed',
                           'Not included: liquidations, DOGE whale flows, verified chain activity, ETF flow, macro events, news, calibrated forecast']},
              'unavailable_markets':{k:v for k,v in errors.items() if k.startswith(('spot:','hourly:'))}}
    atomic_json(OUT/'latest.json', result)
    print('collector_v2',result['status'],len(markets),'of 3 valid; faults:',len(errors),flush=True)
    # Keep file publication possible even if optional sources fail; detailed errors are recorded.
    # Core market failures fail workflow after writing diagnostic JSON locally.
    if len(markets)!=3 or quality is None:
        raise SystemExit(1)

if __name__ == '__main__':
    run()

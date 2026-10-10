#!/usr/bin/env python3
"""DOGE chain analytics: verified daily chart series and richlist balance deltas.
Independent of existing DOGE onchain collector. Free Dogechain API v3.
Unverified exchange netflows deliberately not computed.
"""
import csv
import json
import os
import time
import urllib.request
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data' / 'onchain'
BASE = 'https://dogechain.com/api/v3'
SERIES = ('active_addresses', 'new_addresses', 'tx_count', 'transfers', 'transfers_sent')
CSV_FIELDS = ['date_utc', *SERIES]


def api(route):
    req = urllib.request.Request(BASE + route, headers={
        'Accept': 'application/json', 'User-Agent': 'doge-data-collector/metrics-1.0'})
    with urllib.request.urlopen(req, timeout=25) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or payload.get('status') != 'success':
        raise ValueError('Invalid API envelope: ' + route)
    return payload['data']


def atomic(path, content):
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(content, encoding='utf-8')
    os.replace(tmp, path)


def valid_number(v):
    d = Decimal(str(v))
    if not d.is_finite() or d < 0:
        raise ValueError('Invalid series value')
    return str(d)


def chart(name, today):
    data = api('/chart/' + name + '?interval=day')
    points = data.get('points') if isinstance(data, dict) else None
    if not isinstance(points, list):
        raise ValueError('Missing points array for ' + name)
    records = {}
    for item in points:
        if not isinstance(item, dict):
            continue
        stamp = int(item['t'])
        day = datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y-%m-%d')
        if day >= today:  # exclude unfinished UTC day
            continue
        records[day] = valid_number(item['v'])
    if not records:
        raise ValueError('No closed days for ' + name)
    return records


def richlist():
    data = api('/richlist?page=1')
    rows = data.get('rows') if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) < 10:
        raise ValueError('Richlist missing top ten addresses')
    top = []
    for item in rows:
        address = item['address']
        balance = Decimal(str(item['balance']))
        delta_day = Decimal(str(item['delta_24h']))
        delta_week = Decimal(str(item['delta_7d']))
        if not balance.is_finite() or balance < 0:
            raise ValueError('Invalid balance')
        top.append({
            'rank': int(item['rank']), 'address': address,
            'balance_doge': str(balance),
            'delta_24h_doge': str(delta_day),
            'delta_7d_doge': str(delta_week),
            'provider_label': item.get('label'),
        })
    top.sort(key=lambda x: x['rank'])
    return {
        'observed_addresses': len(top), 'top10': top[:10],
        'top100_positive_balance_delta_24h_doge': str(sum(Decimal(a['delta_24h_doge']) for a in top if Decimal(a['delta_24h_doge']) > 0)),
        'top100_negative_balance_delta_24h_doge': str(sum(Decimal(a['delta_24h_doge']) for a in top if Decimal(a['delta_24h_doge']) < 0)),
        'note': 'Current top 100 addresses only; address net balance changes, NOT transfer volumes, whale trade flow or exchange netflow.'
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    today = now.strftime('%Y-%m-%d')
    errors = {}
    daily = {}
    for name in SERIES:
        try:
            daily[name] = chart(name, today)
        except Exception as exc:
            errors[name] = str(exc)[:300]
    whales = None
    try:
        whales = richlist()
    except Exception as exc:
        errors['richlist'] = str(exc)[:300]
    # Only write rows where ALL required fields were genuinely observed.
    days = sorted(set.intersection(*(set(daily[k]) for k in SERIES))) if len(daily) == len(SERIES) else []
    existing = {}
    path = OUT / 'daily_activity.csv'
    if path.exists():
        with path.open(newline='', encoding='utf-8') as fp:
            reader = csv.DictReader(fp)
            if reader.fieldnames != CSV_FIELDS:
                raise ValueError('Unexpected historical CSV schema')
            for row in reader:
                existing[row['date_utc']] = row
    for day in days:
        existing[day] = {'date_utc': day, **{name: daily[name][day] for name in SERIES}}
    from io import StringIO
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(existing[k] for k in sorted(existing))
    atomic(path, buf.getvalue())
    latest_day = days[-1] if days else None
    result = {
        'schema_version': 1,
        'source': 'Dogechain public API v3',
        'collected_at_utc': now.isoformat(),
        'status': 'ok' if not errors and latest_day and whales else 'partial',
        'latest_complete_day_utc': latest_day,
        'daily_observations': len(existing),
        'daily_activity_latest': {name: daily[name][latest_day] for name in SERIES} if latest_day else None,
        'richlist_balance_deltas': whales,
        'errors': errors,
        'not_available': ['verified_exchange_netflow', 'entity_level_whale_trades', 'full_network_large_transfer_feed'],
        'caveats': ['addresses are not unique people', 'transfers include UTXO change and self-transfers', 'top100 composition changes over time']
    }
    atomic(OUT / 'metrics_latest.json', json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'status': result['status'], 'daily_observations': len(existing), 'errors': errors}, ensure_ascii=False))
    return 0 if result['status'] == 'ok' else 1

if __name__ == '__main__':
    raise SystemExit(main())

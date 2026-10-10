#!/usr/bin/env python3
"""DOGE activity v1.2. Daily metrics + non-scalar transfer buckets, provenance and diagnostics.
No unaudited exchange flow / inferred whale trades.
"""
import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

BASE = 'https://dogechain.com/api/v3'
OUT = Path(__file__).resolve().parent / 'data' / 'onchain'
CORE = ('active_addresses', 'new_addresses', 'tx_count')
BUCKET_SERIES = ('transfers', 'transfers_sent')
FIELDS = ['date_utc', *CORE, *BUCKET_SERIES]


def api(path):
    request = urllib.request.Request(BASE + path, headers={
        'Accept': 'application/json', 'User-Agent': 'doge-data-collector/metrics-1.2'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=22) as res:
                obj = json.load(res)
            if not isinstance(obj, dict) or obj.get('status') != 'success' or 'data' not in obj:
                raise ValueError('Invalid API envelope')
            return obj['data']
        except (OSError, ValueError, TimeoutError):
            if attempt == 2: raise
            time.sleep(1 + attempt)


def write_atomic(path, contents):
    p = path.with_name(path.name + '.tmp')
    p.write_text(contents, encoding='utf-8')
    os.replace(p, path)


def nonnegative(x):
    try:
        val = Decimal(str(x))
    except (InvalidOperation, TypeError):
        raise ValueError('not a numeric scalar')
    if not val.is_finite() or val < 0:
        raise ValueError('negative or nonfinite value')
    return str(val)


def points(name, today):
    body = api('/chart/' + name + '?interval=day')
    raw = body.get('points') if isinstance(body, dict) else None
    if not isinstance(raw, list): raise ValueError('points list missing')
    byday = {}
    skipped = 0
    for point in raw:
        try:
            day = datetime.fromtimestamp(int(point['t']), timezone.utc).date().isoformat()
            if day >= today: continue
            byday[day] = point['v']
        except (TypeError, ValueError, KeyError, OverflowError, OSError): skipped += 1
    if not byday: raise ValueError('no closed UTC day')
    return byday, skipped


def richlist():
    doc = api('/richlist?page=1')
    rows = doc.get('rows') if isinstance(doc, dict) else None
    if not isinstance(rows, list) or len(rows) < 10: raise ValueError('richlist has fewer than ten rows')
    result=[]
    for row in rows:
        bal = Decimal(nonnegative(row['balance']))
        day = Decimal(str(row['delta_24h']))
        week = Decimal(str(row['delta_7d']))
        if not day.is_finite() or not week.is_finite(): raise ValueError('bad delta')
        result.append({'rank': int(row['rank']), 'address': row['address'], 'balance_doge': str(bal),
                       'delta_24h_doge': str(day), 'delta_7d_doge': str(week),
                       'provider_label': row.get('label')})
    result.sort(key=lambda x: x['rank'])
    return {'observed_addresses': len(result), 'top10': result[:10],
            'top100_positive_balance_delta_24h_doge': str(sum((Decimal(x['delta_24h_doge']) for x in result if Decimal(x['delta_24h_doge']) > 0), Decimal(0))),
            'top100_negative_balance_delta_24h_doge': str(sum((Decimal(x['delta_24h_doge']) for x in result if Decimal(x['delta_24h_doge']) < 0), Decimal(0))),
            'note': 'Balance delta of the CURRENT largest addresses; not investor trades or exchange flow.'}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    errors, data, rejected = {}, {}, {}
    for name in (*CORE, *BUCKET_SERIES):
        try: data[name], rejected[name] = points(name, today)
        except Exception as exc: errors[name] = str(exc)[:300]
    whale = None
    try: whale = richlist()
    except Exception as exc: errors['richlist'] = str(exc)[:300]

    # Keep prior CSV schema intact; bucketed series are stored in separate JSON.
    csvpath = OUT / 'daily_activity.csv'
    prior = {}
    if csvpath.exists():
        with csvpath.open(newline='', encoding='utf-8') as f:
            rd = csv.DictReader(f)
            if rd.fieldnames != FIELDS: raise ValueError('CSV schema mismatch: aborting safely')
            for row in rd:
                day = row['date_utc']
                if day in prior: raise ValueError('duplicate UTC day in history: ' + day)
                prior[day] = row
    dates = sorted(set.intersection(*(set(data[k]) for k in CORE))) if all(k in data for k in CORE) else []
    invalid_counts={}
    for day in dates:
        try:
            vals = {k: nonnegative(data[k][day]) for k in CORE}
        except (ValueError, KeyError):
            invalid_counts[day] = 'core value invalid'; continue
        # Explicit empty cells for non-scalar transfer buckets; preserved in separate JSON.
        prior[day] = {'date_utc': day, **vals, **{k: '' for k in BUCKET_SERIES}}
    buf=StringIO()
    w=csv.DictWriter(buf,fieldnames=FIELDS)
    w.writeheader(); w.writerows(prior[k] for k in sorted(prior))
    write_atomic(csvpath,buf.getvalue())

    # Keep provider-native transfer value structures, without inventing an aggregate.
    bucket_path = OUT / 'transfer_buckets.json'
    old = {}
    if bucket_path.exists():
        try:
            existing = json.loads(bucket_path.read_text(encoding='utf-8'))
            if existing.get('schema_version') == 1 and isinstance(existing.get('days'), dict): old = existing['days']
        except (OSError, ValueError): errors['transfer_history_read'] = 'Unable to read previous transfer buckets'
    all_days = set().union(*(set(data[k]) for k in BUCKET_SERIES if k in data))
    for day in all_days:
        r = old.get(day, {})
        for key in BUCKET_SERIES:
            if day in data.get(key, {}): r[key] = data[key][day]
        old[day] = r
    write_atomic(bucket_path,json.dumps({
        'schema_version': 1, 'source':'Dogechain /chart/transfers and /chart/transfers_sent',
        'collected_at_utc':now.isoformat(), 'interpretation':'Provider bucket series, NOT scalar DOGE or trade volume; UTXO change excluded as provider defines',
        'days': {k:old[k] for k in sorted(old)}},ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
    latest = max((d for d in dates if d not in invalid_counts),default=None)
    ready = bool(latest and whale)
    if invalid_counts: errors['invalid_core_days'] = str(len(invalid_counts))+' daily core observations excluded'
    result = {
        'schema_version':3,'source':'Dogechain public API v3', 'collected_at_utc':now.isoformat(),
        'status':('ok' if ready and not errors else 'partial' if ready else 'unavailable'),
        'core_collection_success':ready, 'latest_complete_core_day_utc':latest,
        'daily_observations':len(prior),
        'daily_activity_latest':{k:prior[latest].get(k) or None for k in FIELDS[1:]} if latest else None,
        'transfer_bucket_history_days':len(old),
        'transfer_bucket_latest_day_utc':max(old) if old else None,
        'transfer_bucket_latest':old[max(old)] if old else None,
        'transfer_scalar_fields':'not applicable: values are structured buckets; see transfer_buckets.json',
        'richlist_balance_deltas':whale,'errors':errors,'skipped_malformed_points':rejected,
        'not_available':['verified_exchange_netflow','entity_level_whale_trades','continuous_full_network_large_transfer_feed'],
        'caveats':['Current Top100 addresses are not entities','Full exchange netflow requires validated address labels',
                   'Historical transfer bucket names and amounts must be interpreted using provider schema']}
    write_atomic(OUT/'metrics_latest.json',json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    print(json.dumps({'status':result['status'],'core_success':ready,'days':len(prior),
                      'bucket_days':len(old),'errors':errors},ensure_ascii=False))
    return 0 if ready else 1

if __name__=='__main__':
    raise SystemExit(main())

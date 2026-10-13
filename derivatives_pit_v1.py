"""Official DOGE swap OI/funding capture; raw replay and append-only receipts.

Hourly venue samples are not full-market positions, settled funding or proof of
predictive value. Settlement time may be future; availability starts at receipt.
"""
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
import evidence_append_guard
from factor_eligibility_v1 import utc

ROOT = Path(__file__).resolve().parent
BASE = 'data/derivatives_pit_v1'
LEDGER = BASE+'/observations.jsonl'
FAILURES = BASE+'/failures.jsonl'
URLS = {'OI': 'https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=DOGE-USDT-SWAP',
        'FUNDING': 'https://www.okx.com/api/v5/public/funding-rate?instId=DOGE-USDT-SWAP'}


def canon(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
def digest(value): return hashlib.sha256(canon(value).encode()).hexdigest()


def number(value, nonnegative=False):
    if isinstance(value, bool): raise ValueError('boolean amount')
    value = float(value)
    if not math.isfinite(value) or (nonnegative and value < 0): raise ValueError('invalid amount')
    return value


def parse(kind, raw, requested, seen):
    requested, seen = utc(requested), utc(seen)
    if not requested <= seen or kind not in URLS: raise ValueError('invalid request chronology or kind')
    obj = json.loads(raw)
    if obj.get('code') != '0' or len(obj.get('data', [])) != 1: raise ValueError('invalid OKX response')
    item = obj['data'][0]
    if item.get('instId') != 'DOGE-USDT-SWAP': raise ValueError('wrong instrument')
    stamp = datetime.fromtimestamp(int(item['ts'])/1000, timezone.utc)
    if not 0 <= (seen-stamp).total_seconds() <= 1800: raise ValueError('stale/future provider clock')
    row = {'kind': kind, 'feature_id': kind, 'source_url': URLS[kind],
           'instrument': item['instId'], 'provider_ts_utc': stamp.isoformat(),
           'requested_at_utc': requested.isoformat(), 'first_seen_utc': seen.isoformat(),
           'usable_from_utc': seen.isoformat(), 'source_delay_seconds': (seen-stamp).total_seconds(),
           'venue': 'OKX', 'full_market_coverage': False, 'production_allowed': False,
           'public_pre_target_witness_verified': False}
    if kind == 'OI':
        values = {key: number(item[source], True) for key, source in
                  (('contracts', 'oi'), ('coin', 'oiCcy'), ('usd', 'oiUsd'))}
        row.update(values, value=values['coin'], value_unit='DOGE',
                   interpretation='venue_open_positions_not_direction_or_net_new_money')
    else:
        settlement = datetime.fromtimestamp(int(item['fundingTime'])/1000, timezone.utc)
        next_settlement = datetime.fromtimestamp(int(item['nextFundingTime'])/1000, timezone.utc)
        if not seen < settlement < next_settlement: raise ValueError('invalid funding settlement schedule')
        if item.get('method') != 'current_period': raise ValueError('unknown funding rate method')
        row.update(value=number(item['fundingRate']), value_unit='fraction_per_current_period',
                   funding_time_utc=settlement.isoformat(), next_funding_time_utc=next_settlement.isoformat(),
                   interval_seconds=(next_settlement-settlement).total_seconds(),
                   rate_type='indicative_current_period', realized_funding_cost_verified=False)
    return row


def read(root=ROOT):
    path = root/LEDGER
    body = path.read_bytes() if path.exists() else b''
    evidence_append_guard.guard(root, LEDGER, body)
    rows, prior, ids = [], None, set()
    for line in body.decode('utf-8').splitlines():
        row = json.loads(line); check = dict(row); sha = check.pop('record_sha256')
        if sha != digest(check) or row['previous_sha256'] != prior or row['capture_id'] in ids:
            raise ValueError('derivative chain changed or duplicate capture')
        rawpath = BASE+'/raw/'+row['raw_sha256']+'.json'
        if row['raw_path'] != rawpath: raise ValueError('unsafe raw path')
        raw = (root/rawpath).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['raw_sha256']: raise ValueError('raw response changed')
        expected = parse(row['kind'], raw, row['requested_at_utc'], row['first_seen_utc'])
        if any(row.get(k) != v for k, v in expected.items()): raise ValueError('raw replay differs from normalized observation')
        expected_id = digest({'kind': row['kind'], 'seen': row['first_seen_utc'], 'raw': row['raw_sha256']})
        if row['capture_id'] != expected_id: raise ValueError('capture identity mismatch')
        ids.add(row['capture_id']); prior = sha; rows.append(row)
    return rows


def append(root, kind, raw, requested, seen):
    rows = read(root)
    row = parse(kind, raw, requested, seen)
    rawsha = hashlib.sha256(raw).hexdigest()
    identity = digest({'kind': kind, 'seen': row['first_seen_utc'], 'raw': rawsha})
    for old in rows:
        if old['capture_id'] == identity: return old
    row.update(raw_sha256=rawsha, raw_path=BASE+'/raw/'+rawsha+'.json', capture_id=identity,
               previous_sha256=rows[-1]['record_sha256'] if rows else None)
    target = root/row['raw_path']; target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != raw: raise ValueError('raw hash collision or tampering')
    else:
        with target.open('xb') as output: output.write(raw)
    row['record_sha256'] = digest(row)
    with (root/LEDGER).open('ab') as output: output.write((canon(row)+'\n').encode())
    return row


def read_failures(root=ROOT):
    path = root/FAILURES; body = path.read_bytes() if path.exists() else b''
    evidence_append_guard.guard(root, FAILURES, body)
    rows, previous = [], None
    for line in body.decode('utf-8').splitlines():
        row = json.loads(line); check = dict(row); sha = check.pop('record_sha256')
        if sha != digest(check) or row['previous_sha256'] != previous or row.get('research_allowed') is not False:
            raise ValueError('failed capture chain changed')
        if row.get('raw_path') is not None:
            expected = BASE+'/raw/'+row['raw_sha256']+'.json'
            if row['raw_path'] != expected or hashlib.sha256((root/expected).read_bytes()).hexdigest() != row['raw_sha256']:
                raise ValueError('failed raw capture changed')
        previous = sha; rows.append(row)
    return rows


def retain_failure(root, kind, raw, requested, seen, error, attempt_started):
    old = read_failures(root)
    row = {'kind': kind, 'source_url': URLS[kind], 'status': 'QUARANTINED',
           'research_allowed': False, 'error': type(error).__name__+': '+str(error)[:250],
           'attempt_started_at_utc': attempt_started.isoformat(),
           'failure_observed_at_utc': datetime.now(timezone.utc).isoformat(),
           'raw_path': None, 'raw_sha256': None,
           'previous_sha256': old[-1]['record_sha256'] if old else None}
    if raw is not None:
        sha = hashlib.sha256(raw).hexdigest(); path = BASE+'/raw/'+sha+'.json'
        target = root/path; target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != raw: raise ValueError('failed raw capture changed')
        else:
            with target.open('xb') as output: output.write(raw)
        row.update(raw_path=path, raw_sha256=sha, requested_at_utc=utc(requested).isoformat(),
                   received_at_utc=utc(seen).isoformat())
    row['record_sha256'] = digest(row)
    path = root/FAILURES; path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('ab') as output: output.write((canon(row)+'\n').encode())
    return row


def coverage(rows, kind, at):
    at = utc(at); boundary = at.replace(minute=0, second=0, microsecond=0)
    hours = set()
    for row in rows:
        seen = utc(row['first_seen_utc'])
        if row['kind'] == kind and boundary-timedelta(hours=24) <= seen < boundary:
            hours.add(seen.replace(minute=0, second=0, microsecond=0))
    return len(hours)/24


def fetch(kind):
    requested = datetime.now(timezone.utc)
    req = Request(URLS[kind], headers={'Accept': 'application/json', 'Cache-Control': 'no-cache',
                                      'User-Agent': 'DOGE-derivatives-PIT-v1'})
    with urlopen(req, timeout=18) as response: raw = response.read(1000001)
    if len(raw) > 1000000: raise ValueError('response exceeds size limit')
    return raw, requested, datetime.now(timezone.utc)


def collect(root=ROOT, fetch=fetch):
    read(root)  # Integrity failures must not be mistaken for optional source gaps.
    read_failures(root)
    report = {'schema_version': 1, 'sources': {}, 'production_model_changed': False,
              'predictive_gain_verified': False, 'public_pre_target_witness_verified': False}
    for kind in URLS:
        raw, requested, seen = None, None, None
        attempt_started = datetime.now(timezone.utc)
        try:
            raw, requested, seen = fetch(kind)
            row = append(root, kind, raw, requested, seen)
            report['sources'][kind] = {'status': 'CAPTURED_RAW_REPLAY_VERIFIED',
                                       'capture_id': row['capture_id'], 'first_seen_utc': row['first_seen_utc']}
        except Exception as error:
            failure = retain_failure(root, kind, raw, requested, seen, error, attempt_started)
            report['sources'][kind] = {'status': 'BLOCKED', 'error': failure['error'],
                                       'failure_record_sha256': failure['record_sha256']}
    rows = read(root); now = datetime.now(timezone.utc)
    count = sum(r['status'] == 'CAPTURED_RAW_REPLAY_VERIFIED' for r in report['sources'].values())
    report.update(status='OK' if count == 2 else 'PARTIAL' if count else 'BLOCKED',
                  captured_at_utc=now.isoformat(), ledger_rows=len(rows), quarantined_captures=len(read_failures(root)),
                  last24h_sample_coverage={k: coverage(rows, k, now) for k in URLS},
                  research_status='WAIT_COVERAGE_AND_INDEPENDENT_PRE_TARGET_WITNESS',
                  historical_pit_backfill_allowed=False)
    path = root/BASE/'latest.json'; path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp'); temporary.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)
    return report


if __name__ == '__main__':
    result = collect(); print(json.dumps(result))
    sys.exit(0 if result['status'] == 'OK' else 1)

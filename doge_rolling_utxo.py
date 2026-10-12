#!/usr/bin/env python3
"""Analyze CONFIRMED whale-candidate transactions against historical OKX PoR-address set.

NOT exchange-level netflow; no inference about ownership today, deposits, withdrawals,
market prediction or all-network completeness. Data is append-and-audit, never 'signals'.
"""
import csv
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

ROOT = Path.cwd()
SOURCE = ROOT / 'data/whales_blocks/events.csv'
POR = ROOT / 'okx_doge_addresses.csv'
OUT = ROOT / 'data/stage1/rolling_por_utxo_events.csv'
LATEST = ROOT / 'data/stage1/rolling_por_utxo_latest.json'
API = 'https://dogechain.com/api/v3'
FIELDS = [
    'txid', 'block_height', 'audited_at_utc', 'classification',
    'input_addresses_matched', 'output_addresses_matched',
    'por_input_amount_doge', 'por_output_amount_doge',
    'por_address_set_delta_doge', 'fee_doge', 'balance_reconciled', 'issues'
]
MAX_PER_RUN = 240
MAX_RUN_SECONDS = 390
MIN_REQUEST_INTERVAL = .68
_last_http = 0.0


def utc():
    return datetime.now(timezone.utc).isoformat()


def decimal(x):
    if x is None or isinstance(x, bool):
        raise ValueError('missing or boolean amount')
    try:
        v = Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('unparseable amount') from exc
    if not v.is_finite() or v < 0 or v > Decimal('1000000000000') or v.as_tuple().exponent < -8:
        raise ValueError('non-finite, out of range, or imprecise amount')
    return v


def valstr(v):
    return format(v, '.8f') if v is not None else ''


def getaddresses(obj):
    a = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ('address', 'addr') and isinstance(v, str) and v:
                a.add(v)
            elif k == 'addresses' and isinstance(v, list):
                a.update(x for x in v if isinstance(x, str) and x)
            elif isinstance(v, (dict, list)):
                a.update(getaddresses(v))
    elif isinstance(obj, list):
        for v in obj:
            a.update(getaddresses(v))
    return a


def amount_of(item, is_input):
    for k in ('value', 'value_doge', 'amount'):
        if k in item:
            return decimal(item[k])
    if is_input:
        for k in ('prevout', 'previous_output', 'prev_output'):
            if isinstance(item.get(k), dict):
                return amount_of(item[k], False)
    raise ValueError('amount field missing')


def classify(tx, allow):
    ins, outs = tx.get('inputs'), tx.get('outputs')
    if not isinstance(ins, list) or not isinstance(outs, list) or not ins or not outs:
        raise ValueError('missing input/output arrays')
    if ('inputs_n' in tx and int(tx['inputs_n']) != len(ins)) or ('outputs_n' in tx and int(tx['outputs_n']) != len(outs)):
        raise ValueError('input/output count mismatch')
    issues = []
    sums = {'input': Decimal(0), 'output': Decimal(0)}
    matched_sums = {'input': Decimal(0), 'output': Decimal(0)}
    matched_addrs = {'input': set(), 'output': set()}
    for name, items, is_input in [('input', ins, True), ('output', outs, False)]:
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(name + ' is not an object')
            addr = getaddresses(item)
            relevant = addr & allow
            matched_addrs[name].update(relevant)
            if len(addr) != 1:
                zero_nulldata = False
                if (name == 'output' and not addr and item.get('type') == 'nulldata'
                        and item.get('address') is None):
                    try:
                        zero_nulldata = amount_of(item, False) == Decimal(0)
                    except ValueError:
                        pass
                if not zero_nulldata:
                    issues.append(name + '_' + str(i) + '_ADDRESS_AMBIGUOUS')
            try:
                amount = amount_of(item, is_input)
                sums[name] += amount
                if relevant and len(addr) == 1:
                    matched_sums[name] += amount
                elif relevant:
                    issues.append(name + '_' + str(i) + '_MATCH_MULTIPLE_ADDRESSES')
            except ValueError:
                issues.append(name + '_' + str(i) + '_AMOUNT_UNVERIFIED')
    meta = {}
    for key in ('inputs_value', 'outputs_value', 'fee'):
        try:
            meta[key] = decimal(tx[key])
        except (KeyError, ValueError):
            issues.append(key + '_UNVERIFIED')
    if 'inputs_value' in meta and sums['input'] != meta['inputs_value']:
        issues.append('TOTAL_INPUTS_MISMATCH')
    if 'outputs_value' in meta and sums['output'] != meta['outputs_value']:
        issues.append('TOTAL_OUTPUTS_MISMATCH')
    if all(k in meta for k in ('inputs_value', 'outputs_value', 'fee')):
        if meta['inputs_value'] - meta['outputs_value'] != meta['fee']:
            issues.append('FEE_MISMATCH')
    pin = matched_addrs['input']
    pout = matched_addrs['output']
    if pin and pout: label = 'BOTH_SIDES_MIXED_OR_SELF_RETURN'
    elif pin: label = 'INPUT_ONLY_CANDIDATE'
    elif pout: label = 'OUTPUT_ONLY_CANDIDATE'
    else: label = 'NO_POR_MATCH'
    balanced = not issues
    return {
        'classification': label,
        'input_addresses_matched': len(pin), 'output_addresses_matched': len(pout),
        'por_input_amount_doge': valstr(matched_sums['input']) if balanced else '',
        'por_output_amount_doge': valstr(matched_sums['output']) if balanced else '',
        'por_address_set_delta_doge': valstr(matched_sums['output'] - matched_sums['input']) if balanced else '',
        'fee_doge': valstr(meta.get('fee')) if balanced else '',
        'balance_reconciled': str(balanced).lower(), 'issues': ';'.join(issues)
    }


def fetch(txid, deadline=None):
    global _last_http
    def remaining():
        left = float('inf') if deadline is None else deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError('request time budget exhausted')
        return left

    def pause(seconds):
        if seconds >= remaining():
            raise TimeoutError('request time budget exhausted')
        time.sleep(seconds)

    for i in range(4):
        remaining()
        try:
            wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_http)
            if wait > 0:
                pause(wait)
            _last_http = time.monotonic()
            req = urllib.request.Request(API + '/tx/' + txid,
                                         headers={'Accept': 'application/json', 'User-Agent': 'DOGE-rolling-PoR-UTXO-audit/1.0'})
            with urllib.request.urlopen(req, timeout=min(18, remaining())) as stream:
                j = json.load(stream, parse_float=Decimal)
            if j.get('status') != 'success' or not isinstance(j.get('data'), dict):
                raise ValueError('invalid API envelope')
            return j['data']
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or i == 3:
                raise
            try:
                retry = int(exc.headers.get('Retry-After', '3'))
            except (TypeError, ValueError):
                retry = 3
            pause(min(18, max(2, retry)))
        except (urllib.error.URLError, TimeoutError):
            if i == 3: raise
            pause(2 * (i + 1))


def read_rows(path, expected):
    if not path.exists(): return []
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != expected:
            raise ValueError('CSV schema mismatch: ' + str(path))
        return list(reader)


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(content, encoding='utf-8')
    os.replace(tmp, path)


def save(records, summary):
    # Data and diagnostics are staged together in the same Git commit.
    s = StringIO()
    writer = csv.DictWriter(s, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(records[k] for k in sorted(records, key=lambda z: (int(records[z]['block_height']), z)))
    atomic(OUT, s.getvalue())
    atomic(LATEST, json.dumps(summary, ensure_ascii=False, indent=2) + '\n')


def run():
    begin = time.monotonic()
    summary = {
        'schema_version': 1, 'status': 'error', 'generated_at_utc': utc(),
        'source': 'Dogechain public /tx API and whale-scanner confirmed events; OKX PoR 2026-09-08 disclosed addresses',
        'coverage': 'ONLY confirmed whale-scanner events since scanner checkpoint, not all DOGE transactions',
        'verified_exchange_netflow': False, 'current_exchange_address_ownership_verified': False,
        'results_not_for_trading_signals': True,
        'trigger_event': os.getenv('GITHUB_EVENT_NAME'),
        'audit_workflow_run_id': os.getenv('GITHUB_RUN_ID'),
        'upstream_whale_run_id': os.getenv('UPSTREAM_WHALE_RUN_ID') or None,
        'upstream_whale_run_event': os.getenv('UPSTREAM_WHALE_RUN_EVENT') or None,
        'upstream_whale_run_conclusion': os.getenv('UPSTREAM_WHALE_RUN_CONCLUSION') or None,
        'upstream_whale_run_created_at_utc': os.getenv('UPSTREAM_WHALE_RUN_CREATED_AT') or None,
        'errors': []
    }
    rows = {}
    try:
        if not POR.exists() or not SOURCE.exists():
            raise ValueError('Required PoR CSV or whale events CSV missing')
        source_hash = hashlib.sha256(POR.read_bytes()).hexdigest()
        with POR.open(newline='', encoding='utf-8-sig') as stream:
            porrows = list(csv.DictReader(stream))
        if not {'address', 'network'}.issubset(porrows[0].keys() if porrows else set()):
            raise ValueError('PoR address CSV schema mismatch')
        allowed_list = [x['address'] for x in porrows if x['network'] == 'DOGE']
        if len(allowed_list) != 40134 or len(set(allowed_list)) != 40134:
            raise ValueError('PoR address allowlist size or duplicates invalid')
        allow = set(allowed_list)
        whale_rows = read_rows(SOURCE, ['txid','block_height','amount_doge','headline','observed_at_utc','verification'])
        events = {}
        for x in whale_rows:
            txid = x['txid'].lower()
            if not re.fullmatch('[0-9a-f]{64}', txid) or txid in events or int(x['block_height']) < 1:
                raise ValueError('duplicate/malformed source txid or height')
            if 'confirmed-depth>=6' not in x['verification']:
                raise ValueError('whale event lacks 6-confirmation source marker')
            events[txid] = x
        if OUT.exists():
            for x in read_rows(OUT, FIELDS):
                txid = x['txid']
                if txid in rows or txid not in events or x['block_height'] != events[txid]['block_height']:
                    raise ValueError('inconsistent, orphan, or duplicate audited txid')
                rows[txid] = x
        old = {}
        if LATEST.exists():
            old = json.loads(LATEST.read_text(encoding='utf-8'))
            if rows and not old.get('por_source_sha256'):
                raise ValueError('Existing audit rows lack immutable PoR source version')
            if old.get('por_source_sha256') and old['por_source_sha256'] != source_hash:
                raise ValueError('PoR source changed; historical audit requires explicit rebaseline')
        elif rows:
            raise ValueError('Existing audit rows without source-version manifest')
        ordered = sorted(events, key=lambda x: (int(events[x]['block_height']), x))
        cursor = old.get('last_attempted_txid')
        if cursor in events:
            offset = ordered.index(cursor) + 1
            ordered = ordered[offset:] + ordered[:offset]
        pending = [x for x in ordered if x not in rows or rows[x]['balance_reconciled'] != 'true']
        added = 0
        attempted = 0
        deadline = begin + MAX_RUN_SECONDS
        stop_reason = 'sample_complete'
        for txid in pending[:MAX_PER_RUN]:
            if deadline - time.monotonic() < 20:
                stop_reason = 'time_budget'
                break
            source = events[txid]
            attempted += 1
            cursor = txid
            try:
                obj = fetch(txid, deadline=min(deadline, time.monotonic() + 45))
                tx = obj.get('transaction')
                if not isinstance(tx, dict):
                    raise ValueError('transaction schema missing')
                identity = [tx[k] for k in ('hash', 'txid') if k in tx]
                if not identity or any(value != txid for value in identity):
                    raise ValueError('returned transaction ID mismatch / missing')
                if 'block_height' not in tx or int(tx['block_height']) != int(source['block_height']):
                    raise ValueError('block height mismatch / missing')
                if 'confirmations' not in tx or int(tx['confirmations']) < 6:
                    raise ValueError('confirmation depth below 6')
                result = classify(tx, allow)
                rows[txid] = {'txid': txid, 'block_height': str(source['block_height']),
                              'audited_at_utc': utc(), **result}
                added += 1
            except Exception as exc:
                # Preserve successes, leave this ID pending, and try the next ID.
                summary['errors'].append(txid + ': ' + type(exc).__name__ + ': ' + str(exc)[:200])
        reconciled = sum(x['balance_reconciled'] == 'true' for x in rows.values())
        unfinished = len(events) - reconciled
        if unfinished and stop_reason == 'sample_complete':
            stop_reason = 'batch_limit' if attempted == MAX_PER_RUN else 'retry_pending'
        summary['status'] = 'ok_sample_complete' if not unfinished else 'partial_backlog'
        summary.update({'por_source_sha256': source_hash, 'por_address_count': len(allow),
                        'source_whale_events': len(events), 'audited_unique_events': len(rows),
                        'events_pending': len(events) - len(rows), 'analyzed_this_run': added,
                        'events_pending_reconciliation': unfinished,
                        'attempted_this_run': attempted, 'stop_reason': stop_reason,
                        'last_attempted_txid': cursor,
                        'max_per_run': MAX_PER_RUN, 'time_budget_seconds': MAX_RUN_SECONDS,
                        'source_latest_block_height': max((int(x['block_height']) for x in events.values()), default=None),
                        'candidate_classifications': dict(Counter(x['classification'] for x in rows.values())),
                        'full_utxo_reconciled_events': reconciled,
                        'ambiguous_utxo_events': sum(x['balance_reconciled']!='true' for x in rows.values()),
                        'source_has_no_comprehensive_exchange_coverage': True})
        if len(rows) == len(events) and all(x['balance_reconciled'] == 'true' for x in rows.values()):
            summary['observed_historical_por_address_set_delta_doge_covered_whale_events_only'] = valstr(
                sum((Decimal(x['por_address_set_delta_doge']) for x in rows.values()), Decimal(0)))
        else:
            summary['observed_historical_por_address_set_delta_doge_covered_whale_events_only'] = None
    except Exception as exc:
        summary['status'] = 'error'
        summary['errors'].append(type(exc).__name__ + ': ' + str(exc)[:320])
        summary['audited_unique_events'] = len(rows)
        summary['observed_historical_por_address_set_delta_doge_covered_whale_events_only'] = None
    summary['generated_at_utc'] = utc()
    summary['limitations'] = [
        'Historical PoR address signatures and current ownership are unverified',
        'UTXO change, consolidated holdings and unlabeled wallets prevent verified exchange deposits/withdrawals',
        'Only whale-scanner-selected >=1m DOGE explain.sent candidates; not all transactions nor representative',
        'Signed address-set delta is NOT verified OKX entity netflow, cannot be used as a trading predictor',
        'Aggregated delta is withheld if any stored transaction is unbalanced or pending',
        'TXIDs are deduplicated; transactions with missing/ambiguous accounting retain null deltas'
    ]
    if summary['status'] == 'error':
        # Do not modify historical rows or lose the immutable PoR version after a failure.
        if LATEST.exists():
            try:
                previous = json.loads(LATEST.read_text(encoding='utf-8'))
                if previous.get('por_source_sha256'):
                    summary['por_source_sha256'] = previous['por_source_sha256']
            except (ValueError, OSError, TypeError):
                pass
        atomic(LATEST, json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    else:
        save(rows, summary)
    print(json.dumps({k: summary.get(k) for k in ('status','source_whale_events','audited_unique_events','events_pending','analyzed_this_run','errors')}), flush=True)
    return 1 if (summary['status'] == 'error' or summary['errors']
                 or summary.get('ambiguous_utxo_events', 0)) else 0


if __name__ == '__main__':
    raise SystemExit(run())

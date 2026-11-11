#!/usr/bin/env python3
"""Resumable, contiguous confirmed DOGE scanning from the existing checkpoint.

Every transaction is verified before its block advances coverage. Missing state
fails closed; this maintenance worker never seeds a replacement near the tip.
"""
import csv
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

API = 'https://dogechain.com/api/v3'
OUT = Path(__file__).resolve().parent / 'data' / 'whales_blocks'
THRESHOLD = Decimal('1000000')
CONFIRMATIONS = 6
MAX_BLOCKS = max(1, min(int(os.getenv('DOGE_BLOCK_MAX', '3')), 100))
MIN_REQUEST_INTERVAL = 0.65  # One global start interval, unchanged: <= 93/minute.
RUN_SECONDS = max(60, min(int(os.getenv('DOGE_BLOCK_RUN_SECONDS', '600')), 780))
_last_request_time = 0.0
_retry_not_before = 0.0
FIELDS = ['txid','block_height','amount_doge','headline','observed_at_utc','verification']


class BudgetExhausted(TimeoutError):
    """Local work deadline only; a provider/network failure is never this class."""


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def request(path, *, attempts=4, timeout=20, deadline=None):
    global _last_request_time, _retry_not_before
    url = API + path
    provider_error = None
    for attempt in range(attempts):
        now = time.monotonic()
        wait = max(0, MIN_REQUEST_INTERVAL - (now - _last_request_time),
                   _retry_not_before - now)
        remaining = None if deadline is None else deadline - now - wait
        if remaining is not None and remaining < timeout:
            if provider_error is not None:
                raise provider_error
            raise BudgetExhausted('full request slot unavailable: ' + path)
        if wait:
            time.sleep(wait)
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining < timeout:
            if provider_error is not None:
                raise provider_error
            raise BudgetExhausted('full request slot unavailable after wait: ' + path)
        _last_request_time = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={'Accept':'application/json', 'User-Agent':'DOGE-block-scanner/1.0'})
            with urllib.request.urlopen(req, timeout=timeout if remaining is None else min(timeout, remaining)) as response:
                obj = json.load(response)
            if not isinstance(obj,dict) or obj.get('status') != 'success' or not isinstance(obj.get('data'),dict):
                raise ValueError('invalid API envelope: ' + path)
            if deadline is not None and time.monotonic() >= deadline:
                raise BudgetExhausted('complete successful response exceeded local budget: ' + path)
            return obj['data']
        except BudgetExhausted:
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            provider_error = exc
            if isinstance(exc, urllib.error.HTTPError):
                if exc.code not in (429, 500, 502, 503, 504):
                    raise
                if exc.code == 429:
                    try:
                        delay = max(2, int(exc.headers.get('Retry-After', '4')))
                    except (TypeError, ValueError):
                        delay = 4
                    _retry_not_before = max(_retry_not_before, time.monotonic() + delay)
                else:
                    delay = 2 * (attempt + 1)
            else:
                delay = 2 * (attempt + 1)
            if attempt == attempts - 1:
                raise
            if deadline is not None and time.monotonic() + delay + MIN_REQUEST_INTERVAL + timeout > deadline:
                # The budget cannot erase a real provider failure or its status.
                raise
            time.sleep(delay)


def atomic(path, text):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text, encoding='utf-8')
    os.replace(temp, path)


def valid_hash(value):
    return (isinstance(value, str) and len(value) == 64 and
            all(c in '0123456789abcdefABCDEF' for c in value))


def get_tip(**kwargs):
    data = request('/network', **kwargs)
    info = data.get('info') or {}
    n = info.get('block_count')
    if type(n) is not int or n <= CONFIRMATIONS:
        raise ValueError('network.info.block_count invalid')
    return n


def parse_block(height, **kwargs):
    data = request('/block/' + str(height), **kwargs)
    block = data.get('block', data)
    if not isinstance(block, dict):
        raise ValueError('block payload is not an object')
    if type(block.get('height')) is not int or block['height'] != height:
        raise ValueError('returned height absent or mismatched')
    if not valid_hash(block.get('hash')):
        raise ValueError('block hash invalid')
    previous = block.get('previous_block_hash')
    if (height and not valid_hash(previous)) or (not height and previous is not None):
        raise ValueError('previous block hash invalid')
    if block.get('is_orphan') is not False:
        raise ValueError('block orphan status invalid')
    if type(block.get('confirmations')) is not int or block['confirmations'] < CONFIRMATIONS:
        raise ValueError('block insufficiently confirmed')
    if type(block.get('time')) is not int or block['time'] <= 0:
        raise ValueError('block source time invalid')
    txs = block.get('transactions', block.get('txs'))
    if not isinstance(txs, list) or not txs:
        raise ValueError('complete block transactions list absent')
    if type(block.get('num_txs')) is not int or block['num_txs'] != len(txs):
        raise ValueError('block transaction count mismatch')
    txids = []
    for tx in txs:
        candidate = tx if isinstance(tx, str) else (tx.get('txid') or tx.get('hash')) if isinstance(tx, dict) else None
        if not valid_hash(candidate):
            raise ValueError('invalid txid in block')
        txids.append(candidate.lower())
    if len(txids) != len(set(txids)):
        raise ValueError('duplicate transaction IDs in block')
    return {**block, 'hash': block['hash'].lower(),
            'previous_block_hash': previous.lower() if previous else None,
            'txids': txids}


def explain(txid, block, **kwargs):
    details = request('/tx/' + txid, **kwargs)
    tx = details.get('transaction')
    if not isinstance(tx, dict):
        raise ValueError('transaction identity absent')
    if (not valid_hash(tx.get('hash')) or tx['hash'].lower() != txid or
            type(tx.get('block_height')) is not int or tx['block_height'] != block['height'] or
            not valid_hash(tx.get('block_hash')) or tx['block_hash'].lower() != block['hash'] or
            type(tx.get('confirmations')) is not int or tx['confirmations'] < CONFIRMATIONS or
            type(tx.get('time')) is not int or tx['time'] != block['time']):
        raise ValueError('transaction does not match confirmed block')
    for key in ('inputs', 'outputs'):
        values = tx.get(key)
        if (not isinstance(values, list) or not values or
                type(tx.get(key + '_n')) is not int or tx[key + '_n'] != len(values)):
            raise ValueError('transaction ' + key + ' incomplete')
    exp = details.get('explain')
    if not isinstance(exp, dict) or exp.get('sent') is None:
        raise ValueError('explain.sent missing')
    try:
        amount = Decimal(str(exp['sent']))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('invalid explain.sent') from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError('bad amount')
    return amount, str(exp.get('headline') or '')


def read_events():
    dest = OUT/'events.csv'
    rows = {}
    if dest.exists():
        with dest.open(newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != FIELDS:
                raise ValueError('events CSV schema mismatch')
            for row in reader:
                if row['txid'] in rows:
                    raise ValueError('duplicate stored txid')
                rows[row['txid']] = row
    return rows


def save_events(rows):
    s = StringIO()
    w = csv.DictWriter(s, fieldnames=FIELDS)
    w.writeheader()
    w.writerows(rows[k] for k in sorted(rows))
    atomic(OUT/'events.csv', s.getvalue())


def identity(txid, block):
    return {'txid': txid, 'block_height': block['height'],
            'block_hash': block['hash'], 'block_time': block['time']}


def observation_amount(observation):
    amount = Decimal(observation['amount_doge'])
    if not amount.is_finite() or amount < 0:
        raise ValueError('invalid saved amount')
    stamp = datetime.fromisoformat(observation['observed_at_utc'].replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError('saved observation must be UTC')
    return amount


def progress_for(path, block, last):
    progress = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if progress and progress.get('height') != block['height']:
        # A crash after a durable checkpoint may leave the completed block's
        # pending file. Only that already committed block may be superseded.
        if type(progress.get('height')) is not int or progress['height'] != last:
            raise ValueError('pending height differs from checkpoint; preserved')
        progress = {}
    if progress:
        if (progress.get('txids') != block['txids'] or
                progress.get('block_hash', block['hash']) != block['hash']):
            raise ValueError('pending block identity/list changed; preserved')
        if (not isinstance(progress.get('verified'), dict) or
                set(progress['verified']) - set(block['txids'])):
            raise ValueError('pending verified records do not match block; preserved')
    else:
        progress = {'height': block['height'], 'txids': block['txids'], 'verified': {}}
    return {**progress, 'schema_version': 2, 'block_hash': block['hash']}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    start = timestamp()
    begin_monotonic = time.monotonic()
    scan_deadline = begin_monotonic + RUN_SECONDS - 120
    end_tip_deadline = begin_monotonic + RUN_SECONDS - 60
    errors = []
    block_count = tx_count = new = 0
    stop_reason = 'batch_complete'
    incomplete_block = None
    scanning_height = None
    in_tx_loop = False
    rows = {}
    state_path = OUT/'checkpoint.json'
    tip_start = target_start = current = last = None
    last_block = None
    try:
        rows = read_events()
        state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        last = state.get('last_completed_height')
        if type(last) is not int or last < 0:
            raise ValueError('existing checkpoint required; replacement seeding disabled')
        tip_start = get_tip(deadline=scan_deadline)
        target_start = tip_start - CONFIRMATIONS
        if last > target_start:
            raise ValueError('checkpoint is beyond current confirmed tip')
        last_block = parse_block(last, deadline=scan_deadline)
        if state.get('last_completed_block_hash', last_block['hash']) != last_block['hash']:
            raise ValueError('completed checkpoint block hash changed')
        current = last + 1
        final = min(target_start, current + MAX_BLOCKS - 1)
        for height in range(current, final + 1):
            if time.monotonic() + 20 + MIN_REQUEST_INTERVAL > scan_deadline:
                stop_reason = 'time_budget_before_block'
                break
            scanning_height = height
            in_tx_loop = False
            block = parse_block(height, deadline=scan_deadline)
            if block['previous_block_hash'] != last_block['hash']:
                raise ValueError('block does not extend completed checkpoint')
            txids = block['txids']
            progress_path = OUT/'pending_block.json'
            progress = progress_for(progress_path, block, last)
            verified = progress['verified']
            new_rows = {}
            block_complete = True
            in_tx_loop = True
            for txid in txids:
                saved = verified.get(txid)
                needs_request = saved is None or 'identity' not in saved
                if (time.monotonic() >= scan_deadline or
                        (needs_request and time.monotonic() + 20 + MIN_REQUEST_INTERVAL > scan_deadline)):
                    block_complete = False
                    incomplete_block = height
                    stop_reason = 'time_budget_in_block'
                    break
                tx_count += 1
                stored = rows.get(txid)
                if stored is not None and int(stored['block_height']) != height:
                    raise ValueError('stored txid belongs to another block')
                observation = verified.get(txid)
                if observation is not None:
                    amount = observation_amount(observation)
                    expected = identity(txid, block)
                    if 'identity' in observation and observation['identity'] != expected:
                        raise ValueError('pending transaction identity changed; preserved')
                    if 'identity' not in observation:
                        # Legacy cache keeps its value and first-seen clock. The
                        # new identity witness has its own later verification time.
                        checked, _ = explain(txid, block, deadline=scan_deadline)
                        if checked != amount:
                            raise ValueError('cached transaction amount differs; preserved')
                        observation = {**observation, 'identity': expected,
                                       'identity_verified_at_utc': timestamp()}
                        verified[txid] = observation
                        atomic(progress_path, json.dumps(progress, ensure_ascii=False, indent=2) + '\n')
                else:
                    amount, headline = explain(txid, block, deadline=scan_deadline)
                    first_seen = timestamp()
                    observation = {'amount_doge': str(amount), 'headline': headline,
                                   'observed_at_utc': first_seen, 'identity': identity(txid, block),
                                   'identity_verified_at_utc': first_seen}
                    verified[txid] = observation
                    atomic(progress_path, json.dumps(progress, ensure_ascii=False, indent=2) + '\n')
                if stored is not None:
                    if observation_amount(stored) != amount:
                        raise ValueError('stored transaction amount differs; preserved')
                    continue
                if amount >= THRESHOLD:
                    new_rows[txid] = {'txid': txid, 'block_height': height, 'amount_doge': str(amount),
                                     'headline': observation['headline'],
                                     'observed_at_utc': observation['observed_at_utc'],
                                     'verification': 'confirmed-depth>=6; provider explain.sent; entity unverified'}
            if not block_complete:
                break
            merged = {**rows, **new_rows}
            save_events(merged)
            rows = merged
            next_state = {**state, 'schema_version': 1, 'last_completed_height': height,
                          'last_completed_block_hash': block['hash'],
                          'updated_at_utc': timestamp(), 'confirmed_tip_at_run': target_start}
            atomic(state_path, json.dumps(next_state, indent=2) + '\n')
            # Advance the in-memory coverage only after the marker is durable.
            state = next_state
            last = height
            last_block = block
            new += len(new_rows)
            block_count += 1
            progress_path.unlink(missing_ok=True)
            scanning_height = None
            in_tx_loop = False
    except BudgetExhausted:
        stop_reason = 'time_budget_in_block' if in_tx_loop else 'time_budget_before_block'
        incomplete_block = scanning_height if in_tx_loop else None
    except Exception as exc:
        stop_reason = 'error'
        errors.append(type(exc).__name__ + ': ' + str(exc)[:300])

    tip_end = target_end = tip_observed = None
    try:
        # One short, deadline-bounded read leaves time for diagnostics and Git
        # publication. Failure must not reuse an old zero backlog.
        tip_end = get_tip(attempts=1, timeout=10, deadline=end_tip_deadline)
        tip_observed = timestamp()
        target_end = tip_end - CONFIRMATIONS
        if (tip_start is not None and tip_end < tip_start) or (last is not None and last > target_end):
            raise ValueError('run-end confirmed tip regressed below scan state')
    except Exception as exc:
        tip_end = target_end = tip_observed = None
        stop_reason = 'error'
        errors.append('run_end_tip: ' + type(exc).__name__ + ': ' + str(exc)[:250])
    pending = target_end - last if target_end is not None and last is not None else None
    caught_up = pending == 0 and not errors
    status = 'error' if errors else ('ok' if block_count > 0 or caught_up else 'idle')
    summary = {'schema_version': 4, 'status': status,
               'source': 'Dogechain /network /block/{height} /tx/{txid}',
               'started_at_utc': start, 'ended_at_utc': timestamp(), 'tip_height': tip_end,
               'tip_height_at_start': tip_start, 'confirmation_depth': CONFIRMATIONS,
               'start_height': current, 'last_completed_height': last,
               'last_completed_block_hash': last_block['hash'] if last_block else None,
               'last_completed_block_time_utc': datetime.fromtimestamp(last_block['time'], timezone.utc).isoformat() if last_block else None,
               'confirmed_tip_at_run': target_end, 'confirmed_tip_at_run_start': target_start,
               'run_end_tip_verified': target_end is not None,
               'run_end_tip_observed_at_utc': tip_observed,
               'backlog_blocks_at_run_end': pending,
               'backlog_as_of': 'fresh run-end confirmed tip; unavailable when readback fails',
               'scan_stop_reason': stop_reason, 'incomplete_block_not_committed': incomplete_block,
               'elapsed_seconds': round(time.monotonic() - begin_monotonic, 2),
               'max_blocks_per_run': MAX_BLOCKS, 'request_min_interval_s': MIN_REQUEST_INTERVAL,
               'run_time_budget_s': RUN_SECONDS, 'blocks_completed_this_run': block_count,
               'transactions_verified_this_run': tx_count,
               'transaction_counter_note': 'Includes saved identity cache replay; not HTTP request count',
               'new_whale_transactions': new, 'cumulative_unique_whale_transactions': len(rows),
               'threshold_doge': str(THRESHOLD), 'errors': errors,
               'coverage': 'Only contiguous heights after checkpoint; earlier heights are NOT covered unless explicitly backfilled',
               'limits': ['exchange netflow is unverified',
                          'Legacy event observed_at is its first amount observation, not later identity verification; identity verification timestamps are held in pending until block completion',
                          'single-provider explain.sent may not represent a unique economic entity',
                          'six confirmations reduce but do not eliminate reorganizations',
                          'source availability and API rate limits can delay catch-up']}
    atomic(OUT/'latest.json', json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': status, 'blocks': block_count, 'last': last, 'errors': errors}, ensure_ascii=False), flush=True)
    return 0 if not errors else 1


if __name__ == '__main__':
    raise SystemExit(main())

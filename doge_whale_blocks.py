#!/usr/bin/env python3
"""Auditable confirmed DOGE block scanner; independent of SSE sampler.

Rate-limited resumable block scanner with lag monitoring; zero fabricated trades.
An explicit seed height can be provided with DOGE_BLOCK_START_HEIGHT.
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
MIN_REQUEST_INTERVAL = 0.65  # global request budget: <= 93 per minute
RUN_SECONDS = max(60, min(int(os.getenv('DOGE_BLOCK_RUN_SECONDS', '600')), 780))
_last_request_time = 0.0
FIELDS = ['txid','block_height','amount_doge','headline','observed_at_utc','verification']

def timestamp():
    return datetime.now(timezone.utc).isoformat()

def request(path):
    global _last_request_time
    url = API + path
    for attempt in range(4):
        try:
            wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_time)
            if wait > 0: time.sleep(wait)
            _last_request_time = time.monotonic()
            req = urllib.request.Request(url, headers={'Accept':'application/json', 'User-Agent':'DOGE-block-scanner/1.0'})
            with urllib.request.urlopen(req, timeout=20) as response:
                obj = json.load(response)
            if not isinstance(obj,dict) or obj.get('status') != 'success' or not isinstance(obj.get('data'),dict):
                raise ValueError('invalid API envelope: ' + path)
            return obj['data']
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3: raise
            if exc.code == 429:
                time.sleep(max(2, min(int(exc.headers.get('Retry-After', '4')), 30)))
            else:
                time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError,TimeoutError) as exc:
            if attempt == 3: raise
            time.sleep(2 * (attempt + 1))

def atomic(path, text):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text, encoding='utf-8')
    os.replace(temp, path)

def get_tip():
    data = request('/network')
    info = data.get('info') or {}
    n = info.get('block_count')
    if not isinstance(n,int) or n <= CONFIRMATIONS:
        raise ValueError('network.info.block_count invalid')
    return n

def parse_block(height):
    data = request('/block/' + str(height))
    block = data.get('block',data)
    if not isinstance(block,dict): raise ValueError('block payload is not an object')
    reported = block.get('height', block.get('block_height', block.get('block_count')))
    if reported is not None and int(reported) != height:
        raise ValueError('returned height mismatch')
    txs = block.get('transactions',block.get('txs'))
    if not isinstance(txs,list):
        raise ValueError('block transactions list absent: need schema review')
    txids = []
    for tx in txs:
        candidate = tx if isinstance(tx,str) else (tx.get('txid') or tx.get('hash')) if isinstance(tx,dict) else None
        if not isinstance(candidate,str) or len(candidate)!=64 or any(c not in '0123456789abcdefABCDEF' for c in candidate):
            raise ValueError('invalid txid in block')
        txids.append(candidate.lower())
    if len(txids)!=len(set(txids)):
        raise ValueError('duplicate transaction IDs in block')
    return txids

def explain(txid):
    details = request('/tx/'+txid)
    exp = details.get('explain')
    if not isinstance(exp,dict) or exp.get('sent') is None: raise ValueError('explain.sent missing')
    try: amount = Decimal(str(exp['sent']))
    except (InvalidOperation,ValueError,TypeError): raise ValueError('invalid explain.sent')
    if not amount.is_finite() or amount < 0: raise ValueError('bad amount')
    return amount, str(exp.get('headline') or '')

def read_events():
    dest=OUT/'events.csv'
    rows={}
    if dest.exists():
        with dest.open(newline='',encoding='utf-8') as f:
            reader=csv.DictReader(f)
            if reader.fieldnames!=FIELDS: raise ValueError('events CSV schema mismatch')
            for row in reader:
                if row['txid'] in rows: raise ValueError('duplicate stored txid')
                rows[row['txid']]=row
    return rows

def save_events(rows):
    s=StringIO(); w=csv.DictWriter(s,fieldnames=FIELDS); w.writeheader()
    w.writerows(rows[k] for k in sorted(rows))
    atomic(OUT/'events.csv',s.getvalue())

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    start=timestamp(); begin_monotonic=time.monotonic(); errors=[]; block_count=0; tx_count=0; candidates=0; new=0; stop_reason='batch_complete'; incomplete_block=None
    rows=read_events()
    state_path=OUT/'checkpoint.json'
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    tip=None; target=None; current=None; last=state.get('last_completed_height')
    try:
        tip=get_tip(); target=tip-CONFIRMATIONS
        if last is None:
            initial=os.getenv('DOGE_BLOCK_START_HEIGHT')
            pending_path=OUT/'pending_block.json'
            pending=json.loads(pending_path.read_text(encoding='utf-8')) if pending_path.exists() else {}
            current=int(pending['height']) if pending else (int(initial) if initial else max(1,target-MAX_BLOCKS+1))
            if current<1: raise ValueError('initial height must be positive')
            if current>target: raise ValueError('initial height beyond confirmed tip')
            last=current-1
        else: current=int(last)+1
        final=min(target,current+MAX_BLOCKS-1)
        for height in range(current,final+1):
            if time.monotonic() - begin_monotonic >= RUN_SECONDS - 120:
                stop_reason='time_budget_before_block'; break
            txids=parse_block(height)
            # Bind resumable observations to the complete provider transaction list.
            # Pending rows are not coverage and never enter events until the block completes.
            progress_path=OUT/'pending_block.json'
            progress=json.loads(progress_path.read_text(encoding='utf-8')) if progress_path.exists() else {}
            if progress.get('height') != height or progress.get('txids') != txids:
                progress={'schema_version':1,'height':height,'txids':txids,'verified':{}}
            verified=progress['verified']
            # Fail the block and do not advance checkpoint if any tx cannot be verified.
            new_rows={}
            block_complete=True
            for txid in txids:
                # Stop safely before a transaction request that could exceed the job budget (120 s safety margin).
                # Do not persist any incomplete block or advance its checkpoint.
                if time.monotonic() - begin_monotonic >= RUN_SECONDS - 120:
                    block_complete=False
                    incomplete_block=height
                    stop_reason='time_budget_in_block'
                    break
                tx_count+=1
                if txid in rows: continue
                if txid in verified:
                    observation=verified[txid]
                    amount=Decimal(observation['amount_doge'])
                    if not amount.is_finite() or amount<0: raise ValueError('invalid pending amount')
                    headline=observation['headline']
                else:
                    amount,headline=explain(txid)
                    observation={'amount_doge':str(amount),'headline':headline,'observed_at_utc':timestamp()}
                    verified[txid]=observation
                    atomic(progress_path,json.dumps(progress,ensure_ascii=False,indent=2)+'\n')
                if amount >= THRESHOLD:
                    candidates+=1
                    new_rows[txid]={'txid':txid,'block_height':height,'amount_doge':str(amount),
                                     'headline':headline,'observed_at_utc':observation['observed_at_utc'],
                                     'verification':'confirmed-depth>=6; provider explain.sent; entity unverified'}
            if not block_complete:
                break
            rows.update(new_rows); new+=len(new_rows)
            save_events(rows)
            last=height
            atomic(state_path,json.dumps({'schema_version':1,'last_completed_height':last,
                                          'updated_at_utc':timestamp(), 'confirmed_tip_at_run':target},indent=2)+'\n')
            block_count+=1
            progress_path.unlink(missing_ok=True)
    except Exception as exc:
        stop_reason='error'
        errors.append(type(exc).__name__+': '+str(exc)[:300])
    status='ok' if not errors and block_count>0 else ('idle' if not errors else 'error')
    pending=max(0,target-last) if target is not None and last is not None else None
    summary={'schema_version':3,'status':status,'source':'Dogechain /network /block/{height} /tx/{txid}',
             'started_at_utc':start,'ended_at_utc':timestamp(),'tip_height':tip,
             'confirmation_depth':CONFIRMATIONS,'start_height':current,
             'last_completed_height':last,'confirmed_tip_at_run':target,
             'backlog_blocks_at_run_end':pending,
             'backlog_as_of':'confirmed_tip_at_run (not refreshed at run end)',
             'scan_stop_reason':stop_reason, 'incomplete_block_not_committed':incomplete_block,
             'elapsed_seconds':round(time.monotonic()-begin_monotonic,2),
             'max_blocks_per_run':MAX_BLOCKS,'request_min_interval_s':MIN_REQUEST_INTERVAL,
             'run_time_budget_s':RUN_SECONDS,'blocks_completed_this_run':block_count,
             'transactions_verified_this_run':tx_count, 'new_whale_transactions':new,
             'cumulative_unique_whale_transactions':len(rows),'threshold_doge':str(THRESHOLD),
             'errors':errors,'coverage':'Only contiguous heights after checkpoint; earlier heights are NOT covered unless explicitly backfilled',
             'limits':['exchange netflow is unverified','single-provider explain.sent may not represent a unique economic entity',
                       'six confirmations reduce but do not eliminate reorganizations','source availability and API rate limits can delay catch-up']}
    atomic(OUT/'latest.json',json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':status,'blocks':block_count,'last':last,'errors':errors},ensure_ascii=False),flush=True)
    return 0 if not errors else 1

if __name__=='__main__':
    raise SystemExit(main())

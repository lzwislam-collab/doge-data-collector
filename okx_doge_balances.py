#!/usr/bin/env python3
"""Free, partial-coverage OKX PoR disclosed DOGE address balance sampler.
Not exchange deposit/withdrawal/netflow and not a simultaneous reserve snapshot.
"""
import csv
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

HOME = Path(__file__).resolve().parent
BASE = HOME / 'data' / 'okx_address_balances'
SOURCE = HOME / 'okx_doge_addresses.csv'
AUDIT = HOME / 'audit.json'
API = 'https://dogechain.com/api/v3'
MAX = max(1, min(int(os.getenv('BALANCE_BATCH_SIZE', '75')), 90))
DELAY = 0.67  # below published 120/min, includes safety margin
EVENT_FIELDS = ['cycle','address','confirmed_balance_doge','provider_block_height','observed_at_utc','source_snapshot_date_utc']
CURRENT_FIELDS = ['address','confirmed_balance_doge','provider_block_height','observed_at_utc','cycle']
REQUIRED_COLUMNS = ['address','network','por_snapshot_height','por_snapshot_balance_doge','source','verification_status']

def utc():
    return datetime.now(timezone.utc).isoformat()

def atomic(path, content):
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(content, encoding='utf-8')
    os.replace(tmp, path)

def csv_text(fields, rows):
    s = io.StringIO(); w=csv.DictWriter(s, fieldnames=fields); w.writeheader(); w.writerows(rows)
    return s.getvalue()

def api(path):
    for i in range(4):
        try:
            req=urllib.request.Request(API + path, headers={'Accept':'application/json','User-Agent':'OKX-PoR-DOGE-balance-audit/1.0'})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data=json.load(resp)
            if not isinstance(data,dict) or data.get('status')!='success' or not isinstance(data.get('data'),dict):
                raise ValueError('unexpected Dogechain response: '+path)
            return data['data']
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == 3: raise
            try: sec=max(2,min(30,int(e.headers.get('Retry-After','5'))))
            except ValueError:sec=5
            time.sleep(sec)
        except (urllib.error.URLError, TimeoutError):
            if i == 3: raise
            time.sleep(2*(i+1))

def valid_balance(value):
    try: d=Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError): raise ValueError('balance not numeric')
    if not d.is_finite() or d<0: raise ValueError('balance negative/nonfinite')
    if -d.as_tuple().exponent>8: raise ValueError('balance has >8 decimal places')
    return f'{d:.8f}'

def load_source():
    raw=SOURCE.read_bytes()
    sha=hashlib.sha256(raw).hexdigest()
    audit=json.loads(AUDIT.read_text(encoding='utf-8'))
    rows=[]; seen=set()
    with SOURCE.open(newline='',encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames != REQUIRED_COLUMNS: raise ValueError('unexpected source CSV columns')
        for row in reader:
            addr=row['address']
            if not addr or addr in seen or len(addr)!=34 or row['network']!='DOGE':
                raise ValueError('invalid/duplicate PoR address')
            seen.add(addr); rows.append(addr)
    if len(rows)!=audit.get('addresses') or len(rows)!=40134: raise ValueError('source count/audit mismatch')
    return rows,sha,audit

def load_current(path):
    if not path.exists(): return {}
    with path.open(newline='',encoding='utf-8') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=CURRENT_FIELDS: raise ValueError('current CSV schema mismatch')
        out={}
        for row in reader:
            if row['address'] in out: raise ValueError('duplicate current addresses')
            out[row['address']]=row
        return out

def load_events(path):
    if not path.exists(): return []
    with path.open(newline='',encoding='utf-8') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=EVENT_FIELDS: raise ValueError('event CSV schema mismatch')
        return list(reader)

def main():
    BASE.mkdir(parents=True,exist_ok=True)
    started=utc(); begin=time.monotonic(); errors=[]
    completed=0; new_rows=[]; current={}; events=[]; audit={}; src_hash=None
    checkpoint_path=BASE/'checkpoint.json'; latest=BASE/'latest.json'
    index=None; cycle=None; total=None
    try:
        addresses,src_hash,audit=load_source();total=len(addresses)
        state=json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.exists() else {}
        if state and state.get('source_sha256')!=src_hash:
            raise ValueError('PoR address file changed; stop to avoid mixing baselines')
        index=int(state.get('next_index',0));cycle=int(state.get('cycle',1))
        if not 0<=index<total or cycle<1: raise ValueError('invalid checkpoint index')
        current=load_current(BASE/'current_balances.csv')
        events=load_events(BASE/'observations.csv')
        if any(a not in set(addresses) for a in current): raise ValueError('current balance address not in whitelist')
        # For a partially completed run, each queried address is immediately committed with checkpoint;
        # the workflow commits these files together. No false advancement after errors.
        end=min(total,index+MAX)
        for j in range(index,end):
            address=addresses[j]
            data=api('/address/'+address)
            summary=data.get('summary')
            if not isinstance(summary,dict) or 'confirmed_balance' not in summary:
                raise ValueError('missing address summary.confirmed_balance')
            balance=valid_balance(summary['confirmed_balance'])
            observed=utc()
            # Address page may not publish a block height: keep NULL rather than guessing network tip.
            height=summary.get('height',summary.get('block_height'))
            if height is not None and (not str(height).isdigit() or int(height)<=0):
                raise ValueError('invalid provider block height')
            height_text=str(height) if height is not None else ''
            cur={'address':address,'confirmed_balance_doge':balance,
                 'provider_block_height':height_text,'observed_at_utc':observed,'cycle':str(cycle)}
            event={'cycle':str(cycle),'address':address,'confirmed_balance_doge':balance,
                   'provider_block_height':height_text,'observed_at_utc':observed,
                   'source_snapshot_date_utc':str(audit['por_snapshot_date_utc'])}
            current[address]=cur;new_rows.append(event);completed+=1
            time.sleep(DELAY)
        events.extend(new_rows)
        # Order retained for source traceability; at this scale CSV is intentionally not append-only promises.
        atomic(BASE/'observations.csv',csv_text(EVENT_FIELDS,events))
        atomic(BASE/'current_balances.csv',csv_text(CURRENT_FIELDS,[current[k] for k in sorted(current)]))
        next_index=end if end<total else 0
        next_cycle=cycle if end<total else cycle+1
        atomic(checkpoint_path,json.dumps({'schema_version':1,'source_sha256':src_hash,
            'next_index':next_index,'cycle':next_cycle,'last_committed_utc':utc(),
            'previous_batch_completed':completed},ensure_ascii=False,indent=2)+'\n')
    except Exception as ex:
        errors.append(type(ex).__name__+': '+str(ex)[:300])
    status='error' if errors else 'ok'
    # Coverage refers to unique addresses ever sampled; NOT a synchronous latest reserve snapshot.
    current_coverage=len(current)
    output={'schema_version':1,'status':status,'source':'OKX PoR 2026-09-08 address disclosure + Dogechain public address API',
            'started_at_utc':started,'ended_at_utc':utc(),'source_sha256':src_hash,
            'por_snapshot_date_utc':audit.get('por_snapshot_date_utc'),
            'addresses_in_allowlist':total,'sampled_addresses_ever':current_coverage,
            'sampled_fraction_pct':round(100*current_coverage/total,4) if total else None,
            'batch_size_configured':MAX,'completed_this_run':completed if not errors else 0,
            'next_index':(index+completed)%total if total and index is not None and not errors else index,
            'cycle_this_run':cycle,'duration_seconds':round(time.monotonic()-begin,2),
            'errors':errors,'limitations':['Balances not sampled at the same block/time','Balance deltas are not exchange deposits or withdrawals',
                      'Addresses may change ownership since PoR','PoR ownership signatures not cryptographically verified',
                      'No complete exchange netflow can be inferred']}
    atomic(latest,json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':status,'completed':output['completed_this_run'],'next_index':output['next_index'],'errors':errors},ensure_ascii=False))
    return 1 if errors else 0

if __name__=='__main__':raise SystemExit(main())

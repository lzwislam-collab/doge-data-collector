#!/usr/bin/env python3
"""DOGE whale event sampling via Dogechain SSE. Coverage is PARTIAL by design.
Standard library, no secrets. Writes only confirmed transaction-level explain.sent,
not naive value_out which includes UTXO change. Never labels exchanges without evidence.
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

BASE = 'https://dogechain.com/api/v3'
OUT = Path(__file__).resolve().parent / 'data' / 'whales'
LIMIT = Decimal('1000000')
DURATION_SECONDS = int(os.environ.get('WHALE_LISTEN_SECONDS', '480'))
DURATION_SECONDS = max(30, min(720, DURATION_SECONDS))
FIELDS = ['txid','time_seen_utc','amount_doge','headline','first_observed_utc','source','verification']

def utc(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).isoformat()

def retrieve_tx(txid):
    if len(txid)!=64 or any(x not in '0123456789abcdefABCDEF' for x in txid):
        raise ValueError('invalid txid')
    req = urllib.request.Request(BASE+'/tx/'+txid,headers={'Accept':'application/json','User-Agent':'DOGE-whale-audit/1.0'})
    with urllib.request.urlopen(req, timeout=15) as response:
        p=json.load(response)
    if p.get('status')!='success' or not isinstance(p.get('data'),dict):
        raise ValueError('invalid transaction response')
    expl=p['data'].get('explain') or {}
    d=Decimal(str(expl.get('sent')))
    if not d.is_finite() or d<0:
        raise ValueError('invalid explain.sent amount')
    return d, str(expl.get('headline') or '')

def atomic(path,text):
    t=path.with_name(path.name+'.tmp')
    t.write_text(text,encoding='utf-8')
    os.replace(t,path)

def run():
    from io import StringIO
    OUT.mkdir(parents=True,exist_ok=True)
    start=time.time(); deadline=start+DURATION_SECONDS
    errors=[]; rows={}; candidates=0; checked=0; events=0; ended='timeout'
    path=OUT/'events.csv'
    if path.exists():
        with path.open(newline='',encoding='utf-8') as f:
            reader=csv.DictReader(f)
            if reader.fieldnames!=FIELDS: raise ValueError('CSV schema mismatch')
            for row in reader:
                k=row['txid']
                if k in rows: raise ValueError('duplicate historical txid')
                rows[k]=row
    seen=set(rows)
    last_event_name=''
    try:
        req=urllib.request.Request(BASE+'/live',headers={'Accept':'text/event-stream','Cache-Control':'no-cache','User-Agent':'DOGE-whale-audit/1.0'})
        # SSE server should send heartbeat frequently. Socket timeout bounds silent connections.
        with urllib.request.urlopen(req,timeout=30) as stream:
            ended='time_window_end'
            for raw in stream:
                if time.time()>=deadline: break
                line=raw.decode('utf-8','replace').strip()
                if line.startswith('event:'):
                    last_event_name=line[6:].strip()
                    continue
                if not line.startswith('data:') or last_event_name!='tx':continue
                events+=1
                try:
                    tx=json.loads(line[5:].strip())
                    txid=str(tx.get('txid') or '')
                    if not txid or txid in seen:continue
                    # This is a conservative pre-filter: value_out includes change, never publish this as amount.
                    rough=Decimal(str(tx.get('value_out')))
                    if rough<LIMIT:continue
                    candidates+=1
                    real,headline=retrieve_tx(txid)
                    checked+=1
                    if real<LIMIT:continue
                    seen.add(txid)
                    ts=tx.get('time_seen')
                    try: event_time=utc(int(ts)) if ts is not None else ''
                    except (ValueError,TypeError,OverflowError):event_time=''
                    rows[txid]={'txid':txid,'time_seen_utc':event_time,'amount_doge':str(real),'headline':headline,
                                 'first_observed_utc':utc(),'source':'Dogechain /live + /tx/{txid}',
                                 'verification':'explain.sent; change excluded by provider'}
                except (ValueError,TypeError,KeyError,InvalidOperation,urllib.error.URLError,TimeoutError) as exc:
                    errors.append(str(exc)[:160])
    except Exception as exc:
        ended='stream_error';errors.append(type(exc).__name__+': '+str(exc)[:180])
    buf=StringIO(); w=csv.DictWriter(buf,fieldnames=FIELDS);w.writeheader()
    w.writerows(rows[k] for k in sorted(rows))
    atomic(path,buf.getvalue())
    finish=time.time()
    state=('partial' if not errors else 'degraded')
    summary={'schema_version':1,'source':'Dogechain SSE /live plus /tx/{txid}',
             'status':state,'observed_from_utc':utc(start),'observed_to_utc':utc(finish),
             'elapsed_seconds':round(finish-start,2),'requested_seconds':DURATION_SECONDS,
             'stream_end_reason':ended,'events_seen':events,'candidate_value_out_ge_1m':candidates,
             'candidates_verified':checked,'cumulative_verified_unique_transfers':len(rows),
             'threshold_doge':str(LIMIT),'new_events_in_window':sum(1 for r in rows.values() if r['first_observed_utc']>=utc(start)),
             'errors':errors[:20],
             'coverage':'PARTIAL: sampled SSE during workflow run only; GitHub scheduling gaps, reconnect gaps and historical transactions are NOT covered',
             'no_exchange_inference':True,
             'notes':['not a comprehensive chain scanner','unconfirmed stream events; transaction explanation verified but block confirmation is not established',
                      'sender/receiver inference limited to provider headline','never interpret event counts as total network activity']}
    atomic(OUT/'latest.json',json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':state,'seen':events,'checked':checked,'unique':len(rows),'errors':len(errors)},ensure_ascii=False),flush=True)
    return 0 if ended=='time_window_end' and not errors else 1

if __name__=='__main__':sys.exit(run())

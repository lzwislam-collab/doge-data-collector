"""Supplementary prospective quote telemetry. Never orders, fills, or frozen scores.

CLI makes one bounded public OKX books request. JSONL embeds the raw response and
hash-linked observations. Each UP forecast is a separate 100 USDT illustrative
long valued using indicative visible-depth sweeps; fees, latency, queue position,
market impact and unavailable liquidity are not modeled. These are not fills.
Sampled drawdown is not continuous drawdown; path_gap exposes sampling gaps.
"""
import argparse
import csv
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

URL = 'https://www.okx.com/api/v5/market/books?instId=DOGE-USDT&sz=400'
NOTIONAL = 100.0
MAX_AGE_SECONDS = 30
MAX_SPREAD_BPS = 200
GAP_SECONDS = 1800
POLICY = {'schema_version':2, 'primary_direction':'doge_reversal_direction', 'notional_usdt':NOTIONAL, 'prospective_activation_only':True, 'exit_window_minutes':25, 'depth_band_pct':1, 'max_age_seconds':MAX_AGE_SECONDS, 'max_spread_bps':MAX_SPREAD_BPS, 'gap_seconds':GAP_SECONDS}

def stamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError('explicit UTC timestamp required')
    return dt

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def activation_path(path):
    return Path(path).with_suffix('.activation.json')

def activate(path, now=None):
    """Create policy activation once; callers cannot reset an existing activation."""
    ap = activation_path(path)
    if ap.exists():
        row = json.loads(ap.read_text(encoding='utf-8')); check=dict(row); actual=check.pop('activation_sha256')
        if actual != digest(check) or row['policy'] != POLICY: raise ValueError('activation integrity/policy mismatch')
        stamp(row['activated_at_utc'])
        if now is not None and stamp(now).isoformat()!=row['activated_at_utc']: raise ValueError('activation already fixed')
        return row
    if Path(path).exists(): raise ValueError('cannot activate an existing unregistered observation chain')
    row={'activated_at_utc':stamp(now or datetime.now(timezone.utc)).isoformat(),'policy':POLICY}
    row['activation_sha256']=digest(row)
    ap.parent.mkdir(parents=True,exist_ok=True)
    with ap.open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(row,sort_keys=True)+'\n'); stream.flush(); os.fsync(stream.fileno())
    return row

def sweep(levels, amount, cash=False):
    """Indicative sweep of visible levels, never an executable fill assertion."""
    remaining=amount; units=0.0; notional=0.0
    for level in levels:
        price,size=float(level[0]),float(level[1])
        take=min(size, remaining/price if cash else remaining)
        units+=take; notional+=take*price; remaining-=take*price if cash else take
        if remaining <= amount*1e-12: break
    if remaining > amount*1e-12: raise ValueError('insufficient depth for indicative sweep')
    return {'units':units,'notional_usdt':notional,'vwap':notional/units}

def read_chain(path):
    records = []
    if not Path(path).exists(): return records
    previous = None
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        check = dict(row); actual = check.pop('record_sha256')
        if actual != digest(check) or row['previous_sha256'] != previous:
            raise ValueError('evidence chain integrity failure')
        if records and (stamp(row['captured_at_utc']) <= stamp(records[-1]['captured_at_utc']) or row['quote']['ts_ms'] <= records[-1]['quote']['ts_ms']):
            raise ValueError('non-monotonic evidence chain')
        records.append(row); previous = actual
    return records

def validate_book(payload, requested_at, captured_at):
    if payload.get('code') != '0' or len(payload.get('data', [])) != 1:
        raise ValueError('malformed OKX response')
    data = payload['data'][0]
    ts_ms = int(data['ts']); qt = datetime.fromtimestamp(ts_ms / 1000, timezone.utc)
    age = (captured_at - qt).total_seconds()
    if not 0 <= age <= MAX_AGE_SECONDS or not 0 <= (captured_at-requested_at).total_seconds() <= 20:
        raise ValueError('stale, future, or slow book')
    sides = {}
    for side in ('bids','asks'):
        levels = data[side]
        if not isinstance(levels, list) or not 1 <= len(levels) <= 400: raise ValueError('invalid depth')
        pairs = []
        for level in levels:
            p, q = float(level[0]), float(level[1])
            if not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q <= 0: raise ValueError('invalid price/size')
            pairs.append((p,q))
        prices = [p for p,q in pairs]
        if len(set(prices)) != len(prices) or prices != sorted(prices, reverse=side=='bids'): raise ValueError('unsorted/duplicate depth')
        sides[side] = pairs
    bid, ask = sides['bids'][0][0], sides['asks'][0][0]
    spread = (ask-bid)/((ask+bid)/2)*10000
    if not 0 <= spread <= MAX_SPREAD_BPS: raise ValueError('invalid spread')
    # Require enough cumulative visible notional within 1% of each best price.
    bn = sum(p*q for p,q in sides['bids'] if p >= bid*0.99)
    an = sum(p*q for p,q in sides['asks'] if p <= ask*1.01)
    if min(bn, an) < NOTIONAL: raise ValueError('insufficient visible depth within 1%')
    return {'ts_ms':ts_ms, 'quote_utc':qt.isoformat(), 'bid':bid,'ask':ask,'spread_bps':spread,'age_seconds':age,'bid_depth_notional_usdt':bn,'ask_depth_notional_usdt':an,'depth_band_pct':1}

def load_forecasts(operations, registry, now=None):
    report = json.loads(Path(operations).read_text(encoding='utf-8'))
    if report.get('status') != 'ok' or report.get('frozen_implementation_verified') is not True: raise ValueError('publication audit not verified')
    now=stamp(now or datetime.now(timezone.utc))
    if not 0 <= (now-stamp(report.get('audited_at_utc',''))).total_seconds() <= 21600: raise ValueError('publication audit older than 6h or future')
    if any(len(report.get(key,''))!=length or any(c not in '0123456789abcdef' for c in report[key]) for key,length in [('input_head',40),('monitor_code_sha256',64)]): raise ValueError('malformed audit hash identity')
    expected_monitor_hash=hashlib.sha256(Path(__file__).with_name('p0_operational_audit.py').read_bytes()).hexdigest()
    if report['monitor_code_sha256'] != expected_monitor_hash: raise ValueError('audit code hash differs from current verified monitor')
    with Path(registry).open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    ids = [r['forecast_id'] for r in rows]
    if len(ids) != len(set(ids)): raise ValueError('duplicate forecast IDs')
    out = []
    for row in rows:
        proof = report.get('forecast_proofs',{}).get(row['forecast_id'])
        if not proof: continue
        pub = proof.get('publication',{})
        if pub.get('forecast_id') != row['forecast_id'] or not str(pub.get('run_id','')).isdigit() or len(pub.get('head_sha','')) != 40 or any(c not in '0123456789abcdef' for c in pub['head_sha']) or not pub.get('run_url','').startswith('https://github.com/'):
            raise ValueError('malformed publication proof')
        issued = stamp(row['issued_at_utc']); published = stamp(pub['observed_public_utc']); target = stamp(row['target_utc'])
        if stamp(row['source_quote_utc']) != stamp(proof['source_quote_utc']): raise ValueError('registry source quote disagrees with verified proof')
        if issued != stamp(proof['issued_at_utc']) or published < issued or published > now or target <= issued: raise ValueError('invalid publication chronology')
        out.append(dict(row, observed_public_utc=published.isoformat(), publication=pub))
    return out

def capture(path, forecasts, payload, requested_at, captured_at):
    """Append one live observation. Callers must provide a fresh network response.

    No historical import CLI is provided. The raw quote must follow publication
    and the current request must follow publication before it can become entry.
    """
    requested_at, captured_at = stamp(requested_at), stamp(captured_at)
    activation=activate(path)
    if stamp(activation['activated_at_utc']) > requested_at: raise ValueError('request precedes activation')
    quote = validate_book(payload, requested_at, captured_at)
    qt = stamp(quote['quote_utc'])
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix+'.lock')
    try: fd = os.open(lock, os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise ValueError('capture already in progress; inspect stale lock manually')
    try:
        history = read_chain(path)
        if any(r.get('activation_sha256')!=activation['activation_sha256'] or r.get('schema_version')!=2 for r in history): raise ValueError('observation activation mismatch')
        if history and (captured_at <= stamp(history[-1]['captured_at_utc']) or quote['ts_ms'] <= history[-1]['quote']['ts_ms']): raise ValueError('duplicate/non-monotonic snapshot')
        by_id = {}
        for record in history:
            for obs in record['observations']: by_id.setdefault(obs['forecast_id'], []).append((record,obs))
        observations=[]; seen=set()
        for forecast in forecasts:
            fid=forecast['forecast_id']
            if fid in seen: raise ValueError('duplicate forecast ID')
            seen.add(fid)
            pub, target=stamp(forecast['observed_public_utc']), stamp(forecast['target_utc'])
            if pub >= target or forecast['doge_reversal_direction'] not in ('UP','DOWN') or not forecast.get('model_id'): raise ValueError('invalid forecast')
            if pub > requested_at: continue
            if qt <= pub: raise ValueError('quote is not strictly post-publication')
            past=by_id.get(fid,[])
            identity={'target_utc':target.isoformat(),'observed_public_utc':pub.isoformat(),'doge_reversal_direction':forecast['doge_reversal_direction'],'model_id':forecast['model_id'],'publication':forecast['publication']}
            if past and past[0][1]['forecast'] != identity: raise ValueError('forecast evidence changed')
            if past and past[-1][1]['kind'] in ('exit_quote','missed_exit','missed_entry','legacy_excluded'): continue
            obs={'forecast_id':fid,'forecast':identity,'bid':quote['bid'],'ask':quote['ask']}
            if pub <= stamp(activation['activated_at_utc']):
                obs['kind']='legacy_excluded'; observations.append(obs); continue
            if not past:
                obs['kind']='entry_quote' if qt < target else 'missed_entry'
                obs['entry_latency_seconds']=(qt-pub).total_seconds()
                obs['entry_is_first_observed_valid_quote']=True
            elif qt < target: obs['kind']='bid_mark'
            elif qt <= target+timedelta(minutes=25): obs['kind']='exit_quote'
            else: obs['kind']='missed_exit'
            if obs['kind'] not in ('missed_entry','missed_exit') and forecast['doge_reversal_direction']=='UP':
                data=payload['data'][0]
                asks=[level for level in data['asks'] if float(level[0])<=quote['ask']*1.01]
                bids=[level for level in data['bids'] if float(level[0])>=quote['bid']*0.99]
                entry=sweep(asks,NOTIONAL,cash=True) if not past else past[0][1]['indicative_entry_sweep']
                mark=sweep(bids,entry['units'])
                equity=mark['notional_usdt']
                obs.update(indicative_entry_sweep=entry,indicative_entry_vwap=entry['vwap'],indicative_bid_vwap=mark['vwap'],actual_fill=False)
                peak=max([NOTIONAL,equity]+[o.get('paper_equity_usdt',0) for r,o in past])
                dd=(peak-equity)/peak*100
                obs.update(paper_equity_usdt=equity,paper_notional_usdt=NOTIONAL,sampled_drawdown_pct=max([dd]+[o.get('sampled_drawdown_pct',0) for r,o in past]),path_gap=bool(past and (qt-stamp(past[-1][0]['quote']['quote_utc'])).total_seconds()>=GAP_SECONDS),gap_seconds=(qt-stamp(past[-1][0]['quote']['quote_utc'])).total_seconds() if past else None,continuous_drawdown_verified=False)
            observations.append(obs)
        row={'schema_version':2,'activation_sha256':activation['activation_sha256'],'source':URL,'scope':'supplementary prospectively observed quote telemetry; separate from frozen protocol','requested_at_utc':requested_at.isoformat(),'captured_at_utc':captured_at.isoformat(),'raw_response':payload,'quote':quote,'observations':observations,'actual_fill':False,'profit_validated':False,'fees_slippage_modeled':False,'cost_caveat':'Gross indicative visible-book sweeps only; excludes fees, latency, queue position, market impact and liquidity disappearance. No fills or realizable profit established.','previous_sha256':history[-1]['record_sha256'] if history else None}
        row['record_sha256']=digest(row)
        with path.open('a',encoding='utf-8',newline='\n') as stream:
            stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n'); stream.flush(); os.fsync(stream.fileno())
        return row
    finally:
        os.close(fd); lock.unlink()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operations',default='data/p0_operations/latest.json')
    parser.add_argument('--registry',default='data/direction_v42/forward_registry.csv')
    parser.add_argument('--output',default='data/forward_execution_evidence/observations.jsonl')
    args=parser.parse_args()
    try:
        forecasts=load_forecasts(args.operations,args.registry)
        activation=activate(args.output)
        requested=datetime.now(timezone.utc)
        with urlopen(Request(URL,headers={'User-Agent':'DOGE-Supplementary-Evidence/1.0'}),timeout=15) as response:
            raw=response.read(2_000_001)
        if len(raw)>2_000_000: raise ValueError('oversized response')
        payload=json.loads(raw)
        result=capture(args.output,forecasts,payload,requested,datetime.now(timezone.utc))
        print(json.dumps({'status':'captured','record_sha256':result['record_sha256'],'observations':len(result['observations']),'actual_fill':False,'profit_validated':False}))
    except Exception as exc:
        print(json.dumps({'status':'failed_closed','error':str(exc)})); return 1
    return 0

if __name__=='__main__': raise SystemExit(main())

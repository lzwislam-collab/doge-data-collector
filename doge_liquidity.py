#!/usr/bin/env python3
"""DOGE Stage 2 independent OKX liquidity collector. No fabricated liquidation figures."""
import csv
import hashlib
import uuid
import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'data' / 'liquidity'
API = 'https://www.okx.com'
INSTRUMENT = 'DOGE-USDT-SWAP'
FIELDS = ['collected_at_ms','instrument','book_ts_ms','trade_latest_ts_ms','oi_ts_ms',
          'best_bid','best_ask','spread_bps','bid_depth_usdt_10bps','ask_depth_usdt_10bps',
          'bid_depth_usdt_50bps','ask_depth_usdt_50bps','bid_depth_usdt_100bps','ask_depth_usdt_100bps',
          'buy_1k_vwap','buy_1k_slippage_bps','sell_1k_vwap','sell_1k_slippage_bps',
          'buy_10k_vwap','buy_10k_slippage_bps','sell_10k_vwap','sell_10k_slippage_bps',
          'recent_buy_trades','recent_sell_trades','recent_buy_contracts','recent_sell_contracts',
          'open_interest_usd','funding_rate','liquidation_usd','liquidation_source_status']


def get(path, raw_dir=None, **params):
    url = API+path+'?'+urllib.parse.urlencode(params)
    requested=datetime.now(timezone.utc).isoformat()
    req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'DOGE-liquidity-research/1.1'})
    with urllib.request.urlopen(req,timeout=25) as res:
        raw=res.read()
        received=datetime.now(timezone.utc).isoformat()
        proof={'source_url':url,'requested_at_utc':requested,'received_at_utc':received,
               'first_seen_utc':received,'http_status':getattr(res,'status',200),
               'headers':dict(res.headers),'body_utf8':raw.decode('utf-8'),
               'raw_sha256':hashlib.sha256(raw).hexdigest()}
    if raw_dir is not None:
        directory=Path(raw_dir);directory.mkdir(parents=True,exist_ok=True)
        with (directory/(path.rsplit('/',1)[-1]+'-'+uuid.uuid4().hex+'.json')).open('x',encoding='utf-8') as stream:
            json.dump(proof,stream,ensure_ascii=False,indent=2)
    obj=json.loads(proof['body_utf8'])
    if obj.get('code')!='0' or not isinstance(obj.get('data'),list) or not obj['data']:
        raise ValueError('OKX response invalid for '+path+': '+str(obj.get('msg',''))[:120])
    return obj['data']


def number(x, allow_zero=False):
    n=float(x)
    if not math.isfinite(n) or (n<0 if allow_zero else n<=0): raise ValueError('invalid number')
    return n


def book(raw_dir=None):
    d=get('/api/v5/market/books',raw_dir=raw_dir,instId=INSTRUMENT,sz='400')[0]
    now=int(time.time()*1000); ts=int(d['ts'])
    if not 0<=now-ts<=600000: raise ValueError('stale orderbook timestamp')
    bids=[(number(a[0]),number(a[1],True)) for a in d['bids']]
    asks=[(number(a[0]),number(a[1],True)) for a in d['asks']]
    if not bids or not asks or bids[0][0]>=asks[0][0]:raise ValueError('crossed/empty book')
    if any(bids[i][0]<bids[i+1][0] for i in range(len(bids)-1)):raise ValueError('bids not sorted')
    if any(asks[i][0]>asks[i+1][0] for i in range(len(asks)-1)):raise ValueError('asks not sorted')
    return ts,bids,asks


def instrument_spec(raw_dir=None):
    rows=get('/api/v5/public/instruments',raw_dir=raw_dir,instType='SWAP',instId=INSTRUMENT)
    d=next((x for x in rows if x.get('instId')==INSTRUMENT),None)
    if not d:raise ValueError('instrument metadata absent')
    multiplier=number(d['ctVal'])
    if d.get('ctType')!='linear' or d.get('ctValCcy')!='DOGE' or d.get('state')!='live':
        raise ValueError('unexpected contract denomination; refuse to estimate USDT liquidity')
    return multiplier,{'ctVal':d['ctVal'],'ctValCcy':d['ctValCcy'],'ctType':d['ctType'],'state':d.get('state')}


def depth(levels, mid, bps, side, contract_doge):
    if side=='bid': return sum(p*q*contract_doge for p,q in levels if p>=mid*(1-bps/10000))
    return sum(p*q*contract_doge for p,q in levels if p<=mid*(1+bps/10000))


def walk(levels, usdt, contract_doge, mid, side):
    remaining=usdt; doge=0.; spent=0.
    for p,contracts in levels:
        capacity=p*contracts*contract_doge
        take=min(remaining,capacity)
        if take>0:
            doge+=take/p
            spent+=take
            remaining-=take
        if remaining<1e-7:break
    if remaining>=1e-7 or doge<=0: return None,None
    avg=spent/doge
    slippage=(avg/mid-1)*10000 if side=='buy' else (1-avg/mid)*10000
    return round(avg,10),round(slippage,5)


def trade_metrics(contract_doge,raw_dir=None):
    rows=get('/api/v5/market/trades',raw_dir=raw_dir,instId=INSTRUMENT,limit='500')
    now=int(time.time()*1000)
    ids=set(); counts={'buy':0,'sell':0}; sizes={'buy':0.,'sell':0.}; times=[]
    for x in rows:
        if x.get('instId')!=INSTRUMENT:raise ValueError('wrong instrument in trades')
        side=x.get('side'); ts=int(x['ts']); identifier=x.get('tradeId')
        if side not in counts or not identifier or not 0<=now-ts<=86400000:raise ValueError('invalid trade')
        if identifier in ids:raise ValueError('duplicate tradeId in API response')
        ids.add(identifier);counts[side]+=1;sizes[side]+=number(x['sz'],True);times.append(ts)
    if not times:raise ValueError('empty trades')
    return {'latest_ts_ms':max(times),'earliest_ts_ms':min(times),'sample_size':len(rows),
            'buy_count':counts['buy'],'sell_count':counts['sell'],
            'buy_contracts':sizes['buy'],'sell_contracts':sizes['sell'],
            'buy_doge':sizes['buy']*contract_doge,'sell_doge':sizes['sell']*contract_doge,
            'coverage_note':'Last up to 500 trades, NOT a fixed time window. Missing trades between polls cannot be reconstructed.'}


def derivatives(raw_dir=None):
    oi=get('/api/v5/public/open-interest',raw_dir=raw_dir,instType='SWAP',instId=INSTRUMENT)[0]
    fr=get('/api/v5/public/funding-rate',raw_dir=raw_dir,instId=INSTRUMENT)[0]
    if oi.get('instId')!=INSTRUMENT or fr.get('instId')!=INSTRUMENT:
        raise ValueError('wrong instrument in derivatives response')
    now=int(time.time()*1000); ts=int(oi['ts'])
    if not 0<=now-ts<=1800000:raise ValueError('stale open interest')
    rate=float(fr['fundingRate'])
    if not math.isfinite(rate): raise ValueError('invalid funding rate')
    return {'oi_ts_ms':ts,'open_interest_usd':number(oi['oiUsd'],True),
            'funding_rate':rate,'funding_time_ms':int(fr.get('fundingTime') or 0)}


def atomic(path,text):
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(text,encoding='utf-8');os.replace(temp,path)


def historical(row):
    p=ROOT/'hourly_snapshots.csv'; old={}
    if p.exists():
        with p.open(newline='',encoding='utf-8') as f:
            reader=csv.DictReader(f)
            if reader.fieldnames!=FIELDS:raise ValueError('historical CSV schema mismatch')
            for r in reader:
                k=r['collected_at_ms']
                if k in old:raise ValueError('duplicate historical timestamp')
                old[k]=r
    key=row['collected_at_ms']
    if key in old:raise ValueError('duplicate current timestamp')
    previous=max((int(k) for k in old),default=None)
    old[key]=row
    import io
    s=io.StringIO();w=csv.DictWriter(s,fieldnames=FIELDS);w.writeheader();w.writerows(old[k] for k in sorted(old,key=int))
    atomic(p,s.getvalue())
    stamps=sorted(int(k) for k in old)
    return {'rows':len(stamps),'duplicate_timestamps':0,'first_ms':stamps[0],
            'last_ms':stamps[-1],'gaps_over_90_minutes':sum(b-a>90*60000 for a,b in zip(stamps,stamps[1:])),
            'historical_backfill':False,'previous_snapshot_ms':previous,
            'gap_since_previous_seconds':(int(key)-previous)/1000 if previous is not None else None}


def stamp_collection(result, started_ms):
    """New rows become available after all successful source responses, never at request start."""
    collected_ms=int(time.time()*1000)
    if collected_ms<started_ms:
        raise ValueError('collection clock moved backwards')
    result['started_at_utc']=datetime.fromtimestamp(started_ms/1000,timezone.utc).isoformat()
    result['collected_at_utc']=datetime.fromtimestamp(collected_ms/1000,timezone.utc).isoformat()
    result['collection_elapsed_ms']=collected_ms-started_ms
    return collected_ms


def archive_snapshot(result, raw_dir, repo_root):
    """Reference the immutable snapshot from latest without a circular self-hash."""
    snapshot=Path(raw_dir)/'snapshot.json'
    payload={k:v for k,v in result.items() if k!='snapshot_evidence'}
    raw=(json.dumps(payload,ensure_ascii=False,allow_nan=False,indent=2)+'\n').encode('utf-8')
    with snapshot.open('xb') as stream:
        stream.write(raw)
    result['snapshot_evidence']={'path':snapshot.relative_to(Path(repo_root)).as_posix(),
                                 'sha256':hashlib.sha256(raw).hexdigest()}
    return result


def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    started=int(time.time()*1000)
    raw_dir=ROOT/'raw'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex)
    raw_dir.mkdir(parents=True)
    result={'schema_version':3,'started_at_utc':datetime.fromtimestamp(started/1000,timezone.utc).isoformat(),
            'collected_at_utc':datetime.fromtimestamp(started/1000,timezone.utc).isoformat(),
            'source':'OKX public REST API','instrument':INSTRUMENT,'status':'error','errors':{},
            'liquidations':{'status':'not_available','liquidation_usd':None,
                'note':'No verified public forced-liquidation event feed integrated; OI decline or regular trades must not be relabelled liquidations.'}}
    try:
        ct,spec=instrument_spec(raw_dir)
        ts,bids,asks=book(raw_dir);mid=(bids[0][0]+asks[0][0])/2
        t=trade_metrics(ct,raw_dir);d=derivatives(raw_dir)
        collected=stamp_collection(result,started)
        metrics={'book_ts_ms':ts,'best_bid':bids[0][0],'best_ask':asks[0][0],
                 'spread_bps':(asks[0][0]-bids[0][0])/mid*10000,
                 'book_bid_levels':len(bids),'book_ask_levels':len(asks)}
        for bps in (10,50,100):
            metrics[f'bid_depth_usdt_{bps}bps']=depth(bids,mid,bps,'bid',ct)
            metrics[f'ask_depth_usdt_{bps}bps']=depth(asks,mid,bps,'ask',ct)
        for amount in (1000,10000):
            for side,levels in (('buy',asks),('sell',bids)):
                avg,slip=walk(levels,amount,ct,mid,side)
                metrics[f'{side}_{amount//1000}k_vwap']=avg
                metrics[f'{side}_{amount//1000}k_slippage_bps']=slip
        result.update({'contract_spec':spec,'orderbook':metrics,'recent_trades':t,'derivatives':d,'status':'ok'})
        row={'collected_at_ms':str(collected),'instrument':INSTRUMENT,'book_ts_ms':str(ts),
             'trade_latest_ts_ms':str(t['latest_ts_ms']),'oi_ts_ms':str(d['oi_ts_ms']),
             'best_bid':str(metrics['best_bid']),'best_ask':str(metrics['best_ask']),
             'spread_bps':str(metrics['spread_bps']),
             'recent_buy_trades':str(t['buy_count']),'recent_sell_trades':str(t['sell_count']),
             'recent_buy_contracts':str(t['buy_contracts']),'recent_sell_contracts':str(t['sell_contracts']),
             'open_interest_usd':str(d['open_interest_usd']),'funding_rate':str(d['funding_rate']),
             'liquidation_usd':'','liquidation_source_status':'not_available'}
        for key in FIELDS:
            if key not in row:row[key]='' if metrics.get(key) is None else str(metrics[key])
        result['data_quality']=historical(row)
        result['data_quality']['book_delay_ms']=int(time.time()*1000)-ts
        result['data_quality']['trade_sample_is_fixed_time_window']=False
        result['data_quality']['instrument_spec_check']='linear DOGE-denominated live contract'
        result['data_quality']['missing_liquidation_measure']=True
    except Exception as e:
        result['errors']['collector']=type(e).__name__+': '+str(e)[:350]
        result['status']='error'
    result['raw_evidence_directory']=str(raw_dir.relative_to(ROOT.parent.parent)).replace('\\','/')
    result['raw_evidence']=[{'path':str(p.relative_to(ROOT.parent.parent)).replace('\\','/'),
        'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(raw_dir.iterdir())]
    captures=[json.loads(p.read_text(encoding='utf-8'))['received_at_utc'] for p in raw_dir.iterdir()]
    result['available_at_utc']=max(captures) if captures else None
    result['historical_backfill']=False
    archive_snapshot(result,raw_dir,ROOT.parent.parent)
    atomic(ROOT/'latest.json',json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'errors':result['errors']},ensure_ascii=False),flush=True)
    return 0 if result['status']=='ok' else 1

if __name__=='__main__':raise SystemExit(main())

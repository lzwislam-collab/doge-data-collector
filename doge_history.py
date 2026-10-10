#!/usr/bin/env python3
"""OKX closed UTC 1D history backfill and strictly causal DOGE baseline backtest.
No trade decisions. Uses only public API. One batch per symbol/run; no lookahead.
"""
import csv, io, json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

BASE=Path(__file__).resolve().parent/'data'/'history'
BASE.mkdir(parents=True,exist_ok=True)
SYMBOLS=('DOGE-USDT','BTC-USDT','ETH-USDT')
FIELDS=('symbol','open_utc_ms','open','high','low','close','volume_base','source')
DAY=86400000
API='https://www.okx.com/api/v5/market/history-candles'

def stamp():return datetime.now(timezone.utc).isoformat()
def write(path,txt):
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(txt,encoding='utf-8');os.replace(tmp,path)
def loadcsv(path):
    if not path.exists():return []
    with path.open(newline='',encoding='utf-8') as f:
        rdr=csv.DictReader(f)
        if tuple(rdr.fieldnames or ())!=FIELDS:raise ValueError('Existing history header mismatch')
        return list(rdr)
def dump(rows):
    b=io.StringIO();w=csv.DictWriter(b,fieldnames=FIELDS);w.writeheader();w.writerows(rows);return b.getvalue()
def finite_nonnegative(val):
    d=Decimal(str(val))
    if not d.is_finite() or d<0:raise ValueError('Invalid decimal')
    return d

def request(symbol,oldest=None):
    params={'instId':symbol,'bar':'1Dutc','limit':'100'}
    if oldest is not None:params['after']=str(oldest)
    url=API+'?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'DOGE-history-audit/1.0'})
    with urllib.request.urlopen(req,timeout=35) as response:
        data=json.load(response)
    if data.get('code')!='0' or not isinstance(data.get('data'),list):
        raise ValueError('OKX API error '+str(data.get('code'))+' '+str(data.get('msg')))
    return data['data']

def validrow(symbol,c,now_ms):
    if len(c)<9 or c[8]!='1':return None
    ms=int(c[0]);now_day=(now_ms//DAY)*DAY
    if ms<0 or ms%DAY or ms>=now_day:return None
    o,h,l,cl=[finite_nonnegative(x) for x in c[1:5]]
    vol=finite_nonnegative(c[5]);
    if min(o,cl)<=0 or l<=0 or h<max(o,cl,l) or l>min(o,cl,h):raise ValueError('OHLC constraint '+symbol+' '+str(ms))
    return dict(zip(FIELDS,[symbol,str(ms),str(o),str(h),str(l),str(cl),str(vol),'OKX history-candles 1Dutc confirmed']))

def audit(rows):
    out={};by={s:[] for s in SYMBOLS};seen=set();duplicates=0
    for r in rows:
        k=(r['symbol'],int(r['open_utc_ms']))
        if k in seen:duplicates+=1
        seen.add(k);by[r['symbol']].append(k[1])
    for symbol,dates in by.items():
        ds=sorted(set(dates));gaps=sum(max(0,(b-a)//DAY-1) for a,b in zip(ds,ds[1:]))
        out[symbol]={'days':len(ds),'first_utc':datetime.fromtimestamp(ds[0]/1000,timezone.utc).date().isoformat() if ds else None,'last_utc':datetime.fromtimestamp(ds[-1]/1000,timezone.utc).date().isoformat() if ds else None,'missing_days_between_endpoints':gaps}
    return {'symbols':out,'duplicate_keys':duplicates}

def backtest(rows):
    """Predict 1/7/30d forward direction using trailing 30d close momentum,
    purged training/test chronological split: zero fitted parameters; baseline only.
    No overlapping prediction target crosses the 70% split boundary.
    """
    doge=sorted((r for r in rows if r['symbol']=='DOGE-USDT'),key=lambda r:int(r['open_utc_ms']))
    if len(doge)<400:return {'status':'insufficient_data','minimum_days':400,'actual_days':len(doge)}
    points={int(r['open_utc_ms']):float(r['close']) for r in doge}
    keys=sorted(points)
    split=keys[int(len(keys)*.70)]
    result={'status':'ok','method':'30-day trailing momentum sign vs forward close; no fitted parameters; chronological holdout last 30%; baseline, NOT calibrated probability','train_end_exclusive_utc':datetime.fromtimestamp(split/1000,timezone.utc).date().isoformat(),'horizons':{}}
    for horizon in (1,7,30):
        tested=correct=0;mae=0.0
        for t in keys:
            if t<split:continue
            past=t-30*DAY;future=t+horizon*DAY
            if past not in points or future not in points:continue
            signal=points[t]/points[past]-1
            realized=points[future]/points[t]-1
            if not signal or not realized:continue
            tested+=1;correct+=int((signal>0)==(realized>0));mae+=abs(realized)
        result['horizons'][str(horizon)+'d']={'test_observations':tested,'direction_accuracy_pct':round(correct/tested*100,2) if tested else None,'average_abs_realized_return_pct':round(mae/tested*100,3) if tested else None,'caveat':'overlapping horizons cause dependent outcomes; this is not risk-adjusted profitability'}
    return result

def main():
    started=stamp();path=BASE/'daily.csv';out={'schema_version':1,'started_at_utc':started,'status':'error','source':'OKX public history-candles, confirmed 1Dutc','errors':{},'new_rows':0,'existing_rows':0,'per_symbol':{},'limitations':['History endpoint retention may cap earliest coverage','No intraday release-time aligned ETF/macro features','Daily UTC close availability only after candle closes','Backtest uses simple historical-price-only baseline, not DOGE trading rule']}
    try:
        existing=loadcsv(path);out['existing_rows']=len(existing)
        old={}
        for row in existing:
            k=(row['symbol'],row['open_utc_ms'])
            if k in old:raise ValueError('Duplicate stored candle '+str(k))
            old[k]=row
        now_ms=int(time.time()*1000)
        for symbol in SYMBOLS:
            matching=[int(k[1]) for k in old if k[0]==symbol]
            cursor=min(matching) if matching else None
            try:
                raw=request(symbol,cursor)
                new=0
                for c in raw:
                    row=validrow(symbol,c,now_ms)
                    if row is None:continue
                    key=symbol,row['open_utc_ms']
                    if key not in old:old[key]=row;new+=1
                out['per_symbol'][symbol]={'received':len(raw),'inserted':new,'previous_oldest_ms':cursor}
                out['new_rows']+=new
            except Exception as e:raise RuntimeError(symbol+': '+str(e)) from e
        values=sorted(old.values(),key=lambda r:(r['symbol'],int(r['open_utc_ms'])))
        quality=audit(values)
        if quality['duplicate_keys']:raise ValueError('Duplicated keys after merge')
        write(path,dump(values))
        out['quality']=quality
        result=backtest(values)
        write(BASE/'backtest_latest.json',json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        out['backtest_status']=result['status']
        out['status']='ok'
    except Exception as e:out['errors']['collector']=type(e).__name__+': '+str(e)[:500]
    out['ended_at_utc']=stamp()
    write(BASE/'latest.json',json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':out['status'],'new_rows':out['new_rows'],'errors':out['errors']}),flush=True)
    return 0 if out['status']=='ok' else 1
if __name__=='__main__':sys.exit(main())

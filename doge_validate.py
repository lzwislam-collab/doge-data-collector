#!/usr/bin/env python3
"""Causal DOGE direction baseline audit. Research-only, no trading signals."""
import csv, json, math, os
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
IN=ROOT/'data/history/daily.csv'
OUT=ROOT/'data/validation/latest.json'
DAY=86400000
HORIZONS=(1,7,30)

def calc():
    if not IN.is_file():raise ValueError('Missing historical daily.csv')
    points={}; symbols={}
    with IN.open(newline='',encoding='utf-8') as f:
        rd=csv.DictReader(f)
        for r in rd:
            s=r['symbol'];t=int(r['open_utc_ms']);p=float(r['close'])
            if t%DAY or not math.isfinite(p) or p<=0:raise ValueError('Invalid UTC or close')
            if s not in ('DOGE-USDT','BTC-USDT','ETH-USDT'):raise ValueError('Unexpected symbol')
            key=(s,t)
            if key in symbols:raise ValueError('Duplicate timestamp '+str(key))
            symbols[key]=1
            if s=='DOGE-USDT':points[t]=p
    ks=sorted(points)
    if len(ks)<400:raise ValueError('Insufficient DOGE history')
    gaps=sum(max(0,(b-a)//DAY-1) for a,b in zip(ks,ks[1:]))
    if gaps:raise ValueError(f'Missing DOGE daily candles: {gaps}')
    split=ks[int(len(ks)*.70)]
    stamp=lambda ms:datetime.fromtimestamp(ms/1000,timezone.utc).date().isoformat()
    periods={'2020-2021':('2020-01-01','2021-12-31'),'2022-2023':('2022-01-01','2023-12-31'),'2024-2026':('2024-01-01','2026-12-31')}
    result={'schema_version':1,'status':'ok','source':'OKX public confirmed UTC DOGE-USDT daily candles','generated_at_utc':datetime.now(timezone.utc).isoformat(),'doge_days':len(ks),'start_utc':stamp(ks[0]),'end_utc':stamp(ks[-1]),'missing_internal_days':gaps,'duplicate_keys':0,'holdout_start_utc':stamp(split),'methods':['trailing 30 day momentum sign','always up','prior day return sign'],'horizons':{},'limitations':['Historical-price-only analysis, not investment-rule or trading simulation','No transaction fees, slippage or position sizing; not a profitability estimate','Nonoverlapping horizon samples are low power for 30d','All market regime results are descriptive; 70% chronological holdout is separately identified','No ETF, macro, whale or liquidity inputs; no point-in-time event leakage','No confidence/probability calibration or proven predictive edge']}
    for h in HORIZONS:
        obs=[]
        for t in ks:
            if t-30*DAY not in points or t-DAY not in points or t+h*DAY not in points:continue
            y=points[t+h*DAY]>points[t]
            if points[t+h*DAY]==points[t]:continue
            m=points[t]>points[t-30*DAY]
            if points[t]==points[t-30*DAY]:continue
            d=points[t]>points[t-DAY]
            if points[t]==points[t-DAY]:continue
            obs.append((t,y,m,d))
        def summarize(items):
            n=len(items)
            if n==0:return {'n':0,'momentum_accuracy_pct':None,'always_up_accuracy_pct':None,'prior_day_accuracy_pct':None,'momentum_minus_always_up_pp':None}
            a=sum(y==m for _,y,m,d in items)/n*100
            up=sum(y for _,y,m,d in items)/n*100
            daily=sum(y==d for _,y,m,d in items)/n*100
            return {'n':n,'momentum_accuracy_pct':round(a,2),'always_up_accuracy_pct':round(up,2),'prior_day_accuracy_pct':round(daily,2),'momentum_minus_always_up_pp':round(a-up,2)}
        hold=[x for x in obs if x[0]>=split]
        nonoverlap=[];last_end=-1
        for x in hold:
            if x[0]>last_end:
                nonoverlap.append(x);last_end=x[0]+h*DAY
        regimes={name:summarize([x for x in obs if start<=stamp(x[0])<=end]) for name,(start,end) in periods.items()}
        result['horizons'][str(h)+'d']={'holdout_overlapping':summarize(hold),'holdout_nonoverlapping':summarize(nonoverlap),'regimes_full_history_descriptive':regimes}
    return result

def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    try:data=calc()
    except Exception as e:data={'schema_version':1,'status':'error','generated_at_utc':datetime.now(timezone.utc).isoformat(),'error':type(e).__name__+': '+str(e)[:500]}
    tmp=OUT.with_suffix('.json.tmp');tmp.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');os.replace(tmp,OUT)
    print(json.dumps({'status':data['status'],'doge_days':data.get('doge_days'),'error':data.get('error')}))
    return 0 if data['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())

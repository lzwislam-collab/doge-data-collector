#!/usr/bin/env python3
"""DOGE/BTC/ETH daily-close causal multi-factor walk-forward research audit.
No investment signals; test forecasts are generated with only labels available as of each forecast date.
"""
import csv, json, math, os
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parent
INFILE = ROOT/'data/history/daily.csv'
OUTFILE = ROOT/'data/multifactor/latest.json'
DAY = 86400000
SYMS=('DOGE-USDT','BTC-USDT','ETH-USDT')
HORIZONS=(1,7,30)
FEATURE_NAMES = [f'{s}_{n}' for s in ('DOGE','BTC','ETH') for n in ('ret1','ret7','ret30','vol30')] + ['DOGE_minus_BTC_7d','DOGE_minus_ETH_7d']

def iso(ms):return datetime.fromtimestamp(ms/1000, timezone.utc).date().isoformat()
def load():
    prices={s:{} for s in SYMS};seen=set();count=0
    with INFILE.open(newline='',encoding='utf-8') as f:
        reader=csv.DictReader(f)
        if not reader.fieldnames or not {'symbol','open_utc_ms','close'}.issubset(reader.fieldnames):raise ValueError('Unexpected historical daily.csv schema')
        for r in reader:
            s=r['symbol'];ms=int(r['open_utc_ms']);p=float(r['close'])
            if s not in prices or ms%DAY!=0 or not math.isfinite(p) or p<=0:raise ValueError('Invalid symbol/timestamp/close')
            if (s,ms) in seen:raise ValueError('Duplicate candle key')
            seen.add((s,ms));prices[s][ms]=p;count+=1
    dates=sorted(set.intersection(*(set(x) for x in prices.values())))
    if len(dates)<700:raise ValueError('Fewer than 700 aligned daily prices')
    if any(b-a!=DAY for a,b in zip(dates,dates[1:])):raise ValueError('Gaps in aligned UTC price series')
    return dates,prices,count

def features(dates,prices):
    X=[]; Y={h:[] for h in HORIZONS}; stamps=[]; m={s:np.array([prices[s][d] for d in dates],dtype=float) for s in SYMS}
    for i in range(31,len(dates)-max(HORIZONS)):
        row=[]; returns={}
        for s in SYMS:
            z=m[s]; r1=np.log(z[i]/z[i-1]);r7=np.log(z[i]/z[i-7]);r30=np.log(z[i]/z[i-30]);v=float(np.std(np.diff(np.log(z[i-30:i+1])),ddof=1));row.extend((r1,r7,r30,v));returns[s]=r7
        row.extend([returns['DOGE-USDT']-returns['BTC-USDT'],returns['DOGE-USDT']-returns['ETH-USDT']]);X.append(row);stamps.append(dates[i]);
        for h in HORIZONS:Y[h].append(int(m['DOGE-USDT'][i+h]>m['DOGE-USDT'][i]))
    return np.asarray(stamps,dtype=np.int64),np.asarray(X),{h:np.asarray(y,dtype=int) for h,y in Y.items()}

def evaluate():
    dates,prices,total=load();ts,X,targets=features(dates,prices)
    split=int(len(ts)*.70);holdstart=ts[split];out={'schema_version':1,'status':'ok','generated_at_utc':datetime.now(timezone.utc).isoformat(),'source':'OKX confirmed UTC DOGE/BTC/ETH close data; no future features','input_rows':total,'aligned_days':len(dates),'first_date_utc':iso(dates[0]),'last_date_utc':iso(dates[-1]),'holdout_start_utc':iso(int(holdstart)),'features':FEATURE_NAMES,'method':'expanding-window L2 logistic regression; retrained every 30 calendar days; 30-day minimum feature lookback; training labels require t+h strictly before evaluation t','horizons':{},'limitations':['Prediction formed after UTC daily candle close; execution at that close is not assumed','No ETF, macro, whale or liquidity information until verified historical availability dates exist','Non-overlap is a sensitivity check; low power for 30-day outcomes','No transaction costs, taxes, slippage, profitability or calibrated trading rule','Feature and horizon selection are research choices; no proven predictive edge']}
    for h,y in targets.items():
        models=[];idxs=[];last_fit=-10**9;last_model=None;fit_count=0
        for i in range(split,len(ts)):
            # Outcomes for training sample k become known after close at k+h. Conservative strict inequality.
            eligible=np.flatnonzero(ts[:i]+h*DAY<ts[i]);eligible=eligible[eligible<i]
            if len(eligible)<250 or len(np.unique(y[eligible]))<2:continue
            if i-last_fit>=30 or last_model is None:
                xx=X[eligible];mu=xx.mean(axis=0);sd=xx.std(axis=0);sd[sd<1e-10]=1
                mod=LogisticRegression(C=1.0,max_iter=500,random_state=0)
                mod.fit((xx-mu)/sd,y[eligible]);last_model=(mod,mu,sd);last_fit=i;fit_count+=1
            mod,mu,sd=last_model;p=float(mod.predict_proba(((X[i]-mu)/sd)[None,:])[0,1]);models.append(p>=0.5);idxs.append(i)
        if not idxs:raise ValueError(f'No test predictions for {h}d')
        idxs=np.asarray(idxs);pred=np.asarray(models,dtype=bool);truth=y[idxs].astype(bool)
        trend=X[idxs,2]>0 # DOGE trailing 30d log return
        prior=X[idxs,0]>0
        def agg(mask):
            ii=np.flatnonzero(mask);n=len(ii)
            return {'n':n,'multifactor_accuracy_pct':round(float(np.mean(pred[ii]==truth[ii]))*100,2) if n else None,'momentum_accuracy_pct':round(float(np.mean(trend[ii]==truth[ii]))*100,2) if n else None,'always_up_accuracy_pct':round(float(np.mean(truth[ii]))*100,2) if n else None,'prior_day_accuracy_pct':round(float(np.mean(prior[ii]==truth[ii]))*100,2) if n else None}
        keep=[];lastend=-1
        for j,k in enumerate(idxs):
            if int(ts[k])>lastend:keep.append(j);lastend=int(ts[k])+h*DAY
        mask=np.zeros(len(idxs),dtype=bool);mask[keep]=True
        out['horizons'][f'{h}d']={'expanding_fits':fit_count,'holdout_overlapping':agg(np.ones(len(idxs),dtype=bool)),'holdout_nonoverlapping':agg(mask),'test_first_utc':iso(int(ts[idxs[0]])),'test_last_utc':iso(int(ts[idxs[-1]]))}
    return out

def main():
    OUTFILE.parent.mkdir(parents=True,exist_ok=True)
    try:r=evaluate()
    except Exception as e:r={'schema_version':1,'status':'error','generated_at_utc':datetime.now(timezone.utc).isoformat(),'error':type(e).__name__+': '+str(e)[:500]}
    tmp=OUTFILE.with_suffix('.json.tmp');tmp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(tmp,OUTFILE)
    print(json.dumps({'status':r['status'],'error':r.get('error'),'aligned_days':r.get('aligned_days')}),flush=True)
    return 0 if r['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())

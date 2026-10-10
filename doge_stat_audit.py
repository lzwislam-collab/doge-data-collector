#!/usr/bin/env python3
"""DOGE multi-factor probabilistic out-of-sample audit. Research only.
Reads existing OKX historical daily.csv. Never generates trading recommendations.
"""
import csv
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parent
INFILE = ROOT / 'data/history/daily.csv'
OUTFILE = ROOT / 'data/stat_audit/latest.json'
DAY = 86_400_000
SYMS = ('DOGE-USDT', 'BTC-USDT', 'ETH-USDT')
HORIZONS = (1, 7, 30)
RNG_SEED = 20261010

def dt(t):
    return datetime.fromtimestamp(int(t)/1000, tz=timezone.utc).date().isoformat()

def load():
    values = {s:{} for s in SYMS}
    count = 0
    with INFILE.open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        required = {'symbol','open_utc_ms','open','high','low','close','volume_base'}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError('Invalid daily.csv columns')
        for r in reader:
            s = r['symbol']; t = int(r['open_utc_ms'])
            if s not in values or t % DAY != 0 or t < 0:
                raise ValueError('Invalid symbol or daily UTC timestamp')
            k = values[s]
            if t in k:
                raise ValueError('Duplicate symbol-date key')
            nums = [float(r[c]) for c in ('open','high','low','close','volume_base')]
            o,h,l,c,v = nums
            if not all(math.isfinite(x) for x in nums) or min(o,l,c)>h or l>min(o,c) or min(o,l,c)<=0 or v<0:
                raise ValueError('Bad OHLCV')
            k[t] = c; count += 1
    common = sorted(set.intersection(*(set(values[s]) for s in SYMS)))
    if len(common) < 700:
        raise ValueError('Insufficient aligned history')
    if any(b-a != DAY for a,b in zip(common,common[1:])):
        raise ValueError('Missing daily rows in aligned history')
    return np.array(common, dtype=np.int64), {s:np.array([values[s][t] for t in common]) for s in SYMS},count

def make_features(ts,p):
    X = []; idx=[]
    for i in range(31,len(ts)):
        row=[]; rel={}
        for s in SYMS:
            a=p[s]; r1=math.log(a[i]/a[i-1]); r7=math.log(a[i]/a[i-7]); r30=math.log(a[i]/a[i-30])
            volatility=float(np.std(np.diff(np.log(a[i-30:i+1])),ddof=1))
            row.extend((r1,r7,r30,volatility));rel[s]=r7
        row.extend((rel['DOGE-USDT']-rel['BTC-USDT'],rel['DOGE-USDT']-rel['ETH-USDT']))
        X.append(row);idx.append(i)
    return np.array(idx),np.asarray(X,dtype=float)

def metrics(y,p,baseline):
    y=np.asarray(y,dtype=float);p=np.asarray(p,dtype=float)
    pp=np.clip(p,1e-8,1-1e-8)
    brier=float(np.mean((p-y)**2));logloss=float(-np.mean(y*np.log(pp)+(1-y)*np.log(1-pp)))
    bins=[];ece=0.0
    for k in range(10):
        mask=(p>=k/10)&((p<(k+1)/10) if k<9 else (p<=1))
        if not np.any(mask):continue
        freq=float(np.mean(y[mask]));confidence=float(np.mean(p[mask]));n=int(mask.sum())
        bins.append({'bin':k,'n':n,'predicted_up_probability':round(confidence,4),'observed_up_frequency':round(freq,4)})
        ece+=n/len(y)*abs(freq-confidence)
    acc=lambda guess:round(100*float(np.mean(guess==y)),2)
    return {'n':int(len(y)),'accuracy_pct':acc(p>=.5),'momentum_accuracy_pct':acc(baseline),'always_up_accuracy_pct':acc(np.ones(len(y))), 'brier':round(brier,6),'log_loss':round(logloss,6),'ece_10bin':round(ece,6),'actual_up_rate':round(float(np.mean(y)),5),'mean_predicted_up_probability':round(float(np.mean(p)),5),'calibration_bins':bins}

def block_bootstrap_diff(y,p,m,block,seed):
    """Paired moving-block bootstrap. Reports sensitivity interval, not a causal proof."""
    yy=np.asarray(y).astype(bool);pred=np.asarray(p)>=.5; mom=np.asarray(m).astype(bool)
    delta=(pred==yy).astype(float)-(mom==yy).astype(float)
    n=len(delta);rng=np.random.default_rng(seed)
    B=1500;boot=np.empty(B)
    for b in range(B):
        start=rng.integers(0,n,size=int(math.ceil(n/block)))
        ind=np.concatenate([(np.arange(block)+s)%n for s in start])[:n]
        boot[b]=100*float(np.mean(delta[ind]))
    q=np.quantile(boot,[.025,.975]);point=100*float(np.mean(delta))
    return {'multifactor_minus_momentum_pp':round(point,3),'moving_block_length_days':int(block),'bootstrap_repetitions':B,'bootstrap_95pct_interval_pp':[round(float(q[0]),3),round(float(q[1]),3)],'bootstrap_interval_excludes_zero':bool(q[0]>0 or q[1]<0)}

def evaluate():
    ts,p,total=load();idx,X=make_features(ts,p);spl=int(len(X)*.70)
    out={'schema_version':1,'status':'ok','source':'OKX confirmed 1Dutc DOGE/BTC/ETH spot prices','generated_at_utc':datetime.now(timezone.utc).isoformat(),'input_rows':total,'aligned_days':len(ts),'first_date_utc':dt(ts[0]),'last_date_utc':dt(ts[-1]),'holdout_start_utc':dt(ts[idx[spl]]),'algorithm':'Expanding logistic regression (L2,C=1), re-fit every 30 prediction rows; normalizer fit on eligible past samples only','horizons':{},'limitations':['Target is sign of future close return, not a trade','Probabilities are uncalibrated logistic outputs; calibration is measured, not assumed','Brier/log-loss must be interpreted relative to a properly trained probability baseline','Bootstrap intervals are exploratory, depend on block length, and do not account for multiple hypothesis testing or researcher choices','No ETF, macro, liquidation, news, or unverified historical release-time features','Historical data ends on the last completed UTC day; horizons use distinct observed future closes','No future outcomes may be used in training at prediction time','No fees, position sizing, slippage, or profitability validation']}
    for h in HORIZONS:
        eligible_idx=idx[idx+h < len(ts)]
        eligible_count=len(eligible_idx)
        Xi=X[:eligible_count]; ti=ts[eligible_idx]
        y=(p['DOGE-USDT'][eligible_idx+h] > p['DOGE-USDT'][eligible_idx]).astype(int)
        # Fixed holdout boundary based on all feature-eligible dates, consistent across horizons.
        test_start=spl
        if test_start>=eligible_count: raise ValueError('Empty holdout')
        predictions=[];testids=[];lastfit=-100000; fitted=None;nf=0
        for j in range(test_start,eligible_count):
            # Label at k becomes available after the k+h candle has closed.
            train=np.flatnonzero(ti[:j] + h*DAY < ti[j])
            if len(train)<250 or len(np.unique(y[train]))<2:continue
            if fitted is None or j-lastfit >=30:
                xx=Xi[train]; mu=xx.mean(axis=0);sd=xx.std(axis=0);sd[sd<1e-10]=1.
                model=LogisticRegression(C=1.,max_iter=500,random_state=0)
                model.fit((xx-mu)/sd,y[train]);fitted=(model,mu,sd);lastfit=j;nf+=1
            model,mu,sd=fitted
            predictions.append(float(model.predict_proba(((Xi[j]-mu)/sd)[None,:])[0,1]))
            testids.append(j)
        if not testids:raise ValueError('No holdout predictions')
        testids=np.array(testids);prob=np.array(predictions);truth=y[testids]
        momentum=Xi[testids,2]>0
        daily=Xi[testids,0]>0
        # Keep prediction dates at least h days apart; adjacent 1d outcomes do not overlap.
        keep=[];last=-10**15
        for j,k in enumerate(testids):
            t=ti[k]
            if t-last>=h*DAY:keep.append(j);last=t
        keep=np.array(keep,dtype=int)
        full=metrics(truth,prob,momentum)
        sample=metrics(truth[keep],prob[keep],momentum[keep])
        result={'fit_count':nf,'full_holdout':full,'nonoverlapping':sample,'paired_block_bootstrap':block_bootstrap_diff(truth,prob,momentum,max(7,h),RNG_SEED+h),'last_scored_prediction_date_utc':dt(ti[testids[-1]]),'last_scored_target_date_utc':dt(ti[testids[-1]]+h*DAY),'nonoverlap_stride_days':h}
        # Remove detailed bins from non-overlap low-sample results to reduce output ambiguity.
        result['nonoverlapping'].pop('calibration_bins')
        result['full_holdout']['prior_day_accuracy_pct']=round(100*float(np.mean(daily==truth)),2)
        out['horizons'][f'{h}d']=result
    return out

def main():
    OUTFILE.parent.mkdir(parents=True,exist_ok=True)
    try: obj=evaluate()
    except Exception as e:obj={'schema_version':1,'status':'error','generated_at_utc':datetime.now(timezone.utc).isoformat(),'error':f'{type(e).__name__}: {str(e)[:500]}'}
    tmp=OUTFILE.with_suffix('.json.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(tmp,OUTFILE)
    print(json.dumps({'status':obj['status'],'error':obj.get('error'),'aligned_days':obj.get('aligned_days')}))
    return 0 if obj['status']=='ok' else 1

if __name__=='__main__':raise SystemExit(main())

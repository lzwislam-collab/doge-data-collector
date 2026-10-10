#!/usr/bin/env python3
"""Causal probability-baseline audit for DOGE; research only."""
import json, math, os
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
import doge_stat_audit as base

OUT=Path(__file__).resolve().parent/'data/prob_baseline/latest.json'

def scores(y,p):
    y=np.asarray(y,dtype=float);p=np.asarray(p,dtype=float)
    p=np.clip(p,1e-8,1-1e-8)
    return {'n':len(y),'brier':round(float(np.mean((p-y)**2)),6),
            'log_loss':round(float(np.mean(-y*np.log(p)-(1-y)*np.log(1-p))),6),
            'direction_accuracy_pct':round(float(np.mean((p>=.5)==y))*100,2)}

def audit():
    dates,price,total=base.load()
    idx,X=base.make_features(dates,price)
    split=int(len(X)*.70)
    out={'schema_version':1,'status':'ok','generated_at_utc':datetime.now(timezone.utc).isoformat(),
         'source':'OKX confirmed UTC DOGE/BTC/ETH daily; compare probabilistic historical-frequency benchmark',
         'input_rows':total,'aligned_days':len(dates),'data_end_utc':base.dt(dates[-1]),
         'holdout_start_utc':base.dt(dates[idx[split]]),'horizons':{},
         'caveats':['In-sample target labels require future close and are excluded until actually observable',
                    'Comparison uses same point-in-time eligible labels for both models',
                    'Probability benchmark is expanding historical up-frequency with Laplace smoothing',
                    'Overlapping 7d and 30d targets reduce independent information',
                    'No fees or executable trading rules; not evidence of profit',
                    'Candidate model and features previously selected; not a pristine untouched holdout']}
    for h in (1,7,30):
        eligible=idx[idx+h<len(dates)]
        ti=dates[eligible]; xx=X[:len(eligible)]
        yy=(price['DOGE-USDT'][eligible+h]>price['DOGE-USDT'][eligible]).astype(int)
        preds=[];baseline=[];times=[];truth=[];prev_fit=-100000;fit=None
        for j in range(split,len(eligible)):
            # Strictly before the prediction instant, including target candle close.
            tr=np.flatnonzero(ti[:j]+h*base.DAY<ti[j])
            if len(tr)<250 or len(np.unique(yy[tr]))<2:continue
            base_p=(float(np.sum(yy[tr]))+1)/(len(tr)+2)
            if fit is None or j-prev_fit>=30:
                z=xx[tr];mean=z.mean(axis=0);sd=z.std(axis=0);sd[sd<1e-10]=1
                model=LogisticRegression(C=1.,max_iter=500,random_state=0)
                model.fit((z-mean)/sd,yy[tr]);fit=(model,mean,sd);prev_fit=j
            model,mean,sd=fit
            mp=float(model.predict_proba(((xx[j]-mean)/sd)[None,:])[0,1])
            preds.append(mp);baseline.append(base_p);times.append(int(ti[j]));truth.append(int(yy[j]))
        truth=np.asarray(truth);p=np.asarray(preds);b=np.asarray(baseline);times=np.asarray(times)
        if not len(truth):raise ValueError('no holdout '+str(h))
        subset=[k for k in range(len(times)) if times[k]% (h*base.DAY)==times[0]%(h*base.DAY)]
        # Use a fixed phase so sampled target intervals do not overlap.
        ys=truth[subset];ps=p[subset];bs=b[subset]
        a=scores(truth,p);z=scores(truth,b);a2=scores(ys,ps);z2=scores(ys,bs)
        phases={}
        for name,start,end in [('2024','2024-01-01','2024-12-31'),('2025','2025-01-01','2025-12-31'),('2026','2026-01-01','2026-12-31')]:
            keep=[i for i,t in enumerate(times) if start<=base.dt(t)<=end]
            if keep:phases[name]={'model':scores(truth[keep],p[keep]),'baseline':scores(truth[keep],b[keep])}
        out['horizons'][f'{h}d']={'full_holdout':{'model':a,'historical_frequency':z,
            'brier_improvement_vs_baseline':round(z['brier']-a['brier'],6),
            'log_loss_improvement_vs_baseline':round(z['log_loss']-a['log_loss'],6)},
            'nonoverlapping':{'model':a2,'historical_frequency':z2},
            'calendar_years_descriptive':phases,
            'first_prediction_utc':base.dt(times[0]),'last_scored_prediction_utc':base.dt(times[-1]),
            'last_scored_target_utc':base.dt(times[-1]+h*base.DAY)}
    return out

def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    try:r=audit()
    except Exception as e:r={'status':'error','generated_at_utc':datetime.now(timezone.utc).isoformat(),'error':f'{type(e).__name__}: {e}'[:500]}
    temp=OUT.with_suffix('.json.tmp');temp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');os.replace(temp,OUT)
    print(json.dumps({'status':r['status'],'error':r.get('error')}))
    return int(r['status']!='ok')
if __name__=='__main__':raise SystemExit(main())

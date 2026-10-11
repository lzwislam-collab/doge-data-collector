#!/usr/bin/env python3
"""DOGE V4.2 fixed-rule causal directional benchmarks. Research, not live signals."""
import csv,json,math,os
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data/direction_v42/latest.json'
HOUR=3600000
DAY=86400000
MODELS=('expanding_majority','doge_momentum','doge_reversal','btc_momentum')

def read_csv(path,step):
 prices={'DOGE-USDT':{},'BTC-USDT':{},'ETH-USDT':{}}
 with path.open(newline='',encoding='utf-8') as f:
  reader=csv.DictReader(f)
  needed={'symbol','open_utc_ms','close'}
  if not needed.issubset(reader.fieldnames or []):raise ValueError('missing price columns')
  for row in reader:
   s=row['symbol']
   if s not in prices:continue
   t=int(row['open_utc_ms']);p=float(row['close'])
   if t%step or not math.isfinite(p) or p<=0:raise ValueError('invalid timestamp/price')
   if t in prices[s]:raise ValueError('duplicate symbol/time '+s+str(t))
   prices[s][t]=p
 for sym,series in prices.items():
  stamps=sorted(series)
  if not stamps or any(b-a!=step for a,b in zip(stamps,stamps[1:])):
   raise ValueError('missing internal price data: '+sym)
 common=set.intersection(*(set(series) for series in prices.values()))
 ts=sorted(common)
 if not ts:raise ValueError('no aligned timestamps')
 if any(b-a!=step for a,b in zip(ts,ts[1:])):
  raise ValueError('missing internal synchronized bars')
 if any(max(series)!=ts[-1] for series in prices.values()):
  raise ValueError('stale cross-asset last close')
 return ts,{s:[prices[s][t] for t in ts] for s in prices}

def stats(rows):
 if not rows:return {'n':0}
 n=len(rows);truth=[x['up'] for x in rows]
 p_up=sum(truth)/n
 results={'n':n,'observed_up_pct':round(100*p_up,2),
  'observed_down_or_equal_pct':round(100*(1-p_up),2)}
 for model in MODELS:
  predictions=[x[model] for x in rows]
  correct=sum(int(p==y) for p,y in zip(predictions,truth))
  positives=sum(y==1 for y in truth);negatives=n-positives
  up_recall=sum(p==1 and y==1 for p,y in zip(predictions,truth))/positives if positives else None
  down_recall=sum(p==0 and y==0 for p,y in zip(predictions,truth))/negatives if negatives else None
  balanced=.5*(up_recall+down_recall) if up_recall is not None and down_recall is not None else None
  results[model]={'accuracy_pct':round(100*correct/n,2),
    'correct':correct,'up_recall_pct':round(100*up_recall,2) if up_recall is not None else None,
    'down_recall_pct':round(100*down_recall,2) if down_recall is not None else None,
    'balanced_accuracy_pct':round(100*balanced,2) if balanced is not None else None}
 return results

def audit_horizon(ts,price,h,min_training):
 doge=price['DOGE-USDT'];btc=price['BTC-USDT']
 size=len(ts)
 if size<min_training+h+150:raise ValueError('insufficient history for horizon '+str(h))
 # Never inspect labels whose target close is after the current prediction anchor.
 forward=[int(doge[j+h]>doge[j]) for j in range(size-h)]
 pref=[0]
 for v in forward:pref.append(pref[-1]+v)
 split=max(min_training+h,int(size*.75))
 rows=[]
 for i in range(split,size-h):
  eligible=i-h+1
  if eligible<min_training:continue
  prior_up=pref[eligible]
  history_majority=1 if 2*prior_up>=eligible else 0
  y=forward[i]
  trailing_doge=int(doge[i]>doge[i-h])
  trailing_btc=int(btc[i]>btc[i-h])
  rows.append({'idx':i,'up':y,'expanding_majority':history_majority,
   'doge_momentum':trailing_doge,'doge_reversal':1-trailing_doge,'btc_momentum':trailing_btc})
 if not rows:raise ValueError('empty holdout')
 # Fixed first-phase non-overlapping target periods; NOT independent if serially correlated.
 non=[];prev=-10**12
 for r in rows:
  if r['idx']-prev>=h:non.append(r);prev=r['idx']
 return {'all_overlapping':stats(rows),'fixed_phase_nonoverlap':stats(non),
  'anchor_first_utc':datetime.fromtimestamp(ts[rows[0]['idx']]/1000,timezone.utc).isoformat(),
  'anchor_last_utc':datetime.fromtimestamp(ts[rows[-1]['idx']]/1000,timezone.utc).isoformat(),
  'anchor_label':'last CONFIRMED candle close, not issue-time price',
  'forecast_window_definition':'anchor close vs h subsequent confirmed candle closes',
  'holdout_reused_for_research':True,'models_precommitted_for_future_shadow_comparison':False,
  'research_limit':'Fixed-rule descriptive backtest; not a pristine holdout and not validated forward performance'}

def evaluate():
 hourly_ts,hourly=read_csv(ROOT/'data/hourly_research/ohlc.csv',HOUR)
 daily_ts,daily=read_csv(ROOT/'data/history/daily.csv',DAY)
 data={'schema_version':1,'status':'ok','generated_utc':datetime.now(timezone.utc).isoformat(),
  'scope':'PRIMARY V4.2 DOGE UP/DOWN DIRECTION; extreme move only a subsidiary risk module',
  'target_accuracy_pct':70,'validated_accuracy_70pct':False,'prediction_edge_proven':False,
  'price_direction_label':'up if target close > anchor close; otherwise down/equal',
  'models':list(MODELS),'no_training_on_future_labels':True,
  'sources':{'hourly_last_closed_start_utc':datetime.fromtimestamp(hourly_ts[-1]/1000,timezone.utc).isoformat(),
  'daily_last_closed_start_utc':datetime.fromtimestamp(daily_ts[-1]/1000,timezone.utc).isoformat(),
  'hourly_rows':len(hourly_ts),'daily_rows':len(daily_ts)},
  'horizons':{},'caveats':[
   'All h-step historical returns begin at earlier closed-candle anchor; this is not exact issuance-to-target forecasting',
   'The fixed set of rules is a research screen; all model selection requires new holdout and future prospective logs',
   'h=24 hourly and 7/30 daily returns overlap; report nonoverlap sample counts, do not assume IID',
   'Every valid prediction is up or down/equal; no abstentions or coverage manipulation',
   'No evidence supports 70pct accuracy; reported scores are not trade profitability']}
 data['horizons']['1h']={'candle_unit':'hour',**audit_horizon(hourly_ts,hourly,1,1000)}
 data['horizons']['24h']={'candle_unit':'hour',**audit_horizon(hourly_ts,hourly,24,1000)}
 data['horizons']['7d']={'candle_unit':'UTC day',**audit_horizon(daily_ts,daily,7,365)}
 data['horizons']['30d']={'candle_unit':'UTC day',**audit_horizon(daily_ts,daily,30,365)}
 return data

def main():
 try:r=evaluate()
 except Exception as exc:r={'schema_version':1,'status':'blocked','generated_utc':datetime.now(timezone.utc).isoformat(),
  'error':type(exc).__name__+': '+str(exc)[:320],'validated_accuracy_70pct':False}
 OUT.parent.mkdir(parents=True,exist_ok=True)
 tmp=OUT.with_suffix('.json.tmp');tmp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');os.replace(tmp,OUT)
 print(json.dumps({'status':r['status'],'horizons':{k:v['all_overlapping']['n'] for k,v in r.get('horizons',{}).items()},'error':r.get('error')}))
 return 0 if r['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())

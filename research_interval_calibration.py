#!/usr/bin/env python3
"""Evaluate strictly backward-looking PIT recalibration of V4.1 intervals.
Research-only challenger; NEVER overwrite production prospective registry.
"""
import bisect,json,math,os,random,statistics
from datetime import datetime,timezone
from pathlib import Path
from forecast_v41 import ROOT,BASE,DAY_MS,HORIZONS,TRAIN,quantile,load_prices,interval_score,atomic

LOOKBACK=365
MIN_CALIBRATION_EVENTS=180
BOOTS=500
SEED=20261011

def pit_of_matured_forecast(prices,j,h):
 # The actual forward label at j+h is matured by the observing anchor >= j+h.
 stop=j-h
 if stop<TRAIN-1:return None
 training=sorted(math.log(prices[i+h]/prices[i]) for i in range(stop-TRAIN+1,stop+1))
 realized=math.log(prices[j+h]/prices[j])
 return (bisect.bisect_left(training,realized)+bisect.bisect_right(training,realized)+1)/(2*(len(training)+1))

def pit_distribution(prices,anchor,h,cache):
 # Label from origin j can only be used after target close at j+h.
 latest=anchor-h
 earliest=max(TRAIN+h,latest-LOOKBACK+1)
 pits=[]
 for j in range(earliest,latest+1):
  if j not in cache:cache[j]=pit_of_matured_forecast(prices,j,h)
  if cache[j] is not None:pits.append(cache[j])
 if len(pits)<MIN_CALIBRATION_EVENTS:return None
 lo=quantile(sorted(pits),.10);hi=quantile(sorted(pits),.90)
 return {'p10_pit':lo,'p90_pit':hi,'calibration_n':len(pits),
         'approx_nonoverlap_n':max(1,len(pits)//h)}

def candidate(prices,anchor,h,cache):
 # Model at anchor uses ONLY returns whose target <= anchor.
 stop=anchor-h
 if stop<TRAIN-1:return None
 dist=sorted(math.log(prices[k+h]/prices[k]) for k in range(stop-TRAIN+1,stop+1))
 standard=(quantile(dist,.1),quantile(dist,.9))
 past=pit_distribution(prices,anchor,h,cache)
 if past is None:return None
 tuned=(quantile(dist,past['p10_pit']),quantile(dist,past['p90_pit']))
 return {'baseline':standard,'challenger':tuned,'calibration':past}

def paired_bootstrap_deltas(deltas,h):
 # Paired circular moving-block bootstrap, exploratory not selection proof.
 n=len(deltas);rnd=random.Random(SEED+h)
 means=[]
 length=max(7,h)
 for _ in range(BOOTS):
  indices=[]
  while len(indices)<n:
   s=rnd.randrange(n)
   indices.extend((s+i)%n for i in range(length))
  means.append(sum(deltas[i] for i in indices[:n])/n)
 means.sort()
 return {'mean_challenger_minus_baseline':round(sum(deltas)/n,7),
  'block_days':length,'bootstrap_repetitions':BOOTS,
  '95pct_interval':[round(means[int(.025*BOOTS)],7),round(means[int(.975*BOOTS)-1],7)],
  'note':'Not an untouched holdout and ignores researcher-selection multiplicity.'}

def assess(prices,h):
 n=len(prices);first=max(TRAIN+LOOKBACK+2*h,n-h-365)
 cache={};rows=[]
 for anchor in range(first,n-h):
  q=candidate(prices,anchor,h,cache)
  if q is None:continue
  actual=math.log(prices[anchor+h]/prices[anchor])
  r={'index':anchor,'actual':actual}
  for name in ('baseline','challenger'):
   low,high=q[name]
   r[name+'_hit']=int(low<=actual<=high)
   r[name+'_score']=interval_score(low,high,actual)
   r[name+'_width']=high-low
  rows.append(r)
 if not rows:raise ValueError('no evaluation results for '+str(h))
 def summarize(subset):
  size=len(subset)
  if not size:return {'n':0}
  avg=lambda field:sum(r[field] for r in subset)/size
  out={'n':size}
  for name in ('baseline','challenger'):
   out[name]={'coverage_pct':round(avg(name+'_hit')*100,2),
    'interval_score_log_pct':round(avg(name+'_score')*100,4),
    'mean_width_log_pct':round(avg(name+'_width')*100,4)}
  out['paired_score_diff_log_pct']=round((avg('challenger_score')-avg('baseline_score'))*100,4)
  return out
 nonoverlap=[];prev=-10**8
 for r in rows:
  if r['index']-prev>=h:nonoverlap.append(r);prev=r['index']
 boot=paired_bootstrap_deltas([100*(r['challenger_score']-r['baseline_score']) for r in rows],h)
 latest=candidate(prices,n-1,h,cache)
 return {'all_overlapping':summarize(rows),'nonoverlap':summarize(nonoverlap),
  'paired_block_bootstrap':boot,'latest_research_candidate':latest,
  'automatic_promotion':False,'model_edge_validated':False,
  'interpretation':'Coverage, width and interval score reported together; never select solely by coverage.'}

def evaluate():
 times,prices=load_prices(require_fresh=False)
 now=datetime.now(timezone.utc)
 item={'schema_version':1,'status':'ok','evaluated_utc':now.isoformat(),
  'history_cutoff_utc':datetime.fromtimestamp(times[-1]/1000,timezone.utc).date().isoformat(),
  'baseline_model':'doge_empirical_730d_v4_1',
  'candidate':'rolling PIT quantile recalibration from previously matured 365d observations',
  'research_only':True,'model_edge_validated':False,'production_model_unchanged':True,
  'pits_require_actual_target_close_before_calibration':True,
  'limitations':['Researcher has examined this recent historical period previously, not untouched holdout',
   '365 adjacent h-day outcomes overlap; 30d effective sample very low',
   'PIT calibration can narrow historical intervals without improving future reliability',
   'No auto-promotion; immutable original forecasts untouched'],'horizons':{}}
 for h in HORIZONS:item['horizons'][str(h)+'d']=assess(prices,h)
 return item

def main():
 out=BASE/'calibration_research.json'
 try:item=evaluate()
 except Exception as e:item={'status':'blocked','evaluated_utc':datetime.now(timezone.utc).isoformat(),
    'reason':type(e).__name__+': '+str(e)[:300],'model_edge_validated':False}
 atomic(out,json.dumps(item,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':item['status'],'reason':item.get('reason')}))
 return 0 if item['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())

#!/usr/bin/env python3
"""Score registered V4.1 interval forecasts only after confirmed target close."""
import csv,json,math,os,hashlib
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
from forecast_v41 import ROOT,BASE,COLS,MODEL,atomic,load_prices,interval_score,verify_append_only,iso,DAY_MS,MAX_REGISTRATION_LATENCY_MIN

def score_row(r,observed,now):
 anchor=date.fromisoformat(r['anchor_date_utc']);target=date.fromisoformat(r['target_date_utc'])
 created=datetime.fromisoformat(r['registered_utc'].replace('Z','+00:00'))
 horizon=int(r['horizon_days'])
 anchor_closed_at=datetime.combine(anchor+timedelta(days=1),datetime.min.time(),timezone.utc)
 latency_min=(created-anchor_closed_at).total_seconds()/60
 if not 0<=latency_min<=MAX_REGISTRATION_LATENCY_MIN:
  return 'quarantined','prospective_registration_too_late_or_before_anchor_close'
 if (created.tzinfo is None or created.utcoffset()!=timedelta(0) or horizon not in (1,7,30)
     or created.date()!=anchor+timedelta(days=1) or target!=anchor+timedelta(days=horizon)
     or created>=datetime.combine(target+timedelta(days=1),datetime.min.time(),timezone.utc)
     or r['model_id']!=MODEL):
  return 'quarantined','invalid prospective time or model identity'
 a=float(r['anchor_close']);lo,med,hi=(float(r[k]) for k in ('p10','p50','p90'))
 blo,bmed,bhi=(float(r[k]) for k in ('baseline_p10','baseline_p50','baseline_p90'))
 p=float(r['historical_up_frequency']);bp=float(r['baseline_up_frequency'])
 if not (all(math.isfinite(x) and x>0 for x in (a,lo,med,hi,blo,bmed,bhi))
     and lo<=med<=hi and blo<=bmed<=bhi and 0<p<1 and 0<bp<1):
  return 'quarantined','invalid probability or price bands'
 digest=hashlib.sha256((str(anchor)+':'+format(a,'.12g')).encode()).hexdigest()
 if digest!=r['input_digest']:return 'quarantined','anchor digest mismatch'
 if anchor not in observed or abs(observed[anchor]-a)>1e-10:
  return 'quarantined','anchor close revised or unavailable'
 if now.date()<=target or target not in observed:return 'pending','future or unpublished target day'
 if any(anchor+timedelta(days=i) not in observed for i in range(horizon+1)):
  return 'quarantined','missing intermediate UTC daily close'
 actual=observed[target];rtn=math.log(actual/a);y=int(actual>a)
 calc=lambda lower,upper:interval_score(math.log(lower/a),math.log(upper/a),rtn)*100
 return 'scored',{'forecast_id':r['forecast_id'],'horizon_days':horizon,
   'anchor_date_utc':str(anchor),'target_date_utc':str(target),'realized_target_close':actual,
   'realized_up':y,'elapsed_target_minutes_before_forecast':round(latency_min,2),
   'warning':'Close-to-close horizon began before issued_at_utc; not a full forward period after issuance',
   'interval_hit':lo<=actual<=hi,'baseline_interval_hit':blo<=actual<=bhi,
   'interval_score_log_pct':round(calc(lo,hi),6),
   'baseline_interval_score_log_pct':round(calc(blo,bhi),6),
   'up_brier':round((p-y)**2,8),'baseline_up_brier':round((bp-y)**2,8)}

def score(now=None):
 if now is None:now=datetime.now(timezone.utc)
 times,prices=load_prices(False,now)
 observed={datetime.fromtimestamp(t/1000,timezone.utc).date():p for t,p in zip(times,prices)}
 path=BASE/'registry.csv'
 registry={}
 with path.open(newline='',encoding='utf-8') as file:
  reader=csv.DictReader(file)
  if reader.fieldnames!=COLS:raise ValueError('V4.1 registry schema mismatch')
  for r in reader:
   fid=r['forecast_id']
   if fid in registry:raise ValueError('duplicate forecast '+fid)
   registry[fid]=r
 verify_append_only(registry)
 result={'schema_version':1,'status':'ok','run_utc':now.isoformat(),'source_cutoff_utc':iso(times[-1]),
  'source':'OKX confirmed 1Dutc DOGE-USDT close; immutable key registry','research_only':True,
  'model_edge_proven':False,'claim_permission':'NO_VALIDATED_EDGE',
  'registered':len(registry),'scored':[],'pending':[],'quarantined':[],'horizon_summary':{}}
 for fid,r in sorted(registry.items()):
  try:state,value=score_row(r,observed,now)
  except Exception as e:state,value='quarantined',type(e).__name__+': '+str(e)[:160]
  if state=='scored':result['scored'].append(value)
  else:result[state].append({'forecast_id':fid,'reason':value})
 for h in (1,7,30):
  rows=[x for x in result['scored'] if x['horizon_days']==h]
  unique=[];last=None
  for row in sorted(rows,key=lambda x:x['anchor_date_utc']):
   t=date.fromisoformat(row['anchor_date_utc'])
   if last is None or (t-last).days>=h:unique.append(row);last=t
  def metrics(items):
   if not items:return {'n':0}
   n=len(items)
   avg=lambda key:round(sum(x[key] for x in items)/n,6)
   return {'n':n,'rolling_interval_coverage_pct':round(100*sum(x['interval_hit'] for x in items)/n,2),
    'expanding_interval_coverage_pct':round(100*sum(x['baseline_interval_hit'] for x in items)/n,2),
    'rolling_interval_score_log_pct':avg('interval_score_log_pct'),
    'expanding_interval_score_log_pct':avg('baseline_interval_score_log_pct'),
    'rolling_up_brier':avg('up_brier'),'expanding_up_brier':avg('baseline_up_brier')}
  result['horizon_summary'][str(h)+'d']={'all_overlapping':metrics(rows),
    'nonoverlap_fixed_phase':metrics(unique),'claim_allowed':False}
 result['counts']={'scored':len(result['scored']),'pending':len(result['pending']),
   'quarantined':len(result['quarantined'])}
 if sum(result['counts'].values())!=result['registered']:raise ValueError('classification count mismatch')
 return result

def main():
 try:obj=score()
 except Exception as e:
  obj={'schema_version':1,'status':'blocked','run_utc':datetime.now(timezone.utc).isoformat(),
   'reason':type(e).__name__+': '+str(e)[:350],'claim_permission':'BLOCKED'}
 atomic(BASE/'scores.json',json.dumps(obj,indent=2)+'\n')
 print(json.dumps({'status':obj['status'],'counts':obj.get('counts'),'reason':obj.get('reason')}))
 return 0 if obj['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())

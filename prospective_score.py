#!/usr/bin/env python3
"""Score ONLY genuinely prospective DOGE registered probabilities; quarantine invalid timestamps."""
import csv,json,math,os
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data'/'prospective'/'scores.json'
def parse(s):return datetime.fromisoformat(s.replace('Z','+00:00'))
def main():
 results={'schema_version':1,'run_utc':datetime.now(timezone.utc).isoformat(),'status':'error',
  'source':'OKX confirmed daily data/history/daily.csv','scores':[],
  'quarantined':[],'pending':[],'errors':[],
  'claim_permission':'NO_VALIDATED_EDGE','limitations':['prospective forecasts only; no strategy profitability','registration must predate end of target day']}
 try:
  price={}
  with (ROOT/'data/history/daily.csv').open(newline='',encoding='utf-8') as f:
   for r in csv.DictReader(f):
    if r['symbol']=='DOGE-USDT':
     dt=datetime.fromtimestamp(int(r['open_utc_ms'])/1000,timezone.utc).date()
     if dt in price:raise ValueError('duplicate DOGE daily timestamp')
     price[dt]=float(r['close'])
  seen=set()
  with (ROOT/'data/prospective/registry.csv').open(newline='',encoding='utf-8') as f:
   for r in csv.DictReader(f):
    fid=r['forecast_id']
    if fid in seen:raise ValueError('duplicate forecast ID')
    seen.add(fid)
    reg=parse(r['registered_utc'])
    anchor=date.fromisoformat(r['anchor_date_utc'])
    target=date.fromisoformat(r['target_date_utc'])
    horizon=int(r['horizon_days'])
    p=float(r['p_up'])
    if reg.tzinfo is None or horizon not in (1,7,30) or not 0<p<1 or anchor+timedelta(days=horizon)!=target:
     results['quarantined'].append({'forecast_id':fid,'reason':'invalid_registry_fields'});continue
    if (reg.date()-anchor).days!=1:
     results['quarantined'].append({'forecast_id':fid,'reason':'registered_with_stale_anchor'});continue
    if reg >= datetime.combine(target+timedelta(days=1),datetime.min.time(),timezone.utc):
     results['quarantined'].append({'forecast_id':fid,'reason':'registered_after_target_close'});continue
    if target not in price or datetime.now(timezone.utc).date()<=target:
     results['pending'].append({'forecast_id':fid,'target_date':str(target)});continue
    if anchor not in price or abs(price[anchor]-float(r['anchor_close']))>1e-10:
     results['quarantined'].append({'forecast_id':fid,'reason':'anchor_close_mismatch_or_missing'});continue
    y=1 if price[target]>price[anchor] else 0
    results['scores'].append({'forecast_id':fid,'target_date':str(target),'realized_up':y,
      'brier':round((p-y)**2,9),'brier_50pct_baseline':0.25,'forecast_probability':p,'target_close':price[target],'horizon_days':horizon})
  by_horizon={}
  for h in (1,7,30):
   group=[row for row in results['scores'] if row['horizon_days']==h]
   n=len(group)
   avg=sum(row['brier'] for row in group)/n if n else None
   by_horizon[str(h)+'d']={'n':n,'mean_brier':round(avg,9) if avg is not None else None,
    'brier_improvement_vs_50pct':round(0.25-avg,9) if avg is not None else None,
    'minimum_evaluation_sample':100,'eligible_for_predictive_claim':False}
  results['horizon_summary']=by_horizon
  results.update(status='ok',registered=len(seen),scored=len(results['scores']),
    pending_count=len(results['pending']),quarantined_count=len(results['quarantined']))
 except Exception as e:results['errors'].append(type(e).__name__+': '+str(e)[:250])
 OUT.parent.mkdir(parents=True,exist_ok=True)
 t=OUT.with_suffix('.json.tmp');t.write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8');os.replace(t,OUT)
 print(json.dumps({'status':results['status'],'registered':results.get('registered'),'quarantined':results.get('quarantined_count'),'scored':results.get('scored')}))
 return 0 if results['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())

#!/usr/bin/env python3
"""Prospective DOGE prediction registry, baseline only. No reconstructed forecasts."""
import csv,hashlib,json,math,os
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'/'prospective'
H=(1,7,30)
COLS=['forecast_id','registered_utc','anchor_date_utc','anchor_close','horizon_days','target_date_utc','p_up','model_id','training_days','source_digest']
def utc():return datetime.now(timezone.utc)
def write(path,value):
 tmp=path.with_name(path.name+'.tmp');tmp.write_text(value,encoding='utf-8');os.replace(tmp,path)
def main():
 DATA.mkdir(parents=True,exist_ok=True);timestamp=utc()
 result={'schema_version':1,'run_utc':timestamp.isoformat(),'status':'error','model':'historical up frequency with Laplace smoothing','research_only':True,'no_dca_change':True,'errors':[]}
 try:
  gate=json.loads((ROOT/'data/prediction_gate/latest.json').read_text())
  if gate.get('status')!='ok' or gate.get('claim_permission')!='NO_VALIDATED_EDGE':
   raise ValueError('prediction gate not healthy or benchmark-only status changed')
  prices={}
  with (ROOT/'data/history/daily.csv').open(newline='',encoding='utf-8') as file:
   rows=csv.DictReader(file)
   for row in rows:
    if row['symbol']!='DOGE-USDT':continue
    ts=int(row['open_utc_ms']);price=float(row['close'])
    if ts%86400000 or not math.isfinite(price) or price<=0:raise ValueError('invalid daily bar')
    if ts in prices:raise ValueError('duplicate DOGE date')
    prices[ts]=price
  times=sorted(prices);anchor=times[-1];last=prices[anchor]
  anchor_day=datetime.fromtimestamp(anchor/1000,timezone.utc).date()
  age=(timestamp.date()-anchor_day).days
  if age<1 or age>3:raise ValueError('stale or unconfirmed anchor: '+str(age)+' days')
  regfile=DATA/'registry.csv';existing={}
  if regfile.exists():
   with regfile.open(newline='',encoding='utf-8') as file:
    rd=csv.DictReader(file)
    if rd.fieldnames!=COLS:raise ValueError('registry schema mismatch')
    for row in rd:
     fid=row['forecast_id']
     if fid in existing:raise ValueError('duplicate registry ID')
     existing[fid]=row
  old_count=len(existing);added=0
  digest=hashlib.sha256((str(anchor)+':'+str(last)).encode()).hexdigest()
  for h in H:
   fid='doge_laplace_v1_'+anchor_day.isoformat()+'_'+str(h)+'d'
   if fid in existing:continue
   history=[(prices[t+h*86400000]>prices[t]) for t in times if t+h*86400000<=anchor and t+h*86400000 in prices]
   if len(history)<365:raise ValueError('insufficient matured examples')
   p=(1+sum(history))/(2+len(history))
   target=anchor_day+timedelta(days=h)
   existing[fid]={'forecast_id':fid,'registered_utc':timestamp.isoformat(),
     'anchor_date_utc':anchor_day.isoformat(),'anchor_close':str(last),
     'horizon_days':str(h),'target_date_utc':target.isoformat(),
     'p_up':format(p,'.9f'),'model_id':'doge_laplace_v1',
     'training_days':str(len(history)),'source_digest':digest}
   added+=1
  if added:
   import io
   out=io.StringIO();w=csv.DictWriter(out,fieldnames=COLS);w.writeheader()
   w.writerows(existing[k] for k in sorted(existing))
   write(regfile,out.getvalue())
  result.update(status='ok',anchor_date_utc=anchor_day.isoformat(),latest_confirmed_close=last,
      registry_rows=len(existing),new_forecasts=added,previous_rows=old_count,
      entries=[{'forecast_id':v['forecast_id'],'p_up':v['p_up'],'target_date_utc':v['target_date_utc']} for v in existing.values() if v['anchor_date_utc']==anchor_day.isoformat()],
      eligibility_note='Baseline only. Registrations created now, not backfilled; score only after target day closes.')
 except Exception as ex:result['errors'].append(type(ex).__name__+': '+str(ex)[:240])
 result['ended_at_utc']=utc().isoformat()
 write(DATA/'latest.json',json.dumps(result,indent=2)+'\n')
 print(json.dumps({'status':result['status'],'added':result.get('new_forecasts'),'errors':result['errors']}))
 return 0 if result['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())

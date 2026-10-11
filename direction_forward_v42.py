#!/usr/bin/env python3
"""DOGE V4.2 issuance-price directional shadow forecasts. No trade instructions."""
import csv,hashlib,io,json,math,os,subprocess,time,urllib.parse,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path
from direction_baseline_v42 import read_csv,HOUR

ROOT=Path(__file__).resolve().parent
BASE=ROOT/'data/direction_v42'
FORECAST=BASE/'forward_registry.csv'
SCORES=BASE/'forward_scores.csv'
STATUS=BASE/'forward_latest.json'
MODEL='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1'
HORIZONS=(1,24,168,720)
MIN_HISTORY=720
MAX_ISSUE_DELAY_MIN=45
MAX_QUOTE_AGE_SEC=120
TARGET_GRACE_MIN=25
FCOLS=['forecast_id','model_id','issued_at_utc','source_quote_utc','source_hour_start_utc',
       'reference_price','horizon_hours','target_utc','majority_p_up','majority_direction',
       'doge_reversal_direction','btc_momentum_direction','feature_sha256']
SCOLS=['forecast_id','status','observed_at_utc','target_quote_utc','target_price',
       'realized_up','majority_correct','doge_reversal_correct','btc_momentum_correct','reason']

def utc(s):
 d=datetime.fromisoformat(s.replace('Z','+00:00'))
 if d.tzinfo is None or d.utcoffset()!=timedelta(0):raise ValueError('non-UTC datetime')
 return d

def write(path,source):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.tmp');tmp.write_text(source,encoding='utf-8');os.replace(tmp,path)

def fetch_ticker():
 url='https://www.okx.com/api/v5/market/ticker?'+urllib.parse.urlencode({'instId':'DOGE-USDT'})
 request=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'DOGE-direction-forward-V42/1.0'})
 last=None
 for n in range(3):
  try:
   with urllib.request.urlopen(request,timeout=16) as resp:j=json.load(resp)
   if j.get('code')!='0' or len(j.get('data',[]))!=1:
    raise ValueError('bad ticker status')
   item=j['data'][0]
   price=float(item['last']);stamp=datetime.fromtimestamp(int(item['ts'])/1000,timezone.utc)
   if not math.isfinite(price) or price<=0:raise ValueError('bad quote price')
   return price,stamp
  except Exception as e:
   last=e
   if n<2:time.sleep(n+1)
 raise RuntimeError('OKX ticker unavailable: '+type(last).__name__+': '+str(last)[:130])

def read_rows(path,columns):
 if not path.exists():return {}
 with path.open(newline='',encoding='utf-8') as f:
  reader=csv.DictReader(f)
  if reader.fieldnames!=columns:raise ValueError('registry schema changed '+path.name)
  rows={}
  for r in reader:
   fid=r['forecast_id']
   if fid in rows:raise ValueError('duplicate immutable ID '+fid)
   rows[fid]=r
 return rows

def serial(rows,cols):
 b=io.StringIO();w=csv.DictWriter(b,fieldnames=cols);w.writeheader()
 w.writerows(rows[k] for k in sorted(rows))
 return b.getvalue()

def verify_append_only(path,rows,cols,root):
 # Confirm no alteration of a previous committed entry in the current branch.
 # Check last 10 saved revisions, not an irreversible external signature.
 relative=path.relative_to(root).as_posix()
 revs=subprocess.run(['git','log','-10','--format=%H','--',relative],
  cwd=root,check=True,text=True,capture_output=True).stdout.splitlines()
 prior={}
 for rev in reversed(revs):
  body=subprocess.run(['git','show',rev+':'+relative],cwd=root,
       check=True,text=True,capture_output=True).stdout
  reader=csv.DictReader(io.StringIO(body))
  if reader.fieldnames!=cols:raise ValueError('historic schema changed '+relative)
  snap={}
  for row in reader:
   fid=row['forecast_id']
   if fid in snap:raise ValueError('historic duplicate '+fid)
   snap[fid]=row
  if any(snap.get(k)!=v for k,v in prior.items()):
   raise ValueError('committed historic forecast/score revised '+relative)
  prior=snap
 if any(rows.get(k)!=v for k,v in prior.items()):
  raise ValueError('immutable registry entry revised '+relative)

def features(times,prices,h):
 n=len(times);doge=prices['DOGE-USDT'];btc=prices['BTC-USDT']
 if n<MIN_HISTORY+h+1:raise ValueError('insufficient matured hourly history '+str(h))
 # at current last completed hour, last usable historical label k+h<=n-1
 matured=[int(doge[i+h]>doge[i]) for i in range(n-h)]
 p=(1+sum(matured))/(2+len(matured))
 majority='UP' if p>=.5 else 'DOWN'
 reverse='DOWN' if doge[-1]>doge[-1-h] else 'UP'
 btc_momentum='UP' if btc[-1]>btc[-1-h] else 'DOWN'
 return {'majority_p':p,'majority':majority,'reversal':reverse,'btc_momentum':btc_momentum}

def score(registry,old_scores,price,quote_time,now):
 out=dict(old_scores);added=0
 for fid,row in registry.items():
  if fid in out:continue
  target=utc(row['target_utc'])
  if quote_time<target:continue
  ref=float(row['reference_price']);h=int(row['horizon_hours'])
  if h not in HORIZONS or not math.isfinite(ref) or ref<=0 or utc(row['issued_at_utc'])+timedelta(hours=h)!=target:
   raise ValueError('corrupt forecast target / price '+fid)
  if quote_time>target+timedelta(minutes=TARGET_GRACE_MIN):
   record=[fid,'unscorable_missing_target_quote',now.isoformat(),'','','','','','','late target quote']
  else:
   true='UP' if price>ref else 'DOWN'
   flags=['1' if row[name]==true else '0' for name in
          ('majority_direction','doge_reversal_direction','btc_momentum_direction')]
   record=[fid,'scored',now.isoformat(),quote_time.isoformat(),format(price,'.12g'),
           true,*flags,'']
  out[fid]=dict(zip(SCOLS,record));added+=1
 return out,added

def metrics(registry,records,h,model):
 rows=[(registry[k],v) for k,v in records.items()
   if v['status']=='scored' and int(registry[k]['horizon_hours'])==h]
 rows.sort(key=lambda x:x[0]['issued_at_utc'])
 def measures(data):
  n=len(data)
  if n==0:return {'n':0,'accuracy_pct':None,'up_recall_pct':None,'down_recall_pct':None,'balanced_accuracy_pct':None}
  flag={'majority':'majority_correct','doge_reversal':'doge_reversal_correct','btc_momentum':'btc_momentum_correct'}[model]
  correct=sum(int(v[flag]) for _,v in data)
  up=[v for _,v in data if v['realized_up']=='UP']
  down=[v for _,v in data if v['realized_up']=='DOWN']
  up_hit=sum(int(v[flag]) for v in up)/len(up) if up else None
  down_hit=sum(int(v[flag]) for v in down)/len(down) if down else None
  balance=(up_hit+down_hit)/2 if up_hit is not None and down_hit is not None else None
  return {'n':n,'accuracy_pct':round(100*correct/n,2),
     'up_recall_pct':round(100*up_hit,2) if up_hit is not None else None,
     'down_recall_pct':round(100*down_hit,2) if down_hit is not None else None,
     'balanced_accuracy_pct':round(100*balance,2) if balance is not None else None}
 independent=[];last=None
 for item in rows:
  t=utc(item[0]['issued_at_utc'])
  if last is None or t-last>=timedelta(hours=h):
   independent.append(item);last=t
 return {'all_scored':measures(rows),'nonoverlap_fixed_phase':measures(independent)}

def run(root=ROOT,now=None,ticker=None):
 # Tests inject an exchange quote and timestamp; never add past predictions.
 if ticker is None:
  ticker=fetch_ticker()
  now=datetime.now(timezone.utc)
 elif now is None:raise ValueError('injected quote requires observed clock')
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):raise ValueError('clock not UTC')
 price,quoted=ticker
 if quoted.tzinfo is None or quoted.utcoffset()!=timedelta(0):raise ValueError('exchange clock not UTC')
 lag=(now-quoted).total_seconds()
 if not math.isfinite(price) or price<=0 or not 0<=lag<=MAX_QUOTE_AGE_SEC:
  raise ValueError('stale/future or bad real-time DOGE quote')
 times,prices=read_csv(root/'data/hourly_research/ohlc.csv',HOUR)
 last=int(times[-1]);close_at=datetime.fromtimestamp((last+HOUR)/1000,timezone.utc)
 minutes=(now-close_at).total_seconds()/60
 if minutes<0:raise ValueError('future hourly feature bar')
 # Missing collector timing is not backdated to a prior closed candle.
 registered=read_rows(root/'data/direction_v42/forward_registry.csv',FCOLS)
 prior_scores=read_rows(root/'data/direction_v42/forward_scores.csv',SCOLS)
 verify_append_only(root/'data/direction_v42/forward_registry.csv',registered,FCOLS,root)
 verify_append_only(root/'data/direction_v42/forward_scores.csv',prior_scores,SCOLS,root)
 # Register all candidate directions as an identical issuance-priced checkpoint,
 # not as retrospective chart labels. 7/30d each once around UTC midnight.
 fresh=0<=minutes<=MAX_ISSUE_DELAY_MIN
 due=[1,24]
 if close_at.hour==0:due.extend([168,720])
 new_ids=[]
 if fresh:
  for h in due:
   fid=f'{MODEL}_{last}_{h}h'
   if fid in registered:continue
   x=features(times,prices,h)
   inputs=f'{last}|{h}|{price:.12g}|{quoted.isoformat()}|{x["majority_p"]:.12g}|{x["reversal"]}|{x["btc_momentum"]}'
   payload=[fid,MODEL,now.isoformat(),quoted.isoformat(),close_at.isoformat(),
     format(price,'.12g'),str(h),(now+timedelta(hours=h)).isoformat(),
     format(x['majority_p'],'.10f'),x['majority'],x['reversal'],x['btc_momentum'],
     hashlib.sha256(inputs.encode()).hexdigest()]
   registered[fid]=dict(zip(FCOLS,payload));new_ids.append(fid)
 scored,new_matured=score(registered,prior_scores,price,quoted,now)
 if new_ids:write(root/'data/direction_v42/forward_registry.csv',serial(registered,FCOLS))
 if new_matured:write(root/'data/direction_v42/forward_scores.csv',serial(scored,SCOLS))
 summary={'schema_version':1,'status':'ok' if fresh else 'stale_hour_no_new_registration',
  'generated_utc':now.isoformat(),'quote_at_utc':quoted.isoformat(),'quote_price':price,
  'reference_quote_age_sec':round(lag,1),'feature_close_at_utc':close_at.isoformat(),
  'feature_lag_min':round(minutes,2),'forecast_window_definition':'genuine quote issued_at -> target issued_at + N hours',
  'target_resolution':'first genuinely observed quote in [target,target+25m], never historical reconstruction or interpolation',
  'new_ids':new_ids,'forecast_rows':len(registered),'scores_added':new_matured,
  'scored_count':sum(v['status']=='scored' for v in scored.values()),
  'unscorable_count':sum(v['status']!='scored' for v in scored.values()),
  'pending_count':len(registered)-len(scored),'direction_accuracy_target_pct':70,
  'models':['majority','doge_reversal','btc_momentum'],'horizons':{},
  'validated_70pct':False,'claim_permission':'NO_VALIDATED_EDGE',
  'scope':'V4.2 primary DOGE price UP vs DOWN/equal; crash warning secondary',
  'caveats':['Hourly scheduled runs can slip; no backdating late snapshots',
   'Missing target quotes count as unscorable and reduce coverage; not hidden',
   'h=24,168,720 checkpoints overlap; non-overlap subset is low sample size',
   'Majority model is a historical direction benchmark, not a proven signal',
   'Registering on GitHub branch is a historical audit trace, not tamper-proof against force-push']}
 for h in HORIZONS:
  summary['horizons'][str(h)+'h']={
   'total_registered':sum(int(v['horizon_hours'])==h for v in registered.values()),
   'models':{m:metrics(registered,scored,h,m) for m in ('majority','doge_reversal','btc_momentum')}}
 write(root/'data/direction_v42/forward_latest.json',json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
 return summary

def main():
 try:out=run()
 except Exception as e:
  out={'schema_version':1,'status':'blocked','generated_utc':datetime.now(timezone.utc).isoformat(),
   'reason':type(e).__name__+': '+str(e)[:300],'claim_permission':'NO_VALIDATED_EDGE'}
  write(STATUS,json.dumps(out,indent=2)+'\n')
 print(json.dumps({'status':out['status'],'new':len(out.get('new_ids',[])),
  'scored':out.get('scored_count'),'pending':out.get('pending_count'),
  'error':out.get('reason')},ensure_ascii=False))
 return 0 if out['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())

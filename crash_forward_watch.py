#!/usr/bin/env python3
"""Prospective hourly DOGE shock watch. Research-only, explicitly unvalidated."""
import csv,hashlib,io,json,math,os,statistics,subprocess
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
BASE=ROOT/'data/crash_research'
HOUR=3600000
MAX_DELAY_MIN=90
WINDOW_HOURS=12
MODEL='shock_watch_rule_frozen_20261011_v1'
COLS=['watch_id','rule_id','issued_at_utc','signal_hour_start_utc','available_after_utc',
      'doge_hour_close','doge_hour_low_pct','volume_multiple','relative_btc_pp',
      'alert','source_first_ingested_utc','feature_sha256']

def now_utc():return datetime.now(timezone.utc)
def iso(ms):return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()
def write(path,obj):
 path.parent.mkdir(parents=True,exist_ok=True)
 temp=path.with_suffix(path.suffix+'.tmp')
 temp.write_text(obj,encoding='utf-8');os.replace(temp,path)

def hour_rows(path=ROOT/'data/hourly_research/ohlc.csv'):
 per={s:{} for s in ('DOGE-USDT','BTC-USDT','ETH-USDT')}
 with path.open(newline='',encoding='utf-8') as f:
  reader=csv.DictReader(f)
  for r in reader:
   s=r['symbol'];t=int(r['open_utc_ms'])
   if s not in per or t%HOUR or t in per[s]:raise ValueError('invalid/duplicate hourly record')
   o,hi,lo,c,v=[float(r[x]) for x in ('open','high','low','close','volume_base')]
   if not all(math.isfinite(x) for x in (o,hi,lo,c,v)) or min(o,lo,c)<=0 or hi<max(o,c,lo) or lo>min(o,c) or v<0:
    raise ValueError('bad candle '+s+':'+str(t))
   ingest=datetime.fromisoformat(r['first_ingested_utc'].replace('Z','+00:00'))
   if ingest.tzinfo is None or ingest.utcoffset()!=timedelta(0):
    raise ValueError('missing UTC ingestion timestamp')
   per[s][t]=dict(open=o,low=lo,close=c,volume=v,ingested=ingest)
 points=sorted(per['DOGE-USDT'])
 if not points or any(set(per[s])!=set(points) for s in per):
  raise ValueError('hourly symbol alignment missing')
 if any(b-a!=HOUR for a,b in zip(points,points[1:])):
  raise ValueError('1H historical gap')
 return per,points

def feature(per,points,t):
 i=points.index(t)
 if i<24:raise ValueError('insufficient preceding 24h')
 doge=per['DOGE-USDT'][t];btc=per['BTC-USDT'][t]
 median=statistics.median(per['DOGE-USDT'][x]['volume'] for x in points[i-24:i])
 if median<=0:raise ValueError('invalid preceding 24h volume')
 low=100*(doge['low']/doge['open']-1)
 vol=doge['volume']/median
 relative=100*(doge['close']/doge['open']-btc['close']/btc['open'])
 return {'low_pct':low,'vol_multiple':vol,'relative_btc_pp':relative,
  'alert':low<=-3 and vol>=5 and relative<=-1}

def verify_registry(rows,base=ROOT):
 # Audit every committed CSV version, including previous versions even if
 # a malicious edit was later reverted. Repo history can itself be rewritten.
 git=['git','log','--reverse','--format=%H','--','data/crash_research/prospective_hourly.csv']
 revs=subprocess.run(git,cwd=base,check=True,text=True,capture_output=True).stdout.splitlines()
 previous={}
 for rev in revs:
  p=subprocess.run(['git','show',rev+':data/crash_research/prospective_hourly.csv'],
      cwd=base,check=True,text=True,capture_output=True)
  r=csv.DictReader(io.StringIO(p.stdout))
  if r.fieldnames!=COLS:raise ValueError('historical hourly registry schema')
  snap={}
  for row in r:
   k=row['watch_id']
   if k in snap:raise ValueError('duplicate historic watch '+k)
   snap[k]=row
  if any(snap.get(k)!=v for k,v in previous.items()):
   raise ValueError('prior watch altered or removed from Git history')
  previous=snap
 if any(rows.get(k)!=v for k,v in previous.items()):
  raise ValueError('historical watch changed in working tree')

def read_registry(path):
 if not path.exists():return {}
 with path.open(newline='',encoding='utf-8') as f:
  rdr=csv.DictReader(f)
  if rdr.fieldnames!=COLS:raise ValueError('hourly registry schema')
  values={}
  for row in rdr:
   k=row['watch_id']
   if k in values:raise ValueError('duplicate watch '+k)
   values[k]=row
 return values

def score_rows(rows,per,points,now):
 s=set(points);latest=points[-1];scored=[];pending=[];quarantine=[]
 for key,row in sorted(rows.items()):
  t=int(datetime.fromisoformat(row['signal_hour_start_utc']).timestamp()*1000)
  issue=datetime.fromisoformat(row['issued_at_utc'])
  end=datetime.fromisoformat(row['available_after_utc'])
  if row['rule_id']!=MODEL or issue.tzinfo is None or issue.utcoffset()!=timedelta(0) or end.timestamp()*1000!=t+HOUR:
   quarantine.append({'id':key,'reason':'invalid timestamp or model'});continue
  if (issue-end).total_seconds()/60<0 or (issue-end).total_seconds()/60>MAX_DELAY_MIN:
   quarantine.append({'id':key,'reason':'issued outside realtime window'});continue
  if row['alert'] not in ('0','1') or not math.isfinite(float(row['doge_hour_close'])):
   quarantine.append({'id':key,'reason':'invalid alert/price'});continue
  if any(t+i*HOUR not in s for i in range(WINDOW_HOURS+1)) or latest<t+WINDOW_HOURS*HOUR:
   pending.append({'id':key,'reason':'12h forward window not yet confirmed'});continue
  events=[t+i*HOUR for i in range(1,WINDOW_HOURS+1)
      if 100*(per['DOGE-USDT'][t+i*HOUR]['close']/per['DOGE-USDT'][t+i*HOUR]['open']-1)<=-5]
  scored.append({'id':key,'signal_hour_start_utc':row['signal_hour_start_utc'],
   'alert':row['alert']=='1','realized_crash_next_12h':bool(events),
   'crash_hour_starts_utc':[iso(x) for x in events]})
 alerts=[r for r in scored if r['alert']]
 pos=sum(x['realized_crash_next_12h'] for x in alerts)
 # Event-recall must consider only continuously OBSERVED real-time checkpoints,
 # not historic candles backfilled after a missed registration.
 observed={int(datetime.fromisoformat(r['signal_hour_start_utc']).timestamp()*1000):r for r in scored}
 eligible_events=[]
 for t in points:
  if 100*(per['DOGE-USDT'][t]['close']/per['DOGE-USDT'][t]['open']-1)>-5:continue
  if all(t-i*HOUR in observed for i in range(1,WINDOW_HOURS+1)):
   eligible_events.append(t)
 caught=sum(any(observed[t-i*HOUR]['alert'] for i in range(1,WINDOW_HOURS+1)) for t in eligible_events)
 return {'registered':len(rows),'scored':scored,'pending':pending,'quarantined':quarantine,
  'true_alerts':pos,'false_alerts':len(alerts)-pos,
  'precision_pct':round(100*pos/len(alerts),2) if alerts else None,
  'observed_continuous_crash_events':len(eligible_events),'caught_events':caught,
  'recall_pct':round(100*caught/len(eligible_events),2) if eligible_events else None,
  'minimum_independent_events':30,'target_precision_pct':70,'target_recall_pct':70,
  'passed':False,'model_predictive_advantage':'NO_VALIDATED_EDGE',
  'note':'Overlapping 12h checkpoints are dependent; cannot promote automatically or extrapolate from unobserved historical hours.'}

def run(now=None,base=ROOT):
 if now is None:now=now_utc()
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):raise ValueError('UTC clock required')
 per,points=hour_rows(base/'data/hourly_research/ohlc.csv')
 latest=points[-1];closed=datetime.fromtimestamp((latest+HOUR)/1000,timezone.utc)
 delay=(now-closed).total_seconds()/60
 capture=max(per[s][latest]['ingested'] for s in per)
 file=base/'data/crash_research/prospective_hourly.csv'
 rows=read_registry(file)
 # No retroactive creation, even when run later or refreshing old candles.
 valid=0<=delay<=MAX_DELAY_MIN and capture<=now
 registered=False
 key=MODEL+'_'+str(latest)
 if valid and key not in rows:
  f=feature(per,points,latest)
  raw='|'.join(str(per[s][latest][k]) for s in ('DOGE-USDT','BTC-USDT','ETH-USDT') for k in ('open','low','close','volume'))
  row=dict(zip(COLS,[key,MODEL,now.isoformat(),iso(latest),iso(latest+HOUR),
   format(per['DOGE-USDT'][latest]['close'],'.12g'),
   format(f['low_pct'],'.9g'),format(f['vol_multiple'],'.9g'),
   format(f['relative_btc_pp'],'.9g'),'1' if f['alert'] else '0',
   capture.isoformat(),hashlib.sha256(raw.encode()).hexdigest()]))
  rows[key]=row;registered=True
 verify_registry(rows,base)
 result=score_rows(rows,per,points,now)
 if registered:
  buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=COLS);w.writeheader()
  w.writerows(rows[x] for x in sorted(rows))
  write(file,buf.getvalue())
 result.update({'schema_version':1,'status':'ok' if valid else 'source_stale_no_registration',
  'evaluated_at_utc':now.isoformat(),'latest_hour_start_utc':iso(latest),
  'registered_new':int(registered),'delay_minutes':round(delay,2),
  'research_only':True,'rule_frozen_at_utc':'2026-10-11T00:00:00Z',
  'rule_was_selected_after_oct09_event':True,
  'historical_alerts_must_not_be_called_live':True})
 write(base/'data/crash_research/prospective_scores.json',json.dumps(result,indent=2,ensure_ascii=False)+'\n')
 return result

def main():
 try:
  obj=run()
 except Exception as e:
  obj={'status':'blocked','error':type(e).__name__+': '+str(e)[:360],'model_predictive_advantage':'NO_VALIDATED_EDGE'}
  write(BASE/'prospective_scores.json',json.dumps(obj,indent=2)+'\n')
 print(json.dumps({k:obj.get(k) for k in ('status','registered_new','registered','precision_pct','recall_pct','error')}))
 return int(obj['status']=='blocked')
if __name__=='__main__':raise SystemExit(main())

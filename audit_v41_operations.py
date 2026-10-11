#!/usr/bin/env python3
"""Check V4.1 daily operational completeness; research-only, fail closed after deadline."""
import csv,json,os
from datetime import datetime,timedelta,timezone,date
from pathlib import Path
from forecast_v41 import ROOT,BASE,MODEL,HORIZONS,MAX_REGISTRATION_LATENCY_MIN,atomic

LAUNCH_ANCHOR=date(2026,10,10)
REGISTRATION_DEADLINE_MIN=130
CHAPTERS=tuple('## F%02d｜'%i for i in range(1,12))

def read_json(path):
 with path.open(encoding='utf-8') as f:return json.load(f)

def inspect(now=None,root=ROOT):
 if now is None:now=datetime.now(timezone.utc)
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):
  raise ValueError('inspection clock must be UTC')
 base=root/'data/forecast_v41'
 daily=read_json(root/'data/history/latest.json')
 forecast=read_json(base/'latest.json')
 scores=read_json(base/'scores.json')
 report=(root/'reports/DOGE_V41_LATEST.md').read_text(encoding='utf-8')
 issues=[]
 expected_anchor=now.date()-timedelta(days=1)
 expected=str(expected_anchor)
 last=daily.get('quality',{}).get('symbols',{}).get('DOGE-USDT',{}).get('last_utc')
 if daily.get('status')!='ok' or last!=expected:
  issues.append('daily source not current/healthy: '+str(last)+' expected '+expected)
 if daily.get('quality',{}).get('duplicate_keys')!=0:
  issues.append('daily historical key duplicates')
 if daily.get('quality',{}).get('symbols',{}).get('DOGE-USDT',{}).get('missing_days_between_endpoints')!=0:
  issues.append('DOGE daily gap detected')
 if forecast.get('status')!='ok' or forecast.get('anchor_date_utc')!=last:
  issues.append('forecast stale or not ok')
 if scores.get('status')!='ok' or scores.get('source_cutoff_utc')!=last:
  issues.append('maturity score stale or not ok')
 if not all(s in report for s in CHAPTERS) or 'NO_VALIDATED_EDGE' not in report or expected not in report:
  issues.append('report missing 11 chapters, edge disclosure or current cutoff')
 csvfile=base/'registry.csv'
 ids=set();registered={};duplicates=[]
 with csvfile.open(encoding='utf-8',newline='') as f:
  reader=csv.DictReader(f)
  for row in reader:
   fid=row['forecast_id']
   if fid in ids:duplicates.append(fid)
   ids.add(fid)
   registered[fid]=row
 if duplicates:issues.append('duplicate forecast ids: '+','.join(duplicates[:5]))
 classes={}
 for label in ('scored','pending','quarantined'):
  for entry in scores.get(label,[]):
   fid=entry['forecast_id']
   if fid in classes:issues.append('forecast classified more than once: '+fid)
   classes[fid]=label
 if set(classes)!=ids:
  issues.append('registry and score classifications differ')
 if scores.get('registered')!=len(ids):
  issues.append('registry count does not match scores registered')
 cutoff=datetime.combine(now.date(),datetime.min.time(),timezone.utc)+timedelta(minutes=REGISTRATION_DEADLINE_MIN)
 missing=[];latencies={}
 if expected_anchor>=LAUNCH_ANCHOR:
  for h in HORIZONS:
   fid=f'{MODEL}_{expected}_{h}d'
   if fid not in registered:
    missing.append(fid)
    continue
   row=registered[fid]
   try:
    reg=datetime.fromisoformat(row['registered_utc'].replace('Z','+00:00'))
    anchor_dt=datetime.combine(expected_anchor+timedelta(days=1),datetime.min.time(),timezone.utc)
    lag=(reg-anchor_dt).total_seconds()/60
    latencies[str(h)+'d']=round(lag,2)
    if not (reg.utcoffset()==timedelta(0) and 0<=lag<=MAX_REGISTRATION_LATENCY_MIN):
     issues.append('forecast issued outside allowed window: '+fid)
    if row['anchor_date_utc']!=expected or row['model_id']!=MODEL:
     issues.append('forecast anchor/model mismatch: '+fid)
   except Exception:
    issues.append('invalid forecast registration timestamp: '+fid)
 if missing and now>=cutoff:
  issues.append('forecast not registered before UTC deadline: '+','.join(missing))
 result={'schema_version':1,'checked_at_utc':now.isoformat(),
  'status':'blocked' if issues else ('registration_grace' if missing else 'ok'),
  'source':'GitHub persisted OKX confirmed 1Dutc plus V4.1 report, immutable registration registry and maturity scores',
  'history_cutoff_utc':last,'expected_anchor_date_utc':expected,
  'deadline_utc':cutoff.isoformat(),'missing_expected_registration':missing,
  'registrations_checked':len(ids),'today_registration_lag_minutes':latencies,
  'score_classes':{label:sum(v==label for v in classes.values()) for label in ('scored','pending','quarantined')},
  'report_chapters_found':sum(x in report for x in CHAPTERS),
  'issues':issues,'model_edge_proven':False,'research_only':True,
  'limitations':['GitHub Actions success does not prove an external notification was delivered',
   'An observed GitHub scheduled run must be independently verified from execution history',
   'A forecast registered after 00:00 UTC has partially elapsed target-day market time; label is UTC close-to-close, not next 24h after issuance']}
 return result

def main():
 now=datetime.now(timezone.utc)
 try:item=inspect(now)
 except Exception as e:
  item={'schema_version':1,'checked_at_utc':now.isoformat(),'status':'blocked',
   'issues':[type(e).__name__+': '+str(e)[:300]],'model_edge_proven':False}
 atomic(BASE/'operations.json',json.dumps(item,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':item['status'],'issues':item['issues'],'missing':item.get('missing_expected_registration')},ensure_ascii=False))
 return 1 if item['status']=='blocked' else 0

if __name__=='__main__':raise SystemExit(main())

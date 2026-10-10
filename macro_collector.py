#!/usr/bin/env python3
"""US macro series from public FRED graph CSV; append/revise with audit trail.
Data timestamps are observations, NOT publication timestamps. Research only.
"""
import csv, io, json, os, sys, urllib.request
from collections import Counter
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT=Path(__file__).resolve().parent/'data'/'macro'
SERIES={
 'CPIAUCSL':('CPI all items, seasonally adjusted','index_1982_84_100','monthly'),
 'CPILFESL':('Core CPI, seasonally adjusted','index_1982_84_100','monthly'),
 'PPIACO':('PPI all commodities, not seasonally adjusted','index_1982_100','monthly'),
 'PAYEMS':('Nonfarm payroll employment, seasonally adjusted','thousand_people','monthly'),
 'UNRATE':('Unemployment rate, seasonally adjusted','percent','monthly'),
 'DFEDTARU':('Federal funds target upper bound','percent','daily'),
 'DFEDTARL':('Federal funds target lower bound','percent','daily'),
 'DGS10':('US 10 year Treasury constant maturity','percent','daily'),
}
FIELDS=['series_id','observation_date','value','unit','frequency','source_url','first_seen_utc','last_checked_utc','revision_count']
REVISIONS=['series_id','observation_date','old_value','new_value','observed_at_utc','source_url']

def now():return datetime.now(timezone.utc).isoformat()
def save_atomic(path,s):
 path.parent.mkdir(parents=True,exist_ok=True)
 temp=path.with_name(path.name+'.tmp');temp.write_text(s,encoding='utf-8');os.replace(temp,path)
def csv_text(fields,rows):
 out=io.StringIO();w=csv.DictWriter(out,fieldnames=fields);w.writeheader();w.writerows(rows);return out.getvalue()
def load(path,fields):
 if not path.exists():return []
 with path.open(encoding='utf-8',newline='') as f:
  reader=csv.DictReader(f)
  if reader.fieldnames!=fields:raise ValueError('Invalid saved CSV schema '+str(path))
  return list(reader)
def parse_csv(sid,blob):
 if len(blob)>12000000:raise ValueError('Response too large')
 content=blob.decode('utf-8-sig');reader=csv.reader(io.StringIO(content));header=next(reader,None)
 if header not in (['observation_date',sid],['DATE',sid]):raise ValueError(f'Unexpected FRED CSV header for {sid}: {header}')
 result={};today=date.today()
 for n,row in enumerate(reader,2):
  if len(row)!=2:raise ValueError(f'Bad row {n} {sid}')
  d,v=row;dt=date.fromisoformat(d)
  if dt>today:continue
  if d in result:raise ValueError(f'Duplicate date in provider series {sid}: {d}')
  if v.strip() in ('.',''):continue
  try:value=Decimal(v)
  except InvalidOperation:raise ValueError(f'Invalid decimal {sid} {d}')
  if not value.is_finite():raise ValueError('NaN or Infinity')
  result[d]=str(value)
 if len(result)<12:raise ValueError(f'Too few valid observations for {sid}: {len(result)}')
 return result

def fetch(sid):
 url=f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}'
 req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; doge-macro-collector/1.0)','Accept':'text/csv'})
 with urllib.request.urlopen(req,timeout=60) as r:blob=r.read(12000001)
 return url,parse_csv(sid,blob)

def main():
 ROOT.mkdir(parents=True,exist_ok=True);stamp=now()
 audit={'schema_version':1,'source':'Federal Reserve Bank of St. Louis FRED public graph CSV','started_at_utc':stamp,'ended_at_utc':None,'status':'error','unit_policy':'per_series','series':{},'new_rows':0,'revisions':0,'total_rows':None,'duplicate_keys':None,'errors':{},'limitations':['Observation dates are not release dates or information-availability times','Historical vintage / point-in-time revision history before collector start is unavailable','FRED series may lag official BLS and Fed release times','No inference about policy headlines or DOGE prices from this collector']}
 try:
  old={}
  for row in load(ROOT/'observations.csv',FIELDS):
   key=(row['series_id'],row['observation_date'])
   if key in old:raise ValueError('Duplicate stored key '+str(key))
   old[key]=row
  revisions=load(ROOT/'revisions.csv',REVISIONS)
  # Fetch every series before any history mutation. Fail closed on partial network failure.
  fetched={sid:fetch(sid) for sid in SERIES}
  for sid,(url,data) in fetched.items():
   name,unit,freq=SERIES[sid]
   audit['series'][sid]={'label':name,'count_on_source':len(data),'oldest_observation_date':min(data),'latest_observation_date':max(data),'url':url}
   for d,val in data.items():
    key=sid,d
    if key not in old:
     old[key]={'series_id':sid,'observation_date':d,'value':val,'unit':unit,'frequency':freq,'source_url':url,'first_seen_utc':stamp,'last_checked_utc':stamp,'revision_count':'0'}
     audit['new_rows']+=1
    else:
     row=old[key]
     if Decimal(row['value'])!=Decimal(val):
      revisions.append({'series_id':sid,'observation_date':d,'old_value':row['value'],'new_value':val,'observed_at_utc':stamp,'source_url':url})
      row['value']=val;row['revision_count']=str(int(row['revision_count'])+1);audit['revisions']+=1
     row['last_checked_utc']=stamp
  observations=[old[k] for k in sorted(old)]
  assert len(observations)==len(set(old))
  save_atomic(ROOT/'observations.csv',csv_text(FIELDS,observations))
  save_atomic(ROOT/'revisions.csv',csv_text(REVISIONS,revisions))
  audit.update(status='ok',total_rows=len(observations),duplicate_keys=0)
 except Exception as e:audit['errors']['collector']=f'{type(e).__name__}: {str(e)[:400]}'
 audit['ended_at_utc']=now()
 save_atomic(ROOT/'latest.json',json.dumps(audit,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps({'status':audit['status'],'new_rows':audit['new_rows'],'errors':audit['errors']}))
 return 0 if audit['status']=='ok' else 1
if __name__=='__main__':sys.exit(main())

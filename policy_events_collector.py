#!/usr/bin/env python3
"""Official Fed monetary-policy RSS + BLS release calendar. Research, not trading signals.
Historical publication timestamps are preserved as published by sources, not FRED observation dates.
"""
import csv,io,json,os,sys,urllib.request,xml.etree.ElementTree as ET
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parent/'data'/'policy_events'
FEED='https://www.federalreserve.gov/feeds/press_monetary.xml'
CAL='https://www.bls.gov/schedule/news_release/bls.ics'
FIELDS=['source','event_id','event_type','title','event_time_utc','event_time_precision','first_seen_utc','last_seen_utc','url','point_in_time_rule']
def utc():return datetime.now(timezone.utc).isoformat()
def fetch(url):
 req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; DOGE-policy-source-audit/1.0)','Accept':'application/xml,text/xml,text/calendar,*/*'})
 with urllib.request.urlopen(req,timeout=35) as response:return response.read(3000000).decode('utf-8-sig')
def atom(p,s):
 tmp=p.with_name(p.name+'.tmp');tmp.write_text(s,encoding='utf-8');os.replace(tmp,p)
def fed(text,stamp):
 root=ET.fromstring(text)
 rows=[]
 for item in root.findall('.//item'):
  title=(item.findtext('title') or '').strip();url=(item.findtext('link') or '').strip()
  guid=(item.findtext('guid') or url).strip()
  published=(item.findtext('pubDate') or '').strip()
  if not title or not url or not published or not guid:continue
  dt=parsedate_to_datetime(published)
  if dt.tzinfo is None:raise ValueError('Fed RSS published time has no timezone')
  ts=dt.astimezone(timezone.utc)
  if ts>datetime.now(timezone.utc)+timedelta(hours=1):raise ValueError('future Fed publication date')
  rows.append({'source':'FED_MONETARY_RSS','event_id':guid,'event_type':'federal_reserve_monetary_policy',
  'title':title,'event_time_utc':ts.isoformat(),'event_time_precision':'provider_pubDate',
  'first_seen_utc':stamp,'last_seen_utc':stamp,'url':url,
  'point_in_time_rule':'max(provider_pubDate,first_seen_utc) until publication timing independently verified'})
 if not rows:raise ValueError('no official Fed policy RSS items')
 return rows
def unfold(raw):
 rows=[]
 for x in raw.replace('\r\n','\n').split('\n'):
  if x.startswith((' ','\t')) and rows:rows[-1]+=x[1:]
  else:rows.append(x)
 return rows
def bls(text,stamp):
 if 'BEGIN:VCALENDAR' not in text:raise ValueError('missing BLS iCalendar header')
 rows=[];event=None
 for line in unfold(text):
  if line=='BEGIN:VEVENT':event={};continue
  if line=='END:VEVENT':
   if event:
    title=event.get('SUMMARY','').replace('\\,',',').strip()
    uid=event.get('UID','').strip()
    uri=event.get('URL',CAL)
    raw=event.get('DTSTART','')
    tzid=event.get('DTSTART_TZID','')
    if not title or not uid or not raw:continue
    if raw.endswith('Z'):
     dt=datetime.strptime(raw,'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    elif 'T' in raw:
     if not tzid:raise ValueError('BLS release time lacks explicit timezone')
     dt=datetime.strptime(raw,'%Y%m%dT%H%M%S' if len(raw)==15 else '%Y%m%dT%H%M').replace(tzinfo=ZoneInfo(tzid)).astimezone(timezone.utc)
    else:
     # All-day dates are not actual release instants and must not be used as intraday signals.
     continue
    if any(k in title.lower() for k in ('consumer price index','producer price index','employment situation')):
     rows.append({'source':'BLS_CALENDAR','event_id':uid,'event_type':'scheduled_macro_release',
      'title':title,'event_time_utc':dt.isoformat(),'event_time_precision':'calendar_schedule_not_actual_publication',
      'first_seen_utc':stamp,'last_seen_utc':stamp,'url':uri,
      'point_in_time_rule':'schedule_only: actual released data forbidden until independently timestamped'})
   event=None;continue
  if event is not None and ':' in line:
   left,value=line.split(':',1);key=left.split(';',1)[0]
   if key=='DTSTART':
    event['DTSTART']=value
    if 'TZID=' in left:event['DTSTART_TZID']=left.split('TZID=',1)[1].split(';',1)[0]
   else:event[key]=value
 if not rows:raise ValueError('no timestamped CPI/PPI/employment release dates found')
 return rows
def run():
 ROOT.mkdir(parents=True,exist_ok=True)
 stamp=utc();result={'schema_version':1,'status':'error','started_at_utc':stamp,'sources':{},'errors':{},
 'not_a_news_sentiment_or_crypto_policy_feed':True,'no_claim_of_pit_historical_macro_values':True}
 existing={}
 target=ROOT/'events.csv'
 if target.exists():
  with target.open(newline='',encoding='utf-8') as handle:
   reader=csv.DictReader(handle)
   if reader.fieldnames!=FIELDS:raise ValueError('stored policy CSV schema mismatch')
   for row in reader:
    key=(row['source'],row['event_id'])
    if key in existing:raise ValueError('duplicate historical event ID')
    existing[key]=row
 try:
  fetched={}
  for name,url,parse in [('FED_MONETARY_RSS',FEED,fed),('BLS_CALENDAR',CAL,bls)]:
   try:
    rows=parse(fetch(url),stamp)
    fetched[name]=rows
    result['sources'][name]={'status':'ok','items_this_fetch':len(rows),'url':url}
   except Exception as e:
    result['sources'][name]={'status':'error','url':url}
    result['errors'][name]=type(e).__name__+': '+str(e)[:170]
  for rows in fetched.values():
   for row in rows:
    key=(row['source'],row['event_id'])
    if key in existing:row['first_seen_utc']=existing[key]['first_seen_utc']
    existing[key]=row
  if fetched:
   buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=FIELDS);w.writeheader()
   w.writerows(existing[k] for k in sorted(existing))
   atom(target,buf.getvalue())
  result['records']=len(existing)
  result['duplicate_keys']=0
  result['status']='ok' if len(fetched)==2 else ('partial' if fetched else 'error')
 finally:
  result['ended_at_utc']=utc()
  atom(ROOT/'latest.json',json.dumps(result,indent=2,ensure_ascii=False)+'\n')
  print(json.dumps({'status':result['status'],'sources':result['sources'],'errors':result['errors']},ensure_ascii=False))
 return 0 if result['status']=='ok' else 1
if __name__=='__main__':sys.exit(run())

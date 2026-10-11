#!/usr/bin/env python3
"""Bounded OKX 1H history backfill for hourly crash research; independent from live collector."""
import csv,io,json,math,os,time,urllib.parse,urllib.request
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BASE=ROOT/'data/hourly_research'
BASE.mkdir(parents=True,exist_ok=True)
DATA=BASE/'ohlc.csv'
SYMBOLS=('DOGE-USDT','BTC-USDT','ETH-USDT')
COLUMNS=('symbol','open_utc_ms','open','high','low','close','volume_base','first_ingested_utc','source')
HOUR=3600000
OLDEST_TARGET=int(datetime(2023,10,1,tzinfo=timezone.utc).timestamp()*1000)
PAGE_BUDGET=int(os.getenv('DOGE_HOURLY_PAGE_BUDGET','24'))
if not 0<=PAGE_BUDGET<=24:raise ValueError('invalid hourly page budget')
API='https://www.okx.com/api/v5/market/history-candles'
def iso(ms):return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()
def atomic(p,s):
 t=p.with_name(p.name+'.tmp');t.write_text(s,encoding='utf-8');os.replace(t,p)
def request(symbol,cursor=None):
 params={'instId':symbol,'bar':'1H','limit':'100'}
 if cursor is not None:params['after']=str(cursor)
 url=API+'?'+urllib.parse.urlencode(params)
 req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'DOGE-hourly-extreme-research/1.0'})
 last=None
 for attempt in range(3):
  try:
   with urllib.request.urlopen(req,timeout=24) as resp:obj=json.load(resp)
   if obj.get('code')!='0' or not isinstance(obj.get('data'),list):
    raise ValueError('OKX reply code='+str(obj.get('code'))+' '+str(obj.get('msg')))
   return obj['data']
  except Exception as e:
   last=e
   if attempt<2:time.sleep(1+attempt*2)
 raise RuntimeError('OKX hourly API failed: '+type(last).__name__+': '+str(last)[:100])
def parse(symbol,record,captured,now):
 if len(record)<9 or str(record[8])!='1':return None
 ts=int(record[0]);closed=(now//HOUR)*HOUR
 if ts%HOUR or ts<0 or ts>=closed: return None
 values=[float(x) for x in record[1:6]]
 o,hi,lo,cl,v=values
 if not all(math.isfinite(x) for x in values) or min(o,lo,cl)<=0 or v<0 or hi<max(o,cl) or lo>min(o,cl):
  raise ValueError('bad OKX hourly OHLCV '+symbol+' '+str(ts))
 return dict(zip(COLUMNS,(symbol,str(ts),str(o),str(hi),str(lo),str(cl),str(v),captured,'OKX history-candles 1H confirmed')))
def read_old():
 if not DATA.exists():return {}
 with DATA.open(newline='',encoding='utf-8') as f:
  d=csv.DictReader(f)
  if tuple(d.fieldnames or ())!=COLUMNS:raise ValueError('1H research registry schema changed')
  old={}
  for r in d:
   k=(r['symbol'],int(r['open_utc_ms']))
   if k in old:raise ValueError('duplicate prior hourly key: '+str(k))
   old[k]=r
 return old
def assess(rows):
 per={}
 for symbol in SYMBOLS:
  t=sorted(t for (s,t) in rows if s==symbol)
  if not t:raise ValueError('empty '+symbol)
  gaps=sum(max(0,(b-a)//HOUR-1) for a,b in zip(t,t[1:]))
  per[symbol]={'hours':len(t),'earliest_utc':iso(t[0]),'last_confirmed_start_utc':iso(t[-1]),
   'missing_hours_between_endpoints':gaps}
  if gaps:raise ValueError('historical 1H gap: '+symbol+' '+str(gaps))
 if len({per[s]['last_confirmed_start_utc'] for s in SYMBOLS})>1:
  raise ValueError('latest 1H source timestamp mismatched across symbols')
 return per
def run():
 now=int(time.time()*1000);start=datetime.now(timezone.utc).isoformat()
 old=read_old();status={'schema_version':1,'status':'error','started_utc':start,
  'source':'OKX public history-candles, 1H, confirm=1',
  'intended_oldest_utc':iso(OLDEST_TARGET),'page_budget_per_symbol':PAGE_BUDGET,
  'research_only':True,'no_pre_event_real_time_alert_claim':True,'errors':[],
  'per_symbol':{},'inserted':0,'archive_complete':False}
 try:
  latest_expected=(now//HOUR-1)*HOUR
  for sym in SYMBOLS:
   recent=request(sym);nowcapt=datetime.now(timezone.utc).isoformat()
   recent_valid=[parse(sym,x,nowcapt,now) for x in recent]
   recent_valid=[x for x in recent_valid if x]
   if not recent_valid or max(int(x['open_utc_ms']) for x in recent_valid)<latest_expected:
    raise ValueError('stale 1H source '+sym)
   inserted=0
   for row in recent_valid:
    k=(sym,int(row['open_utc_ms']))
    if k not in old:old[k]=row;inserted+=1
   cursor=min(ts for (s,ts) in old if s==sym)
   pages=0
   for _ in range(PAGE_BUDGET):
    if cursor<=OLDEST_TARGET:break
    response=request(sym,cursor);capt=datetime.now(timezone.utc).isoformat()
    if not response:break
    older=[parse(sym,r,capt,now) for r in response]
    older=[x for x in older if x and int(x['open_utc_ms'])<cursor]
    if not older:break
    for row in older:
     k=(sym,int(row['open_utc_ms']))
     if k not in old:old[k]=row;inserted+=1
    nxt=min(int(x['open_utc_ms']) for x in older)
    if nxt>=cursor:break
    cursor=nxt;pages+=1
   status['per_symbol'][sym]={'new_rows':inserted,'backfill_pages':pages,
      'latest_confirmed_start_utc':iso(max(int(x['open_utc_ms']) for x in recent_valid))}
   status['inserted']+=inserted
  stats=assess(old)
  status['quality']={'symbols':stats,'duplicate_keys':0}
  # Avoid creating fake historical point-in-time event knowledge.
  status['limitations']=['Historical OHLCV retrieved today is not proof signals existed at candle time',
   'Cross-source first_ingested_utc cannot be backdated to historical candle opening',
   'One-hour crashes are rare; no predictive claim until prespecified out-of-sample validation',
   'Historic provider data may have retention limits']
  status['archive_complete']=all(
   datetime.fromisoformat(stats[s]['earliest_utc']).timestamp()*1000<=OLDEST_TARGET
   for s in SYMBOLS)
  status['status']='ok' if status['archive_complete'] else 'partial_backfill'
  buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=COLUMNS);w.writeheader()
  w.writerows(old[k] for k in sorted(old))
  atomic(DATA,buf.getvalue())
 except Exception as e:status['errors'].append(type(e).__name__+': '+str(e)[:380])
 status['ended_utc']=datetime.now(timezone.utc).isoformat()
 atomic(BASE/'latest.json',json.dumps(status,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':status['status'],'added':status['inserted'],
  'completed':status['archive_complete'],'errors':status['errors']},ensure_ascii=False),flush=True)
 return 0 if status['status'] in ('ok','partial_backfill') else 1
if __name__=='__main__':raise SystemExit(run())

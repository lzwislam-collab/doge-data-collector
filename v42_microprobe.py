#!/usr/bin/env python3
"""OKX spot 1m/5m feature-source feasibility pilot; not an ex-ante crash predictor."""
import csv,io,json,math,os,time,urllib.parse,urllib.request
from datetime import datetime,timezone
from pathlib import Path

BASE=Path(__file__).resolve().parent/'data/v42_microprobe'
SYMBOLS=('DOGE-USDT','BTC-USDT','ETH-USDT')
PERIODS={'1m':60000,'5m':300000}
COLS=('symbol','bar','open_utc_ms','open','high','low','close','volume_base','captured_at_utc','source')
API='https://www.okx.com/api/v5/market/history-candles'

def parse(symbol,bar,raw,now_ms,ingest):
 if len(raw)<9 or str(raw[8])!='1':return None
 ts=int(raw[0]);interval=PERIODS[bar]
 if ts<0 or ts%interval or ts+interval>now_ms:return None
 o,hi,lo,c,v=(float(raw[i]) for i in range(1,6))
 if not all(math.isfinite(x) for x in (o,hi,lo,c,v)) or min(o,c,lo)<=0 or v<0 or hi<max(o,c,lo) or lo>min(o,c):
  raise ValueError('invalid OHLCV '+symbol+' '+bar)
 return (symbol,bar,str(ts),str(o),str(hi),str(lo),str(c),str(v),ingest,'OKX public 1m/5m confirmed history-candles')

def get(symbol,bar):
 params=urllib.parse.urlencode({'instId':symbol,'bar':bar,'limit':100})
 request=urllib.request.Request(API+'?'+params,headers={'Accept':'application/json','User-Agent':'DOGE-V42-micro-probe/1.0'})
 error=None
 for retry in range(3):
  try:
   with urllib.request.urlopen(request,timeout=25) as resp:data=json.load(resp)
   if data.get('code')!='0' or not isinstance(data.get('data'),list):
    raise ValueError('OKX '+str(data.get('code'))+': '+str(data.get('msg'))[:100])
   return data['data']
  except Exception as e:
   error=e
   if retry<2:time.sleep(retry+1)
 raise RuntimeError(type(error).__name__+': '+str(error)[:120])

def assess(rows,bar,now_ms):
 interval=PERIODS[bar];times=sorted(int(r[2]) for r in rows)
 if len(set(times))!=len(times):raise ValueError('duplicate confirmed minute key')
 if len(times)<50:raise ValueError('fewer than 50 confirmed bars')
 gaps=sum(max(0,(b-a)//interval-1) for a,b in zip(times,times[1:]))
 if gaps:raise ValueError('missing minute candles '+str(gaps))
 age=(now_ms-(times[-1]+interval))/60000
 if age<-.5 or age>10:raise ValueError('last confirmed candle stale/future '+str(round(age,2))+'m')
 return {'confirmed_bars':len(times),'first_bar_start_utc':datetime.fromtimestamp(times[0]/1000,timezone.utc).isoformat(),
         'last_bar_start_utc':datetime.fromtimestamp(times[-1]/1000,timezone.utc).isoformat(),
         'latest_bar_close_age_minutes':round(age,2),'duplicate_keys':0,'missing_internal_bars':gaps}

def write(path,text):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.tmp')
 tmp.write_text(text,encoding='utf-8');os.replace(tmp,path)

def main():
 now=int(time.time()*1000);ingest=datetime.now(timezone.utc).isoformat()
 report={'schema_version':1,'status':'blocked','observed_utc':ingest,'source':'OKX /api/v5/market/history-candles (confirmed=1) public spot 1m and 5m',
   'purpose':'Interface and timestamp feasibility; no model training/backtest from this pilot',
   'results':{},'errors':[],'prediction_ready':False,'continuous_minute_monitoring_verified':False}
 allrows=[]
 for symbol in SYMBOLS:
  report['results'][symbol]={}
  for bar in PERIODS:
   try:
    raw=get(symbol,bar)
    captured=datetime.now(timezone.utc).isoformat()
    rows=[parse(symbol,bar,x,now,captured) for x in raw]
    rows=[r for r in rows if r is not None]
    report['results'][symbol][bar]=assess(rows,bar,now)
    allrows+=rows
   except Exception as e:
    report['errors'].append(symbol+' '+bar+': '+type(e).__name__+': '+str(e)[:150])
 if not report['errors']:
  report['status']='pilot_ok'
  buf=io.StringIO();wr=csv.writer(buf);wr.writerow(COLS)
  wr.writerows(sorted(allrows,key=lambda row:(row[0],row[1],int(row[2]))))
  write(BASE/'latest_sample.csv',buf.getvalue())
  report['total_verified_sample_rows']=len(allrows)
 report['disclosures']=['GitHub hourly sampling is NOT continuous 1m monitoring',
   'First_ingested time is actual pilot execution time, not the historic candle timestamp',
   'A recent sample does not establish long historical orderbook/trade-side availability',
   'All microstructure features remain research-only until point-in-time integrity and separate prospective scoring.']
 write(BASE/'latest.json',json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':report['status'],'verified_rows':report.get('total_verified_sample_rows'),'errors':report['errors']}))
 return int(report['status']!='pilot_ok')

if __name__=='__main__':raise SystemExit(main())

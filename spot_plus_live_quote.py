#!/usr/bin/env python3
"""Validated, point-in-time public OKX spot quote snapshot for DOGE Spot Plus.

Snapshot-only research: never a continuously streaming order book, never an
executable fill or a prediction signal; do not change V4.2 or DOGE DCA rules.
"""
import json,math,os,time,urllib.parse,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUTPUT=ROOT/'data/stage5/spot_plus/live_quote_latest.json'
SYMBOLS=('DOGE-USDT','BTC-USDT','ETH-USDT')
MAX_TICKER_LAG_SECONDS=120

def parse(raw,now):
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):
  raise ValueError('UTC clock required')
 seen={};records={}
 for inst,item in raw.items():
  if inst not in SYMBOLS:raise ValueError('unknown instrument '+str(inst))
  if item.get('instId')!=inst:raise ValueError('mismatched ticker instId '+inst)
  ts=datetime.fromtimestamp(int(item['ts'])/1000,timezone.utc)
  lag=(now-ts).total_seconds()
  if not 0<=lag<=MAX_TICKER_LAG_SECONDS:
   raise ValueError('stale or future spot ticker '+inst)
  try:last,bid,ask=(float(item[k]) for k in ('last','bidPx','askPx'))
  except Exception as exc:raise ValueError('incomplete spot quote '+inst) from exc
  if (not all(math.isfinite(x) for x in (last,bid,ask))
      or min(last,bid,ask)<=0 or bid>ask):
   raise ValueError('invalid spot ticker spread/price '+inst)
  spread=10000*(ask-bid)/((ask+bid)/2)
  if spread>100:raise ValueError('unreasonable >100bps spread '+inst)
  records[inst]={'inst_id':inst,'last_usdt':last,'best_bid_usdt':bid,
   'best_ask_usdt':ask,'spread_bps':round(spread,4),
   'source_quote_at_utc':ts.isoformat(),'age_seconds':round(lag,2),
   'ticker_time_verified':True}
 if set(records)!=set(SYMBOLS):raise ValueError('missing BTC/ETH/DOGE spot quote')
 return {'schema_version':1,'status':'ok','observed_at_utc':now.isoformat(),
  'source':'OKX public /api/v5/market/ticker for DOGE-USDT, BTC-USDT, ETH-USDT',
  'instruments':records,'ticker_max_age_sec':MAX_TICKER_LAG_SECONDS,
  'snapshot_only_not_continuous_market_stream':True,
  'not_an_executable_fill':True,
  'not_a_probabilistic_price_prediction':True,
  'historical_source_cannot_be_backdated':True,
  'change_to_frozen_v42_or_dca':False}

def fetch(inst):
 url='https://www.okx.com/api/v5/market/ticker?'+urllib.parse.urlencode({'instId':inst})
 req=urllib.request.Request(url,headers={'Accept':'application/json',
     'User-Agent':'DOGE-Spot-Plus-Realtime-Snapshot/1.0'})
 last_error=None
 for attempt in range(3):
  try:
   with urllib.request.urlopen(req,timeout=12) as response:data=json.load(response)
   if data.get('code')!='0' or len(data.get('data',[]))!=1:
    raise ValueError('bad OKX public ticker status')
   return data['data'][0]
  except Exception as exc:
   last_error=exc
   if attempt<2:time.sleep(1+attempt)
 raise RuntimeError('quote failed '+inst+': '+str(last_error)[:130])

def run(root=ROOT,now=None,raw=None):
 if raw is None:
  raw={inst:fetch(inst) for inst in SYMBOLS}
  now=datetime.now(timezone.utc)
 elif now is None:raise ValueError('injected quotes require observed clock')
 report=parse(raw,now)
 output=root/'data/stage5/spot_plus/live_quote_latest.json'
 output.parent.mkdir(parents=True,exist_ok=True)
 temp=output.with_suffix('.json.tmp')
 temp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 os.replace(temp,output)
 return report

def main():
 try:
  result=run()
  print(json.dumps({'status':result['status'],
    'observed_at_utc':result['observed_at_utc'],
    'doge_last':result['instruments']['DOGE-USDT']['last_usdt']}))
  return 0
 except Exception as exc:
  print(json.dumps({'status':'blocked','error':type(exc).__name__+': '+str(exc)[:230]}))
  return 1

if __name__=='__main__':raise SystemExit(main())

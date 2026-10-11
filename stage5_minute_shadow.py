#!/usr/bin/env python3
"""Independent, forward-only DOGE minute-bar evaluation companion.

Scores the first fully closed 1m candle whose OPEN >= the originally registered
issuance+H target. This is NOT the exact target quote and NEVER changes V4.2
forward_scores.csv or its locked point-quote evaluation.
"""
import csv,io,json,math,os,time,urllib.parse,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path
from direction_forward_v42 import FCOLS,read_rows,write,verify_append_only
from stage5_70pct_audit import utc

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data/stage5/minute_shadow'
SCORES=OUT/'minute_scores.csv'
LATEST=OUT/'latest.json'
PROTOCOL=ROOT/'data/stage5/minute_evaluation_protocol.json'
MINUTE_MS=60_000
MAX_PAGES=12
LOOKBACK_HOURS=18
SCOLS=['forecast_id','status','evaluated_at_utc','target_utc',
       'bar_open_utc','bar_closed_at_utc','retrieved_at_utc','minute_close_usdt',
       'realized_up','majority_correct','doge_reversal_correct','btc_momentum_correct',
       'offset_seconds_after_target','protocol_id','evaluation_cohort','reason']
API='https://www.okx.com/api/v5/market/history-candles'
MODEL='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1'
ALLOWED_H={1,24}

def load_protocol(root):
 p=json.loads((root/'data/stage5/minute_evaluation_protocol.json').read_text())
 if (p.get('status')!='frozen_protocol'
     or p.get('protocol_id')!='DOGE_V42_1M_CLOSE_SHADOW_LOCK_20261012'
     or p.get('forecast_source_model_id')!=MODEL
     or p.get('primary_horizons_hours')!=[1,24]
     or p.get('selection_rule')!='first confirmed 1m bar with open >= registered target; use close at open+60s'
     or p.get('max_offset_after_target_seconds')!=120
     or p.get('original_quote_scoring_untouched') is not True):
  raise ValueError('minute shadow evaluation protocol changed')
 return p

def parse_bars(raw,now):
 bars={}
 now_ms=int(now.timestamp()*1000)
 for entry in raw:
  if len(entry)<9 or str(entry[8])!='1':continue
  t=int(entry[0])
  if t%MINUTE_MS or t+MINUTE_MS>now_ms:
   raise ValueError('unconfirmed/future or unaligned 1m bar')
  value=float(entry[4])
  if not math.isfinite(value) or value<=0:raise ValueError('invalid minute close')
  if t in bars and bars[t]!=value:raise ValueError('conflicting minute close for same timestamp')
  bars[t]=value
 return bars

def fetch_history(now,oldest_wanted_ms):
 """Bounded OKX public pagination, newest to older; never fabricate missing bars."""
 found={}
 after=None
 previous_oldest=None
 for page in range(MAX_PAGES):
  query={'instId':'DOGE-USDT','bar':'1m','limit':'100'}
  if after is not None:query['after']=str(after)
  req=urllib.request.Request(API+'?'+urllib.parse.urlencode(query),
      headers={'Accept':'application/json','User-Agent':'DOGE-1m-shadow-audit/1.0'})
  last=None
  for attempt in range(3):
   try:
    with urllib.request.urlopen(req,timeout=20) as response:data=json.load(response)
    if data.get('code')!='0' or not isinstance(data.get('data'),list):
     raise ValueError('OKX rejected historical minute query')
    break
   except Exception as exc:
    last=exc
    if attempt==2:raise RuntimeError('OKX historical minute API unreachable: '+type(last).__name__) from last
    time.sleep(attempt+1)
  segment=parse_bars(data['data'],now)
  if not segment:break
  for t,v in segment.items():
   if t in found and found[t]!=v:raise ValueError('same 1m timestamp changed within read')
   found[t]=v
  oldest=min(segment)
  if previous_oldest is not None and oldest>=previous_oldest:
   raise ValueError('OKX pagination failed to progress backward')
  previous_oldest=oldest
  if oldest<=oldest_wanted_ms or len(segment)<50:break
  after=oldest
  if page<MAX_PAGES-1:time.sleep(0.15)
 return found

def target_minute(timestamp):
 return ((int(timestamp.timestamp()*1000)+MINUTE_MS-1)//MINUTE_MS)*MINUTE_MS

def serialize(rows):
 buff=io.StringIO()
 writer=csv.DictWriter(buff,fieldnames=SCOLS,lineterminator='\n')
 writer.writeheader()
 writer.writerows(rows[key] for key in sorted(rows))
 return buff.getvalue()

def evaluate(root=ROOT,now=None,minute_loader=None):
 if now is None:now=datetime.now(timezone.utc)
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):raise ValueError('clock is not UTC')
 protocol=load_protocol(root)
 freeze=utc(protocol['effective_from_utc'])
 reg=read_rows(root/'data/direction_v42/forward_registry.csv',FCOLS)
 existing=read_rows(root/'data/stage5/minute_shadow/minute_scores.csv',SCOLS)
 if any(fid not in reg for fid in existing):raise ValueError('orphan minute result')
 candidates=[]
 maxage=now-timedelta(hours=LOOKBACK_HOURS)
 for fid,row in reg.items():
  if row['model_id']!=MODEL:raise ValueError('unexpected source model')
  h=int(row['horizon_hours'])
  if h not in (1,24,168,720):raise ValueError('unrecognized horizon')
  if h not in ALLOWED_H or fid in existing:continue
  issued=utc(row['issued_at_utc']);target=utc(row['target_utc'])
  if target!=issued+timedelta(hours=h):raise ValueError('source registry horizon mismatch')
  if target+timedelta(minutes=2)>now or target<maxage:continue
  source_quote=utc(row['source_quote_utc'])
  if not source_quote<=issued<=source_quote+timedelta(seconds=120):
   raise ValueError('source registration quote too old')
  candidates.append((fid,row,issued,target,target_minute(target)))
 minute_bars={}
 if candidates:
  minute_loader=minute_loader or fetch_history
  minute_bars=minute_loader(now,min(q[4] for q in candidates))
  if not isinstance(minute_bars,dict):raise ValueError('minute API output invalid')
 updates={}
 for fid,row,issued,target,expected_open in candidates:
  if expected_open not in minute_bars:continue
  price=float(minute_bars[expected_open])
  if not math.isfinite(price) or price<=0:raise ValueError('bad confirmed minute price')
  original=float(row['reference_price'])
  if not math.isfinite(original) or original<=0:raise ValueError('bad issued price')
  outcome='UP' if price>original else 'DOWN'
  chosen_open=datetime.fromtimestamp(expected_open/1000,timezone.utc)
  chosen_close=chosen_open+timedelta(minutes=1)
  seconds=(chosen_close-target).total_seconds()
  if not 0<seconds<=protocol['max_offset_after_target_seconds']:
   raise ValueError('minute-close scoring offset outside preregistered limit')
  model_flags={name: '1' if row[name+'_direction']==outcome else '0'
               for name in ('majority','doge_reversal','btc_momentum')}
  if any(row[name+'_direction'] not in ('UP','DOWN') for name in model_flags):
   raise ValueError('non-binary locked source forecast')
  updates[fid]={'forecast_id':fid,'status':'scored_from_confirmed_1m_close',
     'evaluated_at_utc':now.isoformat(),'target_utc':target.isoformat(),
     'bar_open_utc':chosen_open.isoformat(),'bar_closed_at_utc':chosen_close.isoformat(),
     'retrieved_at_utc':now.isoformat(),'minute_close_usdt':format(price,'.12g'),
     'realized_up':outcome,
     'majority_correct':model_flags['majority'],
     'doge_reversal_correct':model_flags['doge_reversal'],
     'btc_momentum_correct':model_flags['btc_momentum'],
     'offset_seconds_after_target':format(seconds,'.3f'),
     'protocol_id':protocol['protocol_id'],
     'evaluation_cohort':'pre_freeze_diagnostic_only' if issued<freeze else 'preregistered_forward_shadow',
     'reason':'K-line close is a later one-minute reference, NOT exact target ticker quote'}
 result=dict(existing)
 result.update(updates)
 scored_after=sum(row['evaluation_cohort']=='preregistered_forward_shadow' for row in result.values())
 due=[row for row in reg.values() if int(row['horizon_hours']) in ALLOWED_H and
      utc(row['target_utc'])+timedelta(minutes=2)<=now and utc(row['issued_at_utc'])>=freeze]
 # Later than 18h without a scored result must remain an explicit coverage failure, never disappear.
 eligible=len(due)
 resolved=sum(row['forecast_id'] in result for row in due)
 summary={'schema_version':1,'status':'ok','generated_at_utc':now.isoformat(),
  'source':'OKX public confirmed 1m history-candles DOGE-USDT',
  'protocol_id':protocol['protocol_id'],'original_V42_scores_modified':False,
  'method':'first confirmed 1m open >= registered target, using close after that full minute',
  'not_exact_issuance_to_target_quote':True,'historical_kline_not_a_live_quote':True,
  'data_availability_at_actual_retrieval_time_only':True,
  'max_target_to_bar_close_seconds':120,
  'all_registry_rows':len(reg),'minute_rows_total':len(result),
  'new_minute_scores':len(updates),
  'prefreeze_diagnostic_rows':sum(row['evaluation_cohort']=='pre_freeze_diagnostic_only' for row in result.values()),
  'preregistered_forward_rows':scored_after,
  'postfreeze_due_forecasts':eligible,'postfreeze_scored_forecasts':resolved,
  'postfreeze_quote_coverage_pct':round(100*resolved/eligible,2) if eligible else None,
  'postfreeze_minimum_coverage_target_pct':90,
  'not_validated_model_accuracy':True,'claim_permission':'NO_VALIDATED_EDGE',
  'limitations':['Distinct evaluation method; do NOT merge with V4.2 original ticker-based scored count',
  'History is fetched after the target; cannot assert real-time quote existed at target',
  'Data older than bounded retrieval window may remain unresolved and lower coverage',
  'Provider may revise historical candles; stored first observed bar and retrieval UTC are required',
  'Pre-freeze retrospective diagnostic rows NEVER qualify as forward accuracy evidence']}
 return summary,result

def main():
 OUT.mkdir(parents=True,exist_ok=True)
 try:
  old=read_rows(SCORES,SCOLS)
  verify_append_only(SCORES,old,SCOLS,ROOT)
  report,rows=evaluate()
  if rows!=old:write(SCORES,serialize(rows))
 except Exception as exc:
  report={'schema_version':1,'status':'blocked','generated_at_utc':datetime.now(timezone.utc).isoformat(),
          'claim_permission':'NO_VALIDATED_EDGE','error':type(exc).__name__+': '+str(exc)[:260]}
 write(LATEST,json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':report['status'],'new':report.get('new_minute_scores'),
                   'error':report.get('error'),'coverage':report.get('postfreeze_quote_coverage_pct')}),flush=True)
 return 0 if report['status']=='ok' else 1

if __name__=='__main__':
 raise SystemExit(main())

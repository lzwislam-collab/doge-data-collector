#!/usr/bin/env python3
"""Improve V4.2 genuine ticker outcome observability via existing 15m workflow.

Imports the unchanged, frozen directional score function. A ticker observation
may fill ONLY still-unscored forecasts whose target time has already passed
and whose first eligible quote is no later than target+25min.
Never issue new forecasts, re-score existing rows, impute late quotes,
overwrite a missing-score classification, or promote a claim of 70%.
"""
import json,math,os
from datetime import datetime,timedelta,timezone
from pathlib import Path
from direction_forward_v42 import (ROOT,FCOLS,SCOLS,TARGET_GRACE_MIN,
 MAX_QUOTE_AGE_SEC,fetch_ticker,read_rows,verify_append_only,score,serial,write,utc)

STATUS=ROOT/'data/stage5/spot_plus/quote_watch_latest.json'

def run(root=ROOT,now=None,ticker=None,verify_history=True):
 if ticker is None:
  ticker=fetch_ticker()
  now=datetime.now(timezone.utc)
 elif now is None:raise ValueError('test quote must have a known observation time')
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):
  raise ValueError('current clock must be UTC')
 price,quoted=ticker
 if quoted.tzinfo is None or quoted.utcoffset()!=timedelta(0):
  raise ValueError('quote clock must be UTC')
 age=(now-quoted).total_seconds()
 if not math.isfinite(price) or price<=0 or not 0<=age<=MAX_QUOTE_AGE_SEC:
  raise ValueError('quote stale, from future, or invalid')
 regpath=root/'data/direction_v42/forward_registry.csv'
 scrpath=root/'data/direction_v42/forward_scores.csv'
 registry=read_rows(regpath,FCOLS)
 records=read_rows(scrpath,SCOLS)
 if verify_history:
  verify_append_only(regpath,registry,FCOLS,root)
  verify_append_only(scrpath,records,SCOLS,root)
 eligible={}
 for fid,row in registry.items():
  if fid in records:continue
  target=utc(row['target_utc'])
  # Do not prematurely mark missed target quotes. The existing original
  # hourly scorer owns the final immutable missing/unscorable disposition.
  if target<=quoted<=target+timedelta(minutes=TARGET_GRACE_MIN):
   eligible[fid]=row
 new,added=score(eligible,{},price,quoted,now)
 if added!=len(eligible) or any(v['status']!='scored' for v in new.values()):
  raise ValueError('invalid due quote scoring')
 if added:
  records.update(new)
  write(scrpath,serial(records,SCOLS))
 out={'schema_version':1,'status':'ok','observed_at_utc':now.isoformat(),
  'source_quote_at_utc':quoted.isoformat(),
  'source':'OKX public DOGE-USDT actual ticker, no historical reconstruction',
  'source_quote_age_sec':round(age,2),
  'registered_total':len(registry),
  'previously_scored_or_unscorable_rows':len(records)-added,
  'eligible_new_target_quotes_scored':added,
  'scored_forecast_ids':sorted(new),
  'no_forecast_registered':True,
  'existing_scores_never_mutated':True,
  'missing_after_grace_not_imputed':True,
  'frozen_direction_model_modified':False,
  'claim_permission':'NO_VALIDATED_EDGE',
  'economic_trade_authorized':False}
 path=root/'data/stage5/spot_plus/quote_watch_latest.json'
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix('.json.tmp')
 tmp.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
 os.replace(tmp,path)
 return out

def main():
 try:
  out=run()
  print(json.dumps({'status':out['status'],
    'new':out['eligible_new_target_quotes_scored'],
    'source_quote_at_utc':out['source_quote_at_utc']},ensure_ascii=False))
  return 0
 except Exception as exc:
  print(json.dumps({'status':'blocked','error':type(exc).__name__+': '+str(exc)[:280]}))
  return 1

if __name__=='__main__':
 raise SystemExit(main())

#!/usr/bin/env python3
"""Strict descriptive audit of fixed post-hoc DOGE hourly crash alert. No trading signal."""
import csv,json,math,statistics,os
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'data/hourly_research/ohlc.csv'
OUT=ROOT/'data/crash_research/pilot_1h_latest.json'
HOUR=3600000
TARGET=-5
LOOKAHEAD=12
def iso(ms):return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()
def evaluate():
 d={'DOGE-USDT':{},'BTC-USDT':{},'ETH-USDT':{}}
 with SOURCE.open(newline='',encoding='utf-8') as f:
  reader=csv.DictReader(f)
  need={'symbol','open_utc_ms','open','high','low','close','volume_base','first_ingested_utc'}
  if not need.issubset(reader.fieldnames or []):raise ValueError('history schema incomplete')
  for r in reader:
   s=r['symbol'];t=int(r['open_utc_ms'])
   if s not in d or t%HOUR:raise ValueError('bad hourly key')
   if t in d[s]:raise ValueError('duplicate hourly key: '+s+str(t))
   o,hi,lo,c,v=(float(r[k]) for k in ('open','high','low','close','volume_base'))
   if not all(math.isfinite(x) for x in (o,hi,lo,c,v)) or min(o,lo,c)<=0 or hi<max(o,c,lo) or lo>min(o,c) or v<0:
    raise ValueError('bad OHLCV')
   d[s][t]={'open':o,'high':hi,'low':lo,'close':c,'volume':v,'ingested':r['first_ingested_utc']}
 ts=sorted(d['DOGE-USDT'])
 for s in d:
  if set(d[s])!=set(ts):raise ValueError('unmatched hourly timelines')
 if any(b-a!=HOUR for a,b in zip(ts,ts[1:])):raise ValueError('hour gaps')
 if len(ts)<100:raise ValueError('less than 100 hourly rows')
 crash=[t for t in ts if 100*(d['DOGE-USDT'][t]['close']/d['DOGE-USDT'][t]['open']-1)<=TARGET]
 signals=[]
 for i in range(24,len(ts)):
  t=ts[i];doge=d['DOGE-USDT'][t];btc=d['BTC-USDT'][t]
  prior=[d['DOGE-USDT'][z]['volume'] for z in ts[i-24:i]]
  median=statistics.median(prior)
  if median<=0:continue
  wick=100*(doge['low']/doge['open']-1)
  rel=100*((doge['close']/doge['open'])-(btc['close']/btc['open']))
  vol=doge['volume']/median
  # Rule was formed after viewing Oct 9 crash. DO NOT label it a historical real-time predictor.
  if wick<=-3 and vol>=5 and rel<=-1:
   end=t+HOUR
   future=[e for e in crash if end<=e<=end+LOOKAHEAD*HOUR]
   signals.append({'hour_start_utc':iso(t),'available_after_utc':iso(end),
    'volume_factor_vs_previous_24h_median':round(vol,3),
    'low_vs_open_pct':round(wick,3),'DOGE_minus_BTC_return_pp':round(rel,3),
    'next_12h_crash_starts_utc':[iso(e) for e in future],
    'true_pre_crash_alert':bool(future)})
 # Every single crash counts once, even if several alerts precede it.
 eligible=[e for e in crash if e>=ts[24]+HOUR]
 hit=sum(any(s['true_pre_crash_alert'] and
  datetime.fromisoformat(s['available_after_utc']).timestamp()*1000<=e<=
  datetime.fromisoformat(s['available_after_utc']).timestamp()*1000+LOOKAHEAD*HOUR
  for s in signals) for e in eligible)
 true_alerts=sum(x['true_pre_crash_alert'] for x in signals)
 precision=true_alerts/len(signals) if signals else None
 recall=hit/len(eligible) if eligible else None
 min_crashes=30
 result={'schema_version':1,'status':'research_fail_70pct' if eligible else 'insufficient_events',
  'evaluated_utc':datetime.now(timezone.utc).isoformat(),
  'source':'OKX historic 1H candles, first_ingested_utc in data/hourly_research/ohlc.csv',
  'history_start_utc':iso(ts[0]),'history_last_start_utc':iso(ts[-1]),
  'hourly_rows_by_symbol':len(ts),'duplicate_keys':0,'missing_hours':0,
  'crash_label':'DOGE hour close / hour open - 1 <= -5%',
  'alert_rule':'After complete hour: (hour low/hour open-1)<=-3% AND volume>=5x PRIOR 24h median AND DOGE hourly return minus BTC return<=-1pp',
  'forecast_target':'at least one new >=5% down candle in next 12h after alert becomes available',
  'rule_post_hoc':True,'genuine_prior_warnings_preserved':False,
  'crash_count_total':len(crash),'crash_hours_utc':[iso(t) for t in crash],
  'eligible_crashes':len(eligible),'caught_crashes':hit,
  'alert_count':len(signals),'true_alerts':true_alerts,'false_alerts':len(signals)-true_alerts,
  'recall_pct':round(recall*100,2) if recall is not None else None,
  'precision_pct':round(precision*100,2) if precision is not None else None,
  'always_no_crash_accuracy_pct':round((len(ts)-len(crash))/len(ts)*100,2),
  'signals':signals,
  'acceptance':{'target_recall_pct':70,'target_precision_pct':70,'minimum_distinct_heldout_crashes':min_crashes,
   'independent_holdout_crashes':0,'passed':False,
   'warning':'Thresholds were chosen after observing Oct9: past performance is exploratory and cannot be marketed as ex ante'},
  'NO_VALIDATED_EDGE':True,
  'next':'Accumulate longer validated 1H history, freeze rules before a genuinely future holdout, and report misses, false alerts and warning lead time.'}
 return result
def main():
 try:r=evaluate()
 except Exception as e:r={'status':'blocked','error':type(e).__name__+': '+str(e)[:250],'NO_VALIDATED_EDGE':True}
 OUT.parent.mkdir(parents=True,exist_ok=True)
 tmp=OUT.with_suffix('.json.tmp');tmp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');os.replace(tmp,OUT)
 print(json.dumps({'status':r['status'],'crashes':r.get('eligible_crashes'),
  'alerts':r.get('alert_count'),'precision_pct':r.get('precision_pct'),
  'recall_pct':r.get('recall_pct'),'error':r.get('error')},ensure_ascii=False))
 return 1 if r['status']=='blocked' else 0
if __name__=='__main__':raise SystemExit(main())

#!/usr/bin/env python3
"""Case audit: 2026-10-09 JST DOGE -5% hourly crash. NO hindsight success claim."""
import csv,json,math,statistics,os
from collections import defaultdict
from datetime import datetime,timezone,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
INPUT=ROOT/'data/hourly.csv'
OUT=ROOT/'data/crash_research/oct09_audit.json'
REPORT=ROOT/'reports/DOGE_OCT09_CRASH_POSTMORTEM.md'
DAYMS=3600000
EVENT_TS=int(datetime(2026,10,8,15,tzinfo=timezone.utc).timestamp()*1000)
TARGET_DROP_PCT=-5.0
LEAD_HOURS=12
MIN_DISTINCT_TEST_CRASHES=30
ACCEPTANCE_MIN_PRECISION=.70
ACCEPTANCE_MIN_RECALL=.70

def iso(ms):return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()
def read_hours(path=INPUT):
 result=defaultdict(dict);dupes=[];revised=[];capture={}
 with path.open(newline='',encoding='utf-8') as f:
  reader=csv.DictReader(f)
  need={'collected_at_ms','symbol','closed_hour_start_ms','hour_open','hour_high','hour_low','hour_close','hour_volume_base'}
  if not need.issubset(reader.fieldnames or ()):raise ValueError('missing 1H columns')
  for r in reader:
   symbol=r['symbol']
   if symbol not in ('DOGE-USDT','BTC-USDT','ETH-USDT'):continue
   stamp=int(r['closed_hour_start_ms']);seen=int(r['collected_at_ms'])
   if stamp%DAYMS:raise ValueError('misaligned UTC hour '+str(stamp))
   vals=[float(r[k]) for k in ('hour_open','hour_high','hour_low','hour_close','hour_volume_base')]
   o,hi,lo,c,v=vals
   if not all(math.isfinite(x) for x in vals) or min(o,c,lo)<=0 or v<0 or hi<max(o,c) or lo>min(o,c):
    raise ValueError('invalid hour OHLCV')
   cur={'ts':stamp,'open':o,'high':hi,'low':lo,'close':c,'volume':v}
   if stamp in result[symbol]:
    if result[symbol][stamp]!=cur:revised.append((symbol,iso(stamp)))
    else:dupes.append((symbol,iso(stamp)))
   else:result[symbol][stamp]=cur
   capture[(symbol,stamp)]=min(seen,capture.get((symbol,stamp),seen))
 if revised:raise ValueError('conflicting 1H values '+str(revised[:3]))
 if dupes:raise ValueError('duplicate 1H rows '+str(dupes[:3]))
 for sym,d in result.items():
  ts=sorted(d)
  if any(b-a!=DAYMS for a,b in zip(ts,ts[1:])):raise ValueError('missing hourly candle '+sym)
 if set(result['DOGE-USDT'])!=set(result['BTC-USDT']) or set(result['DOGE-USDT'])!=set(result['ETH-USDT']):
  raise ValueError('unaligned DOGE/BTC/ETH hourly records')
 return result,capture

def ret(x):return 100*(x['close']/x['open']-1)
def wick(x):return 100*(x['low']/x['open']-1)
def candidate_at(bars,ts):
 # All features available only AFTER the bar itself closes; no current-hour/next-hour information.
 d=bars['DOGE-USDT'].get(ts);btc=bars['BTC-USDT'].get(ts)
 if d is None or btc is None:return None
 preceding=[bars['DOGE-USDT'].get(ts-i*DAYMS) for i in range(1,25)]
 if any(x is None for x in preceding):return None
 median=statistics.median(x['volume'] for x in preceding)
 if median<=0:return None
 rel=ret(d)-ret(btc);multiple=d['volume']/median
 return {'hour_start_utc':iso(ts),'known_after_utc':iso(ts+DAYMS),
   'hour_close_return_pct':round(ret(d),3),'hour_open_to_low_pct':round(wick(d),3),
   'prior_24h_median_volume_doge':round(median,2),'volume_multiple':round(multiple,3),
   'doge_minus_btc_hour_return_pp':round(rel,3),
   'fires':bool(wick(d)<=-3 and multiple>=5 and rel<=-1)}

def examine(bars,capture):
 ts=sorted(bars['DOGE-USDT']);n=len(ts)
 events=[t for t in ts if ret(bars['DOGE-USDT'][t])<=TARGET_DROP_PCT]
 if EVENT_TS not in events:raise ValueError('screenshot event not recovered from OKX history')
 case=bars['DOGE-USDT'][EVENT_TS]
 pre=EVENT_TS-11*DAYMS
 alert=candidate_at(bars,pre)
 if alert is None:raise ValueError('missing pre-crash complete 24h volume window')
 # Diagnostics not a successful prediction: candidate rule drafted after observing the outcome.
 triggers=[candidate_at(bars,t) for t in ts]
 triggers=[x for x in triggers if x and x['fires']]
 precursors=[x for x in triggers if
  datetime.fromisoformat(x['known_after_utc']).timestamp()*1000<=EVENT_TS and
  datetime.fromisoformat(x['known_after_utc']).timestamp()*1000>=EVENT_TS-LEAD_HOURS*DAYMS]
 first_ingest=capture[('DOGE-USDT',EVENT_TS)]
 item={'schema_version':1,'status':'insufficient_validation_sample',
   'source':'OKX 1H OHLCV backfilled into data/hourly.csv; market time separate from first local capture',
   'data_start_utc':iso(ts[0]),'data_end_utc':iso(ts[-1]),'hours':n,
   'duplicates':0,'gaps':0,
   'screenshot_crash':{'start_utc':iso(EVENT_TS),'start_jst':iso(EVENT_TS+9*DAYMS)[:-6]+' JST (converted clock)',
     'open':case['open'],'low':case['low'],'close':case['close'],
     'open_close_pct':round(ret(case),3),'open_to_low_pct':round(wick(case),3),
     'volume_doge':case['volume'],
     'btc_same_hour_pct':round(ret(bars['BTC-USDT'][EVENT_TS]),3),
     'eth_same_hour_pct':round(ret(bars['ETH-USDT'][EVENT_TS]),3),
     'source_first_ingested_utc':iso(first_ingest),
     'was_genuinely_warned_before_crash':False},
   'exploratory_earlier_signal':alert,
   'post_hoc_rule_first_alerts_in_prior_12h':precursors,
   'rule_fixed_before_holdout':False,
   'crash_hour_count':len(events),'crash_start_times_utc':[iso(t) for t in events],
   'naive_always_no_crash_accuracy_pct':round((n-len(events))/n*100,2),
   'distinct_independent_validation_events':0,
   'research_acceptance':{'historical_distinct_crashes_minimum':MIN_DISTINCT_TEST_CRASHES,
      'precision_minimum':ACCEPTANCE_MIN_PRECISION,'recall_minimum':ACCEPTANCE_MIN_RECALL,
      'warning_lead_time_hours_minimum':1,'false_alarm_budget':'must be predeclared and audited',
      'passed':False},
   'reason':'Only one historical >=5% 1H close-to-open crash in 116 hours; post-hoc alert thresholds and source backfill cannot certify causal predictive performance',
   'no_forecast_edge_claim':True}
 return item

def main():
 try:obj=examine(*read_hours())
 except Exception as e:obj={'schema_version':1,'status':'blocked','error':type(e).__name__+': '+str(e)[:300]}
 OUT.parent.mkdir(parents=True,exist_ok=True)
 tmp=OUT.with_suffix('.json.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n');os.replace(tmp,OUT)
 if obj['status']!='blocked':
  ev=obj['screenshot_crash'];pre=obj['exploratory_earlier_signal']
  s=f"""# DOGE 2026-10-09（JST）1小时暴跌｜事后审计

**裁决：INSUFFICIENT VALIDATION SAMPLE / NO_VALIDATED_EDGE。**

- 截止：{obj['data_end_utc']}，数据来自OKX 1H历史K线，116小时连续且内部无缺口。
- 主要跌幅窗口：2026-10-08 15:00–16:00 UTC = 2026-10-09 00:00–01:00 JST。
- DOGE：开 {ev['open']}，低 {ev['low']}，收 {ev['close']}，收盘跌 {ev['open_close_pct']}%；当小时BTC {ev['btc_same_hour_pct']}%，ETH {ev['eth_same_hour_pct']}%。
- 数据在本系统的首次入库：{ev['source_first_ingested_utc']}，**在暴跌以后**，不是实盘提前预警。
- 事后观察到11小时前有异常小时线：{pre['hour_start_utc']}；小时内最低下探 {pre['hour_open_to_low_pct']}%，该小时成交量为之前24小时中位数的{pre['volume_multiple']}倍；相对于BTC弱 {pre['doge_minus_btc_hour_return_pp']}个百分点。
- 这套异常规则是**看过2026-10-09的结果后提出**；即使事后在那段数据触发，也不能视为预测成功。
- 全库116小时仅 {obj['crash_hour_count']} 次达到单小时收盘跌幅至少5%；恒定预测无大跌，也能有 {obj['naive_always_no_crash_accuracy_pct']}%的虚高总体准确率。
- 下一步必须积累更长、来源可审计的1H数据，再用时间顺序严格隔离训练、验证、未来前瞻；不足以验证70%目标前保持BLOCKED。
- 实用验收：真正暴跌预警的precision和recall都至少70%，提前量至少1小时，并限制误报频率；这是**目标，不是承诺**。

本报告未更改DOGE定投策略，不产生自动交易信号。
"""
  REPORT.parent.mkdir(exist_ok=True)
  tmp=REPORT.with_suffix('.md.tmp');tmp.write_text(s,encoding='utf-8');os.replace(tmp,REPORT)
 print(json.dumps({'status':obj['status'],'hours':obj.get('hours'),'events':obj.get('crash_hour_count'),
    'prior_signal':obj.get('exploratory_earlier_signal',{}).get('fires'),
    'reason':obj.get('error')},ensure_ascii=False))
 return 0 if obj['status']!='blocked' else 1
if __name__=='__main__':raise SystemExit(main())

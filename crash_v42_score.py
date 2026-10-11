#!/usr/bin/env python3
"""V4.2 prospective-only hourly crash scoring: no hindsight, >=1h lead."""
import json,math
from datetime import datetime,timezone,timedelta
from pathlib import Path
from crash_forward_watch import ROOT,BASE,HOUR,WINDOW_HOURS,MAX_DELAY_MIN,MODEL,hour_rows,read_registry,verify_registry,write,iso

OUT=BASE/'v42_scores.json'
ALARM_COOLDOWN_HOURS=12
CRASH_CLUSTER_HOURS=12
MIN_LEAD_HOURS=1

def ceil_hour(ms):
 return ((ms+HOUR-1)//HOUR)*HOUR

def target_window(issue):
 """Only full hourly candles STARTING >=1h after issued_at and <issued_at+12h."""
 ms=round(issue.timestamp()*1000)
 start=ceil_hour(ms+MIN_LEAD_HOURS*HOUR)
 end=ceil_hour(ms+WINDOW_HOURS*HOUR)
 return tuple(range(start,end,HOUR)),start,end

def parse_utc(s):
 dt=datetime.fromisoformat(s.replace('Z','+00:00'))
 if dt.tzinfo is None or dt.utcoffset()!=timedelta(0):raise ValueError('not UTC')
 return dt

def scoring(rows,per,points,now):
 observed=set(points);latest=points[-1]
 scored=[];pending=[];quarantined=[];issues=[]
 for fid,r in sorted(rows.items(),key=lambda kv:kv[1]['issued_at_utc']):
  try:
   issue=parse_utc(r['issued_at_utc'])
   source_start=parse_utc(r['signal_hour_start_utc'])
   closed_at=parse_utc(r['available_after_utc'])
   source_ingest=parse_utc(r['source_first_ingested_utc'])
   if r['rule_id']!=MODEL or r['watch_id']!=fid:raise ValueError('rule/key changed')
   if int(source_start.timestamp()*1000)%HOUR:raise ValueError('misaligned source')
   if closed_at!=source_start+timedelta(hours=1):raise ValueError('incorrect candle close')
   latency=(issue-closed_at).total_seconds()/60
   if not (0<=latency<=MAX_DELAY_MIN):raise ValueError('late/predated issue')
   if source_ingest>issue:raise ValueError('source not captured by issue time')
   if r['alert'] not in ('0','1'):raise ValueError('alert missing or invalid')
   candles,first,end=target_window(issue)
   if not candles or any(t<=int(source_start.timestamp()*1000) for t in candles):
    raise ValueError('target overlaps observed feature candle')
   assert all(t>=issue.timestamp()*1000+HOUR for t in candles)
  except (ValueError,KeyError,AssertionError) as e:
   quarantined.append({'watch_id':fid,'reason':type(e).__name__+': '+str(e)});continue
  if latest<candles[-1]:
   pending.append({'watch_id':fid,'reason':'awaiting complete forward target candle','last_required_utc':iso(candles[-1])});continue
  if any(t not in observed for t in candles):
   quarantined.append({'watch_id':fid,'reason':'missing complete UTC target hour'});continue
  events=[t for t in candles if 100*(per['DOGE-USDT'][t]['close']/per['DOGE-USDT'][t]['open']-1)<=-5]
  scored.append({'watch_id':fid,'issued_at_utc':issue.isoformat(),
   'signal_hour_start_utc':source_start.isoformat(),
   'target_first_hour_start_utc':iso(first),'target_end_exclusive_hour_start_utc':iso(end),
   'last_hour_required_utc':iso(candles[-1]),'alert':r['alert']=='1',
   'realized_event':bool(events),'event_starts_utc':[iso(e) for e in events]})
 # Each registered alert remains visible; 12h cooldown suppresses repeated notifications.
 active=[];suppressed=[];last_issue=None
 for row in scored:
  if not row['alert']:continue
  issue=parse_utc(row['issued_at_utc'])
  if last_issue is not None and (issue-last_issue).total_seconds()<ALARM_COOLDOWN_HOURS*3600:
   suppressed.append(row['watch_id']);continue
  active.append(row);last_issue=issue
 # Independently cluster close-together 1H crashes as one risk event.
 crashes=[t for t in points if 100*(per['DOGE-USDT'][t]['close']/per['DOGE-USDT'][t]['open']-1)<=-5]
 clusters=[]
 for t in crashes:
  if not clusters or t-clusters[-1][-1]>=CRASH_CLUSTER_HOURS*HOUR:clusters.append([t])
  else:clusters[-1].append(t)
 # A prospective event is evaluable only with 12 consecutive scored real-time checkpoints
 # from the hours BEFORE the event (no backfilled unregistered negatives).
 registered_starts={round(parse_utc(r['signal_hour_start_utc']).timestamp()*1000) for r in scored}
 eligible=[];unobserved=[]
 for group in clusters:
  t=group[0]
  required=[t-i*HOUR for i in range(3,14)]
  (eligible if all(z in registered_starts for z in required) else unobserved).append(t)
 matched=set();matched_alerts=set();lead_hours=[]
 for e in (g[0] for g in clusters):
  for alert in active:
   if alert['watch_id'] in matched_alerts:continue
   issue=parse_utc(alert['issued_at_utc'])
   window,_,_=target_window(issue)
   if any(t in window for t in next(g for g in clusters if g[0]==e)):
    matched.add(e);matched_alerts.add(alert['watch_id']);lead_hours.append(round((e/1000-issue.timestamp())/3600,3));break
 true_alarms=len(matched_alerts);false_alarms=len(active)-true_alarms
 precision=100*true_alarms/len(active) if active else None
 recall=100*len(matched.intersection(eligible))/len(eligible) if eligible else None
 # Keep exploratory metrics apart from 70% approval; sufficiently independent tests absent.
 return {'schema_version':2,'status':'ok','evaluated_utc':now.isoformat(),
  'rule_version':'V4.2_scoring_only_v1','legacy_watch_rows_unmodified':True,
  'horizon':'event candle START >= issued_at + 1h AND < issued_at + 12h; evaluate only after each eligible candle closes',
  'score_window_lead_min_hours':MIN_LEAD_HOURS,'alarm_cooldown_hours':ALARM_COOLDOWN_HOURS,
  'event_cluster_hours':CRASH_CLUSTER_HOURS,
  'registered':len(rows),'scored':scored,'pending':pending,'quarantined':quarantined,
  'scored_count':len(scored),'pending_count':len(pending),'quarantined_count':len(quarantined),
  'active_alerts':len(active),'cooldown_suppressed_alert_ids':suppressed,
  'true_matched_alerts':true_alarms,'false_alerts':false_alarms,'matched_event_lead_hours':lead_hours,
  'alert_precision_pct':round(precision,2) if precision is not None else None,
  'eligible_clustered_events':len(eligible),'unobserved_clustered_events':len(unobserved),
  'matched_clustered_events':len(matched.intersection(eligible)),
  'matched_any_clustered_events':len(matched),
  'event_recall_pct':round(recall,2) if recall is not None else None,
  'target_precision_pct':70,'target_recall_pct':70,'minimum_independent_holdout_events':30,
  'eligible_for_predictive_claim':False,'approval':'BLOCKED_NO_VALIDATED_EDGE',
  'evidence_warning':'Event-coverage eligibility requires each of the 11 eligible prior hourly checkpoints; missing collection is not scored as negative; this remains a research-only, post-hoc selected alert rule.'}

def run(now=None,base=ROOT):
 if now is None:now=datetime.now(timezone.utc)
 per,points=hour_rows(Path(base)/'data/hourly_research/ohlc.csv')
 rows=read_registry(Path(base)/'data/crash_research/prospective_hourly.csv')
 verify_registry(rows,base)
 result=scoring(rows,per,points,now)
 if len(result['scored'])+len(result['pending'])+len(result['quarantined'])!=len(rows):
  raise ValueError('checkpoint accounting mismatch')
 return result

def main():
 try:r=run()
 except Exception as exc:r={'schema_version':2,'status':'blocked','error':type(exc).__name__+': '+str(exc)[:220],
  'approval':'BLOCKED_NO_VALIDATED_EDGE'}
 write(OUT,json.dumps(r,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:r.get(k) for k in ('status','registered','scored_count','pending_count','alert_precision_pct','event_recall_pct','error')}))
 return 0 if r['status']=='ok' else 1
if __name__=='__main__':raise SystemExit(main())

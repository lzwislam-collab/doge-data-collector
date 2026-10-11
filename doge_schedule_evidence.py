#!/usr/bin/env python3
"""Audit real GitHub Actions scheduled execution and persisted collector snapshots."""
import json, os, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUTPUT=ROOT/'data/stage1/schedule_evidence.json'
REPO=os.getenv('GITHUB_REPOSITORY','lzwislam-collab/doge-data-collector')
TOKEN=os.getenv('GITHUB_TOKEN','')
NOW=datetime.now(timezone.utc)
SPECS={
 'whales':('.github/workflows/whale_blocks.yml','data/whales_blocks/latest.json','ended_at_utc'),
 'okx_addresses':('.github/workflows/okx_doge_balances.yml','data/okx_address_balances/latest.json','ended_at_utc'),
 'onchain':('.github/workflows/onchain.yml','data/onchain/latest.json','observed_at_utc'),
 'daily_metrics':('.github/workflows/onchain_metrics.yml','data/onchain/metrics_latest.json','collected_at_utc')
}
STAGE2_SPECS={
 'liquidations_okx':('.github/workflows/liquidations.yml','data/liquidations/latest.json','ended_at_utc',('ok',)),
 'liquidity_okx':('.github/workflows/liquidity.yml','data/liquidity/latest.json','collected_at_utc',('ok',)),
 'liquidations_binance':('.github/workflows/binance_liquidations.yml','data/binance_liquidations/latest.json','ended_at_utc',('partial',)),
 'liquidations_bybit':('.github/workflows/bybit_liquidations.yml','data/bybit_liquidations/latest.json','ended_at_utc',('partial',))
}
STAGE3_SPECS={
 'btc_eth_etf':('.github/workflows/etf_flows.yml','data/etf/latest.json','ended_at_utc',('ok',))
}
STAGE4_SPECS={
 'fred_macro':('.github/workflows/macro.yml','data/macro/latest.json','ended_at_utc',('ok',)),
 'fed_bls_calendar':('.github/workflows/policy_events.yml','data/policy_events/latest.json','ended_at_utc',('ok',)),
 'cftc_regulatory':('.github/workflows/regulatory_events.yml','data/regulatory_events/latest.json','ended_at_utc',('ok',)),
 'sec_regulatory':('.github/workflows/sec_events.yml','data/sec_events/latest.json','ended_at_utc',('ok',))
}

def parse(s):
 d=datetime.fromisoformat(str(s).replace('Z','+00:00'))
 if d.tzinfo is None:raise ValueError('Naive timestamp')
 return d.astimezone(timezone.utc)

def snapshot_status_eligible(name,snap,allowed):
 # Partial means limited observation, NOT automatically a source malfunction.
 # Only OKX WebSocket partial runs can be accepted with independently reported
 # healthy subscription and no parser or transport errors.
 if snap.get('status') in allowed:
  return True
 if name!='liquidations_okx' or snap.get('status')!='partial':
  return False
 dq=snap.get('data_quality') or {}
 try:
  percent=float(snap.get('subscription_coverage_pct',0))
  return (snap.get('not_market_wide') is True
          and snap.get('historical_backfill') is False
          and dq.get('subscribed') is True
          and percent>=85.0
          and not dq.get('errors')
          and int(dq.get('parse_errors',1))==0
          and int(dq.get('subscription_ack_count',0))>=1)
 except (TypeError,ValueError):
  return False

def get(url):
 headers={'Accept':'application/vnd.github+json','User-Agent':'DOGE-collector-schedule-audit/1.0','X-GitHub-Api-Version':'2022-11-28'}
 if TOKEN:headers['Authorization']='Bearer '+TOKEN
 req=urllib.request.Request(url,headers=headers)
 with urllib.request.urlopen(req,timeout=25) as rsp:return json.load(rsp)

def audit():
 if not TOKEN:raise ValueError('GITHUB_TOKEN missing; cannot verify run events')
 if '/' not in REPO:raise ValueError('Invalid GitHub repository')
 reports={};stage2_reports={};stage3_reports={};stage4_reports={};all_ok=True;stage2_ok=True;stage3_ok=True;stage4_ok=True
 for name,workflow,file,time_key,allow_status in (
  [(name,*spec,('ok',)) for name,spec in SPECS.items()]+
  [(name,*spec) for name,spec in STAGE2_SPECS.items()]+
  [(name,*spec) for name,spec in STAGE3_SPECS.items()]+
  [(name,*spec) for name,spec in STAGE4_SPECS.items()]
 ):
  x={'workflow':workflow,'snapshot':file,'state':'UNVERIFIED',
     'allowed_snapshot_statuses':list(allow_status)}
  try:
   # GitHub API directly filters to scheduled runs, never infer schedule from YAML or commit.
   url='https://api.github.com/repos/'+REPO+'/actions/workflows/'+workflow.split('/')[-1]+'/runs?event=schedule&per_page=30'
   data=get(url)
   runs=data.get('workflow_runs',[])
   recent=[r for r in runs if r.get('event')=='schedule' and r.get('status')=='completed' and parse(r['created_at'])>=NOW-timedelta(hours=48)]
   successful=[r for r in recent if r.get('conclusion')=='success']
   x['scheduled_completed_last_48h']=len(recent)
   x['scheduled_success_last_48h']=len(successful)
   x['scheduled_failure_last_48h']=sum(r.get('conclusion')=='failure' for r in recent)
   if successful:
    last=max(successful,key=lambda r:parse(r['created_at']))
    x['latest_scheduled_success']={'id':last['id'],'created_at':last['created_at'],'updated_at':last['updated_at'],'html_url':last.get('html_url')}
   path=ROOT/file
   if not path.exists():raise ValueError('Snapshot file absent')
   snap=json.loads(path.read_text(encoding='utf-8'))
   t=parse(snap[time_key]);x['snapshot_observed_utc']=t.isoformat()
   x['snapshot_age_hours']=round((NOW-t).total_seconds()/3600,2)
   x['snapshot_status']=snap.get('status')
   if t>NOW+timedelta(minutes=5):raise ValueError('Future snapshot time')
   if not successful:
    x['reason']='No successful scheduled run in preceding 48h'
   elif not snapshot_status_eligible(name,snap,allow_status):
    x['reason']='Snapshot status or partial subscription quality not eligible: '+str(snap.get('status'))
   else:
    # Verify data came from at least one eligible successful scheduled run window.
    eligible=[r for r in successful if parse(r['created_at'])-timedelta(minutes=2)<=t<=parse(r['updated_at'])+timedelta(minutes=10)]
    if eligible:
     r=max(eligible,key=lambda z:parse(z['created_at']))
     x['state']='SCHEDULE_AND_SNAPSHOT_TIME_MATCH'
     x['scope']='PARTIAL_SUBSCRIPTION_ONLY_NOT_MARKET_TOTAL' if snap.get('status')=='partial' else 'SOURCE_DEFINED_SNAPSHOT'
     x['matched_run_id']=r['id']
     x['reason']='Schedule event, success and compatible committed snapshot timestamp; commit attribution not proven'
    else:x['reason']='Schedule succeeded but no timestamp-matching snapshot; cannot attribute persistence'
  except (ValueError,KeyError,TypeError,urllib.error.URLError) as exc:
   x['reason']=type(exc).__name__+': '+str(exc)[:280]
  if name in STAGE4_SPECS:
   stage4_reports[name]=x
   if x['state']!='SCHEDULE_AND_SNAPSHOT_TIME_MATCH':stage4_ok=False
  elif name in STAGE3_SPECS:
   stage3_reports[name]=x
   if x['state']!='SCHEDULE_AND_SNAPSHOT_TIME_MATCH':stage3_ok=False
  elif name in STAGE2_SPECS:
   stage2_reports[name]=x
   if x['state']!='SCHEDULE_AND_SNAPSHOT_TIME_MATCH':stage2_ok=False
  else:
   reports[name]=x
   if x['state']!='SCHEDULE_AND_SNAPSHOT_TIME_MATCH':all_ok=False
 return {'schema_version':4,'status':'all_evidence_matched' if all_ok else 'partial_or_unverified',
         'generated_at_utc':NOW.isoformat(),'repository':REPO,'checks':reports,
         'stage2_status':'all_evidence_matched_with_coverage_limits' if stage2_ok else 'partial_or_unverified',
         'stage2_checks':stage2_reports,
         'stage3_status':'all_evidence_matched_data_quality_unverified' if stage3_ok else 'partial_or_unverified',
         'stage3_checks':stage3_reports,
         'stage4_status':'all_evidence_matched_with_pit_limits' if stage4_ok else 'partial_or_unverified',
         'stage4_checks':stage4_reports,
         'stage4_historical_release_vintages_verified':False,
         'stage4_regulatory_feed_is_comprehensive':False,
         'stage3_etf_data_history_complete':False,
         'stage3_etf_backfill_point_in_time_verified':False,
         'stage2_verified_market_wide_liquidations':False,
         'stage2_coverage_note':'Scheduled partial exchange notification windows are NOT 24-hour or market-wide liquidation totals; CoinGlass unavailable due to paid plan.',
         'limitations':['Run event and snapshot timestamp alignment does not prove causal commit attribution','Scheduled runs may be delayed or skipped by GitHub','Do not infer real exchange netflow from address balance samples','Partial liquidation observation windows may miss forced liquidations and are not representative of 24h totals']}

def main():
 OUTPUT.parent.mkdir(parents=True,exist_ok=True)
 try:obj=audit()
 except Exception as exc:obj={'status':'error','generated_at_utc':NOW.isoformat(),'error':type(exc).__name__+': '+str(exc)[:350]}
 temp=OUTPUT.with_suffix('.json.tmp');temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(temp,OUTPUT)
 print(json.dumps({'status':obj['status'],'error':obj.get('error')},ensure_ascii=False))
 return 1 if obj['status']=='error' else 0
if __name__=='__main__':raise SystemExit(main())

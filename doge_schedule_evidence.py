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

def parse(s):
 d=datetime.fromisoformat(str(s).replace('Z','+00:00'))
 if d.tzinfo is None:raise ValueError('Naive timestamp')
 return d.astimezone(timezone.utc)

def get(url):
 headers={'Accept':'application/vnd.github+json','User-Agent':'DOGE-collector-schedule-audit/1.0','X-GitHub-Api-Version':'2022-11-28'}
 if TOKEN:headers['Authorization']='Bearer '+TOKEN
 req=urllib.request.Request(url,headers=headers)
 with urllib.request.urlopen(req,timeout=25) as rsp:return json.load(rsp)

def audit():
 if not TOKEN:raise ValueError('GITHUB_TOKEN missing; cannot verify run events')
 if '/' not in REPO:raise ValueError('Invalid GitHub repository')
 reports={};all_ok=True
 for name,(workflow,file,time_key) in SPECS.items():
  x={'workflow':workflow,'snapshot':file,'state':'UNVERIFIED'}
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
   elif snap.get('status')!='ok':
    x['reason']='Snapshot status not OK'
   else:
    # Verify data came from at least one eligible successful scheduled run window.
    eligible=[r for r in successful if parse(r['created_at'])-timedelta(minutes=2)<=t<=parse(r['updated_at'])+timedelta(minutes=10)]
    if eligible:
     r=max(eligible,key=lambda z:parse(z['created_at']))
     x['state']='SCHEDULE_AND_SNAPSHOT_TIME_MATCH'
     x['matched_run_id']=r['id']
     x['reason']='Schedule event, success and compatible committed snapshot timestamp; commit attribution not proven'
    else:x['reason']='Schedule succeeded but no timestamp-matching snapshot; cannot attribute persistence'
  except (ValueError,KeyError,TypeError,urllib.error.URLError) as exc:
   x['reason']=type(exc).__name__+': '+str(exc)[:280]
  reports[name]=x
  if x['state']!='SCHEDULE_AND_SNAPSHOT_TIME_MATCH':all_ok=False
 return {'schema_version':1,'status':'all_evidence_matched' if all_ok else 'partial_or_unverified',
         'generated_at_utc':NOW.isoformat(),'repository':REPO,'checks':reports,
         'limitations':['Run event and snapshot timestamp alignment does not prove causal commit attribution','Scheduled runs may be delayed or skipped by GitHub','Do not infer real exchange netflow from address balance samples']}

def main():
 OUTPUT.parent.mkdir(parents=True,exist_ok=True)
 try:obj=audit()
 except Exception as exc:obj={'status':'error','generated_at_utc':NOW.isoformat(),'error':type(exc).__name__+': '+str(exc)[:350]}
 temp=OUTPUT.with_suffix('.json.tmp');temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(temp,OUTPUT)
 print(json.dumps({'status':obj['status'],'error':obj.get('error')},ensure_ascii=False))
 return 1 if obj['status']=='error' else 0
if __name__=='__main__':raise SystemExit(main())

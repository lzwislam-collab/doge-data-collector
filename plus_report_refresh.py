"""Bounded GET-only wait inside an existing report. Never dispatches or scores.

This is a freshness hint, not an alternative eligibility/scoring authority.
The unchanged PLUS projection must independently verify all frozen contracts.
"""
import argparse
import base64
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO='lzwislam-collab/doge-data-collector'
WATCH_LOOKAHEAD_SECONDS=300
MAX_WAIT_SECONDS=900
TRACKS={
 'original18':('data/learned_shadow_v1','config/learned_shadow_v1.json','.github/workflows/learned_shadow_v1.yml'),
 'r2':('data/phase2_forward_v2','config/phase2_forward_v2.json','.github/workflows/phase2_forward_audit_v2.yml'),
}

def utc(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)

def watch_targets(root,now,budget):
 result=[]
 for track,(folder,policy,_) in TRACKS.items():
  paths=sorted((Path(root)/folder/'events').glob('*.json'))
  if not paths:continue
  p=json.loads((Path(root)/policy).read_bytes())
  window=timedelta(minutes=p['target_window_minutes']) if track=='original18' else timedelta(seconds=p['target_window_seconds'])
  for path in paths:
   raw=path.read_bytes();e=json.loads(raw)
   if track=='r2' and (e.get('cohort')!='formal' or not e.get('included_in_formal_statistics')):continue
   target=utc(e['target_utc']);deadline=target+window
   if track=='original18':
    deadline=min(deadline,utc(e['slot_utc'])+timedelta(hours=p['slot_steps_hours'][str(e['horizon_hours'])]))
   if target<=now+timedelta(seconds=budget) and now<deadline:
    result.append({'track':track,'event_id':e['event_id'],'target':target.isoformat(),'deadline':deadline.isoformat(),'event_raw':raw})
 return result

def target_witnessed(w,score_raw,runs,read_at):
 folder,_,workflow=TRACKS[w['track']]
 for run in runs:
  try:
   created=utc(run['created_at']);finished=utc(run['updated_at']);head=run['head_sha']
   if (run['repository']['full_name']!=REPO or run['head_branch']!='main'
       or run['path'].split('@')[0]!=workflow or run['event'] not in ('push','workflow_dispatch','workflow_run')
       or w['track']=='original18' and run['event']=='workflow_run'
       or run['status']!='completed' or run['conclusion']!='success'
       or not re.fullmatch('[0-9a-f]{40}',head)
       or not utc(w['target'])<=created<=finished<utc(w['deadline'])):continue
   expected={folder+'/events/'+w['event_id']+'.json':w['event_raw'],folder+'/scores/'+w['event_id']+'.json':score_raw}
   if all(read_at(head,path)==raw for path,raw in expected.items()):return True
  except (KeyError,ValueError,TypeError):continue
 return False

def wait_budget(watch,now):
 # Conservative for mixed targets: never wait past the first original close.
 # This limits report freshness waiting, never extends scoring eligibility.
 return min(MAX_WAIT_SECONDS,max(0,min((utc(w['deadline'])-now).total_seconds() for w in watch))) if watch else 0

def wait_for_witness(watch,probe,*,budget=MAX_WAIT_SECONDS,interval=15,monotonic=time.monotonic,sleep=time.sleep,progress=None):
 if not 0<=budget<=MAX_WAIT_SECONDS or interval<=0:raise ValueError('bounded positive wait required')
 out={'status':'NO_PENDING_TARGET','attempts':0,'pending':[w['event_id'] for w in watch],
      'eligibility_authority':False,'dispatch_performed':False}
 started=monotonic();last=None
 def snapshot(status):
  out.update(status=status,budget_seconds=budget,elapsed_seconds=monotonic()-started)
  if progress:progress(dict(out))
  return out
 if not watch:return snapshot('NO_PENDING_TARGET')
 while True:
  remaining=budget-(monotonic()-started)
  if remaining<=0:
   return snapshot('TIMEOUT')
  out['attempts']+=1
  try:
   last=probe(watch,remaining)
   if not re.fullmatch('[0-9a-f]{40}',last['head_sha']):raise ValueError('invalid pinned main head')
   out.update(head_sha=last['head_sha'],pending=last['pending'])
  except TimeoutError:
   out.update(status='TIMEOUT',error_type='TimeoutError')
   return snapshot('TIMEOUT')
  except Exception as exc:
   out.update(status='ERROR',error_type=type(exc).__name__)
   # Do not emit a checkout ref on a remote read/integrity failure.
   out.pop('head_sha',None)
   return snapshot('ERROR')
  if monotonic()-started>=budget:
   return snapshot('TIMEOUT')
  if not out['pending']:
   return snapshot('READY')
  snapshot('WAITING')
  sleep(min(interval,max(0,budget-(monotonic()-started))))

class Reader:
 def __init__(self,seconds):self.end=time.monotonic()+seconds;self.cache={}
 def get(self,path):
  remaining=self.end-time.monotonic()
  if remaining<=0:raise TimeoutError('refresh read budget exhausted')
  req=Request('https://api.github.com/repos/'+REPO+path,headers={
   'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'DOGE-PLUS-read-only-refresh'})
  with urlopen(req,timeout=min(5,remaining)) as response:return json.load(response)
 def read_at(self,head,path):
  key=(head,path)
  if key not in self.cache:
   try:
    item=self.get('/contents/'+path+'?ref='+head)
    if item.get('encoding')!='base64':raise ValueError('inline pinned bytes required')
    self.cache[key]=base64.b64decode(item['content'],validate=False)
   except HTTPError as exc:
    if exc.code!=404:raise
    self.cache[key]=b''
  return self.cache[key]
 def probe(self,watch,remaining):
  head=self.get('/commits/main')['sha'];pending=[];runs={}
  for w in watch:
   folder,_,workflow=TRACKS[w['track']]
   raw=self.read_at(head,folder+'/scores/'+w['event_id']+'.json')
   if not raw:pending.append(w['event_id']);continue
   if workflow not in runs:
    runs[workflow]=self.get('/actions/workflows/'+workflow.rsplit('/',1)[-1]+'/runs?per_page=100')['workflow_runs']
   if not target_witnessed(w,raw,runs[workflow],self.read_at):pending.append(w['event_id'])
  return {'head_sha':head,'pending':pending}

def save_result(path,result):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 temporary=path.with_name(path.name+'.tmp')
 temporary.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
 temporary.replace(path)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);args=parser.parse_args()
 save_result(args.out,{'status':'STARTED','checked_at_utc':datetime.now(timezone.utc).isoformat(),
                       'dispatch_performed':False,'eligibility_authority':False})
 try:
  now=datetime.now(timezone.utc);watch=watch_targets(Path(__file__).resolve().parent,now,WATCH_LOOKAHEAD_SECONDS)
  budget=wait_budget(watch,datetime.now(timezone.utc))
  reader=Reader(budget)
  out=wait_for_witness(watch,reader.probe,budget=budget,progress=lambda result:save_result(args.out,{
    **result,'checked_at_utc':datetime.now(timezone.utc).isoformat()}))
 except Exception as exc:out={'status':'ERROR','error_type':type(exc).__name__,'dispatch_performed':False,'eligibility_authority':False}
 out['checked_at_utc']=datetime.now(timezone.utc).isoformat()
 save_result(args.out,out)
 output=os.environ.get('GITHUB_OUTPUT')
 if output:
  with open(output,'a',encoding='utf-8') as stream:
   stream.write('status='+out['status']+'\n')
   if 'head_sha' in out:stream.write('head_sha='+out['head_sha']+'\n')
 print(json.dumps(out))
 return 0 if out['status'] in ('READY','NO_PENDING_TARGET') else 1

if __name__=='__main__':raise SystemExit(main())

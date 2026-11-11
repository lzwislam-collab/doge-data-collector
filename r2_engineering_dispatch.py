"""Versioned engineering dispatch: bind actual Actions head before claiming handoff.

No request is a witness. A known stale run may be cancelled and retried once;
an unknown POST or unobserved result consumes the context without a retry.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

REPO='lzwislam-collab/doge-data-collector'
AUDIT='r2_engineering_audit.yml'
LIVE='r2_engineering_live.yml'
HEX=re.compile(r'[0-9a-f]{40}')
BASE='data/phase2_forward_v2'
ENGINEERING_MANIFEST='config/r2_engineering_transport_v1.json'
STATES=('pending','queued','requested','waiting','in_progress')
MAX_RESPONSE_BYTES=24*1024*1024  # same bound as the existing runtime GitHub client
AUDIT_SOURCE='data/hourly_research/ohlc.csv'
# Published readonly workflow: actual-SHA checkout and complete transport guard.
AUDIT_WORKFLOW_SHA256='0ffab7dfdb901226e8c71d6f0cb2a694b15cc81025d3d808b2e6d23338262b67'

def utc(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset()!=timedelta(0):
        raise ValueError('actual UTC clock required')
    return value

class Client:
    """One total40s transport budget, including all reconciliation and cancellation."""
    def __init__(self):self.end=time.monotonic()+40
    def request(self,path,payload=None,accepted=200):
        remaining=self.end-time.monotonic()
        if remaining<=0:raise TimeoutError('engineering dispatch transport budget exhausted')
        # A socket inactivity timeout does not bound DNS, headers or a slowly
        # streaming body. Own a single HTTP-only child and kill/reap it at the
        # remaining total deadline; this child creates no subprocesses/files.
        code='''import json,os,sys,time
from urllib.request import Request,urlopen
from urllib.error import HTTPError
path,payload,wanted,limit,maximum=sys.argv[1:];payload=json.loads(payload);wanted=int(wanted);maximum=int(maximum);end=time.monotonic()+float(limit)
req=Request('https://api.github.com/repos/lzwislam-collab/doge-data-collector'+path,data=None if payload is None else json.dumps(payload).encode(),method='GET' if payload is None else 'POST',headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'DOGE-R2-Engineering-Transport/1'})
try:response=urlopen(req,timeout=min(6,float(limit)))
except HTTPError as exc:
 print(json.dumps({'http_error':exc.code,'url':req.full_url}));sys.exit(0)
with response:
 if response.status!=wanted:raise ValueError('engineering provider status mismatch')
 raw=bytearray()
 if wanted==200:
  while True:
   if time.monotonic()>=end:raise TimeoutError('engineering body deadline')
   block=response.read1(65536)
   if time.monotonic()>=end:raise TimeoutError('engineering body deadline')
   if not block:break
   raw.extend(block)
   if len(raw)>maximum:raise ValueError('engineering response exceeds bound')
print(json.dumps({'value':json.loads(raw) if wanted==200 else wanted},separators=(',',':')))
'''
        try:
            result=subprocess.run([sys.executable,'-I','-B','-c',code,path,json.dumps(payload),str(accepted),str(remaining),str(MAX_RESPONSE_BYTES)],
                capture_output=True,timeout=remaining,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError('engineering HTTP child killed and reaped at total deadline') from exc
        if time.monotonic()>=self.end:raise TimeoutError('engineering response arrived beyond budget')
        if result.returncode!=0:raise RuntimeError('engineering HTTP read/write failed; outcome unverified')
        if len(result.stdout)>MAX_RESPONSE_BYTES+64:raise ValueError('engineering response exceeds bound')
        value=json.loads(result.stdout)
        if 'http_error' in value:raise HTTPError(value['url'],value['http_error'],'GitHub HTTP error',{},None)
        return value['value']
    def api(self,path):return self.request(path)
    def post(self,path,payload):return self.request(path,payload,204)
    def cancel(self,rid):return self.request('/actions/runs/'+str(rid)+'/cancel',{},202)

def _expected(blobs):
    if not isinstance(blobs,dict) or not 1<=len(blobs)<=16:raise ValueError('bounded intended files required')
    for path,sha in blobs.items():
        if (not isinstance(path,str) or not (path in ('data/hourly_research/ohlc.csv',ENGINEERING_MANIFEST) or
            re.fullmatch(re.escape(BASE)+r'/(events|scores)/[0-9]+_formal\.json',path)) or
            not isinstance(sha,str) or not HEX.fullmatch(sha)):
            raise ValueError('invalid intended immutable identity')

def _main(client):
    obj=client.api('/git/ref/heads/main')['object']
    if obj.get('type')!='commit' or not HEX.fullmatch(str(obj.get('sha',''))):raise ValueError('invalid main identity')
    return obj['sha']

def _local_absence(head,path,end=None):
    if not isinstance(head,str) or not HEX.fullmatch(head) or not (path==ENGINEERING_MANIFEST or re.fullmatch(re.escape(BASE)+r'/(events|scores)/[0-9]+_(formal|canary)\.json',str(path))):
        return False
    end=time.monotonic()+10 if end is None else end
    env=dict(os.environ,GIT_NO_LAZY_FETCH='1')
    def read(*args):
        remaining=end-time.monotonic()
        if remaining<=0:raise TimeoutError('engineering presence-proof deadline')
        return subprocess.run(['git','--no-replace-objects','-c','safe.directory=*',*args],
            cwd=Path(__file__).resolve().parent,env=env,capture_output=True,timeout=min(5,remaining),
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        if read('cat-file','-e',head+'^{commit}').returncode!=0:return False
        value=read('ls-tree','--full-tree','-z',head,'--',path)
        return value.returncode==0 and value.stdout==b''
    except (OSError,subprocess.SubprocessError):return False

def _matches(client,head,blobs,absence_proof):
    if not HEX.fullmatch(str(head)):raise ValueError('invalid Actions head')
    for path,sha in sorted(blobs.items()):
        try:value=client.api('/contents/'+path+'?'+urlencode({'ref':head}))
        except HTTPError as exc:
            if exc.code==404 and absence_proof(head,path):return False
            raise
        if (value.get('type')!='file' or value.get('path')!=path or
            not HEX.fullmatch(str(value.get('sha',''))) or type(value.get('size')) is not int or value['size']<0):
            raise ValueError('invalid intended object metadata')
        if value['sha']!=sha:return False
    return True

def queue_snapshot(api,path,validate_row):
    """One reread only for a legal census whose count and entries disagree.

    The caller's same transport client retains its original total deadline.
    Provider errors, bad schema and bad identities never authorize a reread.
    This does not make separate queue observations atomic or retry any POST.
    """
    for attempt in range(2):
        value=api(path)
        if not isinstance(value,dict):raise ValueError('invalid workflow queue schema')
        rows,count=value.get('workflow_runs'),value.get('total_count')
        if not isinstance(rows,list) or type(count) is not int or count<0 or len(rows)>1:
            raise ValueError('invalid workflow queue schema')
        if rows:
            if not isinstance(rows[0],dict):raise ValueError('invalid workflow queue row')
            validate_row(rows[0])
        if bool(rows)==bool(count):return rows,count
        print(json.dumps({'event':'workflow_queue_inconsistent','path':path,
              'count':count,'row_count':len(rows),'read_attempt':attempt+1},sort_keys=True),file=sys.stderr)
        if attempt:raise ValueError('workflow queue inconsistent: count='+str(count)+' rows='+str(len(rows))+' path='+path)


def _active(client,workflow,ignore=()):
    def validate(row):
        if (type(row.get('id')) is not int or row['id']<=0 or row.get('head_branch')!='main' or
                row.get('path','').split('@')[0]!='.github/workflows/'+workflow or
                row.get('status') not in STATES+('completed',)):raise ValueError('invalid queue identity')
    for status in STATES:
        path='/actions/workflows/'+workflow+'/runs?'+urlencode({'branch':'main','status':status,'per_page':1})
        rows,count=queue_snapshot(client.api,path,validate)
        if rows:
            row=rows[0]
            if row['id'] not in ignore or count!=1:return row
    return None

def _audit_queue_row(row):
    if (not isinstance(row,dict) or type(row.get('id')) is not int or row['id']<=0 or
        row.get('head_branch')!='main' or not isinstance(row.get('path'),str) or
        row['path'].split('@')[0]!='.github/workflows/'+AUDIT or
        row.get('status') not in STATES+('completed',)):raise ValueError('invalid audit queue identity')

def _queued_audits(client):
    path='/actions/workflows/'+AUDIT+'/runs?'+urlencode({'branch':'main','status':'queued','per_page':100})
    for attempt in range(2):
        value=client.api(path)
        if not isinstance(value,dict):raise ValueError('invalid queued audit census')
        rows,count=value.get('workflow_runs'),value.get('total_count')
        if (not isinstance(rows,list) or len(rows)>100 or type(count) is not int or
            not 0<=count<=100):raise ValueError('incomplete queued audit census')
        seen=set()
        for row in rows:
            _audit_queue_row(row)
            if row['id'] in seen:raise ValueError('duplicate queued audit identity')
            seen.add(row['id'])
        if bool(rows)==bool(count):
            if count!=len(rows):raise ValueError('incomplete queued audit census')
            return rows
        print(json.dumps({'event':'audit_queue_inconsistent','count':count,'row_count':len(rows),
                          'read_attempt':attempt+1},sort_keys=True),file=sys.stderr)
        if attempt:raise ValueError('queued audit census inconsistent')

def _queued_audit_identity(row):
    if (not isinstance(row,dict) or type(row.get('id')) is not int or row['id']<=0 or
        type(row.get('run_attempt')) is not int or row['run_attempt']<=0 or
        not isinstance(row.get('head_sha'),str) or not HEX.fullmatch(row['head_sha']) or
        row.get('path')!='.github/workflows/'+AUDIT or row.get('head_branch')!='main' or
        row.get('status')!='queued' or row.get('event')!='push' or row.get('conclusion') is not None or
        type(row.get('pull_requests')) is not list or row['pull_requests'] or
        not isinstance(row.get('repository'),dict) or row['repository'].get('full_name')!=REPO or
        not isinstance(row.get('head_repository'),dict) or row['head_repository'].get('full_name')!=REPO or
        not isinstance(row.get('created_at'),str) or not isinstance(row.get('updated_at'),str)):
        return None
    try:
        if (len(row['created_at'])>64 or len(row['updated_at'])>64 or
            utc(datetime.fromisoformat(row['created_at'].replace('Z','+00:00')))>
            utc(datetime.fromisoformat(row['updated_at'].replace('Z','+00:00')))):return None
    except (ValueError,TypeError,OverflowError):return None
    return tuple(row[k] for k in ('id','run_attempt','head_sha','path','head_branch','status',
                                  'event','conclusion','created_at','updated_at'))

def _irrelevant_queued_audit(client,row,source):
    identity=_queued_audit_identity(row)
    if identity is None:return False
    run_path='/actions/runs/'+str(row['id'])
    if _queued_audit_identity(client.api(run_path))!=identity:
        raise ValueError('queued audit identity changed')
    head=row['head_sha']
    def metadata(path,maximum):
        value=client.api('/contents/'+path+'?'+urlencode({'ref':head}))
        if (not isinstance(value,dict) or value.get('type')!='file' or value.get('path')!=path or
            not isinstance(value.get('sha'),str) or not HEX.fullmatch(value['sha']) or
            type(value.get('size')) is not int or not 0<=value['size']<=maximum):
            raise ValueError('invalid queued audit immutable file metadata')
        return value
    old_source=metadata(AUDIT_SOURCE,32000000)
    if old_source['sha']==source:return False
    workflow=metadata('.github/workflows/'+AUDIT,16384)
    if (workflow.get('encoding')!='base64' or not isinstance(workflow.get('content'),str) or
        len(workflow['content'])>32768):raise ValueError('invalid queued audit workflow content')
    raw=base64.b64decode(workflow['content'].replace('\n',''),validate=True)
    if (len(raw)!=workflow['size'] or
        hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=workflow['sha']):
        raise ValueError('queued audit workflow blob mismatch')
    if hashlib.sha256(raw).hexdigest()!=AUDIT_WORKFLOW_SHA256:return False
    jobs=client.api(run_path+'/jobs?filter=all&per_page=1')
    if not isinstance(jobs,dict):raise ValueError('invalid queued audit jobs')
    rows,count=jobs.get('jobs'),jobs.get('total_count')
    if (not isinstance(rows,list) or len(rows)>1 or type(count) is not int or count<0 or
        bool(rows)!=bool(count)):raise ValueError('incomplete queued audit jobs')
    if count:return False
    return True

def _audit_active(client,expected,ignore=()):
    queued=[];seen=set();proven=[]
    for status in STATES:
        if status=='queued':rows=_queued_audits(client);count=len(rows);queued=rows
        else:
            path='/actions/workflows/'+AUDIT+'/runs?'+urlencode({'branch':'main','status':status,'per_page':1})
            rows,count=queue_snapshot(client.api,path,_audit_queue_row)
        for row in rows:
            if row['id'] in seen:raise ValueError('duplicate active audit identity')
            seen.add(row['id'])
            if status!='queued' and (row['id'] not in ignore or count!=1):return row
    for row in queued:
        if row['id'] not in ignore:
            if not _irrelevant_queued_audit(client,row,expected[AUDIT_SOURCE]):return row
            proven.append((row['id'],_queued_audit_identity(row)))
    # Recheck all proven rows together: an earlier row may change while a later
    # row's immutable source/workflow/jobs proof is being read. Same40s client.
    for rid,identity in proven:
        if _queued_audit_identity(client.api('/actions/runs/'+str(rid)))!=identity:
            raise ValueError('queued audit identity changed during source proof')
    for rid,identity in proven:
        print(json.dumps({'event':'audit_queue_irrelevant_source','run_id':rid,
                          'witness_verified':False},sort_keys=True),file=sys.stderr)
    return None

def _observed(client,workflow,token,post_at,sleep):
    title='R2E1 '+('audit' if workflow==AUDIT else 'publish')+' '+token
    for index in range(3):
        rows=client.api('/actions/workflows/'+workflow+'/runs?'+urlencode({'branch':'main','event':'workflow_dispatch','per_page':30}))['workflow_runs']
        if not isinstance(rows,list) or len(rows)>30:raise ValueError('invalid dispatched run listing')
        matches=[row for row in rows if row.get('display_title')==title]
        if len(matches)>1:raise ValueError('ambiguous engineering dispatch identity')
        if matches:
            row=matches[0]
            created=datetime.fromisoformat(row['created_at'].replace('Z','+00:00'))
            if (type(row.get('id')) is not int or row['id']<=0 or row.get('event')!='workflow_dispatch' or
                row.get('head_branch')!='main' or row.get('path','').split('@')[0]!='.github/workflows/'+workflow or
                row.get('repository',{}).get('full_name')!=REPO or created<post_at.replace(microsecond=0)):
                raise ValueError('dispatched run identity mismatch')
            return row
        if index<2:sleep(.5)
    return None

def dispatch_bound(workflow,expected_blobs,not_after,*,client=None,clock=None,sleep=time.sleep,
                   token_factory=None,state=None,absence_proof=None,persist_intent=None,mode=None):
    if workflow not in (AUDIT,LIVE):raise ValueError('unapproved engineering workflow')
    if workflow==LIVE and mode not in ('publish','canary') or workflow==AUDIT and mode is not None:
        raise ValueError('original publisher mode required only for publisher')
    _expected(expected_blobs);not_after=utc(not_after)
    client=client or Client();clock=clock or (lambda:datetime.now(timezone.utc))
    token_factory=token_factory or (lambda:uuid4().hex);state={} if state is None else state
    if absence_proof is None:
        absence_proof=lambda head,path:_local_absence(head,path,getattr(client,'end',None))
    key=hashlib.sha256(json.dumps([workflow,mode,expected_blobs,not_after.isoformat()],sort_keys=True).encode()).hexdigest()
    if key in state:return {'status':'PREVIOUS_INTENT_NOT_RETRIED','witness_verified':False}
    stale=[]
    for attempt in range(2):
        if utc(clock())>=not_after:return {'status':'DEFERRED_WINDOW_CLOSED','stale_run_ids':stale,'witness_verified':False}
        head=_main(client)
        if not _matches(client,head,expected_blobs,absence_proof):return {'status':'DEFERRED_PUBLIC_BYTES_CHANGED','witness_verified':False}
        active=None
        for queued_workflow in (workflow,'phase2_forward_live_v2.yml') if workflow==LIVE else (workflow,):
            active=(_audit_active(client,expected_blobs,stale) if workflow==AUDIT and AUDIT_SOURCE in expected_blobs
                    else _active(client,queued_workflow,stale if queued_workflow==workflow else ()))
            if active is not None:break
        if active is not None:return {'status':'DEFERRED_ACTIVE_WORKFLOW','active_run_id':active['id'],'witness_verified':False}
        token=token_factory()
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',token):raise ValueError('invalid dispatch request identity')
        post_at=utc(clock())
        if post_at>=not_after:return {'status':'DEFERRED_WINDOW_CLOSED','stale_run_ids':stale,'witness_verified':False}
        state[key]={'attempt':attempt+1,'request_id':token,'intended_head':head,'at_utc':post_at.isoformat()}
        if persist_intent is not None:persist_intent(state)
        if utc(clock())>=not_after:return {'status':'DEFERRED_WINDOW_CLOSED','stale_run_ids':stale,'witness_verified':False}
        payload={'ref':'main','inputs':{'request_id':token,'expected_head':head,'not_after_utc':not_after.isoformat()}}
        if workflow==LIVE:payload['inputs']['mode']=mode
        if client.post('/actions/workflows/'+workflow+'/dispatches',payload)!=204:raise ValueError('engineering dispatch not acknowledged')
        run=_observed(client,workflow,token,post_at,sleep)
        if run is None:return {'status':'DISPATCH_RESULT_UNOBSERVED','request_id':token,'witness_verified':False}
        if _matches(client,run['head_sha'],expected_blobs,absence_proof):
            return {'status':'DISPATCH_BOUND_COMPLETION_UNVERIFIED','run_id':run['id'],'head_sha':run['head_sha'],
                'request_id':token,'stale_run_ids':stale,'witness_verified':False}
        # Cancel only the positively identified request whose immutable bytes
        # are demonstrably stale; never cancel an ambiguous or unrelated run.
        if client.cancel(run['id'])!=202:raise ValueError('known stale dispatch cancellation not accepted')
        stale.append(run['id'])
    return {'status':'STALE_DISPATCH_NOT_ACCEPTED','stale_run_ids':stale,'witness_verified':False}

def _git(root,*args):
    return subprocess.check_output(['git','--no-replace-objects','-c','safe.directory=*',*args],cwd=root,timeout=15)

def committed_manifest_blob(root):
    """Pin this executing version, never a potentially stale remote viewhead."""
    root=Path(root);head=_git(root,'rev-parse','HEAD').decode().strip()
    if not HEX.fullmatch(head):raise ValueError('invalid execution commit')
    raw=_git(root,'show',head+':'+ENGINEERING_MANIFEST)
    local=(root/ENGINEERING_MANIFEST).read_bytes().replace(b'\r\n',b'\n')
    if raw!=local:raise ValueError('engineering manifest is not this committed execution version')
    return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()

def current_intent(root,now):
    """Only currently open original source/prediction/target obligations."""
    import phase2_forward_runtime_v2 as r
    p=r.f.policy();root=Path(root);report=json.loads((root/'phase2-forward-output/runtime.json').read_bytes())
    head=_git(root,'rev-parse','HEAD').decode().strip();paths={};deadlines=[]
    pending=report.get('engineering_pending_witness',[])
    if not isinstance(pending,list) or len(pending)>16:raise ValueError('invalid pending engineering witness list')
    extra={'events':[],'scores':[]}
    for item in pending:
        if not isinstance(item,dict) or set(item)!={'event_id','kind'} or item['kind'] not in ('prediction','target'):
            raise ValueError('invalid pending engineering witness kind')
        extra['events' if item['kind']=='prediction' else 'scores'].append(item['event_id'])
    for field,folder in (('new_events','events'),('new_scores','scores')):
        ids=report.get(field,[])
        if not isinstance(ids,list) or len(ids)>8:raise ValueError('invalid new engineering record list')
        ids=sorted(set(ids+extra[folder]))
        for key in ids:
            if not isinstance(key,str) or not re.fullmatch(r'[0-9]+_formal',key):raise ValueError('invalid new formal identity')
            event=json.loads((root/BASE/'events'/(key+'.json')).read_bytes())
            end=r.f.utc(event['target_utc']) if folder=='events' else r.f.deadline(event,p)
            if now>=end:continue
            path=BASE+'/'+folder+'/'+key+'.json'
            raw=(root/path).read_bytes()
            if _git(root,'show',head+':'+path)!=raw:raise ValueError('record not bound to committed bytes')
            paths[path]=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest();deadlines.append(end)
    if not deadlines and report.get('status')=='WAIT_EXACT_INPUT_WITNESS':
        start=r.f.utc(p['effective_from_utc']);slot=start+timedelta(seconds=int((now-start).total_seconds()//p['slot_seconds'])*p['slot_seconds'])
        end=slot+timedelta(seconds=p['issue_window_seconds'])
        if start<=now<min(end,r.f.utc(p['observation_end_utc'])):deadlines.append(end)
    if not deadlines:return None
    raw=_git(root,'show',head+':'+r.SOURCE)
    paths[r.SOURCE]=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    paths[ENGINEERING_MANIFEST]=committed_manifest_blob(root)
    return paths,min(deadlines)

def verify_remote_intent(intent,artifact_id,*,client,clock=None):
    clock=clock or (lambda:datetime.now(timezone.utc));now=utc(clock())
    if (not isinstance(intent,dict) or intent.get('schema_version')!=1 or
        type(intent.get('parent_run_id')) is not int or intent['parent_run_id']<=0 or
        type(intent.get('parent_attempt')) is not int or intent['parent_attempt']<=0 or
        intent.get('artifact_name')!='r2-engineering-audit-intent-'+str(intent['parent_run_id']) or
        type(artifact_id) is not int or artifact_id<=0):raise ValueError('invalid prepared engineering intent')
    _expected(intent.get('expected_blobs'))
    prepared=utc(datetime.fromisoformat(intent['prepared_at_utc']))
    deadline=utc(datetime.fromisoformat(intent['not_after_utc']))
    if not prepared<=now<deadline:raise ValueError('prepared engineering intent is not current')
    row=client.api('/actions/artifacts/'+str(artifact_id))
    created=utc(datetime.fromisoformat(row['created_at'].replace('Z','+00:00')))
    if (row.get('id')!=artifact_id or row.get('name')!=intent['artifact_name'] or row.get('expired') is not False or
        type(row.get('size_in_bytes')) is not int or row['size_in_bytes']<=0 or
        row.get('workflow_run',{}).get('id')!=intent['parent_run_id'] or
        row.get('workflow_run',{}).get('head_branch')!='main' or
        not prepared.replace(microsecond=0)<=created<=utc(clock())):
        raise ValueError('remote engineering intent ownership/identity mismatch')
    return row

def _producer(client,run_id,attempt):
    row=client.api('/actions/runs/'+str(run_id))
    if (row.get('id')!=run_id or row.get('run_attempt')!=attempt or
        row.get('repository',{}).get('full_name')!=REPO or row.get('head_repository',{}).get('full_name')!=REPO or
        row.get('head_branch')!='main' or row.get('path','').split('@')[0]!='.github/workflows/'+LIVE or
        row.get('event') not in ('workflow_dispatch','workflow_run') or row.get('pull_requests') or
        row.get('status')!='in_progress'):
        raise ValueError('engineering intent requires its exact active publisher attempt')
    return row

def _prior_marker(client,run_id,name):
    value=client.api('/actions/runs/'+str(run_id)+'/artifacts?per_page=100')
    rows,count=value.get('artifacts'),value.get('total_count')
    if not isinstance(rows,list) or type(count) is not int or count!=len(rows) or count>100:
        raise ValueError('engineering intent history incomplete')
    return any(row.get('name')==name for row in rows)

def _write(path,value):
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    with temporary.open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(value,sort_keys=True,indent=2)+'\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('kind',choices=['prepare','audit']);args=parser.parse_args()
    if (os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('GITHUB_REF')!='refs/heads/main' or
        os.environ.get('GITHUB_REPOSITORY')!=REPO):raise ValueError('engineering dispatch requires trusted main workflow')
    import r2_engineering_runtime as engineering
    engineering.verify_transport()
    root=Path(__file__).resolve().parent;now=datetime.now(timezone.utc);intent=current_intent(root,now)
    out=root/'phase2-forward-output';out.mkdir(exist_ok=True)
    run_id,attempt=int(os.environ['GITHUB_RUN_ID']),int(os.environ['GITHUB_RUN_ATTEMPT'])
    if run_id<=0 or attempt<=0:raise ValueError('invalid active publisher identity')
    client=Client();_producer(client,run_id,attempt)
    intent_path=out/'engineering-audit-intent.json';name='r2-engineering-audit-intent-'+str(run_id)
    if args.kind=='prepare':
        prepared=False
        if _prior_marker(client,run_id,name):result={'status':'SKIP_PREVIOUS_REMOTE_INTENT','witness_verified':False}
        elif intent is None:result={'status':'SKIP_NO_CURRENT_AUDIT_OBLIGATION','witness_verified':False}
        else:
            paths,deadline=intent
            _write(intent_path,{'schema_version':1,'parent_run_id':run_id,'parent_attempt':attempt,
                'artifact_name':name,'prepared_at_utc':now.isoformat(),'expected_blobs':paths,'not_after_utc':deadline.isoformat()})
            prepared=True;result={'status':'REMOTE_INTENT_UPLOAD_REQUIRED','witness_verified':False}
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as stream:stream.write('prepared='+str(prepared).lower()+'\n')
        print(json.dumps(result,sort_keys=True));return
    prepared=json.loads(intent_path.read_bytes())
    if prepared.get('parent_run_id')!=run_id or prepared.get('parent_attempt')!=attempt:raise ValueError('prepared intent belongs to another attempt')
    verify_remote_intent(prepared,int(os.environ['ENGINEERING_INTENT_ARTIFACT_ID']),client=client)
    expected=(prepared['expected_blobs'],datetime.fromisoformat(prepared['not_after_utc']))
    if intent!=expected:raise ValueError('current original obligation differs from prepared remote intent')
    state_path=out/'engineering-dispatch-intents.json';state=json.loads(state_path.read_bytes()) if state_path.exists() else {}
    def persist(value):_write(state_path,value)
    try:result=dispatch_bound(AUDIT,*expected,state=state,client=client,persist_intent=persist)
    finally:_write(state_path,state)
    (out/'engineering-dispatch.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,sort_keys=True))
    if result['status'] in ('DISPATCH_RESULT_UNOBSERVED','STALE_DISPATCH_NOT_ACCEPTED'):raise RuntimeError('engineering witness dispatch not bound')

if __name__=='__main__':main()

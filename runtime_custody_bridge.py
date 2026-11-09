"""Bounded custody handoff by the existing sole writer; no forecast or OSS writes.

Only immutable operational dispatch intents are created under the existing
runtime receipt namespace. The caller must publish these with the existing
publisher; their exact remote bytes are checked before any POST. An intent
consumes the automatic attempt even if the POST result is unknown.
"""
import base64,hashlib,json,os,re,time
from collections import OrderedDict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen
import forecast_worm as custody

REPO='lzwislam-collab/doge-data-collector'
WORKFLOW='oss_inventory_retention.yml'
INTENTS='data/runtime_deadline_v1/custody_dispatch_attempts'
INPUTS={'engineering_probe':False,'extend_retention_days':'0','force_readback':False}
STATES=('pending','queued','requested','waiting','in_progress')
HEX=re.compile(r'[a-f0-9]{40}')
MAX_RAW=16*1024*1024
POLICIES=('config/learned_shadow_v1.json','config/phase2_forward_v2.json')
MIN_REMAINING_SECONDS=480+120 # original8min job plus startup reserve, not a target extension
_TREE_CACHE=OrderedDict()
_POLICY_CACHE=OrderedDict()
class ObservationUnavailable(RuntimeError):
 """A remote observation failed before any intent writes. Cause is retained."""
 pass
def encode(value):return json.dumps(value,sort_keys=True,indent=2).encode()+b'\n'
def digest(raw):return hashlib.sha256(raw).hexdigest()
def git_blob(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def parse(raw):
 def unique(items):
  value={}
  for key,item in items:
   if key in value:raise ValueError('duplicate JSON key')
   value[key]=item
  return value
 return json.loads(raw,object_pairs_hook=unique)
class _Client:
 """Forty seconds of total transport, excluding caller-owned publication."""
 def __init__(self):self.spent=0.0
 def request(self,path,payload=None):
  remaining=40-self.spent
  if remaining<=0:raise TimeoutError('custody bridge transport budget exhausted')
  request=Request('https://api.github.com/repos/'+REPO+path,
   data=None if payload is None else json.dumps(payload).encode(),
   headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'DOGE-Custody-Bridge'},
   method='GET' if payload is None else 'POST')
  started=time.monotonic();accepted=False
  def budget():
   if time.monotonic()-started>=remaining:raise TimeoutError('custody transport response budget exhausted')
  try:
   with urlopen(request,timeout=min(6,remaining)) as response:
    budget()
    if payload is not None:
     if response.status!=204:raise ValueError('custody dispatch not accepted')
     value=response.status
    else:
     if response.status!=200:raise ValueError('GitHub source read failed')
     raw=bytearray()
     while True:
      budget();chunk=response.read1(65536);budget()
      if not chunk:break
      raw.extend(chunk)
      if len(raw)>24*1024*1024:raise ValueError('GitHub source response exceeds cap')
     budget();value=parse(bytes(raw));budget()
   budget();accepted=True
   return value
  finally:
   self.spent+=time.monotonic()-started
   if accepted and self.spent>=40:raise TimeoutError('custody transport acceptance after budget')
 def api(self,path):return self.request(path)
 def post(self,payload,timeout):return self.request('/actions/workflows/'+WORKFLOW+'/dispatches',payload)

def _head(api):
 obj=api('/git/ref/heads/main')['object']
 if obj.get('type')!='commit' or not isinstance(obj.get('sha'),str) or not HEX.fullmatch(obj['sha']):raise ValueError('invalid main identity')
 return obj['sha']
def _read(api,path,head,missing=False):
 try:result=api('/contents/'+path+'?'+urlencode({'ref':head}))
 except HTTPError as exc:
  if missing and exc.code==404:return None
  raise
 if (not isinstance(result,dict) or result.get('type')!='file' or result.get('path')!=path
  or result.get('encoding')!='base64' or not isinstance(result.get('content'),str)):raise ValueError('invalid pinned file response')
 raw=base64.b64decode(result['content'].replace('\n',''),validate=True)
 if type(result.get('size')) is not int or result['size']!=len(raw) or len(raw)>MAX_RAW or result.get('sha')!=git_blob(raw):raise ValueError('pinned file bytes mismatch')
 return raw
def _event_trees(api,head):
 commit=api('/git/commits/'+head)
 if commit.get('sha')!=head or not HEX.fullmatch(str(commit.get('tree',{}).get('sha',''))):raise ValueError('invalid source commit tree')
 cache={}
 def tree(oid):
  if oid in _TREE_CACHE:
   _TREE_CACHE.move_to_end(oid);return _TREE_CACHE[oid]
  if oid not in cache:
   result=api('/git/trees/'+oid)
   if (result.get('sha')!=oid or result.get('truncated') is not False or not isinstance(result.get('tree'),list)):raise ValueError('incomplete event tree')
   entries={}
   for entry in result['tree']:
    name=entry.get('path') if isinstance(entry,dict) else None
    if not isinstance(name,str) or not name or '/' in name or name in ('.','..') or name in entries or not HEX.fullmatch(str(entry.get('sha',''))):raise ValueError('invalid tree entry')
    entries[name]=entry
   body=bytearray()
   for name,entry in sorted(entries.items(),key=lambda pair:pair[0].encode()+ (b'/' if pair[1].get('type')=='tree' else b'')):
    mode=entry.get('mode');kind=entry.get('type')
    if (kind,mode) not in (('tree','040000'),('blob','100644'),('blob','100755'),('blob','120000'),('commit','160000')):raise ValueError('invalid Git tree mode')
    body.extend(mode.lstrip('0').encode()+b' '+name.encode()+b'\0'+bytes.fromhex(entry['sha']))
   if hashlib.sha1(b'tree '+str(len(body)).encode()+b'\0'+body).hexdigest()!=oid:raise ValueError('Git tree content hash mismatch')
   cache[oid]=entries;_TREE_CACHE[oid]=entries
   while len(_TREE_CACHE)>128:_TREE_CACHE.popitem(last=False)
  return cache[oid]
 def walk(path):
  oid=commit['tree']['sha']
  for component in path.split('/'):
   entry=tree(oid).get(component)
   if entry is None or entry.get('type')!='tree' or entry.get('mode')!='040000':raise ValueError('event namespace missing')
   oid=entry['sha']
  return tree(oid)
 config=tree(commit['tree']['sha']).get('config')
 if config is None or config.get('type')!='tree' or config.get('mode')!='040000':raise ValueError('configuration tree missing')
 result={'original18':walk('data/learned_shadow_v1/events'),'r2':walk('data/phase2_forward_v2/events')}
 tree(config['sha']) # pin all policy/manifest blobs before Contents reads/cache
 return result,config['sha']

def _policies(api,head,config_sha):
 if config_sha in _POLICY_CACHE:
  _POLICY_CACHE.move_to_end(config_sha);return _POLICY_CACHE[config_sha]
 entries=_TREE_CACHE.get(config_sha)
 if entries is None:raise ValueError('verified configuration tree missing')
 def pinned(path):
  name=path.split('/')[-1];entry=entries.get(name)
  if entry is None or entry.get('type')!='blob' or entry.get('mode')!='100644':raise ValueError('policy tree identity missing')
  raw=_read(api,path,head)
  if git_blob(raw)!=entry['sha']:raise ValueError('policy config-tree/blob mismatch')
  return raw
 manifest=parse(pinned('config/phase2_forward_registration_v2.json'))
 raw=[pinned(path) for path in POLICIES]
 for path,value in zip(POLICIES,raw):
  if manifest.get('sha256_lf',{}).get(path)!=digest(value):raise ValueError('frozen custody policy mismatch')
 original,r2=map(parse,raw)
 if (original.get('protocol_id')!='DOGE_LEARNED_SHADOW_V1_20261016' or original.get('horizons_hours')!=[1,24]
  or original.get('max_issue_delay_minutes')!=45 or original.get('slot_steps_hours')!={'1':2,'24':25}
  or r2.get('protocol_id')!='DOGE_V2_FROZEN120_20261020_R2' or r2.get('horizon_seconds')!=3600
  or r2.get('issue_window_seconds')!=2700 or r2.get('slot_seconds')!=7200):raise ValueError('frozen event timing contract changed')
 _POLICY_CACHE[config_sha]=(original,r2)
 while len(_POLICY_CACHE)>16:_POLICY_CACHE.popitem(last=False)
 return original,r2
def _active(api):
 for state in STATES:
  result=api('/actions/workflows/'+WORKFLOW+'/runs?'+urlencode({'branch':'main','status':state,'per_page':1}))
  rows=result['workflow_runs'];count=result['total_count']
  if not isinstance(rows,list) or type(count) is not int or count<0 or len(rows)>1 or bool(count)!=bool(rows):raise ValueError('custody active response incomplete')
  if rows:
   row=rows[0]
   if (not isinstance(row,dict) or type(row.get('id')) is not int or row['id']<=0 or row.get('head_branch')!='main'
    or not isinstance(row.get('path'),str) or row['path'].split('@')[0]!='.github/workflows/'+WORKFLOW
    or row.get('status') not in STATES+('completed',)):raise ValueError('custody active identity invalid')
   return row['id']
 return None
def _event(raw,track,eid,now):
 event=parse(raw);group=custody.event_group(event,track)
 slot=datetime.fromtimestamp(int(eid.split('_')[0]),timezone.utc)
 issued=custody.utc(event['issued_at_utc']);target=custody.utc(event['target_utc'])
 if (event.get('event_id')!=eid or custody.utc(event['slot_utc'])!=slot or not slot<=issued<slot+timedelta(seconds=2700)
  or event.get('protocol_id')!=('DOGE_LEARNED_SHADOW_V1_20261016' if track=='original18' else 'DOGE_V2_FROZEN120_20261020_R2')):raise ValueError('event source binding invalid')
 if track=='r2' and group[1]!=3600:raise ValueError('R2 horizon changed')
 return event,issued<=now<target
def _receipt(api,head,raw,event,track):
 path='audit/forecast_worm/'+track+'/'+event['event_id']+'.json'
 receipt=_read(api,path,head,True)
 if receipt is None:return False
 custody.verify_recorded_receipt(raw,event,track,parse(receipt))
 return True
def _intent_binding(raw,item):
 value=parse(raw)
 expected={'schema':'DOGE_CUSTODY_DISPATCH_INTENT_V1','workflow':WORKFLOW,'inputs':INPUTS,
  'track':item['track'],'event_id':item['event']['event_id'],'event_sha256':digest(item['raw']),
  'issued_at_utc':item['event']['issued_at_utc'],'target_utc':item['event']['target_utc'],'status':'INTENT_ONLY_NOT_CUSTODY_ACCEPTANCE'}
 if any(value.get(k)!=v for k,v in expected.items()):raise ValueError('custody dispatch intent binding changed')
 if not HEX.fullmatch(str(value.get('source_commit',''))) or not re.fullmatch(r'[1-9][0-9]*',str(value.get('run_id',''))) or not re.fullmatch(r'[1-9][0-9]*',str(value.get('run_attempt',''))):raise ValueError('intent owner missing')
 return value
def _observe_prepare(root,*,api=None,clock=None):
 """Stage immutable intents for caller's next existing foreground publication."""
 root=Path(root);clock=clock or (lambda:datetime.now(timezone.utc))
 if (os.environ.get('GITHUB_REF')!='refs/heads/main' or os.environ.get('GITHUB_REPOSITORY')!=REPO
  or os.environ.get('GITHUB_EVENT_NAME') not in ('push','schedule','workflow_dispatch','workflow_run')):raise ValueError('trusted main runtime required')
 for name in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT'):
  if not re.fullmatch(r'[1-9][0-9]*',os.environ.get(name,'')):raise ValueError('runtime owner identity required')
 client=_Client();api=api or client.api
 head=_head(api);now=clock();trees,config_sha=_event_trees(api,head);_policies(api,head,config_sha)
 report={'status':'NO_ELIGIBLE_PRE_TARGET_EVENTS','source_commit':head,'custody_verified':False,'requested_events':[],'existing_attempts':[],'near_target_skipped':[]}
 candidates=[];eligible=0;local_uncertain=False
 for track,entries in trees.items():
  base='data/learned_shadow_v1/events' if track=='original18' else 'data/phase2_forward_v2/events'
  pattern=r'([0-9]+)_(1|24)\.json' if track=='original18' else r'([0-9]+)_formal\.json'
  for name,entry in sorted(entries.items()):
   match=re.fullmatch(pattern,name)
   if not match:continue
   if entry.get('type')!='blob' or entry.get('mode')!='100644':raise ValueError('unsafe event entry')
   slot=datetime.fromtimestamp(int(match[1]),timezone.utc);horizon=int(match[2])*3600 if track=='original18' else 3600
   # Frozen timing proves these cannot still be pre-target; no old payload GET.
   if slot>now or slot+timedelta(seconds=2700+horizon)<=now:continue
   raw=_read(api,base+'/'+name,head)
   if git_blob(raw)!=entry['sha']:raise ValueError('event tree/file mismatch')
   event,active=_event(raw,track,name[:-5],now)
   if not active:continue
   if (custody.utc(event['target_utc'])-now).total_seconds()<=MIN_REMAINING_SECONDS:
    report['near_target_skipped'].append(track+'/'+event['event_id']);continue
   eligible+=1
   if _receipt(api,head,raw,event,track):continue
   item={'track':track,'path':base+'/'+name,'raw':raw,'event':event}
   item['intent_path']=INTENTS+'/'+track+'/'+event['event_id']+'_'+digest(raw)+'.json'
   existing=_read(api,item['intent_path'],head,True)
   if existing is not None:
    _intent_binding(existing,item);report['existing_attempts'].append(track+'/'+event['event_id']);continue
   local=root/item['intent_path']
   if local.exists():
    if local.is_symlink() or not local.is_file():raise ValueError('unsafe local intent')
    with local.open('rb') as stream:local_raw=stream.read(65537)
    if len(local_raw)>65536:raise ValueError('local intent size exceeds bound')
    _intent_binding(local_raw,item);local_uncertain=True;continue
   candidates.append(item)
 if not candidates:
  report['status']='LOCAL_INTENT_UNCONFIRMED_NOT_RETRIED' if local_uncertain else ('ALREADY_ATTEMPTED_CUSTODY_NOT_VERIFIED' if report['existing_attempts'] else ('NO_MISSING_CURRENT_RECEIPTS' if eligible else report['status']))
  if report['status']=='NO_ELIGIBLE_PRE_TARGET_EVENTS' and report['near_target_skipped']:report['status']='SKIP_INSUFFICIENT_CUSTODY_BUDGET'
  return {'report':report,'candidates':[],'api':api,'client':client,'clock':clock}
 active=_active(api)
 if active is not None:
  report.update(status='DEFERRED_ACTIVE_CUSTODY',active_run_id=active)
  return {'report':report,'candidates':[],'api':api,'client':client,'clock':clock}
 return {'report':report,'candidates':candidates,'api':api,'client':client,'clock':clock}

def prepare(root,*,api=None,clock=None):
 """Stage only after all remote GETs succeed; classify pre-write outages."""
 root=Path(root)
 try:
  prepared=_observe_prepare(root,api=api,clock=clock)
 except (HTTPError,URLError,TimeoutError,ConnectionError) as error:
  raise ObservationUnavailable('custody source observation unavailable: '+type(error).__name__) from error
 candidates=prepared['candidates']
 if not candidates:return prepared
 clock=prepared['clock'];head=prepared['report']['source_commit']
 # Each new intent is immutable, source-bound, and consumes its one automatic try.
 for item in candidates:
  value={'schema':'DOGE_CUSTODY_DISPATCH_INTENT_V1','status':'INTENT_ONLY_NOT_CUSTODY_ACCEPTANCE',
   'workflow':WORKFLOW,'inputs':INPUTS,'source_commit':head,'track':item['track'],'event_id':item['event']['event_id'],
   'event_sha256':digest(item['raw']),'issued_at_utc':item['event']['issued_at_utc'],'target_utc':item['event']['target_utc'],
   'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'intent_at_utc':clock().isoformat()}
  item['intent_raw']=encode(value);dest=root/item['intent_path']
  for parent in dest.parents:
   if parent==root:break
   if parent.is_symlink():raise ValueError('unsafe intent directory')
  dest.parent.mkdir(parents=True,exist_ok=True)
  with dest.open('xb') as stream:stream.write(item['intent_raw'])
 return prepared

def after_publish(root,prepared,*,api=None,post=None,clock=None):
 """Check exact public intents after caller's normal publish, then one POST."""
 if os.environ.get('GITHUB_REF')!='refs/heads/main' or os.environ.get('GITHUB_REPOSITORY')!=REPO or os.environ.get('GITHUB_EVENT_NAME') not in ('push','schedule','workflow_dispatch','workflow_run'):raise ValueError('trusted main runtime required')
 report=dict(prepared['report']);candidates=prepared['candidates']
 if not candidates:return report
 api=api or prepared['api'];post=post or prepared['client'].post;clock=clock or prepared['clock']
 fresh=_head(api);report['intent_readback_sha']=fresh
 _,config_sha=_event_trees(api,fresh);_policies(api,fresh,config_sha)
 remaining=[]
 for item in candidates:
  saved=_read(api,item['intent_path'],fresh)
  if saved!=item['intent_raw']:raise ValueError('intent remote exact readback failed')
  _intent_binding(saved,item)
  raw=_read(api,item['path'],fresh)
  if raw!=item['raw']:raise ValueError('event mutated during dispatch preparation')
  event,active=_event(raw,item['track'],item['event']['event_id'],clock())
  if active and (custody.utc(event['target_utc'])-clock()).total_seconds()>MIN_REMAINING_SECONDS and not _receipt(api,fresh,raw,event,item['track']):remaining.append(item)
 if not remaining:return {**report,'status':'SKIP_TARGET_EXPIRED_AFTER_INTENT' if any(custody.utc(i['event']['target_utc'])<=clock() for i in candidates) else ('SKIP_INSUFFICIENT_CUSTODY_BUDGET_AFTER_INTENT' if any((custody.utc(i['event']['target_utc'])-clock()).total_seconds()<=MIN_REMAINING_SECONDS for i in candidates) else 'NO_MISSING_CURRENT_RECEIPTS_AFTER_INTENT')}
 active=_active(api)
 if active is not None:return {**report,'status':'DEFERRED_ACTIVE_CUSTODY_AFTER_INTENT','active_run_id':active}
 if any((custody.utc(i['event']['target_utc'])-clock()).total_seconds()<=MIN_REMAINING_SECONDS for i in remaining):return {**report,'status':'SKIP_INSUFFICIENT_CUSTODY_BUDGET_AFTER_INTENT'}
 payload={'ref':'main','inputs':dict(INPUTS)}
 if post(payload,6)!=204:raise ValueError('custody dispatch not accepted')
 report.update(status='DISPATCH_ACCEPTED_CUSTODY_NOT_VERIFIED',requested_events=[i['track']+'/'+i['event']['event_id'] for i in remaining])
 return report

def tick(root,publish_intent,*,api=None,post=None,clock=None):
 """Composition for tests/tools; live runtime should fuse the two phases."""
 prepared=prepare(root,api=api,clock=clock)
 if prepared['candidates']:
  if not callable(publish_intent):raise ValueError('existing publisher callback required')
  publish_intent()
 return after_publish(root,prepared,api=api,post=post,clock=clock)

# Stable public API for other existing foreground dispatch adapters.
GitHubClient=_Client
current_main_sha=_head
read_pinned_file=_read

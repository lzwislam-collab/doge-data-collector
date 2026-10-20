"""Separately registered prospective engineering collection. Never writes R2."""
import argparse,base64,copy,hashlib,io,json,os,re,subprocess,types,zipfile
from datetime import datetime,timedelta,timezone
from functools import lru_cache
from pathlib import Path
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.parse import urlparse
import phase2_forward_v2 as parent
import phase2_forward_runtime_v2 as original
import phase1_research as prior
import evidence_append_guard

ROOT=Path(__file__).resolve().parent
BASE='data/e1_forward_v1';CONFIG='config/e1_forward_policy_v1.json'
REGISTRATION='config/e1_forward_registration_v1.json'
WITNESS='.github/workflows/e1_forward_witness.yml';LIVE='.github/workflows/e1_forward_live.yml'
LOCATOR=BASE+'/registration_locator.json';OUT='e1-forward-output'
ARCHIVE=BASE+'/registration_artifact.zip'
NEW={'e1_forward.py','test_e1_forward.py',CONFIG,WITNESS,LIVE}
FIXED=original.FIXED|{parent.REGISTRATION}|NEW
encode=parent.encode;digest=parent.digest;utc=parent.utc

def now_utc():return datetime.now(timezone.utc)

def validate_policy(p):
 expected=parent.policy();allowed={'protocol_id','effective_from_utc','observation_end_utc','fixed_review_utc'}
 if set(p)!=set(expected) or any(encode(p[k])!=encode(expected[k]) for k in expected if k not in allowed):raise ValueError('E1 changed original model/strategy parameters')
 start=utc(p['effective_from_utc']);end=utc(p['observation_end_utc'])
 if p['protocol_id']!='DOGE_E1_FORWARD_20261021' or start<utc(expected['effective_from_utc']) or (start-utc(expected['effective_from_utc'])).total_seconds()%expected['slot_seconds'] or end!=start+timedelta(days=90) or [utc(t) for t in p['fixed_review_utc']]!=[end+timedelta(minutes=25),utc(expected['model_expires_utc'])] or end>=utc(expected['model_expires_utc']):raise ValueError('invalid separately prospective E1 observation window')
 return p

def policy():
 cfg=json.loads((ROOT/CONFIG).read_bytes())
 if set(cfg)!={'schema','parent_protocol_sha256','parent_model_sha256','policy'} or cfg['schema']!='DOGE_E1_ENGINEERING_POLICY_V1' or cfg['parent_protocol_sha256']!=hashlib.sha256(original.frozen.file_bytes(ROOT,parent.POLICY)).hexdigest() or cfg['parent_model_sha256']!=hashlib.sha256(original.frozen.file_bytes(ROOT,parent.MODEL)).hexdigest():raise ValueError('E1 parent binding changed')
 return validate_policy(cfg['policy'])

def engine_core(p):
 validate_policy(p);scope=dict(vars(parent))
 for name,value in vars(parent).items():
  if isinstance(value,types.FunctionType) and value.__module__==parent.__name__:scope[name]=types.FunctionType(value.__code__,scope,value.__name__,value.__defaults__,value.__closure__)
 scope['policy']=lambda:copy.deepcopy(p)
 return types.SimpleNamespace(**scope)

def git(root,*args):
 return subprocess.check_output(['git','--literal-pathspecs','-c','safe.directory=*',*args],cwd=root,stderr=subprocess.DEVNULL)

def read_pinned(root,head,path,required=False,remote=None):
 if not re.fullmatch('[a-f0-9]{40}',head) or not re.fullmatch('[A-Za-z0-9_./-]+',path) or path.startswith('/') or '..' in path.split('/'):raise ValueError('unsafe pinned E1 address')
 try:git(root,'cat-file','-e',head+'^{tree}')
 except subprocess.CalledProcessError:
  if remote is None:
   url='https://raw.githubusercontent.com/'+parent.old.REPO+'/'+head+'/'+path
   from urllib.request import urlopen
   with urlopen(Request(url,headers={'User-Agent':'DOGE-E1-Pinned-Read'}),timeout=20) as response:
    if response.geturl()!=url:raise ValueError('unexpected pinned redirect')
    raw=response.read(32000001)
  else:raw=remote(head,path)
  if len(raw)>32000000:raise ValueError('oversize pinned E1 file')
  return raw
 entries=git(root,'ls-tree','-z','--full-tree',head,'--',path)
 if not entries:
  if required:raise ValueError('required original pinned E1 file absent')
  return b''
 metadata,name=entries.rstrip(b'\0').split(b'\t',1)
 if metadata.split()[:2]!=[b'100644',b'blob'] or name.decode()!=path:raise ValueError('unsafe pinned E1 tree entry')
 return git(root,'show',head+':'+path)

def verify_files():
 registration=json.loads((ROOT/REGISTRATION).read_bytes())
 if set(registration.get('sha256_lf',{}))!=FIXED or registration.get('registration_identity')!=digest({k:v for k,v in registration.items() if k!='registration_identity'}):raise ValueError('E1 exact registered set/identity changed')
 files={path:original.frozen.file_bytes(ROOT,path) for path in FIXED}
 if any(hashlib.sha256(raw).hexdigest()!=registration['sha256_lf'][path] for path,raw in files.items()):raise ValueError('registered E1 code changed; new version required')
 if registration.get('protocol_id')!=policy()['protocol_id']:raise ValueError('E1 registration protocol mismatch')
 files[REGISTRATION]=original.frozen.file_bytes(ROOT,REGISTRATION)
 return registration,files

def guard():
 original.guard();p=policy();registration,files=verify_files()
 return p,registration,files

def freeze():
 if (ROOT/LOCATOR).exists() or any((ROOT/BASE/'events').glob('*.json')):raise ValueError('registered/issued E1 cannot be refrozen')
 original.guard();p=policy();value={'schema':'DOGE_E1_REGISTERED_ENGINEERING_V1','protocol_id':p['protocol_id'],'sha256_lf':{path:hashlib.sha256(original.frozen.file_bytes(ROOT,path)).hexdigest() for path in sorted(FIXED)},'real_orders_authorized':False}
 value['registration_identity']=digest(value);(ROOT/REGISTRATION).write_bytes(encode(value));return value

def verified_witness(required,runs,reader,files,before,after=None):
 for run in runs:
  try:
   if run['repository']['full_name']!=parent.old.REPO or run['path'].split('@')[0]!=WITNESS or run['head_branch']!='main' or run['event'] not in ('workflow_dispatch','workflow_run') or run['status']!='completed' or run['conclusion']!='success' or run.get('pull_requests'):continue
   job=run['audit_job'];started=utc(job['started_at']);finished=utc(job['completed_at'])
   if job['name']!='witness' or job['status']!='completed' or job['conclusion']!='success' or not utc(run['created_at'])<=started<=finished<before or after is not None and started<after:continue
   if all(reader(run['head_sha'],path)==raw for path,raw in {**files,**required}.items()):return {'run_id':run['id'],'head_sha':run['head_sha'],'job_started_at_utc':started.isoformat(),'job_completed_at_utc':finished.isoformat()}
  except (KeyError,TypeError,ValueError):continue
 return None

def check_registration(run,p,files,reader,now):
 if run.get('event')!='workflow_dispatch':raise ValueError('explicit manual E1 registration required')
 proof=verified_witness({},[run],reader,files,min(now,utc(p['effective_from_utc'])))
 if not proof:raise ValueError('successful exact E1 registration must precede prospective start')
 return proof

@lru_cache(maxsize=512)
def api(path):return original.frozen.api(path)

def dispatch(kind='verify'):
 if kind not in ('register','verify'):raise ValueError('unknown E1 witness kind')
 token=os.environ.get('GITHUB_TOKEN')
 if not token:raise ValueError('actual E1 dispatch credential absent')
 request=Request('https://api.github.com/repos/'+parent.old.REPO+'/actions/workflows/e1_forward_witness.yml/dispatches',data=json.dumps({'ref':'main','inputs':{'mode':kind}}).encode(),method='POST',headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'DOGE-E1-Witness'})
 from urllib.request import urlopen
 with urlopen(request,timeout=20) as response:
  if response.status!=204:raise ValueError('E1 witness dispatch not accepted')

def run_with_job(run):
 jobs=api('/actions/runs/'+str(run['id'])+'/attempts/'+str(run.get('run_attempt',1))+'/jobs')['jobs']
 found=[job for job in jobs if job['name']=='witness']
 if len(found)!=1:raise ValueError('unique completed E1 witness job required')
 return {**run,'audit_job':found[0]}

def candidates(pinned=()):
 rows=api('/actions/workflows/e1_forward_witness.yml/runs?per_page=30&branch=main&status=success')['workflow_runs'];by_id={r['id']:r for r in rows if r['head_branch']=='main' and r['status']=='completed' and r['conclusion']=='success'}
 for rid in set(pinned):
  if type(rid) is not int or rid<=0:raise ValueError('unsafe E1 run locator')
  if rid not in by_id:by_id[rid]=api('/actions/runs/'+str(rid))
 ordered=sorted(by_id.values(),key=lambda r:r['created_at'],reverse=True)
 selected=[r for r in ordered if r['id'] in set(pinned)]+[r for r in ordered if r['id'] not in set(pinned)][:30]
 return [run_with_job(r) for r in selected]

def read_record(folder,key):
 path=ROOT/BASE/folder/(key+'.json')
 return json.loads(path.read_bytes()) if path.is_file() else None

def append_record(root,folder,key,row):
 if folder not in ('events','scores','attempts','bindings') or not re.fullmatch(r'[0-9]+_formal(?:_(?:prediction|target|attempt_[0-9]+))?',key):raise ValueError('unsafe isolated E1 append path')
 root=Path(root);path=root/BASE/folder/(key+'.json')
 if any(p.is_symlink() for p in (root/'data',root/BASE,path.parent,path)):raise ValueError('unsafe E1 symlink')
 raw=encode(row)
 if (root/'.git').exists():evidence_append_guard.guard(root,path.relative_to(root).as_posix(),raw)
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('xb') as stream:stream.write(raw)

def records(folder):
 answer=[]
 for path in sorted((ROOT/BASE/folder).glob('*.json')):
  if path.is_symlink() or not re.fullmatch(r'[0-9]+_formal(?:_(?:prediction|target|attempt_[0-9]+))?\.json',path.name):raise ValueError('unsafe E1 journal')
  raw=path.read_bytes();row=json.loads(raw)
  if encode(row)!=raw or row.get('event_id')+'.json'!=path.name:raise ValueError('noncanonical E1 journal')
  evidence_append_guard.guard(ROOT,path.relative_to(ROOT).as_posix(),raw);answer.append(row)
 return answer

def check_history():
 names=git(ROOT,'log','--format=','--name-only','--',BASE).decode().splitlines()
 for name in set(filter(None,names)):
  path=ROOT/name
  if path.is_symlink() or not path.is_file():raise ValueError('published E1 journal deleted')
  evidence_append_guard.guard(ROOT,name,path.read_bytes())

def check_state():
 p,registration,files=guard();check_history();core=engine_core(p);events=records('events');scores=records('scores');attempts=records('attempts');bindings=records('bindings');now=now_utc();model=json.loads((ROOT/parent.MODEL).read_bytes())
 emap={e['event_id']:e for e in events}
 if len(emap)!=len(events) or len({s['event_id'] for s in scores})!=len(scores):raise ValueError('duplicate E1 event or score')
 locator=json.loads((ROOT/LOCATOR).read_bytes()) if (ROOT/LOCATOR).is_file() else None
 if events and not locator:raise ValueError('E1 events without original registration locator')
 for event in events:
  at=utc(event['issued_at_utc'])
  if at>now or at<utc(p['effective_from_utc']) or event['cohort']!='formal' or event['registration_run_id']!=locator['run_id']:raise ValueError('future, non-E1 or unregistered event')
  source=read_pinned(ROOT,event['input_source_commit'],original.SOURCE,True)
  if hashlib.sha256(source).hexdigest()!=event['input_source_sha256'] or parent.frame_from_raw(source,at)!=event['frame']:raise ValueError('E1 exact source/frame changed')
  bx=parent.old.causal_features(parent.benchmark_frame(event['frame']),at);old=json.loads((ROOT/parent.old.PACKAGE).read_bytes())['models']['1']['full'];bp=prior.predict(old,[[bx[j] for j in old['columns']]])[0]
  expected=core.issue(p,model,event['frame'],event['entry_packet'],at,bp,'formal')
  if encode({k:event.get(k) for k in expected})!=encode(expected) or set(event)-set(expected)!={'input_source_commit','input_source_sha256','registration_run_id','source_run_id'}:raise ValueError('E1 frozen formula/provenance replay mismatch')
 for attempt in attempts:
  packet=attempt['packet'];request=utc(packet['requested_at_utc']);captured=utc(packet['captured_at_utc'])
  if attempt['forecast_event_id'] not in emap or not request<=captured<=now or attempt['event_id']!=attempt['forecast_event_id']+'_attempt_'+str(int(request.timestamp()*1000)):raise ValueError('invalid actual E1 capture attempt')
  for key,body in packet['raw_base64'].items():
   raw=base64.b64decode(body,validate=True)
   if key not in parent.old.URLS or hashlib.sha256(raw).hexdigest()!=packet['raw_sha256'][key] or key in packet['raw_text'] and raw.decode()!=packet['raw_text'][key]:raise ValueError('E1 capture original bytes changed')
 for score in scores:
  original.verify_attempt_binding(score,attempts)
  if score['event_id'] not in emap or score!=core.score(emap[score['event_id']],score['target_packet'],p) or utc(score['observed_at_utc'])>now:raise ValueError('E1 score replay mismatch')
 return p,registration,files,events,scores

def _download(url):
 class Redirect(HTTPRedirectHandler):
  def redirect_request(self,req,fp,code,msg,headers,newurl):
   result=super().redirect_request(req,fp,code,msg,headers,newurl)
   if result and urlparse(newurl).hostname!=urlparse(req.full_url).hostname:result.remove_header('Authorization')
   return result
 headers={'Accept':'application/vnd.github+json','User-Agent':'DOGE-E1-Registration'}
 if os.environ.get('GITHUB_TOKEN'):headers['Authorization']='Bearer '+os.environ['GITHUB_TOKEN']
 with build_opener(Redirect()).open(Request(url,headers=headers),timeout=30) as response:raw=response.read(2000001)
 if len(raw)>2000000:raise ValueError('oversize E1 registration artifact')
 return raw

def registration_proof(rid,p,registration,files,require_live=True,save_archive=False):
 run=run_with_job(api('/actions/runs/'+str(rid)));proof=check_registration(run,p,files,lambda h,path:read_pinned(ROOT,h,path),now_utc())
 arts=api('/actions/runs/'+str(rid)+'/artifacts')['artifacts'];matches=[a for a in arts if a['name']=='e1-forward-witness-'+str(rid) and not a['expired']]
 archive=ROOT/ARCHIVE
 if len(matches)==1:
  art=matches[0];raw=_download('https://api.github.com/repos/'+parent.old.REPO+'/actions/artifacts/'+str(art['id'])+'/zip')
  if art['digest']!='sha256:'+hashlib.sha256(raw).hexdigest():raise ValueError('E1 registration artifact digest changed')
 elif require_live or not archive.is_file():raise ValueError('actual E1 registration artifact absent')
 else:
  raw=archive.read_bytes();locator=json.loads((ROOT/LOCATOR).read_bytes())
  if archive.is_symlink() or hashlib.sha256(raw).hexdigest()!=locator['artifact_sha256']:raise ValueError('original archived E1 registration bytes changed')
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  names=[n for n in z.namelist() if n=='witness.json' or n.endswith('/witness.json')]
  if len(names)!=1:raise ValueError('unique E1 registration receipt required')
  receipt=json.loads(z.read(names[0]))
 if receipt.get('kind')!='register' or receipt.get('registration_identity')!=registration['registration_identity'] or receipt.get('source_commit')!=run['head_sha']:raise ValueError('wrong E1 registration kind/identity/source SHA')
 if archive.exists() and archive.read_bytes()!=raw:raise ValueError('original E1 registration archive overwritten')
 if save_archive and not archive.exists():
  archive.parent.mkdir(parents=True,exist_ok=True)
  with archive.open('xb') as stream:stream.write(raw)
 return {**proof,'registration_identity':registration['registration_identity'],'artifact_sha256':hashlib.sha256(raw).hexdigest()}

def registered(p,registration,files):
 path=ROOT/LOCATOR
 if not path.is_file():return None
 raw=path.read_bytes();locator=json.loads(raw)
 if path.is_symlink() or encode(locator)!=raw:raise ValueError('unsafe E1 registration locator')
 evidence_append_guard.guard(ROOT,LOCATOR,raw)
 activation_binding=read_record('bindings','0_formal_prediction');pinned=[locator['run_id']]+([activation_binding['run_id']] if activation_binding else [])
 if not (ROOT/ARCHIVE).is_file():raise ValueError('original E1 registration archive absent')
 required={LOCATOR:raw,ARCHIVE:(ROOT/ARCHIVE).read_bytes()}
 activation=verified_witness(required,candidates(pinned),lambda h,path:read_pinned(ROOT,h,path),files,min(now_utc(),utc(p['effective_from_utc'])))
 if not activation:return None
 proof=registration_proof(locator['run_id'],p,registration,files,require_live=False)
 if locator!=proof:raise ValueError('E1 original registration proof changed')
 return {**proof,'activation_publication':activation}

def confirm_registration(rid):
 p,registration,files=guard();proof=registration_proof(rid,p,registration,files,save_archive=True);path=ROOT/LOCATOR
 if path.exists():
  if path.read_bytes()!=encode(proof):raise ValueError('E1 registration already bound differently')
  return proof
 if now_utc()>=utc(p['effective_from_utc']):raise ValueError('E1 activation locator must precede effective start')
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('xb') as stream:stream.write(encode(proof))
 return proof

def qualification(p,files,events,scores,runs):
 qualified=[];rejected={};smap={s['event_id']:s for s in scores};reader=lambda h,path:read_pinned(ROOT,h,path)
 for event in events:
  key=event['event_id'];at=utc(event['issued_at_utc']);target=utc(event['target_utc']);source=read_pinned(ROOT,event['input_source_commit'],original.SOURCE,True)
  src=verified_witness({original.SOURCE:source},[r for r in runs if r['id']==event['source_run_id']],reader,files,at)
  pred=verified_witness({BASE+'/events/'+key+'.json':encode(event)},runs,reader,files,target,at)
  if not src or not pred:rejected[key]='MISSING_SOURCE_OR_PREDICTION_PUBLIC_WITNESS';continue
  score=smap.get(key)
  if not score:rejected[key]='MISSING_REAL_TARGET';continue
  tgt=verified_witness({BASE+'/scores/'+key+'.json':encode(score)},runs,reader,files,parent.deadline(event,p),target)
  if not tgt:rejected[key]='MISSING_TARGET_PUBLIC_WITNESS';continue
  qualified.append({'event_id':key,'prediction':pred,'target':tgt,'formal_eligible':True,'cost_available':score.get('cost_error') is None})
 return qualified,rejected

def tick(event_id=None):
 p,registration,files,events,scores=check_state();now=now_utc();proof=registered(p,registration,files)
 result={'status':'WAIT_REGISTRATION' if not proof else 'WAIT_ACTIVATION','at_utc':now.isoformat(),'new_events':[],'new_scores':[],'new_target_attempts':[],'request_witness':False,'errors':{},'real_orders_authorized':False,'validated_70pct':False,'statistical_gate_passed':False}
 if not proof:return result
 if os.environ.get('GITHUB_REF')!='refs/heads/main' or os.environ.get('GITHUB_WORKFLOW')!='Publish DOGE E1 forward evidence':raise ValueError('E1 writes require actual main live workflow')
 if not read_record('bindings','0_formal_prediction'):append_record(ROOT,'bindings','0_formal_prediction',{'event_id':'0_formal_prediction',**proof['activation_publication']})
 if now<utc(p['effective_from_utc']):return result
 inspected=events[-4:];pinned=[proof['run_id'],*[e['source_run_id'] for e in inspected]]
 for event in inspected:
  for kind in ('prediction','target'):
   binding=read_record('bindings',event['event_id']+'_'+kind)
   if binding:pinned.append(binding['run_id'])
 runs=candidates(pinned)
 emap={e['event_id']:e for e in events};smap={s['event_id']:s for s in scores};reader=lambda h,path:read_pinned(ROOT,h,path);core=engine_core(p)
 for event in inspected:
  key=event['event_id']
  for kind,value,after,before in [('prediction',event,utc(event['issued_at_utc']),utc(event['target_utc'])),('target',smap.get(key),utc(event['target_utc']),parent.deadline(event,p))]:
   if value is None or read_record('bindings',key+'_'+kind):continue
   bound=verified_witness({BASE+('/events/' if kind=='prediction' else '/scores/')+key+'.json':encode(value)},runs,reader,files,before,after)
   if bound:append_record(ROOT,'bindings',key+'_'+kind,{'event_id':key+'_'+kind,**bound})
 for key,event in emap.items():
  now=now_utc()
  if key in smap or event_id is not None and key!=event_id or not utc(event['target_utc'])<=now<parent.deadline(event,p):continue
  pred=verified_witness({BASE+'/events/'+key+'.json':encode(event)},runs,reader,files,utc(event['target_utc']),utc(event['issued_at_utc']))
  if not pred:result['errors'][key]='prediction public witness absent; no target capture';continue
  try:
   packet=original.capture_target();attempt=key+'_attempt_'+str(int(utc(packet['requested_at_utc']).timestamp()*1000));append_record(ROOT,'attempts',attempt,{'event_id':attempt,'forecast_event_id':key,'packet':packet,'included_in_formal_statistics':False,'real_orders_authorized':False});result['new_target_attempts'].append(attempt)
   score=core.score(event,packet,p);append_record(ROOT,'scores',key,score);result['new_scores'].append(key)
  except Exception as exc:result['errors'][key]=type(exc).__name__+': '+str(exc)
 if event_id is not None:
  if event_id not in emap:raise ValueError('unknown E1 target; never synthesize past issuance')
  result['status']='SCORE_CAPTURED' if result['new_scores'] else 'NO_OPEN_UNSCORED_TARGET';result['request_witness']=bool(result['new_scores'] or result['new_target_attempts']);return result
 now=now_utc();start=utc(p['effective_from_utc']);slot=start+timedelta(seconds=int((now-start).total_seconds()//p['slot_seconds'])*p['slot_seconds']);key=str(int(slot.timestamp()))+'_formal'
 result['status']='NO_OPERATION_DUE'
 if key not in emap and now<utc(p['observation_end_utc']) and now<slot+timedelta(seconds=p['issue_window_seconds']):
  source=None;source_proof=None
  for run in runs:
   raw=read_pinned(ROOT,run['head_sha'],original.SOURCE,True)
   try:parent.frame_from_raw(raw,now)
   except ValueError:continue
   found=verified_witness({original.SOURCE:raw},[run],reader,files,now)
   if found:source=raw;source_proof=found;break
  if source is None:
   # Request a new witness only if a fresh real source exists; do not loop on
   # an expired input or turn an upstream failure into a passing source.
   head=git(ROOT,'rev-parse','HEAD').decode().strip();raw=read_pinned(ROOT,head,original.SOURCE,True)
   try:parent.frame_from_raw(raw,now)
   except ValueError:result['status']='WAIT_FRESH_EXACT_SOURCE'
   else:result.update(status='WAIT_SOURCE_PUBLIC_WITNESS',request_witness=True)
  else:
   packet=original.frozen.capture();at=now_utc();frame=parent.frame_from_raw(source,at);bx=parent.old.causal_features(parent.benchmark_frame(frame),at);old=json.loads((ROOT/parent.old.PACKAGE).read_bytes())['models']['1']['full'];bp=prior.predict(old,[[bx[j] for j in old['columns']]])[0];model=json.loads((ROOT/parent.MODEL).read_bytes())
   event=core.issue(p,model,frame,packet,at,bp,'formal');event.update(input_source_commit=source_proof['head_sha'],input_source_sha256=hashlib.sha256(source).hexdigest(),registration_run_id=proof['run_id'],source_run_id=source_proof['run_id']);append_record(ROOT,'events',event['event_id'],event);result.update(status='PROSPECTIVE_ISSUANCE_CAPTURED',event_id=event['event_id'],target_utc=event['target_utc']);result['new_events'].append(event['event_id'])
 result['request_witness']=bool(result['request_witness'] or result['new_events'] or result['new_scores'] or result['new_target_attempts'])
 return result

def panel(online=True):
 p,registration,files,events,scores=check_state();proof=registered(p,registration,files) if online else None
 current=now_utc();start=utc(p['effective_from_utc']);planned=int((utc(p['observation_end_utc'])-start).total_seconds()//p['slot_seconds']);total=max(0,min(planned,int((current-start).total_seconds()//p['slot_seconds'])+1));denominator=sum(start+timedelta(seconds=i*p['slot_seconds']+p['issue_window_seconds']+p['horizon_seconds']+p['target_window_seconds'])<=current for i in range(total));issued={e['event_id'] for e in events};scored={s['event_id'] for s in scores};missing_issue=sum(str(int((start+timedelta(seconds=i*p['slot_seconds'])).timestamp()))+'_formal' not in issued for i in range(denominator))
 result={'protocol_id':p['protocol_id'],'status':'WAIT_REGISTRATION' if not proof else 'ENGINEERING_ONLY_NOT_PERFORMANCE_ACCEPTED','registered':bool(proof),'registration_run_id':proof['run_id'] if proof else None,'effective_from_utc':p['effective_from_utc'],'event_count':len(events),'score_count':len(scores),'qualified_count':0,'counts':{'events':len(events),'scores':len(scores),'qualified_recent':0,'matured_fixed_denominator':denominator},'gaps':{'missing_issuance':missing_issue,'expired_missing_score':sum(utc(e['target_utc'])+timedelta(seconds=p['target_window_seconds'])<=current and e['event_id'] not in scored for e in events)},'qualification_scope':'latest four events only; no full E1 statistical acceptance','latest':None,'validated_70pct':False,'statistical_gate_passed':False,'real_orders_authorized':False,'original_r2_cohort_modified':False}
 if not events:return result
 inspected=events[-4:];pinned=[proof['run_id'],*[e['source_run_id'] for e in inspected]] if proof else []
 for event in inspected:
  for kind in ('prediction','target'):
   binding=read_record('bindings',event['event_id']+'_'+kind)
   if binding:pinned.append(binding['run_id'])
 runs=candidates(pinned) if proof else [];qualified,rejected=qualification(p,files,inspected,scores,runs);result.update(qualified_count=len(qualified),rejected=rejected);result['counts']['qualified_recent']=len(qualified)
 latest=max(events,key=lambda e:e['issued_at_utc']);key=latest['event_id'];pred=verified_witness({BASE+'/events/'+key+'.json':encode(latest)},runs,lambda h,path:read_pinned(ROOT,h,path),files,utc(latest['target_utc']),utc(latest['issued_at_utc'])) if proof else None
 result['latest']={'event_id':key,'issued_at_utc':latest['issued_at_utc'],'target_utc':latest['target_utc'],'direction':'UP' if latest['candidate_p']['selected']>=.5 else 'DOWN','probability_up':latest['candidate_p']['selected'],'prediction_verified':bool(pred),'score_present':key in {s['event_id'] for s in scores},'score_verified':key in {q['event_id'] for q in qualified}}
 return result

def main():
 parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['freeze','check','witness','confirm-registration','tick','score']);parser.add_argument('--kind',choices=['register','verify'],default='verify');parser.add_argument('--run-id',type=int);parser.add_argument('--event-id');args=parser.parse_args();out=ROOT/OUT;out.mkdir(exist_ok=True)
 try:
  if args.mode=='freeze':result=freeze()
  elif args.mode=='check':
   p,reg,files,events,scores=check_state();result={'status':'ORIGINAL_AND_E1_BYTES_AND_FORMULAS_VERIFIED','event_count':len(events),'score_count':len(scores)}
  elif args.mode=='witness':
   p,reg,files,events,scores=check_state()
   if args.kind=='register' and now_utc()>=utc(p['effective_from_utc']):raise ValueError('registration missed prospective start; no retroactive registration')
   if (ROOT/LOCATOR).is_file() and now_utc()<utc(p['effective_from_utc']):
    locator=json.loads((ROOT/LOCATOR).read_bytes());actual=registration_proof(locator['run_id'],p,reg,files,require_live=True)
    if locator!=actual or not (ROOT/ARCHIVE).is_file():raise ValueError('pre-start activation did not publish original verified registration')
   result={'schema':'DOGE_E1_ACTUAL_WITNESS_V1','kind':args.kind,'registration_identity':reg['registration_identity'],'at_utc':now_utc().isoformat(),'source_commit':git(ROOT,'rev-parse','HEAD').decode().strip(),'event_count':len(events),'score_count':len(scores),'statistical_gate_passed':False,'real_orders_authorized':False}
  elif args.mode=='confirm-registration':
   if not args.run_id:raise ValueError('actual registration run id required')
   result={'status':'REGISTRATION_LOCATOR_CONFIRMED','registration':confirm_registration(args.run_id)}
  else:
   if args.mode=='score' and not args.event_id:raise ValueError('exact E1 event id required')
   result=tick(args.event_id if args.mode=='score' else None)
  (out/('witness.json' if args.mode=='witness' else 'runtime.json')).write_bytes(encode(result))
  if os.environ.get('GITHUB_OUTPUT'):
   with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf8') as stream:
    for key in ('event_id','target_utc','request_witness','has_errors'):
     value=bool(result.get('errors')) if key=='has_errors' else result.get(key,False if key=='request_witness' else '')
     stream.write(key+'='+str(value).lower()+'\n' if isinstance(value,bool) else key+'='+str(value)+'\n')
  print(json.dumps(result,sort_keys=True))
 except Exception as exc:
  (out/'failure.json').write_bytes(encode({'error':type(exc).__name__+': '+str(exc),'statistical_gate_passed':False,'real_orders_authorized':False}));raise

if __name__=='__main__':main()

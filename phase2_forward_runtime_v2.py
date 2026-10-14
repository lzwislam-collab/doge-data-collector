"""Actual-clock, append-only paper issuer. Reuses existing completion callbacks."""
import argparse,json,os,re,subprocess,base64
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from functools import lru_cache
import phase2_forward_v2 as f
import learned_shadow_bundle as frozen
import evidence_append_guard
import phase1_research as prior

ROOT=f.ROOT;SOURCE=frozen.SOURCE
CODE={'phase2_forward_v2.py','phase2_forward_runtime_v2.py','test_phase2_forward_v2.py','test_phase2_forward_runtime_v2.py',
 'phase2_recent.py','phase2_models_v2.py','phase2_research.py','phase2_factor_library.py',
 'phase1_research.py','phase1_research_audit.py','evidence_append_guard.py',
 '.github/workflows/phase2_forward_audit_v2.yml','.github/workflows/phase2_forward_live_v2.yml'}
FIXED=CODE|f.old.REGISTERED_FILES|{f.PARENT,f.MODEL,f.POLICY}

def capture_target():
 start=datetime.now(timezone.utc);raw={};text={};hashes={};bodies={};errors={}
 for key,url in f.old.URLS.items():
  raw[key]=None
  try:
   request=Request(url,headers={'User-Agent':'DOGE-Phase2-Raw-Target/2','Accept':'application/json'})
   try:
    with urlopen(request,timeout=9) as response:
     body=response.read(2000001)
     if response.geturl()!=url:errors[key]='unexpected source redirect'
   except HTTPError as exc:
    body=exc.read(2000001);errors[key]='HTTP '+str(exc.code)
   bodies[key]=base64.b64encode(body).decode('ascii');hashes[key]=f.old.sha(body)
   if len(body)>2000000:errors[key]='oversize response';continue
   text[key]=body.decode('utf8');raw[key]=json.loads(text[key])
  except Exception as exc:errors[key]=type(exc).__name__+': '+str(exc)
 return {'requested_at_utc':start.isoformat(),'captured_at_utc':datetime.now(timezone.utc).isoformat(),
  'source_urls':f.old.URLS,'raw':raw,'raw_text':text,'raw_sha256':hashes,'raw_base64':bodies,'capture_errors':errors}

def verify_attempt_binding(score,attempts):
 found=[a for a in attempts if a['event_id']==score['attempt_id']]
 if len(found)!=1 or found[0]['forecast_event_id']!=score['event_id'] or f.encode(found[0]['packet'])!=f.encode(score['target_packet']):raise ValueError('score not bound to exact original capture attempt')

def verify_files(root,registration):
 hashes=registration.get('sha256_lf',{})
 if set(hashes)!=FIXED:raise ValueError('exact registered file set required')
 files={p:frozen.file_bytes(root,p) for p in FIXED}
 if any(f.old.sha(files[p])!=hashes[p] for p in FIXED):raise ValueError('registered source changed')
 return files

def guard():
 _,old,_=frozen.guard();p=json.loads((ROOT/f.POLICY).read_bytes());f.check_policy(p)
 parent=json.loads((ROOT/f.PARENT).read_bytes());model=json.loads((ROOT/f.MODEL).read_bytes())
 if model!=f.make_model(parent):raise ValueError('new model/version does not reproduce frozen parent')
 if f.utc(p['model_expires_utc']).timestamp()*1000!=model['latest_label_end_ms']+120*24*f.H:raise ValueError('model expiry mismatch')
 f.check_age(model,f.utc(p['observation_end_utc']))
 reg=json.loads((ROOT/f.REGISTRATION).read_bytes());files=verify_files(ROOT,reg)
 if reg['protocol_id']!=p['protocol_id'] or reg['model_identity']!=model['trial_model_identity']:raise ValueError('registration binding changed')
 files[f.REGISTRATION]=frozen.file_bytes(ROOT,f.REGISTRATION)
 guard_history(ROOT)
 return p,model,old,files

def guard_history(root):
 names=subprocess.check_output(['git','-c','safe.directory=*','log','--format=','--name-only','--',f.BASE],cwd=root,text=True).splitlines()
 for name in set(filter(None,names)):
  path=Path(root)/name
  if not name.startswith(f.BASE+'/') or path.is_symlink() or not path.is_file():raise ValueError('published journal deleted or unsafe')
  evidence_append_guard.guard(root,name,path.read_bytes())

def append(root,folder,key,value):
 if folder not in ('events','scores','bindings','attempts') or not re.fullmatch(r'[0-9]+_(formal|canary)(_(prediction|target|attempt_[0-9]+))?',key):raise ValueError('unsafe journal path')
 root=Path(root).resolve();path=root/f.BASE/folder/(key+'.json')
 if any(q.is_symlink() for q in [root/'data',root/f.BASE,path.parent,path]):raise ValueError('symlink journal')
 raw=f.encode(value);evidence_append_guard.guard(root,path.relative_to(root).as_posix(),raw)
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('xb') as stream:stream.write(raw)

def records(folder):
 answer=[]
 for path in sorted((ROOT/f.BASE/folder).glob('*.json')):
  if path.is_symlink() or not re.fullmatch(r'[0-9]+_(formal|canary)(_(prediction|target|attempt_[0-9]+))?\.json',path.name):raise ValueError('unsafe record')
  raw=path.read_bytes();row=json.loads(raw)
  if f.encode(row)!=raw:raise ValueError('noncanonical journal')
  if folder!='bindings' and row['event_id']+'.json'!=path.name:raise ValueError('journal identity mismatch')
  evidence_append_guard.guard(ROOT,path.relative_to(ROOT).as_posix(),raw);answer.append(row)
 return answer

def verify_event(e,p,model):
 at=f.utc(e['issued_at_utc']);bx=f.old.causal_features(f.benchmark_frame(e['frame']),at)
 bm=json.loads((ROOT/f.old.PACKAGE).read_bytes())['models']['1']['full']
 bp=prior.predict(bm,[[bx[j] for j in bm['columns']]])[0]
 expected=f.issue(p,model,e['frame'],e['entry_packet'],at,bp,e['cohort'])
 if f.encode({k:e.get(k) for k in expected})!=f.encode(expected):raise ValueError('event replay mismatch')
 if set(e)-set(expected)-{'input_source_commit','input_source_sha256','registration_run_id','source_run_id'}:raise ValueError('unexpected event fields')

def plan(p,events,scores,at):
 formal=[e for e in events if e['cohort']=='formal'];start=f.utc(p['effective_from_utc']);end=f.utc(p['observation_end_utc'])
 slots=[];slot=start
 while slot<end and slot<=at:
  slots.append(slot);slot+=timedelta(seconds=p['slot_seconds'])
 due={str(int(s.timestamp()))+'_formal' for s in slots if s+timedelta(seconds=p['issue_window_seconds'])<=at}
 issued={e['event_id'] for e in formal};scored={s['event_id'] for s in scores}
 targets={e['event_id'] for e in formal if f.deadline(e,p)<=at}
 return {'formal_scheduled':len(slots),'formal_issued':len(formal),'formal_scores_local':len(scored&issued),
  'matured_missing_issuance':len(due-issued),'matured_missing_targets':len(targets-scored),
  'canary_issued':sum(e['cohort']=='canary' for e in events),'promotion_authorized':False,'validated_70pct':False}

def read_at(head,path):
 if not re.fullmatch('[a-f0-9]{40}',head) or path not in FIXED|{f.REGISTRATION,SOURCE} and not re.fullmatch(f.BASE+r'/(events|scores)/[0-9]+_(formal|canary)\.json',path):raise ValueError('unsafe server read')
 try:return subprocess.check_output(['git','-c','safe.directory=*','show',head+':'+path],cwd=ROOT,stderr=subprocess.DEVNULL)
 except subprocess.CalledProcessError:
  request=Request('https://raw.githubusercontent.com/'+f.old.REPO+'/'+head+'/'+path,headers={'User-Agent':'DOGE-Phase2-Forward'})
  with urlopen(request,timeout=20) as response:
   if not response.geturl().startswith('https://raw.githubusercontent.com/'):raise ValueError('unexpected public redirect')
   raw=response.read(32000001)
  if len(raw)>32000000:raise ValueError('oversize public file')
  return raw

def runs_server(extra=()):
 found=frozen.api('/actions/workflows/phase2_forward_audit_v2.yml/runs?per_page=30')['workflow_runs']
 runs=[v for v in found if v['head_branch']=='main' and v['status']=='completed' and v['conclusion']=='success'][:4]
 ids=set(extra);known={r['id'] for r in runs}
 for rid in sorted(ids-known):
  if type(rid) is not int or rid<=0:raise ValueError('invalid witness locator')
  runs.append(frozen.api('/actions/runs/'+str(rid)))
 answer=[]
 for run in runs:
  if run['status']!='completed' or run['conclusion']!='success':continue
  jobs=frozen.api('/actions/runs/'+str(run['id'])+'/jobs')['jobs']
  for job in jobs:
   if job['name']=='audit':answer.append({**run,'audit_job':job})
 return answer

@lru_cache(maxsize=128)
def source_frame(head,issued):
 raw=read_at(head,SOURCE)
 return f.old.sha(raw),f.frame_from_raw(raw,f.utc(issued))

def provenance(e,files,runs):
 required={'input_source_commit','input_source_sha256','registration_run_id','source_run_id'}
 if not required<=set(e):raise ValueError('mandatory per-event source/registration proof missing')
 if any(type(e[k]) is not int or e[k]<=0 for k in ('registration_run_id','source_run_id')):raise ValueError('invalid proof locator')
 at=f.utc(e['issued_at_utc'])
 reg=f.witness(files,[r for r in runs if r['id']==e['registration_run_id']],read_at,min(at,f.utc(f.policy()['effective_from_utc'])))
 if not reg:raise ValueError('exact registration not witnessed before issuance')
 raw=read_at(e['input_source_commit'],SOURCE)
 src=f.witness({SOURCE:raw},[r for r in runs if r['id']==e['source_run_id']],read_at,at)
 source_sha,frame=source_frame(e['input_source_commit'],e['issued_at_utc'])
 if not src or src['head_sha']!=e['input_source_commit'] or source_sha!=e['input_source_sha256'] or frame!=e['frame']:raise ValueError('exact source/frame not witnessed before issuance')
 return {'registration':reg,'source':src}

def publication(p,events,scores,files,runs):
 attempts=records('attempts')
 scores={s['event_id']:s for s in scores};verified=[];rejected={}
 for e in events:
  key=e['event_id']
  try:provenance(e,files,runs)
  except ValueError as exc:rejected[key]=str(exc);continue
  prediction=f.witness({**files,f.BASE+'/events/'+key+'.json':f.encode(e)},runs,read_at,f.utc(e['target_utc']),f.utc(e['issued_at_utc']))
  if not prediction:rejected[key]='MISSING_PREDICTION_PUBLIC_WITNESS';continue
  s=scores.get(key)
  if not s:rejected[key]='MISSING_REAL_TARGET';continue
  try:verify_attempt_binding(s,attempts)
  except ValueError as exc:rejected[key]=str(exc);continue
  target=f.witness({**files,f.BASE+'/scores/'+key+'.json':f.encode(s)},runs,read_at,f.deadline(e,p),f.utc(e['target_utc']))
  if not target:rejected[key]='MISSING_TARGET_PUBLIC_WITNESS';continue
  verified.append({'event_id':key,'cohort':e['cohort'],'prediction':prediction,'target':target,
   'formal_eligible':e['cohort']=='formal' and e['included_in_formal_statistics'] and s['included_in_formal_statistics'],
   'cost_available':s['cost_error'] is None})
 return verified,rejected

def run(mode):
 p,model,old,files=guard();now=datetime.now(timezone.utc);events=records('events');scores=records('scores');bindings=records('bindings');attempts=records('attempts')
 if len({e['event_id'] for e in events})!=len(events) or len({s['event_id'] for s in scores})!=len(scores):raise ValueError('duplicate journal identity')
 if sum(e['cohort']=='canary' for e in events)>1:raise ValueError('engineering canary count exceeded')
 emap={e['event_id']:e for e in events};smap={s['event_id']:s for s in scores}
 for e in events:
  verify_event(e,p,model)
  if not {'input_source_commit','input_source_sha256','registration_run_id','source_run_id'}<=set(e):raise ValueError('mandatory per-event provenance missing')
  if f.utc(e['issued_at_utc'])>now:raise ValueError('future journal entry')
 for s in scores:
  verify_attempt_binding(s,attempts)
  if s['event_id'] not in emap or s!=f.score(emap[s['event_id']],s['target_packet'],p) or f.utc(s['observed_at_utc'])>now:raise ValueError('score replay mismatch')
 for a in attempts:
  packet=a['packet'];key=a['forecast_event_id'];request=f.utc(packet['requested_at_utc']);captured=f.utc(packet['captured_at_utc'])
  if key not in emap or not request<=captured<=now or a['event_id']!=key+'_attempt_'+str(int(request.timestamp()*1000)):raise ValueError('invalid capture attempt identity/time')
  for name,body in packet['raw_base64'].items():
   data=base64.b64decode(body,validate=True)
   if name not in f.old.URLS or f.old.sha(data)!=packet['raw_sha256'][name] or name in packet['raw_text'] and data.decode('utf8')!=packet['raw_text'][name]:raise ValueError('original attempt bytes changed')
 # Routine callbacks verify only recent/active evidence. They never present a
 # partial network audit as the complete 90-day qualification cohort.
 inspected=events if mode=='audit_all' else events[-4:]
 if mode=='local':
  for e in inspected:
   source_sha,frame=source_frame(e['input_source_commit'],e['issued_at_utc'])
   if source_sha!=e['input_source_sha256'] or frame!=e['frame']:raise ValueError('exact recorded source/frame mismatch')
 inspected_ids={e['event_id'] for e in inspected}
 extra=[e[k] for e in inspected for k in ('registration_run_id','source_run_id') if k in e]
 marker=ROOT/f.BASE/'bindings/0_formal_prediction.json'
 if marker.exists():extra.append(json.loads(marker.read_bytes())['run_id'])
 for e in inspected:
  for kind in ('prediction','target'):
   path=ROOT/f.BASE/'bindings'/(e['event_id']+'_'+kind+'.json')
   if path.exists():extra.append(json.loads(path.read_bytes())['run_id'])
 runs=runs_server(extra) if mode!='local' else []
 registration=f.witness(files,runs,read_at,min(now,f.utc(p['effective_from_utc'])))
 qualified,rejected=publication(p,inspected,[s for s in scores if s['event_id'] in inspected_ids],files,runs) if mode!='local' else ([],{})
 result={'status':'WAIT_PUBLIC_REGISTRATION' if not registration else 'WAIT_ACTIVATION' if now<f.utc(p['effective_from_utc']) else 'FORWARD_PAPER_ONLY',
  'at_utc':now.isoformat(),'model_identity':model['trial_model_identity'],'registration':registration,
  'plan':plan(p,events,scores,now),'qualified':qualified,'rejected':rejected,'new_events':[],'new_scores':[],'new_target_attempts':[],
  'network_audit_events':len(inspected),'network_audit_covers_all_events':len(inspected)==len(events),
  'external_immutable_retention_verified':False,'production_promotion_authorized':False,'real_orders_authorized':False,
  'validated_70pct':False,'errors':{},'request_witness':False}
 if mode in ('audit','audit_all','local'):return result
 if os.environ.get('GITHUB_REF')!='refs/heads/main':raise ValueError('writes only from main')
 if not registration:return result
 if not marker.exists():append(ROOT,'bindings','0_formal_prediction',registration)
 for e in inspected:provenance(e,files,runs)
 # Save only actual server locators; these are re-read, never trusted flags.
 for e in inspected:
  key=e['event_id']
  for kind,path,after,before in [('prediction',f.BASE+'/events/'+key+'.json',f.utc(e['issued_at_utc']),f.utc(e['target_utc'])),('target',f.BASE+'/scores/'+key+'.json',f.utc(e['target_utc']),f.deadline(e,p))]:
   value=e if kind=='prediction' else smap.get(key)
   if value is None:continue
   bound=f.witness({**files,path:f.encode(value)},runs,read_at,before,after)
   if bound and not (ROOT/f.BASE/'bindings'/(key+'_'+kind+'.json')).exists():append(ROOT,'bindings',key+'_'+kind,bound)
 for key,e in emap.items():
  if key in smap or not f.utc(e['target_utc'])<=now<f.deadline(e,p):continue
  proof=f.witness({**files,f.BASE+'/events/'+key+'.json':f.encode(e)},runs,read_at,f.utc(e['target_utc']),f.utc(e['issued_at_utc']))
  if not proof:continue
  try:
   packet=capture_target();attempt=key+'_attempt_'+str(int(f.utc(packet['requested_at_utc']).timestamp()*1000))
   append(ROOT,'attempts',attempt,{'event_id':attempt,'forecast_event_id':key,'packet':packet,
    'included_in_formal_statistics':False,'real_orders_authorized':False})
   result['new_target_attempts'].append(attempt)
   scored=f.score(e,packet,p);append(ROOT,'scores',key,scored);result['new_scores'].append(key)
  except Exception as exc:result['errors'][key]=type(exc).__name__+': '+str(exc)
 cohort='canary' if mode=='canary' else 'formal'
 eligible=not any(e['cohort']=='canary' for e in events) if cohort=='canary' else f.utc(p['effective_from_utc'])<=now<f.utc(p['observation_end_utc'])
 if eligible:
  try:
   raw=read_at(subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),SOURCE)
   source=f.witness({SOURCE:raw},runs,read_at,now)
   if not source:result['status']='WAIT_EXACT_INPUT_WITNESS';result['request_witness']=True
   else:
    packet=frozen.capture();at=datetime.now(timezone.utc);frame=f.frame_from_raw(raw,at)
    bx=f.old.causal_features(f.benchmark_frame(frame),at);bm=old['models']['1']['full'];bp=prior.predict(bm,[[bx[j] for j in bm['columns']]])[0]
    event=f.issue(p,model,frame,packet,at,bp,cohort);key=event['event_id']
    if key not in emap:
     event.update(input_source_commit=source['head_sha'],input_source_sha256=f.old.sha(raw),registration_run_id=registration['run_id'],source_run_id=source['run_id'])
     append(ROOT,'events',key,event);result['new_events'].append(key)
  except Exception as exc:result['errors']['issuance']=type(exc).__name__+': '+str(exc)
 result['request_witness']=bool(result['request_witness'] or result['new_events'] or result['new_scores'] or result['new_target_attempts'])
 return result

def dispatch():
 token=os.environ.get('GITHUB_TOKEN')
 if not token:raise ValueError('runtime credential missing')
 request=Request('https://api.github.com/repos/'+f.old.REPO+'/actions/workflows/phase2_forward_audit_v2.yml/dispatches',
  data=b'{"ref":"main"}',method='POST',headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'DOGE-Phase2-Forward'})
 with urlopen(request,timeout=20) as response:
  if response.status!=204:raise ValueError('audit dispatch failed')

def main():
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['audit','audit_all','local','publish','canary','dispatch']);a=p.parse_args()
 if a.mode=='dispatch':dispatch();return
 out=ROOT/'phase2-forward-output';out.mkdir(exist_ok=True)
 try:result=run(a.mode)
 except Exception as exc:
  (out/'failure.json').write_bytes(f.encode({'error':type(exc).__name__+': '+str(exc)}));raise
 (out/'runtime.json').write_bytes(f.encode(result))
 for key,folder in [('new_events','events'),('new_scores','scores'),('new_target_attempts','attempts')]:
  for name in result.get(key,[]):
   target=out/'new-records'/folder/(name+'.json');target.parent.mkdir(parents=True,exist_ok=True)
   target.write_bytes((ROOT/f.BASE/folder/(name+'.json')).read_bytes())
 if os.environ.get('GITHUB_OUTPUT'):
  with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf8') as stream:
   stream.write('request_witness='+str(result['request_witness']).lower()+'\n'+'has_errors='+str(bool(result['errors'])).lower()+'\n')
 print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()

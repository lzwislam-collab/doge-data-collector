"""Independent read-only R2 review, separately preregistered on main."""
import argparse,json,re,subprocess
from datetime import datetime,timezone
from pathlib import Path
import phase2_forward_v2 as f
import phase2_forward_runtime_v2 as forward
import phase3_independent as assessor
import phase3_lifecycle as lifecycle
import phase3_batches as batches

ROOT=Path(__file__).resolve().parent
POLICY='config/phase3_independent_v1.json';REGISTRY='system/phase3_candidates_v1.json'
REGISTRATION='config/phase3_registration_v1.json';WORKFLOW='.github/workflows/phase3_independent.yml'
NEW_FILES={'phase3_independent.py','phase3_lifecycle.py','phase3_runtime.py','phase3_batches.py',
 'test_phase3_independent.py','test_phase3_lifecycle.py','test_phase3_runtime.py','test_phase3_batches.py',POLICY,REGISTRY,WORKFLOW}
EID='5bba9522aa1fa9fb55a432fe0bf9706a9300476915628727d67bdbbd373f987a'
ROLLING='research/phase2_v2/'+EID
ROLLING_FILES={ROLLING+'/'+name for name in ('model_archive.zip','model_package.json','registry.json','result_summary.json','future_protocol_draft.json')}
FIXED=NEW_FILES|forward.FIXED|{f.REGISTRATION}|ROLLING_FILES

def verify_files(root,registration):
 hashes=registration.get('sha256_lf',{})
 if set(hashes)!=FIXED:raise ValueError('exact independent registered file set required')
 files={p:(Path(root)/p).read_bytes() if p.endswith('.zip') else forward.frozen.file_bytes(root,p) for p in FIXED}
 if any(f.old.sha(files[p])!=hashes[p] for p in FIXED):raise ValueError('independent registered code changed')
 return files

def guard():
 forward.guard()
 p=json.loads((ROOT/POLICY).read_bytes());reg=json.loads((ROOT/REGISTRY).read_bytes())
 if p!=assessor.policy() or reg!=lifecycle.registry():raise ValueError('fixed independent policy/registry changed')
 registration=json.loads((ROOT/REGISTRATION).read_bytes());files=verify_files(ROOT,registration)
 if registration.get('assessment_id')!=p['assessment_id'] or registration.get('model_identity')!=p['model_identity']:raise ValueError('independent registration identity mismatch')
 if registration.get('registration_identity')!=f.digest({k:v for k,v in registration.items() if k!='registration_identity'}):raise ValueError('independent registration digest changed')
 files[REGISTRATION]=forward.frozen.file_bytes(ROOT,REGISTRATION)
 # Code is hash-frozen, not an append ledger. Changed procedures must obtain
 # a new exact public witness before the window and the first formal event.
 # R2's append journal history is independently guarded by forward.guard().
 return p,reg,files

def read_at(head,path):
 if not re.fullmatch('[a-f0-9]{40}',head) or path not in FIXED|{REGISTRATION}:raise ValueError('unsafe assessment server read')
 try:return subprocess.check_output(['git','-c','safe.directory=*','show',head+':'+path],cwd=ROOT,stderr=subprocess.DEVNULL)
 except subprocess.CalledProcessError:
  from urllib.request import Request,urlopen
  url='https://raw.githubusercontent.com/'+f.old.REPO+'/'+head+'/'+path
  with urlopen(Request(url,headers={'User-Agent':'DOGE-Independent-Review'}),timeout=20) as response:
   if response.geturl()!=url:raise ValueError('unexpected assessment redirect')
   raw=response.read(32000001)
  if len(raw)>32000000:raise ValueError('oversize assessment file')
  return raw

def registration_witness(files,runs,reader,now,events):
 before=min([now,f.utc(f.policy()['effective_from_utc']),*[f.utc(e['issued_at_utc']) for e in events if e['cohort']=='formal']])
 for run in runs:
  try:
   if run['repository']['full_name']!=f.old.REPO or run['path'].split('@')[0]!=WORKFLOW or run['head_branch']!='main' or run['event'] not in ('push','workflow_dispatch','workflow_run') or run['status']!='completed' or run['conclusion']!='success':continue
   if run['event']=='workflow_run' and run.get('pull_requests'):continue
   job=run['audit_job'];head=run['head_sha'];created=f.utc(run['created_at']);started=f.utc(job['started_at']);finished=f.utc(job['completed_at'])
   if job['name']!='independent' or job['status']!='completed' or job['conclusion']!='success' or not created<=started<=finished<before:continue
   if all(reader(head,path)==raw for path,raw in files.items()):return {'run_id':run['id'],'head_sha':head,'job_completed_at_utc':finished.isoformat()}
  except (KeyError,TypeError,ValueError):continue
 return None

def registration_runs(events):
 from urllib.parse import urlencode
 query=urlencode({'per_page':30,'branch':'main','status':'success','created':'<'+f.policy()['effective_from_utc']})
 found=forward.frozen.api('/actions/workflows/phase3_independent.yml/runs?'+query)['workflow_runs']
 candidates=[r for r in found if r['head_branch']=='main' and r['status']=='completed' and r['conclusion']=='success'][:4]
 # Pinned public locator may be supplied for complete long-term review. It is
 # reread from GitHub, never treated as an approval flag or stored metadata.
 import os
 pinned=os.environ.get('PHASE3_REGISTRATION_RUN_ID')
 if pinned:
  if not re.fullmatch('[1-9][0-9]*',pinned):raise ValueError('invalid independent registration run locator')
  rid=int(pinned)
  if rid not in {r['id'] for r in candidates}:candidates.append(forward.frozen.api('/actions/runs/'+str(rid)))
 answer=[]
 for run in candidates:
  for job in forward.frozen.api('/actions/runs/'+str(run['id'])+'/jobs')['jobs']:
   if job['name']=='independent':answer.append({**run,'audit_job':job})
 return answer

def full_scope(mode,receipt):return mode=='full' and receipt.get('network_audit_covers_all_events') is True

def rolling_audit():
 import zipfile
 root=ROOT/ROLLING;package=json.loads((root/'model_package.json').read_bytes());registry=json.loads((root/'registry.json').read_bytes())
 if package['experiment_id']!=EID or f.digest(package['identity'])!=EID or registry['experiment_id']!=EID:raise ValueError('rolling research identity changed')
 archive=root/'model_archive.zip'
 if f.old.sha(archive.read_bytes())!=package['model_archive_sha256']:raise ValueError('rolling model archive changed')
 objects=package['model_objects']
 with zipfile.ZipFile(archive) as z:
  names=z.namelist()
  if len(names)!=len(set(names)) or set(names)!=set(objects.values()) or len(names)!=package['model_count']:raise ValueError('rolling model coverage mismatch')
  for sha,name in objects.items():
   if name!=sha+'.json' or f.old.sha(z.read(name))!=sha:raise ValueError('rolling model original bytes changed')
 candidates=registry['candidate_models']
 if len({r['candidate_id'] for r in candidates})!=len(candidates):raise ValueError('duplicate historical candidate')
 for row in candidates:
  if f.digest({k:v for k,v in row.items() if k!='candidate_id'})!=row['candidate_id'] or row['model_sha256'] not in objects or row['production_promotion_authorized'] is not False:raise ValueError('rolling registry/weights changed')
  if row['base_label_latest_ms']+24*f.H>=row['calibration_first_anchor_ms']:raise ValueError('rolling calibration front-look leakage')
 return {'experiment_id':EID,'archive_sha256':package['model_archive_sha256'],
  'archive_verified':True,'verified_models':len(objects),'registered_research_candidates':len(candidates),
  'horizon_folds':{str(h):len({r['fold'] for r in candidates if r['horizon_hours']==h}) for h in (1,24)},
  'historical_holdout_pristine':False,'historical_results_authorize_promotion':False,
  'method':'purged time rolling fit/calibration/validation/test, validation-only model choice'}

def run(mode):
 import os
 if mode not in ('routine','batch','full','local'):raise ValueError('unknown assessment mode')
 p,reg,files=guard();now=datetime.now(timezone.utc)
 replay=forward.run('local' if mode in ('full','batch','local') else 'audit')
 events=forward.records('events');scores=forward.records('scores')
 identity=json.loads((ROOT/REGISTRATION).read_bytes())['registration_identity'];certificate=None
 ids=batches.parse_ids(os.environ.get('PHASE3_BATCH_RUN_IDS','')) if mode=='full' else []
 if mode=='batch' or mode=='full' and not ids and len(events)<=batches.BATCH_EVENTS:
  text=os.environ.get('PHASE3_BATCH_INDEX','0')
  if not re.fullmatch('[0-9]+',text):raise ValueError('invalid batch index')
  certificate=batches.capture(events,scores,forward.guard()[3],identity,int(text))
  if mode=='full':replay.update(batches.merge_verified(events,scores,[certificate],identity))
 if mode=='full' and (ids or len(events)>batches.BATCH_EVENTS):
  # Every certificate is re-downloaded, server digest/job/code/head checked.
  # No unverified local cache may claim full-cohort qualification.
  if not ids:raise ValueError('full review requires complete registered batch run IDs; no unbounded REST fallback')
  replay.update(batches.merge_verified(events,scores,batches.load(ids,files,now),identity))
 if full_scope(mode,replay):replay['network_audit_events']=len(events)
 witness=registration_witness(files,registration_runs(events),read_at,now,events) if mode!='local' else None
 result=assessor.evaluate(p,events,scores,replay['qualified'],now,full_scope(mode,replay),witness is not None)
 result.update(mode=mode,independent_registration=witness,registration_identity=identity,
  forward_runtime=replay,rolling_research=rolling_audit(),paper_rollback_drill=lifecycle.drill(reg),
  external_immutable_retention_verified=False,independent_human_approval_verified=False,
  actual_production_switch=False,real_activation_implemented=False)
 if certificate is not None:result['batch_certificate']=certificate
 if witness is None:result['status']='WAIT_INDEPENDENT_PUBLIC_REGISTRATION'
 return result

def main():
 parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['routine','batch','full','local']);args=parser.parse_args()
 out=ROOT/'phase3-independent-output';out.mkdir(exist_ok=True)
 try:result=run(args.mode)
 except Exception as exc:
  (out/'failure.json').write_bytes(f.encode({'error':type(exc).__name__+': '+str(exc)}));raise
 (out/'assessment.json').write_bytes(f.encode(result))
 if 'batch_certificate' in result:(out/'batch_certificate.json').write_bytes(f.encode(result['batch_certificate']))
 print(json.dumps({k:result[k] for k in ('status','scheduled_events','qualified_events','statistical_gate_passed','independent_registration','actual_production_switch','real_orders_authorized')},sort_keys=True))

if __name__=='__main__':main()

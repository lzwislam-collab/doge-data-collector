"""Persistent reviewed prediction-channel selection. No order or legacy writes.

Authority is reread from GitHub and pre-existing COMPLIANCE storage; hashes of
caller-provided flags cannot approve a release. Selection fails to frozen
reference on expiry/revocation and to no prediction when reference is invalid.
"""
import argparse,hashlib,io,json,os,re,zipfile,subprocess
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.parse import urlparse,quote
import phase2_forward_v2 as f
import phase2_forward_runtime_v2 as forward
import phase3_runtime as assessment_runtime
import phase3_independent as assessor
import user_managed_retention_v2 as storage
import evidence_append_guard

ROOT=Path(__file__).resolve().parent;BASE='system/prediction_releases_v1/events'
FALLBACK='c93004aadc669c074f51a4cb6411769c3d84d388b31773d1251823b1d45ccad3'
encode=f.encode;digest=f.digest

def manifest(review_run_id,review_artifact_sha256,evidence_sha256):
 return {'schema':'DOGE_REVIEWED_PREDICTION_RELEASE_V1','model_identity':assessor.policy()['model_identity'],
  'model_record_sha256':f.old.sha(forward.frozen.file_bytes(ROOT,f.MODEL)),
  'assessment_registration_identity':json.loads((ROOT/assessment_runtime.REGISTRATION).read_bytes())['registration_identity'],
  'review_run_id':review_run_id,'review_artifact_sha256':review_artifact_sha256,'evidence_sha256':evidence_sha256,
  'expires_utc':f.policy()['model_expires_utc'],'fallback_sha256':FALLBACK,'real_orders_authorized':False}

def check_manifest(m):
 if set(m)!=set(manifest(1,'a'*64,'b'*64)) or m!=manifest(m['review_run_id'],m['review_artifact_sha256'],m['evidence_sha256']):raise ValueError('release model/version/fallback manifest changed')
 if type(m['review_run_id']) is not int or m['review_run_id']<=0 or any(not re.fullmatch('[a-f0-9]{64}',m[k]) for k in ('review_artifact_sha256','evidence_sha256')):raise ValueError('invalid release proof locator/digest')

def check_assessment(a):
 if a.get('mode')!='full' or a.get('statistical_gate_passed') is not True or a.get('scheduled_events')!=1080 or a.get('qualified_events')!=1080 or not a.get('criteria') or not all(v is True for v in a['criteria'].values()) or a.get('model_identity')!=assessor.policy()['model_identity'] or a.get('registration_identity')!=json.loads((ROOT/assessment_runtime.REGISTRATION).read_bytes())['registration_identity']:raise ValueError('full fixed-window independently qualified evidence not ready')

def check_approval(comment,permission,release_sha,review_finished,now):
 expected='APPROVE_PREDICTION_RELEASE_V1 '+release_sha
 if comment.get('body','').strip()!=expected or comment.get('user',{}).get('type')!='User' or permission not in ('admin','maintain','write') or not comment.get('issue_url','').startswith('https://api.github.com/repos/'+f.old.REPO+'/issues/'):raise ValueError('actual authorized account approval bound to release required')
 if not review_finished<f.utc(comment['created_at'])<=now:raise ValueError('approval must follow actual completed review')

def check_retention(result,expected):
 if result.get('locked_retention_verified') is not True or result.get('readback_sha256')!=expected or not result.get('version_id') or result.get('version_id')=='null':raise ValueError('actual pinned COMPLIANCE evidence readback missing')

def compare_replay(assessment,replay):
 # The frozen writer quantizes JSON numbers. Compare exactly its serialized
 # representation, rather than an unrounded binary float to stored JSON.
 changed=[k for k,v in replay.items() if encode(assessment.get(k))!=encode(v)]
 if changed:raise ValueError('review does not match current complete original cohort replay: '+','.join(changed))

def packet_bytes(assessment,review_raw):
 entries={'model.json':forward.frozen.file_bytes(ROOT,f.MODEL),'assessment.json':encode(assessment),'review_artifact.zip':review_raw}
 for name in (f.POLICY,f.REGISTRATION,assessment_runtime.POLICY,assessment_runtime.REGISTRATION):entries[name]=forward.frozen.file_bytes(ROOT,name)
 for folder in ('events','scores','attempts','bindings'):
  for path in sorted((ROOT/f.BASE/folder).glob('*.json')):entries[path.relative_to(ROOT).as_posix()]=path.read_bytes()
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for name,raw in sorted(entries.items()):
   info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,raw)
 if len(out.getvalue())>storage.LIMIT:raise ValueError('release evidence exceeds cap; no omissions allowed')
 return out.getvalue()

def _download(url):
 class Redirect(HTTPRedirectHandler):
  def redirect_request(self,req,fp,code,msg,headers,newurl):
   new=super().redirect_request(req,fp,code,msg,headers,newurl)
   if new and urlparse(newurl).hostname!=urlparse(req.full_url).hostname:new.remove_header('Authorization')
   return new
 headers={'Accept':'application/vnd.github+json','User-Agent':'DOGE-Reviewed-Release'}
 if os.environ.get('GITHUB_TOKEN'):headers['Authorization']='Bearer '+os.environ['GITHUB_TOKEN']
 with build_opener(Redirect()).open(Request(url,headers=headers),timeout=30) as response:raw=response.read(32000001)
 if len(raw)>32000000:raise ValueError('review artifact exceeds cap')
 return raw

def review_source(rid):
 p,reg,files=assessment_runtime.guard();forward.run('local');api=forward.frozen.api
 run=api('/actions/runs/'+str(rid));jobs=api('/actions/runs/'+str(rid)+'/attempts/'+str(run['run_attempt'])+'/jobs')['jobs'];arts=api('/actions/runs/'+str(rid)+'/artifacts')['artifacts']
 if run['repository']['full_name']!=f.old.REPO or run['path'].split('@')[0]!=assessment_runtime.WORKFLOW or run['head_branch']!='main' or run['status']!='completed' or run['conclusion']!='success' or run['event'] not in ('push','workflow_dispatch','workflow_run') or run.get('pull_requests'):raise ValueError('actual successful same-repository main review required')
 job=next((j for j in jobs if j['name']=='independent'),None)
 if not job or job['status']!='completed' or job['conclusion']!='success':raise ValueError('independent completed job missing')
 if not all(assessment_runtime.read_at(run['head_sha'],path)==raw for path,raw in files.items()):raise ValueError('review did not use current frozen assessment code')
 matches=[a for a in arts if a['name']=='phase3-independent-'+str(rid) and not a['expired']]
 if len(matches)!=1:raise ValueError('unique unexpired actual review artifact required')
 art=matches[0];raw=_download('https://api.github.com/repos/'+f.old.REPO+'/actions/artifacts/'+str(art['id'])+'/zip')
 sha=hashlib.sha256(raw).hexdigest()
 if art['digest']!='sha256:'+sha:raise ValueError('actual review artifact digest mismatch')
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  names=[n for n in z.namelist() if n.endswith('/assessment.json') or n=='assessment.json']
  if len(names)!=1:raise ValueError('unique actual assessment payload required')
  a=json.loads(z.read(names[0]))
 at=f.utc(a['at_utc']);started=f.utc(job['started_at']);finished=f.utc(job['completed_at'])
 if not f.utc(run['created_at'])<=started<=at<=finished<=datetime.now(timezone.utc):raise ValueError('review payload/server clock mismatch')
 # Recompute scores/metrics; stored success booleans are not sufficient.
 events=forward.records('events');scores=forward.records('scores');prereg=a.get('independent_registration')
 if not prereg:raise ValueError('original pre-window assessment registration missing')
 preg_run=api('/actions/runs/'+str(prereg['run_id']));preg_jobs=api('/actions/runs/'+str(prereg['run_id'])+'/jobs')['jobs'];preg=[{**preg_run,'audit_job':j} for j in preg_jobs if j['name']=='independent']
 registered=assessment_runtime.registration_witness(files,preg,assessment_runtime.read_at,at,events) is not None
 replay=assessor.evaluate(p,events,scores,a['forward_runtime']['qualified'],at,a['forward_runtime']['network_audit_covers_all_events'],registered)
 compare_replay(a,replay)
 return a,raw,sha,finished

def authorize(m,approval_comment_id,retention_config):
 check_manifest(m);now=datetime.now(timezone.utc)
 if now>=f.utc(m['expires_utc']):raise ValueError('candidate model expired')
 a,raw,sha,finished=review_source(m['review_run_id']);check_assessment(a)
 if sha!=m['review_artifact_sha256'] or hashlib.sha256(packet_bytes(a,raw)).hexdigest()!=m['evidence_sha256']:raise ValueError('release not bound to exact raw review/model/cohort evidence')
 api=forward.frozen.api
 if type(approval_comment_id) is not int or approval_comment_id<=0:raise ValueError('actual approval comment locator required')
 comment=api('/issues/comments/'+str(approval_comment_id));login=comment['user']['login'];permission=api('/collaborators/'+quote(login,safe='')+'/permission')['permission']
 check_approval(comment,permission,digest(m),finished,now)
 locked=storage.verify(retention_config,m['evidence_sha256']);check_retention(locked,m['evidence_sha256'])
 return {'schema':'VERIFIED_RELEASE_AUTHORITY_V1','manifest_sha256':digest(m),'verified_at_utc':now.isoformat(),
  'review_run_id':m['review_run_id'],'approval_comment_id':approval_comment_id,'approved_account':login,
  'retention_config':retention_config,'retention':locked,'actual_account_approval_verified':True,'independent_custodian_required':False}

def _write(root,row):
 root=Path(root).resolve();path=root/BASE/(str(row['sequence']).zfill(8)+'.json')
 if any(p.is_symlink() for p in (root/'system',root/'system/prediction_releases_v1',root/BASE,path)) or not path.resolve().is_relative_to(root):raise ValueError('unsafe release journal symlink')
 raw=encode(row)
 if (root/'.git').exists():evidence_append_guard.guard(root,path.relative_to(root).as_posix(),raw)
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('xb') as stream:stream.write(raw)

def _event(value):return {**value,'event_sha256':digest(value)}

def initialize(root):
 row=_event({'sequence':0,'action':'INITIALIZE','previous_event_sha256':None,'selection':'FROZEN_REFERENCE','fallback_sha256':FALLBACK,
  'at_utc':datetime.now(timezone.utc).isoformat(),'manifest':None,'authority':None,'real_orders_authorized':False})
 _write(root,row);return row

def load_state(root):
 root=Path(root);rows=sorted((root/BASE).glob('*.json'));previous=None;selection='FROZEN_REFERENCE';state=None
 if any(p.is_symlink() for p in (root/'system',root/'system/prediction_releases_v1',root/BASE)):raise ValueError('unsafe release namespace')
 if (root/'.git').exists():
  historical=subprocess.check_output(['git','-c','safe.directory=*','log','--format=','--name-only','--',BASE],cwd=root,text=True).splitlines()
  if any(not (root/name).is_file() for name in set(historical) if name.strip()):raise ValueError('published release journal deleted')
 for i,path in enumerate(rows):
  if path.is_symlink() or path.name!=str(i).zfill(8)+'.json':raise ValueError('noncontiguous/unsafe release history')
  raw=path.read_bytes();row=json.loads(raw)
  if set(row)!={'sequence','action','previous_event_sha256','selection','fallback_sha256','at_utc','manifest','authority','real_orders_authorized','event_sha256'} or raw!=encode(row) or row.get('event_sha256')!=digest({k:v for k,v in row.items() if k!='event_sha256'}) or row['sequence']!=i or row['previous_event_sha256']!=previous or row['real_orders_authorized'] is not False or row['fallback_sha256']!=FALLBACK:raise ValueError('release history changed/forked')
  if (root/'.git').exists():evidence_append_guard.guard(root,path.relative_to(root).as_posix(),raw)
  if i==0:
   if row['action']!='INITIALIZE' or row['selection']!='FROZEN_REFERENCE' or row['manifest'] is not None or row['authority'] is not None:raise ValueError('invalid baseline initialization')
  elif row['action']=='ACTIVATE':
   check_manifest(row['manifest'])
   if row['selection']!='CANDIDATE' or row['authority'].get('schema')!='VERIFIED_RELEASE_AUTHORITY_V1' or row['authority'].get('manifest_sha256')!=digest(row['manifest']):raise ValueError('invalid release authority binding')
  elif row['action']=='ROLLBACK':
   if row['selection']!='FROZEN_REFERENCE' or row['manifest'] is not None or row['authority'] is not None:raise ValueError('unsafe rollback target')
  else:raise ValueError('unknown release action')
  if state and f.utc(row['at_utc'])<f.utc(state['at_utc']):raise ValueError('release time reversed')
  state=row;selection=row['selection'];previous=row['event_sha256']
 return state or {'sequence':-1,'selection':selection,'fallback_sha256':FALLBACK,'event_sha256':None,'manifest':None,'authority':None}

def apply(root,command,m,at,verify_authority):
 state=load_state(root)
 if state['sequence']<0:raise ValueError('initialize real baseline journal first')
 if at<f.utc(state['at_utc']):raise ValueError('release time reversed')
 if command=='activate':
  check_manifest(m)
  if at>=f.utc(m['expires_utc']):raise ValueError('candidate expired')
  auth=verify_authority(m)
  if auth.get('schema')!='VERIFIED_RELEASE_AUTHORITY_V1' or auth.get('manifest_sha256')!=digest(m):raise ValueError('caller flags are not verified release authority')
  if state['selection']=='CANDIDATE' and state['manifest']==m:raise ValueError('release already selected')
  selection='CANDIDATE';action='ACTIVATE'
 elif command=='rollback':auth=None;selection='FROZEN_REFERENCE';action='ROLLBACK';m=None
 else:raise ValueError('unsupported release operation')
 row=_event({'sequence':state['sequence']+1,'previous_event_sha256':state['event_sha256'],'action':action,'selection':selection,
  'fallback_sha256':FALLBACK,'at_utc':at.isoformat(),'manifest':m,'authority':auth,'real_orders_authorized':False})
 _write(root,row);return row

def select(root,at,verify_authority=None):
 state=load_state(root);result={'selection':'FROZEN_REFERENCE','fallback_sha256':FALLBACK,'real_orders_authorized':False,'reason':'INITIAL_FROZEN_REFERENCE'}
 if state.get('at_utc') and f.utc(state['at_utc'])>at:result['reason']='FUTURE_RELEASE_STATE';return result
 if state['selection']=='CANDIDATE':
  try:
   m=state['manifest'];check_manifest(m)
   if at>=f.utc(m['expires_utc']):raise ValueError('model expired')
   a=verify_authority(m) if verify_authority else authorize(m,state['authority']['approval_comment_id'],state['authority']['retention_config'])
   if a.get('schema')!='VERIFIED_RELEASE_AUTHORITY_V1' or a.get('manifest_sha256')!=digest(m):raise ValueError('release authority not verified')
   result.update(selection='CANDIDATE',manifest=m,reason='CURRENT_AUTHORITY_REVERIFIED')
  except Exception as exc:result['reason']='FAIL_CLOSED_FALLBACK: '+type(exc).__name__+': '+str(exc)
 return result

def infer(root,frame,at):
 selection=select(root,at)
 try:
  _,old,_=forward.frozen.guard()
  if at<f.utc(json.loads((ROOT/f.old.POLICY).read_bytes())['effective_from_utc']):return {**selection,'status':'WAIT_EFFECTIVE_DATE','probability':None}
  f.features(frame,at)
  if selection['selection']=='CANDIDATE':
   meta=json.loads((ROOT/f.MODEL).read_bytes());f.check_age(meta,at);prob=f.models.predict(meta['model'],[f.features(frame,at)])[0]
  else:
   bm=old['models']['1']['full'];x=f.old.causal_features(f.benchmark_frame(frame),at);prob=f.prior.predict(bm,[[x[j] for j in bm['columns']]])[0]
  return {**selection,'status':'PREDICTION_CHANNEL_ONLY_NOT_LEGACY_ISSUANCE','probability':prob,'at_utc':at.isoformat()}
 except Exception as exc:return {**selection,'status':'NO_PREDICTION_INVALID_REFERENCE_OR_SOURCE','probability':None,'error':type(exc).__name__+': '+str(exc)}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['audit','initialize','rollback','activate','prepare']);a=parser.parse_args();out=ROOT/'prediction-release-output';out.mkdir(exist_ok=True);now=datetime.now(timezone.utc)
 result={'mode':a.mode,'at_utc':now.isoformat(),'real_orders_authorized':False,'legacy_production_pointer_changed':False}
 if a.mode in ('initialize','activate','rollback') and os.environ.get('GITHUB_ACTIONS')=='true' and (os.environ.get('GITHUB_REF')!='refs/heads/main' or os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch'):raise ValueError('release mutation requires explicit main dispatch')
 if a.mode=='initialize':result['event']=initialize(ROOT)
 elif a.mode=='rollback':result['event']=apply(ROOT,'rollback',{},now,None)
 elif a.mode=='activate':
  path=Path(os.environ['RELEASE_MANIFEST_PATH']).resolve();allowed=(ROOT/'system/prediction_release_manifests_v1').resolve()
  if not path.is_relative_to(allowed) or path.is_symlink():raise ValueError('release manifest outside approved namespace')
  m=json.loads(path.read_bytes());cfg=json.loads((ROOT/'config/user_managed_prediction_retention.json').read_bytes()) if (ROOT/'config/user_managed_prediction_retention.json').exists() else None
  result['event']=apply(ROOT,'activate',m,now,lambda value:authorize(value,int(os.environ['APPROVAL_COMMENT_ID']),cfg))
 elif a.mode=='prepare':
  rid=int(os.environ['REVIEW_RUN_ID']);assessment,raw,sha,finished=review_source(rid);packet=packet_bytes(assessment,raw);m=manifest(rid,sha,hashlib.sha256(packet).hexdigest())
  (out/'evidence.zip').write_bytes(packet);(out/'manifest.json').write_bytes(encode(m));result.update(manifest_sha256=digest(m),evidence_sha256=m['evidence_sha256'],statistical_gate_passed=assessment['statistical_gate_passed'],cloud_upload_performed=False)
 else:
  result['selection']=select(ROOT,now);result['actual_release_selected']=result['selection']['selection']=='CANDIDATE';result['raw_pointer_authority_is_rechecked']=True
 (out/'control.json').write_bytes(encode(result));print(json.dumps(result,sort_keys=True))

if __name__=='__main__':
 try:main()
 except Exception as exc:
  out=ROOT/'prediction-release-output';out.mkdir(exist_ok=True)
  (out/'failure.json').write_bytes(encode({'error':type(exc).__name__+': '+str(exc),'real_orders_authorized':False,'legacy_production_pointer_changed':False}))
  raise

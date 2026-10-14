"""Bounded REST audit batches, aggregated only from successful frozen jobs."""
import hashlib,io,json,re,zipfile
from datetime import datetime,timezone
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.parse import urlparse
import phase2_forward_v2 as f
import phase2_forward_runtime_v2 as forward

BATCH_EVENTS=20
MAX_BATCHES=64

def partition(events):return [events[i:i+BATCH_EVENTS] for i in range(0,len(events),BATCH_EVENTS)]

def rest_call_upper_bound(events,bindings):
 ids={e[k] for e in events for k in ('registration_run_id','source_run_id')}
 for e in events:ids.update(bindings(e))
 return 2*len(ids)+1

def parse_ids(text):
 if not text:return []
 values=text.split(',')
 if len(values)>MAX_BATCHES or any(not re.fullmatch('[1-9][0-9]*',s.strip()) for s in values):raise ValueError('bounded positive batch run IDs required')
 ids=[int(s.strip()) for s in values]
 if len(ids)!=len(set(ids)):raise ValueError('duplicate batch run ID')
 return ids

def fetch_runs(ids):
 answer=[]
 if len(ids)>4*BATCH_EVENTS:raise ValueError('batch API budget exceeded')
 for rid in sorted(ids):
  if type(rid) is not int or rid<=0:raise ValueError('invalid original witness locator')
  run=forward.frozen.api('/actions/runs/'+str(rid))
  for job in forward.frozen.api('/actions/runs/'+str(rid)+'/jobs')['jobs']:
   if job['name']=='audit':answer.append({**run,'audit_job':job})
 return answer

def capture(events,scores,files,identity,index):
 groups=partition(events)
 if type(index) is not int or not 0<=index<max(1,len(groups)) or len(groups)>MAX_BATCHES:raise ValueError('invalid bounded batch index')
 group=groups[index] if groups else [];keys={e['event_id'] for e in group}
 subset=[s for s in scores if s['event_id'] in keys]
 def locators(e):
  values=[]
  for kind in ('prediction','target'):
   path=forward.ROOT/f.BASE/'bindings'/(e['event_id']+'_'+kind+'.json')
   if path.exists():values.append(json.loads(path.read_bytes())['run_id'])
  return values
 ids={e[k] for e in group for k in ('registration_run_id','source_run_id')}
 for e in group:ids.update(locators(e))
 runs=fetch_runs(ids)
 qualified,rejected=forward.publication(f.policy(),group,subset,files,runs)
 head=__import__('subprocess').check_output(['git','-c','safe.directory=*','rev-parse','HEAD'],cwd=forward.ROOT,text=True).strip()
 return {'kind':'DOGE_PHASE3_REGISTERED_AUDIT_BATCH_V1','generated_at_utc':datetime.now(timezone.utc).isoformat(),
  'assessment_registration_identity':identity,'source_commit':head,'batch_index':index,'batch_events_cap':BATCH_EVENTS,
  'event_sha256':{e['event_id']:f.digest(e) for e in group},'score_sha256':{s['event_id']:f.digest(s) for s in subset},
  'qualified':qualified,'rejected':rejected,'raw_server_runs':runs,'original_locator_count':len(ids),
  'original_rest_calls':2*len(ids),'external_immutable_retention_verified':False,'real_orders_authorized':False}

def verify_remote_certificate(bundle,files,now):
 """API data is read by load(); never accepts standalone caller flags."""
 try:
  import phase3_runtime as runtime
  run=bundle['run'];job=bundle['job'];artifact=bundle['artifact'];raw=bundle['archive'];cert=bundle['certificate']
  if run['repository']['full_name']!=f.old.REPO or run['path'].split('@')[0]!=runtime.WORKFLOW or run['head_branch']!='main' or run['event']!='workflow_dispatch' or run['status']!='completed' or run['conclusion']!='success':raise ValueError('not a successful main independent batch')
  started=f.utc(job['started_at']);finished=f.utc(job['completed_at'])
  if job['name']!='independent' or job['status']!='completed' or job['conclusion']!='success' or not f.utc(run['created_at'])<=started<=finished<=now:raise ValueError('invalid independent batch job')
  if artifact['name']!='phase3-independent-'+str(run['id']) or artifact['expired'] or artifact['digest']!='sha256:'+hashlib.sha256(raw).hexdigest():raise ValueError('actual independent artifact digest mismatch')
  if cert['kind']!='DOGE_PHASE3_REGISTERED_AUDIT_BATCH_V1' or cert['source_commit']!=run['head_sha'] or not started<=f.utc(cert['generated_at_utc'])<=finished or cert['batch_events_cap']!=BATCH_EVENTS or len(cert['event_sha256'])>BATCH_EVENTS:raise ValueError('invalid batch certificate/version/clock')
  if not all(runtime.read_at(run['head_sha'],path)==value for path,value in files.items()):raise ValueError('batch job did not run exact frozen independent code')
  for kind,mapping in (('events',cert['event_sha256']),('scores',cert['score_sha256'])):
   for key,sha in mapping.items():
    if not re.fullmatch('[0-9]+_(formal|canary)',key) or f.old.sha(forward.read_at(run['head_sha'],f.BASE+'/'+kind+'/'+key+'.json'))!=sha:raise ValueError('batch source journal not bound to actual run head')
  # Exact frozen code generated this archive after actual original REST checks.
  # This is an incremental independent replay certificate, not a claim that
  # every old run is reread in this final job or an external WORM receipt.
  return cert
 except (KeyError,TypeError,ValueError) as exc:raise ValueError('unverified independent batch certificate: '+str(exc)) from exc

def load(ids,files,now):
 import os
 class Redirect(HTTPRedirectHandler):
  def redirect_request(self,req,fp,code,msg,headers,newurl):
   target=super().redirect_request(req,fp,code,msg,headers,newurl)
   if target and urlparse(newurl).hostname!=urlparse(req.full_url).hostname:target.remove_header('Authorization')
   return target
 opener=build_opener(Redirect());certs=[]
 for rid in ids:
  run=forward.frozen.api('/actions/runs/'+str(rid));jobs=forward.frozen.api('/actions/runs/'+str(rid)+'/jobs')['jobs'];arts=forward.frozen.api('/actions/runs/'+str(rid)+'/artifacts')['artifacts']
  job=next((j for j in jobs if j['name']=='independent'),None)
  eligible=[a for a in arts if a['name']=='phase3-independent-'+str(rid) and not a['expired']]
  if len(eligible)!=1 or job is None:raise ValueError('unique successful batch artifact/job required')
  art=eligible[0];headers={'Accept':'application/vnd.github+json','User-Agent':'DOGE-Independent-Batches'}
  if os.environ.get('GITHUB_TOKEN'):headers['Authorization']='Bearer '+os.environ['GITHUB_TOKEN']
  url='https://api.github.com/repos/'+f.old.REPO+'/actions/artifacts/'+str(art['id'])+'/zip'
  with opener.open(Request(url,headers=headers),timeout=30) as response:raw=response.read(32000001)
  if len(raw)>32000000:raise ValueError('oversize independent batch artifact')
  with zipfile.ZipFile(io.BytesIO(raw)) as z:
   matches=[n for n in z.namelist() if n.endswith('/batch_certificate.json') or n=='batch_certificate.json']
   if len(matches)!=1:raise ValueError('unique batch certificate required')
   cert=json.loads(z.read(matches[0]))
  certs.append(verify_remote_certificate({'run':run,'job':job,'artifact':art,'archive':raw,'certificate':cert},files,now))
 return certs

def merge_verified(events,scores,certificates,identity):
 expected={e['event_id']:f.digest(e) for e in events};expected_scores={s['event_id']:f.digest(s) for s in scores}
 seen={};seen_scores={};qualified=[];rejected={}
 for c in certificates:
  if c['assessment_registration_identity']!=identity:raise ValueError('batch from different registered assessment')
  keys=set(c['event_sha256'])
  if keys&set(seen) or not keys<=set(expected) or any(expected[k]!=v for k,v in c['event_sha256'].items()):raise ValueError('batch duplicate/changed event coverage')
  if set(c['score_sha256'])!=set(expected_scores)&keys or any(expected_scores[k]!=v for k,v in c['score_sha256'].items()):raise ValueError('batch score snapshot changed or incomplete')
  qkeys=[q['event_id'] for q in c['qualified']]
  if len(qkeys)!=len(set(qkeys)) or set(qkeys)&set(c['rejected']) or set(qkeys)|set(c['rejected'])!=keys:raise ValueError('incomplete batch qualification accounting')
  seen.update(c['event_sha256']);seen_scores.update(c['score_sha256']);qualified.extend(c['qualified']);rejected.update(c['rejected'])
 if seen!=expected or seen_scores!=expected_scores:raise ValueError('full independent audit batches incomplete')
 return {'qualified':qualified,'rejected':rejected,'network_audit_covers_all_events':True,
  'incremental_registered_batch_certificates':len(certificates),'original_api_metadata_reread_in_final_job':False}

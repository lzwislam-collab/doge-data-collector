"""Opt-in OSS BucketWorm daily inventory custody, never historical PIT approval."""
import base64, hashlib, json, os, re, io, zipfile, argparse, urllib.request, subprocess
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

OWNER='1379992038848464'
BUCKET='doge-evidence-1379992038848464-cn'
ENDPOINT='https://oss-cn-hangzhou.aliyuncs.com'
PREFIX='doge/evidence/'
LIMIT=256*1024*1024

def digest(raw):return hashlib.sha256(raw).hexdigest()
def utc(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)

def check_bucket(bucket):
    info=bucket.get_bucket_info();lock=bucket.get_bucket_worm()
    if str(info.owner.id)!=OWNER or info.location!='oss-cn-hangzhou':raise ValueError('bucket owner or location mismatch')
    if bucket.get_bucket_versioning().status!='Enabled':raise ValueError('versioning not enabled')
    days=int(lock.retention_period_days)
    if lock.worm_state!='Locked' or days<730 or not lock.worm_id:raise ValueError('locked 730-day WORM required')
    return {'worm_id':lock.worm_id,'state':lock.worm_state,'retention_days':days,
            'policy_created_at_utc':lock.creation_date,'owner':OWNER}

def upload_one(bucket,raw,extension,lock,now=None):
    if extension not in ('.zip','.json') or not 0<len(raw)<=LIMIT:raise ValueError('invalid evidence payload')
    sha=digest(raw);key=PREFIX+sha+extension;uploaded=False
    try:head=bucket.head_object(key)
    except Exception as exc:
        if getattr(exc,'status',None)!=404:raise
        response=bucket.put_object(key,raw,headers={
            'x-oss-forbid-overwrite':'true','x-oss-server-side-encryption':'AES256',
            'Content-MD5':base64.b64encode(hashlib.md5(raw).digest()).decode(),
            'Content-Type':'application/zip' if extension=='.zip' else 'application/json'})
        head=response;uploaded=True
    version=head.headers.get('x-oss-version-id')
    if not version or version=='null':raise ValueError('non-null pinned version required')
    result=bucket.get_object(key,params={'versionId':version});hasher=hashlib.sha256();size=0
    try:
        for chunk in iter(lambda:result.read(1024*1024),b''):
            size+=len(chunk)
            if size>LIMIT:raise ValueError('readback exceeds cap')
            hasher.update(chunk)
    finally:result.close()
    finished=now or datetime.now(timezone.utc)
    modified=datetime.fromtimestamp(result.last_modified,timezone.utc)
    server=parsedate_to_datetime(result.headers['date']).astimezone(timezone.utc)
    until=modified+timedelta(days=lock['retention_days'])
    if (result.headers.get('x-oss-version-id')!=version or hasher.hexdigest()!=sha or size!=len(raw)
        or modified>server+timedelta(seconds=5) or modified>finished+timedelta(minutes=5)
        or until<finished+timedelta(days=729)
        or result.headers.get('x-oss-server-side-encryption')!='AES256'):
        raise ValueError('version/hash/encryption/server time/remaining retention mismatch')
    return {'key':key,'version_id':version,'readback_sha256':hasher.hexdigest(),'byte_count':size,
            'upload_performed':uploaded,'server_last_modified_utc':modified.isoformat(),
            'server_response_date_utc':server.isoformat(),'calculated_retain_until_utc':until.isoformat(),
            'retention_basis':'OSS locked BucketWorm days + version LastModified',
            'readback_request_id':result.request_id,'verified_at_utc':finished.isoformat(),
            'locked_bytes_verified':True,'independent_custodian_verified':False,
            'legacy_fixed_2028_10_16_horizon_satisfied':until>=utc('2028-10-16T00:00:00Z'),
            'historical_pre_target_retention_verified':False}

def extract_artifact(raw,destination):
    destination=Path(destination).resolve()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries=archive.infolist();names=[e.filename for e in entries]
        if len(names)!=len(set(names)) or sum(e.file_size for e in entries)>1024*1024*1024:
            raise ValueError('duplicate paths or archive exceeds cap')
        for entry in entries:
            name=entry.filename
            if ('\\' in name or ':' in name or name.startswith('/') or
                any(p in ('','.','..') for p in name.rstrip('/').split('/')) or
                (entry.external_attr>>16)&0o170000==0o120000):raise ValueError('unsafe artifact path')
            path=destination/name
            if not path.resolve().is_relative_to(destination):raise ValueError('artifact escape')
            if not entry.is_dir():
                path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('xb') as f:f.write(archive.read(entry))

def github(path,binary=False):
    # Never forward Authorization across artifact-download redirects.
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
            if redirected and urlparse(newurl).hostname!=urlparse(req.full_url).hostname:
                redirected.remove_header('Authorization')
            return redirected
    url='https://api.github.com/repos/lzwislam-collab/doge-data-collector'+path
    headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'DOGE-OSS-retention'}
    with urllib.request.build_opener(Redirect()).open(urllib.request.Request(url,headers=headers),timeout=90) as response:
        raw=response.read(256*1024*1024+1)
    if len(raw)>256*1024*1024:raise ValueError('GitHub response exceeds cap')
    return raw if binary else json.loads(raw)

def stage(root,force=False):
    root=Path(root).resolve();base=root/'data/pit_retention_preparation_v1'
    prior=json.loads((base/'latest.json').read_bytes());marker=json.loads((base/'artifact_publication.json').read_bytes())
    sha=prior['bundle_sha256']
    if not re.fullmatch('[a-f0-9]{64}',sha) or marker['bundle_sha256']!=sha:raise ValueError('publication binding mismatch')
    output=root/'oss-retention-output';output.mkdir(exist_ok=True)
    previous=root/'audit/oss_retention'/f'{sha}.json'
    if previous.exists() and not force:
        old=json.loads(previous.read_bytes())
        if old.get('status')!='LOCKED_DAILY_INVENTORY_VERIFIED' or old.get('bundle_sha256')!=sha:
            raise ValueError('invalid previous receipt')
        (output/'status.json').write_text(json.dumps({'status':'SKIP_PREVIOUSLY_VERIFIED_INVENTORY','bundle_sha256':sha,
            'fresh_object_readback_performed':False,'previous_receipt':str(previous.relative_to(root)),
            'independent_custodian_verified':False},indent=2)+'\n')
        return False
    artifact=github('/actions/artifacts/'+str(int(marker['artifact_id'])))
    run=github('/actions/runs/'+str(int(marker['run_id'])))
    if (artifact.get('expired') or artifact['workflow_run']['id']!=int(marker['run_id']) or
        run['head_branch']!='main' or run['event']=='pull_request' or run['repository']['full_name']!='lzwislam-collab/doge-data-collector' or
        run['path']!='.github/workflows/okx_hourly_extreme_history.yml' or
        artifact.get('digest','').removeprefix('sha256:')!=marker['artifact_archive_sha256']):
        raise ValueError('source artifact provenance mismatch')
    raw=github('/actions/artifacts/'+str(artifact['id'])+'/zip',True)
    if digest(raw)!=marker['artifact_archive_sha256']:raise ValueError('artifact transport digest mismatch')
    stage_root=root/'oss-inventory-stage';destination=stage_root/'data/pit_retention_preparation_v1'
    destination.mkdir(parents=True,exist_ok=True);extract_artifact(raw,destination)
    copied=json.loads((destination/'latest.json').read_bytes())
    if copied['bundle_sha256']!=sha:raise ValueError('artifact inventory mismatch')
    import pit_retention_preparation as pit
    pit.verify_inventory_bundle(stage_root,copied)
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    (output/'source.json').write_text(json.dumps({'source_commit':head,'artifact_source_commit':run['head_sha'],'artifact_id':artifact['id'],
        'artifact_digest':artifact['digest'],'source_run_id':run['id'],'source_run_url':run['html_url'],
        'bundle_sha256':sha,'inventory_objects':copied['objects'],'prepared_at_utc':copied['prepared_at_utc']},indent=2)+'\n')
    return True

def upload_inventory(root):
    root=Path(root).resolve();stage_root=root/'oss-inventory-stage';base=stage_root/'data/pit_retention_preparation_v1'
    prior=json.loads((base/'latest.json').read_bytes());output=root/'oss-retention-output'
    import oss2
    token=os.environ['ALIBABA_CLOUD_SECURITY_TOKEN']
    if not token:raise ValueError('short-lived STS token required')
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkcore.request import CommonRequest
    identity_request=CommonRequest();identity_request.set_domain('sts.aliyuncs.com')
    identity_request.set_version('2015-04-01');identity_request.set_action_name('GetCallerIdentity')
    identity_request.set_accept_format('json');identity_request.set_protocol_type('https')
    client=AcsClient(os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],
                     'cn-hangzhou',security_token=token)
    identity=json.loads(client.do_action_with_exception(identity_request))
    if str(identity.get('AccountId'))!=OWNER or 'doge-evidence-github-main' not in identity.get('Arn',''):
        raise ValueError('unexpected actual caller identity')
    bucket=oss2.Bucket(oss2.StsAuth(os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],token),
                       ENDPOINT,BUCKET,connect_timeout=30)
    lock=check_bucket(bucket)
    import pit_retention_preparation as pit
    pit.verify_inventory_bundle(stage_root,prior)
    index_path=pit.safe(stage_root,prior['bundle_path']);raw=index_path.read_bytes()
    parts=json.loads(raw)['parts'] if prior.get('bundle_kind')=='SHARDED_INVENTORY_INDEX' else []
    receipts=[]
    for part in parts:
        payload=pit.safe(stage_root,part['bundle_path']).read_bytes()
        if digest(payload)!=part['bundle_sha256']:raise ValueError('part changed')
        receipts.append(upload_one(bucket,payload,'.zip',lock))
    receipts.append(upload_one(bucket,raw,'.json' if parts else '.zip',lock))
    final_lock=check_bucket(bucket)
    if final_lock!=lock:raise ValueError('bucket metadata changed during readback')
    report=dict(json.loads((output/'source.json').read_bytes()),status='LOCKED_DAILY_INVENTORY_VERIFIED',
        provider='aliyun_oss_locked_bucket_worm',bucket=BUCKET,endpoint=ENDPOINT,bucket_owner=OWNER,
        custody='USER_MANAGED_EXTERNAL_PROVIDER_WORM',independent_custodian_verified=False,
        objects=receipts,object_count=len(receipts),shard_count=len(parts),bucket_worm=final_lock,
        caller_identity={k:identity.get(k) for k in ('AccountId','Arn','IdentityType','RequestId')},
        verified_at_utc=datetime.now(timezone.utc).isoformat(),historical_pre_target_retention_verified=False,
        scope='Existing once-per-UTC-day inventory only; later forecasts are not covered.',
        limits=['Console lock is not an object receipt. All listed versions were read through OSS API.',
                'No independent human custodian verified. No delete probe performed.',
                'Payment default may lead to provider deletion. No model or investment rule changed.',
                '730-day approval does not automatically satisfy the legacy fixed 2028-10-16 horizon.'])
    report['run_url']='https://github.com/'+os.environ['GITHUB_REPOSITORY']+'/actions/runs/'+os.environ['GITHUB_RUN_ID']
    (output/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    folder=root/'audit/oss_retention';folder.mkdir(parents=True,exist_ok=True)
    (folder/(prior['bundle_sha256']+'.json')).write_text(json.dumps(report,indent=2)+'\n')
    (folder/'latest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['stage','upload']);parser.add_argument('--force',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parent
    if args.mode=='stage':
        ready=stage(root,args.force)
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('ready='+str(ready).lower()+'\n')
        print('Inventory ready for real OSS upload' if ready else 'Unchanged inventory; no fresh readback claimed')
    else:
        try:print(json.dumps(upload_inventory(root),sort_keys=True))
        except Exception as exc:
            folder=root/'oss-retention-output';folder.mkdir(exist_ok=True)
            (folder/'failure.json').write_text(json.dumps({'status':'NOT_VERIFIED','error_type':type(exc).__name__,
                'provider_code':str(getattr(exc,'code','')),'request_id':str(getattr(exc,'request_id','')),
                'locked_bytes_verified':False},indent=2)+'\n')
            raise SystemExit('OSS upload/readback rejected; sanitized details in failure.json')

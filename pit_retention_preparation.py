"""Opt-in PIT metadata checks, exact-byte packages and read-only S3 receipts.

Never changes a frozen model, activates inputs, backdates availability, creates
storage, uploads, deletes, changes retention or grants performance acceptance.
"""
import argparse, copy, hashlib, json, math, re, zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE='data/pit_retention_preparation_v1'
LIMIT_BYTES=256*1024*1024
def clock(value):
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if not isinstance(value,datetime) or value.tzinfo is None:raise ValueError('timezone required')
    return value.astimezone(timezone.utc)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def canonical(row):return json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def safe(root,name):
    root=Path(root).resolve()
    if not isinstance(name,str) or '\\' in name:raise ValueError('unsafe evidence path')
    parts=name.split('/')
    if parts[0] not in ('data','system') and not (len(parts)==1 and name.endswith('.py')):raise ValueError('non-evidence path')
    if any(p in ('','.','..') or ':' in p or p.startswith('.') for p in parts):raise ValueError('unsafe evidence path')
    current=root
    for part in parts:
        current=current/part
        if current.is_symlink():raise ValueError('symlink evidence path')
    resolved=current.resolve()
    if not resolved.is_relative_to(root):raise ValueError('evidence path escape')
    return resolved
def age_limit(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:raise ValueError('invalid age limit')
    return value

def select_asof(root,rows,decision_at,max_age_seconds):
    """Validate the entire local chain; select the last available revision.

    Receipt age is NOT observation age. Caller must separately impose its
    preregistered source-age limits and validate values with the source parser.
    A later downloaded historical value cannot appear at an earlier decision.
    """
    at=clock(decision_at);age_limit(max_age_seconds)
    previous=None;latest={};selected={};seen_records=set();verified=set();receipt_clocks={}
    for row in rows:
        check=dict(row);saved=check.pop('record_sha256',None)
        if saved!=digest(canonical(check)) or saved in seen_records or row.get('previous_sha256')!=previous:raise ValueError('ledger chain/duplicate/hash mismatch')
        seen_records.add(saved);previous=saved
        for field in ('source_id','observation_key','source_url','raw_sha256','raw_path','requested_at_utc','first_seen_utc','usable_from_utc'):
            if not row.get(field):raise ValueError('missing provenance: '+field)
        if not row['source_url'].startswith('https://') or not re.fullmatch('[a-f0-9]{64}',row['raw_sha256']):raise ValueError('invalid provenance')
        if not math.isfinite(float(row['value'])):raise ValueError('invalid observation value')
        key=row['source_id'],row['observation_key']
        if row.get('revision_of_sha256')!=latest.get(key):raise ValueError('revision predecessor mismatch')
        latest[key]=saved
        requested,seen,usable=(clock(row[k]) for k in ('requested_at_utc','first_seen_utc','usable_from_utc'))
        if not requested<=seen<=usable:raise ValueError('receipt chronology invalid')
        if key in receipt_clocks and seen<=receipt_clocks[key]:raise ValueError('revision receipt must advance')
        receipt_clocks[key]=seen
        if row.get('published_at_utc') and clock(row['published_at_utc'])>seen:raise ValueError('publication after receipt')
        expected='data/enhancement_sources_v1/raw/'+row['raw_sha256'][:2]+'/'+row['raw_sha256']+'.txt'
        if row['raw_path']!=expected:raise ValueError('unexpected raw evidence path')
        if expected not in verified:
            if digest(safe(root,expected).read_bytes())!=row['raw_sha256']:raise ValueError('raw evidence hash mismatch')
            verified.add(expected)
        if usable<=at:
            # A stale latest revision must not fall back to an older value.
            selected[key]=copy.deepcopy(row) if 0<=(at-seen).total_seconds()<=max_age_seconds else None
    return [r for _,r in sorted(selected.items()) if r is not None]

def prepare_bundle(root,objects,now=None):
    """Freeze exact bytes in one package. Package time is actual preparation."""
    now=clock(now or datetime.now(timezone.utc));root=Path(root).resolve()
    if not objects:raise ValueError('empty evidence package')
    content={};total=0
    for name,expected in sorted(objects.items()):
        raw=safe(root,name).read_bytes();total+=len(raw)
        if total>LIMIT_BYTES:raise ValueError('evidence package exceeds cap')
        if digest(raw)!=expected:raise ValueError('evidence content hash mismatch')
        content[name]=raw
    manifest={'schema_version':1,'prepared_at_utc':now.isoformat(),'objects':dict(sorted(objects.items())),
        'historical_first_publication_verified':False,'model_use_authorized':False,
        'independent_retention_verified':False,'raw_source_capture_time_independently_verified':False,
        'limits':['Preparation now cannot establish earlier availability or public release.',
                  'S3 version protection does not prevent new versions or delete markers.',
                  'A stored package must be read by pinned VersionId, not current key.']}
    folder=safe(root,BASE+'/bundles');folder.mkdir(parents=True,exist_ok=True)
    import io
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,raw in [('bundle_manifest.json',canonical(manifest))]+list(content.items()):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,raw)
    raw=stream.getvalue();sha=digest(raw);path=safe(root,BASE+'/bundles/'+sha+'.zip')
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('existing package mismatch')
    else:
        with path.open('xb') as handle:handle.write(raw)
    result={'status':'NOT_CONFIGURED','independent_retention_verified':False,'bundle_path':path.relative_to(root).as_posix(),
        'bundle_sha256':sha,'byte_count':len(raw),'objects':len(objects),'prepared_at_utc':now.isoformat(),
        'upload_performed':False,'storage_provisioned':False,'credentials_required_in_chat':False}
    receipt=safe(root,BASE+'/bundles/'+sha+'.json')
    if not receipt.exists():
        with receipt.open('x',encoding='utf-8') as handle:json.dump(result,handle,indent=2)
    return path,result

def verify_external(config,expected_sha256,now=None,client=None,identity_client=None,target_at=None):
    """Read-only version/owner/identity/hash/COMPLIANCE/duration verification.

    Distinct AWS accounts do not prove distinct human administrators. All fake
    clients in tests are fixtures, never evidence of actual cloud validation.
    """
    supplied_clock=now is not None
    now=clock(now or datetime.now(timezone.utc))
    result={'status':'NOT_CONFIGURED','independent_retention_verified':False,'pre_target_retention_verified':False,
        'pre_target_package_existence_verified':False,'historical_lock_effective_time_verified':False,
        'historical_first_publication_verified':False,'expected_sha256':expected_sha256,'verified_at_utc':now.isoformat(),
        'delete_or_overwrite_probe_performed':False}
    if not config:return result
    result['status']='NOT_VERIFIED'
    try:
        if config.get('provider')!='aws_s3_object_lock':raise ValueError('unsupported provider')
        for field in ('bucket','key','version_id'):
            if not isinstance(config.get(field),str) or not config[field] or config[field]=='null':raise ValueError('pinned object missing')
        owner=config['bucket_owner_account_id'];collector=config['collector_account_id']
        if not all(isinstance(a,str) and re.fullmatch('[0-9]{12}',a) for a in (owner,collector)) or owner==collector:raise ValueError('distinct owner and collector accounts required')
        days=config['minimum_retention_days']
        if isinstance(days,bool) or not isinstance(days,int) or not 1<=days<=36500:raise ValueError('explicit positive retention days required')
        if not re.fullmatch('[a-f0-9]{64}',expected_sha256):raise ValueError('invalid expected hash')
        if client is None or identity_client is None:
            import botocore.session
            session=botocore.session.get_session()
            client=client or session.create_client('s3',region_name=config.get('region'))
            identity_client=identity_client or session.create_client('sts',region_name=config.get('region'))
        if identity_client.get_caller_identity()['Account']!=collector:raise ValueError('actual collector identity mismatch')
        bucket={'Bucket':config['bucket'],'ExpectedBucketOwner':owner}
        if client.get_object_lock_configuration(**bucket)['ObjectLockConfiguration']['ObjectLockEnabled']!='Enabled':raise ValueError('Object Lock not enabled')
        if client.get_bucket_versioning(**bucket).get('Status')!='Enabled':raise ValueError('versioning not enabled')
        params=dict(bucket,Key=config['key'],VersionId=config['version_id'])
        retention=client.get_object_retention(**params)['Retention'];until=clock(retention['RetainUntilDate'])
        if retention['Mode']!='COMPLIANCE' or until<now+timedelta(days=days):raise ValueError('COMPLIANCE or minimum remaining duration missing')
        response=client.get_object(**params);body=response['Body'];sha=hashlib.sha256();size=0
        try:
            for chunk in iter(lambda:body.read(1024*1024),b''):
                size+=len(chunk)
                if size>LIMIT_BYTES:raise ValueError('external object exceeds cap')
                sha.update(chunk)
        finally:body.close()
        modified=clock(response['LastModified'])
        completed=now if supplied_clock else datetime.now(timezone.utc)
        target=clock(target_at) if target_at is not None else None
        if until<completed+timedelta(days=days):raise ValueError('minimum remaining retention at completion missing')
        if response.get('VersionId')!=config['version_id'] or sha.hexdigest()!=expected_sha256 or modified>completed:raise ValueError('version/hash/clock mismatch')
        result.update(status='VERIFIED',independent_retention_verified=True,version_id=config['version_id'],
            readback_sha256=sha.hexdigest(),bucket_owner_account_verified=owner,collector_account_verified=collector,
            retain_until_utc=until.isoformat(),server_last_modified_utc=modified.isoformat(),verified_at_utc=completed.isoformat(),
            pre_target_package_existence_verified=bool(target is not None and modified<target),
            pre_target_retention_verified=bool(target is not None and modified<=completed<target),
            limits=['Distinct AWS account IDs do not prove distinct human administrators.',
                    'Server object time witnesses package existence, not when retention first became effective.',
                    'No deletion or overwrite probe was performed.'])
    except ImportError:result.update(status='UNAVAILABLE',reason='Optional botocore unavailable; nothing installed')
    except Exception as exc:result.update(reason=type(exc).__name__+': verification rejected')
    return result

def write_latest(root,result):
    output=safe(root,BASE+'/latest.json');output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')

def implementation_hash():
    return digest(Path(__file__).read_bytes().replace(b'\r\n',b'\n'))

def latest_manifest(root,now):
    import source_evidence_snapshot as snapshots
    root=Path(root).resolve();now=clock(now)
    _,tip,expected,_=snapshots.prior_versions(root,now,snapshots.PLUS_STORAGE_NAMESPACE)
    if not tip:raise ValueError('no actual source inventory')
    path=safe(root,tip);raw=path.read_bytes()
    if digest(raw)!=expected:raise ValueError('latest inventory hash mismatch')
    captured=clock(json.loads(raw)['captured_at_utc'])
    if not 0<=(now-captured).total_seconds()<=900:raise ValueError('source inventory is stale or future')
    return path

def record_failure(root,now=None):
    import os
    result={'status':'BLOCKED','observed_at_utc':clock(now or datetime.now(timezone.utc)).isoformat(),
        'run_id':os.environ.get('GITHUB_RUN_ID'),'generated_new_bundle':False,
        'independent_retention_verified':False,'reason':'preparation step failed or timed out'}
    path=safe(root,BASE+'/run_latest.json');path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result

def record_artifact(root,artifact_id,artifact_digest,run_id,now=None):
    root=Path(root).resolve();now=clock(now or datetime.now(timezone.utc))
    prior=json.loads(safe(root,BASE+'/latest.json').read_bytes())
    if (prior.get('status')!='NOT_CONFIGURED' or prior.get('generated_new_bundle') is not True
        or prior.get('run_id')!=str(run_id) or not str(run_id).isdigit()
        or prior.get('implementation_sha256')!=implementation_hash()):raise ValueError('artifact receipt does not match current preparation')
    artifact_digest=str(artifact_digest).removeprefix('sha256:')
    if not str(artifact_id).isdigit() or int(artifact_id)<=0 or not re.fullmatch('[a-f0-9]{64}',artifact_digest):raise ValueError('valid action artifact outputs required')
    if clock(prior['prepared_at_utc'])>now:raise ValueError('future preparation')
    bundle=safe(root,prior['bundle_path'])
    if digest(bundle.read_bytes())!=prior['bundle_sha256']:raise ValueError('local bundle changed before artifact acknowledgement')
    result={'status':'GITHUB_ARTIFACT_UPLOAD_RECORDED_NOT_WORM','recorded_at_utc':now.isoformat(),
        'artifact_id':str(artifact_id),'artifact_archive_sha256':artifact_digest,'run_id':str(run_id),
        'bundle_sha256':prior['bundle_sha256'],'implementation_sha256':prior['implementation_sha256'],
        'independent_retention_verified':False,'remote_bundle_content_readback_verified':False,
        'confirmation_source':'successful actions/upload-artifact@v4 outputs; not a WORM claim'}
    safe(root,BASE+'/artifact_publication.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result

def prepare_daily(root,now=None):
    """One inventory package per UTC day/code version; no all-day PIT claim."""
    root=Path(root).resolve();now=clock(now or datetime.now(timezone.utc))
    observed={'observed_at_utc':now.isoformat(),'generated_new_bundle':False,
              'independent_retention_verified':False,'upload_performed':False}
    report=safe(root,BASE+'/run_latest.json');report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(dict(observed,status='PENDING'),indent=2)+'\n',encoding='utf-8')
    try:
        code_sha=implementation_hash();previous=safe(root,BASE+'/latest.json')
        if previous.exists():
            prior=json.loads(previous.read_bytes());prepared=clock(prior['prepared_at_utc'])
            marker_path=safe(root,BASE+'/artifact_publication.json')
            marker=json.loads(marker_path.read_bytes()) if marker_path.exists() else {}
            complete=(marker.get('status')=='GITHUB_ARTIFACT_UPLOAD_RECORDED_NOT_WORM'
                and marker.get('bundle_sha256')==prior.get('bundle_sha256')
                and marker.get('run_id')==prior.get('run_id') and prior.get('run_id') is not None
                and marker.get('implementation_sha256')==code_sha
                and prepared<=clock(marker['recorded_at_utc'])<=now)
            if (prior.get('status')=='NOT_CONFIGURED' and prepared<=now and prepared.date()==now.date()
                and prior.get('implementation_sha256')==code_sha
                and re.fullmatch('[a-f0-9]{64}',prior.get('bundle_sha256',''))
                and prior.get('independent_retention_verified') is False and prior.get('upload_performed') is False and complete):
                result=dict(observed,status='SKIP_ALREADY_PREPARED_TODAY',
                    previous_prepared_at_utc=prior['prepared_at_utc'],previous_bundle_sha256=prior['bundle_sha256'],
                    previous_run_id=prior.get('run_id'),previous_artifact_id=marker['artifact_id'],prior_artifact_readback_verified=False)
                report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
                return result
        manifest=latest_manifest(root,now)
        result=prepare_existing(root,manifest,now)
        import os
        result.update(observed,generated_new_bundle=True,implementation_sha256=code_sha,
            source_manifest_path=manifest.relative_to(root).as_posix(),source_manifest_sha256=digest(manifest.read_bytes()),
            run_id=os.environ.get('GITHUB_RUN_ID'),run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),
            scope='once-per-UTC-day inventory package, not every later forecast protected')
        write_latest(root,result)
        report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        return result
    except Exception as exc:
        report.write_text(json.dumps(dict(observed,status='BLOCKED',reason=type(exc).__name__+': preparation rejected'),indent=2)+'\n',encoding='utf-8')
        raise

def prepare_existing(root,manifest,now=None):
    """Reuse the registered inventory without editing its implementation."""
    root=Path(root).resolve();now=clock(now or datetime.now(timezone.utc))
    requested=Path(manifest)
    if not requested.is_absolute():requested=root/requested
    manifest=safe(root,requested.relative_to(root).as_posix())
    namespace=json.loads(manifest.read_bytes()).get('storage_namespace','data/evidence_snapshot')
    safe(root,namespace+'/retention_exports')
    import evidence_retention
    export_path,export=evidence_retention.export_manifest(root,manifest)
    objects={obj['local_path']:obj['sha256'] for obj in export['objects']}
    # Include actual candidate inputs and enhancement originals omitted by the
    # old supporting inventory. This does not retroactively certify their PIT.
    for prefix in ('data/enhancement_sources_v1','data/minimal_market_forward_v1','data/feature_ablation_v1','system/versions'):
        for path in (root/prefix).rglob('*'):
            if path.is_file():
                name=path.relative_to(root).as_posix();objects[name]=digest(safe(root,name).read_bytes())
    for name in ('data/hourly_research/ohlc.csv','system/current_plus_evidence.json','system/current_paired_shadow.json'):
        if (root/name).exists():objects[name]=digest(safe(root,name).read_bytes())
    import plus_evidence_version, minimal_market_forward
    for name in sorted(set(plus_evidence_version.CODE_FILES)|set(minimal_market_forward.CODE_FILES)):
        objects[name]=digest(safe(root,name).read_bytes())
    objects['pit_retention_preparation.py']=digest(safe(root,'pit_retention_preparation.py').read_bytes())
    ledger=root/'data/enhancement_sources_v1/observations.jsonl'
    rows=[json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()] if ledger.exists() else []
    selected=select_asof(root,rows,now,48*3600)
    bundle,result=prepare_bundle(root,objects,now)
    result.update(source_ledger_rows=len(rows),receipt_fresh_asof_rows=len(selected),receipt_freshness_hours=48,
        source_observation_age_limits_verified=False,model_use_authorized=False,historical_first_publication_verified=False,
        retention_export_path=export_path.relative_to(root).as_posix())
    write_latest(root,result)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    sub=p.add_subparsers(dest='mode',required=True)
    prepare=sub.add_parser('prepare');choice=prepare.add_mutually_exclusive_group(required=True)
    choice.add_argument('--manifest',type=Path);choice.add_argument('--latest',action='store_true')
    prepare.add_argument('--daily',action='store_true')
    verify=sub.add_parser('verify');verify.add_argument('--config',required=True,type=Path);verify.add_argument('--expected-sha256',required=True);verify.add_argument('--target-at')
    sub.add_parser('record-failure')
    recorded=sub.add_parser('record-artifact');recorded.add_argument('--artifact-id',required=True);recorded.add_argument('--artifact-digest',required=True);recorded.add_argument('--run-id',required=True)
    args=p.parse_args()
    if args.mode=='prepare':
        if args.daily and not args.latest:p.error('--daily requires --latest')
        result=prepare_daily(args.root) if args.daily else prepare_existing(args.root,latest_manifest(args.root,datetime.now(timezone.utc)) if args.latest else args.manifest)
    elif args.mode=='verify':result=verify_external(json.loads(args.config.read_text(encoding='utf-8')),args.expected_sha256,target_at=args.target_at)
    elif args.mode=='record-failure':result=record_failure(args.root)
    else:result=record_artifact(args.root,args.artifact_id,args.artifact_digest,args.run_id)
    print(json.dumps(result,ensure_ascii=False))
    return 1 if result['status'] in ('NOT_VERIFIED','UNAVAILABLE') else 0
if __name__=='__main__':raise SystemExit(main())

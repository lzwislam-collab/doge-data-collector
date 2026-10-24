"""Opt-in OSS BucketWorm daily inventory custody, never historical PIT approval."""
import struct,stat
import base64, hashlib, json, os, re, io, zipfile, argparse, urllib.request, subprocess, tempfile, shutil
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

OWNER='1379992038848464'
BUCKET='doge-evidence-1379992038848464-cn'
ENDPOINT='https://oss-cn-hangzhou.aliyuncs.com'
PREFIX='doge/evidence/'
LIMIT=256*1024*1024
# Transport container budget is separate from the unchanged per-object cap.
# Transport is streamed; extraction requires an exact post-download capacity plan.
# These are consumer resource budgets, not a maximum frozen-producer inventory.
# 64 ZIP containers plus 448 metadata members can expand to exactly 20 GiB.
ARTIFACT_LIMIT=21*1024*1024*1024
ARTIFACT_EXPANDED_LIMIT=20*1024*1024*1024
ARTIFACT_ENTRY_LIMIT=512
ARTIFACT_DIRECTORY_LIMIT=1024*1024
DISK_RESERVE=256*1024*1024
# The frozen producer caps source bytes, not ZIP headers/manifest/deflate overhead.
# Keep that 256 MiB source cap; separately bound the stored container overhead.
STORED_ZIP_LIMIT=LIMIT+8*1024*1024
INVENTORY_MEMBER_LIMIT=20000
INVENTORY_METADATA_LIMIT=8*1024*1024
ARTIFACT_PATH_DEPTH_LIMIT=8
ARTIFACT_PATH_BYTES_LIMIT=1024

def digest(raw):return hashlib.sha256(raw).hexdigest()
def utc(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)

def identity_client(ak,secret,token):
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkcore.auth.credentials import StsTokenCredential
    return AcsClient(region_id='cn-hangzhou',credential=StsTokenCredential(ak,secret,token),
                     connect_timeout=10,timeout=30)

def check_bucket(bucket):
    info=bucket.get_bucket_info();lock=bucket.get_bucket_worm()
    if str(info.owner.id)!=OWNER or info.location!='oss-cn-hangzhou':raise ValueError('bucket owner or location mismatch')
    if bucket.get_bucket_versioning().status!='Enabled':raise ValueError('versioning not enabled')
    days=int(lock.retention_period_days)
    if lock.state!='Locked' or days<730 or not lock.worm_id:raise ValueError('locked 730-day WORM required')
    return {'worm_id':lock.worm_id,'state':lock.state,'retention_days':days,
            'policy_created_at_utc':lock.creation_date,'owner':OWNER}

def upload_one(bucket,raw,extension,lock,now=None):
    cap=STORED_ZIP_LIMIT if extension=='.zip' else LIMIT
    if extension not in ('.zip','.json') or not 0<len(raw)<=cap:raise ValueError('invalid evidence payload')
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
            if size>cap:raise ValueError('readback exceeds cap')
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

def reject_link_ancestors(path):
    """Check lexical ancestors before resolve; also reject NTFS junctions."""
    path=Path(os.path.abspath(path))
    for component in (path,*path.parents):
        if component.is_symlink() or getattr(component,'is_junction',lambda:False)():
            raise ValueError('unsafe staging link ancestor')
        try:attributes=getattr(component.lstat(),'st_file_attributes',0)
        except FileNotFoundError:continue
        if attributes&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):
            raise ValueError('unsafe staging reparse ancestor')
    return path

def read_bounded(path,cap):
    path=reject_link_ancestors(path)
    if path.stat().st_size>cap:raise ValueError('inventory file exceeds read budget')
    with path.open('rb') as stream:raw=stream.read(cap+1)
    if len(raw)>cap:raise ValueError('inventory file grew beyond read budget')
    return raw

def read_inventory_json(path):
    value=json.loads(read_bounded(path,INVENTORY_METADATA_LIMIT))
    if not isinstance(value,dict):raise ValueError('inventory metadata must be an object')
    return value

def validate_inventory_references(stage_root,prior):
    """Check declared types and sizes before the unchanged verifier parses ZIPs."""
    stage_root=reject_link_ancestors(stage_root)
    base='data/pit_retention_preparation_v1/bundles/'
    sha=prior.get('bundle_sha256');kind=prior.get('bundle_kind')
    if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('invalid inventory digest')
    if type(prior.get('objects')) is not int or prior['objects']<=0:raise ValueError('invalid inventory object count')
    if kind not in (None,'SHARDED_INVENTORY_INDEX'):raise ValueError('unsupported inventory kind')
    expected=base+sha+('.index.json' if kind else '.zip')
    if prior.get('bundle_path')!=expected:raise ValueError('inventory path/type mismatch')
    raw=read_bounded(stage_root/expected,INVENTORY_METADATA_LIMIT if kind else STORED_ZIP_LIMIT)
    if digest(raw)!=sha:raise ValueError('inventory index/package digest mismatch')
    parts=[]
    if kind:
        manifest=json.loads(raw)
        if (not isinstance(manifest,dict) or manifest.get('bundle_kind')!=kind
            or manifest.get('per_part_uncompressed_cap')!=LIMIT or not isinstance(manifest.get('parts'),list)
            or not 2<=len(manifest['parts'])<=64):raise ValueError('invalid inventory shard index')
        parts=manifest['parts'];seen=set()
        for part in parts:
            d=part.get('bundle_sha256');size=part.get('byte_count')
            if (not isinstance(d,str) or not re.fullmatch('[a-f0-9]{64}',d)
                or part.get('bundle_path')!=base+d+'.zip' or d in seen
                or type(size) is not int or not 0<size<=STORED_ZIP_LIMIT):raise ValueError('inventory part path/type/size mismatch')
            seen.add(d);path=reject_link_ancestors(stage_root/part['bundle_path'])
            if path.stat().st_size!=size:raise ValueError('inventory part declared size mismatch')
    return raw,parts

def verify_single_inventory(stage_root,prior):
    """Supply the single-package checks absent from the frozen hash-only branch."""
    import pit_retention_preparation as pit
    path=reject_link_ancestors(Path(stage_root)/prior['bundle_path'])
    with zipfile.ZipFile(path) as archive:
        detail=json.loads(archive.read('bundle_manifest.json'));names=archive.namelist()
        objects=detail.get('objects') if isinstance(detail,dict) else None
        if (not isinstance(objects,dict) or len(objects)!=prior['objects']
            or len(names)!=len(set(names)) or set(names)!={'bundle_manifest.json',*objects}):
            raise ValueError('single inventory coverage/count mismatch')
        total=0
        for name,expected in objects.items():
            pit.safe(stage_root,name)
            if not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected):
                raise ValueError('invalid single inventory object digest')
            total+=archive.getinfo(name).file_size
            if total>LIMIT:raise ValueError('single inventory source exceeds frozen cap')
            hasher=hashlib.sha256()
            with archive.open(name) as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):hasher.update(chunk)
            if hasher.hexdigest()!=expected:raise ValueError('single inventory content hash mismatch')

def staged_capacity(stage_root,archive_bytes):
    destination=reject_link_ancestors(stage_root)/'data/pit_retention_preparation_v1'
    entries=[];nodes=0
    for path in destination.rglob('*'):
        nodes+=1
        if nodes>ARTIFACT_ENTRY_LIMIT*(ARTIFACT_PATH_DEPTH_LIMIT+1):raise ValueError('inventory path count exceeds budget')
        reject_link_ancestors(path)
        relative=path.relative_to(destination).as_posix()
        if len(path.relative_to(destination).parts)>ARTIFACT_PATH_DEPTH_LIMIT or len(relative.encode('utf-8'))>ARTIFACT_PATH_BYTES_LIMIT:
            raise ValueError('inventory path exceeds budget')
        if not path.is_file():continue
        info=zipfile.ZipInfo(relative);info.file_size=path.stat().st_size;entries.append(info)
        if len(entries)>ARTIFACT_ENTRY_LIMIT:raise ValueError('inventory file count exceeds budget')
    if len({e.filename.casefold() for e in entries})!=len(entries):raise ValueError('inventory path collision')
    return inventory_capacity(entries,archive_bytes)

def publish_inventory(private_root,stage_root,prepared_report,report_path):
    """Publish both artifacts, restoring the private directory if report commit fails."""
    reject_link_ancestors(stage_root);reject_link_ancestors(report_path)
    if stage_root.exists():raise ValueError('existing staging destination')
    identity=private_root.stat();private_root.rename(stage_root)
    try:
        reject_link_ancestors(report_path)
        prepared_report.replace(report_path)
    except BaseException:
        reject_link_ancestors(stage_root);current=stage_root.stat()
        if (identity.st_dev,identity.st_ino)!=(current.st_dev,current.st_ino):raise RuntimeError('staging owner changed; refusing rollback of foreign directory')
        stage_root.rename(private_root)
        raise

def prepare_upload_inventory(root):
    root=reject_link_ancestors(root).resolve();stage_root=reject_link_ancestors(root/'oss-inventory-stage')
    output=reject_link_ancestors(root/'oss-retention-output')
    prior=read_inventory_json(stage_root/'data/pit_retention_preparation_v1/latest.json')
    source=read_inventory_json(output/'source.json');capacity=source.get('inventory_capacity')
    if (source.get('bundle_sha256')!=prior.get('bundle_sha256') or source.get('inventory_objects')!=prior.get('objects')
        or not isinstance(capacity,dict)):raise ValueError('staged inventory/source receipt mismatch')
    observed=staged_capacity(stage_root,capacity.get('archive_bytes'))
    if any(observed[k]!=capacity.get(k) for k in ('expanded_bytes','zip_containers','metadata_files')):
        raise ValueError('staged inventory changed after capacity validation')
    raw,parts=validate_inventory_references(stage_root,prior)
    validate_staged_packages(stage_root/'data/pit_retention_preparation_v1')
    import pit_retention_preparation as pit
    pit.verify_inventory_bundle(stage_root,prior)
    if prior.get('bundle_kind') is None:verify_single_inventory(stage_root,prior)
    for path in (output/'receipt.json',root/'audit/oss_retention'/(prior['bundle_sha256']+'.json'),root/'audit/oss_retention/latest.json'):
        reject_link_ancestors(path)
    return {'root':root,'stage_root':stage_root,'output':output,'prior':prior,'source':source,'raw':raw,'parts':parts}

def inventory_capacity(entries,archive_bytes):
    if type(archive_bytes) is not int or not 0<=archive_bytes<=ARTIFACT_LIMIT:
        raise ValueError('artifact transport exceeds cap')
    if len(entries)>ARTIFACT_ENTRY_LIMIT:raise ValueError('artifact entry count exceeds cap')
    expanded=0;zips=0;metadata=0
    for entry in entries:
        size=entry.file_size
        if type(size) is not int or size<0:raise ValueError('invalid inventory member size')
        if entry.is_dir():
            if size:raise ValueError('directory has payload')
            continue
        if entry.filename.lower().endswith('.zip'):
            zips+=1;cap=STORED_ZIP_LIMIT
        else:
            metadata+=1;cap=INVENTORY_METADATA_LIMIT
        if size>cap:raise ValueError('stored inventory object exceeds cap')
        expanded+=size
    if zips>64:raise ValueError('inventory exceeds 64 ZIP containers')
    if expanded>ARTIFACT_EXPANDED_LIMIT:raise ValueError('expanded inventory exceeds cap')
    return {'archive_bytes':archive_bytes,'expanded_bytes':expanded,'zip_containers':zips,
            'metadata_files':metadata,'entry_count':len(entries),'disk_reserve_bytes':DISK_RESERVE,
            'peak_additional_disk_bytes':archive_bytes+expanded+DISK_RESERVE,
            'transport_cap_bytes':ARTIFACT_LIMIT,'expanded_cap_bytes':ARTIFACT_EXPANDED_LIMIT,
            'basis':'declared ZIP sizes, checked before extraction; includes simultaneous archive and extracted bytes',
            'frozen_producer_has_finite_container_maximum':False}

def artifact_capacity(path):
    with Path(path).open('rb') as stream:
        expected=artifact_directory_preflight(stream)
        size=stream.seek(0,2);stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            entries=archive.infolist()
            if len(entries)!=expected:raise ValueError('artifact entry count mismatch')
            return inventory_capacity(entries,size)

def disk_budget(directory,required):
    if shutil.disk_usage(directory).free<required+DISK_RESERVE:raise ValueError('insufficient disk budget for inventory')

def artifact_directory_preflight(stream,entry_limit=None,directory_limit=None,transport_limit=None):
    """Bound central-directory allocations before constructing ZipFile, including ZIP64."""
    entry_limit=ARTIFACT_ENTRY_LIMIT if entry_limit is None else entry_limit
    directory_limit=ARTIFACT_DIRECTORY_LIMIT if directory_limit is None else directory_limit
    transport_limit=ARTIFACT_LIMIT if transport_limit is None else transport_limit
    stream.seek(0,2);size=stream.tell()
    if size>transport_limit:raise ValueError('artifact transport exceeds cap')
    stream.seek(max(0,size-65557));tail=stream.read(65557);index=tail.rfind(b'PK\x05\x06')
    if index<0 or index+22>len(tail):raise ValueError('invalid artifact end record')
    end=max(0,size-65557)+index
    _,disk,start_disk,on_disk,count,cd_size,offset,comment=struct.unpack('<4s4H2LH',tail[index:index+22])
    if end+22+comment!=size or disk or start_disk or on_disk!=count:raise ValueError('invalid/multidisk artifact')
    if count==65535 or cd_size==0xffffffff or offset==0xffffffff:
        if end<20:raise ValueError('invalid ZIP64 locator')
        stream.seek(end-20);locator=stream.read(20)
        signature,record_disk,position,disks=struct.unpack('<4sLQL',locator)
        if signature!=b'PK\x06\x07' or record_disk or disks!=1:raise ValueError('invalid ZIP64 locator')
        if not 0<=position<=end-76:raise ValueError('invalid ZIP64 position')
        stream.seek(position);record=stream.read(56)
        signature,length,_,_,disk,start_disk,on_disk,count,cd_size,offset=struct.unpack('<4sQ2H2L4Q',record)
        if (signature!=b'PK\x06\x06' or not 44<=length<=4096 or position+12+length!=end-20
            or disk or start_disk or on_disk!=count):raise ValueError('invalid ZIP64 end record')
        end=position
    if count>entry_limit:raise ValueError('artifact entry count exceeds cap')
    if cd_size>directory_limit or offset+cd_size>end:raise ValueError('artifact central directory exceeds cap')
    stream.seek(0)
    return count

def extract_artifact(raw,destination):
    destination=reject_link_ancestors(destination).resolve();created=[];directories=[]
    with (io.BytesIO(raw) if isinstance(raw,bytes) else Path(raw).open('rb')) as stream:
      expected=artifact_directory_preflight(stream)
      size=stream.seek(0,2);stream.seek(0)
      with zipfile.ZipFile(stream) as archive:
        entries=archive.infolist();names=[e.filename for e in entries]
        capacity=inventory_capacity(entries,size);total=capacity['expanded_bytes']
        if len(entries)!=expected or len(names)!=len({n.casefold() for n in names}) or total>ARTIFACT_EXPANDED_LIMIT:
            raise ValueError('duplicate paths or archive exceeds cap')
        for entry in entries:
            name=entry.filename
            if (len(name.rstrip('/').split('/'))>ARTIFACT_PATH_DEPTH_LIMIT or len(name.encode('utf-8'))>ARTIFACT_PATH_BYTES_LIMIT
                or '\\' in name or ':' in name or name.startswith('/') or
                any(p in ('','.','..') for p in name.rstrip('/').split('/')) or
                (entry.external_attr>>16)&0o170000 not in (0,0o100000,0o040000)
                or entry.flag_bits&1 or entry.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED)):
                raise ValueError('unsafe artifact path/type')
            path=destination/name
            if not path.resolve().is_relative_to(destination) or path.exists() or path.is_symlink():raise ValueError('artifact escape or existing path')
        disk_budget(destination if destination.exists() else destination.parent,total)
        try:
          for entry in entries:
            if entry.is_dir():continue
            path=destination/entry.filename
            missing=[];parent=path.parent
            while not parent.exists():missing.append(parent);parent=parent.parent
            for parent in reversed(missing):parent.mkdir();directories.append(parent)
            with archive.open(entry) as source,path.open('xb') as target:
                created.append(path);count=0
                for chunk in iter(lambda:source.read(1024*1024),b''):
                    count+=len(chunk)
                    if count>entry.file_size:raise ValueError('artifact expanded bytes exceed declaration')
                    target.write(chunk)
                if count!=entry.file_size:raise ValueError('artifact expanded size mismatch')
        except BaseException:
          for path in reversed(created):path.unlink(missing_ok=True)
          for directory in reversed(directories):directory.rmdir()
          raise

def validate_staged_packages(destination):
    # The unchanged verifier parses inner ZIPs; bound those allocations first.
    for path in Path(destination).rglob('*'):
        if not path.is_file() or path.suffix.lower()!='.zip':continue
        with path.open('rb') as stream:
            expected=artifact_directory_preflight(stream,INVENTORY_MEMBER_LIMIT,INVENTORY_METADATA_LIMIT,STORED_ZIP_LIMIT)
            with zipfile.ZipFile(stream) as archive:
                entries=archive.infolist();names=[e.filename for e in entries]
                if len(entries)!=expected or len(names)!=len(set(names)):raise ValueError('duplicate inner inventory members')
                if sum(e.file_size for e in entries if e.filename!='bundle_manifest.json')>LIMIT:
                    raise ValueError('inventory source bytes exceed frozen cap')
                if archive.getinfo('bundle_manifest.json').file_size>INVENTORY_METADATA_LIMIT:
                    raise ValueError('inventory manifest exceeds metadata cap')

def github_response(path):
    # Never forward Authorization across artifact-download redirects.
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
            if redirected and urlparse(newurl).hostname!=urlparse(req.full_url).hostname:
                redirected.remove_header('Authorization')
            return redirected
    url='https://api.github.com/repos/lzwislam-collab/doge-data-collector'+path
    headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'DOGE-OSS-retention'}
    return urllib.request.build_opener(Redirect()).open(urllib.request.Request(url,headers=headers),timeout=90)

def github(path,binary=False):
    with github_response(path) as response:
        raw=response.read(256*1024*1024+1)
    if len(raw)>256*1024*1024:raise ValueError('GitHub response exceeds cap')
    return raw if binary else json.loads(raw)

def download_artifact(artifact,destination):
    """Stream a verified outer ZIP without relaxing any stored evidence cap."""
    size=artifact.get('size_in_bytes');expected=artifact.get('digest','').removeprefix('sha256:')
    if (isinstance(size,bool) or not isinstance(size,int) or not 0<size<=ARTIFACT_LIMIT
        or not re.fullmatch('[a-f0-9]{64}',expected)):
        raise ValueError('invalid artifact size or digest')
    destination=reject_link_ancestors(destination);disk_budget(destination.parent,size)
    created=False;count=0;hasher=hashlib.sha256()
    try:
        with destination.open('xb') as target:
            created=True
            with github_response('/actions/artifacts/'+str(int(artifact['id']))+'/zip') as response:
                for chunk in iter(lambda:response.read(1024*1024),b''):
                    count+=len(chunk)
                    if count>size:raise ValueError('artifact transport exceeds declared size')
                    target.write(chunk);hasher.update(chunk)
            if count!=size or hasher.hexdigest()!=expected:
                raise ValueError('artifact transport size or digest mismatch')
        return destination
    except BaseException:
        if created:destination.unlink(missing_ok=True)
        raise

def stage(root,force=False):
    root=reject_link_ancestors(root).resolve();base=root/'data/pit_retention_preparation_v1'
    prior=read_inventory_json(base/'latest.json');marker=read_inventory_json(base/'artifact_publication.json')
    sha=prior['bundle_sha256']
    if not re.fullmatch('[a-f0-9]{64}',sha) or marker['bundle_sha256']!=sha:raise ValueError('publication binding mismatch')
    output=reject_link_ancestors(root/'oss-retention-output');output.mkdir(exist_ok=True)
    reject_link_ancestors(output/'source.json');reject_link_ancestors(output/'status.json')
    previous=reject_link_ancestors(root/'audit/oss_retention'/f'{sha}.json')
    if previous.exists() and not force:
        old=read_inventory_json(previous)
        if old.get('status')!='LOCKED_DAILY_INVENTORY_VERIFIED' or old.get('bundle_sha256')!=sha:
            raise ValueError('invalid previous receipt')
        write_json_atomic(output/'status.json',{'status':'SKIP_PREVIOUSLY_VERIFIED_INVENTORY','bundle_sha256':sha,
            'fresh_object_readback_performed':False,'previous_receipt':str(previous.relative_to(root)),
            'independent_custodian_verified':False})
        return False
    artifact=github('/actions/artifacts/'+str(int(marker['artifact_id'])))
    run=github('/actions/runs/'+str(int(marker['run_id'])))
    if (artifact.get('expired') or artifact['workflow_run']['id']!=int(marker['run_id']) or
        run['head_branch']!='main' or run['event']=='pull_request' or run['repository']['full_name']!='lzwislam-collab/doge-data-collector' or
        run['path']!='.github/workflows/okx_hourly_extreme_history.yml' or
        artifact.get('digest','').removeprefix('sha256:')!=marker['artifact_archive_sha256']):
        raise ValueError('source artifact provenance mismatch')
    stage_root=reject_link_ancestors(root/'oss-inventory-stage')
    if stage_root.exists():raise ValueError('existing staging directory; refusing overwrite')
    with tempfile.TemporaryDirectory(prefix='oss-artifact-',dir=root) as temporary:
        private_root=Path(temporary)/'stage'
        destination=private_root/'data/pit_retention_preparation_v1'
        destination.mkdir(parents=True)
        archive=download_artifact(artifact,Path(temporary)/'inventory.zip')
        capacity=artifact_capacity(archive)
        # The archive already occupies disk. Require only the additional expanded
        # bytes plus reserve, rather than double-counting the archive as free space.
        disk_budget(destination,capacity['expanded_bytes'])
        extract_artifact(archive,destination)
        copied=read_inventory_json(destination/'latest.json')
        if copied['bundle_sha256']!=sha:raise ValueError('artifact inventory mismatch')
        validate_inventory_references(private_root,copied)
        validate_staged_packages(destination)
        import pit_retention_preparation as pit
        pit.verify_inventory_bundle(private_root,copied)
        if copied.get('bundle_kind') is None:verify_single_inventory(private_root,copied)
        head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
        prepared_report=Path(temporary)/'publication-source.json'
        prepared_report.write_text(json.dumps({'source_commit':head,'artifact_source_commit':run['head_sha'],'artifact_id':artifact['id'],
            'artifact_digest':artifact['digest'],'source_run_id':run['id'],'source_run_url':run['html_url'],
            'bundle_sha256':sha,'inventory_objects':copied['objects'],'prepared_at_utc':copied['prepared_at_utc'],
            'inventory_capacity':capacity},indent=2)+'\n')
        publish_inventory(private_root,stage_root,prepared_report,output/'source.json')
    return True

def write_json_atomic(path,value):
    """Preserve an existing receipt until its complete replacement is durable."""
    path=reject_link_ancestors(path)
    raw=(json.dumps(value,indent=2)+'\n').encode('utf-8')
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(prefix='.receipt-',suffix='.tmp',dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name)
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        reject_link_ancestors(path)
        temporary.replace(path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)

def record_upload_failure(root,exc):
    try:
        folder=reject_link_ancestors(Path(root)/'oss-retention-output');folder.mkdir(exist_ok=True)
        write_json_atomic(folder/'failure.json',{'status':'NOT_VERIFIED','error_type':type(exc).__name__,
            'provider_code':str(getattr(exc,'code','')),'request_id':str(getattr(exc,'request_id','')),
            'locked_bytes_verified':False})
        return True
    except Exception:
        return False

def upload_inventory(root):
    checked=prepare_upload_inventory(root)
    root=checked['root'];stage_root=checked['stage_root'];prior=checked['prior'];output=checked['output']
    import oss2
    token=os.environ['ALIBABA_CLOUD_SECURITY_TOKEN']
    if not token:raise ValueError('short-lived STS token required')
    from aliyunsdkcore.request import CommonRequest
    identity_request=CommonRequest();identity_request.set_domain('sts.aliyuncs.com')
    identity_request.set_version('2015-04-01');identity_request.set_action_name('GetCallerIdentity')
    identity_request.set_accept_format('json');identity_request.set_protocol_type('https')
    client=identity_client(os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],token)
    identity=json.loads(client.do_action_with_exception(identity_request))
    if str(identity.get('AccountId'))!=OWNER or 'doge-evidence-github-main' not in identity.get('Arn',''):
        raise ValueError('unexpected actual caller identity')
    bucket=oss2.Bucket(oss2.StsAuth(os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],token),
                       ENDPOINT,BUCKET,connect_timeout=30)
    lock=check_bucket(bucket)
    raw=checked['raw'];parts=checked['parts']
    receipts=[]
    for part in parts:
        payload=read_bounded(stage_root/part['bundle_path'],STORED_ZIP_LIMIT)
        if digest(payload)!=part['bundle_sha256']:raise ValueError('part changed')
        receipts.append(upload_one(bucket,payload,'.zip',lock))
    receipts.append(upload_one(bucket,raw,'.json' if parts else '.zip',lock))
    final_lock=check_bucket(bucket)
    if final_lock!=lock:raise ValueError('bucket metadata changed during readback')
    report=dict(checked['source'],status='LOCKED_DAILY_INVENTORY_VERIFIED',
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
    folder=root/'audit/oss_retention';folder.mkdir(parents=True,exist_ok=True)
    write_json_atomic(folder/(prior['bundle_sha256']+'.json'),report)
    write_json_atomic(folder/'latest.json',report)
    write_json_atomic(output/'receipt.json',report)
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
            recorded=record_upload_failure(root,exc)
            raise SystemExit('OSS upload/readback rejected; '+('sanitized details in failure.json' if recorded else 'failure details unavailable'))

"""User-owned COMPLIANCE version readback. Custody separation is not asserted.

No resource creation, IAM changes or uploads unless upload() is explicitly called.
Tests inject fake clients; their success never constitutes actual cloud proof.
"""
import base64
import hashlib
import re
from datetime import datetime,timedelta,timezone

LIMIT=256*1024*1024


def utc(value):
    value=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    if not isinstance(value,datetime) or value.tzinfo is None:raise ValueError('explicit UTC required')
    return value.astimezone(timezone.utc)


def clients(config):
    import botocore.session
    from botocore.config import Config
    session=botocore.session.get_session()
    options={'region_name':config.get('region','ap-northeast-1'),
             'config':Config(connect_timeout=10,read_timeout=30,retries={'max_attempts':2})}
    return session.create_client('s3',**options),session.create_client('sts',**options)


def checked_config(config,expected):
    if config.get('provider')!='aws_s3_object_lock':raise ValueError('unsupported provider')
    for name in ('bucket_owner_account_id','collector_account_id'):
        if not re.fullmatch(r'[0-9]{12}',str(config.get(name,''))):raise ValueError('invalid account identity')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]',config.get('bucket','')):raise ValueError('invalid bucket')
    if not re.fullmatch('[a-f0-9]{64}',expected):raise ValueError('invalid digest')
    if config.get('key')!='doge/evidence/'+expected+'.zip':raise ValueError('object key not bound to bytes')
    until=utc(config['required_until_utc'])
    if until<utc('2028-03-01T00:00:00Z'):raise ValueError('retention must cover fixed reviews plus audit period')
    return until


def verify(config,expected,now=None,client=None,identity=None,target_at=None):
    started=utc(now or datetime.now(timezone.utc));supplied=now is not None
    result={'status':'NOT_CONFIGURED','custody':'USER_MANAGED',
            'locked_retention_verified':False,'independent_custodian_verified':False,
            'locked_before_target_verified':False,'production_allowed':False,
            'resource_created':False,'delete_probe_performed':False,'verified_at_utc':started.isoformat()}
    if not config:return result
    result['status']='NOT_VERIFIED';body=None
    try:
        required=checked_config(config,expected)
        version=config.get('version_id')
        if not isinstance(version,str) or not version or version=='null':raise ValueError('pinned non-null version required')
        if client is None or identity is None:client,identity=clients(config)
        if identity.get_caller_identity()['Account']!=config['collector_account_id']:raise ValueError('actual collector identity mismatch')
        bucket={'Bucket':config['bucket'],'ExpectedBucketOwner':config['bucket_owner_account_id']}
        if client.get_object_lock_configuration(**bucket)['ObjectLockConfiguration']['ObjectLockEnabled']!='Enabled':raise ValueError('Object Lock disabled')
        if client.get_bucket_versioning(**bucket).get('Status')!='Enabled':raise ValueError('versioning disabled')
        params=dict(bucket,Key=config['key'],VersionId=version)
        retention=client.get_object_retention(**params)['Retention'];until=utc(retention['RetainUntilDate'])
        if retention['Mode']!='COMPLIANCE' or until<required or until<=started:raise ValueError('insufficient COMPLIANCE retention')
        response=client.get_object(**params);body=response['Body'];digest=hashlib.sha256();size=0
        for chunk in iter(lambda:body.read(1024*1024),b''):
            size+=len(chunk)
            if size>LIMIT:raise ValueError('object exceeds evidence cap')
            digest.update(chunk)
        modified=utc(response['LastModified']);finished=started if supplied else datetime.now(timezone.utc)
        if response.get('VersionId')!=version or digest.hexdigest()!=expected or modified>finished or until<=finished:
            raise ValueError('version/hash/server clock mismatch')
        result.update(status='LOCKED_BYTES_VERIFIED_USER_MANAGED',locked_retention_verified=True,
                      version_id=version,readback_sha256=digest.hexdigest(),byte_count=size,
                      server_last_modified_utc=modified.isoformat(),retain_until_utc=until.isoformat(),
                      verified_at_utc=finished.isoformat(),
                      locked_before_target_verified=bool(target_at is not None and modified<=finished<utc(target_at)),
                      limitations=['Custody is user managed; account IDs do not prove independent administrators.',
                                   'Current lock cannot prove when it first became effective without an earlier verification.',
                                   'No original prediction or promotion is authorized by storage verification.'])
    except Exception as exc:result['error']=type(exc).__name__+': '+str(exc)[:500]
    finally:
        if body is not None:body.close()
    return result


def upload(config,raw,client=None,identity=None):
    """Opt-in immutable upload to an ALREADY configured user-owned resource."""
    if not isinstance(raw,bytes) or not 0<len(raw)<=LIMIT:raise ValueError('invalid evidence bytes')
    expected=hashlib.sha256(raw).hexdigest();checked_config(config,expected)
    if client is None or identity is None:client,identity=clients(config)
    if identity.get_caller_identity()['Account']!=config['collector_account_id']:raise ValueError('actual collector identity mismatch')
    bucket={'Bucket':config['bucket'],'ExpectedBucketOwner':config['bucket_owner_account_id']}
    lock=client.get_object_lock_configuration(**bucket)['ObjectLockConfiguration']
    if lock.get('ObjectLockEnabled')!='Enabled' or client.get_bucket_versioning(**bucket).get('Status')!='Enabled':raise ValueError('locked versioned bucket required')
    default=lock.get('Rule',{}).get('DefaultRetention',{})
    if default.get('Mode')!='COMPLIANCE' or default.get('Days',0)<730:raise ValueError('approved 730-day default COMPLIANCE lock required')
    response=client.put_object(**bucket,Key=config['key'],Body=raw,ContentType='application/zip',
                              IfNoneMatch='*',ChecksumSHA256=base64.b64encode(hashlib.sha256(raw).digest()).decode(),
                              ObjectLockMode='COMPLIANCE',
                              ObjectLockRetainUntilDate=max(utc(config['required_until_utc']),datetime.now(timezone.utc)+timedelta(days=730)))
    pinned=dict(config,version_id=response.get('VersionId'))
    result=verify(pinned,expected,client=client,identity=identity)
    result['upload_performed']=True;result['receipt_config']=pinned
    if not result['locked_retention_verified']:raise ValueError('uploaded version did not pass independent API readback')
    return result

#!/usr/bin/env python3
"""Export evidence metadata and verify an existing AWS S3 Object Lock receipt.

No bucket creation, upload, provisioning, or credential output. A local hash,
environment flag, or unverified receipt is never proof of independent WORM.
"""
import argparse
import hashlib
import json
import uuid
from source_evidence_snapshot import storage_path, DEFAULT_STORAGE_NAMESPACE, PLUS_STORAGE_NAMESPACE
from datetime import datetime, timezone, timedelta
from pathlib import Path


def verify_receipt(config, expected_sha256, client=None, now=None):
    now = now or datetime.now(timezone.utc)
    result = {'status': 'NOT_CONFIGURED', 'independent_retention_verified': False,
              'expected_sha256': expected_sha256, 'verified_at_utc': now.isoformat()}
    required = ('bucket', 'key', 'version_id')
    if not config or config.get('provider') != 'aws_s3_object_lock' or not all(config.get(k) for k in required):
        result['reason'] = 'No supported pinned external retention object configured'
        return result
    result.update(provider='aws_s3_object_lock', **{k: config[k] for k in required})
    if config['version_id'] == 'null':
        result.update(status='NOT_VERIFIED', reason='A non-null object version is required')
        return result
    if client is None:
        try:
            import botocore.session
            client = botocore.session.get_session().create_client('s3', region_name=config.get('region'))
        except ImportError:
            result.update(status='UNAVAILABLE', reason='Optional botocore not installed; no dependencies installed')
            return result
        except Exception as exc:
            result.update(status='NOT_VERIFIED', reason='S3 client unavailable: '+type(exc).__name__)
            return result
    try:
        lock = client.get_object_lock_configuration(Bucket=config['bucket'])
        params = {'Bucket': config['bucket'], 'Key': config['key'], 'VersionId': config['version_id']}
        retention = client.get_object_retention(**params)['Retention']
        until = retention['RetainUntilDate']
        if isinstance(until, str):
            until = datetime.fromisoformat(until.replace('Z', '+00:00'))
        if until.tzinfo is None or now.tzinfo is None:
            raise ValueError('retention timestamps require timezone')
        response = client.get_object(**params)
        body = response['Body']
        sha = hashlib.sha256()
        try:
            for chunk in iter(lambda: body.read(1024*1024), b''):
                sha.update(chunk)
        finally:
            body.close()
        readback = sha.hexdigest()
        result.update(readback_sha256=readback, object_version=response.get('VersionId'),
                      retention_mode=retention.get('Mode'), retain_until_utc=until.astimezone(timezone.utc).isoformat(),
                      bucket_object_lock_enabled=lock.get('ObjectLockConfiguration', {}).get('ObjectLockEnabled')=='Enabled')
        valid = (result['bucket_object_lock_enabled'] and retention.get('Mode')=='COMPLIANCE'
                 and until > now and response.get('VersionId')==config['version_id'] and readback==expected_sha256)
        result.update(status='VERIFIED' if valid else 'NOT_VERIFIED', independent_retention_verified=valid,
                      reason='Pinned version readback and active COMPLIANCE retention verified' if valid else 'Lock, active COMPLIANCE mode, pinned version, or readback hash check failed')
    except Exception as exc:
        result.update(status='NOT_VERIFIED', reason='External verification failed: '+type(exc).__name__)
    return result


def export_manifest(root, manifest, config=None, client=None, storage_namespace=None):
    root, manifest = Path(root).resolve(), Path(manifest).resolve()
    if storage_namespace is None:
        matches = [n for n in (DEFAULT_STORAGE_NAMESPACE, PLUS_STORAGE_NAMESPACE)
                   if manifest.is_relative_to((root/n/'manifests').resolve())]
        if len(matches) != 1:
            raise ValueError('expected supplemental snapshot manifest')
        storage_namespace = matches[0]
    storage, namespace = storage_path(root, storage_namespace)
    if not manifest.is_relative_to(storage/'manifests'):
        raise ValueError('expected supplemental snapshot manifest')
    objects = []
    visited = set()
    current = manifest
    expected_manifest_sha = None
    while current is not None:
        if not current.is_relative_to(storage/'manifests') or current in visited:
            raise ValueError('unsafe or cyclic manifest ancestry')
        visited.add(current)
        raw = current.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if expected_manifest_sha is not None and sha!=expected_manifest_sha:
            raise ValueError('predecessor manifest hash mismatch')
        snapshot = json.loads(raw)
        if snapshot.get('storage_namespace', DEFAULT_STORAGE_NAMESPACE) != namespace:
            raise ValueError('manifest namespace mismatch')
        objects.append({'local_path': current.relative_to(root).as_posix(), 'sha256': sha,
                        'kind': 'snapshot_manifest' if current==manifest else 'ancestor_manifest'})
        evidence_items = list(snapshot['sources'].values()) + list(snapshot.get('supporting_evidence', {}).values())
        for item in evidence_items:
            if item.get('sha256'):
                path = (root/item['archive_path']).resolve()
                if not path.is_relative_to(storage/'blobs'):
                    raise ValueError('unsafe evidence archive path')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                    raise ValueError('evidence content hash mismatch')
                if not any(o['local_path']==item['archive_path'] for o in objects):
                    objects.append({'local_path': item['archive_path'], 'sha256': item['sha256'], 'kind': 'evidence_bytes'})
        predecessor = snapshot.get('previous_manifest_path')
        expected_manifest_sha = snapshot.get('previous_manifest_sha256')
        if predecessor and not expected_manifest_sha:
            raise ValueError('predecessor manifest hash missing')
        current = (root/predecessor).resolve() if predecessor else None
    receipts = (config or {}).get('objects', {})
    for obj in objects:
        obj['retention_receipt'] = verify_receipt(receipts.get(obj['local_path']), obj['sha256'], client=client)
    verified = all(o['retention_receipt']['independent_retention_verified'] for o in objects)
    configured = any(o['retention_receipt']['status']!='NOT_CONFIGURED' for o in objects)
    result = {'schema_version': 1, 'storage_namespace': namespace,
              'version_id': snapshot.get('version_id', 'LEGACY_EVIDENCE_SNAPSHOT'), 'exported_at_utc': datetime.now(timezone.utc).isoformat(),
              'status': 'VERIFIED' if verified else 'NOT_VERIFIED' if configured else 'NOT_CONFIGURED',
              'independent_retention_verified': verified, 'objects': objects,
              'requirements': ['Each manifest and content blob requires its own pinned non-null S3 version',
                               'Read-only API verification of enabled Object Lock, active COMPLIANCE retention and exact SHA256 readback',
                               'Local evidence is mutable by a local administrator; hashes alone are not WORM']}
    out = storage/'retention_exports'/f'{uuid.uuid4().hex}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return out, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--storage-namespace', help='Optional storage root; inferred for supported namespaces')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--receipt-config', type=Path, help='JSON objects map: local_path -> provider,bucket,key,version_id,optional region')
    args = parser.parse_args()
    config = json.loads(args.receipt_config.read_text(encoding='utf-8')) if args.receipt_config else None
    path, result = export_manifest(args.root, args.manifest, config, storage_namespace=args.storage_namespace)
    print(json.dumps({'status': result['status'], 'export_path': str(path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

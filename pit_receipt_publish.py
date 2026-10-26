"""Publish one validated PIT artifact receipt from a private clean worktree.

The collector's index, dirty files and HEAD are never stashed, reset or committed.
No OSS operations are performed. A changed remote inventory fails closed.
"""
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
import pit_retention_preparation as prep

MARKER = prep.BASE + '/artifact_publication.json'
LATEST = prep.BASE + '/latest.json'
BINDING = ('bundle_sha256', 'bundle_path', 'run_id', 'implementation_sha256',
           'prepared_at_utc', 'bundle_kind', 'part_count', 'objects', 'byte_count')

def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

def validate(root, marker, run_id):
    latest = json.loads(prep.safe(root, LATEST).read_bytes())
    if (not re.fullmatch('[1-9][0-9]*', str(run_id))
        or latest.get('run_id') != str(run_id)
        or marker.get('run_id') != str(run_id)
        or latest.get('status') != 'NOT_CONFIGURED'
        or latest.get('generated_new_bundle') is not True
        or marker.get('status') != 'GITHUB_ARTIFACT_UPLOAD_RECORDED_NOT_WORM'
        or marker.get('independent_retention_verified') is not False
        or marker.get('remote_bundle_content_readback_verified') is not False
        or not re.fullmatch('[1-9][0-9]*', str(marker.get('artifact_id', '')))
        or not re.fullmatch('[a-f0-9]{64}', str(marker.get('artifact_archive_sha256', '')))):
        raise ValueError('invalid receipt source or action artifact binding')
    for name in ('bundle_sha256', 'implementation_sha256'):
        if (not re.fullmatch('[a-f0-9]{64}', str(marker.get(name, '')))
            or marker[name] != latest.get(name)):
            raise ValueError('receipt preparation digest mismatch')
    if marker['implementation_sha256'] != prep.implementation_hash():
        raise ValueError('receipt producer implementation mismatch')
    if prep.clock(marker['recorded_at_utc']) < prep.clock(latest['prepared_at_utc']):
        raise ValueError('receipt predates preparation')
    prep.verify_inventory_bundle(root, latest)
    return latest

def publish(root, run_id, attempts=5):
    root = Path(root).resolve()
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise ValueError('invalid publication retry count')
    # Git JSON text uses LF; do not let Windows checkout conversion affect
    # the exact committed/read-back receipt bytes. Source file stays untouched.
    payload = prep.safe(root, MARKER).read_bytes().replace(b'\r\n', b'\n')
    marker = json.loads(payload)
    latest = validate(root, marker, run_id)
    for attempt in range(attempts):
        # Fetch changes only remote refs, never the collector worktree/index.
        git(root, 'fetch', '--no-tags', 'origin', 'refs/heads/main')
        base = git(root, 'rev-parse', 'FETCH_HEAD').decode().strip()
        with tempfile.TemporaryDirectory(prefix='doge-pit-receipt-') as folder:
            work = Path(folder) / 'publish'
            git(root, 'worktree', 'add', '--detach', '--no-checkout', str(work), base)
            try:
                # Keep the complete base tree in this private index, but
                # materialize only binding metadata, never inventory payloads.
                git(work, 'read-tree', base)
                git(work, 'checkout-index', '--force', '--', LATEST,
                    'pit_retention_preparation.py')
                remote_latest = json.loads(prep.safe(work, LATEST).read_bytes())
                if any(remote_latest.get(k) != latest.get(k) for k in BINDING):
                    raise ValueError('remote inventory source binding changed')
                producer = (work / 'pit_retention_preparation.py').read_bytes()
                if hashlib.sha256(producer.replace(b'\r\n', b'\n')).hexdigest() != marker['implementation_sha256']:
                    raise ValueError('remote producer implementation changed')
                path = prep.safe(work, MARKER)
                path.parent.mkdir(parents=True, exist_ok=True)
                if git(work, 'ls-tree', '--name-only', base, '--', MARKER):
                    prior_raw = git(work, 'show', base + ':' + MARKER)
                    if prior_raw == payload:
                        return {'status': 'ALREADY_PUBLISHED', 'commit_sha': base}
                    prior = json.loads(prior_raw)
                    if prior.get('bundle_sha256') == marker['bundle_sha256']:
                        raise ValueError('conflicting receipt for same inventory')
                path.write_bytes(payload)
                git(work, 'add', '-f', '--', MARKER)
                changed = git(work, 'diff', '--cached', '--name-only', '-z').split(b'\0')
                if changed != [MARKER.encode(), b'']:
                    raise ValueError('publication includes non-receipt paths')
                git(work, '-c', 'user.name=github-actions[bot]',
                    '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                    'commit', '-m', 'audit: record completed daily PIT package artifact')
                commit = git(work, 'rev-parse', 'HEAD').decode().strip()
                try:
                    git(work, 'push', 'origin', 'HEAD:refs/heads/main')
                except subprocess.CalledProcessError:
                    if attempt + 1 == attempts:
                        raise
                    continue
                # Read back the branch and exact marker before reporting success.
                git(work, 'fetch', '--no-tags', 'origin', 'refs/heads/main')
                if git(work, 'show', 'FETCH_HEAD:' + MARKER) != payload:
                    raise ValueError('remote published receipt readback mismatch')
                return {'status': 'PUBLISHED', 'commit_sha': commit}
            finally:
                git(root, 'worktree', 'remove', '--force', str(work))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.root, args.run_id)))

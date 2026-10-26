"""Publish only the completed artifact marker without touching drained capture bytes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

MARKER = 'data/pit_retention_preparation_v1/artifact_publication.json'
PREPARATION = 'data/pit_retention_preparation_v1/latest.json'


def publish_marker(root):
    root = Path(root)
    raw = (root / MARKER).read_bytes()
    preparation = (root / PREPARATION).read_bytes()
    def git(*args, env=None, data=None):
        return subprocess.run(['git', *args], cwd=root, env=env, input=data,
                              check=True, capture_output=True).stdout
    # A separate index is based on the current remote tree, never the dirty runner
    # checkout. No reset, stash, rebase, checkout, or force push can lose capture.
    with tempfile.TemporaryDirectory(prefix='doge-pit-index-') as temp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / 'index'))
        blob = git('hash-object', '-w', '--stdin', data=raw).decode().strip()
        for attempt in range(5):
            git('fetch', 'origin', 'main')
            parent = git('rev-parse', 'FETCH_HEAD').decode().strip()
            if git('show', parent + ':' + PREPARATION) != preparation:
                raise ValueError('remote PIT preparation differs; refusing marker publication')
            git('read-tree', parent, env=env)
            git('update-index', '--add', '--cacheinfo', '100644', blob, MARKER, env=env)
            tree = git('write-tree', env=env).decode().strip()
            parent_tree = git('rev-parse', parent + '^{tree}').decode().strip()
            if tree != parent_tree:
                commit = git('commit-tree', tree, '-p', parent,
                             data=b'audit: record completed daily PIT package artifact\n').decode().strip()
                try:
                    git('push', 'origin', commit + ':refs/heads/main')
                except subprocess.CalledProcessError:
                    if attempt == 4:
                        raise
                    time.sleep(4)
                    continue
            git('fetch', 'origin', 'main')
            if git('show', 'FETCH_HEAD:' + MARKER) != raw:
                raise ValueError('PIT artifact marker exact remote readback mismatch')
            return {'status': 'verified_remote_readback',
                    'remote_head': git('rev-parse', 'FETCH_HEAD').decode().strip(),
                    'marker_sha256': hashlib.sha256(raw).hexdigest()}
    raise RuntimeError('PIT artifact marker was not published')


if __name__ == '__main__':
    print(json.dumps(publish_marker(Path(__file__).resolve().parent)))

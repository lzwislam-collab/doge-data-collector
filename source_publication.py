"""Publish only one source's files and prove their bytes exist on remote main.

No dispatch, prediction, scoring, checkpoint reset or arbitrary path selection.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import subprocess
import time

SOURCE_FILES = {
    'onchain': ('data/onchain/latest.json', 'data/onchain/daily.csv'),
    'liquidations_okx': ('data/liquidations/latest.json', 'data/liquidations/events.csv'),
    'liquidity': ('data/liquidity/latest.json', 'data/liquidity/hourly_snapshots.csv'),
    'etf': ('data/etf/latest.json', 'data/etf/daily_flows.csv', 'data/etf/revisions.csv'),
    'macro': ('data/macro/latest.json', 'data/macro/observations.csv', 'data/macro/revisions.csv'),
    'whale_confirmed_blocks': tuple('data/whales_blocks/'+p for p in ('latest.json', 'events.csv', 'checkpoint.json', 'pending_block.json')),
    'quality': ('data/quality/latest.json',),
}


def publish(source, root=Path(__file__).resolve().parent, *, attempts=5, collector_outcome='success'):
    if source not in SOURCE_FILES or not 1<=attempts<=5 or collector_outcome not in ('success','failure','skipped','cancelled'):
        raise ValueError('unknown source or invalid attempts')
    root=Path(root)
    paths=list(SOURCE_FILES[source])
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.STDOUT)
    def staged():return set(git('diff', '--cached', '--name-only').decode().splitlines())
    # Keep the only valid deletion narrow: a fully completed pending block.
    optional='data/whales_blocks/pending_block.json'
    for path in paths:
        if path!=optional and not (root/path).is_file():
            raise ValueError('required source file missing: '+path)
    if source in ('liquidations_okx','liquidity'):
        meta=json.loads((root/paths[0]).read_bytes())
        prefix='data/'+('liquidations' if source=='liquidations_okx' else 'liquidity')+'/raw/'
        manifest=meta.get('raw_evidence',[])
        if not isinstance(manifest,list):raise ValueError('invalid raw evidence manifest')
        for item in manifest:
            path=item.get('path','')
            parts=PurePosixPath(path).parts
            if (not path.startswith(prefix) or '\\' in path or ':' in path
                or '..' in parts or str(PurePosixPath(path))!=path):
                raise ValueError('raw evidence outside source prefix')
            file=root/path
            if (not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(root.resolve())
                or not file.resolve().is_relative_to((root/prefix).resolve())):
                raise ValueError('raw evidence missing or unsafe')
            if hashlib.sha256(file.read_bytes()).hexdigest()!=item.get('sha256'):
                raise ValueError('raw evidence hash mismatch')
            if path in paths:raise ValueError('duplicate raw evidence path')
            paths.append(path)
        snapshot=meta.get('snapshot_evidence')
        if snapshot:
            # Snapshot is an independent immutable copy, not a recursive self-hash.
            path=snapshot.get('path','')
            if not isinstance(path,str) or not path.startswith(prefix) or '..' in PurePosixPath(path).parts or '\\' in path or ':' in path:
                raise ValueError('invalid raw snapshot path')
            file=root/path
            if (not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(root.resolve())
                or not file.resolve().is_relative_to((root/prefix).resolve())
                or hashlib.sha256(file.read_bytes()).hexdigest()!=snapshot.get('sha256')
                or json.loads(file.read_bytes())!={k:v for k,v in meta.items() if k!='snapshot_evidence'}):
                raise ValueError('raw snapshot missing or mismatched')
            paths.append(path)
    if staged()-set(paths):raise ValueError('unrelated staged files; publication refused')
    def outgoing_scope(base):
        for commit in git('rev-list',base+'..HEAD').decode().splitlines():
            changed=set(git('diff-tree','--root','--no-commit-id','--name-only','-r','-m',commit).decode().splitlines())
            if changed-set(paths):raise ValueError('unrelated outgoing commit; publication refused')
    git('fetch','origin','main')
    base=git('rev-parse','FETCH_HEAD').decode().strip()
    outgoing_scope(base)
    if source in ('macro','etf'):
        meta=json.loads((root/paths[0]).read_bytes())
        if collector_outcome!='success' or meta.get('status')!='ok':
            # A second atomic replace can fail after the first history file was
            # written. Keep remote history intact; failure artifacts retain the
            # local diagnostic/partial pair. Compare against remote, not index.
            for path in SOURCE_FILES[source][1:]:
                if not git('ls-tree',base,'--',path).strip() or git('show',base+':'+path)!=(root/path).read_bytes():
                    raise ValueError('failed collection changed history; publication refused: '+path)
    expected={}
    for path in paths:
        file=root/path
        if file.exists():
            expected[path]=file.read_bytes()
            git('add', '-f', '--', path)
        else:
            expected[path]=None
            if git('ls-files', '--', path).strip():git('add', '-u', '--', path)
    if staged()-set(paths):raise ValueError('unexpected staged path')
    changed=bool(staged())
    if changed:
        git('commit', '-m', 'data: publish '+source+' source observations')
        for attempt in range(attempts):
            # A conflict fails closed; never discard or replace source evidence.
            git('pull', '--rebase', 'origin', 'main')
            outgoing_scope(git('rev-parse','FETCH_HEAD').decode().strip())
            try:
                git('push', 'origin', 'HEAD:main')
                break
            except subprocess.CalledProcessError:
                if attempt+1==attempts:raise
                time.sleep(4)
    git('fetch', 'origin', 'main')
    head=git('rev-parse', 'FETCH_HEAD').decode().strip()
    verified={}
    for path, raw in expected.items():
        exists=bool(git('ls-tree', head, '--', path).strip())
        remote=git('show', head+':'+path) if exists else None
        if remote!=raw:raise ValueError('remote source bytes mismatch: '+path)
        verified[path]=hashlib.sha256(raw).hexdigest() if raw is not None else None
    return {'source':source, 'committed':changed, 'remote_head':head,
            'remote_bytes_verified':True, 'files':verified}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=tuple(SOURCE_FILES), required=True)
    args=parser.parse_args()
    print(json.dumps(publish(args.source,collector_outcome=os.environ.get('SOURCE_COLLECTION_OUTCOME','success')), sort_keys=True))


if __name__=='__main__':main()

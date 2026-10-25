"""Immutable archive inputs plus one live serial writer during upload."""
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

TREES = ('data', 'reports', 'system', 'publication-refresh-output')


def atomic_json(path, value):
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(value,stream)
            stream.flush()
            os.fsync(stream.fileno())
        # Publish a complete file without overwriting any existing receipt.
        os.link(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): result.update(block)
    return result.hexdigest()


def freeze(root, snapshot):
    root, snapshot = Path(root), Path(snapshot)
    snapshot.mkdir(exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    files = []
    for tree in TREES:
        source = root/tree
        if not source.exists(): continue
        if source.is_symlink(): raise ValueError('archive source link rejected')
        for path in sorted(source.rglob('*')):
            if path.is_symlink(): raise ValueError('archive source link rejected')
            if path.is_dir(): continue
            if not stat.S_ISREG(path.stat().st_mode): raise ValueError('archive special file rejected')
            files.append(path)
    total = sum(p.stat().st_size for p in files)
    if shutil.disk_usage(snapshot).free < total+512*1024*1024:
        raise ValueError('insufficient space for independent archive snapshot')
    hashes = {}
    for source in files:
        relative = source.relative_to(root).as_posix()
        target = snapshot/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        expected = digest(source)
        if digest(target) != expected: raise ValueError('archive snapshot copy mismatch')
        hashes[relative] = expected
    manifest = {'schema_version':1,'snapshot_copy_started_utc':started,
                'snapshot_copy_ended_utc':datetime.now(timezone.utc).isoformat(),
                'file_sha256':hashes,'source_bytes':total,'live_capture_backfill':False}
    with (snapshot/'manifest.json').open('x',encoding='utf-8') as stream: json.dump(manifest,stream)
    return manifest


def verify(snapshot):
    snapshot = Path(snapshot)
    manifest = json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))
    actual = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob('*') if p.is_file()}
    if actual != set(manifest['file_sha256']) | {'manifest.json'}: raise ValueError('archive snapshot file set changed')
    for relative, expected in manifest['file_sha256'].items():
        path = snapshot/relative
        if path.is_symlink() or digest(path) != expected: raise ValueError('archive snapshot bytes changed')
    return manifest


def trusted():
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('GITHUB_REF') != 'refs/heads/main'
        or os.environ.get('GITHUB_REPOSITORY') != 'lzwislam-collab/doge-data-collector'):
        raise ValueError('archive capture bridge requires trusted main workflow')


def start(root, snapshot, control):
    trusted()
    control.mkdir(exist_ok=False)
    freeze(root,snapshot)
    # Diagnostics/Git writes are complete; PIT publication waits until drain.
    # Upload actions only read the independent snapshot; the child is now the
    # sole producer. Its GITHUB_OUTPUT must not alter the upload step outputs.
    env = dict(os.environ, GITHUB_OUTPUT=str(control/'runtime.outputs'))
    with (control/'runtime.log').open('xb') as log:
        process = subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'worker',
            '--control',str(control)],cwd=root,env=env,stdout=log,stderr=log,start_new_session=True)
    with (control/'started.json').open('x',encoding='utf-8') as stream:
        json.dump({'pid':process.pid,'started_at_utc':datetime.now(timezone.utc).isoformat(),
                   'qualified_stop_required':os.environ.get('DOGE_ARCHIVE_QUALIFIED_DRAIN')=='1'},stream)


def worker(root, control):
    trusted()
    args = [sys.executable,str(root/'runtime_deadline_service.py'),
        '--duration-seconds','600','--stop-file',str(control/'stop')]
    if os.environ.get('DOGE_ARCHIVE_QUALIFIED_DRAIN') == '1': args.append('--require-qualified-stop')
    code = subprocess.call(args,cwd=root)
    atomic_json(control/'completed.json',{'exit_code':code,'ended_at_utc':datetime.now(timezone.utc).isoformat()})


def stop(root, snapshot, control):
    trusted()
    if not (control/'started.json').exists(): raise ValueError('archive capture was not started')
    (control/'stop').touch(exist_ok=True)
    started = json.loads((control/'started.json').read_text(encoding='utf-8'))
    required = started.get('qualified_stop_required') is True
    deadline = time.monotonic()+(420 if required else 180)
    while not (control/'completed.json').exists():
        if time.monotonic() >= deadline: raise TimeoutError('archive capture has not drained; no success claimed')
        time.sleep(1)
    result = json.loads((control/'completed.json').read_text(encoding='utf-8'))
    if result['exit_code'] != 0: raise ValueError('archive capture service failed')
    verify(snapshot)
    outputs = (control/'runtime.outputs').read_text(encoding='utf-8').splitlines()
    if not outputs or outputs[-1] != 'fatal=false': raise ValueError('archive capture final publication unsafe')
    formal = {}
    if required:
        formal = json.loads((root/'data/runtime_deadline_v1/latest.json').read_text(encoding='utf-8'))
        if formal.get('qualified_stop_satisfied') is not True:
            raise ValueError('archive capture ended without a new formally qualified book')
    receipt = dict(result, status='verified', snapshot_manifest_sha256=digest(snapshot/'manifest.json'),
                   qualified_stop_required=required,qualified_stop_satisfied=formal.get('qualified_stop_satisfied'),
                   last_qualified_book_sha256=formal.get('last_qualified_book_sha256'),
                   historical_backfill=False,economic_acceptance_passed=False)
    path = root/'data/runtime_deadline_v1'/('archive_bridge_'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as stream: json.dump(receipt,stream)
    from runtime_deadline_service import LiveAdapter
    adapter = LiveAdapter(root)
    adapter.action('publish')
    print(json.dumps(receipt))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode',choices=('start','worker','stop'))
    parser.add_argument('--snapshot',type=Path,default=Path('archive-snapshot'))
    parser.add_argument('--control',type=Path,required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    control = args.control.resolve()
    snapshot = args.snapshot.resolve()
    if args.mode == 'start': start(root,snapshot,control)
    elif args.mode == 'worker': worker(root,control)
    else: stop(root,snapshot,control)


if __name__ == '__main__': main()

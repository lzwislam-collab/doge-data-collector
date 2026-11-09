"""Build a verified local Git query index; all frozen history guards still run.

Git remains authoritative. This only changes objects/info commit-graph metadata.
Official semantics: https://git-scm.com/docs/git-commit-graph
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import time
import runtime_process

TOTAL_SECONDS = 100
WRITE_SECONDS = 60
VERIFY_SECONDS = 25
READ_SECONDS = 5
REDIRECTS = ('GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE', 'GIT_OBJECT_DIRECTORY',
             'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_INDEX_FILE', 'GIT_SHALLOW_FILE',
             'GIT_REPLACE_REF_BASE')


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _file_digest(path):
    if path.is_symlink():
        raise ValueError('unsafe repository metadata link')
    return _digest(path.read_bytes()) if path.exists() else None


def _snapshot(run, root):
    return {'head': run('head', 'rev-parse', '--verify', 'HEAD').strip(),
            'refs': run('refs', 'for-each-ref', '--sort=refname', '--format=%(refname) %(objectname)'),
            'tracked_status': run('tracked_status', 'status', '--porcelain=v1', '-z', '--untracked-files=no'),
            'diff': _digest(run('diff', 'diff', '--binary', '--no-ext-diff', '--no-textconv', 'HEAD').encode()),
            'staged_diff': _digest(run('staged_diff', 'diff', '--cached', '--binary', '--no-ext-diff',
                                       '--no-textconv', 'HEAD').encode()),
            'index': _file_digest(root/'.git/index'), 'config': _file_digest(root/'.git/config')}


def _graphs(root):
    """Require real changed-path chunks in every active split layer, not exit0 alone."""
    info = root/'.git/objects/info'
    chain = info/'commit-graphs/commit-graph-chain'
    if chain.exists():
        if chain.is_symlink() or chain.stat().st_size > 8192:
            raise ValueError('unsafe commit graph chain')
        names = chain.read_text(encoding='ascii').splitlines()
        if not names or len(names) > 64 or len(names) != len(set(names)) or any(
                not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', name) for name in names):
            raise ValueError('invalid commit graph chain')
        paths = [chain.parent/('graph-'+name+'.graph') for name in names]
    else:
        paths = [info/'commit-graph']
    result = []
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError('commit graph was not written')
        size = path.stat().st_size
        with path.open('rb') as stream:
            header = stream.read(8)
            if len(header) != 8 or header[:4] != b'CGPH' or header[4] != 1 or header[5] not in (1, 2):
                raise ValueError('invalid commit graph header')
            count, hash_bytes = header[6], 20 if header[5] == 1 else 32
            table = stream.read((count+1)*12)
        if len(table) != (count+1)*12 or not 1 <= count <= 64:
            raise ValueError('invalid commit graph chunks')
        entries = [struct.unpack('>4sQ', table[i*12:(i+1)*12]) for i in range(count+1)]
        ids = [item[0] for item in entries[:-1]]
        offsets = [item[1] for item in entries]
        if (entries[-1][0] != bytes(4) or len(ids) != len(set(ids))
                or offsets != sorted(offsets) or offsets[0] < 8+len(table)
                or offsets[-1] > size-hash_bytes or not {b'BIDX', b'BDAT'}.issubset(ids)):
            raise ValueError('verified changed-path Bloom chunks absent or invalid')
        chunks = {entries[i][0]: entries[i+1][1]-entries[i][1] for i in range(count)}
        if chunks[b'BIDX'] < 4 or chunks[b'BIDX'] % 4 or chunks[b'BDAT'] < 12:
            raise ValueError('empty changed-path Bloom index')
        result.append({'path': path.relative_to(root).as_posix(), 'bytes': size,
                       'bloom_chunks': ['BIDX', 'BDAT']})
    return result


def prepare(root, *, command=None, monotonic=None):
    """Owned commands share100s; failures/cancellation propagate, no success fallback."""
    root = Path(root).resolve()
    if not (root/'.git').is_dir() or (root/'.git').is_symlink():
        raise ValueError('ordinary full runner checkout required')
    if any(os.environ.get(key) for key in REDIRECTS):
        raise ValueError('Git repository redirection is not supported')
    for name in ('objects', 'objects/info', 'objects/info/commit-graph',
                 'objects/info/commit-graphs', 'objects/info/alternates', 'info/grafts'):
        path = root/'.git'/name
        if path.is_symlink():
            raise ValueError('shared or redirected repository metadata rejected')
    for name in ('objects/info/alternates', 'info/grafts'):
        path = root/'.git'/name
        if path.exists() and path.stat().st_size:
            raise ValueError('alternate objects or grafted history rejected')
    command = command or runtime_process.command
    monotonic = monotonic or time.monotonic
    start = monotonic()
    deadline = start+TOTAL_SECONDS
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
    phases = {}
    def run(phase, *args, cap=READ_SECONDS):
        left = deadline-monotonic()
        if left <= 0:
            raise TimeoutError('local history index overall budget exhausted')
        began = monotonic()
        try:
            return command(['git', '--no-optional-locks', *args], cwd=root, env=env,
                           timeout=min(cap, left))
        except BaseException as error:
            error.history_graph_phase = phase
            raise
        finally:
            phases[phase] = monotonic()-began
    layout = run('layout', 'rev-parse', '--is-shallow-repository', '--show-toplevel',
                 '--absolute-git-dir', '--git-common-dir').splitlines()
    if (len(layout) != 4 or layout[0] != 'false' or Path(layout[1]).resolve() != root
            or Path(layout[2]).resolve() != (root/'.git').resolve()
            or (root/layout[3]).resolve() != (root/'.git').resolve()):
        raise ValueError('full isolated runner checkout without shared Git directory required')
    version = run('version', '--version').strip()
    if run('read_config', 'config', '--type=bool', '--default=true', '--get', 'core.commitGraph').strip() != 'true':
        raise ValueError('commit graph consumer disabled')
    try:
        changed_version = int(run('bloom_config', 'config', '--type=int', '--get',
                                  'commitGraph.changedPathsVersion').strip())
    except subprocess.CalledProcessError as error:
        if error.returncode != 1 or error.output or error.stderr:
            raise
        changed_version = None
    if changed_version is None:
        if run('legacy_bloom_config', 'config', '--type=bool', '--default=true',
               '--get', 'commitGraph.readChangedPaths').strip() != 'true':
            raise ValueError('changed-path consumer disabled')
    elif changed_version not in (-1, 1, 2):
        raise ValueError('changed-path consumer version disabled or unsupported')
    before = _snapshot(run, root)
    if any(line.startswith('refs/replace/') for line in before['refs'].splitlines()):
        raise ValueError('replacement history rejected')
    run('write', 'commit-graph', 'write', '--reachable', '--changed-paths',
        '--max-new-filters=-1', '--split=replace', '--no-progress', cap=WRITE_SECONDS)
    run('verify', 'commit-graph', 'verify', '--no-progress', cap=VERIFY_SECONDS)
    graphs = _graphs(root)
    after = _snapshot(run, root)
    if before != after:
        raise ValueError('repository state changed during local indexing')
    if monotonic() >= deadline:
        raise TimeoutError('local history index overall budget exhausted')
    return {'status': 'LOCAL_GIT_HISTORY_INDEX_VERIFIED', 'git_version': version,
            'head': before['head'], 'graph_files': graphs, 'bloom_filters_present': True,
            'head_refs_tracked_state_and_diffs_unchanged': True,
            'frozen_guards_skipped': False, 'production_performance_verified': False,
            'seconds': monotonic()-start, 'phase_seconds': phases}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    with runtime_process.cancellation_signals():
        try:
            result = prepare(args.root)
        except (KeyboardInterrupt, runtime_process.RuntimeCancelled) as error:
            signum = getattr(error, 'signum', signal.SIGINT)
            print(json.dumps({'status': 'LOCAL_GIT_HISTORY_INDEX_FAILED',
                              'error_type': type(error).__name__, 'cancelled': True,
                              'frozen_guards_skipped': False, 'production_performance_verified': False},
                             sort_keys=True))
            return 128+signum
        except Exception as error:
            result = {'status': 'LOCAL_GIT_HISTORY_INDEX_FAILED', 'error_type': type(error).__name__,
                      'phase': getattr(error, 'history_graph_phase', 'preflight_or_postcondition'),
                      'frozen_guards_skipped': False, 'production_performance_verified': False}
            print(json.dumps(result, sort_keys=True))
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())

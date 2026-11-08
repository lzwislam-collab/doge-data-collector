"""Offline, bounded full/sparse audit-checkout comparison; never runs an audit.

The candidate is the real current runtime_audit_worker.prepare_checkout helper.
Its compatibility fallback is retained and cannot count as a sparse speedup.
All measurements use new clones, one fixed commit and the same local object store.
"""
import argparse
import ast
from contextlib import ExitStack
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

EXCLUDED = 'data/plus_evidence_v1_0/sources/'
INPUTS = ('data/direction_v42/forward_registry.csv',
          'data/direction_v42/forward_scores.csv', 'data/quote_evidence_v2/bindings.csv',
          'data/paired_forward_v1/pairs.jsonl', 'data/hourly_research/ohlc.csv')
SCRIPTS = ('forward_validation_v2.py', 'p0_operational_audit.py', 'paired_cohort_audit.py')


class ProfileError(ValueError):
    pass


def safe_environment():
    # Same offline boundary as runtime_publication_profile; no credentials inherited.
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR') if key in os.environ}
    env.update(GIT_ALLOW_PROTOCOL='file', GIT_NO_LAZY_FETCH='1',
               GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS='0')
    config = {'protocol.allow': 'never', 'protocol.file.allow': 'always',
              'core.hooksPath': os.devnull, 'core.autocrlf': 'false',
              'gc.auto': '0', 'maintenance.auto': 'false'}
    env['GIT_CONFIG_COUNT'] = str(len(config))
    for index, (key, value) in enumerate(config.items()):
        env['GIT_CONFIG_KEY_' + str(index)] = key
        env['GIT_CONFIG_VALUE_' + str(index)] = value
    return env


def lock_down_worker():
    def reject(event, args):
        if event.startswith('socket.') or event in ('os.system', 'os.exec', 'os.posix_spawn'):
            raise PermissionError('diagnostic network and shell access denied')
        if event == 'subprocess.Popen' and Path(os.fsdecode(args[0])).name != 'git':
            raise PermissionError('only offline Git subprocesses are allowed')
    sys.addaudithook(reject)


def git(root, *args, data=None):
    return subprocess.run(['git', *args], cwd=root, input=data, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=safe_environment(), timeout=240).stdout


def write_report(output, report):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp-' + str(os.getpid()))
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(report, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def source_state(source, helper_file):
    return {'head': git(source, 'rev-parse', 'HEAD').decode().strip(),
            'tree': git(source, 'rev-parse', 'HEAD^{tree}').decode().strip(),
            'parents': git(source, 'show', '-s', '--format=%P', 'HEAD').decode().split(),
            'tracked_status_sha256': sha256(git(source, 'status', '--porcelain', '--untracked-files=no')),
            'helper_sha256': sha256(Path(helper_file).read_bytes())}


def require_full_history(root):
    if git(root, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ProfileError('full Git history is required')
    if git(root, 'for-each-ref', '--format=%(refname)', 'refs/replace').strip():
        raise ProfileError('replacement Git history is not supported')
    grafts = Path(os.fsdecode(git(root, 'rev-parse', '--git-path', 'info/grafts').strip()))
    if not grafts.is_absolute(): grafts = Path(root) / grafts
    if grafts.exists() and grafts.stat().st_size:
        raise ProfileError('grafted Git history is not supported')
    config = git(root, 'config', '--local', '--list').decode('utf-8', 'replace').lower()
    if 'extensions.partialclone=' in config or re.search(r'remote\.[^\n]+\.promisor=true', config):
        raise ProfileError('partial-clone object stores are not supported')


def tree_entries(root, head):
    raw = git(root, 'ls-tree', '-r', '-z', '-l', '--full-tree', head)
    entries = []
    for item in raw.split(b'\0'):
        if not item: continue
        header, path = item.split(b'\t', 1)
        mode, kind, oid, size = header.split()
        if kind != b'blob' or mode not in (b'100644', b'100755', b'120000'):
            raise ProfileError('unsupported tracked object mode')
        name = os.fsdecode(path)
        if name.startswith('/') or any(part in ('', '.', '..') for part in name.split('/')):
            raise ProfileError('unsafe tracked path')
        entries.append({'path': name, 'mode': mode.decode(), 'oid': oid.decode(), 'bytes': int(size)})
    if not entries: raise ProfileError('empty pinned tree cannot establish equivalence')
    return raw, entries


def protected_paths(full):
    engine = 'p2_paired_execution_v3_1.py'
    parsed = ast.parse((Path(full) / engine).read_bytes())
    values = {}
    for node in parsed.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ('CODE_FILES', 'POLICY', 'SYSTEM'):
                    values[target.id] = ast.literal_eval(node.value)
    codes = values.get('CODE_FILES')
    if not isinstance(codes, (list, tuple)) or not codes or not all(isinstance(x, str) for x in codes):
        raise ProfileError('original frozen file identities are unavailable')
    paths = set(INPUTS + SCRIPTS + (engine, 'runtime_audit_worker.py',
                'system/current_p2_execution_shadow_v3_1.json')) | set(codes)
    for name in ('POLICY', 'SYSTEM'):
        value = values.get(name)
        if not isinstance(value, str): raise ProfileError('original frozen registry identity is unavailable')
        paths.add(value)
    if any(path.startswith(EXCLUDED) for path in paths):
        raise ProfileError('protected audit identity is inside the excluded subtree')
    return paths


def materialized(root, entries):
    result = {'files': 0, 'bytes': 0}
    for item in entries:
        path = Path(root) / item['path']
        try: info = path.lstat()
        except FileNotFoundError: continue
        is_link = item['mode'] == '120000'
        if is_link != stat.S_ISLNK(info.st_mode) or (not is_link and not stat.S_ISREG(info.st_mode)):
            raise ProfileError('tracked object materialized with an unexpected type')
        if not is_link and bool(info.st_mode & 0o111) != (item['mode'] == '100755'):
            raise ProfileError('tracked executable mode changed')
        if info.st_size != item['bytes']:
            raise ProfileError('tracked object materialized with an unexpected size')
        result['files'] += 1
        result['bytes'] += info.st_size
    return result


def tracked_stream(root, item, stack):
    path = Path(root) / item['path']
    try:
        if item['mode'] == '120000':
            return stack.enter_context(io.BytesIO(os.readlink(os.fsencode(path))))
        return stack.enter_context(path.open('rb'))
    except OSError as exc:
        raise ProfileError('required tracked file is missing or unreadable') from exc


def read_exact(stream, size):
    pieces = []
    while size:
        part = stream.read(size)
        if not part: raise ProfileError('Git blob stream ended early')
        pieces.append(part)
        size -= len(part)
    return b''.join(pieces)


def verify_blob_bytes(candidate, full, entries, effective_sparse, identities):
    """Read every current-tree blob from Git; compare full and retained files bytewise."""
    manifest = hashlib.sha256()
    identity_report = {}
    process = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=candidate,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=safe_environment())
    try:
        for item in entries:
            process.stdin.write((item['oid'] + '\n').encode('ascii'))
            process.stdin.flush()
            header = process.stdout.readline().split()
            expected = [item['oid'].encode(), b'blob', str(item['bytes']).encode()]
            if header != expected: raise ProfileError('Git blob identity or size mismatch')
            excluded = item['path'].startswith(EXCLUDED)
            digest = hashlib.sha256()
            object_digest = hashlib.sha1(b'blob ' + str(item['bytes']).encode() + b'\0')
            with ExitStack() as stack:
                streams = [tracked_stream(full, item, stack)]
                if not (effective_sparse and excluded):
                    streams.append(tracked_stream(candidate, item, stack))
                remaining = item['bytes']
                while remaining:
                    data = read_exact(process.stdout, min(1024 * 1024, remaining))
                    if any(stream.read(len(data)) != data for stream in streams):
                        raise ProfileError('tracked file bytes differ from the pinned Git blob')
                    digest.update(data)
                    object_digest.update(data)
                    remaining -= len(data)
                if any(stream.read(1) for stream in streams):
                    raise ProfileError('tracked file contains an unverified tail')
            if read_exact(process.stdout, 1) != b'\n' or object_digest.hexdigest() != item['oid']:
                raise ProfileError('Git blob bytes do not match their object identity')
            manifest.update(os.fsencode(item['path']) + b'\0' + item['mode'].encode() + b'\0'
                            + item['oid'].encode() + b'\0' + digest.digest())
            if item['path'] in identities:
                identity_report[item['path']] = {'git_blob': item['oid'], 'sha256': digest.hexdigest(),
                                                'bytes': item['bytes']}
        process.stdin.close()
        if process.wait(timeout=10) != 0: raise ProfileError('Git blob reader failed')
    finally:
        for stream in (process.stdin, process.stdout):
            if stream is not None and not stream.closed: stream.close()
        if process.poll() is None:
            process.kill()
        process.wait()
    if set(identity_report) != identities:
        raise ProfileError('a required original audit or frozen identity is missing')
    return manifest.hexdigest(), identity_report


def compare_checkouts(source, head, full, candidate, *, effective_sparse):
    tree = git(source, 'rev-parse', head + '^{tree}').decode().strip()
    history = git(source, 'rev-list', '--reverse', '--topo-order', head)
    objects = git(source, 'rev-list', '--objects', '--no-object-names', head)
    if not history.strip() or not objects.strip(): raise ProfileError('complete reachable history is required')
    original_tree, entries = tree_entries(source, head)
    excluded = [item for item in entries if item['path'].startswith(EXCLUDED)]
    if not excluded: raise ProfileError('excluded source subtree is absent in the pinned tree')
    links = sum(item['mode'] == '120000' for item in entries if not item['path'].startswith(EXCLUDED))
    if effective_sparse and links: raise ProfileError('retained symlink forbids claiming sparse equivalence')
    for checkout in (full, candidate):
        require_full_history(checkout)
        if git(checkout, 'rev-parse', 'HEAD').decode().strip() != head:
            raise ProfileError('checkout does not use the requested fixed commit')
        if git(checkout, 'rev-parse', 'HEAD^{tree}').decode().strip() != tree:
            raise ProfileError('pinned tree changed')
        if git(checkout, 'rev-list', '--reverse', '--topo-order', 'HEAD') != history:
            raise ProfileError('complete ordered reachable history differs')
        if git(checkout, 'rev-list', '--objects', '--no-object-names', 'HEAD') != objects:
            raise ProfileError('complete reachable object sequence differs')
        if tree_entries(checkout, 'HEAD')[0] != original_tree:
            raise ProfileError('tracked tree entries differ')
        if git(checkout, 'status', '--porcelain', '--untracked-files=all').strip():
            raise ProfileError('isolated checkout has unexpected modifications')
    # Check every reachable object, including history, without demanding historical materialization.
    checked = git(candidate, 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)', data=objects)
    rows = checked.splitlines()
    if len(rows) != len(objects.splitlines()): raise ProfileError('reachable object check is incomplete')
    for oid, row in zip(objects.splitlines(), rows):
        fields = row.split()
        if len(fields) != 3 or fields[0] != oid or fields[1] not in (b'blob', b'tree', b'commit', b'tag') or not fields[2].isdigit():
            raise ProfileError('a reachable Git object is unavailable')
    if effective_sparse and os.path.lexists(Path(candidate) / EXCLUDED):
        raise ProfileError('excluded subtree was materialized in the candidate')
    full_size, candidate_size = materialized(full, entries), materialized(candidate, entries)
    expected_count = len(entries) - (len(excluded) if effective_sparse else 0)
    if full_size['files'] != len(entries) or candidate_size['files'] != expected_count:
        raise ProfileError('tracked materialization count differs from the exact selected tree')
    manifest, identities = verify_blob_bytes(candidate, full, entries, effective_sparse, protected_paths(full))
    return {'pinned_head': head, 'pinned_tree': tree, 'commit_count': len(history.splitlines()),
            'ordered_history_sha256': sha256(history), 'reachable_objects': len(rows),
            'ordered_reachable_objects_sha256': sha256(objects), 'tracked_tree_manifest_sha256': sha256(original_tree),
            'verified_file_manifest_sha256': manifest, 'full_reachable_history_equal': True,
            'all_reachable_objects_available': True, 'retained_bytes_equal': True,
            'baseline_tracked_bytes_equal_to_git': True, 'excluded_git_blob_bytes_verified': True,
            'excluded_files': len(excluded), 'excluded_bytes': sum(item['bytes'] for item in excluded),
            'excluded_subtree_absent': bool(effective_sparse), 'retained_files': len(entries) - len(excluded),
            'retained_symlinks': links, 'baseline_materialized': full_size, 'candidate_materialized': candidate_size,
            'audit_inputs': {path: identities[path] for path in INPUTS},
            'original_script_and_frozen_identities': {path: value for path, value in identities.items() if path not in INPUTS}}


def initial_report(head):
    return {'schema_version': 1, 'probe': 'AUDIT_CHECKOUT_EQUIVALENCE_V1', 'status': 'not_started',
            'pinned_head': head, 'excluded_worktree_prefix': EXCLUDED, 'trials': [],
            'order': 'fresh_full_then_fresh_candidate', 'operating_system_cache_control': 'none',
            'audit_scripts_executed': False, 'sparse_optimization_verified': False,
            'scope': 'Checkout materialization and Git/byte equivalence only; no audit or production execution.'}


def worker(source, head, output, workspace, helper_file):
    source, workspace, helper_file = Path(source).resolve(), Path(workspace).resolve(), Path(helper_file).resolve()
    report = initial_report(head)
    report['status'] = 'running'
    write_report(output, report)
    lock_down_worker()
    before = None
    try:
        before = source_state(source, helper_file)
        report.update(actual_source_head=before['head'], actual_source_tree=before['tree'],
                      actual_source_parents=before['parents'], candidate_helper_sha256=before['helper_sha256'],
                      probe_sha256=sha256(Path(__file__).read_bytes()))
        require_full_history(source)
        git(source, 'cat-file', '-e', head + '^{commit}')
        report['pinned_original_worker_sha256'] = sha256(git(source, 'show', head + ':runtime_audit_worker.py'))
        if helper_file.is_relative_to(source):
            relative = helper_file.relative_to(source).as_posix()
            actual = git(source, 'show', before['head'] + ':' + relative)
            if actual != helper_file.read_bytes(): raise ProfileError('helper differs from actual CI checkout commit')
            report['candidate_helper_matches_actual_source_commit'] = True
        else:
            report['candidate_helper_matches_actual_source_commit'] = False
        spec = importlib.util.spec_from_file_location('measured_audit_checkout_helper', helper_file)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        if tuple(helper.INPUTS) != INPUTS or tuple(helper.SCRIPTS) != SCRIPTS:
            raise ProfileError('original five audit inputs or three entrypoints changed')
        locations = []
        for name, requested in (('full', False), ('candidate', True)):
            trial = {'name': name, 'status': 'running', 'requested_exclude_source_archive': requested}
            report['trials'].append(trial)
            write_report(output, report)
            checkout = workspace / name
            started = time.perf_counter()
            detail = helper.prepare_checkout(source, checkout, head, exclude_source_archive=requested)
            trial['prepare_wall_seconds'] = time.perf_counter() - started
            if not isinstance(detail, dict) or type(detail.get('exclude_source_archive')) is not bool:
                raise ProfileError('checkout helper did not report its effective mode')
            for key in ('clone_seconds', 'checkout_seconds'):
                value = detail.get(key)
                if not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                    raise ProfileError('checkout helper timing is invalid')
            trial['helper'] = detail
            trial['status'] = 'completed'
            locations.append(checkout)
            write_report(output, report)
        if report['trials'][0]['helper']['exclude_source_archive']:
            raise ProfileError('full baseline unexpectedly excluded files')
        effective = report['trials'][1]['helper']['exclude_source_archive']
        verification_started = time.perf_counter()
        report['equivalence'] = compare_checkouts(source, head, *locations, effective_sparse=effective)
        report['equivalence_seconds'] = time.perf_counter() - verification_started
        report['sparse_optimization_verified'] = effective
        report['status'] = 'completed' if effective else 'fallback'
    except Exception as exc:
        report['status'] = 'error'
        report['error'] = {'type': type(exc).__name__, 'reason': str(exc)[:800]}
        for trial in report['trials']:
            if trial['status'] == 'running': trial['status'] = 'error'
    finally:
        try:
            if before is not None and source_state(source, helper_file) != before:
                raise ProfileError('source checkout or candidate implementation changed during the probe')
            report['source_state_unchanged'] = before is not None
        except Exception as exc:
            report['status'] = 'error'
            report['source_state_error'] = {'type': type(exc).__name__, 'reason': str(exc)[:800]}
            report.setdefault('error', report['source_state_error'])
        write_report(output, report)
    return 0 if report['status'] == 'completed' else (2 if report['status'] == 'fallback' else 1)


def run_bounded(command, environment, timeout_seconds):
    process = subprocess.Popen(command, env=environment, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    timed_out = False
    try:
        try: process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired: timed_out = True
    finally:
        # Kill the entire session even if its original parent has already exited.
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
    return {'returncode': process.returncode, 'timed_out': timed_out}


def run_profile(source, head, output, timeout_seconds=360, helper_file=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    helper_file = Path(helper_file or Path(__file__).with_name('runtime_audit_worker.py')).resolve()
    if os.name != 'posix' or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 360:
        raise ValueError('offline checkout diagnostic requires a timeout in (0, 360] seconds')
    if not re.fullmatch('[0-9a-f]{40}', head): raise ValueError('complete fixed commit SHA is required')
    if output.is_relative_to(source): raise ValueError('report must be outside the source checkout')
    started = time.perf_counter()
    write_report(output, initial_report(head))
    with tempfile.TemporaryDirectory(prefix='runtime-audit-checkout-profile-') as name:
        workspace = Path(name).resolve()
        if workspace.is_relative_to(source) or source.is_relative_to(workspace):
            raise ValueError('temporary checkouts must not overlap the source checkout')
        command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--worker',
                   '--source-root', str(source), '--ref', head, '--output', str(output),
                   '--helper-file', str(helper_file), '--workspace', str(workspace)]
        remaining = max(0.001, timeout_seconds - (time.perf_counter() - started))
        result = run_bounded(command, safe_environment(), remaining)
    try: report = json.loads(output.read_text(encoding='utf-8'))
    except (OSError, ValueError): report = initial_report(head)
    if result['timed_out']:
        report['status'] = 'timeout'
        for trial in report['trials']:
            if trial['status'] == 'running': trial['status'] = 'timeout'
    elif result['returncode'] != 0 and not (result['returncode'] == 2 and report['status'] == 'fallback'):
        report['status'] = 'error'
    elif report['status'] not in ('completed', 'fallback'):
        report['status'] = 'error'
    report.update(total_seconds=time.perf_counter() - started, timeout_seconds=timeout_seconds,
                  temporary_checkouts_removed=True)
    write_report(output, report)
    return 0 if report['status'] == 'completed' else (2 if report['status'] == 'fallback' else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--ref', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout-seconds', type=float, default=360)
    parser.add_argument('--helper-file', type=Path)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--workspace', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.source_root, args.ref, args.output, args.workspace, args.helper_file)
    code = run_profile(args.source_root, args.ref, args.output, args.timeout_seconds, args.helper_file)
    print(args.output.read_text(encoding='utf-8'), end='')
    return code


if __name__ == '__main__':
    raise SystemExit(main())

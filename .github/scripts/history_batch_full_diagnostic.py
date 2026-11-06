"""One bounded, read-only Ubuntu comparison. Never execute a rescue/publisher."""
import argparse
import csv
import ctypes
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
try:
    import resource
except ImportError:
    resource = None  # Local control checks only; actual comparison requires Linux.
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
INPUT = '04b83739c1105c4fa7222cd14c8567abac2d9b8d'
TREE = 'c3c25654fbfb6fe18548671e5003353cd183c8bc'
ORACLE = 'f0831c7d38581543f84a85639c6d69a7305ff382'
FINAL = 'da20c7710a4e7ec0792cc4701399181632cb00ef'
CODE_SHA = 'cbb7b0ac125e7344ad9d7f2ab6d973fbf87afcbcaeeda5bbdc1e999557d54f45'
TEST_SHA = 'c22be8fd713dba2f09171c211ea07b5715ac4238de05bd61c53ca11e725263d6'
MANIFEST_SHA = 'e35417b4f3652aadc45665b41d300a6c70175a811f0ed947ce82bf2edc35ffdb'
ABSOLUTE = 1793970000.0  # 2026-11-06T13:00:00Z; checked independently below.


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def left(deadline):
    result = min(deadline-time.monotonic(), ABSOLUTE-time.time())
    if result <= 0:
        raise TimeoutError('diagnostic phase/absolute deadline exhausted')
    return result


def git(root, *args, deadline=None, data=None, binary=False):
    deadline = deadline if deadline is not None else float(os.environ['HB_PHASE_DEADLINE'])
    left(deadline)
    started = time.monotonic()
    p = subprocess.Popen(['git', *args], cwd=root, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = p.communicate(data, timeout=left(deadline))
        if p.returncode:
            raise subprocess.CalledProcessError(p.returncode, ['git', *args], stderr=err[-8192:])
        return out if binary else out.decode('utf-8').strip()
    finally:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
        if os.environ.get('HB_OUT'):
            with (Path(os.environ['HB_OUT'])/'diagnostic-git.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(args=list(args), seconds=time.monotonic()-started,
                    exit_code=p.returncode, label=os.environ.get('HB_LABEL'))) + '\n')


def identities():
    a = (ROOT/'forward_validation.py').read_bytes()
    b = (ROOT/'test_forward_validation.py').read_bytes()
    if hashlib.sha256(a).hexdigest() != CODE_SHA or hashlib.sha256(b).hexdigest() != TEST_SHA:
        raise ValueError('Exact final code/test identity changed')
    return dict(origin_commit=FINAL, new_path=str(ROOT/'forward_validation.py'), new_sha256=CODE_SHA,
                test_sha256=TEST_SHA, oracle_blob=ORACLE)


def load_modules(out):
    sys.path.insert(0, str(ROOT))
    identity = identities()
    raw = git(ROOT, 'cat-file', 'blob', ORACLE, binary=True)
    if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() != ORACLE:
        raise ValueError('Original oracle blob identity mismatch')
    old_path = Path(out)/'old-original.py'
    old_path.write_bytes(raw)
    old = types.ModuleType('independent_original_oracle')
    old.__file__ = str(old_path)
    exec(compile(raw, str(old_path), 'exec'), old.__dict__)
    spec = importlib.util.spec_from_file_location('exact_final_experiment', ROOT/'forward_validation.py')
    new = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(new)
    if old.git is new.git or old.history_receipts is new.history_receipts:
        raise ValueError('Oracle and experiment were not independently loaded')
    identity.update(old_path=str(old_path), old_sha256=hashlib.sha256(raw).hexdigest(),
                    old_module=old.__name__, new_module=new.__name__)
    return old, new, identity


def deny_network(*args, **kwargs):
    raise RuntimeError('External network forbidden during function/platform workers')


def prepare(args):
    mbytes = Path(args.manifest).read_bytes()
    if hashlib.sha256(mbytes).hexdigest() != MANIFEST_SHA:
        raise ValueError('Fixed original manifest changed')
    m = json.loads(mbytes)
    if m['production_input_sha'] != INPUT or m['tree'] != TREE:
        raise ValueError('Wrong actual input identity')
    identities()
    git(ROOT, 'worktree', 'add', '--quiet', '--detach', args.root, INPUT)
    root = Path(args.root)
    if git(root, 'rev-parse', 'HEAD') != INPUT or git(root, 'rev-parse', 'HEAD^{tree}') != TREE:
        raise ValueError('Fixed checkout identity mismatch')
    if git(root, 'rev-parse', '--is-shallow-repository') != 'false':
        raise ValueError('Shallow input forbidden')
    config = git(root, 'config', '--local', '--list').lower()
    if 'promisor=true' in config or 'partialclone' in config:
        raise ValueError('Implicit object fetching forbidden')
    objects = git(root, 'rev-list', '--objects', '--missing=print', INPUT).splitlines()
    if any(x.startswith('?') for x in objects):
        raise ValueError('Missing reachable objects')
    if int(git(root, 'rev-list', '--count', INPUT)) != 8995:
        raise ValueError('Wrong reachable commit count')
    data = git(root, 'cat-file', '--batch', data=''.join(v['git_blob']+'\n' for v in m['files'].values()).encode(), binary=True)
    position = 0
    for name, expected in m['files'].items():
        left(float(os.environ['HB_PHASE_DEADLINE']))
        end = data.index(b'\n', position)
        oid, kind, size = data[position:end].split()
        position = end+1
        size = int(size)
        body = data[position:position+size]
        position += size
        if data[position:position+1] != b'\n':
            raise ValueError('Truncated manifest object frame')
        position += 1
        if (oid.decode() != expected['git_blob'] or kind != b'blob' or size != expected['bytes']
                or hashlib.sha256(body).hexdigest() != expected['sha256'] or (root/name).read_bytes() != body):
            raise ValueError('Manifest body mismatch: '+name)
    if position != len(data):
        raise ValueError('Unexpected extra object response')
    snapshots = git(root, 'ls-tree', '-r', '--name-only', INPUT, '--', 'data/quote_evidence_v2/snapshots').splitlines()
    expected = {p for p in m['files'] if p.startswith('data/quote_evidence_v2/snapshots/')}
    if len(snapshots) != 1562 or set(snapshots) != expected:
        raise ValueError('Snapshot set mismatch')
    histories = {}
    for name, expected in m['history'].items():
        ordered = git(root, 'log', '--reverse', '--format=%H%x00%cI', INPUT, '--', name).splitlines()
        rows = list(csv.DictReader(io.StringIO((root/name).read_text(encoding='utf-8'))))
        if len(ordered) != expected['revisions'] or len(rows) != expected['rows']:
            raise ValueError('History/row count mismatch: '+name)
        histories[name] = dict(ordered=ordered, order_sha256=digest(ordered), versions=len(ordered), rows=len(rows))
    git(root, 'fsck', '--strict', '--no-reflogs', INPUT)
    save(Path(args.out)/'verified-input.json', dict(status='PASS', input=INPUT, tree=TREE, commits=8995,
         snapshots=1562, reachable_objects=len(objects), manifest_sha256=MANIFEST_SHA, history=histories,
         full_fsck=True, implicit_fetch=False, preparation_only_not_cached_validation_pass=True))


def function_worker(args):
    old, new, identity = load_modules(args.out)
    socket.socket.connect = deny_network
    fn = old.history_receipts if args.worker == 'old' else new.history_receipts
    active = old if args.worker == 'old' else new
    specs = [(old.REG, old.FCOLS), (old.SCORES, old.SCOLS),
             ('data/quote_evidence_v2/bindings.csv', ['forecast_id', 'prediction_id', 'kind', 'quote_id']),
             (old.WITNESSES, old.WCOLS), (old.RUN_CACHE, old.RCOLS)]
    report = dict(status='in_progress', mode=args.worker, code=identity, input=INPUT, calls={})
    target = Path(args.out)/(args.label+'.json')
    total_count = 0
    original_popen = subprocess.Popen
    original_git = active.git
    original_reader = new._HistoryBatch.read_blob
    trace = []
    def spawn(command, *a, **kw):
        nonlocal total_count
        if not isinstance(command, (list, tuple)) or command[0] != 'git' or command[1] not in {
                'rev-parse', 'config', 'for-each-ref', 'show', 'log', 'cat-file'}:
            raise RuntimeError('Non-read-only process forbidden in timed function')
        total_count += 1
        return original_popen(command, *a, **kw)
    def observed_git(root, *command):
        if command[0] == 'show' and len(command) == 2 and ':' in command[1]:
            trace.append(command[1].split(':', 1)[0])
        return original_git(root, *command)
    def observed_reader(reader, expression):
        trace.append(expression.split(':', 1)[0])
        return original_reader(reader, expression)
    try:
        for name, columns in specs:
            rows = old.read_rows(Path(args.root)/name, columns)
            trace.clear()
            before = total_count
            save(target, report)
            started = time.monotonic()
            with patch.object(subprocess, 'Popen', side_effect=spawn), patch.object(active, 'git', observed_git), patch.object(new._HistoryBatch, 'read_blob', observed_reader):
                receipts = fn(Path(args.root), name, columns, rows)
            elapsed = time.monotonic()-started
            report['calls'][name] = dict(receipts=receipts, receipts_sha256=digest(receipts),
                    traversed=list(trace), traversal_sha256=digest(trace), versions=len(trace),
                    seconds=elapsed, git_processes=total_count-before)
            save(target, report)
        report.update(status='PASS', total_function_seconds=sum(x['seconds'] for x in report['calls'].values()),
                      git_processes=total_count)
    except BaseException as exc:
        report.update(status='FAIL', error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        report['worker_peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report['maximum_child_peak_rss_kib'] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        report['peak_scope'] = 'OS per-process lifetime high-water; child maximum is not aggregate simultaneous RSS'
        save(target, report)


def platform_worker(args):
    old, new, identity = load_modules(args.out)
    socket.socket.connect = deny_network
    sys.path.insert(0, str(ROOT))
    os.environ['HISTORY_EQUIVALENCE_LOG'] = str(Path(args.out)/'platform-equivalence.jsonl')
    suite = unittest.defaultTestLoader.loadTestsFromName('test_forward_validation.BatchHistoryTests')
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(
        'test_forward_validation.ValidationTests.test_complete_history_detects_mutation_older_than_ten_revisions'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise ValueError('Platform safety regression failed')
    import forward_validation_v2 as v2
    protocol = json.loads((ROOT/v2.PROTOCOL).read_text())
    try:
        v2.check_code(ROOT, protocol)
    except ValueError as exc:
        if 'forward_validation.py' not in str(exc):
            raise
        protection = dict(status='PASS', gate_result='REJECTED', message=str(exc), mocked=False)
    else:
        raise ValueError('Old frozen gate incorrectly accepted experiment')
    import quote_capture_retry as retry
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        git(root, 'init', '-q')
        reader = new._HistoryBatch(root, timeout=.1, command=[sys.executable, '-c', 'import time;time.sleep(30)'])
        try:
            with reader:
                reader.read_blob('a'*40+':ledger.csv')
        except TimeoutError as exc:
            actual_timeout = exc
        else:
            raise ValueError('Expected reader timeout not observed')
        attempts = []
        delays = []
        def fail(*a):
            attempts.append(len(attempts)+1)
            raise actual_timeout
        outcome = retry.capture('direction', root=root, collect=fail, sleep=delays.append)
        if outcome['status'] != 'failed' or len(attempts) != 3 or delays != [2, 4]:
            raise ValueError('Unexpected existing TimeoutError retry behavior')
        (root/'ledger.csv').write_bytes(b'forecast_id,value\na,\xff\n')
        git(root, 'add', 'ledger.csv')
        git(root, '-c', 'user.name=Compatibility Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '-qm', 'Non-production malformed UTF8 fixture')
        utf8_outcomes = {}
        for name, module in [('old', old), ('new', new)]:
            try:
                module.history_receipts(root, 'ledger.csv', ['forecast_id', 'value'],
                                        {'a': {'forecast_id': 'a', 'value': 'invalid'}})
            except Exception as exc:
                captured = exc
            else:
                raise ValueError('Malformed UTF8 unexpectedly accepted')
            def reject_utf8(*a):
                raise captured
            captured_outcome = retry.capture('direction', root=root, collect=reject_utf8,
                                            sleep=lambda n: (_ for _ in ()).throw(ValueError('Unexpected UTF8 retry')))
            if captured_outcome['status'] != 'failed' or captured_outcome['attempts'] != 1:
                raise ValueError('Invalid UTF8 failure propagation changed')
            utf8_outcomes[name] = dict(actual_exception=type(captured).__name__, caller=captured_outcome)
    save(Path(args.out)/'platform-result.json', dict(status='PASS for safety tests only', code=identity,
         tests=result.testsRun, skipped=[x[1] for x in result.skipped], frozen_gate=protection,
         exception_contract='FAIL: known reader TimeoutError enters existing transient retry; no business fix made',
         timeout_compatibility=dict(actual_type=type(actual_timeout).__name__, caller_outcome=outcome,
              attempts=attempts, simulated_sleep_seconds=delays, network_calls=0,
              scope='Actual reader timeout raised by injected non-network collect; unchanged watched state; not a real capture'),
         utf8_results='platform-equivalence.jsonl', full_rescue_executed=False))
    result_path = Path(args.out)/'platform-result.json'
    recorded = json.loads(result_path.read_text())
    recorded['utf8_actual_caller_outcomes'] = utf8_outcomes
    save(result_path, recorded)


def process_tree():
    entries = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path/'stat').read_text().rsplit(')', 1)[1].split()
            entries[int(path.name)] = dict(ppid=int(fields[1]), start=fields[19],
                    rss=int((path/'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE'))
        except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError):
            pass
    known = {os.getpid()}
    while True:
        found = {pid for pid, row in entries.items() if row['ppid'] in known}
        added = found-known
        if not added:
            break
        known |= added
    return {pid: entries[pid] for pid in known if pid in entries and pid != os.getpid()}


def temp_size(root, pids):
    seen = set()
    logical = allocated = errors = 0
    paths = [Path(root), *Path(root).rglob('*')]
    for pid in pids:
        try:
            for fd in Path('/proc', str(pid), 'fd').iterdir():
                try:
                    if os.readlink(fd).startswith(str(root)+os.sep):
                        paths.append(fd)
                except (OSError, ValueError):
                    pass
        except OSError:
            pass
    for path in paths:
        try:
            stat = path.stat()
            if (stat.st_dev, stat.st_ino) in seen:
                continue
            seen.add((stat.st_dev, stat.st_ino))
            if path.is_file():
                logical += stat.st_size
            allocated += stat.st_blocks*512
        except OSError:
            errors += 1
    return logical, allocated, errors


def child(args, mode, label, deadline):
    left(deadline)
    tmp = Path(os.environ['RUNNER_TEMP'])/'hb-private'/label
    tmp.mkdir(parents=True, exist_ok=False)
    tmp = tmp.resolve(strict=True)
    env = dict(os.environ, HB_PHASE_DEADLINE=str(deadline), TMPDIR=str(tmp), TMP=str(tmp), TEMP=str(tmp),
               HB_OUT=args.out, HB_LABEL=label,
               PYTHONDONTWRITEBYTECODE='1', GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0',
               GIT_OPTIONAL_LOCKS='0', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    for key in list(env):
        if key in ('GITHUB_TOKEN', 'GH_TOKEN') or key.startswith(('ACTIONS_RUNTIME_', 'ACTIONS_ID_TOKEN_')):
            env.pop(key)
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', mode, '--label', label,
               '--root', args.root, '--out', args.out, '--manifest', args.manifest]
    started = time.monotonic()
    peak_rss = peak_logical = peak_allocated = errors = samples = 0
    expired = False
    with (Path(args.out)/(label+'.log')).open('wb') as log:
        p = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        try:
            while p.poll() is None:
                try:
                    left(deadline)
                except TimeoutError:
                    expired = True
                    break
                entries = process_tree()
                peak_rss = max(peak_rss, sum(x['rss'] for x in entries.values()))
                logical, allocated, failed = temp_size(tmp, entries)
                peak_logical = max(peak_logical, logical)
                peak_allocated = max(peak_allocated, allocated)
                errors += failed
                samples += 1
                try:
                    remaining = left(deadline)
                except TimeoutError:
                    expired = True
                    break
                time.sleep(min(.02, remaining))
        finally:
            unexpected = list(process_tree()) if p.poll() is not None and p.returncode == 0 else []
            if p.poll() is None:
                for pid in process_tree():
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                p.wait()
            # Controller is a Linux subreaper: orphaned separate Git sessions are still owned.
            for pid in process_tree():
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            end = time.monotonic()+2
            while time.monotonic() < end:
                try:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if pid == 0:
                    time.sleep(.01)
            remaining = list(process_tree())
            profile = dict(label=label, mode=mode, command=command, wall_seconds=time.monotonic()-started,
                exit_code=p.returncode, timeout=expired, sampled_aggregate_rss_peak_bytes=peak_rss,
                sampled_temp_logical_peak_bytes=peak_logical, sampled_temp_allocated_peak_bytes=peak_allocated,
                sampling_errors=errors, samples=samples, target_interval_seconds=.02,
                resource_limit='Samples may miss short peaks; RSS shared pages may be counted twice. Temp logical is regular-file payload; allocated includes directory st_blocks and open unlinked files deduplicated by inode, not whole-device allocation. OS worker high-water recorded separately.',
                residual_descendants=remaining, private_temp=str(tmp))
            profile['unexpected_descendants_before_cleanup'] = unexpected
            save(Path(args.out)/(label+'-resources.json'), profile)
    if expired or p.returncode or remaining or unexpected:
        raise RuntimeError('Worker failed/stopped: '+label)
    return profile


def equal_round(reference, candidate, verified):
    if reference['status'] != 'PASS' or candidate['status'] != 'PASS':
        raise ValueError('Normal comparison did not complete successfully')
    if set(reference['calls']) != set(verified['history']) or set(candidate['calls']) != set(reference['calls']):
        raise ValueError('Different table set')
    for name, expected in verified['history'].items():
        order = [x.split('\0')[0] for x in expected['ordered']]
        a, b = reference['calls'][name], candidate['calls'][name]
        if a['receipts'] != b['receipts'] or a['traversed'] != order or b['traversed'] != order:
            raise ValueError('Receipt/SHA/UTC/traversal mismatch: '+name)


def controller(args):
    from datetime import datetime, timezone
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = float(os.environ['HB_PREP_STARTED_MONOTONIC'])
    prep_deadline = started+180
    overall = started+900
    args.root = str(Path(os.environ['RUNNER_TEMP'])/'hb-fixed-input')
    summary = dict(status='in_progress', completed_rounds=[], full_ABBA=False, compatibility='NOT_VERIFIED')
    save(out/'summary.json', summary)
    try:
        if sys.platform != 'linux':
            raise RuntimeError('Actual comparison is Ubuntu/Linux only')
        if datetime.fromisoformat('2026-11-06T13:00:00+00:00').timestamp() != ABSOLUTE:
            raise ValueError('Absolute cutoff constant incorrect')
        if ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) != 0:
            raise RuntimeError('Cannot own/reap diagnostic descendants')
        save(out/'environment.json', dict(platform=platform.platform(), python=sys.version,
             git=git(ROOT, '--version', deadline=prep_deadline), controller_pid=os.getpid(),
             transport_checkout=git(ROOT, 'rev-parse', 'HEAD', deadline=prep_deadline),
             transport_parents=git(ROOT, 'show', '-s', '--format=%P', 'HEAD', deadline=prep_deadline),
             code=identities(), prep_started_monotonic=started, complete_job_limit=900,
             prep_limit=180, ABBA_group_limit=600, hard_UTC_cutoff='2026-11-06T13:00:00Z',
             cache='Warm cache: complete input checkout, object verification/fsck, platform fixtures precede ABBA. No cache flush, no actual-input function prewarm, fresh Python workers; fixed old,new,new,old order.'))
        child(args, 'prepare', 'prepare', prep_deadline)
        child(args, 'platform', 'platform', prep_deadline)
        summary['preparation_seconds'] = time.monotonic()-started
        summary['compatibility'] = 'FAIL: known TimeoutError retry classification retained; see platform-result.json'
        verified = json.loads((out/'verified-input.json').read_text())
        if min(ABSOLUTE-time.time(), overall-time.monotonic()) < 630:
            raise TimeoutError('Insufficient full ABBA plus retention window; do not start')
        group_started = time.monotonic()
        group_deadline = min(group_started+600, overall-30)
        reference = None
        for i, mode in enumerate(('old', 'new', 'new', 'old'), 1):
            label = str(i)+'-'+mode
            child(args, mode, label, group_deadline)
            report = json.loads((out/(label+'.json')).read_text())
            if reference is None:
                reference = report
            equal_round(reference, report, verified)
            if git(Path(args.root), 'rev-parse', 'HEAD', deadline=group_deadline) != INPUT or git(Path(args.root), 'status', '--porcelain', '--untracked-files=no', deadline=group_deadline):
                raise ValueError('Fixed input changed during comparison')
            summary['completed_rounds'].append(dict(label=label, mode=mode, function_seconds=report['total_function_seconds'], git_processes=report['git_processes']))
            save(out/'summary.json', summary)
        summary.update(status='PASS: function equivalence and completed bounded comparison only',
                       full_ABBA=True, ABBA_wall_seconds=time.monotonic()-group_started,
                       production_admission='NOT_VERIFIED; known compatibility blocker; no full rescue/network/publication measured')
    except BaseException as exc:
        summary.update(status='FAIL or incomplete experiment; not a production candidate verdict',
                       error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        summary.update(total_since_bootstrap_seconds=time.monotonic()-started, utc=datetime.now(timezone.utc).isoformat())
        save(out/'summary.json', summary)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--worker', choices=['prepare', 'platform', 'old', 'new'])
    p.add_argument('--root', default='')
    p.add_argument('--label', default='')
    p.add_argument('--out', required=True)
    p.add_argument('--manifest', required=True)
    p.add_argument('--input-sha', default=INPUT)
    p.add_argument('--order', default='old,new,new,old')
    p.add_argument('--preparation-seconds', type=int, default=180)
    p.add_argument('--comparison-seconds', type=int, default=600)
    p.add_argument('--deadline-utc', default='2026-11-06T13:00:00Z')
    args = p.parse_args()
    if (args.input_sha != INPUT or args.order != 'old,new,new,old' or args.preparation_seconds != 180
            or args.comparison_seconds != 600 or args.deadline_utc != '2026-11-06T13:00:00Z'):
        raise ValueError('Approved fixed experiment parameters changed')
    if args.worker == 'prepare':
        prepare(args)
    elif args.worker == 'platform':
        platform_worker(args)
    elif args.worker:
        function_worker(args)
    else:
        controller(args)


if __name__ == '__main__':
    main()

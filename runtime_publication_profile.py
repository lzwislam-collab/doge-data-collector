"""Bounded, offline profiling of real frozen validation and fixed-HEAD readback.

This does not call LiveAdapter.action('publish'), commit, push, issue, witness or
OSS. The original checkout is read-only. Timings exclude production transport.
"""
import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time


DATA_PREFIXES = ('system', 'data/stage5', 'data/direction_v42',
                 'data/quote_evidence_v2', 'data/paired_forward_v1',
                 'data/p2_paired_execution_v3_1')
HISTORY_PATHS = ('data/direction_v42/forward_registry.csv',
                 'data/direction_v42/forward_scores.csv',
                 'data/quote_evidence_v2/bindings.csv',
                 'data/paired_forward_v1/pairs.jsonl',
                 'data/p2_paired_execution_v3_1/books.jsonl')


class ProfileError(ValueError):
    """An expected, non-secret diagnostic precondition failure."""


def safe_environment():
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


def git(root, *args, input=None):
    return subprocess.run(['git', *args], cwd=root, env=safe_environment(),
                          input=input, check=True, capture_output=True).stdout


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


def prepare_checkout(source, source_sha, checkout):
    source, checkout = Path(source).resolve(), Path(checkout).resolve()
    if checkout.is_relative_to(source) or source.is_relative_to(checkout):
        raise ProfileError('isolated checkout must not overlap the original repository')
    if not re.fullmatch('[0-9a-f]{40}', source_sha):
        raise ProfileError('a complete fixed commit SHA is required')
    if git(source, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ProfileError('full git history required')
    git(source, 'cat-file', '-e', source_sha + '^{commit}')
    git(source, 'clone', '--quiet', '--shared', '--local', '--no-checkout',
        '--', str(source), str(checkout))
    git(checkout, 'config', 'core.sparseCheckout', 'true')
    git(checkout, 'config', 'core.sparseCheckoutCone', 'false')
    patterns = '/*\n!/*/\n' + ''.join('/' + prefix + '/\n' for prefix in DATA_PREFIXES)
    (checkout / '.git/info/sparse-checkout').write_text(patterns, encoding='utf-8')
    git(checkout, 'checkout', '--quiet', '--detach', source_sha)
    git(checkout, 'remote', 'remove', 'origin')
    if git(checkout, 'rev-parse', 'HEAD').decode().strip() != source_sha:
        raise ProfileError('isolated checkout commit mismatch')
    if git(checkout, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ProfileError('isolated checkout lost complete history')
    return int(git(checkout, 'rev-list', '--count', 'HEAD').strip())


def core_inputs(root, publisher, evidence):
    paths = list(publisher.PATHS)
    paths += sorted(path.relative_to(root).as_posix()
                    for path in (root / evidence.BASE / 'snapshots').glob('*/*.json'))
    if len(paths) != len(set(paths)):
        raise ProfileError('duplicate core input path')
    result = {}
    for path in paths:
        candidate = root / path
        if candidate.is_symlink() or not candidate.is_file() or not candidate.resolve().is_relative_to(root):
            raise ProfileError('missing or unsafe core input')
        result[path] = candidate.read_bytes()
    return result


def input_manifest(inputs):
    rows = [{'path': path, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
            for path, body in sorted(inputs.items())]
    encoded = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    return {'files': len(rows), 'bytes': sum(row['bytes'] for row in rows),
            'manifest_sha256': hashlib.sha256(encoded).hexdigest()}


def phase(report, output, stage, function, *, records=None):
    record = {'stage': stage, 'status': 'running'}
    (report['phases'] if records is None else records).append(record)
    write_report(output, report)
    started = time.perf_counter()
    primary_failed = False
    try:
        result = function()
        record['status'] = 'ok'
        return result
    except BaseException:
        primary_failed = True
        record['status'] = 'error'
        raise
    finally:
        diagnostic_error = None

        def unavailable(operation, error):
            record['diagnostic_status'] = 'unavailable'
            report.setdefault('diagnostic_errors', []).append(
                {'stage': stage, 'operation': operation, 'type': type(error).__name__})

        try:
            elapsed = time.perf_counter() - started
            if not math.isfinite(elapsed) or elapsed < 0:
                raise ProfileError('invalid monotonic phase duration')
            record['seconds'] = elapsed
        except Exception as exc:
            diagnostic_error = exc
            unavailable('clock', exc)
        try:
            write_report(output, report)
        except Exception as exc:
            diagnostic_error = diagnostic_error or exc
            unavailable('report', exc)
        # A diagnostic failure must never replace the validator's exception or
        # its cause. A successful validator still cannot make failed profiling green.
        if diagnostic_error is not None and not primary_failed:
            raise diagnostic_error


def initial_report(source_sha, index_experiment=False):
    report = {'schema_version': 1, 'status': 'running', 'source_sha': source_sha,
            'scope': 'single_pass_local_validation_and_fixed_HEAD_readback',
            'clock': 'monotonic', 'phases': [], 'python_network_denied': True,
            'git_protocols_allowed': ['file'], 'lazy_fetch_disabled': True,
            'production_network_time_measured': False,
            'production_publication_verified': False,
            'two_publication_transactions_measured': False}
    if index_experiment:
        report['scope'] = 'native_git_commit_graph_off_on_off_same_checkout'
        report['index_experiment'] = {
            'status': 'running', 'trials': [],
            'baseline_kind': 'explicit_graph_off_not_production_baseline',
            'build': {'status': 'not_started', 'phases': []},
            'p2_breakdown': {'status': 'not_started', 'phases': [],
                             'scope': 'additional_pass_do_not_add_to_trial_totals'}}
    return report


def graph_inventory(objects):
    objects = Path(objects)
    single = objects / 'info/commit-graph'
    chain = objects / 'info/commit-graphs/commit-graph-chain'
    files = ([single] if single.is_file() else []) + ([chain] if chain.is_file() else [])
    files += sorted((objects / 'info/commit-graphs').glob('*.graph'))
    return {'has_commit_graph': single.is_file(), 'has_split_chain': chain.is_file(),
            'files': [{'name': path.relative_to(objects).as_posix(),
                       'bytes': path.stat().st_size,
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in files]}


def object_directory(root):
    path = Path(git(root, 'rev-parse', '--git-path', 'objects').decode().strip())
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def source_state(source):
    return {'head': git(source, 'rev-parse', 'HEAD').decode().strip(),
            'tracked_status_sha256': hashlib.sha256(
                git(source, 'status', '--porcelain', '--untracked-files=no')).hexdigest(),
            'runtime_code_sha256': hashlib.sha256((source / 'runtime_deadline_service.py').read_bytes()).hexdigest(),
            'graph': graph_inventory(object_directory(source))}


def history_oids(root):
    result = {}
    for path in HISTORY_PATHS:
        revisions = git(root, 'log', '--reverse', '--format=%H', '--', path).decode('ascii').splitlines()
        if any(not re.fullmatch('[0-9a-f]{40}', rev) for rev in revisions):
            raise ProfileError('invalid ordered history OID')
        result[path] = revisions
    return result


def history_manifest(histories):
    return {path: {'commits': len(revisions),
                   'ordered_oids_sha256': hashlib.sha256(
                       json.dumps(revisions, separators=(',', ':')).encode()).hexdigest()}
            for path, revisions in histories.items()}


def index_experiment(checkout, source, source_sha, report, output, validators, readback, deadline):
    experiment = report['index_experiment']
    objects = object_directory(checkout)
    if objects != (checkout / '.git/objects').resolve() or not objects.is_relative_to(checkout):
        raise ProfileError('commit graph must use isolated checkout objects')
    alternates_file = objects / 'info/alternates'
    alternates = alternates_file.read_text().splitlines() if alternates_file.exists() else []
    experiment['git_version'] = git(checkout, '--version').decode().strip()
    experiment['initial_graph_state'] = {
        'own': graph_inventory(objects),
        'alternates': [graph_inventory((objects / alternate).resolve()) for alternate in alternates],
        'core_commit_graph': git(checkout, 'config', '--default', 'unset', '--get', 'core.commitGraph').decode().strip(),
        'changed_paths_version': git(checkout, 'config', '--default', 'unset', '--get', 'commitGraph.changedPathsVersion').decode().strip()}
    experiment['source_before'] = source_state(source)
    tracked_before = git(checkout, 'status', '--porcelain', '--untracked-files=no')
    baseline_history = None

    def check_fixed_state():
        head = git(checkout, 'rev-parse', 'HEAD').decode().strip()
        if head != source_sha:
            raise ProfileError('fixed diagnostic HEAD changed')
        if git(checkout, 'status', '--porcelain', '--untracked-files=no') != tracked_before:
            raise ProfileError('tracked input state changed during index experiment')
        return head

    for name, enabled in (('off_before', False), ('on', True), ('off_after', False)):
        if enabled:
            build = experiment['build']
            build.update(status='running', object_directory_scope='isolated_checkout_only',
                         source_sha=source_sha)
            try:
                phase(report, output, 'commit_graph_build', lambda: git(
                    checkout, '-c', 'core.commitGraph=true', 'commit-graph',
                    '--object-dir=' + str(objects), 'write', '--stdin-commits',
                    '--changed-paths', '--max-new-filters=-1', '--no-progress',
                    input=(source_sha + '\n').encode('ascii')), records=build['phases'])
                build['graph_after'] = graph_inventory(objects)
                if not build['graph_after']['has_commit_graph'] and not build['graph_after']['has_split_chain']:
                    raise ProfileError('native commit graph was not written to isolated objects')
                phase(report, output, 'commit_graph_verify', lambda: git(
                    checkout, '-c', 'core.commitGraph=true', 'commit-graph',
                    '--object-dir=' + str(objects), 'verify', '--no-progress'), records=build['phases'])
                build['status'] = 'completed'
            except BaseException:
                build['status'] = 'error'
                raise
        git(checkout, 'config', '--local', 'core.commitGraph', 'true' if enabled else 'false')
        git(checkout, 'config', '--local', 'commitGraph.changedPathsVersion', '-1')
        trial = {'name': name, 'commit_graph_enabled': enabled, 'status': 'running', 'phases': []}
        experiment['trials'].append(trial)
        write_report(output, report)
        try:
            trial['head_before'] = check_fixed_state()
            trial['full_history_commit_count'] = int(git(checkout, 'rev-list', '--count', 'HEAD').strip())
            if trial['full_history_commit_count'] != report['full_history_commit_count']:
                raise ProfileError('full history count changed during index experiment')
            # These additional queries prove equivalence; they are deliberately
            # outside the original validator/readback phase timings.
            current_history = history_oids(checkout)
            trial['history_sequences'] = history_manifest(current_history)
            if baseline_history is None:
                baseline_history = current_history
            elif current_history != baseline_history:
                raise ProfileError('ordered history OID lists changed with native index mode')
            for stage, function in validators:
                phase(report, output, stage, function, records=trial['phases'])
            phase(report, output, 'core_exact_readback', readback, records=trial['phases'])
            trial['head_after'] = check_fixed_state()
            trial['validation_and_readback_seconds'] = sum(p['seconds'] for p in trial['phases'])
            trial['status'] = 'completed'
            write_report(output, report)
        except BaseException:
            trial['status'] = 'error'
            raise
    experiment['history_sequences_equal'] = True
    breakdown = experiment['p2_breakdown']
    remaining = max(0.0, deadline - time.perf_counter())
    estimate = 1.25 * max(t['phases'][0]['seconds'] for t in experiment['trials'])
    breakdown.update(estimated_seconds=estimate, remaining_seconds=remaining, reserve_seconds=10)
    if remaining < estimate + 10:
        breakdown['status'] = 'not_run_due_budget'
    else:
        breakdown['status'] = 'running'
        try:
            engine = importlib.import_module('p2_paired_execution_v3_1')
            phase(report, output, 'p2_guard', lambda: engine.guard(checkout), records=breakdown['phases'])
            events = phase(report, output, 'p2_history_guard', lambda: engine.history_guard(checkout),
                           records=breakdown['phases'])
            def books():
                for _ in engine.event_books(checkout, events): pass
            phase(report, output, 'p2_event_books', books, records=breakdown['phases'])
            breakdown['status'] = 'completed'
        except BaseException:
            breakdown['status'] = 'error'
            raise
    check_fixed_state()
    experiment['source_after'] = source_state(source)
    experiment['source_unchanged'] = experiment['source_before'] == experiment['source_after']
    if not experiment['source_unchanged']:
        raise ProfileError('original source state changed during index experiment')
    experiment['status'] = 'completed'
    write_report(output, report)


def worker(source, source_sha, output, workspace, use_index_experiment=False, timeout_seconds=360):
    source, output, workspace = Path(source).resolve(), Path(output).resolve(), Path(workspace).resolve()
    if output.is_relative_to(source) or workspace.is_relative_to(source):
        raise ProfileError('worker output and workspace must be outside the original checkout')
    deadline = time.perf_counter() + timeout_seconds
    report = initial_report(source_sha, use_index_experiment)
    write_report(output, report)
    lock_down_worker()
    checkout = workspace / 'checkout'
    original_runtime = None
    try:
        setup = time.perf_counter()
        report['full_history_commit_count'] = prepare_checkout(source, source_sha, checkout)
        report['setup_seconds'] = time.perf_counter() - setup
        original_runtime = (checkout / 'runtime_deadline_service.py').read_bytes()
        measured_runtime = (source / 'runtime_deadline_service.py').read_bytes()
        report['runtime_code_sha256'] = hashlib.sha256(measured_runtime).hexdigest()
        report['fixed_runtime_code_sha256'] = hashlib.sha256(original_runtime).hexdigest()
        (checkout / 'runtime_deadline_service.py').write_bytes(measured_runtime)
        sys.path.insert(0, str(checkout))
        runtime = importlib.import_module('runtime_deadline_service')
        publisher = importlib.import_module('publish_forward_evidence')
        pair = importlib.import_module('paired_forward_cohort')
        evidence = importlib.import_module('quote_evidence_v2')
        expected = core_inputs(checkout, publisher, evidence)
        report['core_inputs_before'] = input_manifest(expected)
        validators = [(name, lambda function=function: function(checkout)) for name, function in (
                ('preflight_validate_p2', runtime.validate_p2),
                ('preflight_validate_state', publisher.validate_state),
                ('preflight_pair_guard', pair.guard),
                ('preflight_pair_history_guard', pair.history_guard))]

        def readback():
            if core_inputs(checkout, publisher, evidence) != expected:
                raise ProfileError('core input bytes changed during validation')
            if not publisher.verify_readback(checkout, 'HEAD', expected):
                raise ProfileError('fixed HEAD core readback mismatch')
        if use_index_experiment:
            index_experiment(checkout, source, source_sha, report, output, validators, readback, deadline)
        else:
            for name, function in validators:
                phase(report, output, name, function)
            report['frozen_validation_completed'] = True
            phase(report, output, 'core_exact_readback', readback)
        report['frozen_validation_completed'] = True
        after = core_inputs(checkout, publisher, evidence)
        report['core_inputs_after'] = input_manifest(after)
        if after != expected:
            raise ProfileError('core input bytes changed during readback')
        report['input_bytes_unchanged'] = True
        (checkout / 'runtime_deadline_service.py').write_bytes(original_runtime)
        original_runtime = None
        report['tracked_worktree_clean'] = not git(checkout, 'status', '--porcelain', '--untracked-files=no').strip()
        if not report['tracked_worktree_clean']:
            raise ProfileError('tracked inputs changed during read-only diagnostic')
        report['local_readback_verified'] = True
        report['status'] = 'completed'
    except Exception as exc:
        report['status'] = 'error'
        if use_index_experiment: report['index_experiment']['status'] = 'error'
        report['error'] = {'type': type(exc).__name__}
        if isinstance(exc, ProfileError):
            report['error']['reason'] = str(exc)
    finally:
        if original_runtime is not None:
            (checkout / 'runtime_deadline_service.py').write_bytes(original_runtime)
        write_report(output, report)
    return 0 if report['status'] == 'completed' else 1


def run_bounded(command, environment, timeout_seconds):
    process = subprocess.Popen(command, env=environment, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    timed_out = False
    try:
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: pass
    finally:
        # Also stop descendants whose parent already exited after SIGTERM.
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
    return {'returncode': process.returncode, 'timed_out': timed_out}


def run_profile(source, source_sha, output, timeout_seconds, index_experiment=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source):
        raise ValueError('diagnostic output must be outside the original checkout')
    if (os.name != 'posix' or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 360):
        raise ValueError('Ubuntu diagnostic requires a timeout in (0, 360] seconds')
    if not re.fullmatch('[0-9a-f]{40}', source_sha):
        raise ValueError('a complete fixed commit SHA is required')
    report = initial_report(source_sha, index_experiment)
    write_report(output, report)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='runtime-publication-profile-') as workspace:
        command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                              '--worker', '--source-repo', str(source), '--source-sha', source_sha,
                              '--output', str(output), '--workspace', workspace,
                              '--timeout-seconds', str(timeout_seconds)]
        if index_experiment: command.append('--index-experiment')
        result = run_bounded(command, safe_environment(), timeout_seconds)
    try: report = json.loads(output.read_text(encoding='utf-8'))
    except (OSError, ValueError): report = initial_report(source_sha, index_experiment)
    if result['timed_out']:
        report['status'] = 'timeout'
        for record in report['phases']:
            if record['status'] == 'running': record['status'] = 'timeout'
        if index_experiment:
            experiment = report['index_experiment']
            experiment['status'] = 'timeout'
            for section in [experiment['build'], *experiment['trials'], experiment['p2_breakdown']]:
                if section['status'] == 'running': section['status'] = 'timeout'
                for record in section['phases']:
                    if record['status'] == 'running': record['status'] = 'timeout'
    elif result['returncode'] != 0 or report['status'] != 'completed':
        report['status'] = 'error'
    report['total_seconds'] = time.perf_counter() - started
    report['timeout_seconds'] = timeout_seconds
    write_report(output, report)
    return 0 if report['status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--timeout-seconds', type=float, default=360)
    parser.add_argument('--index-experiment', action='store_true',
                        help='compare native Git commit-graph off/on/off in the same isolated checkout')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--source-repo', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--workspace', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.source_repo, args.source_sha, args.output, args.workspace,
                      args.index_experiment, args.timeout_seconds)
    code = run_profile(Path(__file__).resolve().parent, args.source_sha, args.output,
                       args.timeout_seconds, args.index_experiment)
    print(args.output.read_text(encoding='utf-8'))
    return code


if __name__ == '__main__':
    raise SystemExit(main())

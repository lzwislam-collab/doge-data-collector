"""Bounded, offline profiling of real frozen validation and fixed-HEAD readback.

This does not call LiveAdapter.action('publish'), commit, push, issue, witness or
OSS. The original checkout is read-only. Timings exclude production transport.
"""
import argparse
from contextlib import nullcontext, redirect_stdout
import hashlib
import importlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time


DATA_PREFIXES = ('system', 'data/stage5', 'data/direction_v42',
                 'data/quote_evidence_v2', 'data/paired_forward_v1',
                 'data/p2_paired_execution_v3_1')
HISTORY_PATHS = ('data/direction_v42/forward_registry.csv',
                 'data/direction_v42/forward_scores.csv',
                 'data/quote_evidence_v2/bindings.csv',
                 'data/paired_forward_v1/pairs.jsonl',
                 'data/p2_paired_execution_v3_1/books.jsonl')
IMPLEMENTATION_FILES = ('runtime_deadline_service.py', 'runtime_p2_history.py')


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


def phase(report, output, stage, function, *, records=None, lock=None):
    record = {'stage': stage, 'status': 'running'}
    with lock or nullcontext():
        (report['phases'] if records is None else records).append(record)
        write_report(output, report)
    started = time.perf_counter()
    primary_failed = False
    try:
        result = function()
        with lock or nullcontext():
            record['status'] = 'ok'
        return result
    except BaseException:
        primary_failed = True
        with lock or nullcontext():
            record['status'] = 'error'
        raise
    finally:
        diagnostic_error = None

        def unavailable(operation, error):
            record['diagnostic_status'] = 'unavailable'
            report.setdefault('diagnostic_errors', []).append(
                {'stage': stage, 'operation': operation, 'type': type(error).__name__})

        with lock or nullcontext():
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


def initial_report(source_sha, index_experiment=False, parallel_validation_experiment=False,
                   history_batch_experiment=False, p2_equivalence_experiment=False):
    if sum(bool(x) for x in (index_experiment, parallel_validation_experiment,
                            history_batch_experiment, p2_equivalence_experiment)) > 1:
        raise ProfileError('diagnostic experiments are mutually exclusive')
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
    if parallel_validation_experiment:
        report['scope'] = 'serial_parallel_serial_same_checkout_full_validation'
        report['parallel_validation_experiment'] = {
            'status': 'running', 'trials': [], 'threads': 2,
            'timing_scope': 'phase_elapsed_times_overlap_do_not_add_as_wall_time'}
    if history_batch_experiment:
        report['scope'] = 'original_full_validation_then_native_git_patch_byte_comparison'
        report['history_batch_experiment'] = {
            'status': 'not_started', 'path': HISTORY_PATHS[-1],
            'production_validator_replaced': False}
    if p2_equivalence_experiment:
        report['scope'] = 'reference_candidate_reference_full_p2_validation'
        report['p2_equivalence_experiment'] = {
            'status': 'not_started', 'trials': [], 'production_validator_replaced': False,
            'timing_scope': 'main_validation_wall_excludes_additional_event_equivalence_pass'}
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
            'helper_code_sha256': file_sha256(source / 'runtime_p2_history.py'),
            'graph': graph_inventory(object_directory(source))}


def file_sha256(path):
    if path.is_symlink():
        raise ProfileError('implementation files must not be symbolic links')
    if not path.exists():
        return None
    if not path.is_file():
        raise ProfileError('implementation path is not a regular file')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_implementation_overlays(checkout, source, report, originals):
    report['implementation_overlays'] = {}
    for name in IMPLEMENTATION_FILES:
        target, current = checkout / name, source / name
        fixed_sha, measured_sha = file_sha256(target), file_sha256(current)
        if name == 'runtime_deadline_service.py' and measured_sha is None:
            raise ProfileError('current runtime implementation is missing')
        originals[name] = target.read_bytes() if fixed_sha is not None else None
        report['implementation_overlays'][name] = {
            'fixed_sha256': fixed_sha, 'measured_sha256': measured_sha}
        if measured_sha is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(current.read_bytes())
            if file_sha256(target) != measured_sha:
                raise ProfileError('implementation overlay bytes changed during installation')
    report['runtime_code_sha256'] = report['implementation_overlays']['runtime_deadline_service.py']['measured_sha256']
    report['fixed_runtime_code_sha256'] = report['implementation_overlays']['runtime_deadline_service.py']['fixed_sha256']


def restore_implementation_overlays(checkout, originals, report):
    for name, body in originals.items():
        path = checkout / name
        if path.is_symlink():
            path.unlink()
        if body is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(body)
        expected = None if body is None else hashlib.sha256(body).hexdigest()
        if file_sha256(path) != expected:
            raise ProfileError('fixed implementation overlay was not restored')
    report['overlays_restored'] = True


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


def elapsed_seconds(started):
    elapsed = time.perf_counter() - started
    if not math.isfinite(elapsed) or elapsed < 0:
        raise ProfileError('invalid monotonic experiment duration')
    return elapsed


def parallel_experiment(checkout, source, source_sha, report, output,
                        validators, readback, core_snapshot):
    experiment = report['parallel_validation_experiment']
    baseline_source = source_state(source)
    baseline_status = git(checkout, 'status', '--porcelain', '--untracked-files=no')
    baseline_history = history_oids(checkout)
    baseline_inputs = core_snapshot()
    experiment['source_before'] = baseline_source
    lock = threading.RLock()

    def check_state(trial, suffix):
        head = git(checkout, 'rev-parse', 'HEAD').decode().strip()
        trial['head_' + suffix] = head
        if head != source_sha:
            raise ProfileError('fixed diagnostic HEAD changed')
        tracked_status = git(checkout, 'status', '--porcelain', '--untracked-files=no')
        trial['tracked_status_' + suffix + '_sha256'] = hashlib.sha256(tracked_status).hexdigest()
        if tracked_status != baseline_status:
            raise ProfileError('tracked input state changed during parallel experiment')
        histories = history_oids(checkout)
        trial['history_' + suffix] = history_manifest(histories)
        if histories != baseline_history:
            raise ProfileError('ordered history OID lists changed during parallel experiment')
        inputs = core_snapshot()
        trial['core_inputs_' + suffix] = input_manifest(inputs)
        if inputs != baseline_inputs:
            raise ProfileError('core input bytes changed during parallel experiment')
        current_source = source_state(source)
        trial['source_' + suffix] = current_source
        if current_source != baseline_source:
            raise ProfileError('original source state changed during parallel experiment')

    for name in ('serial_before', 'parallel', 'serial_after'):
        trial = {'name': name, 'status': 'running', 'phases': [], 'groups': []}
        experiment['trials'].append(trial)
        write_report(output, report)
        started = time.perf_counter()
        primary = None
        try:
            check_state(trial, 'before')
            validation_started = time.perf_counter()
            if name == 'parallel':
                validator_failures = {}
                diagnostic_failures = {}
                completed_validators = set()
                groups = [('p2', [0]), ('remaining_in_original_order', list(range(1, len(validators))))]
                trial['groups'] = [{'name': label, 'status': 'not_started',
                                    'stages': [validators[i][0] for i in indices]}
                                   for label, indices in groups]

                def run_group(group, indices, done):
                    group_started = None
                    current_index = indices[0]
                    try:
                        with lock:
                            group['status'] = 'running'
                        # Entry diagnostics belong inside the failure boundary too.
                        group_started = time.perf_counter()
                        for current_index in indices:
                            stage, function = validators[current_index]

                            def validate(function=function, index=current_index):
                                try:
                                    result = function()
                                except BaseException as exc:
                                    with lock:
                                        validator_failures[index] = (exc, exc.__traceback__)
                                    raise
                                with lock:
                                    completed_validators.add(index)
                                return result

                            phase(report, output, stage, validate, records=trial['phases'], lock=lock)
                        with lock:
                            group['status'] = 'completed'
                    except BaseException as exc:
                        with lock:
                            if current_index not in validator_failures:
                                diagnostic_failures.setdefault(current_index, (exc, exc.__traceback__))
                            group['status'] = 'error'
                    finally:
                        try:
                            with lock:
                                try:
                                    if group_started is not None:
                                        group['wall_seconds'] = elapsed_seconds(group_started)
                                    else:
                                        group['diagnostic_status'] = 'unavailable'
                                    write_report(output, report)
                                except BaseException as exc:
                                    group['status'] = 'error'
                                    diagnostic_failures.setdefault(current_index, (exc, exc.__traceback__))
                        finally:
                            done.set()

                readers = [(threading.Thread(target=run_group, args=(group, indices, done)), done)
                           for group, (_, indices), done in zip(
                               trial['groups'], groups, [threading.Event(), threading.Event()])]
                # Never propagate a failure until every started reader has stopped.
                # Cleanup remains inside the original run_bounded process-group
                # deadline; an interruption cannot start a new trial or extend it.
                started_readers = []
                try:
                    for thread, done in readers:
                        started_readers.append((thread, done))
                        thread.start()
                except BaseException as exc:
                    diagnostic_failures.setdefault(len(validators), (exc, exc.__traceback__))
                finally:
                    for thread, done in started_readers:
                        # Registration precedes start: an interrupted return can
                        # hide a live reader. A missing ident means no launch was
                        # observed, so do not join a never-started thread.
                        if thread.ident is None:
                            continue
                        # The completion event also covers a join interrupted while
                        # Python's Thread bookkeeping prematurely reports stopped.
                        while not done.is_set() or thread.is_alive():
                            try:
                                thread.join(timeout=.05)
                            except BaseException as exc:
                                diagnostic_failures.setdefault(len(validators), (exc, exc.__traceback__))
                            if not done.is_set():
                                try:
                                    done.wait(.05)
                                except BaseException as exc:
                                    diagnostic_failures.setdefault(len(validators), (exc, exc.__traceback__))
                # A real validation failure always outranks diagnostics in any
                # group; ties among validators retain their original order.
                failures = validator_failures or diagnostic_failures
                if failures:
                    error, traceback = failures[min(failures)]
                    raise error.with_traceback(traceback)
                if (completed_validators != set(range(len(validators)))
                        or any(group['status'] != 'completed' for group in trial['groups'])):
                    raise ProfileError('parallel validation groups did not fully complete')
            else:
                for stage, function in validators:
                    phase(report, output, stage, function, records=trial['phases'])
            if (len(trial['phases']) != len(validators)
                    or {p['stage'] for p in trial['phases']} != {stage for stage, _ in validators}
                    or any(p['status'] != 'ok' for p in trial['phases'])):
                raise ProfileError('required validator phases did not fully complete')
            trial['validation_wall_seconds'] = elapsed_seconds(validation_started)
            phase(report, output, 'core_exact_readback', readback, records=trial['phases'])
            trial['validation_and_readback_wall_seconds'] = elapsed_seconds(validation_started)
            trial['phase_seconds_sum'] = sum(p['seconds'] for p in trial['phases'])
        except BaseException as exc:
            primary = (exc, exc.__traceback__)
        finally:
            try:
                check_state(trial, 'after')
                trial['wall_seconds'] = elapsed_seconds(started)
            except BaseException as exc:
                trial['state_or_timing_error'] = {'type': type(exc).__name__}
                if isinstance(exc, ProfileError): trial['state_or_timing_error']['reason'] = str(exc)
                if primary is None: primary = (exc, exc.__traceback__)
            trial['status'] = 'error' if primary else 'completed'
            if primary:
                experiment['status'] = 'error'
                trial['error'] = {'type': type(primary[0]).__name__}
            try:
                write_report(output, report)
            except BaseException as exc:
                report.setdefault('diagnostic_errors', []).append(
                    {'stage': name, 'operation': 'report', 'type': type(exc).__name__})
                if primary is None:
                    primary = (exc, exc.__traceback__)
                    trial['status'] = experiment['status'] = 'error'
        if primary:
            raise primary[0].with_traceback(primary[1])
    experiment.update(status='completed', history_sequences_equal=True,
                      source_after=source_state(source), source_unchanged=True)
    write_report(output, report)


def history_batch_probe(checkout, source, source_sha, report, output, deadline):
    """Compare native Git I/O only; never replace a frozen validation function."""
    experiment = report['history_batch_experiment']
    path = experiment['path']

    def state():
        return {'head': git(checkout, 'rev-parse', 'HEAD').decode().strip(),
                'tracked_status': git(checkout, 'status', '--porcelain', '--untracked-files=no'),
                'histories': history_oids(checkout),
                'path_bytes': (checkout / path).read_bytes(),
                'source': source_state(source)}

    before = state()
    if before['head'] != source_sha or not before['histories'][path]:
        raise ProfileError('patch probe requires the fixed nonempty history')
    experiment.update(status='running', head_before=before['head'],
                      history_before=history_manifest(before['histories']))
    write_report(output, report)
    primary = None
    try:
        spec = importlib.util.spec_from_file_location(
            'offline_git_history_batch_probe', source / 'git_history_batch_probe.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError('patch probe has no remaining diagnostic budget')
        result = phase(report, output, 'history_patch_byte_probe',
                       lambda: module.run_probe(checkout, path, timeout_seconds=remaining,
                                                allow_object_directory=object_directory(source)))
        experiment['result'] = result
        if (not isinstance(result, dict) or result.get('status') != 'completed'
                or result.get('patch_bytes_equal') is not True
                or result.get('ordered_oids_equal') is not True
                or type(result.get('commit_count')) is not int
                or result['commit_count'] != len(before['histories'][path])):
            raise ProfileError('patch probe did not establish complete byte equivalence')
    except BaseException as exc:
        primary = (exc, exc.__traceback__)
    finally:
        try:
            after = state()
            experiment.update(head_after=after['head'],
                              history_after=history_manifest(after['histories']))
            if after != before:
                raise ProfileError('patch probe changed its fixed source or tracked inputs')
            experiment['state_unchanged'] = True
        except BaseException as exc:
            experiment['state_error'] = {'type': type(exc).__name__}
            if primary is None:
                primary = (exc, exc.__traceback__)
        experiment['status'] = 'error' if primary else 'completed'
        if primary:
            experiment['error'] = {'type': type(primary[0]).__name__}
        try:
            write_report(output, report)
        except BaseException as exc:
            if primary is None:
                primary = (exc, exc.__traceback__)
                experiment['status'] = 'error'
    if primary:
        raise primary[0].with_traceback(primary[1])


def p2_equivalence_experiment(checkout, source, source_sha, report, output,
                              runtime, validators, readback, core_snapshot):
    """Measure the real entry point; retain full event equality separately."""
    experiment = report['p2_equivalence_experiment']
    engine = importlib.import_module('p2_paired_execution_v3_1')
    helper = importlib.import_module('runtime_p2_history')
    for module, filename in ((engine, 'p2_paired_execution_v3_1.py'),
                             (helper, 'runtime_p2_history.py'),
                             (runtime, 'runtime_deadline_service.py')):
        if Path(module.__file__).resolve() != (checkout / filename).resolve():
            raise ProfileError('P2 implementation imported outside the isolated fixed checkout')

    def state():
        return {'head': git(checkout, 'rev-parse', 'HEAD').decode().strip(),
                'tracked_status': git(checkout, 'status', '--porcelain', '--untracked-files=no'),
                'histories': history_oids(checkout), 'core': core_snapshot(),
                'p2_bytes': (checkout / HISTORY_PATHS[-1]).read_bytes(),
                'implementation': {name: file_sha256(checkout / name) for name in
                    (*IMPLEMENTATION_FILES, 'p2_paired_execution_v3_1.py')},
                'source': source_state(source)}

    baseline = state()
    if baseline['head'] != source_sha or not baseline['histories'][HISTORY_PATHS[-1]]:
        raise ProfileError('P2 equivalence requires fixed complete nonempty history')
    experiment.update(status='running', source_before=baseline['source'],
                      implementation_sha256=baseline['implementation'])

    def check_state(trial, suffix):
        observed = state()
        trial['head_' + suffix] = observed['head']
        trial['history_' + suffix] = history_manifest(observed['histories'])
        trial['core_inputs_' + suffix] = input_manifest(observed['core'])
        if observed != baseline:
            raise ProfileError('P2 equivalence changed fixed history, inputs, implementation or source')

    def original_p2():
        engine.guard(checkout)
        events = engine.history_guard(checkout)
        for _ in engine.event_books(checkout, events):
            pass
        return events

    def event_bytes(events):
        if not isinstance(events, list):
            raise ProfileError('full ordered P2 events must be a list')
        return json.dumps(events, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')

    reference_bytes = None
    for name in ('reference_before', 'candidate', 'reference_after'):
        trial = {'name': name, 'status': 'running', 'phases': [], 'equivalence_phases': []}
        experiment['trials'].append(trial)
        write_report(output, report)
        started = time.perf_counter()
        primary = None
        try:
            check_state(trial, 'before')
            validation_started = time.perf_counter()
            if name == 'candidate':
                captured = io.StringIO()
                def candidate_p2():
                    with redirect_stdout(captured):
                        outcome = runtime.validate_p2(checkout)
                    if outcome is not None:
                        raise ProfileError('candidate validate_p2 contract must return None')
                phase(report, output, 'preflight_validate_p2', candidate_p2,
                      records=trial['phases'])
            else:
                events = phase(report, output, 'preflight_validate_p2', original_p2,
                               records=trial['phases'])
            for stage, function in validators[1:]:
                phase(report, output, stage, function, records=trial['phases'])
            trial['validation_wall_seconds'] = elapsed_seconds(validation_started)
            phase(report, output, 'core_exact_readback', readback, records=trial['phases'])
            trial['validation_and_readback_wall_seconds'] = elapsed_seconds(validation_started)
            trial['phase_seconds_sum'] = sum(p['seconds'] for p in trial['phases'])
            if name == 'candidate':
                raw = captured.getvalue()
                if len(raw) > 65536:
                    raise ProfileError('candidate validation diagnostics exceed the bounded report')
                logs = []
                for line in raw.splitlines():
                    try: entry = json.loads(line)
                    except ValueError: continue
                    if isinstance(entry, dict) and entry.get('kind') == 'runtime_p2_history_validation':
                        logs.append(entry)
                if (len(logs) != 1 or logs[0].get('validation_status') != 'ok'
                        or logs[0].get('status') != 'ok'
                        or logs[0].get('mode') not in ('native_batch', 'legacy_full_fallback')
                        or logs[0].get('module_sha256') != baseline['implementation']['runtime_p2_history.py']):
                    raise ProfileError('candidate entry point diagnostics do not identify a completed implementation')
                trial['candidate_diagnostics'] = logs[0]
                diagnostics = {}
                events = phase(report, output, 'candidate_history_equivalence',
                               lambda: helper.history_guard(checkout, engine=engine, diagnostics=diagnostics),
                               records=trial['equivalence_phases'])
                trial['equivalence_diagnostics'] = diagnostics
                if (diagnostics.get('status') != 'ok'
                        or diagnostics.get('mode') not in ('native_batch', 'legacy_full_fallback')):
                    raise ProfileError('additional event equivalence validation did not complete')
            encoded = event_bytes(events)
            trial['events'] = {'count': len(events), 'sha256': hashlib.sha256(encoded).hexdigest()}
            if reference_bytes is None:
                reference_bytes = encoded
            elif encoded != reference_bytes:
                raise ProfileError('complete P2 event values or order differ from the original reference')
        except BaseException as exc:
            primary = (exc, exc.__traceback__)
        finally:
            try:
                check_state(trial, 'after')
                trial['wall_seconds'] = elapsed_seconds(started)
            except BaseException as exc:
                trial['state_or_timing_error'] = {'type': type(exc).__name__}
                if primary is None: primary = (exc, exc.__traceback__)
            trial['status'] = 'error' if primary else 'completed'
            if primary:
                experiment['status'] = 'error'
                trial['error'] = {'type': type(primary[0]).__name__}
            try:
                write_report(output, report)
            except BaseException as exc:
                if primary is None:
                    primary = (exc, exc.__traceback__)
                    trial['status'] = experiment['status'] = 'error'
        if primary:
            raise primary[0].with_traceback(primary[1])
    experiment.update(status='completed', events_equal=True, source_unchanged=True,
                      source_after=source_state(source))
    write_report(output, report)


def worker(source, source_sha, output, workspace, use_index_experiment=False, timeout_seconds=360,
           use_parallel_validation_experiment=False, use_history_batch_experiment=False,
           use_p2_equivalence_experiment=False):
    source, output, workspace = Path(source).resolve(), Path(output).resolve(), Path(workspace).resolve()
    if output.is_relative_to(source) or workspace.is_relative_to(source):
        raise ProfileError('worker output and workspace must be outside the original checkout')
    deadline = time.perf_counter() + timeout_seconds
    report = initial_report(source_sha, use_index_experiment, use_parallel_validation_experiment,
                            use_history_batch_experiment, use_p2_equivalence_experiment)
    write_report(output, report)
    lock_down_worker()
    checkout = workspace / 'checkout'
    original_implementations = {}
    baseline_source = None
    try:
        baseline_source = source_state(source)
        setup = time.perf_counter()
        report['full_history_commit_count'] = prepare_checkout(source, source_sha, checkout)
        report['setup_seconds'] = time.perf_counter() - setup
        install_implementation_overlays(checkout, source, report, original_implementations)
        if (use_p2_equivalence_experiment
                and report['implementation_overlays']['runtime_p2_history.py']['measured_sha256'] is None):
            raise ProfileError('P2 equivalence requires the current runtime helper')
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
        elif use_parallel_validation_experiment:
            parallel_experiment(checkout, source, source_sha, report, output, validators, readback,
                                lambda: core_inputs(checkout, publisher, evidence))
        elif use_p2_equivalence_experiment:
            p2_equivalence_experiment(checkout, source, source_sha, report, output,
                                     runtime, validators, readback,
                                     lambda: core_inputs(checkout, publisher, evidence))
        else:
            for name, function in validators:
                phase(report, output, name, function)
            report['frozen_validation_completed'] = True
            phase(report, output, 'core_exact_readback', readback)
        report['frozen_validation_completed'] = True
        if use_history_batch_experiment:
            # The original validators above measured the current runtime wrapper.
            # The separate Git-byte probe must inspect a clean fixed-commit tree.
            restore_implementation_overlays(checkout, original_implementations, report)
            history_batch_probe(checkout, source, source_sha, report, output, deadline)
        after = core_inputs(checkout, publisher, evidence)
        report['core_inputs_after'] = input_manifest(after)
        if after != expected:
            raise ProfileError('core input bytes changed during readback')
        report['input_bytes_unchanged'] = True
        restore_implementation_overlays(checkout, original_implementations, report)
        report['tracked_worktree_clean'] = not git(checkout, 'status', '--porcelain', '--untracked-files=no').strip()
        if not report['tracked_worktree_clean']:
            raise ProfileError('tracked inputs changed during read-only diagnostic')
        report['local_readback_verified'] = True
        report['status'] = 'completed'
    except Exception as exc:
        report['status'] = 'error'
        if use_index_experiment: report['index_experiment']['status'] = 'error'
        if use_parallel_validation_experiment: report['parallel_validation_experiment']['status'] = 'error'
        if use_history_batch_experiment and report['history_batch_experiment']['status'] != 'not_started':
            report['history_batch_experiment']['status'] = 'error'
        if use_p2_equivalence_experiment and report['p2_equivalence_experiment']['status'] != 'not_started':
            report['p2_equivalence_experiment']['status'] = 'error'
        report['error'] = {'type': type(exc).__name__}
        if isinstance(exc, ProfileError):
            report['error']['reason'] = str(exc)
    finally:
        try:
            if original_implementations:
                restore_implementation_overlays(checkout, original_implementations, report)
            if baseline_source is not None and source_state(source) != baseline_source:
                raise ProfileError('original source implementation or state changed')
        except Exception as exc:
            report['status'] = 'error'
            report['restore_or_source_error'] = {'type': type(exc).__name__}
            report.setdefault('error', {'type': type(exc).__name__})
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


def run_profile(source, source_sha, output, timeout_seconds, index_experiment=False,
                parallel_validation_experiment=False, history_batch_experiment=False,
                p2_equivalence_experiment=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source):
        raise ValueError('diagnostic output must be outside the original checkout')
    if (os.name != 'posix' or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 360):
        raise ValueError('Ubuntu diagnostic requires a timeout in (0, 360] seconds')
    if not re.fullmatch('[0-9a-f]{40}', source_sha):
        raise ValueError('a complete fixed commit SHA is required')
    report = initial_report(source_sha, index_experiment, parallel_validation_experiment,
                            history_batch_experiment, p2_equivalence_experiment)
    write_report(output, report)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='runtime-publication-profile-') as workspace:
        command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                              '--worker', '--source-repo', str(source), '--source-sha', source_sha,
                              '--output', str(output), '--workspace', workspace,
                              '--timeout-seconds', str(timeout_seconds)]
        if index_experiment: command.append('--index-experiment')
        if parallel_validation_experiment: command.append('--parallel-validation-experiment')
        if history_batch_experiment: command.append('--history-batch-experiment')
        if p2_equivalence_experiment: command.append('--p2-equivalence-experiment')
        result = run_bounded(command, safe_environment(), timeout_seconds)
    try: report = json.loads(output.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        report = initial_report(source_sha, index_experiment, parallel_validation_experiment,
                                history_batch_experiment, p2_equivalence_experiment)
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
        if parallel_validation_experiment:
            experiment = report['parallel_validation_experiment']
            experiment['status'] = 'timeout'
            for trial in experiment['trials']:
                if trial['status'] == 'running': trial['status'] = 'timeout'
                for record in trial['phases'] + trial['groups']:
                    if record['status'] == 'running': record['status'] = 'timeout'
        if history_batch_experiment and report['history_batch_experiment']['status'] == 'running':
            report['history_batch_experiment']['status'] = 'timeout'
        if p2_equivalence_experiment:
            experiment = report['p2_equivalence_experiment']
            if experiment['status'] == 'running': experiment['status'] = 'timeout'
            for trial in experiment['trials']:
                if trial['status'] == 'running': trial['status'] = 'timeout'
                for record in trial['phases'] + trial['equivalence_phases']:
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
    experiments = parser.add_mutually_exclusive_group()
    experiments.add_argument('--index-experiment', action='store_true',
                        help='compare native Git commit-graph off/on/off in the same isolated checkout')
    experiments.add_argument('--parallel-validation-experiment', action='store_true',
                             help='compare complete serial/parallel/serial validation offline')
    experiments.add_argument('--history-batch-experiment', action='store_true',
                             help='compare native Git patch bytes after original full validation')
    experiments.add_argument('--p2-equivalence-experiment', action='store_true',
                             help='compare full original and current P2 validation with ordered event equivalence')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--source-repo', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--workspace', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.source_repo, args.source_sha, args.output, args.workspace,
                      args.index_experiment, args.timeout_seconds, args.parallel_validation_experiment,
                      args.history_batch_experiment, args.p2_equivalence_experiment)
    code = run_profile(Path(__file__).resolve().parent, args.source_sha, args.output,
                       args.timeout_seconds, args.index_experiment, args.parallel_validation_experiment,
                       args.history_batch_experiment, args.p2_equivalence_experiment)
    print(args.output.read_text(encoding='utf-8'))
    return code


if __name__ == '__main__':
    raise SystemExit(main())

"""Offline reference/candidate/reference profiling of publication history checks.

All trials retain the current native P2 validator. Additional complete receipt
and ordered paired-record equivalence passes are outside main trial timings.
This never publishes, fetches, issues, scores, or measures network transport.
"""
import argparse
from contextlib import redirect_stdout
import hashlib
import importlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time


_spec = importlib.util.spec_from_file_location(
    '_runtime_core_profile_support', Path(__file__).with_name('runtime_publication_profile.py'))
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)
ProfileError = support.ProfileError
OVERLAYS = ('runtime_deadline_service.py', 'runtime_publication_state.py',
            'runtime_paired_history.py', 'runtime_p2_history.py')
AUDIT_INPUTS = ('data/direction_v42/forward_registry.csv',
                'data/direction_v42/forward_scores.csv', 'data/quote_evidence_v2/bindings.csv',
                'data/paired_forward_v1/pairs.jsonl', 'data/hourly_research/ohlc.csv')
REPORT_PATHS = ('data/p0_operations/latest.json', 'data/p0_operations/publication_latest.json',
                'data/paired_forward_v1/audit_latest.json',
                'data/stage5/forward_validation/latest.json',
                'data/stage5/forward_validation_v2/latest.json')
DIAGNOSTICS = {
    'runtime_p2_history_validation': 'runtime_p2_history.py',
    'runtime_publication_state_validation': 'runtime_publication_state.py',
    'runtime_paired_history_validation': 'runtime_paired_history.py',
}


def _code_hashes(root):
    return {path.name: support.file_sha256(path) for path in sorted(root.glob('*.py'))}


def _protected_hashes(root):
    paths = set(AUDIT_INPUTS + REPORT_PATHS + support.HISTORY_PATHS)
    paths.add('data/direction_v42/forward_latest.json')
    paths.update(path.relative_to(root).as_posix() for path in (root/'reports').rglob('*') if path.is_file())
    paths.update(path.relative_to(root).as_posix()
                 for path in (root/'data/quote_evidence_v2/snapshots').glob('*/*.json'))
    return {path: support.file_sha256(root/path) for path in sorted(paths)}


def source_snapshot(source):
    """Exact protected bytes and ordered histories; object files use metadata."""
    source = Path(source).resolve()
    objects = support.object_directory(source)
    inventory = {}
    for path in sorted(objects.rglob('*')):
        if path.is_file():
            stat = path.stat()
            inventory[path.relative_to(objects).as_posix()] = {
                'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    git_files = {}
    for name in ('config', 'index', 'HEAD', 'packed-refs'):
        path = Path(support.git(source, 'rev-parse', '--git-path', name).decode().strip())
        path = path if path.is_absolute() else source/path
        git_files[name] = support.file_sha256(path)
    return {'state': support.source_state(source), 'implementation': _code_hashes(source),
            'protected_sha256': _protected_hashes(source),
            'histories': support.history_oids(source),
            'refs_sha256': hashlib.sha256(support.git(source, 'show-ref')).hexdigest(),
            'git_files_sha256': git_files, 'objects_metadata': inventory}


def _source_summary(snapshot):
    objects = _value_manifest(snapshot['objects_metadata'])
    return {'state': snapshot['state'], 'refs_sha256': snapshot['refs_sha256'],
            'git_files_sha256': snapshot['git_files_sha256'],
            'code_manifest': _value_manifest(snapshot['implementation']),
            'protected_bytes_manifest': _value_manifest(snapshot['protected_sha256']),
            'audit_input_sha256': {path: snapshot['protected_sha256'][path] for path in AUDIT_INPUTS},
            'history_sequences': support.history_manifest(snapshot['histories']),
            'objects_metadata': {'files': objects['count'], 'manifest_sha256': objects['sha256']}}


def _deny_source_writes(source):
    """Keep Python writes and writable Git commands outside source/shared objects."""
    roots = (source, support.object_directory(source), Path(support.git(
        source, 'rev-parse', '--absolute-git-dir').decode().strip()).resolve())

    def protected(value, cwd=None):
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        path = Path(os.fsdecode(value))
        if not path.is_absolute():
            path = (Path(cwd) if cwd else Path.cwd())/path
        path = path.resolve()
        return any(path.is_relative_to(root) for root in roots)

    def reject(event, args):
        if event == 'open':
            path, mode, flags = args
            writable = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC))
            if writable and protected(path):
                raise PermissionError('diagnostic source writes denied')
        elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.chown', 'os.utime',
                       'os.truncate', 'os.rename', 'os.link', 'os.symlink'):
            if any(protected(value) for value in args[:2]):
                raise PermissionError('diagnostic source writes denied')
        elif event == 'subprocess.Popen':
            executable, argv, cwd, environment = args
            if Path(os.fsdecode(executable)).name != 'git' or not isinstance(argv, (list, tuple)):
                return  # The mature worker policy rejects other executables.
            command = list(argv[1:])
            location = Path(cwd).resolve() if cwd else Path.cwd()
            while command and command[0] in ('-C', '-c'):
                option, value = command[:2]
                if option == '-C':
                    location = (location/str(value)).resolve()
                command = command[2:]
            if any(str(x).startswith(('--git-dir', '--work-tree')) for x in command):
                raise PermissionError('diagnostic Git repository redirection denied')
            allowed = {'rev-parse', 'cat-file', 'status', 'log', 'show', 'diff',
                       'merge-base', 'ls-files', 'ls-tree', 'rev-list', 'show-ref', '--version'}
            if command and command[0] not in allowed | {'clone', 'hash-object'} and any(
                    protected(value, location) for value in command[1:] if not str(value).startswith('-')):
                raise PermissionError('diagnostic explicit source Git writes denied')
            if any(str(value).startswith('--output') for value in command) or (
                    command and command[0] == 'hash-object' and '-w' in command):
                raise PermissionError('diagnostic Git output writes denied')
            if protected(location):
                readonly = bool(command) and command[0] in allowed
                readonly |= bool(command) and command[0] == 'hash-object' and '-w' not in command
                readonly |= (bool(command) and command[0] == 'clone'
                             and not protected(command[-1], location))
                if not readonly:
                    raise PermissionError('diagnostic source Git writes denied')
    sys.addaudithook(reject)


def _initial(source_sha):
    return {'schema_version': 1, 'status': 'running', 'source_sha': source_sha,
            'scope': 'reference_candidate_reference_current_p2_full_publication_history',
            'clock': 'monotonic', 'phases': [], 'trials': [],
            'equivalence': {'status': 'not_started', 'phases': [], 'receipts': {}},
            'os_cache_controlled': False,
            'cache_caveat': 'OS caches are uncontrolled; original/candidate/original use one checkout in that order',
            'timing_scope': 'main_validation_wall_excludes_setup_state_inventory_and_additional_equivalence',
            'p2_validator_same_current_native_implementation_in_all_trials': True,
            'python_network_denied': True, 'git_protocols_allowed': ['file'],
            'lazy_fetch_disabled': True, 'source_writes_denied': True,
            'source_object_inventory_scope': 'names_sizes_mtime_ns_not_pack_content_hashes',
            'production_network_time_measured': False, 'production_publication_verified': False,
            'frozen_validation_completed': False, 'overlays_restored': False}


def _error(exc):
    result = {'type': type(exc).__name__}
    if isinstance(exc, ProfileError):
        result['reason'] = str(exc)
    if exc.__cause__ is not None:
        result['cause_type'] = type(exc.__cause__).__name__
    return result


class _BoundedOutput(io.StringIO):
    def write(self, value):
        if self.tell() + len(value) > 65536:
            raise ProfileError('validation stdout exceeds the bounded diagnostic limit')
        return super().write(value)


def _diagnostics(captured, implementation, candidate):
    raw = captured.getvalue()
    logs = []
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get('kind') in DIAGNOSTICS:
            logs.append(entry)
    wanted = list(DIAGNOSTICS) if candidate else ['runtime_p2_history_validation']
    if [entry.get('kind') for entry in logs] != wanted:
        raise ProfileError('validation diagnostics are missing or out of order')
    for entry in logs:
        if entry.get('status') != 'ok' or entry.get('validation_status') != 'ok' or entry.get('mode') != 'native_batch':
            raise ProfileError('validation did not complete through the native implementation')
        if entry.get('module_sha256') != implementation[DIAGNOSTICS[entry['kind']]]:
            raise ProfileError('validation diagnostics identify a different implementation')
    return {'characters': len(raw), 'sha256': hashlib.sha256(raw.encode()).hexdigest(), 'diagnostics': logs}


def _value_manifest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode()
    return {'count': len(value), 'sha256': hashlib.sha256(encoded).hexdigest()}


def _install(checkout, source, report, originals):
    report['implementation_overlays'] = {}
    for name in OVERLAYS:
        target, current = checkout/name, source/name
        measured = support.file_sha256(current)
        if measured is None:
            raise ProfileError('current runtime implementation is missing: ' + name)
        fixed = support.file_sha256(target)
        originals[name] = target.read_bytes() if fixed is not None else None
        raw = current.read_bytes()
        if hashlib.sha256(raw).hexdigest() != measured:
            raise ProfileError('source implementation changed during overlay installation')
        target.write_bytes(raw)
        report['implementation_overlays'][name] = {'fixed_sha256': fixed, 'measured_sha256': measured}
    report['runtime_code_sha256'] = report['implementation_overlays']['runtime_deadline_service.py']['measured_sha256']


def _trials(checkout, report, output, runtime, publisher, pair, implementation, check_state, readback):
    reference = None
    for name in ('reference_before', 'candidate', 'reference_after'):
        trial = {'name': name, 'status': 'running', 'phases': []}
        report['trials'].append(trial)
        support.write_report(output, report)
        started = time.perf_counter()
        primary = None
        try:
            check_state(trial, 'before')
            captured = _BoundedOutput()
            validation_started = time.perf_counter()
            with redirect_stdout(captured):
                result = support.phase(report, output, 'preflight_validate_p2',
                                       lambda: runtime.validate_p2(checkout), records=trial['phases'])
                if result is not None:
                    raise ProfileError('current validate_p2 contract must return None')
                validator = runtime.validate_publication_state if name == 'candidate' else publisher.validate_state
                result = support.phase(report, output, 'preflight_validate_state',
                                       lambda: validator(checkout), records=trial['phases'])
                if result is not None:
                    raise ProfileError('publication state validation contract must return None')
                support.phase(report, output, 'preflight_pair_guard', lambda: pair.guard(checkout), records=trial['phases'])
                validator = runtime.validate_paired_history if name == 'candidate' else pair.history_guard
                records = support.phase(report, output, 'preflight_pair_history_guard',
                                        lambda: validator(checkout), records=trial['phases'])
                trial['validation_wall_seconds'] = support.elapsed_seconds(validation_started)
                support.phase(report, output, 'core_exact_readback', readback, records=trial['phases'])
                trial['validation_and_readback_wall_seconds'] = support.elapsed_seconds(validation_started)
            trial['candidate_stdout'] = _diagnostics(captured, implementation, name == 'candidate')
            if not isinstance(records, list):
                raise ProfileError('complete ordered paired records must be a list')
            trial['paired_records'] = _value_manifest(records)
            if reference is None:
                reference = records
            elif records != reference or _value_manifest(records) != _value_manifest(reference):
                raise ProfileError('complete paired records or their order differ from the original reference')
            trial['phase_seconds_sum'] = sum(p['seconds'] for p in trial['phases'])
        except BaseException as exc:
            primary = (exc, exc.__traceback__)
        finally:
            try:
                check_state(trial, 'after')
                trial['wall_seconds'] = support.elapsed_seconds(started)
            except BaseException as exc:
                trial['state_or_timing_error'] = _error(exc)
                if primary is None:
                    primary = (exc, exc.__traceback__)
            trial['status'] = 'error' if primary else 'completed'
            if primary:
                trial['error'] = _error(primary[0])
            try:
                support.write_report(output, report)
            except BaseException as exc:
                if primary is None:
                    primary = (exc, exc.__traceback__)
                    trial['status'] = 'error'
        if primary:
            raise primary[0].with_traceback(primary[1])


def _equivalence(checkout, report, output, publisher, pair, state_helper, pair_helper):
    section = report['equivalence']
    section['status'] = 'running'
    base, frozen, evidence = publisher.base, publisher.frozen, publisher.evidence
    primary_failed = False
    try:
        for relative, columns in ((base.REG, frozen.FCOLS), (base.SCORES, frozen.SCOLS),
                                  (evidence.BINDINGS, evidence.BCOLS)):
            current = frozen.read_rows(checkout/relative, columns)
            original = support.phase(report, output, 'reference_receipts:' + relative,
                lambda: base.history_receipts(checkout, relative, columns, current), records=section['phases'])
            diagnostics = {}
            candidate = support.phase(report, output, 'candidate_receipts:' + relative,
                lambda: state_helper.history_receipts(checkout, relative, columns, current, diagnostics=diagnostics),
                records=section['phases'])
            if diagnostics.get('status') != 'ok' or diagnostics.get('mode') != 'native_batch':
                raise ProfileError('receipt equivalence did not use a completed native implementation')
            if not isinstance(original, dict) or not isinstance(candidate, dict) or original != candidate or _value_manifest(original) != _value_manifest(candidate):
                raise ProfileError('complete first-commit receipts differ from the original reference')
            section['receipts'][relative] = {'count': len(original),
                'reference_sha256': _value_manifest(original)['sha256'],
                'candidate_sha256': _value_manifest(candidate)['sha256'], 'equal': True,
                'candidate_diagnostics': diagnostics}
        original = support.phase(report, output, 'reference_paired_records',
                                 lambda: pair.history_guard(checkout), records=section['phases'])
        diagnostics = {}
        candidate = support.phase(report, output, 'candidate_paired_records',
            lambda: pair_helper.history_guard(checkout, cohort=pair, diagnostics=diagnostics), records=section['phases'])
        if diagnostics.get('status') != 'ok' or diagnostics.get('mode') != 'native_batch':
            raise ProfileError('paired equivalence did not use a completed native implementation')
        if not isinstance(original, list) or not isinstance(candidate, list) or original != candidate or _value_manifest(original) != _value_manifest(candidate):
            raise ProfileError('complete paired records or their order differ from the original reference')
        section['paired_records'] = {'count': len(original),
            'reference_sha256': _value_manifest(original)['sha256'],
            'candidate_sha256': _value_manifest(candidate)['sha256'], 'equal': True,
            'candidate_diagnostics': diagnostics}
        section['status'] = 'completed'
    except BaseException:
        primary_failed = True
        section['status'] = 'error'
        raise
    finally:
        try:
            support.write_report(output, report)
        except BaseException as exc:
            section['status'] = 'error'
            section['diagnostic_error'] = _error(exc)
            if not primary_failed:
                raise


def worker(source, source_sha, output, workspace):
    source, output, workspace = Path(source).resolve(), Path(output).resolve(), Path(workspace).resolve()
    if output.is_relative_to(source) or workspace.is_relative_to(source) or source.is_relative_to(workspace):
        raise ProfileError('worker output and workspace must be outside the original checkout')
    report = _initial(source_sha)
    support.write_report(output, report)
    support.lock_down_worker()
    _deny_source_writes(source)
    checkout = workspace/'checkout'
    originals = {}
    baseline_source = None
    primary = None
    try:
        baseline_source = source_snapshot(source)
        count = support.phase(report, output, 'prepare_checkout',
                              lambda: support.prepare_checkout(source, source_sha, checkout))
        report['full_history_commit_count'] = count
        patterns = checkout/'.git/info/sparse-checkout'
        patterns.write_text(patterns.read_text() + '/reports/\n/data/p0_operations/\n/data/hourly_research/\n')
        support.git(checkout, 'read-tree', '-mu', 'HEAD')
        fixed_code = _code_hashes(checkout)
        _install(checkout, source, report, originals)
        sys.path.insert(0, str(checkout))
        names = ('runtime_deadline_service', 'publish_forward_evidence', 'paired_forward_cohort',
                 'quote_evidence_v2', 'runtime_publication_state', 'runtime_paired_history',
                 'runtime_p2_history')
        modules = {name: importlib.import_module(name) for name in names}
        for name, module in modules.items():
            if Path(module.__file__).resolve() != (checkout/(name+'.py')).resolve():
                raise ProfileError('implementation imported outside the isolated fixed checkout')
        runtime, publisher, pair = (modules[name] for name in names[:3])
        expected = support.core_inputs(checkout, publisher, modules['quote_evidence_v2'])
        report['core_inputs_before'] = support.input_manifest(expected)
        implementation = {name: support.file_sha256(checkout/name) for name in OVERLAYS}
        report['implementation_sha256'] = implementation

        def state():
            return {'head': support.git(checkout, 'rev-parse', 'HEAD').decode().strip(),
                    'tracked_status': support.git(checkout, 'status', '--porcelain', '--untracked-files=no'),
                    'histories': support.history_oids(checkout),
                    'protected': _protected_hashes(checkout), 'code': _code_hashes(checkout),
                    'core': support.core_inputs(checkout, publisher, modules['quote_evidence_v2'])}
        baseline = state()
        if baseline['head'] != source_sha or any(not values for values in baseline['histories'].values()):
            raise ProfileError('fixed complete nonempty histories are required')
        if any(baseline['protected'].get(path) is None for path in AUDIT_INPUTS):
            raise ProfileError('complete five audit inputs are required')
        report['history_sequences_before'] = support.history_manifest(baseline['histories'])

        def check_state(section, suffix):
            observed = state()
            section['head_' + suffix] = observed['head']
            section['history_' + suffix] = support.history_manifest(observed['histories'])
            section['core_inputs_' + suffix] = support.input_manifest(observed['core'])
            if observed != baseline:
                raise ProfileError('fixed history, core inputs, reports or implementation changed')

        def readback():
            if support.core_inputs(checkout, publisher, modules['quote_evidence_v2']) != expected:
                raise ProfileError('core input bytes changed during validation')
            if not publisher.verify_readback(checkout, 'HEAD', expected):
                raise ProfileError('fixed HEAD core readback mismatch')

        _trials(checkout, report, output, runtime, publisher, pair, implementation, check_state, readback)
        _equivalence(checkout, report, output, publisher, pair,
                     modules['runtime_publication_state'], modules['runtime_paired_history'])
        check_state(report, 'after_equivalence')
        report['core_inputs_after'] = support.input_manifest(support.core_inputs(checkout, publisher, modules['quote_evidence_v2']))
        report['history_sequences_after'] = support.history_manifest(support.history_oids(checkout))
        support.restore_implementation_overlays(checkout, originals, report)
        if _code_hashes(checkout) != fixed_code:
            raise ProfileError('fixed source code was not restored')
        report['tracked_worktree_clean'] = not support.git(checkout, 'status', '--porcelain', '--untracked-files=no').strip()
        if not report['tracked_worktree_clean']:
            raise ProfileError('tracked inputs changed during the read-only diagnostic')
        report.update(status='completed', frozen_validation_completed=True,
                      input_bytes_unchanged=True, local_readback_verified=True)
    except BaseException as exc:
        primary = exc
        report['status'] = 'error'
        report['error'] = _error(exc)
    finally:
        try:
            if originals:
                support.restore_implementation_overlays(checkout, originals, report)
            report['source_unchanged'] = baseline_source is not None and source_snapshot(source) == baseline_source
            if not report['source_unchanged']:
                raise ProfileError('original source implementation or state changed')
        except BaseException as exc:
            report['status'] = 'error'
            report['restore_or_source_error'] = _error(exc)
            if primary is None:
                report['error'] = _error(exc)
        if report['status'] != 'completed':
            report['frozen_validation_completed'] = False
        try:
            support.write_report(output, report)
        except BaseException:
            if primary is not None:
                raise primary
            raise
    return 0 if report['status'] == 'completed' else 1


def _mark_running(value, status):
    if isinstance(value, dict):
        if value.get('status') == 'running':
            value['status'] = status
        for child in value.values():
            _mark_running(child, status)
    elif isinstance(value, list):
        for child in value:
            _mark_running(child, status)


def run_profile(source, source_sha, output, timeout_seconds=360):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source):
        raise ValueError('diagnostic output must be outside the original checkout')
    if os.name != 'posix' or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 360:
        raise ValueError('Ubuntu diagnostic requires a timeout in (0, 360] seconds')
    if not re.fullmatch('[0-9a-f]{40}', source_sha):
        raise ValueError('a complete fixed commit SHA is required')
    before = source_snapshot(source)
    report = _initial(source_sha)
    support.write_report(output, report)
    started = time.perf_counter()
    failure = None
    workspace = None
    try:
        with tempfile.TemporaryDirectory(prefix='runtime-core-history-profile-') as workspace:
            command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--worker',
                       '--source-root', str(source), '--ref', source_sha, '--output', str(output),
                       '--workspace', workspace]
            result = support.run_bounded(command, support.safe_environment(), timeout_seconds)
    except BaseException as exc:
        failure = exc
        result = {'returncode': 1, 'timed_out': False}
    try:
        report = json.loads(output.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        report = _initial(source_sha)
    if result['timed_out']:
        report['status'] = 'timeout'
        _mark_running(report, 'timeout')
    elif result['returncode'] != 0 or report['status'] != 'completed':
        report['status'] = 'error'
    if failure is not None:
        report.setdefault('error', _error(failure))
    report['source_before'] = _source_summary(before)
    try:
        after = source_snapshot(source)
        report['source_after'] = _source_summary(after)
        report['source_unchanged'] = after == before
        if not report['source_unchanged']:
            raise ProfileError('original source implementation or state changed')
    except BaseException as exc:
        report['source_unchanged'] = False
        report['source_verification_error'] = _error(exc)
        report.setdefault('error', _error(exc))
        if report['status'] != 'timeout':
            report['status'] = 'error'
    if report['status'] != 'completed':
        report['frozen_validation_completed'] = False
    report.update(total_seconds=time.perf_counter()-started, timeout_seconds=timeout_seconds,
                  workspace_cleaned=workspace is not None and not Path(workspace).exists())
    support.write_report(output, report)
    return 0 if report['status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True, type=Path)
    parser.add_argument('--ref', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--timeout-seconds', type=float, default=360)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--workspace', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.workspace is None:
            parser.error('worker workspace is required')
        return worker(args.source_root, args.ref, args.output, args.workspace)
    code = run_profile(args.source_root, args.ref, args.output, args.timeout_seconds)
    print(args.output.read_text(encoding='utf-8'))
    return code


if __name__ == '__main__':
    raise SystemExit(main())

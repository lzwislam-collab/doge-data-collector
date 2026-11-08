"""Slow audits in an isolated, pinned local clone; only two reports return.

The worker cannot publish, issue, score, or touch the live ledger. Its temporary
audit caches are discarded. The live serial writer installs complete matching
reports, and frozen P2 checks still decide eligibility at capture time.
"""
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

INPUTS = ('data/direction_v42/forward_registry.csv',
          'data/direction_v42/forward_scores.csv', 'data/quote_evidence_v2/bindings.csv',
          'data/paired_forward_v1/pairs.jsonl', 'data/hourly_research/ohlc.csv')
OUTPUTS = ('data/p0_operations/latest.json', 'data/paired_forward_v1/audit_latest.json')
SCRIPTS = ('forward_validation_v2.py', 'p0_operational_audit.py', 'paired_cohort_audit.py')
EXCLUDED_PREFIX = 'data/plus_evidence_v1_0/sources/'
# Reviewed dependency closure at efe0857e. A change falls back to the original
# full checkout; this is an optimization eligibility check, not a new freeze.
AUDITED_BLOBS = {
    'direction_baseline_v42.py': '4bd0417548ab8251ec9d92e168db46d6ec1068bb',
    'direction_forward_v42.py': '368abc641db6d5ea6496ac4db624554161034665',
    'forward_execution_evidence.py': '7c2fd41af2fc5e52afd7fcabe2ea909b9457b7bb',
    'forward_validation.py': 'f0831c7d38581543f84a85639c6d69a7305ff382',
    'forward_validation_v2.py': '3adbdcc43c5dbdb57ecd482b1d4138e720146ba1',
    'okx_hourly_research_history.py': '6a403d175243d8766650e2b53ede52660ea94386',
    'p0_operational_audit.py': 'c615bdc665d24481569f545922d3d7aa04594d60',
    'p1_adaptive_v2.py': '2fb92e67a1915e552eb9a236f202fc424c9b1b50',
    'p1_model_competition.py': '20fb6c70b64e6d46c6ec58525301eec72e08705d',
    'paired_cohort_audit.py': 'a6bdf0c54ffcf648384c756d400e742ab57a1d61',
    'paired_forward_cohort.py': '02e6b3ba51f063ba17cddcb98ff6445a08235c97',
    'paired_selection_v1.py': 'd53e2588439b9e3fce9485b011edb6d2eaf95466',
    'publish_forward_evidence.py': 'd982f4f8bd278d7a1505073bc35dd71b80eeb095',
    'quote_capture_retry.py': '0cc8f737040c704ca123b36eb4ffa556bc316046',
    'quote_evidence_v2.py': '7d1e62e60f3aa78949ef1d5d81d74086d1907c91',
    'spot_plus_quote_watch.py': '132ac443c8421960b19c43e237b9e638609c5b23',
    'stage5_70pct_audit.py': '1095886e5e493676e15a2de5c2bf0bd1abbd7dc9',
    'stage5_forward_quality.py': '8c9717dd2fabb49ed3ba1c3f4c1df1281d13f51b',
    'stored_history_integrity.py': '0257e7cf48845452a1341c5b691bb28bebcd62bc',
    'data/stage5/pre_registration.json': '9d2671df7cec81a7ca7165bf35e3d25444de653d',
}


def emit_progress(phase, event, **metadata):
    """Bounded metadata only, flushed to the inherited log; never raw reports."""
    try:
        if phase not in ('clone', 'checkout', 'result', 'worker', 'parent_close')+SCRIPTS:
            return
        if event not in ('begin', 'end', 'before_cleanup'): return
        row = {'kind': 'runtime_source_audit_progress', 'schema_version': 1,
               'at_utc': datetime.now(timezone.utc).isoformat(), 'phase': phase, 'event': event}
        allowed = {
            'status': ('ok', 'error'), 'result_status': ('ok', 'failed'),
            'mode': ('full_checkout', 'full_checkout_fallback', 'sparse_source_archive_excluded'),
            'fallback_reason': ('dependency_identity_changed', 'retained_symlink'),
            'reason': ('shutdown', 'inputs_changed', 'audit_timeout', 'result_consumed'),
            'worker_state': ('running', 'exited', 'absent'),
        }
        for key, values in allowed.items():
            if metadata.get(key) in values: row[key] = metadata[key]
        for key in ('input_head', 'result_sha256'):
            value = metadata.get(key)
            if isinstance(value, str) and re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', value): row[key] = value
        for key in ('worker_pid', 'worker_returncode', 'result_bytes'):
            value = metadata.get(key)
            if type(value) is int: row[key] = value
        for key in ('result_present', 'result_json_valid', 'termination_requested'):
            if type(metadata.get(key)) is bool: row[key] = metadata[key]
        seconds = metadata.get('seconds')
        if type(seconds) in (float, int) and math.isfinite(seconds) and seconds >= 0:
            row['seconds'] = seconds
        values = metadata.get('input_fingerprints')
        if isinstance(values, dict) and set(values) == set(INPUTS) and all(
                isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) for v in values.values()):
            row['input_fingerprints'] = values
        error = metadata.get('error')
        if error is not None:
            kinds = (ValueError, RuntimeError, TimeoutError, OSError, KeyError, TypeError,
                     AssertionError, subprocess.CalledProcessError, subprocess.TimeoutExpired)
            row['error_type'] = type(error).__name__ if type(error) in kinds else 'Exception'
        print(json.dumps(row, sort_keys=True, allow_nan=False), flush=True)
    except Exception:
        pass


def elapsed(start):
    try: return time.monotonic()-start
    except Exception: return None


def command(*args, cwd):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                          text=True, encoding='utf-8', timeout=240).stdout


def timed_command(phase, *args, cwd, **metadata):
    start = time.monotonic()
    emit_progress(phase, 'begin', **metadata)
    try:
        output = command(*args, cwd=cwd)
    except BaseException as exc:
        emit_progress(phase, 'end', status='error', seconds=elapsed(start), error=exc, **metadata)
        raise
    seconds = time.monotonic()-start
    emit_progress(phase, 'end', status='ok', seconds=seconds, **metadata)
    return output, seconds


def checkout_eligibility(snapshot, head):
    # Read pinned Git objects before materializing files. The full tree also
    # detects retained symlinks that could otherwise hide an excluded target.
    tree = subprocess.run(['git', 'ls-tree', '-r', '-z', '--full-tree', head],
                          cwd=snapshot, check=True, capture_output=True, timeout=240).stdout
    blobs = {}
    retained_symlink = False
    for entry in tree.split(b'\0'):
        if not entry: continue
        metadata, raw_path = entry.split(b'\t', 1)
        mode, kind, oid = metadata.decode('ascii').split()
        path = os.fsdecode(raw_path)
        if mode == '120000' and not path.startswith(EXCLUDED_PREFIX): retained_symlink = True
        if kind == 'blob' and mode in ('100644', '100755'): blobs[path] = oid
    if retained_symlink: return 'retained_symlink'
    if any(blobs.get(path) != oid for path, oid in AUDITED_BLOBS.items()):
        return 'dependency_identity_changed'
    return None


def prepare_checkout(source, checkout, head, *, exclude_source_archive=True):
    """Shared full object history; optionally omit one reviewed worktree tree."""
    if type(exclude_source_archive) is not bool: raise ValueError('explicit checkout mode required')
    source, checkout = Path(source), Path(checkout)
    _, clone_seconds = timed_command('clone', 'git', 'clone', '--shared', '--no-checkout',
                                     str(source), str(checkout), cwd=source, input_head=head)
    eligibility_seconds = 0.0
    fallback = None
    if exclude_source_archive:
        start = time.monotonic()
        fallback = checkout_eligibility(checkout, head)
        eligibility_seconds = time.monotonic()-start
    sparse = exclude_source_archive and fallback is None
    mode = ('sparse_source_archive_excluded' if sparse else
            'full_checkout_fallback' if fallback else 'full_checkout')
    metadata = {'input_head': head, 'mode': mode, 'fallback_reason': fallback}
    start = time.monotonic()
    emit_progress('checkout', 'begin', **metadata)
    try:
        command('git', 'config', '--local', 'core.sparseCheckout', str(sparse).lower(), cwd=checkout)
        command('git', 'config', '--local', 'core.sparseCheckoutCone', 'false', cwd=checkout)
        command('git', 'config', '--local', 'index.sparse', 'false', cwd=checkout)
        if sparse:
            path = checkout/'.git/info/sparse-checkout'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('/*\n!/data/plus_evidence_v1_0/sources/\n', encoding='utf-8')
        command('git', '-c', 'core.autocrlf=false', 'checkout', '--detach', head, cwd=checkout)
    except BaseException as exc:
        emit_progress('checkout', 'end', status='error', seconds=elapsed(start), error=exc, **metadata)
        raise
    checkout_seconds = time.monotonic()-start
    emit_progress('checkout', 'end', status='ok', seconds=checkout_seconds, **metadata)
    result = {'clone_seconds': clone_seconds, 'checkout_seconds': checkout_seconds,
              'eligibility_seconds': eligibility_seconds,
              'requested_exclude_source_archive': exclude_source_archive,
              'exclude_source_archive': sparse, 'mode': mode}
    if fallback: result['fallback_reason'] = fallback
    return result


def fingerprint(root):
    return {p: hashlib.sha256((Path(root)/p).read_bytes()).hexdigest() for p in INPUTS}


def result_metadata(directory):
    metadata = {'result_present': False, 'result_json_valid': False}
    if directory is None: return metadata
    try: body = (Path(directory.name)/'result.json').read_bytes()
    except OSError: return metadata
    metadata.update(result_present=True, result_sha256=hashlib.sha256(body).hexdigest(), result_bytes=len(body))
    try: result = json.loads(body)
    except (ValueError, UnicodeError): return metadata
    metadata['result_json_valid'] = True
    if isinstance(result, dict):
        metadata.update(result_status=result.get('status'), input_head=result.get('input_head'),
                        input_fingerprints=result.get('inputs'))
    return metadata


def audit_snapshot(root, head, temporary_parent=None, *, exclude_source_archive=True):
    with tempfile.TemporaryDirectory(prefix='doge-source-audit-', dir=temporary_parent) as name:
        snapshot = Path(name)/'snapshot'
        checkout = prepare_checkout(root, snapshot, head, exclude_source_archive=exclude_source_archive)
        inputs = fingerprint(snapshot)
        timings = []
        for script in SCRIPTS:
            _, seconds = timed_command(script, sys.executable, script, cwd=snapshot, input_head=head)
            timings.append({'stage': script, 'seconds': seconds})
        outputs = {p: (snapshot/p).read_text(encoding='utf-8') for p in OUTPUTS}
        return {'status': 'ok', 'input_head': head, 'inputs': inputs,
                'outputs': outputs, 'timings': timings, 'checkout': checkout}


def accept(root, result):
    if result.get('status') != 'ok': raise ValueError('isolated audit failed: '+str(result.get('error'))[:500])
    if result['inputs'] != fingerprint(root): return {'status': 'discarded_input_changed'}
    if set(result['outputs']) != set(OUTPUTS): raise ValueError('unexpected isolated audit outputs')
    for p in OUTPUTS:
        if json.loads(result['outputs'][p]).get('status') != 'ok': raise ValueError('blocked isolated report')
    # Called by the only live writer, never by the background process. If a disk
    # error interrupts the pair installation, the caller fails closed before book.
    for p in OUTPUTS:
        path = Path(root)/p
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result['outputs'][p], encoding='utf-8')
    return {'status': 'ready', 'input_head': result['input_head'], 'timings': result['timings']}


class Auditor:
    def __init__(self, root):
        self.root = Path(root)
        self.process = None
        self.directory = None
        self.inputs = None

    def poll(self):
        # An issuer/target watcher can change inputs while a read-only audit is
        # running. Never spend the next ticks waiting for an obsolete snapshot.
        if self.process is not None and self.inputs != fingerprint(self.root):
            self.close(reason='inputs_changed')
        if self.process is not None:
            if self.process.poll() is None:
                if time.monotonic()-self.started > 600:
                    self.close(reason='audit_timeout')
                    raise TimeoutError('isolated source audit exceeded 600 seconds')
                return {'status': 'pending'}
            try:
                if self.process.returncode: raise ValueError('isolated audit worker exited unsuccessfully')
                result = json.loads((Path(self.directory.name)/'result.json').read_text(encoding='utf-8'))
                accepted = accept(self.root, result)
            finally:
                self.close(reason='result_consumed')
            if accepted['status'] == 'ready': return accepted
        # Snapshot only a committed source version. No live .git/index, ledger,
        # or output directory is shared with the worker.
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.root).decode().strip()
        self.directory = tempfile.TemporaryDirectory(prefix='doge-audit-result-')
        self.started = time.monotonic()
        self.inputs = fingerprint(self.root)
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
            str(self.root), head, str(Path(self.directory.name)/'result.json')],
            cwd=self.root,
            start_new_session=True)
        emit_progress('worker', 'begin', input_head=head, worker_pid=self.process.pid,
                      input_fingerprints=self.inputs)
        return {'status': 'pending', 'input_head': head}

    def close(self, reason='shutdown'):
        process = self.process
        if process is not None or self.directory is not None:
            running = process is not None and process.poll() is None
            metadata = {'reason': reason, 'worker_state': 'running' if running else 'exited' if process else 'absent',
                        'worker_pid': process.pid if process else None, 'termination_requested': False}
            metadata.update(result_metadata(self.directory))
            emit_progress('parent_close', 'begin', **metadata)
            try:
                if running:
                    try:
                        if os.name == 'posix': os.killpg(process.pid, signal.SIGTERM)
                        else: process.terminate()
                        metadata['termination_requested'] = True
                    except ProcessLookupError:
                        pass  # It finished between poll() and the termination request.
                    process.wait(timeout=10)
                # Capture the final result again before cleanup, including a
                # completion racing with shutdown; never install it here.
                metadata.update(result_metadata(self.directory))
                metadata['worker_returncode'] = process.returncode if process else None
                emit_progress('parent_close', 'before_cleanup', **metadata)
                self.process = None
                if self.directory is not None: self.directory.cleanup()
                self.directory = None
            except BaseException as exc:
                emit_progress('parent_close', 'end', status='error', error=exc, **metadata)
                raise
            emit_progress('parent_close', 'end', status='ok', **metadata)


def main():
    try: result = audit_snapshot(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]).parent)
    except Exception as exc: result = {'status': 'failed', 'error': type(exc).__name__+': '+str(exc)[:500]}
    emit_progress('result', 'begin', input_head=sys.argv[2])
    start = time.monotonic()
    try:
        body = json.dumps(result).encode('utf-8')
        Path(sys.argv[3]).write_bytes(body)
    except Exception as exc:
        emit_progress('result', 'end', status='error', error=exc, seconds=elapsed(start), input_head=sys.argv[2])
        return 1
    emit_progress('result', 'end', status='ok' if result['status'] == 'ok' else 'error',
                  result_status=result['status'], result_sha256=hashlib.sha256(body).hexdigest(),
                  result_bytes=len(body), input_head=sys.argv[2], input_fingerprints=result.get('inputs'),
                  seconds=elapsed(start))
    return 0


if __name__ == '__main__': raise SystemExit(main())

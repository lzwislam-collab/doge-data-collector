"""Full P2 history verification with native Git batching at the runtime boundary.

The frozen producer and its registration remain unchanged. Only patch transport
differs: original ordered log, original show diff options, original append/hash/
prefix rules. No validation result survives a call. Unsupported native output
falls back to the entire original history_guard, never a partially checked tail.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid


IMPLEMENTATION = 'runtime_p2_history_native_batch_v1'
BATCH_SIZE = 128


class _NativeCompatibility(Exception):
    pass


def _diagnostic(diagnostics, **values):
    # Diagnostic failures cannot change the original validation result/exception.
    try:
        if isinstance(diagnostics, dict):
            diagnostics.update(values)
    except Exception:
        pass


def _increment(diagnostics, key):
    try:
        diagnostics[key] = diagnostics.get(key, 0) + 1
    except Exception:
        pass


def _batch_output(root, oids, marker, engine):
    pretty = 'tformat:%x00' + marker[1:].decode('ascii') + '%H%x00'
    return engine.source.publication.command(
        root, 'show', '--no-walk=unsorted', '--format=' + pretty,
        '--unified=0', '--no-renames', *oids, '--', engine.EVENTS)


def _frames(raw, marker, oids):
    if raw and not raw.startswith(marker):
        raise _NativeCompatibility('framing')
    positions = {oid: index for index, oid in enumerate(oids)}
    previous = -1
    parsed = {}
    for block in raw.split(marker)[1:]:
        encoded, separator, body = block.partition(b'\0\n')
        try:
            oid = encoded.decode('ascii')
        except UnicodeDecodeError as exc:
            raise _NativeCompatibility('framing') from exc
        if not separator or oid not in positions or positions[oid] <= previous:
            raise _NativeCompatibility('oid_sequence')
        if body:
            if not body.startswith(b'\n'):
                raise _NativeCompatibility('framing')
            patch = body[1:]
        else:
            patch = b''
        # NUL payloads, marker collisions and unfamiliar formats use the complete
        # original verifier; never guess delimiters or normalize patch contents.
        if b'\0' in patch or (patch and not patch.endswith(b'\n')):
            raise _NativeCompatibility('framing')
        parsed[oid] = patch
        previous = positions[oid]
    return parsed


def _native_patches(root, oids, engine, diagnostics):
    marker = ('\0RUNTIME-P2:' + uuid.uuid4().hex + ':').encode('ascii')
    for offset in range(0, len(oids), BATCH_SIZE):
        chunk = oids[offset:offset + BATCH_SIZE]
        _increment(diagnostics, 'native_batch_calls')
        parsed = _frames(_batch_output(root, chunk, marker, engine), marker, chunk)
        for oid in chunk:
            if oid not in parsed:
                # Native show may omit a path-empty commit. Only an independently
                # read, genuinely empty original patch may fill that position.
                _increment(diagnostics, 'empty_fallback_show_calls')
                patch = engine.source.publication.command(
                    root, 'show', '--format=', '--unified=0', '--no-renames',
                    oid, '--', engine.EVENTS)
                if patch != b'':
                    raise _NativeCompatibility('nonempty_omission')
                parsed[oid] = patch
            yield parsed[oid]


def history_guard(root, engine=None, diagnostics=None):
    """Return the same complete events, or original validation error, per call."""
    if engine is None:
        import p2_paired_execution_v3_1 as engine
    try:
        for key in ('fallback_reason', 'ordered_oids_sha256'):
            if isinstance(diagnostics, dict):
                diagnostics.pop(key, None)
    except Exception:
        pass
    _diagnostic(diagnostics, implementation=IMPLEMENTATION, mode='not_started',
                status='running', attempted_commit_count=0, native_batch_calls=0,
                empty_fallback_show_calls=0)
    succeeded = False
    try:
        # Original precedence: current event validation precedes any history read.
        current = engine.read_events(root)
        previous = []
        last_hash = None
        _diagnostic(diagnostics, mode='native_batch')
        try:
            revisions = engine.source.publication.command(
                root, 'log', '--reverse', '--format=%H', '--', engine.EVENTS).decode().splitlines()
            if (len(set(revisions)) != len(revisions) or any(
                    not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid) for oid in revisions)):
                raise _NativeCompatibility('oid_sequence')
            _diagnostic(diagnostics, attempted_commit_count=len(revisions),
                        ordered_oids_sha256=hashlib.sha256(
                            ('\n'.join(revisions) + '\n').encode()).hexdigest())
            for patch_bytes in _native_patches(root, revisions, engine, diagnostics):
                patch = patch_bytes.decode('utf-8')
                in_hunk = False
                for line in patch.splitlines():
                    if line.startswith('@@'):
                        match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
                        if (not match or int(match[2] or 1) != 0
                                or int(match[1]) != len(previous)
                                or int(match[3]) != len(previous) + 1):
                            raise ValueError('P2 historic tail removed/revised')
                        in_hunk = True
                        continue
                    if in_hunk:
                        if not line.startswith('+'):
                            raise ValueError('P2 historic record modified')
                        row = json.loads(line[1:])
                        check = dict(row)
                        actual = check.pop('record_sha256')
                        if actual != engine.digest(check) or row['previous_sha256'] != last_hash:
                            raise ValueError('P2 historic event hash mismatch')
                        previous.append(row)
                        last_hash = actual
        except (_NativeCompatibility, subprocess.CalledProcessError, OSError) as exc:
            reason = str(exc) if isinstance(exc, _NativeCompatibility) else 'git_read_error'
            _diagnostic(diagnostics, mode='legacy_full_fallback', fallback_reason=reason)
            # A fresh whole call preserves current-read precedence and every old
            # commit check even when batching failed after earlier chunks.
            result = engine.history_guard(root)
            succeeded = True
            return result
        if current[:len(previous)] != previous:
            raise ValueError('P2 committed event tail removed/changed')
        succeeded = True
        return current
    finally:
        _diagnostic(diagnostics, status='ok' if succeeded else 'error')


def emit_diagnostics(diagnostics, validation_succeeded):
    """Small archive/stdout evidence; no files, payloads, or validation effects."""
    try:
        row = {key: diagnostics[key] for key in (
            'implementation', 'mode', 'status', 'attempted_commit_count',
            'ordered_oids_sha256', 'native_batch_calls', 'empty_fallback_show_calls',
            'fallback_reason') if key in diagnostics}
        row.update(kind='runtime_p2_history_validation', implementation=IMPLEMENTATION,
                   module_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   validation_status='ok' if validation_succeeded else 'error')
        print(json.dumps(row, sort_keys=True, allow_nan=False), flush=True)
    except Exception:
        pass

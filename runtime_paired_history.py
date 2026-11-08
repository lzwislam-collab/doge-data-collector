"""Complete paired history checks with native Git patch transport batching.

Only transport changes. Every call reads the original ordered history and keeps
the frozen hunk, chain, duplicate, prefix and individual-record predicates. A
transport or dependency incompatibility restarts the entire original verifier.
No validation result survives a call and no frozen module is modified.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid


IMPLEMENTATION = 'runtime_paired_history_native_batch_v1'
BATCH_SIZE = 128


class _NativeCompatibility(Exception):
    pass


def _diagnostic(diagnostics, **values):
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


def _compatible(cohort):
    try:
        dependencies = (
            (cohort, '02e6b3ba51f063ba17cddcb98ff6445a08235c97'),
            (cohort.publication, 'd982f4f8bd278d7a1505073bc35dd71b80eeb095'),
            (cohort.depth, '7c2fd41af2fc5e52afd7fcabe2ea909b9457b7bb'),
        )
        for module, expected in dependencies:
            raw = Path(module.__file__).read_bytes()
            if hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != expected:
                return False
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _batch_output(root, oids, marker, cohort):
    pretty = 'tformat:%x00' + marker[1:].decode('ascii') + '%H%x00'
    return cohort.publication.command(
        root, 'show', '--no-walk=unsorted', '--format=' + pretty,
        '--unified=0', '--no-renames', *oids, '--', cohort.REG)


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
        if b'\0' in patch or (patch and not patch.endswith(b'\n')):
            raise _NativeCompatibility('framing')
        parsed[oid] = patch
        previous = positions[oid]
    return parsed


def _native_patches(root, oids, cohort, diagnostics):
    marker = ('\0RUNTIME-PAIRED:' + uuid.uuid4().hex + ':').encode('ascii')
    for offset in range(0, len(oids), BATCH_SIZE):
        chunk = oids[offset:offset + BATCH_SIZE]
        _increment(diagnostics, 'native_batch_calls')
        parsed = _frames(_batch_output(root, chunk, marker, cohort), marker, chunk)
        for oid in chunk:
            if oid not in parsed:
                _increment(diagnostics, 'empty_fallback_show_calls')
                patch = cohort.publication.command(
                    root, 'show', '--format=', '--unified=0', '--no-renames',
                    oid, '--', cohort.REG)
                if patch != b'':
                    raise _NativeCompatibility('nonempty_omission')
                parsed[oid] = patch
            yield parsed[oid]


def history_guard(root, cohort=None, diagnostics=None):
    """Return complete original records or the original validation exception."""
    if cohort is None:
        import paired_forward_cohort as cohort
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
        if not _compatible(cohort):
            _diagnostic(diagnostics, mode='legacy_full_fallback',
                        fallback_reason='dependency_identity_changed')
            result = cohort.history_guard(root)
            succeeded = True
            return result
        # Current chain errors must precede all Git history reads, as before.
        current = cohort.read_chain(root / cohort.REG)
        previous = []
        seen = set()
        last_hash = None
        _diagnostic(diagnostics, mode='native_batch')
        try:
            try:
                revisions = cohort.publication.command(
                    root, 'log', '--reverse', '--format=%H', '--', cohort.REG).decode().splitlines()
            except UnicodeDecodeError as exc:
                raise _NativeCompatibility('oid_sequence') from exc
            if len(set(revisions)) != len(revisions) or any(
                    not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid) for oid in revisions):
                raise _NativeCompatibility('oid_sequence')
            _diagnostic(diagnostics, attempted_commit_count=len(revisions),
                        ordered_oids_sha256=hashlib.sha256(
                            ('\n'.join(revisions) + '\n').encode()).hexdigest())
            for patch_bytes in _native_patches(root, revisions, cohort, diagnostics):
                try:
                    patch = patch_bytes.decode('utf-8')
                except UnicodeDecodeError as exc:
                    raise _NativeCompatibility('patch_encoding') from exc
                additions = []
                in_hunk = False
                for line in patch.splitlines():
                    if line.startswith('@@'):
                        match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
                        if not match:
                            raise ValueError('unknown paired history patch')
                        old_start = int(match[1])
                        old_count = int(match[2] or 1)
                        new_start = int(match[3])
                        if old_count != 0 or old_start != len(previous) or new_start != len(previous) + 1:
                            raise ValueError('historic paired chain removed/changed away from append boundary')
                        in_hunk = True
                        continue
                    if in_hunk:
                        if not line.startswith('+'):
                            raise ValueError('historic paired chain removed/changed')
                        additions.append(line[1:])
                for line in additions:
                    row = json.loads(line)
                    check = dict(row)
                    actual = check.pop('record_sha256')
                    fid = row['production']['forecast_id']
                    if actual != cohort.digest(check) or row['previous_sha256'] != last_hash or fid in seen:
                        raise ValueError('historical appended paired hash/duplicate mismatch')
                    seen.add(fid)
                    last_hash = actual
                    previous.append(row)
        except (_NativeCompatibility, subprocess.SubprocessError, OSError) as exc:
            reason = str(exc) if isinstance(exc, _NativeCompatibility) else 'git_read_error'
            _diagnostic(diagnostics, mode='legacy_full_fallback', fallback_reason=reason)
            result = cohort.history_guard(root)
            succeeded = True
            return result
        if current[:len(previous)] != previous:
            raise ValueError('committed paired records removed/changed')
        for row in current:
            path = cohort.record_path(root / cohort.REG, row['production']['forecast_id'])
            if not path.exists() or json.loads(path.read_text(encoding='utf-8')) != row:
                raise ValueError('individual paired record absent/changed')
        succeeded = True
        return current
    finally:
        _diagnostic(diagnostics, status='ok' if succeeded else 'error')


def emit_diagnostics(diagnostics, validation_succeeded):
    """Bounded metadata only; diagnostics cannot change audit decisions."""
    try:
        row = {key: diagnostics[key] for key in (
            'implementation', 'mode', 'status', 'attempted_commit_count',
            'ordered_oids_sha256', 'native_batch_calls', 'empty_fallback_show_calls',
            'fallback_reason') if key in diagnostics}
        row.update(kind='runtime_paired_history_validation', implementation=IMPLEMENTATION,
                   module_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   validation_status='ok' if validation_succeeded else 'error')
        print(json.dumps(row, sort_keys=True, allow_nan=False), flush=True)
    except Exception:
        pass

"""Offline native-Git I/O equivalence experiment; never a production validator.

The serial command is the frozen P2 history_guard command, byte for byte.
Batching adds only --no-walk=unsorted and a tformat OID envelope. Git documents
that unsorted preserves supplied commit order (git-rev-list --no-walk), and
git-show documents its normal combined merge diff. No diff options are changed.
https://git-scm.com/docs/git-rev-list#Documentation/git-rev-list.txt---no-walksortedunsorted
https://git-scm.com/docs/git-show

Every patch is compared directly with both serial reads, including empty patches.
No result is cached between invocations. The caller must provide an isolated,
full-history checkout and retain its existing process-group hard deadline.
"""
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
import time


class ProbeError(RuntimeError):
    """Incomplete, changed, unsafe, or non-equivalent diagnostic inputs."""


class ProbeTimeout(ProbeError):
    """The single invocation's total deadline expired."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _path(value):
    value = str(value)
    parsed = PurePosixPath(value)
    if (not value or parsed.is_absolute() or '..' in parsed.parts
            or any(char in value for char in '\x00\n\r:*?[]\\')
            or value != parsed.as_posix() or value == '.'):
        raise ProbeError('path must be one literal relative repository path')
    return value


def _oids(values):
    values = list(values)
    if (not values or len(set(values)) != len(values)
            or any(not isinstance(oid, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid)
                   for oid in values)):
        raise ProbeError('history must contain distinct full commit OIDs')
    return values


class _Git:
    def __init__(self, root, timeout_seconds, allow_object_directory=None):
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 360:
            raise ValueError('timeout_seconds must be finite and in (0, 360]')
        self.root = Path(root).resolve()
        self.allowed_objects = (Path(allow_object_directory).resolve()
                                if allow_object_directory is not None else None)
        self.deadline = time.perf_counter() + timeout_seconds
        self.environment = os.environ.copy()
        for key in ('GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE',
                    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                    'GIT_SHALLOW_FILE', 'GIT_REPLACE_REF_BASE', 'GIT_NAMESPACE',
                    'GIT_GRAFT_FILE', 'GIT_EXTERNAL_DIFF', 'GIT_NO_REPLACE_OBJECTS'):
            if self.environment.get(key):
                raise ProbeError('alternative Git view or external diff environment: ' + key)
        # Keep effective diff/config semantics, but disallow locks, prompts and
        # implicit network fetching. Never detach children from the caller's PGID.
        self.environment.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0',
                                GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='')

    def remaining(self):
        remaining = self.deadline - time.perf_counter()
        if remaining <= 0:
            raise ProbeTimeout('history batch probe deadline expired')
        return remaining

    def run(self, *args):
        try:
            result = subprocess.run(['git', *args], cwd=self.root, env=self.environment,
                                    capture_output=True, timeout=self.remaining())
        except subprocess.TimeoutExpired as exc:
            # subprocess.run kills and waits for its Git child. All children also
            # remain covered by the outer diagnostic's process-group hard kill.
            raise ProbeTimeout('Git read exceeded history probe deadline') from exc
        except OSError as exc:
            raise ProbeError('Git read could not execute') from exc
        self.remaining()
        if result.returncode:
            raise ProbeError('Git read failed: ' + args[0] + ' (exit ' + str(result.returncode) + ')')
        return result.stdout

    def check_repository(self):
        config = self.run('config', '--null', '--list')
        for record in config.split(b'\0'):
            key, _, value = record.partition(b'\n')
            key = key.decode('utf-8', 'replace').lower()
            if (key == 'diff.external' or (key.startswith('diff.')
                    and key.endswith(('.textconv', '.command')))):
                raise ProbeError('external diff/textconv is outside this read-only probe')
            if key == 'core.fsmonitor' and value.lower() not in (b'', b'false', b'no', b'0'):
                raise ProbeError('external fsmonitor is outside this read-only probe')
            if key == 'extensions.partialclone' or (key.startswith('remote.')
                    and key.endswith('.promisor') and value.lower() not in (b'false', b'no', b'0')):
                raise ProbeError('partial clone cannot prove complete local history')
        if self.run('rev-parse', '--is-shallow-repository').strip() != b'false':
            raise ProbeError('shallow history is unsupported')
        if self.run('for-each-ref', '--format=%(refname)', 'refs/replace/'):
            raise ProbeError('replace refs are unsupported')
        filename = self.git_path('info/grafts')
        if filename.exists() and filename.read_bytes().strip():
            raise ProbeError('alternative Git view is unsupported: info/grafts')
        self.alternates_snapshot()
        return config

    def git_path(self, relative):
        filename = Path(os.fsdecode(self.run('rev-parse', '--git-path', relative).strip()))
        return filename if filename.is_absolute() else self.root / filename

    def alternates_snapshot(self):
        filename = self.git_path('objects/info/alternates')
        raw = filename.read_bytes() if filename.exists() else b''
        if raw:
            lines = raw.splitlines()
            if (self.allowed_objects is None or len(lines) != 1 or not lines[0]
                    or lines[0].startswith(b'"') or b'\0' in lines[0]):
                raise ProbeError('alternates require one explicit trusted object directory')
            alternate = Path(os.fsdecode(lines[0]))
            if not alternate.is_absolute():
                alternate = filename.parent.parent / alternate
            if not alternate.is_dir() or alternate.resolve() != self.allowed_objects:
                raise ProbeError('alternates do not match the trusted object directory')
            # Do not accept a second, unreviewed object store transitively.
            nested = alternate / 'info' / 'alternates'
            if nested.exists() and nested.read_bytes().strip():
                raise ProbeError('nested object directory alternates are unsupported')
        return raw


def _history(git, path):
    # This is deliberately the original history selection, not rev-list or a
    # simplified, date-limited, parent-only, cached or count-only substitute.
    return _oids(git.run('log', '--reverse', '--format=%H', '--', path).decode('ascii').splitlines())


def _show(git, oid, path):
    return git.run('show', '--format=', '--unified=0', '--no-renames', oid, '--', path)


def _state(git, path):
    status = git.run('status', '--porcelain=v1', '--untracked-files=no')
    if status:
        raise ProbeError('input changed or checkout is not clean')
    return (git.run('rev-parse', 'HEAD'), tuple(_history(git, path)),
            git.run('config', '--null', '--list'), status, git.alternates_snapshot())


def verify_batch_output(output, marker, oids, originals):
    """Parse exact tformat framing and reject all nonempty omissions or changes.

    Empty omissions are returned as missing; read_batch must verify each using
    a fresh original show before restoring its position. No bytes are fabricated.
    """
    oids = _oids(oids)
    if set(originals) != set(oids) or not marker.startswith(b'\0'):
        raise ProbeError('invalid original patch manifest or frame marker')
    if any(marker in patch for patch in originals.values()):
        raise ProbeError('frame marker collides with original patch content')
    if output and not output.startswith(marker):
        raise ProbeError('unexpected bytes before first frame')
    positions = {oid: index for index, oid in enumerate(oids)}
    parsed = {}
    previous = -1
    for block in output.split(marker)[1:]:
        raw_oid, separator, body = block.partition(b'\0\n')
        try:
            oid = raw_oid.decode('ascii')
        except UnicodeDecodeError as exc:
            raise ProbeError('invalid OID frame') from exc
        if not separator or oid not in positions or positions[oid] <= previous:
            raise ProbeError('incomplete, duplicate, unknown or reordered OID frame')
        if body:
            if not body.startswith(b'\n'):
                raise ProbeError('invalid patch framing separator')
            patch = body[1:]
        else:
            patch = b''
        if patch != originals[oid]:
            raise ProbeError('native batch patch bytes differ for ' + oid)
        parsed[oid] = patch
        previous = positions[oid]
    if any(oid not in parsed and originals[oid] != b'' for oid in oids):
        raise ProbeError('native batch omitted a nonempty patch')
    return parsed


def read_batch(root, path, oids, originals, timeout_seconds=360, chunk_size=128,
               allow_object_directory=None):
    """Read all supplied commits natively, preserving exact supplied order."""
    git = _Git(root, timeout_seconds, allow_object_directory)
    path, oids = _path(path), _oids(oids)
    if not isinstance(chunk_size, int) or not 1 <= chunk_size <= 1024:
        raise ProbeError('chunk_size must be in [1, 1024]')
    if set(originals) != set(oids):
        raise ProbeError('original patch manifest is incomplete')
    git.check_repository()
    nonce = secrets.token_hex(16)
    marker = ('\0DPP-HISTORY:' + nonce + ':').encode('ascii')
    pretty = 'tformat:%x00DPP-HISTORY:' + nonce + ':%H%x00'
    result = {}
    stats = {'batch_show_count': 0, 'fallback_count': 0, 'returned_batch_count': 0}
    for offset in range(0, len(oids), chunk_size):
        chunk = oids[offset:offset + chunk_size]
        expected = {oid: originals[oid] for oid in chunk}
        raw = git.run('show', '--no-walk=unsorted', '--format=' + pretty,
                      '--unified=0', '--no-renames', *chunk, '--', path)
        stats['batch_show_count'] += 1
        parsed = verify_batch_output(raw, marker, chunk, expected)
        stats['returned_batch_count'] += len(parsed)
        for oid in chunk:
            if oid not in parsed:
                # Native show can omit path-empty commits even with --always.
                # Re-read them explicitly; only an actual empty patch is accepted.
                patch = _show(git, oid, path)
                stats['fallback_count'] += 1
                if patch != b'' or originals[oid] != b'':
                    raise ProbeError('omitted commit did not verify as an empty patch')
                parsed[oid] = patch
            result[oid] = parsed[oid]
        git.remaining()
    if list(result) != oids:
        raise ProbeError('completed batch does not preserve the complete ordered history')
    return result, stats


def run_probe(root, path, timeout_seconds=360, allow_object_directory=None):
    """Compare full serial/batch/serial history; return hashes, counts and times.

    completed proves only byte equivalence for this immutable input and Git/config
    identity. It does not validate P2 records, authorize a frozen implementation
    change, or establish production performance or sustained acceptance.
    """
    git = _Git(root, timeout_seconds, allow_object_directory)
    path = _path(path)
    git.check_repository()
    original_state = _state(git, path)
    oids = list(original_state[1])
    started = time.perf_counter()
    originals = {oid: _show(git, oid, path) for oid in oids}
    serial_before = time.perf_counter() - started
    if _state(git, path) != original_state:
        raise ProbeError('input changed after serial history read')
    started = time.perf_counter()
    batch, stats = read_batch(root, path, oids, originals, timeout_seconds=git.remaining(),
                             allow_object_directory=allow_object_directory)
    batch_seconds = time.perf_counter() - started
    if _state(git, path) != original_state:
        raise ProbeError('input changed after batch history read')
    if list(batch) != oids or any(batch[oid] != originals[oid] for oid in oids):
        raise ProbeError('batch history differs from original ordered patches')
    started = time.perf_counter()
    for oid in oids:
        if _show(git, oid, path) != originals[oid]:
            raise ProbeError('serial patch bytes changed after batch read')
    serial_after = time.perf_counter() - started
    if _state(git, path) != original_state:
        raise ProbeError('input changed after final serial history read')
    git.check_repository()
    manifest = [{'oid': oid, 'patch_bytes': len(originals[oid]),
                 'patch_sha256': hashlib.sha256(originals[oid]).hexdigest()} for oid in oids]
    metadata = {'schema_version': 1, 'head': original_state[0].decode().strip(), 'path': path,
                'git_version': git.run('--version').decode().strip(),
                'commit_count': len(oids), 'ordered_oids_sha256': _digest(oids),
                'patch_manifest_sha256': _digest(manifest),
                'effective_config_sha256': hashlib.sha256(original_state[2]).hexdigest(),
                'object_alternates_sha256': hashlib.sha256(original_state[4]).hexdigest()}
    git.remaining()
    return {**metadata, **stats, 'metadata_sha256': _digest(metadata),
            'status': 'completed', 'patch_bytes_equal': True, 'ordered_oids_equal': True,
            'verified_patch_count': len(oids), 'total_patch_bytes': sum(map(len, originals.values())),
            'serial_before_seconds': serial_before, 'serial_after_seconds': serial_after,
            'serial_seconds': min(serial_before, serial_after), 'batch_seconds': batch_seconds,
            'serial_show_count_per_trial': len(oids), 'empty_patch_count': sum(not p for p in originals.values())}

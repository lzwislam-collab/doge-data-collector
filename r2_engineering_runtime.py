"""Explicit R2 engineering transport; original model and qualification stay frozen.

Only synchronous command execution is supported. All scoped module bindings are
restored even on cancellation. Actual server run rows are never relabelled.
"""
from contextlib import contextmanager
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import Request, urlopen

import engineering_run_receipt as terminal
import phase2_forward_runtime_v2 as r
import phase2_forward_v2 as f
import r2_witness_presence as presence

ROOT = r.ROOT
VERSION = 'DOGE_R2_ENGINEERING_TRANSPORT_V1'
MANIFEST = 'config/r2_engineering_transport_v1.json'
ENGINEERING_AUDIT = '.github/workflows/r2_engineering_audit.yml'
ENGINEERING_LIVE = '.github/workflows/r2_engineering_live.yml'
ORIGINAL_REGISTRATION_SHA256 = 'd0aa618d6336dea8222f6187e4b14ca8f20a60dd98714f80c48b27009f3e6103'
TRANSPORT_FILES = frozenset({
    'r2_engineering_runtime.py', 'test_r2_engineering_runtime.py',
    'r2_witness_presence.py', 'test_r2_witness_presence.py',
    'r2_engineering_readiness.py', 'r2_engineering_dispatch.py',
    'test_r2_engineering_dispatch.py', 'forward_readiness.py',
    'engineering_run_receipt.py', 'learned_audit_handoff.py',
    'test_learned_audit_handoff.py', 'r2_engineering_routing.py',
    'test_r2_engineering_routing.py', 'runtime_forward_bridge.py', ENGINEERING_AUDIT, ENGINEERING_LIVE,
})
_ORIGINAL_WORKFLOW = f.WORKFLOW
_ORIGINAL_WITNESS = f.witness
_ORIGINAL_RUNS_SERVER = r.runs_server
_transport = None
IDENTITY_CACHE_MAX_BYTES = 64 * 1024 * 1024
IDENTITY_CACHE_MAX_ENTRIES = 256
_identity_cache = OrderedDict()
_identity_cache_bytes = 0
# Published PR117 transport. Witness compatibility only; local execution must
# still verify this checkout's complete current manifest and registered bytes.
PUBLISHED_PREDECESSOR_MANIFESTS = frozenset({
    'deecd431679db728b59790ad5043d5361620e49a4c4b5befe621e2138680262a',
})


def _lf(raw):
    return raw.replace(b'\r\n', b'\n')


def _sha(raw):
    return hashlib.sha256(_lf(raw)).hexdigest()


def _manifest(raw):
    value = json.loads(raw)
    fields = {'schema', 'engineering_version', 'audit_workflow', 'live_workflow',
              'original_registration_path', 'original_registration_sha256_lf',
              'engineering_files_sha256_lf', 'paper_only'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('exact engineering manifest contract required')
    if (value['schema'] != VERSION or value['engineering_version'] != VERSION or
        value['audit_workflow'] != ENGINEERING_AUDIT or value['live_workflow'] != ENGINEERING_LIVE or
        value['original_registration_path'] != f.REGISTRATION or
        value['original_registration_sha256_lf'] != ORIGINAL_REGISTRATION_SHA256 or
        value['paper_only'] is not True):
        raise ValueError('engineering compatibility identity changed')
    hashes = value['engineering_files_sha256_lf']
    if not isinstance(hashes, dict) or set(hashes) != TRANSPORT_FILES or any(
            not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in hashes.values()):
        raise ValueError('exact engineering transport file set required')
    return value


def _local_bytes(path):
    target = Path(ROOT) / path
    if target.is_symlink() or not target.is_file():
        raise ValueError('engineering identity source missing or unsafe')
    return target.read_bytes()


def _verify_files(manifest, read):
    raw = read(f.REGISTRATION)
    if _sha(raw) != ORIGINAL_REGISTRATION_SHA256:
        raise ValueError('original R2 registration bytes changed')
    registration = json.loads(raw)
    hashes = registration.get('sha256_lf')
    if not isinstance(hashes, dict) or set(hashes) != r.FIXED:
        raise ValueError('original R2 registered file set changed')
    for path, digest in {**hashes, **manifest['engineering_files_sha256_lf']}.items():
        if _sha(read(path)) != digest:
            raise ValueError('engineering compatibility bytes changed: ' + path)


def verify_transport():
    """Verify the new identity and all original registered bytes locally."""
    raw = _local_bytes(MANIFEST)
    manifest = _manifest(raw)
    _verify_files(manifest, _local_bytes)
    return manifest, _sha(raw)


def read_identity_at(head, path):
    """Read only allowlisted transport identity bytes at an exact immutable head."""
    if (not isinstance(head, str) or not re.fullmatch('[0-9a-f]{40}', head) or
        path not in TRANSPORT_FILES | r.FIXED | {f.REGISTRATION, MANIFEST}):
        raise ValueError('unsafe engineering identity read')
    try:
        return subprocess.check_output(
            ['git', '--no-replace-objects', '-c', 'safe.directory=*', 'show', head + ':' + path],
            cwd=ROOT, stderr=subprocess.DEVNULL, timeout=5)
    except subprocess.CalledProcessError:
        url = 'https://raw.githubusercontent.com/' + f.old.REPO + '/' + head + '/' + path
        request = Request(url, headers={'User-Agent': 'DOGE-R2-Engineering-Transport/1'})
        with urlopen(request, timeout=20) as response:
            if response.geturl() != url:
                raise ValueError('unexpected engineering identity redirect')
            raw = response.read(32000001)
        if len(raw) > 32000000:
            raise ValueError('oversize engineering identity file')
        return raw


def _cached_identity_at(head, path):
    """Cache only exact immutable raw bytes; never cache a verdict or failure."""
    global _identity_cache_bytes
    if (not isinstance(head, str) or not re.fullmatch('[0-9a-f]{40}', head) or
        path not in TRANSPORT_FILES | r.FIXED | {f.REGISTRATION, MANIFEST}):
        raise ValueError('unsafe engineering identity read')
    key = (head, path)
    if key in _identity_cache:
        _identity_cache.move_to_end(key)
        return _identity_cache[key]
    raw = read_identity_at(head, path)
    if not isinstance(raw, bytes):
        raise TypeError('engineering identity read did not return bytes')
    if len(raw) > 32000000:
        raise ValueError('oversize engineering identity file')
    if len(raw) <= IDENTITY_CACHE_MAX_BYTES and IDENTITY_CACHE_MAX_ENTRIES > 0:
        while _identity_cache and (
                _identity_cache_bytes + len(raw) > IDENTITY_CACHE_MAX_BYTES or
                len(_identity_cache) >= IDENTITY_CACHE_MAX_ENTRIES):
            unused, previous = _identity_cache.popitem(last=False)
            _identity_cache_bytes -= len(previous)
        _identity_cache[key] = raw
        _identity_cache_bytes += len(raw)
    return raw


def _candidate_transport(head, trusted):
    manifest, digest = trusted
    raw = _cached_identity_at(head, MANIFEST)
    actual_digest = _sha(raw)
    candidate = _manifest(raw)
    if actual_digest == digest:
        if candidate != manifest:
            raise ValueError('candidate current engineering manifest differs')
    elif actual_digest not in PUBLISHED_PREDECESSOR_MANIFESTS:
        raise ValueError('candidate is not current or a published engineering predecessor')
    _verify_files(candidate, lambda path: _cached_identity_at(head, path))
    return actual_digest


def _original_candidate(files, row, read_at, before, after, workflow):
    original = f.WORKFLOW
    f.WORKFLOW = workflow
    try:
        return _ORIGINAL_WITNESS(files, [row], read_at, before, after)
    finally:
        f.WORKFLOW = original


def witness(files, runs, read_at, before, after=None):
    """Use original predicates/bytes/windows with an explicit compatible identity."""
    for row in runs:
        path = row.get('path', '').split('@')[0]
        if path == _ORIGINAL_WORKFLOW:
            proof = _original_candidate(files, row, read_at, before, after, _ORIGINAL_WORKFLOW)
            if proof:
                return proof
        elif path == ENGINEERING_AUDIT:
            if (row.get('head_repository', {}).get('full_name') != f.old.REPO or
                type(row.get('run_attempt')) is not int or row['run_attempt'] <= 0):
                continue
            if not _original_candidate({}, row, read_at, before, after, ENGINEERING_AUDIT):
                continue
            try:
                digest = _candidate_transport(row['head_sha'], _transport or verify_transport())
            except ValueError:
                continue
            proof = _original_candidate(files, row, read_at, before, after, ENGINEERING_AUDIT)
            if proof:
                return {**proof, 'workflow_path': path, 'run_attempt': row['run_attempt'],
                        'engineering_version': VERSION, 'engineering_manifest_sha256': digest}
    return None


def _same_attempt(expected, actual):
    keys = ('id', 'run_attempt', 'head_sha', 'path', 'status', 'conclusion')
    if any(actual.get(key) != expected.get(key) for key in keys):
        raise ValueError('engineering audit run or attempt changed')


def runs_server(extra=()):
    """Preserve legacy candidates and add bounded, exact-attempt new candidates."""
    legacy = _ORIGINAL_RUNS_SERVER(())
    candidates = []
    prefetched = {}
    legacy_extra = []
    known = {row['id'] for row in legacy}
    for rid in sorted(set(extra) - known):
        if type(rid) is not int or rid <= 0:
            raise ValueError('invalid witness locator')
        row = r.frozen.api('/actions/runs/' + str(rid))
        if row.get('path', '').split('@')[0] == ENGINEERING_AUDIT:
            candidates.append(row)
            prefetched[(row.get('id'), row.get('run_attempt'))] = row
        else:
            legacy_extra.append(rid)
    if legacy_extra:
        legacy = _ORIGINAL_RUNS_SERVER(legacy_extra)
    found = r.frozen.api('/actions/workflows/r2_engineering_audit.yml/runs?per_page=30')['workflow_runs']
    candidates.extend([row for row in found if row.get('head_branch') == 'main' and
        row.get('status') == 'completed' and row.get('conclusion') == 'success'][:4])
    answer = [row for row in legacy if row.get('path', '').split('@')[0] != ENGINEERING_AUDIT]
    candidates.extend(row for row in legacy if row.get('path', '').split('@')[0] == ENGINEERING_AUDIT)
    seen = set()
    for row in candidates:
        rid, attempt = row.get('id'), row.get('run_attempt')
        if type(rid) is not int or rid <= 0 or type(attempt) is not int or attempt <= 0:
            raise ValueError('invalid engineering audit attempt identity')
        key = (rid, attempt)
        if key in seen:
            continue
        seen.add(key)
        if row.get('path', '').split('@')[0] != ENGINEERING_AUDIT:
            raise ValueError('unexpected engineering audit workflow')
        if row.get('status') != 'completed' or row.get('conclusion') != 'success':
            continue
        fresh = prefetched.get(key) or r.frozen.api('/actions/runs/' + str(rid))
        _same_attempt(row, fresh)
        jobs = terminal.pages(r.frozen.api, f'/actions/runs/{rid}/attempts/{attempt}/jobs', 'jobs')
        _same_attempt(fresh, r.frozen.api('/actions/runs/' + str(rid)))
        for job in jobs:
            if job.get('name') == 'audit':
                answer.append({**fresh, 'audit_job': job})
    return answer


@contextmanager
def context():
    """Verify transport, then adapt original synchronous runtime/readiness calls."""
    global _transport
    trusted = verify_transport()
    original = f.witness, r.runs_server, r.run, f.WORKFLOW, _transport
    def versioned_run(mode):
        return versioned_result(original[2](mode), trusted, mode=mode)
    f.witness, r.runs_server, _transport = witness, runs_server, trusted
    r.run = versioned_run
    try:
        with presence.witness_presence():
            yield
    finally:
        f.witness, r.runs_server, r.run, f.WORKFLOW, _transport = original


def pending_witness(result, events, scores, at, policy):
    """Operational requests only; original missing gaps and qualification remain."""
    events = {event['event_id']: event for event in events}
    scored = {score['event_id'] for score in scores}
    pending = []
    for key, reason in sorted(result.get('rejected', {}).items()):
        event = events.get(key)
        if event is None:
            continue
        target = f.utc(event['target_utc'])
        if (reason == 'MISSING_PREDICTION_PUBLIC_WITNESS' and
            f.utc(event['issued_at_utc']) <= at < target):
            pending.append({'event_id': key, 'kind': 'prediction'})
        elif (reason == 'MISSING_TARGET_PUBLIC_WITNESS' and key in scored and
              target <= at < f.deadline(event, policy)):
            pending.append({'event_id': key, 'kind': 'target'})
    return pending


def versioned_result(result, trusted=None, *, mode=None):
    """Add execution-version evidence without changing original journal rows."""
    trusted = trusted or _transport or verify_transport()
    out = {**result, 'engineering_version': VERSION,
           'engineering_manifest_sha256': trusted[1],
           'compatible_audit_workflow': ENGINEERING_AUDIT}
    if mode == 'publish':
        pending = pending_witness(result, r.records('events'), r.records('scores'),
                                  datetime.now(timezone.utc), f.policy())
        out['engineering_pending_witness'] = pending
        if pending:
            out['request_witness'] = True
    return out


def run(mode):
    with context():
        return r.run(mode)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'dispatch':
        raise ValueError('use r2_engineering_dispatch.py audit for a verified engineering dispatch')
    with context():
        return r.main()


if __name__ == '__main__':
    main()

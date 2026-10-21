"""Bounded positive engineering receipts; never an independent fresh audit."""
import copy
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone


WORKFLOW = '.github/workflows/e1_observer.yml'
SCHEMA = 'DOGE_E1_OBSERVER_POSITIVE_CACHE_V1'
CODE = ('e1_observer.py', 'e1_observer_cache.py', WORKFLOW,
        '.github/scripts/e1_observer_notify.cjs')
MAX_ZIP = 2_000_000
MAX_PAYLOAD = 4_000_000
MAX_ENTRIES = 10000


class CacheError(RuntimeError):
    pass


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def _utc(value):
    if not isinstance(value, str):
        raise CacheError('invalid receipt clock')
    try:
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise CacheError('invalid receipt clock') from exc
    if at.tzinfo is None or at.utcoffset() != timezone.utc.utcoffset(at):
        raise CacheError('explicit UTC receipt clock required')
    return at


def fingerprint(root, registration_identity):
    hashes = {}
    for name in CODE:
        path = root / name
        if path.is_symlink():
            raise CacheError('unsafe observer fingerprint path')
        hashes[name] = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    return {'registration_identity': registration_identity, 'sha256_lf': hashes}


def validate_proof(proof, now):
    fields = {'run_id', 'head_sha', 'job_started_at_utc', 'job_completed_at_utc'}
    if (not isinstance(proof, dict) or set(proof) != fields or
            type(proof['run_id']) is not int or proof['run_id'] <= 0 or
            not isinstance(proof['head_sha'], str) or not re.fullmatch('[a-f0-9]{40}', proof['head_sha'])):
        raise CacheError('malformed positive receipt')
    start, finish = _utc(proof['job_started_at_utc']), _utc(proof['job_completed_at_utc'])
    if not start <= finish <= now:
        raise CacheError('future or reversed positive receipt')
    return proof


class PositiveCache:
    def __init__(self, expected_fingerprint, proofs=None):
        self.fingerprint = copy.deepcopy(expected_fingerprint)
        self.proofs = copy.deepcopy(proofs or {})
        self.hits = 0
        self.loaded_from_run_id = None

    def _key(self, required, files, before, after):
        return _digest({'identity': self.fingerprint['registration_identity'],
                        'required': {k: hashlib.sha256(v).hexdigest() for k, v in sorted(required.items())},
                        'registered': {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())},
                        'before': before.isoformat(), 'after': after.isoformat() if after else None})

    def get(self, required, files, before, after, now, pinned=(), discover=True):
        if now < before:
            return None
        proof = self.proofs.get(self._key(required, files, before, after))
        if proof is None:
            return None
        validate_proof(proof, now)
        if (_utc(proof['job_completed_at_utc']) >= before or
                after is not None and _utc(proof['job_started_at_utc']) < after):
            raise CacheError('receipt does not satisfy its exact window')
        if not discover and proof['run_id'] not in pinned:
            return None
        self.hits += 1
        return copy.deepcopy(proof)

    def remember(self, required, files, before, after, proof, now):
        if proof is None or now < before:
            return
        validate_proof(proof, now)
        if (_utc(proof['job_completed_at_utc']) >= before or
                after is not None and _utc(proof['job_started_at_utc']) < after):
            raise CacheError('non-qualifying receipt cannot be persisted')
        self.proofs[self._key(required, files, before, after)] = copy.deepcopy(proof)
        if len(self.proofs) > MAX_ENTRIES:
            raise CacheError('positive receipt count exceeds bounded capacity')

    def payload(self):
        value = {'schema': SCHEMA, 'fingerprint': self.fingerprint, 'proofs': self.proofs}
        if len(_encode(value)) > MAX_PAYLOAD:
            raise CacheError('positive receipts exceed bounded payload size')
        return copy.deepcopy(value)

    @classmethod
    def from_payload(cls, payload, expected_fingerprint, now):
        if (not isinstance(payload, dict) or set(payload) != {'schema', 'fingerprint', 'proofs'} or
                payload['schema'] != SCHEMA or not isinstance(payload['fingerprint'], dict) or
                not isinstance(payload['proofs'], dict) or len(payload['proofs']) > MAX_ENTRIES):
            raise CacheError('malformed positive cache payload')
        if payload['fingerprint'] != expected_fingerprint:
            return None
        for key, proof in payload['proofs'].items():
            if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key):
                raise CacheError('malformed receipt key')
            validate_proof(proof, now)
        return cls(expected_fingerprint, payload['proofs'])


def unpack(raw, expected_fingerprint, now):
    if not isinstance(raw, bytes) or len(raw) > MAX_ZIP:
        raise CacheError('oversize positive cache archive')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as stream:
            names = [info for info in stream.infolist()
                     if info.filename == 'witness-cache.json' or info.filename.endswith('/witness-cache.json')]
            if not names:
                return None  # Probe/older inspector artifacts have no receipt map.
            if len(names) != 1 or names[0].file_size > MAX_PAYLOAD or names[0].flag_bits & 1:
                raise CacheError('ambiguous or oversize positive cache member')
            body = stream.read(names[0])
            if len(body) > MAX_PAYLOAD:
                raise CacheError('oversize decompressed positive cache')
            payload = json.loads(body)
        return PositiveCache.from_payload(payload, expected_fingerprint, now)
    except CacheError:
        raise
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise CacheError('unreadable positive cache archive') from exc


def load_previous(engine, expected_fingerprint, now, api=None, download=None):
    """At most ten recent successful main inspections; no PR/probe trust."""
    api = api or engine.api
    download = download or getattr(engine, '_download', None)
    cold = PositiveCache(expected_fingerprint)
    try:
        payload = api('/actions/workflows/e1_observer.yml/runs?per_page=10&branch=main&status=success')
        rows = payload['workflow_runs']
        if not isinstance(rows, list):
            raise CacheError('malformed observer run listing')
        for run in rows[:10]:
            if (run['repository']['full_name'] != engine.parent.old.REPO or
                    run['path'].split('@')[0] != WORKFLOW or run['head_branch'] != 'main' or
                    run['event'] not in ('workflow_run', 'workflow_dispatch') or
                    run['status'] != 'completed' or run['conclusion'] != 'success' or run.get('pull_requests')):
                continue
            rid, attempt = run['id'], run.get('run_attempt', 1)
            if type(rid) is not int or rid <= 0 or type(attempt) is not int or attempt <= 0:
                raise CacheError('invalid observer run identity')
            jobs_payload = api('/actions/runs/' + str(rid) + '/attempts/' + str(attempt) + '/jobs?per_page=100')
            jobs = jobs_payload['jobs']
            if (not isinstance(jobs, list) or type(jobs_payload.get('total_count')) is not int or
                    len(jobs) != jobs_payload['total_count']):
                raise CacheError('incomplete observer job list')
            observe = [j for j in jobs if j['name'] == 'observe']
            if len(observe) != 1 or observe[0]['status'] != 'completed' or observe[0]['conclusion'] != 'success':
                continue
            if observe[0].get('head_sha', run['head_sha']) != run['head_sha']:
                raise CacheError('observer job source identity changed')
            artifacts = api('/actions/runs/' + str(rid) + '/artifacts?per_page=100')['artifacts']
            expected_name = 'e1-observer-' + str(rid) + '-' + str(attempt)
            matches = [a for a in artifacts if a['name'] == expected_name and not a['expired']]
            if not matches:
                continue
            if len(matches) != 1 or download is None:
                raise CacheError('ambiguous observer receipt artifact')
            art = matches[0]
            if type(art['id']) is not int or art['id'] <= 0:
                raise CacheError('invalid observer artifact identity')
            raw = download('https://api.github.com/repos/' + engine.parent.old.REPO +
                           '/actions/artifacts/' + str(art['id']) + '/zip')
            if not isinstance(raw, bytes) or len(raw) > MAX_ZIP or art['digest'] != 'sha256:' + hashlib.sha256(raw).hexdigest():
                raise CacheError('observer receipt artifact digest mismatch')
            found = unpack(raw, expected_fingerprint, now)
            if found is not None:
                found.loaded_from_run_id = rid
                return found
        return cold
    except CacheError:
        raise
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise CacheError('positive cache read failed') from exc

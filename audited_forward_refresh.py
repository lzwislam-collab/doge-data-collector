"""Bounded daily refresh of frozen audits; no forecast generation or score promotion."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

REG = 'data/direction_v42/forward_registry.csv'
SCORES = 'data/direction_v42/forward_scores.csv'
V1 = 'data/stage5/forward_validation/daily_latest.json'
V2 = 'data/stage5/forward_validation_v2/daily_latest.json'
ARCHIVE = 'data/plus_evidence_v1_0/forward_refresh'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transient(error):
    # Whitelist transport failures only; integrity errors can contain an HTTP string.
    text = str(error)
    if re.search(r'integrity|modified|mismatch|ValueError|invalid|mutation|hash', text, re.I):
        return False
    return bool(re.search(r'HTTP(?: Error| status)?\s*[:=]?\s*(?:429|5\d\d)\b|timed out|TimeoutError|connection (?:reset|refused|aborted)|temporary failure|URLError|network unreachable|remote end closed', text, re.I))


def run_frozen(command, *, cwd, timeout):
    # Spool output rather than retaining arbitrarily large or secret-bearing logs.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        done = subprocess.run(command, cwd=cwd, stdout=stdout, stderr=stderr, timeout=timeout)
        stdout.seek(0); stderr.seek(0)
        return subprocess.CompletedProcess(command, done.returncode,
            stdout.read(16384).decode('utf-8', 'replace'), stderr.read(16384).decode('utf-8', 'replace'))


def read_output(path):
    raw = path.read_text(encoding='utf-8') if path.exists() else None
    try:
        value = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        value = None
    return raw, value


def validate(v1, v2, before, after, started, finished):
    if before != after:
        raise ValueError('raw registry/scores changed during read-only daily audit')
    transport_errors = []
    for version, value in ((1, v1), (2, v2)):
        if not isinstance(value, dict):
            raise ValueError('missing or invalid audit JSON')
        stamp = datetime.fromisoformat(value['audited_at_utc'].replace('Z', '+00:00'))
        if stamp.tzinfo is None or not started <= stamp <= finished or finished - stamp > timedelta(minutes=20):
            raise ValueError('stale or future audit timestamp')
        if value.get('status') != 'ok':
            error = value.get('error', 'audit blocked without transport error')
            if transient(error):
                transport_errors.append(error)
                continue
            raise ValueError(error)
        if value.get('schema_version') != version or not value.get('protocol_id'):
            raise ValueError('invalid audit schema')
    if transport_errors:
        raise ConnectionError('; '.join(transport_errors))
    if v1.get('full_reachable_history_checked') is not True:
        raise ValueError('V1 history integrity receipt missing')
    for key in ('input_commit_sha', 'protocol_sha256'):
        size = 40 if key == 'input_commit_sha' else 64
        if not re.fullmatch('[0-9a-f]{%d}' % size, str(v1.get(key, ''))):
            raise ValueError('invalid V1 receipt ' + key)
    for key in ('registry_sha256', 'scores_sha256'):
        if v1.get(key) != after[key]:
            raise ValueError('V1 receipt hash mismatch: ' + key)
    if v2.get('locked_implementation_checked') is not True or not str(v2.get('official_score_source', '')).startswith('V2 only;'):
        raise ValueError('V2 official audit evidence missing')


def refresh(root=None, *, runner=run_frozen, sleeper=time.sleep):
    root = Path(root or Path(__file__).resolve().parent)
    for attempt in range(1, 4):
        started = datetime.now(timezone.utc)
        before = {'registry_sha256': digest(root / REG), 'scores_sha256': digest(root / SCORES)}
        failure = None
        try:
            done = runner([sys.executable, '-X', 'utf8', 'forward_validation_v2.py', '--daily'], cwd=root, timeout=60)
            returncode = done.returncode
        except (subprocess.TimeoutExpired, ConnectionError) as exc:
            failure = ConnectionError('TimeoutError' if isinstance(exc, subprocess.TimeoutExpired) else str(exc))
            returncode = None
        finished = datetime.now(timezone.utc)
        raw1, v1 = read_output(root / V1)
        raw2, v2 = read_output(root / V2)
        after = {'registry_sha256': digest(root / REG), 'scores_sha256': digest(root / SCORES)}
        try:
            if before != after: raise ValueError('raw input mutation during audit')
            if failure: raise failure
            validate(v1, v2, before, after, started, finished)
            if returncode != 0: raise ValueError('frozen V2 process failed despite successful receipts')
        except (ValueError, KeyError, TypeError, ConnectionError) as exc:
            failure = exc
        archive = root / ARCHIVE
        archive.mkdir(parents=True, exist_ok=True)
        path = archive / (started.strftime('%Y%m%dT%H%M%S%fZ') + '_' + uuid.uuid4().hex + '.json')
        receipt = dict(schema_version=1, attempt=attempt, started_at_utc=started.isoformat(),
                       finished_at_utc=finished.isoformat(), status='blocked' if failure else 'ok',
                       returncode=returncode, v1=v1, v2=v2, original_v1_json=raw1,
                       original_v2_json=raw2, before=before, after=after,
                       original_reports={name: (root / 'reports' / name).read_text(encoding='utf-8')
                           if (root / 'reports' / name).exists() else None
                           for name in ('DOGE_FORWARD_VALIDATION_DAILY.md', 'DOGE_FORWARD_VALIDATION_V2_DAILY.md')},
                       source_integrity_only=True, official_score_source='V2 only',
                       external_immutable_storage_configured=False,
                       error_type=type(failure).__name__ if failure else None)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        if failure:
            if isinstance(failure, ConnectionError) and transient(failure) and attempt < 3:
                sleeper(attempt * 2)
                continue
            raise ValueError('daily source integrity refresh blocked; inspect preserved attempt evidence') from failure
        return {'status': 'ok', 'attempts': attempt, 'receipt': str(path.relative_to(root)),
                'source_integrity_only': True, 'official_score_source': 'V2 only',
                'external_immutable_storage_configured': False}


def main():
    try:
        print(json.dumps(refresh()))
        return 0
    except Exception:
        # Do not expose subprocess logs, credentials, or API error URLs.
        print(json.dumps({'status': 'blocked', 'message': 'Daily integrity refresh failed; preserved evidence must be reviewed.'}))
        return 1


if __name__ == '__main__': raise SystemExit(main())

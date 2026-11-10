"""One bounded market-only recovery from the existing trusted hourly callback."""
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import feed_freshness_audit as freshness
import forward_readiness as readiness
import runtime_custody_bridge as transport
import runtime_r2_bridge as queue

WORKFLOW = 'collect.yml'
COLLECTORS = (WORKFLOW, 'okx_hourly_extreme_history.yml', 'doge_history.yml')
REQUIRED_STEPS = ('Fail closed before writes on full-history or frozen protocol mutation',
                  'Persist data and diagnostics')


def _utc(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', value):
        raise ValueError('explicit UTC timestamp required')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _now(clock):
    at = clock()
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise ValueError('actual UTC clock required')
    return at


def _budget(client):
    spent = client.spent
    if type(spent) not in (int, float) or not math.isfinite(spent) or spent < 0:
        raise ValueError('invalid shared transport budget')
    if spent >= 40:
        raise TimeoutError('market recovery shared transport budget exhausted')


def _run_identity(row, workflow):
    if (not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0
            or type(row.get('run_attempt')) is not int or row['run_attempt'] <= 0
            or not isinstance(row.get('head_sha'), str) or not transport.HEX.fullmatch(row['head_sha'])
            or row.get('repository', {}).get('full_name') != transport.REPO
            or row.get('head_repository', {}).get('full_name') != transport.REPO
            or row.get('head_branch') != 'main' or not isinstance(row.get('path'), str)
            or row['path'].split('@')[0] != '.github/workflows/'+workflow
            or row.get('status') not in queue.STATES+('completed',)):
        raise ValueError('invalid workflow run identity')


def _jobs(api, parent):
    rows, total, ids = [], None, set()
    for page in range(1, 101):
        value = api('/actions/runs/'+str(parent['id'])+'/attempts/'+str(parent['run_attempt'])
                    +'/jobs?per_page=100&page='+str(page))
        batch, count = value.get('jobs'), value.get('total_count')
        if (not isinstance(batch, list) or len(batch) > 100 or type(count) is not int
                or count <= 0 or total is not None and count != total):
            raise ValueError('incomplete parent job listing')
        total = count
        for job in batch:
            if (not isinstance(job, dict) or type(job.get('id')) is not int or job['id'] <= 0
                    or job['id'] in ids or job.get('run_id') != parent['id']
                    or job.get('status') != 'completed' or not isinstance(job.get('steps'), list)
                    or any(not isinstance(step, dict) for step in job['steps'])):
                raise ValueError('invalid completed parent job')
            ids.add(job['id'])
            rows.append(job)
        if len(rows) == total:
            return rows
        if len(batch) < 100 or len(rows) > total:
            raise ValueError('incomplete parent job listing')
    raise ValueError('parent job pagination incomplete')


def _snapshot(api, clock):
    path, field, limit, required, allowed = freshness.FEEDS['market']
    if (path, field, limit, required, allowed) != (
            'data/latest.json', 'collected_at_utc', 3, True, ('ok',)):
        raise ValueError('original market freshness contract changed')
    head = transport.current_main_sha(api)
    raw = transport.read_pinned_file(api, path, head)
    value = transport.parse(raw)
    json.dumps(value, allow_nan=False)
    if not isinstance(value, dict) or value.get('status') not in allowed:
        raise ValueError('market snapshot source status invalid')
    at, stamp = _now(clock), _utc(value.get(field))
    if stamp > at:
        raise ValueError('future market snapshot timestamp')
    return {'main_sha': head, 'snapshot_blob': transport.git_blob(raw),
            'snapshot_utc': stamp.isoformat(), 'checked_at_utc': at.isoformat(),
            'age_limit_hours': limit, 'stale': at-stamp > timedelta(hours=limit)}


def recover(event, *, client=None, clock=None, environ=None):
    """No producer calls, retries of a POST, credentials or independent clock identity."""
    environ = os.environ if environ is None else environ
    if (environ.get('GITHUB_REPOSITORY') != transport.REPO
            or environ.get('GITHUB_REF') != 'refs/heads/main'
            or environ.get('GITHUB_EVENT_NAME') != 'workflow_run'
            or not isinstance(event, dict) or event.get('action') != 'completed'
            or event.get('repository', {}).get('full_name') != transport.REPO):
        raise ValueError('existing main workflow_run completion required')
    source = event.get('workflow_run')
    _run_identity(source, 'okx_hourly_extreme_history.yml')
    if source.get('status') != 'completed':
        raise ValueError('source completion required')
    client = transport.GitHubClient() if client is None else client
    clock = clock or (lambda: datetime.now(timezone.utc))

    def api(path):
        _budget(client)
        value = client.api(path)
        _budget(client)
        return value

    parent = api('/actions/runs/'+str(source['id']))
    _run_identity(parent, 'okx_hourly_extreme_history.yml')
    readiness.match_event(source, parent)
    jobs = _jobs(api, parent)
    if not readiness.trusted_completion(parent, jobs) or not readiness.trusted_completion(source, jobs):
        raise ValueError('original trusted completion guard rejected parent')
    for name in REQUIRED_STEPS:
        matching = [step for job in jobs for step in job['steps'] if step.get('name') == name]
        if len(matching) != 1 or matching[0].get('status') != 'completed' or matching[0].get('conclusion') != 'success':
            raise ValueError('parent guard/persistence step not uniquely successful')
    began = _now(clock)
    hour = began.replace(minute=0, second=0, microsecond=0)
    receipt = {'workflow': WORKFLOW, 'parent_run_id': parent['id'],
               'parent_run_attempt': parent['run_attempt'], 'attempt_hour_utc': hour.isoformat(),
               'snapshot_freshness_verified': False}

    def same_hour():
        at = _now(clock)
        if at < began:
            raise ValueError('UTC clock moved backwards during recovery')
        return at.replace(minute=0, second=0, microsecond=0) == hour

    def active():
        for workflow in COLLECTORS:
            def checked(path):
                value = api(path)
                if not isinstance(value, dict) or not isinstance(value.get('workflow_runs'), list):
                    raise ValueError('invalid collector queue response')
                for row in value['workflow_runs']:
                    _run_identity(row, workflow)
                return value
            row = queue._active(checked, workflow)
            if row is not None:
                return {'status': 'DEFERRED_ACTIVE_COLLECT', 'active_workflow': workflow,
                        'active_run_id': row['id']}
        return None

    def attempted():
        value = api('/actions/workflows/'+WORKFLOW+'/runs?'+urlencode({
            'branch': 'main', 'event': 'workflow_dispatch',
            'created': '>='+hour.isoformat().replace('+00:00', 'Z'), 'per_page': 1}))
        rows, count = value.get('workflow_runs'), value.get('total_count')
        if (not isinstance(rows, list) or type(count) is not int or count < 0
                or len(rows) > 1 or bool(rows) != bool(count)):
            raise ValueError('incomplete current-hour dispatch listing')
        if rows:
            row = rows[0]
            _run_identity(row, WORKFLOW)
            if row.get('event') != 'workflow_dispatch' or not hour <= _utc(row.get('created_at')) <= _now(clock):
                raise ValueError('current-hour dispatch filter mismatch')
            return {'status': 'ALREADY_ATTEMPTED_THIS_UTC_HOUR', 'previous_run_id': row['id']}
        return None

    first = _snapshot(api, clock)
    receipt.update(first)
    if not first['stale']:
        return {**receipt, 'status': 'SKIP_MARKET_FRESH'}
    for view in range(2):
        if view:
            current = _snapshot(api, clock)
            receipt.update(current)
            if not current['stale']:
                return {**receipt, 'status': 'SKIP_MARKET_FRESH'}
            if (current['main_sha'], current['snapshot_blob']) != (first['main_sha'], first['snapshot_blob']):
                return {**receipt, 'status': 'DEFERRED_MAIN_CHANGED'}
        result = active()
        if result:
            return {**receipt, **result}
        if not same_hour():
            return {**receipt, 'status': 'DEFERRED_UTC_HOUR_CHANGED'}
        result = attempted()
        if result:
            return {**receipt, **result}
    # Final main read detects a publication during the final queue/attempt reads.
    if transport.current_main_sha(api) != first['main_sha']:
        return {**receipt, 'status': 'DEFERRED_MAIN_CHANGED'}
    if not same_hour():
        return {**receipt, 'status': 'DEFERRED_UTC_HOUR_CHANGED'}
    _budget(client)
    if client.request('/actions/workflows/'+WORKFLOW+'/dispatches', {'ref': 'main'}) != 204:
        raise ValueError('existing market collection dispatch not accepted')
    return {**receipt, 'status': 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED'}


def main():
    out = Path('forward-readiness-output/market-snapshot-recovery.json')
    out.parent.mkdir(exist_ok=True)
    try:
        event = transport.parse(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
        result = recover(event)
    except Exception as exc:
        out.write_bytes(transport.encode({'status': 'MARKET_RECOVERY_BLOCKED',
            'error': type(exc).__name__+': '+str(exc), 'snapshot_freshness_verified': False}))
        raise
    out.write_bytes(transport.encode(result))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

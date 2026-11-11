"""Explicit existing-audit to existing-readiness handoff; never a journal writer.

The requesting audit does not wait for itself. The separately dispatched terminal
checks its actual completion, source identity and current frozen obligations.
No forecast, quote, score, policy, registered source or witness predicate changes.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time

ROOT = Path(__file__).resolve().parent
REPO = 'lzwislam-collab/doge-data-collector'
AUDITS = {'legacy': '.github/workflows/learned_shadow_v1.yml',
          'r2': '.github/workflows/r2_engineering_audit.yml'}
AUDIT_STEPS = {
    'legacy': ('Full regression and frozen production history',
               'Audit exact future bundle and probe raw public quote without predictions',
               'Preserve full audit and failures'),
    'r2': ('All tests and original frozen history',
           'Original frozen registration and real publication replay through engineering v1',
           'Preserve full engineering audit and failures')}
TERMINAL = 'doge_engineering_completion.yml'
SOURCE = 'data/hourly_research/ohlc.csv'
HEX = re.compile(r'[a-f0-9]{40}\Z')


def _utc(at):
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise ValueError('actual UTC clock required')
    return at


def _identity(run_id, attempt, head, blob):
    if (type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0
            or not isinstance(head, str) or not HEX.fullmatch(head)
            or not isinstance(blob, str) or not HEX.fullmatch(blob)):
        raise ValueError('invalid source audit locator')


def _parent_kind(row):
    path = row.get('path', '').split('@')[0] if isinstance(row, dict) else ''
    matches = [kind for kind, allowed in AUDITS.items() if path == allowed]
    if len(matches) != 1:
        raise ValueError('unregistered source audit workflow')
    return matches[0]


def _parent(row, run_id, attempt, head, at, kind):
    if kind not in AUDITS or _parent_kind(row) != kind:
        raise ValueError('claimed source audit kind does not match actual workflow')
    if (not isinstance(row, dict) or row.get('id') != run_id or row.get('run_attempt') != attempt
            or row.get('head_sha') != head or row.get('head_branch') != 'main'
            or row.get('repository', {}).get('full_name') != REPO
            or row.get('head_repository', {}).get('full_name') != REPO
            or row.get('event') not in (('push', 'workflow_dispatch', 'workflow_run')
                                      if kind == 'r2' else ('push', 'workflow_dispatch'))
            or row.get('pull_requests')):
        raise ValueError('unexpected source audit identity')
    created = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
    updated = datetime.fromisoformat(row['updated_at'].replace('Z', '+00:00'))
    if not _utc(created) <= _utc(updated) <= at:
        raise ValueError('invalid source audit server clocks')
    if row.get('status') not in ('queued', 'in_progress', 'completed', 'waiting', 'requested', 'pending'):
        raise ValueError('invalid source audit status')
    if row['status'] == 'completed' and row.get('conclusion') != 'success':
        raise ValueError('parent audit did not succeed')
    return row['status'] == 'completed'


def _source(api, head):
    rows = api('/contents/data/hourly_research?ref=' + head)
    if not isinstance(rows, list):
        raise ValueError('invalid public source directory')
    matches = [r for r in rows if isinstance(r, dict) and r.get('name') == 'ohlc.csv']
    if len(matches) != 1:
        raise ValueError('source metadata missing or ambiguous')
    row = matches[0]
    if (row.get('path') != SOURCE or row.get('type') != 'file'
            or not isinstance(row.get('sha'), str) or not HEX.fullmatch(row['sha'])
            or type(row.get('size')) is not int or not 0 < row['size'] <= 32000000):
        raise ValueError('invalid public source metadata')
    return row['sha']


def _current_source(api):
    obj = api('/git/ref/heads/main')['object']
    if obj.get('type') != 'commit' or not isinstance(obj.get('sha'), str) or not HEX.fullmatch(obj['sha']):
        raise ValueError('invalid current main identity')
    return obj['sha'], _source(api, obj['sha'])


def _active_terminal(api):
    states = ('pending', 'queued', 'requested', 'waiting', 'in_progress')
    for state in states:
        value = api('/actions/workflows/'+TERMINAL+'/runs?branch=main&status='+state+'&per_page=1')
        rows, count = value.get('workflow_runs'), value.get('total_count')
        if (not isinstance(rows, list) or type(count) is not int or count < 0 or len(rows) > 1
                or bool(rows) != bool(count)):
            raise ValueError('terminal queue response incomplete')
        if rows:
            row = rows[0]
            if (not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0
                    or row.get('head_branch') != 'main'
                    or row.get('path', '').split('@')[0] != '.github/workflows/'+TERMINAL
                    or row.get('status') not in states+('completed',)):
                raise ValueError('terminal queue identity changed')
            return row['id']
    return None


def _audit_steps(api, run_id, attempt, kind):
    value = api('/actions/runs/'+str(run_id)+'/attempts/'+str(attempt)+'/jobs?per_page=100')
    rows, count = value.get('jobs'), value.get('total_count')
    if not isinstance(rows, list) or type(count) is not int or count != len(rows) or count > 100:
        raise ValueError('source audit job history incomplete')
    audits = [row for row in rows if row.get('name') == 'audit']
    if (len(audits) != 1 or audits[0].get('run_id') != run_id
            or audits[0].get('status') != 'completed' or audits[0].get('conclusion') != 'success'):
        raise ValueError('original audit job did not succeed')
    steps = audits[0].get('steps', [])
    for name in AUDIT_STEPS[kind]:
        if not any(s.get('name') == name and s.get('conclusion') == 'success' for s in steps):
            raise ValueError('original source audit step did not succeed: '+name)


def obligation_keys(policy, events, scores, at):
    """Use the unchanged frozen planner, including its original strict windows."""
    import learned_shadow_runtime as original
    original.shadow.check_policy(policy)
    at = _utc(at)
    planned = original.plan(policy, events, scores, at)
    start = original.shadow.utc(policy['effective_from_utc'])
    keys = []
    for horizon in planned['issue']:
        step = timedelta(hours=policy['slot_steps_hours'][str(horizon)])
        slot = start + ((at-start)//step)*step
        keys.append('issue:' + str(int(slot.timestamp())) + '_' + str(horizon))
    return sorted(keys + ['target:' + key for key in planned['score']])


def r2_obligation_keys(policy, events, scores, at):
    """The original formal slot, issue window and target deadline stay exact."""
    import phase2_forward_runtime_v2 as original
    original.f.check_policy(policy)
    at = _utc(at)
    start = original.f.utc(policy['effective_from_utc'])
    end = original.f.utc(policy['observation_end_utc'])
    keys = []
    if start <= at < end:
        step = timedelta(seconds=policy['slot_seconds'])
        slot = start + ((at-start)//step)*step
        key = str(int(slot.timestamp()))+'_formal'
        if at < slot+timedelta(seconds=policy['issue_window_seconds']) and key not in {e['event_id'] for e in events}:
            keys.append('issue:'+key)
    for event in events:
        if (event['cohort'] == 'formal' and event['event_id'] not in scores
                and original.f.utc(event['target_utc']) <= at < original.f.deadline(event, policy)):
            keys.append('target:'+event['event_id'])
    return sorted(keys)


def current_obligation(at, kind='legacy'):
    if kind == 'r2':
        import phase2_forward_runtime_v2 as original
        events = original.records('events')
        scores = {row['event_id']: row for row in original.records('scores')}
        return r2_obligation_keys(original.f.policy(), events, scores, at)
    if kind != 'legacy':
        raise ValueError('unregistered source audit kind')
    import learned_shadow_runtime as original
    policy = json.loads((ROOT/original.shadow.POLICY).read_bytes())
    events = original.read_records(ROOT, 'events')
    scores = {r['event_id']: r for r in original.read_records(ROOT, 'scores')}
    return obligation_keys(policy, events, scores, at)


def prepare_handoff(run_id, attempt, head, blob, *, client, clock=None,
                    obligation=None, receipt=None, kind='legacy'):
    _identity(run_id, attempt, head, blob)
    clock = clock or (lambda: datetime.now(timezone.utc))
    obligation = obligation or (lambda at: current_obligation(at, kind))
    out = receipt if receipt is not None else {}
    out.update(parent_run_id=run_id, parent_attempt=attempt, source_head=head, source_blob=blob,
               source_audit_kind=kind, admitted=False, prepared=False,
               request_attempted=False, witness_or_forecast_verified=False)
    _parent(client.api('/actions/runs/'+str(run_id)), run_id, attempt, head, _utc(clock()), kind)
    _audit_steps(client.api, run_id, attempt, kind)
    artifacts = client.api('/actions/runs/'+str(run_id)+'/artifacts?per_page=100')
    rows = artifacts.get('artifacts')
    count = artifacts.get('total_count')
    if not isinstance(rows, list) or type(count) is not int or count != len(rows) or count > 100:
        raise ValueError('handoff attempt history incomplete')
    # One receipt per source audit, including reruns and unknown POST outcomes.
    if any(a.get('name') == 'learned-audit-handoff-request-'+str(run_id) for a in rows):
        return dict(out, status='SKIP_PREVIOUS_HANDOFF_ATTEMPT')
    if _source(client.api, head) != blob:
        raise ValueError('claimed source audit blob does not match parent')
    current, actual = _current_source(client.api)
    if actual != blob:
        return dict(out, status='SKIP_SOURCE_CHANGED', current_main=current)
    keys = obligation(_utc(clock()))
    if not keys:
        return dict(out, status='SKIP_NO_CURRENT_OBLIGATION')
    return dict(out, status='HANDOFF_PREPARED_NO_POST', prepared=True,
                obligation_keys=keys, intent_nonce=secrets.token_hex(16))


def request_terminal(run_id, attempt, head, blob, *, client, intent_artifact_id=None,
                     expected_keys=None, clock=None, obligation=None, receipt=None,
                     persist_intent=None, kind='legacy'):
    _identity(run_id, attempt, head, blob)
    clock = clock or (lambda: datetime.now(timezone.utc))
    obligation = obligation or (lambda at: current_obligation(at, kind))
    out = receipt if receipt is not None else {}
    if out.get('request_attempted') is True:
        return dict(out, status='SKIP_PREVIOUS_LOCAL_HANDOFF_ATTEMPT', admitted=False)
    out.update(parent_run_id=run_id, parent_attempt=attempt, source_head=head, source_blob=blob,
               source_audit_kind=kind, admitted=False, request_attempted=False,
               witness_or_forecast_verified=False)
    if (type(intent_artifact_id) is not int or intent_artifact_id <= 0
            or not isinstance(expected_keys, list) or not expected_keys
            or any(not isinstance(key, str) for key in expected_keys)):
        raise ValueError('uploaded handoff intent and planned context required')
    marker = client.api('/actions/artifacts/'+str(intent_artifact_id))
    if (not isinstance(marker, dict) or marker.get('id') != intent_artifact_id
            or marker.get('name') != 'learned-audit-handoff-request-'+str(run_id)
            or marker.get('expired') is not False
            or marker.get('workflow_run', {}).get('id') != run_id
            or marker.get('workflow_run', {}).get('head_sha') != head
            or marker.get('workflow_run', {}).get('head_branch') != 'main'):
        raise ValueError('uploaded handoff intent does not match source audit')
    _parent(client.api('/actions/runs/'+str(run_id)), run_id, attempt, head, _utc(clock()), kind)
    _audit_steps(client.api, run_id, attempt, kind)
    if _source(client.api, head) != blob:
        raise ValueError('claimed source audit blob does not match parent')
    current, actual = _current_source(client.api)
    if actual != blob:
        return dict(out, status='SKIP_SOURCE_CHANGED', current_main=current)
    keys = obligation(_utc(clock()))
    if not keys:
        return dict(out, status='SKIP_NO_CURRENT_OBLIGATION')
    if keys != expected_keys:
        return dict(out, status='SKIP_OBLIGATION_CHANGED')
    active = _active_terminal(client.api)
    if active is not None:
        return dict(out, status='SKIP_EXISTING_TERMINAL_ACTIVE', active_run_id=active)
    payload = {'ref': 'main', 'inputs': {'mode': 'bridge', 'source_audit_run_id': str(run_id),
               'source_audit_attempt': str(attempt), 'source_audit_head': head,
               'source_audit_blob': blob, 'source_audit_kind': kind}}
    if not callable(persist_intent):
        raise ValueError('durable handoff intent writer required')
    out.update(request_attempted=True, status='HANDOFF_REQUEST_ATTEMPTED')
    out['intent_artifact_id'] = intent_artifact_id
    persist_intent(dict(out))
    # The remote marker is already immutable and the local attempt is durable.
    # Neither provider I/O nor a retry occurs after this actual-clock check.
    if obligation(_utc(clock())) != keys:
        return dict(out, status='SKIP_OBLIGATION_CHANGED')
    if client.request('/actions/workflows/'+TERMINAL+'/dispatches', payload) != 204:
        raise ValueError('terminal handoff request not accepted')
    return dict(out, status='DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED', obligation_keys=keys)


def verify_parent(run_id, attempt, head, blob, *, client, clock=None, sleep=None,
                  obligation=None, wait_seconds=40, kind='legacy', local_source_blob=None):
    _identity(run_id, attempt, head, blob)
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 40:
        raise ValueError('invalid bounded parent wait')
    clock = clock or (lambda: datetime.now(timezone.utc))
    sleep = sleep or time.sleep
    obligation = obligation or (lambda at: current_obligation(at, kind))
    out = {'parent_run_id': run_id, 'parent_attempt': attempt, 'source_head': head,
           'source_blob': blob, 'source_audit_kind': kind, 'admitted': False, 'readiness_mode': 'audit',
           'witness_or_forecast_verified': False}
    # At most forty one-second waits. The shared client separately bounds total
    # provider transport; ambiguous provider responses are never retried here.
    for elapsed in range(wait_seconds+1):
        row = client.api('/actions/runs/'+str(run_id))
        if _parent(row, run_id, attempt, head, _utc(clock()), kind):
            break
        if elapsed == wait_seconds:
            return dict(out, status='SKIP_PARENT_NOT_COMPLETE')
        sleep(1)
    _audit_steps(client.api, run_id, attempt, kind)
    if _source(client.api, head) != blob:
        raise ValueError('claimed source audit blob does not match parent')
    current, actual = _current_source(client.api)
    if actual != blob:
        return dict(out, status='SKIP_SOURCE_CHANGED', current_main=current)
    keys = obligation(_utc(clock()))
    if not keys:
        return dict(out, status='SKIP_NO_CURRENT_OBLIGATION')
    if local_source_blob is None:
        path = ROOT/SOURCE
        if path.is_symlink():
            raise ValueError('local source path is not a regular snapshot')
        raw = path.read_bytes()
        local_source_blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    if not isinstance(local_source_blob, str) or not HEX.fullmatch(local_source_blob):
        raise ValueError('invalid local source blob')
    if local_source_blob != blob:
        return dict(out, status='SKIP_LOCAL_SOURCE_CHANGED', local_source_blob=local_source_blob)
    if obligation(_utc(clock())) != keys:
        return dict(out, status='SKIP_OBLIGATION_CHANGED')
    return dict(out, status='SOURCE_AUDIT_HANDOFF_ADMITTED', admitted=True, readiness_mode='bridge',
                current_main=current, obligation_keys=keys, parent_wait_seconds=elapsed,
                parent_wait_cap_seconds=wait_seconds, transport_cap_seconds=40)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('prepare', 'post', 'verify'))
    args = parser.parse_args(argv)
    if (os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_REPOSITORY') != REPO):
        raise ValueError('handoff requires this repository main')
    import runtime_custody_bridge as transport
    client = transport.GitHubClient()
    output = ROOT/'forward-readiness-output'
    output.mkdir(exist_ok=True)
    out = {}
    name = 'learned-audit-handoff-'+args.mode+'.json'
    def save(value):
        temporary = output/(name+'.tmp')
        with open(temporary, 'wb') as stream:
            stream.write(transport.encode(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output/name)
    try:
        if args.mode in ('prepare', 'post'):
            run_id, attempt = int(os.environ['GITHUB_RUN_ID']), int(os.environ['GITHUB_RUN_ATTEMPT'])
            head = os.environ['GITHUB_SHA']
            kind = _parent_kind(client.api('/actions/runs/'+str(run_id)))
            blob = _source(client.api, head)
            previous = json.loads((output/name).read_bytes()) if (output/name).exists() else {}
            if (previous.get('request_attempted') is True and previous.get('parent_run_id') == run_id
                    and previous.get('source_head') == head and previous.get('source_blob') == blob):
                out = dict(previous, status='SKIP_PREVIOUS_LOCAL_HANDOFF_ATTEMPT', admitted=False)
            elif args.mode == 'prepare':
                out = prepare_handoff(run_id, attempt, head, blob, client=client, receipt=out, kind=kind)
            else:
                prepared = json.loads((output/'learned-audit-handoff-prepare.json').read_bytes())
                if (prepared.get('prepared') is not True or prepared.get('parent_run_id') != run_id
                        or prepared.get('parent_attempt') != attempt or prepared.get('source_head') != head
                        or prepared.get('source_blob') != blob
                        or prepared.get('source_audit_kind') != kind
                        or not re.fullmatch('[a-f0-9]{32}', str(prepared.get('intent_nonce', '')))):
                    raise ValueError('prepared local handoff context changed')
                out = request_terminal(run_id, attempt, head, blob,
                    intent_artifact_id=int(os.environ['HANDOFF_INTENT_ARTIFACT_ID']),
                    expected_keys=prepared['obligation_keys'], client=client, receipt=out,
                    persist_intent=save, kind=kind)
        else:
            out = verify_parent(int(os.environ['SOURCE_AUDIT_RUN_ID']),
                int(os.environ['SOURCE_AUDIT_ATTEMPT']), os.environ['SOURCE_AUDIT_HEAD'],
                os.environ['SOURCE_AUDIT_BLOB'], client=client, kind=os.environ['SOURCE_AUDIT_KIND'])
    except Exception as exc:
        out.update(status='SOURCE_AUDIT_HANDOFF_BLOCKED', admitted=False,
                   error=type(exc).__name__+': '+str(exc)[:300])
        save(out)
        raise
    save(out)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            if args.mode == 'verify':
                stream.write('admitted='+str(out['admitted']).lower()+'\n')
                stream.write('readiness_mode='+out['readiness_mode']+'\n')
            elif args.mode == 'prepare':
                stream.write('prepared='+str(out.get('prepared') is True).lower()+'\n')
    print(json.dumps(out, sort_keys=True))


if __name__ == '__main__':
    main()

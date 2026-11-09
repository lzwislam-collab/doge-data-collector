"""Bounded routing to existing R2 jobs; never a second local journal writer."""
import base64
import copy
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
import phase2_forward_runtime_v2 as frozen
import runtime_custody_bridge as transport

STATES = ('pending', 'queued', 'requested', 'waiting', 'in_progress')
AUDIT = 'phase2_forward_audit_v2.yml'
PUBLISHER = 'phase2_forward_live_v2.yml'
HEX = re.compile('[a-f0-9]{40}')


def _utc(at):
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise ValueError('actual UTC clock required')
    return at


def _main_only():
    if (os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_REPOSITORY') != frozen.f.old.REPO):
        raise ValueError('R2 bridge requires existing main collector')


def _active(api, workflow):
    for state in STATES:
        result = api('/actions/workflows/'+workflow+'/runs?'+urlencode({
            'branch': 'main', 'status': state, 'per_page': 1}))
        rows, count = result['workflow_runs'], result['total_count']
        if (not isinstance(rows, list) or type(count) is not int or count < 0
                or len(rows) > 1 or bool(rows) != bool(count)):
            raise ValueError('incomplete workflow queue response')
        if rows:
            row = rows[0]
            if (not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0
                    or row.get('head_branch') != 'main'
                    or row.get('path', '').split('@')[0] != '.github/workflows/'+workflow
                    or row.get('status') not in STATES+('completed',)):
                raise ValueError('invalid active workflow identity')
            return row
    return None


def dispatch_existing(workflow, mode=None, *, client=None, before_post=None):
    """Same approved payloads and server-filtered guard, one 40s transport client."""
    _main_only()
    if workflow not in (AUDIT, PUBLISHER, 'learned_shadow_v1.yml'):
        raise ValueError('unapproved bounded dispatch')
    if (workflow == PUBLISHER and mode not in ('publish', 'canary')
            or workflow != PUBLISHER and mode is not None):
        raise ValueError('unapproved existing workflow input')
    client = client or transport.GitHubClient()
    active = _active(client.api, workflow)
    if active is not None:
        return {'workflow': workflow, 'status': 'ALREADY_PENDING', 'active_run_id': active['id']}
    payload = {'ref': 'main'}
    if workflow == PUBLISHER:
        payload['inputs'] = {'mode': mode}
    if before_post is not None:
        if not callable(before_post):
            raise ValueError('invalid dispatch intent callback')
        before_post()
    if client.request('/actions/workflows/'+workflow+'/dispatches', payload) != 204:
        raise ValueError('existing workflow dispatch not accepted')
    return {'workflow': workflow, 'status': 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED'}


def _fixed_files(root):
    return {name: frozen.frozen.file_bytes(Path(root), name)
            for name in frozen.FIXED | {frozen.f.REGISTRATION}}


def _server_bytes(root, client, state, head, path):
    """Pinned provider bytes, cached by immutable head/path, with exact blob hash."""
    if (not HEX.fullmatch(head) or path not in frozen.FIXED | {frozen.f.REGISTRATION, frozen.SOURCE}
            and not re.fullmatch(re.escape(frozen.f.BASE)+r'/(events|scores)/[0-9]+_(formal|canary)\.json', path)):
        raise ValueError('unsafe frozen witness path')
    cache = state.setdefault('r2_server_bytes', {})
    key = (head, path)
    if key in cache:
        return cache[key]
    try:
        value = client.api('/contents/'+path+'?'+urlencode({'ref': head}))
    except HTTPError as exc:
        if exc.code != 404:
            raise
        # A provider 404 can be transient even at a pinned commit.
        return b''
    if (value.get('type') != 'file' or value.get('path') != path
            or not HEX.fullmatch(str(value.get('sha', '')))
            or type(value.get('size')) is not int or not 0 <= value['size'] <= 32000000):
        raise ValueError('invalid immutable witness file')
    blob = value
    if value.get('encoding') == 'none':
        blob = client.api('/git/blobs/'+value['sha'])
        if blob.get('sha') != value['sha'] or blob.get('size') != value['size']:
            raise ValueError('large witness blob metadata changed')
    if blob.get('encoding') != 'base64' or not isinstance(blob.get('content'), str):
        raise ValueError('immutable witness content absent')
    raw = base64.b64decode(blob['content'].replace('\n', ''), validate=True)
    if len(raw) != value['size'] or transport.git_blob(raw) != value['sha']:
        raise ValueError('immutable witness blob hash mismatch')
    if len(cache) >= 192:
        cache.clear()
    cache[key] = raw
    return raw


def _remote_fixed_files(root, client, state, head):
    """Use only local immutable bytes matching this public registration manifest."""
    raw = _server_bytes(root, client, state, head, frozen.f.REGISTRATION)
    registration = transport.parse(raw)
    files = frozen.verify_files(Path(root), registration)
    return {**files, frozen.f.REGISTRATION: raw}


def _server_runs(api, extra=()):
    value = api('/actions/workflows/'+AUDIT+'/runs?per_page=30')['workflow_runs']
    if not isinstance(value, list):
        raise ValueError('invalid original R2 audit listing')
    rows = [row for row in value if row.get('head_branch') == 'main'
            and row.get('status') == 'completed' and row.get('conclusion') == 'success'][:4]
    known = {row['id'] for row in rows}
    for rid in sorted(set(extra)-known):
        if type(rid) is not int or rid <= 0:
            raise ValueError('invalid original R2 witness locator')
        rows.append(api('/actions/runs/'+str(rid)))
    result = []
    for row in rows:
        if row.get('status') != 'completed' or row.get('conclusion') != 'success':
            continue
        jobs = api('/actions/runs/'+str(row['id'])+'/jobs')['jobs']
        if not isinstance(jobs, list):
            raise ValueError('invalid R2 server job response')
        for job in jobs:
            if job.get('name') == 'audit':
                if job.get('run_id') != row['id']:
                    raise ValueError('R2 job belongs to another run')
                result.append({**row, 'audit_job': job})
    return result


def _view(root, client, state, now):
    head = transport.current_main_sha(client.api)
    trees, config = transport._event_trees(client.api, head)
    _, policy = transport._policies(client.api, head, config)
    frozen.f.check_policy(policy)
    entries = trees['r2']
    if any(not re.fullmatch(r'[0-9]+_(formal|canary)\.json', name)
           or row.get('type') != 'blob' or row.get('mode') != '100644'
           for name, row in entries.items()):
        raise ValueError('unexpected R2 event tree member')
    canaries = [name for name in entries if name.endswith('_canary.json')]
    if len(canaries) != 1:
        raise ValueError('target-only route requires exactly one existing frozen canary')
    start = frozen.f.utc(policy['effective_from_utc'])
    slot = start+timedelta(seconds=int((now-start).total_seconds()//policy['slot_seconds'])*policy['slot_seconds'])
    wanted = {str(int((slot-timedelta(seconds=n*policy['slot_seconds'])).timestamp()))+'_formal.json'
              for n in (0, 1)} | set(canaries)
    events, hashes = [], {}
    cache = state.setdefault('r2_event_blobs', {})
    for name in sorted(wanted & set(entries)):
        row = entries[name]
        if row['sha'] not in cache:
            raw = transport.read_pinned_file(client.api, frozen.f.BASE+'/events/'+name, head)
            if transport.git_blob(raw) != row['sha']:
                raise ValueError('R2 event tree/blob mismatch')
            value = transport.parse(raw)
            if frozen.f.encode(value) != raw:
                raise ValueError('noncanonical R2 event')
            cache[row['sha']] = value
        event = copy.deepcopy(cache[row['sha']])
        cohort = 'canary' if name.endswith('_canary.json') else 'formal'
        issued = frozen.f.utc(event['issued_at_utc'])
        recorded_slot = frozen.f.utc(event['slot_utc'])
        horizon = policy['canary_horizon_seconds'] if cohort == 'canary' else policy['horizon_seconds']
        if (event['event_id']+'.json' != name or event.get('cohort') != cohort
                or event.get('protocol_id') != policy['protocol_id']
                or event.get('policy_sha256') != frozen.f.digest(policy)
                or event.get('horizon_seconds') != horizon
                or event.get('included_in_formal_statistics') is not (cohort == 'formal')
                or event['event_id'] != str(int(recorded_slot.timestamp()))+'_'+cohort
                or cohort == 'formal' and (recorded_slot < start
                    or (recorded_slot-start).total_seconds() % policy['slot_seconds']
                    or not recorded_slot <= issued < recorded_slot+timedelta(seconds=policy['issue_window_seconds']))
                or cohort == 'canary' and recorded_slot != issued
                or any(type(event.get(key)) is not int or event[key] <= 0
                       for key in ('registration_run_id', 'source_run_id'))
                or issued > now or frozen.f.utc(event['target_utc']) != issued+timedelta(seconds=horizon)):
            raise ValueError('R2 event identity/timing changed')
        events.append(event)
        hashes[event['event_id']] = row['sha']
    scores, bindings = {}, {}
    for event in events:
        if event['cohort'] != 'formal':
            continue
        key = event['event_id']
        if now < frozen.f.deadline(event, policy):
            score = transport.read_pinned_file(client.api, frozen.f.BASE+'/scores/'+key+'.json', head, missing=True)
            if score is not None:
                value = transport.parse(score)
                if frozen.f.encode(value) != score or value.get('event_id') != key:
                    raise ValueError('invalid existing R2 score')
                scores[key] = value
            bound = transport.read_pinned_file(client.api, frozen.f.BASE+'/bindings/'+key+'_prediction.json', head, missing=True)
            if bound is not None:
                value = transport.parse(bound)
                if frozen.f.encode(value) != bound:
                    raise ValueError('invalid existing R2 binding')
                bindings[key] = value
    if len(cache) > 32:
        state['r2_event_blobs'] = {hashes[e['event_id']]: e for e in events}
    return {'head': head, 'policy': policy, 'events': events, 'scores': scores, 'bindings': bindings,
            'hashes': hashes, 'slot': slot, 'canary_id': canaries[0][:-5], 'existing_canary_count': 1}


def _choice(root, client, state, view, now, *, remote_only=False):
    policy, events, scores = view['policy'], view['events'], view['scores']
    active = [event for event in events if event['cohort'] == 'formal'
              and now < frozen.f.deadline(event, policy)]
    due = [event for event in active if frozen.f.utc(event['target_utc']) <= now and event['event_id'] not in scores]
    unbound = [event for event in active if frozen.f.utc(event['target_utc']) > now
               and event['event_id'] not in view['bindings']]
    issue_key = str(int(view['slot'].timestamp()))+'_formal'
    issue = (frozen.f.utc(policy['effective_from_utc']) <= now < frozen.f.utc(policy['observation_end_utc'])
             and now < view['slot']+timedelta(seconds=policy['issue_window_seconds'])
             and issue_key not in {event['event_id'] for event in events})
    if not due and not unbound and not issue:
        return {'status': 'SKIP_NO_CURRENT_R2_OBLIGATION'}
    extra = [event[k] for event in active for k in ('registration_run_id', 'source_run_id')]
    extra += [value['run_id'] for value in view['bindings'].values()]
    runs = _server_runs(client.api, extra)
    def proof(files, before, after=None):
        failures = []
        def read(head, path):
            try:
                return _server_bytes(root, client, state, head, path)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                # The frozen predicate correctly treats an unavailable witness
                # as unqualified. The outer router must additionally preserve
                # the transport/content error and avoid a new POST on ambiguity.
                failures.append(exc)
                return b''
        found = frozen.f.witness(files, runs, read, before, after)
        if failures:
            raise failures[0]
        return found
    if due or unbound:
        files = (_remote_fixed_files(root, client, state, view['head'])
                 if remote_only else _fixed_files(root))
        ready = [event for event in due or unbound if proof({**files,
                 frozen.f.BASE+'/events/'+event['event_id']+'.json': frozen.f.encode(event)},
                 frozen.f.utc(event['target_utc']), frozen.f.utc(event['issued_at_utc']))]
        if ready:
            purpose = 'target' if due else 'binding'
            ids = sorted(event['event_id'] for event in ready)
            return {'workflow': PUBLISHER, 'mode': 'canary', 'purpose': purpose,
                    'key': purpose+':'+','.join(ids), 'event_ids': ids}
        if due:
            return {'status': 'WAIT_MISSING_ORIGINAL_PREDICTION_WITNESS'}
        ids = sorted(event['event_id'] for event in unbound)
        return {'workflow': AUDIT, 'mode': None, 'purpose': 'prediction_witness',
                'key': 'prediction:'+','.join(view['hashes'][key] for key in ids), 'event_ids': ids}
    if remote_only:
        raw = _server_bytes(root, client, state, view['head'], frozen.SOURCE)
    else:
        raw = (Path(root)/frozen.SOURCE).read_bytes()
        metadata = client.api('/contents/data/hourly_research?'+urlencode({'ref': view['head']}))
        found = [row for row in metadata if row.get('type') == 'file' and row.get('path') == frozen.SOURCE]
        if (len(found) != 1 or found[0].get('sha') != transport.git_blob(raw)
                or found[0].get('size') != len(raw)):
            return {'status': 'WAIT_SOURCE_PUBLICATION'}
    try:
        frame = frozen.f.frame_from_raw(raw, now)
    except ValueError:
        return {'status': 'WAIT_CURRENT_FROZEN_SOURCE_FRAME'}
    state['r2_source_frame_context'] = {'head': view['head'], 'frame': frame, 'at': now}
    witness = proof({frozen.SOURCE: raw}, now)
    if witness is None:
        return {'workflow': AUDIT, 'mode': None, 'purpose': 'source_witness',
                'key': 'source:'+hashlib.sha256(raw).hexdigest(), 'event_ids': []}
    return {'workflow': PUBLISHER, 'mode': 'publish', 'purpose': 'issue',
            'key': 'issue:'+issue_key, 'event_ids': [issue_key]}


def _post_window_open(choice, view, state, now):
    """Pure final admission after every provider read, using actual current UTC."""
    policy = view['policy']
    purpose = choice['purpose']
    if purpose in ('source_witness', 'issue'):
        if not (frozen.f.utc(policy['effective_from_utc']) <= now < frozen.f.utc(policy['observation_end_utc'])
                and view['slot'] <= now < view['slot']+timedelta(seconds=policy['issue_window_seconds'])):
            return False
        context = state.get('r2_source_frame_context', {})
        if context.get('head') != view['head'] or now < context['at']:
            return False
        try:
            frozen.f.features(context['frame'], now)
        except ValueError:
            return False
        return True
    events = {event['event_id']: event for event in view['events']}
    if purpose not in ('target', 'binding', 'prediction_witness') or not choice['event_ids']:
        return False
    for key in choice['event_ids']:
        event = events.get(key)
        if event is None or event['cohort'] != 'formal' or key in view['scores']:
            return False
        target = frozen.f.utc(event['target_utc'])
        if purpose == 'target':
            if not target <= now < frozen.f.deadline(event, policy):
                return False
        elif not frozen.f.utc(event['issued_at_utc']) <= now < target:
            return False
    return True


def route_r2(root, publication, state, *, clock=None, client=None):
    _main_only()
    remote_only = publication is None
    if not remote_only and (not isinstance(publication, dict)
            or publication.get('status') != 'verified_remote_readback'
            or not HEX.fullmatch(str(publication.get('head', '')))):
        raise ValueError('R2 route requires verified publication or explicit remote-only context')
    context = {'remote_pinned_state_only': remote_only, 'parent_publication_verified': not remote_only}
    client = client or transport.GitHubClient()
    clock = clock or (lambda: datetime.now(timezone.utc))
    began = time.monotonic()
    view = _view(root, client, state, _utc(clock()))
    choice = _choice(root, client, state, view, _utc(clock()), remote_only=remote_only)
    if 'workflow' not in choice:
        return {**choice, **context}
    attempts = state.setdefault('r2_dispatch_attempts', {})
    if choice['key'] in attempts:
        return {'status': 'ALREADY_ATTEMPTED_THIS_R2_CONTEXT', **choice}
    active = _active(client.api, choice['workflow'])
    if active is not None:
        return {'status': 'DEFERRED_ACTIVE_R2_WORKFLOW', 'active_run_id': active['id'], **choice}
    current = _view(root, client, state, _utc(clock()))
    fresh = _choice(root, client, state, current, _utc(clock()), remote_only=remote_only)
    if (fresh.get('key') != choice['key'] or fresh.get('workflow') != choice['workflow']
            or fresh.get('mode') != choice['mode']):
        return {'status': 'DEFERRED_R2_STATE_CHANGED_BEFORE_POST'}
    if (current['canary_id'] != view['canary_id']
            or current['hashes'][current['canary_id']] != view['hashes'][view['canary_id']]):
        raise ValueError('existing frozen canary changed during routing')
    if time.monotonic()-began >= 40:
        raise TimeoutError('R2 routing phase budget exhausted before POST')
    payload = {'ref': 'main'}
    if choice['workflow'] == PUBLISHER:
        payload['inputs'] = {'mode': choice['mode']}
    # No provider I/O occurs between this final UTC admission and the POST.
    post_at = _utc(clock())
    if not _post_window_open(fresh, current, state, post_at):
        return {'status': 'DEFERRED_R2_WINDOW_CLOSED_BEFORE_POST', **context}
    # An unknown POST result consumes this context in this existing runtime.
    attempts[choice['key']] = {'at_utc': post_at.isoformat(), 'head': current['head']}
    status = client.request('/actions/workflows/'+choice['workflow']+'/dispatches', payload)
    if status != 204:
        raise ValueError('R2 dispatch not accepted')
    return {'status': 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED', **choice,
            'main_sha': current['head'], 'existing_canary_count': 1, 'witness_or_forecast_verified': False, **context}

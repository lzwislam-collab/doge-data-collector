"""Sequential outer scheduling for frozen original18; never a witness itself.

The normal collector owns the existing shared writer lock. It may call the
unchanged frozen producer sequentially, publish its exact new journal paths
through the same publisher, then request the existing independent audit.
"""
import hashlib
import copy
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import learned_shadow_runtime as original

CAPTURE_TIMEOUT_SECONDS = 90
_KEY = r'[0-9]+_(?:1|24)'
_BUNDLE_CACHE = {}


def _utc(at):
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise ValueError('actual UTC clock required')
    return at


def _main_only():
    if (os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_REPOSITORY') != original.shadow.REPO):
        raise ValueError('original18 outer writer restricted to existing main collector')


def _allowed(name):
    return (name == original.BASE+'/runtime_registration.json'
            or re.fullmatch(re.escape(original.BASE)+r'/(?:events|scores|runtime_receipts)/'+_KEY+r'\.json', name)
            or re.fullmatch(re.escape(original.BASE)+r'/witness_bindings/'+_KEY+r'_(?:prediction|target)\.json', name))


def _journal(root):
    root = Path(root)
    base = root/original.BASE
    for path in (root, root/'data', base):
        if path.is_symlink():
            raise ValueError('symlink original18 journal')
    rows = {}
    for path in base.rglob('*'):
        if path.is_symlink():
            raise ValueError('symlink original18 journal member')
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if not _allowed(name):
            raise ValueError('unexpected original18 journal path')
        rows[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return rows


def original18_state(root):
    """Frozen policy, canonical journals and complete history guards unchanged."""
    root = Path(root)
    policy = json.loads((root/original.shadow.POLICY).read_bytes())
    original.shadow.check_policy(policy)
    events = original.read_records(root, 'events')
    scores = {row['event_id']: row for row in original.read_records(root, 'scores')}
    return {'policy': policy, 'events': events, 'scores': scores,
            'bindings': original.read_bindings(root)}


def _bundle_inputs(root):
    root = Path(root).resolve()
    files = {name: original.bundle.file_bytes(root, name)
             for name in original.shadow.REGISTERED_FILES}
    package = json.loads(files[original.shadow.PACKAGE])
    manifest_raw = original.bundle.file_bytes(root, original.MANIFEST)
    manifest = json.loads(manifest_raw)
    runtime = original.verify_runtime_files(root, manifest['sha256_lf'])
    commit = package['training_source_commit']
    if not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('invalid frozen training source identity')
    training = original.bundle.git(root, 'show', commit+':'+original.bundle.SOURCE)
    if original.shadow.sha(training) != package['training_input_sha256']:
        raise ValueError('training source Git bytes changed')
    actual = {**files, **runtime, original.MANIFEST: manifest_raw}
    key = (str(root), original.bundle.np.__version__,
           tuple(sorted((name, hashlib.sha256(raw).hexdigest()) for name, raw in actual.items())),
           hashlib.sha256(training).hexdigest())
    return key


def _checked_bundle(root):
    """Only the complete immutable model guard is cached; journal guards are not."""
    root = Path(root).resolve()
    try:
        key = _bundle_inputs(root)
    except Exception:
        for old in [old for old in _BUNDLE_CACHE if old[0] == str(root)]:
            del _BUNDLE_CACHE[old]
        raise
    if key not in _BUNDLE_CACHE:
        result = original.bundle.guard(root)
        if _bundle_inputs(root) != key:
            raise ValueError('immutable original18 inputs changed during full guard')
        if len(_BUNDLE_CACHE) >= 8:
            _BUNDLE_CACHE.clear()
        _BUNDLE_CACHE[key] = copy.deepcopy(result)
    return copy.deepcopy(_BUNDLE_CACHE[key])


def validate_original18(root):
    """Complete model/runtime guard plus fresh canonical and append history audit.

    Invoke before staging and after each rebase. Existing historical failures
    remain audit facts; this local integrity validation is not a public witness.
    """
    root = Path(root).resolve()
    _journal(root)
    policy, package, _ = _checked_bundle(root)
    manifest = json.loads(original.bundle.file_bytes(root, original.MANIFEST))
    original.verify_runtime_files(root, manifest['sha256_lf'])
    if (manifest['model_package_sha256'] != original.shadow.digest(package)
            or manifest['effective_from_utc'] != policy['effective_from_utc']
            or manifest['publication_only_not_independent_worm'] is not True):
        raise ValueError('runtime policy changed')
    names = original.bundle.git(root, 'log', '--format=', '--name-only', '--', original.BASE).decode().splitlines()
    # Include deleted history paths, rather than auditing only currently present files.
    for name in set(filter(None, names)):
        path = root/name
        if not _allowed(name) or path.is_symlink() or not path.is_file():
            raise ValueError('published original18 journal deleted or unsafe')
        original.evidence_append_guard.guard(root, name, path.read_bytes())
    events = original.read_records(root, 'events')
    scores = original.read_records(root, 'scores')
    original.read_records(root, 'runtime_receipts')
    original.read_bindings(root)
    locator = root/original.BASE/'runtime_registration.json'
    if locator.exists():
        raw = locator.read_bytes()
        if raw != original.shadow.encode(json.loads(raw)):
            raise ValueError('noncanonical original18 runtime registration')
    original.shadow.audit_records(policy, events, scores, datetime.now(timezone.utc))
    return {'status': 'ORIGINAL18_LOCAL_APPEND_ONLY_VALIDATED',
            'events': len(events), 'scores': len(scores), 'public_witness_verified': False}


def capture_original18(root, producer, *, clock=None, state_reader=None):
    """Call one bounded frozen producer, without local parallel writers or POST.

    producer(timeout_seconds) must use the runner's existing owned-process
    command primitive. Timeout/cancellation propagates unchanged. The caller
    must preserve reported partial errors and publish every changed_path before
    requesting any event/score witness. No returned field is a qualification.
    """
    _main_only()
    at = _utc((clock or (lambda: datetime.now(timezone.utc)))())
    state = (state_reader or original18_state)(Path(root))
    todo = original.plan(state['policy'], state['events'], state['scores'], at)
    bindings = state['bindings']
    pending = any(at < original.shadow.target_deadline(event, state['policy'])
                  and (event['event_id']+'_prediction' not in bindings
                       or event['event_id'] in state['scores']
                       and event['event_id']+'_target' not in bindings)
                  for event in state['events'])
    result = {'status': 'SKIP_NO_CURRENT_OBLIGATION', 'at_utc': at.isoformat(),
              'plan': todo, 'changed_paths': [], 'requires_publication': False,
              'request_witness': False, 'errors': {}, 'historical_backfill': False}
    if not todo['issue'] and not todo['score'] and not pending:
        return result
    before = _journal(root)
    # The caller supplies the existing timeout/owned-process cancellation path.
    raw = producer(CAPTURE_TIMEOUT_SECONDS)
    value = json.loads(raw)
    if (not isinstance(value, dict) or not isinstance(value.get('status'), str)
            or not isinstance(value.get('errors'), dict)
            or type(value.get('request_witness', False)) is not bool):
        raise ValueError('invalid frozen original18 producer result')
    for kind in ('new_events', 'new_scores'):
        if (not isinstance(value.get(kind), list)
                or any(not isinstance(key, str) or not re.fullmatch(_KEY, key) for key in value[kind])):
            raise ValueError('invalid frozen original18 record identity')
    after = _journal(root)
    if any(after.get(path) != digest for path, digest in before.items()):
        raise ValueError('existing original18 journal changed or deleted')
    changed = sorted(set(after)-set(before))
    for kind, folder in (('new_events', 'events'), ('new_scores', 'scores')):
        if any(original.BASE+'/'+folder+'/'+key+'.json' not in changed for key in value[kind]):
            raise ValueError('claimed original18 record not newly present')
    result.update(status='CAPTURE_ERRORS_PRESERVED' if value['errors'] else 'FROZEN_PRODUCER_COMPLETED',
                  runtime_status=value['status'], changed_paths=changed,
                  requires_publication=bool(changed), errors=value['errors'],
                  request_witness=value.get('request_witness', False),
                  new_events=value['new_events'], new_scores=value['new_scores'])
    return result


def request_original18_witness(capture, publication, state, *, dispatch=None, client=None):
    """After exact data readback, request one existing audit per public head.

    WAIT_EXACT_SOURCE_WITNESS also needs the real original audit: no event is
    manufactured while waiting. ALREADY_PENDING is not a witness or acceptance.
    """
    _main_only()
    if not capture.get('request_witness'):
        return {'status': 'SKIP_NO_WITNESS_REQUEST'}
    if (not isinstance(publication, dict)
            or publication.get('status') != 'verified_remote_readback'
            or not isinstance(publication.get('head'), str)
            or not re.fullmatch('[a-f0-9]{40}', publication['head'])):
        raise ValueError('original18 audit requires verified public data')
    paths = capture.get('changed_paths', [])
    if (not isinstance(paths, list) or any(not isinstance(path, str) or not _allowed(path) for path in paths)
            or not set(paths) <= set(publication.get('verified_paths', []))):
        raise ValueError('new original18 paths not independently read back')
    head = publication['head']
    if state.get('original18_audit_requested_head') == head:
        return {'status': 'ALREADY_REQUESTED_THIS_PUBLIC_HEAD', 'requested_head': head}
    if state.get('original18_audit_attempted_head') == head:
        return {'status': 'UNKNOWN_PREVIOUS_AUDIT_DISPATCH_NOT_RETRIED', 'requested_head': head}
    if dispatch is None:
        from runtime_r2_bridge import dispatch_existing
        def dispatch(name):
            return dispatch_existing(name, client=client, before_post=lambda:
                                     state.update(original18_audit_attempted_head=head))
    outcome = dispatch('learned_shadow_v1.yml')
    if (not isinstance(outcome, dict) or outcome.get('workflow') != 'learned_shadow_v1.yml'
            or outcome.get('status') not in ('ALREADY_PENDING', 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')):
        raise ValueError('original18 audit request result invalid')
    if outcome['status'] == 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED':
        state['original18_audit_requested_head'] = head
    return {**outcome, 'requested_head': head, 'exact_head_witness_verified': False}


def route_r2(root, publication, state, **options):
    from runtime_r2_bridge import route_r2 as route
    return route(root, publication, state, **options)

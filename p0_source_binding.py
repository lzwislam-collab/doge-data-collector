"""Producer publication identity; original scientific validators remain authoritative."""
import hashlib
import json
from pathlib import Path
import forward_validation as base
import direction_forward_v42 as frozen
import quote_evidence_v2 as quotes
import p2_paired_execution_v3_1 as engine

REPO = 'lzwislam-collab/doge-data-collector'
PROJECTION_KEY = 'p0_locked_publication_context_v1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode('utf-8')).hexdigest()


def _projection(events):
    result = {}
    for event in events:
        for item in event['source_items']:
            if item.get('source_verified') is not True:
                continue
            fid = item['forecast_id']
            if fid in result and result[fid] != item:
                raise ValueError('contradictory locked source contexts')
            result[fid] = dict(item)
    return result


def locked_contexts(root, *, validate_history=True):
    root = Path(root)
    if validate_history:
        if not (root/engine.EVENTS).exists() and not base.git(
                root, 'log', '-1', '--format=%H', '--', engine.EVENTS):
            return {}
        engine.guard(root)
        import runtime_p2_history
        events = runtime_p2_history.history_guard(root, engine=engine)
    else:
        events = engine.read_events(root)
    return _projection(events)


def binding_fingerprint(root):
    # A new book repeating an existing complete context is not a new audit input.
    return _digest(locked_contexts(root, validate_history=False))


def _first_book_proof_bytes(root, event):
    commits = base.git(root, 'log', '--reverse', '--format=%H', '-S',
                       event['record_sha256'], '--', engine.EVENTS).splitlines()
    if not commits:
        raise ValueError('locked source event is not durably committed')
    commit = commits[0]
    base.git(root, 'merge-base', '--is-ancestor', commit, 'HEAD')
    historical = [json.loads(line) for line in base.git(
        root, 'show', commit+':'+engine.EVENTS).splitlines()]
    if event not in historical:
        raise ValueError('first published source event mismatch')
    return base.git(root, 'show', commit+':data/p0_operations/latest.json')


def _first_proof(root, item):
    events = engine.read_events(root)
    event = next((event for event in events if item in event['source_items']), None)
    if event is None:
        raise ValueError('locked source event absent')
    proof = json.loads(_first_book_proof_bytes(root, event))
    return proof.get('forecast_proofs', {}).get(item['forecast_id'], {})


class _BatchUnavailable(Exception):
    pass


def _recovery_basis(root):
    """Fresh bytes for every current input of the shared original guards."""
    source = engine.source
    paths = set(source.CODE_FILES + (source.POLICY, source.SYSTEM,
        'system/current_paired_shadow.json', source.REG, engine.EVENTS))
    records = root/source.BASE/'records'
    paths.update(path.relative_to(root).as_posix() for path in records.glob('*.json'))
    files = {}
    for relative in sorted(paths):
        path = root/relative
        if path.is_symlink(): raise _BatchUnavailable('unknown recovery input location')
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return (base.git(root, 'rev-parse', 'HEAD'),
            base.git(root, 'for-each-ref', '--format=%(refname) %(objectname)'), files)


class _RecoveryBatch:
    """One evaluate call's work group; no global or cross-pass result storage."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.basis = _recovery_basis(self.root)
        self.events = None
        self.proof_bytes = {}
        self.pairs = None
        self.disabled = False

    def check(self):
        if self.disabled: raise _BatchUnavailable('recovery work group unavailable')
        try:
            if _recovery_basis(self.root) != self.basis:
                raise _BatchUnavailable('recovery source changed during evaluate')
        except Exception:
            self.disabled = True
            raise

    def first_proof(self, item):
        self.check()
        if self.events is None:
            self.events = engine.read_events(self.root)
        event = next((event for event in self.events if item in event['source_items']), None)
        if event is None: raise ValueError('locked source event absent')
        key = event['record_sha256']
        if key not in self.proof_bytes:
            raw = _first_book_proof_bytes(self.root, event)
            self.check()
            self.proof_bytes[key] = raw
        # Only immutable original bytes are shared. Each source gets a fresh map.
        return json.loads(self.proof_bytes[key]).get('forecast_proofs', {}).get(item['forecast_id'], {})

    def paired_records(self):
        self.check()
        if self.pairs is None:
            engine.source.guard(self.root)
            import runtime_paired_history
            records = runtime_paired_history.history_guard(self.root)
            self.check()
            self.pairs = records
        return self.pairs


def recovery_batch(root):
    try: return _RecoveryBatch(root)
    except Exception: return None  # Unknown state retains the original per-source path.


def recover_locked_publication(root, row, bindings, item, *, batch=None):
    from datetime import timedelta
    root = Path(root)
    fid = row['forecast_id']
    shared = type(batch) is _RecoveryBatch and batch.root == root.resolve()
    try:
        proof = batch.first_proof(item) if shared else _first_proof(root, item)
    except Exception:
        if not shared: raise
        batch.disabled = True
        return recover_locked_publication(root, row, bindings, item)
    witness = proof.get('publication', {})
    if (set(witness) != set(base.WCOLS) or witness.get('forecast_id') != fid
            or witness.get('head_sha') != item.get('source_witness_head_sha')
            or witness.get('observed_public_utc') != item.get('observed_public_utc')
            or proof.get('issued_at_utc') != row['issued_at_utc']
            or proof.get('source_quote_utc') != row['source_quote_utc']
            or proof.get('quote_id') != bindings.get(fid+'|ISSUE', {}).get('quote_id')):
        raise ValueError('locked source original P0 proof mismatch')
    if (item.get('issued_at_utc') != row['issued_at_utc']
            or item.get('target_utc') != row['target_utc']
            or item.get('horizon_hours') != int(row['horizon_hours'])
            or item.get('source_error') is not None or item.get('source_verified') is not True
            or item.get('paired_audit_verified') is not True):
        raise ValueError('locked source forecast context mismatch')
    if shared:
        try: pairs = batch.paired_records()
        except Exception:
            batch.disabled = True
            return recover_locked_publication(root, row, bindings, item)
    else:
        engine.source.guard(root)
        import runtime_paired_history
        pairs = runtime_paired_history.history_guard(root)
    pair = next((pair for pair in pairs if pair['production']['forecast_id'] == fid), None)
    if (pair is None or pair['production'] != row or pair['record_sha256'] != item['pair_sha256']
            or pair['decision']['paper_action'] != item['paper_action']):
        raise ValueError('locked source paired identity mismatch')
    rid = witness['run_id']
    run = base.github_json('/actions/runs/'+rid)
    if (str(run.get('id')) != rid or run.get('head_sha') != witness['head_sha']
            or run.get('html_url') != witness['run_url']
            or witness['run_url'] != f'https://github.com/{REPO}/actions/runs/{rid}'
            or run.get('repository', {}).get('full_name') != REPO
            or run.get('head_repository', {}).get('full_name') != REPO
            or frozen.utc(run.get('created_at', '')) != frozen.utc(witness['observed_public_utc'])):
        raise ValueError('locked source server identity mismatch')
    stamp = frozen.utc(witness['observed_public_utc'])
    issued = frozen.utc(row['issued_at_utc'])
    if not issued-timedelta(seconds=1) <= stamp <= issued+timedelta(minutes=45) or stamp >= frozen.utc(row['target_utc']):
        raise ValueError('locked source original publication window violated')
    checked = base.remote_witnesses(root, {fid: row}, {fid: witness}, [run], persist_cache=False)[fid]
    quotes.verify_bound(root, row, None, bindings)
    quotes.verify_public_issue(root, row, bindings, checked['head_sha'])
    import csv
    import io
    public_bindings = {r['forecast_id']: r for r in csv.DictReader(io.StringIO(
        base.git(root, 'show', checked['head_sha']+':'+quotes.BINDINGS)))}
    if public_bindings.get(fid+'|ISSUE') != bindings.get(fid+'|ISSUE'):
        raise ValueError('locked source ISSUE binding absent at public head')
    if shared:
        try: batch.check()
        except Exception: return recover_locked_publication(root, row, bindings, item)
    return checked


def current_basis(root):
    import runtime_audit_worker as worker
    return {'inputs': worker.fingerprint(root), 'reports': {
        path: hashlib.sha256((Path(root)/path).read_bytes()).hexdigest() for path in worker.OUTPUTS}}


def validate_binding_history(root):
    import runtime_publication_state
    rows = frozen.read_rows(Path(root)/base.WITNESSES, base.WCOLS)
    runtime_publication_state.history_receipts(root, base.WITNESSES, base.WCOLS, rows)
    return rows


def _upcoming(root, now):
    from datetime import timedelta
    policy = engine.guard(root)
    effective = frozen.utc(policy['effective_from_utc'])
    rows = frozen.read_rows(root/base.REG, frozen.FCOLS)
    items = []
    for row in rows.values():
        h = int(row['horizon_hours']); issued = frozen.utc(row['issued_at_utc'])
        if h not in (1, 24) or issued < effective: continue
        if issued > now: raise ValueError('future P2 source forecast')
        items.append(dict(row, horizon_hours=h))
    # Reservation is the unchanged original function and includes unqualified rows.
    return [row for h in (1, 24) for row in engine.reserve([
        row for row in items if row['horizon_hours'] == h])
        if frozen.utc(row['issued_at_utc']) <= now <= frozen.utc(row['target_utc'])+timedelta(minutes=25)]


def _check_publication(root, row, proof, bindings):
    from datetime import timedelta
    witness = proof.get('publication', {})
    fid = row['forecast_id']
    if (set(witness) != set(base.WCOLS) or witness.get('forecast_id') != fid
            or proof.get('issued_at_utc') != row['issued_at_utc']
            or proof.get('source_quote_utc') != row['source_quote_utc']
            or proof.get('quote_id') != bindings.get(fid+'|ISSUE', {}).get('quote_id')):
        raise ValueError('first source publication proof mismatch')
    rid = witness['run_id']; run = base.github_json('/actions/runs/'+rid)
    if (str(run.get('id')) != rid or run.get('head_sha') != witness['head_sha']
            or run.get('html_url') != witness['run_url']
            or witness['run_url'] != f'https://github.com/{REPO}/actions/runs/{rid}'
            or run.get('repository', {}).get('full_name') != REPO
            or run.get('head_repository', {}).get('full_name') != REPO
            or frozen.utc(run.get('created_at', '')) != frozen.utc(witness['observed_public_utc'])):
        raise ValueError('first source server identity mismatch')
    stamp = frozen.utc(witness['observed_public_utc']); issued = frozen.utc(row['issued_at_utc'])
    if not issued-timedelta(seconds=1) <= stamp <= issued+timedelta(minutes=45) or stamp >= frozen.utc(row['target_utc']):
        raise ValueError('first source original publication window violated')
    checked = base.remote_witnesses(root, {fid: row}, {fid: witness}, [run], persist_cache=False)[fid]
    quotes.verify_bound(root, row, None, bindings)
    quotes.verify_public_issue(root, row, bindings, checked['head_sha'])
    import csv, io
    public = {r['forecast_id']: r for r in csv.DictReader(io.StringIO(
        base.git(root, 'show', checked['head_sha']+':'+quotes.BINDINGS)))}
    if public.get(fid+'|ISSUE') != bindings.get(fid+'|ISSUE'):
        raise ValueError('first source public binding differs')
    return checked


def prepare_first_book_bindings(root, operations, now, *, basis):
    import os
    root = Path(root)
    locked = locked_contexts(root)
    selected = [row for row in _upcoming(root, now) if row['forecast_id'] not in locked
                and row['forecast_id'] in operations.get('forecast_proofs', {})]
    if not selected: return {'requires_publication': False}
    if not isinstance(basis, dict) or basis != current_basis(root):
        raise ValueError('complete accepted source basis changed or unavailable')
    if operations != json.loads((root/'data/p0_operations/latest.json').read_bytes()):
        raise ValueError('accepted P0 report changed')
    prior = validate_binding_history(root)
    bindings = frozen.read_rows(root/quotes.BINDINGS, quotes.BCOLS)
    merged = dict(prior); added = {}
    for row in selected:
        fid = row['forecast_id']; row = dict(row, horizon_hours=str(row['horizon_hours']))
        witness = _check_publication(root, row, operations['forecast_proofs'][fid], bindings)
        if fid in prior and prior[fid] != witness:
            raise ValueError('existing source binding differs; fresh audit required')
        if fid not in prior:
            merged[fid] = witness; added[fid] = witness
    before = (root/base.WITNESSES).read_bytes() if (root/base.WITNESSES).exists() else b''
    if added:
        text = frozen.serial(merged, base.WCOLS)
        encoded = text.replace('\n', os.linesep).encode('utf-8')
        # Original-format rows, including their line bytes, must survive serialization.
        if any(line not in encoded for line in before.splitlines(keepends=True)):
            raise ValueError('original witness row bytes would change')
        base.write(root/base.WITNESSES, text)
    expected = (root/base.WITNESSES).read_bytes()
    after = current_basis(root)
    if (after['reports'] != basis['reports'] or any(value != after['inputs'][key]
            for key, value in basis['inputs'].items() if key != base.WITNESSES)):
        raise ValueError('source basis changed during binding preparation')
    return {'requires_publication': True, 'basis': basis, 'after_basis': after,
            'expected_csv': expected, 'added': added,
            'publications': {row['forecast_id']: operations['forecast_proofs'][row['forecast_id']]['publication']
                             for row in selected}}


def verify_prepared_transition(root, plan):
    if (not isinstance(plan, dict) or plan.get('requires_publication') is not True
            or current_basis(root) != plan.get('after_basis')
            or (Path(root)/base.WITNESSES).read_bytes() != plan.get('expected_csv')):
        raise ValueError('prepared source binding transition changed')
    operations = json.loads((Path(root)/'data/p0_operations/latest.json').read_bytes())
    rows = frozen.read_rows(Path(root)/base.WITNESSES, base.WCOLS)
    for fid, witness in plan['publications'].items():
        if operations.get('forecast_proofs', {}).get(fid, {}).get('publication') != witness or rows.get(fid) != witness:
            raise ValueError('prepared source binding no longer matches P0 proof')


def verify_first_book_bindings(root, plan, publication):
    import publish_forward_evidence as publisher
    verify_prepared_transition(root, plan)
    if (not isinstance(publication, dict) or publication.get('status') != 'verified_remote_readback'
            or base.WITNESSES not in publication.get('verified_paths', ())
            or publisher.command(root, 'rev-parse', 'FETCH_HEAD').decode().strip() != publication.get('head')
            or not publisher.verify_readback(root, 'FETCH_HEAD', {base.WITNESSES: plan['expected_csv']})):
        raise ValueError('first source publication has no exact remote ACK')
    return plan['after_basis']

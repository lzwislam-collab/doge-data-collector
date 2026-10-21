"""Read-only E1 deadline observer. Never issues, captures, dispatches or repairs.

Cloud notification state belongs to the independent delivery component. This
module only writes its own report; it never writes the registered E1 namespace.
"""
import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import e1_forward as frozen
import e1_observer_cache as receipt_cache


SCHEMA = 'DOGE_E1_OBSERVER_V1'


@dataclass
class Snapshot:
    policy: dict
    registration: dict
    files: dict
    events: list
    scores: list
    attempts: list
    bindings: dict
    source_commit: str


def clock():
    return datetime.now(timezone.utc)


def utc(value):
    at = frozen.utc(value)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('timezone required')
    return at.astimezone(timezone.utc)


def anomaly(snapshot, kind, event_id, deadline, details):
    identity = [snapshot.policy['protocol_id'],
                snapshot.registration['registration_identity'], event_id, kind]
    key = hashlib.sha256(json.dumps(identity, separators=(',', ':'),
                                    ensure_ascii=True).encode()).hexdigest()
    return {'key': key, 'kind': kind, 'event_id': event_id,
            'deadline_utc': deadline.isoformat() if deadline else None,
            'details': details}


def report(snapshot, checked_at, anomalies=(), status='CHECKED', cutoff=None):
    return {'schema': SCHEMA, 'checked_at_utc': checked_at.isoformat(),
            'evidence_cutoff_utc': (cutoff or checked_at).isoformat(),
            'source_commit': snapshot.source_commit,
            'registration_identity': snapshot.registration['registration_identity'],
            'status': status, 'event_count': len(snapshot.events),
            'score_count': len(snapshot.scores), 'anomalies': list(anomalies),
            'statistical_gate_passed': False, 'validated_70pct': False,
            'real_orders_authorized': False}


def failed(snapshot, at, exc, kind='OBSERVATION_FAILED'):
    # Exception text can contain credentials, response bodies, or signed URLs.
    item = anomaly(snapshot, kind, 'observer', None,
                   {'error_type': type(exc).__name__,
                    'reason': 'Evidence could not be validated; absence was not inferred.'})
    return report(snapshot, at, [item], 'OBSERVATION_FAILED')


class WitnessStore:
    """Exact frozen predicates, with complete range paging and per-check caches."""
    def __init__(self, engine, files, api=None, reader=None, positive_cache=None, now=None):
        self.engine = engine
        self.files = files
        self.api = api or engine.api
        self.reader = reader or (lambda head, path: engine.read_pinned(engine.ROOT, head, path))
        self._responses = {}
        self._runs = {}
        self._bytes = {}
        self.positive_cache = positive_cache
        self.now = now or clock()

    def _get(self, path):
        if path not in self._responses:
            try:
                result = self.api(path)
            except (ValueError, KeyError, TypeError) as exc:
                raise RuntimeError('unreadable witness API response') from exc
            if not isinstance(result, dict):
                raise RuntimeError('malformed witness API response')
            self._responses[path] = result
        return self._responses[path]

    def read(self, head, path):
        key = (head, path)
        if key not in self._bytes:
            try:
                raw = self.reader(head, path)
            except (ValueError, KeyError, TypeError) as exc:
                # The frozen witness predicate intentionally treats these
                # exceptions as candidate mismatches. At the I/O boundary they
                # mean unreadable/unsafe evidence, not verified tree absence.
                raise RuntimeError('pinned witness bytes could not be read safely') from exc
            if not isinstance(raw, bytes):
                raise RuntimeError('malformed pinned witness bytes')
            self._bytes[key] = raw
        return self._bytes[key]

    def _run(self, rid, row=None):
        if type(rid) is not int or rid <= 0:
            raise ValueError('invalid witness locator')
        if rid not in self._runs:
            run = row or self._get('/actions/runs/' + str(rid))
            needed = {'id', 'repository', 'path', 'head_branch', 'head_sha',
                      'status', 'conclusion', 'event', 'created_at'}
            if (not isinstance(run, dict) or not needed <= set(run) or
                    not isinstance(run['repository'], dict) or
                    'full_name' not in run['repository']):
                raise RuntimeError('malformed actual witness run')
            if run['id'] != rid:
                raise RuntimeError('witness API identity mismatch')
            jobs = []
            page = 1
            while True:
                payload = self._get('/actions/runs/' + str(rid) + '/attempts/' +
                                    str(run.get('run_attempt', 1)) +
                                    '/jobs?per_page=100&page=' + str(page))
                if (not isinstance(payload.get('jobs'), list) or
                        type(payload.get('total_count')) is not int):
                    raise RuntimeError('malformed witness job page')
                batch = payload['jobs']
                if any(not isinstance(j, dict) or not {'name', 'status', 'conclusion',
                           'started_at', 'completed_at'} <= set(j) for j in batch):
                    raise RuntimeError('malformed actual witness job')
                jobs.extend(batch)
                if len(jobs) >= payload['total_count']:
                    break
                if not batch:
                    raise RuntimeError('incomplete witness job pagination')
                page += 1
            witnesses = [j for j in jobs if j['name'] == 'witness']
            if len(witnesses) == 1 and witnesses[0]['conclusion'] == 'success':
                try:
                    for stamp in (run['created_at'], witnesses[0]['started_at'], witnesses[0]['completed_at']):
                        utc(stamp)
                except (ValueError, KeyError, TypeError) as exc:
                    raise RuntimeError('malformed actual witness clock') from exc
            # CI-only runs are not successful evidence witnesses.
            self._runs[rid] = {**run, 'audit_job': witnesses[0]} if len(witnesses) == 1 else run
        return self._runs[rid]

    def _check(self, row, required, before, after):
        proof = self.engine.verified_witness(required, [row], self.read,
                                             self.files, before, after)
        if self.positive_cache is not None:
            self.positive_cache.remember(required, self.files, before, after, proof, self.now)
        return proof

    def find(self, required, before, after=None, pinned=(), discover=True,
             discover_from=None):
        if self.positive_cache is not None:
            found = self.positive_cache.get(required, self.files, before, after,
                                            self.now, pinned=pinned, discover=discover)
            if found is not None:
                return found
        for rid in dict.fromkeys(pinned):
            proof = self._check(self._run(rid), required, before, after)
            if proof:
                return proof
        if not discover:
            return None
        # Exact event/score bytes can only enter this frozen protocol's Git tree
        # after actual issuance. Floor the lower bound to whole API seconds so
        # timestamp truncation never excludes the first second of publication.
        lower = discover_from or after
        lower = lower.replace(microsecond=0) if lower else None
        interval = ((lower.isoformat() + '..') if lower else '<=') + before.isoformat()
        seen = set()
        page = 1
        while True:
            query = urlencode({'per_page': 100, 'page': page, 'branch': 'main',
                               'status': 'success', 'created': interval})
            payload = self._get('/actions/workflows/e1_forward_witness.yml/runs?' + query)
            if not isinstance(payload.get('workflow_runs'), list):
                raise RuntimeError('malformed witness run page')
            total = payload.get('total_count')
            if type(total) is not int or total < 0 or total > 1000:
                # GitHub caps filtered run searches at 1000. Never call a
                # truncated range evidence of absence.
                raise RuntimeError('witness range exceeds complete API search capacity')
            batch = payload['workflow_runs']
            for row in batch:
                if not isinstance(row, dict) or type(row.get('id')) is not int:
                    raise RuntimeError('malformed witness run locator')
                rid = row['id']
                if rid in seen:
                    raise RuntimeError('unstable witness pagination')
                seen.add(rid)
                proof = self._check(self._run(rid, row), required, before, after)
                if proof:
                    return proof
            if len(seen) >= total:
                return None
            if not batch:
                raise RuntimeError('incomplete witness run pagination')
            page += 1


def audit(snapshot, checked_at, proofs, validate_event, validate_score, cutoff=None):
    """Pure deadline classification over a fully read snapshot.

    Validation callbacks replay frozen formulas. Any transport/read failure
    discards partial absence findings; it cannot turn an API outage into gaps.
    """
    at = utc(checked_at.isoformat())
    try:
        maturity = utc(cutoff.isoformat()) if cutoff is not None else at
        if maturity > at:
            raise ValueError('evidence cutoff is in the future')
    except (ValueError, TypeError, AttributeError) as exc:
        return failed(snapshot, at, exc)
    p = snapshot.policy
    findings = []
    try:
        start, end = utc(p['effective_from_utc']), utc(p['observation_end_utc'])
        step = timedelta(seconds=p['slot_seconds'])
        issue_window = timedelta(seconds=p['issue_window_seconds'])
        horizon = timedelta(seconds=p['horizon_seconds'])
        target_window = timedelta(seconds=p['target_window_seconds'])
        if step.total_seconds() <= 0 or end <= start:
            raise ValueError('invalid observation window')
        events = {}
        previous_target = None
        for event in sorted(snapshot.events, key=lambda e: e['issued_at_utc']):
            key = event['event_id']; slot = utc(event['slot_utc'])
            issued, target = utc(event['issued_at_utc']), utc(event['target_utc'])
            if (key in events or key != str(int(slot.timestamp())) + '_formal' or
                    event['cohort'] != 'formal' or event['protocol_id'] != p['protocol_id'] or
                    not start <= slot < end or (slot - start).total_seconds() % step.total_seconds() or
                    not slot <= issued < slot + issue_window or issued > at or
                    target != issued + horizon or previous_target is not None and issued < previous_target):
                raise ValueError('future, overlapping, duplicate or inconsistent E1 event')
            events[key] = event
            previous_target = target
        scores = {}
        for score in snapshot.scores:
            key = score['event_id']
            if key in scores or key not in events:
                raise ValueError('duplicate or orphan E1 score')
            scores[key] = score
        # Validate all event formulas before drawing any absence conclusion.
        sources = {key: validate_event(event) for key, event in events.items()}
        slot = start
        while slot < end and slot + issue_window <= maturity:
            key = str(int(slot.timestamp())) + '_formal'
            if key not in events:
                findings.append(anomaly(snapshot, 'MISSING_ISSUANCE', key,
                                        slot + issue_window, {'slot_utc': slot.isoformat()}))
            slot += step
        for key, event in events.items():
            issued, target = utc(event['issued_at_utc']), utc(event['target_utc'])
            deadline = target + target_window
            source = proofs.find({frozen.original.SOURCE: sources[key]}, issued,
                                 pinned=(event['source_run_id'],), discover=False)
            if source and source['head_sha'] != event['input_source_commit']:
                raise ValueError('source witness is bound to a different Git snapshot')
            if not source:
                findings.append(anomaly(snapshot, 'MISSING_SOURCE_WITNESS', key, issued,
                                        {'source_run_id': event['source_run_id']}))
            if maturity >= target:
                binding = snapshot.bindings.get(key + '_prediction')
                pred = proofs.find({frozen.BASE + '/events/' + key + '.json': frozen.encode(event)},
                                   target, issued,
                                   pinned=(binding['run_id'],) if binding else (),
                                   discover_from=issued)
                if not pred:
                    findings.append(anomaly(snapshot, 'MISSING_PREDICTION_WITNESS', key, target,
                                            {'required': 'successful exact public witness before actual target'}))
            score = scores.get(key)
            if score is not None:
                try:
                    validate_score(score, event)
                except (ValueError, KeyError, TypeError) as exc:
                    findings.append(anomaly(snapshot, 'INVALID_SCORE', key, deadline,
                                            {'error_type': type(exc).__name__}))
                    continue
            if maturity < deadline:
                continue
            if score is None:
                findings.append(anomaly(snapshot, 'MISSING_SCORE', key, deadline,
                                        {'required': 'actual target capture and original scoring formula'}))
                continue
            binding = snapshot.bindings.get(key + '_target')
            target_proof = proofs.find({frozen.BASE + '/scores/' + key + '.json': frozen.encode(score)},
                                       deadline, target,
                                       pinned=(binding['run_id'],) if binding else (),
                                       discover_from=issued)
            if not target_proof:
                findings.append(anomaly(snapshot, 'MISSING_TARGET_WITNESS', key, deadline,
                                        {'required': 'successful exact public witness inside target window'}))
        return report(snapshot, at, findings, cutoff=maturity)
    except (ValueError, KeyError, TypeError) as exc:
        return failed(snapshot, at, exc, 'INTEGRITY_FAILURE')
    except Exception as exc:
        return failed(snapshot, at, exc)


def load_snapshot(engine=frozen):
    """Read frozen guards, append history and the complete E1 namespace only."""
    p, registration, files = engine.guard()
    engine.check_history()
    events, scores, attempts, bindings = [engine.records(folder)
                                          for folder in ('events', 'scores', 'attempts', 'bindings')]
    # Also re-read actual registration, archive and pre-start activation proof.
    if engine.registered(p, registration, files) is None:
        raise ValueError('independent E1 registration/activation not verified')
    head = engine.git(engine.ROOT, 'rev-parse', 'HEAD').decode().strip()
    return Snapshot(p, registration, files, events, scores, attempts,
                    {b['event_id']: b for b in bindings}, head)


def validate_attempts(snapshot, at, engine=frozen):
    """Original capture checks also cover failed/unscored capture attempts."""
    events = {event['event_id'] for event in snapshot.events}
    seen = set()
    for attempt in snapshot.attempts:
        packet = attempt['packet']
        requested, captured = utc(packet['requested_at_utc']), utc(packet['captured_at_utc'])
        key = attempt['event_id']
        expected = attempt['forecast_event_id'] + '_attempt_' + str(int(requested.timestamp() * 1000))
        if (key in seen or key != expected or attempt['forecast_event_id'] not in events or
                not requested <= captured <= at or packet['source_urls'] != engine.parent.old.URLS):
            raise ValueError('invalid actual E1 capture attempt')
        seen.add(key)
        for name, body in packet['raw_base64'].items():
            raw = base64.b64decode(body, validate=True)
            if (name not in engine.parent.old.URLS or
                    hashlib.sha256(raw).hexdigest() != packet['raw_sha256'][name] or
                    name in packet['raw_text'] and raw.decode() != packet['raw_text'][name]):
                raise ValueError('E1 original attempt bytes changed')


def validators(snapshot, store, engine=frozen):
    """Reuse the frozen issue/score code and original raw-capture binding."""
    core = engine.engine_core(snapshot.policy)
    model = json.loads((engine.ROOT / engine.parent.MODEL).read_bytes())
    old_model = json.loads((engine.ROOT / engine.parent.old.PACKAGE).read_bytes())['models']['1']['full']
    locator = json.loads((engine.ROOT / engine.LOCATOR).read_bytes())
    validate_attempts(snapshot, clock(), engine)

    def event_check(event):
        at = utc(event['issued_at_utc'])
        if event['registration_run_id'] != locator['run_id']:
            raise ValueError('wrong original registration')
        source = store.read(event['input_source_commit'], engine.original.SOURCE)
        if (hashlib.sha256(source).hexdigest() != event['input_source_sha256'] or
                engine.parent.frame_from_raw(source, at) != event['frame']):
            raise ValueError('source bytes/frame mismatch')
        bx = engine.parent.old.causal_features(engine.parent.benchmark_frame(event['frame']), at)
        probability = engine.prior.predict(old_model, [[bx[j] for j in old_model['columns']]])[0]
        expected = core.issue(snapshot.policy, model, event['frame'], event['entry_packet'], at, probability, 'formal')
        extra = {'input_source_commit', 'input_source_sha256', 'registration_run_id', 'source_run_id'}
        if (engine.encode({k: event.get(k) for k in expected}) != engine.encode(expected) or
                set(event) - set(expected) != extra):
            raise ValueError('frozen issuance replay mismatch')
        return source

    def score_check(score, event):
        engine.original.verify_attempt_binding(score, snapshot.attempts)
        if (score != core.score(event, score['target_packet'], snapshot.policy) or
                utc(score['observed_at_utc']) > clock()):
            raise ValueError('frozen score replay mismatch')
        packet = score['target_packet']
        for key, body in packet['raw_base64'].items():
            raw = base64.b64decode(body, validate=True)
            if (key not in engine.parent.old.URLS or hashlib.sha256(raw).hexdigest() != packet['raw_sha256'][key] or
                    key in packet['raw_text'] and raw.decode() != packet['raw_text'][key]):
                raise ValueError('original capture bytes mismatch')
    return event_check, score_check


def observe(now=None, loader=None, engine=frozen, api=None, reader=None, cutoff=None,
            cache_sink=None):
    # Capture time BEFORE reading main's evidence, so a read that straddles a
    # deadline cannot falsely declare a just-matured gap from an earlier tree.
    at = now or clock()
    empty = Snapshot({'protocol_id': 'DOGE_E1_FORWARD_20261021'},
                     {'registration_identity': 'UNVERIFIED'}, {}, [], [], [], {}, None)
    try:
        empty.source_commit = engine.git(engine.ROOT, 'rev-parse', 'HEAD').decode().strip()
        snapshot = (loader or (lambda: load_snapshot(engine)))()
        fingerprint = receipt_cache.fingerprint(engine.ROOT, snapshot.registration['registration_identity'])
        positive = receipt_cache.load_previous(engine, fingerprint, at, api=api)
        store = WitnessStore(engine, snapshot.files, api, reader,
                             positive_cache=positive, now=at)
        event_check, score_check = validators(snapshot, store, engine)
        try:
            parsed_cutoff = utc(cutoff) if isinstance(cutoff, str) else cutoff
        except (ValueError, TypeError) as exc:
            return failed(snapshot, at, exc)
        value = audit(snapshot, at, store, event_check, score_check, cutoff=parsed_cutoff)
        value['witness_cache'] = {'positive_entries': len(positive.proofs), 'hits': positive.hits,
                                  'loaded_from_run_id': positive.loaded_from_run_id,
                                  'scope': 'prior verified positive engineering receipts; not a fresh independent public audit'}
        if value['status'] == 'CHECKED' and cache_sink is not None:
            cache_sink(positive.payload())
        return value
    except (ValueError, KeyError, TypeError) as exc:
        return failed(empty, at, exc, 'INTEGRITY_FAILURE')
    except Exception as exc:
        return failed(empty, at, exc)


def main():
    output = frozen.ROOT / 'e1-observer-output'
    output.mkdir(exist_ok=True)
    cache_path = output / 'witness-cache.json'
    if cache_path.exists():
        cache_path.unlink()  # Never publish a stale cache after a failed inspection.
    value = observe(cutoff=os.environ.get('E1_OBSERVER_CUTOFF_UTC'),
                    cache_sink=lambda payload: cache_path.write_bytes(frozen.encode(payload)))
    (output / 'report.json').write_bytes(frozen.encode(value))
    print(json.dumps(value, sort_keys=True))
    return 1 if value['status'] == 'OBSERVATION_FAILED' else 0


if __name__ == '__main__':
    raise SystemExit(main())

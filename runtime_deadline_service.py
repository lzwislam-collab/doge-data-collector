"""Serial future-only capture on the existing runner. Never changes frozen rules.

This is an operational service, not a model or an economic cohort. Historical
missing targets and blocked paper prefixes remain missing/blocked. No new cron.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = 'data/runtime_deadline_v1'
REPO = 'lzwislam-collab/doge-data-collector'
PUBLICATION_PROFILE_LIMIT = 64
PUBLICATION_PROFILE_SCOPE = 'nested_publisher_totals_include_callbacks_do_not_sum'
PUBLICATION_PHASES = frozenset(
    ['preflight_validate_p2', 'preflight_validate_state', 'preflight_pair_guard',
     'preflight_pair_history_guard', 'data_publisher_total', 'supplementary_readback',
     'receipt_publisher_total', 'receipt_readback'] +
    [scope+'_post_rebase_'+stage for scope in ('data', 'receipt')
     for stage in ('validate_state', 'pair_guard', 'pair_history_guard', 'validate_p2')])


def finite_seconds(value):
    return (type(value) in (int, float) and 0 <= value <= sys.float_info.max
            and math.isfinite(value))


def safe_publication_profile(value):
    """Keep only fixed labels and bounded numeric diagnostics, never payloads."""
    if (type(value) is not dict or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1 or type(value.get('phases')) is not list
            or type(value.get('status')) is not str or value['status'] not in ('ok', 'error')):
        return None
    phases = []
    truncated = value.get('truncated') is True or len(value['phases']) > PUBLICATION_PROFILE_LIMIT
    missing_timings = 0
    for raw in value['phases'][:PUBLICATION_PROFILE_LIMIT]:
        if (type(raw) is not dict or type(raw.get('stage')) is not str
                or raw['stage'] not in PUBLICATION_PHASES
                or type(raw.get('invocation')) is not int
                or not 1 <= raw['invocation'] <= PUBLICATION_PROFILE_LIMIT
                or type(raw.get('status')) is not str or raw['status'] not in ('ok', 'error')):
            truncated = True
            continue
        row = {key: raw[key] for key in ('stage', 'invocation', 'status')}
        if finite_seconds(raw.get('seconds')):
            row['seconds'] = raw['seconds']
        else:
            missing_timings += 1
        phases.append(row)
    errors = value.get('timing_errors', 0)
    errors = min(PUBLICATION_PROFILE_LIMIT, max(0, errors)) if type(errors) is int else 0
    return {'schema_version': 1, 'clock': 'monotonic', 'scope': PUBLICATION_PROFILE_SCOPE,
            'status': value['status'], 'phases': phases, 'truncated': truncated,
            'timing_errors': max(errors, missing_timings)}


class PublicationProfile:
    """Observe the unchanged publication path; diagnostics cannot reject it."""
    def __init__(self):
        self.phases = []
        self.invocations = {}
        self.truncated = False
        self.timing_errors = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc is not None:
            try:
                exc.publication_profile = self.snapshot('error')
            except Exception:
                pass
        return False

    def clock(self):
        try:
            value = time.monotonic()
            if finite_seconds(value): return value
        except Exception:
            pass
        self.timing_errors = min(PUBLICATION_PROFILE_LIMIT, self.timing_errors+1)
        return None

    def call(self, stage, operation, *args, **kwargs):
        row = started = None
        try:
            if stage in PUBLICATION_PHASES and len(self.phases) < PUBLICATION_PROFILE_LIMIT:
                invocation = self.invocations.get(stage, 0)+1
                self.invocations[stage] = invocation
                row = {'stage': stage, 'invocation': invocation, 'status': 'error'}
                # Append at entry: parent totals precede their nested callbacks.
                self.phases.append(row)
            else:
                self.truncated = True
            started = self.clock()
        except Exception:
            pass
        succeeded = False
        try:
            result = operation(*args, **kwargs)
            succeeded = result is not False
            return result
        finally:
            try:
                ended = self.clock()
                if row is not None:
                    row['status'] = 'ok' if succeeded else 'error'
                    if started is not None and ended is not None and finite_seconds(ended-started):
                        row['seconds'] = ended-started
                    elif started is not None and ended is not None:
                        self.timing_errors = min(PUBLICATION_PROFILE_LIMIT, self.timing_errors+1)
            except Exception:
                pass

    def snapshot(self, status):
        try:
            result = safe_publication_profile({'schema_version': 1, 'status': status,
                'phases': self.phases, 'truncated': self.truncated, 'timing_errors': self.timing_errors})
            if result is not None: return result
        except Exception:
            pass
        return {'schema_version': 1, 'clock': 'monotonic', 'scope': PUBLICATION_PROFILE_SCOPE,
                'status': status, 'phases': [], 'truncated': True, 'timing_errors': 1}


def diagnostic_text(value, limit=1000):
    """Bounded, best-effort redaction; never add argv or an environment dump."""
    if len(value) > 16384 or (isinstance(value, str)
                            and len(value.encode('utf-8', errors='replace')) > 16384):
        return '[omitted: diagnostic input exceeds limit]'
    text = value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value
    secrets = {v for k, v in os.environ.items() if v and re.search(
        r'token|secret|password|passwd|credential|authorization|private.?key|access.?key', k, re.I)}
    # Exception summaries may repr() argv; captured output may contain JSON.
    secrets |= {form for v in secrets for form in
                (repr(v)[1:-1], json.dumps(v)[1:-1], json.dumps(v, ensure_ascii=False)[1:-1])}
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)',
                  '[REDACTED]', text, flags=re.S)
    # Hide the whole URL, including userinfo, path, query and fragment. Also
    # handle JSON-escaped slashes rather than trying to identify every SAS key.
    text = re.sub(r'''\b[a-z][a-z0-9+.-]*:(?:\\?/){2}[^\s<>]+''',
                  '[REDACTED]', text, flags=re.I)
    text = re.sub(r'\b(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b',
                  '[REDACTED]', text)
    text = re.sub(r'''(\b(?:authorization|proxy-authorization|cookie|set-cookie|password|passwd|pwd|(?:[a-z0-9]+[_-])*(?:token|secret|signature|credentials?|api[_-]?key|access[_-]?key|session[_-]?key)|sig)\b[\\"']*\s*[:=]\s*)[^\r\n]+''',
                  r'\1[REDACTED]', text, flags=re.I)
    text = re.sub(r'\b(?:bearer|basic)\s+\S+', '[REDACTED]', text, flags=re.I)
    marker = '[truncated]'
    return text if len(text) <= limit else text[:limit-len(marker)] + marker


def exception_details(exc):
    """Add only subprocess metadata already present; diagnostics cannot raise."""
    prefix = type(exc).__name__ + ': '
    try:
        result = {'error': prefix + diagnostic_text(str(exc), 500)}
        if isinstance(exc, subprocess.CalledProcessError):
            if type(exc.returncode) is int:
                result['returncode'] = exc.returncode
            for name in ('stdout', 'stderr'):
                value = getattr(exc, name, None)
                if isinstance(value, (str, bytes)):
                    result[name] = diagnostic_text(value)
    except Exception:
        result = {'error': prefix + '[diagnostic unavailable]'}
    try:
        profile = safe_publication_profile(getattr(exc, 'publication_profile', None))
        if profile is not None: result['publication_profile'] = profile
    except Exception:
        pass
    return result


def utc(value):
    at = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise ValueError('UTC clock required')
    return at


def open_targets(registry, scores, now):
    now = utc(now)
    return sorted(row['forecast_id'] for row in registry
                  if row['forecast_id'] not in scores
                  and utc(row['target_utc']) <= now <= utc(row['target_utc']) + timedelta(minutes=25))


def cycle(adapter, state, clock):
    started = utc(clock())
    out = {'observed_at_utc': started.isoformat(), 'id': uuid.uuid4().hex,
           'errors': [], 'actions': [], 'issuance': 'NOT_ATTEMPTED', 'fatal': False,
           'historical_backfill': False, 'economic_acceptance_passed': False,
           'this_cycle_smoke_publication_deferred': False,
           'this_cycle_deferred_smoke_pending_at_end': False}
    issuance_publication_complete = False
    audit_pending = False

    def action(name):
        before = utc(clock())
        try:
            result = adapter.action(name)
            out['actions'].append({'stage': name, 'result': result,
                                   'started_at_utc': before.isoformat(),
                                   'seconds': (utc(clock())-before).total_seconds()})
            return True
        except Exception as exc:
            out['errors'].append({'stage': name, **exception_details(exc),
                                  'started_at_utc': before.isoformat(),
                                  'seconds': (utc(clock())-before).total_seconds()})
            return False

    def publish():
        if not action('publish'):
            out['fatal'] = True
            return False
        out['this_cycle_deferred_smoke_pending_at_end'] = False
        # Dispatch is only a request, never claimed to be an exact-head witness.
        action('witness')
        return True

    registry, scores = adapter.state()
    out['expired_unscored_ids'] = sorted(r['forecast_id'] for r in registry
        if r['forecast_id'] not in scores and started > utc(r['target_utc']) + timedelta(minutes=25))
    if open_targets(registry, scores, started):
        action('watch')
        # A collector can fail after a valid append; persist/validate before any
        # other writer. Failed integrity/readback stops the service, not a reset.
        if not publish(): return out

    current_hour = started.replace(minute=0, second=0, microsecond=0)
    issued = any(int(r['horizon_hours']) == 1 and utc(r['source_hour_start_utc']) == current_hour
                 for r in registry)
    if issued:
        out['issuance'] = 'ALREADY_ISSUED'
    elif started >= current_hour + timedelta(minutes=40):
        out['issuance'] = 'SKIP_RESERVE_EXHAUSTED'
    elif action('prepare'):
        after = utc(clock())
        if after.replace(minute=0, second=0, microsecond=0) != current_hour:
            out['issuance'] = 'SKIP_SOURCE_HOUR_CHANGED'
        elif after >= current_hour + timedelta(minutes=40):
            out['issuance'] = 'SKIP_RESERVE_EXHAUSTED'
        else:
            out['issuance'] = 'ATTEMPT_COMPLETED' if action('issue') else 'FAILED'
            state.pop('source_audited_at', None)
            if not publish(): return out
            issuance_publication_complete = out['issuance'] == 'ATTEMPT_COMPLETED' and not out['errors']
    else:
        out['issuance'] = 'FAILED_PREPARATION'

    registry, scores = adapter.state()
    if open_targets(registry, scores, utc(clock())):
        action('watch')
        if not publish(): return out

    # Verify public pair context before recording its eligibility at capture.
    # No later replay may retrofit a previously unverified book into eligibility.
    now = utc(clock())
    if state.get('source_audited_at') is None or (now - state['source_audited_at']).total_seconds() >= 300:
        if not action('source_audit'):
            publish()
            return out
        audit_pending = out['actions'][-1]['result'].get('status') == 'pending'
        if not audit_pending:
            state['source_audited_at'] = utc(clock())
    # New issues invalidate cached registry/source audits even within 5 minutes.
    elif out['issuance'] == 'ATTEMPT_COMPLETED':
        if not action('source_audit'):
            publish()
            return out
        audit_pending = out['actions'][-1]['result'].get('status') == 'pending'
        state['source_audited_at'] = utc(clock())
    book_succeeded = action('book')
    book = out['actions'][-1]['result'] if book_succeeded else {}
    may_defer = (issuance_publication_complete and not out['errors'] and audit_pending
                 and book_succeeded and book.get('mode') == 'smoke'
                 and book.get('formal_eligibility') is False)
    if may_defer and action('preserve_smoke'):
        # The original raw file remains in place and an exact independent copy
        # is already inside the existing always-uploaded runtime artifact tree.
        # A later normal tick or finish() publishes both; this is no remote claim.
        out['this_cycle_smoke_publication_deferred'] = True
        out['this_cycle_deferred_smoke_pending_at_end'] = True
    elif not publish():
        return out

    # Slow preparation/audit may have opened a target. Use a fresh clock, not
    # the tick's original timestamp, before yielding back to the scheduler.
    registry, scores = adapter.state()
    if open_targets(registry, scores, utc(clock())):
        action('watch')
        publish()
    out['completed_at_utc'] = utc(clock()).isoformat()
    return out


def preserve_smoke_book(root, qid):
    """Bind this command's raw book to a local copy in the existing artifact."""
    if not isinstance(qid, str) or not re.fullmatch('[0-9a-f]{64}', qid):
        raise ValueError('current smoke book hash missing or invalid')
    root = Path(root).resolve()
    source = 'data/p2_paired_execution_v3_1/engineering_books/'+qid[:2]+'/'+qid+'.json'
    latest_path = root/'data/p2_paired_execution_v3_1/engineering_smoke_latest.json'
    if latest_path.resolve() != latest_path or (root/source).resolve() != root/source:
        raise ValueError('redirected smoke evidence path')
    latest = json.loads(latest_path.read_text(encoding='utf-8'))
    if (latest.get('status') != 'ok' or latest.get('book_sha256') != qid
            or latest.get('snapshot_path') != source):
        raise ValueError('current smoke stdout/latest binding mismatch')
    body = (root/source).read_bytes()
    if hashlib.sha256(body).hexdigest() != qid:
        raise ValueError('current smoke raw book hash mismatch')
    relative = BASE+'/deferred_smoke_books/'+qid[:2]+'/'+qid+'.json'
    target = root/relative
    if target.resolve() != target:
        raise ValueError('redirected smoke artifact copy path')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != body: raise ValueError('existing smoke artifact copy mismatch')
    else:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', dir=target.parent,
                                             prefix='.smoke-copy-', delete=False) as stream:
                temporary = Path(stream.name)
                if stream.write(body) != len(body): raise OSError('incomplete smoke artifact copy write')
                stream.flush()
                os.fsync(stream.fileno())
            # Install complete bytes without ever replacing an existing copy.
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.resolve() != target or target.read_bytes() != body:
                    raise ValueError('existing smoke artifact copy mismatch')
        finally:
            if temporary is not None: temporary.unlink()
    if target.read_bytes() != body: raise ValueError('smoke artifact copy readback mismatch')
    return {'status': 'local_copy_verified', 'book_sha256': qid, 'source_path': source,
            'artifact_path': relative, 'bytes': len(body),
            'remote_readback_verified': False, 'worm_verified': False}


def save_receipt(root, receipt):
    directory = Path(root) / BASE / 'receipts'
    directory.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(receipt, sort_keys=True, allow_nan=False) + '\n').encode()
    # Exclusive create: existing evidence is never overwritten by a retry.
    with (directory / (receipt['id'] + '.json')).open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def qualified_capture(row, book):
    """Additional shutdown gate; frozen capture/validation remains authoritative."""
    try:
        captured = utc(row['captured_at_utc'])
        if not 0 <= (captured-utc(row['quote_utc'])).total_seconds() <= 30: return False
        return any(x.get('source_verified') is True and x.get('paired_audit_verified') is True
                   and x.get('source_error') is None and x.get('observed_public_utc')
                   and utc(row['quote_utc']) > utc(x['observed_public_utc'])
                   and utc(book['requested_at_utc']) >= utc(x['observed_public_utc'])
                   and utc(row['quote_utc']) < utc(x['target_utc']) for x in row['source_items'])
    except (KeyError, ValueError, TypeError): return False


def run(root, adapter, duration_seconds=2400, clock=None, sleep=None, stop_file=None,
        require_qualified_stop=False):
    if type(duration_seconds) not in (int, float) or not 0 < duration_seconds <= 3000:
        raise ValueError('bounded runtime duration required')
    clock = clock or (lambda: datetime.now(timezone.utc))
    sleep = sleep or time.sleep
    start = utc(clock())
    end = start + timedelta(seconds=duration_seconds)
    state = {}
    count = errors = 0
    maximum = 0
    fatal = False
    if require_qualified_stop and not stop_file: raise ValueError('qualified stop requires a stop file')
    def stop_requested(): return bool(stop_file and Path(stop_file).exists())
    while utc(clock()) < end:
        if stop_requested() and (not require_qualified_stop or adapter.can_stop()): break
        before = utc(clock())
        receipt = cycle(adapter, state, clock)
        elapsed = (utc(clock()) - before).total_seconds()
        receipt['cycle_seconds'] = elapsed
        save_receipt(root, receipt)
        count += 1
        errors += len(receipt['errors'])
        maximum = max(maximum, elapsed)
        if receipt['fatal']:
            fatal = True
            break
        remaining = (end - utc(clock())).total_seconds()
        if remaining > 0: sleep(min(max(1, 60-elapsed), remaining))
    result = {'version': 'RUNTIME_DEADLINE_V1', 'started_at_utc': start.isoformat(),
              'ended_at_utc': utc(clock()).isoformat(), 'cycles': count, 'errors': errors,
              'fatal': fatal, 'maximum_cycle_seconds': maximum, 'cadence_target_met': maximum <= 300,
              'status': 'CAPTURE_LOOP_COMPLETED' if not errors and maximum <= 300 else 'DEGRADED',
              'economic_acceptance_passed': False, 'historical_gaps_repaired': False,
              'real_orders_authorized': False, 'new_economic_cohort_activated': False}
    if require_qualified_stop:
        result['qualified_stop_required'] = True
        result['qualified_stop_satisfied'] = bool(stop_requested() and adapter.can_stop() and not fatal)
        result['last_qualified_book_sha256'] = getattr(adapter,'last_qualified_book',{}).get('book_sha256')
        if not result['qualified_stop_satisfied']:
            result.update(fatal=True, status='DEGRADED',
                          fatal_reason='REQUIRED_QUALIFIED_STOP_NOT_SATISFIED')
    path = Path(root)/BASE/'latest.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return result


class LiveAdapter:
    """Only frozen producer entry points; all writers execute sequentially."""
    def __init__(self, root):
        if os.environ.get('GITHUB_REF') != 'refs/heads/main' or os.environ.get('GITHUB_REPOSITORY') != REPO:
            raise ValueError('live service requires the repository main workflow')
        self.root = Path(root)
        self.last_witness_head = None
        self.core_witness_pending = False
        from runtime_audit_worker import Auditor
        self.auditor = Auditor(self.root)
        self.last_qualified_book = {}

    def command(self, *args, env=None):
        result = subprocess.run(args, cwd=self.root, env=dict(os.environ, **(env or {})),
                                capture_output=True, text=True, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(' '.join(args[:3])+': '+(result.stderr or result.stdout)[-1000:])
        return result.stdout

    def python(self, *args, env=None):
        return self.command(sys.executable, *args, env=env)

    def state(self):
        def read(path):
            with (self.root/path).open(encoding='utf-8', newline='') as stream:
                return list(csv.DictReader(stream))
        reg = read('data/direction_v42/forward_registry.csv')
        scores = {r['forecast_id']: r for r in read('data/direction_v42/forward_scores.csv')}
        return reg, scores

    def action(self, name):
        if name == 'watch':
            self.python('quote_capture_retry.py', 'watch')
        elif name == 'prepare':
            self.python('okx_hourly_research_history.py', env={'DOGE_HOURLY_PAGE_BUDGET': '0'})
            self.python('derived_four_hour.py')
            self.python('feature_ablation.py', 'prepare')
            self.python('minimal_market_forward.py', 'prepare')
        elif name == 'issue':
            self.last_qualified_book = {}
            self.python('paired_forward_cohort.py', 'issue')
            self.python('minimal_market_forward.py', 'bind')
            self.python('feature_ablation.py', 'bind')
        elif name == 'source_audit':
            return self.auditor.poll()
        elif name == 'book':
            # Preserve raw depth during initial/mismatched audit waits without
            # calling it an eligible formal event or retrofitting it later.
            mode = 'capture' if self.source_ready() else 'smoke'
            self.last_smoke_output = None
            captured_output = self.python('p2_paired_execution_v3_1.py', mode)
            if mode == 'smoke': self.last_smoke_output = captured_output
            self.last_qualified_book = {}
            source_count = None
            context = 'AUDIT_NOT_READY'
            if mode == 'capture':
                import p2_paired_execution_v3_1 as engine
                events = engine.read_events(self.root)
                context = 'NO_FORMAL_CAPTURE_EVENT'
                if events:
                    source_count = len(events[-1]['source_items'])
                    context = 'RESERVED_SOURCE_NOT_QUALIFIED' if source_count else 'NO_RESERVED_SOURCE_CONTEXT'
                    book = next(engine.event_books(self.root, [events[-1]]))
                    if qualified_capture(events[-1], book):
                        self.last_qualified_book = events[-1]
                        context = 'QUALIFIED_FORMAL_BOOK'
            return {'status': 'command_completed', 'stage': name, 'mode': mode,
                    'formal_eligibility': bool(self.last_qualified_book),
                    'source_context_status': context, 'reserved_source_count': source_count}
        elif name == 'preserve_smoke':
            captured = json.loads(self.last_smoke_output or '{}')
            if captured.get('status') != 'ok': raise ValueError('successful current smoke output missing')
            return preserve_smoke_book(self.root, captured.get('book_sha256'))
        elif name == 'publish':
            import publish_forward_evidence as publisher
            import paired_forward_cohort as pair
            with PublicationProfile() as profile:
                profile.call('preflight_validate_p2', validate_p2, self.root)
                profile.call('preflight_validate_state', publisher.validate_state, self.root)
                profile.call('preflight_pair_guard', pair.guard, self.root)
                profile.call('preflight_pair_history_guard', pair.history_guard, self.root)
                core_paths = ('data/direction_v42/forward_registry.csv',
                              'data/direction_v42/forward_scores.csv', 'data/quote_evidence_v2/bindings.csv')
                self.core_witness_pending = self.core_witness_pending or bool(
                    self.command('git', 'diff', 'HEAD', '--name-only', '--', *core_paths).strip())
                paths = ['data/hourly_research', 'data/derived_4h_v1', 'data/feature_ablation_v1',
                         'data/minimal_market_forward_v1', 'data/p0_operations',
                         'data/p2_paired_execution_v3_1', 'data/paired_forward_v1', BASE]
                existing = [p for p in paths if (self.root/p).exists()]
                # Source histories can only be appended by their existing producers.
                # Stage an explicit allowlist; never git add the entire checkout.
                for p in existing: self.command('git', 'add', '-f', '--', p)
                staged = self.command('git', 'diff', '--cached', '--name-only').splitlines()
                allowed = tuple(p + '/' for p in paths) + tuple(p + '/' for p in ('data/direction_v42', 'data/quote_evidence_v2'))
                if any(not p.startswith(allowed) for p in staged):
                    raise ValueError('unexpected staged path outside runtime evidence allowlist')
                expected = {p: (self.root/p).read_bytes() for p in staged}
                publication_scope = 'data'
                def validate(current_root):
                    profile.call(publication_scope+'_post_rebase_validate_state', publisher.validate_state, current_root)
                    profile.call(publication_scope+'_post_rebase_pair_guard', pair.guard, current_root)
                    profile.call(publication_scope+'_post_rebase_pair_history_guard', pair.history_guard, current_root)
                    profile.call(publication_scope+'_post_rebase_validate_p2', validate_p2, current_root)
                publication = profile.call('data_publisher_total', publisher.publish, self.root, validate=validate)
                if not profile.call('supplementary_readback', publisher.verify_readback, self.root, 'FETCH_HEAD', expected):
                    raise ValueError('runtime supplementary evidence remote readback mismatch')
                if publication.get('status') != 'verified_remote_readback':
                    raise ValueError('core publication not verified')
                publication.update(pair_error=None, paired_verified_files=sum(p.startswith(pair.BASE+'/') for p in expected))
                receipt_path = pair.BASE+'/publication_latest.json'
                (self.root/receipt_path).write_text(json.dumps(publication)+'\n', encoding='utf-8')
                # This receipt describes the first verified data publication. Persist
                # it in a descendant commit, then stop writing tracked files.
                expected[receipt_path] = (self.root/receipt_path).read_bytes()
                self.command('git', 'add', '-f', '--', receipt_path)
                publication_scope = 'receipt'
                receipt_publication = profile.call('receipt_publisher_total', publisher.publish, self.root, validate=validate)
                if (receipt_publication.get('status') != 'verified_remote_readback'
                        or not profile.call('receipt_readback', publisher.verify_readback, self.root, 'FETCH_HEAD', expected)):
                    raise ValueError('runtime publication receipt remote readback mismatch')
                self.public_head = receipt_publication['remote_head']
                return {'status': 'verified_remote_readback', 'head': self.public_head,
                        'publication_profile': profile.snapshot('ok')}
        elif name == 'witness':
            from urllib.request import Request, urlopen
            if not self.core_witness_pending: return {'status': 'no_new_core_evidence'}
            head = getattr(self, 'public_head', None)
            if not head: raise ValueError('cannot request witness without public head')
            if head == self.last_witness_head: return {'status': 'already_requested', 'head': head}
            token = os.environ.get('GITHUB_TOKEN')
            if not token: raise ValueError('server witness token unavailable')
            request = Request('https://api.github.com/repos/'+REPO+'/actions/workflows/test_forward_validation.yml/dispatches',
                data=json.dumps({'ref': 'main'}).encode(), method='POST',
                headers={'Authorization': 'Bearer '+token, 'Accept': 'application/vnd.github+json',
                         'Content-Type': 'application/json'})
            with urlopen(request, timeout=15) as response:
                if response.status != 204: raise ValueError('witness dispatch not accepted')
            self.last_witness_head = head
            self.core_witness_pending = False
            return {'status': 'requested_only', 'requested_head': head, 'exact_head_verified': False}
        else:
            raise ValueError('unknown runtime action')
        return {'status': 'command_completed', 'stage': name}

    def source_ready(self):
        now = datetime.now(timezone.utc)
        try:
            operations = json.loads((self.root/'data/p0_operations/latest.json').read_text(encoding='utf-8'))
            audit = json.loads((self.root/'data/paired_forward_v1/audit_latest.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            return False
        return (operations.get('status') == audit.get('status') == 'ok'
                and 0 <= (now-utc(operations['audited_at_utc'])).total_seconds() <= 900
                and 0 <= (now-utc(audit['evaluated_at_utc'])).total_seconds() <= 900
                and audit['input_registry_sha256'] == hashlib.sha256(
                    (self.root/'data/direction_v42/forward_registry.csv').read_bytes()).hexdigest())

    def can_stop(self):
        if not self.last_qualified_book or not self.source_ready(): return False
        import p2_paired_execution_v3_1 as engine
        events = engine.read_events(self.root)
        return bool(events and events[-1]['book_sha256'] == self.last_qualified_book['book_sha256']
                    and qualified_capture(events[-1], next(engine.event_books(self.root, [events[-1]]))))


def validate_p2(root):
    import p2_paired_execution_v3_1 as engine
    engine.guard(root)
    events = engine.history_guard(root)
    for _ in engine.event_books(root, events): pass


def finish(root, adapter, result):
    def output(fatal):
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
                stream.write('fatal='+str(fatal).lower()+'\n')
    # Missing output is unsafe in the workflow too. Start unsafe before any
    # disk/network work; explicitly clear only after final receipt persistence.
    output(True)
    # Older run() results only contain the satisfied field when stop was required.
    required = result.get('qualified_stop_required', 'qualified_stop_satisfied' in result)
    if required is True and result.get('qualified_stop_satisfied') is not True:
        result.update(fatal=True, status='DEGRADED',
                      fatal_reason='REQUIRED_QUALIFIED_STOP_NOT_SATISFIED')
    path = Path(root)/BASE/'latest.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish the complete final receipt, including any foreground budget.
    # A successful publication must not be followed by another tracked write.
    path.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    if not result.get('fatal'):
        try:
            adapter.action('publish')
        except Exception as exc:
            details = exception_details(exc)
            result.update(fatal=True, status='DEGRADED', final_publication_error=details.pop('error'))
            if details: result['final_publication_diagnostics'] = details
            path.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    output(bool(result.get('fatal')))
    return result


def finish_due_on_budget_exhaustion(root, adapter, error):
    """One serial due-target attempt; never start issuance or a capture loop."""
    started = datetime.now(timezone.utc)
    registry, scores = adapter.state()
    receipt = {'id': uuid.uuid4().hex, 'observed_at_utc': started.isoformat(),
               'mode': 'WORKFLOW_BUDGET_EXHAUSTED_DUE_ONLY',
               'open_target_ids': open_targets(registry, scores, started),
               'actions': [], 'errors': [], 'fatal': True,
               'historical_backfill': False, 'economic_acceptance_passed': False}
    if receipt['open_target_ids']:
        # A failed watch may still have appended valid evidence. The existing
        # publisher validates and reads back that evidence before we exit.
        for name in ('watch', 'publish'):
            before = datetime.now(timezone.utc)
            try:
                outcome = adapter.action(name)
                receipt['actions'].append({'stage': name, 'result': outcome,
                    'started_at_utc': before.isoformat(),
                    'seconds': (datetime.now(timezone.utc)-before).total_seconds()})
            except Exception as exc:
                receipt['errors'].append({'stage': name, **exception_details(exc),
                    'started_at_utc': before.isoformat(),
                    'seconds': (datetime.now(timezone.utc)-before).total_seconds()})
    receipt['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
    # This degraded receipt is preserved locally/by the existing artifact step;
    # finish() must not republish or clear the fatal downstream-writer gate.
    save_receipt(root, receipt)
    return {'fatal': True, 'status': 'DEGRADED',
            'fatal_reason': 'WORKFLOW_BUDGET_EXHAUSTED', **exception_details(error),
            'observed_at_utc': started.isoformat(), 'cycles': 0,
            'economic_acceptance_passed': False, 'historical_gaps_repaired': False,
            'archive_window_budget': error.budget, 'due_only_receipt': receipt}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration-seconds', type=int, default=2400)
    parser.add_argument('--stop-file', type=Path)
    parser.add_argument('--require-qualified-stop', action='store_true')
    parser.add_argument('--archive-reserve-seconds', type=int)
    parser.add_argument('--workflow-started-at')
    parser.add_argument('--workflow-timeout-seconds', type=int)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    adapter = LiveAdapter(root)
    try:
        if ((args.workflow_started_at is not None or args.workflow_timeout_seconds is not None)
                and args.archive_reserve_seconds is None):
            raise ValueError('workflow budget applies only to foreground archive reserve')
        adapter.python('forward_validation_v2.py', '--integrity-only')
        adapter.command('git', 'config', 'user.name', 'github-actions[bot]')
        adapter.command('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
        duration = args.duration_seconds
        budget = None
        budget_exhausted = None
        if args.archive_reserve_seconds is not None:
            if args.stop_file or args.require_qualified_stop:
                raise ValueError('archive reserve applies only to foreground service')
            from archive_window_budget import WorkflowBudgetExhausted, from_root
            try:
                budget = from_root(root, datetime.now(timezone.utc), duration, args.archive_reserve_seconds,
                                   workflow_started_at=args.workflow_started_at,
                                   workflow_timeout_seconds=args.workflow_timeout_seconds)
            except WorkflowBudgetExhausted as exc:
                budget_exhausted = exc
            else:
                duration = budget['duration_seconds']
                print(json.dumps({'archive_window_budget': budget}), flush=True)
        if budget_exhausted is not None:
            result = finish_due_on_budget_exhaustion(root, adapter, budget_exhausted)
        else:
            result = run(root, adapter, duration, stop_file=args.stop_file,
                         require_qualified_stop=args.require_qualified_stop)
            if budget is not None: result['archive_window_budget'] = budget
    except Exception as exc:
        result = {'fatal': True, 'status': 'DEGRADED', **exception_details(exc),
                  'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'economic_acceptance_passed': False}
    adapter.auditor.close()
    result = finish(root, adapter, result)
    print(json.dumps(result))
    return 0 if result['status'] == 'CAPTURE_LOOP_COMPLETED' else 1


if __name__ == '__main__': raise SystemExit(main())

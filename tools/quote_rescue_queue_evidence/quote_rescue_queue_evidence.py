"""Offline queue appendix for the saved independent single-run verification chain.

No network, collector imports, dispatches, scoring, or runtime policy changes.
An attached old validation receipt is cross-linked, never executed or rewritten.
"""
import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path


def _positive_id(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError('invalid ' + label)
    return value


def _utc(value, label, issues):
    if value is None or value == '':
        return None
    try:
        if not isinstance(value, str):
            raise ValueError()
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if at.tzinfo is None or at.utcoffset() != timedelta(0):
            raise ValueError()
        return at
    except (TypeError, ValueError, OverflowError):
        issues.append('INVALID_TIMESTAMP:' + label)
        return None


def _iso(at):
    return at.isoformat() if at is not None else None


def _seconds(start, end, label, issues):
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds()
    if seconds < 0:
        issues.append('INVALID_ORDER:' + label)
        return None
    return seconds


def _identity(obj, run_id, attempt, label):
    if (type(obj.get('run_id')) is not int or obj['run_id'] != run_id
            or type(obj.get('run_attempt')) is not int or obj['run_attempt'] != attempt):
        raise ValueError(label + ' run/attempt mismatch')


def _rescue_job(response, run_id, attempt):
    if not isinstance(response, dict) or not isinstance(response.get('jobs'), list):
        raise ValueError('jobs response must contain a jobs array')
    jobs = response['jobs']
    total = response.get('total_count')
    if type(total) is not int or total < 0 or total != len(jobs):
        raise ValueError('incomplete or inconsistent jobs response')
    candidates = []
    for job in jobs:
        if not isinstance(job, dict) or type(job.get('run_id')) is not int or job['run_id'] != run_id:
            raise ValueError('job belongs to another run or lacks identity')
        if job.get('name') == 'rescue' and job.get('run_attempt') == attempt:
            if type(job.get('run_attempt')) is not int:
                raise ValueError('invalid job attempt')
            _positive_id(job.get('id'), 'job id')
            candidates.append(job)
    if len(candidates) > 1:
        raise ValueError('ambiguous rescue jobs in the same attempt')
    return candidates[0] if candidates else None


def _required_time(value, label):
    at = _utc(value, label, [])
    if at is None:
        raise ValueError('prior association requires valid UTC ' + label)
    return at


def _attach_receipt(assessment, run, result, quotes, job):
    if assessment is None:
        return {'status': 'NOT_PROVIDED', 'replayed': False}
    if not isinstance(assessment, dict):
        raise ValueError('invalid prior assessment')
    old_id = assessment.get('run_id', assessment.get('run', {}).get('id'))
    if type(old_id) is not int or old_id != run['id']:
        raise ValueError('prior receipt run mismatch')
    old_attempt = assessment.get('run_attempt', assessment.get('run', {}).get('run_attempt'))
    if old_attempt is None:
        # Legacy saved receipts identify only the first attempt. Do not reuse them for reruns.
        if run['run_attempt'] != 1:
            raise ValueError('prior receipt attempt unavailable for rerun')
    elif type(old_attempt) is not int or old_attempt != run['run_attempt']:
        raise ValueError('prior receipt attempt mismatch')
    if (run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or job is None or job.get('status') != 'completed' or job.get('conclusion') != 'success'
            or result.get('status') != 'verified_window_quote_rescue'
            or assessment.get('status') not in (
                'VERIFIED_NATURAL_WINDOW_RESCUE', 'VERIFIED_NATURAL_WINDOW_RESCUE_NEW_CALLER')):
        raise ValueError('prior receipt is not an existing verified rescue result')
    run_created = _required_time(run.get('created_at'), 'run_created')
    job_created = _required_time(job.get('created_at'), 'job_created')
    job_started = _required_time(job.get('started_at'), 'job_started')
    job_completed = _required_time(job.get('completed_at'), 'job_completed')
    if not run_created <= job_created <= job_started <= job_completed:
        raise ValueError('prior receipt conflicts with execution interval')
    old_flags = [assessment[k] for k in (
        'old_rows_registry_snapshots_frozen_code_unchanged',
        'old_registry_scores_bindings_and_snapshots_unchanged') if k in assessment]
    if not old_flags or any(v is not True for v in old_flags):
        raise ValueError('prior preservation verification missing or false')
    publication = result.get('publication', {})
    old_publication = assessment.get('publication', {})
    verified = _required_time(publication.get('verified_at_utc'), 'verified_at_utc')
    _required_time(old_publication.get('verified_at_utc'), 'prior_verified_at_utc')
    if not publication.get('remote_head') or any(
            publication.get(k) != old_publication.get(k)
            for k in ('remote_head', 'verified_at_utc')):
        raise ValueError('prior publication receipt mismatch')
    old_targets = assessment.get('targets', [])
    old = {t['forecast_id']: t for t in old_targets}
    current = {t['forecast_id']: t for t in quotes}
    scored = result.get('scored_ids', [])
    if (not old or len(old) != len(old_targets) or set(old) != set(current)
            or len(scored) != len(set(scored)) or set(scored) != set(current)):
        raise ValueError('prior target set mismatch')
    for fid, q in current.items():
        t = old[fid]
        target = _required_time(q.get('target_utc'), 'target_utc')
        quoted = _required_time(q.get('target_quote_utc'), 'target_quote_utc')
        observed = _required_time(q.get('observed_at_utc'), 'observed_at_utc')
        for key in ('target_utc', 'quote_utc', 'remote_verified_at_utc'):
            _required_time(t.get(key), 'prior_' + key)
        if (not target <= quoted <= observed <= target + timedelta(minutes=20)
                or not job_started <= observed <= verified <= job_completed
                or verified > target + timedelta(minutes=25)):
            raise ValueError('prior receipt conflicts with target/execution clocks')
        if (t.get('issue_target_binding_and_score_verified') is not True
                or t.get('target_utc') != q.get('target_utc')
                or t.get('quote_utc') != q.get('target_quote_utc')
                or t.get('remote_verified_at_utc') != publication.get('verified_at_utc')):
            raise ValueError('prior target verification mismatch')
    # This is a reference to the existing receipt, not a new raw/score validation.
    return {'status': 'ATTACHED_PRIOR_VERIFIED_RECEIPT',
            'source_status': assessment['status'], 'replayed': False}


def build_queue_evidence(evidence, *, assessment=None, jobs_response=None,
                         caller_record=None, concurrency_snapshot=None):
    run = evidence.get('run', {})
    run_id = _positive_id(run.get('id'), 'run id')
    attempt = _positive_id(run.get('run_attempt'), 'run attempt')
    if (run.get('path') != '.github/workflows/quote_only_rescue.yml'
            or run.get('event') != 'workflow_dispatch' or run.get('head_branch') != 'main'):
        raise ValueError('only independent quote-only main dispatch evidence is accepted')
    issues = []
    response = jobs_response if jobs_response is not None else evidence.get('jobs')
    job = _rescue_job(response, run_id, attempt) if response is not None else None
    if response is None:
        issues.append('JOBS_RESPONSE_UNAVAILABLE')
    if job is None:
        issues.append('RESCUE_JOB_FOR_ATTEMPT_UNAVAILABLE')
    if job and job.get('head_sha') is not None and job['head_sha'] != run.get('head_sha'):
        raise ValueError('job trigger head differs from run trigger head')
    job_times = job or {}
    result = evidence.get('result') or {}
    if result and attempt != 1:
        # The saved legacy result format has no artifact/run-attempt provenance.
        # A copied attempt field is not evidence. Keep only explicit matching job clocks.
        issues.append('RESULT_ATTEMPT_UNPROVEN')
        result = {}
    quotes = result.get('quotes', [])
    if not isinstance(quotes, list) or any(not isinstance(q, dict) or not q.get('forecast_id') for q in quotes):
        raise ValueError('invalid result quote list')
    if len({q['forecast_id'] for q in quotes}) != len(quotes):
        raise ValueError('duplicate result target')
    prior = _attach_receipt(assessment, run, result, quotes, job)
    clock = {
        'run_created_at_utc': _utc(run.get('created_at'), 'run_created_at_utc', issues),
        'job_created_at_utc': _utc(job_times.get('created_at'), 'job_created_at_utc', issues),
        'job_started_at_utc': _utc(job_times.get('started_at'), 'job_started_at_utc', issues),
        'job_completed_at_utc': _utc(job_times.get('completed_at'), 'job_completed_at_utc', issues),
        'dispatch_requested_at_utc': None,
        'capture_attempt_started_at_utc': None,
        'remote_verified_at_utc': _utc(result.get('publication', {}).get('verified_at_utc'),
                                       'remote_verified_at_utc', issues)}
    if job and job.get('conclusion') == 'skipped':
        clock['job_started_at_utc'] = None
        clock['job_completed_at_utc'] = None
        issues.append('RESCUE_JOB_SKIPPED_NO_EXECUTION_CLOCK')
    if caller_record is not None:
        _identity(caller_record, run_id, attempt, 'caller')
        clock['dispatch_requested_at_utc'] = _utc(caller_record.get('dispatch_requested_at_utc'),
                                                 'dispatch_requested_at_utc', issues)
    events = result.get('capture', {}).get('events', [])
    if events:
        starts = [_utc(e.get('started_at_utc'), 'capture_event_started_at_utc', issues) for e in events]
        if (all(type(e.get('attempt')) is int and e['attempt'] == i + 1 for i, e in enumerate(events))
                and all(t is not None for t in starts)
                and all(a <= b for a, b in zip(starts, starts[1:]))):
            clock['capture_attempt_started_at_utc'] = starts[0]
        else:
            issues.append('CAPTURE_ATTEMPT_SEQUENCE_UNAVAILABLE_OR_INVALID')
    segments = {}
    for label, first, second in (
        ('dispatch_to_run_created', 'dispatch_requested_at_utc', 'run_created_at_utc'),
        ('run_created_to_job_created', 'run_created_at_utc', 'job_created_at_utc'),
        ('job_created_to_started', 'job_created_at_utc', 'job_started_at_utc'),
        ('job_started_to_capture_attempt', 'job_started_at_utc', 'capture_attempt_started_at_utc'),
        ('capture_attempt_to_job_completed', 'capture_attempt_started_at_utc', 'job_completed_at_utc'),
        ('remote_verified_to_job_completed', 'remote_verified_at_utc', 'job_completed_at_utc'),
        ('job_started_to_completed', 'job_started_at_utc', 'job_completed_at_utc')):
        segments[label] = _seconds(clock[first], clock[second], label, issues)
    targets = []
    for q in quotes:
        fid = q['forecast_id']
        target = _utc(q.get('target_utc'), fid + ':target_utc', issues)
        quoted = _utc(q.get('target_quote_utc'), fid + ':target_quote_utc', issues)
        observed = _utc(q.get('observed_at_utc'), fid + ':observed_at_utc', issues)
        capture_cutoff = target + timedelta(minutes=20) if target else None
        quote_deadline = target + timedelta(minutes=25) if target else None
        verified = clock['remote_verified_at_utc']
        targets.append({
            'forecast_id': fid, 'target_utc': _iso(target), 'target_quote_utc': _iso(quoted),
            'observed_at_utc': _iso(observed), 'remote_verified_at_utc': _iso(verified),
            'capture_cutoff_utc': _iso(capture_cutoff), 'quote_deadline_utc': _iso(quote_deadline),
            'observed_by_capture_cutoff': target <= observed <= capture_cutoff
                if target is not None and observed is not None else None,
            'remote_verified_by_quote_deadline': target <= verified <= quote_deadline
                if target is not None and verified is not None else None,
            'segments_seconds': {
                'job_started_to_observed': _seconds(clock['job_started_at_utc'], observed,
                                                    fid + ':job_started_to_observed', issues),
                'observed_to_job_completed': _seconds(observed, clock['job_completed_at_utc'],
                                                      fid + ':observed_to_job_completed', issues),
                'capture_attempt_to_observed': _seconds(clock['capture_attempt_started_at_utc'], observed,
                                                        fid + ':capture_attempt_to_observed', issues),
                'observed_to_remote_verified': _seconds(observed, verified,
                                                        fid + ':observed_to_remote_verified', issues),
                'target_to_observed': _seconds(target, observed, fid + ':target_to_observed', issues),
                'target_to_remote_verified': _seconds(target, verified, fid + ':target_to_remote_verified', issues)}})
    concurrency = {'snapshot_available': False, 'historical_cause_proven': False}
    if concurrency_snapshot is not None:
        _identity(concurrency_snapshot, run_id, attempt, 'concurrency snapshot')
        stamp = _utc(concurrency_snapshot.get('captured_at_utc'), 'concurrency_captured_at_utc', issues)
        concurrency.update(snapshot_available=True, captured_at_utc=_iso(stamp),
                           response=concurrency_snapshot.get('response'),
                           interpretation='READ_TIME_SNAPSHOT_NOT_HISTORICAL_LOCK_PROOF')
    return {
        'schema_version': 1, 'scope': 'QUEUE_EVIDENCE_ONLY',
        'run': {k: run.get(k) for k in ('id', 'run_attempt', 'path', 'event', 'head_branch',
                                       'head_sha', 'status', 'conclusion')},
        'job': {k: job.get(k) for k in ('id', 'run_attempt', 'name', 'status', 'conclusion')} if job else None,
        'clocks_utc': {k: _iso(v) for k, v in clock.items()},
        'segments_seconds': segments, 'targets': targets, 'issues': issues,
        'queue_cause': 'UNKNOWN', 'concurrency': concurrency, 'prior_validation': prior,
        'clock_evidence_source': 'PRODUCER_REPORTED_SAVED_SINGLE_RUN_INPUT',
        'rescue_acceptance_evaluated': False, 'performance_acceptance_evaluated': False,
        'no_network_or_runtime_actions': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--assessment', type=Path)
    parser.add_argument('--jobs', type=Path)
    parser.add_argument('--caller-record', type=Path)
    parser.add_argument('--concurrency-snapshot', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    data, digests = {}, {}
    for role, path in (('evidence', args.evidence), ('assessment', args.assessment),
                       ('jobs_response', args.jobs), ('caller_record', args.caller_record),
                       ('concurrency_snapshot', args.concurrency_snapshot)):
        if path is not None:
            raw = path.read_bytes()
            data[role] = json.loads(raw.decode('utf-8-sig'))
            digests[role] = hashlib.sha256(raw).hexdigest()
    report = build_queue_evidence(**data)
    report['input_sha256'] = digests
    # Serialize completely before exclusive creation; never open any input for writing.
    payload = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(payload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

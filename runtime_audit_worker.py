"""Slow audits in an isolated, pinned local clone; only two reports return.

The worker cannot publish, issue, score, or touch the live ledger. Its temporary
audit caches are discarded. The live serial writer installs complete matching
reports, and frozen P2 checks still decide eligibility at capture time.
"""
import hashlib
from contextlib import contextmanager
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
import runtime_process
try:
    import resource as _resource
except Exception:
    _resource = None

SOURCE_BINDING_BLOB='f9b5136cc207558268cafeeb709436cff5e7a733'

def _source_binding_backend():
    path=Path(__file__).resolve().with_name('p0_source_binding.py')
    raw=path.read_bytes().replace(b'\r\n',b'\n')
    if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=SOURCE_BINDING_BLOB:
        raise ValueError('unreviewed source binding implementation')
    import p0_source_binding as module
    if Path(module.__file__).resolve()!=path:raise ValueError('source binding implementation location changed')
    return module

INPUTS = ('data/direction_v42/forward_registry.csv',
          'data/direction_v42/forward_scores.csv', 'data/quote_evidence_v2/bindings.csv',
          'data/paired_forward_v1/pairs.jsonl', 'data/hourly_research/ohlc.csv')
PRODUCER_INPUTS = ('data/stage5/forward_validation/remote_witnesses.csv',
                   'p0_locked_publication_context_v1')
OUTPUTS = ('data/p0_operations/latest.json', 'data/paired_forward_v1/audit_latest.json')
SCRIPTS = ('forward_validation_v2.py', 'p0_operational_audit.py', 'paired_cohort_audit.py')
CLI_HELPERS = ('runtime_audit_cli.py', 'runtime_publication_state.py', 'runtime_paired_history.py',
               'runtime_p0_inventory_transport.py', 'runtime_paired_git_stream.py',
               'runtime_witness_git_stream.py')
TRANSPORT_IMPLEMENTATIONS = {
    'runtime_publication_state_validation': 'runtime_publication_state_native_batch_v1',
    'runtime_paired_history_validation': 'runtime_paired_history_native_batch_v1',
}
TRANSPORT_REASONS = frozenset(('dependency_identity_changed', 'dependency_identity_unavailable',
    'git_read_error', 'history_fallback', 'log_framing', 'log_oid_sequence', 'log_encoding',
    'object_resolution_count', 'object_resolution_identity', 'object_resolution_framing',
    'blob_identity_or_order', 'blob_length_or_delimiter', 'blob_content_hash', 'unexpected_blob_tail',
    'unsupported_path_framing', 'commit_time_format', 'framing', 'oid_sequence',
    'nonempty_omission', 'patch_encoding', 'script_identity_changed', 'backend_identity_changed',
    'backend_location_changed', 'backend_import_unavailable'))
TRANSPORT_RELATIVES = frozenset(INPUTS + ('data/stage5/forward_validation/remote_witnesses.csv',
                                        'data/stage5/forward_validation/verified_run_metadata.csv'))
EXCLUDED_PREFIX = 'data/plus_evidence_v1_0/sources/'
# Reviewed dependency closure at efe0857e. A change falls back to the original
# full checkout; this is an optimization eligibility check, not a new freeze.
AUDITED_BLOBS = {
    'p0_source_binding.py': 'f9b5136cc207558268cafeeb709436cff5e7a733',
    'direction_baseline_v42.py': '4bd0417548ab8251ec9d92e168db46d6ec1068bb',
    'direction_forward_v42.py': '368abc641db6d5ea6496ac4db624554161034665',
    'forward_execution_evidence.py': '7c2fd41af2fc5e52afd7fcabe2ea909b9457b7bb',
    'forward_validation.py': 'f0831c7d38581543f84a85639c6d69a7305ff382',
    'forward_validation_v2.py': '3adbdcc43c5dbdb57ecd482b1d4138e720146ba1',
    'okx_hourly_research_history.py': '6a403d175243d8766650e2b53ede52660ea94386',
    'p0_operational_audit.py': 'db494c83ddfbb47662a864b1764ad42283f6a0f1',
    'p1_adaptive_v2.py': '2fb92e67a1915e552eb9a236f202fc424c9b1b50',
    'p1_model_competition.py': '20fb6c70b64e6d46c6ec58525301eec72e08705d',
    'paired_cohort_audit.py': 'a6bdf0c54ffcf648384c756d400e742ab57a1d61',
    'paired_forward_cohort.py': '02e6b3ba51f063ba17cddcb98ff6445a08235c97',
    'paired_selection_v1.py': 'd53e2588439b9e3fce9485b011edb6d2eaf95466',
    'publish_forward_evidence.py': 'd982f4f8bd278d7a1505073bc35dd71b80eeb095',
    'quote_capture_retry.py': '0cc8f737040c704ca123b36eb4ffa556bc316046',
    'quote_evidence_v2.py': '7d1e62e60f3aa78949ef1d5d81d74086d1907c91',
    'spot_plus_quote_watch.py': '132ac443c8421960b19c43e237b9e638609c5b23',
    'stage5_70pct_audit.py': '1095886e5e493676e15a2de5c2bf0bd1abbd7dc9',
    'stage5_forward_quality.py': '8c9717dd2fabb49ed3ba1c3f4c1df1281d13f51b',
    'stored_history_integrity.py': '0257e7cf48845452a1341c5b691bb28bebcd62bc',
    'data/stage5/pre_registration.json': '9d2671df7cec81a7ca7165bf35e3d25444de653d',
}


def failure_diagnostics(phase, error):
    """Summarize captured failures without emitting output, URLs, or reports.

    A blocked JSON message is only a reported classification, not proof of its
    underlying cause. Text hashes cover subprocess's UTF-8 captured text (which
    already has universal-newline normalization), not the original wire bytes.
    """
    if phase not in ('clone',)+SCRIPTS or not isinstance(
            error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
        return None
    details = {'schema_version': 1, 'phase': phase,
               'failure_kind': 'command_timeout' if isinstance(error, subprocess.TimeoutExpired) else 'nonzero_exit',
               'reported_category': 'unknown', 'reported_error_code': 'unknown',
               'stdout_parse': 'not_attempted'}
    if isinstance(error, subprocess.CalledProcessError):
        if type(error.returncode) is int and -(2**31) <= error.returncode < 2**31:
            details['returncode'] = error.returncode
    elif type(error.timeout) in (float, int) and math.isfinite(error.timeout) and error.timeout >= 0:
        details['timeout_seconds'] = error.timeout
    captured = {}
    for stream in ('stdout', 'stderr'):
        value = getattr(error, stream, None)
        if type(value) is str:
            body, representation = value.encode('utf-8'), 'captured_text_utf8'
        elif type(value) is bytes:
            body, representation = value, 'captured_bytes'
        else:
            details[stream+'_representation'] = 'absent' if value is None else 'unavailable'
            continue
        captured[stream] = body
        details.update({stream+'_representation': representation, stream+'_bytes': len(body),
                        stream+'_sha256': hashlib.sha256(body).hexdigest()})
    if not isinstance(error, subprocess.CalledProcessError) or phase not in SCRIPTS:
        return details
    body = captured.get('stdout')
    if body is None: return details
    if len(body) > 65536:
        details['stdout_parse'] = 'oversized'
        return details
    try: report = json.loads(body.decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError):
        details['stdout_parse'] = 'malformed'
        return details
    if not isinstance(report, dict) or report.get('status') not in ('blocked', 'BLOCKED') or type(report.get('error')) is not str:
        details['stdout_parse'] = 'unrecognized_json'
        return details
    details['stdout_parse'] = 'blocked_json'
    message = report['error']
    if len(message) > 4096: return details
    http = re.fullmatch(r'HTTP Error ([45][0-9]{2}): [^\r\n]*', message)
    if http:
        details.update(reported_category='http_error', reported_error_code='reported_http_error',
                       reported_http_status=int(http.group(1)))
    elif message in ('timed out', 'The read operation timed out', '<urlopen error timed out>'):
        details.update(reported_category='timeout', reported_error_code='reported_timeout')
    else:
        known = {
            'full git history required': 'full_git_history_required',
            'frozen prediction implementation modified': 'frozen_prediction_implementation_modified',
            'frozen model modified': 'frozen_model_modified',
            'invalid V2 protocol or incomplete implementation manifest': 'invalid_v2_protocol',
            'V2 protocol witness mismatch': 'v2_protocol_witness_mismatch',
            'invalid V2 protocol witness': 'invalid_v2_protocol_witness',
            'missing original protocol witness': 'missing_original_protocol_witness',
            'duplicate historical ID': 'duplicate_historical_id',
            'duplicate hourly source key': 'duplicate_hourly_source_key',
            'audit clock must be UTC': 'audit_clock_not_utc',
        }
        if message in known:
            details.update(reported_category='validation_error', reported_error_code=known[message])
        elif message == 'one-day operational query exceeds 1000 records; cannot classify missing runs':
            details.update(reported_category='operational_limit', reported_error_code='operational_run_query_limit')
    return details


def emit_progress(phase, event, **metadata):
    """Bounded metadata only, flushed to the inherited log; never raw reports."""
    try:
        if phase not in ('clone', 'checkout', 'result', 'worker', 'parent_close')+SCRIPTS:
            return
        if event not in ('begin', 'end', 'before_cleanup'): return
        row = {'kind': 'runtime_source_audit_progress', 'schema_version': 1,
               'at_utc': datetime.now(timezone.utc).isoformat(), 'phase': phase, 'event': event}
        allowed = {
            'status': ('ok', 'error'), 'result_status': ('ok', 'failed'),
            'mode': ('full_checkout', 'full_checkout_fallback', 'sparse_source_archive_excluded'),
            'fallback_reason': ('dependency_identity_changed', 'retained_symlink'),
            'reason': ('shutdown', 'inputs_changed', 'audit_timeout', 'result_consumed'),
            'worker_state': ('running', 'exited', 'absent'),
        }
        for key, values in allowed.items():
            if metadata.get(key) in values: row[key] = metadata[key]
        for key in ('input_head', 'result_sha256'):
            value = metadata.get(key)
            if isinstance(value, str) and re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', value): row[key] = value
        for key in ('worker_pid', 'worker_returncode', 'result_bytes'):
            value = metadata.get(key)
            if type(value) is int: row[key] = value
        for key in ('result_present', 'result_json_valid', 'termination_requested',
                    'kill_requested', 'leader_reaped'):
            if type(metadata.get(key)) is bool: row[key] = metadata[key]
        seconds = metadata.get('seconds')
        if type(seconds) in (float, int) and math.isfinite(seconds) and seconds >= 0:
            row['seconds'] = seconds
        values = metadata.get('input_fingerprints')
        if isinstance(values, dict) and set(values) == set(INPUTS + PRODUCER_INPUTS) and all(
                isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) for v in values.values()):
            row['input_fingerprints'] = values
        error = metadata.get('error')
        if error is not None:
            kinds = (ValueError, RuntimeError, TimeoutError, OSError, KeyError, TypeError,
                     AssertionError, subprocess.CalledProcessError, subprocess.TimeoutExpired)
            row['error_type'] = type(error).__name__ if type(error) in kinds else 'Exception'
            try: details = failure_diagnostics(phase, error)
            except Exception: details = None
            if details is not None: row['failure_diagnostics'] = details
        print(json.dumps(row, sort_keys=True, allow_nan=False), flush=True)
    except Exception:
        pass


def elapsed(start):
    try: return time.monotonic()-start
    except Exception: return None


def _diagnostic_object(pairs):
    row = {}
    for key, value in pairs:
        if key in row: raise ValueError('duplicate diagnostic member')
        row[key] = value
    return row


def _transport_fields(row, implementation, nested=False):
    if not isinstance(row, dict): raise ValueError('diagnostic object required')
    enums = {'implementation': (implementation,), 'mode': ('native_batch', 'legacy_full_fallback', 'not_started'),
             'status': ('ok', 'error', 'running'), 'validation_status': ('ok', 'error')}
    counts = ('attempted_commit_count', 'native_batch_calls', 'empty_fallback_show_calls')
    hashes = ('module_sha256', 'ordered_oids_sha256')
    allowed = set(enums) | set(counts) | set(hashes) | {'relative', 'fallback_reason'}
    if not nested: allowed |= {'kind', 'histories'}
    if set(row)-allowed: raise ValueError('unknown diagnostic member')
    if not nested and not {'implementation','mode','status','validation_status','module_sha256'} <= set(row):
        raise ValueError('incomplete native diagnostic')
    out = {}
    for key, choices in enums.items():
        if key in row:
            if row[key] not in choices: raise ValueError('unknown diagnostic enum')
            out[key] = row[key]
    for key in counts:
        if key in row:
            if type(row[key]) is not int or not 0 <= row[key] <= 2147483647:
                raise ValueError('invalid diagnostic count')
            out[key] = row[key]
    for key in hashes:
        if key in row:
            if not isinstance(row[key], str) or not re.fullmatch('[0-9a-f]{64}', row[key]):
                raise ValueError('invalid diagnostic hash')
            out[key] = row[key]
    if 'relative' in row:
        if row['relative'] not in TRANSPORT_RELATIVES: raise ValueError('unknown diagnostic path')
        out['relative'] = row['relative']
    if 'fallback_reason' in row:
        value = row['fallback_reason']
        out['fallback_reason'] = value if isinstance(value,str) and value in TRANSPORT_REASONS else 'UNRECOGNIZED_OMITTED'
    if 'histories' in row:
        if not isinstance(row['histories'],list) or len(row['histories'])>3: raise ValueError('diagnostic history limit')
        out['histories'] = [_transport_fields(child,implementation,nested=True) for child in row['histories']]
    return out


def _fresh_stream_fields(source,expected_sha256,implementation):
    counts={'requests','verified_reads','processes','fallback_calls'}
    flags={'installed','reaped','reader_joined','disabled'}
    if (set(source)!=counts|flags|{'kind','implementation','mode','status','module_sha256'}
            or source.get('implementation')!=implementation
            or source.get('mode')!='fresh_sequential_blobs' or source.get('status')!='ok'
            or not isinstance(expected_sha256,str) or not re.fullmatch('[0-9a-f]{64}',expected_sha256)
            or source.get('module_sha256')!=expected_sha256
            or any(type(source[key]) is not int or not 0<=source[key]<=10**9 for key in counts)
            or any(type(source[key]) is not bool for key in flags)
            or source['installed'] is not True or source['reaped'] is not True
            or source['reader_joined'] is not True or source['disabled'] is not False
            or source['processes']!=1 or source['requests']==0
            or source['verified_reads']!=source['requests'] or source['fallback_calls']!=0):
        raise ValueError('invalid authenticated paired stream receipt')
    return {key:value for key,value in source.items() if key!='kind'}


def _paired_stream_fields(source,expected_sha256):
    return _fresh_stream_fields(source,expected_sha256,'paired_fresh_git_blob_stream_v1')


def forward_transport_diagnostics(stderr, stage, expected_stream_sha256=None, expected_witness_sha256=None):
    """Forward only bounded fixed metadata; never raw subprocess stderr."""
    try:
        if stage not in SCRIPTS or not isinstance(stderr,str) or len(stderr)>65536:
            return
        if len(stderr.encode('utf-8'))>65536: return
        emitted = 0; output_bytes = 0
        for line in stderr.splitlines()[:128]:
            if emitted >= 32: break
            if len(line)>4096 or len(line.encode('utf-8'))>4096: continue
            try:
                source = json.loads(line,object_pairs_hook=_diagnostic_object)
                if not isinstance(source,dict): continue
                kind = source.get('kind')
                if kind in TRANSPORT_IMPLEMENTATIONS:
                    fields = _transport_fields(source,TRANSPORT_IMPLEMENTATIONS[kind])
                elif kind == 'runtime_canonical_json_transport':
                    counts = ('calls','hits','misses','direct_calls','fallback_calls','sha256_calls',
                              'encoded_calls','evictions','entries','cached_bytes')
                    if (stage != 'paired_cohort_audit.py' or set(source) !=
                            set(counts)|{'kind','implementation','mode','status','module_sha256'}
                            or source['implementation'] != 'paired_canonical_utf8_lru_v2_fit1h_direct'
                            or source['mode'] != 'canonical_bytes_lru'
                            or source['status'] not in ('ok','error')
                            or not isinstance(source['module_sha256'],str)
                            or not re.fullmatch('[0-9a-f]{64}',source['module_sha256'])
                            or any(type(source[k]) is not int or not 0<=source[k]<=2147483647 for k in counts)):
                        continue
                    fields = {k:v for k,v in source.items() if k != 'kind'}
                elif kind == 'runtime_p0_inventory_transport':
                    counts = ('native_calls','fallback_calls','real_gets','days')
                    if (stage != 'p0_operational_audit.py' or set(source) !=
                            set(counts)|{'kind','implementation','mode','status','installed','module_sha256','window'}
                            or source['implementation'] != 'p0_fresh_inventory_window4_v1'
                            or source['mode'] != 'fresh_bounded_inventory'
                            or source['status'] not in ('ok','error')
                            or type(source['installed']) is not bool
                            or type(source['window']) is not int or source['window'] != 4
                            or not isinstance(source['module_sha256'],str)
                            or not re.fullmatch('[0-9a-f]{64}',source['module_sha256'])
                            or any(type(source[k]) is not int or not 0<=source[k]<=2147483647 for k in counts)):
                        continue
                    fields = {k:v for k,v in source.items() if k != 'kind'}
                elif kind == 'runtime_paired_git_stream':
                    if stage!='paired_cohort_audit.py':continue
                    fields=_paired_stream_fields(source,expected_stream_sha256)
                elif kind == 'runtime_witness_git_stream':
                    if stage not in ('forward_validation_v2.py','p0_operational_audit.py'):continue
                    fields=_fresh_stream_fields(source,expected_witness_sha256,'witness_fresh_git_blob_stream_v1')
                elif kind == 'runtime_audit_cli_fallback':
                    if set(source)!={'kind','backend','reason'} or source['backend'] not in ('original_cli','runtime_publication_state','runtime_paired_history','runtime_p0_inventory_transport','runtime_paired_git_stream','runtime_witness_git_stream'):
                        continue
                    value = source['reason']
                    fields = {'backend':source['backend'],'mode':'legacy_full_fallback',
                        'fallback_reason':value if isinstance(value,str) and value in TRANSPORT_REASONS else 'UNRECOGNIZED_OMITTED'}
                else: continue
                row = dict(fields,kind='runtime_source_audit_transport',schema_version=1,
                    source_kind=kind,stage=stage,worker_pid=os.getpid(),
                    at_utc=datetime.now(timezone.utc).isoformat())
                output = json.dumps(row,sort_keys=True,allow_nan=False)
                if len(output.encode('utf-8'))>4096: continue
            except Exception:
                continue
            size = len(output.encode('utf-8'))+1
            if output_bytes+size>65536: break
            print(output,flush=True); emitted += 1; output_bytes += size
    except Exception:
        pass


def command(*args, cwd):
    completed = subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                               text=True, encoding='utf-8', timeout=240)
    try:
        if (len(args)==3 and Path(args[0]).resolve()==Path(sys.executable).resolve()
                and Path(args[1]).resolve()==(Path(cwd).resolve()/CLI_HELPERS[0]) and args[2] in SCRIPTS):
            expected=None
            if args[2]=='paired_cohort_audit.py':
                path=Path(cwd)/'runtime_paired_git_stream.py'
                if path.is_file() and not path.is_symlink():expected=hashlib.sha256(path.read_bytes()).hexdigest()
            witness=None
            if args[2] in ('forward_validation_v2.py','p0_operational_audit.py'):
                path=Path(cwd)/'runtime_witness_git_stream.py'
                if path.is_file() and not path.is_symlink():witness=hashlib.sha256(path.read_bytes()).hexdigest()
            forward_transport_diagnostics(completed.stderr,args[2],expected_stream_sha256=expected,
                                          expected_witness_sha256=witness)
    except Exception:
        pass
    return completed.stdout


_TELEMETRY_CPU_KEYS = ('parent_user_seconds', 'parent_system_seconds',
                       'child_user_seconds', 'child_system_seconds')
_TELEMETRY_CHILD_COUNTERS = ('ru_inblock', 'ru_majflt', 'ru_nvcsw', 'ru_nivcsw')


def _telemetry_number(value, maximum):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum


def _telemetry_thread_value(value):
    return value if type(value) is str and re.fullmatch(r'[1-9][0-9]{0,5}', value) else 'unknown'


def _read_command_telemetry():
    """Read existing local counters only; unsupported measurements stay absent.

    Linux RUSAGE_CHILDREN includes waited children and their waited descendants.
    It is cumulative accounting, not a claim of exclusive ownership of a CPU.
    """
    row = {}
    for field, name in (('openblas_num_threads', 'OPENBLAS_NUM_THREADS'),
                        ('omp_num_threads', 'OMP_NUM_THREADS')):
        try: row[field] = _telemetry_thread_value(os.environ.get(name))
        except Exception: row[field] = 'unknown'
    try:
        if _resource is not None:
            parent = _resource.getrusage(_resource.RUSAGE_SELF)
            children = _resource.getrusage(_resource.RUSAGE_CHILDREN)
            values = (parent.ru_utime, parent.ru_stime, children.ru_utime, children.ru_stime)
            if all(_telemetry_number(value, 10**9) for value in values):
                row.update(zip(_TELEMETRY_CPU_KEYS, values))
            for name in _TELEMETRY_CHILD_COUNTERS:
                try:
                    value = getattr(children, name)
                    if type(value) is int and 0 <= value <= 10**15:
                        row['child_' + name] = value
                except Exception:
                    pass
    except Exception:
        pass
    try:
        load = os.getloadavg()
        if type(load) is tuple and len(load) == 3 and all(_telemetry_number(v, 10**6) for v in load):
            row['load'] = load
    except Exception:
        pass
    try:
        count = os.cpu_count()
        if type(count) is int and 0 < count <= 65536:
            row['os_cpu_count'] = count
    except Exception:
        pass
    try:
        affinity = os.sched_getaffinity(0)
        if (type(affinity) is set and 0 < len(affinity) <= 65536
                and all(type(cpu) is int and 0 <= cpu < 2**31 for cpu in affinity)):
            row['sched_getaffinity_count'] = len(affinity)
    except Exception:
        pass
    return row


def _command_telemetry_enabled():
    """Keep the established progress-only log unless an owner opts in."""
    try:
        return os.environ.get('DOGE_AUDIT_COMMAND_TELEMETRY') == '1'
    except Exception:
        return False


def _safe_command_telemetry():
    if not _command_telemetry_enabled():
        return {}
    try:
        row = _read_command_telemetry()
        return row if type(row) is dict else {}
    except Exception:
        return {}


def _emit_command_telemetry(phase, before, **metadata):
    """Independent bounded diagnostic: never change a command result or error."""
    try:
        if not _command_telemetry_enabled():
            return
        if phase not in ('clone',) + SCRIPTS:
            return
        after = _safe_command_telemetry()
        row = {'kind': 'runtime_source_audit_telemetry', 'schema_version': 1,
               'phase': phase, 'worker_pid': os.getpid(),
               'at_utc': datetime.now(timezone.utc).isoformat(), 'cpu_status': 'unknown'}
        for field in ('openblas_num_threads', 'omp_num_threads'):
            row[field] = _telemetry_thread_value(before.get(field))
        head = metadata.get('input_head')
        if type(head) is str and re.fullmatch(r'[0-9a-f]{40}', head):
            row['input_head'] = head
        if all(_telemetry_number(s.get(key), 10**9) for s in (before, after) for key in _TELEMETRY_CPU_KEYS):
            deltas = {key + '_delta': after[key] - before[key] for key in _TELEMETRY_CPU_KEYS}
            if all(_telemetry_number(value, 10**9) for value in deltas.values()):
                row.update(deltas)
                row['cpu_status'] = 'ok'
        for name in _TELEMETRY_CHILD_COUNTERS:
            key = 'child_' + name
            if (all(type(s.get(key)) is int and 0 <= s[key] <= 10**15 for s in (before, after))
                    and after[key] >= before[key]):
                row[key + '_delta'] = after[key] - before[key]
        for suffix, snapshot in (('before', before), ('after', after)):
            for key in ('os_cpu_count', 'sched_getaffinity_count'):
                value = snapshot.get(key)
                if type(value) is int and 0 < value <= 65536:
                    row[key + '_' + suffix] = value
            load = snapshot.get('load')
            if type(load) is tuple and len(load) == 3 and all(_telemetry_number(v, 10**6) for v in load):
                row.update((f'load_{minutes}m_{suffix}', value) for minutes, value in zip((1, 5, 15), load))
        print(json.dumps(row, sort_keys=True, allow_nan=False), flush=True)
    except Exception:
        pass


def timed_command(phase, *args, cwd, **metadata):
    start = time.monotonic()
    telemetry_before = _safe_command_telemetry()
    emit_progress(phase, 'begin', **metadata)
    try:
        output = command(*args, cwd=cwd)
    except BaseException as exc:
        try: exc._audit_command_phase = phase
        except Exception: pass
        emit_progress(phase, 'end', status='error', seconds=elapsed(start), error=exc, **metadata)
        _emit_command_telemetry(phase, telemetry_before, **metadata)
        raise
    seconds = time.monotonic()-start
    emit_progress(phase, 'end', status='ok', seconds=seconds, **metadata)
    _emit_command_telemetry(phase, telemetry_before, **metadata)
    return output, seconds


def checkout_eligibility(snapshot, head):
    # Read pinned Git objects before materializing files. The full tree also
    # detects retained symlinks that could otherwise hide an excluded target.
    tree = subprocess.run(['git', 'ls-tree', '-r', '-z', '--full-tree', head],
                          cwd=snapshot, check=True, capture_output=True, timeout=240).stdout
    blobs = {}
    retained_symlink = False
    for entry in tree.split(b'\0'):
        if not entry: continue
        metadata, raw_path = entry.split(b'\t', 1)
        mode, kind, oid = metadata.decode('ascii').split()
        path = os.fsdecode(raw_path)
        if mode == '120000' and not path.startswith(EXCLUDED_PREFIX): retained_symlink = True
        if kind == 'blob' and mode in ('100644', '100755'): blobs[path] = oid
    if retained_symlink: return 'retained_symlink'
    if any(blobs.get(path) != oid for path, oid in AUDITED_BLOBS.items()):
        return 'dependency_identity_changed'
    return None


def prepare_checkout(source, checkout, head, *, exclude_source_archive=True):
    """Shared full object history; optionally omit one reviewed worktree tree."""
    if type(exclude_source_archive) is not bool: raise ValueError('explicit checkout mode required')
    source, checkout = Path(source), Path(checkout)
    _, clone_seconds = timed_command('clone', 'git', 'clone', '--shared', '--no-checkout',
                                     str(source), str(checkout), cwd=source, input_head=head)
    eligibility_seconds = 0.0
    fallback = None
    if exclude_source_archive:
        start = time.monotonic()
        fallback = checkout_eligibility(checkout, head)
        eligibility_seconds = time.monotonic()-start
    sparse = exclude_source_archive and fallback is None
    mode = ('sparse_source_archive_excluded' if sparse else
            'full_checkout_fallback' if fallback else 'full_checkout')
    metadata = {'input_head': head, 'mode': mode, 'fallback_reason': fallback}
    start = time.monotonic()
    emit_progress('checkout', 'begin', **metadata)
    try:
        command('git', 'config', '--local', 'core.sparseCheckout', str(sparse).lower(), cwd=checkout)
        command('git', 'config', '--local', 'core.sparseCheckoutCone', 'false', cwd=checkout)
        command('git', 'config', '--local', 'index.sparse', 'false', cwd=checkout)
        if sparse:
            path = checkout/'.git/info/sparse-checkout'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('/*\n!/data/plus_evidence_v1_0/sources/\n', encoding='utf-8')
        command('git', '-c', 'core.autocrlf=false', 'checkout', '--detach', head, cwd=checkout)
    except BaseException as exc:
        emit_progress('checkout', 'end', status='error', seconds=elapsed(start), error=exc, **metadata)
        raise
    checkout_seconds = time.monotonic()-start
    emit_progress('checkout', 'end', status='ok', seconds=checkout_seconds, **metadata)
    result = {'clone_seconds': clone_seconds, 'checkout_seconds': checkout_seconds,
              'eligibility_seconds': eligibility_seconds,
              'requested_exclude_source_archive': exclude_source_archive,
              'exclude_source_archive': sparse, 'mode': mode}
    if fallback: result['fallback_reason'] = fallback
    return result


def fingerprint(root):
    p0_source_binding = _source_binding_backend()
    result = {p: hashlib.sha256((Path(root)/p).read_bytes()).hexdigest() for p in INPUTS}
    path = Path(root)/PRODUCER_INPUTS[0]
    result[PRODUCER_INPUTS[0]] = hashlib.sha256(b'\1'+path.read_bytes() if path.exists() else b'\0').hexdigest()
    result[PRODUCER_INPUTS[1]] = p0_source_binding.binding_fingerprint(root)
    return result


def result_metadata(directory):
    metadata = {'result_present': False, 'result_json_valid': False}
    if directory is None: return metadata
    try: body = (Path(directory.name)/'result.json').read_bytes()
    except OSError: return metadata
    metadata.update(result_present=True, result_sha256=hashlib.sha256(body).hexdigest(), result_bytes=len(body))
    try: result = json.loads(body)
    except (ValueError, UnicodeError): return metadata
    metadata['result_json_valid'] = True
    if isinstance(result, dict):
        metadata.update(result_status=result.get('status'), input_head=result.get('input_head'),
                        input_fingerprints=result.get('inputs'))
    return metadata


def verify_cli_sources(snapshot, head):
    """Use only exact helper bytes from this isolated audit's selected Git head."""
    launcher = command('git', 'ls-tree', '--full-tree', '--name-only', head,
                       '--', CLI_HELPERS[0], cwd=snapshot).splitlines()
    if not launcher:
        return False
    if launcher != [CLI_HELPERS[0]]:
        raise ValueError('unexpected isolated audit launcher entry')
    for relative in CLI_HELPERS:
        expected = command('git', 'rev-parse', head+':'+relative, cwd=snapshot).strip()
        actual = command('git', 'hash-object', '--no-filters', '--', relative, cwd=snapshot).strip()
        if actual != expected:
            raise ValueError('isolated audit helper differs from pinned source')
    return True


@contextmanager
def paired_stream_source(source,snapshot):
    """Explicitly bind one worker-created shared clone to its selected source."""
    key='DOGE_AUDIT_STREAM_TRUSTED_OBJECT_DIRECTORY'
    previous=os.environ.pop(key,None)
    try:
        try:
            source=Path(source).resolve();snapshot=Path(snapshot).resolve()
            objects=source/'.git/objects';alternate=snapshot/'.git/objects/info/alternates'
            if (source!=snapshot and objects.is_dir() and not objects.is_symlink()
                    and not (source/'.git').is_symlink()
                    and not alternate.is_symlink() and alternate.is_file()):
                with alternate.open('rb') as handle:raw=handle.read(16385)
                if len(raw)<=16384:
                    lines=raw.decode('utf-8').splitlines()
                    if len(lines)==1:
                        target=Path(lines[0])
                        if not target.is_absolute():target=alternate.parent.parent/target
                        if target.resolve()==objects.resolve():os.environ[key]=str(objects.resolve())
        except (OSError,ValueError,TypeError):pass
        yield
    finally:
        os.environ.pop(key,None)
        if previous is not None:os.environ[key]=previous


def audit_snapshot(root, head, temporary_parent=None, *, exclude_source_archive=True):
    with tempfile.TemporaryDirectory(prefix='doge-source-audit-', dir=temporary_parent) as name:
        snapshot = Path(name)/'snapshot'
        checkout = prepare_checkout(root, snapshot, head, exclude_source_archive=exclude_source_archive)
        use_launcher = verify_cli_sources(snapshot, head)
        inputs = fingerprint(snapshot)
        timings = []
        with paired_stream_source(root,snapshot):
            for script in SCRIPTS:
                arguments = (str(snapshot/'runtime_audit_cli.py'), script) if use_launcher else (script,)
                _, seconds = timed_command(script, sys.executable, *arguments,
                                           cwd=snapshot, input_head=head)
                timings.append({'stage': script, 'seconds': seconds})
        outputs = {p: (snapshot/p).read_text(encoding='utf-8') for p in OUTPUTS}
        return {'status': 'ok', 'input_head': head, 'inputs': inputs,
                'outputs': outputs, 'timings': timings, 'checkout': checkout}


def accept(root, result):
    if result.get('status') != 'ok': raise ValueError('isolated audit failed: '+str(result.get('error'))[:500])
    if result['inputs'] != fingerprint(root): return {'status': 'discarded_input_changed'}
    if set(result['outputs']) != set(OUTPUTS): raise ValueError('unexpected isolated audit outputs')
    for p in OUTPUTS:
        if json.loads(result['outputs'][p]).get('status') != 'ok': raise ValueError('blocked isolated report')
    # Called by the only live writer, never by the background process. If a disk
    # error interrupts the pair installation, the caller fails closed before book.
    for p in OUTPUTS:
        path = Path(root)/p
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result['outputs'][p], encoding='utf-8')
    return {'status': 'ready', 'input_head': result['input_head'], 'timings': result['timings'],
            'basis': {'inputs': dict(result['inputs']), 'reports': {
                p: hashlib.sha256((Path(root)/p).read_bytes()).hexdigest() for p in OUTPUTS}}}


class Auditor:
    def __init__(self, root):
        self.root = Path(root)
        self.process = None
        self.directory = None
        self.inputs = None

    def poll(self):
        # An issuer/target watcher can change inputs while a read-only audit is
        # running. Never spend the next ticks waiting for an obsolete snapshot.
        if self.process is not None and self.inputs != fingerprint(self.root):
            self.close(reason='inputs_changed')
        if self.process is not None:
            if self.process.poll() is None:
                if time.monotonic()-self.started > 600:
                    self.close(reason='audit_timeout')
                    raise TimeoutError('isolated source audit exceeded 600 seconds')
                return {'status': 'pending'}
            try:
                if self.process.returncode: raise ValueError('isolated audit worker exited unsuccessfully')
                result = json.loads((Path(self.directory.name)/'result.json').read_text(encoding='utf-8'))
                accepted = accept(self.root, result)
            finally:
                self.close(reason='result_consumed')
            if accepted['status'] == 'ready': return accepted
        # Snapshot only a committed source version. No live .git/index, ledger,
        # or output directory is shared with the worker.
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.root).decode().strip()
        self.directory = tempfile.TemporaryDirectory(prefix='doge-audit-result-')
        self.started = time.monotonic()
        self.inputs = fingerprint(self.root)
        # main() can only close a newly started group once its handle is owned.
        with runtime_process.defer_cancellation():
            self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                str(self.root), head, str(Path(self.directory.name)/'result.json')],
                cwd=self.root,
                start_new_session=True)
        emit_progress('worker', 'begin', input_head=head, worker_pid=self.process.pid,
                      input_fingerprints=self.inputs)
        return {'status': 'pending', 'input_head': head}

    def close(self, reason='shutdown'):
        process = self.process
        if process is not None or self.directory is not None:
            running = process is not None and process.poll() is None
            metadata = {'reason': reason, 'worker_state': 'running' if running else 'exited' if process else 'absent',
                        'worker_pid': process.pid if process else None, 'termination_requested': False}
            metadata.update(result_metadata(self.directory))
            emit_progress('parent_close', 'begin', **metadata)
            try:
                if process is not None:
                    metadata.update(runtime_process.terminate_group(process))
                # Capture the final result again before cleanup, including a
                # completion racing with shutdown; never install it here.
                metadata.update(result_metadata(self.directory))
                metadata['worker_returncode'] = process.returncode if process else None
                emit_progress('parent_close', 'before_cleanup', **metadata)
                self.process = None
                if self.directory is not None: self.directory.cleanup()
                self.directory = None
            except BaseException as exc:
                cleanup = getattr(exc, 'process_cleanup', None)
                if isinstance(cleanup, dict): metadata.update(cleanup)
                emit_progress('parent_close', 'end', status='error', error=exc, **metadata)
                raise
            emit_progress('parent_close', 'end', status='ok', **metadata)


def main():
    try: result = audit_snapshot(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]).parent)
    except Exception as exc:
        result = {'status': 'failed', 'error': type(exc).__name__+': '+str(exc)[:500]}
        try: details = failure_diagnostics(getattr(exc, '_audit_command_phase', None), exc)
        except Exception: details = None
        if details is not None: result['failure_diagnostics'] = details
    emit_progress('result', 'begin', input_head=sys.argv[2])
    start = time.monotonic()
    try:
        body = json.dumps(result).encode('utf-8')
        Path(sys.argv[3]).write_bytes(body)
    except Exception as exc:
        emit_progress('result', 'end', status='error', error=exc, seconds=elapsed(start), input_head=sys.argv[2])
        return 1
    emit_progress('result', 'end', status='ok' if result['status'] == 'ok' else 'error',
                  result_status=result['status'], result_sha256=hashlib.sha256(body).hexdigest(),
                  result_bytes=len(body), input_head=sys.argv[2], input_fingerprints=result.get('inputs'),
                  seconds=elapsed(start))
    return 0


if __name__ == '__main__': raise SystemExit(main())

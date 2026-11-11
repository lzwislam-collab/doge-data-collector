"""Diagnostic only: whole original/native source-audit CLI equivalence.

The original pass records actual public GitHub GET replies and their latency.
The candidate replays exactly those replies, including the captured latency.
Only these diagnostic processes use a clock captured from real UTC at startup.
Production CLIs, source data, deadlines and qualification logic are unchanged.
"""
import argparse
import base64
from collections import Counter, defaultdict
from contextlib import contextmanager, nullcontext
import datetime as datetime_module
import hashlib
import io
import json
import math
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

SCRIPTS = ('forward_validation_v2.py', 'p0_operational_audit.py', 'paired_cohort_audit.py')
INPUTS = ('data/direction_v42/forward_registry.csv', 'data/direction_v42/forward_scores.csv',
          'data/quote_evidence_v2/bindings.csv', 'data/paired_forward_v1/pairs.jsonl',
          'data/hourly_research/ohlc.csv')
REPORTS = ('data/stage5/forward_validation/latest.json',
           'data/stage5/forward_validation_v2/latest.json',
           'data/p0_operations/latest.json', 'data/paired_forward_v1/audit_latest.json')
PREFIXES = ('https://api.github.com/repos/lzwislam-collab/doge-data-collector/',
            'https://raw.githubusercontent.com/lzwislam-collab/doge-data-collector/')
MAX_BODY = 32 * 1024 * 1024
MAX_FIXTURE = 256 * 1024 * 1024
_PROFILE_STANDARD_JSON_DUMPS = json.dumps


class ProfileError(ValueError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n',
                         encoding='utf-8')
    os.replace(temporary, path)


class Response(io.BytesIO):
    def __init__(self, body, entry):
        super().__init__(body)
        self.status = self.code = entry['status']
        self.headers = entry['headers']
        self.url = entry['response_url']

    def geturl(self): return self.url
    def getcode(self): return self.status
    def info(self): return self.headers
    def getheader(self, name, default=None): return self.headers.get(name, default)


def response_from_entry(entry):
    body = base64.b64decode(entry['body_base64'], validate=True)
    if len(body) > MAX_BODY or digest(body) != entry['sha256']:
        raise ProfileError('captured reply digest mismatch')
    if entry.get('http_error'):
        raise urllib.error.HTTPError(entry['url'], entry['status'], entry['reason'],
                                     entry['headers'], io.BytesIO(body))
    return Response(body, entry)


@contextmanager
def transport(mode, path):
    """No request credentials are recorded; unknown replay reads fail closed."""
    path = Path(path)
    original = urllib.request.urlopen
    entries = []
    if mode == 'replay':
        if path.stat().st_size > MAX_FIXTURE:
            raise ProfileError('oversize captured metadata fixture')
        entries = json.loads(path.read_text(encoding='utf-8'))['entries']
    elif mode != 'record':
        raise ProfileError('unknown metadata mode')
    by_url = defaultdict(list)
    for entry in entries:
        by_url[entry['url']].append(entry)
    consumed = Counter()
    lock = threading.Lock()
    recorded_bytes = 0

    def open_recorded(request, *args, **kwargs):
        nonlocal recorded_bytes
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        if not url.startswith(PREFIXES):
            raise ProfileError('only actual public repository GET metadata is allowed')
        if isinstance(request, urllib.request.Request) and request.get_method() != 'GET':
            raise ProfileError('diagnostic network writes are forbidden')
        if (args and args[0] is not None) or kwargs.get('data') is not None:
            raise ProfileError('diagnostic network writes are forbidden')
        if mode == 'replay':
            with lock:
                index = consumed[url]
                if index >= len(by_url[url]):
                    raise ProfileError('unrecorded metadata request: ' + url)
                consumed[url] += 1
                entry = by_url[url][index]
            elapsed = entry['elapsed_seconds']
            if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or not 0 <= elapsed <= 240:
                raise ProfileError('invalid captured network duration')
            time.sleep(elapsed)
            if entry.get('url_error'):
                raise urllib.error.URLError(entry['reason'])
            return response_from_entry(entry)
        entry = {'url': url}
        with lock:
            if len(entries) >= 2048:
                raise ProfileError('captured metadata request limit exceeded')
            entries.append(entry)
        start = time.perf_counter()
        try:
            try:
                response = original(request, *args, **kwargs)
            except urllib.error.HTTPError as error:
                response = error
                entry.update(http_error=True, reason=str(error.reason))
            with response:
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise ProfileError('actual metadata reply exceeds diagnostic limit')
                with lock:
                    recorded_bytes += len(body)
                    if recorded_bytes > MAX_FIXTURE // 2:
                        raise ProfileError('total captured metadata limit exceeded')
                entry.update(status=getattr(response, 'status', getattr(response, 'code', 200)),
                    response_url=response.geturl(),
                    headers={k: v for k, v in response.headers.items()
                             if k.lower() in ('content-type', 'content-length', 'date', 'etag',
                                 'x-ratelimit-limit', 'x-ratelimit-remaining', 'x-ratelimit-reset',
                                 'x-ratelimit-resource', 'x-ratelimit-used', 'retry-after')},
                    body_base64=base64.b64encode(body).decode('ascii'), sha256=digest(body))
        except urllib.error.URLError as error:
            entry.update(url_error=True, reason=str(error.reason))
            raise
        finally:
            entry['elapsed_seconds'] = time.perf_counter() - start
        return response_from_entry(entry)

    urllib.request.urlopen = open_recorded
    try:
        yield entries
        if mode == 'replay' and sum(consumed.values()) != len(entries):
            raise ProfileError('unconsumed captured metadata replies')
    finally:
        urllib.request.urlopen = original
        if mode == 'record':
            write_json(path, {'entries': entries, 'request_credentials_recorded': False})


@contextmanager
def frozen_clock(at):
    original = datetime_module.datetime
    if at.tzinfo is None or at.utcoffset() != datetime_module.timedelta(0):
        raise ProfileError('diagnostic clock must be actual captured UTC')

    class FrozenDatetime(original):
        __slots__ = ()
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz is not None else at.replace(tzinfo=None)
        @classmethod
        def utcnow(cls): return at.replace(tzinfo=None)
        @classmethod
        def today(cls): return cls.now()

    datetime_module.datetime = FrozenDatetime
    try:
        yield
    finally:
        datetime_module.datetime = original


def paired_profile_enabled(optimized, script):
    """Formal timings are uninstrumented unless diagnostics are explicitly requested."""
    return (optimized and script == 'paired_cohort_audit.py'
            and os.environ.get('DOGE_AUDIT_PAIRED_CPU_PROFILE') == '1')


def paired_component_profile_enabled(optimized, script):
    """Detailed component wrappers require an explicit diagnostic opt-in."""
    return (optimized and script == 'paired_cohort_audit.py'
            and os.environ.get('DOGE_AUDIT_PAIRED_COMPONENT_PROFILE') == '1')


@contextmanager
def paired_component_timing(launcher, enabled=True, clock=None):
    """Diagnostic counters only; every original call and exception is retained."""
    names = ('digest', 'marshal', 'marshal_loads', 'eligibility', 'json_encode', 'sha256', 'cohort_utc')
    stats = {'kind': 'paired_component_timing', 'enabled': bool(enabled),
             'status': 'disabled' if not enabled else 'unavailable',
             'instrumented_standard_encoder': False,
             'business_completed': False, 'timer_errors': 0,
             'components': {name: {'calls': 0, 'seconds': 0.0} for name in names}}
    if not enabled:
        yield stats
        stats['business_completed'] = True
        return
    try:
        targets = [(launcher._CanonicalBytes, 'digest', 'digest'),
                   (launcher.marshal, 'dumps', 'marshal'),
                   (launcher.marshal, 'loads', 'marshal_loads'),
                   (launcher, '_json_closed', 'eligibility'),
                   (launcher.json, 'dumps', 'json_encode'),
                   (launcher.hashlib, 'sha256', 'sha256')]
        originals = [(owner, name, kind, getattr(owner, name))
                     for owner, name, kind in targets]
        hooks = launcher._hooks
        if not all(callable(row[3]) for row in originals) or not callable(hooks):
            raise AttributeError('diagnostic target unavailable')
    except (AttributeError, TypeError):
        yield stats
        stats['business_completed'] = True
        return
    clock = time.perf_counter if clock is None else clock
    depth = 0

    def read_clock():
        try:
            value = clock()
            if type(value) not in (float, int) or not math.isfinite(value):
                raise ValueError('invalid diagnostic timer')
            return value
        except Exception:
            stats['timer_errors'] += 1
            return None

    def timed(original, kind, *args, **kwargs):
        stats['components'][kind]['calls'] += 1
        start = read_clock()
        try:
            return original(*args, **kwargs)
        finally:
            end = read_clock()
            if start is not None and end is not None and end >= start:
                stats['components'][kind]['seconds'] += end - start

    def wrap(original, kind):
        def measured(*args, **kwargs):
            nonlocal depth
            if kind == 'digest':
                depth += 1
                try:
                    return timed(original, kind, *args, **kwargs)
                finally:
                    depth -= 1
            if depth:
                return timed(original, kind, *args, **kwargs)
            return original(*args, **kwargs)
        return measured

    @contextmanager
    def measured_hooks(root, script):
        with hooks(root, script):
            cohort = sys.modules.get('paired_forward_cohort')
            original_utc = getattr(cohort, 'utc', None)
            if script != 'paired_cohort_audit.py' or not callable(original_utc):
                yield
                return
            def measured_utc(*args, **kwargs):
                return timed(original_utc, 'cohort_utc', *args, **kwargs)
            cohort.utc = measured_utc
            try:
                yield
            finally:
                cohort.utc = original_utc

    installed = []
    try:
        for owner, name, kind, original in originals:
            measured = wrap(original, kind)
            # This diagnostic wrapper still calls the same captured standard
            # encoder exactly once. Never bless an already replaced encoder.
            if (kind == 'json_encode' and original is _PROFILE_STANDARD_JSON_DUMPS
                    and getattr(launcher, '_STANDARD_JSON_DUMPS', None) is original):
                launcher._STANDARD_JSON_DUMPS = measured
                installed.append((launcher, '_STANDARD_JSON_DUMPS', original))
                stats['instrumented_standard_encoder'] = True
            setattr(owner, name, measured)
            installed.append((owner, name, original))
        launcher._hooks = measured_hooks
        stats['status'] = 'ok'
        yield stats
        stats['business_completed'] = True
    finally:
        launcher._hooks = hooks
        for owner, name, original in reversed(installed):
            setattr(owner, name, original)


def write_component_metrics(path, stats):
    """Best effort diagnostic output cannot replace an audit result or error."""
    try:
        Path(path).write_text(json.dumps(stats, sort_keys=True, indent=2,
                                        allow_nan=False) + '\n', encoding='utf-8')
        return True
    except Exception:
        return False


@contextmanager
def paired_cpu_profile(enabled, fixture):
    """Diagnostic only; profile overhead stays included in the measured trial."""
    if not enabled:
        yield
        return
    import cProfile
    destination = Path(fixture).resolve().parent.parent / 'native-paired.cprofile'
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        yield
    finally:
        active_error = sys.exc_info()[0] is not None
        profiler.disable()
        try:
            profiler.dump_stats(str(destination))
        except Exception:
            if not active_error:
                raise


def child(root, script, mode, fixture, clock, optimized):
    root = Path(root).resolve()
    if script not in SCRIPTS:
        raise ProfileError('unknown original audit CLI')
    sys.path.insert(0, str(root))
    # Load scientific C extensions before replacing the Python datetime binding.
    # This common diagnostic startup cost is included in both whole-CLI timings.
    import numpy  # noqa: F401; only the C extension required by these real CLIs
    at = datetime_module.datetime.fromisoformat(clock)
    with transport(mode, fixture), frozen_clock(at), paired_cpu_profile(
            paired_profile_enabled(optimized, script), fixture):
        previous = sys.argv
        sys.argv = [script]
        try:
            if optimized:
                import runtime_audit_cli as launcher
                stats = None
                try:
                    with paired_component_timing(
                            launcher, enabled=paired_component_profile_enabled(optimized, script)) as stats:
                        result = launcher.run_cli(root, script)
                finally:
                    if script == 'paired_cohort_audit.py' and stats is not None:
                        write_component_metrics(Path(fixture).resolve().parent.parent /
                                                'native-paired-components.json', stats)
                return 0 if result is None else result
            try:
                runpy.run_path(str(root / script), run_name='__main__')
            except SystemExit as error:
                if error.code not in (0, None):
                    raise
            return 0
        finally:
            sys.argv = previous


def git(root, *args, timeout=30):
    return subprocess.run(['git', *args], cwd=root, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout,
                          env=dict(os.environ, GIT_OPTIONAL_LOCKS='0')).stdout


def source_control_state(source, helper):
    state = helper.source_state(source, Path(source) / 'runtime_audit_worker.py')
    state['all_refs_sha256'] = digest(git(source, 'for-each-ref', '--format=%(refname)%00%(objectname)'))
    for name in ('index', 'config', 'config.worktree'):
        path = Path(os.fsdecode(git(source, 'rev-parse', '--git-path', name).strip()))
        if not path.is_absolute(): path = Path(source) / path
        state[name + '_sha256'] = digest(path.read_bytes()) if path.exists() else None
    return state


def _diagnostic_members(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ProfileError('duplicate native diagnostic member')
        result[key]=value
    return result


def validate_native_diagnostics(stderr, script, expected_launcher_sha256=None, expected_p0_sha256=None,
                                expected_stream_sha256=None, expected_witness_sha256=None):
    if len(stderr.encode('utf-8')) > 1024 * 1024:
        raise ProfileError('oversize native diagnostics')
    required = {'runtime_publication_state_validation'}
    if script == 'paired_cohort_audit.py':
        required.add('runtime_paired_history_validation')
    rows, canonical, inventory, streams, witnesses = [], [], [], [], []
    counts = {'calls', 'hits', 'misses', 'direct_calls', 'fallback_calls', 'sha256_calls',
              'encoded_calls', 'evictions', 'entries', 'cached_bytes'}
    for line in stderr.splitlines():
        if not line.lstrip().startswith('{'): continue
        try: row = json.loads(line,object_pairs_hook=_diagnostic_members)
        except ValueError: continue
        if not isinstance(row, dict): continue
        if row.get('kind') == 'runtime_audit_cli_fallback':
            raise ProfileError('legacy CLI/backend fallback cannot establish native performance')
        if row.get('kind') in ('runtime_paired_git_stream','runtime_witness_git_stream'):
            witness=row['kind']=='runtime_witness_git_stream'
            expected_sha256=expected_witness_sha256 if witness else expected_stream_sha256
            implementation='witness_fresh_git_blob_stream_v1' if witness else 'paired_fresh_git_blob_stream_v1'
            valid_stage=script in ('forward_validation_v2.py','p0_operational_audit.py') if witness else script=='paired_cohort_audit.py'
            fields={'requests','verified_reads','processes','fallback_calls'}
            flags={'installed','reaped','reader_joined','disabled'}
            if (not valid_stage or set(row)!=fields|flags|
                    {'kind','implementation','mode','status','module_sha256'}
                    or row.get('implementation')!=implementation
                    or row.get('mode')!='fresh_sequential_blobs' or row.get('status')!='ok'
                    or not isinstance(expected_sha256,str)
                    or len(expected_sha256)!=64
                    or any(c not in '0123456789abcdef' for c in expected_sha256)
                    or row.get('module_sha256')!=expected_sha256
                    or any(type(row[key]) is not int or not 0<=row[key]<=10**9 for key in fields)
                    or any(type(row[key]) is not bool for key in flags)
                    or row['installed'] is not True or row['reaped'] is not True
                    or row['reader_joined'] is not True or row['disabled'] is not False
                    or row['processes']!=1 or row['requests']==0 or row['fallback_calls']!=0
                    or row['verified_reads']!=row['requests']):
                raise ProfileError('invalid actual paired fresh Git stream evidence')
            (witnesses if witness else streams).append(row)
            continue
        if row.get('kind') == 'runtime_p0_inventory_transport':
            fields = {'native_calls','fallback_calls','real_gets','days'}
            if (script != 'p0_operational_audit.py' or set(row) != fields |
                    {'kind','implementation','mode','status','installed','module_sha256','window'}
                    or row.get('implementation') != 'p0_fresh_inventory_window4_v1'
                    or row.get('mode') != 'fresh_bounded_inventory' or row.get('status') != 'ok'
                    or row.get('installed') is not True or type(row.get('window')) is not int or row['window'] != 4
                    or row.get('module_sha256') != expected_p0_sha256
                    or any(type(row[k]) is not int or not 0 <= row[k] <= 10**9 for k in fields)
                    or row['native_calls'] == 0 or row['fallback_calls'] != 0
                    or row['days'] == 0 or row['real_gets'] < row['days'] + row['native_calls']):
                raise ProfileError('invalid actual P0 fresh inventory transport evidence')
            inventory.append(row)
            continue
        if row.get('kind') == 'runtime_canonical_json_transport':
            if (script != 'paired_cohort_audit.py'
                    or set(row) != counts | {'kind', 'implementation', 'mode', 'status', 'module_sha256'}
                    or row.get('implementation') != 'paired_canonical_utf8_lru_v2_fit1h_direct'
                    or row.get('mode') != 'canonical_bytes_lru' or row.get('status') != 'ok'):
                raise ProfileError('invalid canonical transport evidence')
            identity = row['module_sha256']
            if (not isinstance(identity, str) or len(identity) != 64
                    or any(c not in '0123456789abcdef' for c in identity)
                    or (expected_launcher_sha256 is not None and identity != expected_launcher_sha256)):
                raise ProfileError('canonical transport implementation identity differs')
            if (any(type(row[k]) is not int or not 0 <= row[k] <= 10**9 for k in counts)
                    or row['calls'] == 0 or row['hits'] == 0
                    or row['sha256_calls'] != row['calls']
                    or row['fallback_calls'] != 0
                    or row['hits'] + row['misses'] + row['direct_calls'] != row['calls']
                    or row['encoded_calls'] != row['misses'] or row['misses'] == 0
                    or row['entries'] > row['misses'] or row['evictions'] > row['misses']
                    or row['entries'] + row['evictions'] > row['misses']
                    or (row['entries'] == 0) != (row['cached_bytes'] == 0)
                    or row['entries'] > 128 or row['cached_bytes'] > 64 * 1024 * 1024):
                raise ProfileError('canonical transport reuse or fresh SHA evidence missing')
            canonical.append(row)
            continue
        if row.get('kind') not in required: continue
        if (row.get('mode') != 'native_batch' or row.get('status') != 'ok'
                or row.get('validation_status') != 'ok'):
            raise ProfileError('native validation/fallback did not complete successfully')
        rows.append(row)
    seen = {r['kind'] for r in rows if r.get('native_batch_calls', 0) > 0}
    if seen != required:
        raise ProfileError('required native validation evidence missing')
    if script == 'paired_cohort_audit.py' and len(canonical) != 1:
        raise ProfileError('exactly one canonical paired transport receipt required')
    if script == 'p0_operational_audit.py' and expected_p0_sha256 is not None and len(inventory) != 1:
        raise ProfileError('exactly one actual P0 inventory receipt required')
    if script == 'paired_cohort_audit.py' and expected_stream_sha256 is not None and len(streams)!=1:
        raise ProfileError('exactly one authenticated actual paired stream receipt required')
    if script in ('forward_validation_v2.py','p0_operational_audit.py') and expected_witness_sha256 is not None and len(witnesses)!=1:
        raise ProfileError('exactly one authenticated actual witness stream receipt required')
    return rows + canonical + inventory + streams + witnesses


def fingerprint(root):
    return {path: digest((Path(root) / path).read_bytes()) for path in INPUTS}


class NumberLexeme(str):
    pass


def comparison_digest(body, relative):
    if not relative.endswith('.json'):
        return digest(body)
    def object_pairs(pairs):
        out = {}
        for key, value in pairs:
            if key in out: raise ProfileError('duplicate output JSON member')
            out[key] = value
        return out
    def invalid_constant(value):
        raise ProfileError('nonfinite output JSON number: ' + value)
    value = json.loads(body, object_pairs_hook=object_pairs, parse_int=NumberLexeme,
                       parse_float=NumberLexeme, parse_constant=invalid_constant)
    def canonical(item):
        if isinstance(item, NumberLexeme): return ['number', str(item)]
        if isinstance(item, dict): return ['object', [[k, canonical(item[k])] for k in sorted(item)]]
        if isinstance(item, list): return ['array', [canonical(v) for v in item]]
        if isinstance(item, str): return ['string', item]
        if item is None: return ['null']
        return ['boolean', item]
    return digest(json.dumps(canonical(value), ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def equivalent_manifests(left, right):
    return ({p: row['comparison_sha256'] for p, row in left.items()} ==
            {p: row['comparison_sha256'] for p, row in right.items()})


def code_fingerprint(root):
    paths = git(root, 'ls-files', '-z').split(b'\0')
    result = {}
    for item in paths:
        if not item: continue
        relative = os.fsdecode(item)
        if relative.endswith('.py') or relative.startswith(('config/', 'system/')):
            path = Path(root) / relative
            if path.is_symlink() or not path.is_file():
                raise ProfileError('protected implementation is missing or symlinked')
            result[relative] = digest(path.read_bytes())
    return result


def output_manifest(root, destination):
    root, destination = Path(root), Path(destination)
    changed = git(root, 'diff', '--name-only', '-z', 'HEAD').split(b'\0')
    extra = git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0')
    manifest = {}
    for raw in sorted(set(changed + extra)):
        if not raw: continue
        relative = os.fsdecode(raw)
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ProfileError('unsafe output path')
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ProfileError('audit deleted or replaced a tracked file')
        if path.stat().st_size > MAX_BODY:
            raise ProfileError('oversize individual diagnostic output')
        body = path.read_bytes()
        manifest[relative] = {'bytes': len(body), 'sha256': digest(body),
                              'comparison_sha256': comparison_digest(body, relative)}
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    reports = {path: json.loads((root / path).read_text(encoding='utf-8')) for path in REPORTS}
    if any(report.get('status') != 'ok' for report in reports.values()):
        raise ProfileError('an original complete audit report is not ok')
    return manifest


def worker(source, head, output, timeout_seconds, max_native_seconds):
    source, output = Path(source).resolve(), Path(output).resolve()
    sys.path.insert(0, str(source))
    import runtime_audit_worker as audit
    import runtime_audit_checkout_profile as checkout_profile
    checkout_profile.require_full_history(source)
    before = source_control_state(source, checkout_profile)
    start = time.perf_counter()
    deadline = start + timeout_seconds
    clock = datetime_module.datetime.now(datetime_module.timezone.utc).isoformat()
    report = {'status': 'running', 'source_head': head, 'actual_captured_clock_utc': clock,
              'clock_scope': 'diagnostic children only; no production override',
              'metadata': 'actual original GET replies; candidate replays equal measured latency',
              'production_dispatch': False, 'production_changes': False, 'trials': [],
              'diagnostic_profiling': {'scope': 'native paired CLI only',
                  'enabled': paired_profile_enabled(True, 'paired_cohort_audit.py'),
                  'file': 'native-paired.cprofile' if paired_profile_enabled(True, 'paired_cohort_audit.py') else None,
                  'included_in_native_elapsed': paired_profile_enabled(True, 'paired_cohort_audit.py'),
                  'production_override': False},
              'diagnostic_components': {
                  'components_enabled': paired_component_profile_enabled(True, 'paired_cohort_audit.py'),
                  'statistics': ('MEASURED_DIAGNOSTIC' if paired_component_profile_enabled(True, 'paired_cohort_audit.py')
                                 else 'NOT_MEASURED_DISABLED'),
                  'file': 'native-paired-components.json',
                  'included_in_native_elapsed': paired_component_profile_enabled(True, 'paired_cohort_audit.py'),
                  'production_override': False},
              'comparison': 'JSON object keys canonicalized; numeric lexemes, every field and list order retained; other files exact bytes',
              'diagnostic_environment': {'PYTHONHASHSEED': '0', 'PYTHONUTF8': '1',
                  'OPENBLAS_NUM_THREADS': os.environ.get('OPENBLAS_NUM_THREADS'),
                  'OMP_NUM_THREADS': os.environ.get('OMP_NUM_THREADS')}}
    report['source_control_before'] = before
    write_json(output / 'summary.json', report)
    try:
        with tempfile.TemporaryDirectory(prefix='audit-pipeline-profile-') as name:
            workspace = Path(name).resolve()
            if workspace.is_relative_to(source) or source.is_relative_to(workspace):
                raise ProfileError('temporary checkouts overlap source')
            inputs = None
            for optimized, label in ((False, 'original'), (True, 'native')):
                whole_start = time.perf_counter()
                snapshot = workspace / label
                checkout = audit.prepare_checkout(source, snapshot, head)
                if git(snapshot, 'rev-parse', 'HEAD').decode().strip() != head:
                    raise ProfileError('isolated audit did not check out the selected fixed head')
                current_inputs = fingerprint(snapshot)
                original_code = code_fingerprint(snapshot)
                if optimized and 'runtime_paired_git_stream.py' in getattr(audit,'CLI_HELPERS',()):
                    if ('runtime_paired_git_stream.py' not in original_code or
                            audit.verify_cli_sources(snapshot,head) is not True):
                        raise ProfileError('selected native helper closure is not authenticated')
                if inputs is None: inputs = current_inputs
                if current_inputs != inputs:
                    raise ProfileError('original/native input fingerprints differ')
                trial = {'kind': label, 'checkout': checkout, 'inputs': current_inputs, 'phases': [],
                    'snapshot_head': head, 'implementation_file_count': len(original_code),
                    'implementation_fingerprints_sha256': digest(
                        json.dumps(original_code, sort_keys=True).encode('utf-8'))}
                report['trials'].append(trial)
                trial_start = time.perf_counter()
                for script in SCRIPTS:
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0: raise TimeoutError('whole diagnostic budget exhausted')
                    fixture = output / 'captured-get' / (script + '.json')
                    command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--child',
                               '--source-root', str(snapshot), '--script', script,
                               '--fixture', str(fixture), '--clock', clock]
                    if optimized: command.append('--optimized')
                    phase_start = time.perf_counter()
                    with audit.paired_stream_source(source,snapshot) if hasattr(audit,'paired_stream_source') else nullcontext():
                        result = subprocess.run(command, cwd=snapshot, capture_output=True, text=True,
                            encoding='utf-8', timeout=min(240, remaining),
                            env=dict(os.environ, PYTHONHASHSEED='0', PYTHONUTF8='1',
                                     PYTHONDONTWRITEBYTECODE='1'))
                    elapsed = time.perf_counter() - phase_start
                    (output / (label + '-' + script + '.stdout')).write_text(result.stdout, encoding='utf-8')
                    (output / (label + '-' + script + '.stderr')).write_text(result.stderr, encoding='utf-8')
                    trial['phases'].append({'script': script, 'seconds': elapsed, 'exit_code': result.returncode})
                    if optimized:
                        trial['phases'][-1]['native_diagnostics'] = validate_native_diagnostics(
                            result.stderr, script, original_code['runtime_audit_cli.py'],
                            expected_p0_sha256=original_code['runtime_p0_inventory_transport.py'],
                            expected_stream_sha256=original_code.get('runtime_paired_git_stream.py'),
                            expected_witness_sha256=original_code.get('runtime_witness_git_stream.py'))
                    trial['phases'][-1]['stdout_sha256'] = digest(result.stdout.encode('utf-8'))
                    write_json(output / 'summary.json', report)
                    if result.returncode != 0:
                        raise ProfileError(label + ' original CLI failed: ' + script)
                trial['pipeline_seconds'] = time.perf_counter() - trial_start
                if fingerprint(snapshot) != inputs:
                    raise ProfileError('audit mutated one of five protected inputs')
                if code_fingerprint(snapshot) != original_code:
                    raise ProfileError('audit mutated implementation/config/system bytes')
                if git(snapshot, 'rev-parse', 'HEAD').decode().strip() != head:
                    raise ProfileError('audit changed the selected fixed head')
                trial['implementation_and_config_bytes_unchanged'] = True
                trial['outputs'] = output_manifest(snapshot, output / label / 'outputs')
                if snapshot.is_symlink() or snapshot.resolve() == workspace or not snapshot.resolve().is_relative_to(workspace):
                    raise ProfileError('unsafe owned snapshot cleanup target')
                cleanup_start = time.perf_counter()
                shutil.rmtree(snapshot)
                trial['snapshot_cleanup_seconds'] = time.perf_counter() - cleanup_start
                trial['whole_source_seconds'] = time.perf_counter() - whole_start
            if not equivalent_manifests(report['trials'][0]['outputs'], report['trials'][1]['outputs']):
                raise ProfileError('whole original/native output manifests differ')
            if ([p['stdout_sha256'] for p in report['trials'][0]['phases']] !=
                    [p['stdout_sha256'] for p in report['trials'][1]['phases']]):
                raise ProfileError('original CLI stdout changed')
            after = source_control_state(source, checkout_profile)
            report['source_control_after'] = after
            if after != before:
                raise ProfileError('source HEAD/tree/tracked state changed')
            report.update(status='PASS_WHOLE_CLI_EQUIVALENCE', five_inputs_unchanged=True,
                source_state_unchanged=True, output_semantics_exactly_equal=True,
                output_bytes_exactly_equal=report['trials'][0]['outputs'] == report['trials'][1]['outputs'],
                original_pipeline_seconds=report['trials'][0]['pipeline_seconds'],
                native_pipeline_seconds=report['trials'][1]['pipeline_seconds'],
                original_whole_source_seconds=report['trials'][0]['whole_source_seconds'],
                native_whole_source_seconds=report['trials'][1]['whole_source_seconds'],
                measured_speedup=report['trials'][0]['pipeline_seconds'] / report['trials'][1]['pipeline_seconds'],
                real_natural_qualified_stop='NOT_VERIFIED_BY_DIAGNOSTIC')
            native = report['native_whole_source_seconds']
            reference = {'remaining_seconds_in_failed_archive_after_worker_restart': 257.442452,
                'book_seconds_from_successful_run42643383889': 49.814651,
                'publication_seconds_from_successful_run42643383889': 57.336526,
                'existing_poll_interval_seconds': 60,
                'projection_scope': 'Historical measured reference, not a guarantee for future natural work'}
            reference['whole_plus_measured_book_publication_seconds'] = native + 49.814651 + 57.336526
            reference['including_worst_case_existing_poll_seconds'] = native + 49.814651 + 57.336526 + 60
            report['reference_budget_comparison'] = reference
            if native > max_native_seconds:
                raise ProfileError('whole native source pass including checkout exceeds diagnostic performance threshold')
            report['max_native_whole_source_seconds'] = max_native_seconds
    except BaseException as error:
        report.update(status='FAIL', error_type=type(error).__name__, error=str(error)[:1000])
        raise
    finally:
        report['total_seconds'] = time.perf_counter() - start
        write_json(output / 'summary.json', report)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--ref')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout-seconds', type=float, default=600)
    parser.add_argument('--max-native-seconds', type=float, default=140,
                        help='maximum whole native source pass including checkout, diagnostic only')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--script', choices=SCRIPTS, help=argparse.SUPPRESS)
    parser.add_argument('--fixture', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--clock', help=argparse.SUPPRESS)
    parser.add_argument('--optimized', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        return child(args.source_root, args.script, 'replay' if args.optimized else 'record',
                     args.fixture, args.clock, args.optimized)
    if args.output is None:
        parser.error('--output is required')
    source, output = args.source_root.resolve(), args.output.resolve()
    if os.name != 'posix' or output.is_relative_to(source):
        raise ProfileError('diagnostic requires POSIX and an output outside source')
    if not 0 < args.timeout_seconds <= 600 or not math.isfinite(args.timeout_seconds):
        raise ProfileError('finite diagnostic budget in (0,600] required')
    if not 0 < args.max_native_seconds <= 240 or not math.isfinite(args.max_native_seconds):
        raise ProfileError('finite native diagnostic threshold in (0,240] required')
    head = args.ref or git(source, 'rev-parse', 'HEAD').decode().strip()
    if len(head) != 40 or any(c not in '0123456789abcdef' for c in head):
        raise ProfileError('complete fixed input commit SHA required')
    if args.worker:
        return worker(source, head, output, args.timeout_seconds, args.max_native_seconds)
    sys.path.insert(0, str(source))
    import runtime_process
    command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--worker',
               '--source-root', str(source), '--ref', head, '--output', str(output),
               '--timeout-seconds', str(args.timeout_seconds),
               '--max-native-seconds', str(args.max_native_seconds)]
    with runtime_process.cancellation_signals():
        result = runtime_process.command(command, cwd=source, timeout=args.timeout_seconds)
    print(result, end='')
    print((output / 'summary.json').read_text(encoding='utf-8'), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

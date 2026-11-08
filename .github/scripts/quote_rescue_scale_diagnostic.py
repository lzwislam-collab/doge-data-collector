"""PR85-only non-production scale measurement. Never call a live rescue.

Real pinned input/history is immutable. Only two temporary fixture commits add
one explicitly synthetic prediction/witness. All publication goes to a local
bare repository; HTTP is denied except one in-memory synthetic ticker response.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ACCEPTED = 'd6ce37bf01ad844b953467f15abb250937ccdcd9'
INPUT = '04b83739c1105c4fa7222cd14c8567abac2d9b8d'
PREPARATION_LIMIT = 180.0
EXPERIMENT_LIMIT = 480.0
sys.path.insert(0, str(ROOT))


def git(root, *args, env=None):
    deadline = os.environ.get('SCALE_PREP_DEADLINE_MONOTONIC')
    timeout = float(deadline)-time.monotonic() if deadline else 40
    if timeout <= 0:
        raise subprocess.TimeoutExpired(['git', *args], 0)
    begun = time.monotonic()
    status = 'not_completed'
    code = None
    try:
        body = subprocess.check_output(['git', *args], cwd=root, env=env,
                                       stderr=subprocess.PIPE, timeout=timeout)
        status, code = 'completed', 0
        return body.decode().strip()
    except subprocess.TimeoutExpired:
        status = 'preparation_deadline' if deadline else 'diagnostic_command_timeout'
        raise
    except subprocess.CalledProcessError as exc:
        status, code = 'command_failed', exc.returncode
        raise
    finally:
        if os.environ.get('SCALE_PREP_LOG'):
            append(Path(os.environ['SCALE_PREP_LOG']), dict(command=list(args),
                started_monotonic=begun, seconds=time.monotonic()-begun,
                utc=datetime.now(timezone.utc).isoformat(), status=status,
                exit_code=code, remaining_budget_seconds=max(0, float(deadline)-time.monotonic())))


def save(path, value):
    temporary = path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def append(path, value):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False)+'\n')
        stream.flush()


def summary(value):
    return dict(sha256=hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())


def identity(out):
    """Reuse completed CI only when all functional/test/protocol inputs match."""
    bootstrap = time.monotonic()-float(os.environ['SCALE_JOB_STARTED_MONOTONIC'])
    identity_started = time.monotonic()
    workflow = '.github/workflows/quote_only_rescue.yml'
    original = git(ROOT, 'show', ACCEPTED+':'+workflow).split('\n  rescue:\n', 1)[1]
    current = git(ROOT, 'show', 'HEAD:'+workflow).split('\n  rescue:\n', 1)[1]
    if original != current:
        raise ValueError('Production rescue job changed')
    paths = set(git(ROOT, 'ls-tree', '-r', '--name-only', ACCEPTED).splitlines())
    paths.update(git(ROOT, 'ls-tree', '-r', '--name-only', 'HEAD').splitlines())
    checked = {}
    for p in sorted(paths):
        relevant = p.endswith(('.py', '.gs', '.cjs', '.js', '.yml', '.yaml'))
        relevant |= 'protocol' in p.lower() and p.endswith('.json')
        relevant |= p.startswith(('requirements', 'pyproject', 'package'))
        if not relevant or p in ('.github/workflows/quote_only_rescue.yml',
                                '.github/scripts/quote_rescue_scale_diagnostic.py'):
            continue
        before = git(ROOT, 'rev-parse', ACCEPTED + ':' + p)
        after = git(ROOT, 'rev-parse', 'HEAD:' + p)
        if before != after:
            raise ValueError('Accepted functional CI no longer applies: ' + p)
        checked[p] = after
    proof = dict(accepted_head=ACCEPTED, prior_run=42172384300,
                 tested_checkout=git(ROOT, 'rev-parse', 'HEAD'), checked_blobs=checked,
                 functional_tests_rerun=False, bootstrap_seconds=bootstrap,
                 identity_diagnostic_seconds=time.monotonic()-identity_started)
    save(out / 'accepted-input-identity.json', proof)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        stream.write('reuse=true\n')


def prepare(out, scratch):
    import direction_forward_v42 as frozen
    import forward_validation as base
    import forward_validation_v2 as v2
    import quote_evidence_v2 as evidence
    import publish_forward_evidence as publisher
    started = time.monotonic()
    bare, source = scratch / 'remote.git', scratch / 'source'
    current_phase = None
    def phase(name):
        nonlocal current_phase
        if current_phase:
            append(out/'preparation-phases.jsonl',dict(event='phase_end',phase=current_phase['phase'],
                utc=datetime.now(timezone.utc).isoformat(),monotonic=time.monotonic(),
                seconds=time.monotonic()-current_phase['monotonic'],exit_reason='completed'))
        record = dict(phase=name, input_sha=INPUT, utc=datetime.now(timezone.utc).isoformat(),
            monotonic=time.monotonic(), elapsed=time.monotonic()-started, completed=False,
            remaining_seconds=max(0,float(os.environ['SCALE_PREP_DEADLINE_MONOTONIC'])-time.monotonic()))
        current_phase = record
        save(out/'preparation-phase.json', record)
        append(out/'preparation-phases.jsonl',dict(event='phase_start',**record))
    # Preserve the COMPLETE real graph. No shallow clone, alternates or hardlinks.
    phase('clone_full_graph_to_local_bare')
    git(ROOT, 'clone', '--bare', '--no-local', '--no-hardlinks', str(ROOT), str(bare))
    git(bare, 'update-ref', 'refs/heads/main', INPUT)
    git(bare, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    # The existing candidate requests blob:none. Supply that capability on the
    # mock server, while retaining EVERY original object in its complete store.
    # No candidate attempt is prewarmed; its clone and all lazy reads are timed.
    git(bare, 'config', 'uploadpack.allowFilter', 'true')
    phase('clone_independent_canonical_fixture')
    git(ROOT, 'clone', '--no-local', '--no-hardlinks', '--no-checkout',
        '--single-branch', '--branch', 'main', str(bare), str(source))
    required = set(v2.REQUIRED_CODE) | {base.PROTOCOL, v2.PROTOCOL,
        base.WITNESSES, base.RUN_CACHE, evidence.BINDINGS, 'quote_only_rescue.py',
        'quote_capture_retry.py', 'publish_forward_evidence.py'} | set(publisher.PATHS)
    patterns = ['/' + p for p in sorted(required)] + ['/' + evidence.BASE + '/snapshots/']
    git(source, 'sparse-checkout', 'set', '--no-cone', *patterns)
    git(source, 'config', 'core.autocrlf', 'false')
    git(source, 'checkout', 'main')
    git(source, 'config', 'user.name', 'Nonproduction Scale Fixture')
    git(source, 'config', 'user.email', 'nonproduction@example.invalid')
    phase('hash_actual_input_and_count_history')
    files = {}
    for p in sorted(source.rglob('*')):
        if p.is_file() and '.git' not in p.relative_to(source).parts:
            name = p.relative_to(source).as_posix()
            data = p.read_bytes()
            blob = git(source, 'rev-parse', INPUT + ':' + name)
            if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=blob:
                raise ValueError('Fixture bytes differ from pinned Git blob: '+name)
            files[name] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), git_blob=blob)
    expected_snapshots = set(git(source, 'ls-tree', '-r', '--name-only', INPUT, '--',
                                 evidence.BASE+'/snapshots/').splitlines())
    actual_snapshots = {p for p in files if p.startswith(evidence.BASE+'/snapshots/')}
    if actual_snapshots!=expected_snapshots:
        raise ValueError('Required actual snapshots were not fully materialized')
    histories = {}
    for p in (base.REG, base.SCORES, evidence.BINDINGS, base.WITNESSES, base.RUN_CACHE):
        histories[p] = dict(revisions=len(git(source, 'log', '--format=%H', INPUT, '--', p).splitlines()),
                           rows=len(list(csv.DictReader(io.StringIO((source/p).read_text())))))
    manifest = dict(production_input_sha=INPUT, tree=git(source, 'rev-parse', INPUT+'^{tree}'),
        graph_commits=int(git(source, 'rev-list', '--count', INPUT)),
        full_history=git(source, 'rev-parse', '--is-shallow-repository') == 'false',
        files=files, history=histories, actual_input_bytes=sum(x['bytes'] for x in files.values()),
        snapshot_count=len(actual_snapshots), mock_remote_filter_enabled=True,
        fixture_history_unchanged=True, candidate_attempt_prewarmed=False)
    save(out / 'input-manifest.json', manifest)
    phase('append_explicit_synthetic_fixture')
    registry = frozen.read_rows(source/base.REG, frozen.FCOLS)
    # No real record can enter the synthetic request, including expired 1H/7D.
    latest = max(frozen.utc(r['target_utc']) for r in registry.values())
    issued = (latest + timedelta(days=2)).replace(hour=3, minute=5, second=0, microsecond=0)
    target = issued + timedelta(hours=24)
    hour = issued.replace(minute=0)
    fid = f'{frozen.MODEL}_{int((hour-timedelta(hours=1)).timestamp()*1000)}_24h'
    if fid in registry:
        raise ValueError('Synthetic identity collision')
    row = dict(zip(frozen.FCOLS, [fid, frozen.MODEL, issued.isoformat(), issued.isoformat(),
        hour.isoformat(), '1', '24', target.isoformat(), '.5', 'UP', 'UP', 'DOWN',
        'NONPRODUCTION_SYNTHETIC_SCALE_ONLY']))
    registry[fid] = row
    frozen.write(source/base.REG, frozen.serial(registry, frozen.FCOLS))
    raw = json.dumps({'code':'0','data':[{'instId':'DOGE-USDT','last':'1',
                                       'ts':str(int(issued.timestamp()*1000))}]})
    bindings = frozen.read_rows(source/evidence.BINDINGS, evidence.BCOLS)
    qid = evidence.save_quote(source, raw, issued)
    evidence.bind(bindings, fid, 'ISSUE', qid)
    frozen.write(source/evidence.BINDINGS, frozen.serial(bindings, evidence.BCOLS))
    stamp = (issued + timedelta(minutes=1)).isoformat()
    env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    git(source, 'add', '-f', '--', base.REG, evidence.BINDINGS,
        evidence.snapshot_path(source, qid).relative_to(source).as_posix())
    git(source, 'commit', '-qm', 'NONPRODUCTION synthetic scale ISSUE', env=env)
    run = dict(id=999999999999999, head_sha=git(source, 'rev-parse', 'HEAD'), created_at=stamp)
    witnesses = frozen.read_rows(source/base.WITNESSES, base.WCOLS)
    witnesses[fid] = dict(zip(base.WCOLS, [fid, str(run['id']), stamp, run['head_sha'],
        'https://github.com/lzwislam-collab/doge-data-collector/actions/runs/'+str(run['id'])]))
    frozen.write(source/base.WITNESSES, frozen.serial(witnesses, base.WCOLS))
    git(source, 'add', '-f', '--', base.WITNESSES)
    git(source, 'commit', '-qm', 'NONPRODUCTION synthetic scale witness', env=env)
    git(source, 'push', str(bare), 'HEAD:refs/heads/main')
    fixture = dict(source=str(source), bare=str(bare), forecast_id=fid, run=run,
        test_start=(target+timedelta(minutes=5)).isoformat(),
        fixture_head=git(source, 'rev-parse', 'HEAD'), preparation_seconds=time.monotonic()-started,
        synthetic_commits=git(source, 'rev-list', INPUT+'..HEAD').splitlines(),
        limitations=['Synthetic ticker transport and run metadata; not live OKX.',
                    'Local bare publication; live GitHub writes/token latency untested.'])
    save(out / 'fixture.json', fixture)
    phase('complete')
    record = dict(phase='complete', input_sha=INPUT, utc=datetime.now(timezone.utc).isoformat(),
        monotonic=time.monotonic(), elapsed=time.monotonic()-started, completed=True, exit_reason='completed')
    save(out/'preparation-phase.json', record)
    append(out/'preparation-phases.jsonl', record)


def worker(out):
    import_started = time.monotonic()
    import quote_only_rescue as rescue
    import forward_validation as base
    import quote_evidence_v2 as evidence
    import_seconds = time.monotonic()-import_started
    fixture = json.loads((out/'fixture.json').read_text())
    source, bare = Path(fixture['source']), Path(fixture['bare'])
    origin = git(source, 'remote', 'get-url', 'origin')
    if Path(origin).resolve() != bare.resolve() or not bare.is_absolute():
        raise ValueError('Non-local publication destination')
    if git(bare, 'rev-parse', '--is-bare-repository') != 'true':
        raise ValueError('Non-bare fixture remote')
    for key in list(os.environ):
        if key.startswith('GIT_') or key in ('GH_TOKEN', 'GITHUB_TOKEN', 'ACTIONS_RUNTIME_TOKEN'):
            os.environ.pop(key)
    start = time.monotonic()
    epoch = datetime.fromisoformat(fixture['test_start'])
    def clock():
        return epoch + timedelta(seconds=time.monotonic()-start)
    class TestClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock()
    checkpoints = out / 'timings.jsonl'
    probe_seconds = 0.0
    def event(name, **kw):
        nonlocal probe_seconds
        probe_started = time.monotonic()
        append(checkpoints, dict(event=name, utc=datetime.now(timezone.utc).isoformat(),
            monotonic=time.monotonic(), elapsed=time.monotonic()-start, **kw))
        probe_seconds += time.monotonic()-probe_started
    def measured(name, function):
        def wrapped(*args, **kwargs):
            begun = time.monotonic()
            event(name+'.start', path=str(args[1]) if name=='history_receipts' and len(args)>1 else None)
            try:
                return function(*args, **kwargs)
            finally:
                event(name+'.end', seconds=time.monotonic()-begun)
        return wrapped
    def metadata(suffix):
        if suffix != '/actions/runs/'+str(fixture['run']['id']):
            raise AssertionError('Unexpected network metadata request')
        return dict(fixture['run'])
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            stamp = clock()
            raw = json.dumps({'code':'0','data':[{'instId':'DOGE-USDT','last':'1.1',
                'ts':str(int(stamp.timestamp()*1000))}]}, separators=(',', ':')).encode()
            (out/'synthetic-ticker.json').write_bytes(raw)
            event('synthetic_ticker', sha256=hashlib.sha256(raw).hexdigest())
            return raw
    def transport(request, **kwargs):
        if getattr(request, 'full_url', None) != evidence.URL:
            raise AssertionError('Unexpected HTTP request')
        return Response()
    before = dict(business=rescue.business_identity(source), metadata=rescue.metadata(source),
                  remote_head=git(bare, 'rev-parse', 'main'))
    save(out/'before.json', dict(business=summary(before['business']),
        metadata=summary(before['metadata']), remote_head=before['remote_head']))
    # No gate, history validator, collector, publisher or readback is replaced.
    # These wrappers preserve arguments, results and exceptions verbatim.
    with patch.object(base, 'github_json', side_effect=metadata), \
         patch.object(evidence.urllib.request, 'urlopen', side_effect=transport), \
         patch.object(rescue.publisher, 'datetime', TestClock), \
         patch.object(base, 'history_receipts', measured('history_receipts', base.history_receipts)), \
         patch.object(rescue, 'guard_protocol', measured('guard_protocol', rescue.guard_protocol)), \
         patch.object(rescue, 'create_attempt', measured('create_attempt', rescue.create_attempt)), \
         patch.object(rescue, 'verify_formal_issues', measured('verify_formal_issues', rescue.verify_formal_issues)), \
         patch.object(rescue, 'collect_scoped', measured('collect_scoped', rescue.collect_scoped)), \
         patch.object(rescue, 'verify_capture', measured('verify_capture', rescue.verify_capture)), \
         patch.object(rescue.publisher, 'validate_state', measured('validate_state', rescue.publisher.validate_state)), \
         patch.object(rescue.publisher, 'verify_readback', measured('verify_readback', rescue.publisher.verify_readback)), \
         patch.object(rescue.publisher, 'publish', measured('publish', rescue.publisher.publish)):
        event('rescue.start')
        full_path = False
        candidate_started = time.monotonic()
        save(out/'worker-timing.json', dict(candidate_entered=True, full_target_path_completed=False,
            candidate_import_seconds=import_seconds, started_utc=datetime.now(timezone.utc).isoformat(),
            started_monotonic=candidate_started))
        candidate_started = time.monotonic()
        candidate_finished = None
        try:
            result = rescue.run(source, [fixture['forecast_id']], clock=clock)
            candidate_finished = time.monotonic()
            save(out/'rescue-result.json', result)
            if result['status'] != 'verified_window_quote_rescue':
                raise ValueError('Full path not completed: '+result['status'])
            full_path = True
        finally:
            candidate_seconds = (candidate_finished or time.monotonic())-candidate_started
            event('rescue.end')
            save(out/'worker-timing.json', dict(candidate_entered=True,
                full_target_path_completed=full_path, candidate_seconds=candidate_seconds,
                candidate_import_seconds=import_seconds, probe_logging_seconds=probe_seconds,
                completed_utc=datetime.now(timezone.utc).isoformat(),
                completed_monotonic=time.monotonic()))
            audit_file = source/'quote_rescue_result.json'
            if audit_file.exists():
                import configparser
                audit = json.loads(audit_file.read_text())
                attempt = Path(audit.get('attempt',{}).get('path',''))
                if attempt.is_absolute() and (attempt/'.git/config').is_file():
                    config = configparser.ConfigParser(interpolation=None)
                    config.read(attempt/'.git/config')
                    save(out/'attempt-transport.json',dict(
                        promisor=config.getboolean('remote "origin"','promisor',fallback=False),
                        partial_clone_filter=config.get('remote "origin"','partialclonefilter',fallback=None),
                        shallow=(attempt/'.git/shallow').exists(),
                        alternates_present=(attempt/'.git/objects/info/alternates').exists()))
            after_business, after_metadata = rescue.business_identity(source), rescue.metadata(source)
            save(out/'after.json', dict(business=summary(after_business),
                metadata=summary(after_metadata), remote_head=git(bare, 'rev-parse', 'main'),
                canonical_unchanged=(after_business==before['business'] and after_metadata==before['metadata'])))
    if rescue.business_identity(source) != before['business'] or rescue.metadata(source) != before['metadata']:
        raise AssertionError('Canonical fixture changed')


def group_usage(pgid):
    rss, pids = 0, []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry/'stat').read_text().rsplit(')',1)[1].split()
            if int(fields[2])==pgid:
                if fields[0]!='Z':
                    pids.append(int(entry.name))
                rss += int(fields[21])*os.sysconf('SC_PAGE_SIZE')
        except (OSError,ValueError,IndexError):
            pass
    return rss, pids


def observe(proc, deadline, out, stage, roots):
    """Bound one experimental process group; never act on another run's group."""
    started, last_disk = time.monotonic(), -float('inf')
    stats = dict(scope='Sampled process-group aggregate RSS and du allocated bytes',
        rss_interval_seconds=0.5, disk_interval_seconds=5, peak_rss_bytes=0,
        peak_disk_allocated_bytes=0, disk_peaks_by_directory={}, sample_overhead_seconds=0.0)
    def sample(force_disk=False):
        nonlocal last_disk
        begun = time.monotonic()
        rss, pids = group_usage(proc.pid)
        stats['peak_rss_bytes'] = max(stats['peak_rss_bytes'],rss)
        if force_disk or begun-last_disk>=5:
            paths = [str(p) for p in roots if p.exists()]
            if paths:
                try:
                    remaining = 2 if force_disk else min(2,max(0,deadline-time.monotonic()))
                    if remaining<=0:
                        return pids
                    du = subprocess.run(['du','--summarize','--block-size=1','--',*paths],
                        capture_output=True,timeout=remaining)
                    values = {p:int(n) for n,p in (line.split('\t',1) for line in du.stdout.decode().splitlines())}
                    stats['peak_disk_allocated_bytes'] = max(stats['peak_disk_allocated_bytes'],sum(values.values()))
                    for p,n in values.items():
                        stats['disk_peaks_by_directory'][p] = max(stats['disk_peaks_by_directory'].get(p,0),n)
                except (OSError,ValueError,subprocess.TimeoutExpired):
                    stats['disk_sample_errors'] = stats.get('disk_sample_errors',0)+1
            last_disk = begun
            save(out/(stage+'-resources.json'),stats)
        stats['sample_overhead_seconds'] += time.monotonic()-begun
        return pids
    timed_out = False
    while True:
        if proc.poll() is not None:
            break
        remaining = deadline-time.monotonic()
        if remaining<=0:
            timed_out = True
            break
        sample()
        if deadline<=time.monotonic():
            timed_out = proc.poll() is None
            break
        time.sleep(min(0.5,max(0,deadline-time.monotonic())))
    # Also clean descendants on a failed leader exit (e.g. inner Git timeout).
    # A successful leader must not leave background maintenance running either.
    _, descendants = group_usage(proc.pid)
    if descendants:
        try:
            os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()
    execution_stopped = time.monotonic()
    execution_stopped_utc = datetime.now(timezone.utc).isoformat()
    cleanup_deadline = time.monotonic()+1
    while group_usage(proc.pid)[1] and time.monotonic()<cleanup_deadline:
        time.sleep(0.05)
    sample(force_disk=True)
    stats.update(seconds=execution_stopped-started, timed_out=timed_out,
        exit_code=proc.returncode, stopped_utc=execution_stopped_utc,
        stopped_monotonic=execution_stopped, final_resource_retention_seconds=time.monotonic()-execution_stopped,
        remaining_live_children=group_usage(proc.pid)[1],
        exit_reason='deadline' if timed_out else 'completed' if proc.returncode==0 else 'process_failure')
    save(out/(stage+'-resources.json'),stats)
    return stats


def measure(out):
    accepted = json.loads((out/'accepted-input-identity.json').read_text())
    bootstrap = accepted['bootstrap_seconds']
    # Thirty seconds reserve covers final status and lightweight artifact upload.
    # This observation cap is NOT the unchanged 180-second performance standard.
    experiment_deadline = min(float(os.environ['SCALE_JOB_STARTED_MONOTONIC'])+EXPERIMENT_LIMIT-30,
        time.monotonic()+max(0,datetime(2026,11,6,13,tzinfo=timezone.utc).timestamp()-time.time()))
    scratch = Path(tempfile.mkdtemp(prefix='doge-nonproduction-scale-')).resolve()
    roots = [scratch/'remote.git',scratch/'source',scratch/'attempts']
    prep_started = time.monotonic()
    preparation_deadline = min(prep_started+PREPARATION_LIMIT,experiment_deadline)
    if preparation_deadline<=prep_started:
        save(out/'scale-result.json',dict(status='FAIL',scope='Diagnostic authorization/observation deadline',
            rescue_started=False,production_3minute_budget='NOT_VERIFIED'))
        return 1
    prep_env = dict(os.environ,SCALE_PREP_DEADLINE_MONOTONIC=str(preparation_deadline),
        SCALE_PREP_LOG=str(out/'preparation-git.jsonl'))
    save(out/'preparation-control.json',dict(input_sha=INPUT,limit_seconds=PREPARATION_LIMIT,
        started_utc=datetime.now(timezone.utc).isoformat(),started_monotonic=prep_started,
        deadline_monotonic=preparation_deadline,subcommands_use_same_remaining_budget=True))
    with (out/'preparation.log').open('wb') as prep_log:
        prep = subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--prepare',
            '--scratch',str(scratch),'--out',str(out)],stdout=prep_log,
            stderr=subprocess.STDOUT,start_new_session=True,env=prep_env)
        prep_stats = observe(prep,preparation_deadline,out,'preparation',roots)
    last_phase = json.loads((out/'preparation-phase.json').read_text()) if (out/'preparation-phase.json').exists() else {}
    command_log = out/'preparation-git.jsonl'
    commands = []
    if command_log.exists():
        for line in command_log.read_text().splitlines():
            try:
                commands.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # A force-stopped last line is retained verbatim in the artifact.
    inner_deadline = bool(commands and commands[-1]['status']=='preparation_deadline')
    if inner_deadline:
        prep_stats['timed_out'],prep_stats['exit_reason'] = True,'preparation_deadline'
    last_phase.update(exit_code=prep.returncode,exit_reason=prep_stats['exit_reason'],
        stopped_utc=prep_stats['stopped_utc'],stopped_monotonic=prep_stats['stopped_monotonic'],
        total_seconds=time.monotonic()-prep_started)
    save(out/'preparation-phase.json',last_phase)
    append(out/'preparation-phases.jsonl',dict(event='preparation_end',**last_phase))
    if prep_stats['timed_out'] or prep.returncode or prep_stats['remaining_live_children']:
        result = dict(status='FAIL',scope='Diagnostic fixture preparation',
            preparation_timed_out=prep_stats['timed_out'],exit_code=prep.returncode,
            rescue_started=False,production_3minute_budget='NOT_VERIFIED',
            no_real_rescue=True,no_production_writes=True)
        save(out/'scale-result.json',result)
        print(json.dumps(result),flush=True)
        return 1
    if experiment_deadline<=time.monotonic():
        save(out/'scale-result.json',dict(status='FAIL',scope='Diagnostic observation limit before candidate',
            preparation_status='PASS',rescue_started=False,production_3minute_budget='NOT_VERIFIED'))
        return 1
    roots[2].mkdir()
    worker_env = dict(os.environ,TMPDIR=str(roots[2]))
    worker_env.pop('SCALE_PREP_DEADLINE_MONOTONIC',None)
    worker_env.pop('SCALE_PREP_LOG',None)
    save(out/'budget.json',dict(production_standard_seconds=180,bootstrap_seconds=bootstrap,
        experiment_limit_seconds=EXPERIMENT_LIMIT,preparation_limit_seconds=PREPARATION_LIMIT,
        worker_observation_remaining_seconds=max(0,experiment_deadline-time.monotonic()),
        production_network_auth_upload='NOT_VERIFIED',
        excluded_diagnostic_costs={
            'accepted_input_identity':'Only required to reuse a completed test run.',
            'fixture_preparation':'Mock bare server, synthetic ISSUE/witness and dedicated test source are absent in production.',
            'input_manifest':'Additional fixture reproducibility inventory; candidate still performs its own unchanged integrity checks.'},
        included_candidate_costs=['Imports','Own create_attempt clone and checkout','Full historical reads',
            'All freeze/scope/idempotency gates','Scoped capture and scoring','Publication','Remote readback','Own result persistence']))
    with (out/'scale.log').open('wb') as log:
        proc = subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker','--out',str(out)],
            stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=worker_env)
        worker_stats = observe(proc,experiment_deadline,out,'worker',roots)
    timing = json.loads((out/'worker-timing.json').read_text()) if (out/'worker-timing.json').exists() else {}
    elapsed = timing.get('candidate_seconds')
    if elapsed is None and timing.get('started_monotonic'):
        elapsed = worker_stats['stopped_monotonic']-timing['started_monotonic']
    # Subtract every measured probe overhead for a conservative necessary-path
    # lower bound. Instrumentation is never used to claim a production PASS.
    observed_accounted = (bootstrap+timing.get('candidate_import_seconds',0)+elapsed
        -timing.get('probe_logging_seconds',0)) if elapsed is not None else None
    accounted = (observed_accounted-worker_stats['sample_overhead_seconds']) if observed_accounted is not None else None
    within = observed_accounted is not None and observed_accounted<=180
    full_path = (timing.get('full_target_path_completed') is True and proc.returncode==0
        and not worker_stats['remaining_live_children'])
    result = dict(status='PASS' if full_path and within else 'FAIL',
        scope='Actual-scale NONPRODUCTION internal path; not production readiness',
        preparation_status='PASS',rescue_started=timing.get('candidate_entered',False),
        full_target_path_completed=full_path,worker_exit_code=proc.returncode,
        timed_out=worker_stats['timed_out'],bootstrap_seconds=bootstrap,
        candidate_seconds=elapsed,observed_accounted_seconds=observed_accounted,
        conservative_accounted_lower_bound_seconds=accounted,
        local_internal_180_second_comparison='PASS' if full_path and within else 'FAIL' if accounted is not None and accounted>180 else 'NOT_VERIFIED',
        production_3minute_budget='FAIL' if accounted is not None and accounted>180 else 'NOT_VERIFIED',
        no_real_rescue=True,no_production_writes=True,historical_fail_preserved=True)
    audit = Path(json.loads((out/'fixture.json').read_text())['source'])/'quote_rescue_result.json'
    if audit.exists():
        (out/'retained-rescue-audit.json').write_bytes(audit.read_bytes())
    save(out/'scale-result.json',result)
    print(json.dumps(result),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--identity', action='store_true')
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--scratch', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.identity:
        identity(args.out)
    elif args.worker:
        worker(args.out)
    elif args.prepare:
        prepare(args.out, args.scratch)
    else:
        raise SystemExit(measure(args.out))

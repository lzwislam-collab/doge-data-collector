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
sys.path.insert(0, str(ROOT))


def git(root, *args, env=None):
    return subprocess.check_output(['git', *args], cwd=root, env=env,
                                   stderr=subprocess.PIPE, timeout=40).decode().strip()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def summary(value):
    return dict(sha256=hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())


def identity(out):
    """Reuse completed CI only when all functional/test/protocol inputs match."""
    bootstrap = time.time()-float(os.environ['SCALE_JOB_STARTED_EPOCH'])
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
    def phase(name):
        save(out/'preparation-phase.json', dict(phase=name, input_sha=INPUT,
            elapsed=time.monotonic()-started, completed=False))
    # Preserve the COMPLETE real graph. No shallow clone, alternates or hardlinks.
    phase('clone_full_graph_to_local_bare')
    git(ROOT, 'clone', '--bare', '--no-local', '--no-hardlinks', str(ROOT), str(bare))
    git(bare, 'update-ref', 'refs/heads/main', INPUT)
    git(bare, 'symbolic-ref', 'HEAD', 'refs/heads/main')
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
            files[name] = dict(bytes=p.stat().st_size,
                sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                git_blob=git(source, 'rev-parse', INPUT + ':' + name))
    histories = {}
    for p in (base.REG, base.SCORES, evidence.BINDINGS, base.WITNESSES, base.RUN_CACHE):
        histories[p] = dict(revisions=len(git(source, 'log', '--format=%H', INPUT, '--', p).splitlines()),
                           rows=len(list(csv.DictReader(io.StringIO((source/p).read_text())))))
    manifest = dict(production_input_sha=INPUT, tree=git(source, 'rev-parse', INPUT+'^{tree}'),
        graph_commits=int(git(source, 'rev-list', '--count', INPUT)),
        full_history=git(source, 'rev-parse', '--is-shallow-repository') == 'false',
        files=files, history=histories, actual_input_bytes=sum(x['bytes'] for x in files.values()))
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
    save(out/'preparation-phase.json', dict(phase='complete', input_sha=INPUT,
        elapsed=time.monotonic()-started, completed=True))


def worker(out):
    import quote_only_rescue as rescue
    import forward_validation as base
    import quote_evidence_v2 as evidence
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
    def event(name, **kw):
        with checkpoints.open('a') as stream:
            stream.write(json.dumps(dict(event=name, elapsed=time.monotonic()-start, **kw))+'\n')
            stream.flush()
    def measured(name, function):
        def wrapped(*args, **kwargs):
            begun = time.monotonic()
            event(name+'.start')
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
         patch.object(rescue.publisher, 'validate_state', measured('validate_state', rescue.publisher.validate_state)), \
         patch.object(rescue.publisher, 'publish', measured('publish', rescue.publisher.publish)):
        event('rescue.start')
        try:
            result = rescue.run(source, [fixture['forecast_id']], clock=clock)
            save(out/'rescue-result.json', result)
            if result['status'] != 'verified_window_quote_rescue':
                raise ValueError('Full path not completed: '+result['status'])
        finally:
            event('rescue.end')
            after_business, after_metadata = rescue.business_identity(source), rescue.metadata(source)
            save(out/'after.json', dict(business=summary(after_business),
                metadata=summary(after_metadata), remote_head=git(bare, 'rev-parse', 'main'),
                canonical_unchanged=(after_business==before['business'] and after_metadata==before['metadata'])))
    if rescue.business_identity(source) != before['business'] or rescue.metadata(source) != before['metadata']:
        raise AssertionError('Canonical fixture changed')


def measure(out):
    bootstrap = json.loads((out/'accepted-input-identity.json').read_text())['bootstrap_seconds']
    # Test fixture construction is diagnostic overhead, separately reported.
    scratch = Path(tempfile.mkdtemp(prefix='doge-nonproduction-scale-')).resolve()
    prep_log = (out/'preparation.log').open('wb')
    prep = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--prepare',
        '--scratch', str(scratch), '--out', str(out)], stdout=prep_log,
        stderr=subprocess.STDOUT, start_new_session=True)
    prep_timeout = False
    try:
        prep.wait(timeout=60)
    except subprocess.TimeoutExpired:
        prep_timeout = True
        os.killpg(prep.pid, signal.SIGKILL)
        prep.wait()
    finally:
        prep_log.close()
    if prep_timeout or prep.returncode:
        result = dict(status='NOT_VERIFIED', scope='Actual-scale fixture preparation',
            preparation_timed_out=prep_timeout, exit_code=prep.returncode,
            rescue_started=False, production_3minute_budget='NOT_VERIFIED',
            no_real_rescue=True, no_production_writes=True)
        save(out/'scale-result.json', result)
        print(json.dumps(result), flush=True)
        return 1
    budget = max(0.0, 180.0-bootstrap)
    save(out/'budget.json', dict(total_budget_seconds=180, bootstrap_seconds=bootstrap,
        remaining_rescue_seconds=budget, fixture_preparation_excluded=True,
        production_network_and_upload='NOT_VERIFIED', scratch=str(scratch)))
    log = (out/'scale.log').open('wb')
    started = time.monotonic()
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', '--out', str(out)],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    timed_out = False
    try:
        proc.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    finally:
        log.close()
    elapsed = time.monotonic()-started
    result = dict(status='FAIL' if timed_out or proc.returncode else 'PASS',
        scope='Actual-scale NONPRODUCTION internal path; not production readiness',
        timed_out=timed_out, worker_exit_code=proc.returncode, rescue_wall_seconds=elapsed,
        bootstrap_seconds=bootstrap, accounted_seconds=bootstrap+elapsed,
        production_3minute_budget='FAIL' if timed_out else 'NOT_VERIFIED',
        no_real_rescue=True, no_production_writes=True, historical_fail_preserved=True)
    # Preserve permitted audit and raw checkpoints without copying entire repos.
    audit = Path(json.loads((out/'fixture.json').read_text())['source'])/'quote_rescue_result.json'
    if audit.exists():
        (out/'retained-rescue-audit.json').write_bytes(audit.read_bytes())
    save(out/'scale-result.json', result)
    print(json.dumps(result), flush=True)
    return 1 if result['status']=='FAIL' else 0


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

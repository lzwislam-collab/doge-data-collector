"""Second bounded quote pass only for genuinely open targets after slow steps.

Runs inside the existing serialized hourly workflow, after frozen integrity and
first capture. Expired windows never cause a capture or a reconstructed score.
"""
import json, os, subprocess, sys, uuid, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import forward_diagnostics as diagnostics
import quote_capture_retry as retry


def run(root=diagnostics.ROOT, now=None, capture=None):
    root=Path(root); now=now or datetime.now(timezone.utc)
    reg=diagnostics.read_csv(root/diagnostics.SOURCES['production_registry'],required=True,
        required_columns=('forecast_id','model_id','issued_at_utc','target_utc'))
    scores=diagnostics.read_csv(root/diagnostics.SOURCES['production_scores'],required=True,
        required_columns=('forecast_id','status'))
    radar=diagnostics.deadlines(reg,{r['forecast_id']:r for r in scores},now)
    open_ids=[r['forecast_id'] for r in radar if r['state'] in ('DUE_OPEN','DUE_URGENT')]
    result={'observed_at_utc':now.isoformat(),'open_ids':open_ids,'historical_backfill_allowed':False}
    if not open_ids: result.update(status='SKIP_NO_OPEN_TARGET',attempts=0)
    else:
        result['capture']=(capture or retry.capture)('watch',root=root)
        result.update(status=result['capture']['status'],attempts=result['capture']['attempts'])
    return result


def needs_tail_bridge(root, now):
    if (os.environ.get('DOGE_TAIL_CAPTURE_BRIDGE') != '1'
        or os.environ.get('GITHUB_ACTIONS') != 'true'
        or os.environ.get('GITHUB_REF') != 'refs/heads/main'
        or os.environ.get('GITHUB_REPOSITORY') != 'lzwislam-collab/doge-data-collector'):
        return False
    from runtime_deadline_service import utc
    path = Path(root)/'data/runtime_deadline_v1/latest.json'
    if not path.exists(): return False
    prior = json.loads(path.read_text(encoding='utf-8'))
    if (prior.get('status') != 'CAPTURE_LOOP_COMPLETED' or prior.get('fatal') is not False
        or not 0 <= (now-utc(prior['ended_at_utc'])).total_seconds() <= 180):
        return False
    reg = diagnostics.read_csv(Path(root)/diagnostics.SOURCES['production_registry'], required=True,
                               required_columns=('forecast_id','horizon_hours','source_hour_start_utc','target_utc'))
    scores = diagnostics.read_csv(Path(root)/diagnostics.SOURCES['production_scores'], required=True,
                                  required_columns=('forecast_id','status'))
    scored = {r['forecast_id'] for r in scores}
    hour = now.replace(minute=0,second=0,microsecond=0)
    missing_hour = now < hour+timedelta(minutes=40) and not any(
        int(r['horizon_hours']) == 1 and utc(r['source_hour_start_utc']) == hour for r in reg)
    imminent = any(r['forecast_id'] not in scored and now <= utc(r['target_utc']) <= now+timedelta(minutes=5) for r in reg)
    if missing_hour or imminent: return True
    if os.environ.get('DOGE_PROSPECTIVE_RUNTIME_BRIDGE') != '1': return False
    return _original18_tail_obligation(root, prior['ended_at_utc'])


def _original18_tail_obligation(root, ended_at_utc):
    """Read-only admission through the existing owned frozen journal reader."""
    from types import SimpleNamespace
    import runtime_process
    from runtime_deadline_service import LiveAdapter, utc
    consumer = SimpleNamespace(root=Path(root), _prospective_env=LiveAdapter._prospective_env)
    with runtime_process.cancellation_signals():
        state = LiveAdapter.original18_state_owned(consumer)
    # The owned read can take time. A stale completed foreground never admits
    # a new tail from the old pre-read clock.
    from runtime_forward_bridge import original
    at = datetime.now(timezone.utc)
    if not 0 <= (at-utc(ended_at_utc)).total_seconds() <= 180: return False
    policy, events, scores, bindings = (state[key] for key in ('policy','events','scores','bindings'))
    todo = original.plan(policy, events, scores, at)
    if todo['issue'] or todo['score']: return True
    for event in events:
        if at >= original.shadow.target_deadline(event, policy): continue
        key = event['event_id']
        target = original.shadow.utc(event['target_utc'])
        if key not in scores and at <= target <= at+timedelta(minutes=5): return True
        # Same pending-binding condition as the existing sequential bridge.
        if (key+'_prediction' not in bindings
                or key in scores and key+'_target' not in bindings): return True
    return False


def tail_bridge(root, now):
    if not needs_tail_bridge(root, now): return {'status':'SKIP_NO_IMMINENT_HANDOFF'}
    # The first service is finished; this subprocess is the only writer. Keep
    # target watching/real current-hour issuance ahead of slow tail diagnostics.
    started = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix='doge-tail-capture-') as directory:
        output = Path(directory)/'outputs'
        result = subprocess.run([sys.executable, str(Path(root)/'runtime_deadline_service.py'),
                                 '--duration-seconds','300'], cwd=root,
                                 env=dict(os.environ,GITHUB_OUTPUT=str(output)))
        safe = output.exists() and output.read_text(encoding='utf-8').splitlines()[-1:] == ['fatal=false']
    receipt = {'status':'completed' if result.returncode == 0 else 'failed',
               'started_at_utc':started.isoformat(), 'ended_at_utc':datetime.now(timezone.utc).isoformat(),
               'duration_limit_seconds':300, 'exit_code':result.returncode,
               'historical_backfill':False, 'economic_acceptance_passed':False}
    path = Path(root)/'data/p0_operations'/('tail_bridge_'+uuid.uuid4().hex+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream: json.dump(receipt,stream)
    if result.returncode or not safe: raise RuntimeError('bounded tail capture bridge failed or unsafe')
    return receipt


if __name__=='__main__':
    def safety_output(value):
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as stream:
                stream.write('tail_fatal='+str(value).lower()+'\n')
    safety_output(True)
    try:
        result=run()
        result['tail_bridge']=tail_bridge(diagnostics.ROOT,datetime.now(timezone.utc))
        if result['status']=='failed': raise RuntimeError('target guard failed')
        safety_output(False)
        print(json.dumps(result))
        raise SystemExit(1 if result['status']=='failed' else 0)
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error':str(exc)})); raise SystemExit(1)

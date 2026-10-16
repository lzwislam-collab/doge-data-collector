"""Independent, bounded TARGET-only capture. No issuance, backfill, or orders."""
import argparse,hashlib,json,subprocess
from datetime import datetime,timedelta,timezone
from pathlib import Path
import direction_forward_v42 as frozen
import forward_validation as base
import forward_validation_v2 as v2
import quote_evidence_v2 as evidence
import quote_capture_retry as retry
import publish_forward_evidence as publisher

RESERVED_MINUTES=5

def eligible_ids(registry,scores,now,expected_ids=None):
    if now.tzinfo is None or now.utcoffset()!=timedelta(0):raise ValueError('UTC clock required')
    permitted=None if expected_ids is None else set(expected_ids)
    return sorted(fid for fid,row in registry.items() if fid not in scores
                  and (permitted is None or fid in permitted)
                  and frozen.utc(row['target_utc'])<=now<=frozen.utc(row['target_utc'])+timedelta(minutes=20))

def ensure_clean_checkout(root):
    try:
        publisher.command(root,'diff','--exit-code','--quiet')
        publisher.command(root,'diff','--cached','--exit-code','--quiet')
    except subprocess.CalledProcessError as exc:raise ValueError('dirty tracked checkout or staged changes') from exc

def unsafe_aggregate_batch(registry,scores,now):
    # Frozen watch can use a quote up to 120 seconds old. Do not invoke it if
    # that quote could select an unscored row outside our five-minute reserve.
    allowed=set(eligible_ids(registry,scores,now))
    return any(fid not in scores and fid not in allowed
               and now-timedelta(minutes=27)<=frozen.utc(row['target_utc'])<=now
               for fid,row in registry.items())

def check_reserve(quotes,now):
    if any(now>frozen.utc(q['target_utc'])+timedelta(minutes=20) for q in quotes):
        raise ValueError('verification consumed five-minute reserve')

def guard_protocol(root):
    protocol=json.loads((root/v2.PROTOCOL).read_text(encoding='utf-8'))
    v2.check_protocol(protocol);v2.check_code(root,protocol)
    base.locked_file(root,v2.PROTOCOL);base.locked_file(root,base.PROTOCOL)
    if protocol['eligible_target_quote_window_minutes']!=25 or frozen.TARGET_GRACE_MIN!=25:
        raise ValueError('frozen target window differs from reviewed rescue contract')

def take_state(root):
    snapshots=root/evidence.BASE/'snapshots'
    return {'registry':frozen.read_rows(root/base.REG,frozen.FCOLS),
            'scores':frozen.read_rows(root/base.SCORES,frozen.SCOLS),
            'bindings':frozen.read_rows(root/evidence.BINDINGS,evidence.BCOLS),
            'snapshots':{p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshots.glob('*/*.json')}}

def verify_capture(root,before,allowed,now):
    after=take_state(root)
    if after['registry']!=before['registry']:raise ValueError('rescue changed prediction registry')
    for name in ('scores','bindings','snapshots'):
        if any(after[name].get(k)!=value for k,value in before[name].items()):
            raise ValueError('existing '+name+' evidence changed')
    added=set(after['scores'])-set(before['scores'])
    if not added.issubset(allowed):raise ValueError('capture scored an expired or unauthorized target')
    if set(after['bindings'])-set(before['bindings'])!={fid+'|TARGET' for fid in added}:
        raise ValueError('unexpected new quote binding')
    quotes=[]
    for fid in sorted(added):
        row=after['registry'][fid];score=after['scores'][fid];target=frozen.utc(row['target_utc'])
        deadline=target+timedelta(minutes=frozen.TARGET_GRACE_MIN-RESERVED_MINUTES)
        quoted=frozen.utc(score['target_quote_utc']);observed=frozen.utc(score['observed_at_utc'])
        if score['status']!='scored' or not target<=quoted<=observed<=deadline or now>deadline:
            raise ValueError('capture or publication preparation consumed five-minute reserve')
        evidence.verify_bound(root,row,score,after['bindings'])
        quotes.append({'forecast_id':fid,'quote_id':after['bindings'][fid+'|TARGET']['quote_id'],
                       'target_utc':row['target_utc'],'target_quote_utc':score['target_quote_utc'],
                       'observed_at_utc':score['observed_at_utc']})
    return quotes

def run(root,expected_ids=None,clock=None,capture_fn=None,publish_fn=None):
    root=Path(root);clock=clock or (lambda:datetime.now(timezone.utc))
    ensure_clean_checkout(root);guard_protocol(root)
    before=take_state(root);allowed=eligible_ids(before['registry'],before['scores'],clock(),expected_ids)
    if not allowed:return {'status':'skip_no_open_unscored_target','formal_prediction_issuance':False}
    publisher.validate_state(root)
    # Integrity validation and queueing use real time; stale input never permits backfill.
    capture_started=clock()
    if not eligible_ids(before['registry'],before['scores'],capture_started,expected_ids):
        return {'status':'skip_window_closed_during_validation','formal_prediction_issuance':False}
    if unsafe_aggregate_batch(before['registry'],before['scores'],capture_started):
        return {'status':'skip_unsafe_aggregate_watch_batch','formal_prediction_issuance':False}
    capture=(capture_fn or (lambda:retry.capture('watch',root=root)))()
    if capture.get('status')!='ok':raise ValueError('genuine quote capture failed: '+json.dumps(capture))
    guard_protocol(root)
    observed=clock()
    # Requested IDs witness freshness; frozen watch legitimately handles every
    # currently open target, including targets opening during the HTTP request.
    allowed=eligible_ids(before['registry'],before['scores'],observed)
    quotes=verify_capture(root,before,set(allowed),observed)
    check_reserve(quotes,clock())
    if not quotes:return {'status':'skip_no_eligible_genuine_quote','capture':capture,'formal_prediction_issuance':False}
    def validate_before_push(current_root):
        guard_protocol(current_root);publisher.validate_state(current_root)
        verified_quotes=verify_capture(current_root,before,set(allowed),clock())
        check_reserve(verified_quotes,clock())
    validate_before_push(root)
    publisher.command(root,'config','user.name','github-actions[bot]')
    publisher.command(root,'config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    result=(publish_fn or publisher.publish)(root,validate=validate_before_push)
    if (result.get('status')!='verified_remote_readback' or result.get('new_predictions') is not False
            or not result.get('remote_head') or not result.get('verified_at_utc')):
        raise ValueError('independent remote readback proof missing or unexpected issuance')
    verified=frozen.utc(result['verified_at_utc'])
    in_window=all(verified<=frozen.utc(q['target_utc'])+timedelta(minutes=25) for q in quotes)
    return {'status':'verified_window_quote_rescue' if in_window else 'verified_remote_readback_after_window',
            'scored_ids':[q['forecast_id'] for q in quotes],'quotes':quotes,'capture':capture,
            'publication':result,'remote_readback_within_window':in_window,
            'formal_prediction_issuance':False,'real_orders_authorized':False,'historical_backfill':False}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--expected-ids-json',required=True);args=parser.parse_args()
    try:
        expected=json.loads(args.expected_ids_json)
        if (not isinstance(expected,list) or not 1<=len(expected)<=100 or len(expected)!=len(set(expected))
                or any(not isinstance(x,str) or not x or len(x)>200 for x in expected)):
            raise ValueError('expected IDs must be a nonempty bounded list of distinct strings')
        out=run(Path(__file__).resolve().parent,expected)
    except Exception as exc:out={'status':'blocked','error':type(exc).__name__+': '+str(exc)[:700],'real_orders_authorized':False}
    Path('quote_rescue_result.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False))
    return 0 if out['status'].startswith('skip_') or out['status']=='verified_window_quote_rescue' else 1

if __name__=='__main__':raise SystemExit(main())

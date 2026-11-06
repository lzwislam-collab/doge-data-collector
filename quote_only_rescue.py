"""Independent, bounded TARGET-only capture. No issuance, backfill, or orders."""
import argparse,hashlib,json,re,subprocess
from datetime import datetime,timedelta,timezone
from pathlib import Path
import direction_forward_v42 as frozen
import forward_validation as base
import forward_validation_v2 as v2
import quote_evidence_v2 as evidence
import quote_capture_retry as retry
import publish_forward_evidence as publisher

RESERVED_MINUTES=5

def strict_utc(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)',value):
        raise ValueError('canonical UTC evidence required')
    return frozen.utc(value)

def strict_24h(fid,row):
    match=re.fullmatch(re.escape(frozen.MODEL)+r'_([0-9]{13})_24h',fid)
    if (not match or row.get('forecast_id')!=fid or row.get('model_id')!=frozen.MODEL
            or row.get('horizon_hours')!='24'):
        raise ValueError('unauthorized 24H identity or period')
    issued=strict_utc(row['issued_at_utc']);target=strict_utc(row['target_utc'])
    source=strict_utc(row['source_hour_start_utc']);quoted=strict_utc(row['source_quote_utc'])
    bar=int(match[1])
    if (bar%3600000 or source!=datetime.fromtimestamp((bar+3600000)/1000,timezone.utc)
            or not 0<=(issued-source).total_seconds()<=frozen.MAX_ISSUE_DELAY_MIN*60
            or not 0<=(issued-quoted).total_seconds()<=frozen.MAX_QUOTE_AGE_SEC
            or target-issued!=timedelta(hours=24)):
        raise ValueError('24H issue, target or source identity mismatch')
    return target

def requested_ids(registry,expected_ids):
    if (not isinstance(expected_ids,list) or not 1<=len(expected_ids)<=100
            or any(not isinstance(fid,str) for fid in expected_ids)
            or len(set(expected_ids))!=len(expected_ids)):
        raise ValueError('explicit distinct 24H IDs required')
    for fid in expected_ids:
        if fid not in registry:raise ValueError('requested prediction not registered')
        strict_24h(fid,registry[fid])
    return set(expected_ids)

def eligible_ids(registry,scores,now,expected_ids=None):
    if now.tzinfo is None or now.utcoffset()!=timedelta(0):raise ValueError('UTC clock required')
    permitted=requested_ids(registry,expected_ids)
    return sorted(fid for fid,row in registry.items() if fid not in scores
                  and fid in permitted
                  and frozen.utc(row['target_utc'])<=now<=frozen.utc(row['target_utc'])+timedelta(minutes=20))

def capture_scope(registry,scores,expected_ids,quoted,observed):
    permitted=requested_ids(registry,expected_ids)
    if (observed.tzinfo is None or observed.utcoffset()!=timedelta(0)
            or quoted.tzinfo is None or quoted.utcoffset()!=timedelta(0)
            or not 0<=(observed-quoted).total_seconds()<=frozen.MAX_QUOTE_AGE_SEC):
        raise ValueError('invalid genuine quote clock')
    selected={fid:row for fid,row in registry.items() if fid not in scores
              and frozen.utc(row['target_utc'])<=quoted<=frozen.utc(row['target_utc'])+timedelta(minutes=25)}
    if not selected or not set(selected).issubset(permitted):
        raise ValueError('aggregate watch would escape requested 24H scope')
    for fid,row in selected.items():
        target=strict_24h(fid,row)
        if not target<=quoted<=observed<=target+timedelta(minutes=20):
            raise ValueError('capture consumed five-minute reserve')
    return selected

def collect_scoped(root,expected_ids,clock=None):
    """Validate the actual exchange quote and entire frozen watch batch before writes."""
    clock=clock or (lambda:datetime.now(timezone.utc))
    with evidence.urllib.request.urlopen(evidence.urllib.request.Request(evidence.URL,
            headers={'Accept':'application/json','User-Agent':'DOGE-evidence-v2'}),timeout=20) as response:
        raw=response.read().decode('utf-8')
    now=clock()
    payload={'schema_version':2,'source_url':evidence.URL,'observed_at_utc':now.isoformat(),
             'raw_response_utf8':raw,'transport':'HTTPS collector observation; not exchange-signed and not an executable fill'}
    price,quoted,_=evidence.parse(payload)
    registry=frozen.read_rows(root/base.REG,frozen.FCOLS);scores=frozen.read_rows(root/base.SCORES,frozen.SCOLS)
    bindings=frozen.read_rows(root/evidence.BINDINGS,evidence.BCOLS)
    selected=capture_scope(registry,scores,expected_ids,quoted,now)
    frozen.verify_append_only(root/base.REG,registry,frozen.FCOLS,root)
    frozen.verify_append_only(root/base.SCORES,scores,frozen.SCOLS,root)
    evidence.history_receipts(root,evidence.BINDINGS,evidence.BCOLS,bindings)
    planned,added=frozen.score(selected,{},price,quoted,now)
    if added!=len(selected) or any(s['status']!='scored' for s in planned.values()):
        raise ValueError('invalid planned target scoring')
    qid=hashlib.sha256((json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')).hexdigest()
    for fid,row in selected.items():
        if fid+'|TARGET' in bindings:raise ValueError('unscored target already has a residual binding')
        evidence.verify_bound(root,row,None,bindings)
        evidence.bind(bindings,fid,'TARGET',qid)
    fresh=clock()
    if fresh<now or any(fresh>strict_24h(fid,row)+timedelta(minutes=20) for fid,row in selected.items()):
        raise ValueError('pre-write verification consumed five-minute reserve')
    # All deterministic scope/history/scoring/binding gates precede persistence.
    # I/O or publication errors still propagate; genuine evidence is never erased.
    if evidence.save_quote(root,raw,now)!=qid:raise ValueError('raw quote identity mismatch')
    out=evidence.watch.run(root,now,(price,quoted))
    if out['status']!='ok' or out['scored_forecast_ids']!=sorted(selected):
        raise ValueError('frozen watch result differs from prevalidated batch')
    frozen.write(root/evidence.BINDINGS,frozen.serial(bindings,evidence.BCOLS))
    return 0

def verify_formal_issues(root,registry,bindings,ids):
    """Reuse frozen remote-witness verification; never substitute a Git timestamp."""
    protocol=json.loads((root/v2.PROTOCOL).read_text(encoding='utf-8'))
    prior=frozen.read_rows(root/base.WITNESSES,base.WCOLS)
    base.history_receipts(root,base.WITNESSES,base.WCOLS,prior)
    if any(fid not in prior for fid in ids):raise ValueError('formal pre-target remote witness missing')
    subset={fid:registry[fid] for fid in ids}
    public=base.remote_witnesses(root,subset,{fid:prior[fid] for fid in ids},[],persist_cache=False)
    receipts=base.history_receipts(root,base.REG,frozen.FCOLS,registry)
    for fid,row in subset.items():
        issued=frozen.utc(row['issued_at_utc']);target=strict_24h(fid,row)
        stamp=frozen.utc(public[fid]['observed_public_utc'])
        if issued<frozen.utc(protocol['effective_from_utc']) or not issued-timedelta(seconds=1)<=stamp<=issued+timedelta(minutes=45) or stamp>=target:
            raise ValueError('prediction lacks eligible frozen formal publication')
        receipt=receipts.get(fid)
        committed=frozen.utc(receipt['committed_at_utc']) if receipt else None
        if committed is None or not issued-timedelta(seconds=1)<=committed<=issued+timedelta(minutes=45) or committed>=target:
            raise ValueError('prediction lacks eligible immutable commit receipt')
        evidence.verify_bound(root,row,None,bindings)
        evidence.verify_public_issue(root,row,bindings,public[fid]['head_sha'])

def ensure_clean_checkout(root):
    try:
        publisher.command(root,'diff','--exit-code','--quiet')
        publisher.command(root,'diff','--cached','--exit-code','--quiet')
    except subprocess.CalledProcessError as exc:raise ValueError('dirty tracked checkout or staged changes') from exc

def unsafe_aggregate_batch(registry,scores,now,expected_ids):
    # Frozen watch can use a quote up to 120 seconds old. Do not invoke it if
    # that quote could select an unscored row outside our five-minute reserve.
    allowed=set(eligible_ids(registry,scores,now,expected_ids))
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
    verify_formal_issues(root,before['registry'],before['bindings'],requested_ids(before['registry'],expected_ids))
    # Integrity validation and queueing use real time; stale input never permits backfill.
    capture_started=clock()
    if not eligible_ids(before['registry'],before['scores'],capture_started,expected_ids):
        return {'status':'skip_window_closed_during_validation','formal_prediction_issuance':False}
    if unsafe_aggregate_batch(before['registry'],before['scores'],capture_started,expected_ids):
        return {'status':'skip_unsafe_aggregate_watch_batch','formal_prediction_issuance':False}
    capture=(capture_fn or (lambda:retry.capture('watch',root=root,
        collect=lambda current_root,mode:collect_scoped(current_root,expected_ids,clock))))()
    if capture.get('status')!='ok':raise ValueError('genuine quote capture failed: '+json.dumps(capture))
    guard_protocol(root)
    observed=clock()
    # Never expand an explicit request after collection.
    allowed=eligible_ids(before['registry'],before['scores'],observed,expected_ids)
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

"""Independent future market-rule cohort. Never invokes the production issuer.

Prepare before the existing issuer, bind new originals, stage before its existing
publisher, then require exact pre-target public witnesses for every scored pair.
"""
import csv, hashlib, json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import minimal_market_candidate as candidate
import paired_forward_cohort as source
import paired_cohort_audit as paired
import p2_paired_execution_v3_1 as economic
import evidence_append_guard

ROOT=Path(__file__).resolve().parent
BASE='data/minimal_market_forward_v1'
POLICY='data/stage5/minimal_market_forward_v1_protocol.json'
SYSTEM='system/versions/DOGE_MINIMAL_MARKET_FORWARD_V1_20261013_0300.json'
RULES=('DOGE_MOMENTUM_1H','DOGE_REVERSAL_1H','BTC_MOMENTUM_1H')
CODE_FILES=sorted(set(economic.CODE_FILES)|{'minimal_market_candidate.py','minimal_market_forward.py','evidence_append_guard.py'})

def utc(s): return candidate.utc(s)
def digest(x): return source.digest(x)
def write(path,x):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes((json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode())

def guard(root=ROOT):
    policy=json.loads((root/POLICY).read_bytes()); registry=json.loads((root/SYSTEM).read_bytes())
    hashes={p:hashlib.sha256((root/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in CODE_FILES}
    if hashes!=registry['code_sha256_lf'] or hashlib.sha256((root/POLICY).read_bytes()).hexdigest()!=registry['protocol_sha256']:
        raise ValueError('minimal market registration integrity mismatch')
    if policy['protocol_id']!=registry['version_id'] or policy['rules']!=list(RULES) or policy['horizons_hours']!=[1,24]:
        raise ValueError('minimal market identity/rules changed')
    for key in ('no_backfill','no_real_orders','no_auto_promotion','no_dca_changes'):
        if policy.get(key) is not True: raise ValueError('minimal market boundary changed')
    if policy['effective_from_utc']!=registry['effective_from_utc']:raise ValueError('activation changed')
    return policy

def history(root=ROOT):
    path=root/BASE/'contexts.jsonl';body=path.read_bytes() if path.exists() else b''
    evidence_append_guard.guard(root,BASE+'/contexts.jsonl',body)
    previous=None;rows=[];ids=set()
    for line in body.decode().splitlines():
        r=json.loads(line);body=dict(r);sha=body.pop('record_sha256');fid=r['original']['forecast_id']
        if sha!=digest(body) or r['previous_sha256']!=previous or fid in ids:raise ValueError('minimal context chain/duplicate mismatch')
        if not source.re.fullmatch(r'[A-Za-z0-9_-]+',fid):raise ValueError('unsafe forecast ID')
        if json.loads((root/BASE/'records'/(fid+'.json')).read_bytes())!=r:raise ValueError('minimal record changed')
        previous=sha;ids.add(fid);rows.append(r)
    return rows

def make_context(original, prepared, now, effective):
    now=candidate.clock(now);effective=candidate.clock(effective)
    if not source.re.fullmatch(r'[A-Za-z0-9_-]+',original['forecast_id']):raise ValueError('unsafe original ID')
    issued=utc(original['issued_at_utc']);h=int(original['horizon_hours'])
    if original['forecast_id'] in prepared['starting_ids']:raise ValueError('old original cannot be backfilled')
    if h not in (1,24) or utc(original['target_utc'])!=issued+timedelta(hours=h):raise ValueError('invalid original target')
    if issued<effective or not 0<=(now-issued).total_seconds()<=30:raise ValueError('pre-activation or late decision')
    if utc(prepared['prepared_at_utc'])>issued:raise ValueError('future preparation')
    if prepared['status'] not in ('WAIT_SOURCE','PREPARED_NOT_ISSUED'):raise ValueError('invalid preparation status')
    if prepared['status']=='WAIT_SOURCE' and prepared['directions']:raise ValueError('missing source has injected direction')
    if prepared['status']=='PREPARED_NOT_ISSUED':
        input_rows=[r for rows in prepared['input_rows'].values() for r in rows]
        rebuilt=candidate.prepare(input_rows,utc(prepared['prepared_at_utc']))
        if any(rebuilt[k]!=prepared[k] for k in ('directions','features','feature_input_sha256')):raise ValueError('prepared input changed')
        last=max(int(r['open_utc_ms']) for r in input_rows)+candidate.HOUR_MS
        if int(utc(original['source_hour_start_utc']).timestamp()*1000)!=last:raise ValueError('different source cutoff')
    return {'schema_version':1,'original':dict(original),'prepared_sha256':digest(prepared),
        'prepared_at_utc':prepared['prepared_at_utc'],'decision_recorded_at_utc':now.isoformat(),
        'directions':{k:prepared['directions'].get(k) for k in RULES},'feature_status':prepared['status'],
        'p_up':None,'paper_action':'NO_TRADE','probability_calibration_validated':False,'real_orders_authorized':False}

def prepare(root=ROOT):
    guard(root);now=datetime.now(timezone.utc)
    with (root/'data/hourly_research/ohlc.csv').open(encoding='utf-8',newline='') as f:rows=list(csv.DictReader(f))
    prepared=candidate.prepare(rows,now)
    prepared['starting_ids']=sorted(source.frozen.read_rows(root/'data/direction_v42/forward_registry.csv',source.frozen.FCOLS))
    write(root/BASE/'pending_prepare.json',prepared)
    return {'status':prepared['status'],'prediction_issued':False}

def bind(root=ROOT):
    policy=guard(root);now=datetime.now(timezone.utc);prior=history(root)
    if now<utc(policy['effective_from_utc']):return {'status':'WAIT_ACTIVATION','new_contexts':0,'new_ids':[]}
    prepared=json.loads((root/BASE/'pending_prepare.json').read_bytes())
    original=source.frozen.read_rows(root/'data/direction_v42/forward_registry.csv',source.frozen.FCOLS)
    known={r['original']['forecast_id'] for r in prior};new=[]
    for fid,row in original.items():
        if fid in known or fid in prepared['starting_ids'] or int(row['horizon_hours']) not in (1,24):continue
        record=make_context(row,prepared,now,utc(policy['effective_from_utc']))
        record['previous_sha256']=new[-1]['record_sha256'] if new else prior[-1]['record_sha256'] if prior else None
        record['record_sha256']=digest(record);new.append(record)
    if new:
        prep_path=root/BASE/'prepared'/(digest(prepared)+'.json')
        if prep_path.exists() and json.loads(prep_path.read_bytes())!=prepared:raise ValueError('prior preparation changed')
        if not prep_path.exists():write(prep_path,prepared)
        for r in new:
            path=root/BASE/'records'/(r['original']['forecast_id']+'.json')
            if path.exists():raise ValueError('record already exists')
            write(path,r)
        with (root/BASE/'contexts.jsonl').open('ab') as f:
            for r in new:f.write((source.causal.canon(r)+'\n').encode())
        source.publication.command(root,'add','-f','--',BASE)
    return {'status':'ok','new_contexts':len(new),'new_ids':[r['original']['forecast_id'] for r in new],'historical_backfill':False}

def publication(root=ROOT):
    guard(root);history(root)
    expected={p.relative_to(root).as_posix():p.read_bytes() for p in (root/BASE).rglob('*') if p.is_file() and p.name not in ('failure_latest.json','audit_latest.json')}
    # Pending preparation is not staged on pre-activation/no-new-ID runs.
    expected={p:b for p,b in expected.items() if '/records/' in p or '/prepared/' in p or p.endswith('/contexts.jsonl')}
    if not source.publication.verify_readback(root,'FETCH_HEAD',expected):raise ValueError('minimal remote readback mismatch')
    summary=json.loads((root/BASE/'bind_latest.json').read_bytes())
    ids=summary['new_ids']
    if len(ids)!=summary['new_contexts'] or any(BASE+'/records/'+fid+'.json' not in expected for fid in ids):raise ValueError('bind readback IDs mismatch')
    return {'status':'verified_remote_readback','verified_files':len(expected),'verified_new_contexts':len(ids),'new_samples_verified':bool(ids)}

def verify_registration_bytes(registry, policy_raw, read_file):
    if hashlib.sha256(policy_raw).hexdigest()!=registry['protocol_sha256']:raise ValueError('witness protocol differs')
    for path,expected in registry['code_sha256_lf'].items():
        if hashlib.sha256(read_file(path).replace(b'\r\n',b'\n')).hexdigest()!=expected:raise ValueError('witness code differs: '+path)

def verify(root,record,original,proof,score,bindings):
    if record['original']!=original:raise ValueError('original row changed')
    raw=(root/BASE/'prepared'/(record['prepared_sha256']+'.json')).read_bytes();prepared=json.loads(raw)
    if digest(prepared)!=record['prepared_sha256']:raise ValueError('preparation hash mismatch')
    rebuilt=make_context(original,prepared,utc(record['decision_recorded_at_utc']),utc(guard(root)['effective_from_utc']))
    if any(record[k]!=v for k,v in rebuilt.items()):raise ValueError('context reproduction mismatch')
    public=proof['publication'];head=public['head_sha'];issued=utc(original['issued_at_utc']);observed=utc(public['observed_public_utc'])
    if not utc(record['decision_recorded_at_utc'])<=observed<utc(original['target_utc']) or observed>issued+timedelta(minutes=45):raise ValueError('late public decision')
    source.quotes.verify_bound(root,original,score,bindings)
    source.quotes.verify_public_issue(root,original,bindings,head)
    for path,expected in [(BASE+'/prepared/'+record['prepared_sha256']+'.json',raw),
        (BASE+'/records/'+original['forecast_id']+'.json',(root/BASE/'records'/(original['forecast_id']+'.json')).read_bytes())]:
        if source.publication.command(root,'show',head+':'+path)!=expected:raise ValueError('minimal context not at original public witness')
    return public

def compare(originals, contexts, scores, verified, now):
    tracks={}
    for h in (1,24):
        rows=[r for r in originals if int(r['horizon_hours'])==h]
        reserved=paired.nonoverlap([{'forecast_id':r['forecast_id'],'issued_at_utc':r['issued_at_utc'],'target_utc':r['target_utc'],'original':r} for r in rows])
        due=[r['original'] for r in reserved if utc(r['target_utc'])+timedelta(minutes=25)<=now]
        for rule in RULES:
            reasons={k:0 for k in ('missing_context','missing_features','zero_return_abstention','source_or_publication_unverified','missing_target_score')}
            for r in due:
                fid=r['forecast_id'];context=contexts.get(fid)
                reason='missing_context' if context is None else 'missing_features' if context['feature_status']=='WAIT_SOURCE' else 'zero_return_abstention' if context['directions'][rule] is None else 'source_or_publication_unverified' if fid not in verified else 'missing_target_score' if scores.get(fid,{}).get('status')!='scored' else None
                if reason:reasons[reason]+=1
            valid=[r for r in due if r['forecast_id'] in verified and contexts[r['forecast_id']]['directions'][rule] in ('UP','DOWN') and scores.get(r['forecast_id'],{}).get('status')=='scored']
            correct=sum(contexts[r['forecast_id']]['directions'][rule]==scores[r['forecast_id']]['realized_up'] for r in valid)
            comps={}
            for name,col in paired.COMPARATORS.items():
                wins=losses=0
                for r in valid:
                    fid=r['forecast_id'];y=scores[fid]['realized_up'];new=contexts[fid]['directions'][rule]
                    old='UP' if name=='always_up' else 'DOWN' if name=='always_down' else r[col]
                    wins+=int(new==y and old!=y);losses+=int(new!=y and old==y)
                comps[name]={'wins':wins,'losses':losses,'p_descriptive':paired.statistics.exact_mcnemar(wins,losses),'promotion_authorized':False}
            tracks[rule+'_'+str(h)+'h']={'issued_source_events':len(rows),'registered_contexts':sum(r['forecast_id'] in contexts for r in rows),
                'nonoverlap_excluded_source_events':len(rows)-len(reserved),'missing_reason_counts':reasons,
                'abstentions':reasons['zero_return_abstention'],'data_missing':sum(reasons.values())-reasons['zero_return_abstention'],
                'due_reserved':len(due),'qualified':len(valid),'missing':len(due)-len(valid),
                'accuracy_pct':100*correct/len(valid) if valid else None,
                'accuracy_missing_as_wrong_pct':100*correct/len(due) if due else None,
                'no_trade_in_accuracy':len(valid),'selected_paper_long':0,'brier':None,'comparators':comps}
    return tracks

def audit(root=ROOT):
    policy=guard(root);now=datetime.now(timezone.utc);records=history(root)
    out={'version_id':policy['protocol_id'],'audited_at_utc':now.isoformat(),'status':'WAIT_ACTIVATION' if now<utc(policy['effective_from_utc']) else 'ACTIVE_RESEARCH_SHADOW',
        'real_orders_authorized':False,'validated_70pct':False,'profitability_validated':False,'tracks':{},'rejected':{},
        'registered_contexts':len(records),'p_up_available':False,'inference':'Descriptive direction-only rules; manual fixed-date review; no sequential stopping or automatic promotion'}
    expected=max(0,int((now-utc(policy['effective_from_utc'])-timedelta(minutes=45)).total_seconds()//3600)+1)
    out['expected_closed_hourly_slots_each_horizon']=expected
    original=source.frozen.read_rows(root/'data/direction_v42/forward_registry.csv',source.frozen.FCOLS)
    rows=[r for r in original.values() if int(r['horizon_hours']) in (1,24) and utc(r['issued_at_utc'])>=utc(policy['effective_from_utc'])]
    out['closed_source_slots_each_horizon']={str(h):len({int((utc(r['issued_at_utc'])-utc(policy['effective_from_utc'])).total_seconds()//3600) for r in rows if int(r['horizon_hours'])==h and utc(r['issued_at_utc'])<=now-timedelta(minutes=45)}) for h in (1,24)}
    out['missing_issuer_slots_each_horizon']={h:max(0,expected-n) for h,n in out['closed_source_slots_each_horizon'].items()}
    out['preregistration_server_witness_verified']=False
    witness_path=root/BASE/'preregistration_witness.json'
    witness=json.loads(witness_path.read_bytes()) if witness_path.exists() else None
    runs=[source.publication.base.github_json('/actions/runs/'+str(witness['id']))] if witness else source.publication.base.fetch_runs()
    if records or runs:
        # Use a server run created before activation whose committed code/registry
        # exactly matches this preregistration. Git author timestamps alone fail.
        for run in runs:
            if utc(run['created_at'])>=utc(policy['effective_from_utc']):continue
            try:
                if source.publication.command(root,'show',run['head_sha']+':'+SYSTEM)!=(root/SYSTEM).read_bytes():continue
                registry=json.loads((root/SYSTEM).read_bytes())
                verify_registration_bytes(registry,source.publication.command(root,'show',run['head_sha']+':'+POLICY),
                    lambda path:source.publication.command(root,'show',run['head_sha']+':'+path))
                source.publication.command(root,'merge-base','--is-ancestor',run['head_sha'],'HEAD')
                out['preregistration_server_witness_verified']=True
                out['preregistration_witness']={k:run[k] for k in ('id','head_sha','created_at')}
                if witness and out['preregistration_witness']!=witness:raise ValueError('registration server witness changed')
                if not witness:write(witness_path,out['preregistration_witness'])
                break
            except (source.publication.subprocess.CalledProcessError,ValueError):continue
        if records and not out['preregistration_server_witness_verified']:raise ValueError('no pre-activation server preregistration witness')
    scores=source.frozen.read_rows(root/'data/direction_v42/forward_scores.csv',source.frozen.SCOLS)
    bindings=source.frozen.read_rows(root/source.quotes.BINDINGS,source.quotes.BCOLS)
    operations=json.loads((root/'data/p0_operations/latest.json').read_bytes());verified={};contexts={r['original']['forecast_id']:r for r in records}
    if records and (operations.get('status')!='ok' or not 0<=(now-utc(operations['audited_at_utc'])).total_seconds()<=900):raise ValueError('minimal source audit stale')
    for fid,record in contexts.items():
        try:verified[fid]=verify(root,record,original[fid],operations['forecast_proofs'][fid],scores.get(fid),bindings)
        except Exception as exc:out['rejected'][fid]=str(exc)
    out['tracks']=compare(rows,contexts,scores,verified,now)
    write(root/BASE/'audit_latest.json',out)
    return out

if __name__=='__main__':
    try:
        mode=sys.argv[1];result={'prepare':prepare,'bind':bind,'publication':publication,'audit':audit}[mode]()
        if mode=='bind':write(ROOT/BASE/'bind_latest.json',result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('tracks','rejected')}))
    except Exception as exc:
        write(ROOT/BASE/'failure_latest.json',{'status':'BLOCKED','error':str(exc),'at_utc':datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'status':'BLOCKED','error':str(exc)}));raise SystemExit(1)

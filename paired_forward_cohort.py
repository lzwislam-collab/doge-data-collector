"""Precompute a challenger, then bind only newly issued production events.

Production issuer and all frozen files remain untouched. Paired challenger and
paper selection share production quote/target, are prepared before issuance,
and published in the same commit. No historical import or real orders.
"""
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.request import Request,urlopen
import direction_forward_v42 as frozen
import p1_adaptive_v2 as causal
import quote_evidence_v2 as quotes
import quote_capture_retry as retry
import publish_forward_evidence as publication
import forward_execution_evidence as depth
import paired_selection_v1 as selection

ROOT=Path(__file__).resolve().parent
POLICY='data/stage5/paired_selection_v1_protocol.json'
BASE='data/paired_forward_v1'
REG=BASE+'/pairs.jsonl'
SYSTEM='system/versions/DOGE_PAIRED_SELECTIVE_SHADOW_V1_20261012_1600.json'
CODE_FILES=('paired_selection_v1.py','paired_forward_cohort.py','paired_cohort_audit.py',
            'p1_adaptive_v2.py','direction_forward_v42.py','direction_baseline_v42.py','quote_evidence_v2.py')


def utc(s):return frozen.utc(s)


def digest(obj):return depth.digest(obj)


def code_hashes(root):
    return {p:hashlib.sha256((root/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in CODE_FILES}


def guard(root):
    p=json.loads((root/POLICY).read_text(encoding='utf-8'))
    r=json.loads((root/SYSTEM).read_text(encoding='utf-8'))
    pointer=json.loads((root/'system/current_paired_shadow.json').read_text(encoding='utf-8'))
    if (pointer.get('version_id')!=p['protocol_id'] or pointer.get('registry_path')!=SYSTEM
        or pointer.get('registry_sha256')!=hashlib.sha256((root/SYSTEM).read_bytes()).hexdigest()
        or pointer.get('real_orders_authorized') is not False):raise ValueError('paired current system pointer mismatch')
    if (r['code_sha256_lf']!=code_hashes(root) or r['protocol_sha256']!=hashlib.sha256((root/POLICY).read_bytes()).hexdigest()
        or r.get('engineering_audit_pass') is not True or r.get('real_orders_authorized') is not False
        or r['selection_rule']!=selection.RULE):raise ValueError('paired system registration/code integrity mismatch')
    if p['protocol_id']!=r['version_id'] or p['effective_from_utc']!=r['effective_from_utc']:
        raise ValueError('paired protocol identity mismatch')
    if not all(p[k] is True for k in ('no_backfill','no_auto_promotion','no_real_orders','no_dca_changes','all_predictions_scored_including_no_trade')):
        raise ValueError('paired protocol safety boundary changed')
    return p


def parse_chain(body):
    records=[];previous=None;seen=set()
    for line in body.splitlines():
        r=json.loads(line);check=dict(r);actual=check.pop('record_sha256')
        if actual!=digest(check) or r['previous_sha256']!=previous:raise ValueError('paired chain hash mismatch')
        fid=r['production']['forecast_id']
        if fid in seen:raise ValueError('duplicate paired original ID')
        seen.add(fid);previous=actual;records.append(r)
    return records


def read_chain(path):return parse_chain(path.read_text(encoding='utf-8')) if path.exists() else []


def history_guard(root):
    current=read_chain(root/REG);previous=[]
    revisions=publication.command(root,'log','--reverse','--format=%H','--',REG).decode().splitlines()
    seen=set();last_hash=None
    for rev in revisions:
        patch=publication.command(root,'show','--format=','--unified=0','--no-renames',rev,'--',REG).decode('utf-8')
        additions=[];in_hunk=False
        for line in patch.splitlines():
            if line.startswith('@@'):
                match=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',line)
                if not match:raise ValueError('unknown paired history patch')
                old_start=int(match[1]);old_count=int(match[2] or 1);new_start=int(match[3])
                if old_count!=0 or old_start!=len(previous) or new_start!=len(previous)+1:
                    raise ValueError('historic paired chain removed/changed away from append boundary')
                in_hunk=True;continue
            if in_hunk:
                if not line.startswith('+'):raise ValueError('historic paired chain removed/changed')
                additions.append(line[1:])
        for line in additions:
            row=json.loads(line);check=dict(row);actual=check.pop('record_sha256');fid=row['production']['forecast_id']
            if actual!=digest(check) or row['previous_sha256']!=last_hash or fid in seen:
                raise ValueError('historical appended paired hash/duplicate mismatch')
            seen.add(fid);last_hash=actual;previous.append(row)
    if current[:len(previous)]!=previous:raise ValueError('committed paired records removed/changed')
    for row in current:
        path=record_path(root/REG,row['production']['forecast_id'])
        if not path.exists() or json.loads(path.read_text(encoding='utf-8'))!=row:
            raise ValueError('individual paired record absent/changed')
    return current


def record_path(registry,fid):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',fid):raise ValueError('unsafe production ID path')
    return registry.parent/'records'/(fid+'.json')


def append(path,rows):
    if not rows:return
    existing=read_chain(path);ids={r['production']['forecast_id'] for r in existing}
    previous=existing[-1]['record_sha256'] if existing else None
    text=[]
    for row in rows:
        row=dict(row);fid=row['production']['forecast_id']
        if fid in ids:raise ValueError('duplicate paired original ID')
        ids.add(fid);row['previous_sha256']=previous;row['record_sha256']=digest(row)
        record=record_path(path,fid);record.parent.mkdir(parents=True,exist_ok=True)
        raw=(causal.canon(row)+'\n').encode()
        if record.exists() and record.read_bytes()!=raw:raise ValueError('individual pair record changed')
        if not record.exists():
            with record.open('xb') as f:f.write(raw)
        text.append(causal.canon(row)+'\n');previous=row['record_sha256']
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8',newline='\n') as f:
        f.writelines(text);f.flush();os.fsync(f.fileno())


def make_pair(original,prepared,qid,now):
    issued=utc(original['issued_at_utc']);h=int(original['horizon_hours'])
    if h not in (1,24) or utc(original['target_utc'])!=issued+timedelta(hours=h):raise ValueError('invalid paired target')
    if not 0<=candidate_probability(prepared,h)<=1:raise ValueError('invalid challenger probability')
    if not 0<=(now-issued).total_seconds()<=30:raise ValueError('paired decision clock exceeds fixed deadline')
    if utc(prepared['prepared_at_utc'])>issued:raise ValueError('challenger prepared after production issuance')
    if prepared['source_close_utc']!=original['source_hour_start_utc']:raise ValueError('different feature cutoff')
    candidate=prepared['candidates'][str(h)]
    if candidate['training_last_label_ms']>int(utc(prepared['source_close_utc']).timestamp()*1000):raise ValueError('future training/calibration/validation label')
    for key,col in [('reversal','doge_reversal_direction'),('majority','majority_direction'),('btc_momentum','btc_momentum_direction')]:
        if candidate['frozen_features'][key]!=original[col]:raise ValueError('frozen feature mismatch')
    book=prepared.get('book')
    if book:
        book=dict(book)
        book['age_seconds']=(issued-utc(book['quote_utc'])).total_seconds()
        if utc(book['captured_at_utc'])>issued:raise ValueError('future book capture')
    decision=selection.select(candidate['p_up'],candidate,book)
    return {'schema_version':1,'production':dict(original),'issue_quote_sha256':qid,
        'prepared_at_utc':prepared['prepared_at_utc'],'prepared_snapshot_sha256':digest(prepared),
        'source_sha256':prepared['source_sha256'],'feature_cutoff_utc':prepared['source_close_utc'],
        'challenger':{'model_id':'DOGE_PAIRED_CAUSAL_PLATT_V1','p_up':candidate['p_up'],
                      'direction':'UP' if candidate['p_up']>=.5 else 'DOWN'},
        'decision_recorded_at_utc':now.isoformat(),'decision':decision,
        'exante_regime':{'btc_momentum':original['btc_momentum_direction'],
                        'doge_return_24h':candidate['doge_return_24h'],'btc_return_24h':candidate['btc_return_24h']},
        'book_error':prepared.get('book_error'),'comparison_context':'exact original quote, target and confirmed features; model prepared beforehand',
        'real_orders_authorized':False}


def candidate_probability(prepared,h):
    return selection.number(prepared['candidates'][str(h)]['p_up'])


def prepare(root):
    path=root/'data/hourly_research/ohlc.csv';raw_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    times,prices=frozen.read_csv(path,frozen.HOUR)
    close=datetime.fromtimestamp((times[-1]+frozen.HOUR)/1000,timezone.utc)
    candidates={}
    for h in (1,24):
        model=selection.fit_candidate(times,prices,h)
        model['frozen_features']=frozen.features(times,prices,h);candidates[str(h)]=model
    book=None;error=None
    try:
        requested=datetime.now(timezone.utc)
        with urlopen(Request(depth.URL,headers={'User-Agent':'DOGE-Paired-Shadow/1'}),timeout=8) as response:raw=response.read(2_000_001)
        captured=datetime.now(timezone.utc)
        if len(raw)>2_000_000:raise ValueError('oversized paired depth')
        payload=json.loads(raw);book=depth.validate_book(payload,requested,captured)
        book.update(captured_at_utc=captured.isoformat(),raw_response=payload)
    except Exception as exc:error=type(exc).__name__+': '+str(exc)[:200]
    return {'schema_version':1,'source_sha256':raw_hash,'source_close_utc':close.isoformat(),
        'prepared_at_utc':datetime.now(timezone.utc).isoformat(),'candidates':candidates,
        'book':book,'book_error':error,'calibration_scope':'historical candle label proxy; no forward validation claim'}


def save_snapshot(root,prepared):
    raw=(causal.canon(prepared)+'\n').encode();qid=digest(prepared)
    path=root/BASE/'prepared'/(qid+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.read_bytes()!=raw:raise ValueError('paired preparation snapshot tampered')
    if not path.exists():
        with path.open('xb') as f:f.write(raw)
    return qid


def issue(root=ROOT):
    policy=None;error=None
    try:policy=guard(root);history_guard(root)
    except Exception as exc:error=type(exc).__name__+': '+str(exc)[:200]
    before=frozen.read_rows(root/'data/direction_v42/forward_registry.csv',frozen.FCOLS)
    active=bool(policy and datetime.now(timezone.utc)>=utc(policy['effective_from_utc']))
    prepared=None
    if active and not error:
        try:
            times,_=frozen.read_csv(root/'data/hourly_research/ohlc.csv',frozen.HOUR)
            expected=[f'{frozen.MODEL}_{times[-1]}_{h}h' for h in (1,24)]
            if any(fid not in before for fid in expected):prepared=prepare(root)
        except Exception as exc:error=type(exc).__name__+': '+str(exc)[:200]
    result=retry.capture('direction',root=root)
    after=frozen.read_rows(root/'data/direction_v42/forward_registry.csv',frozen.FCOLS)
    new=[after[fid] for fid in sorted(set(after)-set(before)) if int(after[fid]['horizon_hours']) in (1,24)]
    added=[]
    if active and new:
        try:
            if error or prepared is None:raise ValueError(error or 'challenger not prepared; pair missing remains missing')
            if hashlib.sha256((root/'data/hourly_research/ohlc.csv').read_bytes()).hexdigest()!=prepared['source_sha256']:
                raise ValueError('source changed between preparation and production issuance')
            bindings=frozen.read_rows(root/quotes.BINDINGS,quotes.BCOLS)
            save_snapshot(root,prepared)
            for row in new:
                if utc(row['issued_at_utc'])<utc(policy['effective_from_utc']):continue
                qid=bindings[row['forecast_id']+'|ISSUE']['quote_id']
                pair=make_pair(row,prepared,qid,datetime.now(timezone.utc));added.append(pair)
            append(root/REG,added)
        except Exception as exc:error=type(exc).__name__+': '+str(exc)[:200]
    summary={'status':result['status'],'active':active,'production_new':len(new),
        'paired_new':len(added) if not error else 0,'pair_error':error,'capture':result,
        'historical_pair_backfill':False,'observed_at_utc':datetime.now(timezone.utc).isoformat()}
    frozen.write(root/BASE/'issue_latest.json',json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))
    return 1 if result['status']!='ok' or error else 0


def publish(root=ROOT):
    publication.validate_state(root)
    pair_error=None
    try:guard(root);history_guard(root)
    except Exception as exc:pair_error=type(exc).__name__+': '+str(exc)[:200]
    publication.command(root,'config','user.name','github-actions[bot]')
    publication.command(root,'config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    paths=[] if pair_error else sorted(p.relative_to(root).as_posix() for p in (root/BASE).rglob('*') if p.is_file())
    expected={p:(root/p).read_bytes() for p in paths}
    if paths:publication.command(root,'add','-f','--',*paths)
    def validate(r):
        publication.validate_state(r)
        if not pair_error:guard(r);history_guard(r)
    result=publication.publish(root,validate=validate)
    if not publication.verify_readback(root,'FETCH_HEAD',expected):raise ValueError('paired remote readback mismatch')
    result['paired_verified_files']=len(paths);result['pair_error']=pair_error
    frozen.write(root/BASE/'publication_latest.json',json.dumps(result,indent=2)+'\n')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('new_predictions='+str(result['new_predictions']).lower()+'\n')
    print(json.dumps(result));return 1 if pair_error else 0


def smoke(root=ROOT):
    guard(root);prepared=prepare(root)
    output={'status':'ok' if prepared['book'] else 'BLOCKED_LIVE_BOOK',
        'scope':'engineering preparation only; never an issued forecast or a paired record',
        'source_close_utc':prepared['source_close_utc'],'prepared_at_utc':prepared['prepared_at_utc'],
        'book_verified':bool(prepared['book']),'book_error':prepared['book_error'],
        'models':{h:{'p_up':c['p_up'],'calibration_n':c['calibration_n'],'validation_n':c['validation_n'],
                     'validation_selected_n':c['validation_selected_n'],
                     'decision':selection.select(c['p_up'],c,prepared['book'])} for h,c in prepared['candidates'].items()},
        'new_predictions':0,'real_orders_authorized':False}
    frozen.write(root/BASE/'engineering_smoke_latest.json',json.dumps(output,indent=2)+'\n')
    print(json.dumps(output));return 0 if prepared['book'] else 1


if __name__=='__main__':
    try:
        command=sys.argv[1] if len(sys.argv)>1 else 'issue'
        if command=='issue':raise SystemExit(issue())
        if command=='publish':raise SystemExit(publish())
        if command=='smoke':raise SystemExit(smoke())
        raise ValueError('unknown paired command')
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error':str(exc)}));raise SystemExit(1)

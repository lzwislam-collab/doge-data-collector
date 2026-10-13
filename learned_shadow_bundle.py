"""Build once, freeze, publicly audit and probe future candidates. No issuance CLI."""
import argparse
import csv
import io
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import quote as urlquote

import numpy as np
import phase1_research as research
import phase1_research_audit as source_audit
import learned_shadow_v1 as shadow
import evidence_append_guard

ROOT=Path(__file__).resolve().parent
SOURCE='data/hourly_research/ohlc.csv'
URLS=shadow.URLS
OUTPUT_OWNER=b'DOGE_LEARNED_SHADOW_OUTPUT_V1\n'


def git(root,*args):return subprocess.check_output(['git',*args],cwd=root)
def file_bytes(root,path):return (root/path).read_bytes().replace(b'\r\n',b'\n')


def check_output(out):
    at=out.resolve()
    if ROOT.is_relative_to(at) or any(at.is_relative_to(ROOT/p) for p in ('data','research','system','config','docs','audit')):
        raise ValueError('audit output overlaps protected repository assets')
    if at.exists() and not at.is_dir():raise ValueError('output must be a directory')
    if at.parent not in (ROOT,ROOT.parent) or not at.name.startswith(('stage2-','learned-shadow-')):
        raise ValueError('use a new stage2-/learned-shadow- output next to or inside this checkout')
    if at.exists() and any(at.iterdir()):
        marker=at/'.learned-shadow-owner'
        names={p.name for p in at.iterdir()}
        if (not marker.is_file() or marker.read_bytes()!=OUTPUT_OWNER or
            not names<={'audit.json','registry.json','raw_probe.json','failure.json','.learned-shadow-owner'}):
            raise ValueError('existing foreign output cannot be overwritten')


def restrict_review(review):
    r=dict(review);r['production_allowed']=False
    if r['status'] in ('REVIEW_PROMOTION','REVIEW_RETIREMENT'):
        r['unapproved_statistical_signal']=r['status']
        r['status']='WAIT_EXTERNAL_RETENTION_AND_INDEPENDENT_AUDIT'
    return r


def guard(root=ROOT):
    files={p:file_bytes(root,p) for p in shadow.REGISTERED_FILES}
    p=json.loads(files[shadow.POLICY]);package=json.loads(files[shadow.PACKAGE]);shadow.check_policy(p)
    if not shadow.registration_files_valid(p,package,files):raise ValueError('frozen candidate bundle changed or incomplete')
    if np.__version__!='2.3.5':raise ValueError('frozen NumPy runtime required')
    if package['training_source_path']!=SOURCE:raise ValueError('training source path changed')
    raw=git(root,'show',package['training_source_commit']+':'+SOURCE)
    if shadow.sha(raw)!=package['training_input_sha256']:raise ValueError('training source Git bytes changed')
    expected=replay_training(raw,shadow.utc(package['trained_at_utc']),json.loads(files[shadow.TRAIN_POLICY]))
    compare_training(package,expected)
    # Check both training and model structure, not merely a self-reported ready flag.
    if set(package['models'])!={'1','24'}:raise ValueError('training horizons incomplete')
    for h,models in package['models'].items():
        if set(models)!=set(p['groups']):raise ValueError('training candidate family incomplete')
        for name,groups in research.variants().items():
            columns=[i for g in groups for i in research.GROUPS[g]];model=models[name]
            if model['columns']!=columns or model['n_train']!=4096:raise ValueError('training shape changed')
            values=[*model['mean'],*model['scale'],*model['weights'],model['intercept']]
            if any(not np.isfinite(v) for v in values) or any(v<=0 for v in model['scale']):raise ValueError('model numeric invalid')
            if len(model['mean'])!=len(columns) or len(model['scale'])!=len(columns) or len(model['weights'])!=len(columns):
                raise ValueError('model dimensions invalid')
    return p,package,files


def compare_training(package,expected):
    for key in ('models','majority_p','training_windows'):
        if shadow.encode(package[key])!=shadow.encode(expected[key]):
            raise ValueError('fixed source/policy training replay differs: '+key)


def replay_training(raw,at,config):
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'source.csv';path.write_bytes(raw)
        frame,_=source_audit.market(path,3600000,at)
    answer={'models':{},'majority_p':{},'training_windows':{}}
    for h in (1,24):
        rows=research.features(frame,h,'candle_close_research')
        eligible=[r for r in rows if r is not None and r['label_end_ms']+24*3600000<int(at.timestamp()*1000)
                  and all(frame[s]['seen'][r['index']]<=int(at.timestamp()*1000) for s in ('DOGE-USDT','BTC-USDT'))
                  and frame['DOGE-USDT']['seen'][r['index']+h]<=int(at.timestamp()*1000)]
        train=eligible[-4096:]
        if len(train)!=4096:raise ValueError('insufficient known training labels')
        answer['models'][str(h)]={}
        for name,groups in research.variants().items():
            columns=[i for g in groups for i in research.GROUPS[g]]
            model=research.fit([[r['x'][i] for i in columns] for r in train],[r['y'] for r in train],config)
            model['columns']=columns;answer['models'][str(h)][name]=model
        answer['majority_p'][str(h)]=(1+sum(r['y'] for r in train))/(2+len(train))
        answer['training_windows'][str(h)]={'first_anchor_ms':train[0]['anchor_ms'],'last_anchor_ms':train[-1]['anchor_ms'],
                                          'last_target_ms':train[-1]['label_end_ms'],'n':4096}
    return research.canonical(answer)


def create_package(root=ROOT,source_commit=None):
    if any((root/p).exists() for p in (shadow.PACKAGE,shadow.ROLLING,shadow.REGISTRY)):
        raise ValueError('candidate package is immutable; new version needed')
    policy_raw=file_bytes(root,shadow.POLICY);p=json.loads(policy_raw);shadow.check_policy(p)
    if policy_raw!=shadow.encode(p):raise ValueError('policy bytes must be canonical before package creation')
    now=datetime.now(timezone.utc)
    if now>=shadow.utc(p['effective_from_utc']):raise ValueError('registration requires future activation')
    source_commit=source_commit or git(root,'rev-parse','origin/main').decode().strip()
    if not re.fullmatch('[a-f0-9]{40}',source_commit):raise ValueError('exact source commit required')
    raw=git(root,'show',source_commit+':'+SOURCE)
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'source.csv';path.write_bytes(raw)
        frame,quality=source_audit.market(path,3600000,now)
    config=json.loads(file_bytes(root,shadow.TRAIN_POLICY))
    result=research.experiment(frame,config)
    result['identity']={'market_input_sha256':shadow.sha(raw),'source_commit':source_commit,
        'research_policy_sha256':shadow.sha(file_bytes(root,'config/phase1_research_v1.json')),
        'code_sha256_lf':{name:shadow.sha(file_bytes(root,name)) for name in shadow.CODE_FILES}}
    result['experiment_id']=shadow.digest(result['identity']);result['result_sha256']=shadow.digest(result)
    rolling=shadow.encode(result)
    package={'schema_version':1,'trained_at_utc':now.isoformat(),'training_source_commit':source_commit,
        'training_source_path':SOURCE,'training_input_sha256':shadow.sha(raw),
        'training_availability_scope':'all_input_and_target_receipts_known_at_training_not_historical_PIT',
        'training_target':'closed candle close to h later close; prospective task uses actual issue quote to target quote',
        'rolling_artifact_sha256':shadow.sha(rolling),'models':{},'majority_p':{},'training_windows':{},
        'numpy_version':np.__version__,'selection':'ALL_NINE_FIXED_FAMILY_NO_BACKTEST_WINNER_SELECTION',
        'production_model_changed':False,'real_orders_authorized':False,'prediction_issued':False}
    for h in (1,24):
        rows=research.features(frame,h,'candle_close_research')
        eligible=[r for r in rows if r is not None and r['label_end_ms']+24*3600000<int(now.timestamp()*1000)
                  and all(frame[s]['seen'][r['index']]<=int(now.timestamp()*1000) for s in ('DOGE-USDT','BTC-USDT'))
                  and frame['DOGE-USDT']['seen'][r['index']+h]<=int(now.timestamp()*1000)]
        train=eligible[-4096:]
        if len(train)!=4096:raise ValueError('insufficient known training labels')
        package['models'][str(h)]={}
        for name,groups in research.variants().items():
            columns=[i for g in groups for i in research.GROUPS[g]]
            model=research.fit([[r['x'][i] for i in columns] for r in train],[r['y'] for r in train],config)
            model['columns']=columns;package['models'][str(h)][name]=model
        package['majority_p'][str(h)]=(1+sum(r['y'] for r in train))/(2+len(train))
        package['training_windows'][str(h)]={'first_anchor_ms':train[0]['anchor_ms'],'last_anchor_ms':train[-1]['anchor_ms'],
                                           'last_target_ms':train[-1]['label_end_ms'],'n':4096}
    for path,value in [(shadow.PACKAGE,shadow.encode(package)),(shadow.ROLLING,rolling)]:
        dest=root/path;dest.parent.mkdir(parents=True,exist_ok=True)
        with dest.open('xb') as stream:stream.write(value)
    registry={'schema_version':1,'version_id':p['protocol_id'],'effective_from_utc':p['effective_from_utc'],
        'registered_locally_at_utc':datetime.now(timezone.utc).isoformat(),
        'file_sha256_lf':{name:shadow.sha(file_bytes(root,name)) for name in shadow.REGISTERED_FILES-{shadow.REGISTRY}},
        'public_registration_status':'REQUIRES_EXACT_SUCCESSFUL_SERVER_RUN_BEFORE_ACTIVATION',
        'production_model_changed':False,'automatic_promotion':False,'real_orders_authorized':False}
    (root/shadow.REGISTRY).write_bytes(shadow.encode(registry))
    guard(root)
    return {'package_sha256':shadow.digest(package),'source_commit':source_commit,'quality':quality,
            'model_count':18,'prediction_issued':False}


def decode_response(raw):
    if len(raw)>2000000:raise ValueError('oversize response')
    return json.loads(raw)


def capture():
    start=datetime.now(timezone.utc);raw={};text={};hashes={}
    for key,url in URLS.items():
        request=Request(url,headers={'User-Agent':'DOGE-Learned-Shadow-Probe/1','Accept':'application/json'})
        with urlopen(request,timeout=9) as response:
            if response.geturl()!=url:raise ValueError('unexpected market source redirect')
            body=response.read(2000001)
        raw[key]=decode_response(body);text[key]=body.decode('utf-8');hashes[key]=shadow.sha(body)
    packet={'requested_at_utc':start.isoformat(),'captured_at_utc':datetime.now(timezone.utc).isoformat(),
            'source_urls':URLS,'raw':raw,'raw_text':text,'raw_sha256':hashes}
    shadow.quote(packet);return packet


def api(path):
    headers={'Accept':'application/vnd.github+json','User-Agent':'DOGE-Learned-Shadow-Audit/1'}
    token=os.environ.get('GITHUB_TOKEN')
    if token:headers['Authorization']='Bearer '+token
    request=Request('https://api.github.com/repos/'+shadow.REPO+path,headers=headers)
    with urlopen(request,timeout=20) as response:body=response.read(4000001)
    if len(body)>4000000:raise ValueError('oversize server metadata')
    return json.loads(body)


def read_at(head,path):
    if not re.fullmatch('[a-f0-9]{40}',head) or (path not in shadow.REGISTERED_FILES|{SOURCE} and
            not re.fullmatch(r'data/learned_shadow_v1/(events|scores)/[0-9]+_(1|24)\.json',path)):
        raise ValueError('unregistered public source path')
    try:return git(ROOT,'show',head+':'+path)
    except subprocess.CalledProcessError:
        # Public contents only; never send API credentials to the raw host.
        request=Request('https://raw.githubusercontent.com/'+shadow.REPO+'/'+head+'/'+urlquote(path,safe='/'),
                        headers={'User-Agent':'DOGE-Learned-Shadow-Audit/1'})
        with urlopen(request,timeout=20) as response:raw=response.read(32000001)
        if len(raw)>32000000:raise ValueError('oversize public file')
        return raw


def audit_bundle(out,probe=False):
    check_output(out);out.mkdir(parents=True,exist_ok=True)
    marker=out/'.learned-shadow-owner'
    if not marker.exists():marker.write_bytes(OUTPUT_OWNER)
    p,package,files=guard();now=datetime.now(timezone.utc)
    events=[];scores=[]
    for folder,records in [('events',events),('scores',scores)]:
        base=ROOT/'data/learned_shadow_v1'/folder
        for path in sorted(base.glob('*.json')):
            if not re.fullmatch(r'[0-9]+_(1|24)\.json',path.name) or path.is_symlink():raise ValueError('unsafe journal filename')
            evidence_append_guard.guard(ROOT,path.relative_to(ROOT).as_posix(),path.read_bytes())
            records.append(json.loads(path.read_bytes()))
    raw=shadow.audit_records(p,events,scores,now)
    runs=api('/actions/runs?per_page=100')['workflow_runs']
    runs=[r for r in runs if r['path'].split('@')[0]==shadow.WORKFLOW]
    # IDs are locators, never verified flags. Re-fetch actual server objects.
    index_path=ROOT/'data/learned_shadow_v1/witness_run_ids.json'
    if index_path.exists():
        ids=json.loads(index_path.read_bytes())
        if not isinstance(ids,list) or len(ids)>1000 or any(type(v) is not int or v<=0 for v in ids):
            raise ValueError('invalid/oversize witness locator index')
        known={r['id'] for r in runs}
        runs.extend(api('/actions/runs/'+str(rid)) for rid in ids if rid not in known)
    registration,rows,rejected=shadow.verified_rows(p,package,events,scores,files,runs,read_at,now)
    reviewed={}
    for h in (1,24):
        evaluation_policy=dict(p,slot_step_hours=p['slot_steps_hours'][str(h)])
        for group in p['groups']:
            reviewed[str(h)+'_'+group]=restrict_review(shadow.evaluation.evaluate(
                evaluation_policy,rows.get((h,group),[]),now,registered=bool(registration),horizon=h,group=group))
    output={'status':'FUTURE_PREREGISTRATION_VERIFIED' if registration else 'WAIT_PUBLIC_REGISTRATION',
            'audited_at_utc':now.isoformat(),'server_registration':registration,'local_structure':raw,
            'rejected_events':rejected,'reviews':reviewed,'model_package_sha256':shadow.digest(package),
            'witness_discovery_limit':'latest 100 total runs plus explicit server run ID locators; missing witnesses remain WAIT',
            'automatic_issuance_configured':False,'prediction_issued':False,'production_model_changed':False,
            'external_immutable_retention_verified':False,'apps_script_verified':False,
            'validated_70pct':False,'profitability_validated':False}
    if probe:
        packet=capture();q=shadow.quote(packet)
        (out/'raw_probe.json').write_bytes(shadow.encode(packet))
        output['live_probe']={'status':'RAW_PUBLIC_QUOTE_REPLAY_OK','price':q['price'],
            'packet_sha256':shadow.digest(packet),'source_quote_utc':q['at'].isoformat(),
            'sufficient_visible_depth':q['book'] is not None,'prediction_issued':False,
            'exchange_signed_response_verified':False,'actual_fill_verified':False}
    (out/'audit.json').write_bytes(shadow.encode(output))
    (out/'registry.json').write_bytes(files[shadow.REGISTRY])
    return {k:output[k] for k in ('status','model_package_sha256','prediction_issued','automatic_issuance_configured')}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['build','audit','probe'])
    parser.add_argument('--out',type=Path);parser.add_argument('--source-commit')
    args=parser.parse_args()
    try:
        if args.mode=='build':result=create_package(source_commit=args.source_commit)
        elif args.out is None:raise ValueError('--out required')
        else:result=audit_bundle(args.out,probe=args.mode=='probe')
        print(json.dumps(result,sort_keys=True))
    except Exception as exc:
        if args.out is not None:
            check_output(args.out);args.out.mkdir(parents=True,exist_ok=True)
            (args.out/'failure.json').write_bytes(shadow.encode({'status':'BLOCKED','error':type(exc).__name__+': '+str(exc),
                'prediction_issued':False,'at_utc':datetime.now(timezone.utc).isoformat()}))
        print(json.dumps({'status':'BLOCKED','error':type(exc).__name__+': '+str(exc)}));raise SystemExit(1)

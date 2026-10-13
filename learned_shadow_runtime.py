"""Append-only actual-clock candidate adapter. No orders, no promotions, no cron.

The original 13-file research bundle is immutable. This separate adapter must
itself be publicly witnessed before activation. GitHub is publication, not WORM.
"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

import learned_shadow_v1 as shadow
import learned_shadow_bundle as bundle
import evidence_append_guard

ROOT=Path(__file__).resolve().parent
BASE='data/learned_shadow_v1'
MANIFEST='config/learned_shadow_runtime_v1.json'


def status(value):
    return {'status':value, 'external_immutable_retention_verified':False,
            'promotion_authorized':False, 'real_orders_authorized':False,
            'production_model_changed':False, 'validated_70pct':False}


def plan(p,events,scores,now):
    """No backfill: issuance only current window, target only its strict window."""
    now=shadow.utc(now); start=shadow.utc(p['effective_from_utc'])
    emap={e['event_id']:e for e in events}
    if len(emap)!=len(events):raise ValueError('duplicate event')
    result={'issue':[], 'score':[], 'matured_missing_issuance':{}}
    for h in p['horizons_hours']:
        matured={str(int(s.timestamp()))+'_'+str(h) for s in shadow.due_slots(p,now,h)}
        result['matured_missing_issuance'][str(h)]=len(matured-set(emap))
        if now<start:continue
        step=p['slot_steps_hours'][str(h)]*3600
        slot=start+timedelta(seconds=int((now-start).total_seconds()//step)*step)
        key=str(int(slot.timestamp()))+'_'+str(h)
        if key not in emap and now<slot+timedelta(minutes=45):result['issue'].append(h)
    for key,e in sorted(emap.items()):
        if key not in scores and shadow.utc(e['target_utc'])<=now<shadow.target_deadline(e,p):
            result['score'].append(key)
    return result


def append_record(root,folder,key,value):
    if folder not in ('events','scores','runtime_receipts') or not re.fullmatch(r'[0-9]+_(1|24)',key):
        raise ValueError('unsafe journal path')
    root=Path(root).resolve(); path=root/BASE/folder/(key+'.json')
    for parent in (root/'data',root/BASE,path.parent,path):
        if parent.is_symlink():raise ValueError('symlink journal')
    raw=shadow.encode(value)
    evidence_append_guard.guard(root,path.relative_to(root).as_posix(),raw)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(raw)
    return path


def read_records(root,folder):
    result=[]
    for path in sorted((root/BASE/folder).glob('*.json')):
        if path.is_symlink() or not re.fullmatch(r'[0-9]+_(1|24)\.json',path.name):raise ValueError('unsafe journal')
        raw=path.read_bytes();value=json.loads(raw)
        if raw!=shadow.encode(value):raise ValueError('noncanonical journal')
        if value['event_id']+'.json'!=path.name:raise ValueError('journal identity mismatch')
        evidence_append_guard.guard(root,path.relative_to(root).as_posix(),raw)
        result.append(value)
    return result


def verify_runtime_files(root,hashes):
    if not hashes:raise ValueError('empty runtime registration')
    files={}
    for name,expected in hashes.items():
        if name not in ('learned_shadow_runtime.py','test_learned_shadow_runtime.py',
                         '.github/workflows/learned_shadow_live.yml'):
            raise ValueError('unregistered runtime file')
        raw=bundle.file_bytes(root,name)
        if shadow.sha(raw)!=expected:raise ValueError('runtime registration changed')
        files[name]=raw
    if len(files)!=3:raise ValueError('runtime registration incomplete')
    return files


def source_witness(raw,runs,read_at,at):
    return shadow.server_witness({bundle.SOURCE:raw},runs,read_at,at)


def attempt_work(items,action):
    errors={}
    for key in items:
        try:action(key)
        except Exception as exc:errors[key]=type(exc).__name__+': '+str(exc)
    return errors


def score_locator_ids(keys,bindings):
    return sorted({bindings[k+'_prediction']['run_id'] for k in keys if k+'_prediction' in bindings})


def publication_state(p,events,scores,runs,read_at,now,files=None):
    now=shadow.utc(now);found={};pending=False;files=files or {}
    for e in events:
        key=e['event_id'];target=shadow.utc(e['target_utc'])
        witness=shadow.server_witness({**files,BASE+'/events/'+key+'.json':shadow.encode(e)},
                    runs,read_at,target,shadow.utc(e['issued_at_utc']))
        if witness:found[key+'_prediction']=witness
        elif now<target:pending=True
        if key in scores:
            witness=shadow.target_witness(e,scores[key],runs,read_at,p)
            if witness:found[key+'_target']=witness
            elif now<shadow.target_deadline(e,p):pending=True
    return {'bindings':found,'request_witness':pending}


def read_bindings(root):
    bindings={}
    for path in sorted((root/BASE/'witness_bindings').glob('*.json')):
        if path.is_symlink() or not re.fullmatch(r'[0-9]+_(1|24)_(prediction|target)\.json',path.name):
            raise ValueError('unsafe witness binding')
        raw=path.read_bytes();value=json.loads(raw)
        if raw!=shadow.encode(value):raise ValueError('noncanonical witness binding')
        evidence_append_guard.guard(root,path.relative_to(root).as_posix(),raw)
        bindings[path.stem]=value
    return bindings


def append_binding(root,key,value):
    if not re.fullmatch(r'[0-9]+_(1|24)_(prediction|target)',key):raise ValueError('unsafe binding key')
    path=root/BASE/'witness_bindings'/(key+'.json')
    for parent in (root/'data',root/BASE,path.parent,path):
        if parent.is_symlink():raise ValueError('symlink binding')
    raw=shadow.encode(value);evidence_append_guard.guard(root,path.relative_to(root).as_posix(),raw)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(raw)


def read_at(head,path):
    if path in {MANIFEST,'learned_shadow_runtime.py','test_learned_shadow_runtime.py',
                '.github/workflows/learned_shadow_live.yml'}:
        if not re.fullmatch('[a-f0-9]{40}',head):raise ValueError('invalid pinned server head')
        try:return bundle.git(ROOT,'show',head+':'+path)
        except subprocess.CalledProcessError:return b''
    return bundle.read_at(head,path)


def runs_from_server(extra_ids=()):
    runs=bundle.api('/actions/runs?per_page=100')['workflow_runs']
    # Locators only. Always read actual server clocks and pinned public bytes.
    fixed=[38482045946,*extra_ids]
    # One fixed adapter registration locator. Event source locators are for a
    # separate long-window audit, not thousands of requests every 10-minute tick.
    locator=ROOT/BASE/'runtime_registration.json'
    if locator.exists():fixed.append(json.loads(locator.read_bytes())['run_id'])
    known={r['id'] for r in runs}
    for rid in sorted(set(fixed)-known):
        if type(rid) is not int or rid<=0:raise ValueError('invalid witness locator')
        runs.append(bundle.api('/actions/runs/'+str(rid)))
    return runs


def dispatch_witness():
    token=os.environ.get('GITHUB_TOKEN')
    if not token:raise ValueError('runtime token required')
    request=Request('https://api.github.com/repos/'+shadow.REPO+'/actions/workflows/learned_shadow_v1.yml/dispatches',
                    data=json.dumps({'ref':'main'}).encode(),method='POST',
                    headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'DOGE-Shadow-Runtime'})
    with urlopen(request,timeout=20) as response:
        if response.status!=204:raise ValueError('witness dispatch failed')


def run(publish=False):
    p,package,files=bundle.guard();now=datetime.now(timezone.utc)
    events=read_records(ROOT,'events');score_rows=read_records(ROOT,'scores')
    shadow.audit_records(p,events,score_rows,now)
    scores={s['event_id']:s for s in score_rows}
    todo=plan(p,events,scores,now)
    manifest_raw=bundle.file_bytes(ROOT,MANIFEST);manifest=json.loads(manifest_raw)
    runtime_files=verify_runtime_files(ROOT,manifest['sha256_lf']);runtime_files[MANIFEST]=manifest_raw
    if (manifest['model_package_sha256']!=shadow.digest(package) or manifest['effective_from_utc']!=p['effective_from_utc']
        or manifest['publication_only_not_independent_worm'] is not True):raise ValueError('runtime policy changed')
    bindings=read_bindings(ROOT)
    callback=os.environ.get('EVENT_WITNESS_RUN_ID')
    active=[e['event_id'] for e in events if now<shadow.target_deadline(e,p)]
    extra=score_locator_ids(active,bindings)
    if callback:extra.append(int(callback))
    runs=runs_from_server(extra)
    registration=shadow.server_witness({**files,**runtime_files},runs,read_at,shadow.utc(p['effective_from_utc']))
    result={**status('WAIT_RUNTIME_PREREGISTRATION' if not registration else 'WAIT_ACTIVATION' if now<shadow.utc(p['effective_from_utc']) else 'READY_PUBLICATION_ONLY'),
            'at_utc':now.isoformat(),'plan':todo,'registration':registration,'new_events':[],'new_scores':[],
            'automatic_runtime_installed':publish,'future_effective_utc':p['effective_from_utc'],'errors':{}}
    if not registration or not publish:return result
    if os.environ.get('GITHUB_REF')!='refs/heads/main':raise ValueError('writes restricted to main runtime')
    locator=ROOT/BASE/'runtime_registration.json'
    if not locator.exists():
        locator.parent.mkdir(parents=True,exist_ok=True)
        with locator.open('xb') as stream:stream.write(shadow.encode(registration))
    if now<shadow.utc(p['effective_from_utc']):return result
    # The callback and saved locator are independently reread from the server.
    state=publication_state(p,events,scores,runs,read_at,now,files)
    for key,witness in state['bindings'].items():
        if key not in bindings:append_binding(ROOT,key,witness)
    result['request_witness']=state['request_witness']
    # Score real due quotes first. No target reconstruction and no overwrite.
    emap={e['event_id']:e for e in events}
    def score_one(key):
        witness=shadow.server_witness({**files,BASE+'/events/'+key+'.json':shadow.encode(emap[key])},
                                      runs,read_at,shadow.utc(emap[key]['target_utc']),shadow.utc(emap[key]['issued_at_utc']))
        if not witness:return
        packet=bundle.capture(); score=shadow.score(emap[key],packet,p)
        append_record(ROOT,'scores',key,score);result['new_scores'].append(key)
    result['errors'].update(attempt_work(todo['score'],score_one))
    if todo['issue']:
        def issue_batch(_):
            raw=bundle.git(ROOT,'show','HEAD:'+bundle.SOURCE); now=datetime.now(timezone.utc)
            source=source_witness(raw,runs,read_at,now)
            if source is None:
                result['status']='WAIT_EXACT_SOURCE_WITNESS';result['request_witness']=True;return
            packet=bundle.capture(); issued=datetime.now(timezone.utc)
            frame=shadow.frame_from_csv(raw,issued)
            def issue_one(h):
                event=shadow.issue(p,package,frame,packet,issued,h)
                event.update({'input_source_path':bundle.SOURCE,'input_source_sha256':shadow.sha(raw),
                              'input_source_commit':source['head_sha']})
                append_record(ROOT,'events',event['event_id'],event)
                result['new_events'].append(event['event_id'])
                append_record(ROOT,'runtime_receipts',event['event_id'],{'event_id':event['event_id'],
                    'recorded_at_utc':issued.isoformat(),'witness_run_ids':[registration['run_id'],source['run_id']],
                    'runtime_manifest_sha256':shadow.sha(manifest_raw),'external_immutable_retention_verified':False})
            result['errors'].update(attempt_work(plan(p,events,scores,issued)['issue'],issue_one))
        result['errors'].update(attempt_work(['issuance'],issue_batch))
    result['request_witness']=bool(result.get('request_witness') or result['new_events'] or result['new_scores'])
    return result


def full_audit():
    """Long-window audit; locator count never truncates the prospective cohort."""
    p,package,files=bundle.guard();events=read_records(ROOT,'events');scores=read_records(ROOT,'scores')
    bindings=read_bindings(ROOT);ids=[v['run_id'] for v in bindings.values()]
    for receipt in (ROOT/BASE/'runtime_receipts').glob('*.json'):
        ids.extend(json.loads(receipt.read_bytes())['witness_run_ids'])
    runs=runs_from_server(ids);now=datetime.now(timezone.utc)
    registration,rows,rejected=shadow.verified_rows(p,package,events,scores,files,runs,read_at,now)
    result={**status('PUBLIC_BYTES_AUDITED_NOT_WORM'), 'at_utc':now.isoformat(),
            'registration':registration,'local_structure':shadow.audit_records(p,events,scores,now),
            'rejected_events':rejected,'qualified_rows':{str(k):len(v) for k,v in rows.items()}}
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['inspect','publish','dispatch','audit'])
    args=parser.parse_args()
    if args.mode=='dispatch':dispatch_witness()
    else:
        result=full_audit() if args.mode=='audit' else run(publish=args.mode=='publish')
        out=ROOT/'learned-shadow-runtime-output';out.mkdir(exist_ok=True)
        (out/'runtime.json').write_bytes(shadow.encode(result))
        output=os.environ.get('GITHUB_OUTPUT')
        if output:
            with open(output,'a',encoding='utf-8') as stream:
                stream.write('request_witness='+str(result.get('request_witness',False)).lower()+'\n')
                stream.write('has_errors='+str(bool(result.get('errors'))).lower()+'\n')
        print(json.dumps(result,sort_keys=True))

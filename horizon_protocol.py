"""Bounded 1H/24H/7D prospective protocols. No orders or production promotion.

Existing estimators/features/quotes are reused; legacy registered bytes stay intact.
Historical candle labels are research proxies, never retroactive formal forecasts.
"""
import argparse
import csv
import io
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
import phase1_research as research
import phase1_research_audit as audit
import phase2_models_v2 as models
import phase2_forward_v2 as r2
import phase2_forward_runtime_v2 as capture
import learned_shadow_v1 as legacy
import learned_shadow_bundle as bundle
import evidence_append_guard
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent
BASE='data/horizon_protocol_v1'
PACKAGE='config/horizon_protocol_v1.json'
SOURCE='data/hourly_research/ohlc.csv'
WORKFLOW='.github/workflows/horizon_protocol.yml'
HORIZONS=(1,24,168)
utc=audit.utc
encode=research.encode
sha=audit.sha
digest=lambda v:sha(encode(v))

def new_policy(start):
    weekly=start+timedelta(days=(-start.weekday())%7)
    return {'schema':'DOGE_HORIZON_PROTOCOL_V1','starts':{str(h):(weekly if h==168 else start).isoformat() for h in HORIZONS},
            'steps_hours':{'1':2,'24':24,'168':168},'issue_window_minutes':45,'target_window_minutes':25,
            'label':'fixed slot endpoint; entry at actual issue quote; target at slot+h, not issue+h',
            'daily_target_n':100,'fee_per_side':.001,'slippage_per_side':.0005,'notional_usdt':100,
            'real_orders_authorized':False,'production_promotion_authorized':False,'automatic_retraining':False}

def slot(p,h,now):
    if h not in HORIZONS:raise ValueError('unregistered horizon')
    start=utc(p['starts'][str(h)]);step=p['steps_hours'][str(h)]*3600
    return start+timedelta(seconds=math.floor((now-start).total_seconds()/step)*step)

def issue_due(p,h,now,events):
    s=slot(p,h,now)
    return now>=utc(p['starts'][str(h)]) and now<s+timedelta(minutes=p['issue_window_minutes']) and not any(e['event_id']==event_id(s,h) for e in events)

def event_id(s,h):return str(int(s.timestamp()))+'_'+str(h)

def indexed(rows):
    result={r['event_id']:r for r in rows}
    if len(result)!=len(rows):raise ValueError('duplicate event/score')
    return result

def source_tail(raw):
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')));fields=reader.fieldnames;rows=list(reader)
    last=max(int(r['open_utc_ms']) for r in rows);first=last-168*3600000
    out=io.StringIO(newline='');writer=csv.DictWriter(out,fieldnames=fields,lineterminator='\n');writer.writeheader()
    writer.writerows(r for r in rows if int(r['open_utc_ms'])>=first)
    return out.getvalue().encode()

def qualified(proof):
    return bool(proof.get('prediction') and proof.get('score')) if isinstance(proof,dict) else proof is True

def curve(rows,margins):
    result=[]
    for m in sorted(set(margins)):
        selected=[r for r in rows if abs(r['raw_score']-.5)>m]
        n=len(selected);hits=sum(int(r['raw_score']>.5)==r['y'] for r in selected)
        acc=hits/n if n else None
        z=1.959963984540054
        lower=((acc+z*z/(2*n)-z*math.sqrt(acc*(1-acc)/n+z*z/(4*n*n)))/(1+z*z/n)) if n else None
        result.append({'margin':m,'n':n,'coverage':n/len(rows) if rows else 0,'accuracy':acc,'wilson_lower':lower})
    return result

def freeze_threshold(rows,cutoff_ms):
    if any(r['label_end_ms']>=cutoff_ms or not 0<=r['raw_score']<=1 or r['y'] not in (0,1) for r in rows):raise ValueError('future/invalid threshold data')
    margins=sorted(abs(r['raw_score']-.5) for r in rows)
    grid=[0.]+[margins[min(len(margins)-1,int(q*len(margins)))] for q in (.2,.4,.6,.8)] if rows else [0.]
    c=curve(rows,grid)
    eligible=[r for r in c if r['n']>=30 and r['coverage']>=.1 and r['wilson_lower']>.5]
    best=max(eligible,key=lambda r:(r['wilson_lower'],r['coverage'],-r['margin'])) if eligible else None
    return {'status':'FROZEN' if best else 'FROZEN_NO_SIGNAL','margin':best['margin'] if best else None,
            'cutoff_ms':cutoff_ms,'source_sha256':digest(rows),'n':len(rows),'curve':c,
            'selection_rule':'five empirical margins; >=30 selected, >=10% coverage, Wilson lower>0.5; maximize lower then coverage',
            'scope':'historical proxy; no future tuning; all NO_TRADE when no eligible candidate'}

def action(score,threshold):
    m=threshold.get('margin')
    return 'NO_TRADE' if m is None or abs(score-.5)<=m else ('UP' if score>.5 else 'DOWN')

def probability_layer(raw,m,h):
    if m['horizon_hours']!=h:raise ValueError('cross-horizon model use')
    if not math.isfinite(raw) or not 0<=raw<=1:raise ValueError('invalid raw score')
    cal=m['calibration'];p=None
    if cal['status']=='INDEPENDENT_HISTORICAL_PROXY':p=research.predict(cal['model'],models.logits([raw]))[0]
    return {'raw_score':raw,'probability_up':p,'label':'calibrated historical proxy' if p is not None else 'raw score / uncalibrated',
            'calibration_status':cal['status'],'forward_calibration_verified':False}

def core_table(p,events,scores,proofs,now):
    em=indexed(events);sm=indexed(scores)
    if set(sm)-set(em):raise ValueError('orphan score')
    result={}
    for h in HORIZONS:
        es=[e for e in events if e['horizon_hours']==h];mature=[];s=utc(p['starts'][str(h)])
        while s+timedelta(hours=h,minutes=p['target_window_minutes'])<=now:
            mature.append(event_id(s,h));s+=timedelta(hours=p['steps_hours'][str(h)])
        for e in es:
            t=utc(e['slot_utc']);issued=utc(e['issued_at_utc'])
            if e['event_id']!=event_id(t,h) or slot(p,h,t)!=t or t<utc(p['starts'][str(h)]) or not t<=issued<t+timedelta(minutes=p['issue_window_minutes']) or issued>now or utc(e['target_utc'])!=t+timedelta(hours=h):raise ValueError('off-grid/future/mismatched timestamp')
        pairs=[(em[k],sm[k]) for k in mature if k in em and k in sm and qualified(proofs.get(k))]
        n=len(pairs);selected=[(e,s) for e,s in pairs if e['selective_direction']!='NO_TRADE']
        def mean(values):return sum(values)/len(values) if values else None
        def correct(e,s,key):return int((e[key]=='UP')==s['y'])
        econ=[s.get('net_return') for e,s in pairs]
        equity=peak=1.;dd=0.
        if n and all(v is not None for v in econ):
            for value in econ:equity*=1+value;peak=max(peak,equity);dd=max(dd,1-equity/peak)
            net=equity-1
        else:net=None;dd=None
        probs=[(e['probability_up'],s['y']) for e,s in pairs if e['probability_up'] is not None]
        bins=[]
        for i in range(10):
            b=[(v,y) for v,y in probs if min(9,int(v*10))==i]
            if b:bins.append({'bin':i,'n':len(b),'mean_probability':mean([v for v,y in b]),'observed_up':mean([y for v,y in b])})
        result[str(h)]={'Issued':len(es),'Matured':len(mature),'Scored':n,'Missing':len(mature)-n,
            'Missing issuance':sum(k not in em for k in mature),'Unverified or missing score':sum(k in em and not (k in sm and qualified(proofs.get(k))) for k in mature),
            'Issuance Coverage':sum(k in em for k in mature)/len(mature) if mature else None,
            'Coverage':n/len(mature) if mature else None,'Direction Accuracy':mean([correct(e,s,'direction') for e,s in pairs]),
            'Accuracy if missing wrong':sum(correct(e,s,'direction') for e,s in pairs)/len(mature) if mature else None,
            'Selective Accuracy':mean([correct(e,s,'selective_direction') for e,s in selected]),
            'Selective coverage':len(selected)/n if n else None,'NO_TRADE rate':1-len(selected)/n if n else None,
            'Brier':mean([(v-y)**2 for v,y in probs]) if len(probs)==n else None,
            'Raw score squared error (not calibrated Brier)':mean([(e['raw_score']-s['y'])**2 for e,s in pairs]),
            'Calibration':{'status':'WAIT_FORWARD_VALIDATION','n':len(probs),'reliability_bins':bins},
            'Always Up':mean([s['y'] for e,s in pairs]),'Always Down':mean([1-s['y'] for e,s in pairs]),
            'BTC baseline':mean([correct(e,s,'btc_direction') for e,s in pairs]),
            'Frozen model':mean([correct(e,s,'frozen_direction') for e,s in pairs]) if n and all(e.get('frozen_direction') is not None for e,s in pairs) else None,
            'Net return':net,'Max drawdown':dd,'economics_scope':'nonoverlapping spot paper LONG on selective UP, cash on DOWN/NO_TRADE; no short orders',
            'minimum_coverage_gate':bool(mature and n/len(mature)>=.9 and sum(k in em for k in mature)/len(mature)>=.9),
            'target_n':100 if h==24 else None,'status':'WAIT','duplicate_count':0,'timestamp_errors':0}
    return result

def build_package(now):
    frame,quality=audit.market(ROOT/SOURCE,3600000,now)
    cutoff=int(now.timestamp()*1000);tp=json.loads((ROOT/'config/phase1_research_v1.json').read_bytes())
    old=json.loads((ROOT/legacy.PACKAGE).read_bytes());hour=json.loads((ROOT/r2.MODEL).read_bytes())
    package={'policy':new_policy((now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)),
             'frozen_at_utc':now.isoformat(),'source_sha256':sha((ROOT/SOURCE).read_bytes()),'source_commit':git('rev-parse','HEAD').decode().strip(),
             'source_quality':quality,'models':{},'real_orders_authorized':False,'promotion_authorized':False,
             'method_sources':['https://scikit-learn.org/stable/modules/calibration.html','https://scikit-learn.org/stable/modules/classification_threshold.html']}
    for h in HORIZONS:
        rows=[r for r in research.features(frame,h,'candle_close_research') if r]
        if h==1:
            import phase2_factor_library as library
            rows=[r for r in library.rows(frame,1,'candle_close_research') if r and r['anchor_ms']>hour['trained_at_ms']]
            for r in rows:r['raw_score']=models.predict(hour['model'],[[round(v,9) for v in r['x'][5:]]])[0]
            base={'kind':'r2','model':hour['model'],'parent_identity':hour['trial_model_identity']}
        elif h==24:
            base={'kind':'linear','model':old['models']['24']['full'],'parent_package_sha256':digest(old)}
            rows=[r for r in rows if r['anchor_ms']>int(utc(old['trained_at_utc']).timestamp()*1000) and (r['anchor_ms']//3600000)%24==0]
            for r in rows:r['raw_score']=research.predict(base['model'],[r['x']])[0]
        else:
            rows=[r for r in rows if (r['anchor_ms']//3600000)%168==0]
            boundary=rows[int(len(rows)*.6)]['anchor_ms'];train=[r for r in rows if r['label_end_ms']<boundary]
            base={'kind':'linear','model':research.fit([r['x'] for r in train],[r['y'] for r in train],tp),
                  'training_last_label_ms':max(r['label_end_ms'] for r in train),'existing_features':research.FEATURE_NAMES}
            rows=[r for r in rows if r['anchor_ms']>=boundary]
            for r in rows:r['raw_score']=research.predict(base['model'],[r['x']])[0]
        # Chronological calibration -> threshold selection -> untouched validation;
        # purge labels crossing each boundary. All outcomes precede freeze.
        a=len(rows)//2;b=3*len(rows)//4
        cal=[r for r in rows[:a] if a<len(rows) and r['label_end_ms']<rows[a]['anchor_ms']]
        tune=[r for r in rows[a:b] if b<len(rows) and r['label_end_ms']<rows[b]['anchor_ms']]
        validation=rows[b:]
        calibration={'status':'UNCALIBRATED','n_fit':len(cal),'n_validation':len(validation),'reason':'requires >=100 independent fit and >=100 untouched validation labels; historical is not forward quote calibration'}
        if len(cal)>=100 and len(validation)>=100 and len({r['y'] for r in cal})==2:
            cm=research.fit(models.logits([r['raw_score'] for r in cal]),[r['y'] for r in cal],{**tp,'l2':1.})
            vp=research.predict(cm,models.logits([r['raw_score'] for r in validation]))
            rawloss=sum((r['raw_score']-r['y'])**2 for r in validation)/len(validation)
            loss=sum((v-r['y'])**2 for v,r in zip(vp,validation))/len(validation)
            if loss<=rawloss and loss<.25:calibration.update(status='INDEPENDENT_HISTORICAL_PROXY',model=cm,validation_brier=loss)
        threshold=freeze_threshold(tune,cutoff) if h==1 else {'status':'DIRECTION_ONLY','margin':0.}
        threshold['untouched_validation_curve']=curve(validation,[r['margin'] for r in threshold.get('curve',[])]) if h==1 else []
        m={'horizon_hours':h,'base':base,'calibration':calibration,'threshold':threshold,
           'historical_proxy_not_formal':True,'split_counts':{'calibration':len(cal),'threshold_selection':len(tune),'untouched_validation':len(validation)},
           'split_sha256':digest({'calibration':cal,'threshold_selection':tune,'validation':validation}),
           'last_label_ms':max((r['label_end_ms'] for r in rows),default=0)}
        if m['last_label_ms']>=cutoff:raise ValueError('future freeze labels')
        m['model_id']=digest(m);package['models'][str(h)]=m
    package['fixed_files']={n:sha((ROOT/n).read_bytes()) for n in ['horizon_protocol.py','test_horizon_protocol.py',WORKFLOW,
        'phase1_research.py','phase1_research_audit.py','phase2_models_v2.py','phase2_research.py','phase2_factor_library.py',
        'phase2_forward_v2.py','phase2_forward_runtime_v2.py','learned_shadow_v1.py','learned_shadow_bundle.py',
        'evidence_append_guard.py','forward_execution_evidence.py','factor_evaluation_v1.py',legacy.PACKAGE,r2.MODEL]}
    package['identity']=digest(package)
    return package

def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)

def load_package():
    p=json.loads((ROOT/PACKAGE).read_bytes())
    if digest({k:v for k,v in p.items() if k!='identity'})!=p['identity']:raise ValueError('package identity changed')
    for name,d in p['fixed_files'].items():
        if sha((ROOT/name).read_bytes())!=d:raise ValueError('registered code changed: '+name)
    for h,m in p['models'].items():
        if m['horizon_hours']!=int(h) or digest({k:v for k,v in m.items() if k!='model_id'})!=m['model_id']:raise ValueError('model binding changed')
    return p

def append(folder,key,value):
    if folder not in ('events','scores') or not all(c in '0123456789_' for c in key):raise ValueError('unsafe journal')
    path=ROOT/BASE/folder/(key+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    evidence_append_guard.guard(ROOT,path.relative_to(ROOT).as_posix(),encode(value))
    with path.open('xb') as f:f.write(encode(value))

def records(folder):
    result=[]
    for p in sorted((ROOT/BASE/folder).glob('*.json')):
        v=json.loads(p.read_bytes())
        if p.is_symlink() or p.stem!=v['event_id'] or p.read_bytes()!=encode(v):raise ValueError('journal bytes/identity changed')
        # Exact Git blob comparison; no weakening of legacy append guards.
        tracked=subprocess.run(['git','ls-files','--error-unmatch',str(p.relative_to(ROOT))],cwd=ROOT,capture_output=True).returncode==0
        if tracked and git('show','HEAD:'+p.relative_to(ROOT).as_posix())!=p.read_bytes():raise ValueError('published journal changed')
        evidence_append_guard.guard(ROOT,p.relative_to(ROOT).as_posix(),p.read_bytes())
        result.append(v)
    indexed(result);return result

def predict(m,raw,at):
    h=m['horizon_hours']
    if m['base']['kind']=='r2':
        r2.check_age(json.loads((ROOT/r2.MODEL).read_bytes()),at)
        f=r2.frame_from_raw(raw,at);x=r2.features(f,at);score=models.predict(m['base']['model'],[x])[0]
    else:
        f=legacy.frame_from_csv(raw,at.isoformat());x=legacy.causal_features(f,at);score=research.predict(m['base']['model'],[x])[0]
    return score,f,x

def snapshot_frame(raw,at):
    with tempfile.TemporaryDirectory() as temp:
        p=Path(temp)/'source.csv';p.write_bytes(raw)
        return audit.market(p,3600000,at)[0]

def derived(rawsource,raw,m,at):
    frame=snapshot_frame(rawsource,at);h=m['horizon_hours'];b=frame['BTC-USDT']['close']
    full=json.loads((ROOT/legacy.PACKAGE).read_bytes())
    frozen=research.predict(full['models'][str(h)]['full'],[legacy.causal_features(legacy.frame_from_csv(rawsource,at.isoformat()),at)])[0] if h in (1,24) else None
    return {**probability_layer(raw,m,h),'direction':'UP' if raw>.5 else 'DOWN','selective_direction':action(raw,m['threshold']),
            'btc_direction':'UP' if b[-1]>b[-1-h] else 'DOWN','frozen_direction':None if frozen is None else 'UP' if frozen>.5 else 'DOWN'}

def validate_event(e,package):
    m=package['models'][str(e['horizon_hours'])];at=utc(e['issued_at_utc'])
    if e['package_identity']!=package['identity'] or e['model_id']!=m['model_id'] or e['real_orders_authorized'] is not False:raise ValueError('event model/protocol mismatch')
    source=e['source_raw'].encode()
    if e['source_sha256']!=sha(source):raise ValueError('source snapshot hash mismatch')
    raw,f,x=predict(m,source,at)
    expected=derived(source,raw,m,at)
    if x!=e['features'] or any(e.get(k)!=v for k,v in expected.items()):raise ValueError('inference/decision/baseline replay mismatch')
    q=legacy.quote(e['entry_packet'])
    if e['entry_packet'].get('capture_errors') or not q['captured']<=at<=q['captured']+timedelta(seconds=30):raise ValueError('stale/failed entry capture')

@lru_cache(maxsize=4096)
def read_at(head,path):
    if not re.fullmatch('[a-f0-9]{40}',head) or not re.fullmatch('[A-Za-z0-9_./-]+',path) or '..' in path.split('/') or path.startswith('/'):raise ValueError('unsafe pinned horizon path')
    try:return git('show',head+':'+path)
    except subprocess.CalledProcessError:
        url='https://raw.githubusercontent.com/'+legacy.REPO+'/'+head+'/'+path
        with urlopen(Request(url,headers={'User-Agent':'DOGE-Horizon-Pinned-Read'}),timeout=20) as response:
            if response.geturl()!=url:raise ValueError('unexpected source redirect')
            raw=response.read(32000001)
        if len(raw)>32000000:raise ValueError('oversize public file')
        return raw

def witnesses(events,scores,package,now):
    """Only completed successful server jobs with exact pinned pre-target bytes."""
    try:
        runs=bundle.api('/actions/workflows/horizon_protocol.yml/runs?per_page=100')['workflow_runs'];proof={};registration=None
        # Durable locators survive the server's first-page retention window.
        known={r['id'] for r in runs}
        for path in sorted((ROOT/BASE/'locators').glob('*.json')):
            rid=json.loads(path.read_bytes())['run_id']
            if type(rid) is not int or path.stem!=str(rid):raise ValueError('invalid server run locator')
            if rid not in known:runs.append(bundle.api('/actions/runs/'+str(rid)))
        for run in runs:
            if run['head_branch']!='main' or run['status']!='completed' or run['conclusion']!='success' or run['event'] not in ('workflow_dispatch','workflow_run'):continue
            created=utc(run['created_at']);updated=utc(run.get('updated_at',now.isoformat()))
            need_registration=registration is None and created<min(utc(v) for v in package['policy']['starts'].values())
            need_event=any((not proof.get(e['event_id'],{}).get('prediction') and created<utc(e['target_utc']) and updated>=utc(e['issued_at_utc'])) or
                (e['event_id'] in indexed(scores) and not proof.get(e['event_id'],{}).get('score') and created<utc(e['target_utc'])+timedelta(minutes=package['policy']['target_window_minutes']) and updated>=utc(indexed(scores)[e['event_id']]['observed_at_utc'])) for e in events)
            if not need_registration and not need_event:continue
            jobs=bundle.api('/actions/runs/'+str(run['id'])+'/jobs')['jobs']
            job=next((j for j in jobs if j['name']=='horizons' and j['conclusion']=='success'),None)
            if not job:continue
            finished=utc(job['completed_at'])
            if not utc(run['created_at'])<=utc(job['started_at'])<=finished<=now:continue
            try:
                if read_at(run['head_sha'],PACKAGE)!=encode(package):continue
                if any(sha(read_at(run['head_sha'],n))!=d for n,d in package['fixed_files'].items()):continue
            except Exception:continue
            if finished<min(utc(v) for v in package['policy']['starts'].values()):registration={'run_id':run['id'],'head_sha':run['head_sha'],'completed_at_utc':finished.isoformat()}
            for e in events:
                try:
                    if read_at(run['head_sha'],BASE+'/events/'+e['event_id']+'.json')!=encode(e):continue
                    key=e['event_id'];proof.setdefault(key,{})
                    record={'run_id':run['id'],'head_sha':run['head_sha'],'completed_at_utc':finished.isoformat()}
                    if utc(e['issued_at_utc'])<=utc(job['started_at'])<=finished<utc(e['target_utc']):proof[key]['prediction']=record
                    s=indexed(scores).get(key)
                    if s and utc(s['observed_at_utc'])<=utc(job['started_at'])<=finished<utc(e['target_utc'])+timedelta(minutes=package['policy']['target_window_minutes']) and read_at(run['head_sha'],BASE+'/scores/'+key+'.json')==encode(s):proof[key]['score']=record
                except Exception:continue
        used={v['run_id'] for b in proof.values() for v in b.values()}
        if registration:used.add(registration['run_id'])
        for rid in used:
            path=ROOT/BASE/'locators'/(str(rid)+'.json');path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists():
                with path.open('xb') as f:f.write(encode({'run_id':rid}))
        return proof,None,registration
    except Exception as exc:return {},type(exc).__name__+': '+str(exc),None

def render(table):
    keys=['Issued','Matured','Scored','Missing','Missing issuance','Unverified or missing score','Issuance Coverage','Coverage','Direction Accuracy','Accuracy if missing wrong','Selective Accuracy','Selective coverage','NO_TRADE rate','Brier','Calibration','Always Up','Always Down','BTC baseline','Frozen model','Net return','Max drawdown','minimum_coverage_gate']
    lines=['# DOGE 1H / 24H / 7D core scorecard','Formal prospective records only; N/A is not zero. All tracks WAIT until evidence matures.','|Horizon|'+'|'.join(keys)+'|','|---|'+'|'.join(['---']*len(keys))+'|']
    for h,row in table.items():
        vals=[row[k]['status'] if isinstance(row[k],dict) else 'N/A' if row[k] is None else str(round(row[k],6)) if isinstance(row[k],float) else str(row[k]) for k in keys]
        lines.append('|'+('7D' if h=='168' else h+'H')+'|'+'|'.join(vals)+'|')
    return '\n'.join(lines)+'\n'

def run(online=False):
    package=load_package();p=package['policy'];now=datetime.now(timezone.utc)
    events=records('events');scores=records('scores');errors={}
    proof,werror,registration=witnesses(events,scores,package,now) if online else ({},'offline: server witnesses not checked',None)
    # Validate existing event and outcome replay before any further writes.
    for e in events:
        validate_event(e,package)
    for s in scores:
        e=indexed(events)[s['event_id']]
        if score_event(e,s['target_packet'],p)!=s:raise ValueError('outcome replay mismatch')
    core_table(p,events,scores,{},now)
    if online and registration:
        for e in events:
            if e['event_id'] in indexed(scores):continue
            target=utc(e['target_utc'])
            if target<=now<target+timedelta(minutes=p['target_window_minutes']):
                try:
                    s=score_event(e,capture.capture_target(),p);append('scores',e['event_id'],s);scores.append(s)
                except Exception as exc:errors[e['event_id']]=str(exc)
        for h in HORIZONS:
            now=datetime.now(timezone.utc)
            if not issue_due(p,h,now,events):continue
            try:
                # No simultaneous/overlapping actual quote windows for daily/weekly.
                previous=[e for e in events if e['horizon_hours']==h]
                if previous:
                    prev=max(previous,key=lambda e:e['slot_utc']);ps=indexed(scores).get(prev['event_id'])
                    if ps is None and now<utc(prev['target_utc'])+timedelta(minutes=p['target_window_minutes']):raise ValueError('previous target window still open')
                rawsource=source_tail((ROOT/SOURCE).read_bytes());packet=capture.capture_target();at=datetime.now(timezone.utc);q=legacy.quote(packet)
                if not q['captured']<=at<=q['captured']+timedelta(seconds=30):raise ValueError('entry quote stale')
                m=package['models'][str(h)];raw,f,x=predict(m,rawsource,at);s=slot(p,h,at)
                # BTC momentum uses h historical closed hours; not another model.
                e={'event_id':event_id(s,h),'horizon_hours':h,'slot_utc':s.isoformat(),'issued_at_utc':at.isoformat(),'target_utc':(s+timedelta(hours=h)).isoformat(),
                   'package_identity':package['identity'],'model_id':m['model_id'],'source_sha256':sha(rawsource),'source_raw':rawsource.decode(),
                   'entry_packet':packet,'features':x,**derived(rawsource,raw,m,at),'real_orders_authorized':False}
                validate_event(e,package)
                append('events',e['event_id'],e);events.append(e)
            except Exception as exc:errors[str(h)]=str(exc)
    table=core_table(p,events,scores,proof,now)
    report={'at_utc':now.isoformat(),'package_identity':package['identity'],'source_cutoff':package['source_quality'],
            'table':table,'witnesses':proof,'witness_error':werror,'registration_witness':registration,'errors':errors,'real_orders_authorized':False,
            'new_cron_count':0,'data_quality':{'duplicates':0,'timestamp_errors':0,'missing_retained':True},'status':'WAIT'}
    out=ROOT/'horizon-output';out.mkdir(exist_ok=True);(out/'report.json').write_bytes(encode(report));(out/'scorecard.md').write_text(render(table),encoding='utf8')
    (out/'runtime.json').write_bytes(encode({'run_id':os.environ.get('GITHUB_RUN_ID'),'head_sha':git('rev-parse','HEAD').decode().strip(),'at_utc':now.isoformat(),'online':online,'errors':errors}))
    return report

def score_event(e,packet,p):
    if packet.get('capture_errors'):raise ValueError('failed raw target capture')
    entry=legacy.quote(e['entry_packet']);exit=legacy.quote(packet);target=utc(e['target_utc'])
    if not target<=utc(packet['requested_at_utc'])<=exit['captured']<target+timedelta(minutes=p['target_window_minutes']) or exit['at']<target:raise ValueError('outside real target quote window')
    net=None;error=None
    try:
        if exit['book'] is None or exit['book']['ts_ms']<int(target.timestamp()*1000):raise ValueError('target depth unavailable/early')
        long=legacy.paper_net(entry,exit,p,1)
        net=long if e['selective_direction']=='UP' else 0.
    except ValueError as exc:error=str(exc)
    return research.canonical({'event_id':e['event_id'],'event_sha256':digest(e),'observed_at_utc':exit['captured'].isoformat(),'target_packet':packet,
                              'y':int(exit['price']>entry['price']),'net_return':net,'cost_error':error,'real_orders_authorized':False})

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['freeze','audit','tick']);a=ap.parse_args()
    if a.mode=='freeze':
        path=ROOT/PACKAGE
        if path.exists() or (ROOT/BASE).exists():raise ValueError('cannot refreeze published protocol')
        p=build_package(datetime.now(timezone.utc));path.write_bytes(encode(p));print(p['identity'])
    else:
        r=run(a.mode=='tick');print(json.dumps({'status':r['status'],'errors':r['errors'],'table':r['table']}))
        if r['errors']:raise SystemExit(1)

if __name__=='__main__':main()

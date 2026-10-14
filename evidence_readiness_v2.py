"""Read-only readiness, actual server-job publication and self-managed archive.

No forecast issuance, no historical PIT backdating, no admission to frozen models.
"""
import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.request import Request,urlopen

import derivatives_pit_v1 as derivatives
import factor_eligibility_v1 as eligibility
import user_managed_retention_v2 as retention
import learned_shadow_v1 as shadow
import learned_shadow_bundle as frozen_bundle

ROOT=Path(__file__).resolve().parent
REPO='lzwislam-collab/doge-data-collector'
WORKFLOW='.github/workflows/evidence_readiness_v2.yml'
LEDGER=derivatives.LEDGER
POLICY='config/evidence_custody_v2.json'
OUTPUT='evidence-v2-output'


def canon(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,stderr=subprocess.DEVNULL)


def api(path):
    headers={'Accept':'application/vnd.github+json','User-Agent':'DOGE-Evidence-Readiness/2'}
    if os.environ.get('GITHUB_TOKEN'):headers['Authorization']='Bearer '+os.environ['GITHUB_TOKEN']
    request=Request('https://api.github.com/repos/'+REPO+path,headers=headers)
    with urlopen(request,timeout=20) as response:raw=response.read(4000001)
    if len(raw)>4000000:raise ValueError('server metadata exceeds cap')
    return json.loads(raw)


def read_at(head,path):
    if (not re.fullmatch('[a-f0-9]{40}',head) or path!=LEDGER and not re.fullmatch(r'data/derivatives_pit_v1/raw/[a-f0-9]{64}\.json',path)):
        raise ValueError('unregistered public source path')
    try:return git('show',head+':'+path)
    except subprocess.CalledProcessError:return b''


def publication(row,proofs,reader,decision):
    decision=retention.utc(decision);seen=retention.utc(row['first_seen_utc'])
    if seen>decision:return None
    for run,jobs in proofs:
        try:
            if (run['repository']['full_name']!=REPO or run['path'].split('@')[0]!=WORKFLOW
                or run['head_branch']!='main' or run['event'] not in ('push','workflow_dispatch','workflow_run')
                or not re.fullmatch('[a-f0-9]{40}',run['head_sha'])):continue
            for job in jobs:
                if (job['run_id']!=run['id'] or job['name']!='source-publication' or job['status']!='completed'
                    or job['conclusion']!='success'):continue
                created,start,end=(retention.utc(v) for v in (run['created_at'],job['started_at'],job['completed_at']))
                if not created<=start<=end<=decision or seen>end:continue
                raw=reader(run['head_sha'],row['raw_path'])
                if hashlib.sha256(raw).hexdigest()!=row['raw_sha256']:continue
                ledger=reader(run['head_sha'],LEDGER)
                if canon(row) not in ledger.splitlines():continue
                return {'run_id':run['id'],'job_id':job['id'],'head_sha':run['head_sha'],
                        'public_completed_at_utc':end.isoformat(),'usable_from_utc':max(end,seen).isoformat(),
                        'scope':'SOURCE_AVAILABLE_BEFORE_DECISION_NOT_PAIRED_FORECAST_OR_EXCHANGE_SIGNATURE'}
        except (KeyError,ValueError,TypeError,OSError):continue
    return None


def latest_asof(rows,decision):
    decision=retention.utc(decision);latest={}
    for row in rows:
        seen=retention.utc(row['first_seen_utc'])
        if seen<=decision and (row['kind'] not in latest or retention.utc(latest[row['kind']]['first_seen_utc'])<seen):
            latest[row['kind']]=row
    return latest


def known_coverage(rows,kind,decision,published):
    boundary=retention.utc(decision).replace(minute=0,second=0,microsecond=0)
    hours={retention.utc(r['first_seen_utc']).replace(minute=0,second=0,microsecond=0) for r in rows
           if r['kind']==kind and r['capture_id'] in published
           and boundary-timedelta(hours=24)<=retention.utc(r['first_seen_utc'])<boundary}
    return len(hours)/24


def proofs_from_server():
    runs=api('/actions/workflows/evidence_readiness_v2.yml/runs?per_page=30')['workflow_runs']
    current=os.environ.get('GITHUB_RUN_ID')
    if current and int(current) not in {r['id'] for r in runs}:runs.append(api('/actions/runs/'+current))
    return [(r,api('/actions/runs/'+str(r['id'])+'/jobs?per_page=100')['jobs']) for r in runs
            if r['head_branch']=='main' and r['event']!='pull_request']


def package(objects,head,at):
    manifest={'prepared_at_utc':at.isoformat(),'source_commit':head,'custody_policy':'USER_MANAGED',
              'objects':{name:hashlib.sha256(raw).hexdigest() for name,raw in objects.items()},
              'independent_custodian_required':False,'locked_retention_verified':False}
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,raw in [('manifest.json',canon(manifest)),*sorted(objects.items())]:
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,raw)
    return stream.getvalue()


def run(upload=False):
    frozen_bundle.guard()
    now=datetime.now(timezone.utc);rows=derivatives.read(ROOT);failures=derivatives.read_failures(ROOT)
    policy=json.loads((ROOT/'config/factor_eligibility_v1.json').read_bytes())
    custody=json.loads((ROOT/POLICY).read_bytes());head=git('rev-parse','HEAD').decode().strip()
    if custody['independent_custodian_required'] is not False or custody['object_lock_required'] is not True:
        raise ValueError('user-approved custody boundaries changed')
    proofs=proofs_from_server();recent=[r for r in rows if now-timedelta(hours=25)<=retention.utc(r['first_seen_utc'])<=now]
    witnesses={r['capture_id']:w for r in recent if (w:=publication(r,proofs,read_at,now))}
    latest=latest_asof(rows,now);result={'audited_at_utc':now.isoformat(),'source_commit':head,'factors':{},
         'total_replayed_rows':len(rows),'quarantined_capture_count':len(failures),'historical_backdating_allowed':False,
         'independent_custodian_required':False,'independent_custodian_verified':False,
         'production_model_changed':False,'promotion_authorized':False,'real_orders_authorized':False,
         'new_predictions_issued':0,'future_effective_utc':'2026-10-16T00:00:00Z',
         'prospective_issue_target_chain_verified':False,'validated_70pct':False}
    for kind in derivatives.URLS:
        row=latest.get(kind);coverage=known_coverage(rows,kind,now,set(witnesses));w=witnesses.get(row['capture_id']) if row else None
        if row:
            observation=dict(row,raw_verified=True,public_pre_target_witness_verified=bool(w),source_coverage_fraction=coverage)
            if w:observation['usable_from_utc']=w['usable_from_utc']
            gate=eligibility.qualify(policy,observation,now)
        else:gate={'research_allowed':False,'production_allowed':False,'reasons':['no_observation']}
        result['factors'][kind]={'latest_capture_id':row['capture_id'] if row else None,
            'latest_first_seen_utc':row['first_seen_utc'] if row else None,'public_witness':w,
            'published_last24h_hourly_coverage':coverage,'gate':gate,
            'scope':'DATA_READINESS_ONLY_NEW_MODEL_REGISTRATION_AND_PROSPECTIVE_ABLATION_STILL_REQUIRED'}
    out=ROOT/OUTPUT
    if out.is_symlink():raise ValueError('unsafe output')
    out.mkdir(exist_ok=True)
    objects={POLICY:git('show','HEAD:'+POLICY),LEDGER:git('show','HEAD:'+LEDGER)}
    for row in recent:objects[row['raw_path']]=git('show','HEAD:'+row['raw_path'])
    for name in ('evidence_readiness_v2.py','user_managed_retention_v2.py',WORKFLOW,'config/factor_eligibility_v1.json'):
        objects[name]=git('show','HEAD:'+name)
    # Same bundle can protect original forecasts and raw targets, without
    # issuing forecasts or changing any frozen files. Keep only recent slots.
    for name in shadow.REGISTERED_FILES|{'config/learned_shadow_runtime_v1.json','learned_shadow_runtime.py',
                                       'data/hourly_research/ohlc.csv'}:
        objects[name]=git('show','HEAD:'+name)
    target_limits={}
    frozen=json.loads(objects[shadow.POLICY])
    events={}
    prefixes={}
    current_source=objects['data/hourly_research/ohlc.csv']
    for folder in ('events','scores'):
        for path in sorted((ROOT/'data/learned_shadow_v1'/folder).glob('*.json')):
            if path.is_symlink() or not re.fullmatch(r'[0-9]+_(1|24)\.json',path.name):raise ValueError('unsafe learned journal')
            row=json.loads(path.read_bytes())
            if folder=='events' and retention.utc(row['issued_at_utc'])>=now-timedelta(hours=72):events[row['event_id']]=row
            if row['event_id'] not in events:continue
            name=path.relative_to(ROOT).as_posix();objects[name]=git('show','HEAD:'+name)
            event=events[row['event_id']]
            target_limits[name]=event['target_utc'] if folder=='events' else shadow.target_deadline(event,frozen).isoformat()
            if folder=='events':
                source_commit=event['input_source_commit']
                if not re.fullmatch('[a-f0-9]{40}',source_commit) or event['input_source_path']!='data/hourly_research/ohlc.csv':raise ValueError('unsafe pinned event source')
                original=git('show',source_commit+':data/hourly_research/ohlc.csv')
                if hashlib.sha256(original).hexdigest()!=event['input_source_sha256']:raise ValueError('event input bytes changed')
                if shadow.frame_from_csv(original,shadow.utc(event['issued_at_utc']))!=event['frame']:raise ValueError('event frame source differs')
                if current_source.startswith(original):
                    prefixes[source_commit]={'source_path':'data/hourly_research/ohlc.csv','prefix_bytes':len(original),'sha256':event['input_source_sha256']}
                else:objects['input_sources/'+source_commit+'/ohlc.csv']=original
    objects['input_source_prefixes.json']=canon(prefixes)
    if sum(len(raw) for raw in objects.values())>retention.LIMIT:raise ValueError('uncompressed evidence exceeds cap')
    result['archive_target_deadlines']=target_limits
    objects['readiness.json']=canon(result);raw=package(objects,head,now);sha=hashlib.sha256(raw).hexdigest()
    (out/'bundle.zip').write_bytes(raw);(out/'bundle-sha256.txt').write_text(sha+'\n',encoding='utf-8')
    config=None
    if os.environ.get('DOGE_WORM_BUCKET'):
        config={'provider':'aws_s3_object_lock','bucket':os.environ['DOGE_WORM_BUCKET'],
                'bucket_owner_account_id':os.environ.get('DOGE_WORM_ACCOUNT',''),
                'collector_account_id':os.environ.get('DOGE_WORM_ACCOUNT',''),
                'required_until_utc':custody['required_until_utc'],'region':os.environ.get('AWS_REGION','ap-northeast-1'),
                'key':'doge/evidence/'+sha+'.zip'}
    if upload:
        if os.environ.get('DOGE_WORM_UPLOAD_ENABLED')!='true' or not config:raise ValueError('explicit existing-resource upload opt-in required')
        result['retention']=retention.upload(config,raw)
        verified=retention.utc(result['retention']['verified_at_utc'])
        result['per_record_pre_deadline_lock_verified']={name:verified<retention.utc(limit) for name,limit in target_limits.items()}
    else:result['retention']=retention.verify(None,sha)
    result['bundle_sha256']=sha;result['bundle_bytes']=len(raw)
    (out/'readiness.json').write_bytes(canon(result))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--upload',action='store_true');args=parser.parse_args()
    print(json.dumps(run(upload=args.upload),sort_keys=True))

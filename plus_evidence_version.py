"""Audit-gated registration of a separate evidence layer, never a trading model."""
import argparse,hashlib,json,os
from datetime import datetime,timezone,timedelta
from pathlib import Path

VERSION_ID='DOGE_PLUS_EVIDENCE_V1_0_20261012'
DATA_NAMESPACE='data/plus_evidence_v1_0'
SOURCE_NAMESPACE=DATA_NAMESPACE+'/sources'
REPORT='reports/DOGE_PLUS_EVIDENCE_V1_0.md'
CODE_FILES=('plus_evidence_version.py','supplemental_evidence_audit.py',
            'source_evidence_snapshot.py','evidence_retention.py',
            'forward_execution_evidence.py','audited_forward_refresh.py')

def code_hashes(root):
    root=Path(root)
    return {name:hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for name in CODE_FILES}

def utc(value):
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None or dt.utcoffset()!=timedelta(0):
        raise ValueError('explicit UTC audit clock required')
    return dt

def register(root,audit):
    root=Path(root)
    hashes=code_hashes(root)
    now=datetime.now(timezone.utc)
    if (audit.get('engineering_pass') is not True or audit.get('tests_passed',0)<=0
        or audit.get('tests_failed')!=0 or audit.get('real_orders_authorized') is not False
        or audit.get('overall')!='REVISE' or utc(audit['audited_at_utc'])>now):
        raise ValueError('audit is not qualified for evidence-only registration')
    if audit.get('code_sha256_lf')!=hashes:
        raise ValueError('code changed after audit')
    folder=root/'system/versions'
    path=folder/(VERSION_ID+'.json')
    if path.exists():
        record=json.loads(path.read_text(encoding='utf-8'))
        if record['code_sha256_lf']!=hashes or record['audit']!=audit:
            raise ValueError('version registration already fixed; use a new version')
    else:
        record={'schema_version':1,'version_id':VERSION_ID,'registered_at_utc':now.isoformat(),
            'layer':'supplementary_evidence_only','data_namespace':DATA_NAMESPACE,
            'source_namespace':SOURCE_NAMESPACE,'report_path':REPORT,
            'code_sha256_lf':hashes,'audit':audit,'overall':'REVISE',
            'new_prediction_model':False,'automatic_promotion':False,
            'real_orders_authorized':False,'dca_rules_changed':False,
            'legacy_versions':[
                {'version':'SPOT_PLUS_v0.1','role':'legacy_historical_research',
                 'data':'data/stage5/spot_plus','report':'reports/DOGE_SPOT_PLUS_DAILY.md'},
                {'version':'FORWARD_V1','role':'legacy_diagnostic_and_integrity_receipt',
                 'data':'data/stage5/forward_validation'},
                {'version':'FORWARD_V2','role':'frozen_official_forward_scores',
                 'data':'data/stage5/forward_validation_v2'},
                {'version':'SUPPLEMENTAL_PRE_RELEASE','role':'archived_pre_registration_evidence',
                 'data':'data/supplemental_evidence','sources':'data/evidence_snapshot',
                 'execution':'data/forward_execution_evidence'}],
            'separation_rules':['No historical scores or activations copied into the new namespace',
                'Old records remain in their original paths; current source copies are context only',
                'New evidence is not a replacement for frozen V2/P1/P2 official performance',
                'Model promotion and actual trading are never authorized by this registry']}
        folder.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8',newline='\n') as f:
            json.dump(record,f,ensure_ascii=False,indent=2); f.write('\n')
    pointer={'schema_version':1,'version_id':VERSION_ID,'registry_path':path.relative_to(root).as_posix(),
             'data_namespace':DATA_NAMESPACE,'layer':'supplementary_evidence_only',
             'new_prediction_model':False,'real_orders_authorized':False,
             'registry_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    target=root/'system/current_plus_evidence.json'
    temp=target.with_suffix('.json.tmp')
    temp.write_text(json.dumps(pointer,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temp,target)
    return record

def inspect_registration(root):
    root=Path(root)
    path=root/'system/versions'/(VERSION_ID+'.json')
    pointer=root/'system/current_plus_evidence.json'
    if not path.exists() or not pointer.exists():
        return 'not_registered'
    record=json.loads(path.read_text(encoding='utf-8'))
    current=json.loads(pointer.read_text(encoding='utf-8'))
    if (record['code_sha256_lf']!=code_hashes(root) or current['version_id']!=VERSION_ID
        or current['registry_sha256']!=hashlib.sha256(path.read_bytes()).hexdigest()
        or record['real_orders_authorized'] is not False):
        raise ValueError('registered evidence version integrity mismatch')
    return 'registered_evidence_only'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    p.add_argument('--register-audit',type=Path,required=True)
    args=p.parse_args()
    record=register(args.root,json.loads(args.register_audit.read_text(encoding='utf-8')))
    print(json.dumps({'status':'registered_evidence_only','version_id':record['version_id'],
                     'overall':'REVISE','new_prediction_model':False},ensure_ascii=False))
if __name__=='__main__':
    main()

"""Register reviewed exact paired-shadow bytes; never authorize trading."""
import argparse
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
import paired_forward_cohort as cohort
import paired_selection_v1 as selection


def register(root,audit):
    root=Path(root);now=datetime.now(timezone.utc)
    protocol=json.loads((root/cohort.POLICY).read_text(encoding='utf-8'))
    hashes=cohort.code_hashes(root)
    ph=hashlib.sha256((root/cohort.POLICY).read_bytes()).hexdigest()
    if (audit.get('engineering_pass') is not True or audit.get('independent_review_pass') is not True
        or audit.get('tests_failed')!=0 or audit.get('tests_passed',0)<1
        or audit.get('code_sha256_lf')!=hashes or audit.get('protocol_sha256')!=ph
        or audit.get('real_orders_authorized') is not False):raise ValueError('paired audit missing or code changed after review')
    if cohort.utc(audit['audited_at_utc'])>now or now>=cohort.utc(protocol['effective_from_utc']):
        raise ValueError('registration must precede prospective activation; create new version if late')
    record={'schema_version':1,'version_id':protocol['protocol_id'],
        'effective_from_utc':protocol['effective_from_utc'],'registered_at_utc':now.isoformat(),
        'layer':'paired_forecast_and_selective_paper_shadow','data_namespace':cohort.BASE,
        'code_sha256_lf':hashes,'protocol_sha256':ph,'selection_rule':selection.RULE,
        'engineering_audit_pass':True,'audit':audit,'forward_efficacy_status':'WAIT_FUTURE_DATA',
        'real_orders_authorized':False,'automatic_promotion':False,'dca_changed':False,
        'old_models_and_ledgers_preserved':True}
    path=root/cohort.SYSTEM;path.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode()
    if path.exists():
        old=json.loads(path.read_text(encoding='utf-8'))
        if old['code_sha256_lf']!=hashes or old['audit']!=audit:raise ValueError('registered paired version immutable; use a new version')
        raw=path.read_bytes();record=old
    else:
        with path.open('xb') as f:f.write(raw)
    pointer={'schema_version':1,'version_id':protocol['protocol_id'],'registry_path':cohort.SYSTEM,
        'registry_sha256':hashlib.sha256(raw).hexdigest(),'layer':record['layer'],
        'forward_efficacy_status':'WAIT_FUTURE_DATA','real_orders_authorized':False,
        'authorized_action':'NO_TRADE'}
    target=root/'system/current_paired_shadow.json'
    target.write_bytes((json.dumps(pointer,ensure_ascii=False,indent=2)+'\n').encode())
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--audit',type=Path,required=True)
    args=parser.parse_args()
    r=register(cohort.ROOT,json.loads(args.audit.read_text(encoding='utf-8')))
    print(json.dumps({'status':'registered_paired_shadow','version_id':r['version_id'],'forward_efficacy_status':r['forward_efficacy_status']}))

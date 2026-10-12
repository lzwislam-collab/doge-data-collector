"""Register independent audited P2 paper engine; no real trading permission."""
import argparse
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
import p2_paired_execution_v3_1 as p


def register(root,audit):
    root=Path(root);now=datetime.now(timezone.utc);policy=json.loads((root/p.POLICY).read_text(encoding='utf-8'))
    policy_bytes=(root/p.POLICY).read_bytes()
    if b'\r' in policy_bytes:raise ValueError('P2 protocol must use LF bytes before hashing/deployment')
    hashes=p.code_hashes(root);ph=hashlib.sha256(policy_bytes).hexdigest()
    if (audit.get('engineering_pass') is not True or audit.get('independent_review_pass') is not True
        or audit.get('tests_failed')!=0 or audit.get('tests_passed',0)<1
        or audit.get('code_sha256_lf')!=hashes or audit.get('protocol_sha256')!=ph
        or audit.get('real_orders_authorized') is not False):raise ValueError('P2 audit missing or reviewed bytes changed')
    if now>=p.utc(policy['effective_from_utc']) or p.utc(audit['audited_at_utc'])>now:raise ValueError('P2 registration must precede activation')
    record={'schema_version':3,'version_id':policy['protocol_id'],'effective_from_utc':policy['effective_from_utc'],
        'registered_at_utc':now.isoformat(),'layer':'independent_paired_decision_spot_execution_paper',
        'code_sha256_lf':hashes,'protocol_sha256':ph,'data_namespace':p.BASE,
        'engineering_audit_pass':True,'audit':audit,'forward_profit_status':'WAIT_FUTURE_INTERVALS',
        'real_orders_authorized':False,'no_dca_changes':True,'original_p2_v1_preserved':True,'original_p2_v2_preserved':True,
        'cost_assumptions':{'fee_each_side':p.FEE,'extra_slippage_each_side':p.SLIP,'account_fee_verified':False},
        'continuous_drawdown_verified':False,'no_auto_profit_claim':True}
    path=root/p.SYSTEM;path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        old=json.loads(path.read_text(encoding='utf-8'))
        if old['audit']!=audit or old['code_sha256_lf']!=hashes:raise ValueError('P2 registered version fixed; create new version')
        record=old;raw=path.read_bytes()
    else:
        raw=(json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode()
        with path.open('xb') as f:f.write(raw)
    pointer={'schema_version':1,'version_id':policy['protocol_id'],'registry_path':p.SYSTEM,
        'registry_sha256':hashlib.sha256(raw).hexdigest(),'authorized_action':'NO_TRADE',
        'real_orders_authorized':False,'forward_profit_status':'WAIT_FUTURE_INTERVALS'}
    (root/'system/current_p2_execution_shadow_v3_1.json').write_bytes((json.dumps(pointer,indent=2)+'\n').encode())
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--audit',type=Path,required=True);a=parser.parse_args()
    r=register(p.ROOT,json.loads(a.audit.read_text(encoding='utf-8')));print(json.dumps({'status':'registered_p2_paper','version_id':r['version_id']}))

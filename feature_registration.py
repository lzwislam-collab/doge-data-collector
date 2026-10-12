"""Register exact future feature experiment bytes; no promotion or trading."""
import argparse,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
import feature_ablation as a
def register(root,audit):
    raw=(root/a.POLICY).read_bytes();p=json.loads(raw)
    if b'\r' in raw:raise ValueError('protocol must use LF exact deployment bytes')
    hashes={f:hashlib.sha256((root/f).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for f in a.code_files()}
    if (audit.get('independent_review_pass') is not True or audit.get('tests_failed')!=0 or audit.get('tests_passed',0)<1
        or audit.get('code_sha256_lf')!=hashes or audit.get('protocol_sha256')!=hashlib.sha256(raw).hexdigest()):raise ValueError('feature audit missing or bytes changed')
    if datetime.now(timezone.utc)>=a.utc(p['effective_from_utc']):raise ValueError('new future activation required')
    record={'schema_version':1,'version_id':p['protocol_id'],'effective_from_utc':p['effective_from_utc'],
        'registered_at_utc':datetime.now(timezone.utc).isoformat(),'code_sha256_lf':hashes,'protocol_sha256':hashlib.sha256(raw).hexdigest(),
        'audit':audit,'real_orders_authorized':False,'validated_70pct':False,'profitability_validated':False,
        'status':'registered_isolated_feature_research_only'}
    path=root/a.SYSTEM;path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():raise ValueError('registered feature version fixed; new version required')
    body=(json.dumps(record,indent=2)+'\n').encode();path.write_bytes(body)
    pointer={'version_id':p['protocol_id'],'registry_path':a.SYSTEM,'registry_sha256':hashlib.sha256(body).hexdigest(),
        'forecast_gain_status':'WAIT_FUTURE_EVENTS','paper_profit_status':'WAIT_FUTURE_EVENTS','real_orders_authorized':False}
    (root/'system/current_feature_research.json').write_bytes((json.dumps(pointer,indent=2)+'\n').encode());return record
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--audit',type=Path,required=True);args=parser.parse_args()
    r=register(a.ROOT,json.loads(args.audit.read_bytes()));print(json.dumps({'status':r['status'],'version_id':r['version_id']}))

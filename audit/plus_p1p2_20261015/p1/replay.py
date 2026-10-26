import hashlib, json, sys
from pathlib import Path
from datetime import datetime, timezone
root=Path.cwd()
sys.path.insert(0,str(root))
import plus_p1_audit as quality
import phase1_research as engine
out=root/'audit/plus_p1p2_20261015/p1'
now=datetime.now(timezone.utc)
frame,hourly=quality.market(root/'data/hourly_research/ohlc.csv',3600000,now)
_,daily=quality.market(root/'data/history/daily.csv',86400000,now)
policy=json.loads((root/'config/factor_eligibility_v1.json').read_bytes())
counts=quality.frozen.catalog(policy['legacy36'])
source={'audited_at_utc':now.isoformat(),'hourly':hourly,'daily':daily,'catalog_counts':counts,'catalog_source_sha256':quality.frozen.sha((root/'config/factor_eligibility_v1.json').read_bytes()),'production_changed':False}
(out/'source_audit.json').write_bytes(engine.encode(source))
research_policy=json.loads((root/'config/phase1_research_v1.json').read_bytes())
result=engine.experiment(frame,research_policy)
result.update(evidence_kind='NEW_PLUS_DESCRIPTIVE_REPLAY_NOT_FROZEN_PHASE1_IDENTITY',audited_at_utc=now.isoformat(),source_sha256=hourly['sha256'],policy=research_policy,code_sha256_lf={name:quality.frozen.sha((root/name).read_bytes().replace(b'\r\n',b'\n')) for name in ('plus_p1_audit.py','phase1_research.py','phase1_research_audit.py')},production_changed=False)
(out/'phase1_replay.json').write_bytes(engine.encode(result))
print(json.dumps({'counts':counts,'hourly':hourly,'daily':daily,'horizons':{h:{k:v[k] for k in ('nonoverlap_n','descriptive_metrics','descriptive_ablation_gain')} for h,v in result['horizons'].items()}},ensure_ascii=False))

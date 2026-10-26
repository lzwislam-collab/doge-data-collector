import json,sys
from pathlib import Path
from datetime import datetime,timezone
root=Path.cwd();sys.path.insert(0,str(root))
import numpy, scipy, sklearn
import plus_p1_audit as quality
import phase1_research as prior
import phase2_research_v2 as engine
out=root/'audit/plus_p1p2_20261015/p1'
assert (numpy.__version__,scipy.__version__,sklearn.__version__)==('2.3.5','1.16.3','1.7.2')
frame,source=quality.market(root/'data/hourly_research/ohlc.csv',3600000,datetime.now(timezone.utc))
p=json.loads((root/'config/phase2_research_v2.json').read_bytes())
result=engine.experiment(frame,p)
result.update(evidence_kind='NEW_PLUS_DESCRIPTIVE_REPLAY_NOT_FROZEN_PHASE2_IDENTITY',source_sha256=source['sha256'],policy=p,production_changed=False,actual_forward_predictions_issued=0)
(out/'phase2_v2_replay.json').write_bytes(prior.encode(result))
print(json.dumps({'fits': result['actual_fits'], 'horizons': {h: hr['selected_probability_metrics'] for h, hr in result['horizons'].items()}}))

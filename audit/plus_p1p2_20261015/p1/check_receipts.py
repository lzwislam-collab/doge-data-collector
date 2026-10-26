import json,sys
from pathlib import Path
from datetime import datetime,timezone
root=Path.cwd();sys.path.insert(0,str(root))
import plus_p1_audit as audit
import phase1_research as engine
out=root/'audit/plus_p1p2_20261015/p1'
frame,report=audit.market(root/'data/hourly_research/ohlc.csv',3600000,datetime.now(timezone.utc))
result={}
for h in (1,24):
    rows=engine.features(frame,h,'receipt_time')
    known=[r for r in rows if r is not None]
    result[str(h)]={'total_bars':len(rows),'receipt_available_feature_anchors':len(known),'earliest_anchor_utc':datetime.fromtimestamp(known[0]['anchor_ms']/1000,timezone.utc).isoformat() if known else None,'latest_anchor_utc':datetime.fromtimestamp(known[-1]['anchor_ms']/1000,timezone.utc).isoformat() if known else None,'qualification_is_independent_public_witness':False}
(out/'receipt_availability.json').write_bytes(engine.encode(result))
print(json.dumps(result))

import gzip,json,hashlib
from pathlib import Path
p=Path('audit/plus_p1p2_20261015/p1/phase2_v2_replay.json')
raw=p.read_bytes();r=json.loads(raw)
compressed=gzip.compress(raw,mtime=0)
p.with_suffix('.json.gz').write_bytes(compressed)
summary={k:v for k,v in r.items() if k!='horizons'}
summary['full_artifact']={'path':'phase2_v2_replay.json.gz','uncompressed_sha256':hashlib.sha256(raw).hexdigest(),'compressed_sha256':hashlib.sha256(compressed).hexdigest(),'uncompressed_bytes':len(raw),'compressed_bytes':len(compressed)}
summary['horizons']={h:{k:v for k,v in hr.items() if k in ('test_events','nonoverlap_n','due_test_anchors','missing_feature_anchors','selected_metrics','selected_probability_metrics','probability_baselines','same_event_baselines','factor_stability','ablation','regime_metrics')} for h,hr in r['horizons'].items()}
Path('audit/plus_p1p2_20261015/p1/phase2_v2_summary.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'keys':{h:list(hr) for h,hr in r['horizons'].items()},'metrics':{h:hr['selected_probability_metrics'] for h,hr in r['horizons'].items()},'compressed_bytes':len(compressed)}))
p.unlink()

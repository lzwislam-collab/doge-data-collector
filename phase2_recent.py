"""Fixed V2 architecture, newly trained weights. No issuer or production writer."""
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import phase1_research as prior
import phase1_research_audit as audit
import phase2_factor_library as library
import phase2_models_v2 as models
import learned_shadow_bundle as frozen

ROOT=Path(__file__).resolve().parent
SOURCE='data/hourly_research/ohlc.csv'
EXPERIMENT='5bba9522aa1fa9fb55a432fe0bf9706a9300476915628727d67bdbbd373f987a'
DRAFT='research/phase2_v2/'+EXPERIMENT+'/future_protocol_draft.json'
H=3600000
COLS=list(range(5,17))
SETTING={'model':'shrinkage_lda','regularization':.3}

def train(frame,now):
    if type(now) is not int or now<0:raise ValueError('integer training UTC milliseconds required')
    t=frame['times'];n=len(t)
    if n<4169 or len(set(t))!=n or any(type(q) is not int or q%H for q in t) or any(b-a!=H for a,b in zip(t,t[1:])):
        raise ValueError('insufficient aligned hourly source')
    for s in audit.SYMBOLS:
        if s not in frame:raise ValueError('three assets required')
        for key in ('close','volume','seen'):
            if len(frame[s][key])!=n:raise ValueError('unaligned assets')
        for i,seen in enumerate(frame[s]['seen']):
            if type(seen) is not int or not t[i]+H<=seen<=now:raise ValueError('unknown or future receipt')
        c=np.asarray(frame[s]['close']);v=np.asarray(frame[s]['volume'])
        if not np.isfinite(c).all() or (c<=0).any() or not np.isfinite(v).all() or (v<0).any():
            raise ValueError('invalid source prices or volume')
    # Downloads are known NOW, not at historical anchors. Never claim historical PIT.
    rows=[r for r in library.rows(frame,1,'candle_close_research') if r is not None][-4096:]
    if len(rows)!=4096:raise ValueError('4096 usable matured training labels required')
    available=max(frame['DOGE-USDT']['seen'][r['index']+1] for r in rows)
    if available>now:raise ValueError('future training label')
    # Explicit input quantization also applies to this version's future inference.
    # Avoid claiming bit-identical transcendental results across CPU libraries.
    x=[[round(r['x'][j],9) for j in COLS] for r in rows];y=[r['y'] for r in rows]
    model={'base':models.fit_base(SETTING,x,y,{}),'calibration':'raw',
           'probability_calibration_validated':False}
    return {'schema':'DOGE_PHASE2_RECENT_RETRAIN_V1','hypothesis':'1_raw__drop_doge_momentum',
        'setting':SETTING.copy(),'columns':COLS.copy(),'feature_decimal_places':9,
        'training_n':len(rows),'trained_at_ms':now,
        'first_training_anchor_ms':rows[0]['anchor_ms'],'last_training_anchor_ms':rows[-1]['anchor_ms'],
        'latest_label_end_ms':max(r['label_end_ms'] for r in rows),'latest_label_available_ms':available,
        'max_training_data_age_days':14,'model':model,
        'model_sha256':audit.sha(prior.encode(model)),
        'training_matrix_sha256':audit.sha(prior.encode({'x':x,'y':y})),
        'training_volatility_median':round(float(np.median([r['x'][6] for r in rows])),12),
        'historical_pit_validated':False,'validation_of_fresh_weights_completed':False,
        'auto_issuance_authorized':False,'production_promotion_authorized':False,
        'real_orders_authorized':False,'validated_70pct':False,
        'formal_earliest_effective_utc':'2026-10-20T00:00:00Z',
        'status':'RECENT_WEIGHTS_PENDING_NEW_PUBLIC_PROTOCOL_AND_RUNTIME'}

def check_age(record,now):
    if type(now) is not int or now<record['trained_at_ms']:raise ValueError('inference precedes training')
    if now>record['latest_label_end_ms']+14*24*H:raise ValueError('stale training data; new registration required')
    if record['max_training_data_age_days']!=14:raise ValueError('age policy altered')

def build(commit,clock):
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('exact source Git commit required')
    now=int(audit.utc(clock).timestamp()*1000)
    raw=subprocess.check_output(['git','-c','safe.directory=*','show',commit+':'+SOURCE],cwd=ROOT)
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'ohlc.csv';path.write_bytes(raw)
        frame,report=audit.market(path,H,datetime.fromtimestamp(now/1000,timezone.utc))
    if set(report['symbols'])!=set(audit.SYMBOLS):raise ValueError('missing source asset')
    if any(v['bars']!=len(frame['times']) for v in report['symbols'].values()):raise ValueError('cross-asset history mismatch')
    draft=json.loads((ROOT/DRAFT).read_bytes())
    if len(draft['candidates'])!=1 or draft['candidates'][0]['hypothesis']!='1_raw__drop_doge_momentum' or not draft['candidates'][0]['validation_only_screen_passed']:
        raise ValueError('fixed accepted V2 architecture required')
    _,package,_=frozen.guard()
    record=train(frame,now)
    record.update(source_commit=commit,source_path=SOURCE,source_audit=report,
        architecture_experiment_id=EXPERIMENT,architecture_draft_sha256=audit.sha((ROOT/DRAFT).read_bytes()),
        historical_prototype_sha256=draft['candidates'][0]['model_sha256'],
        frozen_original18_package_sha256=audit.sha(prior.encode(package)))
    record['recent_identity']=audit.sha(prior.encode(record))
    return record

def main():
    p=argparse.ArgumentParser();p.add_argument('--source-commit',required=True);p.add_argument('--trained-at',required=True)
    p.add_argument('--out',required=True);p.add_argument('--verify',action='store_true');a=p.parse_args()
    result=build(a.source_commit,a.trained_at);path=Path(a.out);raw=prior.encode(result)
    if a.verify:
        if path.read_bytes()!=raw:raise ValueError('registered recent training replay differs')
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(raw)
    print(json.dumps({k:result[k] for k in ('recent_identity','training_n','latest_label_end_ms','model_sha256','status')}))

if __name__=='__main__':main()

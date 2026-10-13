"""Budgeted causal historical research, isolated from all production issuers.

FreqAI-style rolling windows, Qlib-style recorded artifacts, RD-Agent-style
factor ablations. No framework migration, historical PIT claim or promotion.
"""
import argparse
import hashlib
import json
import math
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import phase1_research_audit as audit

ROOT=Path(__file__).resolve().parent
GROUPS={'doge_momentum':[0,1,2], 'doge_volatility':[3,4], 'doge_volume':[5], 'btc_context':[6,7,8]}
FEATURE_NAMES=['doge_ret_1h','doge_ret_4h','doge_ret_24h','doge_vol_24h','doge_range_24h',
               'doge_volume_ratio_24h','btc_ret_1h','btc_ret_4h','btc_ret_24h']


def variants():
    groups=list(GROUPS)
    return {'full':groups,**{'drop_'+g:[x for x in groups if x!=g] for g in groups},
            **{'only_'+g:[g] for g in groups}}


def validate_policy(p):
    for key in ('train_bars','validation_bars','test_bars','step_bars','max_folds','iterations'):
        if type(p[key]) is not int or p[key]<1:raise ValueError('positive integer '+key+' required')
    if type(p['embargo_bars']) is not int or p['embargo_bars']<1 or p['step_bars']<p['test_bars']:
        raise ValueError('positive embargo and nonoverlapping test windows required')
    for key in ('learning_rate','l2'):
        if not math.isfinite(p[key]) or p[key]<=0:raise ValueError('invalid optimizer policy')
    if not p['horizons'] or any(type(h) is not int or h not in (1,24) for h in p['horizons']):
        raise ValueError('only distinct 1H/24H horizons supported')
    if len(set(p['horizons']))!=len(p['horizons']):raise ValueError('duplicate horizons')


def splits(n,h,p):
    validate_policy(p)
    # Equality is excluded: a label must be strictly mature before embargo.
    gap=h+p['embargo_bars']+1
    width=p['train_bars']+p['validation_bars']+p['test_bars']+2*gap
    if n-24-h<width:return []
    count=min(p['max_folds'],1+(n-24-h-width)//p['step_bars'])
    start=n-h-width-(count-1)*p['step_bars']
    answer=[]
    for k in range(count):
        a=start+k*p['step_bars'];b=a+p['train_bars'];v=b+gap
        t=v+p['validation_bars']+gap
        answer.append({'train':list(range(a,b)),'validation':list(range(v,v+p['validation_bars'])),
                       'test':list(range(t,t+p['test_bars']))})
    return answer


def features(frame,h,mode):
    if mode not in ('candle_close_research','receipt_time'):raise ValueError('unknown availability mode')
    t=frame['times'];n=len(t)
    d=np.asarray(frame['DOGE-USDT']['close'],dtype=float);b=np.asarray(frame['BTC-USDT']['close'],dtype=float)
    v=np.asarray(frame['DOGE-USDT']['volume'],dtype=float)
    if any(len(frame[s][key])!=n for s in ('DOGE-USDT','BTC-USDT') for key in ('close','volume','seen')):
        raise ValueError('unaligned source arrays')
    if n and (not np.isfinite(d).all() or not np.isfinite(b).all() or not np.isfinite(v).all()
              or (d<=0).any() or (b<=0).any() or (v<0).any()):raise ValueError('invalid source values')
    if len(set(t))!=n or any(y-x!=3600000 for x,y in zip(t,t[1:])):raise ValueError('source timestamp gap/duplicate')
    rows=[None]*n
    ld=np.log(d);lb=np.log(b)
    for i in range(max(24,h),n-h):
        anchor=t[i]+3600000
        if mode=='receipt_time' and any(frame[s]['seen'][j] is None or frame[s]['seen'][j]>anchor
                for s in ('DOGE-USDT','BTC-USDT') for j in range(i-24,i+1)):
            continue
        returns=np.diff(ld[i-24:i+1]);prior_volume=float(np.mean(v[i-24:i]))
        if prior_volume<=0:continue
        x=[ld[i]-ld[i-1],ld[i]-ld[i-4],ld[i]-ld[i-24],float(np.std(returns)),
           float(np.max(ld[i-24:i+1])-np.min(ld[i-24:i+1])),v[i]/prior_volume-1,
           lb[i]-lb[i-1],lb[i]-lb[i-4],lb[i]-lb[i-24]]
        rows[i]={'index':i,'event_id':str(anchor)+'_'+str(h),'anchor_ms':anchor,
                 'label_end_ms':t[i+h]+3600000,'x':[float(q) for q in x],
                 'label_available_ms':max(t[i+h]+3600000,frame['DOGE-USDT']['seen'][i+h] or 10**30)
                                     if mode=='receipt_time' else t[i+h]+3600000,
                 'y':int(d[i+h]>d[i]),'frozen_direction':int(d[i]<=d[i-h]),
                 'btc_direction':int(b[i]>b[i-h]),'regime':'UP' if d[i]>d[i-24] else 'DOWN'}
    return rows


def fit(x,y,p):
    x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float)
    if x.ndim!=2 or not len(x) or len(x)!=len(y) or not np.isfinite(x).all() or not np.isin(y,[0,1]).all():
        raise ValueError('finite nonempty training inputs required')
    mean=x.mean(axis=0);scale=x.std(axis=0);scale=np.where(scale<1e-12,1.,scale)
    z=np.clip((x-mean)/scale,-10,10);w=np.zeros(x.shape[1]);intercept=0.
    for _ in range(p['iterations']):
        pred=1/(1+np.exp(-np.clip(z@w+intercept,-30,30)));error=pred-y
        w-=p['learning_rate']*(z.T@error/len(x)+p['l2']*w)
        intercept-=p['learning_rate']*float(error.mean())
    return {'mean':mean.tolist(),'scale':scale.tolist(),'weights':w.tolist(),'intercept':intercept,
            'n_train':len(x),'probability_calibration_validated':False}


def predict(model,x):
    z=np.clip((np.asarray(x,dtype=float)-model['mean'])/model['scale'],-10,10)
    return (1/(1+np.exp(-np.clip(z@np.asarray(model['weights'])+model['intercept'],-30,30)))).tolist()


def metrics(events,name):
    if not events:return {'n':0,'accuracy':None,'balanced_accuracy':None,'brier':None}
    y=[e['y'] for e in events];p=[e['candidate_p'][name] for e in events];pred=[int(q>=.5) for q in p]
    recalls=[sum(a==c and b==c for a,b in zip(pred,y))/y.count(c) if c in y else None for c in (0,1)]
    return {'n':len(y),'accuracy':sum(a==b for a,b in zip(pred,y))/len(y),
            'balanced_accuracy':sum(recalls)/2 if all(q is not None for q in recalls) else None,
            'brier':sum((a-b)**2 for a,b in zip(p,y))/len(y)}


def majority_probability(frame,index,h,mode):
    d=frame['DOGE-USDT']['close'];seen=frame['DOGE-USDT']['seen'];anchor=frame['times'][index]+3600000
    prior=[int(d[q+h]>d[q]) for q in range(index-h+1)
           if mode=='candle_close_research' or
           (seen[q] is not None and seen[q+h] is not None and max(seen[q],seen[q+h])<=anchor)]
    return (1+sum(prior))/(2+len(prior))


def experiment(frame,p):
    validate_policy(p)
    result={'status':'HISTORICAL_RESEARCH_ONLY','horizons':{},'promotion_authorized':False,
            'historical_pit_validated':False,'validated_70pct':False,'feature_names':FEATURE_NAMES,
            'test_labels_used_for_selection':False,'cost_net_return_validated':False}
    for h in p['horizons']:
        prepared=features(frame,h,p['availability_mode']);folds=[]
        for k,s in enumerate(splits(len(prepared),h,p)):
            usable={key:[prepared[i] for i in indices if prepared[i] is not None] for key,indices in s.items()}
            validation_clock=frame['times'][s['validation'][0]]+3600000
            test_clock=frame['times'][s['test'][0]]+3600000
            embargo=p['embargo_bars']*3600000
            usable['train']=[r for r in usable['train'] if r['label_available_ms']+embargo<validation_clock]
            usable['validation']=[r for r in usable['validation'] if r['label_available_ms']+embargo<test_clock]
            item={'fold':k,'test_start_index':s['test'][0],
                  'windows':{key:{'start_index':ix[0],'end_index':ix[-1]} for key,ix in s.items()},
                  'due_test_anchors':len(s['test']),'missing_feature_anchors':len(s['test'])-len(usable['test']),
                  'models':{},'events':[],'validation':{}}
            if len(usable['train'])<p['train_bars'] or len(usable['validation'])<p['validation_bars']:
                item['status']='WAIT_AVAILABLE_TRAINING_DATA';folds.append(item);continue
            train=usable['train'];tests=usable['test'];valid=usable['validation']
            # Only train labels available before validation; all horizons separated.
            if max(r['label_end_ms'] for r in train)+p['embargo_bars']*3600000>=valid[0]['anchor_ms']:
                raise ValueError('training label leakage')
            if valid[-1]['label_end_ms']+p['embargo_bars']*3600000>=(frame['times'][s['test'][0]]+3600000):
                raise ValueError('validation label leakage')
            outputs={};voutputs={}
            for name,groups in variants().items():
                columns=[i for g in groups for i in GROUPS[g]]
                model=fit([[r['x'][i] for i in columns] for r in train],[r['y'] for r in train],p)
                model['columns']=columns;item['models'][name]=model
                outputs[name]=predict(model,[[r['x'][i] for i in columns] for r in tests]) if tests else []
                voutputs[name]=predict(model,[[r['x'][i] for i in columns] for r in valid])
            # Expanding majority matches frozen production helper's definition;
            # include only matured labels, never the fold's future test labels.
            for j,r in enumerate(tests):
                item['events'].append({**{q:r[q] for q in ('event_id','anchor_ms','label_end_ms','y','regime','frozen_direction','btc_direction')},
                    'candidate_p':{name:values[j] for name,values in outputs.items()},
                    'majority_p':majority_probability(frame,r['index'],h,p['availability_mode']),'frozen_p':None,
                    'net_return':None,'cost_evidence_status':'WAIT_SAME_EVENT_BID_ASK_DEPTH_FEES_SLIPPAGE',
                    'evidence_kind':'retrospective_candle_close_not_issue_quote'})
            vevents=[{'y':r['y'],'candidate_p':{name:values[j] for name,values in voutputs.items()}} for j,r in enumerate(valid)]
            item['validation']={name:metrics(vevents,name) for name in variants()}
            item['status']='RESEARCH_ONLY';folds.append(item)
        events=[r for f in folds for r in f['events']]
        if len({e['event_id'] for e in events})!=len(events):raise ValueError('duplicate test events')
        # Whole-cohort outcome-blind fixed phase; reserve full target plus one-hour gap.
        non=[];last=-1
        for e in events:
            if e['anchor_ms']>last:non.append(e);last=e['label_end_ms']
        all_metrics={name:metrics(non,name) for name in variants()}
        baseline_functions={'frozen_production_rule':lambda e:e['frozen_direction'],
                                  'btc_momentum':lambda e:e['btc_direction'],
                                  'majority':lambda e:int(e['majority_p']>=.5),
                                  'always_up':lambda e:1,'always_down':lambda e:0}
        baselines={name:{'n':len(non),'accuracy':sum(fn(e)==e['y'] for e in non)/len(non) if non else None}
                   for name,fn in baseline_functions.items()}
        pairs={}
        for name,fn in baseline_functions.items():
            wins=sum(int(e['candidate_p']['full']>=.5)==e['y'] and fn(e)!=e['y'] for e in non)
            losses=sum(int(e['candidate_p']['full']>=.5)!=e['y'] and fn(e)==e['y'] for e in non)
            pairs[name]={'wins':wins,'losses':losses,'ties':len(non)-wins-losses,'inference_validated':False}
        regimes={state:{name:metrics([e for e in non if e['regime']==state],name) for name in variants()}
                 for state in ('UP','DOWN')}
        gain={g:{'accuracy_full_minus_drop':all_metrics['full']['accuracy']-all_metrics['drop_'+g]['accuracy'],
                 'brier_drop_minus_full':all_metrics['drop_'+g]['brier']-all_metrics['full']['brier']}
              for g in GROUPS} if non else {}
        result['horizons'][str(h)]={'folds':folds,'test_events':len(events),'nonoverlap_n':len(non),
             'descriptive_metrics':all_metrics,'same_event_baselines':baselines,'descriptive_ablation_gain':gain,
             'paired_comparisons':pairs,'regime_metrics':regimes,
             'inference_validated':False,'status':'RESEARCH_ONLY' if events else 'WAIT_AVAILABLE_DATA'}
    return result


def encode(value):return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def register(directory,record):
    directory.mkdir(parents=True,exist_ok=True);identity=record['experiment_id']
    if not identity or any(c not in '0123456789abcdef' for c in identity):raise ValueError('invalid identity')
    path=directory/(identity+'.json');raw=encode(record)
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('immutable candidate registration conflict')
        return path
    with path.open('xb') as stream:stream.write(raw)
    return path


def check_output(root,out,freeze):
    target=out.resolve()
    if target==root.resolve() or any((root/path).resolve().is_relative_to(target)
                                    for path in freeze['assets']):
        raise ValueError('output would overlap protected assets')


def run(root,out,policy_path,freeze_path):
    policy_raw=policy_path.read_bytes();p=json.loads(policy_raw)
    if np.__version__!=p['numpy_version']:raise ValueError('frozen NumPy runtime required')
    freeze=json.loads(freeze_path.read_bytes());check_output(root,out,freeze)
    check_output(ROOT,out,freeze);before=audit.verify(root,freeze)
    frame,source_audit=audit.audit(root,datetime.now(timezone.utc))
    result=experiment(frame,p)
    code={name:audit.sha((ROOT/name).read_bytes().replace(b'\r\n',b'\n')) for name in
          ('phase1_research.py','phase1_research_audit.py','phase1_research_snapshot.py')}
    identity={'code_sha256':code,'policy_sha256':audit.sha(policy_raw),
              'market_input_sha256':source_audit['hourly']['sha256'],
              'freeze_sha256':audit.sha(freeze_path.read_bytes()),'numpy_version':np.__version__,
              'python_minor':'.'.join(platform.python_version_tuple()[:2])}
    eid=audit.sha(encode(identity));result['experiment_id']=eid;result['identity']=identity
    result['policy']=p;result['result_sha256']=audit.sha(encode(result))
    record={'experiment_id':eid,'identity':identity,'result_sha256':result['result_sha256'],
            'status':'RESEARCH_ONLY','auto_promotion_authorized':False,
            'real_orders_authorized':False,'next_stage':'NEW_FUTURE_PUBLIC_PREREGISTRATION_AND_PROSPECTIVE_EVIDENCE',
            'prospective_evaluator':'factor_evaluation_evidence_v1.py (existing registered groups only)',
            'economic_evaluator':'p2_economic_acceptance.py', 'error_review':'stage5_error_review.py'}
    record['candidate_family']={name:{'groups':groups,'status':'RESEARCH_ONLY',
        'fold_model_sha256':{h:{str(f['fold']):audit.sha(encode(f['models'][name]))
                              for f in hr['folds'] if name in f['models']} for h,hr in result['horizons'].items()},
        'production_promotion_authorized':False} for name,groups in variants().items()}
    out.mkdir(parents=True,exist_ok=True)
    register(out/'candidates',record)
    after=audit.verify(root,freeze)
    (out/'research.json').write_bytes(encode(result));(out/'source_audit.json').write_bytes(encode(source_audit))
    receipt={'experiment_id':eid,'source_commit':freeze['source_commit'],'run_head':subprocess.check_output(
             ['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'before':before,'after':after,
             'research_sha256':audit.sha((out/'research.json').read_bytes()),
             'generated_at_utc':datetime.now(timezone.utc).isoformat(),
             'cloud_deployment_verified':False,'production_model_changed':False}
    (out/'receipt.json').write_bytes(encode(receipt));return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--source',type=Path,default=ROOT)
    parser.add_argument('--policy',type=Path,default=ROOT/'config/phase1_research_v1.json')
    parser.add_argument('--freeze',type=Path,default=ROOT/'research/phase1_v1/frozen_assets.json')
    args=parser.parse_args()
    if args.out.resolve()==ROOT or args.out.resolve().is_relative_to(ROOT/'data'):
        raise ValueError('output must not target existing data assets')
    print(json.dumps(run(args.source,args.out,args.policy,args.freeze),sort_keys=True))

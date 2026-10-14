"""Sequential V2: fit/calibrate, causal stability, validation-only future drafts."""
import argparse,json,math,platform,subprocess
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import phase1_research as prior
import phase1_research_audit as audit
import phase2_research as v1
import phase2_factor_library as library
import phase2_models_v2 as models
import phase2_protocol_v2 as protocol
import learned_shadow_bundle as frozen

ROOT=Path(__file__).resolve().parent;sha=v1.sha;encode=prior.encode;STATES=protocol.STATES
SETTINGS=[{'model':'logistic','regularization':.3},{'model':'shrinkage_lda','regularization':.3},
          {'model':'gradient_boosting','depth':2},{'model':'gradient_boosting','depth':3}]
CODE=('phase2_research_v2.py','phase2_models_v2.py','phase2_protocol_v2.py','phase2_research.py',
      'phase2_factor_library.py','phase1_research.py','phase1_research_audit.py','phase1_research_snapshot.py')

def family():
 return {str(i)+'_'+cal+'__'+name:{**setting,'calibration':cal,'variant':name,'columns':[j for g in groups for j in library.GROUPS[g]]}
   for i,setting in enumerate(SETTINGS) for cal in ('raw','sigmoid') for name,groups in library.variants().items()}

def inner(train,h,p):
 n=p['calibration_bars']
 if type(n) is not int or n<10 or n>=len(train):raise ValueError('positive disjoint calibration window required')
 calibration=train[-n:];before=calibration[0]['anchor_ms'];embargo=p['embargo_bars']*3600000
 fit=[r for r in train[:-n] if r['label_available_ms']+embargo<before]
 if len(fit)<10:raise ValueError('insufficient purged base training')
 return fit,calibration

def probability_metrics(y,probs):
 if not len(y):return {'n':0,'brier':None,'logloss':None,'ece':None,'accuracy':None}
 y=np.asarray(y,dtype=float);q=np.asarray(probs,dtype=float)
 if len(q)!=len(y) or not np.isfinite(q).all() or ((q<0)|(q>1)).any():raise ValueError('invalid probabilities')
 brier=float(np.mean((q-y)**2));clip=np.clip(q,1e-12,1-1e-12);ece=0
 bins=np.minimum((q*10).astype(int),9)
 for b in range(10):
  mask=bins==b
  if mask.any():ece+=float(mask.mean()*abs(q[mask].mean()-y[mask].mean()))
 return prior.canonical({'n':len(y),'brier':brier,'logloss':float(np.mean(-y*np.log(clip)-(1-y)*np.log(1-clip))),
   'ece':ece,'accuracy':float(np.mean((q>=.5)==y))})

def majority_values(frame,rows,h,mode):
 # Receipt mode uses original explicit availability guard, never backdates downloads.
 if mode=='receipt_time':return [prior.majority_probability(frame,r['index'],h,mode) for r in rows]
 d=frame['DOGE-USDT']['close'];outcomes=np.asarray([int(d[i+h]>d[i]) for i in range(len(d)-h)])
 prefix=np.concatenate([[0],np.cumsum(outcomes)])
 return [round(float((1+prefix[r['index']-h+1])/(2+r['index']-h+1)),12) for r in rows]

def experiment(frame,p):
 prior.validate_policy(p)
 if type(p['max_fits']) is not int or not 1<=p['max_fits']<=1200 or p['max_folds']>6:raise ValueError('bounded fixed budget required')
 for key in ('tree_estimators','tree_min_leaf'):
  if type(p[key]) is not int or not 1<=p[key]<=120:raise ValueError('bounded tree parameter required')
 fam=family();splits={h:[s for s in prior.splits(len(frame['times']),h,p) if s['train'][0]>=72] for h in p['horizons']}
 planned=sum(len(v) for v in splits.values())*88
 if planned>p['max_fits']:raise ValueError('budget exceeded before fitting')
 result={'status':'HISTORICAL_RESEARCH_ONLY','policy':p,'hypotheses':fam,'feature_names':library.NAMES,
  'actual_fits':0,'actual_base_fits':0,'actual_calibration_fits':0,'planned_fits':planned,'horizons':{},
  'production_promotion_authorized':False,'real_orders_authorized':False,'validated_70pct':False,
  'historical_holdout_pristine':False,'historical_pit_validated':False,'cost_net_return_validated':False,
  'test_labels_used_for_selection':False,'nonprice_factors':'QUARANTINED','original18_unchanged':True}
 for h,windows in splits.items():
  rows=library.rows(frame,h,p['availability_mode']);folds=[]
  for k,s in enumerate(windows):
   used={key:[rows[i] for i in indices if rows[i] is not None] for key,indices in s.items()}
   clocks={key:frame['times'][s[key][0]]+3600000 for key in ('validation','test')};gap=p['embargo_bars']*3600000
   used['train']=[r for r in used['train'] if r['label_available_ms']+gap<clocks['validation']]
   used['validation']=[r for r in used['validation'] if r['label_available_ms']+gap<clocks['test']]
   f={'fold':k,'windows':{key:{'start_index':ix[0],'end_index':ix[-1]} for key,ix in s.items()},
      'due_test_anchors':len(s['test']),'missing_feature_anchors':len(s['test'])-len(used['test']),
      'unissued_test_anchors':len(s['test']),'models':{},'validation':{},'validation_regimes':{},'events':[],
      'selected_candidate':None,'status':'WAIT_AVAILABLE_TRAINING_DATA'}
   if len(used['train'])<p['train_bars'] or len(used['validation'])<p['validation_bars']:folds.append(f);continue
   fitting,calibration=inner(used['train'],h,p)
   volcut=round(float(np.median([r['x'][6] for r in fitting])),12)
   def state(r):return r['regime']+('_HIGH_VOL' if r['x'][6]>=volcut else '_LOW_VOL')
   f.update(base_training_n=len(fitting),calibration_n=len(calibration),train_volatility_median=volcut,
     train_label_latest_ms=max(r['label_available_ms'] for r in used['train']),
     validation_label_latest_ms=max(r['label_available_ms'] for r in used['validation']),
     base_label_latest_ms=max(r['label_available_ms'] for r in fitting),calibration_first_anchor_ms=calibration[0]['anchor_ms'])
   if f['base_label_latest_ms']+gap>=f['calibration_first_anchor_ms']:raise ValueError('calibration label overlap')
   vp={};tp={}
   for i,setting in enumerate(SETTINGS):
    for variant,groups in library.variants().items():
     cols=[j for g in groups for j in library.GROUPS[g]]
     def matrix(what):return [[r['x'][j] for j in cols] for r in what]
     base=models.fit_base(setting,matrix(fitting),[r['y'] for r in fitting],p);result['actual_base_fits']+=1
     calibrated=models.calibrate(base,matrix(calibration),[r['y'] for r in calibration],p);result['actual_calibration_fits']+=1
     for mode,model in [('raw',{'base':base,'calibration':'raw','probability_calibration_validated':False}),('sigmoid',calibrated)]:
      name=str(i)+'_'+mode+'__'+variant;model={**model,'columns':cols};f['models'][name]=model
      vp[name]=models.predict(model,matrix(used['validation']));tp[name]=models.predict(model,matrix(used['test'])) if used['test'] else []
      f['validation'][name]=probability_metrics([r['y'] for r in used['validation']],vp[name])
   majority=majority_values(frame,used['validation'],h,p['availability_mode'])
   f['validation_probability_baselines']={'half':probability_metrics([r['y'] for r in used['validation']],[.5]*len(majority)),
                                        'majority':probability_metrics([r['y'] for r in used['validation']],majority)}
   f['validation_regimes']={st:{name:probability_metrics([r['y'] for r in used['validation'] if state(r)==st],
                          [q for r,q in zip(used['validation'],probs) if state(r)==st]) for name,probs in vp.items()} for st in STATES}
   chosen=v1.choose(f['validation']);f['selected_candidate']=chosen;testmajority=majority_values(frame,used['test'],h,p['availability_mode'])
   for j,r in enumerate(used['test']):
    probs={name:q[j] for name,q in tp.items()}
    f['events'].append({**{key:r[key] for key in ('event_id','anchor_ms','label_end_ms','y')},'regime':state(r),
     'candidate_p':probs,'selected_candidate':chosen,'selected_setting':chosen.split('__')[0],'selected_p':probs[chosen],
     'majority_p':testmajority[j],'baseline_directions':{'frozen_production_rule':r['frozen_direction'],'btc_momentum':r['btc_direction'],
       'majority':int(testmajority[j]>=.5),'always_up':1,'always_down':0},'net_return':None,'evidence_kind':'RETROSPECTIVE_NOT_LIVE_PREDICTION'})
   f['status']='RESEARCH_ONLY';f['unissued_test_anchors']=len(s['test'])-len(f['events']);folds.append(f)
  hr=v1.summarize(folds,fam);non=hr['nonoverlap_events'];y=[r['y'] for r in non]
  hr['probability_baselines']={'half':probability_metrics(y,[.5]*len(non)),'majority':probability_metrics(y,[r['majority_p'] for r in non])}
  hr['selected_probability_metrics']=probability_metrics(y,[r['selected_p'] for r in non])
  hr['factor_stability']={g:{'by_fold':[], 'by_regime':{}} for g in library.GROUPS}
  for g in library.GROUPS:
   for f in folds:
    ev=[r for r in non if r['event_id'] in {q['event_id'] for q in f['events']}]
    pairs=[((r['candidate_p'][r['selected_setting']+'__drop_'+g]-r['y'])**2-(r['candidate_p'][r['selected_setting']+'__full']-r['y'])**2) for r in ev]
    hr['factor_stability'][g]['by_fold'].append({'fold':f['fold'],'n':len(pairs),'brier_drop_minus_full':sum(pairs)/len(pairs) if pairs else None})
   for st in STATES:
    ev=[r for r in non if r['regime']==st];pairs=[(r['candidate_p'][r['selected_setting']+'__drop_'+g]-r['y'])**2-(r['candidate_p'][r['selected_setting']+'__full']-r['y'])**2 for r in ev]
    hr['factor_stability'][g]['by_regime'][st]={'n':len(pairs),'brier_drop_minus_full':sum(pairs)/len(pairs) if pairs else None}
  result['horizons'][str(h)]=hr
 result['actual_fits']=result['actual_base_fits']+result['actual_calibration_fits']
 return prior.canonical(result)

def verify_output(out):
 out=Path(out);v1.verify_output(out);manifest=json.loads((out/'manifest.json').read_bytes())
 if 'future_protocol_draft.json' not in manifest['objects']:raise ValueError('required future protocol missing')
 research=json.loads((out/'research.json').read_bytes());draft=json.loads((out/'future_protocol_draft.json').read_bytes())
 expected=protocol.prepare(research,research['policy']);expected['experiment_id']=research['experiment_id']
 if encode(draft)!=encode(expected):raise ValueError('future protocol differs from validation-only evidence or authorization boundaries')
 return True

def run(root,out,policy_path):
 out=v1.check_output(out);p=json.loads(policy_path.read_bytes())
 import sklearn,scipy
 if (np.__version__,sklearn.__version__,scipy.__version__)!=('2.3.5','1.7.2','1.16.3'):raise ValueError('pinned numerical runtime required')
 before=frozen.guard();freeze_path=ROOT/'research/phase1_v1/frozen_assets.json';freeze=json.loads(freeze_path.read_bytes())
 protection=audit.verify(root,freeze);frame,source=audit.audit(root,datetime.now(timezone.utc));result=experiment(frame,p)
 identity={'source_commit':freeze['source_commit'],'source_sha256':source['hourly']['sha256'],'policy_sha256':sha(policy_path.read_bytes()),
  'code_sha256_lf':{n:sha((ROOT/n).read_bytes().replace(b'\r\n',b'\n')) for n in CODE},'numpy_version':np.__version__,
  'sklearn_version':sklearn.__version__,'scipy_version':scipy.__version__,'python_minor':'.'.join(platform.python_version_tuple()[:2]),
  'frozen_registered_assets_sha256_lf':{n:sha(b) for n,b in before[2].items()}}
 eid=sha(encode(identity));result.update(experiment_id=eid,identity=identity);draft=protocol.prepare(result,p);draft['experiment_id']=eid
 out.mkdir(parents=True,exist_ok=True);v1.write_immutable(out/'.phase2-owner',v1.OWNER)
 registry={'experiment_id':eid,'status':'RESEARCH_ONLY','production_promotion_authorized':False,'candidate_models':[],
           'future_preregistration_required':True,'real_orders_authorized':False}
 for h,hr in result['horizons'].items():
  for fold in hr['folds']:
   for name,model in fold['models'].items():
    data=encode(model);mid=sha(data);path='models/'+mid+'.json';v1.write_immutable(out/path,data)
    record={'experiment_id':eid,'horizon_hours':int(h),'fold':fold['fold'],'hypothesis_id':name,'model_path':path,'model_sha256':mid,
     'validation':fold['validation'][name],'selected_in_fold':name==fold['selected_candidate'],'production_promotion_authorized':False,
     'status':'RESEARCH_ONLY','training_window':fold['windows']['train'],'base_training_n':fold['base_training_n'],
     'calibration_n':fold['calibration_n'],'base_label_latest_ms':fold['base_label_latest_ms'],'calibration_first_anchor_ms':fold['calibration_first_anchor_ms']}
    cid=sha(encode(record));record['candidate_id']=cid;v1.write_immutable(out/('candidates/'+cid+'.json'),encode(record));registry['candidate_models'].append(record)
 receipt={'experiment_id':eid,'result_sha256':sha(encode(result)),'before':protection,'after':audit.verify(root,freeze),
   'run_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'snapshot_commit':freeze['source_commit'],
   'production_model_changed':False,'frozen_candidate_package_sha256':sha(before[2][frozen.shadow.PACKAGE]),
   'generated_at_utc':datetime.now(timezone.utc).isoformat(),'actual_forward_predictions_issued':0}
 frozen.guard()
 for name,value in [('research.json',result),('registry.json',registry),('source_audit.json',source),('receipt.json',receipt),('future_protocol_draft.json',draft)]:v1.write_immutable(out/name,encode(value))
 manifest={'experiment_id':eid,'objects':{q.relative_to(out).as_posix():sha(q.read_bytes()) for q in out.rglob('*') if q.is_file()}}
 v1.write_immutable(out/'manifest.json',encode(manifest));verify_output(out);return receipt

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
 p.add_argument('--policy',type=Path,default=ROOT/'config/phase2_research_v2.json');a=p.parse_args();print(json.dumps(run(a.source,a.out,a.policy)))

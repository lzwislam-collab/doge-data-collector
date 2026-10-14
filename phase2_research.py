"""Deterministic factor/model research. No production writer or network calls."""
import argparse,hashlib,json,math,platform,subprocess
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import phase1_research as prior
import phase1_research_audit as audit
import phase2_factor_library as library
import learned_shadow_bundle as frozen

ROOT=Path(__file__).resolve().parent
OWNER=b'DOGE_PHASE2_RESEARCH_OUTPUT_V1\n'
sha=lambda raw:hashlib.sha256(raw).hexdigest()
encode=prior.encode
SETTINGS=[{'model':'logistic','regularization':.03},{'model':'logistic','regularization':.3},
          {'model':'shrinkage_lda','regularization':.3},{'model':'shrinkage_lda','regularization':1.0}]
CODE=('phase2_research.py','phase2_factor_library.py','phase1_research.py','phase1_research_audit.py',
      'phase1_research_snapshot.py')


def hypotheses(p):
    prior.validate_policy(p)
    if p.get('availability_mode') not in ('candle_close_research','receipt_time'):raise ValueError('invalid availability')
    if p.get('hypothesis_settings')!=SETTINGS:raise ValueError('fixed four hypothesis settings required')
    if type(p.get('max_fits')) is not int or not 1<=p['max_fits']<=600:raise ValueError('invalid fit budget')
    if p['max_folds']>6 or p['iterations']>120:raise ValueError('bounded research required')
    return {str(i)+'__'+variant:{**setting,'variant':variant,'groups':groups,
                 'columns':[j for group in groups for j in library.GROUPS[group]]}
            for i,setting in enumerate(SETTINGS) for variant,groups in library.variants().items()}


def fit(setting,x,y,p):
    x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float)
    if x.ndim!=2 or not len(x) or x.shape[1]<1 or len(x)!=len(y) or not np.isfinite(x).all() or not np.isin(y,[0,1]).all():
        raise ValueError('finite nonempty binary training required')
    reg=setting['regularization']
    if isinstance(reg,bool) or not isinstance(reg,(int,float)) or not math.isfinite(reg) or reg<=0:
        raise ValueError('positive regularization required')
    if setting['model']=='logistic':
        model=prior.fit(x,y,{**p,'l2':reg})
    elif setting['model']=='shrinkage_lda':
        mean=x.mean(axis=0);scale=x.std(axis=0);scale=np.where(scale<1e-12,1.,scale)
        z=np.clip((x-mean)/scale,-10,10);counts=[int((y==c).sum()) for c in (0,1)]
        # Smoothed prior handles single-class training without fictional samples.
        intercept=math.log((counts[1]+1)/(counts[0]+1));w=np.zeros(x.shape[1])
        if min(counts)>0:
            mu=[z[y==c].mean(axis=0) for c in (0,1)]
            residual=z-np.asarray([mu[int(c)] for c in y]);var=np.mean(residual**2,axis=0)+reg
            w=(mu[1]-mu[0])/var;intercept-=float(.5*(mu[1]+mu[0])@w)
        model=prior.canonical({'mean':mean.tolist(),'scale':scale.tolist(),'weights':w.tolist(),
                              'intercept':intercept,'n_train':len(x),'probability_calibration_validated':False})
    else:raise ValueError('unsupported model')
    return {**model,'model_family':setting['model'],'regularization':reg}


def predict(model,x):return prior.predict(model,x)


def choose(validation):
    validation=prior.canonical(validation)
    eligible={name:v for name,v in validation.items() if v['n']>0 and v['brier'] is not None and math.isfinite(v['brier'])}
    if not eligible:raise ValueError('validation evidence required')
    return min(eligible,key=lambda name:(eligible[name]['brier'],name))


def measured(events,key):
    if key=='selected':
        return prior.metrics([{'y':e['y'],'candidate_p':{'selected':e['selected_p']}} for e in events],key)
    return prior.metrics(events,key)


def nonoverlap(events):
    result=[];end=-1
    for e in events:
        if e['anchor_ms']>end:result.append(e);end=e['label_end_ms']
    return result


def summarize(folds,family):
    events=[e for f in folds for e in f['events']]
    if len({e['event_id'] for e in events})!=len(events):raise ValueError('duplicate test event')
    non=nonoverlap(events)
    validation={}
    for name in family:
        vals=[f['validation'][name] for f in folds if name in f['validation']]
        n=sum(v['n'] for v in vals)
        validation[name]={'n':n,'brier':sum(v['n']*v['brier'] for v in vals)/n if n else None}
    ranking=sorted(validation,key=lambda k:(validation[k]['brier'] is None,validation[k]['brier'] or 0,k))
    baselines={}
    for key in ('frozen_production_rule','btc_momentum','majority','always_up','always_down'):
        n=len(non);correct=sum(e['baseline_directions'][key]==e['y'] for e in non)
        wins=sum(int(e['selected_p']>=.5)==e['y'] and e['baseline_directions'][key]!=e['y'] for e in non)
        losses=sum(int(e['selected_p']>=.5)!=e['y'] and e['baseline_directions'][key]==e['y'] for e in non)
        baselines[key]={'n':n,'accuracy':correct/n if n else None,'selected_wins':wins,'selected_losses':losses,
                        'ties':n-wins-losses,'inference_validated':False}
    gains={}
    for group in library.GROUPS:
        pairs=[(e['candidate_p'][e['selected_setting']+'__full'],e['candidate_p'][e['selected_setting']+'__drop_'+group],e['y']) for e in non]
        n=len(pairs)
        gains[group]={'n':n,'accuracy_full_minus_drop':sum(int((a>=.5)==y)-int((b>=.5)==y) for a,b,y in pairs)/n if n else None,
                      'brier_drop_minus_full':sum((b-y)**2-(a-y)**2 for a,b,y in pairs)/n if n else None,
                      'matched_hyperparameters':True,'inference_validated':False}
    regimes={state:{'selected':measured([e for e in non if e['regime']==state],'selected')}
             for state in ('UP_HIGH_VOL','UP_LOW_VOL','DOWN_HIGH_VOL','DOWN_LOW_VOL')}
    error_rows=[{'event_id':e['event_id'],'selected_candidate':e['selected_candidate'],'regime':e['regime'],
                 'p_up':e['selected_p'],'y':e['y'],'error_type':'DIRECTION_MISMATCH',
                 'source_kind':'HISTORICAL_RESEARCH_NOT_REAL_ISSUANCE'} for e in non if int(e['selected_p']>=.5)!=e['y']]
    return {'folds':folds,'test_events':len(events),'nonoverlap_n':len(non),'nonoverlap_events':non,
            'due_test_anchors':sum(f['due_test_anchors'] for f in folds),
            'missing_feature_anchors':sum(f['missing_feature_anchors'] for f in folds),
            'validation_ranking':ranking,'validation_aggregate':validation,
            'ranking_use':'FUTURE_RESEARCH_SCREEN_ONLY_NOT_RETROSPECTIVE_TEST_SELECTION',
            'descriptive_metrics':{name:measured(non,name) for name in family},'selected_metrics':measured(non,'selected'),
            'same_event_baselines':baselines,'descriptive_ablation_gain':gains,'regime_metrics':regimes,'error_review':error_rows,
            'status':'RESEARCH_ONLY' if events else 'WAIT_AVAILABLE_DATA','inference_validated':False}


def experiment(frame,p):
    family=hypotheses(p)
    splitmap={h:[s for s in prior.splits(len(frame['times']),h,p) if s['train'][0]>=72] for h in p['horizons']}
    planned=sum(len(s) for s in splitmap.values())*len(family)
    if planned>p['max_fits']:raise ValueError('planned fits exceed fixed budget')
    result={'status':'HISTORICAL_RESEARCH_ONLY','policy':p,'feature_names':library.NAMES,'groups':library.GROUPS,
            'hypotheses':family,'planned_fits':planned,'actual_fits':0,'horizons':{},
            'historical_holdout_pristine':False,'historical_pit_validated':False,'test_labels_used_for_selection':False,
            'production_promotion_authorized':False,'real_orders_authorized':False,'validated_70pct':False,
            'cost_net_return_validated':False,'frozen_learned_comparison_status':'BLOCKED_TRAINED_AFTER_HISTORICAL_TEST_USE_FUTURE_EVENTS',
            'research_method':'FIXED_HYPOTHESES_VALIDATION_SELECTION_ROLLING_RETRAIN_ABLATION_FEEDBACK',
            'frameworks_installed':False,'llm_generated_code_execution_enabled':False,
            'nonprice_factor_admission':'QUARANTINED_UNTIL_NEW_PIT_AND_MODEL_SPECIFIC_REGISTRATION'}
    for h,ss in splitmap.items():
        prepared=library.rows(frame,h,p['availability_mode']);folds=[]
        for k,s in enumerate(ss):
            used={key:[prepared[i] for i in ix if prepared[i] is not None] for key,ix in s.items()}
            clocks={key:frame['times'][s[key][0]]+3600000 for key in ('validation','test')}
            embargo=p['embargo_bars']*3600000
            used['train']=[r for r in used['train'] if r['label_available_ms']+embargo<clocks['validation']]
            used['validation']=[r for r in used['validation'] if r['label_available_ms']+embargo<clocks['test']]
            item={'fold':k,'windows':{key:{'start_index':ix[0],'end_index':ix[-1]} for key,ix in s.items()},
                  'due_test_anchors':len(s['test']),'missing_feature_anchors':len(s['test'])-len(used['test']),
                  'models':{},'validation':{},'events':[],'selected_candidate':None,'status':'WAIT_AVAILABLE_TRAINING_DATA'}
            if len(used['train'])<p['train_bars'] or len(used['validation'])<p['validation_bars']:
                item['unissued_test_anchors']=len(s['test']);folds.append(item);continue
            if (max(r['label_available_ms'] for r in used['train'])+embargo>=clocks['validation'] or
                max(r['label_available_ms'] for r in used['validation'])+embargo>=clocks['test']):raise ValueError('label leakage')
            preds={}
            for name,setting in family.items():
                col=setting['columns'];model=fit(setting,[[r['x'][i] for i in col] for r in used['train']],
                                                [r['y'] for r in used['train']],p)
                model['columns']=col;item['models'][name]=model;result['actual_fits']+=1
                probs=predict(model,[[r['x'][i] for i in col] for r in used['validation']])
                item['validation'][name]=prior.canonical(measured([{'y':r['y'],'candidate_p':{name:prob}} for r,prob in zip(used['validation'],probs)],name))
                preds[name]=predict(model,[[r['x'][i] for i in col] for r in used['test']]) if used['test'] else []
            # Test labels are accessed only after the validation choice is frozen.
            selected=choose(item['validation']);item['selected_candidate']=selected;prefix=selected.split('__')[0]
            volcut=round(float(np.median([r['x'][6] for r in used['train']])),12);item['train_volatility_median']=volcut
            item['train_label_latest_ms']=max(r['label_available_ms'] for r in used['train'])
            item['validation_label_latest_ms']=max(r['label_available_ms'] for r in used['validation'])
            for j,r in enumerate(used['test']):
                allp={name:values[j] for name,values in preds.items()};prob=allp[selected]
                item['events'].append({**{q:r[q] for q in ('event_id','anchor_ms','label_end_ms','y')},
                    'candidate_p':allp,'selected_p':prob,'selected_candidate':selected,'selected_setting':prefix,
                    'regime':r['regime']+('_HIGH_VOL' if r['x'][6]>=volcut else '_LOW_VOL'),
                    'baseline_directions':{'frozen_production_rule':r['frozen_direction'],'btc_momentum':r['btc_direction'],
                         'majority':int(prior.majority_probability(frame,r['index'],h,p['availability_mode'])>=.5),
                         'always_up':1,'always_down':0},
                    'net_return':None,'cost_evidence_status':'WAIT_REAL_SAME_EVENT_BID_ASK_DEPTH_FEES_SLIPPAGE',
                    'evidence_kind':'RETROSPECTIVE_CONFIRMED_CANDLE_NOT_ISSUE_QUOTE'})
            item['status']='RESEARCH_ONLY';item['unissued_test_anchors']=len(s['test'])-len(item['events']);folds.append(item)
        result['horizons'][str(h)]=summarize(folds,family)
    return prior.canonical(result)


def check_output(out):
    path=Path(out).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('output symlink forbidden')
    target=path.resolve()
    if ROOT.is_relative_to(target) or any(target.is_relative_to(ROOT/p) for p in ('data','research','system','config','docs','audit','sources','.git','.github','.agents','.codex')):
        raise ValueError('output overlaps protected assets')
    if path.exists() and (not path.is_dir() or any(path.iterdir())):raise ValueError('new empty output directory required')
    return target


def write_immutable(path,raw):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('immutable path symlink forbidden')
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('immutable artifact conflict')
    else:
        with path.open('xb') as stream:stream.write(raw)
    return path


def verify_output(out):
    out=Path(out);manifest=json.loads((out/'manifest.json').read_bytes())
    required={'.phase2-owner','research.json','registry.json','source_audit.json','receipt.json'}
    if not required<=set(manifest['objects']):raise ValueError('mandatory research artifacts missing')
    for name,digest in manifest['objects'].items():
        path=out/name
        if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('artifact symlink forbidden: '+name)
        path=audit.safe(out,name)
        if sha(path.read_bytes())!=digest:raise ValueError('artifact hash mismatch: '+name)
    if (out/'.phase2-owner').read_bytes()!=OWNER:raise ValueError('wrong output owner')
    research=json.loads((out/'research.json').read_bytes());registry=json.loads((out/'registry.json').read_bytes())
    receipt=json.loads((out/'receipt.json').read_bytes());source=json.loads((out/'source_audit.json').read_bytes())
    eid=manifest['experiment_id']
    if (sha(encode(research['identity']))!=eid or any(r['experiment_id']!=eid for r in (research,registry,receipt))
        or receipt['result_sha256']!=sha((out/'research.json').read_bytes())
        or source['hourly']['sha256']!=research['identity']['source_sha256']):raise ValueError('experiment identity mismatch')
    if research['production_promotion_authorized'] is not False or registry['production_promotion_authorized'] is not False:
        raise ValueError('research cannot authorize production')
    candidates=registry['candidate_models']
    if len(candidates)!=research['actual_fits'] or len({r['candidate_id'] for r in candidates})!=len(candidates):
        raise ValueError('incomplete or duplicate candidate registration')
    folds={(h,f['fold']):f for h,hr in research['horizons'].items() for f in hr['folds']}
    expected={(h,k,name) for (h,k),f in folds.items() for name in f['models']}
    actual=set()
    for record in candidates:
        cid=record['candidate_id'];original={k:v for k,v in record.items() if k!='candidate_id'}
        modelpath=record['model_path'];candidatepath='candidates/'+cid+'.json'
        if (sha(encode(original))!=cid or modelpath!='models/'+record['model_sha256']+'.json'
            or modelpath not in manifest['objects'] or candidatepath not in manifest['objects']):
            raise ValueError('candidate identity or artifact missing')
        if (out/candidatepath).read_bytes()!=encode(record) or manifest['objects'][modelpath]!=record['model_sha256']:
            raise ValueError('candidate bytes differ')
        key=(str(record['horizon_hours']),record['fold'],record['hypothesis_id']);actual.add(key)
        if key not in expected:raise ValueError('unexpected candidate model')
        fold=folds[key[:2]]
        if (sha(encode(fold['models'][key[2]]))!=record['model_sha256']
            or record['validation']!=fold['validation'][key[2]] or record['experiment_id']!=eid
            or record['selected_in_fold']!=(key[2]==fold['selected_candidate'])):raise ValueError('candidate evidence differs')
    if actual!=expected:raise ValueError('candidate family incomplete')
    return True


def run(root,out,policy_path):
    out=check_output(out);raw=policy_path.read_bytes();p=json.loads(raw)
    if np.__version__!=p['numpy_version']:raise ValueError('fixed NumPy runtime required')
    guard_before=frozen.guard();freeze_path=ROOT/'research/phase1_v1/frozen_assets.json';freeze=json.loads(freeze_path.read_bytes())
    before=audit.verify(root,freeze);frame,source=audit.audit(root,datetime.now(timezone.utc))
    result=experiment(frame,p)
    identity={'source_commit':freeze['source_commit'],'source_sha256':source['hourly']['sha256'],
              'policy_sha256':sha(raw),'code_sha256_lf':{name:sha((ROOT/name).read_bytes().replace(b'\r\n',b'\n')) for name in CODE},
              'freeze_sha256':sha(freeze_path.read_bytes()),
              'frozen_registered_assets_sha256_lf':{name:sha(data) for name,data in guard_before[2].items()},
              'numpy_version':np.__version__,
              'python_minor':'.'.join(platform.python_version_tuple()[:2])}
    eid=sha(encode(identity));result['experiment_id']=eid;result['identity']=identity
    out.mkdir(parents=True,exist_ok=True);write_immutable(out/'.phase2-owner',OWNER)
    registry={'experiment_id':eid,'status':'RESEARCH_ONLY','production_promotion_authorized':False,
              'real_orders_authorized':False,'hypothesis_count':len(result['hypotheses']),'candidate_models':[],
              'future_preregistration_required':True,'historical_selection_does_not_authorize_issuance':True,
              'interfaces':{'prospective':'NEW_MODEL_SPECIFIC_PUBLIC_FUTURE_PROTOCOL_REQUIRED',
                            'costs':'p2_economic_acceptance.py','error_review':'stage5_error_review.py'}}
    for h,hr in result['horizons'].items():
        for fold in hr['folds']:
            for name,model in fold['models'].items():
                data=encode(model);mid=sha(data);path='models/'+mid+'.json';write_immutable(out/path,data)
                record={'experiment_id':eid,'horizon_hours':int(h),'fold':fold['fold'],'hypothesis_id':name,
                        'model_path':path,'model_sha256':mid,'validation':fold['validation'][name],
                        'selected_in_fold':name==fold['selected_candidate'],'status':'RESEARCH_ONLY',
                        'training_window':fold['windows']['train'],'train_label_latest_ms':fold['train_label_latest_ms'],
                        'production_promotion_authorized':False,'prospective_registration_required':True}
                cid=sha(encode(record));record['candidate_id']=cid;write_immutable(out/('candidates/'+cid+'.json'),encode(record))
                registry['candidate_models'].append(record)
    write_immutable(out/'research.json',encode(result));write_immutable(out/'registry.json',encode(registry))
    write_immutable(out/'source_audit.json',encode(source));after=audit.verify(root,freeze);frozen.guard()
    receipt={'experiment_id':eid,'result_sha256':sha(encode(result)),
             'run_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
             'snapshot_commit':freeze['source_commit'],'before':before,'after':after,
             'frozen_candidate_package_sha256':sha(guard_before[2][frozen.shadow.PACKAGE]),
             'production_model_changed':False,'generated_at_utc':datetime.now(timezone.utc).isoformat(),
             'cloud_execution_verified_by_this_local_receipt':False}
    write_immutable(out/'receipt.json',encode(receipt))
    objects={path.relative_to(out).as_posix():sha(path.read_bytes()) for path in out.rglob('*') if path.is_file()}
    write_immutable(out/'manifest.json',encode({'experiment_id':eid,'objects':objects}));verify_output(out)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--policy',type=Path,default=ROOT/'config/phase2_research_v1.json');args=parser.parse_args()
    print(json.dumps(run(args.source,args.out,args.policy),sort_keys=True))

"""Independent semantic audit and safe entrypoint; frozen research stays unchanged."""
import argparse,json,math
from pathlib import Path
import phase2_research as v1

ROOT=Path(__file__).resolve().parent
FALSE_FIELDS=('production_promotion_authorized','real_orders_authorized','validated_70pct','historical_pit_validated','historical_holdout_pristine','cost_net_return_validated','test_labels_used_for_selection')

def check_semantics(research,registry):
 if research.get('status')!='HISTORICAL_RESEARCH_ONLY' or registry.get('status')!='RESEARCH_ONLY':raise ValueError('research-only status required')
 if any(research.get(k) is not False for k in FALSE_FIELDS):raise ValueError('research authorization/PIT/holdout claims changed')
 if any(registry.get(k) is not False for k in ('production_promotion_authorized','real_orders_authorized')) or registry.get('future_preregistration_required') is not True:raise ValueError('registry cannot authorize production')
 folds={(str(h),f['fold']):f for h,hr in research['horizons'].items() for f in hr['folds']}
 for (h,_),fold in folds.items():
  embargo=research.get('policy',{}).get('embargo_bars',0)*3600000
  if 'base_label_latest_ms' in fold and fold['base_label_latest_ms']+embargo>=fold['calibration_first_anchor_ms']:raise ValueError('base/calibration label leakage')
  anchors=[e['anchor_ms'] for e in fold['events'] if 'anchor_ms' in e]
  if any(b<=a for a,b in zip(anchors,anchors[1:])):raise ValueError('historical event ordering changed')
  if fold['models']:
   if fold['selected_candidate']!=v1.choose(fold['validation']):raise ValueError('selection differs from validation-only deterministic choice')
   if set(fold['models'])!=set(fold['validation']):raise ValueError('validation model coverage changed')
  for event in fold['events']:
   if 'anchor_ms' in event and (event['event_id']!=str(event['anchor_ms'])+'_'+h or event['label_end_ms']!=event['anchor_ms']+int(h)*3600000):raise ValueError('historical event identity/horizon changed')
   chosen=fold['selected_candidate'];probs=event['candidate_p']
   if set(probs)!=set(fold['models']) or event['selected_candidate']!=chosen or event['selected_setting']!=chosen.split('__')[0] or event['selected_p']!=probs[chosen]:raise ValueError('event selection/probability differs from frozen fold')
   if type(event['y']) is not int or event['y'] not in (0,1) or any(isinstance(q,bool) or not math.isfinite(q) or not 0<=q<=1 for q in probs.values()):raise ValueError('invalid historical probability/label')
  if 'due_test_anchors' in fold and fold['due_test_anchors']!=len(fold['events'])+fold['unissued_test_anchors']:raise ValueError('missing research anchors excluded from denominator')
 for record in registry['candidate_models']:
  fold=folds[(str(record['horizon_hours']),record['fold'])]
  if record.get('status')!='RESEARCH_ONLY' or record.get('production_promotion_authorized') is not False:raise ValueError('candidate cannot authorize production')
  if record['training_window']!=fold['windows']['train']:raise ValueError('candidate training window differs from fold')
  for key in ('base_training_n','calibration_n','base_label_latest_ms','calibration_first_anchor_ms','train_label_latest_ms'):
   if key in record and record[key]!=fold[key]:raise ValueError('candidate training evidence differs: '+key)
  for key in ('real_orders_authorized','validated_70pct','auto_promotion_authorized'):
   if key in record and record[key] is not False:raise ValueError('candidate safety boundary changed')
  if 'prospective_registration_required' in record and record['prospective_registration_required'] is not True:raise ValueError('future registration required')
 return True

def metric_equal(a,b):
 # Stored JSON is rounded to 12 decimals; aggregation can add one rounding unit.
 if type(a) is bool or type(b) is bool:return type(a) is type(b) and a==b
 if isinstance(a,(int,float)) and isinstance(b,(int,float)):
  return math.isfinite(a) and math.isfinite(b) and abs(a-b)<=5e-12
 if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(metric_equal(a[k],b[k]) for k in a)
 if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(metric_equal(x,y) for x,y in zip(a,b))
 return type(a) is type(b) and a==b

def is_v2(research):
 return 'phase2_research_v2.py' in research.get('identity',{}).get('code_sha256_lf',{}) or 'actual_calibration_fits' in research

def v2_metrics(hr):
 import phase2_research_v2 as v2
 non=hr['nonoverlap_events'];y=[e['y'] for e in non]
 result={'probability_baselines':{'half':v2.probability_metrics(y,[.5]*len(non)),'majority':v2.probability_metrics(y,[e['majority_p'] for e in non])},
  'selected_probability_metrics':v2.probability_metrics(y,[e['selected_p'] for e in non]),'factor_stability':{}}
 for group in v1.library.GROUPS:
  def evidence(events):
   pairs=[(e['candidate_p'][e['selected_setting']+'__drop_'+group]-e['y'])**2-(e['candidate_p'][e['selected_setting']+'__full']-e['y'])**2 for e in events]
   return {'n':len(pairs),'brier_drop_minus_full':sum(pairs)/len(pairs) if pairs else None}
  by_fold=[]
  for fold in hr['folds']:
   ids={e['event_id'] for e in fold['events']};by_fold.append({'fold':fold['fold'],**evidence([e for e in non if e['event_id'] in ids])})
  result['factor_stability'][group]={'by_fold':by_fold,'by_regime':{st:evidence([e for e in non if e['regime']==st]) for st in v2.STATES}}
 return result

def check_v2_metrics(hr):
 for key,value in v2_metrics(hr).items():
  if not metric_equal(hr.get(key),value):raise ValueError('saved V2 metric replay differs: '+key)

def verify_output(out):
 out=Path(out)
 research=json.loads((out/'research.json').read_bytes())
 if is_v2(research):
  import phase2_research_v2 as v2
  v2.verify_output(out)
 else:v1.verify_output(out)
 research=json.loads((out/'research.json').read_bytes());registry=json.loads((out/'registry.json').read_bytes());check_semantics(research,registry)
 # Replay saved test predictions, same-event baselines, ablation and denominators.
 # This is not a substitute for refitting against the exact source snapshot.
 for hr in research['horizons'].values():
  if is_v2(research):check_v2_metrics(hr)
  expected=v1.summarize(hr['folds'],research['hypotheses'])
  for key,value in expected.items():
   if not metric_equal(hr.get(key),value):raise ValueError('saved test metric replay differs: '+key)
 return {'experiment_id':research['experiment_id'],'status':'RESEARCH_ONLY_SEMANTICS_AND_SAVED_METRICS_VERIFIED','candidate_count':len(registry['candidate_models']),
  'training_refitted_by_this_check':False,'historical_pit_validated':False,'production_promotion_authorized':False,'real_orders_authorized':False}

def fresh_output(root,out):
 root=Path(root).resolve();out=Path(out)
 if any(p.is_symlink() for p in (out,*out.parents)):raise ValueError('research output symlink forbidden')
 target=out.resolve()
 if target==root or root.is_relative_to(target) or any(target.is_relative_to(root/name) for name in ('data','research','system','config','sources','.git','docs','audit')):raise ValueError('research output overlaps protected namespace')
 if target.exists() and (not target.is_dir() or any(target.iterdir())):raise ValueError('new empty research output required')
 return target

def main():
 parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='mode',required=True)
 verify=sub.add_parser('verify');verify.add_argument('--out',type=Path,required=True)
 run=sub.add_parser('phase1');run.add_argument('--out',type=Path,required=True);run.add_argument('--source',type=Path,required=True)
 args=parser.parse_args()
 if args.mode=='verify':result=verify_output(args.out)
 else:
  import phase1_research as p
  out=fresh_output(ROOT,args.out);fresh_output(args.source,out)
  result=p.run(args.source,out,ROOT/'config/phase1_research_v1.json',ROOT/'research/phase1_v1/frozen_assets.json')
 print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()

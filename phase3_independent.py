"""Read-only fixed-look comparison; conditional martingale bounds, no orders."""
import math
from datetime import timedelta
import phase2_forward_v2 as f

STATES=('UP_HIGH_VOL','UP_LOW_VOL','DOWN_HIGH_VOL','DOWN_LOW_VOL')
PROB_BASELINES=('frozen_full','constant_05','frozen_majority')
DIR_BASELINES=(*PROB_BASELINES,'frozen_rule')
COST_BASELINES=(*DIR_BASELINES,'hold','cash')

def policy():
 return {'schema':'DOGE_PHASE3_INDEPENDENT_V1','assessment_id':'DOGE_R2_INDEPENDENT_FIXED_LOOK_20261014',
  'protocol_id':f.policy()['protocol_id'],'model_identity':'0843d2e836f0d94cd46e66644460f1a543aef460e51ad884394a5880dadf3baf',
  'family_size_upper_bound':10000,'fixed_looks':2,'maximum_claims_per_look':64,'familywise_alpha':.05,
  'method':'FIXED_LAMBDA_CONDITIONAL_HOEFFDING_SUPERMARTINGALE',
  'estimand':'average conditional expected score over this fixed observed window, not future stationary performance',
  'minimum_accuracy_lower_bound':.70,'accuracy_margin':.01,'brier_margin':.002,
  'regime_min_events':20,'regime_max_brier_harm':.01,'minimum_nonoverlap_events':100,
  'block_days':3,'fixed_review_window_seconds':1800,'cost_win_lower_bound':.5,
  'missing_events':'worst score, no deletion','raw_return_mean_confidence_interval_proved':False,
  'real_orders_authorized':False,'automatic_production_promotion':False}

def bound(values,ranges,alpha,planned_count,max_width):
 """Fixed lambda before data, with predictable ranges including regime masks.

 E exp(lambda*(sum X-sum E[X|past])-lambda^2*sum(width^2)/8)<=1.
 Markov gives a lower bound for the mean conditional expectation. Lambda
 does NOT use realized volatility, label, or random regime sample count.
 """
 if not 0<alpha<1 or type(planned_count) is not int or planned_count<=0 or not math.isfinite(max_width) or max_width<=0:raise ValueError('invalid fixed bound parameters')
 if not values:return None
 if len(values)!=len(ranges):raise ValueError('unaligned bounds')
 variance=0
 for x,(lo,hi) in zip(values,ranges):
  if any(isinstance(v,bool) or not math.isfinite(v) for v in (x,lo,hi)) or not lo<=x<=hi or hi-lo>max_width+1e-12:raise ValueError('score outside predictable range')
  variance+=(hi-lo)**2
 lam=math.sqrt(8*math.log(1/alpha)/(planned_count*max_width**2))
 return (math.fsum(values)-math.log(1/alpha)/lam-lam*variance/8)/len(values)

def lower_bound(values,lo,hi,alpha):
 return bound(values,[(lo,hi)]*len(values),alpha,len(values),hi-lo)

def _map(rows):
 out={}
 for r in rows:
  key=r['event_id']
  if key in out:raise ValueError('duplicate event/score/witness')
  out[key]=r
 return out

def _prob(x):
 if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or not 0<=x<=1:raise ValueError('invalid prediction probability')
 return float(x)

def _mean(values):return math.fsum(values)/len(values) if values else None

def evaluate(p,events,scores,qualified,now,full_audit,registered):
 if f.encode(p)!=f.encode(policy()):raise ValueError('statistical policy changed, new preregistration required')
 fp=f.policy();start=f.utc(fp['effective_from_utc']);end=f.utc(fp['observation_end_utc'])
 planned=int((end-start).total_seconds()//fp['slot_seconds']);alpha=p['familywise_alpha']/(p['family_size_upper_bound']*p['fixed_looks']*p['maximum_claims_per_look'])
 emap=_map(events);smap=_map(scores);qmap=_map(qualified);formal={}
 for e in events:
  if e['cohort']=='canary':continue
  if e['cohort']!='formal' or e.get('included_in_formal_statistics') is not True or e['protocol_id']!=p['protocol_id'] or e['model_identity']!=p['model_identity']:raise ValueError('unregistered model or cohort')
  slot=f.utc(e['slot_utc']);at=f.utc(e['issued_at_utc']);target=f.utc(e['target_utc'])
  offset=(slot-start).total_seconds()
  if not start<=slot<end or offset%fp['slot_seconds'] or e['event_id']!=str(int(slot.timestamp()))+'_formal':raise ValueError('off-grid formal event')
  if not slot<=at<slot+timedelta(seconds=fp['issue_window_seconds']) or at>now or target!=at+timedelta(seconds=fp['horizon_seconds']) or e['horizon_seconds']!=fp['horizon_seconds']:raise ValueError('future/overlapping forecast')
  if e['regime'] not in STATES or set(e['candidate_p'])!={'selected',*PROB_BASELINES} or type(e['frozen_rule_direction']) is not int or e['frozen_rule_direction'] not in (0,1):raise ValueError('unknown issuance-time comparator/regime')
  for prob in e['candidate_p'].values():_prob(prob)
  formal[e['event_id']]=e
 ordered=sorted(formal.values(),key=lambda e:e['issued_at_utc'])
 if any(f.utc(a['target_utc'])>f.utc(b['issued_at_utc']) for a,b in zip(ordered,ordered[1:])):raise ValueError('overlapping forecast observations')
 if any(k not in emap for k in smap if not k.endswith('_canary')):raise ValueError('orphan formal score')
 slots=[]
 for i in range(planned):
  slot=start+timedelta(seconds=i*fp['slot_seconds'])
  # Use final possible slot deadline, independent of early/late actual issuance.
  if slot+timedelta(seconds=fp['issue_window_seconds']+fp['horizon_seconds']+fp['target_window_seconds'])<=now:slots.append(slot)
 hits=[];hit_ranges=[];missing=[];error_review=[];valid=0;cost_missing=0;metrics={};blocks={}
 regimes={state:{'n':0,'values':{b:[] for b in PROB_BASELINES},'ranges':{b:[] for b in PROB_BASELINES}} for state in STATES}
 for b in DIR_BASELINES:metrics['accuracy:'+b]={'values':[],'ranges':[],'width':2}
 for b in PROB_BASELINES:metrics['brier:'+b]={'values':[],'ranges':[],'width':2}
 economic={b:{mode:[] for mode in ('net_return','stress_net_return')} for b in ('selected',*COST_BASELINES)}
 for b in COST_BASELINES:
  for mode in ('net_return','stress_net_return'):metrics['costwin:'+mode+':'+b]={'values':[],'ranges':[],'width':1}
 for slot in slots:
  key=str(int(slot.timestamp()))+'_formal';e=formal.get(key);s=smap.get(key);q=qmap.get(key)
  reason='MISSING_ISSUANCE' if e is None else 'MISSING_SCORE' if s is None else 'MISSING_INDEPENDENT_PUBLIC_WITNESS' if not q or q.get('formal_eligible') is not True or q.get('cohort')!='formal' else None
  valid_row=reason is None
  if valid_row:
   if type(s.get('y')) is not int or s['y'] not in (0,1) or s.get('included_in_formal_statistics') is not True:raise ValueError('invalid realized formal label')
   y=s['y'];pc=e['candidate_p']['selected'];hit=int((pc>=.5)==y);valid+=1
   if not hit:error_review.append({'event_id':key,'category':'DIRECTION_WRONG_NO_CAUSAL_ATTRIBUTION','regime':e['regime']})
  else:
   hit=0;missing.append({'event_id':key,'reason':reason});error_review.append({'event_id':key,'category':reason})
  hits.append(float(hit));hit_ranges.append((0.,1.))
  block=int((slot-start).total_seconds()//(p['block_days']*86400));blocks.setdefault(block,[]).append(hit)
  for b in DIR_BASELINES:
   if e is not None:
    direction=e['frozen_rule_direction'] if b=='frozen_rule' else int(e['candidate_p'][b]>=.5)
    limits=(0.,0.) if direction==int(e['candidate_p']['selected']>=.5) else (-1.,1.)
   else:limits=(-1.,1.)
   value=hit-int(direction==y) if valid_row else limits[0]
   metrics['accuracy:'+b]['values'].append(float(value));metrics['accuracy:'+b]['ranges'].append(limits)
  for b in PROB_BASELINES:
   if e is not None:
    pb=e['candidate_p'][b];candidate=e['candidate_p']['selected']
    limits=tuple(sorted((pb**2-candidate**2,(pb-1)**2-(candidate-1)**2)))
   else:limits=(-1.,1.)
   value=(pb-y)**2-(pc-y)**2 if valid_row else limits[0]
   metrics['brier:'+b]['values'].append(float(value));metrics['brier:'+b]['ranges'].append(limits)
   for state,r in regimes.items():
    # The range/mask is fixed at issuance, never selected by target validity.
    mask=e is not None and e['regime']==state
    r['values'][b].append(float(value) if mask else 0.);r['ranges'][b].append(limits if mask else (0.,0.))
  for state in STATES:
   if e is not None and e['regime']==state:regimes[state]['n']+=1
  cost_ok=valid_row and s.get('cost_error') is None and q.get('cost_available') is True
  if cost_ok:
   try:
    returns=s['economic_returns']
    if set(returns)!={'selected',*COST_BASELINES}:raise ValueError('incomplete cost comparators')
    for b in returns:
     for mode in economic[b]:
      x=returns[b][mode]
      if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x):raise ValueError('invalid raw return')
   except (KeyError,TypeError,ValueError):cost_ok=False
  if not cost_ok:cost_missing+=1
  for b in economic:
   for mode in economic[b]:economic[b][mode].append(float(returns[b][mode]) if cost_ok else None)
  for b in COST_BASELINES:
   for mode in ('net_return','stress_net_return'):
    metric=metrics['costwin:'+mode+':'+b];metric['values'].append(float(returns['selected'][mode]>returns[b][mode]) if cost_ok else 0.)
    metric['ranges'].append((0.,1.))
 comparisons={k:{'mean':_mean(v['values']),'lower':bound(v['values'],v['ranges'],alpha,planned,v['width'])} for k,v in metrics.items()}
 rresult={}
 for state,r in regimes.items():
  coverage=r['n']/len(slots) if slots else 0.
  lower={b:bound(r['values'][b],r['ranges'][b],alpha,planned//4,2) for b in PROB_BASELINES}
  rresult[state]={'n':r['n'],'scheduled_denominator':len(slots),'coverage':coverage,'masked_brier_lower':lower,
   'pass':r['n']>=p['regime_min_events'] and all(v is not None and v>=-coverage*p['regime_max_brier_harm'] for v in lower.values())}
 raw={};cost_pass=bool(slots) and cost_missing==0
 for mode in ('net_return','stress_net_return'):
  if cost_missing==0 and slots:
   raw[mode]={'candidate_mean':_mean(economic['selected'][mode]),'mean_excess':{b:_mean([a-z for a,z in zip(economic['selected'][mode],economic[b][mode])]) for b in COST_BASELINES}}
   cost_pass=cost_pass and raw[mode]['candidate_mean']>0 and all(x>0 for x in raw[mode]['mean_excess'].values()) and all(comparisons['costwin:'+mode+':'+b]['lower']>p['cost_win_lower_bound'] for b in COST_BASELINES)
  else:raw[mode]=None
 lower=bound(hits,hit_ranges,alpha,planned,1)
 active_looks=[i for i,t in enumerate(fp['fixed_review_utc']) if f.utc(t)<=now<f.utc(t)+timedelta(seconds=p['fixed_review_window_seconds'])]
 criteria={'fixed_review':bool(active_looks),'complete_fixed_window':len(slots)==planned,
  'independent_full_audit':full_audit is True,'preregistered_assessment':registered is True,
  'no_missing_events':bool(slots) and not missing,'sample_count':valid>=p['minimum_nonoverlap_events'],
  'accuracy_70_lower':lower is not None and lower>=p['minimum_accuracy_lower_bound'],
  'direction_excess':all(comparisons['accuracy:'+b]['lower'] is not None and comparisons['accuracy:'+b]['lower']>=p['accuracy_margin'] for b in DIR_BASELINES),
  'brier_excess':all(comparisons['brier:'+b]['lower'] is not None and comparisons['brier:'+b]['lower']>=p['brier_margin'] for b in PROB_BASELINES),
  'cross_regime_no_harm':all(r['pass'] for r in rresult.values()),'raw_nominal_stress_cost':bool(cost_pass)}
 passed=all(criteria.values())
 late=not active_looks and any(now>=f.utc(t)+timedelta(seconds=p['fixed_review_window_seconds']) for t in fp['fixed_review_utc'])
 return {'schema':p['schema'],'assessment_id':p['assessment_id'],'model_identity':p['model_identity'],'at_utc':now.isoformat(),
  'status':'READY_FOR_INDEPENDENT_REVIEW' if passed else 'WAIT_ACTIVATION' if now<start else 'MISSED_FIXED_REVIEW' if late else 'BLOCKED_OR_DIAGNOSTIC_ONLY',
  'fixed_look_index':active_looks[0] if active_looks else None,'criteria':criteria,'statistical_gate_passed':passed,
  'scheduled_events':len(slots),'planned_events':planned,'qualified_events':valid,'missing':missing,'cost_missing':cost_missing,
  'accuracy':_mean(hits),'accuracy_lower':lower,'comparisons':comparisons,'regimes':rresult,'raw_economic_results':raw,
  'calendar_blocks':[{'block':i,'events':len(v),'accuracy':_mean(v)} for i,v in sorted(blocks.items())],
  'calendar_blocks_assumed_independent':False,'error_review':error_review,'claims_computed':1+len(comparisons)+12,
  'single_claim_alpha':alpha,'estimand':p['estimand'],'raw_return_mean_confidence_interval_proved':False,
  'validated_70pct_future_performance':False,'production_promotion_authorized':False,'real_orders_authorized':False}

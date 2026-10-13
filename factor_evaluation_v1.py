"""Fixed-look paired factor review. Inputs must come from the evidence adapter.

Moving calendar blocks preserve within-block dependence, not arbitrary long-range
dependence. Intervals are asymptotic research evidence, never an order or automatic
model change. Missing outcomes are not replaced by reconstructed labels.
"""
import math
from datetime import datetime, timedelta, timezone

ECONOMIC_KEYS=('paired_net','cash_net','hold_net',
               'paired_stress_net','cash_stress_net','hold_stress_net')
PREDICTION_KEYS=('accuracy_vs_frozen','accuracy_vs_paired','brier_vs_paired')


def utc(value):
    at=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    if not isinstance(at,datetime) or at.tzinfo is None or at.utcoffset()!=timedelta(0):
        raise ValueError('explicit UTC required')
    return at


def number(value,low=None,high=None):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('finite numeric value required')
    if low is not None and value<low or high is not None and value>high:
        raise ValueError('numeric value outside range')
    return value


def block_interval(dates,values,policy):
    """Percentile MBB on calendar-day (sum,count) clusters; empty days remain.

    Bonferroni one-sided tails cover the preregistered family, both signs and all
    looks. No optional stopping. Degenerate fixtures are not market validation.
    """
    if not values or len(values)!=len(dates):raise ValueError('nonempty aligned blocks required')
    daily={}
    for day,value in zip(dates,values):
        number(value)
        day=datetime.fromisoformat(day).date()
        total,count=daily.get(day,(0.,0));daily[day]=(total+value,count+1)
    first,last=min(daily),max(daily);days=(last-first).days+1
    clusters=[daily.get(first+timedelta(days=i),(0.,0)) for i in range(days)]
    width=min(policy['block_days'],days)
    # Circular wrapping would turn the last observed regime into the first.
    starts=range(days-width+1)
    block_sums=[(sum(clusters[j][0] for j in range(i,i+width)),
                 sum(clusters[j][1] for j in range(i,i+width))) for i in starts]
    # Bounded batches avoid a giant bootstrap-by-calendar matrix.
    import numpy as np
    if np.__version__!=policy.get('numpy_version','2.3.5'):raise ValueError('frozen bootstrap runtime version mismatch')
    rng=np.random.Generator(np.random.PCG64(policy['bootstrap_seed']))
    sums=np.asarray([v[0] for v in block_sums]);counts=np.asarray([v[1] for v in block_sums])
    full,remainder=divmod(days,width)
    partial_sums=np.asarray([sum(clusters[j][0] for j in range(i,i+remainder)) for i in starts])
    partial_counts=np.asarray([sum(clusters[j][1] for j in range(i,i+remainder)) for i in starts])
    draws=np.empty(policy['bootstrap_replicates'])
    for offset in range(0,len(draws),1024):
        n=min(1024,len(draws)-offset)
        sampled=rng.integers(0,len(block_sums),size=(n,full+bool(remainder)))
        total=sums[sampled[:,:full]].sum(axis=1);count=counts[sampled[:,:full]].sum(axis=1)
        if remainder:
            total+=partial_sums[sampled[:,-1]];count+=partial_counts[sampled[:,-1]]
        draws[offset:offset+n]=np.divide(total,count,out=np.zeros(n),where=count!=0)
    draws.sort();tail=policy['familywise_alpha']/policy['family_size']
    lo=draws[min(len(draws)-1,max(0,int(tail*len(draws))))]
    hi=draws[min(len(draws)-1,max(0,int((1-tail)*len(draws))-1))]
    return {'mean':sum(values)/len(values),'lower':float(lo),'upper':float(hi),
            'calendar_days':days,'occupied_days':len(daily),'block_days':width,
            'nominal_one_sided_alpha':tail,'replicates':len(draws),
            'method':'moving_calendar_day_blocks_percentile_Bonferroni',
            'finite_sample_guarantee':False}


def validate_rows(policy,rows,horizon,group,now):
    ids=set();start=utc(policy['effective_from_utc']);answer=[]
    for row in rows:
        fid=row['event_id']
        if not fid or fid in ids:raise ValueError('duplicate/empty event')
        ids.add(fid)
        if row['horizon_hours']!=horizon or row['group']!=group:raise ValueError('mixed horizon/group')
        issued,target=utc(row['issued_at_utc']),utc(row['target_utc'])
        if issued<start or issued>now or target!=issued+timedelta(hours=horizon):
            raise ValueError('invalid event chronology')
        for key in ('candidate_p','paired_p'):
            if row.get(key) is not None:number(row[key],0,1)
        for key in ('candidate_direction','paired_direction','frozen_direction'):
            if row.get(key) not in ('UP','DOWN',None):raise ValueError('invalid direction')
        for pkey,dkey in [('candidate_p','candidate_direction'),('paired_p','paired_direction')]:
            p=row.get(pkey);d=row.get(dkey)
            if p is not None and d!=('UP' if p>=.5 else 'DOWN'):
                raise ValueError('direction/probability mismatch')
        if row.get('y') not in (0,1,None) or isinstance(row.get('y'),bool):raise ValueError('invalid outcome')
        if row.get('y') is not None and target+timedelta(minutes=policy['target_window_minutes'])>now:
            raise ValueError('future target label')
        if row.get('regime') not in ('UP','DOWN',None):raise ValueError('invalid ex ante regime')
        if row.get('paper_action') not in ('NO_TRADE','PAPER_LONG',None):raise ValueError('invalid paper action')
        economics=row.get('economic_deltas')
        if economics is not None:
            if set(economics)!=set(ECONOMIC_KEYS):raise ValueError('incomplete cost comparators')
            for value in economics.values():number(value)
        answer.append(row)
    return sorted(answer,key=lambda r:(utc(r['issued_at_utc']),r['event_id']))


def reserve(policy,rows,horizon,cutoff):
    """Fixed slots from activation; first issuance in each window, outcome blind."""
    step=policy.get('slot_step_hours',horizon+1)
    if step<horizon+1:raise ValueError('overlapping planned slots')
    at=utc(policy['effective_from_utc']);slots=[];index=0
    while at+timedelta(hours=horizon,minutes=policy['max_issue_delay_minutes']+policy['target_window_minutes'])<=cutoff:
        while index<len(rows) and utc(rows[index]['issued_at_utc'])<at:index+=1
        row=None
        if index<len(rows) and utc(rows[index]['issued_at_utc'])<at+timedelta(minutes=policy['max_issue_delay_minutes']):
            row=rows[index];index+=1
        slots.append({'slot_utc':at.isoformat(),'row':row})
        at+=timedelta(hours=step)
    return slots


def qualified(row):
    return (row is not None and row.get('y') is not None
            and all(row.get(k) is not None for k in ('candidate_p','paired_p','frozen_direction','regime')))


def differences(row):
    y=row['y'];candidate=int((row['candidate_direction']=='UP')==bool(y))
    return {'accuracy_vs_frozen':candidate-int((row['frozen_direction']=='UP')==bool(y)),
            'accuracy_vs_paired':candidate-int((row['paired_direction']=='UP')==bool(y)),
            'brier_vs_paired':(row['paired_p']-y)**2-(row['candidate_p']-y)**2}


def metrics(rows):
    n=len(rows)
    hits=sum((r['candidate_direction']=='UP')==bool(r['y']) for r in rows)
    recall={str(y):sum((r['candidate_direction']=='UP')==bool(y) for r in rows if r['y']==y)/
            sum(r['y']==y for r in rows) if any(r['y']==y for r in rows) else None for y in (0,1)}
    bins=[];ece=0.
    for i in range(10):
        part=[r for r in rows if min(9,int(10*r['candidate_p']))==i]
        p=sum(r['candidate_p'] for r in part)/len(part) if part else None
        freq=sum(r['y'] for r in part)/len(part) if part else None
        if part:ece+=len(part)/n*abs(p-freq)
        bins.append({'lower':i/10,'upper':(i+1)/10,'n':len(part),'mean_p':p,'up_frequency':freq})
    return {'n':n,'candidate_accuracy':hits/n if n else None,
            'paired_accuracy':sum((r['paired_direction']=='UP')==bool(r['y']) for r in rows)/n if n else None,
            'frozen_accuracy':sum((r['frozen_direction']=='UP')==bool(r['y']) for r in rows)/n if n else None,
            'brier_raw_probability':sum((r['candidate_p']-r['y'])**2 for r in rows)/n if n else None,
            'paired_brier':sum((r['paired_p']-r['y'])**2 for r in rows)/n if n else None,
            'balanced_accuracy':sum(recall.values())/2 if all(v is not None for v in recall.values()) else None,
            'recall':recall,'fixed_reliability_bins':bins,'ece_descriptive':ece if n else None,
            'no_trade_n':sum(r['paper_action']=='NO_TRADE' for r in rows),
            'probability_calibration_validated':False}


def evaluate(policy,rows,now,registered=False,horizon=1,group='FOUR_HOUR'):
    now=utc(now);rows=validate_rows(policy,rows,horizon,group,now)
    looks=[utc(v) for v in policy['review_dates_utc']]
    look=next((v for v in looks if v.date()==now.date()),None)
    cutoff=look if look is not None else now
    slots=reserve(policy,rows,horizon,cutoff)
    valid=[s['row'] for s in slots if qualified(s['row'])]
    due=len(slots);n=len(valid);fraction=n/due if due else 0
    days=max(0,(cutoff-utc(policy['effective_from_utc'])).total_seconds()/86400)
    reasons=[]
    if not registered:reasons.append('registration_or_independent_witness_missing')
    if look is None:reasons.append('not_fixed_review_date')
    if n<policy['minimum_samples']:reasons.append('insufficient_nonoverlap_samples')
    if days<policy['minimum_days']:reasons.append('insufficient_observation_days')
    if fraction<policy['minimum_coverage']:reasons.append('insufficient_full_opportunity_coverage')
    if not n or not all(r.get('rolling_validation_verified') is True for r in valid):reasons.append('rolling_validation_not_verified')
    promotion_blockers=[]
    if not n or any(r.get('economic_deltas') is None for r in valid) or n!=due:promotion_blockers.append('incomplete_same_event_cost_evidence')
    if days//policy['block_days']<policy['minimum_blocks']:reasons.append('insufficient_calendar_blocks')
    if policy['bootstrap_replicates']*policy['familywise_alpha']/policy['family_size']<policy['minimum_bootstrap_tail']:
        reasons.append('insufficient_bootstrap_tail_resolution')
    if any(sum(r['regime']==regime for r in valid)<policy['minimum_regime_samples'] for regime in ('UP','DOWN')):
        reasons.append('insufficient_exante_regime_coverage')
    start=utc(policy['effective_from_utc'])
    for name,part in [('all',valid)]+[(v,[r for r in valid if r['regime']==v]) for v in ('UP','DOWN')]:
        occupied={int((utc(r['issued_at_utc'])-start).total_seconds()//(86400*policy['block_days'])) for r in part}
        if len(occupied)<policy['minimum_blocks']:reasons.append('insufficient_occupied_blocks_'+name)
    discordant={key:sum(differences(r)[key]!=0 for r in valid) for key in PREDICTION_KEYS[:2]}
    if any(v<policy['minimum_discordant'] for v in discordant.values()):reasons.append('insufficient_discordant_pairs')
    result={'protocol_id':policy['protocol_id'],'group':group,'horizon_hours':horizon,
            'status':'WAIT_EVIDENCE','reasons':reasons,'promotion_blockers':promotion_blockers,'evaluated_at_utc':now.isoformat(),
            'fixed_outcome_cutoff_utc':cutoff.isoformat(),'observation_days':days,
            'coverage':{'expected':due,'issued':sum(s['row'] is not None for s in slots),
                        'source_present':sum(s['row'] is not None and s['row'].get('candidate_p') is not None for s in slots),
                        'target_scored':sum(s['row'] is not None and s['row'].get('y') is not None for s in slots),
                        'cost_complete':sum(s['row'] is not None and s['row'].get('economic_deltas') is not None for s in slots),
                        'qualified':n,'missing':due-n,'fraction':fraction},
            'metrics':metrics(valid),'regimes':{v:metrics([r for r in valid if r['regime']==v]) for v in ('UP','DOWN')},
            'discordant_pairs':discordant,'intervals':{},'production_allowed':False,
            'real_orders_authorized':False,'automatic_model_change':False,
            'formal_existing_acceptance_replaced':False,'empirical_edge_verified':False}
    if reasons:return result
    dates=[utc(r['issued_at_utc']).date().isoformat() for r in valid]
    diffs=[differences(r) for r in valid]
    for key in PREDICTION_KEYS:
        interval=block_interval(dates,[d[key] for d in diffs],policy)
        # Unknown events can reverse both direction and squared-error benefit.
        interval['full_denominator_lower']=fraction*interval['lower']-(1-fraction)
        interval['full_denominator_upper']=fraction*interval['upper']+(1-fraction)
        result['intervals'][key]=interval
    for regime in ('UP','DOWN'):
        part=[r for r in valid if r['regime']==regime]
        for key in PREDICTION_KEYS:
            result['intervals'][regime+'_'+key]=block_interval(
                [utc(r['issued_at_utc']).date().isoformat() for r in part],
                [differences(r)[key] for r in part],policy)
    if not promotion_blockers:
        for key in ECONOMIC_KEYS:
            result['intervals'][key]=block_interval(dates,[r['economic_deltas'][key] for r in valid],policy)
    pred=result['intervals']
    positive=all(pred[k]['full_denominator_lower']>(policy['brier_margin'] if k=='brier_vs_paired' else policy['accuracy_margin']) for k in PREDICTION_KEYS)
    negative=all(pred[k]['full_denominator_upper']<-(policy['brier_margin'] if k=='brier_vs_paired' else policy['accuracy_margin']) for k in PREDICTION_KEYS)
    regime_safe=all(pred[v+'_'+k]['lower']>=-(policy['brier_margin'] if k=='brier_vs_paired' else policy['accuracy_margin']) for v in ('UP','DOWN') for k in PREDICTION_KEYS)
    economic_gain=not promotion_blockers and all(pred[k]['lower']>policy['net_return_margin'] for k in ECONOMIC_KEYS)
    if positive and regime_safe and economic_gain:result['status']='REVIEW_PROMOTION'
    elif negative:result['status']='REVIEW_RETIREMENT'
    else:result['reasons'].append('no_reliable_preregistered_joint_gain_or_harm')
    result['review_only']=True
    result['inference_limit']='Asymptotic block-bootstrap approximation; stationarity/mixing and block-length adequacy require independent review. No finite-sample proof or live profitability claim.'
    return result

"""Fixed causal shadow selection. Historical calibration is a proxy, not proof.

No parameter search. Train -> purged calibration -> purged validation, with
nonoverlapping historical labels per horizon. Every future direction is scored
whether paper action is LONG or NO_TRADE. This module never executes orders.
"""
import math
import p1_adaptive_v2 as logistic

RULE={'confidence_up_min':.60,'calibration_min_n':100,'validation_min_n':100,
      'validation_selected_min_n':30,'validation_brier_max':.25,
      'fee_each_side_bps':10,'half_spread_each_side_bps':2.5,
      'slippage_each_side_bps':5,'edge_reserve_bps':10,
      'book_max_spread_bps':20,'book_max_age_seconds':30,
      'visible_depth_min_usdt':100,'doge_abs_return24h_max':.15,
      'btc_abs_return24h_max':.12,'train_max_anchors':4000,
      'iterations':80,'learning_rate':.09,'l2':.006}


def number(v):
    v=float(v)
    if not math.isfinite(v):raise ValueError('nonfinite selection input')
    return v


def select(p,meta,book):
    p=number(p)
    if not 0<=p<=1:raise ValueError('invalid probability')
    reasons=[]
    if p<RULE['confidence_up_min']:reasons.append('insufficient_up_confidence_or_down_forecast')
    for key,minimum in [('calibration_n',100),('validation_n',100),('validation_selected_n',30)]:
        if number(meta.get(key,0))<minimum:reasons.append('insufficient_'+key)
    if number(meta.get('validation_brier',1))>=RULE['validation_brier_max']:reasons.append('historical_validation_not_better_than_half_probability')
    cost=(2*(RULE['fee_each_side_bps']+RULE['half_spread_each_side_bps']+RULE['slippage_each_side_bps'])+RULE['edge_reserve_bps'])/10000
    lower=meta.get('selected_return_lower_proxy')
    if lower is None or number(lower)<=cost:reasons.append('insufficient_conservative_return_proxy_after_cost')
    for key,limit in [('doge_return_24h',.15),('btc_return_24h',.12)]:
        if abs(number(meta.get(key,1)))>limit:reasons.append('extreme_'+key)
    if not book:reasons.append('book_unverified')
    else:
        if not 0<=number(book['spread_bps'])<=RULE['book_max_spread_bps']:reasons.append('spread_too_wide')
        if not 0<=number(book['age_seconds'])<=RULE['book_max_age_seconds']:reasons.append('book_stale')
        if min(number(book['bid_depth_notional_usdt']),number(book['ask_depth_notional_usdt']))<RULE['visible_depth_min_usdt']:reasons.append('insufficient_visible_depth')
    return {'paper_action':'NO_TRADE' if reasons else 'PAPER_LONG',
            'reasons':reasons or ['fixed_shadow_conditions_met'],
            'roundtrip_cost_plus_reserve_fraction':cost,
            'selection_is_historical_proxy':True,'forward_probability_calibration_validated':False,
            'authorized_action':'NO_TRADE','real_orders_authorized':False,
            'does_not_remove_direction_from_accuracy_denominator':True}


def split_anchors(anchors,h):
    a=int(len(anchors)*.6);b=int(len(anchors)*.8)
    if a==0 or b>=len(anchors):raise ValueError('insufficient causal split')
    calibration=[i for i in anchors[a:b] if i+h<anchors[b]]
    train=[i for i in anchors[:a] if i+h<anchors[a]]
    if not train or not calibration:raise ValueError('empty purged split')
    return {'train':train,'calibration':calibration,'validation':anchors[b:]}


def return_lower(values):
    if len(values)<2:return None
    mean=sum(values)/len(values)
    variance=sum((x-mean)**2 for x in values)/(len(values)-1)
    if variance<=1e-20:return None
    return mean-1.959963984540054*math.sqrt(variance/len(values))


def logit(p):
    p=max(1e-9,min(1-1e-9,p));return math.log(p/(1-p))


def fit_candidate(times,prices,h):
    if h not in (1,24) or len(times)<720:raise ValueError('invalid fit horizon/history')
    anchors=list(range(24,len(times)-h,h))[-RULE['train_max_anchors']:]
    split=split_anchors(anchors,h)
    if len(split['train'])<100:raise ValueError('insufficient training')
    def x(i):return logistic.feat(prices,i)
    def y(i):return int(prices['DOGE-USDT'][i+h]>prices['DOGE-USDT'][i])
    weights=logistic.train([(x(i),y(i)) for i in split['train']],RULE['iterations'],RULE['learning_rate'],RULE['l2'])
    calibration=logistic.train([([1.,logit(logistic.probability(weights,x(i)))],y(i)) for i in split['calibration']],RULE['iterations'],RULE['learning_rate'],RULE['l2'])
    def probability(i):return logistic.probability(calibration,[1.,logit(logistic.probability(weights,x(i)))])
    vals=[(i,probability(i),y(i)) for i in split['validation']]
    selected=[prices['DOGE-USDT'][i+h]/prices['DOGE-USDT'][i]-1 for i,p,y_ in vals if p>=RULE['confidence_up_min']]
    last=len(times)-1
    examples={name:[{'anchor_ms':times[i],'features':x(i),'label_up':y(i),
                     'label_close_ms':times[i+h]+3600000,
                     'return_fraction':prices['DOGE-USDT'][i+h]/prices['DOGE-USDT'][i]-1} for i in indices]
              for name,indices in split.items()}
    return {'p_up':probability(last),'weights':weights,'platt_weights':calibration,
        'fit_examples':examples,'fit_examples_sha256':logistic.digest(examples),'prediction_features':x(last),
        'training_n':len(split['train']),'calibration_n':len(split['calibration']),
        'validation_n':len(vals),'validation_selected_n':len(selected),
        'validation_brier':sum((p-y_)**2 for i,p,y_ in vals)/len(vals),
        'selected_return_lower_proxy':return_lower(selected),
        'training_last_label_ms':times[split['validation'][-1]+h]+3600000,
        'split_anchors_sha256':logistic.digest(split),
        'split_boundaries':{k:[times[v[0]],times[v[-1]]] for k,v in split.items()},
        'doge_return_24h':prices['DOGE-USDT'][last]/prices['DOGE-USDT'][last-24]-1,
        'btc_return_24h':prices['BTC-USDT'][last]/prices['BTC-USDT'][last-24]-1,
        'historical_price_label_proxy_not_live_quote_calibration':True}

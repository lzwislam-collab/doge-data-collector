"""Read-only diagnostics V1: no forecast, protocol, score, fill or promotion writes.

Probability diagnostics are descriptive, not fitted calibration or significance
tests. Costs are declared assumptions on prospective book observations. Frozen
P1/P2 remain the sole formal evaluators. This file has its own output namespace.
"""
import csv
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import forward_execution_evidence as execution

ROOT = Path(__file__).resolve().parent
OUT = 'data/forward_diagnostics_v1'
SOURCES = {
    'production_registry':'data/direction_v42/forward_registry.csv',
    'production_scores':'data/direction_v42/forward_scores.csv',
    'adaptive_registry':'data/stage5/p1_adaptive_v2/registry.csv',
    'adaptive_scores':'data/stage5/p1_adaptive_v2/scores.csv',
    'adaptive_protocol':'data/stage5/p1_adaptive_v2_protocol.json',
    'adaptive_report':'data/stage5/p1_adaptive_v2/latest.json',
    'operations':'data/p0_operations/latest.json',
    'official':'data/stage5/forward_validation_v2/latest.json',
    'execution':'data/plus_evidence_v1_0/execution/observations.jsonl',
    'activation':'data/plus_evidence_v1_0/execution/observations.activation.json',
}


def utc(s):
    return execution.stamp(s)


def finite(value, low=0, high=None):
    value=float(value)
    if not math.isfinite(value) or value<low or (high is not None and value>high):
        raise ValueError('invalid numeric value')
    return value


def unique(rows):
    ids=[r['forecast_id'] for r in rows]
    if any(not x for x in ids) or len(ids)!=len(set(ids)): raise ValueError('duplicate or empty forecast ID')


def deadlines(registry, scores, now):
    unique(registry)
    out=[]
    for f in registry:
        if utc(f['issued_at_utc'])>now: raise ValueError('future issuance')
        target=utc(f['target_utc']); end=target+timedelta(minutes=25)
        if scores.get(f['forecast_id'],{}).get('status')=='scored': continue
        left=(end-now).total_seconds()
        state=('MISSED_WINDOW' if left<0 else 'DUE_URGENT' if target<=now and left<=600
               else 'DUE_OPEN' if target<=now else 'WAIT_TARGET')
        out.append({'forecast_id':f['forecast_id'],'model_id':f['model_id'],
                    'target_utc':target.isoformat(),'deadline_utc':end.isoformat(),
                    'seconds_left':left,'state':state,'historical_backfill_allowed':False})
    return sorted(out,key=lambda r:(r['target_utc'],r['forecast_id']))


def calibration(registry, scores, now, effective):
    unique(registry)
    if len({r['horizon_hours'] for r in registry})>1: raise ValueError('horizons cannot be pooled')
    selected=[]; reserved=None; eligible=[]; qualified=[]
    group=sorted((r for r in registry if utc(r['issued_at_utc'])>=effective),
                 key=lambda r:(r['issued_at_utc'],r['forecast_id']))
    for r in group:
        p=finite(r['p_up'],0,1); issued=utc(r['issued_at_utc']); target=utc(r['target_utc'])
        if utc(r['training_last_label_utc'])>issued: raise ValueError('future training label')
        if r.get('last_confirmed_close_utc') and utc(r['training_last_label_utc'])>utc(r['last_confirmed_close_utc']):
            raise ValueError('training label after confirmed feature clock')
        if issued>now or target!=issued+timedelta(hours=int(r['horizon_hours'])):
            raise ValueError('invalid forecast chronology')
        if r['direction'] not in ('UP','DOWN') or r['btc_momentum'] not in ('UP','DOWN'):
            raise ValueError('invalid ex ante direction')
        if reserved is not None and issued<reserved: continue
        selected.append(r); reserved=target+timedelta(minutes=25)
        if reserved>now: continue
        eligible.append(r); s=scores.get(r['forecast_id'])
        if not s or s['status']!='scored': continue
        if not target<=utc(s['target_quote_utc'])<=reserved: raise ValueError('outside target window')
        if s['realized_up'] not in ('UP','DOWN'): raise ValueError('invalid outcome')
        y=int(s['realized_up']=='UP'); b=(p-y)**2
        if abs(finite(s['brier'],0,1)-b)>1e-9: raise ValueError('stored Brier disagrees with outcome')
        qualified.append((r,p,y,b))
    n=len(qualified); due=len(eligible); hits=sum((r['direction']=='UP')==bool(y) for r,p,y,b in qualified)
    bins=[]; ece=0
    for i in range(10):
        part=[(p,y) for _,p,y,_ in qualified if min(9,int(p*10))==i]
        avg=sum(p for p,y in part)/len(part) if part else None
        freq=sum(y for p,y in part)/len(part) if part else None
        bins.append({'lower':i/10,'upper':(i+1)/10,'n':len(part),'mean_probability':avg,'up_frequency':freq})
        if part: ece+=len(part)/n*abs(avg-freq)
    mean=sum(b for _,p,y,b in qualified)/n if n else None
    holdout=sum(finite(r['walkforward_holdout_brier'],0,1) for r,p,y,b in qualified)/n if n else None
    return {'status':'DESCRIPTIVE_ONLY' if n else 'WAIT_FUTURE_DATA',
            'selected':len(selected),'overlap_excluded':len(group)-len(selected),
            'due':due,'scored':n,'missing':due-n,'quote_coverage_pct':100*n/due if due else None,
            'accuracy_pct':100*hits/n if n else None,
            'accuracy_if_missing_wrong_pct':100*hits/due if due else None,
            'brier_mean':mean,'brier_half_probability_baseline':.25 if n else None,
            'brier_skill_vs_half':1-mean/.25 if n else None,
            'log_loss':sum(-math.log(max(1e-15,min(1-1e-15,p if y else 1-p))) for _,p,y,b in qualified)/n if n else None,
            'reliability_bins_fixed_10':bins,'ece_descriptive':ece if n else None,
            'exante_btc_regimes':{v:{'n':len(part),'brier_mean':sum(x[3] for x in part)/len(part) if part else None}
                for v in ('UP','DOWN') for part in [[x for x in qualified if x[0]['btc_momentum']==v]]},
            'mean_historical_holdout_brier':holdout,
            'forward_minus_holdout_brier':mean-holdout if n else None,
            'overfit_gap_is_comparable_estimate':False,
            'caveat':'Historical candle labels differ from live ticker labels; gap is a monitoring diagnostic. Brier includes discrimination and uncertainty, not calibration alone. No fitted recalibration or tuned bin boundaries.',
            'provenance_scope':'Stored shadow scores checked for chronology and arithmetic; this diagnostic does not independently requalify public witnesses or replace the frozen P1 evaluator',
            'promotion_authorized':False,'validated_70pct':False}


def cost_scenario(ask, bid, notional, fee_bps, slippage_bps):
    ask=finite(ask,1e-15); bid=finite(bid,1e-15); notional=finite(notional,1e-15)
    fee=finite(fee_bps,0,500)/10000; slip=finite(slippage_bps,0,500)/10000
    units=notional/(ask*(1+slip)*(1+fee))
    final=units*bid*(1-slip)*(1-fee)
    return {'net_return_pct':(final/notional-1)*100,'final_equity_usdt':final,
            'fee_each_side_bps':fee_bps,'extra_slippage_each_side_bps':slippage_bps,
            'spread_included_in_ask_bid':True,'actual_fill':False}


def execution_costs(records):
    grouped={}
    for r in records:
        for obs in r['observations']:
            grouped.setdefault(obs['forecast_id'],[]).append((r,obs))
    intervals=[]
    for fid,part in sorted(grouped.items()):
        entry=next((o for r,o in part if o['kind']=='entry_quote'),None)
        exit_=next((o for r,o in part if o['kind']=='exit_quote'),None)
        missing=any(o['kind'] in ('missed_entry','missed_exit','legacy_excluded') for r,o in part)
        gap=any(o.get('path_gap',False) for r,o in part)
        out={'forecast_id':fid,'status':'EXCLUDED_MISSING_OR_LEGACY' if missing else 'WAIT_EXIT',
             'strategy_net_return_pct':None,'hold_net_return_pct':None,'cash_net_return_pct':0,
             'path_gap':gap,'profit_validated':False,'continuous_drawdown_verified':False}
        if entry and exit_ and not missing:
            direction=entry['forecast']['doge_reversal_direction']
            if direction not in ('UP','DOWN'): raise ValueError('invalid execution direction')
            ask=entry.get('indicative_entry_vwap',entry['ask'])
            bid=exit_.get('indicative_bid_vwap',exit_['bid'])
            nominal=cost_scenario(ask,bid,100,10,5)
            stress=cost_scenario(ask,bid,100,20,10)
            out.update(status='INDICATIVE_COST_PROXY',direction=direction,
                strategy_net_return_pct=nominal['net_return_pct'] if direction=='UP' else 0,
                double_cost_strategy_net_return_pct=stress['net_return_pct'] if direction=='UP' else 0,
                hold_net_return_pct=nominal['net_return_pct'],
                sampled_gross_drawdown_pct=max(o.get('sampled_drawdown_pct',0) for r,o in part),
                cost_assumptions=nominal,
                hold_is_depth_sweep_verified=direction=='UP')
        intervals.append(out)
    return {'status':'DESCRIPTIVE_ONLY' if intervals else 'WAIT_FUTURE_DATA','intervals':intervals,
            'notional_per_independent_observation_usdt':100,'compounded_portfolio_return':None,
            'continuous_drawdown_verified':False,'profit_validated':False,
            'caveat':'Separate observation diagnostics, not frozen P2 ledger. Overlapping forecasts are never compounded. DOWN hold uses top-of-book proxy; depth, fills, latency, impact and actual account fees are unverified.'}


def read_csv(path, required=False, required_columns=('forecast_id',)):
    if not path.exists():
        if required: raise ValueError('required CSV missing: '+str(path))
        return []
    with path.open(encoding='utf-8',newline='') as f:
        reader=csv.DictReader(f)
        header=reader.fieldnames
        if not header or len(header)!=len(set(header)) or not set(required_columns)<=set(header):
            raise ValueError('invalid CSV header: '+str(path))
        rows=list(reader)
        if any(None in r or any(v is None for v in r.values()) for r in rows):
            raise ValueError('malformed CSV row: '+str(path))
    unique(rows)
    return rows


def run(root=ROOT, now=None):
    now=utc(now or datetime.now(timezone.utc)); root=Path(root)
    hashes={key:{'path':p,'sha256':hashlib.sha256((root/p).read_bytes()).hexdigest() if (root/p).exists() else None}
            for key,p in SOURCES.items()}
    reg=read_csv(root/SOURCES['production_registry'],required=True,required_columns=('forecast_id','model_id','issued_at_utc','target_utc'))
    scores=read_csv(root/SOURCES['production_scores'],required=True,required_columns=('forecast_id','status'))
    adaptive=read_csv(root/SOURCES['adaptive_registry']); a_scores=read_csv(root/SOURCES['adaptive_scores'])
    policy=json.loads((root/SOURCES['adaptive_protocol']).read_text(encoding='utf-8'))
    eff=utc(policy['effective_from_utc'])
    due=deadlines(reg,{r['forecast_id']:r for r in scores},now)
    due+=deadlines(adaptive,{r['forecast_id']:r for r in a_scores},now)
    chain=execution.read_chain(root/SOURCES['execution'])
    # Verify activation without creating or rewriting it.
    if chain:
        activation_path=execution.activation_path(root/SOURCES['execution'])
        activation=json.loads(activation_path.read_text(encoding='utf-8'))
        copy=dict(activation); expected=copy.pop('activation_sha256')
        if execution.digest(copy)!=expected or any(r.get('activation_sha256')!=expected for r in chain):
            raise ValueError('execution activation integrity mismatch')
    result={'schema_version':1,'diagnostic_version':'DOGE_FORWARD_DIAGNOSTICS_V1',
        'status':'ok','audited_at_utc':now.isoformat(),'inputs':hashes,
        'deadline_radar':due,'urgent_due_count':sum(x['state']=='DUE_URGENT' for x in due),
        'adaptive_calibration':{str(h)+'h':calibration([r for r in adaptive if int(r['horizon_hours'])==h],
            {r['forecast_id']:r for r in a_scores},now,eff) for h in (1,24)},
        'execution_cost_diagnostics':execution_costs(chain),
        'action':'NO_TRADE','reason':'Diagnostic layer grants no trading or model promotion authority',
        'frozen_protocols_changed':False,'formal_scores_replaced':False,'external_retention_verified':False,
        'future_adaptive_activation_utc':eff.isoformat(),
        'method_sources':['https://scikit-learn.org/stable/modules/calibration.html',
            'https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html',
            'https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows']}
    if any(hashlib.sha256((root/p).read_bytes()).hexdigest()!=hashes[k]['sha256'] for k,p in SOURCES.items() if hashes[k]['sha256']):
        raise ValueError('input changed during read-only diagnostics')
    body=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n'
    base=root/OUT; (base/'snapshots').mkdir(parents=True,exist_ok=True)
    snap=base/'snapshots'/(hashlib.sha256(body.encode()).hexdigest()+'.json')
    if snap.exists() and snap.read_bytes()!=body.encode(): raise ValueError('diagnostic snapshot tampered')
    if not snap.exists():
        with snap.open('x',encoding='utf-8',newline='\n') as f: f.write(body)
    temporary=base/'latest.json.tmp'; temporary.write_text(body,encoding='utf-8'); os.replace(temporary,base/'latest.json')
    report=root/'reports/DOGE_FORWARD_DIAGNOSTICS_V1.md'
    report.write_text('# DOGE 增量诊断 V1\n\n结论：仅诊断；NO_TRADE，不授权晋级或盈利声明。\n\n'
        +'来源：本仓库真实登记/评分、Plus公开后盘口哈希链；各输入SHA256见JSON。截止UTC：'+now.isoformat()
        +'。\n\n到期窗口即将关闭：'+str(result['urgent_due_count'])+'。历史漏发/漏采保留，不补造。\n\n'
        +'P1 V2生效：'+eff.isoformat()+'；生效前和零成熟样本均WAIT。固定分箱/Brier/log loss/分市况/缺评最坏界仅作描述；不拟合新校准器。\n\n'
        +'P2补充：公开后ask/bid或可见深度VWAP，单侧手续费10bp、额外滑点5bp、双倍压力；均研究假设。独立100 USDT区间不复利，不替代冻结P2。盘口不是成交，采样回撤不是连续最大回撤。\n',encoding='utf-8')
    return result


if __name__=='__main__':
    try:
        r=run(); print(json.dumps({'status':r['status'],'action':r['action'],'urgent_due_count':r['urgent_due_count']}))
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error':str(exc)})); raise SystemExit(1)

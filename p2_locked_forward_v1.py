"""Frozen-start forward PAPER cohort; no capture, edits to prior books, or orders.

Read-only derived audit from the existing sealed V3.1 original issued forecasts,
public paired decisions and captured original depth snapshots. No auto-reset
after a missed book, invalid interval, or market-path gap.
"""
import hashlib
import json
import math
import subprocess
from datetime import datetime,timedelta,timezone
from pathlib import Path

import p2_paired_execution_v3_1 as engine

ROOT=Path(__file__).resolve().parent
PROTOCOL='data/stage5/p2_locked_forward_v1_protocol.json'
LATEST='data/p2_locked_forward_v1/latest.json'
EXPECTED_ID='DOGE_P2_LOCKED_FORWARD_COHORT_20261014_0000'


def utc(x):
    dt=engine.utc(x) if isinstance(x,str) else x
    if dt.tzinfo is None:raise ValueError('naive timestamp')
    return dt.astimezone(timezone.utc)


def verify_protocol(root, now, protocol):
    if (protocol.get('protocol_id')!=EXPECTED_ID or
        protocol.get('effective_from_utc')!='2026-10-14T00:00:00+00:00' or
        protocol.get('read_only') is not True or
        protocol.get('no_auto_reset') is not True or
        protocol.get('initial_usdt')!=engine.INITIAL or
        protocol.get('max_gap_seconds')!=engine.GAP_SECONDS or
        protocol.get('horizons_hours')!=[1,24] or
        protocol.get('minimum_observation_days')!=30 or
        protocol.get('minimum_completed_intervals')!=100 or
        protocol.get('minimum_issuance_and_target_coverage_pct')!=90):
        raise ValueError('forward cohort protocol changed')
    if protocol.get('model_production_unchanged') is not True or protocol.get('cash_benchmark_pct')!=0:
        raise ValueError('model/economic scope changed')
    # A code or protocol revision after deployment requires a separately dated new cohort.
    blob=protocol.get('locked_code_git_blob_sha')
    if not isinstance(blob,str) or len(blob)!=40:
        raise ValueError('forward code freeze not registered')
    if (root/'.git').is_dir():
        result=subprocess.run(['git','hash-object','p2_locked_forward_v1.py'],cwd=root,
                              text=True,capture_output=True,check=True)
        if result.stdout.strip()!=blob:raise ValueError('forward implementation changed after freeze')
    return utc(protocol['effective_from_utc'])


def by_horizon(items,h):
    # All issues are reserved before knowing source validity, action or outcome.
    return engine.reserve([i for i in items if int(i['horizon_hours'])==h])


def assess(items,book_events,books,now,effective,protocol):
    """All mandatory score denominators and gaps are fixed before looking at profits."""
    now=utc(now);effective=utc(effective)
    if now<effective:
        return {'status':'WAIT_ACTIVATION','effective_from_utc':effective.isoformat(),
                'evaluated_at_utc':now.isoformat(),'portfolios':{},
                'economic_edge_validated':False,'allow_model_promotion':False}
    # The cohort never treats books collected before activation as forward evidence.
    future=[b for b in books if utc(b['quote']['quote_utc'])>=effective]
    stamps=[utc(b['quote']['quote_utc']) for b in future]
    if any(b<=a for a,b in zip(stamps,stamps[1:])):
        raise ValueError('out-of-order fresh book')
    segments=[];gaps=[]
    if stamps:
        if (stamps[0]-effective).total_seconds()>engine.GAP_SECONDS:
            gaps.append({'from_utc':effective.isoformat(),
                         'to_utc':stamps[0].isoformat(),
                         'duration_seconds':(stamps[0]-effective).total_seconds(),
                         'kind':'activation_to_first_quote'})
        for a,b in zip(stamps,stamps[1:]):
            if (b-a).total_seconds()>engine.GAP_SECONDS:
                gaps.append({'from_utc':a.isoformat(),'to_utc':b.isoformat(),
                             'duration_seconds':(b-a).total_seconds(),'kind':'missing_book_path'})
    tail=(now-stamps[-1]).total_seconds() if stamps else (now-effective).total_seconds()
    if tail<0:raise ValueError('future book')
    if tail>engine.GAP_SECONDS:
        gaps.append({'from_utc':(stamps[-1] if stamps else effective).isoformat(),
                     'to_utc':now.isoformat(),'duration_seconds':tail,
                     'kind':'stale_tail_currently_open'})
    # Locked cohort: even historical gaps that later heal remain observed blockers.
    recovered=engine.replay(items, future, now)
    days=(now-effective).total_seconds()/86400
    slots=max(0,int((now-effective-timedelta(minutes=45)).total_seconds()//3600)+1)
    result={}
    for h in (1,24):
        key=f'{h}h';all_h=[x for x in items if int(x['horizon_hours'])==h]
        selected=by_horizon(items,h)
        due=[x for x in selected if utc(x['target_utc'])+timedelta(minutes=25)<=now]
        first_by_bin={}
        for x in all_h:
            k=int((utc(x['issued_at_utc'])-effective).total_seconds()//3600)
            if 0<=k<slots:first_by_bin.setdefault(k,[]).append(x)
        issued=sum(bool(first_by_bin.get(k)) for k in range(slots))
        verified=sum(any(x.get('paired_audit_verified') is True for x in first_by_bin.get(k,[])) for k in range(slots))
        p=recovered[key]
        completed=int(p['completed_due_intervals'])
        quote_cov=100*completed/len(due) if due else None
        issue_cov=100*issued/slots if slots else None
        verified_cov=100*verified/slots if slots else None
        sret=p['strategy']['prefix_net_return_pct']
        hret=p['hold']['prefix_net_return_pct']
        dret=p['double_cost']['prefix_net_return_pct']
        dhret=p['double_cost_hold']['prefix_net_return_pct']
        gain=None if sret is None or hret is None else sret-hret
        cash_gain=sret
        stressed_gain=None if dret is None or dhret is None else dret-dhret
        # Positive returns are not sufficient to prove a causal trading edge.
        ready=bool(days>=30 and completed>=100 and slots and
                   issue_cov>=90 and verified_cov>=90 and quote_cov is not None and quote_cov>=90
                   and not gaps and p['path_gap_count']==0 and p['missing_mark_count']==0
                   and not p['blocked_forecast_id'] and gain is not None and gain>0
                   and cash_gain is not None and cash_gain>0
                   and stressed_gain is not None and stressed_gain>0 and dret is not None and dret>0)
        result[key]={
            'expected_closed_hourly_issue_slots':slots,
            'actual_issued_slots':issued,'verified_paired_source_slots':verified,
            'missing_issue_slots':slots-issued,
            'issuance_coverage_pct':issue_cov,'verified_pair_coverage_pct':verified_cov,
            'fixed_precommitted_nonoverlap_selected':len(selected),
            'matured_due_intervals':len(due),'completed_due_intervals':completed,
            'original_due_book_coverage_pct':quote_cov,
            'failed_or_unscored_due_intervals':len(due)-completed,
            'observation_days':round(days,4),
            'paper_long_completed':p['paper_long_completed'],
            'no_trade_completed':p['no_trade_completed'],
            'blocked_forecast_id':p['blocked_forecast_id'],
            'blocked_reason':p['blocked_reason'],
            'original_paper_path_gaps':p['path_gap_count'],
            'missing_mark_count':p['missing_mark_count'],
            'strategy_return_pct':sret,'hold_return_pct':hret,
            'cash_return_pct':0,'stressed_strategy_return_pct':dret,
            'stressed_hold_return_pct':dhret,
            'strategy_minus_hold_percentage_points':gain,
            'strategy_minus_cash_percentage_points':cash_gain,
            'stressed_strategy_minus_hold_percentage_points':stressed_gain,
            'sampled_drawdown_pct':p['strategy']['sampled_drawdown_pct'],
            'continuous_max_drawdown_verified':False,
            'fee_slippage_spread_assumptions':'Reused frozen V3.1, not actual fills',
            'eligible_for_independent_paper_review_only':ready,
            'economic_edge_validated':False
        }
    return {'status':'WAIT_FUTURE_EVIDENCE' if days<30 or not all(v['eligible_for_independent_paper_review_only'] for v in result.values()) else 'REVIEW_ONLY',
            'effective_from_utc':effective.isoformat(),'evaluated_at_utc':now.isoformat(),
            'source':'frozen V3.1 append-only raw depth observations and public paired decisions',
            'observed_depth_books_after_lock':len(future),
            'gap_count_including_open_tail':len(gaps),
            'gap_records':gaps,
            'no_auto_reset':True,
            'no_historical_gap_repair':True,
            'completed_without_missing_path':not gaps,
            'portfolios':result,
            'economic_edge_validated':False,
            'allow_model_promotion':False,
            'real_orders_enabled':False,
            'warning':'This independent cohort starts at fixed activation and never discards failed cases; sample drawdown is not continuous maximum drawdown.'}


def run(root=ROOT,now=None):
    root=Path(root);now=utc(now or datetime.now(timezone.utc))
    protocol=json.loads((root/PROTOCOL).read_text(encoding='utf-8'))
    effective=verify_protocol(root,now,protocol)
    engine.guard(root)
    if now<effective:
        result=assess([],[],[],now,effective,protocol)
    else:
        # Reuse existing immutable P2 book and original forecast/cost mechanics.
        events=engine.history_guard(root)
        original_effective=utc(json.loads((root/engine.POLICY).read_text())['effective_from_utc'])
        prior={}
        for event in events:
            for item in event.get('source_items',[]):
                fid=item['forecast_id']
                if item.get('source_verified'):
                    if fid in prior and prior[fid]!=item:raise ValueError('original source was rewritten')
                    prior[fid]=item
        source=engine.source_items(root,now,original_effective,prior)
        selected=[x for x in source if utc(x['issued_at_utc'])>=effective]
        future_events=[e for e in events if utc(e['quote_utc'])>=effective]
        fresh=list(engine.event_books(root,future_events))
        result=assess(selected,future_events,fresh,now,effective,protocol)
    result.update(protocol_id=EXPECTED_ID,protocol_version_sha256=hashlib.sha256((root/PROTOCOL).read_bytes()).hexdigest(),
                  historical_v3_1_untouched=True,
                  independent_retention_verified=False,
                  complete_PIT_verified=False)
    path=root/LATEST;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return result


def main():
    from sys import exit
    try:
        result=run()
        print(json.dumps({'status':result['status'],'books':result.get('observed_depth_books_after_lock',0),
                          'gap_count':result.get('gap_count_including_open_tail',0),
                          'economic_edge_validated':False}))
    except Exception as exc:
        # A failed run never reports evidence acceptance or silently changes the start.
        print(json.dumps({'status':'BLOCKED','error':type(exc).__name__+': '+str(exc)[:250]}))
        exit(1)


if __name__=='__main__':main()

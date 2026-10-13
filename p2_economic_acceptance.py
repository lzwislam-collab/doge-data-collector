"""Read-only supplementary V3.1 PAPER economic review; never capture or trade.

Report assertions are not exchange evidence. Frozen engine/protocol remain
unchanged. A positive paper-review result cannot authorize economic acceptance,
real orders, or automatic promotion. Caller supplies report and aware UTC time.
"""
from copy import deepcopy
import math
import json
import csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
import p2_paired_execution_v3_1 as engine

VERSION = 'DOGE_P2_PAIRED_EXECUTION_V3_1_20261013_0000'
COVERAGE = ('selected_intervals', 'selected_source_intervals', 'verified_selected_source_intervals',
    'matured_due_intervals', 'completed_due_intervals', 'completed_prefix_intervals',
    'expected_closed_source_hour_slots', 'original_source_issue_slots', 'verified_pair_slots',
    'missing_original_or_verified_pair_slots')
ACCOUNTS = ('strategy', 'hold', 'double_cost', 'double_cost_hold')


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('missing/nonfinite numeric evidence')
    return float(value)


def _json_safe(value):
    # Invalid observations remain null, never zero; blockers retain the defect.
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {k:_json_safe(v) for k,v in value.items()}
    if isinstance(value,list):return [_json_safe(v) for v in value]
    return value


def _clock(value):
    if not isinstance(value,(str,datetime)):raise ValueError('timestamp missing/invalid')
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z','+00:00'))
    if stamp.tzinfo is None:raise ValueError('timezone missing')
    return stamp.astimezone(timezone.utc)


def _leg(leg, side, stress):
    if not isinstance(leg,dict) or leg.get('actual_fill') is not False:raise ValueError('missing/nonpaper leg')
    costs=leg['costs'];prefix='entry' if side=='buy' else 'exit'
    keys=(side+'_fee_usdt',prefix+'_extra_slippage_usdt',prefix+'_stress_extra_spread_usdt')
    values={k:_number(costs[k]) for k in keys}
    if set(costs)!=set(keys) or any(v<0 for v in values.values()):raise ValueError('incomplete/invalid leg cost components')
    gross=engine.positive(_number(leg['gross_usdt']));fee=values[keys[0]];slip=values[keys[1]];spread=values[keys[2]]
    rate=2 if stress else 1
    base=gross+spread if side=='buy' else gross-spread
    fee_base=base+slip if side=='buy' else base-slip
    if not math.isclose(slip,base*engine.SLIP*rate,abs_tol=1e-8) or not math.isclose(fee,fee_base*engine.FEE*rate,abs_tol=1e-8):raise ValueError('leg friction arithmetic mismatch')
    if not stress and spread!=0:raise ValueError('nominal extra spread')
    cash=_number(leg['cash_spent_usdt' if side=='buy' else 'cash_usdt'])
    expected=gross+sum(values.values()) if side=='buy' else gross-sum(values.values())
    if not math.isclose(cash,expected,abs_tol=1e-8):raise ValueError('leg cash arithmetic mismatch')
    if side=='buy' and not math.isclose(engine.positive(leg['quantity'])*engine.positive(leg['vwap']),gross,abs_tol=1e-8):raise ValueError('quantity/vwap mismatch')
    return values


def _portfolio(p, now, horizon, sources):
    issues = []
    def number(value, label):
        try:return _number(value)
        except (ValueError, TypeError):issues.append(label + ': missing/invalid');return None
    coverage = {k: deepcopy(p.get(k)) for k in COVERAGE}
    counts = {}
    for k, v in coverage.items():
        n = number(v, k)
        if n is not None and (n < 0 or n != int(n)):issues.append(k + ': invalid count');n = None
        counts[k] = n
    ratios = {}
    for label, numerator, denominator in (
        ('selected_source_verification_pct', 'verified_selected_source_intervals', 'selected_source_intervals'),
        ('paired_issue_coverage_pct', 'verified_pair_slots', 'expected_closed_source_hour_slots'),
        ('due_book_interval_coverage_pct', 'completed_due_intervals', 'matured_due_intervals')):
        a, b = counts[numerator], counts[denominator]
        ratios[label] = 100*a/b if a is not None and b is not None and b > 0 else None
        if a is None or b is None or b <= 0 or a > b or a/b < .9:issues.append(label + ': insufficient coverage')
    for a, b in (('completed_due_intervals', 'completed_prefix_intervals'),
                 ('completed_prefix_intervals', 'selected_intervals'),
                 ('matured_due_intervals', 'selected_intervals'),
                 ('verified_pair_slots', 'original_source_issue_slots'),
                 ('original_source_issue_slots', 'expected_closed_source_hour_slots')):
        if counts[a] is not None and counts[b] is not None and counts[a] > counts[b]:issues.append(a + ': inconsistent denominator')
    if counts['selected_intervals'] != counts['selected_source_intervals']:issues.append('selected source/reservation mismatch')
    if all(counts[k] is not None for k in ('missing_original_or_verified_pair_slots','expected_closed_source_hour_slots','verified_pair_slots')):
        if counts['missing_original_or_verified_pair_slots'] != counts['expected_closed_source_hour_slots']-counts['verified_pair_slots']:
            issues.append('missing source slots mismatch')
    age = number(p.get('observation_days'), 'observation_days')
    if age is None or age < 30 or counts['completed_due_intervals'] is None or counts['completed_due_intervals'] < 100:
        issues.append('immature: requires 30 observation days and 100 completed due intervals')
    if p.get('blocked_forecast_id') or p.get('blocked_reason'):issues.append('execution prefix blocked')
    gaps = {k:deepcopy(p.get(k)) for k in ('path_gap_count','missing_mark_count','tail_path_gap','tail_gap_seconds','latest_mark_fresh','last_valid_mark_utc')}
    for k in ('path_gap_count','missing_mark_count','tail_gap_seconds'):
        if number(p.get(k), k) != 0:issues.append(k + ': unresolved gap')
    if p.get('tail_path_gap') is not False or p.get('latest_mark_fresh') is not True:issues.append('tail mark not fresh')
    try:
        if not 0 <= (now-_clock(p['last_valid_mark_utc'])).total_seconds() <= engine.GAP_SECONDS:raise ValueError('stale/future mark')
    except (ValueError, TypeError, KeyError):issues.append('mark timestamp missing/stale/future')
    account_facts, returns = {}, {}
    for name in ACCOUNTS:
        account = p.get(name) if isinstance(p.get(name),dict) else {}
        ret = number(account.get('prefix_net_return_pct'), name + ' return')
        initial = number(account.get('initial_equity_usdt'), name + ' initial')
        equity = number(account.get('prefix_liquidation_equity_usdt'), name + ' equity')
        if initial is not None:
            try:engine.positive(initial)
            except ValueError:issues.append(name + ': invalid initial');initial=None
        if initial is not None and equity is not None and ret is not None and not math.isclose((equity/initial-1)*100, ret,abs_tol=1e-8):
            issues.append(name + ': return/equity mismatch')
        costs = account.get('recorded_trade_cost_components_usdt')
        total = None
        if isinstance(costs,dict):
            values = [number(v, name + ' cost '+k) for k,v in costs.items()]
            if all(v is not None and v >= 0 for v in values):total=sum(values)
            else:issues.append(name + ': invalid costs')
        else:issues.append(name + ': missing cost ledger')
        for side in ('buy','sell'):
            legs = number(account.get(side+'_legs'),name+' '+side+'_legs')
            if legs is not None and (legs<0 or legs != int(legs)):issues.append(name + ': invalid leg count')
            if legs is not None and legs > 0:
                prefix='entry' if side=='buy' else 'exit'
                for key in (side+'_fee_usdt',prefix+'_extra_slippage_usdt',prefix+'_stress_extra_spread_usdt'):
                    if not isinstance(costs,dict) or costs.get(key) is None:issues.append(name + ': missing '+key)
        if name in ('hold','double_cost_hold') and (account.get('buy_legs') != 1 or account.get('sell_legs') != 0):
            issues.append(name + ': continuous hold must buy once and retain position')
        if account.get('actual_fill') is not False:issues.append(name + ': not a paper-only account')
        returns[name]=ret
        account_facts[name]={'prefix_net_return_pct':ret,'prefix_liquidation_equity_usdt':equity,
            'recorded_trade_cost_components_usdt':deepcopy(costs),'recorded_trade_cost_total_usdt':total,
            'sampled_drawdown_pct':number(account.get('sampled_drawdown_pct'),name+' sampled drawdown'),
            'buy_legs':account.get('buy_legs'),'sell_legs':account.get('sell_legs')}
    ledger = p.get('ledger')
    end = None
    if not isinstance(ledger,list) or len(ledger) != counts['completed_prefix_intervals'] or not ledger:issues.append('missing/inconsistent same-period ledger')
    else:
        previous = None;reserved_until=None;seen=set();due=0
        totals={name:{} for name in ('strategy','double_cost')};leg_counts={name:{'buy':0,'sell':0} for name in totals}
        try:
            for row in ledger:
                stamp = _clock(row['exit_quote_utc'])
                entry=_clock(row['entry_quote_utc']);fid=row['forecast_id']
                if not isinstance(fid,str) or not fid or fid in seen:raise ValueError('missing/duplicate forecast id')
                seen.add(fid)
                if not entry<stamp<=now or (previous is not None and stamp<=previous) or row.get('actual_fill') is not False:raise ValueError('invalid ledger clock/scope')
                context=sources.get(fid) if isinstance(sources,dict) else None
                if not isinstance(context,dict):raise ValueError('missing original source timing')
                issued=_clock(context['issued_at_utc']);target=_clock(context['target_utc']);public=_clock(context['observed_public_utc'])
                if int(context['horizon_hours'])!=horizon or target!=issued+timedelta(hours=horizon):raise ValueError('wrong source horizon/target')
                if not issued<=public<entry<target<=stamp or (entry-public).total_seconds()>engine.MAX_ENTRY_SECONDS or stamp>target+timedelta(minutes=25):raise ValueError('source/entry/exit window mismatch')
                if reserved_until is not None and issued<reserved_until:raise ValueError('overlapping reservation')
                reserved_until=target+timedelta(minutes=25)
                due+=int(reserved_until<=now)
                action=row['paper_action']
                if action not in ('PAPER_LONG','NO_TRADE'):raise ValueError('invalid paper action')
                for name in totals:
                    for side,field in (('buy','entry_legs'),('sell','exit_legs')):
                        leg=row[field].get(name)
                        if action=='NO_TRADE':
                            if leg is not None:raise ValueError('no-trade leg present')
                            continue
                        for k,v in _leg(leg,side,name=='double_cost').items():totals[name][k]=totals[name].get(k,0)+v
                        leg_counts[name][side]+=1
                previous=stamp
            if due!=counts['completed_due_intervals']:raise ValueError('completed due count/timing mismatch')
            open_interval=p.get('open_interval')
            if open_interval:
                if open_interval.get('paper_action') not in ('PAPER_LONG','NO_TRADE'):raise ValueError('invalid open paper action')
                if open_interval['paper_action']=='PAPER_LONG':
                    for name in totals:
                        for k,v in _leg(open_interval['entry_legs'][name],'buy',name=='double_cost').items():totals[name][k]=totals[name].get(k,0)+v
                        leg_counts[name]['buy']+=1
            for name in totals:
                account=p[name];recorded=account['recorded_trade_cost_components_usdt']
                if set(recorded)!=set(totals[name]) or any(not math.isclose(_number(recorded[k]),v,abs_tol=1e-8) for k,v in totals[name].items()):raise ValueError('account/ledger cost totals mismatch')
                if any(account[side+'_legs']!=n for side,n in leg_counts[name].items()):raise ValueError('account/ledger leg count mismatch')
            for name in ('hold','double_cost_hold'):
                c=p[name]['recorded_trade_cost_components_usdt'];rate=2 if name=='double_cost_hold' else 1
                slip=_number(c['entry_extra_slippage_usdt']);fee=_number(c['buy_fee_usdt']);spread=_number(c['entry_stress_extra_spread_usdt'])
                base=slip/(engine.SLIP*rate)
                if slip<=0 or spread<0 or (rate==1 and spread!=0) or not math.isclose(fee,(base+slip)*engine.FEE*rate,abs_tol=1e-8):raise ValueError('hold cost arithmetic mismatch')
            end=ledger[-1]['exit_quote_utc']
            for name in ACCOUNTS:
                value=_number(ledger[-1]['equity_usdt'][name]);expected=account_facts[name]['prefix_liquidation_equity_usdt']
                if expected is None or not math.isclose(value,expected,abs_tol=1e-8):issues.append(name+': common prefix equity mismatch')
        except (KeyError,TypeError,ValueError,AttributeError) as exc:issues.append('ledger/source/cost invalid: '+str(exc))
    cash=number(p.get('cash_net_return_pct'),'cash return')
    def difference(a,b):return a-b if a is not None and b is not None else None
    comparison = {'common_prefix_end_utc':end, 'strategy_net_return_pct':returns['strategy'],
        'hold_net_return_pct':returns['hold'],'cash_net_return_pct':cash,
        'double_cost_net_return_pct':returns['double_cost'],'double_cost_hold_net_return_pct':returns['double_cost_hold'],
        'strategy_minus_hold_pct_points':difference(returns['strategy'],returns['hold']),
        'strategy_minus_cash_pct_points':difference(returns['strategy'],cash),
        'double_cost_minus_hold_pct_points':difference(returns['double_cost'],returns['double_cost_hold'])}
    for k in ('strategy_minus_hold_pct_points','strategy_minus_cash_pct_points','double_cost_minus_hold_pct_points','double_cost_net_return_pct'):
        if comparison[k] is None or comparison[k] <= 0:issues.append(k + ': not positive')
    for nominal,stress in (('strategy','double_cost'),('hold','double_cost_hold')):
        if returns[nominal] is not None and returns[stress] is not None and returns[stress] > returns[nominal]+1e-8:
            issues.append(stress + ': exceeds nominal return')
    return {'eligible_for_independent_paper_review_only':not issues,'blockers':issues,
        'observation_days':age,'coverage':coverage,'coverage_ratios':ratios,'comparison':comparison,
        'account_facts':account_facts,'sampling_and_tail':gaps,'continuous_drawdown_verified':False,
        'actual_account_fee_verified':False}


def evaluate(report, now, sources=None):
    """Pure fail-closed assessment of existing V3.1 report; no I/O or orders."""
    now=_clock(now)
    result={'status':'BLOCKED','audited_at_utc':now.isoformat(),'source_report_audited_at_utc':None,
        'source':'Existing V3.1 portfolios report; no new captures or exchange account verification',
        'eligible_for_independent_paper_review_only':False,'economic_acceptance_passed':False,
        'real_orders_authorized':False,'auto_promotion_authorized':False,'exact_real_profit_validated':False,
        'continuous_drawdown_verified':False,'actual_account_fee_verified':False,'portfolios':{},'blockers':[],
        'limitations':['Sampled drawdown does not prove continuous maximum drawdown.',
            'Recorded costs are paper ledger amounts; actual account fee tier and fills remain unverified.',
            'Liquidation proxy includes hypothetical exit costs; cash interest is not modeled.',
            'Supplementary review never changes frozen policy, DCA rules, or promotes to real orders.']}
    if not isinstance(report,dict):result['blockers'].append('report missing');return result
    result['source_report_audited_at_utc']=report.get('audited_at_utc')
    if report.get('status')!='ok' or report.get('version_id')!=VERSION:result['blockers'].append('report status/version mismatch')
    if report.get('real_orders_authorized') is not False:result['blockers'].append('paper scope not verified')
    try:
        if not 0 <= (now-_clock(report['audited_at_utc'])).total_seconds() <= 900:raise ValueError('stale/future report')
    except (KeyError,ValueError,TypeError):result['blockers'].append('report clock missing/stale/future')
    portfolios=report.get('portfolios')
    for horizon in ('1h','24h'):
        p=portfolios.get(horizon) if isinstance(portfolios,dict) else None
        if not isinstance(p,dict):result['blockers'].append(horizon+': missing portfolio');continue
        result['portfolios'][horizon]=_portfolio(p,now,int(horizon[:-1]),sources)
    if not result['blockers']:
        result['eligible_for_independent_paper_review_only']=all(p['eligible_for_independent_paper_review_only'] for p in result['portfolios'].values())
        result['status']='ok' if result['eligible_for_independent_paper_review_only'] else 'WAIT'
    return _json_safe(result)


def main(root=None, now=None):
    """Read existing report and write only supplementary audit; no capture."""
    root=Path(root) if root is not None else Path(__file__).resolve().parent
    now=now if now is not None else datetime.now(timezone.utc)
    try:
        report=json.loads((root/'data/p2_paired_execution_v3_1/latest.json').read_text(encoding='utf-8'))
        sources={}
        registry=root/'data/direction_v42/forward_registry.csv'
        if registry.exists():
            with registry.open(encoding='utf-8',newline='') as stream:
                for row in csv.DictReader(stream):
                    fid=row['forecast_id']
                    if fid in sources:raise ValueError('duplicate original source id')
                    sources[fid]=dict(row)
        events=root/engine.EVENTS
        if events.exists():
            for line in events.read_text(encoding='utf-8').splitlines():
                for item in json.loads(line)['source_items']:
                    fid=item['forecast_id']
                    if fid in sources and item.get('source_verified') is True:
                        original=sources[fid]
                        if any(str(original[k])!=str(item[k]) for k in ('issued_at_utc','target_utc','horizon_hours')):raise ValueError('source capture/original mismatch')
                        public=item.get('observed_public_utc')
                        if original.get('observed_public_utc') not in (None,public):raise ValueError('public source context changed')
                        original['observed_public_utc']=public
        result=evaluate(report,now,sources=sources)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        result=evaluate(None,now)
        result['blockers'].append(type(exc).__name__+': '+str(exc)[:300])
    target=root/'data/p2_economic_acceptance_v1/latest.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],
        'eligible_for_independent_paper_review_only':result['eligible_for_independent_paper_review_only'],
        'economic_acceptance_passed':False,'real_orders_authorized':False,'auto_promotion_authorized':False}))
    return 1 if result['status']=='BLOCKED' else 0


if __name__=='__main__':raise SystemExit(main())

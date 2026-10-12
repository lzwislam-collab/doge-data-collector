#!/usr/bin/env python3
"""Forward DOGE spot-only paper ledger. No exchange orders, no historical fills.

Official V2 is the only forecast/quote evidence; historical V1 is used solely for
registry hashes. Entry and exit are hypothetical quote proxies, NOT fills.
"""
import csv
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POLICY = "data/stage5/p2_spot_paper_protocol.json"
OFFICIAL = "data/stage5/forward_validation_v2/daily_latest.json"
LEGACY = "data/stage5/forward_validation/daily_latest.json"
REGISTRY = "data/direction_v42/forward_registry.csv"
SCORES = "data/direction_v42/forward_scores.csv"
OUTPUT = "data/stage5/p2_spot_paper/latest.json"
REPORT = "reports/DOGE_P2_SPOT_PAPER.md"

def utc(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC clock required")
    return dt

def rows(path):
    data = {}
    with path.open(encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            fid=row.get("forecast_id")
            if not fid or fid in data:
                raise ValueError("empty or duplicate forecast ID: " + str(path))
            data[fid]=row
    return data

def positive(value):
    val=float(value)
    if not math.isfinite(val) or val<=0:
        raise ValueError("invalid strictly positive price")
    return val

def precommit(root, protocol):
    if protocol.get("status")!="frozen_paper_protocol" or protocol.get("no_trade_orders") is not True:
        raise ValueError("invalid/future paper protocol")
    if protocol.get("automatic_profit_claim") is not False or protocol.get("no_dca_changes") is not True:
        raise ValueError("paper strategy must not authorize profit claims or DCA changes")
    for file in (POLICY,"p2_spot_paper.py"):
        commits=subprocess.run(["git","log","--format=%H","--",file],
                                cwd=root,check=True,text=True,capture_output=True).stdout.splitlines()
        if len(commits)!=1:
            raise ValueError("P2 frozen protocol/evaluator changed: create new future version ("+file+")")
        ctime=subprocess.run(["git","show","-s","--format=%cI",commits[0]],
                             cwd=root,check=True,text=True,capture_output=True).stdout.strip()
        if datetime.fromisoformat(ctime).astimezone(timezone.utc)>=utc(protocol["effective_from_utc"]):
            raise ValueError("P2 protocol or evaluator not public before prospective start")

def verify_source(v2,v1,protocol,reg,scores,source_bytes):
    if (v2.get("status")!="ok" or not v2.get("locked_implementation_checked")
        or v2.get("protocol_id")!=protocol["source_protocol_id"]
        or not v1.get("full_reachable_history_checked")
        or v2.get("economic_edge_validated") is not False):
        raise ValueError("official V2 provenance/economic claim invalid")
    if hashlib.sha256(source_bytes[0]).hexdigest()!=v1.get("registry_sha256") or (
       hashlib.sha256(source_bytes[1]).hexdigest()!=v1.get("scores_sha256")):
        raise ValueError("registry/scores differ from official audited V1 snapshot")
    return utc(v2["audited_at_utc"])

def verify_qualified_cohort(v2,reg,scores,now):
    cohort=v2["horizons"]["24h"]["nonoverlap"]
    selected=cohort["selected_ids"]
    if len(selected)!=len(set(selected)):
        raise ValueError("duplicate selected ID in official V2 cohort")
    entries=[]
    due=scored=0
    for fid in selected:
        if fid not in reg:
            raise ValueError("missing official selected forecast")
        f=reg[fid]
        if int(f["horizon_hours"])!=24 or utc(f["target_utc"])!=utc(f["issued_at_utc"])+timedelta(hours=24):
            raise ValueError("bad published 24h forecast horizon")
        if any(f[x] not in ("UP","DOWN") for x in ("doge_reversal_direction","btc_momentum_direction")):
            raise ValueError("bad original directional input")
        matured=utc(f["target_utc"])+timedelta(minutes=25)<=now
        item=scores.get(fid)
        if matured:
            due+=1
        valid=None
        if matured and item and item["status"]=="scored":
            quote=utc(item["target_quote_utc"])
            if quote<utc(f["target_utc"]) or quote>utc(f["target_utc"])+timedelta(minutes=25):
                raise ValueError("late target quote")
            realized="UP" if positive(item["target_price"])>positive(f["reference_price"]) else "DOWN"
            if item["realized_up"]!=realized:
                raise ValueError("wrong outcome label")
            for key in ("majority","doge_reversal","btc_momentum"):
                if item[key+"_correct"]!=str(int(f[key+"_direction"]==realized)):
                    raise ValueError("original score flag conflict")
            valid=item
            scored+=1
        entries.append((f,valid,matured))
    if due!=cohort["due"] or scored!=cohort["scored"]:
        raise ValueError("verified cohort does not match V2 official denominator")
    return entries

def sell_factor(fee,slip,spread):
    return (1-slip-spread)*(1-fee)

def buy_quantity(cash,price,fee,slip,spread):
    impact=slip+spread
    notional=cash/(1+impact)
    return notional*(1-fee)/price

def trade_long(cash,entry,exit_price,fee,slip,spread):
    # Buy taker fee is subtracted in base DOGE; sell fee in USDT. Full cash invested.
    impact=slip+spread
    principal=cash/(1+impact)
    units_before_fee=principal/entry
    quantity=units_before_fee*(1-fee)
    exit_gross=quantity*exit_price
    exit_after_impact=exit_gross*(1-impact)
    exit_cash=exit_after_impact*(1-fee)
    costs={"buy_fee_usdt_equiv":principal*fee,
           "sell_fee_usdt":exit_after_impact*fee,
           "entry_slippage_usdt":principal*slip,
           "exit_slippage_usdt":exit_gross*slip,
           "entry_half_spread_usdt":principal*spread,
           "exit_half_spread_usdt":exit_gross*spread}
    if exit_cash<=0:
        raise ValueError("invalid simulated trade equity")
    return exit_cash, quantity, costs

def max_dd(points):
    peak=points[0]
    dd=0.0
    for x in points:
        peak=max(peak,x)
        dd=max(dd,(peak-x)/peak)
    return 100*dd

def simulate(prefix,policy,model,cost_multiplier):
    start=positive(policy["starting_equity_usdt"])
    fees=policy["execution_cost_assumptions"]
    fee=fees["taker_fee_fraction_each_side"]*cost_multiplier
    slip=fees["slippage_fraction_each_side"]*cost_multiplier
    halfspread=fees["half_spread_fraction_each_side"]*cost_multiplier
    if not 0<=fee<1 or not 0<=slip+halfspread<1:
        raise ValueError("invalid stress cost")
    balance=start
    equity_points=[start]
    trades=[]
    costs_total={k:0.0 for k in ("buy_fee_usdt_equiv","sell_fee_usdt",
        "entry_slippage_usdt","exit_slippage_usdt","entry_half_spread_usdt","exit_half_spread_usdt")}
    turnover=0.
    legs=0
    regime={}
    for f,s in prefix:
        entry=positive(f["reference_price"])
        target=positive(s["target_price"])
        if model=="cash":
            decision="DOWN"
        elif model=="always_interval_up":
            decision="UP"
        else:
            decision=f[model+"_direction"]
        prior=balance
        qty=0.
        if decision=="UP":
            balance,qty,costs=trade_long(balance,entry,target,fee,slip,halfspread)
            for k,v in costs.items():
                costs_total[k]+=v
            turnover+=prior+qty*target
            legs+=2
        else:
            costs={k:0. for k in costs_total}
        equity_points.append(balance)
        gain=(balance/prior-1)*100
        regime_name="BTC_momentum_issued_"+f["btc_momentum_direction"]
        state=regime.setdefault(regime_name,{"intervals":0,"long_intervals":0,"sum_interval_net_pct":0.0})
        state["intervals"]+=1
        state["long_intervals"]+=int(decision=="UP")
        state["sum_interval_net_pct"]+=gain
        trades.append({"forecast_id":f["forecast_id"],"start_utc":f["issued_at_utc"],
                       "end_utc":s["target_quote_utc"],"signal":decision,
                       "entry_reference_usdt":entry,"exit_reference_usdt":target,
                       "doge_bought_net":qty,"equity_before_usdt":prior,
                       "equity_after_usdt":balance,"interval_net_return_pct":gain,
                       "cost_usdt_components":costs,
                       "execution_status":"HYPOTHETICAL_LAST_PRICE_PROXY_NOT_PUBLIC_EXECUTION"})
    # Buy/hold continuous across the same interval from first reference quote to
    # last reference quote. Both entry and final liquidation charged exactly once.
    if prefix:
        first=positive(prefix[0][0]["reference_price"])
        last=positive(prefix[-1][1]["target_price"])
        qty=buy_quantity(start,first,fee,slip,halfspread)
        hold_end=qty*last*sell_factor(fee,slip,halfspread)
        hold_marks=[start]+[qty*positive(s["target_price"])*sell_factor(fee,slip,halfspread)
                            for _,s in prefix]
        hold_mdd=max_dd(hold_marks)
    else:
        hold_end=None
        hold_mdd=None
    return {"model":model,"completed_intervals":len(prefix),
        "initial_equity_usdt":start,"final_equity_usdt":balance if prefix else None,
        "net_return_pct":100*(balance/start-1) if prefix else None,
        "checkpoint_max_drawdown_pct":max_dd(equity_points) if prefix else None,
        "turnover_usdt":turnover,"trade_legs":legs,"cost_usdt_components":costs_total,
        "by_exante_BTC_momentum_proxy":regime,"ledger":trades,
        "hold_final_equity_usdt":hold_end,"hold_net_return_pct":100*(hold_end/start-1) if prefix else None,
        "hold_checkpoint_max_drawdown_pct":hold_mdd,
        "cash_net_return_pct":0.0 if prefix else None,
        "excess_over_hold_pct_points":100*(balance-hold_end)/start if prefix else None,
        "excess_over_cash_pct_points":100*(balance-start)/start if prefix else None}

def matured_missing_issuance_slots(registry, effective, now):
    # Absence is confirmed only when an entire possible 24h issue bin AND its
    # last possible 24h outcome +25-minute capture grace have expired.
    horizon=timedelta(hours=24)
    mature=now-effective-timedelta(hours=48,minutes=25)
    eligible_bins=max(0,int(mature.total_seconds()//86400)+1)
    observed=set()
    for f in registry.values():
        if int(f["horizon_hours"])!=24:
            continue
        issued=utc(f["issued_at_utc"])
        if issued>=effective:
            observed.add(int((issued-effective).total_seconds()//86400))
    return [(effective+horizon*i).isoformat() for i in range(eligible_bins)
            if i not in observed]

def evaluate(v2,v1,protocol,registry,scores,source_bytes):
    now=verify_source(v2,v1,protocol,registry,scores,source_bytes)
    effective=utc(protocol["effective_from_utc"])
    if effective<=utc(v2["effective_from_utc"]):
        raise ValueError("P2 must begin after frozen V2")
    if protocol["horizon_hours"]!=24 or protocol["official_signal"]!="doge_reversal_direction":
        raise ValueError("unexpected strategy, prohibited automatic model switch")
    if protocol["comparator_signal"]!="btc_momentum_direction":
        raise ValueError("unexpected benchmark strategy")
    items=verify_qualified_cohort(v2,registry,scores,now)
    matched=[x for x in items if utc(x[0]["issued_at_utc"])>=effective]
    missing_slots=matured_missing_issuance_slots(registry,effective,now)
    first_missing=utc(missing_slots[0]) if missing_slots else None
    prefix=[]
    unresolved=[]
    blocked=None
    prev_exit=None
    for f,s,mature in matched:
        if first_missing is not None and utc(f["issued_at_utc"])>=first_missing:
            blocked="MISSING_24H_ISSUANCE_SLOT_"+missing_slots[0]
            unresolved.append({"slot_start_utc":missing_slots[0],"reason":"matured_missing_issuance"})
            break
        if not mature:
            break
        if prev_exit is not None and utc(f["issued_at_utc"])<prev_exit:
            blocked=f["forecast_id"]
            unresolved.append({"forecast_id":blocked,"reason":"overlapping_publication_interval"})
            break
        if s is None:
            blocked=f["forecast_id"]
            unresolved.append({"forecast_id":blocked,"reason":"missing_qualified_target_quote"})
            break
        prefix.append((f,s))
        prev_exit=utc(s["target_quote_utc"])
    if blocked is None and first_missing is not None:
        blocked="MISSING_24H_ISSUANCE_SLOT_"+missing_slots[0]
        unresolved.append({"slot_start_utc":missing_slots[0],"reason":"matured_missing_issuance"})
    base=simulate(prefix,protocol,"doge_reversal",1)
    btc=simulate(prefix,protocol,"btc_momentum",1)
    always=simulate(prefix,protocol,"always_interval_up",1)
    stress=simulate(prefix,protocol,"doge_reversal",protocol["execution_cost_assumptions"]["stress_multiplier"])
    covered=v2["horizons"]["24h"]
    day_issued=covered["issuance"]["coverage_pct"]
    quote_covered=covered["quote_coverage_pct"]
    observation=(now-effective).total_seconds()/86400
    ready=(observation>=protocol["min_observation_days"]
           and len(prefix)>=protocol["min_completed_independent_intervals"]
           and blocked is None
           and day_issued is not None and day_issued>=100*protocol["min_issuance_coverage_fraction"]
           and quote_covered is not None and quote_covered>=100*protocol["min_quote_coverage_fraction"]
           and base["excess_over_hold_pct_points"]>0 and base["excess_over_cash_pct_points"]>0
           and stress["excess_over_hold_pct_points"]>0
           and base["checkpoint_max_drawdown_pct"] is not None)
    return {"schema_version":1,"status":"ok","source_audited_at_utc":v2["audited_at_utc"],
       "protocol_id":protocol["protocol_id"],"effective_from_utc":protocol["effective_from_utc"],
       "source_v2_qualified":True,"paper_not_real_order_execution":True,
       "execution_bid_ask_verified":False,"entry_public_after_quote_not_verified":True,
       "external_execution_quality_not_verified":True,
       "issuance_coverage_pct":day_issued,"due_quote_coverage_pct":quote_covered,
       "eligible_24h_nonoverlap_forecasts":len(matched),
       "matured_missing_24h_issuance_slots":missing_slots,
       "completed_prefix_intervals":len(prefix),
       "blocked_forecast_id":blocked,"excluded_or_blocking":[*unresolved],
       "strategy":base,"btc_momentum_strategy":btc,"always_interval_up":always,
       "double_cost_stress":stress,
       "strict_cost_model":protocol["execution_cost_assumptions"],
       "paper_checkpoint_excess_review_candidate":bool(ready),
       "economic_edge_validated":False,"real_profit_validated":False,
       "limitations":["Entry is issue-time LAST quote before remote publication; it cannot establish tradable post-public execution.",
         "Exit is a genuine quoted TARGET tick in frozen V2 window; not a best-bid execution or real fill.",
         "Fee, half-spread and slippage are explicit assumed costs, not observed account-level executions.",
         "Drawdown computed only on target checkpoints, not pathwise/intraday maximum drawdown.",
         "BTC momentum split is an ex-ante proxy, not full volatility-based regime classification.",
         "A 24-hour forecast may be issued more than once per UTC day; only V2 preselected nonoverlap rows enter this analysis.",
         "No automatic economic claim or trading authorization; need prospective executable quotes after publication and independent live verification."]}

def main():
    try:
        policy=json.loads((ROOT/POLICY).read_text(encoding="utf-8"))
        precommit(ROOT,policy)
        v2=json.loads((ROOT/OFFICIAL).read_text(encoding="utf-8"))
        v1=json.loads((ROOT/LEGACY).read_text(encoding="utf-8"))
        raw=((ROOT/REGISTRY).read_bytes(),(ROOT/SCORES).read_bytes())
        out=evaluate(v2,v1,policy,rows(ROOT/REGISTRY),rows(ROOT/SCORES),raw)
    except Exception as exc:
        out={"status":"blocked","error":type(exc).__name__+": "+str(exc)[:400],
             "economic_edge_validated":False,"real_profit_validated":False}
    (ROOT/OUTPUT).parent.mkdir(parents=True,exist_ok=True)
    (ROOT/REPORT).parent.mkdir(parents=True,exist_ok=True)
    (ROOT/OUTPUT).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["# DOGE P2 现货纸面盈利验证｜独立账本","","仅现货研究，不真实下单，不涉及DOGE定投。",
           "执行成本中手续费、滑点与半价差分开核算；按真实到期报价计价，预测发行参考价不能视为公开后可交易买价。"]
    if out["status"]=="ok":
        a=out["strategy"]
        money=lambda x:"暂无已完成的前瞻区间" if x is None else f"{x:.4f}%"
        lines+=["",f"冻结P2规则生效UTC：{out['effective_from_utc']}",
                f"源V2审计截止UTC：{out['source_audited_at_utc']}",
                f"合格前瞻区间：{out['completed_prefix_intervals']}；阻断ID：{out['blocked_forecast_id'] or '无'}。",
                "",
                "|指标|正式模型策略|BTC动量策略|持币基准|现金基准|双倍成本正式模型|",
                "|---|---:|---:|---:|---:|---:|",
                "|净收益|"+ "|".join(money(z) for z in (a["net_return_pct"],out["btc_momentum_strategy"]["net_return_pct"],a["hold_net_return_pct"],a["cash_net_return_pct"],out["double_cost_stress"]["net_return_pct"]))+"|",
                f"|已完成区间|{a['completed_intervals']}|{out['btc_momentum_strategy']['completed_intervals']}|{a['completed_intervals']}|{a['completed_intervals']}|{out['double_cost_stress']['completed_intervals']}|",
                "",f"正式策略：交易腿数 {a['trade_legs']}，名义成交金额 {a['turnover_usdt']:.2f} USDT。",
                f"正式策略最大检查点回撤：{money(a['checkpoint_max_drawdown_pct'])}；持币最大检查点回撤：{money(a['hold_checkpoint_max_drawdown_pct'])}。完整区间最大回撤未知。",
                "费用和双倍成本压力测试是预先设定的研究假设；盘口成交与实际账户费率未经验证。",
                "评估资格仅是纸面检查点候选，不是已证明交易盈利："+
                ("候选达标" if out["paper_checkpoint_excess_review_candidate"] else "尚未达到复核门槛")+".",
                "与P0/P1并行；正式预测、原始报价、历史漏发和错误结果一律保留。"]
    else:
        lines+=["","P2审计阻断："+out["error"],"停止收益声明，禁止把缺失预测或合成报价当实际成交。"]
    (ROOT/REPORT).write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"status":out["status"],"completed":out.get("completed_prefix_intervals",0),
                      "error":out.get("error"),"economic_edge_validated":False}))
    return 0 if out["status"]=="ok" else 1

if __name__=="__main__":
    sys.exit(main())

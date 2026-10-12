#!/usr/bin/env python3
"""DOGE three-risk gate: read-only governance, PIT-aware ablation, NO_TRADE, acceptance.

Not a trading strategy, not a replacement for frozen V2/P1/P2 evaluations.
Any historical feature-ablation result is RESEARCH ONLY, never a forward score.
"""
import csv
import hashlib
import io
import json
import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import direction_baseline_v42 as hourly
import direction_forward_v42 as frozen
import forward_validation as immutable
import p1_adaptive_v2 as challenger

ROOT = Path(__file__).resolve().parent
POLICY = "data/stage5/three_risk_gates_protocol.json"
OUT = "data/stage5/three_risk_gates/latest.json"
REPORT = "reports/DOGE_THREE_RISK_GATES.md"
DECISIONS = "data/stage5/three_risk_gates/no_trade_decisions.csv"
DCOLS = ["forecast_id", "issued_at_utc", "target_utc", "decision_recorded_at_utc",
         "decision", "reason", "timely_pretrade_decision", "source_protocol_id"]
KEYFILES = {
    "v2": "data/stage5/forward_validation_v2/daily_latest.json",
    "p0": "data/p0_operations/latest.json",
    "p2": "data/stage5/p2_spot_paper/latest.json",
    "p1": "data/stage5/p1_adaptive_v2/latest.json",
}
FEATURE_NAMES = ["bias", "DOGE return 1h", "DOGE return 24h",
                 "BTC return 1h", "BTC return 24h", "DOGE abs return 24h"]
GROUPS = {name: n for n, name in enumerate(FEATURE_NAMES) if n > 0}


def utc(value):
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if when.tzinfo is None or when.utcoffset() != timedelta(0):
        raise ValueError("UTC timestamp required")
    return when


def jread(path):
    file = ROOT / path
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else None


def policy_guard(p):
    if not isinstance(p, dict) or p.get("status") != "frozen_governance_protocol":
        raise ValueError("missing frozen risk-governance protocol")
    if p.get("no_real_orders") is not True or p.get("no_dca_changes") is not True:
        raise ValueError("no-orders and DCA isolation must remain true")
    if p.get("trade_gate", {}).get("action") != "NO_TRADE":
        raise ValueError("risk gate must not issue an unvalidated trading signal")
    if p.get("feature_ablation", {}).get("candidate_columns") != FEATURE_NAMES:
        raise ValueError("unexpected P1 feature group definition")
    for path in (POLICY, "three_risk_gates.py"):
        result = subprocess.run(["git", "log", "--format=%H", "--", path], cwd=ROOT,
                                check=True, capture_output=True, text=True)
        commits = result.stdout.splitlines()
        if not commits:
            raise ValueError("risk governance must be committed before operation")
        for commit in commits:
            stamp = subprocess.run(["git", "show", "-s", "--format=%cI", commit],
                                   cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
            if datetime.fromisoformat(stamp).astimezone(timezone.utc) >= utc(p["effective_from_utc"]):
                raise ValueError("risk governance edited after prospective activation; new version required")


def inventory_36(root):
    file = root / "data/stage5/spot_plus/legacy36_map.csv"
    with file.open(encoding="utf-8-sig", newline="") as fp:
        rows = list(csv.DictReader(fp))
    if len(rows) != 36 or [str(i) for i in range(1, 37)] != [r["Source36#"] for r in rows]:
        raise ValueError("original 36 entries missing/duplicated/out of order")
    groups = {x: sum(r["Decision"] == x for r in rows)
              for x in ("KEEP_MERGE", "BACKEND_ONLY", "REMOVE_SCORE")}
    if groups != {"KEEP_MERGE": 21, "BACKEND_ONLY": 8, "REMOVE_SCORE": 7}:
        raise ValueError("unauthorized change of 36 item mapping")
    return {"catalogue_entries": 36, "report_and_governance_groups": groups,
            "verified_numeric_predictive_features_from_36": 0,
            "zero_means_not_yet_authorized_not_proven_useless": True,
            "excluded_unsupported_scores": [r["Legacy item"] for r in rows
                                            if r["Decision"] == "REMOVE_SCORE"],
            "not_a_numeric_36_feature_trained_model": True}


def fit_fold(prices, train_idx, test_idx, h, excluded):
    # Train labels must close STRICTLY before first test feature timestamp.
    first_test = min(test_idx)
    safe = [i for i in train_idx if i + h < first_test]
    if len(safe) < 100:
        raise ValueError("insufficient purged training rows")
    def x(i):
        values = challenger.feat(prices, i)
        if excluded:
            values[excluded] = 0.0
        return values
    train = [(x(i), int(prices["DOGE-USDT"][i+h] > prices["DOGE-USDT"][i])) for i in safe]
    weights = challenger.train(train, 80, .09, .006)
    vals = []
    for i in test_idx:
        y = int(prices["DOGE-USDT"][i+h] > prices["DOGE-USDT"][i])
        q = challenger.probability(weights, x(i))
        vals.append((q-y)**2)
    return sum(vals) / len(vals), len(safe)


def historic_ablation(root, p):
    # Fixed chronological and horizon-purged folds. Historical backfills may
    # have appeared after their nominal market timestamp: NOT genuine PIT proof.
    times, prices = hourly.read_csv(root / "data/hourly_research/ohlc.csv", hourly.HOUR)
    if len(times) < 3000:
        raise ValueError("insufficient historical bars for fixed ablation")
    fields = p["feature_ablation"]
    if fields.get("exclude_groups") != list(GROUPS):
        raise ValueError("ablation groups must be predetermined before outcomes")
    summary = {}
    for h in fields["horizons_hours"]:
        stride = h if h == 24 else 1
        anchors = list(range(25, len(times)-h, stride))[-fields["history_anchors"]:]
        end = len(anchors)
        tests = fields["test_anchors"]
        nfold = fields["walkforward_folds"]
        rows = {name: [] for name in ("FULL", *GROUPS)}
        training_sample_sizes = []
        for fold in range(nfold):
            cutoff = end - (nfold-fold)*tests
            fit_indices = anchors[max(0, cutoff-fields["train_anchors"]):cutoff]
            test_indices = anchors[cutoff:cutoff+tests]
            if not test_indices:
                raise ValueError("empty chronological fold")
            for group, index in [("FULL", 0), *GROUPS.items()]:
                score, ntrain = fit_fold(prices, fit_indices, test_indices, h, index)
                rows[group].append(score)
                if group == "FULL":
                    training_sample_sizes.append(ntrain)
        base = sum(rows["FULL"]) / len(rows["FULL"])
        summary[str(h)+"h"] = {
            "fold_count": nfold, "test_points_per_fold": tests,
            "min_purged_training": min(training_sample_sizes),
            "full_model_historical_brier": round(base, 8),
            "ablation": {
                name: {"brier_without_group": round(sum(vals)/len(vals), 8),
                       "delta_minus_full_brier": round(sum(vals)/len(vals)-base, 8),
                       "suggests_possible_overfit_if_negative": sum(vals)/len(vals) < base}
                for name, vals in rows.items() if name != "FULL"},
            "is_real_independent_prospective_evaluation": False
        }
    # first_ingested_utc reveals the observed archival collection, but cannot
    # prove the exact data vintage available at every past hypothetical fold.
    with (root / "data/hourly_research/ohlc.csv").open(newline="", encoding="utf-8") as fp:
        header = next(csv.reader(fp))
    if "first_ingested_utc" not in header:
        raise ValueError("historical candles missing provenance/first_ingested_utc column")
    return {"status": "historical_diagnostic_only", "models_fit_from": "P1 V2 six candidate features",
            "feature_groups_under_ablation": len(GROUPS),
            "folds": summary,
            "fixed_chronological_folds_and_purged_train_labels": True,
            "has_first_ingested_provenance_field": True,
            "historical_vintage_as_known_at_each_old_test_time_proven": False,
            "results_authorize_add_or_remove_live_model_features": False,
            "future_pit_feature_family_trials_registered": False,
            "study_limit": "Historical backfill vintages, exploration and correlated groups prevent a genuine blind feature attribution claim"}


def read_decisions(root):
    path = root / DECISIONS
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != DCOLS:
            raise ValueError("NO_TRADE registry schema changed")
        out = {}
        for r in reader:
            fid = r["forecast_id"]
            if not fid or fid in out:
                raise ValueError("duplicate NO_TRADE decision ID")
            if r["decision"] != "NO_TRADE" or r["timely_pretrade_decision"] not in ("0","1"):
                raise ValueError("unauthorized shadow trade decision")
            out[fid] = r
        return out


def record_no_trade(root, p, v2, now, existing=None):
    previous = read_decisions(root) if existing is None else existing
    immutable.history_receipts(root, DECISIONS, DCOLS, previous)
    registry = frozen.read_rows(root / "data/direction_v42/forward_registry.csv", frozen.FCOLS)
    chosen = v2["horizons"]["24h"]["nonoverlap"]["selected_ids"]
    if len(chosen) != len(set(chosen)):
        raise ValueError("duplicate official V2 preselected IDs")
    updates = dict(previous)
    new = 0
    for fid in chosen:
        if fid not in registry:
            raise ValueError("selected official V2 forecast absent")
        r = registry[fid]
        issue = utc(r["issued_at_utc"])
        if issue < utc(p["effective_from_utc"]) or fid in updates:
            continue
        if issue > now or r["doge_reversal_direction"] not in ("UP","DOWN"):
            raise ValueError("invalid future pretrade candidate")
        # This gate deliberately NEVER pretends a daily audit is a synchronous
        # executable instruction from the hourly P0 price capture.
        timely = now <= issue + timedelta(minutes=45) and now < utc(r["target_utc"])
        updates[fid] = dict(zip(DCOLS, [
            fid, r["issued_at_utc"], r["target_utc"], now.isoformat(), "NO_TRADE",
            "UNVERIFIED_NET_EXPECTANCY_AND_NO_POST_PUBLIC_BID_ASK",
            "1" if timely else "0", v2["protocol_id"]
        ]))
        new += 1
    if new:
        path = root / DECISIONS
        path.parent.mkdir(parents=True, exist_ok=True)
        s = io.StringIO()
        w = csv.DictWriter(s, fieldnames=DCOLS, lineterminator="\n")
        w.writeheader()
        w.writerows(updates[k] for k in sorted(updates))
        frozen.write(path, s.getvalue())
    return {"action": "NO_TRADE", "new_records": new,
            "all_shadow_records": len(updates),
            "timely_audited_records": sum(r["timely_pretrade_decision"] == "1" for r in updates.values()),
            "may_count_missing_as_correct_direction": False,
            "no_trade_subset_is_not_a_replacement_for_full_forecast_accuracy": True,
            "unverified_calibrated_probability_or_expected_edge": True,
            "post_public_executable_bid_ask_evidence": False,
            "real_trading_authorized": False,
            "no_trade_registry_path": DECISIONS}


def stage_gate(p, v2, p0, p1, p2, now):
    a = p["acceptance"]
    if v2.get("status") != "ok" or v2.get("locked_implementation_checked") is not True:
        raise ValueError("frozen V2 audit is not valid")
    if v2.get("protocol_id") != p["source_protocols"]["v2"]:
        raise ValueError("wrong frozen V2 provenance")
    if p2.get("status") != "ok" or p2.get("protocol_id") != p["source_protocols"]["p2"]:
        raise ValueError("P2 source/strategy not verified")
    if p1.get("status") != "ok" or p1.get("protocol_id") != p["source_protocols"]["p1_adaptive"]:
        raise ValueError("P1 adaptive version mismatch")
    if v2.get("economic_edge_validated") is not False or p2.get("real_profit_validated") is not False:
        raise ValueError("unsupported upstream profit claim")
    op = (p0 or {}).get("rolling_7d", {})
    p0_tracks = {}
    for h in ("1h","24h"):
        m = op.get(h, {})
        good = m.get("seven_full_days_observed") is True and (
            m.get("coverage_pct") or 0) >= a["p0_issuance_coverage_pct"] and (
            m.get("target_coverage_pct") or 0) >= a["p0_target_coverage_pct"] and (
            m.get("target_due") or 0) > 0
        p0_tracks[h] = {
            "closed_slots": m.get("expected",0), "issued": m.get("issued",0),
            "issuance_coverage_pct": m.get("coverage_pct"),
            "matured_targets": m.get("target_due",0),
            "target_coverage_pct": m.get("target_coverage_pct"),
            "seven_days_pass": bool(good)}
    # An old successful P0 file must never pass when the hourly monitor has stopped.
    audited = (p0 or {}).get("audited_at_utc")
    age_sec = (now-utc(audited)).total_seconds() if audited else float("inf")
    p0_fresh = -300 <= age_sec <= 6*3600
    p0_seven = p0_fresh and (p0 or {}).get("status") == "ok" and all(
        x["seven_days_pass"] for x in p0_tracks.values())
    thirty = (p0 or {}).get("rolling_30d", {})
    thirty_available = all(h in thirty for h in ("1h", "24h"))
    p0_thirty = bool(p0_fresh and (p0 or {}).get("status") == "ok" and thirty_available
        and all(thirty[h].get("full_window_observed") is True
            and thirty[h].get("rolling_window_hours") == 24*a["p0_30d_days"]
            and thirty[h].get("expected",0) >= int(24*a["p0_30d_days"]/int(h[:-1]))
            and (thirty[h].get("coverage_pct") or 0) >= a["p0_30d_coverage_pct"]
            and thirty[h].get("target_due",0)>0
            and (thirty[h].get("target_coverage_pct") or 0) >= a["p0_30d_coverage_pct"]
            for h in ("1h", "24h")))
    elapsed = (now - utc(v2["effective_from_utc"])).total_seconds()/86400
    n = v2["horizons"]["24h"]["nonoverlap"]
    official = n["models"]["doge_reversal"]
    p1_candidate = (elapsed >= a["p1_preliminary_days"]
                    and n["scored"] >= a["p1_preliminary_min_nonoverlap"]
                    and (n["quote_coverage_pct"] or 0)>=90
                    and (v2["horizons"]["24h"]["issuance"]["coverage_pct"] or 0)>=90
                    and (official["wilson95_lower_pct_descriptive_only"] or 0)>=a["p1_wilson_lower_pct"])
    # These are only minimum descriptive gates. Paired-significance/regime
    # replication MUST be reviewed independently before any signal claim.
    p1_replication = p1_candidate and (
        elapsed>=a["p1_replication_days"]
        and n["scored"] >= a["p1_replication_min_nonoverlap"])
    complete = p2.get("completed_prefix_intervals",0)
    econ_age = (now-utc(p2["effective_from_utc"])).total_seconds()/86400
    econ_candidate = bool(
        econ_age >= a["p2_min_days"] and complete >= a["p2_min_complete_intervals"]
        and p2.get("paper_checkpoint_excess_review_candidate") is True)
    return {
        "p0": {"seven_day_initial": "PASS" if p0_seven else "WAIT",
               "p0_30d_higher_reliability": "PASS" if p0_thirty else "WAIT",
               "p0_30d_horizons": thirty,
               "p0_7d_horizons": p0_tracks,
               "p0_monitor_fresh_under_6h": p0_fresh,
               "thirty_day_rate_source_available": thirty_available},
        "p1": {"official_nonoverlap_valid_24h":n["scored"],
               "official_due_24h":n["due"],
               "preliminary_evidence_candidate":p1_candidate,
               "replication_sample_minimum_reached":p1_replication,
               "paired_baseline_and_regime_replication_independently_verified":False,
               "signal_edge_validated":False},
        "p2": {"completed_independent_intervals":complete,
               "paper_candidate":econ_candidate,
               "execution_bid_ask_verified":p2.get("execution_bid_ask_verified",False),
               "real_profit_validated":False},
        "overall": "REVISE",
        "may_claim_70pct":False,
        "may_claim_reliable_realized_profit":False,
        "manual_review_mandatory":True,
        "never_promote_on_sample_count_alone":True
    }


def evaluate(root=ROOT, now=None):
    # Real runs use currently independently audited timestamps.
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() != timedelta(0):
        raise ValueError("audit needs UTC aware clock")
    p = jread(POLICY)
    policy_guard(p)
    files = {key:jread(path) for key,path in KEYFILES.items()}
    if any(files.get(k) is None for k in ("v2","p2","p1")):
        raise ValueError("missing official P0/P1/P2 source audit")
    v2 = files["v2"]
    stages = stage_gate(p, v2, files["p0"], files["p1"], files["p2"], now)
    feature = inventory_36(root)
    ablation = historic_ablation(root,p)
    decisions = record_no_trade(root,p,v2,now)
    return {"schema_version":1,"status":"ok","audited_at_utc":now.isoformat(),
            "protocol_id":p["protocol_id"], "source_v2_cutoff_utc":v2["audited_at_utc"],
            "official_V2_P1_P2_frozen_files_unchanged":True,
            "catalogue":feature,"feature_ablation":ablation,
            "shadow_decision_gate":decisions,"evidence_maturity":stages,
            "claims_permitted":"NONE",
            "limitations":[
                "Six price-model features are not the 36 report topics",
                "Chronological purged historical ablation is exploratory; data vintage at each historical fold not proven",
                "NO_TRADE logging during daily audit may be later than the actual issuance; it never constitutes an executable order",
                "Probability alone cannot establish expected net return; executable post-public bid/ask and regime replication missing",
                "Seven-day minimum does not imply thirty-day 99% stability or economic profitability",
                "All technical success is separate from statistical edge, genuine execution and realized profit"]}


def main():
    now = datetime.now(timezone.utc)
    try:
        out = evaluate(ROOT,now)
    except Exception as exc:
        out = {"status":"blocked","audited_at_utc":now.isoformat(),
               "error":type(exc).__name__+": "+str(exc)[:420],
               "claims_permitted":"NONE", "real_trading_authorized":False}
    target = ROOT/OUT
    target.parent.mkdir(parents=True,exist_ok=True)
    frozen.write(target,json.dumps(out,indent=2,ensure_ascii=False)+"\n")
    lines=["# DOGE 三大漏洞治理｜过拟合 / NO_TRADE / 验收门槛",
           "","不更改冻结 V2/P1/P2、原始报价、历史预测或 DOGE 定投。"]
    if out["status"] == "ok":
        m = out["evidence_maturity"]
        lines += ["","原始来源：GitHub P0 运维、正式 V2 原始报价与不可覆盖预测、P1/P2 各自审计；",
                  "上游V2截止 UTC："+out["source_v2_cutoff_utc"],
                  "本次审计 UTC："+out["audited_at_utc"], "",
                  "## 1. 特征准入与消融",
                  "36项为研究/报告目录（21保留合并、8后台治理、7删除伪分数），不是36个可交易特征。",
                  "已对P1 V2的五组实际价格特征执行固定时间顺序及标签间隔的历史消融。均只算探索诊断。",
                  "未来数据是否在当时确实可用尚未获完整原始vintage存证；不能据此自动删改冻结模型。",
                  "","|期限|历史折数|完整模型历史Brier|已比较消融组数|",
                  "|---|---:|---:|---:|"]
        for h,s in out["feature_ablation"]["folds"].items():
            lines.append(f"|{h}|{s['fold_count']}|{s['full_model_historical_brier']:.6f}|{len(s['ablation'])}|")
        lines += ["","## 2. 不交易门槛（独立影子记录）",
                  "动作：**NO_TRADE**。直到未来真实样本证明概率校准、扣成本预期收益为正，并取得公开预测之后可成交盘口，均不得把方向信号升级为可执行交易。",
                  f"本次新增不交易记录：{out['shadow_decision_gate']['new_records']}；累计：{out['shadow_decision_gate']['all_shadow_records']}。",
                  "不交易是单独策略层，不会挑选有把握的样本来夸大正式方向准确率。",
                  "","## 3. 证据成熟总闸",
                  "|里程碑|当前判定|","|---|---|",
                  f"|P0 连续七天发行/报价均≥90%|{m['p0']['seven_day_initial']}|",
                  "|P0 连续三十天达到99%|WAIT（尚未建立完整30天证明）|",
                  f"|P1 至少100个独立样本与30天|{'待独立审查' if m['p1']['preliminary_evidence_candidate'] else 'WAIT'}|",
                  f"|P1 至少300个样本、90天|{'仍需市场分层/基准证据' if m['p1']['replication_sample_minimum_reached'] else 'WAIT'}|",
                  f"|P2 至少100个合格现货区间与成本比较|{'仅为纸面复审候选' if m['p2']['paper_candidate'] else 'WAIT'}|",
                  "|真实可执行净收益与最大回撤|未通过|",
                  "","**整体 REVISE：不得宣称70%稳定正确率或现货可持续盈利。**",
                  "研究中错误、漏发和缺价全部保留；模型修正需未来版本重新登记。"]
    else:
        lines += ["","严格审计阻断："+out["error"],"不发布优势与盈利声明。"]
    report = ROOT/REPORT
    report.parent.mkdir(parents=True,exist_ok=True)
    frozen.write(report,"\n".join(lines)+"\n")
    print(json.dumps({"status":out["status"],"error":out.get("error"),
                      "verdict":out.get("evidence_maturity",{}).get("overall","BLOCKED"),
                      "claims":"NONE"},ensure_ascii=False))
    return 0 if out["status"]=="ok" else 1

if __name__=="__main__":
    sys.exit(main())

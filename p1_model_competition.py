#!/usr/bin/env python3
"""P1 prospective shadow model league. Read-only on V2 evidence, never edits forecasts.

Shadow model is a precommitted fixed majority vote over the three ex-ante
directions already published in each official V2 forecast record.
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
POLICY = "data/stage5/p1_model_league_protocol.json"
OFFICIAL = "data/stage5/forward_validation_v2/daily_latest.json"
LEGACY = "data/stage5/forward_validation/daily_latest.json"
REGISTRY = "data/direction_v42/forward_registry.csv"
SCORES = "data/direction_v42/forward_scores.csv"
OUT = "data/stage5/p1_model_league/latest.json"
REPORT = "reports/DOGE_P1_MODEL_LEAGUE.md"
BASELINES = ("doge_reversal", "majority", "btc_momentum", "always_up", "always_down")
Z = 1.959963984540054

def utc(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError("UTC timestamp required")
    return dt

def read_rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = {}
        for row in csv.DictReader(stream):
            key = row.get("forecast_id")
            if not key or key in rows:
                raise ValueError("duplicate/empty forecast ID: " + str(path))
            rows[key] = row
        return rows

def wilson(k, n):
    if not n:
        return None
    p = k / n
    z2 = Z * Z
    return max(0.0, (p + z2 / (2*n) - Z * math.sqrt((p*(1-p)+z2/(4*n))/n))/(1+z2/n))

def exact_mcnemar(wins, losses):
    n = wins + losses
    if n == 0:
        return 1.0
    left = min(wins, losses)
    # Exact two-sided paired-binomial p-value, capped at 1.
    return min(1.0, 2 * sum(math.comb(n, j) for j in range(left+1)) / (2**n))

def shadow(row):
    votes = [row[name] for name in ("majority_direction", "doge_reversal_direction", "btc_momentum_direction")]
    if any(v not in ("UP", "DOWN") for v in votes):
        raise ValueError("invalid issuance-time vote")
    return "UP" if votes.count("UP") >= 2 else "DOWN"

def compare_row(row, name):
    if name == "always_up":
        return "UP"
    if name == "always_down":
        return "DOWN"
    return row[name + "_direction"]

def score_group(entries, now, effective, policy):
    due = [x for x in entries if utc(x["forecast"]["issued_at_utc"]) >= effective
           and utc(x["forecast"]["target_utc"]) + timedelta(minutes=25) <= now]
    scored = [x for x in due if x["score"] is not None]
    total = len(due)
    hits = sum(shadow(x["forecast"]) == x["score"]["realized_up"] for x in scored)
    n = len(scored)
    pairs = {}
    for name in BASELINES:
        win = sum(shadow(x["forecast"]) == x["score"]["realized_up"]
                  and compare_row(x["forecast"], name) != x["score"]["realized_up"] for x in scored)
        loss = sum(shadow(x["forecast"]) != x["score"]["realized_up"]
                   and compare_row(x["forecast"], name) == x["score"]["realized_up"] for x in scored)
        pairs[name] = {"wins":win, "losses":loss, "ties":n-win-loss,
                       "p_exact_two_sided":exact_mcnemar(win, loss),
                       "bonferroni_pass":win > loss and win+loss >= policy["min_discordant_pairs"]
                                          and exact_mcnemar(win, loss) <= policy["bonferroni_p_max"]}
    regimes = {}
    for val in ("UP", "DOWN"):
        subset = [x for x in scored if x["forecast"]["btc_momentum_direction"] == val]
        regimes["BTC_momentum_at_issue_" + val] = {
            "n":len(subset), "correct":sum(shadow(x["forecast"]) == x["score"]["realized_up"] for x in subset)}
    errors = []
    for item in due:
        f = item["forecast"]
        s = item["score"]
        if s is None:
            category = "unresolved_or_missing_quote"
        elif shadow(f) != s["realized_up"]:
            category = "direction_wrong_no_causal_attribution"
        else:
            continue
        errors.append({"forecast_id":f["forecast_id"],"issued_at_utc":f["issued_at_utc"],
                       "target_utc":f["target_utc"],"category":category,
                       "prediction":shadow(f),"observed_direction":s["realized_up"] if s else None,
                       "may_rewrite_forecast":False})
    age_days = max(0, (now-effective).total_seconds()/86400)
    eligible = (n >= policy["min_valid_nonoverlap_scores"]
                and age_days >= policy["min_observation_days"]
                and wilson(hits,n) >= policy["min_wilson95_lower_fraction"]
                and total > 0 and n/total >= 0.9
                and all(t["bonferroni_pass"] for t in pairs.values()))
    return {"due":total, "qualified_scores":n, "unresolved":total-n,
            "quote_coverage_pct":100*n/total if total else None,
            "accuracy_pct":100*hits/n if n else None,
            "wilson95_lower_pct":100*wilson(hits,n) if n else None,
            "comparisons":pairs, "regimes_ex_ante_proxy_only":regimes,
            "errors_and_missing":errors,
            "eligible_for_manual_review_only":eligible,
            "validated_edge":False}

def evaluate(v2, v1, policy, registry, scores):
    if (v2.get("status") != "ok" or v1.get("status") != "ok"
            or not v2.get("locked_implementation_checked")
            or v2.get("protocol_id") != policy["source_protocol_id"]
            or v1.get("full_reachable_history_checked") is not True
            or v2.get("accuracy_70pct_validated") is not False):
        raise ValueError("official V2 audit or frozen provenance not verified")
    if (policy.get("status") != "frozen_shadow_protocol"
            or policy.get("automatic_promotion") is not False
            or policy.get("requires_manual_approval") is not True
            or policy.get("primary_horizons_hours") != [1,24]
            or tuple(policy.get("comparators",[])) != BASELINES):
        raise ValueError("unexpected shadow protocol")
    now = utc(v2["audited_at_utc"])
    effective = utc(policy["effective_from_utc"])
    if effective <= utc(v2["effective_from_utc"]):
        raise ValueError("P1 challenger must not score previous V2 cohort")
    results = {}
    for horizon in (1,24):
        name = str(horizon)+"h"
        official = v2["horizons"][name]["nonoverlap"]
        ids = official["selected_ids"]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate official preselected ID")
        entries = []
        for fid in ids:
            if fid not in registry:
                raise ValueError("missing source prediction ID")
            f = registry[fid]
            if int(f["horizon_hours"]) != horizon:
                raise ValueError("mismatched horizon")
            issued = utc(f["issued_at_utc"])
            target = utc(f["target_utc"])
            if target != issued + timedelta(hours=horizon):
                raise ValueError("bad forecast timestamp")
            record = scores.get(fid)
            if record and record["status"] != "scored":
                record = None
            if record and target + timedelta(minutes=25) > now:
                record = None  # no future scores, even if present in an old snapshot
            if record:
                if utc(record["target_quote_utc"]) < target or utc(record["target_quote_utc"]) > target+timedelta(minutes=25):
                    raise ValueError("unqualified target quote timestamp")
                expected = "UP" if float(record["target_price"]) > float(f["reference_price"]) else "DOWN"
                if record["realized_up"] != expected:
                    raise ValueError("corrupt realized label")
                for model in ("majority","doge_reversal","btc_momentum"):
                    if record[model+"_correct"] != str(int(f[model+"_direction"]==expected)):
                        raise ValueError("corrupt original correctness flag")
            entries.append({"forecast":f,"score":record})
        # These official denominators are certified by the existing V2 evidence auditor.
        matured = [x for x in entries if utc(x["forecast"]["target_utc"])+timedelta(minutes=25) <= now]
        if len(matured) != official["due"] or sum(x["score"] is not None for x in matured) != official["scored"]:
            raise ValueError("V2 source snapshot differs from official evidence-qualified nonoverlap cohort")
        results[name] = score_group(entries,now,effective,policy)
        results[name]["official_v2_nonoverlap_baselines"] = official["models"]
        results[name]["pre_effective_rows_excluded"] = sum(utc(x["forecast"]["issued_at_utc"])<effective for x in entries)
    return {"schema_version":1, "status":"ok", "source_audited_at_utc":v2["audited_at_utc"],
            "protocol_id":policy["protocol_id"], "challenger_model":policy["model_id"],
            "effective_from_utc":policy["effective_from_utc"],
            "source_protocol_id":policy["source_protocol_id"],
            "formal_production_model_unchanged":True,
            "same_qualified_quote_and_preselected_nonoverlap_ids":True,
            "model_is_research_shadow_only":True,
            "no_auto_promotion":True, "claimed_validated_edge":False,
            "tracks":results,
            "limitations":["Challenger vote is derived deterministically from original issuance-time features, not separately deployed live executable orders",
                           "GitHub commit timestamp is not external tamperproof attestation",
                           "No promotion without independent review and market-regime replication",
                           "Ex-ante BTC momentum split is a proxy, not a full market-regime volatility classification"]}

def verify_rule_precommitted(root, policy):
    result = subprocess.run(["git","log","--format=%H","--",POLICY],cwd=root,
                            capture_output=True,text=True,check=True)
    revisions = result.stdout.splitlines()
    if len(revisions) != 1:
        raise ValueError("shadow protocol not committed exactly once; model version needs a new future cohort")
    commit_time = subprocess.run(["git","show","-s","--format=%cI",revisions[0]],
                                 cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    if utc(datetime.fromisoformat(commit_time).astimezone(timezone.utc).isoformat()) >= utc(policy["effective_from_utc"]):
        raise ValueError("shadow rule not precommitted before effective date")
    return revisions[0]

def main():
    try:
        p = json.loads((ROOT/POLICY).read_text(encoding="utf-8"))
        commit = verify_rule_precommitted(ROOT,p)
        rawreg = (ROOT/REGISTRY).read_bytes()
        rawscores = (ROOT/SCORES).read_bytes()
        v2 = json.loads((ROOT/OFFICIAL).read_text(encoding="utf-8"))
        v1 = json.loads((ROOT/LEGACY).read_text(encoding="utf-8"))
        if (hashlib.sha256(rawreg).hexdigest()!=v1["registry_sha256"]
                or hashlib.sha256(rawscores).hexdigest()!=v1["scores_sha256"]):
            raise ValueError("P1 registry/score inputs differ from last official audit")
        out=evaluate(v2,v1,p,read_rows(ROOT/REGISTRY),read_rows(ROOT/SCORES))
        out["shadow_protocol_commit_sha"]=commit
        out["shadow_protocol_sha256"]=hashlib.sha256((ROOT/POLICY).read_bytes()).hexdigest()
    except Exception as exc:
        out={"status":"blocked","error":type(exc).__name__+": "+str(exc)[:400],
             "claimed_validated_edge":False,"no_auto_promotion":True}
    (ROOT/OUT).parent.mkdir(parents=True,exist_ok=True)
    (ROOT/REPORT).parent.mkdir(parents=True,exist_ok=True)
    (ROOT/OUT).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["# DOGE P1 模型竞赛（正式冻结模型 vs 前瞻影子挑战者）","","结论：仅研究；正式预测模型不修改，不自动晋级，不涉及定投或真实下单。",
           "","数据来源：正式 V2 前瞻预测、原始报价证据评分、冻结 P1 规则。"]
    if out["status"]=="ok":
        lines += ["",f"V2 数据审核截止 UTC：{out['source_audited_at_utc']}",
                  f"P1 生效 UTC：{out['effective_from_utc']}。既有历史预测不会补造或事后提升为正式 P1 成绩。",
                  "","|期限|已到期独立样本|有合格报价|影子正确率|Wilson 95%下界|晋级复审|",
                  "|---|---:|---:|---|---|---|"]
        for h,x in out["tracks"].items():
            def pct(v): return "暂无" if v is None else f"{v:.2f}%"
            lines.append(f"|{h}|{x['due']}|{x['qualified_scores']}|{pct(x['accuracy_pct'])}|{pct(x['wilson95_lower_pct'])}|{'仅允许人工复审' if x['eligible_for_manual_review_only'] else '不具备条件'}|")
        lines += ["","官方冻结模型与四个基准的同样本成绩保留在 V2 报告中；此处不将主模型分数替换为影子模型。",
                  "概率校准：主模型未发行概率，本版本不捏造概率或计算虚假 Brier 分数。",
                  "错误复盘：每个正式到期但缺少合格报价的样本保留在分母；无法归因于特定因果因素。",
                  "正式挑战者晋级还需要：>=100个独立有效样本、>=30天、Wilson下界>=70%、覆盖率>=90%、对各基准配对检验及人工复核。"]
    else:
        lines += ["","审计阻断："+out["error"],"任何模型优势或晋级声明均不允许。"]
    (ROOT/REPORT).write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"status":out["status"],"error":out.get("error"),
                      "qualified_1h":out.get("tracks",{}).get("1h",{}).get("qualified_scores",0)}))
    return 0 if out["status"]=="ok" else 1

if __name__=="__main__":
    sys.exit(main())

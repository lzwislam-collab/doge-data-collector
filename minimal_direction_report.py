#!/usr/bin/env python3
"""Strict, evidence-based, single-direction DOGE forecast digest.

Reads immutable V4.2 issued snapshots and frozen V2 audited scoring.
Does not revise model, reissue predictions, or estimate probabilities.
"""
import csv
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REG = "data/direction_v42/forward_registry.csv"
SCORES = "data/direction_v42/forward_scores.csv"
V2 = "data/stage5/forward_validation_v2/latest.json"
PUBLICATION = "data/paired_forward_v1/publication_latest.json"
OUT = "data/minimal_direction_report/latest.json"
MARKDOWN = "reports/DOGE_MINIMAL_DIRECTION.md"
MODEL = "DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1"
PRIMARY = "doge_reversal_direction"
HORIZONS = (1, 24)
GRACE = timedelta(minutes=25)

def utc(value):
    if not isinstance(value, str):
        raise ValueError("missing UTC timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return stamp

def rows(root, filename):
    with (root / filename).open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "forecast_id" not in reader.fieldnames:
            raise ValueError("invalid registry schema: " + filename)
        found = {}
        for row in reader:
            key = row["forecast_id"]
            if not key or key in found:
                raise ValueError("missing/duplicate forecast_id: " + key)
            found[key] = row
        return found

def load(root, filename):
    return json.loads((root / filename).read_text(encoding="utf-8"))

def audit_score(pred, score):
    if score is None:
        return "未评分", None
    state = score["status"]
    if state != "scored":
        if state == "unscorable_missing_target_quote":
            return "到期报价缺失", None
        raise ValueError("unexpected score state")
    target = utc(pred["target_utc"])
    actual_at = utc(score["target_quote_utc"])
    if not target <= actual_at <= target + GRACE:
        raise ValueError("target quote outside allowed scoring window")
    if utc(score["observed_at_utc"]) < actual_at:
        raise ValueError("outcome observed before quote")
    actual = float(score["target_price"])
    reference = float(pred["reference_price"])
    if not math.isfinite(actual) or actual <= 0:
        raise ValueError("invalid outcome price")
    true = "UP" if actual > reference else "DOWN"  # flat is DOWN by frozen rule
    is_correct = (true == pred[PRIMARY])
    if score["realized_up"] != true or score["doge_reversal_correct"] != str(int(is_correct)):
        raise ValueError("frozen score conflicts with actual direction")
    return ("正确" if is_correct else "错误"), actual

def evaluate(root=ROOT, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("now must be UTC")
    register = rows(root, REG)
    scores = rows(root, SCORES)
    if any(fid not in register for fid in scores):
        raise ValueError("orphan score ID")
    forecasts = {h: [] for h in HORIZONS}
    for fid, record in register.items():
        h = int(record["horizon_hours"])
        if h not in HORIZONS:
            continue
        issue = utc(record["issued_at_utc"])
        quote = utc(record["source_quote_utc"])
        target = utc(record["target_utc"])
        ref = float(record["reference_price"])
        if (record["model_id"] != MODEL or record[PRIMARY] not in ("UP", "DOWN")
                or not math.isfinite(ref) or ref <= 0
                or not quote <= issue <= now + timedelta(seconds=5)
                or not 0 <= (issue - quote).total_seconds() <= 120
                or target != issue + timedelta(hours=h)):
            raise ValueError("invalid issued prediction: " + fid)
        if fid in scores:
            audit_score(record, scores[fid])
        forecasts[h].append(record)
    audited = load(root, V2)
    if audited.get("protocol_id") != "DOGE_FORWARD_RAW_QUOTE_BOUND_V2_20261012_0200" or not audited.get("locked_implementation_checked"):
        raise ValueError("official frozen V2 audit unavailable")
    audit_at = utc(audited["audited_at_utc"])
    if audit_at > now + timedelta(seconds=5):
        raise ValueError("official audit is from the future")
    pub = load(root, PUBLICATION)
    confirmed_ids = set(pub.get("new_ids", ())) if pub.get("status") == "verified_remote_readback" else set()
    results = {}
    for h in HORIZONS:
        if not forecasts[h]:
            raise ValueError("no registered forecast for " + str(h) + "h")
        seq = sorted(forecasts[h], key=lambda x: (utc(x["issued_at_utc"]), x["forecast_id"]))
        latest = seq[-1]
        fid = latest["forecast_id"]
        issue = utc(latest["issued_at_utc"])
        target = utc(latest["target_utc"])
        is_public = fid in confirmed_ids and utc(pub["verified_at_utc"]) >= issue and utc(pub["verified_at_utc"]) < target
        # Never substitute another candidate model when publication cannot be proven.
        current_state = ("已到期" if target <= now else "待到期") if is_public else "公开登记待核验"
        latest_due = next((r for r in reversed(seq) if utc(r["target_utc"]) <= now), None)
        due_result = None
        if latest_due:
            due_result = {"forecast_id": latest_due["forecast_id"],
                          "target_utc": latest_due["target_utc"]}
            due_result["result"], due_result["target_price"] = audit_score(
                latest_due, scores.get(latest_due["forecast_id"]))
        official = audited["horizons"][str(h) + "h"]
        fixed = official["nonoverlap"]
        tested = fixed["models"]["doge_reversal"]
        n = int(tested["n"])
        correct = int(tested["correct"])
        if n != int(fixed["scored"]) or not 0 <= correct <= n:
            raise ValueError("official V2 fixed scoring denominator changed")
        if abs(float(tested["accuracy_pct"]) - (100 * correct / n if n else 0)) > .02 if n else tested["accuracy_pct"] is not None:
            raise ValueError("official accuracy inconsistent")
        results[str(h) + "h"] = {
            "forecast_id": fid, "issued_at_utc": latest["issued_at_utc"],
            "target_utc": latest["target_utc"],
            "reference_price": float(latest["reference_price"]),
            "direction": latest[PRIMARY], "publication_verified": is_public,
            "forecast_state": current_state, "most_recent_due": due_result,
            "official_nonoverlap_correct": correct,
            "official_nonoverlap_scored": n,
            "official_accuracy_pct": round(100 * correct / n, 2) if n else None
        }
    issuance = audited["horizons"]["1h"]["issuance"]
    coverage = issuance.get("coverage_pct")
    if int(issuance["missing"]) != int(issuance["expected"]) - int(issuance["issued"]):
        raise ValueError("invalid issuance denominator")
    issues = []
    if audit_at < now - timedelta(hours=2):
        issues.append("正式V2评分超过2小时未刷新")
    if coverage is None or coverage < 90:
        issues.append("正式预测发行覆盖不足90%")
    if any(not v["publication_verified"] for v in results.values()):
        issues.append("最新预测公开登记尚未核验")
    if not audited.get("accuracy_70pct_validated", False):
        issues.append("70%准确率尚未验证")
    return {
        "schema_version": 1, "status": "REVISE" if issues else "PASS",
        "generated_at_utc": now.isoformat(), "v2_audited_at_utc": audit_at.isoformat(),
        "official_source": V2, "frozen_production_model": MODEL,
        "primary_direction_field": PRIMARY, "horizons": results,
        "issuance_coverage_pct": coverage, "audit_findings": issues,
        "predictive_edge_validated": False,
        "disclaimer": "方向是发行时已记录的明确预测，不代表已证明准确率或概率校准。"
    }

def render(result):
    fmt = lambda x: "上涨" if x == "UP" else "下跌"
    lines = ["# DOGE 极简方向预测｜正式冻结V2",
             "", "生成UTC：" + result["generated_at_utc"],
             "；正式审计UTC：" + result["v2_audited_at_utc"], "",
             "| 期限 | 唯一方向 | 发行参考价(USDT) | 到期UTC | 状态 |",
             "|---|---|---:|---|---|"]
    for label in ("1h", "24h"):
        row = result["horizons"][label]
        state = row["forecast_state"]
        lines.append(f"| {label} | {fmt(row['direction'])} | {row['reference_price']:.8f} | {row['target_utc']} | {state} |")
    lines += ["", "## 上一期到期核验", ""]
    for label in ("1h", "24h"):
        due = result["horizons"][label]["most_recent_due"]
        lines.append(f"- {label}：" + (due["result"] if due else "暂无成熟预测"))
    lines += ["", "## 正式V2固定非重叠前瞻成绩", ""]
    for label in ("1h", "24h"):
        row = result["horizons"][label]
        n = row["official_nonoverlap_scored"]
        lines.append(f"- {label}：" + (f"{row['official_nonoverlap_correct']}/{n} = {row['official_accuracy_pct']:.2f}%" if n else "暂无成熟样本"))
    lines += ["", "## 审计", "", "状态：" + result["status"],
              "。预测优势：尚未验证。"]
    lines += ["- " + finding for finding in result["audit_findings"]]
    lines += ["", result["disclaimer"],
              "若公开登记未核验，该行不得作为已确认正式发行预测。", ""]
    return "\n".join(lines)

def main():
    output = evaluate()
    view = render(output)
    for filename, text in ((OUT, json.dumps(output, ensure_ascii=False, indent=2) + "\n"),
                           (MARKDOWN, view)):
        path = ROOT / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    print(view)

if __name__ == "__main__":
    main()

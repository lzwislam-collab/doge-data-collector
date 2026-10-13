"""P1 research challenger: ex-ante DOGE/BTC volatility-normalized relative trend.

Fixed, non-fitted formula. Reads independently published pre-target minimal
contexts and original V2 quote scores. No retrospective backfill, automatic
promotion, calibrated probability, or execution. All missing source/score
observations stay in the denominator.
"""
import hashlib
import json
import math
import subprocess
from datetime import datetime,timedelta,timezone
from pathlib import Path

import minimal_market_forward as minimal
import minimal_market_candidate as candidate
import direction_forward_v42 as frozen
import p1_model_competition as league
import paired_cohort_audit as paired

ROOT=Path(__file__).resolve().parent
PROTOCOL="data/stage5/p1_regime_forward_v1_protocol.json"
OUT="data/p1_regime_forward_v1/latest.json"
REPORT="reports/DOGE_P1_REGIME_FORWARD_V1.md"
FROZEN_V2="data/stage5/forward_validation_v2/latest.json"
MINIMAL_AUDIT="data/minimal_market_forward_v1/audit_latest.json"
SOURCE_REGISTRY="data/direction_v42/forward_registry.csv"
SOURCE_SCORES="data/direction_v42/forward_scores.csv"
SOURCE_BINDINGS="data/quote_evidence_v2/bindings.csv"
ID="DOGE_P1_RELATIVE_VOL_REGIME_V1_20261014"
BASELINES=("doge_reversal","majority","btc_momentum","always_up","always_down")


def utc(value):
    if isinstance(value,str):
        value=datetime.fromisoformat(value.replace("Z","+00:00"))
    if value.tzinfo is None or value.utcoffset()!=timedelta(0):
        raise ValueError("UTC required")
    return value


def verify_protocol(root,p):
    if (p.get("protocol_id")!=ID or p.get("effective_from_utc")!="2026-10-14T00:00:00+00:00"
        or p.get("horizons_hours")!=[1,24] or p.get("model_training")!="none_fixed_rule"
        or p.get("no_backfill") is not True or p.get("no_auto_promotion") is not True
        or p.get("read_only") is not True or p.get("paper_action")!="NO_TRADE"
        or p.get("volatility_stress_ratio")!=1.8
        or p.get("doge_volume_stress_ratio")!=2.0
        or p.get("btc_weight_stress")!=2.0 or p.get("btc_weight_normal")!=1.0
        or p.get("relative_strength_weight")!=0.5
        or p.get("minimum_forward_nonoverlap_scores")!=100
        or p.get("required_issuer_and_quote_coverage_pct")!=90
        or p.get("minimum_observation_days")!=30
        or p.get("comparators")!=list(BASELINES)):
        raise ValueError("precommitted regime protocol changed")
    blob=p.get("model_code_blob_sha")
    if not isinstance(blob,str) or len(blob)!=40 or any(c not in "0123456789abcdef" for c in blob):
        raise ValueError("unlocked challenger code blob")
    if (root/".git").exists():
        actual=subprocess.run(["git","hash-object","p1_regime_forward_v1.py"],cwd=root,
            capture_output=True,text=True,check=True).stdout.strip()
        if actual!=blob:
            raise ValueError("changed challenger code; create new future cohort instead")
    return utc(p["effective_from_utc"])


def predict(features,h,p):
    """Only 25 already-confirmed bars; no prices, labels or scores after issue."""
    if h not in (1,24): raise ValueError("unsupported horizon")
    if not isinstance(features,dict) or set(("BTC-USDT","DOGE-USDT"))-set(features):
        raise ValueError("missing pre-issue features")
    d=features["DOGE-USDT"];b=features["BTC-USDT"]
    required=("return_1h","return_24h","realized_volatility_24h","volume_ratio_1h_to_prior24h")
    for item in (b,d):
        for key in required:
            value=item.get(key)
            if not isinstance(value,(float,int)) or isinstance(value,bool) or not math.isfinite(value):
                raise ValueError("missing/nonfinite ex-ante feature: "+key)
        if item["realized_volatility_24h"]<=0 or item["volume_ratio_1h_to_prior24h"]<0:
            raise ValueError("invalid volatility or volume")
    stressed=(d["realized_volatility_24h"]/b["realized_volatility_24h"]>=p["volatility_stress_ratio"]
              or d["volume_ratio_1h_to_prior24h"]>=p["doge_volume_stress_ratio"])
    btc_weight=p["btc_weight_stress"] if stressed else p["btc_weight_normal"]
    horizon_scale=math.sqrt(24) if h==24 else 1.0
    d_z=d["return_24h" if h==24 else "return_1h"]/(d["realized_volatility_24h"]*horizon_scale)
    b_z=b["return_24h" if h==24 else "return_1h"]/(b["realized_volatility_24h"]*horizon_scale)
    rel=(d["return_24h"]-b["return_24h"])/(math.sqrt(24)*max(d["realized_volatility_24h"],b["realized_volatility_24h"]))
    score=d_z+btc_weight*b_z+p["relative_strength_weight"]*rel
    if not math.isfinite(score):raise ValueError("nonfinite challenger signal")
    direction="UP" if score>0 else "DOWN" if score<0 else None
    return {"direction":direction,"raw_standardized_signal":round(score,9),
        "stress_regime":stressed,"btc_weight":btc_weight,
        "probability_up":None,"probability_calibrated":False,
        "paper_action":"NO_TRADE"}


def score_track(selected,contexts,scores,rejected,effective,now,p):
    due=[r for r in selected if utc(r["issued_at_utc"])>=effective
         and utc(r["target_utc"])+timedelta(minutes=25)<=now]
    missing={"missing_context":0,"missing_features":0,"not_verified_pre_target":0,
             "missing_target_quote":0,"zero_signal":0}
    matched=[]
    for original in due:
        fid=original["forecast_id"]
        ctx=contexts.get(fid)
        if not ctx:
            missing["missing_context"]+=1;continue
        if ctx.get("feature_status")!="PREPARED_NOT_ISSUED":
            missing["missing_features"]+=1;continue
        if fid in rejected:
            missing["not_verified_pre_target"]+=1;continue
        if not isinstance(ctx.get("_prepared_features"),dict):
            missing["missing_features"]+=1;continue
        signal=predict(ctx["_prepared_features"],int(original["horizon_hours"]),p)
        if signal["direction"] is None:
            missing["zero_signal"]+=1;continue
        outcome=scores.get(fid)
        if not outcome or outcome["status"]!="scored":
            missing["missing_target_quote"]+=1;continue
        if outcome["realized_up"] not in ("UP","DOWN"):
            raise ValueError("invalid original realized direction")
        target=utc(original["target_utc"])
        qt=utc(outcome["target_quote_utc"])
        if not target<=qt<=target+timedelta(minutes=25):
            raise ValueError("original target quote outside frozen window")
        true_dir="UP" if float(outcome["target_price"])>float(original["reference_price"]) else "DOWN"
        if outcome["realized_up"]!=true_dir:raise ValueError("original target label changed")
        matched.append((original,signal,true_dir))
    n=len(matched);k=sum(signal["direction"]==y for _,signal,y in matched)
    confusion={s:{"n":0,"correct":0} for s in ("UP","DOWN")}
    for row,signal,y in matched:
        confusion[y]["n"]+=1;confusion[y]["correct"]+=int(signal["direction"]==y)
    comparisons={}
    for name in BASELINES:
        wins=losses=0
        for row,signal,y in matched:
            baseline=("UP" if name=="always_up" else "DOWN" if name=="always_down"
                      else row[name+"_direction"])
            if baseline not in ("UP","DOWN"):raise ValueError("invalid preregistered baseline")
            wins+=int(signal["direction"]==y and baseline!=y)
            losses+=int(signal["direction"]!=y and baseline==y)
        comparisons[name]={"wins":wins,"losses":losses,
                           "mcnemar_p_descriptive_only":league.exact_mcnemar(wins,losses),
                           "candidate_promoted":False}
    denom=len(due)
    return {"matured_original_nonoverlap_slots":denom,"scored":n,
        "missing_or_abstained":denom-n,"missing_by_reason":missing,
        "accuracy_pct":100*k/n if n else None,
        "accuracy_missing_as_wrong_pct":100*k/denom if denom else None,
        "wilson95_lower_pct_descriptive":100*league.wilson(k,n) if n else None,
        "balanced_accuracy_pct":50*sum(confusion[s]["correct"]/confusion[s]["n"] for s in confusion)
            if all(confusion[s]["n"] for s in confusion) else None,
        "actual_up_recall_pct":100*confusion["UP"]["correct"]/confusion["UP"]["n"] if confusion["UP"]["n"] else None,
        "actual_down_recall_pct":100*confusion["DOWN"]["correct"]/confusion["DOWN"]["n"] if confusion["DOWN"]["n"] else None,
        "quote_coverage_pct":100*n/denom if denom else None,
        "comparisons":comparisons,"no_economic_edge_claim":True,"no_auto_promotion":True,
        "candidate_70pct_proven":False}


def evaluate(root,now=None):
    root=Path(root);now=utc(now or datetime.now(timezone.utc))
    p=json.loads((root/PROTOCOL).read_bytes())
    effective=verify_protocol(root,p)
    base={"schema_version":1,"protocol_id":ID,"evaluated_at_utc":now.isoformat(),
          "effective_from_utc":effective.isoformat(),"source":"Same-event frozen V2 quotes and minimal market preregistered issue-time features",
          "production_v2_unchanged":True,"trained_model_replaced":False,
          "valid_probability_claim":False,"validated_70pct":False,
          "economic_edge_validated":False,"no_trade":True,"real_orders_authorized":False}
    if now<effective:
        return dict(base,status="WAIT_ACTIVATION",tracks={},
                    preregistration_verified_from_parent=False)
    minimal.guard(root)
    contexts_raw=minimal.history(root)
    original=frozen.read_rows(root/SOURCE_REGISTRY,frozen.FCOLS)
    scores=frozen.read_rows(root/SOURCE_SCORES,frozen.SCOLS)
    official=json.loads((root/FROZEN_V2).read_bytes())
    prior=json.loads((root/MINIMAL_AUDIT).read_bytes())
    if official.get("status")!="ok" or official.get("locked_implementation_checked") is not True:
        raise ValueError("official V2 provenance not accepted")
    if prior.get("status")!="ACTIVE_RESEARCH_SHADOW" or prior.get("preregistration_server_witness_verified") is not True:
        raise ValueError("minimal pre-activation publication witness missing")
    for ts in (official["audited_at_utc"],prior["audited_at_utc"]):
        age=(now-utc(ts)).total_seconds()
        if not 0<=age<=1200:raise ValueError("stale source audit")
    if prior.get("registered_contexts")!=len(contexts_raw):
        raise ValueError("new contexts not included in source audit")
    rejected=prior.get("rejected")
    if not isinstance(rejected,dict):raise ValueError("missing rejection ledger")
    contexts={}
    for rec in contexts_raw:
        row=rec["original"];fid=row["forecast_id"]
        if fid not in original or original[fid]!=row:raise ValueError("original issued forecast changed")
        if fid in contexts:raise ValueError("duplicate original source context")
        issued=utc(row["issued_at_utc"])
        if not utc(rec["prepared_at_utc"])<=issued<=utc(rec["decision_recorded_at_utc"])<utc(row["target_utc"]):
            raise ValueError("preparation/decision used after outcome")
        if rec["feature_status"]=="PREPARED_NOT_ISSUED":
            file=root/minimal.BASE/"prepared"/(rec["prepared_sha256"]+".json")
            prepared=json.loads(file.read_bytes())
            if minimal.digest(prepared)!=rec["prepared_sha256"]:
                raise ValueError("historical prepared snapshot mismatch")
            rec=dict(rec)
            rec["_prepared_features"]=prepared["features"]
        contexts[fid]=rec
    tracks={}
    for h in (1,24):
        selected=official["horizons"][str(h)+"h"]["nonoverlap"]["selected_ids"]
        if len(selected)!=len(set(selected)):raise ValueError("duplicate original nonoverlap reservation")
        group=[original[fid] for fid in selected]
        if any(int(row["horizon_hours"])!=h for row in group):
            raise ValueError("mixed source horizons")
        tracks[str(h)+"h"]=score_track(group,contexts,scores,rejected,effective,now,p)
        tracks[str(h)+"h"]["minimum_review_n"]=p["minimum_forward_nonoverlap_scores"]
        tracks[str(h)+"h"]["eligible_for_manual_review_only"]=False
    return dict(base,status="ACTIVE_RESEARCH_NOT_VALIDATED",tracks=tracks,
                preregistration_verified_from_parent=True,
                parent_first_publication_witness=prior.get("preregistration_witness"),
                original_forecast_total=len(original),score_total=len(scores),
                limitations=["New rule is a deterministic derived challenger over original published pre-target features, not an independently time-issued original forecast.",
                             "No learning or probability calibration; raw signal strength is not a 50% probability claim.",
                             "Older outcomes before activation are strictly excluded, even if known.",
                             "Missing original issues are tracked separately by P0 and are not reconstructed here.",
                             "Original V2 frozen model, original responses and paper economics unchanged."])


def main(root=ROOT,now=None):
    root=Path(root);now=utc(now or datetime.now(timezone.utc))
    try:
        result=evaluate(root,now)
    except Exception as exc:
        result={"status":"BLOCKED","evaluated_at_utc":now.isoformat(),
                "error":type(exc).__name__+": "+str(exc)[:300],
                "production_v2_unchanged":True,
                "validated_70pct":False,"economic_edge_validated":False}
    output=root/OUT;output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"valid_accuracy_70pct":False}))
    return 1 if result["status"]=="BLOCKED" else 0


if __name__=="__main__":
    raise SystemExit(main())

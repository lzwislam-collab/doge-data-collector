#!/usr/bin/env python3
"""P1 V2: causal trainable shadow predictions; independent future-only cohort.
Uses one existing daily workflow. Never edits official V2/P1 V1 registries or trades.
"""
import csv
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import direction_baseline_v42 as bars
import direction_forward_v42 as frozen
import quote_evidence_v2 as quotes
from p1_model_competition import wilson, exact_mcnemar

ROOT = Path(__file__).resolve().parent
POLICY = "data/stage5/p1_adaptive_v2_protocol.json"
REG = "data/stage5/p1_adaptive_v2/registry.csv"
SCORE = "data/stage5/p1_adaptive_v2/scores.csv"
OUT = "data/stage5/p1_adaptive_v2/latest.json"
REPORT = "reports/DOGE_P1_ADAPTIVE_V2.md"
SNAPS = "data/stage5/p1_adaptive_v2/snapshots"
C = ["forecast_id","model_id","issued_at_utc","source_quote_utc","reference_price",
     "horizon_hours","target_utc","last_confirmed_close_utc","p_up","direction",
     "frozen_rule_recomputed","historical_majority","btc_momentum","always_up","always_down",
     "training_samples","training_last_label_utc","training_sha256","weights_json","weights_sha256",
     "issue_quote_sha256","walkforward_holdout_accuracy","walkforward_holdout_brier"]
SC = ["forecast_id","status","scored_at_utc","target_quote_utc","target_price","target_quote_sha256",
      "realized_up","correct","brier","reason"]
NAMES = ("frozen_rule_recomputed","historical_majority","btc_momentum","always_up","always_down")

def utc(s):
    d=datetime.fromisoformat(s.replace("Z","+00:00"))
    if d.tzinfo is None or d.utcoffset()!=timedelta(0):
        raise ValueError("UTC required")
    return d

def canon(obj):
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def digest(obj):
    return hashlib.sha256(canon(obj).encode()).hexdigest()

def rows(path, cols):
    if not path.exists(): return {}
    with path.open(newline="",encoding="utf-8") as stream:
        reader=csv.DictReader(stream)
        if reader.fieldnames!=cols: raise ValueError("registry schema change: "+str(path))
        data={}
        for row in reader:
            fid=row["forecast_id"]
            if not fid or fid in data: raise ValueError("duplicate or empty forecast ID")
            data[fid]=row
        return data

def write_rows(path,data,cols):
    path.parent.mkdir(parents=True,exist_ok=True)
    f=io.StringIO()
    w=csv.DictWriter(f,fieldnames=cols,lineterminator="\n")
    w.writeheader()
    w.writerows(data[key] for key in sorted(data))
    atomic(path,f.getvalue())

def atomic(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+".tmp")
    temporary.write_text(text,encoding="utf-8")
    os.replace(temporary,path)

def history_guard(root,rel,cols,data):
    """Check all reachable versions for appended-only records; not tamper-proof off GitHub."""
    path=root/rel
    rev=subprocess.run(["git","log","--reverse","--format=%H","--",rel],
                       cwd=root,text=True,capture_output=True,check=True).stdout.splitlines()
    previous={}
    for commit in rev:
        body=subprocess.run(["git","show",commit+":"+rel],
                            cwd=root,text=True,capture_output=True,check=True).stdout
        reader=csv.DictReader(io.StringIO(body))
        if reader.fieldnames!=cols: raise ValueError("historic schema changed")
        current={}
        for row in reader:
            fid=row["forecast_id"]
            if not fid or fid in current: raise ValueError("historic duplicate forecast")
            current[fid]=row
        if any(current.get(k)!=v for k,v in previous.items()):
            raise ValueError("historical record removed or modified")
        previous=current
    if any(data.get(k)!=v for k,v in previous.items()):
        raise ValueError("previously committed record modified")

def precommit(root,policy):
    if (policy["status"]!="frozen_future_shadow" or
        policy["automatic_promotion"] or not policy["manual_promotion_only"] or
        policy["bonferroni_per_comparison"]!=0.01 or tuple(policy["comparators"])!=NAMES):
        raise ValueError("invalid P1 V2 protocol")
    for rel in (POLICY,"p1_adaptive_v2.py"):
        commits=subprocess.run(["git","log","--format=%H","--",rel],cwd=root,
                               text=True,capture_output=True,check=True).stdout.splitlines()
        if len(commits)!=1: raise ValueError("P1 V2 frozen source revised or not committed: "+rel)
        stamp=subprocess.run(["git","show","-s","--format=%cI",commits[0]],cwd=root,
                             text=True,capture_output=True,check=True).stdout.strip()
        if utc(datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat())>=utc(policy["effective_from_utc"]):
            raise ValueError("P1 V2 rule published after effective time")

def feat(prices,i):
    if i<24: raise ValueError("insufficient feature history")
    d=prices["DOGE-USDT"];b=prices["BTC-USDT"]
    def r(arr,step,scale):
        return max(-4.,min(4.,math.log(arr[i]/arr[i-step])*scale))
    return [1.,r(d,1,20),r(d,24,5),r(b,1,20),r(b,24,5),abs(r(d,24,5))]

def sigmoid(x):
    x=max(-30.,min(30.,x))
    return 1/(1+math.exp(-x))

def probability(w,x):
    return sigmoid(sum(a*b for a,b in zip(w,x)))

def train(data, iterations=80, eta=.09, l2=.006):
    if not data: raise ValueError("empty training rows")
    w=[0.]*len(data[0][0])
    for _ in range(iterations):
        grad=[0.]*len(w)
        for x,y in data:
            diff=probability(w,x)-y
            for j,value in enumerate(x): grad[j]+=diff*value
        for j in range(len(w)):
            w[j]-=eta*(grad[j]/len(data)+(0 if j==0 else l2*w[j]))
    return [round(v,12) for v in w]

def fit_asof(times,prices,h,anchor_index,policy):
    """Historical labels must mature by the anchor's last confirmed close."""
    if anchor_index < 200 or h not in (1,24): raise ValueError("invalid fit history/horizon")
    max_anchors=policy["fit"]["training_max_anchors"]
    indices=list(range(24,anchor_index-h+1,h if h==24 else 1))
    indices=indices[-max_anchors:]
    examples=[(i,feat(prices,i),int(prices["DOGE-USDT"][i+h]>prices["DOGE-USDT"][i])) for i in indices]
    if len(examples)<policy["fit"]["minimum_train"]: raise ValueError("insufficient matured examples")
    split=int(len(examples)*(1-policy["fit"]["holdout_fraction"]))
    first_holdout=examples[split][0]
    # Purge training labels that touch holdout feature/label interval.
    train_only=[(x,y) for i,x,y in examples[:split] if i+h<first_holdout]
    holdout=examples[split:]
    if len(train_only)<policy["fit"]["minimum_train"]: raise ValueError("insufficient purged training")
    fit=policy["fit"]
    diagnostic=train(train_only,fit["iterations"],fit["learning_rate"],fit["l2"])
    correct=sum((probability(diagnostic,x)>=.5)==bool(y) for _,x,y in holdout)
    brier=sum((probability(diagnostic,x)-y)**2 for _,x,y in holdout)/len(holdout)
    # Final trained on every label genuinely matured by the current prediction anchor.
    full=[(x,y) for _,x,y in examples]
    weights=train(full,fit["iterations"],fit["learning_rate"],fit["l2"])
    return weights, {"n":len(full),"last_label_utc":datetime.fromtimestamp(
        (times[examples[-1][0]+h]+bars.HOUR)/1000,timezone.utc).isoformat(),
        "training_sha256":digest(examples),
        "holdout_accuracy":round(correct/len(holdout),6),
        "holdout_brier":round(brier,8),"holdout_n":len(holdout)}

def create_snap(root,raw,now):
    payload={"schema_version":2,"source_url":quotes.URL,"observed_at_utc":now.isoformat(),
             "raw_response_utf8":raw,"transport":"HTTPS observed at issuance; exchange not digitally signed"}
    price,stamp,_=quotes.parse(payload)
    rawbytes=(canon(payload)+"\n").encode()
    qid=hashlib.sha256(rawbytes).hexdigest()
    path=root/SNAPS/(qid+".json")
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=rawbytes: raise ValueError("hash collision")
    else:
        with path.open("xb") as f:f.write(rawbytes)
    return qid,price,stamp

def issue(root,now,policy,registry,raw=None):
    if now<utc(policy["effective_from_utc"]): return []
    missing=[h for h in (1,24) if f"{policy['model_id']}_{now:%Y%m%d}_{h}h" not in registry]
    if not missing: return []
    times,prices=bars.read_csv(root/"data/hourly_research/ohlc.csv",bars.HOUR)
    last_close=datetime.fromtimestamp((times[-1]+bars.HOUR)/1000,timezone.utc)
    age=(now-last_close).total_seconds()/3600
    if age<0 or age>policy["max_candle_staleness_hours"]:
        raise ValueError("stale or future confirmed hourly features")
    if raw is None:
        raise ValueError("issuer raw quote must be captured before observation clock")
    qid,price,stamp=create_snap(root,raw,now)
    if not 0<=(now-stamp).total_seconds()<=policy["max_quote_age_seconds"]:
        raise ValueError("unfresh issuer quote")
    new=[]
    for h in missing:
        weights,meta=fit_asof(times,prices,h,len(times)-1,policy)
        p=probability(weights,feat(prices,len(times)-1))
        same=frozen.features(times,prices,h)
        fid=f"{policy['model_id']}_{now:%Y%m%d}_{h}h"
        record=dict(zip(C,[fid,policy["model_id"],now.isoformat(),stamp.isoformat(),
           format(price,".12g"),str(h),(now+timedelta(hours=h)).isoformat(),
           last_close.isoformat(),format(p,".12g"),"UP" if p>=.5 else "DOWN",
           same["reversal"],same["majority"],same["btc_momentum"],"UP","DOWN",
           str(meta["n"]),meta["last_label_utc"],meta["training_sha256"],
           canon(weights),digest(weights),qid,str(meta["holdout_accuracy"]),str(meta["holdout_brier"])]))
        registry[fid]=record;new.append(fid)
    return new

def quote_candidates(root):
    """Only genuine P0 captured raw responses; never backfill exchange history."""
    result=[]
    base=root/quotes.BASE/"snapshots"
    for path in base.glob("*/*.json"):
        try:
            price,ts,seen=quotes.load_quote(root,path.stem)
            result.append((seen,ts,price,path.stem,path.relative_to(root).as_posix()))
        except (ValueError,KeyError,TypeError): raise ValueError("invalid frozen raw quote evidence "+str(path))
    return sorted(result)

def first_commit(root,rel):
    result=subprocess.run(["git","log","--reverse","--format=%cI","--",rel],cwd=root,
                          text=True,capture_output=True,check=True).stdout.splitlines()
    return utc(datetime.fromisoformat(result[0]).astimezone(timezone.utc).isoformat()) if result else None

def score_due(root,now,policy,registry,scores,evidence=None,receipts=True):
    if evidence is None: evidence=quote_candidates(root)
    due=[r for r in registry.values() if r["forecast_id"] not in scores
         and utc(r["target_utc"])+timedelta(minutes=policy["target_quote_window_minutes"])<=now]
    for r in sorted(due,key=lambda x:x["forecast_id"]):
        fid=r["forecast_id"];target=utc(r["target_utc"])
        eligible=[x for x in evidence if target<=x[1]<=x[0]<=target+timedelta(minutes=25)]
        accepted=None;why="no_genuinely_observed_target_quote"
        issue_ok=True
        if receipts:
            # Per-ID first appearance, not the creation timestamp of the CSV itself.
            # A late appended row must not inherit an older row's timely publication.
            from forward_validation import history_receipts
            hist = history_receipts(root,REG,C,registry)
            rec = hist.get(fid)
            reg_commit=utc(rec["committed_at_utc"]) if rec else None
            source_commit=first_commit(root,SNAPS+"/"+r["issue_quote_sha256"]+".json")
            issue_ok=bool(reg_commit and source_commit and
                          reg_commit<target and source_commit<target and
                          reg_commit<=utc(r["issued_at_utc"])+timedelta(minutes=45) and
                          source_commit<=utc(r["issued_at_utc"])+timedelta(minutes=45))
        if not issue_ok:why="issue_not_timely_public"
        else:
            for candidate in eligible:
                if not receipts:
                    accepted=candidate;break
                commit=first_commit(root,candidate[4])
                if commit is not None and commit<=target+timedelta(minutes=45):
                    accepted=candidate;break
            if eligible and accepted is None:why="target_quote_not_timely_public"
        if accepted:
            seen,ts,price,qid,_=accepted
            label="UP" if price>float(r["reference_price"]) else "DOWN"
            p=float(r["p_up"]);k=int(label==r["direction"])
            record=[fid,"scored",now.isoformat(),ts.isoformat(),format(price,".12g"),qid,
                    label,str(k),format((p-int(label=="UP"))**2,".12g"),""]
        else:
            record=[fid,"unscorable",now.isoformat(),"","","","","","",""+why]
        scores[fid]=dict(zip(SC,record))

def report(policy,registry,scores,now):
    eff=utc(policy["effective_from_utc"])
    summary={}
    for h in (1,24):
        closed=max(0,int((now-eff-timedelta(days=0,hours=24)).total_seconds()//86400)+1)
        group=[r for r in registry.values() if int(r["horizon_hours"])==h and utc(r["issued_at_utc"])>=eff]
        group.sort(key=lambda r:(utc(r["issued_at_utc"]),r["forecast_id"]))
        selected=[];reserved=None
        for r in group:
            if reserved is None or utc(r["issued_at_utc"])>=reserved:
                selected.append(r);reserved=utc(r["target_utc"])+timedelta(minutes=25)
        eligible=[r for r in selected if utc(r["target_utc"])+timedelta(minutes=25)<=now]
        qualified=[(r,scores[r["forecast_id"]]) for r in eligible
                   if scores.get(r["forecast_id"],{}).get("status")=="scored"]
        covered=len({utc(r["issued_at_utc"]).date() for r in group
                     if utc(r["issued_at_utc"])<eff+timedelta(days=closed)})
        n=len(qualified);k=sum(int(s["correct"]) for _,s in qualified)
        pairs={}
        for base in NAMES:
            win=sum(s["realized_up"]==r["direction"] and s["realized_up"]!=r[base] for r,s in qualified)
            loss=sum(s["realized_up"]!=r["direction"] and s["realized_up"]==r[base] for r,s in qualified)
            p=exact_mcnemar(win,loss)
            pairs[base]={"wins":win,"losses":loss,"p_exact":p,
                         "passes_fiveway_bonferroni":win>loss and win+loss>=policy["min_discordant_pairs"]
                         and p<=policy["bonferroni_per_comparison"]}
        issuance=covered/closed if closed else None
        quote_cov=n/len(eligible) if eligible else None
        age=(now-eff).total_seconds()/86400
        meet=bool(n>=policy["minimum_valid_nonoverlap"] and age>=policy["minimum_observation_days"]
                  and issuance is not None and issuance>=policy["required_issuance_coverage"]
                  and quote_cov is not None and quote_cov>=policy["required_quote_coverage"]
                  and wilson(k,n)>=policy["wilson95_lower_min"]
                  and all(x["passes_fiveway_bonferroni"] for x in pairs.values()))
        summary[str(h)+"h"]={"expected_closed_daily_issue_slots":closed,"issued":covered,
            "missing_issue_slots":closed-covered,"issued_predictions":len(group),
            "fixed_nonoverlap_selected":len(selected),"overlap_excluded":len(group)-len(selected),
            "due":len(eligible),"scored":n,"unscorable_or_missing":len(eligible)-n,
            "issuance_coverage_pct":100*issuance if issuance is not None else None,
            "quote_coverage_pct":100*quote_cov if quote_cov is not None else None,
            "accuracy_pct":100*k/n if n else None,"wilson95_lower_pct":100*wilson(k,n) if n else None,
            "brier_mean":sum(float(s["brier"]) for _,s in qualified)/n if n else None,
            "paired":pairs,"promotion_review_eligible_only":meet,
            "promotion_authorized":False}
    return {"schema_version":2,"status":"ok","evaluated_at_utc":now.isoformat(),
       "protocol_id":policy["protocol_id"],"effective_from_utc":eff.isoformat(),
       "training_automated":True,"formal_v2_unchanged":True,"p1_v1_unchanged":True,
       "same_issue_recomputed_baselines_not_formal_v2_competition":True,
       "validated_70pct":False,"automatic_promotion":False,"spot_profit_validated":False,
       "scoring_source":"P0 actual raw OKX target quote captured within 25m and timely Git-committed",
       "horizons":summary}

def main(root=ROOT,now=None,raw=None):
    now=now or datetime.now(timezone.utc)
    p=json.loads((root/POLICY).read_text(encoding="utf-8"))
    precommit(root,p)
    registry=rows(root/REG,C);scores=rows(root/SCORE,SC)
    history_guard(root,REG,C,registry);history_guard(root,SCORE,SC,scores)
    if raw is None and now>=utc(p["effective_from_utc"]) and any(
            f"{p['model_id']}_{now:%Y%m%d}_{h}h" not in registry for h in (1,24)):
        request=urllib.request.Request(quotes.URL,headers={"Accept":"application/json","User-Agent":"DOGE-P1-V2/1"})
        with urllib.request.urlopen(request,timeout=18) as response:
            raw=response.read().decode("utf-8")
        # Clock must be AFTER the exchange reply, not before the network request.
        now=datetime.now(timezone.utc)
    added=issue(root,now,p,registry,raw=raw)
    if added:write_rows(root/REG,registry,C)
    # A newly issued prediction cannot mature in the same daily run.
    score_due(root,now,p,registry,scores)
    if scores!=rows(root/SCORE,SC): write_rows(root/SCORE,scores,SC)
    result=report(p,registry,scores,now)
    result["new_forecasts"]=added
    atomic(root/OUT,json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    lines=["# DOGE P1 V2 自适应前瞻挑战者","",
      "状态：仅影子研究；自动训练不等于自动晋级，未验证70%正确率或盈利。",
      "数据源：OKX历史小时线、发行即时真实ticker、P0哈希归档的真实到期ticker；不是回溯造预测。",
      "审核截止 UTC："+now.isoformat(),"未来生效："+p["effective_from_utc"],"",
      "|期限|已关闭发行日|实际发行|到期/合格|准确率|晋级资格|",
      "|---|---:|---:|---:|---|---|"]
    for h,x in result["horizons"].items():
        rate="暂无" if x["accuracy_pct"] is None else "%.2f%%"%x["accuracy_pct"]
        lines.append(f'|{h}|{x["expected_closed_daily_issue_slots"]}|{x["issued"]}|{x["due"]}/{x["scored"]}|{rate}|{"待人工审查" if x["promotion_review_eligible_only"] else "未满足"}|')
    lines.extend(["","训练采用过去已成熟的小时K线标签，保留训练摘要和固定历史验证诊断；未来报价预测独立验证。",
                  "比较基准是在本次新发行时间重新计算的规则，并非正式V2已发行记录的配对比较。",
                  "五个基准采用 Bonferroni p<=0.01（族错误率5%），需要独立未来样本才可能通过。",
                  "冻结P1 V1/V2正式预测不修改；P0不增加重复定时器；不交易，不涉及定投。"])
    atomic(root/REPORT,"\n".join(lines)+"\n")
    print(json.dumps({"status":"ok","new":len(added),"horizons":{k:(v["due"],v["scored"]) for k,v in result["horizons"].items()}},ensure_ascii=False))
    return result

if __name__=="__main__":
    try: main()
    except Exception as exc:
        print(json.dumps({"status":"blocked","reason":type(exc).__name__+": "+str(exc)[:250]}))
        sys.exit(1)

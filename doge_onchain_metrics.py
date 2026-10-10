#!/usr/bin/env python3
"""DOGE chain activity collector v1.1: independent, fail-closed on required series."""
import csv
import json
import os
import time
import urllib.request
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path
from io import StringIO

BASE = "https://dogechain.com/api/v3"
OUT = Path(__file__).resolve().parent / "data" / "onchain"
REQUIRED = ("active_addresses", "new_addresses", "tx_count")
OPTIONAL = ("transfers", "transfers_sent")
SERIES = REQUIRED + OPTIONAL
FIELDS = ["date_utc", *SERIES]

def api(route):
    req = urllib.request.Request(
        BASE + route,
        headers={"Accept":"application/json","User-Agent":"doge-data-collector/metrics-1.1"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        obj = json.load(resp)
    if not isinstance(obj, dict) or obj.get("status") != "success":
        raise ValueError("API response envelope invalid: " + route)
    return obj["data"]

def atomic(path, content):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)

def numeric(value):
    try:
        d = Decimal(str(value))
    except (TypeError, InvalidOperation):
        raise ValueError("non-numeric data point")
    if not d.is_finite() or d < 0:
        raise ValueError("negative or nonfinite data point")
    return str(d)

def chart(name, today):
    obj = api("/chart/" + name + "?interval=day")
    points = obj.get("points") if isinstance(obj, dict) else None
    if not isinstance(points, list):
        raise ValueError("points array missing")
    valid, invalid = {}, 0
    for item in points:
        try:
            if not isinstance(item, dict):
                raise ValueError("invalid point")
            ts = int(item["t"])
            day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
            if day >= today:
                continue
            valid[day] = numeric(item["v"])
        except (ValueError, KeyError, TypeError, OverflowError, OSError):
            invalid += 1
    if not valid:
        raise ValueError("no valid completed daily observations")
    return valid, invalid

def richlist():
    obj = api("/richlist?page=1")
    rows = obj.get("rows") if isinstance(obj, dict) else None
    if not isinstance(rows, list) or len(rows) < 10:
        raise ValueError("richlist top 10 missing")
    data = []
    for x in rows:
        bal = Decimal(str(x["balance"]))
        d24 = Decimal(str(x["delta_24h"]))
        d7 = Decimal(str(x["delta_7d"]))
        if any(not v.is_finite() for v in (bal, d24, d7)) or bal < 0:
            raise ValueError("invalid richlist values")
        data.append({"rank":int(x["rank"]), "address":x["address"],
                     "balance_doge":str(bal), "delta_24h_doge":str(d24),
                     "delta_7d_doge":str(d7), "provider_label":x.get("label")})
    data.sort(key=lambda x:x["rank"])
    return {
        "observed_addresses":len(data),
        "top10":data[:10],
        "top100_positive_balance_delta_24h_doge":str(sum(
            (Decimal(x["delta_24h_doge"]) for x in data if Decimal(x["delta_24h_doge"])>0),
            Decimal("0"))),
        "top100_negative_balance_delta_24h_doge":str(sum(
            (Decimal(x["delta_24h_doge"]) for x in data if Decimal(x["delta_24h_doge"])<0),
            Decimal("0"))),
        "note":"Current top100 address balances, not entity whale trades or exchange netflows."
    }

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    now=datetime.now(timezone.utc)
    today=now.strftime("%Y-%m-%d")
    series,errors,skipped={}, {}, {}
    for name in SERIES:
        try:
            series[name], skipped[name] = chart(name,today)
            if skipped[name]:
                errors[name+"_invalid_points"] = str(skipped[name])+" invalid observations excluded"
        except Exception as e:
            errors[name]=str(e)[:300]
    whales=None
    try:
        whales=richlist()
    except Exception as e:
        errors["richlist"]=str(e)[:300]
    if all(name in series for name in REQUIRED):
        days=sorted(set.intersection(*(set(series[x]) for x in REQUIRED)))
    else:
        days=[]
    old={}
    dest=OUT/"daily_activity.csv"
    if dest.exists():
        with dest.open(newline="",encoding="utf-8") as f:
            reader=csv.DictReader(f)
            if reader.fieldnames!=FIELDS:
                raise ValueError("CSV schema mismatch, refusing silent migration")
            for row in reader:
                day=row["date_utc"]
                if day in old:
                    raise ValueError("duplicate historical date: "+day)
                old[day]=row
    for day in days:
        old[day]={"date_utc":day, **{k:series[k].get(day,"") if k in series else "" for k in SERIES}}
    buf=StringIO()
    writer=csv.DictWriter(buf,fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(old[day] for day in sorted(old))
    atomic(dest,buf.getvalue())
    latest=days[-1] if days else None
    required_ok=bool(latest and whales and all(k in series for k in REQUIRED))
    optional_ok=all(k in series and not skipped.get(k) for k in OPTIONAL)
    result={
        "schema_version":2, "source":"Dogechain public API v3",
        "collected_at_utc":now.isoformat(),
        "status":"ok" if required_ok and optional_ok and not errors else ("partial" if required_ok else "unavailable"),
        "core_collection_success":required_ok,
        "latest_complete_core_day_utc":latest,
        "daily_observations":len(old),
        "daily_activity_latest":{k:(series[k].get(latest) if k in series else None) for k in SERIES} if latest else None,
        "richlist_balance_deltas":whales,
        "errors":errors,
        "not_available":["verified_exchange_netflow","entity_level_whale_trades","full_network_large_transfer_feed"],
        "caveats":["Addresses do not equal people","Transfers can include UTXO change",
                   "Richlist top100 composition changes over time",
                   "A successful core run does not imply optional metrics are healthy"]
    }
    atomic(OUT/"metrics_latest.json",json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"status":result["status"],"core_success":required_ok,
                      "daily_observations":len(old),"errors":errors},ensure_ascii=False))
    return 0 if required_ok else 1

if __name__=="__main__":
    raise SystemExit(main())

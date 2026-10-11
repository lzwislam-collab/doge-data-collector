#!/usr/bin/env python3
"""CoinGlass multi-exchange DOGE liquidation aggregates. Distinct from OKX public notices."""
import csv
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "data" / "coinglass_liquidations"
API = "https://open-api-v4.coinglass.com/api/futures/liquidation/aggregated-history"
EXCHANGES = "Binance,OKX,Bybit,Bitget"
INTERVAL = "4h"  # Hobbyist minimum; 1h needs Startup or higher.
FIELDS = ["bucket_start_ms","interval","exchange_list","long_usd","short_usd","total_usd","first_seen_utc","last_checked_utc"]
def utc():
    return datetime.now(timezone.utc).isoformat()
def atomic(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(value, encoding="utf-8")
    os.replace(temp,path)
def amount(v):
    try: n = Decimal(str(v))
    except (InvalidOperation,TypeError): raise ValueError("invalid liquidation amount")
    if not n.is_finite() or n < 0: raise ValueError("negative/nonfinite liquidation amount")
    return n
def run():
    ROOT.mkdir(parents=True,exist_ok=True)
    stamp = utc()
    result = {"schema_version":1, "status":"unavailable", "collected_at_utc":stamp,
              "source":"CoinGlass v4 coin aggregated liquidation history",
              "symbol":"DOGE", "exchange_list":EXCHANGES.split(","),
              "interval":INTERVAL, "unit":"USD",
              "covers_entire_global_market":False,
              "separate_from_okx_notice_feed":True,
              "errors":{}, "records":None,
              "limitations":["Multi-exchange vendor aggregate, not audited global liquidation totals",
                             "Exchange coverage depends on CoinGlass support and licensing",
                             "4h bars do not provide independent 1h observations"]}
    key=os.environ.get("COINGLASS_API_KEY","").strip()
    if not key:
        result["status"]="missing_api_key"
        result["errors"]["auth"]="COINGLASS_API_KEY repository secret not configured"
    else:
        try:
            url=API+"?"+urllib.parse.urlencode({"exchange_list":EXCHANGES,"symbol":"DOGE","interval":INTERVAL,"limit":100})
            request=urllib.request.Request(url,headers={"accept":"application/json","CG-API-KEY":key,"User-Agent":"DOGE-data-quality/1.0"})
            with urllib.request.urlopen(request,timeout=35) as response:
                payload=json.load(response)
            if str(payload.get("code"))!="0" or not isinstance(payload.get("data"),list) or not payload["data"]:
                raise ValueError("CoinGlass error or empty payload: "+str(payload.get("msg",""))[:120])
            old={}
            history=ROOT/"four_hour.csv"
            if history.exists():
                with history.open(newline="",encoding="utf-8") as f:
                    rd=csv.DictReader(f)
                    if rd.fieldnames!=FIELDS:raise ValueError("history schema mismatch")
                    for row in rd:
                        t=int(row["bucket_start_ms"])
                        if t in old:raise ValueError("duplicate stored bucket")
                        old[t]=row
            now_ms=int(datetime.now(timezone.utc).timestamp()*1000)
            bucket_ms=4*3600*1000
            new=0
            seen=set()
            for entry in payload["data"]:
                if not isinstance(entry,dict):raise ValueError("invalid record")
                ts=int(entry["time"])
                if ts in seen:raise ValueError("duplicate API bucket timestamp")
                seen.add(ts)
                if ts%bucket_ms!=0 or ts>now_ms or ts<1_600_000_000_000:raise ValueError("invalid bucket time")
                if ts+bucket_ms>now_ms:continue  # Never save unfinished candles.
                long=amount(entry["aggregated_long_liquidation_usd"])
                short=amount(entry["aggregated_short_liquidation_usd"])
                row={"bucket_start_ms":str(ts),"interval":INTERVAL,"exchange_list":EXCHANGES,
                     "long_usd":str(long),"short_usd":str(short),"total_usd":str(long+short),
                     "first_seen_utc":old[ts]["first_seen_utc"] if ts in old else stamp,
                     "last_checked_utc":stamp}
                if ts not in old:new+=1
                old[ts]=row
            if not seen or not old:raise ValueError("no complete 4h records")
            buf=io.StringIO()
            writer=csv.DictWriter(buf,fieldnames=FIELDS);writer.writeheader()
            writer.writerows(old[k] for k in sorted(old))
            atomic(history,buf.getvalue())
            result.update(status="ok",records=len(old),new_records=new,
                          latest_bucket_start_ms=max(old),duplicate_keys=0)
        except Exception as exc:
            result["status"]="error"
            result["errors"]["collector"]=type(exc).__name__+": "+str(exc)[:240]
    atomic(ROOT/"latest.json",json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"status":result["status"],"records":result["records"],"errors":result["errors"]},ensure_ascii=False))
    return 0 if result["status"]=="ok" else 1
if __name__=="__main__":sys.exit(run())

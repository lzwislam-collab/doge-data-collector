#!/usr/bin/env python3
"""Conservative policy/DOGE forward-return availability audit.

Historical event publication dates are not substituted for first_seen_utc.
BLS calendar schedule is never treated as actual macro data publication.
A horizon is scored only when an entry bar and a matured target bar exist.
"""
import csv, json, os
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"data"/"event_price_audit"
SOURCES=[
 ("FED",ROOT/"data/policy_events/events.csv"),
 ("SEC",ROOT/"data/sec_events/events.csv"),
 ("CFTC",ROOT/"data/regulatory_events/events.csv")]
HORIZONS=(1,24,168)
HOUR_MS=3600000

def parse_time(value):
 dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
 if dt.tzinfo is None:raise ValueError("timezone missing")
 return int(dt.timestamp()*1000)
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 now=datetime.now(timezone.utc).isoformat()
 audit={"schema_version":1,"run_utc":now,"status":"error",
        "price_source":"OKX DOGE-USDT closed hourly candles in data/hourly.csv",
        "event_availability_rule":"max(publication_utc, first_seen_utc); no retroactive backdating",
        "price_entry_rule":"first candle that OPENS at or after availability; use its CLOSE one hour later",
        "horizons_hours":list(HORIZONS),"events_total":0,
        "events_eligible":0,"event_horizon_results":[],
        "blocked":{},"not_proof_of_predictive_power":True}
 try:
  candles={}
  with (ROOT/"data/hourly.csv").open(newline="",encoding="utf-8") as file:
   reader=csv.DictReader(file)
   expected={"symbol","closed_hour_start_ms","hour_close"}
   if not expected.issubset(reader.fieldnames or []):raise ValueError("hourly schema missing")
   for r in reader:
    if r["symbol"]!="DOGE-USDT":continue
    t=int(r["closed_hour_start_ms"]);value=float(r["hour_close"])
    if not value>0:raise ValueError("invalid price")
    if t in candles and candles[t]!=value:raise ValueError("conflicting DOGE candle")
    candles[t]=value
  if not candles:raise ValueError("no DOGE hourly candles")
  stamps=sorted(candles)
  audit["price_first_hour_start_utc"]=datetime.fromtimestamp(stamps[0]/1000,timezone.utc).isoformat()
  audit["price_last_hour_start_utc"]=datetime.fromtimestamp(stamps[-1]/1000,timezone.utc).isoformat()
  # Candles require full close; entry is closed after availability.
  events=[]
  for name,path in SOURCES:
   if not path.exists():raise ValueError("source missing: "+name)
   with path.open(newline="",encoding="utf-8") as file:
    reader=csv.DictReader(file)
    for row in reader:
     if name=="FED":
      if row["source"]!="FED_MONETARY_RSS":continue
      publication=parse_time(row["event_time_utc"])
      first=parse_time(row["first_seen_utc"])
     else:
      publication=parse_time(row["published_utc"])
      first=parse_time(row["first_seen_utc"])
     event_id=row["event_id"]
     if not event_id:raise ValueError("empty event ID")
     events.append((name,event_id,max(publication,first),publication,first))
  audit["events_total"]=len(events)
  for name,event_id,available,published,first in events:
   audit["blocked"].setdefault("event_not_available_during_price_history",0)
   i=bisect_left(stamps,((available+HOUR_MS-1)//HOUR_MS)*HOUR_MS)
   if i>=len(stamps):
    audit["blocked"]["event_not_available_during_price_history"]+=1
    continue
   entry_ts=stamps[i]
   if entry_ts<available or entry_ts>available+2*HOUR_MS:
    audit["blocked"]["missing_entry_candle"] = audit["blocked"].get("missing_entry_candle",0)+1
    continue
   audit["events_eligible"]+=1
   for h in HORIZONS:
    end_ts=entry_ts+h*HOUR_MS
    if end_ts not in candles:
     key="missing_mature_target_"+str(h)+"h"
     audit["blocked"][key]=audit["blocked"].get(key,0)+1
     continue
    audit["event_horizon_results"].append({
      "source":name,"event_id":event_id,
      "available_utc":datetime.fromtimestamp(available/1000,timezone.utc).isoformat(),
      "entry_hour_start_utc":datetime.fromtimestamp(entry_ts/1000,timezone.utc).isoformat(),
      "horizon_hours":h,
      "return_pct":round(100*(candles[end_ts]/candles[entry_ts]-1),5),
      "interpretation":"descriptive forward return; no causal attribution"})
  audit["scored_pairs"]=len(audit["event_horizon_results"])
  audit["status"]="insufficient_forward_data" if not audit["event_horizon_results"] else "descriptive_only"
  audit["limitations"]=[
   "BLS schedules excluded from realized events",
   "No contemporaneous historical capture before collector first_seen",
   "No causal conclusions; no out-of-sample predictive advantage",
   "1h candle series must be gap-checked before statistical use"]
 except Exception as e:
  audit["error"]=type(e).__name__+": "+str(e)[:240]
 out=OUT/"latest.json";tmp=out.with_suffix(".json.tmp")
 tmp.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 os.replace(tmp,out)
 print(json.dumps({"status":audit["status"],"events":audit["events_total"],
                   "scored_pairs":audit.get("scored_pairs",0),"blocked":audit["blocked"]}))
 return 0 if audit["status"]!="error" else 1
if __name__=="__main__":raise SystemExit(main())

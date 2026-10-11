#!/usr/bin/env python3
"""Official CFTC press-release watch; keyword candidates are NOT market signals.
Publication time can predate first observation; PIT use is gated to first_seen_utc.
"""
import csv,io,json,os,urllib.request,xml.etree.ElementTree as ET
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parent/"data"/"regulatory_events"
URL="https://www.cftc.gov/RSS/RSSGP/rssgp.xml"
WORDS=("crypto","digital asset","bitcoin","virtual currency","blockchain","stablecoin","token","spot exchange-traded","dogecoin")
FIELDS=["source","event_id","title","published_utc","first_seen_utc","last_checked_utc","source_url","keyword_candidate","backtest_available_from_utc"]
def now():return datetime.now(timezone.utc).isoformat()
def write(path,data):
 tmp=path.with_name(path.name+".tmp");tmp.write_text(data,encoding="utf-8");os.replace(tmp,path)
def main():
 ROOT.mkdir(parents=True,exist_ok=True);stamp=now()
 audit={"schema_version":1,"source":"CFTC official general press release RSS","source_url":URL,
        "started_at_utc":stamp,"status":"error","errors":{},
        "scope":"keyword-matched official CFTC releases, NOT all crypto regulation",
        "point_in_time_policy":"never backdate information availability to RSS publication timestamp",
        "not_an_impact_or_price_direction_label":True}
 prior={};path=ROOT/"events.csv"
 try:
  if path.exists():
   with path.open(newline="",encoding="utf-8") as f:
    r=csv.DictReader(f)
    if r.fieldnames!=FIELDS:raise ValueError("CSV schema mismatch")
    for item in r:
     if item["event_id"] in prior:raise ValueError("duplicate stored event")
     prior[item["event_id"]]=item
  req=urllib.request.Request(URL,headers={"User-Agent":"Mozilla/5.0 (DOGE-research-policy/1.0)","Accept":"application/rss+xml,application/xml,text/xml"})
  with urllib.request.urlopen(req,timeout=35) as response:blob=response.read(2_000_000)
  root=ET.fromstring(blob)
  candidates=0;seen=set()
  for item in root.findall(".//item"):
   title=(item.findtext("title") or "").strip()
   guid=(item.findtext("guid") or item.findtext("link") or "").strip()
   link=(item.findtext("link") or "").strip()
   pub=(item.findtext("pubDate") or "").strip()
   desc=" ".join(item.itertext()).lower()
   if not title or not guid or not link or not pub:continue
   if guid in seen:raise ValueError("duplicate provider event ID")
   seen.add(guid)
   if not any(word in desc for word in WORDS):continue
   dt=parsedate_to_datetime(pub)
   if dt.tzinfo is None:raise ValueError("publication timezone missing")
   p=dt.astimezone(timezone.utc)
   if p>datetime.now(timezone.utc):raise ValueError("future-dated publication")
   candidates+=1
   first=prior[guid]["first_seen_utc"] if guid in prior else stamp
   prior[guid]={"source":"CFTC_PRESS_RSS","event_id":guid,"title":title,
                "published_utc":p.isoformat(),"first_seen_utc":first,
                "last_checked_utc":stamp,"source_url":link,"keyword_candidate":"true",
                "backtest_available_from_utc":first}
  if not seen:raise ValueError("no RSS entries")
  out=io.StringIO();w=csv.DictWriter(out,fieldnames=FIELDS);w.writeheader()
  w.writerows(prior[k] for k in sorted(prior))
  write(path,out.getvalue())
  audit.update(status="ok",feed_items=len(seen),matched_this_fetch=candidates,stored_events=len(prior),duplicate_ids=0)
 except Exception as e:
  audit["errors"]["collector"]=type(e).__name__+": "+str(e)[:200]
 audit["ended_at_utc"]=now()
 write(ROOT/"latest.json",json.dumps(audit,indent=2,ensure_ascii=False)+"\n")
 print(json.dumps({"status":audit["status"],"errors":audit["errors"]}))
 return 0 if audit["status"]=="ok" else 1
if __name__=="__main__":raise SystemExit(main())

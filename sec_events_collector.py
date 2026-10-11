#!/usr/bin/env python3
"""SEC official press release RSS keyword candidates; conservative PIT availability."""
import csv,io,json,os,urllib.request,xml.etree.ElementTree as ET
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parent/"data"/"sec_events"
URL="https://www.sec.gov/news/pressreleases.rss"
KEYWORDS=("crypto","digital asset","bitcoin","ether","blockchain","stablecoin","tokenization","dogecoin","exchange-traded fund")
FIELDS=["source","event_id","title","published_utc","first_seen_utc","last_checked_utc","source_url","keyword_candidate","backtest_available_from_utc"]
def now():return datetime.now(timezone.utc).isoformat()
def save(p,s):
 t=p.with_name(p.name+".tmp");t.write_text(s,encoding="utf-8");os.replace(t,p)
def main():
 ROOT.mkdir(parents=True,exist_ok=True);stamp=now()
 out={"schema_version":1,"source":"SEC official press releases RSS","source_url":URL,
      "started_at_utc":stamp,"status":"error","errors":{},
      "scope":"keyword-matched SEC press releases; not all regulatory actions",
      "not_sentiment_or_causal_classification":True,
      "historical_pit_rule":"first observed UTC; no retroactive information availability"}
 prior={};history=ROOT/"events.csv"
 try:
  if history.exists():
   with history.open(encoding="utf-8",newline="") as file:
    reader=csv.DictReader(file)
    if reader.fieldnames!=FIELDS:raise ValueError("CSV schema mismatch")
    for row in reader:
     if row["event_id"] in prior:raise ValueError("duplicate stored event")
     prior[row["event_id"]]=row
  req=urllib.request.Request(URL,headers={"User-Agent":"DOGE research archive contact: research@example.com","Accept":"application/rss+xml,application/xml,text/xml"})
  with urllib.request.urlopen(req,timeout=40) as response:doc=response.read(3_000_000)
  root=ET.fromstring(doc);seen=set();matched=0
  for item in root.findall(".//item"):
   title=(item.findtext("title") or "").strip()
   link=(item.findtext("link") or "").strip()
   guid=(item.findtext("guid") or link).strip()
   published=(item.findtext("pubDate") or "").strip()
   if not title or not link or not guid or not published:continue
   if guid in seen:raise ValueError("duplicate feed item")
   seen.add(guid)
   searchable=" ".join(item.itertext()).lower()
   if not any(word in searchable for word in KEYWORDS):continue
   dt=parsedate_to_datetime(published)
   if dt.tzinfo is None:raise ValueError("RSS date timezone missing")
   if dt.astimezone(timezone.utc)>datetime.now(timezone.utc):raise ValueError("future SEC publication date")
   first=prior[guid]["first_seen_utc"] if guid in prior else stamp
   prior[guid]={"source":"SEC_PRESS_RSS","event_id":guid,"title":title,
                "published_utc":dt.astimezone(timezone.utc).isoformat(),
                "first_seen_utc":first,"last_checked_utc":stamp,
                "source_url":link,"keyword_candidate":"true","backtest_available_from_utc":first}
   matched+=1
  if not seen:raise ValueError("no valid SEC RSS entries")
  buf=io.StringIO();wr=csv.DictWriter(buf,fieldnames=FIELDS);wr.writeheader()
  wr.writerows(prior[k] for k in sorted(prior))
  save(history,buf.getvalue())
  out.update(status="ok",feed_items=len(seen),matched_this_fetch=matched,stored_events=len(prior),duplicate_ids=0)
 except Exception as e:out["errors"]["collector"]=type(e).__name__+": "+str(e)[:250]
 out["ended_at_utc"]=now()
 save(ROOT/"latest.json",json.dumps(out,ensure_ascii=False,indent=2)+"\n")
 print(json.dumps({"status":out["status"],"errors":out["errors"]},ensure_ascii=False))
 return 0 if out["status"]=="ok" else 1
if __name__=="__main__":raise SystemExit(main())

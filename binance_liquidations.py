#!/usr/bin/env python3
"""Free Binance DOGEUSDT USD-M force liquidation SNAPSHOTS, not all fills."""
import csv,io,json,os,threading,time
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
import websocket
ROOT=Path(__file__).resolve().parent/"data"/"binance_liquidations"
WS="wss://fstream.binance.com/market/ws/dogeusdt@forceOrder"
SECONDS=max(30,min(int(os.environ.get("BINANCE_LISTEN_SECONDS","480")),600))
FIELDS=["event_key","event_ts_ms","order_side","position_liquidated","quantity_doge","average_price_usdt","notional_usdt","observed_at_utc"]
def utc():return datetime.now(timezone.utc).isoformat()
def amount(x):
 d=Decimal(str(x))
 if not d.is_finite() or d<=0:raise ValueError("invalid positive value")
 return d
def put(path,s):
 temp=path.with_name(path.name+".tmp");temp.write_text(s,encoding="utf-8");os.replace(temp,path)
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 old={};p=ROOT/"events.csv"
 if p.exists():
  with p.open(newline="",encoding="utf-8") as f:
   rd=csv.DictReader(f)
   if rd.fieldnames!=FIELDS:raise ValueError("CSV schema mismatch")
   for row in rd:
    if row["event_key"] in old:raise ValueError("duplicate old key")
    old[row["event_key"]]=row
 start=time.monotonic()
 result={"schema_version":1,"source":"Binance official DOGEUSDT forceOrder 1000ms snapshots",
         "started_at_utc":utc(),"status":"error","errors":[],"messages":0,"new_events":0,
         "duplicate_events":0,"requested_seconds":SECONDS,"scheduled_interval_seconds":900,
         "planned_time_coverage_ceiling_pct":round(100*SECONDS/900,2),
         "all_fills_available":False,"all_exchanges_covered":False,
         "data_is_complete_24h":False}
 def on_message(ws,raw):
  try:
   msg=json.loads(raw)
   if msg.get("e")!="forceOrder":return
   o=msg["o"]
   if o.get("s")!="DOGEUSDT":return
   result["messages"]+=1
   t=int(o["T"]);side=o["S"]
   if side not in ("BUY","SELL"):raise ValueError("invalid order side")
   q=amount(o["z"]) if Decimal(str(o.get("z","0")))>0 else amount(o["q"])
   px=amount(o["ap"]) if Decimal(str(o.get("ap","0")))>0 else amount(o["p"])
   key="|".join([str(t),side,str(q),str(px)])
   if key in old:
    result["duplicate_events"]+=1;return
   old[key]={"event_key":key,"event_ts_ms":str(t),"order_side":side,
             "position_liquidated":"long" if side=="SELL" else "short",
             "quantity_doge":str(q),"average_price_usdt":str(px),
             "notional_usdt":str(q*px),"observed_at_utc":utc()}
   result["new_events"]+=1
  except Exception as e:
   result["errors"].append(type(e).__name__+": "+str(e)[:150]);ws.close()
 def on_error(ws,e):result["errors"].append("connection: "+str(e)[:150])
 opened=[False]
 def on_open(ws):opened[0]=True
 app=websocket.WebSocketApp(WS,on_open=on_open,on_message=on_message,on_error=on_error)
 timer=threading.Timer(SECONDS,app.close);timer.daemon=True;timer.start()
 try:app.run_forever(ping_interval=25,ping_timeout=10)
 except Exception as e:result["errors"].append(type(e).__name__+": "+str(e)[:150])
 finally:timer.cancel()
 result["ended_at_utc"]=utc()
 result["elapsed_seconds"]=round(time.monotonic()-start,2)
 result["socket_opened"]=opened[0]
 result["stored_unique_snapshots"]=len(old)
 result["status"]="partial" if opened[0] and not result["errors"] and result["elapsed_seconds"]>=SECONDS*0.9 else "error"
 if result["status"]=="error" and not result["errors"]:
  result["errors"].append("socket closed before minimum listening duration")
 out=io.StringIO();w=csv.DictWriter(out,fieldnames=FIELDS);w.writeheader()
 w.writerows(old[k] for k in sorted(old,key=lambda k:(int(old[k]["event_ts_ms"]),k)))
 put(p,out.getvalue());put(ROOT/"latest.json",json.dumps(result,indent=2,ensure_ascii=False)+"\n")
 print(json.dumps({"status":result["status"],"messages":result["messages"],"errors":result["errors"]}))
 return 0 if result["status"]=="partial" else 1
if __name__=="__main__":raise SystemExit(main())

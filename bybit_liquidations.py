#!/usr/bin/env python3
"""Sample public Bybit DOGEUSDT liquidation stream without API key.
Only listening-window observations; do not interpret as global liquidation totals.
"""
import csv
import io
import json
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import websocket

OUT = Path(__file__).resolve().parent / "data" / "bybit_liquidations"
WS = "wss://stream.bybit.com/v5/public/linear"
DURATION = max(30, min(int(os.getenv("BYBIT_LISTEN_SECONDS", "480")), 600))
FIELDS = ["event_key","event_ts_ms","position_side","quantity_doge","bankruptcy_price_usdt","notional_usdt","observed_at_utc"]
def utc():
    return datetime.now(timezone.utc).isoformat()
def positive(v):
    n = Decimal(str(v))
    if not n.is_finite() or n <= 0: raise ValueError("invalid quantity or price")
    return n
def atomic(path,text):
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(text,encoding="utf-8")
    os.replace(tmp,path)
def run():
    OUT.mkdir(parents=True,exist_ok=True)
    old={}
    history=OUT/"events.csv"
    if history.exists():
        with history.open(newline="",encoding="utf-8") as file:
            reader=csv.DictReader(file)
            if reader.fieldnames!=FIELDS:raise ValueError("stored CSV schema differs")
            for row in reader:
                if row["event_key"] in old:raise ValueError("duplicate historical event")
                old[row["event_key"]]=row
    started=utc()
    begin=time.monotonic()
    status={"schema_version":1,"source":"Bybit official public allLiquidation.DOGEUSDT",
            "started_at_utc":started,"ended_at_utc":None,"status":"error",
            "interval_seconds":900,"requested_listen_seconds":DURATION,
            "planned_coverage_ceiling_pct":round(100*DURATION/900,2),
            "global_market_total":False,"data_is_complete_24h":False,
            "messages":0,"new_events":0,"duplicates":0,"errors":[],
            "subscription_ack":False,"subscription_seconds":0}
    subscribed=[None]
    online=[0.]
    def on_open(ws):
        ws.send(json.dumps({"op":"subscribe","args":["allLiquidation.DOGEUSDT"]}))
    def on_message(ws,raw):
        try:
            obj=json.loads(raw)
            if obj.get("op")=="subscribe":
                if obj.get("success") is True:
                    status["subscription_ack"]=True
                    subscribed[0]=time.monotonic()
                else:raise ValueError("Bybit subscription declined")
                return
            if obj.get("topic")!="allLiquidation.DOGEUSDT":return
            data=obj.get("data")
            if not isinstance(data,list):raise ValueError("unexpected Bybit payload")
            status["messages"]+=1
            for x in data:
                if x.get("s")!="DOGEUSDT":continue
                ts=int(x["T"])
                side=x["S"]
                if side not in ("Buy","Sell"):raise ValueError("invalid position side")
                size=positive(x["v"])
                price=positive(x["p"])
                # Stream has no globally unique liquidation ID; key collisions are possible.
                key="|".join([str(ts),side,str(size),str(price)])
                if key in old:
                    status["duplicates"]+=1
                    continue
                old[key]={"event_key":key,"event_ts_ms":str(ts),
                          "position_side":"long" if side=="Buy" else "short",
                          "quantity_doge":str(size),"bankruptcy_price_usdt":str(price),
                          "notional_usdt":str(size*price),"observed_at_utc":utc()}
                status["new_events"]+=1
        except Exception as error:
            status["errors"].append(type(error).__name__+": "+str(error)[:150])
            ws.close()
    def on_error(ws,error):
        status["errors"].append("connection: "+str(error)[:150])
    def on_close(ws,code,reason):
        if subscribed[0] is not None:
            online[0]+=max(0,time.monotonic()-subscribed[0])
            subscribed[0]=None
    app=websocket.WebSocketApp(WS,on_open=on_open,on_message=on_message,on_error=on_error,on_close=on_close)
    timer=threading.Timer(DURATION,app.close)
    timer.daemon=True
    timer.start()
    try:
        app.run_forever(ping_interval=25,ping_timeout=10)
    except Exception as exc:
        status["errors"].append(type(exc).__name__+": "+str(exc)[:150])
    finally:
        timer.cancel()
        if subscribed[0] is not None:
            online[0]+=max(0,time.monotonic()-subscribed[0])
    elapsed=time.monotonic()-begin
    status["ended_at_utc"]=utc()
    status["subscription_seconds"]=round(min(online[0],elapsed),2)
    status["in_window_subscription_pct"]=round(100*min(online[0],elapsed)/elapsed,2) if elapsed else 0
    status["stored_unique_events"]=len(old)
    status["status"]="partial" if (status["subscription_ack"] and not status["errors"] and elapsed>=DURATION*0.9 and online[0]>=DURATION*0.85) else "error"
    if status["status"]=="error" and not status["errors"]:
        status["errors"].append("insufficient listening duration or subscription coverage")
    out=io.StringIO()
    writer=csv.DictWriter(out,fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(old[key] for key in sorted(old,key=lambda k:(int(old[k]["event_ts_ms"]),k)))
    atomic(history,out.getvalue())
    atomic(OUT/"latest.json",json.dumps(status,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps({"status":status["status"],"events":status["new_events"],"errors":status["errors"]},ensure_ascii=False))
    return 0 if status["status"]=="partial" else 1
if __name__=="__main__":
    raise SystemExit(run())

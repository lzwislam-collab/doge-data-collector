#!/usr/bin/env python3
"""Observed OKX DOGE liquidation events (partial coverage), not market total."""
import csv, json, os, time, threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import websocket

BASE=Path(__file__).resolve().parent/'data'/'liquidations'
WS='wss://ws.okx.com:8443/ws/v5/public'
FIELDS=['event_key','event_ts_ms','side','pos_side','contracts','bankruptcy_price','notional_usdt','received_at_utc']
DURATION=max(30,min(int(os.environ.get('LIQ_LISTEN_SECONDS','480')),600))

def now(): return datetime.now(timezone.utc).isoformat()
def atomic(path,content):
    t=path.with_suffix(path.suffix+'.tmp');t.write_text(content,encoding='utf-8');os.replace(t,path)
def dec(v):
    x=Decimal(str(v))
    if not x.is_finite() or x<0:raise ValueError('invalid quantity/price')
    return x

def main():
    BASE.mkdir(parents=True,exist_ok=True)
    started=now(); start=time.monotonic(); rows={}; old=BASE/'events.csv'
    if old.exists():
        with old.open(newline='',encoding='utf-8') as f:
            reader=csv.DictReader(f)
            if reader.fieldnames!=FIELDS: raise ValueError('CSV schema mismatch')
            for row in reader:
                key=row['event_key']
                if key in rows:raise ValueError('existing duplicate event key')
                rows[key]=row
    state={'subscribed':False,'messages':0,'observed_doge_messages':0,'errors':[],'disconnects':0,'reconnects':0,'new_events':0,'duplicate_events':0}
    deadline=start+DURATION
    lock=threading.Lock()
    def on_open(ws):
        ws.send(json.dumps({'op':'subscribe','args':[{'channel':'liquidation-orders','instType':'SWAP'}]}))
    def on_message(ws,raw):
        try:
            msg=json.loads(raw)
            if not isinstance(msg,dict): return
            if msg.get('event')=='subscribe' and msg.get('arg',{}).get('channel')=='liquidation-orders':
                state['subscribed']=True;return
            if msg.get('event')=='error':
                state['errors'].append('WS subscribe error: '+str(msg.get('code'))+' '+str(msg.get('msg'))[:150]);ws.close();return
            if msg.get('arg',{}).get('channel')!='liquidation-orders':return
            state['messages']+=1
            for item in msg.get('data',[]):
                if item.get('instId')!='DOGE-USDT-SWAP':continue
                state['observed_doge_messages']+=1
                for d in item.get('details',[]):
                    # OKX: sz is contracts; DOGE-USDT-SWAP 1 contract = 1000 DOGE (validated elsewhere)
                    size=dec(d['sz']);price=dec(d['bkPx']);ts=int(d['ts'])
                    if size<=0 or price<=0 or ts<=0:raise ValueError('bad liquidation event')
                    side=str(d.get('side',''));pos_side=str(d.get('posSide',''))
                    if side not in ('buy','sell'): raise ValueError('unknown event side')
                    if pos_side not in ('long','short','net',''): raise ValueError('unknown posSide')
                    key='|'.join([str(ts),side,pos_side,str(size),str(price)])
                    with lock:
                        if key in rows:state['duplicate_events']+=1;continue
                        rows[key]={'event_key':key,'event_ts_ms':str(ts),'side':side,'pos_side':pos_side,'contracts':str(size),
                                   'bankruptcy_price':str(price),'notional_usdt':str(size*Decimal(1000)*price),'received_at_utc':now()}
                        state['new_events']+=1
        except Exception as e:
            state['errors'].append(type(e).__name__+': '+str(e)[:200]);ws.close()
    def on_error(ws,error):state['errors'].append('connection: '+str(error)[:200])
    def on_close(ws,code,message): state['disconnects']+=1
    while time.monotonic()<deadline and not state['errors']:
        app=websocket.WebSocketApp(WS,on_open=on_open,on_message=on_message,on_error=on_error,on_close=on_close)
        remaining=deadline-time.monotonic()
        timer=threading.Timer(max(1,remaining),app.close);timer.daemon=True;timer.start()
        try: app.run_forever(ping_interval=25,ping_timeout=10)
        finally: timer.cancel()
        if time.monotonic()<deadline and not state['errors']:
            state['reconnects']+=1;time.sleep(min(3,deadline-time.monotonic()))
    import io
    with lock:
        output=io.StringIO();writer=csv.DictWriter(output,fieldnames=FIELDS);writer.writeheader()
        writer.writerows(sorted(rows.values(),key=lambda r:(int(r['event_ts_ms']),r['event_key'])))
        atomic(old,output.getvalue())
    status='ok' if state['subscribed'] and not state['errors'] else 'error'
    summary={'schema_version':1,'status':status,'source':'OKX public WS liquidation-orders; DOGE-USDT-SWAP',
             'started_at_utc':started,'ended_at_utc':now(),'listen_seconds':round(time.monotonic()-start,2),
             'coverage':'Only connected listening windows, not full-day or market-wide liquidations',
             'notional_definition':'sz (contracts) * 1000 DOGE/contract * bkPx; bankruptcy-price notional, not executed proceeds',
             'historical_backfill':False,'event_rows_total':len(rows),'data_quality':state}
    atomic(BASE/'latest.json',json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':status,**state},ensure_ascii=False),flush=True)
    return 0 if status=='ok' else 1
if __name__=='__main__':raise SystemExit(main())

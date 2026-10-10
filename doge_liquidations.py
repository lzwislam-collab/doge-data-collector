#!/usr/bin/env python3
"""Observe OKX public liquidation notices; audited connection coverage, NOT total liquidations."""
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

BASE = Path(__file__).resolve().parent / 'data' / 'liquidations'
WS = 'wss://ws.okx.com:8443/ws/v5/public'
FIELDS = ['event_key','event_ts_ms','side','pos_side','contracts','bankruptcy_price','notional_usdt','received_at_utc']
DURATION = max(30, min(int(os.getenv('LIQ_LISTEN_SECONDS', '480')), 600))
CONTRACT_DOGE = Decimal('1000')  # Verify contract specification before changing instrument.

def utc():
    return datetime.now(timezone.utc).isoformat()

def atomic(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(value, encoding='utf-8')
    os.replace(temp, path)

def positive(x):
    d = Decimal(str(x))
    if not d.is_finite() or d <= 0:
        raise ValueError('nonpositive or invalid event quantity/price')
    return d

def load_events(path):
    rows = {}
    if path.exists():
        with path.open(newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != FIELDS:
                raise ValueError('previous events CSV schema mismatch')
            for row in reader:
                key = row['event_key']
                if not key or key in rows:
                    raise ValueError('invalid/duplicate stored event key')
                rows[key] = row
    return rows

def main():
    BASE.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    deadline = start + DURATION
    started = utc()
    rows = load_events(BASE / 'events.csv')
    lock = threading.Lock()
    state = {'subscribed': False, 'subscription_ack_count': 0, 'messages': 0,
             'observed_doge_messages': 0, 'errors': [], 'disconnects': 0,
             'reconnects': 0, 'new_events': 0, 'duplicate_events': 0,
             'connections': [], 'parse_errors': 0}
    online_total = 0.0
    attempts = 0

    while time.monotonic() < deadline and not state['errors']:
        attempts += 1
        session = {'connected_at_utc': None, 'subscribed_at_utc': None,
                   'disconnected_at_utc': None, 'close_code': None,
                   'close_reason': '', 'subscribed_seconds': 0.0}
        state['connections'].append(session)
        active_since = [None]
        closed_by_deadline = [False]
        session_errors = []

        def on_open(ws):
            session['connected_at_utc'] = utc()
            ws.send(json.dumps({'op': 'subscribe', 'args': [{'channel': 'liquidation-orders', 'instType': 'SWAP'}]}))

        def on_message(ws, raw):
            try:
                if raw == 'pong':
                    return
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('nonobject WS payload')
                if message.get('event') == 'subscribe' and message.get('arg', {}).get('channel') == 'liquidation-orders':
                    session['subscribed_at_utc'] = utc()
                    active_since[0] = time.monotonic()
                    state['subscribed'] = True
                    state['subscription_ack_count'] += 1
                    return
                if message.get('event') == 'error':
                    session_errors.append('subscription: ' + str(message.get('code')) + ' ' + str(message.get('msg'))[:120])
                    ws.close()
                    return
                if message.get('arg', {}).get('channel') != 'liquidation-orders':
                    return
                state['messages'] += 1
                data = message.get('data')
                if not isinstance(data, list):
                    raise ValueError('liquidation payload data not list')
                for item in data:
                    if item.get('instId') != 'DOGE-USDT-SWAP':
                        continue
                    state['observed_doge_messages'] += 1
                    details = item.get('details')
                    if not isinstance(details, list):
                        raise ValueError('DOGE liquidation details not list')
                    for d in details:
                        sz = positive(d['sz'])
                        px = positive(d['bkPx'])
                        ts = int(d['ts'])
                        if ts <= 0:
                            raise ValueError('invalid event time')
                        side, pos_side = str(d.get('side', '')), str(d.get('posSide', ''))
                        if side not in ('buy', 'sell') or pos_side not in ('long', 'short', 'net', ''):
                            raise ValueError('unexpected liquidation side')
                        key = '|'.join([str(ts), side, pos_side, str(sz), str(px)])
                        with lock:
                            if key in rows:
                                state['duplicate_events'] += 1
                                continue
                            rows[key] = {'event_key': key, 'event_ts_ms': str(ts), 'side': side,
                                         'pos_side': pos_side, 'contracts': str(sz),
                                         'bankruptcy_price': str(px),
                                         'notional_usdt': str(sz * CONTRACT_DOGE * px),
                                         'received_at_utc': utc()}
                            state['new_events'] += 1
            except Exception as e:
                state['parse_errors'] += 1
                session_errors.append(type(e).__name__ + ': ' + str(e)[:180])
                ws.close()

        def on_error(ws, err):
            session_errors.append('connection: ' + str(err)[:180])

        def on_close(ws, code, reason):
            session['disconnected_at_utc'] = utc()
            session['close_code'] = code
            session['close_reason'] = str(reason or '')[:150]
            state['disconnects'] += 1

        app = websocket.WebSocketApp(WS, on_open=on_open, on_message=on_message,
                                     on_error=on_error, on_close=on_close)
        remaining = max(0.1, deadline - time.monotonic())
        def end_session():
            closed_by_deadline[0] = True
            app.close()
        timer = threading.Timer(remaining, end_session)
        timer.daemon = True
        timer.start()
        try:
            app.run_forever(ping_interval=25, ping_timeout=10)
        except Exception as e:
            session_errors.append('run_forever: ' + type(e).__name__ + ': ' + str(e)[:150])
        finally:
            timer.cancel()
            end = min(time.monotonic(), deadline)
            if active_since[0] is not None:
                duration = max(0., end - active_since[0])
                session['subscribed_seconds'] = round(duration, 3)
                online_total += duration
            if session['disconnected_at_utc'] is None:
                session['disconnected_at_utc'] = utc()
        if session_errors:
            state['errors'].extend(session_errors)
            break
        if time.monotonic() < deadline:
            state['reconnects'] += 1
            time.sleep(min(3., max(0., deadline - time.monotonic())))

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(sorted(rows.values(), key=lambda r: (int(r['event_ts_ms']), r['event_key'])))
    atomic(BASE / 'events.csv', output.getvalue())

    elapsed = time.monotonic() - start
    coverage_pct = round(100 * min(online_total, elapsed) / elapsed, 2) if elapsed else 0.
    # A clean run can still be partial. An OKX notice feed is not exhaustive by design.
    status = ('error' if state['errors'] or not state['subscribed']
              else 'ok' if coverage_pct >= 99.0 else 'partial')
    summary = {'schema_version': 2, 'status': status, 'source': 'OKX public WS liquidation-orders',
               'instrument': 'DOGE-USDT-SWAP', 'started_at_utc': started, 'ended_at_utc': utc(),
               'listen_seconds': round(elapsed, 2), 'subscribed_seconds': round(online_total, 2),
               'subscription_coverage_pct': coverage_pct, 'connections_attempted': attempts,
               'coverage': 'Partial public notification feed; only subscribed windows; NOT market-wide totals',
               'notional_definition': 'sz * 1000 DOGE/contract * bkPx, bankruptcy-price notional',
               'historical_backfill': False, 'event_rows_total': len(rows),
               'event_key_caveat': 'Potential collision when timestamp, direction, size and price match; not globally unique',
               'data_quality': state}
    atomic(BASE / 'latest.json', json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': status, 'coverage_pct': coverage_pct,
                      'events': state['new_events'], 'errors': state['errors']}, ensure_ascii=False), flush=True)
    return 1 if status == 'error' else 0

if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Observe OKX public notices with proved windows; never whole-market liquidation totals."""
import csv
import hashlib
import io
import json
import os
import time
import urllib.request
import urllib.parse
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parent / 'data' / 'liquidations'
WS = 'wss://ws.okx.com:443/ws/v5/public'
FIELDS = ['event_key','event_ts_ms','side','pos_side','contracts','bankruptcy_price','notional_usdt','received_at_utc']
DURATION = max(30, min(int(os.getenv('LIQ_LISTEN_SECONDS', '480')), 600))
REQUIRED_WINDOW_SECONDS = 480
PING_INTERVAL_SECONDS = 20
PONG_TIMEOUT_SECONDS = 10
SUBSCRIPTION_TIMEOUT_SECONDS = 20


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


def contract_doge(raw_dir=None):
    """Keep the source bytes before interpreting the contract denomination."""
    url = 'https://www.okx.com/api/v5/public/instruments?' + urllib.parse.urlencode(
        {'instType': 'SWAP', 'instId': 'DOGE-USDT-SWAP'})
    requested = utc()
    req = urllib.request.Request(url, headers={'Accept':'application/json','User-Agent':'DOGE-liquidation-audit/1.2'})
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read()
        proof = {'source_url':url, 'requested_at_utc':requested, 'received_at_utc':utc(),
                 'http_status':getattr(response, 'status', 200), 'headers':dict(response.headers),
                 'body_utf8':raw.decode('utf-8'), 'raw_sha256':hashlib.sha256(raw).hexdigest()}
    proof['first_seen_utc'] = proof['received_at_utc']
    if raw_dir is not None:
        raw_dir = Path(raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        with (raw_dir / ('instruments-' + uuid.uuid4().hex + '.json')).open('x', encoding='utf-8') as stream:
            json.dump(proof, stream, ensure_ascii=False, indent=2)
    payload = json.loads(proof['body_utf8'])
    if payload.get('code') != '0' or not isinstance(payload.get('data'), list):
        raise ValueError('OKX instrument metadata unavailable')
    inst = next((x for x in payload['data'] if x.get('instId') == 'DOGE-USDT-SWAP'), None)
    if not inst or inst.get('ctType') != 'linear' or inst.get('ctValCcy') != 'DOGE' or inst.get('state') != 'live':
        raise ValueError('DOGE swap denomination/state unexpected')
    return positive(inst['ctVal'])


def load_events(path):
    rows = {}
    if path.exists():
        with path.open(newline='', encoding='utf-8') as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != FIELDS:
                raise ValueError('previous events CSV schema mismatch')
            for row in reader:
                key = row['event_key']
                if not key or key in rows:
                    raise ValueError('invalid/duplicate stored event key')
                rows[key] = row
    return rows


class PublicSocket:
    """The optional client is loaded only for a real public connection."""
    def __init__(self, endpoint):
        import websocket
        self.timeout_error = websocket.WebSocketTimeoutException
        self.client = websocket.create_connection(endpoint, timeout=20)
        self.client.settimeout(1)
    def send(self, raw):
        self.client.send(raw)
    def recv(self):
        try:
            return self.client.recv()
        except self.timeout_error as error:
            raise TimeoutError('public socket receive timeout') from error
    def close(self):
        self.client.close()


def observe_window(rows, contract_multiplier, record, duration=DURATION, connect=None,
                   monotonic=None, now=None):
    """A complete empty DOGE window is valid evidence of observation, never global zero.

    Handshake time is measured but does not consume the requested subscribed
    window. Any unknown disconnection, unacknowledged heartbeat or parse error
    fails the whole window; reconnects cannot silently erase a coverage gap.
    """
    connect = connect or PublicSocket
    monotonic = monotonic or time.monotonic
    now = now or utc
    if not isinstance(duration, (int, float)) or not 0 < duration <= 600:
        raise ValueError('invalid observation duration')
    start = monotonic()
    started = now()
    active = None
    deadline = None
    pending_ping = None
    next_ping = None
    last_verified = None
    sock = None
    session = {'connected_at_utc':None, 'subscribed_at_utc':None, 'disconnected_at_utc':None,
               'close_code':None, 'close_reason':'', 'subscribed_seconds':0.}
    state = {'subscribed':False, 'subscription_ack_count':0, 'messages':0,
             'observed_doge_messages':0, 'observed_doge_events':0, 'errors':[],
             'disconnects':0, 'unexpected_disconnects':0, 'reconnects':0,
             'new_events':0, 'duplicate_events':0, 'connections':[session], 'parse_errors':0,
             'heartbeat_pings':0, 'heartbeat_pongs':0, 'late_events':[],
             'first_reception_is_availability':True}
    try:
        record({'kind':'connection_attempt', 'at_utc':started, 'endpoint':WS})
        sock = connect(WS)
        session['connected_at_utc'] = now()
        request = json.dumps({'op':'subscribe', 'args':[{'channel':'liquidation-orders','instType':'SWAP'}]})
        sock.send(request)
        record({'kind':'outbound', 'sent_at_utc':now(), 'raw_utf8':request})
        while True:
            tick = monotonic()
            if active is None and tick - start >= SUBSCRIPTION_TIMEOUT_SECONDS:
                raise ValueError('subscription acknowledgement timeout')
            if pending_ping is not None and tick - pending_ping >= PONG_TIMEOUT_SECONDS:
                raise ValueError('heartbeat pong timeout; silent connection is not coverage')
            if deadline is not None and tick >= deadline and pending_ping is None:
                break
            if active is not None and pending_ping is None and tick >= next_ping:
                sock.send('ping')
                pending_ping = monotonic()
                state['heartbeat_pings'] += 1
                record({'kind':'outbound', 'sent_at_utc':now(), 'raw_utf8':'ping'})
            try:
                raw = sock.recv()
            except TimeoutError:
                continue
            received = now()
            record({'kind':'inbound', 'received_at_utc':received, 'raw_utf8':raw})
            if active is None and monotonic() - start >= SUBSCRIPTION_TIMEOUT_SECONDS:
                raise ValueError('subscription acknowledgement arrived after timeout')
            if raw in ('', None):
                state['unexpected_disconnects'] += 1
                raise ValueError('unexpected disconnect before observation window ended')
            if raw == 'pong':
                if pending_ping is not None:
                    if monotonic() - pending_ping >= PONG_TIMEOUT_SECONDS:
                        raise ValueError('heartbeat pong arrived after timeout')
                    state['heartbeat_pongs'] += 1
                    last_verified = monotonic()
                    pending_ping = None
                    next_ping = last_verified + PING_INTERVAL_SECONDS
                continue
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('nonobject WS payload')
                arg = message.get('arg') or {}
                if message.get('event') == 'error':
                    raise ValueError('subscription error: ' + str(message.get('code')) + ' ' + str(message.get('msg'))[:120])
                if message.get('event') == 'subscribe':
                    if arg.get('channel') != 'liquidation-orders' or arg.get('instType') != 'SWAP':
                        raise ValueError('unexpected subscription acknowledgement')
                    state['subscription_ack_count'] += 1
                    if active is None:
                        active = monotonic()
                        deadline = active + duration
                        next_ping = active + PING_INTERVAL_SECONDS
                        session['subscribed_at_utc'] = received
                        state['subscribed'] = True
                    continue
                if message.get('event') == 'notice':
                    continue
                if arg.get('channel') != 'liquidation-orders':
                    continue
                if active is None:
                    raise ValueError('channel data before subscription acknowledgement')
                state['messages'] += 1
                data = message.get('data')
                if not isinstance(data, list):
                    raise ValueError('liquidation payload data not list')
                for item in data:
                    if not isinstance(item, dict):
                        raise ValueError('nonobject liquidation item')
                    if item.get('instId') != 'DOGE-USDT-SWAP':
                        continue
                    if item.get('instType', 'SWAP') != 'SWAP':
                        raise ValueError('unexpected DOGE instrument type')
                    state['observed_doge_messages'] += 1
                    details = item.get('details')
                    if not isinstance(details, list):
                        raise ValueError('DOGE liquidation details not list')
                    for detail in details:
                        sz, px = positive(detail['sz']), positive(detail['bkPx'])
                        raw_ts = str(detail['ts'])
                        if not raw_ts.isdigit():
                            raise ValueError('noninteger event timestamp')
                        ts = int(raw_ts)
                        stamp = datetime.fromtimestamp(ts/1000, timezone.utc)
                        receipt = datetime.fromisoformat(received.replace('Z','+00:00'))
                        if ts < 1262304000000 or (stamp-receipt).total_seconds() > 5:
                            raise ValueError('invalid/future event timestamp')
                        side, pos_side = str(detail.get('side','')), str(detail.get('posSide',''))
                        if side not in ('buy','sell') or pos_side not in ('long','short','net',''):
                            raise ValueError('unexpected liquidation side')
                        key = '|'.join([str(ts), side, pos_side, str(sz), str(px)])
                        state['observed_doge_events'] += 1
                        if key in rows:
                            state['duplicate_events'] += 1
                            continue
                        rows[key] = {'event_key':key, 'event_ts_ms':str(ts), 'side':side,
                                     'pos_side':pos_side, 'contracts':str(sz), 'bankruptcy_price':str(px),
                                     'notional_usdt':str(sz * contract_multiplier * px), 'received_at_utc':received}
                        state['new_events'] += 1
                        delay = (receipt-stamp).total_seconds()
                        if delay > 600:
                            state['late_events'].append({'event_key':key, 'event_ts_ms':ts,
                                'received_at_utc':received, 'delay_seconds':round(delay,3),
                                'usable_from_utc':received, 'historical_event_time_feature_allowed':False,
                                'disposition':'late_notification_quarantine_for_event_time_features'})
            except (ValueError, TypeError, KeyError, ArithmeticError, OverflowError) as error:
                state['parse_errors'] += 1
                raise ValueError('packet validation: ' + str(error)) from error
    except Exception as error:
        state['errors'].append(type(error).__name__ + ': ' + str(error)[:250])
    finally:
        ended_tick = monotonic()
        ended = now()
        if sock is not None:
            try:
                sock.close()
            except Exception as error:
                state['errors'].append('close: ' + str(error)[:120])
        session['disconnected_at_utc'] = ended
        state['disconnects'] += int(sock is not None)
    state['heartbeat_pending_at_end'] = pending_ping is not None
    elapsed = ended_tick-start
    subscribed = max(0., ended_tick-active) if active is not None else 0.
    session['subscribed_seconds'] = round(subscribed,3)
    complete = bool(active is not None and subscribed >= duration and duration >= REQUIRED_WINDOW_SECONDS
                    and pending_ping is None and state['heartbeat_pongs'] > 0 and not state['errors'])
    if not complete and not state['errors']:
        state['errors'].append('incomplete original 480-second subscribed observation window')
    coverage = round(100 * min(subscribed, elapsed)/elapsed,2) if elapsed else 0.
    last_event = max((int(row['event_ts_ms']) for row in rows.values()), default=None)
    last_receipt = max((row['received_at_utc'] for row in rows.values()), default=None)
    status = 'error' if not complete else 'ok' if coverage >= 99. else 'partial'
    summary = {'schema_version':4, 'status':status, 'source':'OKX public WS liquidation-orders',
        'instrument':'DOGE-USDT-SWAP', 'endpoint':WS, 'started_at_utc':started, 'ended_at_utc':ended,
        'listen_seconds':round(elapsed,3), 'requested_listen_seconds':duration,
        'required_listen_seconds':REQUIRED_WINDOW_SECONDS, 'subscribed_seconds':round(subscribed,3),
        'subscription_coverage_pct':coverage, 'window_complete':complete,
        'window_started_at_utc':session['subscribed_at_utc'], 'window_ended_at_utc':ended,
        'heartbeat_verified_seconds':round(subscribed if complete else max(0., last_verified-active) if last_verified is not None and active is not None else 0.,3),
        'heartbeat_interval_seconds':PING_INTERVAL_SECONDS, 'heartbeat_timeout_seconds':PONG_TIMEOUT_SECONDS,
        'connections_attempted':1, 'observed_doge_events_in_window':state['observed_doge_events'],
        'zero_observation_is_market_total':False, 'missing_window_equals_zero':False,
        'last_doge_event_utc':datetime.fromtimestamp(last_event/1000,timezone.utc).isoformat() if last_event else None,
        'last_doge_received_at_utc':last_receipt, 'last_doge_values_are_current_window_totals':False,
        'coverage':'Partial public notification feed; only subscribed windows; NOT market-wide totals',
        'notional_definition':'sz * contract_doge_per_contract * bkPx; liquidation-account price estimate, NOT market-wide',
        'historical_backfill':False, 'event_rows_total':len(rows), 'contract_doge_per_contract':str(contract_multiplier),
        'planned_coverage_ceiling_pct':round(100 * duration/900,2), 'scheduled_interval_minutes':15,
        'coverage_is_per_listening_window_only':True, 'not_market_wide':True,
        'event_key_caveat':'Potential collision when timestamp, direction, size and price match; not globally unique',
        'data_quality':state}
    record({'kind':'window_result', 'at_utc':ended, 'status':status, 'window_complete':complete,
            'subscribed_seconds':summary['subscribed_seconds'], 'errors':state['errors']})
    return summary


def include_raw_evidence(summary, run_dir, repo_root):
    """Attach every retained source packet even after collection/parse failure."""
    run_dir, repo_root = Path(run_dir), Path(repo_root)
    summary['raw_evidence_directory'] = run_dir.relative_to(repo_root).as_posix()
    summary['raw_evidence'] = [{'path':path.relative_to(repo_root).as_posix(),
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(run_dir.iterdir()) if path.is_file()]
    return summary


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    run_dir = BASE / 'raw' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex)
    run_dir.mkdir(parents=True)
    started = utc()
    try:
        rows = load_events(BASE/'events.csv')
        multiplier = contract_doge(run_dir)
        journal = run_dir/'websocket.jsonl'
        with journal.open('x', encoding='utf-8', newline='\n') as stream:
            def record(item):
                stream.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + '\n')
                stream.flush()
            summary = observe_window(rows, multiplier, record)
            os.fsync(stream.fileno())
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sorted(rows.values(), key=lambda row:(int(row['event_ts_ms']),row['event_key'])))
        atomic(BASE/'events.csv', output.getvalue())
        summary['raw_legacy_packet_verified'] = False
    except Exception as error:
        summary = {'schema_version':4, 'status':'error', 'source':'OKX public WS liquidation-orders',
            'instrument':'DOGE-USDT-SWAP','started_at_utc':started,'ended_at_utc':utc(),
            'window_complete':False, 'not_market_wide':True,
            'coverage':'Partial public notification feed; only subscribed windows; NOT market-wide totals',
            'errors':{'collector':type(error).__name__+': '+str(error)[:300]},
            'raw_evidence_directory':str(run_dir.relative_to(BASE.parent.parent)).replace('\\','/')}
    include_raw_evidence(summary, run_dir, BASE.parent.parent)
    atomic(BASE/'latest.json', json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
    print(json.dumps({'status':summary['status'], 'window_complete':summary['window_complete'],
                      'errors':summary.get('errors',summary.get('data_quality',{}).get('errors',[]))}), flush=True)
    return int(summary['status']=='error')


if __name__ == '__main__':
    raise SystemExit(main())

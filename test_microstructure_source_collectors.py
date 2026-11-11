"""P1-5 source collectors: network-free contract regressions with retained fixtures."""
import csv
import hashlib
import importlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

# The network client is an external boundary. Tests run the real parser/window
# accounting against a controlled public-socket transport, never an HTTP main.
with patch.dict(sys.modules, {'websocket': types.SimpleNamespace()}):
    import doge_liquidations as liq
import doge_liquidity as liquidity

ACK = json.dumps({'event':'subscribe','arg':{'channel':'liquidation-orders','instType':'SWAP'},'connId':'test'})
EPOCH = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)

class Clock:
    def __init__(self): self.t = 0.
    def monotonic(self): return self.t
    def utc(self): return (EPOCH + timedelta(seconds=self.t)).isoformat()

class Socket:
    def __init__(self, clock, events=None, pong=True):
        self.clock=clock; self.events=list(events if events is not None else [(1.,ACK)])
        self.pong=pong; self.sent=[]; self.closed=False
    def send(self, value):
        self.sent.append(value)
        if value == 'ping' and self.pong:
            self.events.append((self.clock.t+.05,'pong'))
            self.events.sort(key=lambda x:x[0])
    def recv(self):
        end=self.clock.t+1.
        if self.events and self.events[0][0] <= end:
            self.clock.t,value=self.events.pop(0)
            return value
        self.clock.t=end
        raise TimeoutError('test receive timeout')
    def close(self): self.closed=True

def packet(event_seconds=-3600):
    return json.dumps({'arg':{'channel':'liquidation-orders','instType':'SWAP'},'data':[{
        'instType':'SWAP','instId':'DOGE-USDT-SWAP','instFamily':'DOGE-USDT','uly':'DOGE-USDT',
        'details':[{'bkLoss':'0','bkPx':'0.08','ccy':'','posSide':'long','side':'sell','sz':'2',
                    'ts':str(int((EPOCH+timedelta(seconds=event_seconds)).timestamp()*1000))}]}]})

class WindowTests(unittest.TestCase):
    def observe(self, events=None, pong=True, duration=480, rows=None):
        observer=getattr(liq,'observe_window',None)
        self.assertTrue(callable(observer), 'missing complete subscribed-window and application heartbeat proof')
        clock=Clock(); sock=Socket(clock,events,pong); records=[]; endpoints=[]
        def connect(endpoint):
            endpoints.append(endpoint)
            return sock
        result=observer(rows if rows is not None else {}, Decimal('1000'), records.append,
                        duration=duration, connect=connect, monotonic=clock.monotonic, now=clock.utc)
        return result,records,sock,endpoints
    def test_complete_empty_window_is_healthy_without_claiming_global_zero(self):
        result,records,sock,endpoints=self.observe()
        self.assertEqual(endpoints,['wss://ws.okx.com:443/ws/v5/public'])
        self.assertIn(result['status'],('ok','partial'))
        self.assertTrue(result['window_complete'])
        self.assertGreaterEqual(result['subscribed_seconds'],480)
        self.assertGreaterEqual(result['listen_seconds'],480)
        self.assertEqual(result['observed_doge_events_in_window'],0)
        self.assertFalse(result['zero_observation_is_market_total'])
        self.assertIsNone(result['last_doge_event_utc'])
        self.assertGreaterEqual(result['data_quality']['heartbeat_pongs'],23)
        self.assertTrue(any(r.get('raw_utf8')=='pong' for r in records))
        self.assertTrue(sock.closed)
    def test_disconnect_before_end_does_not_receive_window_credit(self):
        result,_,_,_=self.observe(events=[(1.,ACK),(80.,'')])
        self.assertEqual(result['status'],'error')
        self.assertFalse(result['window_complete'])
        self.assertLess(result['subscribed_seconds'],480)
        self.assertTrue(any('disconnect' in e for e in result['data_quality']['errors']))
    def test_silent_socket_without_pong_fails_even_after_subscription_ack(self):
        result,_,_,_=self.observe(pong=False)
        self.assertEqual(result['status'],'error')
        self.assertFalse(result['window_complete'])
        self.assertTrue(any('heartbeat' in e for e in result['data_quality']['errors']))
    def test_no_ack_is_not_a_subscribed_observation_window(self):
        result,_,_,_=self.observe(events=[])
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['subscribed_seconds'],0)
        self.assertFalse(result['window_complete'])
    def test_short_diagnostic_cannot_satisfy_original_480_second_window(self):
        result,_,_,_=self.observe(duration=30)
        self.assertEqual(result['status'],'error')
        self.assertFalse(result['window_complete'])
        self.assertEqual(result['requested_listen_seconds'],30)
        self.assertEqual(result['required_listen_seconds'],480)
    def test_legacy_event_is_not_retimestamped_by_a_new_empty_window(self):
        old={'event_key':'old','event_ts_ms':'1792051281054','side':'buy','pos_side':'short',
             'contracts':'1','bankruptcy_price':'0.08','notional_usdt':'80',
             'received_at_utc':'2026-10-15T08:01:22.403016+00:00'}
        rows={'old':dict(old)}
        result,_,_,_=self.observe(rows=rows)
        self.assertEqual(rows['old'],old)
        self.assertEqual(result['observed_doge_events_in_window'],0)
        self.assertEqual(result['last_doge_received_at_utc'],'2026-10-15T08:01:22.403016+00:00')
    def test_delayed_duplicate_preserves_first_receive_and_raw_packets(self):
        rows={}
        result,records,_,_=self.observe(events=[(1.,ACK),(2.,packet()),(3.,packet())],rows=rows)
        self.assertIn(result['status'],('ok','partial'))
        self.assertEqual(len(rows),1)
        row=next(iter(rows.values()))
        self.assertEqual(row['received_at_utc'],'2026-11-11T12:00:02+00:00')
        self.assertEqual(row['notional_usdt'],'160.00')
        self.assertEqual(result['data_quality']['new_events'],1)
        self.assertEqual(result['data_quality']['duplicate_events'],1)
        late=result['data_quality']['late_events'][0]
        self.assertEqual(late['usable_from_utc'],row['received_at_utc'])
        self.assertFalse(late['historical_event_time_feature_allowed'])
        self.assertEqual(sum(r.get('raw_utf8')==packet() for r in records),2)
    def test_malformed_doge_packet_fails_closed(self):
        result,_,_,_=self.observe(events=[(1.,ACK),(2.,'{"arg":{"channel":"liquidation-orders"},"data":[{"instId":"DOGE-USDT-SWAP","details":"bad"}]}')])
        self.assertEqual(result['status'],'error')
        self.assertFalse(result['window_complete'])
        self.assertEqual(result['data_quality']['parse_errors'],1)
    def test_future_event_timestamp_fails_closed(self):
        result,_,_,_=self.observe(events=[(1.,ACK),(2.,packet(1000))])
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['data_quality']['new_events'],0)

class Response(io.BytesIO):
    status=200
    headers={'Content-Type':'application/json'}
    def __enter__(self): return self
    def __exit__(self,*args): self.close()

class LiquidityTests(unittest.TestCase):
    def setUp(self):
        root=os.getenv('P15_TEST_ARTIFACT_ROOT')
        # Local runs supply the protected formal evidence root. No fixture is deleted.
        self.base=Path(tempfile.mkdtemp(prefix='liquidity-',dir=root)) if root else Path(tempfile.mkdtemp(prefix='p15-liquidity-'))
    def test_raw_response_is_preserved_before_values_are_interpreted(self):
        raw=b'{"code":"0","msg":"","data":[{"instId":"DOGE-USDT-SWAP","value":"1"}]}'
        with patch.object(liquidity.urllib.request,'urlopen',return_value=Response(raw)):
            rows=liquidity.get('/api/v5/public/instruments',raw_dir=self.base,instId='DOGE-USDT-SWAP')
        self.assertEqual(rows,[{'instId':'DOGE-USDT-SWAP','value':'1'}])
        files=list(self.base.glob('*.json'))
        self.assertEqual(len(files),1,'missing byte-exact source evidence')
        proof=json.loads(files[0].read_text(encoding='utf-8'))
        self.assertEqual(proof['body_utf8'],raw.decode())
        self.assertEqual(proof['raw_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(proof['first_seen_utc'],proof['received_at_utc'])
        self.assertNotIn('raw_dir',proof['source_url'])
    def test_provider_error_body_is_retained_and_never_fabricated_as_data(self):
        raw=b'{"code":"50011","msg":"Rate limit reached","data":[]}'
        with patch.object(liquidity.urllib.request,'urlopen',return_value=Response(raw)):
            with self.assertRaises(ValueError):
                liquidity.get('/api/v5/market/books',raw_dir=self.base,instId='DOGE-USDT-SWAP')
        files=list(self.base.glob('*.json'))
        self.assertEqual(len(files),1,'failed provider response lost')
        self.assertEqual(json.loads(files[0].read_text(encoding='utf-8'))['body_utf8'],raw.decode())
    def test_historical_append_exposes_pause_gap_without_synthetic_rows(self):
        row={key:'' for key in liquidity.FIELDS}
        row.update(collected_at_ms='1792050347940',instrument='DOGE-USDT-SWAP',best_bid='0.08014',
                   best_ask='0.08015',liquidation_source_status='not_available')
        with patch.object(liquidity,'ROOT',self.base):
            liquidity.historical(row)
            newer=dict(row,collected_at_ms='1794398677000')
            result=liquidity.historical(newer)
        with (self.base/'hourly_snapshots.csv').open(newline='',encoding='utf-8') as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0],row)
        self.assertEqual(result['gaps_over_90_minutes'],1)
        self.assertFalse(result.get('historical_backfill',True),'pause gap must remain explicitly unfilled')
        self.assertEqual(result['gap_since_previous_seconds'],2348329.06)
        self.assertTrue(all(r['liquidation_usd']=='' for r in rows))
    def test_derivatives_rejects_another_instrument_response(self):
        oi={'instId':'BTC-USDT-SWAP','instType':'SWAP','ts':str(int(EPOCH.timestamp()*1000)),'oiUsd':'100'}
        fr={'instId':'DOGE-USDT-SWAP','fundingRate':'0.0001','fundingTime':'1794412800000'}
        with patch.object(liquidity,'get',side_effect=[[oi],[fr]]),patch.object(liquidity.time,'time',return_value=EPOCH.timestamp()):
            with self.assertRaisesRegex(ValueError,'instrument'):
                liquidity.derivatives()


class AdditionalEvidenceTests(unittest.TestCase):
    def test_liquidation_instrument_metadata_is_retained_before_validation(self):
        root=os.getenv('P15_TEST_ARTIFACT_ROOT')
        base=Path(tempfile.mkdtemp(prefix='instrument-',dir=root)) if root else Path(tempfile.mkdtemp(prefix='p15-instrument-'))
        raw=b'{"code":"0","data":[{"instId":"DOGE-USDT-SWAP","ctType":"linear","ctValCcy":"DOGE","state":"live","ctVal":"1000"}]}'
        with patch.object(liq.urllib.request,'urlopen',return_value=Response(raw)):
            self.assertEqual(liq.contract_doge(base),Decimal('1000'))
        proof=json.loads(next(base.glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(proof['raw_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(proof['first_seen_utc'],proof['received_at_utc'])
    def test_small_future_event_is_rejected_like_existing_csv_timestamp_policy(self):
        case=WindowTests()
        result,_,_,_=case.observe(events=[(1.,ACK),(2.,packet(8))])
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['data_quality']['new_events'],0)
    def test_duplicate_ack_does_not_inflate_or_restart_coverage(self):
        case=WindowTests()
        result,_,_,_=case.observe(events=[(1.,ACK),(200.,ACK)])
        self.assertIn(result['status'],('ok','partial'))
        self.assertEqual(result['data_quality']['subscription_ack_count'],2)
        self.assertLess(result['subscribed_seconds'],481)
        self.assertEqual(result['window_started_at_utc'],'2026-11-11T12:00:01+00:00')



class RawManifestCompletionTests(unittest.TestCase):
    def fixture(self, prefix):
        root=os.getenv('P15_TEST_ARTIFACT_ROOT')
        return Path(tempfile.mkdtemp(prefix=prefix,dir=root)) if root else Path(tempfile.mkdtemp(prefix=prefix))
    def test_liquidation_failure_still_attaches_existing_source_packets(self):
        finish=getattr(liq,'include_raw_evidence',None)
        self.assertTrue(callable(finish),'failed collector report does not attach existing raw source packets')
        root=self.fixture('liq-failed-');run=root/'data/liquidations/raw/run';run.mkdir(parents=True)
        (run/'instruments.json').write_bytes(b'{"code":"0"}')
        (run/'websocket.jsonl').write_bytes(b'{"raw_utf8":"malformed"}\n')
        report={'status':'error','errors':{'collector':'test parse failure'}}
        finish(report,run,root)
        self.assertEqual({p['path'] for p in report['raw_evidence']},
                         {'data/liquidations/raw/run/instruments.json','data/liquidations/raw/run/websocket.jsonl'})
        self.assertTrue(all(len(p['sha256'])==64 for p in report['raw_evidence']))
        self.assertEqual(report['status'],'error')
    def test_liquidity_snapshot_has_independent_verified_reference_without_self_hash(self):
        archive=getattr(liquidity,'archive_snapshot',None)
        self.assertTrue(callable(archive),'archived liquidity snapshot is not referenced by latest')
        root=self.fixture('liquidity-snapshot-');run=root/'data/liquidity/raw/run';run.mkdir(parents=True)
        report={'status':'error','collected_at_utc':'2026-11-11T12:00:00+00:00','raw_evidence':[]}
        archive(report,run,root)
        ref=report['snapshot_evidence']
        raw=(root/ref['path']).read_bytes()
        self.assertEqual(ref['path'],'data/liquidity/raw/run/snapshot.json')
        self.assertEqual(ref['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertNotIn('snapshot_evidence',json.loads(raw))
        self.assertEqual(json.loads(raw)['status'],'error')



class DeadlineAndAvailabilityTests(unittest.TestCase):
    def run_socket(self, socket_type):
        clock=Clock();sock=socket_type(clock);records=[]
        result=liq.observe_window({},Decimal('1000'),records.append,
            connect=lambda endpoint:sock,monotonic=clock.monotonic,now=clock.utc)
        return result
    def test_last_unanswered_ping_cannot_certify_full_window(self):
        class SlowPongs(Socket):
            def __init__(self,clock):super().__init__(clock);self.n=0
            def send(self,value):
                self.sent.append(value)
                if value=='ping':
                    self.n+=1
                    if self.n<=16:
                        self.events.append((self.clock.t+8.3,'pong'))
                        self.events.sort(key=lambda x:x[0])
        result=self.run_socket(SlowPongs)
        self.assertEqual(result['status'],'error')
        self.assertFalse(result['window_complete'])
        self.assertLess(result['heartbeat_verified_seconds'],result['subscribed_seconds'])
        self.assertTrue(any('heartbeat' in e for e in result['data_quality']['errors']))
    def test_blocked_receive_cannot_accept_ack_after_subscription_deadline(self):
        class LateAck(Socket):
            def __init__(self,clock):super().__init__(clock);self.first=True
            def recv(self):
                if self.first:
                    self.first=False;self.events=[];self.clock.t=25.
                    return ACK
                return super().recv()
        result=self.run_socket(LateAck)
        self.assertEqual(result['status'],'error')
        self.assertFalse(result['window_complete'])
        self.assertEqual(result['data_quality']['subscription_ack_count'],0)
        self.assertTrue(any('subscription' in e for e in result['data_quality']['errors']))
    def test_new_liquidity_row_clock_starts_after_successful_response_collection(self):
        stamp=getattr(liquidity,'stamp_collection',None)
        self.assertTrue(callable(stamp),'new CSV rows still use the pre-request run clock')
        result={'status':'ok'}
        start=int(EPOCH.timestamp()*1000)
        with patch.object(liquidity.time,'time',return_value=EPOCH.timestamp()+30):
            collected=stamp(result,start)
        self.assertEqual(collected,start+30000)
        self.assertEqual(result['started_at_utc'],'2026-11-11T12:00:00+00:00')
        self.assertEqual(result['collected_at_utc'],'2026-11-11T12:00:30+00:00')
        self.assertEqual(result['collection_elapsed_ms'],30000)


if __name__=='__main__': unittest.main()

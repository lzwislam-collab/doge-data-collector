"""Narrow official ETF recovery URLs; keep original required and age rules."""
import csv
import hashlib
import json
import unittest
from datetime import timedelta
from feed_freshness_audit import FEEDS, check_etf_csv, evaluate
import test_feed_quality as fixtures


class RecoveredSourceQualityTests(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.QualityAuditTests();self.f.setUp()
 def tearDown(self):self.f.tearDown()
 def rewrite(self, asset, url):
  file=self.f.b/'data/etf/daily_flows.csv'
  with file.open(newline='') as inp:
   reader=csv.DictReader(inp);rows=list(reader);fields=reader.fieldnames
  for row in rows:
   if row['asset']==asset:row['source_url']=url
  with file.open('w',newline='') as out:
   writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader();writer.writerows(rows)
 def test_exact_official_all_data_urls_pass_without_claiming_old_availability(self):
  self.rewrite('BTC','https://farside.co.uk/bitcoin-etf-flow-all-data/')
  self.rewrite('ETH','https://farside.co.uk/ethereum-etf-flow-all-data/')
  result=check_etf_csv(self.f.b,self.f.t)
  self.assertEqual(result['state'],'ok',result)
  self.assertFalse(result['point_in_time_historical_backfill_verified'])
 def test_wrong_asset_table_and_lookalike_hosts_fail(self):
  for url in ('https://farside.co.uk/ethereum-etf-flow-all-data/',
              'https://farside.co.uk.evil.invalid/bitcoin-etf-flow-all-data/',
              'https://farside.co.uk/bitcoin-etf-flow-all-data/?forged=1'):
   with self.subTest(url=url):
    self.rewrite('BTC',url)
    self.assertEqual(check_etf_csv(self.f.b,self.f.t)['state'],'error')
 def test_six_required_and_original_limits_remain(self):
  for key,limit in {'onchain':36,'liquidations_okx':3,'liquidity':3,'etf':96,'macro':48,'whale_confirmed_blocks':2}.items():
   self.assertTrue(FEEDS[key][3]);self.assertEqual(FEEDS[key][2],limit)
 def test_old_confirmed_checkpoint_still_blocks_even_with_fresh_end_time(self):
  file=self.f.b/'data/whales_blocks/latest.json'
  data=json.loads(file.read_text());data['backlog_blocks_at_run_end']=39322
  file.write_text(json.dumps(data))
  self.assertIn('whale_confirmed_blocks',evaluate(self.f.b,self.f.t)['critical_failures'])
 def test_incomplete_new_liquidation_window_cannot_be_mislabeled_healthy(self):
  file=self.f.b/'data/liquidations/latest.json'
  data=json.loads(file.read_text());data.update(schema_version=4,window_complete=False,
   subscribed_seconds=60,required_listen_seconds=480,requested_listen_seconds=480,
   data_quality={'errors':[],'heartbeat_pongs':1},not_market_wide=True,coverage_is_per_listening_window_only=True)
  file.write_text(json.dumps(data))
  self.assertIn('liquidations_okx',evaluate(self.f.b,self.f.t)['critical_failures'])
 def test_unknown_run_end_tip_cannot_qualify_zero_backlog(self):
  file=self.f.b/'data/whales_blocks/latest.json'
  data=json.loads(file.read_text());data.update(schema_version=4,run_end_tip_verified=False)
  file.write_text(json.dumps(data))
  self.assertIn('whale_confirmed_blocks',evaluate(self.f.b,self.f.t)['critical_failures'])
 def test_new_liquidity_raw_packet_must_exist_and_match_digest(self):
  file=self.f.b/'data/liquidity/latest.json'
  data=json.loads(file.read_text());data.update(schema_version=3,raw_evidence=[
   {'path':'data/liquidity/raw/missing.json','sha256':'0'*64}])
  file.write_text(json.dumps(data))
  self.assertIn('liquidity',evaluate(self.f.b,self.f.t)['critical_failures'])
 def complete_window(self):
  path='data/liquidations/raw/window/websocket.jsonl'
  begin=self.f.t-timedelta(seconds=480)
  packets=[{'kind':'inbound','received_at_utc':begin.isoformat(),
   'raw_utf8':json.dumps({'event':'subscribe','arg':{'channel':'liquidation-orders','instType':'SWAP'}})}]
  for second in range(20,480,20):
   stamp=(begin+timedelta(seconds=second)).isoformat()
   packets.extend([{'kind':'outbound','sent_at_utc':stamp,'raw_utf8':'ping'},
                   {'kind':'inbound','received_at_utc':stamp,'raw_utf8':'pong'}])
  packets.append({'kind':'window_result','at_utc':self.f.t.isoformat(),'window_complete':True})
  raw=('\n'.join(json.dumps(p) for p in packets)+'\n').encode()
  target=self.f.b/path;target.parent.mkdir(parents=True);target.write_bytes(raw)
  file=self.f.b/'data/liquidations/latest.json'
  data=json.loads(file.read_text());data.update(schema_version=4,window_complete=True,
   subscribed_seconds=480,required_listen_seconds=480,requested_listen_seconds=480,
   data_quality={'errors':[],'heartbeat_pings':23,'heartbeat_pongs':23,'heartbeat_pending_at_end':False},
   heartbeat_verified_seconds=480,started_at_utc=begin.isoformat(),
   window_started_at_utc=begin.isoformat(),window_ended_at_utc=self.f.t.isoformat(),
   not_market_wide=True,coverage_is_per_listening_window_only=True,
   observed_doge_events_in_window=0,raw_evidence=[{'path':path,'sha256':hashlib.sha256(raw).hexdigest()}])
  file.write_text(json.dumps(data))
  return file,data
 def test_complete_zero_event_window_with_real_raw_reference_is_healthy(self):
  self.complete_window()
  result=evaluate(self.f.b,self.f.t)
  self.assertNotIn('liquidations_okx',result['critical_failures'])
  self.assertEqual(result['sources']['liquidations_okx']['raw_evidence_files_verified'],1)
 def test_heartbeat_or_window_contradictions_cannot_pass_independent_audit(self):
  file,data=self.complete_window()
  import copy
  for change in ('pending','counts','duration','utc_span','nonfinite'):
   broken=copy.deepcopy(data)
   if change=='pending':broken['data_quality']['heartbeat_pending_at_end']=True
   if change=='counts':broken['data_quality']['heartbeat_pings']=24
   if change=='duration':broken['heartbeat_verified_seconds']=452.8
   if change=='utc_span':broken['window_started_at_utc']=(self.f.t-timedelta(seconds=60)).isoformat()
   if change=='nonfinite':broken['heartbeat_verified_seconds']='NaN'
   with self.subTest(change=change):
    file.write_text(json.dumps(broken))
    self.assertIn('liquidations_okx',evaluate(self.f.b,self.f.t)['critical_failures'])
 def test_true_caught_up_checkpoint_has_verified_run_end_tip(self):
  file=self.f.b/'data/whales_blocks/latest.json'
  data=json.loads(file.read_text());data.update(schema_version=4,run_end_tip_verified=True,
   tip_height=106,confirmation_depth=6,confirmed_tip_at_run=100,last_completed_height=100,
   run_end_tip_observed_at_utc=self.f.t.isoformat(),started_at_utc=self.f.t.isoformat())
  file.write_text(json.dumps(data))
  self.assertNotIn('whale_confirmed_blocks',evaluate(self.f.b,self.f.t)['critical_failures'])


if __name__=='__main__':unittest.main()

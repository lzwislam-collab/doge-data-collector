#!/usr/bin/env python3
"""Source health is not same as source coverage; synthetic fail-closed audit."""
import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from feed_freshness_audit import FEEDS,evaluate

class QualityAuditTests(unittest.TestCase):
 def setUp(self):
  self.t=datetime(2026,10,11,2,0,tzinfo=timezone.utc)
  self.tmp=tempfile.TemporaryDirectory();self.b=Path(self.tmp.name)
  for key,(path,timefield,limit,required,allowed) in FEEDS.items():
   p=self.b/path;p.parent.mkdir(parents=True,exist_ok=True)
   obj={'status':'ok','coverage':'NOT market-wide totals','latest_trade_date':{'BTC':'2026-10-09','ETH':'2026-10-09'},
    'backlog_blocks_at_run_end':0,'errors':[],timefield:self.t.isoformat()}
   p.write_text(json.dumps(obj))
  h=self.b/'data/hourly_research/latest.json';h.parent.mkdir(parents=True,exist_ok=True)
  h.write_text(json.dumps({'status':'partial_backfill','archive_complete':False,
   'quality':{'symbols':{'DOGE-USDT':{'last_confirmed_start_utc':'2026-10-11T00:00:00+00:00',
    'missing_hours_between_endpoints':0}}}}))
 def tearDown(self):self.tmp.cleanup()
 def test_complete_critical_with_partial_historical_hourly(self):
  x=evaluate(self.b,self.t)
  self.assertEqual(x['critical_failures'],[])
  self.assertEqual(x['sources']['hourly_research']['state'],'ok_coverage_limited')
 def test_stale_legacy_sse_only_is_warning(self):
  f=self.b/'data/whales/latest.json'
  x=json.loads(f.read_text());x['observed_to_utc']='2026-10-09T00:00:00+00:00'
  f.write_text(json.dumps(x));r=evaluate(self.b,self.t)
  self.assertNotEqual(r['status'],'blocked')
  self.assertIn('whale_sse_optional',r['optional_source_warnings'])
  self.assertEqual(r['sources']['whale_confirmed_blocks']['state'],'ok_coverage_limited')
 def test_stale_confirmed_blocks_blocks(self):
  f=self.b/'data/whales_blocks/latest.json'
  x=json.loads(f.read_text());x['ended_at_utc']='2026-10-10T16:00:00+00:00'
  f.write_text(json.dumps(x));r=evaluate(self.b,self.t)
  self.assertEqual(r['status'],'blocked')
  self.assertIn('whale_confirmed_blocks',r['critical_failures'])
 def test_required_market_missing_blocks(self):
  (self.b/'data/latest.json').unlink()
  self.assertEqual(evaluate(self.b,self.t)['status'],'blocked')
 def test_future_timestamp_blocks(self):
  f=self.b/'data/liquidity/latest.json';x=json.loads(f.read_text())
  x['collected_at_utc']='2026-10-12T00:00:00+00:00';f.write_text(json.dumps(x))
  self.assertEqual(evaluate(self.b,self.t)['status'],'blocked')

if __name__=='__main__':unittest.main(verbosity=2)

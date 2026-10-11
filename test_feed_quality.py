#!/usr/bin/env python3
"""Source health is not same as source coverage; synthetic fail-closed audit."""
import json,tempfile,unittest,csv
from pathlib import Path
from datetime import datetime,timezone,timedelta
from feed_freshness_audit import FEEDS,STAGE2_CSVS,evaluate

class QualityAuditTests(unittest.TestCase):
 def setUp(self):
  self.t=datetime(2026,10,11,2,0,tzinfo=timezone.utc)
  self.tmp=tempfile.TemporaryDirectory();self.b=Path(self.tmp.name)
  for key,(path,timefield,limit,required,allowed) in FEEDS.items():
   p=self.b/path;p.parent.mkdir(parents=True,exist_ok=True)
   obj={'status':'ok','coverage':'NOT market-wide totals','latest_trade_date':{'BTC':'2026-10-09','ETH':'2026-10-09'},
    'backlog_blocks_at_run_end':0,'errors':[],timefield:self.t.isoformat()}
   p.write_text(json.dumps(obj))
  for name,(path,idfield,tsfield,utcfield,countfield) in STAGE2_CSVS.items():
   target=self.b/path;target.parent.mkdir(parents=True,exist_ok=True)
   columns=list(dict.fromkeys([idfield,tsfield]+([utcfield] if utcfield else ['instrument','best_bid','best_ask','liquidation_usd','liquidation_source_status'])))
   row={c:'' for c in columns};row[idfield]=str(int(self.t.timestamp()*1000)) if idfield==tsfield else 'unique_id'
   row[tsfield]=str(int(self.t.timestamp()*1000))
   if utcfield:row[utcfield]=self.t.isoformat()
   else:row.update({'instrument':'DOGE-USDT-SWAP','best_bid':'0.08','best_ask':'0.09','liquidation_usd':'','liquidation_source_status':'not_available'})
   with target.open('w',newline='',encoding='utf-8') as out:
    w=csv.DictWriter(out,fieldnames=columns);w.writeheader();w.writerow(row)
   meta=self.b/FEEDS[name][0];j=json.loads(meta.read_text())
   if countfield=='data_quality.rows':j['data_quality']={'rows':1}
   else:j[countfield]=1
   meta.write_text(json.dumps(j))
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

 def test_duplicate_liquidation_event_is_blocked(self):
  f=self.b/'data/bybit_liquidations/events.csv';lines=f.read_text().splitlines()
  f.write_text('\n'.join(lines+[lines[-1]])+'\n')
  r=evaluate(self.b,self.t)
  self.assertIn('stage2_csv_quality',r['critical_failures'])
  self.assertEqual(r['sources']['stage2_csv_quality']['checks']['bybit_liquidations_optional']['duplicates'],1)
 def test_liquidity_may_not_fabricate_liquidation_usd(self):
  f=self.b/'data/liquidity/hourly_snapshots.csv'
  with f.open(newline='') as inp:rows=list(csv.DictReader(inp));header=list(rows[0])
  rows[0]['liquidation_usd']='100000'
  with f.open('w',newline='') as out:
   writer=csv.DictWriter(out,fieldnames=header);writer.writeheader();writer.writerows(rows)
  result=evaluate(self.b,self.t)
  self.assertIn('stage2_csv_quality',result['critical_failures'])
 def test_liquidation_utc_mismatch_is_blocked(self):
  f=self.b/'data/liquidations/events.csv'
  with f.open(newline='') as inp:rows=list(csv.DictReader(inp));header=list(rows[0])
  rows[0]['received_at_utc']=(self.t+timedelta(hours=1)).isoformat()
  with f.open('w',newline='') as out:
   writer=csv.DictWriter(out,fieldnames=header);writer.writeheader();writer.writerows(rows)
  self.assertIn('stage2_csv_quality',evaluate(self.b,self.t)['critical_failures'])

if __name__=='__main__':unittest.main(verbosity=2)

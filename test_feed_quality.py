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
  etf=self.b/'data/etf';etf.mkdir(parents=True,exist_ok=True)
  headers=['asset','trade_date_us','total_usd_m','reported_funds','funds_with_values','source_url','first_seen_utc','last_checked_utc','revision_count']
  with (etf/'daily_flows.csv').open('w',newline='') as out:
   writer=csv.DictWriter(out,fieldnames=headers);writer.writeheader()
   for symbol in ('BTC','ETH'):
    writer.writerow({'asset':symbol,'trade_date_us':'2026-10-09','total_usd_m':'1.2',
     'reported_funds':'2','funds_with_values':'2','source_url':'https://farside.co.uk/'+symbol.lower()+'/',
     'first_seen_utc':'2026-10-10T01:00:00+00:00','last_checked_utc':'2026-10-10T02:00:00+00:00','revision_count':'0'})
  (etf/'revisions.csv').write_text('asset,trade_date_us,old_total_usd_m,new_total_usd_m,observed_at_utc,source_url\n')
  etfmeta=etf/'latest.json';meta=json.loads(etfmeta.read_text());meta['stored_rows']=2;etfmeta.write_text(json.dumps(meta))
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

 def test_duplicate_etf_trade_day_blocks(self):
  file=self.b/'data/etf/daily_flows.csv'
  rows=file.read_text().splitlines();file.write_text('\n'.join(rows+[rows[1]])+'\n')
  obj=evaluate(self.b,self.t)
  self.assertIn('stage3_etf_csv_quality',obj['critical_failures'])
 def test_etf_mislabeled_first_seen_blocks(self):
  file=self.b/'data/etf/daily_flows.csv'
  with file.open(newline='') as inp:rows=list(csv.DictReader(inp));cols=list(rows[0])
  rows[0]['first_seen_utc']='2026-10-08T20:00:00+00:00'
  with file.open('w',newline='') as out:
   writer=csv.DictWriter(out,fieldnames=cols);writer.writeheader();writer.writerows(rows)
  obj=evaluate(self.b,self.t)
  self.assertIn('stage3_etf_csv_quality',obj['critical_failures'])
 def test_etf_point_in_time_history_not_claimed(self):
  obj=evaluate(self.b,self.t)
  self.assertEqual(obj['sources']['stage3_etf_csv_quality']['state'],'ok')
  self.assertFalse(obj['stage3_etf_historical_point_in_time_verified'])

if __name__=='__main__':unittest.main(verbosity=2)

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
  # Minimal synthetic Stage 4 fixtures; never require live network for unit tests.
  macro=self.b/'data/macro';macro.mkdir(parents=True,exist_ok=True)
  macros=['CPIAUCSL','CPILFESL','PPIACO','PAYEMS','UNRATE','DFEDTARU','DFEDTARL','DGS10']
  macrofields=['series_id','observation_date','value','unit','frequency','source_url','first_seen_utc','last_checked_utc','revision_count']
  with (macro/'observations.csv').open('w',newline='') as out:
   writer=csv.DictWriter(out,fieldnames=macrofields);writer.writeheader()
   for sid in sorted(macros):
    writer.writerow({'series_id':sid,'observation_date':'2026-10-09','value':'1.5','unit':'percent',
     'frequency':'daily','source_url':'https://fred.stlouisfed.org/graph/fredgraph.csv?id='+sid,
     'first_seen_utc':'2026-10-10T01:00:00+00:00','last_checked_utc':'2026-10-10T02:00:00+00:00','revision_count':'0'})
  (macro/'revisions.csv').write_text('series_id,observation_date,old_value,new_value,observed_at_utc,source_url\n')
  m=macro/'latest.json';j=json.loads(m.read_text());j['total_rows']=8
  j['series']={sid:{'count_on_source':1,'oldest_observation_date':'2026-10-09','latest_observation_date':'2026-10-09'} for sid in macros}
  m.write_text(json.dumps(j))
  policy=self.b/'data/policy_events';policy.mkdir(parents=True,exist_ok=True)
  policyfields=['source','event_id','event_type','title','event_time_utc','event_time_precision','first_seen_utc','last_seen_utc','url','point_in_time_rule']
  with (policy/'events.csv').open('w',newline='') as out:
   wr=csv.DictWriter(out,fieldnames=policyfields);wr.writeheader()
   wr.writerow({'source':'BLS_CALENDAR','event_id':'bls1','event_type':'scheduled_macro_release','title':'Consumer Price Index',
     'event_time_utc':'2026-11-01T13:30:00+00:00','event_time_precision':'calendar_schedule_not_actual_publication',
     'first_seen_utc':'2026-10-10T01:00:00+00:00','last_seen_utc':'2026-10-10T02:00:00+00:00',
     'url':'https://www.bls.gov/schedule/news_release/bls.ics','point_in_time_rule':'schedule_only'})
   wr.writerow({'source':'FED_MONETARY_RSS','event_id':'fed1','event_type':'federal_reserve_monetary_policy','title':'FOMC Minutes',
     'event_time_utc':'2026-10-09T18:00:00+00:00','event_time_precision':'provider_pubDate',
     'first_seen_utc':'2026-10-10T01:00:00+00:00','last_seen_utc':'2026-10-10T02:00:00+00:00',
     'url':'https://www.federalreserve.gov/newsevents/','point_in_time_rule':'max(provider_pubDate,first_seen_utc)'})
  m=policy/'latest.json';j={'status':'ok','records':2};m.write_text(json.dumps(j))
  for folder,prefix in [('regulatory_events','CFTC_PRESS_RSS'),('sec_events','SEC_PRESS_RSS')]:
   target=self.b/'data'/folder;target.mkdir(parents=True,exist_ok=True)
   fields=['source','event_id','title','published_utc','first_seen_utc','last_checked_utc','source_url','keyword_candidate','backtest_available_from_utc']
   with (target/'events.csv').open('w',newline='') as out:
    wr=csv.DictWriter(out,fieldnames=fields);wr.writeheader()
    wr.writerow({'source':prefix,'event_id':'reg1','title':'Digital Assets Policy',
     'published_utc':'2026-10-09T18:00:00+00:00',
     'first_seen_utc':'2026-10-10T01:00:00+00:00',
     'last_checked_utc':'2026-10-10T02:00:00+00:00',
     'source_url':'https://www.example.gov/policy','keyword_candidate':'true',
     'backtest_available_from_utc':'2026-10-10T01:00:00+00:00'})
   m=target/'latest.json';j={'status':'ok','stored_events':1};m.write_text(json.dumps(j))
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

 def test_macro_observation_date_is_not_release(self):
  result=evaluate(self.b,self.t)
  self.assertEqual(result['sources']['stage4_macro_policy_csv_quality']['state'],'ok')
  self.assertFalse(result['stage4_macro_historical_vintages_verified'])
 def test_macro_first_seen_cannot_precede_observation(self):
  file=self.b/'data/macro/observations.csv'
  with file.open(newline='') as inp:rows=list(csv.DictReader(inp));cols=list(rows[0])
  rows[0]['first_seen_utc']='2026-10-08T00:00:00+00:00'
  with file.open('w',newline='') as out:
   wr=csv.DictWriter(out,fieldnames=cols);wr.writeheader();wr.writerows(rows)
  self.assertIn('stage4_macro_policy_csv_quality',evaluate(self.b,self.t)['critical_failures'])
 def test_bls_schedule_cannot_masquerade_as_released_data(self):
  file=self.b/'data/policy_events/events.csv'
  with file.open(newline='') as inp:rows=list(csv.DictReader(inp));cols=list(rows[0])
  rows[0]['event_time_precision']='provider_pubDate'
  with file.open('w',newline='') as out:
   wr=csv.DictWriter(out,fieldnames=cols);wr.writeheader();wr.writerows(rows)
  self.assertIn('stage4_macro_policy_csv_quality',evaluate(self.b,self.t)['critical_failures'])
 def test_regulatory_first_seen_cannot_be_backdated(self):
  file=self.b/'data/sec_events/events.csv'
  with file.open(newline='') as inp:rows=list(csv.DictReader(inp));cols=list(rows[0])
  rows[0]['backtest_available_from_utc']='2026-10-08T01:00:00+00:00'
  with file.open('w',newline='') as out:
   wr=csv.DictWriter(out,fieldnames=cols);wr.writeheader();wr.writerows(rows)
  self.assertIn('stage4_macro_policy_csv_quality',evaluate(self.b,self.t)['critical_failures'])

if __name__=='__main__':unittest.main(verbosity=2)

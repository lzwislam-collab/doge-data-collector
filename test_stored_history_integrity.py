"""Reject damaged history and revisions before any collector overwrite."""
import contextlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import doge_history as daily
import okx_hourly_research_history as hourly

class StoredHistory(unittest.TestCase):
 def daily_row(self):return daily.validrow('DOGE-USDT',['0','1','2','.5','1','5','0','0','1'],4*daily.DAY)
 def hourly_row(self):return hourly.parse('DOGE-USDT',['0','1','2','.5','1','5','0','0','1'],'1970-01-01T02:00:00+00:00',4*hourly.HOUR)
 def test_daily_stored_values_source_and_closure_are_rechecked(self):
  for key,bad in [('close','NaN'),('low','-1'),('volume_base','-1'),('source','forged'),('symbol','OTHER'),('open_utc_ms','1'),('open_utc_ms',str(4*daily.DAY))]:
   with self.subTest(key=key,bad=bad),tempfile.TemporaryDirectory() as directory:
    row=self.daily_row();row[key]=bad;p=Path(directory)/'daily.csv';p.write_text(daily.dump([row]),encoding='utf-8')
    with patch.object(daily.time,'time',return_value=4*daily.DAY/1000),self.assertRaises(ValueError):daily.loadcsv(p)
 def test_hourly_stored_values_and_ingestion_are_rechecked(self):
  for key,bad in [('close','NaN'),('low','-1'),('volume_base','-1'),('source','forged'),('symbol','OTHER'),('open_utc_ms','1'),('first_ingested_utc','1970-01-01T00:30:00+00:00'),('first_ingested_utc','1970-01-01T02:00:00'),('first_ingested_utc','2999-01-01T02:00:00+00:00')]:
   with self.subTest(key=key,bad=bad),tempfile.TemporaryDirectory() as directory:
    row=self.hourly_row();row[key]=bad;p=Path(directory)/'ohlc.csv'
    import csv
    with p.open('w',newline='',encoding='utf-8') as f:
     w=csv.DictWriter(f,fieldnames=hourly.COLUMNS);w.writeheader();w.writerow(row)
    with patch.object(hourly,'DATA',p),patch.object(hourly.time,'time',return_value=4*hourly.HOUR/1000),self.assertRaises(ValueError):hourly.read_old()
 def test_daily_revision_preserves_old_bytes_and_backtest(self):
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory);rows=[]
   for symbol in daily.SYMBOLS:
    row=self.daily_row();row['symbol']=symbol;rows.append(row)
   p=base/'daily.csv';p.write_text(daily.dump(rows),encoding='utf-8');before=p.read_bytes()
   def fetch(symbol,cursor=None):return [['0','1','2','.5','1.1','5','0','0','1'],[str(daily.DAY),'1','2','.5','1','5','0','0','1']]
   with patch.object(daily,'BASE',base),patch.object(daily,'request',side_effect=fetch),patch.object(daily.time,'time',return_value=2*daily.DAY/1000),contextlib.redirect_stdout(io.StringIO()):code=daily.main()
   self.assertEqual(code,1);self.assertEqual(p.read_bytes(),before);self.assertFalse((base/'backtest_latest.json').exists())
 def test_hourly_corruption_is_reported_without_overwrite(self):
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory);p=base/'ohlc.csv';p.write_text('bad header\n',encoding='utf-8');before=p.read_bytes()
   with patch.object(hourly,'BASE',base),patch.object(hourly,'DATA',p),patch.object(hourly,'request') as request,contextlib.redirect_stdout(io.StringIO()):code=hourly.run()
   self.assertEqual(code,1);request.assert_not_called();self.assertEqual(p.read_bytes(),before);self.assertEqual(json.loads((base/'latest.json').read_bytes())['status'],'error')

class HourlyPagination(unittest.TestCase):
 def test_all_rows_from_older_page_are_merged(self):
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory);path=base/'ohlc.csv'
   def bar(i):return [str(i*hourly.HOUR),'1','2','.5','1','5','0','0','1']
   def fetch(symbol,cursor=None):return [bar(3)] if cursor is None else [bar(i) for i in (2,1,0)]
   with patch.object(hourly,'BASE',base),patch.object(hourly,'DATA',path),patch.object(hourly,'PAGE_BUDGET',1),patch.object(hourly,'OLDEST_TARGET',0),patch.object(hourly,'request',side_effect=fetch),patch.object(hourly.time,'time',return_value=4*hourly.HOUR/1000),contextlib.redirect_stdout(io.StringIO()):code=hourly.run()
   self.assertEqual(code,0);self.assertEqual(json.loads((base/'latest.json').read_bytes())['inserted'],12)

if __name__=='__main__':unittest.main()


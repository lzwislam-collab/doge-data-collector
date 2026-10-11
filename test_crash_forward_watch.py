#!/usr/bin/env python3
"""Ex-ante DOGE hourly risk alert causal, duplication, and timing tests."""
import csv,tempfile,subprocess,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from crash_forward_watch import run,feature,hour_rows,MODEL,COLS,HOUR

class HourlyWatchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.base=Path(self.tmp.name)
  (self.base/'data/hourly_research').mkdir(parents=True)
  (self.base/'data/crash_research').mkdir(parents=True)
  subprocess.run(['git','init','-q'],cwd=self.base,check=True)
  self.base_ms=int(datetime(2026,10,11,0,tzinfo=timezone.utc).timestamp()*1000)
  self.make_rows(45)
 def tearDown(self):self.tmp.cleanup()
 def make_rows(self,n,force_alert=False):
  f=self.base/'data/hourly_research/ohlc.csv'
  with f.open('w',newline='') as out:
   wr=csv.writer(out)
   wr.writerow(['symbol','open_utc_ms','open','high','low','close','volume_base','first_ingested_utc','source'])
   for i in range(n):
    t=self.base_ms+i*HOUR
    for s in ('DOGE-USDT','BTC-USDT','ETH-USDT'):
     o=0.1 if s=='DOGE-USDT' else 100.
     alert=force_alert and i==n-1
     c=o*(.97 if alert and s=='DOGE-USDT' else 1.)
     low=o*(.96 if alert and s=='DOGE-USDT' else .999)
     vol=20000 if alert and s=='DOGE-USDT' else 100
     wr.writerow([s,t,o,o,low,c,vol,
       datetime.fromtimestamp((t+HOUR)/1000,timezone.utc).isoformat(),'synthetic'])
  self.now=datetime.fromtimestamp((self.base_ms+n*HOUR+15*60000)/1000,timezone.utc)
 def test_new_checkpoint_even_when_no_alarm(self):
  x=run(self.now,self.base)
  self.assertEqual(x['status'],'ok');self.assertEqual(x['registered_new'],1)
  r=list(csv.DictReader((self.base/'data/crash_research/prospective_hourly.csv').open()))
  self.assertEqual(r[0]['alert'],'0')
  self.assertEqual(run(self.now,self.base)['registered_new'],0)
 def test_stale_hour_cannot_retroactively_register(self):
  t=self.now+timedelta(hours=3)
  x=run(t,self.base)
  self.assertEqual(x['status'],'source_stale_no_registration')
  self.assertEqual(x['registered_new'],0)
  self.assertFalse((self.base/'data/crash_research/prospective_hourly.csv').exists())
 def test_positive_candidate_not_historical_success(self):
  self.make_rows(45,True)
  x=run(self.now,self.base)
  self.assertEqual(x['status'],'ok')
  self.assertEqual(x['registered_new'],1)
  r=list(csv.DictReader((self.base/'data/crash_research/prospective_hourly.csv').open()))
  self.assertEqual(r[0]['alert'],'1')
  self.assertEqual(x['pending'][0]['reason'],'12h forward window not yet confirmed')
  self.assertEqual(x['scored'],[])
  self.assertFalse(x['passed'])
 def test_scores_only_after_full_horizon(self):
  run(self.now,self.base)
  self.make_rows(56)
  x=run(self.now,self.base)
  self.assertEqual(len(x['scored']),0) # last watch needs 12 new future closes
  self.make_rows(57)
  x=run(self.now,self.base)
  self.assertEqual(len(x['scored']),1)
  self.assertFalse(x['scored'][0]['realized_crash_next_12h'])
 def test_data_aligned(self):
  per,points=hour_rows(self.base/'data/hourly_research/ohlc.csv')
  self.assertEqual(len(points),45)
  self.assertFalse(feature(per,points,points[-1])['alert'])

if __name__=='__main__':unittest.main(verbosity=2)

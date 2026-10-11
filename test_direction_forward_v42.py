#!/usr/bin/env python3
"""Deterministic genuine issuance-clock DOGE directional prediction tests."""
import csv,hashlib,json,math,subprocess,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from direction_forward_v42 import run,read_rows,FCOLS,SCOLS,MODEL,HOUR

class ForwardDirectionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.root=Path(self.tmp.name)
  (self.root/'data/hourly_research').mkdir(parents=True)
  (self.root/'data/direction_v42').mkdir(parents=True)
  subprocess.run(['git','init','-q'],cwd=self.root,check=True)
  subprocess.run(['git','-c','user.name=QA','-c','user.email=qa@example.test',
                  'commit','--allow-empty','-qm','init'],cwd=self.root,check=True)
  self.end=datetime(2026,10,11,3,0,tzinfo=timezone.utc)
  self.start=self.end-timedelta(hours=1600)
  self.seed_hours(1600)
 def tearDown(self):self.tmp.cleanup()
 def seed_hours(self,n):
  end=self.start+timedelta(hours=n)
  with (self.root/'data/hourly_research/ohlc.csv').open('w',newline='') as f:
   w=csv.writer(f)
   w.writerow(['symbol','open_utc_ms','open','high','low','close','volume_base','first_ingested_utc','source'])
   for i in range(n):
    t=int((self.start+timedelta(hours=i)).timestamp()*1000)
    for s,k in [('DOGE-USDT',1),('BTC-USDT',100000),('ETH-USDT',5000)]:
     # must be positive but not monotonically rising; both direction classes exist
     p=k*(1+.00015*i+.012*math.sin(i/6))
     w.writerow([s,t,p,p*1.01,p*.99,p,10000,self.end.isoformat(),'synthetic'])
 def run_at(self,clock,price=0.11):
  quote=(price,clock-timedelta(seconds=6))
  return run(self.root,clock,quote)
 def load(self):
  return read_rows(self.root/'data/direction_v42/forward_registry.csv',FCOLS)
 def test_all_four_directions_at_midnight(self):
  # synthetic last bar is 02:00, so midnight-only registrations should not appear
  now=self.end+timedelta(minutes=13)
  x=self.run_at(now)
  self.assertEqual(x['status'],'ok')
  self.assertEqual(len(x['new_ids']),2)
  rows=self.load()
  self.assertEqual(set(int(v['horizon_hours']) for v in rows.values()),{1,24})
  for row in rows.values():
   self.assertEqual(row['issued_at_utc'],now.isoformat())
   self.assertEqual(row['reference_price'],'.11')
   self.assertEqual(row['model_id'],MODEL)
   self.assertEqual(row['target_utc'],(now+timedelta(hours=int(row['horizon_hours']))).isoformat())
 def test_genuine_full_one_hour_forward_and_no_double_register(self):
  now=self.end+timedelta(minutes=13)
  x=self.run_at(now)
  self.assertEqual(len(x['new_ids']),2)
  self.assertEqual(self.run_at(now+timedelta(minutes=5))['new_ids'],[])
  self.seed_hours(1601)
  later=now+timedelta(hours=1,minutes=1)
  y=self.run_at(later,0.12)
  self.assertEqual(y['scored_count'],1)
  rows=read_rows(self.root/'data/direction_v42/forward_scores.csv',SCOLS)
  row=next(x for x in rows.values() if x['status']=='scored')
  self.assertEqual(row['realized_up'],'UP')
  self.assertEqual(row['target_quote_utc'],(later-timedelta(seconds=6)).isoformat())
  self.assertEqual(y['pending_count'],3) # 24h old plus 2 newly issued
 def test_missed_target_does_not_fabricate_score(self):
  now=self.end+timedelta(minutes=13)
  self.run_at(now)
  self.seed_hours(1602)
  y=self.run_at(now+timedelta(hours=2),0.12)
  self.assertEqual(y['unscorable_count'],1)
  self.assertEqual(y['scored_count'],0)
  rows=read_rows(self.root/'data/direction_v42/forward_scores.csv',SCOLS)
  self.assertTrue(any(r['status']=='unscorable_missing_target_quote' for r in rows.values()))
 def test_stale_quote_fails_closed(self):
  now=self.end+timedelta(minutes=13)
  with self.assertRaisesRegex(ValueError,'stale/future'):
   run(self.root,now,(0.11,now-timedelta(minutes=5)))
  self.assertFalse((self.root/'data/direction_v42/forward_registry.csv').exists())
 def test_late_run_does_not_retroactively_issue(self):
  now=self.end+timedelta(minutes=53)
  y=self.run_at(now)
  self.assertEqual(y['status'],'stale_hour_no_new_registration')
  self.assertEqual(y['new_ids'],[])
  self.assertEqual(y['forecast_rows'],0)
 def test_daily_horizons_when_recent_closed_hour_is_utc_23(self):
  self.start-=timedelta(hours=3)
  self.end-=timedelta(hours=3)
  self.seed_hours(1600)
  now=self.end+timedelta(minutes=13)
  y=self.run_at(now)
  self.assertEqual(len(y['new_ids']),4)
  self.assertEqual(set(int(v['horizon_hours']) for v in self.load().values()),{1,24,168,720})
 def test_tampered_saved_forecast_blocked(self):
  now=self.end+timedelta(minutes=13)
  self.run_at(now)
  subprocess.run(['git','add','data/direction_v42/forward_registry.csv'],cwd=self.root,check=True)
  subprocess.run(['git','-c','user.name=QA','-c','user.email=qa@example.test','commit','-qm','save'],cwd=self.root,check=True)
  file=self.root/'data/direction_v42/forward_registry.csv'
  file.write_text(file.read_text().replace('0.11','0.99'))
  with self.assertRaisesRegex(ValueError,'immutable registry entry revised'):
   self.run_at(now+timedelta(minutes=1))
if __name__=='__main__':unittest.main(verbosity=2)

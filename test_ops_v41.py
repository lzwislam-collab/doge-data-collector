#!/usr/bin/env python3
"""Deterministic synthetic operational audit tests."""
import csv,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta,date
from audit_v41_operations import inspect,CHAPTERS
from forecast_v41 import MODEL,HORIZONS

class V41OperationalTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory()
  self.root=Path(self.temp.name)
  self.folder=self.root/'data/forecast_v41';self.folder.mkdir(parents=True)
  (self.root/'data/history').mkdir(parents=True)
  (self.root/'reports').mkdir()
  self.t=datetime(2026,10,12,1,40,tzinfo=timezone.utc)
  self.anchor='2026-10-11'
  self.ids=[f'{MODEL}_{self.anchor}_{h}d' for h in HORIZONS]
  self.rows=[dict(forecast_id=k,anchor_date_utc=self.anchor,
    registered_utc='2026-10-12T00:37:00+00:00',model_id=MODEL) for k in self.ids]
  self.write_rows()
  self.write('data/history/latest.json',{'status':'ok','quality':{'duplicate_keys':0,
    'symbols':{'DOGE-USDT':{'last_utc':self.anchor,'missing_days_between_endpoints':0}}}})
  self.write('data/forecast_v41/latest.json',{'status':'ok','anchor_date_utc':self.anchor})
  self.write('data/forecast_v41/scores.json',{'status':'ok','source_cutoff_utc':self.anchor,
   'registered':3,'scored':[],'pending':[{'forecast_id':k} for k in self.ids],'quarantined':[]})
  (self.root/'reports/DOGE_V41_LATEST.md').write_text(self.anchor+' NO_VALIDATED_EDGE\n'+'\n'.join(CHAPTERS))
 def tearDown(self):self.temp.cleanup()
 def write(self,path,obj):
  (self.root/path).write_text(json.dumps(obj))
 def write_rows(self):
  with (self.folder/'registry.csv').open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=['forecast_id','anchor_date_utc','registered_utc','model_id'])
   w.writeheader();w.writerows(self.rows)
 def test_valid_day(self):
  x=inspect(self.t,self.root)
  self.assertEqual(x['status'],'ok')
  self.assertEqual(x['registrations_checked'],3)
  self.assertEqual(x['report_chapters_found'],11)
 def test_missed_registration_grace_then_alarm(self):
  self.rows=self.rows[:2];self.write_rows()
  sc=json.loads((self.folder/'scores.json').read_text())
  sc['registered']=2;sc['pending']=sc['pending'][:2]
  self.write('data/forecast_v41/scores.json',sc)
  self.assertEqual(inspect(self.t,self.root)['status'],'registration_grace')
  later=datetime(2026,10,12,3,0,tzinfo=timezone.utc)
  r=inspect(later,self.root)
  self.assertEqual(r['status'],'blocked')
  self.assertIn('forecast not registered',str(r['issues']))
 def test_stale_daily_rejected(self):
  f=self.root/'data/history/latest.json'
  d=json.loads(f.read_text());d['quality']['symbols']['DOGE-USDT']['last_utc']='2026-10-10'
  self.write('data/history/latest.json',d)
  self.assertEqual(inspect(self.t,self.root)['status'],'blocked')
 def test_duplicate_registry_rejected(self):
  self.rows.append(self.rows[0].copy());self.write_rows()
  self.assertEqual(inspect(self.t,self.root)['status'],'blocked')
 def test_score_registration_mismatch_rejected(self):
  o=json.loads((self.folder/'scores.json').read_text());o['pending']=o['pending'][:1]
  self.write('data/forecast_v41/scores.json',o)
  self.assertEqual(inspect(self.t,self.root)['status'],'blocked')
 def test_registration_late_rejected(self):
  self.rows[0]['registered_utc']='2026-10-12T02:30:00+00:00';self.write_rows()
  r=inspect(self.t,self.root)
  self.assertEqual(r['status'],'blocked')
  self.assertIn('outside allowed',str(r['issues']))
 def test_no_silent_no_probability_claim(self):
  text=(self.root/'reports/DOGE_V41_LATEST.md').read_text()
  (self.root/'reports/DOGE_V41_LATEST.md').write_text(text.replace('NO_VALIDATED_EDGE','YES_VALIDATED_EDGE'))
  self.assertEqual(inspect(self.t,self.root)['status'],'blocked')

if __name__=='__main__':unittest.main(verbosity=2)

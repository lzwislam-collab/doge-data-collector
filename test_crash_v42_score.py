#!/usr/bin/env python3
"""Boundary and anti-hindsight regression for V4.2 event-start definition."""
import unittest
from datetime import datetime,timezone,timedelta
from crash_v42_score import target_window,scoring,ceil_hour
from crash_forward_watch import HOUR,MODEL,iso

BASE=datetime(2026,10,11,0,0,tzinfo=timezone.utc)
BASE_MS=int(BASE.timestamp()*1000)

def fixture(end_hour=16,crash_hours=(),issued_minute=13,alert='1',ingest_late=False):
 points=[BASE_MS+i*HOUR for i in range(end_hour+1)]
 per={s:{} for s in ('DOGE-USDT','BTC-USDT','ETH-USDT')}
 for i,t in enumerate(points):
  for s in per:
   c=.093 if (s=='DOGE-USDT' and i in crash_hours) else .1
   per[s][t]={'open':.1,'close':c,'low':min(c,.1),'volume':100.,'ingested':BASE}
 issued=BASE+timedelta(hours=2,minutes=issued_minute)
 row={'watch_id':'test','rule_id':MODEL,'issued_at_utc':issued.isoformat(),
  'signal_hour_start_utc':(BASE+timedelta(hours=1)).isoformat(),
  'available_after_utc':(BASE+timedelta(hours=2)).isoformat(),
  'source_first_ingested_utc':(issued+timedelta(minutes=1) if ingest_late else issued-timedelta(minutes=1)).isoformat(),
  'doge_hour_close':'.1','doge_hour_low_pct':'0','volume_multiple':'1',
  'relative_btc_pp':'0','alert':alert,'feature_sha256':'digest'}
 return {'test':row},per,points,issued

class TimingBoundaryQA(unittest.TestCase):
 def test_precise_issuance_excludes_already_started_candle(self):
  issued=BASE+timedelta(hours=2,minutes=13)
  targets,first,end=target_window(issued)
  self.assertEqual(first,BASE_MS+4*HOUR)
  self.assertEqual(end,BASE_MS+15*HOUR)
  self.assertEqual(len(targets),11)
  self.assertNotIn(BASE_MS+3*HOUR,targets)
  self.assertIn(BASE_MS+4*HOUR,targets)
  self.assertIn(BASE_MS+14*HOUR,targets)
  self.assertNotIn(BASE_MS+15*HOUR,targets)
 def test_early_first_target_not_counted(self):
  r,per,ts,now=fixture(crash_hours=(3,))
  x=scoring(r,per,ts,now)
  self.assertEqual(x['scored_count'],1)
  self.assertFalse(x['scored'][0]['realized_event'])
  self.assertEqual(x['false_alerts'],1)
 def test_first_eligible_crash_counted(self):
  r,per,ts,now=fixture(crash_hours=(4,))
  x=scoring(r,per,ts,now)
  self.assertTrue(x['scored'][0]['realized_event'])
  self.assertEqual(x['true_matched_alerts'],1)
  self.assertEqual(x['alert_precision_pct'],100.)
  self.assertIsNone(x['event_recall_pct']) # 11 hourly checkpoints not yet registered
 def test_final_window_boundary(self):
  r,per,ts,now=fixture(crash_hours=(14,15))
  x=scoring(r,per,ts,now)
  self.assertEqual(x['scored'][0]['event_starts_utc'],[iso(BASE_MS+14*HOUR)])
 def test_pending_until_last_target_candle_confirmed(self):
  r,per,ts,now=fixture(end_hour=13)
  x=scoring(r,per,ts,now)
  self.assertEqual(x['pending_count'],1)
  r,per,ts,now=fixture(end_hour=14)
  x=scoring(r,per,ts,now)
  self.assertEqual(x['scored_count'],1)
 def test_reject_source_captured_after_prediction(self):
  r,per,ts,now=fixture(ingest_late=True)
  x=scoring(r,per,ts,now)
  self.assertEqual(x['quarantined_count'],1)
 def test_no_alert_has_no_precision(self):
  r,per,ts,now=fixture(alert='0')
  x=scoring(r,per,ts,now)
  self.assertEqual(x['active_alerts'],0)
  self.assertIsNone(x['alert_precision_pct'])
 def test_late_registration_is_quarantined(self):
  r,per,ts,now=fixture(issued_minute=93)
  x=scoring(r,per,ts,now)
  self.assertEqual(x['quarantined_count'],1)

if __name__=='__main__':unittest.main(verbosity=2)

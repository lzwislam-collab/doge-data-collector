#!/usr/bin/env python3
"""Offline acceptance: new 15m quote sampler must obey frozen V4.2 target rules."""
import csv,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from spot_plus_quote_watch import run
from direction_forward_v42 import FCOLS,SCOLS,read_rows

def csvfile(path,columns,records):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(records)

class QuoteWatchTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory()
  self.root=Path(self.temp.name)
  self.start=datetime(2026,10,11,12,tzinfo=timezone.utc)
  self.target=self.start+timedelta(hours=1)
  self.now=self.target+timedelta(minutes=5,seconds=2)
  self.quoted=self.now-timedelta(seconds=2)
  def pred(fid,issued,target,h=1):
   return dict(forecast_id=fid,model_id='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',
    issued_at_utc=issued.isoformat(),source_quote_utc=issued.isoformat(),
    source_hour_start_utc=issued.isoformat(),reference_price='0.10',
    horizon_hours=str(h),target_utc=target.isoformat(),majority_p_up='0.4',
    majority_direction='DOWN',doge_reversal_direction='UP',
    btc_momentum_direction='UP',feature_sha256='testhash')
  self.valid=pred('target-due',self.start,self.target)
  self.future=pred('future',self.target,self.target+timedelta(hours=1))
  self.old=pred('already-scored',self.start,self.target)
  self.late=pred('late-too-old',self.start-timedelta(hours=1),
    self.target-timedelta(hours=1))
  self.original={'forecast_id':'already-scored','status':'scored',
    'observed_at_utc':self.target.isoformat(),
    'target_quote_utc':self.target.isoformat(),
    'target_price':'0.2','realized_up':'UP','majority_correct':'0',
    'doge_reversal_correct':'1','btc_momentum_correct':'1','reason':''}
  self.reg=[self.valid,self.future,self.old,self.late]
  self.scores=[self.original]
  self.save()

 def tearDown(self):self.temp.cleanup()

 def save(self):
  csvfile(self.root/'data/direction_v42/forward_registry.csv',FCOLS,self.reg)
  csvfile(self.root/'data/direction_v42/forward_scores.csv',SCOLS,self.scores)

 def execute(self,now=None,quoted=None,price=.09):
  return run(self.root,now or self.now,(price,quoted or self.quoted),verify_history=False)

 def test_scores_only_actual_target_quote_and_never_predicts_or_backfills(self):
  b=(self.root/'data/direction_v42/forward_registry.csv').read_bytes()
  out=self.execute()
  self.assertEqual(out['eligible_new_target_quotes_scored'],1)
  scores=read_rows(self.root/'data/direction_v42/forward_scores.csv',SCOLS)
  self.assertEqual(scores['target-due']['realized_up'],'DOWN')
  self.assertEqual(scores['target-due']['doge_reversal_correct'],'0')
  self.assertEqual(scores['target-due']['target_quote_utc'],self.quoted.isoformat())
  self.assertEqual(scores['already-scored'],self.original)
  self.assertNotIn('future',scores)
  self.assertNotIn('late-too-old',scores)
  self.assertEqual((self.root/'data/direction_v42/forward_registry.csv').read_bytes(),b)
  self.assertTrue(out['no_forecast_registered'])

 def test_repeat_does_not_rewrite_score(self):
  self.execute()
  before=(self.root/'data/direction_v42/forward_scores.csv').read_bytes()
  out=self.execute()
  self.assertEqual(out['eligible_new_target_quotes_scored'],0)
  self.assertEqual(before,(self.root/'data/direction_v42/forward_scores.csv').read_bytes())

 def test_quote_after_grace_is_not_hindsight_rescored(self):
  late=self.target+timedelta(minutes=26)
  out=self.execute(now=late,quoted=late-timedelta(seconds=1))
  scores=read_rows(self.root/'data/direction_v42/forward_scores.csv',SCOLS)
  self.assertEqual(out['eligible_new_target_quotes_scored'],0)
  self.assertNotIn('target-due',scores)

 def test_quote_issued_before_target_does_not_score(self):
  before=self.target-timedelta(seconds=2)
  out=self.execute(now=before+timedelta(seconds=1),quoted=before)
  self.assertEqual(out['eligible_new_target_quotes_scored'],0)

 def test_price_and_timestamp_must_be_genuine(self):
  with self.assertRaisesRegex(ValueError,'stale'):
   self.execute(quoted=self.now-timedelta(minutes=3))
  with self.assertRaisesRegex(ValueError,'invalid'):
   self.execute(price=-.02)
  scores=read_rows(self.root/'data/direction_v42/forward_scores.csv',SCOLS)
  self.assertNotIn('target-due',scores)

 def test_existing_unscorable_is_never_reclassified(self):
  self.scores.append({'forecast_id':'target-due','status':'unscorable_missing_target_quote',
   'observed_at_utc':self.target.isoformat(),'target_quote_utc':'',
   'target_price':'','realized_up':'','majority_correct':'','doge_reversal_correct':'',
   'btc_momentum_correct':'','reason':'late target quote'})
  self.save()
  self.assertEqual(self.execute()['eligible_new_target_quotes_scored'],0)
  scores=read_rows(self.root/'data/direction_v42/forward_scores.csv',SCOLS)
  self.assertEqual(scores['target-due']['status'],'unscorable_missing_target_quote')

if __name__=='__main__':
 unittest.main(verbosity=2)

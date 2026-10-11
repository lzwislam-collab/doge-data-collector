#!/usr/bin/env python3
"""Offline, deterministic tests: 1m close shadow never replaces issued-quote scores."""
import csv,json,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from direction_forward_v42 import FCOLS
from stage5_minute_shadow import evaluate,parse_bars,target_minute,MODEL,SCOLS

class MinuteShadowTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  (self.root/'data/direction_v42').mkdir(parents=True)
  (self.root/'data/stage5/minute_shadow').mkdir(parents=True)
  self.now=datetime(2026,10,12,4,45,tzinfo=timezone.utc)
  self.issued=datetime(2026,10,12,1,0,22,tzinfo=timezone.utc)
  self.target=self.issued+timedelta(hours=1)
  self.rows=[dict(zip(FCOLS,[
   'test1',MODEL,self.issued.isoformat(),
   (self.issued-timedelta(seconds=3)).isoformat(),
   '2026-10-12T01:00:00+00:00','0.1','1',self.target.isoformat(),
   '0.49','DOWN','UP','DOWN','digest']))]
  self.save(self.root/'data/direction_v42/forward_registry.csv',FCOLS,self.rows)
  self.original=self.root/'data/direction_v42/forward_scores.csv'
  self.original.write_text('keep original scores untouched\n')
  self.p=self.root/'data/stage5/minute_evaluation_protocol.json'
  self.p.write_text(json.dumps({'status':'frozen_protocol',
    'protocol_id':'DOGE_V42_1M_CLOSE_SHADOW_LOCK_20261012',
    'forecast_source_model_id':MODEL,'primary_horizons_hours':[1,24],
    'selection_rule':'first confirmed 1m bar with open >= registered target; use close at open+60s',
    'max_offset_after_target_seconds':120,'original_quote_scoring_untouched':True,
    'effective_from_utc':'2026-10-12T00:00:00+00:00'}))

 def tearDown(self):self.temp.cleanup()

 def save(self,path,headers,rows):
  path.parent.mkdir(parents=True,exist_ok=True)
  with path.open('w',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=headers);writer.writeheader()
   writer.writerows(rows)

 def test_first_minute_after_target_scores_without_rewriting_quote(self):
  expected=target_minute(self.target)
  self.assertEqual(datetime.fromtimestamp(expected/1000,timezone.utc).isoformat(),'2026-10-12T02:01:00+00:00')
  def loader(now,earliest):
   self.assertEqual(earliest,expected)
   return {expected:.11,expected+60_000:.05}
  summary,rows=evaluate(self.root,self.now,loader)
  self.assertEqual(summary['status'],'ok')
  self.assertEqual(summary['postfreeze_quote_coverage_pct'],100)
  self.assertEqual(summary['preregistered_forward_rows'],1)
  self.assertEqual(rows['test1']['realized_up'],'UP')
  self.assertEqual(rows['test1']['doge_reversal_correct'],'1')
  self.assertEqual(rows['test1']['bar_open_utc'],'2026-10-12T02:01:00+00:00')
  self.assertEqual(rows['test1']['bar_closed_at_utc'],'2026-10-12T02:02:00+00:00')
  self.assertEqual(rows['test1']['offset_seconds_after_target'],'98.000')
  self.assertEqual(self.original.read_text(),'keep original scores untouched\n')

 def test_missing_target_bar_is_not_replaced_by_later_bar(self):
  when=target_minute(self.target)
  report,rows=evaluate(self.root,self.now,lambda _now,_earliest:{when+60_000:.11})
  self.assertEqual(report['postfreeze_quote_coverage_pct'],0)
  self.assertEqual(report['postfreeze_scored_forecasts'],0)
  self.assertFalse(rows)

 def test_existing_shadow_score_does_not_change(self):
  old={'forecast_id':'test1','status':'scored_from_confirmed_1m_close',
   'evaluated_at_utc':self.now.isoformat(),'target_utc':self.target.isoformat(),
   'bar_open_utc':'2026-10-12T02:01:00+00:00','bar_closed_at_utc':'2026-10-12T02:02:00+00:00',
   'retrieved_at_utc':self.now.isoformat(),'minute_close_usdt':'0.11',
   'realized_up':'UP','majority_correct':'0','doge_reversal_correct':'1',
   'btc_momentum_correct':'0','offset_seconds_after_target':'98.000',
   'protocol_id':'DOGE_V42_1M_CLOSE_SHADOW_LOCK_20261012',
   'evaluation_cohort':'preregistered_forward_shadow','reason':'locked'}
  self.save(self.root/'data/stage5/minute_shadow/minute_scores.csv',SCOLS,[old])
  summary,rows=evaluate(self.root,self.now,lambda *_:self.fail('must not refetch'))
  self.assertEqual(summary['new_minute_scores'],0)
  self.assertEqual(rows['test1'],old)

 def test_prefreeze_predictions_diagnostic_only(self):
  self.rows[0]['issued_at_utc']='2026-10-11T01:00:22+00:00'
  self.rows[0]['source_quote_utc']='2026-10-11T01:00:19+00:00'
  self.rows[0]['target_utc']='2026-10-11T02:00:22+00:00'
  self.save(self.root/'data/direction_v42/forward_registry.csv',FCOLS,self.rows)
  clock=datetime(2026,10,11,2,15,tzinfo=timezone.utc)
  expected=target_minute(datetime(2026,10,11,2,0,22,tzinfo=timezone.utc))
  report,rows=evaluate(self.root,clock,lambda *_:{expected:.2})
  self.assertEqual(rows['test1']['evaluation_cohort'],'pre_freeze_diagnostic_only')
  self.assertEqual(report['preregistered_forward_rows'],0)

 def test_protocol_cannot_change_scoring_offset(self):
  p=json.loads(self.p.read_text());p['max_offset_after_target_seconds']=300
  self.p.write_text(json.dumps(p))
  with self.assertRaisesRegex(ValueError,'protocol changed'):
   evaluate(self.root,self.now,lambda *_:{})

 def test_duplicate_registered_forecast_rejected(self):
  self.save(self.root/'data/direction_v42/forward_registry.csv',FCOLS,self.rows+self.rows)
  with self.assertRaisesRegex(ValueError,'duplicate'):
   evaluate(self.root,self.now,lambda *_:{})

 def test_expired_target_is_unresolved_not_hidden(self):
  late=self.now+timedelta(days=2)
  report,rows=evaluate(self.root,late,lambda *_:self.fail('must not fetch ancient history'))
  self.assertEqual(report['postfreeze_due_forecasts'],1)
  self.assertEqual(report['postfreeze_quote_coverage_pct'],0)
  self.assertEqual(len(rows),0)

 def test_unconfirmed_minute_bar_not_considered(self):
  minute=target_minute(self.target)
  raw=[[str(minute),'0.1','0.12','0.08','0.11','123','0','0','0']]
  self.assertEqual(parse_bars(raw,self.now),{})

 def test_duplicate_conflicting_minute_close_rejected(self):
  minute=target_minute(self.target)
  mk=lambda c:[str(minute),'0.1','0.12','0.08',str(c),'123','0','0','1']
  with self.assertRaisesRegex(ValueError,'conflicting'):
   parse_bars([mk(.11),mk(.09)],self.now)

if __name__=='__main__':
 unittest.main(verbosity=2)

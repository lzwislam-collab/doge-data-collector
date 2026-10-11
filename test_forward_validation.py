import importlib
import tempfile
import unittest
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from direction_forward_v42 import FCOLS, SCOLS, serial

class ValidationTests(unittest.TestCase):
 def setUp(self):
  self.v=importlib.import_module('forward_validation')
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name)
  self.now=datetime(2026,10,12,0,tzinfo=timezone.utc)
 def tearDown(self):self.t.cleanup()
 def row(self,fid='one',h=24,at=None):
  at=at or self.now
  return dict(zip(FCOLS,[fid,'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',at.isoformat(),at.isoformat(),at.replace(minute=0).isoformat(),'1',str(h),(at+timedelta(hours=h)).isoformat(),'.5','UP','UP','DOWN','abc']))
 def score(self,row,price='1.1',delay=0):
  q=datetime.fromisoformat(row['target_utc'])+timedelta(minutes=delay)
  true='UP' if float(price)>float(row['reference_price']) else 'DOWN'
  flags=['1' if row[k]==true else '0' for k in ('majority_direction','doge_reversal_direction','btc_momentum_direction')]
  return dict(zip(SCOLS,[row['forecast_id'],'scored',q.isoformat(),q.isoformat(),price,true,*flags,'']))
 def test_late_or_wrong_labels_fail_closed(self):
  r=self.row(h=1)
  with self.assertRaisesRegex(ValueError,'window'):
   self.v.validate_scores({'one':r},{'one':self.score(r,delay=26)},self.now+timedelta(days=2))
  s=self.score(r);s['doge_reversal_correct']='0'
  with self.assertRaisesRegex(ValueError,'label'):
   self.v.validate_scores({'one':r},{'one':s},self.now+timedelta(days=2))
 def test_same_quote_baselines_and_missing_denominator(self):
  a=self.row(h=1);b=self.row('two',h=1,at=self.now+timedelta(hours=1))
  result=self.v.compare([a,b],{'one':self.score(a)},self.now+timedelta(hours=4))
  self.assertEqual(result['due'],2);self.assertEqual(result['quote_coverage_pct'],50)
  self.assertEqual(result['models']['always_up']['accuracy_pct'],100)
  self.assertEqual(result['models']['always_down']['accuracy_pct'],0)
  self.assertEqual(result['models']['btc_momentum']['accuracy_pct'],0)
  self.assertEqual(result['coverage_state'],'BELOW_90')
 def test_costs_apply_to_entry_and_exit(self):
  a=self.row();result=self.v.paper([a],{'one':self.score(a)},.01,.02)
  # Entry 1.02*1.01=1.0302, exit 1.1*.98*.99=1.06722.
  self.assertAlmostEqual(result['strategy_net_return_pct'],3.5934769947583)
  self.assertEqual(result['completed_intervals'],1)
  self.assertEqual(result['cash_net_return_pct'],0)
  self.assertFalse(result['economic_edge_validated'])
 def test_missing_outcome_blocks_later_profit_cherry_picking(self):
  a=self.row();b=self.row('two',at=self.now+timedelta(days=1,minutes=1))
  result=self.v.paper([a,b],{'two':self.score(b)},.001,.0005)
  self.assertEqual(result['completed_intervals'],0)
  self.assertIsNone(result['strategy_net_return_pct'])
  self.assertEqual(result['blocked_forecast_id'],'one')
 def test_down_stays_in_cash_and_hold_loses_after_cost(self):
  a=self.row();a['doge_reversal_direction']='DOWN'
  result=self.v.paper([a],{'one':self.score(a,price='.9')},.001,.0005)
  self.assertEqual(result['strategy_net_return_pct'],0)
  self.assertLess(result['buy_hold_net_return_pct'],-10)
 def test_trailing_missing_daily_issuance_blocks_accounting(self):
  a=self.row()
  rows=self.v.economic_cohort([a],self.now,self.now+timedelta(days=4))
  result=self.v.paper(rows,{'one':self.score(a)},.001,.0005)
  self.assertEqual(result['completed_intervals'],1)
  self.assertEqual(result['blocked_forecast_id'],'MISSING_ISSUANCE_2026-10-13')
  self.assertFalse(result['all_selected_intervals_complete'])
 def test_overlapping_predictions_only_count_descriptively(self):
  a=self.row();b=self.row('two',at=self.now+timedelta(hours=23,minutes=50))
  c=self.row('three',at=self.now+timedelta(days=2))
  r=self.v.compare([a,b,c],{x['forecast_id']:self.score(x) for x in (a,b,c)},self.now+timedelta(days=4))
  self.assertEqual(r['scored'],3)
  self.assertEqual(r['nonoverlap']['scored'],2)
  self.assertEqual(r['nonoverlap']['selected_ids'],['one','three'])
 def test_complete_history_detects_mutation_older_than_ten_revisions(self):
  subprocess.run(['git','init','-q'],cwd=self.root,check=True)
  path=self.root/'registry.csv';a=self.row();rows={'one':a}
  def commit():
   path.write_text(serial(rows,FCOLS));subprocess.run(['git','add','.'],cwd=self.root,check=True)
   subprocess.run(['git','-c','user.name=QA','-c','user.email=qa@example.test','commit','-qm','save'],cwd=self.root,check=True)
  commit();a['reference_price']='2';commit()
  for i in range(11):rows[str(i)]=self.row(str(i));commit()
  with self.assertRaisesRegex(ValueError,'revised'):
   self.v.history_receipts(self.root,'registry.csv',FCOLS,rows)
 def test_remote_run_must_contain_same_forecast_before_target(self):
  subprocess.run(['git','init','-q'],cwd=self.root,check=True)
  a=self.row(h=1);path=self.root/self.v.REG;path.parent.mkdir(parents=True)
  path.write_text(serial({'one':a},FCOLS))
  subprocess.run(['git','add','.'],cwd=self.root,check=True)
  subprocess.run(['git','-c','user.name=QA','-c','user.email=qa@example.test','commit','-qm','save'],cwd=self.root,check=True)
  head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.root,text=True).strip()
  runs=[{'id':10,'head_sha':head,'created_at':(self.now+timedelta(hours=1,minutes=1)).isoformat()}]
  self.assertEqual(self.v.remote_witnesses(self.root,{'one':a},{},runs),{})
  runs.append({'id':11,'head_sha':head,'created_at':(self.now+timedelta(minutes=20)).isoformat()})
  self.assertEqual(self.v.remote_witnesses(self.root,{'one':a},{},runs)['one']['run_id'],'11')
  witness={'forecast_id':'one','run_id':'11','head_sha':head,
   'observed_public_utc':(self.now+timedelta(minutes=30)).isoformat(),
   'run_url':'https://github.com/lzwislam-collab/doge-data-collector/actions/runs/11'}
  with self.assertRaisesRegex(ValueError,'does not match'):
   self.v.remote_witnesses(self.root,{'one':a},{'one':witness},runs)

if __name__=='__main__':unittest.main()

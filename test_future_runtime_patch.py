import unittest,tempfile,json,copy,csv,io,contextlib
from pathlib import Path
from unittest.mock import patch
import okx_hourly_research_history as collector
import importlib.util,types,sys
try:
 import prospective_witness_budget as budget
except ModuleNotFoundError:
 budget=None

H=collector.HOUR
def candle(t,close='2'):
 return [str(t),'2','3','1',close,'10','20','20','1']
def existing(last):
 return {(s,t):collector.parse(s,candle(t),'2026-10-18T04:00:01+00:00',last+H) for s in collector.SYMBOLS for t in [last-H,last]}

class Refresh(unittest.TestCase):
 def test_current_snapshot_does_not_fetch(self):
  old=existing(3*H);fetch=lambda s:self.fail('unneeded network')
  new,n,at=collector.publication_refresh(old,lambda:4*H+1000,fetch)
  self.assertEqual(new,old);self.assertEqual(n,0)
 def test_cross_hour_adds_only_closed_bars_preserving_receipts(self):
  old=existing(2*H);before=copy.deepcopy(old);calls=[]
  def fetch(s):calls.append(s);return [candle(3*H),candle(2*H),candle(4*H)]
  new,n,_=collector.publication_refresh(old,lambda:4*H+1000,fetch)
  self.assertEqual(old,before);self.assertEqual(n,3);self.assertEqual(calls,list(collector.SYMBOLS))
  for k,v in before.items():self.assertEqual(new[k],v)
  self.assertTrue(all(t<4*H for s,t in new))
 def test_second_boundary_fails_without_unbounded_retry(self):
  old=existing(2*H);before=copy.deepcopy(old);ticks=iter([4*H+1,4*H+2,4*H+3,4*H+4,5*H+1])
  with self.assertRaisesRegex(ValueError,'publication source stale'):
   collector.publication_refresh(old,lambda:next(ticks),lambda s:[candle(3*H)])
  self.assertEqual(old,before)
 def test_conflict_never_overwrites_original(self):
  old=existing(2*H);before=copy.deepcopy(old)
  with self.assertRaises(ValueError):collector.publication_refresh(old,lambda:4*H+1,lambda s:[candle(3*H),candle(2*H,'2.1')])
  self.assertEqual(old,before)
 def test_partial_provider_failure_does_not_publish_subset(self):
  old=existing(2*H);before=copy.deepcopy(old)
  def fetch(s):
   if s=='BTC-USDT':raise TimeoutError('offline simulated timeout')
   return [candle(3*H)]
  with self.assertRaises(TimeoutError):collector.publication_refresh(old,lambda:4*H+1,fetch)
  self.assertEqual(old,before)
 def test_unconfirmed_latest_rejected(self):
  old=existing(2*H);r=candle(3*H);r[8]='0'
  with self.assertRaisesRegex(ValueError,'publication source stale'):collector.publication_refresh(old,lambda:4*H+1,lambda s:[r])
 def test_run_failure_keeps_existing_csv_bytes(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);data=root/'ohlc.csv';data.write_bytes(b'old immutable test bytes\n')
   with patch.object(collector,'BASE',root),patch.object(collector,'DATA',data),patch.object(collector,'REFRESH_RECEIPTS',root/'receipts'),patch.object(collector,'read_old',return_value=existing(2*H)),patch.object(collector,'publication_refresh',side_effect=TimeoutError('test')),patch.dict('os.environ',{'DOGE_HOURLY_PUBLICATION_REFRESH':'1'}):
    self.assertEqual(collector.run(),1)
   self.assertEqual(data.read_bytes(),b'old immutable test bytes\n')
   self.assertEqual(json.loads((root/'latest.json').read_text())['status'],'error')
   receipts=list((root/'receipts').glob('*.json'));self.assertEqual(len(receipts),1)
   self.assertIn('TimeoutError',json.loads(receipts[0].read_text())['errors'][0])
 def test_failure_receipt_cannot_overwrite_previous_attempt(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(collector,'REFRESH_RECEIPTS',Path(tmp)):
   a=collector.save_refresh_receipt({'status':'error','errors':['first']})
   old=a.read_bytes();b=collector.save_refresh_receipt({'status':'ok','errors':[]})
   self.assertNotEqual(a,b);self.assertEqual(a.read_bytes(),old)
 def test_clock_crossing_during_atomic_write_reports_failure(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);data=root/'ohlc.csv';now=[4*H+1];rows=existing(3*H)
   def atomic(path,text):
    path.write_text(text,encoding='utf-8')
    if path==data:now[0]=5*H+1
   with patch.object(collector,'BASE',root),patch.object(collector,'DATA',data),patch.object(collector,'REFRESH_RECEIPTS',root/'receipts'),patch.object(collector,'read_old',return_value=rows),patch.object(collector,'publication_refresh',return_value=(rows,0,4*H+1)),patch.object(collector,'atomic',side_effect=atomic),patch.object(collector.time,'time',side_effect=lambda:now[0]/1000),patch.dict('os.environ',{'DOGE_HOURLY_PUBLICATION_REFRESH':'1'}),contextlib.redirect_stdout(io.StringIO()):
    self.assertEqual(collector.run(),1)
   self.assertIn('during data write',json.loads((root/'latest.json').read_text())['errors'][0])
   self.assertEqual(len(list((root/'receipts').glob('*.json'))),1)
 def test_real_file_success_preserves_old_rows_and_uses_bounded_requests(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);data=root/'ohlc.csv';old=existing(2*H)
   for row in old.values():row['first_ingested_utc']=collector.iso(3*H+1)
   with data.open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=collector.COLUMNS);w.writeheader();w.writerows(old.values())
   with patch.object(collector,'BASE',root),patch.object(collector,'DATA',data),patch.object(collector,'REFRESH_RECEIPTS',root/'receipts'),patch.object(collector.time,'time',return_value=(4*H+1000)/1000),patch.object(collector,'request',return_value=[candle(3*H),candle(2*H)]) as request,patch.dict('os.environ',{'DOGE_HOURLY_PUBLICATION_REFRESH':'1'}),contextlib.redirect_stdout(io.StringIO()):
    self.assertEqual(collector.run(),0)
   self.assertEqual(request.call_count,3)
   for c in request.call_args_list:self.assertEqual(c.kwargs,{'attempts':1,'timeout':8})
   with data.open(newline='',encoding='utf-8') as f:rows=list(csv.DictReader(f))
   self.assertEqual(len(rows),9);indexed={(r['symbol'],int(r['open_utc_ms'])):r for r in rows}
   for k,v in old.items():self.assertEqual(indexed[k],v)

class Witness(unittest.TestCase):
 def test_publication_preparation_crosses_hour_rejected_without_fetch(self):
  rows=existing(2*H);buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=collector.COLUMNS);w.writeheader();w.writerows(rows.values())
  with self.assertRaisesRegex(ValueError,'stale'):
   budget.assert_source_fresh(buf.getvalue().encode(),4*H+1)
 def test_near_boundary_publication_budget_fails_closed(self):
  rows=existing(3*H);buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=collector.COLUMNS);w.writeheader();w.writerows(rows.values())
  with self.assertRaisesRegex(ValueError,'reserve'):
   budget.assert_source_fresh(buf.getvalue().encode(),5*H-1000)
 def test_actual_fresh_publication_start_passes(self):
  rows=existing(3*H);buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=collector.COLUMNS);w.writeheader();w.writerows(rows.values())
  self.assertIsNone(budget.assert_source_fresh(buf.getvalue().encode(),4*H+1))
 def test_actual_late_witness_remains_failed(self):
  r=budget.assess('2026-10-17T21:56:03.375907Z','2026-10-17T21:57:01.818198Z',90)
  self.assertFalse(r['budget_sufficient']);self.assertAlmostEqual(r['seconds_remaining'],58.442291)
  self.assertFalse(budget.exact_witness('a','a','2026-10-17T21:57:27Z','2026-10-17T21:57:01.818198Z'))
 def test_exact_deadline_is_rejected(self):
  self.assertFalse(budget.exact_witness('a','a','2026-10-17T21:57:01Z','2026-10-17T21:57:01Z'))
 def test_wrong_bytes_rejected_even_when_early(self):
  self.assertFalse(budget.exact_witness('a','b','2026-10-17T21:56:01Z','2026-10-17T21:57:01Z'))
 def test_early_exact_witness(self):
  self.assertTrue(budget.exact_witness('a','a','2026-10-17T21:56:01Z','2026-10-17T21:57:01Z'))
 def test_budget_is_diagnostic_not_authorization(self):
  r=budget.assess('2026-10-17T21:50:00Z','2026-10-17T21:57:00Z',90)
  self.assertTrue(r['budget_sufficient']);self.assertFalse(r['publication_verified']);self.assertFalse(r['issuance_authorized'])

class PublicationIntegration(unittest.TestCase):
 def test_preparation_crossing_skips_source_but_preserves_original_publication(self):
  self.check_publication(stale=True)
 def test_fresh_source_is_staged_and_exact_remote_bytes_are_checked(self):
  self.check_publication(stale=False)
 def test_source_remote_readback_mismatch_never_claims_verified(self):
  self.check_publication(stale=False,remote_bad=True)
 def test_refresh_failure_keeps_original_outputs_and_skips_source(self):
  self.check_publication(stale=False,refresh_failed=True)
 def test_receipt_disk_failure_still_preserves_core_publication(self):
  self.check_publication(stale=True,receipt_bad=True)
 def test_original_publisher_failure_is_not_swallowed(self):
  self.check_publication(stale=True,core_bad=True)
 def test_original_registry_failure_is_not_swallowed(self):
  self.check_publication(stale=True,registry_bad=True)
 def check_publication(self,stale,remote_bad=False,refresh_failed=False,receipt_bad=False,core_bad=False,registry_bad=False):
  commands=[];fake=types.SimpleNamespace(PATHS=('registry.csv',))
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);source=root/'data/hourly_research/ohlc.csv';source.parent.mkdir(parents=True)
   b=io.StringIO();w=csv.DictWriter(b,fieldnames=collector.COLUMNS);w.writeheader();w.writerows(existing(2*H if stale else 3*H).values());source.write_bytes(b.getvalue().encode())
   (root/'registry.csv').write_bytes(b'forecast_id\nnew-id\n')
   output=root/'output.txt'
   def command(root,*args):
    commands.append(args)
    if args[0]=='ls-tree':return b'registry.csv\n'
    if args[0]=='show':return (b'wrong remote bytes' if remote_bad else source.read_bytes()) if args[1].endswith('ohlc.csv') else b'forecast_id\n'
    return b''
   fake.command=command
   spec=importlib.util.spec_from_file_location('recovery_candidate',Path(__file__).with_name('publication_recovery.py'));module=importlib.util.module_from_spec(spec)
   with patch.dict(sys.modules,{'publish_forward_evidence':fake}):spec.loader.exec_module(module)
   called=[]
   def publish(root):
    called.append(True)
    if core_bad:raise ValueError('core failed')
    return {'status':'verified_remote_readback'}
   original_command=fake.command
   def remote_registry(root,*args):
    if args==('show','FETCH_HEAD:registry.csv') and called and not registry_bad:return b'forecast_id\nnew-id\n'
    return original_command(root,*args)
   fake.command=remote_registry
   with patch.object(module.time,'time',return_value=(4*H+1)/1000),patch.object(collector,'REFRESH_RECEIPTS',root/'receipts'),patch.dict('os.environ',{'DOGE_VERIFY_HOURLY_PUBLICATION':'1','DOGE_HOURLY_REFRESH_FAILED':str(int(refresh_failed)),'GITHUB_OUTPUT':str(output)}),patch.object(collector,'save_refresh_receipt',side_effect=OSError('disk full') if receipt_bad else collector.save_refresh_receipt):
    if core_bad or registry_bad:
     with self.assertRaisesRegex(ValueError,'core failed' if core_bad else 'missing remotely'):module.publish(root,publish)
     self.assertFalse(output.exists());return
    r=module.publish(root,publish)
    self.assertEqual(called,[True]);self.assertEqual(r['status'],'verified_remote_readback')
    self.assertTrue(r['new_predictions']);self.assertIn('new_predictions=true',output.read_text())
    if stale or remote_bad or refresh_failed:
     self.assertFalse(r['hourly_source_public_readback'])
     self.assertEqual(r['hourly_source_status'],'error')
     self.assertIn('hourly_source_failed=true',output.read_text())
     self.assertEqual(len(list((root/'receipts').glob('*.json'))),0 if receipt_bad else 1)
     if receipt_bad:self.assertIn('disk full',r['hourly_source_failure']['receipt_error'])
    else:self.assertTrue(r['hourly_source_public_readback'])
    self.assertEqual(('add','-f','--','data/hourly_research/ohlc.csv') in commands,not (stale or refresh_failed))
    if stale or refresh_failed:self.assertIn(('reset','HEAD','--','data/hourly_research/ohlc.csv'),commands)

if __name__=='__main__':unittest.main()

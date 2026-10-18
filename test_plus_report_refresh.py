"""Offline behavioral checks: no real requests, sleep, collection or scoring."""
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from datetime import datetime, timezone, timedelta
try:
 import plus_report_refresh as r
except ModuleNotFoundError:
 r=None

NOW=datetime(2026,10,18,9,30,tzinfo=timezone.utc)
HEAD='a'*40
class Clock:
 def __init__(self):self.t=0
 def mono(self):return self.t
 def sleep(self,n):self.t+=n

class RefreshTests(unittest.TestCase):
 def setUp(self):
  self.assertIsNotNone(r,'future report refresh gate is missing')
  self.clock=Clock()
  self.watch=[{'track':'original18','event_id':'1792310400_1','target':'2026-10-18T09:29:06Z','deadline':'2026-10-18T09:54:06Z','event_raw':b'{"event_id":"1792310400_1"}'}]
 def gate(self,probe,budget=30):
  return r.wait_for_witness(self.watch,probe,budget=budget,interval=10,monotonic=self.clock.mono,sleep=self.clock.sleep)
 def test_projection_released_only_after_witness_completion(self):
  states=iter([{'head_sha':HEAD,'pending':['1792310400_1']},{'head_sha':'b'*40,'pending':[]}])
  out=self.gate(lambda w,t:next(states))
  self.assertEqual((out['status'],out['head_sha'],out['attempts'],self.clock.t),('READY','b'*40,2,10))
 def test_timeout_keeps_latest_pinned_head_and_pending_diagnostic(self):
  out=self.gate(lambda w,t:{'head_sha':HEAD,'pending':['1792310400_1']})
  self.assertEqual(out['status'],'TIMEOUT')
  self.assertEqual(out['pending'],['1792310400_1'])
  self.assertEqual(out['head_sha'],HEAD)
  self.assertEqual(self.clock.t,30)
 def test_read_budget_exhaustion_is_timeout_and_keeps_last_complete_snapshot(self):
  calls=[0]
  def probe(w,t):
   calls[0]+=1
   if calls[0]>1:raise TimeoutError('read budget exhausted')
   return {'head_sha':HEAD,'pending':['1792310400_1']}
  out=self.gate(probe)
  self.assertEqual(out['status'],'TIMEOUT')
  self.assertEqual(out['head_sha'],HEAD)
 def test_remote_failure_is_retained_without_claiming_ready(self):
  def unavailable(w,t):raise PermissionError('403')
  out=self.gate(unavailable)
  self.assertEqual(out['status'],'ERROR')
  self.assertEqual(out['error_type'],'PermissionError')
  self.assertNotIn('head_sha',out)
 def test_error_after_previous_snapshot_does_not_emit_stale_checkout_ref(self):
  responses=iter([{'head_sha':HEAD,'pending':['x']},PermissionError('denied')])
  def probe(w,t):
   value=next(responses)
   if isinstance(value,Exception):raise value
   return value
  out=self.gate(probe)
  self.assertEqual(out['status'],'ERROR')
  self.assertNotIn('head_sha',out)
 def test_main_retains_failed_gate_diagnostic_and_exits_nonzero(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'refresh.json'
   with patch('sys.argv',['plus_report_refresh.py','--out',str(path)]),patch.object(r,'watch_targets',side_effect=ValueError('invalid local journal')):
    self.assertEqual(r.main(),1)
   self.assertEqual(json.loads(path.read_bytes())['status'],'ERROR')
 def test_abrupt_interruption_retains_started_diagnostic(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'refresh.json'
   with patch('sys.argv',['plus_report_refresh.py','--out',str(path)]),patch.object(r,'watch_targets',side_effect=KeyboardInterrupt):
    with self.assertRaises(KeyboardInterrupt):r.main()
   self.assertTrue(path.exists(),'diagnostic must exist before waiting starts')
   self.assertEqual(json.loads(path.read_bytes())['status'],'STARTED')
 def test_failure_diagnostics_are_written_even_without_projection(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'refresh.json'; r.save_result(p,{'status':'ERROR','pending':['x']})
   self.assertEqual(json.loads(p.read_bytes()),{'status':'ERROR','pending':['x']})
 def test_empty_watch_does_not_make_network_request(self):
  def forbidden(*args):self.fail('unnecessary network call')
  out=r.wait_for_witness([],forbidden)
  self.assertEqual(out['status'],'NO_PENDING_TARGET')
 def test_slow_probe_cannot_release_after_budget(self):
  def slow(w,t):self.clock.t+=31;return {'head_sha':HEAD,'pending':[]}
  self.assertEqual(self.gate(slow)['status'],'TIMEOUT')
 def test_invalid_remote_commit_is_not_checkout_output(self):
  out=self.gate(lambda w,t:{'head_sha':'main;bad','pending':[]})
  self.assertEqual(out['status'],'ERROR')
  self.assertNotIn('head_sha',out)
 def test_reader_uses_only_get_and_preserves_permission_failure(self):
  from urllib.error import HTTPError
  methods=[]
  def denied(req,timeout):
   methods.append(req.get_method())
   raise HTTPError(req.full_url,403,'Forbidden',{},None)
  with patch.dict('os.environ',{'GITHUB_TOKEN':'offline-test-token'}),patch.object(r,'urlopen',side_effect=denied):
   reader=r.Reader(30)
   out=self.gate(reader.probe)
  self.assertEqual(methods,['GET'])
  self.assertEqual(out['error_type'],'HTTPError')
 def witness(self,**updates):
  value={'id':1,'repository':{'full_name':'lzwislam-collab/doge-data-collector'},'head_branch':'main','path':'.github/workflows/learned_shadow_v1.yml','event':'workflow_dispatch','status':'completed','conclusion':'success','created_at':'2026-10-18T09:31:18Z','updated_at':'2026-10-18T09:32:43Z','head_sha':HEAD}
  value.update(updates);return value
 def test_successful_exact_public_target_unblocks(self):
  blobs={'data/learned_shadow_v1/events/1792310400_1.json':self.watch[0]['event_raw'],'data/learned_shadow_v1/scores/1792310400_1.json':b'score'}
  self.assertTrue(r.target_witnessed(self.watch[0],b'score',[self.witness()],lambda h,p:blobs[p]))
 def test_failed_late_wrong_repo_branch_or_bytes_never_unblock(self):
  variants=[{'conclusion':'failure'},{'status':'in_progress'},{'updated_at':'2026-10-18T09:54:06Z'},{'created_at':'2026-10-18T09:28:00Z'},{'repository':{'full_name':'other/repo'}},{'head_branch':'feature/x'},{'path':'.github/workflows/plus_shadow.yml'},{'event':'pull_request'}]
  for change in variants:
   with self.subTest(change=change):
    self.assertFalse(r.target_witnessed(self.watch[0],b'score',[self.witness(**change)],lambda h,p:self.watch[0]['event_raw'] if '/events/' in p else b'score'))
  self.assertFalse(r.target_witnessed(self.watch[0],b'score',[self.witness()],lambda h,p:b'changed'))
 def test_closed_history_and_far_future_are_not_wait_targets(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); path=root/'data/learned_shadow_v1/events';path.mkdir(parents=True)
   (root/'config').mkdir(); (root/'config/learned_shadow_v1.json').write_text('{"target_window_minutes":25}')
   for key,target in [('old','2026-10-18T08:00:00Z'),('near','2026-10-18T09:31:00Z'),('far','2026-10-19T09:00:00Z')]:
    (path/(key+'.json')).write_text(json.dumps({'event_id':key,'target_utc':target}))
   self.assertEqual([w['event_id'] for w in r.watch_targets(root,NOW,300)],['near'])

if __name__=='__main__':unittest.main()

"""Offline real-Git regressions for the existing custody job's snapshot refresh."""
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import tempfile
import time
import unittest
from unittest import mock

import forecast_custody_refresh as refresh

class CustodyRefreshTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.remote=self.root/'remote';self.work=self.root/'work'
  self.remote.mkdir();self.git(self.remote,'init','-q','-b','main')
  self.git(self.remote,'config','user.name','Offline fixture');self.git(self.remote,'config','user.email','offline@example.invalid')
  self.write('forecast_custody_refresh.py',Path(refresh.__file__).read_text())
  self.write('runtime_process.py',Path(refresh.runtime_process.__file__).read_text())
  self.write('forecast_worm.py','original code\n');self.old=self.commit('initial snapshot')
  self.git(self.root,'clone','--quiet','--no-local','--depth','1',str(self.remote),str(self.work))
 def git(self,root,*args):
  return subprocess.check_output(['git',*args],cwd=root,stderr=subprocess.PIPE,text=True).strip()
 def write(self,path,text):
  target=self.remote/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
 def commit(self,message):
  self.git(self.remote,'add','.');self.git(self.remote,'commit','-qm',message);return self.git(self.remote,'rev-parse','HEAD')
 def advance(self):
  self.write('data/phase2_forward_v2/events/1794204000_formal.json','{"event_id":"1794204000_formal"}\n')
  self.write('forecast_worm.py','updated code in same trusted main snapshot\n')
  return self.commit('append genuine new event before custody')
 def test_stale_shallow_checkout_refreshes_once_before_source_commit_pin(self):
  new=self.advance();event=self.work/'data/phase2_forward_v2/events/1794204000_formal.json'
  self.assertFalse(event.exists());self.assertEqual(self.git(self.work,'rev-parse','HEAD'),self.old)
  result=refresh.refresh(self.work)
  self.assertEqual(result,{'status':'TRUSTED_MAIN_FAST_FORWARD_VERIFIED','before_commit':self.old,'source_commit':new})
  self.assertEqual(self.git(self.work,'rev-parse','HEAD'),new);self.assertTrue(event.exists())
  self.assertEqual((self.work/'forecast_worm.py').read_text(),'updated code in same trusted main snapshot\n')
  self.assertEqual(self.git(self.work,'status','--porcelain'),'')
 def test_no_change_still_proves_exact_current_head(self):
  self.assertEqual(refresh.refresh(self.work)['source_commit'],self.old)
 def test_dirty_tracked_or_untracked_checkout_fails_before_fetch(self):
  for path in ('forecast_worm.py','new-local.txt'):
   with self.subTest(path=path):
    target=self.work/path;original=target.read_bytes() if target.exists() else None;target.write_text('local changes')
    try:
     with mock.patch.object(refresh,'fetch_main') as fetch:
      with self.assertRaisesRegex(ValueError,'clean'):refresh.refresh(self.work)
      fetch.assert_not_called()
     self.assertEqual(self.git(self.work,'rev-parse','HEAD'),self.old)
    finally:
     if original is None:target.unlink()
     else:target.write_bytes(original)
 def test_divergent_main_fails_without_reset_rebase_or_merge_commit(self):
  self.advance();self.git(self.work,'config','user.name','Local fixture');self.git(self.work,'config','user.email','offline@example.invalid')
  (self.work/'local.txt').write_text('independent local commit');self.git(self.work,'add','.');self.git(self.work,'commit','-qm','divergent local head')
  local=self.git(self.work,'rev-parse','HEAD')
  with self.assertRaisesRegex((ValueError,subprocess.CalledProcessError),'fast.forward|ancestor|returned non-zero'):refresh.refresh(self.work)
  self.assertEqual(self.git(self.work,'rev-parse','HEAD'),local)
 def test_fetch_failure_cannot_proceed_to_custody_head(self):
  with mock.patch.object(refresh,'fetch_main',side_effect=OSError('fetch unavailable')):
   with self.assertRaisesRegex(OSError,'fetch unavailable'):refresh.refresh(self.work)
  self.assertEqual(self.git(self.work,'rev-parse','HEAD'),self.old)
 def test_fetch_failure_or_timeout_is_not_retried(self):
  with mock.patch.object(refresh,'fetch_main',side_effect=subprocess.TimeoutExpired('git fetch',45)) as fetch:
   with self.assertRaises(subprocess.TimeoutExpired):refresh.refresh(self.work)
   self.assertEqual(fetch.call_count,1)
  self.assertEqual(self.git(self.work,'rev-parse','HEAD'),self.old)
 def test_rewritten_remote_main_is_rejected_without_changing_local_history(self):
  self.git(self.remote,'checkout','--orphan','rewritten')
  self.write('replacement.txt','rewritten history');self.commit('unrelated trusted-main rewrite')
  self.git(self.remote,'branch','-M','main')
  with self.assertRaises(subprocess.CalledProcessError):refresh.refresh(self.work)
  self.assertEqual(self.git(self.work,'rev-parse','HEAD'),self.old)
 def test_dirty_change_during_fetch_never_fast_forwards(self):
  new=self.advance();actual=refresh.fetch_main
  def fetched(root,deadline):
   target=actual(root,deadline);(self.work/'local.txt').write_text('arrived during fetch');return target
  with mock.patch.object(refresh,'fetch_main',side_effect=fetched):
   with self.assertRaisesRegex(ValueError,'clean'):refresh.refresh(self.work)
  self.assertEqual(self.git(self.work,'rev-parse','HEAD'),self.old)
 def test_origin_identity_is_checked_before_fetch(self):
  with mock.patch.object(refresh,'fetch_main') as fetch:
   with self.assertRaisesRegex(ValueError,'origin identity'):refresh.refresh(self.work,expected_origin={'https://github.com/incorrect/repo'})
   fetch.assert_not_called()
  self.assertEqual(self.git(self.work,'rev-parse','HEAD'),self.old)
 def test_timeout_is_bounded_and_cannot_advance_head(self):
  with self.assertRaisesRegex(ValueError,'budget'):refresh.refresh(self.work,timeout_seconds=0)
  self.assertEqual(self.git(self.work,'rev-parse','HEAD'),self.old)
 def test_workflow_refreshes_inside_custody_step_before_pinning_and_oss(self):
  flow=Path(__file__).with_name('.github').joinpath('workflows/oss_inventory_retention.yml').read_text()
  step=flow.split('      - name: Explicit extension and pre-target exact-byte pinned readback',1)[1].split('      - name: Publish only new per-event receipt files',1)[0]
  self.assertIn('SOURCE_COMMIT="$(python -B forecast_custody_refresh.py)"',step)
  self.assertLess(step.index('python -B forecast_custody_refresh.py'),step.index('export SOURCE_COMMIT'))
  self.assertLess(step.index('export SOURCE_COMMIT'),step.index('test "$(git rev-parse HEAD)" = "$SOURCE_COMMIT"'))
  self.assertLess(step.index('test "$(git rev-parse HEAD)"'),step.index('python forecast_worm.py'))
  self.assertIn('    timeout-minutes: 8',flow.split('  forecast-retain:',1)[1]);self.assertNotIn('git reset',step);self.assertNotIn('git rebase',step)
 def test_actual_cli_with_bytecode_disabled_returns_verified_pin_without_own_dirty_cache(self):
  new=self.advance()
  # Only expected-origin authority is adapted to this offline fixture; the real
  # CLI main, branch/clean checks, Git operations and output path all execute.
  code=("import forecast_custody_refresh as f; original=f.refresh; "
        "f.refresh=lambda **kwargs:original(expected_origin={"+repr(str(self.remote))+"}); "
        "raise SystemExit(f.main())")
  env={**os.environ,'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':refresh.REPO}
  result=subprocess.run([sys.executable,'-B','-c',code],cwd=self.work,env=env,
   capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,0,result.stderr+result.stdout)
  self.assertEqual(result.stdout.strip(),new)
  self.assertEqual(self.git(self.work,'rev-parse','HEAD'),new)
  self.assertFalse((self.work/'__pycache__').exists())
  self.assertEqual(__import__('json').loads((self.work/'forecast-worm-output/main_refresh.json').read_text())['source_commit'],new)
 def test_git_delegates_remaining_budget_to_existing_owned_process_runner(self):
  with mock.patch.object(refresh.runtime_process,'command',return_value='verified stdout\n') as command:
   result=refresh.git(self.work,refresh.time.monotonic()+10,'rev-parse','HEAD')
  self.assertEqual(result,'verified stdout');self.assertEqual(command.call_count,1)
  self.assertGreater(command.call_args.kwargs['timeout'],0);self.assertLessEqual(command.call_args.kwargs['timeout'],10)
 def test_cli_cancellation_is_blocked_without_a_successful_source_pin(self):
  for error in (KeyboardInterrupt(),refresh.runtime_process.RuntimeCancelled(signal.SIGTERM)):
   with self.subTest(kind=type(error).__name__),mock.patch.object(refresh,'ROOT',self.work),mock.patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':refresh.REPO}),mock.patch.object(refresh,'refresh',side_effect=error):
    output=io.StringIO()
    with contextlib.redirect_stdout(output):code=refresh.main()
    self.assertEqual(code,1)
    report=json.loads(output.getvalue());self.assertEqual(report['status'],'BLOCKED_CUSTODY_MAIN_REFRESH')
    self.assertEqual(report['error_type'],type(error).__name__);self.assertNotIn('source_commit',report)
 def fake_git(self,parent_exits=True):
  fakebin=self.root/'fakebin';fakebin.mkdir();fakegit=fakebin/'git'
  heartbeat=self.root/'heartbeat';pids=self.root/'pids.json'
  child=("import os,signal,time;from pathlib import Path;"
         "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
         "p=Path("+repr(str(heartbeat))+");"
         "exec(\"while True:\\n with p.open('a') as stream:stream.write('tick\\\\n')\\n time.sleep(.02)\")")
  fakegit.write_text('#!'+sys.executable+'\n'+
   'import json,os,signal,subprocess,sys,time\n'+
   'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'+
   'args=sys.argv[3:]\n'+
   'if args[0]=="symbolic-ref":print("main");raise SystemExit(0)\n'+
   'if args[0]=="remote":print("https://github.com/'+refresh.REPO+'");raise SystemExit(0)\n'+
   'if args[0]=="status":raise SystemExit(0)\n'+
   'if args[0]=="rev-parse":print('+repr(self.old)+');raise SystemExit(0)\n'+
   'if args[0]!="fetch":raise SystemExit(2)\n'+
   'child=subprocess.Popen([sys.executable,"-c",'+repr(child)+'])\n'+
   'with open('+repr(str(pids))+',"w") as stream:json.dump({"parent":os.getpid(),"child":child.pid},stream)\n'+
   'print("fake local Git started",flush=True)\n'+
   ('time.sleep(30)\n' if not parent_exits else ''))
  fakegit.chmod(0o700);return fakebin,heartbeat,pids
 def cleanup_fake_git(self,pids):
  if pids.exists():
   group=json.loads(pids.read_text())['parent']
   if group!=os.getpgrp():
    try:os.killpg(group,signal.SIGKILL)
    except ProcessLookupError:pass
 @unittest.skipUnless(os.name=='posix','TERM-resistant Git descendant ownership requires production POSIX')
 def test_term_resistant_fake_git_descendant_cannot_hold_pipes_after_timeout(self):
  # An exited Git leader is insufficient while its child holds captured pipes.
  fakebin,heartbeat,pids=self.fake_git();started=time.monotonic()
  try:
   with mock.patch.dict(os.environ,{'PATH':str(fakebin)+os.pathsep+os.environ.get('PATH','')}):
    with self.assertRaises(subprocess.TimeoutExpired) as caught:refresh.git(self.work,time.monotonic()+.5,'fetch','local-only')
   cleanup=caught.exception.process_cleanup
   self.assertEqual(cleanup['scope'],'process_group');self.assertTrue(cleanup['kill_requested']);self.assertTrue(cleanup['leader_reaped']);self.assertTrue(cleanup['pipes_drained'])
   self.assertLess(time.monotonic()-started,.5+refresh.runtime_process.TERM_GRACE_SECONDS+2*refresh.runtime_process.KILL_WAIT_SECONDS+1)
   self.assertTrue(heartbeat.exists(),'fake descendant must really start before the timeout')
   before=heartbeat.read_bytes();time.sleep(.15);self.assertEqual(heartbeat.read_bytes(),before,'descendant still writes after timeout return')
  finally:
   self.cleanup_fake_git(pids)
 @unittest.skipUnless(os.name=='posix','Real CLI SIGTERM process-group ownership requires production POSIX')
 def test_actual_cli_sigterm_cleans_git_descendant_and_saves_nonzero_receipt(self):
  fakebin,heartbeat,pids=self.fake_git(parent_exits=False)
  env={**os.environ,'PATH':str(fakebin)+os.pathsep+os.environ.get('PATH',''),
   'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':refresh.REPO}
  process=subprocess.Popen([sys.executable,'-B',str(self.work/'forecast_custody_refresh.py')],
   cwd=self.work,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
  try:
   until=time.monotonic()+5
   while not pids.exists() or not heartbeat.exists():
    if process.poll() is not None or time.monotonic()>until:self.fail('actual CLI did not reach local fake Git descendant')
    time.sleep(.01)
   started=time.monotonic();os.kill(process.pid,signal.SIGTERM)
   stdout,stderr=process.communicate(timeout=8)
   self.assertNotEqual(process.returncode,0,stderr)
   report=json.loads(stdout);self.assertEqual(report['status'],'BLOCKED_CUSTODY_MAIN_REFRESH')
   self.assertEqual(report['error_type'],'RuntimeCancelled');self.assertNotIn('source_commit',report)
   self.assertTrue(report['process_cleanup']['kill_requested']);self.assertTrue(report['process_cleanup']['pipes_drained']);self.assertTrue(report['process_cleanup']['leader_reaped'])
   self.assertLess(time.monotonic()-started,refresh.runtime_process.TERM_GRACE_SECONDS+2*refresh.runtime_process.KILL_WAIT_SECONDS+1)
   self.assertEqual(json.loads((self.work/'forecast-worm-output/main_refresh.json').read_text()),report)
   before=heartbeat.read_bytes();time.sleep(.15);self.assertEqual(heartbeat.read_bytes(),before)
   self.assertFalse((self.work/'__pycache__').exists())
  finally:
   if process.poll() is None:
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
   process.communicate(timeout=2);self.cleanup_fake_git(pids)

if __name__=='__main__':unittest.main()

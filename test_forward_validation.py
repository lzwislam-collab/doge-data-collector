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

class BatchHistoryTests(unittest.TestCase):
 def setUp(self):
  import hashlib,types,os
  from unittest.mock import patch
  self.v=importlib.import_module('forward_validation')
  repo=Path(__file__).resolve().parent
  blob='f0831c7d38581543f84a85639c6d69a7305ff382'
  body=subprocess.check_output(['git','cat-file','blob',blob],cwd=repo)
  self.assertEqual(hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest(),blob)
  self.old=types.ModuleType('history_oracle');self.old.__file__=str(repo/'ORACLE_READ_ONLY.py')
  exec(compile(body,'frozen-blob:'+blob,'exec'),self.old.__dict__)
  self.assertIsNot(self.old.git,self.v.git)
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.env=patch.dict(os.environ,{'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1','GIT_OPTIONAL_LOCKS':'0','GIT_NO_LAZY_FETCH':'1'});self.env.start()
  self.git('init','-q','-b','main');self.git('config','core.autocrlf','false')
  self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
  self.path='ledger.csv';self.cols=['forecast_id','value'];self.rows={}
 def tearDown(self):self.env.stop();self.temp.cleanup()
 def git(self,*args,env=None):
  return subprocess.check_output(['git',*args],cwd=self.root,env=env,stderr=subprocess.PIPE).decode('utf-8').strip()
 def commit(self,body,stamp='2026-10-12T00:00:00+00:00'):
  import os
  p=self.root/self.path;p.write_bytes(body)
  self.git('add','--',self.path)
  self.git('commit','--allow-empty','-qm','fixture',env=dict(os.environ,GIT_AUTHOR_DATE=stamp,GIT_COMMITTER_DATE=stamp))
  return self.git('rev-parse','HEAD')
 def both(self,rows=None):
  rows=self.rows if rows is None else rows
  def result(fn):
   try:return ('ok',fn(self.root,self.path,self.cols,rows))
   except Exception as exc:return ('reject',type(exc).__name__)
  old,new=result(self.old.history_receipts),result(self.v.history_receipts)
  self.assertEqual(old[0],new[0],(old,new))
  if old[0]=='ok':self.assertEqual(old[1],new[1])
  import os,json,hashlib
  report=os.environ.get('HISTORY_EQUIVALENCE_LOG')
  if report:
   with open(report,'a',encoding='utf-8') as f:
    f.write(json.dumps(dict(test=self._testMethodName,old_status=old[0],new_status=new[0],old_result=old[1] if old[0]=='reject' else hashlib.sha256(json.dumps(old[1],sort_keys=True).encode()).hexdigest(),new_result=new[1] if new[0]=='reject' else hashlib.sha256(json.dumps(new[1],sort_keys=True).encode()).hexdigest()))+'\n')
  return old,new
 def test_structural_git_process_reduction_without_skipping_versions(self):
  from unittest.mock import patch
  for i in range(12):
   self.rows[str(i)]={'forecast_id':str(i),'value':'x'}
   self.commit(serial(self.rows,self.cols).encode())
  expected=self.old.history_receipts(self.root,self.path,self.cols,self.rows)
  original=subprocess.Popen;calls=[]
  def spawn(*args,**kw):calls.append(args[0]);return original(*args,**kw)
  with patch.object(subprocess,'Popen',side_effect=spawn):
   actual=self.v.history_receipts(self.root,self.path,self.cols,self.rows)
  self.assertEqual(expected,actual);self.assertLess(len(calls),20)
 def test_unicode_newlines_and_uncommitted_append(self):
  self.path='行情 data.csv';a={'forecast_id':'甲','value':'中文😀\nquoted "value"'}
  self.rows={'甲':a};first=self.commit(serial(self.rows,self.cols).encode())
  self.commit(serial(self.rows,self.cols).replace('\r\n','\n').rstrip('\n').encode(),'2026-10-11T01:00:00-05:30')
  old,new=self.both();self.assertEqual(old[1]['甲']['commit_sha'],first)
  self.rows['乙']={'forecast_id':'乙','value':'e\u0301'}
  (self.root/self.path).write_bytes(serial(self.rows,self.cols).encode())
  old,new=self.both();self.assertNotIn('乙',new[1])
 def test_nonmonotonic_timestamps_and_same_semantic_content(self):
  self.rows={'a':{'forecast_id':'a','value':'1'}};first=self.commit(serial(self.rows,self.cols).encode(),'2026-10-12T09:00:00+09:00')
  self.commit(serial(self.rows,self.cols).replace('\r\n','\n').encode(),'2026-10-10T20:00:00-04:00')
  self.rows['b']={'forecast_id':'b','value':'2'};second=self.commit(serial(self.rows,self.cols).encode(),'2026-10-09T00:00:00+00:00')
  old,new=self.both();self.assertEqual(new[1]['a']['commit_sha'],first);self.assertEqual(new[1]['b']['commit_sha'],second)
 def test_merge_history_preserves_original_selection_and_order(self):
  self.rows={'a':{'forecast_id':'a','value':'1'}};self.commit(serial(self.rows,self.cols).encode())
  self.git('checkout','-qb','side');self.rows['b']={'forecast_id':'b','value':'2'};self.commit(serial(self.rows,self.cols).encode())
  self.git('checkout','-q','main');(self.root/'unrelated').write_text('x');self.git('add','unrelated');self.git('commit','-qm','unrelated')
  self.git('merge','--no-ff','-qm','merge','side');old,new=self.both();self.assertEqual(new[0],'ok')
 def test_historic_edit_deletion_schema_and_duplicate_rejected(self):
  cases=[b'forecast_id,value\na,2\n',b'forecast_id,value\n',b'wrong,value\na,1\n',b'forecast_id,value\na,1\na,1\n',b'forecast_id,value\n,1\n']
  for body in cases:
   with self.subTest(body=body):
    self.git('checkout','-q','--orphan','case'+str(cases.index(body)));self.git('rm','-qrf','--ignore-unmatch','.')
    self.rows={'a':{'forecast_id':'a','value':'1'}};self.commit(serial(self.rows,self.cols).encode())
    self.commit(body);old,new=self.both();self.assertEqual(new[0],'reject')
 def test_current_edit_and_delete_rejected(self):
  self.rows={'a':{'forecast_id':'a','value':'1'}};self.commit(serial(self.rows,self.cols).encode())
  for current in ({},{'a':{'forecast_id':'a','value':'2'}}):
   old,new=self.both(current);self.assertEqual(new[0],'reject')
 def test_missing_corrupt_objects_and_shallow_rejected(self):
  self.rows={'a':{'forecast_id':'a','value':'1'}};self.commit(serial(self.rows,self.cols).encode())
  oid=self.git('rev-parse','HEAD:'+self.path);p=self.root/'.git/objects'/oid[:2]/oid[2:];raw=p.read_bytes()
  self.assertTrue(p.resolve().is_relative_to((self.root/'.git/objects').resolve()))
  import stat
  p.chmod(stat.S_IREAD|stat.S_IWRITE)
  p.unlink();old,new=self.both();self.assertEqual(new[0],'reject')
  p.write_bytes(b'broken');old,new=self.both();self.assertEqual(new[0],'reject');p.write_bytes(raw)
  (self.root/'.git/shallow').write_text(self.git('rev-parse','HEAD')+'\n');old,new=self.both();self.assertEqual(new[0],'reject')
 def test_repeat_call_revalidates_and_changed_input_is_rejected(self):
  self.rows={'a':{'forecast_id':'a','value':'1'}};self.commit(serial(self.rows,self.cols).encode());self.both()
  self.rows['a']['value']='changed';self.commit(serial(self.rows,self.cols).encode());old,new=self.both();self.assertEqual(new[0],'reject')
 def test_inflight_head_ref_workspace_protocol_changes_fail(self):
  from unittest.mock import patch
  self.rows={'a':{'forecast_id':'a','value':'1'}};self.commit(serial(self.rows,self.cols).encode())
  for kind in ('head','ref','workspace','protocol','mapping'):
   with self.subTest(kind=kind):
    original=self.v._HistoryBatch.read_blob;done=[]
    def change(reader,expr):
     body=original(reader,expr)
     if not done:
      done.append(True)
      if kind=='head':self.git('commit','--allow-empty','-qm','concurrent')
      elif kind=='ref':self.git('update-ref','refs/heads/other',self.git('rev-parse','HEAD'))
      elif kind=='workspace':(self.root/self.path).write_bytes(b'changed')
      elif kind=='protocol':
       p=self.root/self.v.PROTOCOL;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('changed')
      else:self.rows['a']['value']='changed'
     return body
    with patch.object(self.v._HistoryBatch,'read_blob',change):
     with self.assertRaisesRegex(ValueError,'changed|current record revised'):
      self.v.history_receipts(self.root,self.path,self.cols,self.rows)
    self.rows={'a':{'forecast_id':'a','value':'1'}};(self.root/self.path).write_bytes(serial(self.rows,self.cols).encode())
 def test_transient_head_change_cannot_select_other_history(self):
  from unittest.mock import patch
  empty=self.commit(b'forecast_id,value\n')
  self.rows={'a':{'forecast_id':'a','value':'1'}};head=self.commit(serial(self.rows,self.cols).encode())
  expected=self.old.history_receipts(self.root,self.path,self.cols,self.rows)
  original=self.v.git;seen=[]
  def transient(root,*args):
   if args and args[0]=='log':
    seen.append(True);self.git('update-ref','refs/heads/main',empty)
    try:return original(root,*args)
    finally:self.git('update-ref','refs/heads/main',head)
   return original(root,*args)
  with patch.object(self.v,'git',side_effect=transient):
   actual=self.v.history_receipts(self.root,self.path,self.cols,self.rows)
  self.assertTrue(seen);self.assertEqual(expected,actual)
 def test_large_object_equivalence_and_unchanged_frozen_rejection(self):
  self.rows={'a':{'forecast_id':'a','value':'中'*700000}};self.commit(serial(self.rows,self.cols).encode());self.both()
  import json,forward_validation_v2 as v2
  repo=Path(__file__).resolve().parent;protocol=json.loads((repo/v2.PROTOCOL).read_text())
  with self.assertRaisesRegex(ValueError,'forward_validation.py'):v2.check_code(repo,protocol)
 def test_protocol_faults_timeout_and_child_cleanup(self):
  import sys,time,hashlib
  # A separate process emits adversarial batch frames; it is never the oracle.
  for mode in ('missing','ambiguous','excluded','wrong_type','wrong_oid','wrong_body','wrong_size','truncated','separator','extra','exit','nonzero_after','stall','stderr'):
   with self.subTest(mode=mode):
    script=r'''import sys,time,hashlib
mode=sys.argv[1];r=sys.stdin.buffer;w=sys.stdout.buffer
def query():
 b=bytearray()
 while True:
  c=r.read(1)
  if not c:return None
  if c==b'\0':return bytes(b)
  b+=c
q=query();oid=hashlib.sha1(b'blob 3\0abc').hexdigest().encode()
if mode=='stall':time.sleep(30)
elif mode in ('missing','ambiguous','excluded'):w.write(q.split(b' ',1)[1]+b' '+mode.encode()+b'\0');w.flush()
elif mode=='wrong_type':w.write(oid+b' commit 3\0');w.flush()
else:
 if mode=='stderr':sys.stderr.buffer.write(b'x'*(1024*1024));sys.stderr.buffer.flush()
 size=b'9' if mode=='truncated' else b'3'
 w.write(oid+b' blob '+size+b'\0');w.flush();query()
 if mode=='exit':sys.exit(7)
 if mode=='wrong_oid':w.write(b'2'*40+b' blob 3\0abc\0')
 elif mode in ('truncated','wrong_size'):w.write(oid+b' blob 9\0abc')
 elif mode=='separator':w.write(oid+b' blob 3\0abcX')
 elif mode=='wrong_body':w.write(oid+b' blob 3\0xyz\0')
 elif mode in ('extra','nonzero_after'):
  w.write(oid+b' blob 3\0abc\0');w.flush()
  if mode=='extra':w.write(b'unsolicited\0')
  else:sys.exit(7)
 elif mode=='stderr':w.write(oid+b' blob 3\0abc\0')
 w.flush()
'''
    reader=self.v._HistoryBatch(self.root,timeout=.25,command=[sys.executable,'-u','-c',script,mode])
    begun=time.monotonic()
    if mode=='stderr':
     with reader:self.assertEqual(reader.read_blob('a'*40+':ledger.csv'),'abc')
    else:
     with self.assertRaises(Exception) as caught:
      with reader:reader.read_blob('a'*40+':ledger.csv')
     if mode=='wrong_body':self.assertIn('content identity',str(caught.exception))
     if mode=='truncated':self.assertIn('truncated',str(caught.exception))
    self.assertIsNotNone(reader.process.poll());self.assertFalse(reader.threads_alive())
    self.assertLess(time.monotonic()-begun,5)

 def test_static_blob_replacements_keep_original_git_semantics(self):
  self.rows={'a':{'forecast_id':'a','value':'1'}};self.commit(serial(self.rows,self.cols).encode())
  old=self.git('rev-parse','HEAD:'+self.path)
  body=serial(self.rows,self.cols).replace('\r\n','\n').encode()
  new=subprocess.check_output(['git','hash-object','-w','--stdin'],cwd=self.root,input=body).decode().strip()
  self.git('replace',old,new);old_result,new_result=self.both();self.assertEqual(new_result[0],'ok')
  from unittest.mock import patch
  import os
  for value in ('1','0','false',''):
   with patch.dict(os.environ,{'GIT_NO_REPLACE_OBJECTS':value}):
    old_result,new_result=self.both();self.assertEqual(new_result[0],'ok')
  self.git('config','core.useReplaceRefs','false');old_result,new_result=self.both();self.assertEqual(new_result[0],'ok')
  self.git('config','core.useReplaceRefs','true');self.git('update-ref','refs/customreplace/'+old,new)
  with patch.dict(os.environ,{'GIT_REPLACE_REF_BASE':'refs/customreplace/'}):
   old_result,new_result=self.both();self.assertEqual(new_result[0],'ok')

 def test_text_decoding_matches_old_for_crlf_cr_nul_and_invalid_utf8(self):
  cases=[(b'forecast_id,value\ra,"line1\rline2"\r','line1\nline2'),(b'forecast_id,value\r\na,"line1\r\nline2"','line1\nline2'),(b'forecast_id,value\na,\x00','\x00'),(b'forecast_id,value\na,\xff','invalid')]
  for i,(body,value) in enumerate(cases):
   self.git('checkout','-q','--orphan','text'+str(i));self.git('rm','-qrf','--ignore-unmatch','.')
   self.rows={'a':{'forecast_id':'a','value':value}};self.commit(body);old,new=self.both()
   self.assertEqual(new[0],'reject' if value=='invalid' else 'ok')

 @unittest.skipUnless(__import__('os').name=='nt','Windows-only child ownership test')
 def test_suspended_start_assignment_failure_and_spawned_child_cleanup(self):
  import ctypes,json,os,sys,time
  from unittest.mock import patch
  marker=self.root/'started.json'
  script='import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("started")'
  reader=self.v._HistoryBatch(self.root,command=[sys.executable,'-c',script,str(marker)])
  def refuse(process):time.sleep(.3);raise OSError('simulated assignment refusal')
  with patch.object(self.v,'_history_windows_job',side_effect=refuse):
   with self.assertRaises(OSError):
    with reader:pass
  self.assertFalse(marker.exists());self.assertIsNotNone(reader.process.poll())
  script='import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(30)'
  reader=self.v._HistoryBatch(self.root,timeout=1,command=[sys.executable,'-c',script,str(marker)])
  with self.assertRaises(TimeoutError):
   with reader:reader.read_blob('a'*40+':ledger.csv')
  self.assertTrue(marker.exists());pid=int(marker.read_text())
  from ctypes import wintypes as w
  k=ctypes.WinDLL('kernel32',use_last_error=True);k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
  k.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)];k.CloseHandle.argtypes=[w.HANDLE]
  def active():
   h=k.OpenProcess(0x1000,False,pid)
   if not h:return False
   code=w.DWORD();ok=k.GetExitCodeProcess(h,ctypes.byref(code));k.CloseHandle(h)
   return bool(ok and code.value==259)
  until=time.monotonic()+2
  while active() and time.monotonic()<until:time.sleep(.02)
  self.assertFalse(active());self.assertFalse(reader.threads_alive())

def _benchmark_worker(mode,root,out):
 """One timed function bundle; no production files, network or frozen gate mock."""
 import ctypes,hashlib,json,os,threading,time,types
 from unittest.mock import patch
 import forward_validation as new
 from ctypes import wintypes as w
 code=subprocess.check_output(['git','cat-file','blob','f0831c7d38581543f84a85639c6d69a7305ff382'],cwd=Path(__file__).parent)
 assert hashlib.sha1(b'blob '+str(len(code)).encode()+b'\0'+code).hexdigest()=='f0831c7d38581543f84a85639c6d69a7305ff382'
 old=types.ModuleType('benchmark_frozen_oracle');old.__file__=str(Path(__file__).parent/'ORACLE.py');exec(compile(code,'frozen-oracle','exec'),old.__dict__)
 fn=old.history_receipts if mode=='old' else new.history_receipts
 inputs=json.loads((root/'inputs.json').read_text());active=[];lock=threading.Lock();count=[0];stop=threading.Event();samples=[]
 class Memory(ctypes.Structure):
  _fields_=[('cb',w.DWORD),('faults',w.DWORD)]+[(n,ctypes.c_size_t) for n in ('peak_ws','ws','peak_pool_p','pool_p','peak_pool_n','pool_n','pagefile','peak_pagefile','private')]
 kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.GetCurrentProcess.restype=w.HANDLE
 psapi=ctypes.WinDLL('psapi');psapi.GetProcessMemoryInfo.argtypes=[w.HANDLE,ctypes.c_void_p,w.DWORD]
 def memory(handle):
  data=Memory();data.cb=ctypes.sizeof(data)
  return data.ws if psapi.GetProcessMemoryInfo(w.HANDLE(int(handle)),ctypes.byref(data),data.cb) else 0
 def sample():
  while not stop.is_set():
   with lock:
    active[:]=[p for p in active if p.poll() is None];rss=sum(memory(p._handle) for p in active)
   samples.append(memory(kernel.GetCurrentProcess())+rss);stop.wait(.02)
 original=subprocess.Popen
 def spawn(*args,**kwargs):
  p=original(*args,**kwargs)
  with lock:count[0]+=1;active.append(p)
  return p
 disk_before=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
 sampler=threading.Thread(target=sample);sampler.start();calls=[];outputs={}
 try:
  with patch.object(subprocess,'Popen',side_effect=spawn):
   for item in inputs:
    before=count[0];started=time.monotonic()
    result=fn(root,item['path'],item['columns'],item['rows'])
    elapsed=time.monotonic()-started
    outputs[item['path']]=result
    calls.append(dict(path=item['path'],seconds=elapsed,git_processes=count[0]-before,receipts=len(result)))
 finally:stop.set();sampler.join()
 disk_after=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
 report=dict(mode=mode,calls=calls,total_function_seconds=sum(c['seconds'] for c in calls),git_processes=count[0],
   output_sha256=hashlib.sha256(json.dumps(outputs,sort_keys=True).encode()).hexdigest(),
   sampled_aggregate_working_set_peak_bytes=max(samples,default=0),memory_sample_interval_seconds=.02,
   memory_scope='Worker plus tracked live Git children; sampled, shared pages may be double-counted and short-lived peaks may be missed',
   fixture_logical_bytes_before=disk_before,fixture_logical_bytes_after=disk_after,
   temporary_stderr_payload_bytes='NOT_VERIFIED: temporary stderr storage outside the input tree was not sampled',
   remaining_tracked_processes=[p.pid for p in active if p.poll() is None])
 out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

def _benchmark(out):
 import hashlib,json,os,platform,sys,time
 import forward_validation as base,quote_evidence_v2 as evidence
 out.mkdir(parents=True,exist_ok=True);root=out/'input-synthetic'
 if root.exists():raise RuntimeError('Benchmark input already exists; no unapproved repeat')
 root.mkdir();env=dict(os.environ,GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM='1',GIT_OPTIONAL_LOCKS='0',GIT_NO_LAZY_FETCH='1')
 def git(*args,local_env=None):return subprocess.check_output(['git',*args],cwd=root,env=local_env or env,stderr=subprocess.PIPE).decode().strip()
 git('init','-q','-b','main');git('config','core.autocrlf','false');git('config','user.name','Synthetic Benchmark');git('config','user.email','synthetic@example.invalid')
 specs=[(base.REG,FCOLS),(base.SCORES,SCOLS),(evidence.BINDINGS,evidence.BCOLS),(base.WITNESSES,base.WCOLS),(base.RUN_CACHE,base.RCOLS)]
 inputs=[dict(path=p,columns=c,rows={}) for p,c in specs];history={p:[] for p,c in specs};started=time.monotonic()
 for i in range(64):
  bodies={}
  for item in inputs:
   fid='LOCAL_SYNTHETIC_'+str(i);item['rows'][fid]={c:(fid if c=='forecast_id' else 'local-'+str(i)+'-'+c) for c in item['columns']}
   data=serial(item['rows'],item['columns']).encode();p=root/item['path'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);bodies[item['path']]=data
  git('add','data');stamp=(datetime(2026,10,12,tzinfo=timezone.utc)+timedelta(hours=i)).isoformat()
  git('commit','-qm','synthetic '+str(i),local_env=dict(env,GIT_AUTHOR_DATE=stamp,GIT_COMMITTER_DATE=stamp));head=git('rev-parse','HEAD')
  for path,data in bodies.items():history[path].append(dict(commit=head,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
 (root/'inputs.json').write_text(json.dumps(inputs,ensure_ascii=False),encoding='utf-8')
 manifest=dict(scope='Synthetic function-only small sample; NOT verified production-scale input',head=git('rev-parse','HEAD'),tree=git('rev-parse','HEAD^{tree}'),
  history=history,files=[dict(path=p,versions=len(git('log','--format=%H','--',p).splitlines()),rows=64,
   current_bytes=(root/p).stat().st_size,current_sha256=hashlib.sha256((root/p).read_bytes()).hexdigest(),historical_bytes=sum(r['bytes'] for r in history[p])) for p,c in specs],
  preparation_seconds=time.monotonic()-started,python=sys.version,git=git('--version'),platform=platform.platform(),
  cache_conditions='No OS cache flush. Input generated locally immediately before measurement; treat as warm-cache. Fresh worker processes, fixed ABBA order; no selection of best repeats.',order=['old','new','new','old'])
 (out/'input-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 reports=[]
 for i,mode in enumerate(manifest['order']):
  target=out/('measure-'+str(i+1)+'-'+mode+'.json')
  p=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--benchmark-worker',mode,str(root),str(target)],cwd=Path(__file__).parent,env=env,capture_output=True,timeout=180)
  (out/('measure-'+str(i+1)+'.log')).write_bytes(p.stdout+p.stderr)
  if p.returncode:raise RuntimeError('Benchmark worker failed; retain evidence and stop')
  reports.append(json.loads(target.read_text()))
 assert len({r['output_sha256'] for r in reports})==1
 assert all(not r['remaining_tracked_processes'] for r in reports)
 summary=dict(status='PASS',scope='Same-machine warm-cache synthetic function comparison only',order=manifest['order'],reports=reports,all_outputs_equal=True,
  production_scale_benefit='NOT_VERIFIED: the5 real ledger objects are locally missing',ubuntu_benefit='NOT_VERIFIED',full_rescue_180_seconds='NOT_VERIFIED',
  baseline_not_retimed_from_old_ubuntu_run=True,no_real_quote_or_production_writes=True)
 (out/'benchmark-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))

if __name__=='__main__':
 import sys
 if len(sys.argv)>1 and sys.argv[1]=='--benchmark-worker':_benchmark_worker(sys.argv[2],Path(sys.argv[3]),Path(sys.argv[4]))
 elif len(sys.argv)>1 and sys.argv[1]=='--benchmark':_benchmark(Path(sys.argv[2]).resolve())
 else:unittest.main()

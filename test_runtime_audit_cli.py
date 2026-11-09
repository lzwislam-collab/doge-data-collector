"""Owned launcher safety and real-Git differential primitive integration.

Real-history tests execute exact frozen function AST bodies against real Git;
full original three-CLI report/performance equivalence is a separate gate.
"""
import ast,contextlib,csv,hashlib,importlib.util,io,json,os,re,shutil,subprocess,sys,tempfile,types,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest import mock

HERE=Path(__file__).parent
REFERENCE=HERE/'reference' if (HERE/'reference').is_dir() else HERE
SCRIPTS=('forward_validation_v2.py','p0_operational_audit.py','paired_cohort_audit.py')
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def subject():
 path=HERE/'runtime_audit_cli.py'
 if not path.exists():raise AssertionError('Owned audit launcher is missing')
 return load('audit_cli_under_test',path)
def functions(module,path,names):
 module.__file__=str(path)
 tree=ast.parse(path.read_bytes());wanted=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
 if {n.name for n in wanted}!=set(names):raise AssertionError('Frozen fixture function absent')
 exec(compile(ast.Module(body=wanted,type_ignores=[]),str(path),'exec'),module.__dict__)

class LauncherSafetyTests(unittest.TestCase):
 def setUp(self):
  self.subject=subject();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.base=types.ModuleType('forward_validation');self.cohort=types.ModuleType('paired_forward_cohort')
  self.old_base=mock.Mock(return_value={'original':'receipts'});self.old_pair=mock.Mock(return_value=['original-chain'])
  self.base.history_receipts=self.old_base;self.cohort.history_guard=self.old_pair
  self.native=types.ModuleType('runtime_publication_state');self.paired=types.ModuleType('runtime_paired_history')
  self.native.emit_diagnostics=mock.Mock();self.paired.emit_diagnostics=mock.Mock()
  # This really follows the helpers' callback shape: careless hooks recurse.
  self.native.history_receipts=mock.Mock(side_effect=lambda *a,**k:self.base.history_receipts(*a))
  self.paired.history_guard=mock.Mock(side_effect=lambda root,cohort=None,**k:cohort.history_guard(root))
  self.mods={'forward_validation':self.base,'runtime_publication_state':self.native,'paired_forward_cohort':self.cohort,'runtime_paired_history':self.paired}
  for name,module in (('runtime_publication_state',self.native),('runtime_paired_history',self.paired)):
   path=self.root/(name+'.py');shutil.copyfile(REFERENCE/(name+'.py'),path);module.__file__=str(path)
  self.modules=mock.patch.dict(sys.modules,self.mods);self.modules.start();self.addCleanup(self.modules.stop)
 def run_script(self,name,body):
  path=self.root/name;path.write_text('def main():\n'+''.join(' '+l+'\n' for l in body.splitlines())+' return locals()\n\nif __name__=="__main__":raise SystemExit(main())\n',encoding='utf-8')
  with mock.patch.dict(self.subject.SCRIPT_BLOBS,{name:self.subject._blob(path)}):return self.subject.run_cli(self.root,name)
 def restored(self):
  self.assertIs(self.base.history_receipts,self.old_base);self.assertIs(self.cohort.history_guard,self.old_pair)
 def test_allowlist_rejects_traversal_unknown_and_extra_path_before_import(self):
  for value in ('../forward_validation_v2.py','publish_forward_evidence.py','/tmp/p0_operational_audit.py','forward_validation_v2.py --extra'):
   with self.subTest(value=value),mock.patch.object(self.subject.importlib,'import_module') as imported:
    with self.assertRaises(ValueError):self.subject.run_cli(self.root,value)
    imported.assert_not_called()
 def test_real_runpy_script_keeps_argv_stdout_and_fallback_without_recursion(self):
  out=io.StringIO();argv=sys.argv;path=list(sys.path)
  with contextlib.redirect_stdout(out):
   result=self.run_script(SCRIPTS[0],"import forward_validation as b,sys\nassert len(sys.argv)==1 and sys.argv[0].endswith('forward_validation_v2.py')\nvalue=b.history_receipts('root','path',[],{})\nprint('{\"status\":\"ok\"}')\n")
  self.assertEqual(result['value'],{'original':'receipts'});self.old_base.assert_called_once();self.native.history_receipts.assert_called_once()
  self.assertEqual(out.getvalue(),'{"status":"ok"}\n');self.assertIs(sys.argv,argv);self.assertEqual(sys.path,path);self.restored()
 def test_fwd_and_p0_do_not_import_paired_dependencies(self):
  original=self.subject.importlib.import_module
  for script in SCRIPTS[:2]:
   names=[]
   def imported(name):names.append(name);return original(name)
   with mock.patch.object(self.subject.importlib,'import_module',side_effect=imported):self.run_script(script,'pass\n')
   self.assertNotIn('paired_forward_cohort',names);self.assertNotIn('runtime_paired_history',names)
  self.restored()
 def test_only_paired_script_installs_real_paired_fallback_hook(self):
  result=self.run_script(SCRIPTS[2],"import paired_forward_cohort as c\nvalue=c.history_guard('root')\n")
  self.assertEqual(result['value'],['original-chain']);self.old_pair.assert_called_once();self.paired.history_guard.assert_called_once();self.restored()
 def test_original_validation_exception_is_identical_and_state_restored(self):
  error=ValueError('original validation must survive');self.old_base.side_effect=error
  with self.assertRaises(ValueError) as got:self.run_script(SCRIPTS[0],"import forward_validation as b\nb.history_receipts('root','p',[],{})\n")
  self.assertIs(got.exception,error);self.old_base.assert_called_once();self.restored()
 def test_systemexit_restores_both_hooks_argv_and_path(self):
  argv=sys.argv;path=list(sys.path)
  with self.assertRaises(SystemExit) as got:self.run_script(SCRIPTS[2],"import forward_validation as b,paired_forward_cohort as c\nb.history_receipts('r','p',[],{})\nc.history_guard('r')\nraise SystemExit(7)\n")
  self.assertEqual(got.exception.code,7);self.assertIs(sys.argv,argv);self.assertEqual(sys.path,path);self.restored()
 def test_failed_optional_paired_backend_import_preserves_complete_original(self):
  original=self.subject.importlib.import_module
  def imported(name):
   if name=='runtime_paired_history':raise ImportError('paired adapter unavailable')
   return original(name)
  with mock.patch.object(self.subject.importlib,'import_module',side_effect=imported),contextlib.redirect_stderr(io.StringIO()):
   result=self.run_script(SCRIPTS[2],"import paired_forward_cohort as c\nvalue=c.history_guard('root')\n")
  self.assertEqual(result['value'],['original-chain']);self.old_pair.assert_called_once()
  self.restored()
 def test_optional_base_backend_import_failure_keeps_original_cli_success(self):
  with mock.patch.object(self.subject.importlib,'import_module',side_effect=ImportError('optional backend missing')),contextlib.redirect_stderr(io.StringIO()):
   result=self.run_script(SCRIPTS[0],"import forward_validation as b\nvalue=b.history_receipts('r','p',[],{})\n")
  self.assertEqual(result['value'],{'original':'receipts'});self.native.history_receipts.assert_not_called();self.restored()
 def test_unknown_backend_identity_uses_original_without_importing_it(self):
  (self.root/'runtime_publication_state.py').write_text('raise RuntimeError("unreviewed helper")',encoding='utf-8')
  with mock.patch.object(self.subject.importlib,'import_module') as imported,contextlib.redirect_stderr(io.StringIO()):result=self.run_script(SCRIPTS[0],"import forward_validation as b\nvalue=b.history_receipts('r','p',[],{})\n")
  self.assertEqual(result['value'],{'original':'receipts'});imported.assert_not_called();self.restored()
 def test_unknown_cli_identity_uses_whole_original_main_guard(self):
  script=SCRIPTS[0];(self.root/script).write_text('if __name__=="__main__":raise SystemExit(23)\n',encoding='utf-8')
  with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as got:self.subject.run_cli(self.root,script)
  self.assertEqual(got.exception.code,23);self.native.history_receipts.assert_not_called();self.restored()
 def test_unknown_cli_without_main_guard_keeps_implicit_success(self):
  script=SCRIPTS[0];(self.root/script).write_text('print("original-only-output")\n',encoding='utf-8');out=io.StringIO()
  with contextlib.redirect_stderr(io.StringIO()),contextlib.redirect_stdout(out):result=self.subject.run_cli(self.root,script)
  self.assertIsNone(result);self.assertEqual(out.getvalue(),'original-only-output\n');self.restored()
 def test_original_module_import_failure_precedes_optional_adapter_loading(self):
  path=self.root/SCRIPTS[0];path.write_text('raise ImportError("original import error")\n',encoding='utf-8')
  with mock.patch.dict(self.subject.SCRIPT_BLOBS,{SCRIPTS[0]:self.subject._blob(path)}),mock.patch.object(self.subject.importlib,'import_module') as imported,self.assertRaisesRegex(ImportError,'original import error'):self.subject.run_cli(self.root,SCRIPTS[0])
  imported.assert_not_called();self.restored()
 def test_diagnostics_cannot_hide_original_blocked_json_or_exit(self):
  def emit(*a):print('{"kind":"backend"}')
  self.native.emit_diagnostics.side_effect=emit;out=io.StringIO();err=io.StringIO()
  with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err),self.assertRaises(SystemExit):self.run_script(SCRIPTS[0],"import forward_validation as b\nb.history_receipts('r','p',[],{})\nprint('{\"status\":\"blocked\",\"error\":\"full git history required\"}')\nraise SystemExit(1)\n")
  self.assertEqual(json.loads(out.getvalue())['error'],'full git history required');self.assertIn('backend',err.getvalue());self.restored()
 def test_broken_diagnostics_do_not_replace_original_error(self):
  error=ValueError('original error');self.old_base.side_effect=error;self.native.emit_diagnostics.side_effect=RuntimeError('diagnostic failed')
  with self.assertRaises(ValueError) as got:self.run_script(SCRIPTS[0],"import forward_validation as b\nb.history_receipts('r','p',[],{})\n")
  self.assertIs(got.exception,error);self.restored()
 def test_main_uses_own_root_and_rejects_root_override(self):
  with mock.patch.object(self.subject,'run_cli') as run:self.subject.main([SCRIPTS[0]])
  run.assert_called_once_with(Path(self.subject.__file__).resolve().parent,SCRIPTS[0])
  with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):self.subject.main([SCRIPTS[0],'--root',str(self.root)])

class WorkerRouteTests(unittest.TestCase):
 def worker(self):
  with mock.patch.dict(sys.modules,{'runtime_process':types.ModuleType('runtime_process')}):return load('worker_under_test',HERE/'runtime_audit_worker.py')
 def test_same_three_scripts_stay_serial_with_owned_launcher_and_original_phases(self):
  worker=self.worker();seen=[];tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
  def prepare(source,snapshot,head,**kw):snapshot.mkdir();return {'unchanged':'checkout'}
  def command(phase,*args,cwd,**meta):
   self.assertEqual(phase,SCRIPTS[len(seen)]);self.assertEqual(args,(sys.executable,str(cwd/'runtime_audit_cli.py'),phase));self.assertEqual(meta,{'input_head':'a'*40});seen.append(phase);return '',.01
  with mock.patch.object(worker,'prepare_checkout',side_effect=prepare),mock.patch.object(worker,'verify_cli_sources',return_value=True),mock.patch.object(worker,'fingerprint',return_value={'same':'five-input-contract'}),mock.patch.object(worker,'timed_command',side_effect=command),mock.patch.object(Path,'read_text',return_value='{"status":"ok"}'):
   result=worker.audit_snapshot(root,'a'*40,root)
  self.assertEqual(tuple(seen),SCRIPTS);self.assertEqual(tuple(t['stage'] for t in result['timings']),SCRIPTS);self.assertEqual(set(result['outputs']),set(worker.OUTPUTS))
 def test_child_failure_stops_before_later_cli(self):
  worker=self.worker();seen=[]
  def command(phase,*args,**kw):seen.append(phase);raise subprocess.CalledProcessError(1,args)
  with tempfile.TemporaryDirectory() as name,mock.patch.object(worker,'prepare_checkout'),mock.patch.object(worker,'verify_cli_sources'),mock.patch.object(worker,'fingerprint',return_value={}),mock.patch.object(worker,'timed_command',side_effect=command),self.assertRaises(subprocess.CalledProcessError):worker.audit_snapshot(Path(name),'b'*40,Path(name))
  self.assertEqual(seen,[SCRIPTS[0]])

class RealGitLauncherTests(unittest.TestCase):
 def setUp(self):
  self.subject=subject();self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.git('init','-q');self.git('config','user.name','fixture');self.git('config','user.email','fixture@example.invalid');self.git('config','core.autocrlf','false')
  base=types.ModuleType('forward_validation');base.__dict__.update(csv=csv,io=io,subprocess=subprocess,datetime=datetime,timezone=timezone)
  functions(base,REFERENCE/'forward_validation.py',{'git','history_receipts'})
  publication=types.ModuleType('publish_forward_evidence');publication.subprocess=subprocess
  functions(publication,REFERENCE/'publish_forward_evidence.py',{'command'})
  depth=types.ModuleType('forward_execution_evidence');depth.__dict__.update(hashlib=hashlib,json=json)
  functions(depth,REFERENCE/'forward_execution_evidence.py',{'digest'})
  cohort=types.ModuleType('paired_forward_cohort');cohort.__dict__.update(json=json,re=re,depth=depth,publication=publication,REG='data/paired_forward_v1/pairs.jsonl')
  functions(cohort,REFERENCE/'paired_forward_cohort.py',{'digest','parse_chain','read_chain','history_guard','record_path'})
  self.mods={'forward_validation':base,'publish_forward_evidence':publication,'paired_forward_cohort':cohort}
  self.patcher=mock.patch.dict(sys.modules,self.mods);self.patcher.start();self.addCleanup(self.patcher.stop)
  for name in ('runtime_publication_state.py','runtime_paired_history.py'):shutil.copyfile(REFERENCE/name,self.root/name)
  self.native=load('real_publication_backend',self.root/'runtime_publication_state.py');self.paired=load('real_paired_backend',self.root/'runtime_paired_history.py')
  sys.modules.update(runtime_publication_state=self.native,runtime_paired_history=self.paired)
  self.addCleanup(lambda:sys.modules.pop('runtime_publication_state',None));self.addCleanup(lambda:sys.modules.pop('runtime_paired_history',None))
  self.base=base;self.cohort=cohort;self.original_history=base.history_receipts;self.original_pair=cohort.history_guard
 def git(self,*args):return subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True).stdout
 def commit(self):self.git('add','-A');self.git('commit','--allow-empty','-qm','fixture')
 def history(self,values):
  path=self.root/'data/test.csv';path.parent.mkdir(exist_ok=True);path.write_text('forecast_id,value\n'+''.join(str(i)+','+v+'\n' for i,v in enumerate(values)),encoding='utf-8')
  return {str(i):{'forecast_id':str(i),'value':v} for i,v in enumerate(values)}
 def run_script(self,script,body):
  path=self.root/script;path.write_text('def main():\n'+''.join(' '+l+'\n' for l in body.splitlines())+' return locals()\n\nif __name__=="__main__":raise SystemExit(main())\n',encoding='utf-8')
  with mock.patch.dict(self.subject.SCRIPT_BLOBS,{script:self.subject._blob(path)}),contextlib.redirect_stderr(io.StringIO()):return self.subject.run_cli(self.root,script)
 def test_real_csv_native_receipts_and_uncommitted_append_equal_frozen(self):
  self.history(['a']);self.commit();self.history(['a','b']);self.commit();current=self.history(['a','b','c'])
  expected=self.original_history(self.root,'data/test.csv',['forecast_id','value'],current);self.base.fixture_current=current
  result=self.run_script(SCRIPTS[0],"import forward_validation as b\nfrom pathlib import Path\nvalue=b.history_receipts(Path(__file__).parent,'data/test.csv',['forecast_id','value'],b.fixture_current)\n")
  self.assertEqual(result['value'],expected);self.assertIs(self.base.history_receipts,self.original_history)
 def test_real_late_native_csv_failure_restarts_whole_original_without_recursion(self):
  for values in (['a'],['a','b'],['a','b','c']):self.history(values);self.commit()
  current=self.history(['a','b','c','d']);self.base.fixture_current=current
  expected=self.original_history(self.root,'data/test.csv',['forecast_id','value'],current)
  original=self.native._batch_output;calls=[]
  def failure(*args):
   calls.append(1)
   if len(calls)==2:raise subprocess.CalledProcessError(1,['git','cat-file'])
   return original(*args)
  with mock.patch.object(self.native,'BATCH_SIZE',1),mock.patch.object(self.native,'_batch_output',side_effect=failure):result=self.run_script(SCRIPTS[0],"import forward_validation as b\nfrom pathlib import Path\nvalue=b.history_receipts(Path(__file__).parent,'data/test.csv',['forecast_id','value'],b.fixture_current)\n")
  self.assertEqual(len(calls),2);self.assertEqual(result['value'],expected);self.assertIs(self.base.history_receipts,self.original_history)
 def test_real_restore_after_historical_csv_edit_still_rejected(self):
  self.history(['a']);self.commit();self.history(['edited']);self.commit();current=self.history(['a']);self.commit();self.base.fixture_current=current
  with self.assertRaises(ValueError) as old:self.original_history(self.root,'data/test.csv',['forecast_id','value'],current)
  with self.assertRaises(type(old.exception)) as new:self.run_script(SCRIPTS[0],"import forward_validation as b\nfrom pathlib import Path\nb.history_receipts(Path(__file__).parent,'data/test.csv',['forecast_id','value'],b.fixture_current)\n")
  self.assertEqual(str(new.exception),str(old.exception));self.assertIs(self.base.history_receipts,self.original_history)
 def test_real_unknown_dependency_bytes_keep_complete_original_fallback(self):
  self.history(['a']);self.commit();current=self.history(['a','b']);self.base.fixture_current=current
  expected=self.original_history(self.root,'data/test.csv',['forecast_id','value'],current)
  unknown=self.root/'unknown-base.py';unknown.write_bytes((REFERENCE/'forward_validation.py').read_bytes()+b'\n# unknown engineering revision\n');self.base.__file__=str(unknown)
  with mock.patch.object(self.native,'_native_history',side_effect=AssertionError('unknown source must never use native')) as native:
   result=self.run_script(SCRIPTS[0],"import forward_validation as b\nfrom pathlib import Path\nvalue=b.history_receipts(Path(__file__).parent,'data/test.csv',['forecast_id','value'],b.fixture_current)\n")
  self.assertEqual(result['value'],expected);native.assert_not_called();self.assertIs(self.base.history_receipts,self.original_history)
 def paired_rows(self,count):
  rows=[]
  for i in range(count):
   row={'production':{'forecast_id':'f'+str(i)},'previous_sha256':rows[-1]['record_sha256'] if rows else None};row['record_sha256']=self.cohort.digest(row);rows.append(row)
  path=self.root/self.cohort.REG;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
  for row in rows:
   record=self.cohort.record_path(path,row['production']['forecast_id']);record.parent.mkdir(exist_ok=True);record.write_text(json.dumps(row),encoding='utf-8')
  return rows
 def test_real_paired_chain_native_and_late_fallback_equal_frozen(self):
  for n in (1,2,3):self.paired_rows(n);self.commit()
  expected=self.original_pair(self.root);original=self.paired._batch_output;calls=[]
  def failure(*args):
   calls.append(1)
   if len(calls)==2:raise subprocess.CalledProcessError(1,['git','show'])
   return original(*args)
  body="import paired_forward_cohort as c\nfrom pathlib import Path\nvalue=c.history_guard(Path(__file__).parent)\n"
  self.assertEqual(self.run_script(SCRIPTS[2],body)['value'],expected)
  with mock.patch.object(self.paired,'BATCH_SIZE',1),mock.patch.object(self.paired,'_batch_output',side_effect=failure):result=self.run_script(SCRIPTS[2],body)
  self.assertEqual(len(calls),2);self.assertEqual(result['value'],expected);self.assertIs(self.cohort.history_guard,self.original_pair)
 def test_real_paired_historical_edit_restoration_is_still_rejected(self):
  rows=self.paired_rows(1);self.commit();bad=dict(rows[0]);bad['value']='tampered';bad.pop('record_sha256');bad['record_sha256']=self.cohort.digest(bad)
  path=self.root/self.cohort.REG;path.write_text(json.dumps(bad)+'\n',encoding='utf-8');self.cohort.record_path(path,'f0').write_text(json.dumps(bad),encoding='utf-8');self.commit();self.paired_rows(1);self.commit()
  with self.assertRaises(ValueError) as old:self.original_pair(self.root)
  with self.assertRaises(type(old.exception)) as new:self.run_script(SCRIPTS[2],"import paired_forward_cohort as c\nfrom pathlib import Path\nc.history_guard(Path(__file__).parent)\n")
  self.assertEqual(str(new.exception),str(old.exception));self.assertIs(self.cohort.history_guard,self.original_pair)

class WorkerSourceBindingTests(unittest.TestCase):
 def test_sources_are_bound_to_selected_head_and_tampering_is_rejected(self):
  with mock.patch.dict(sys.modules,{'runtime_process':types.ModuleType('runtime_process')}):worker=load('binding_worker',HERE/'runtime_audit_worker.py')
  with tempfile.TemporaryDirectory() as name:
   root=Path(name)
   def git(*args):return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True).stdout.decode().strip()
   git('init','-q');git('config','user.name','fixture');git('config','user.email','fixture@example.invalid')
   for path in worker.CLI_HELPERS:(root/path).write_bytes(b'# original\n')
   git('add','-A');git('commit','-qm','selected source');head=git('rev-parse','HEAD');self.assertTrue(worker.verify_cli_sources(root,head))
   (root/'runtime_audit_cli.py').write_bytes(b'# caller newer head\n');git('add','-A');git('commit','-qm','later caller source')
   with self.assertRaisesRegex(ValueError,'pinned source'):worker.verify_cli_sources(root,head)
   current=git('rev-parse','HEAD');self.assertTrue(worker.verify_cli_sources(root,current))
   for relative in worker.CLI_HELPERS:
    with self.subTest(missing=relative):
     path=root/relative;body=path.read_bytes();path.unlink()
     try:
      with self.assertRaises((ValueError,subprocess.CalledProcessError)):worker.verify_cli_sources(root,current)
     finally:path.write_bytes(body)
 def test_old_head_ignores_untracked_fake_launcher_and_runs_original_clis(self):
  with mock.patch.dict(sys.modules,{'runtime_process':types.ModuleType('runtime_process')}):worker=load('missing_binding_worker',HERE/'runtime_audit_worker.py')
  with tempfile.TemporaryDirectory() as name:
   root=Path(name)
   def git(*args):return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True).stdout.decode().strip()
   git('init','-q');git('config','user.name','fixture');git('config','user.email','fixture@example.invalid')
   for relative in worker.INPUTS:
    path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('unchanged source input',encoding='utf-8')
   for index,script in enumerate(SCRIPTS):
    code="from pathlib import Path\nimport json\np=Path('order.json');seen=json.loads(p.read_text()) if p.exists() else [];seen.append("+repr(script)+");p.write_text(json.dumps(seen))\n"
    if index:
     code+="out=Path("+repr(worker.OUTPUTS[index-1])+");out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps({'status':'ok','order':seen}))\n"
    (root/script).write_text(code,encoding='utf-8')
   git('add','-A');git('commit','-qm','old source');head=git('rev-parse','HEAD')
   fake=b'raise RuntimeError("untracked caller launcher must never execute")\n';(root/'runtime_audit_cli.py').write_bytes(fake)
   self.assertFalse(worker.verify_cli_sources(root,head))
   with contextlib.redirect_stdout(io.StringIO()):result=worker.audit_snapshot(root,head,root)
   self.assertEqual(result['status'],'ok');self.assertEqual(json.loads(result['outputs'][worker.OUTPUTS[1]])['order'],list(SCRIPTS))
   self.assertEqual((root/'runtime_audit_cli.py').read_bytes(),fake);self.assertFalse((root/worker.OUTPUTS[0]).exists())

if __name__=='__main__':unittest.main()

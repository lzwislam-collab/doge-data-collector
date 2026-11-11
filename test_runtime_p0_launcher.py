"""Owned P0 launcher boundary and bounded diagnostic forwarding."""
import contextlib,hashlib,importlib.util,io,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest import mock
HERE=Path(__file__).parent

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class P0LauncherTests(unittest.TestCase):
 def setUp(self):
  self.launcher=load('p0_launcher_under_test',HERE/'runtime_audit_cli.py');self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.original=lambda: 'unchanged';self.globals={'fetch_operational_runs':self.original};self.seen=[]
  self.helper=types.SimpleNamespace(__file__=str(HERE/'runtime_p0_inventory_transport.py'))
  @contextlib.contextmanager
  def installed(ns):
   self.seen.append(ns);previous=ns['fetch_operational_runs'];ns['fetch_operational_runs']='wrapped'
   try:yield {'installed':True,'implementation':'p0_fresh_inventory_window4_v1','window':4,'native_calls':1,'fallback_calls':0,'real_gets':30,'days':29}
   finally:ns['fetch_operational_runs']=previous
  self.helper.installed=installed
 def hook(self,script='p0_operational_audit.py'):
  self.assertTrue(hasattr(self.launcher,'_p0_hook'),'P0 original-main launcher context is absent')
  return self.launcher._p0_hook(self.root,script,self.globals)
 def test_p0_installs_exact_main_globals_restores_and_keeps_stdout(self):
  err=io.StringIO();out=io.StringIO()
  with mock.patch.object(self.launcher,'_backend',return_value=self.helper),contextlib.redirect_stderr(err),contextlib.redirect_stdout(out):
   with self.hook():self.assertEqual(self.globals['fetch_operational_runs'],'wrapped');print('original stdout')
  self.assertEqual(out.getvalue(),'original stdout\n');self.assertIs(self.globals['fetch_operational_runs'],self.original);self.assertIs(self.seen[0],self.globals)
  row=json.loads(err.getvalue());self.assertEqual(row['kind'],'runtime_p0_inventory_transport');self.assertEqual(row['status'],'ok');self.assertEqual(row['module_sha256'],hashlib.sha256((HERE/'runtime_p0_inventory_transport.py').read_bytes()).hexdigest());self.assertEqual(row['real_gets'],30)
 def test_original_exception_and_cancellation_identity_survive_restoration(self):
  for error in (ValueError('original'),SystemExit(7),KeyboardInterrupt()):
   with self.subTest(error=type(error).__name__),mock.patch.object(self.launcher,'_backend',return_value=self.helper),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(type(error)) as got:
    with self.hook():raise error
   self.assertIs(got.exception,error);self.assertIs(self.globals['fetch_operational_runs'],self.original)
 def test_fwd_and_paired_never_import_or_install_p0(self):
  for script in ('forward_validation_v2.py','paired_cohort_audit.py'):
   with mock.patch.object(self.launcher,'_backend') as backend:
    with self.hook(script):self.assertIs(self.globals['fetch_operational_runs'],self.original)
   backend.assert_not_called()
 def test_missing_or_unknown_helper_keeps_full_original(self):
  with mock.patch.object(self.launcher,'_backend',return_value=None):
   with self.hook():self.assertIs(self.globals['fetch_operational_runs'],self.original)
  self.assertFalse(self.seen)
 def test_helper_is_bound_to_worker_selected_source_and_launcher_identity(self):
  with mock.patch.dict(sys.modules,{'runtime_process':types.ModuleType('runtime_process')}):worker=load('p0_binding_worker',HERE/'runtime_audit_worker.py')
  self.assertIn('runtime_p0_inventory_transport.py',worker.CLI_HELPERS)
  self.assertEqual(self.launcher.BACKEND_BLOBS['runtime_p0_inventory_transport'],self.launcher._blob(HERE/'runtime_p0_inventory_transport.py'))
 def test_runpy_uses_function_globals_instead_of_namespace_copy(self):
  self.assertTrue(hasattr(self.launcher,'_p0_hook'),'P0 original-main launcher context is absent')
  def main():return 123
  namespace={'main':main,'sentinel':'copied namespace'};path=self.root/'p0_operational_audit.py';path.write_bytes(b'# known fixture')
  with mock.patch.dict(self.launcher.SCRIPT_BLOBS,{'p0_operational_audit.py':self.launcher._blob(path)}),mock.patch.object(self.launcher.runpy,'run_path',return_value=namespace),mock.patch.object(self.launcher,'_hooks',return_value=contextlib.nullcontext()),mock.patch.object(self.launcher,'_p0_hook',return_value=contextlib.nullcontext()) as hook:
   self.assertEqual(self.launcher.run_cli(self.root,'p0_operational_audit.py'),123)
  self.assertIs(hook.call_args.args[2],main.__globals__);self.assertIsNot(hook.call_args.args[2],namespace)
 def test_worker_forwards_fixed_p0_metadata_only_for_p0(self):
  with mock.patch.dict(sys.modules,{'runtime_process':types.ModuleType('runtime_process')}):worker=load('p0_metadata_worker',HERE/'runtime_audit_worker.py')
  row={'kind':'runtime_p0_inventory_transport','implementation':'p0_fresh_inventory_window4_v1','mode':'fresh_bounded_inventory','status':'ok','installed':True,'module_sha256':'a'*64,'window':4,'native_calls':1,'fallback_calls':0,'real_gets':30,'days':29}
  def forwarded(value,stage='p0_operational_audit.py'):
   out=io.StringIO()
   with contextlib.redirect_stdout(out):worker.forward_transport_diagnostics(json.dumps(value),stage)
   return out.getvalue()
  accepted=forwarded(row);self.assertTrue(accepted,'P0 transport receipt is missing');self.assertEqual(json.loads(accepted)['source_kind'],row['kind'])
  for changed in (dict(row,secret='PRIVATE_TOKEN'),dict(row,real_gets=True),dict(row,window=100),dict(row,installed='yes'),dict(row,days=2**32),dict(row,module_sha256='PRIVATE_TOKEN')):self.assertEqual(forwarded(changed),'')
  self.assertEqual(forwarded(row,'forward_validation_v2.py'),'');self.assertEqual(forwarded(row,'paired_cohort_audit.py'),'')


"""Actual launcher/worker/performance-gate closure for immutable raw reads."""
import contextlib,hashlib,importlib.util,io,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest import mock
HERE=Path(__file__).parent
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def receipt():
 return dict(kind='runtime_witness_git_transport',implementation='witness_git_raw_lru_v1',mode='immutable_git_raw_lru',status='ok',installed=True,module_sha256='c'*64,native_calls=1,fallback_calls=0,hits=3,misses=4,successful_reads=4,evictions=0,entries=0,cached_bytes=0,view_invalidations=0,max_bytes=8*1024*1024,max_entries=16,peak_bytes=600,peak_entries=4)

class WitnessLauncherTests(unittest.TestCase):
 def setUp(self):
  self.cli=load('witness_cli_under_test',HERE/'runtime_audit_cli.py')
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.base=types.SimpleNamespace(history_receipts=object(),remote_witnesses=lambda *args,**kwargs: None);self.orig=self.base.remote_witnesses
  self.helper=types.SimpleNamespace(__file__=str(HERE/'runtime_witness_git_transport.py'));self.seen=[]
  @contextlib.contextmanager
  def installed(base,root):
   self.seen.append((base,root,base.history_receipts));base.remote_witnesses='actual_wrapped'
   try:yield {k:v for k,v in receipt().items() if k not in ('kind','mode','status','module_sha256')}
   finally:base.remote_witnesses=self.orig
  self.helper.installed=installed
 def hook(self,script='forward_validation_v2.py'):
  self.assertTrue(hasattr(self.cli,'_witness_hook'),'actual raw Git launcher is missing')
  return self.cli._witness_hook(self.root,script)
 def test_actual_context_restores_original_and_preserves_stdout_and_exception(self):
  for error in (None,ValueError('original'),KeyboardInterrupt()):
   out,err=io.StringIO(),io.StringIO()
   with mock.patch.dict(sys.modules,{'forward_validation':self.base}),mock.patch.object(self.cli,'_backend',return_value=self.helper),contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
    try:
     with self.hook():
      self.assertEqual(self.base.remote_witnesses,'actual_wrapped');print('original stdout')
      if error is not None:raise error
    except BaseException as got:self.assertIs(got,error)
   self.assertIs(self.base.remote_witnesses,self.orig);self.assertEqual(out.getvalue(),'original stdout\n')
   r=json.loads(err.getvalue());self.assertEqual(r['kind'],'runtime_witness_git_transport')
   self.assertEqual(r['module_sha256'],hashlib.sha256(Path(self.helper.__file__).read_bytes()).hexdigest())
   self.assertEqual(r['status'],'ok' if error is None else 'error')
 def test_paired_and_absent_helper_keep_original_route(self):
  with mock.patch.object(self.cli,'_backend') as backend:
   with self.hook('paired_cohort_audit.py'):pass
  backend.assert_not_called()
  with mock.patch.dict(sys.modules,{'forward_validation':self.base}),mock.patch.object(self.cli,'_backend',return_value=None):
   with self.hook():self.assertIs(self.base.remote_witnesses,self.orig)
 def test_real_run_cli_enters_after_native_history_and_restores_all_contexts(self):
  history_marker=object();old_history=self.base.history_receipts
  @contextlib.contextmanager
  def native(*args):
   self.base.history_receipts=history_marker
   try:yield
   finally:self.base.history_receipts=old_history
  def main():
   self.assertEqual(self.base.remote_witnesses,'actual_wrapped');return 123
  path=self.root/'forward_validation_v2.py';path.write_bytes(b'# original fixture')
  with mock.patch.dict(sys.modules,{'forward_validation':self.base}),mock.patch.object(self.cli,'_backend',return_value=self.helper),mock.patch.dict(self.cli.SCRIPT_BLOBS,{'forward_validation_v2.py':self.cli._blob(path)}),mock.patch.object(self.cli.runpy,'run_path',return_value={'main':main}),mock.patch.object(self.cli,'_hooks',side_effect=native),mock.patch.object(self.cli,'_p0_hook',return_value=contextlib.nullcontext()),contextlib.redirect_stderr(io.StringIO()):
   self.assertEqual(self.cli.run_cli(self.root,'forward_validation_v2.py'),123)
  self.assertIs(self.seen[0][2],history_marker);self.assertIs(self.base.history_receipts,old_history);self.assertIs(self.base.remote_witnesses,self.orig)
 def test_selected_worker_and_exact_backend_identity_bind_new_helper(self):
  with mock.patch.dict(sys.modules,{'runtime_process':types.ModuleType('runtime_process')}):w=load('witness_worker_bound',HERE/'runtime_audit_worker.py')
  self.assertIn('runtime_witness_git_transport.py',w.CLI_HELPERS)
  self.assertEqual(self.cli.BACKEND_BLOBS.get('runtime_witness_git_transport'),self.cli._blob(HERE/'runtime_witness_git_transport.py'))
 def test_worker_forwards_only_fixed_bounded_metadata_for_original_fwd_p0(self):
  with mock.patch.dict(sys.modules,{'runtime_process':types.ModuleType('runtime_process')}):w=load('witness_worker_receipt',HERE/'runtime_audit_worker.py')
  def forward(r,stage):
   out=io.StringIO()
   with contextlib.redirect_stdout(out):w.forward_transport_diagnostics(json.dumps(r),stage)
   return out.getvalue()
  for stage in ('forward_validation_v2.py','p0_operational_audit.py'):
   self.assertTrue(forward(receipt(),stage))
  self.assertEqual(forward(receipt(),'paired_cohort_audit.py'),'')
  for change in ({'secret':'PRIVATE'},{'hits':True},{'peak_bytes':2**32},{'installed':'true'},{'max_entries':17}):
   self.assertEqual(forward(dict(receipt(),**change),'forward_validation_v2.py'),'')

class WitnessProfileReceiptTests(unittest.TestCase):
 def setUp(self):self.p=load('witness_profile_under_test',HERE/'runtime_audit_pipeline_profile.py');self.base={'kind':'runtime_publication_state_validation','mode':'native_batch','status':'ok','validation_status':'ok','native_batch_calls':1}
 def check(self,*rows):
  self.assertIn('expected_witness_sha256',__import__('inspect').signature(self.p.validate_native_diagnostics).parameters,'actual performance gate is missing')
  return self.p.validate_native_diagnostics('\n'.join(json.dumps(r) for r in rows),'forward_validation_v2.py',expected_witness_sha256='c'*64)
 def test_exactly_one_actual_native_bounded_reuse_receipt_is_required(self):
  self.assertIn(receipt(),self.check(self.base,receipt()))
  for rows in ((self.base,),(self.base,receipt(),receipt())):
   with self.assertRaises(self.p.ProfileError):self.check(*rows)
 def test_view_change_fallback_wrong_sha_unknown_fields_and_no_real_hits_fail(self):
  for change in ({'view_invalidations':1},{'fallback_calls':1},{'module_sha256':'d'*64},{'hits':0},{'native_calls':0},{'installed':False},{'peak_bytes':8*1024*1024+1},{'max_entries':17},{'entries':1},{'cached_bytes':1},{'secret':'PRIVATE'}):
   with self.subTest(change=change),self.assertRaises(self.p.ProfileError):self.check(self.base,dict(receipt(),**change))

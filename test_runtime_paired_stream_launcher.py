"""Owned paired stream wiring; full original source definitions, tiny Git only."""
import contextlib,hashlib,importlib.util,io,json,os,shutil,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

fixtures=load('stream_launcher_fixtures',HERE/'test_runtime_paired_git_stream.py')

class PairedStreamLauncherTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.RealGitTests('test_fresh_repeated_reads_preserve_bytes_and_call_order')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.root;self.launcher=load('stream_launcher_subject',HERE/'runtime_audit_cli.py')
        shutil.copyfile(HERE/'runtime_paired_git_stream.py',self.root/'runtime_paired_git_stream.py')
        self.backend=load('runtime_paired_git_stream',self.root/'runtime_paired_git_stream.py')
        self.modules=patch.dict(sys.modules,dict(self.f.modules,runtime_paired_git_stream=self.backend))
        self.modules.start();self.addCleanup(self.modules.stop)
    def hook(self,script='paired_cohort_audit.py',namespace=None):
        self.assertTrue(hasattr(self.launcher,'_paired_stream_hook'),'new original paired transport hook missing')
        return self.launcher._paired_stream_hook(self.root,script,self.f.main if namespace is None else namespace)
    def test_real_original_context_fresh_bytes_and_authenticated_receipt(self):
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            with self.hook():
                for path in (self.f.path,self.f.prepared,self.f.path):
                    self.assertEqual(self.f.show(path),self.f.original(self.root,'show',self.f.head+':'+path))
                print('unchanged original stdout')
        self.assertEqual(out.getvalue(),'unchanged original stdout\n')
        row=json.loads(err.getvalue())
        self.assertEqual(row['kind'],'runtime_paired_git_stream')
        self.assertEqual(row['module_sha256'],hashlib.sha256((HERE/'runtime_paired_git_stream.py').read_bytes()).hexdigest())
        self.assertEqual((row['requests'],row['verified_reads'],row['processes'],row['fallback_calls']),(3,3,1,0))
        self.assertTrue(row['reaped']);self.assertTrue(row['reader_joined']);self.assertFalse(row['disabled'])
        self.assertIs(self.f.pub.command,self.f.original)
    def test_fake_script_even_with_mocked_script_allowlist_is_silent_before_import(self):
        path=self.root/'paired_cohort_audit.py';path.write_bytes(b'# fake AST fixture\n')
        with patch.dict(self.launcher.SCRIPT_BLOBS,{path.name:self.launcher._blob(path)}),patch.object(self.launcher,'_backend') as backend,contextlib.redirect_stderr(io.StringIO()) as err:
            with self.hook():pass
        backend.assert_not_called();self.assertEqual(err.getvalue(),'')
    def test_exact_source_with_fake_live_main_context_is_silent_before_import(self):
        for namespace in ({'main':lambda:0},dict(self.f.main,verify_pair=lambda *args:True)):
            with self.subTest(),patch.object(self.launcher,'_backend') as backend,contextlib.redirect_stderr(io.StringIO()) as err:
                with self.hook(namespace=namespace):pass
            backend.assert_not_called();self.assertEqual(err.getvalue(),'')
    def test_other_cli_never_imports_stream(self):
        for script in ('forward_validation_v2.py','p0_operational_audit.py'):
            with patch.object(self.launcher,'_backend') as backend:
                with self.hook(script):pass
            backend.assert_not_called()
    def test_original_exception_and_cancel_restores_then_reports_error(self):
        for error in (ValueError('original'),KeyboardInterrupt(),SystemExit(7)):
            with self.subTest(error=type(error).__name__),contextlib.redirect_stderr(io.StringIO()) as err,self.assertRaises(type(error)) as got:
                with self.hook():self.f.show();raise error
            self.assertIs(got.exception,error);self.assertIs(self.f.pub.command,self.f.original)
            row=json.loads(err.getvalue());self.assertEqual(row['status'],'error')
            self.assertTrue(row['reaped']);self.assertTrue(row['reader_joined'])
    def test_helper_selected_head_binding_and_backend_blob_identity(self):
        worker=load('stream_binding_worker',HERE/'runtime_audit_worker.py')
        self.assertIn('runtime_paired_git_stream.py',worker.CLI_HELPERS)
        self.assertEqual(self.launcher.BACKEND_BLOBS['runtime_paired_git_stream'],self.launcher._blob(HERE/'runtime_paired_git_stream.py'))
    def test_worker_trust_context_uses_only_explicit_clone_source_and_restores(self):
        worker=load('stream_trust_worker',HERE/'runtime_audit_worker.py')
        self.assertTrue(hasattr(worker,'paired_stream_source'),'explicit paired shared-source trust context missing')
        other=self.root/'clone';fixtures.git(self.root,'-c','core.longpaths=true','clone','--shared','--no-checkout','-q',str(self.root),str(other))
        key='DOGE_AUDIT_STREAM_TRUSTED_OBJECT_DIRECTORY'
        with patch.dict(os.environ,{key:'prior caller value'}):
            with worker.paired_stream_source(self.root,other):
                self.assertEqual(Path(os.environ[key]).resolve(),(self.root/'.git/objects').resolve())
            self.assertEqual(os.environ[key],'prior caller value')
            with worker.paired_stream_source(self.root,other/'unknown'):
                self.assertNotIn(key,os.environ)
            self.assertEqual(os.environ[key],'prior caller value')
            (other/'.git/objects/info/alternates').write_bytes(b'\xff')
            with worker.paired_stream_source(self.root,other):self.assertNotIn(key,os.environ)
            self.assertEqual(os.environ[key],'prior caller value')

if __name__=='__main__':unittest.main(verbosity=2)

"""Miniature real Git boundaries; never execute the paired business CLI."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PROJECT = next((p for p in HERE.parents if (p/'continuation-20261110/pinned').is_dir()),None)
sources=(HERE,HERE/'reference',HERE.parent/'pinned')+(
    (PROJECT/'continuation-20261110/pinned',) if PROJECT is not None else ())
PINNED=next((path for path in sources if all((path/name).is_file() for name in
    ('paired_cohort_audit.py','paired_forward_cohort.py','publish_forward_evidence.py'))),HERE)
HELPER = HERE/'runtime_paired_git_stream.py'
runtime_path=HERE/'runtime_process.py'
if not runtime_path.is_file() and PROJECT is not None:runtime_path=PROJECT/'continuation-20261110/integration/runtime_process.py'
runtime_spec=importlib.util.spec_from_file_location('runtime_process',runtime_path)
runtime=importlib.util.module_from_spec(runtime_spec);runtime_spec.loader.exec_module(runtime)
sys.modules['runtime_process']=runtime
if HELPER.exists():
    spec = importlib.util.spec_from_file_location('runtime_paired_git_stream', HELPER)
    stream = importlib.util.module_from_spec(spec); spec.loader.exec_module(stream)
else:
    stream = None


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True).stdout


class ProtectedGitDirectory:
    """Retain new Git fixtures; project Git is never eligible for cleanup."""
    def __init__(self,dir=None):
        base=(PROJECT if PROJECT is not None else Path(tempfile.gettempdir()))/'v11-protected-git-fixtures'
        path=base/uuid.uuid4().hex[:12]
        path.mkdir(parents=True)
        self.name=str(path)
    def cleanup(self):pass


def original_modules(root):
    modules={}
    for name in ('direction_forward_v42','forward_validation','quote_evidence_v2',
                 'p1_adaptive_v2','quote_capture_retry','forward_execution_evidence',
                 'paired_selection_v1','p1_model_competition'):
        modules[name]=types.ModuleType(name)
    modules['quote_evidence_v2'].BINDINGS='fixture-bindings'
    for name in ('publish_forward_evidence','paired_forward_cohort','paired_cohort_audit'):
        path=root/(name+'.py');shutil.copyfile(PINNED/(name+'.py'),path)
        module=types.ModuleType(name);module.__file__=str(path)
        with patch.dict(sys.modules,modules):
            exec(compile(path.read_bytes(),str(path),'exec',dont_inherit=True),module.__dict__)
        modules[name]=module
    return modules


class RealGitTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(stream, 'owned stream helper has not been implemented')
        self.tmp = ProtectedGitDirectory(dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'fixture')
        git(self.root, 'config', 'user.email', 'fixture@invalid')
        git(self.root, 'config', 'core.autocrlf', 'false')
        git(self.root, 'config', 'core.longpaths', 'true')
        self.body = b'{"fixture":1}\r\n\x00tail\n'
        self.path = 'data/paired_forward_v1/records/FIX_1.json'
        self.prepared = 'data/paired_forward_v1/prepared/'+'a'*64+'.json'
        for name, raw in ((self.path,self.body),(self.prepared,b'no-final-newline')):
            path = self.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)
        git(self.root,'add','--all');git(self.root,'-c','core.autocrlf=false','commit','-qm','fixture')
        self.head = git(self.root,'rev-parse','HEAD').decode().strip()
        # Only imports/module definitions execute. No business function executes.
        self.modules=original_modules(self.root)
        self.pub=self.modules['publish_forward_evidence']
        self.main=self.modules['paired_cohort_audit'].main.__globals__
        self.original=self.pub.command

    def installed(self, **kwargs):
        return stream.installed(self.root,self.main,**kwargs)

    def show(self,path=None):
        return self.pub.command(self.root,'show',self.head+':'+(path or self.path))

    def test_fresh_repeated_reads_preserve_bytes_and_call_order(self):
        with self.installed() as report:
            self.assertTrue(report['installed'])
            for path in (self.path,self.prepared,self.path):
                self.assertEqual(self.show(path),self.original(self.root,'show',self.head+':'+path))
            self.assertEqual(report['requests'],3)
            self.assertEqual(report['processes'],1)
            self.assertEqual(report['fallback_calls'],0)
        self.assertIs(self.pub.command,self.original)
        self.assertTrue(report['reaped']);self.assertTrue(report['reader_joined'])

    def test_noneligible_routes_and_native_history_forward_original(self):
        with self.installed() as report:
            for args in (('show','HEAD:'+self.path),('show',self.head+':data/other.json'),
                         ('show','--format=','--unified=0','--no-renames',self.head,'--',self.path),
                         ('log','--reverse','--format=%H','--',self.path)):
                try: want=self.original(self.root,*args)
                except subprocess.CalledProcessError as expected:
                    with self.assertRaises(subprocess.CalledProcessError) as got:self.pub.command(self.root,*args)
                    self.assertEqual((got.exception.returncode,got.exception.stdout,got.exception.stderr),
                                     (expected.returncode,expected.stdout,expected.stderr))
                else:self.assertEqual(self.pub.command(self.root,*args),want)
            self.assertEqual(report['requests'],0)

    def test_missing_replays_current_original_and_disables_later(self):
        missing=self.head+':data/paired_forward_v1/records/MISSING.json'
        try:self.original(self.root,'show',missing)
        except subprocess.CalledProcessError as expected:
            with self.installed() as report:
                with self.assertRaises(subprocess.CalledProcessError) as got:self.pub.command(self.root,'show',missing)
                self.assertEqual((got.exception.cmd,got.exception.returncode,got.exception.stdout,got.exception.stderr),
                                 (expected.cmd,expected.returncode,expected.stdout,expected.stderr))
                self.assertEqual(self.show(),self.body)
                self.assertEqual(report['requests'],1);self.assertEqual(report['fallback_calls'],2)
            self.assertTrue(report['reaped'])

    def test_complete_compiled_module_identity_and_live_bindings_required(self):
        paired=self.modules['paired_cohort_audit']
        mutations=(('main',lambda:0),('verify_pair',lambda *args:True),('json',types.SimpleNamespace()),
                   ('OrderedDict',dict),('datetime',None),('COMPARATORS',{}),('max',lambda *args:0))
        for name,replacement in mutations:
            with self.subTest(name=name),patch.dict(paired.__dict__,{name:replacement}):
                with self.installed() as report:self.assertFalse(report['installed'])
        with patch.object(paired.evaluate,'__defaults__',(None,None)):
            with self.installed() as report:self.assertFalse(report['installed'])
        with patch.object(self.pub.command,'__defaults__',(None,)):
            with self.installed() as report:self.assertFalse(report['installed'])

    def test_changed_source_blob_is_ineligible_even_with_same_live_code(self):
        path=self.root/'paired_cohort_audit.py';path.write_bytes(path.read_bytes()+b'\n# unknown\n')
        with self.installed() as report:self.assertFalse(report['installed'])

    def test_git_environment_redirect_is_rejected(self):
        for key,value in (('GIT_DIR',str(self.root/'.git')),('GIT_CONFIG_COUNT','0'),
                          ('GIT_ALTERNATE_OBJECT_DIRECTORIES',str(self.root/'.git/objects'))):
            with self.subTest(key=key),patch.dict(os.environ,{key:value}):
                with self.installed() as report:self.assertFalse(report['installed'])

    def test_replacement_grafts_promisor_and_config_include_rejected(self):
        for relative,body in (('info/grafts',b'bad\n'),('refs/replace/'+'1'*40,b'2'*40+b'\n'),
                              ('objects/info/alternates',b'/untrusted\n')):
            path=self.root/'.git'/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
            try:
                with self.installed() as report:self.assertFalse(report['installed'])
            finally:path.unlink()
        git(self.root,'config','remote.origin.promisor','true')
        with self.installed() as report:self.assertFalse(report['installed'])
        git(self.root,'config','--unset','remote.origin.promisor')
        git(self.root,'config','include.path',str(self.root/'external-config'))
        with self.installed() as report:self.assertFalse(report['installed'])

    def test_view_drift_disables_stream_before_next_request(self):
        with self.installed() as report:
            self.assertEqual(self.show(),self.body)
            git(self.root,'config','fixture.changed','yes')
            self.assertEqual(self.show(),self.body)
            self.assertEqual(report['requests'],1);self.assertEqual(report['fallback_calls'],1)

    def test_head_and_index_changes_do_not_trigger_per_request_full_scan(self):
        with self.installed() as report:
            self.assertEqual(self.show(),self.body)
            git(self.root,'update-ref','refs/heads/unrelated',self.head)
            (self.root/'.git/index').write_bytes(b'irrelevant index for exact blob lookup')
            self.assertEqual(self.show(),self.body)
            self.assertEqual(report['requests'],2);self.assertEqual(report['fallback_calls'],0)

    def test_shared_checkout_only_accepts_explicit_single_trusted_alternate(self):
        other=self.root/'shared';git(self.root,'-c','core.longpaths=true','clone','--shared','-q',str(self.root),str(other))
        # This call exercises repository eligibility without any business module.
        with stream.installed(other,self.main) as report:self.assertFalse(report['installed'])
        # Full source location authentication still prevents using another root's globals.
        with stream.installed(other,self.main,trusted_object_directory=self.root/'.git/objects') as report:
            self.assertFalse(report['installed'])
        modules=original_modules(other);pub=modules['publish_forward_evidence']
        with stream.installed(other,modules['paired_cohort_audit'].main.__globals__,
                              trusted_object_directory=self.root/'.git/objects') as report:
            self.assertTrue(report['installed'])
            self.assertEqual(pub.command(other,'show',self.head+':'+self.path),self.body)
            self.assertEqual(report['requests'],1);self.assertEqual(report['fallback_calls'],0)

    def fake_child(self,body):
        original_popen=subprocess.Popen
        script=self.root/'child.py';script.write_text(body,encoding='utf-8')
        def spawn(args,**kwargs):
            if len(args)>2 and args[1]=='cat-file' and args[2].startswith('--batch='):
                return original_popen([sys.executable,'-I','-B',str(script)],**kwargs)
            return original_popen(args,**kwargs)
        original_batch=stream._Batch
        def batch(root,report):
            # Fault injection begins after pure API identity eligibility.
            with patch.object(subprocess,'Popen',spawn):return original_batch(root,report)
        return patch.object(stream,'_Batch',batch)

    def test_bad_frame_timeout_exit_and_oversize_replay_only_current_call(self):
        oid=hashlib.sha1(b'blob '+str(len(self.body)).encode()+b'\0'+self.body).hexdigest()
        frames=(b'unknown missing\n',b'0'*40+b' blob 1\nx\n',
                oid.encode()+b' tree 1\nx\n',oid.encode()+b' blob 999999999999\n',
                oid.encode()+b' blob -1\n',oid.encode()+b' blob 3 extra\nabc\n',
                oid.encode()+b' blob 3\nab',oid.encode()+b' blob 3\nabcX')
        scripts=['import sys\nsys.stdin.buffer.readline()\nsys.stdout.buffer.write('+repr(frame)+');sys.stdout.buffer.flush()\n' for frame in frames]
        scripts+=['import sys,time\nsys.stdin.buffer.readline()\ntime.sleep(20)\n',
                  'import sys\nsys.stdin.buffer.readline()\nsys.exit(7)\n']
        for script in scripts:
            with self.subTest(script=script[-50:]),self.fake_child(script),self.installed(request_timeout=.05) as report:
                self.assertEqual(self.show(),self.body)
                self.assertEqual(self.show(),self.body)
                self.assertEqual(report['requests'],1);self.assertEqual(report['fallback_calls'],2)
            self.assertTrue(report['reaped']);self.assertTrue(report['reader_joined'])

    def test_keyboardinterrupt_and_systemexit_keep_identity_restore_and_reap(self):
        for error in (KeyboardInterrupt('cancel'),SystemExit(17)):
            report=None
            with self.assertRaises(type(error)) as got:
                with self.installed() as report:
                    self.assertEqual(self.show(),self.body)
                    raise error
            self.assertIs(got.exception,error);self.assertIs(self.pub.command,self.original)
            self.assertTrue(report['reaped']);self.assertTrue(report['reader_joined'])

    def test_signal_handlers_restore_and_term_cleans_owned_child(self):
        before={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
        with self.assertRaises(BaseException) as got:
            with self.installed() as report:
                self.assertEqual(self.show(),self.body)
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM,None)
        self.assertEqual(type(got.exception).__name__,'RuntimeCancelled')
        self.assertEqual(got.exception.signum,signal.SIGTERM)
        self.assertEqual({s:signal.getsignal(s) for s in before},before)
        self.assertTrue(report['reaped']);self.assertTrue(report['reader_joined'])

    def test_no_reader_thread_leaks_after_success(self):
        before={t.ident for t in threading.enumerate()}
        with self.installed() as report:self.assertEqual(self.show(),self.body)
        self.assertEqual({t.ident for t in threading.enumerate()},before)

    def test_valid_hash_with_wrong_request_marker_replays_original(self):
        oid=hashlib.sha1(b'blob '+str(len(self.body)).encode()+b'\0'+self.body).hexdigest()
        wrong=('PAIR-BLOB-'+'0'*32+'-999').encode()
        frame=oid.encode()+b' blob '+str(len(self.body)).encode()+b' '+wrong+b'\n'+self.body+b'\n'
        script='import sys,time\nsys.stdin.buffer.readline()\nsys.stdout.buffer.write('+repr(frame)+');sys.stdout.buffer.flush()\ntime.sleep(20)\n'
        with self.fake_child(script),self.installed(request_timeout=.1) as report:
            self.assertEqual(self.show(),self.body)
            self.assertEqual(report['requests'],1);self.assertEqual(report['fallback_calls'],1)
        self.assertTrue(report['reaped']);self.assertTrue(report['reader_joined'])

    def test_reader_start_failure_replays_original_without_leaking_child(self):
        start=threading.Thread.start
        def fail_owned(thread):
            if thread.name=='paired-git-stream-reader':raise RuntimeError('cannot start reader')
            return start(thread)
        with patch.object(threading.Thread,'start',fail_owned):
            with self.installed() as report:
                self.assertEqual(self.show(),self.body)
                self.assertEqual(report['fallback_calls'],1)
        self.assertTrue(report['reaped']);self.assertTrue(report['reader_joined'])

    def test_nonowner_thread_uses_original_without_touching_stream(self):
        result=[]
        with self.installed() as report:
            worker=threading.Thread(target=lambda:result.append(self.show()))
            worker.start();worker.join(timeout=2)
            self.assertFalse(worker.is_alive());self.assertEqual(result,[self.body])
            self.assertEqual(report['requests'],0)

    def test_cancellation_during_child_construction_does_not_lose_ownership(self):
        popen=subprocess.Popen;children=[]
        def spawn(args,**kwargs):
            child=popen(args,**kwargs)
            if len(args)>2 and args[1]=='cat-file' and args[2].startswith('--batch='):
                children.append(child)
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM,None)
            return child
        try:
            original_batch=stream._Batch
            def batch(root,report):
                with patch.object(subprocess,'Popen',spawn):return original_batch(root,report)
            with patch.object(stream,'_Batch',batch),self.assertRaises(runtime.RuntimeCancelled):
                with self.installed() as report:self.show()
            self.assertEqual(len(children),1)
            self.assertIsNotNone(children[0].poll())
            self.assertIs(self.pub.command,self.original)
            self.assertEqual(report['fallback_calls'],0)
        finally:
            for child in children:
                if child.poll() is None:child.kill();child.wait(timeout=2)
                for handle in (child.stdin,child.stdout):
                    if handle is not None:handle.close()

    def test_view_startup_cancellation_is_owned_and_preserves_signal_identity(self):
        popen=subprocess.Popen;children=[]
        before=signal.getsignal(signal.SIGTERM)
        def spawn(args,**kwargs):
            child=popen(args,**kwargs)
            if args[1:]==['config','--show-origin','-z','--list']:
                children.append(child)
                handler=signal.getsignal(signal.SIGTERM)
                self.assertTrue(callable(handler),'view startup must own cancellation')
                handler(signal.SIGTERM,None)
            return child
        try:
            original_command=runtime.command
            def owned_command(args,**kwargs):
                with patch.object(subprocess,'Popen',spawn):return original_command(args,**kwargs)
            with patch.object(runtime,'command',owned_command),self.assertRaises(runtime.RuntimeCancelled) as got:
                with self.installed():pass
            self.assertEqual(got.exception.signum,signal.SIGTERM)
            self.assertEqual(signal.getsignal(signal.SIGTERM),before)
            self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())
            self.assertIs(self.pub.command,self.original)
        finally:
            for child in children:
                if child.poll() is None:child.kill();child.wait(timeout=2)
                for handle in (child.stdin,child.stdout,child.stderr):
                    if handle is not None:handle.close()

    def test_legal_keyword_root_preserves_original_exception_surface(self):
        try:self.original(root=self.root)
        except subprocess.CalledProcessError as expected:
            with self.installed() as report:
                with self.assertRaises(subprocess.CalledProcessError) as got:self.pub.command(root=self.root)
                self.assertEqual((got.exception.cmd,got.exception.returncode,got.exception.stdout,got.exception.stderr),
                                 (expected.cmd,expected.returncode,expected.stdout,expected.stderr))
                self.assertEqual(report['requests'],0)

    def test_replaced_subprocess_run_live_function_disables_optimization(self):
        with patch.object(subprocess,'run',lambda *args,**kwargs:types.SimpleNamespace(stdout=b'replaced')):
            with self.installed() as report:self.assertFalse(report['installed'])

    def test_replaced_subprocess_popen_live_api_disables_optimization(self):
        popen=subprocess.Popen
        def changed(args,**kwargs):return popen(args,**kwargs)
        with patch.object(subprocess,'Popen',changed):
            with self.installed() as report:self.assertFalse(report['installed'])
        with patch.object(subprocess.Popen,'__exit__',subprocess.Popen.terminate):
            with self.installed() as report:self.assertFalse(report['installed'])

    def test_nonfunction_original_popen_method_is_ineligible(self):
        original=subprocess.Popen._execute_child
        class DelegatingDescriptor:
            def __get__(self,instance,owner):return original.__get__(instance,owner)
        with patch.object(subprocess.Popen,'_execute_child',DelegatingDescriptor()):
            with self.installed() as report:self.assertFalse(report['installed'])

    def test_relevant_view_checks_never_use_unbounded_pathlib_scans_or_reads(self):
        read_bytes=Path.read_bytes;calls=[]
        def bounded_only(path):
            if path.name in ('packed-refs','alternates'):calls.append(path.name)
            return read_bytes(path)
        with (patch.object(Path,'glob',side_effect=AssertionError('unbounded glob')),
                patch.object(Path,'rglob',side_effect=AssertionError('unbounded recursive glob')),
                patch.object(Path,'read_bytes',bounded_only)):
            with self.installed() as report:self.assertTrue(report['installed'])
        self.assertFalse(calls)

    def test_commondir_redirection_is_unsupported(self):
        (self.root/'.git/commondir').write_text('.',encoding='utf-8')
        with self.installed() as report:self.assertFalse(report['installed'])

    def test_unknown_equal_default_is_not_blessed_as_original_none(self):
        class Unknown:
            def __eq__(self,other):return True
        with patch.object(self.modules['paired_cohort_audit'].verify_pair,'__defaults__',(Unknown(),)):
            with self.installed() as report:self.assertFalse(report['installed'])

    def test_unknown_equal_api_defaults_are_not_blessed_as_literals(self):
        class Unknown:
            def __eq__(self,other):return True
        with patch.object(subprocess.Popen.wait,'__defaults__',(Unknown(),)):
            with self.installed() as report:self.assertFalse(report['installed'])
        changed=dict(subprocess.run.__kwdefaults__,timeout=Unknown())
        with patch.object(subprocess.run,'__kwdefaults__',changed):
            with self.installed() as report:self.assertFalse(report['installed'])

    def test_control_files_are_bounded_before_read_and_during_growth(self):
        open_file=Path.open;fstat=os.fstat
        for relative in ('packed-refs','objects/info/alternates'):
            path=self.root/'.git'/relative;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'x'*(1024*1024+1));reads=[];descriptors=set()
            class Handle:
                def __init__(self,handle):self.handle=handle;descriptors.add(handle.fileno())
                def __enter__(self):return self
                def __exit__(self,*args):self.handle.close()
                def fileno(self):return self.handle.fileno()
                def read(self,count=-1):reads.append(count);return self.handle.read(count)
            def tracked(target,*args,**kwargs):
                handle=open_file(target,*args,**kwargs)
                return Handle(handle) if target==path else handle
            def growing(fd):
                result=fstat(fd)
                if fd in descriptors:
                    values=list(result);values[6]=0
                    return os.stat_result(values)
                return result
            try:
                with self.subTest(relative=relative),patch.object(Path,'open',tracked):
                    with self.installed(trusted_object_directory=self.root/'.git/objects') as report:
                        self.assertFalse(report['installed'])
                    self.assertEqual(reads,[])
                    with patch.object(os,'fstat',growing):
                        with self.installed(trusted_object_directory=self.root/'.git/objects') as report:
                            self.assertFalse(report['installed'])
                    self.assertEqual(reads,[1024*1024+1])
            finally:path.unlink()

    def test_explicit_no_lazy_fetch_one_is_supported_but_other_values_are_unknown(self):
        with patch.dict(os.environ,{'GIT_NO_LAZY_FETCH':'1'}),self.installed() as report:
            self.assertTrue(report['installed']);self.assertEqual(self.show(),self.body)
            self.assertEqual(report['requests'],1);self.assertEqual(report['fallback_calls'],0)
        with patch.dict(os.environ,{'GIT_NO_LAZY_FETCH':'0'}),self.installed() as report:
            self.assertFalse(report['installed'])


if __name__=='__main__':unittest.main(verbosity=2)

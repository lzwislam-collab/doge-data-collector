"""Real-Git frozen remote function fixtures; no model, CLI or HTTP is run."""
from collections import Counter
import contextlib
import csv
from datetime import datetime, timedelta, timezone
import importlib.util
import io
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REFERENCE = HERE if (HERE/'forward_validation.py').exists() else HERE/'reference'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RawWitnessGit(unittest.TestCase):
    def setUp(self):
        helper = HERE/'runtime_witness_git_transport.py'
        self.assertTrue(helper.is_file(), 'bounded raw witness Git transport missing')
        self.helper = load('witness_raw_subject', helper)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.git('init', '-q')
        self.git('config', 'user.name', 'test')
        self.git('config', 'user.email', 'test@example.invalid')
        for name in ('forward_validation.py', 'direction_forward_v42.py'):
            shutil.copyfile(REFERENCE/name, self.root/name)
        self.csv('data/direction_v42/forward_registry.csv',
            ['forecast_id','issued_at_utc','target_utc'],
            [{'forecast_id': name,'issued_at_utc':'2026-11-10T00:00:00+00:00',
              'target_utc':'2026-11-10T01:00:00+00:00'} for name in ('TEST_f1','TEST_f2')])
        self.csv('data/stage5/forward_validation/verified_run_metadata.csv',
                 ['forecast_id','head_sha','created_at','first_verified_at_utc'], [])
        self.commit('original public data')
        self.public = self.git('rev-parse', 'HEAD').strip()
        self.csv('data/stage5/forward_validation/verified_run_metadata.csv',
                 ['forecast_id','head_sha','created_at','first_verified_at_utc'],
                 [{'forecast_id':'7','head_sha':self.public,
                   'created_at':'2026-11-10T00:00:01+00:00',
                   'first_verified_at_utc':'2026-11-10T00:00:01+00:00'}])
        self.commit('original metadata')
        self.base = types.ModuleType('forward_validation')
        self.base.__file__ = str(self.root/'forward_validation.py')
        self.base.__dict__['__name__'] = 'forward_validation'
        direction = types.ModuleType('direction_forward_v42')
        direction.ROOT = self.root; direction.FCOLS = []; direction.SCOLS = []; direction.MODEL = 'TEST_ONLY'
        # Execute only the unchanged required function code from the whole file.
        whole = compile((self.root/'direction_forward_v42.py').read_bytes(),
                        str(self.root/'direction_forward_v42.py'), 'exec', dont_inherit=True)
        env = dict(csv=csv,datetime=datetime,timedelta=timedelta,timezone=timezone)
        for name, defaults in (('read_rows',None),('utc',None),('write',None)):
            code = next(x for x in whole.co_consts if isinstance(x,types.CodeType) and x.co_name==name)
            setattr(direction,name,types.FunctionType(code,env,name,argdefs=defaults))
        quality = types.ModuleType('stage5_forward_quality')
        quality.evaluate = lambda *a,**k: self.fail('science evaluation forbidden in unit fixture')
        audit = types.ModuleType('stage5_70pct_audit'); audit.wilson_lower = quality.evaluate
        with patch.dict(sys.modules, {'direction_forward_v42':direction,
                'stage5_forward_quality':quality,'stage5_70pct_audit':audit}):
            exec(compile((self.root/'forward_validation.py').read_bytes(),
                         str(self.root/'forward_validation.py'),'exec',dont_inherit=True), self.base.__dict__)
        self.original_git = self.base.git; self.original_remote = self.base.remote_witnesses
        self.scientific_history = self.base.history_receipts
        self.history_calls = []
        def history(*args):
            self.history_calls.append(self.base.git is self.original_git)
            return {}
        self.base.history_receipts = history
        self.original_history = history
        self.registry = {row['forecast_id']:row for row in csv.DictReader(
            io.StringIO((self.root/self.base.REG).read_text()))}
        self.prior = {name: {'forecast_id':name,'run_id':'7',
            'observed_public_utc':'2026-11-10T00:00:01+00:00','head_sha':self.public,
            'run_url':'https://github.com/lzwislam-collab/doge-data-collector/actions/runs/7'}
            for name in self.registry}
        self.runs = [{'id':7,'head_sha':self.public,'created_at':'2026-11-10T00:00:01+00:00'}]

    def git(self,*args):
        return subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True,
                              text=True,encoding='utf8',timeout=20).stdout

    def csv(self,name,columns,rows):
        path = self.root/name; path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',newline='',encoding='utf8') as stream:
            writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader();writer.writerows(rows)

    def commit(self,message):
        self.git('add','.');self.git('commit','-qm',message)

    def call(self):
        with patch.object(self.base,'github_json',side_effect=AssertionError('HTTP forbidden')):
            return self.base.remote_witnesses(self.root,self.registry,self.prior,self.runs)

    def test_original_full_function_results_equal_and_only_exact_blob_show_reused(self):
        expected=self.call(); calls=[]; original_run=subprocess.run
        def observed(args,*a,**kw):
            calls.append(tuple(args));return original_run(args,*a,**kw)
        with patch.object(subprocess,'run',side_effect=observed):
            with self.helper.installed(self.base,self.root) as report:
                self.assertEqual(self.call(),expected)
        shows=[x for x in calls if len(x)==3 and x[1]=='show' and x[2]==self.public+':'+self.base.REG]
        ancestors=[x for x in calls if 'merge-base' in x]
        self.assertEqual(len(shows),1)
        self.assertEqual(len(ancestors),3)
        self.assertEqual(report['hits'],2)
        self.assertEqual(report['native_calls'],1)
        self.assertEqual(self.history_calls,[True,True])
        self.assertIs(self.base.git,self.original_git)
        self.assertIs(self.base.remote_witnesses,self.original_remote)
        self.assertIs(self.base.history_receipts,self.original_history)

    def test_every_original_remote_invocation_has_a_fresh_cache(self):
        calls=[]; original_run=subprocess.run
        def observed(args,*a,**kw):
            calls.append(tuple(args));return original_run(args,*a,**kw)
        with patch.object(subprocess,'run',side_effect=observed):
            with self.helper.installed(self.base,self.root) as report:
                self.call();self.call()
        self.assertEqual(sum(x==('git','show',self.public+':'+self.base.REG) for x in calls),2)
        self.assertEqual(report['native_calls'],2)
        self.assertEqual(report['hits'],4)

    def test_unknown_original_reader_identity_does_not_install(self):
        self.base.git=lambda *a:'untrusted'
        previous=self.base.git
        with self.helper.installed(self.base,self.root) as report:
            self.assertIs(self.base.git,previous)
            self.assertIs(self.base.remote_witnesses,self.original_remote)
        self.assertFalse(report['installed'])

    def test_source_change_outside_function_remains_ineligible(self):
        with (self.root/'forward_validation.py').open('ab') as stream:stream.write(b'\n# changed\n')
        with self.helper.installed(self.base,self.root) as report:
            self.assertIs(self.base.remote_witnesses,self.original_remote)
        self.assertFalse(report['installed'])

    def test_unknown_git_environment_and_grafts_use_full_original(self):
        for mode in ('env','grafts','replace'):
            with self.subTest(mode=mode):
                graft=self.root/'.git/info/grafts'; replacement=self.root/'.git/refs/replace'
                if mode=='grafts':graft.write_text('')
                if mode=='replace':replacement.mkdir()
                try:
                    context=patch.dict('os.environ',{'GIT_WITNESS_TEST_UNRECOGNIZED':'1'}) if mode=='env' else contextlib.nullcontext()
                    with context,self.helper.installed(self.base,self.root) as report:
                        self.assertEqual(self.call(),self.prior)
                    self.assertEqual(report['native_calls'],0)
                    self.assertEqual(report['hits'],0)
                finally:
                    if mode=='grafts':graft.unlink()
                    if mode=='replace':replacement.rmdir()

    def test_mid_invocation_view_change_discards_old_raw_and_never_hits(self):
        changed=[]; original_run=subprocess.run
        def observed(args,*a,**kw):
            done=original_run(args,*a,**kw)
            if args==['git','show',self.public+':'+self.base.REG] and not changed:
                changed.append(True)
                with (self.root/'.git/config').open('a') as stream:stream.write('\n[test]\n changed=true\n')
            return done
        with patch.object(subprocess,'run',side_effect=observed):
            with self.helper.installed(self.base,self.root) as report:
                self.assertEqual(self.call(),self.prior)
        self.assertEqual(report['hits'],0)
        self.assertEqual(report['view_invalidations'],1)

    def test_original_history_exception_and_cancellation_restore_all_globals(self):
        for error in (ValueError('original history'),SystemExit(9),KeyboardInterrupt()):
            with self.subTest(kind=type(error).__name__):
                def failure(*args):raise error
                self.base.history_receipts=failure
                with self.assertRaises(type(error)) as got:
                    with self.helper.installed(self.base,self.root):self.call()
                self.assertIs(got.exception,error)
                self.assertIs(self.base.git,self.original_git)
                self.assertIs(self.base.remote_witnesses,self.original_remote)
                self.assertIs(self.base.history_receipts,failure)

    def test_oversize_success_stays_uncached_without_changing_original(self):
        with patch.object(self.helper,'MAX_BYTES',32):
            with self.helper.installed(self.base,self.root) as report:
                self.assertEqual(self.call(),self.prior)
        self.assertEqual(report['hits'],0)
        self.assertEqual(report['cached_bytes'],0)

    def test_actual_native_history_remains_native_and_sees_original_git(self):
        publisher=types.ModuleType('publish_forward_evidence')
        publisher.__file__=str(self.root/'publish_forward_evidence.py')
        shutil.copyfile(REFERENCE/'publish_forward_evidence.py',publisher.__file__)
        reports=[];identities=[]
        with patch.dict(sys.modules,{'forward_validation':self.base,'publish_forward_evidence':publisher}):
            native=load('actual_publication_native',REFERENCE/'runtime_publication_state.py')
        def history(*args):
            identities.append(self.base.git is self.original_git)
            self.base.history_receipts=self.scientific_history
            diagnostics={}
            try:return native.history_receipts(*args,diagnostics=diagnostics)
            finally:
                reports.append(diagnostics);self.base.history_receipts=history
        self.base.history_receipts=history
        with self.helper.installed(self.base,self.root) as report:
            self.assertEqual(self.call(),self.prior)
        self.assertEqual(identities,[True])
        self.assertEqual(reports[0]['mode'],'native_batch')
        self.assertGreater(reports[0]['native_batch_calls'],0)
        self.assertEqual(reports[0]['status'],'ok')
        self.assertGreater(report['hits'],0)
        self.assertIs(self.base.history_receipts,history)

    def test_successful_empty_output_is_cached_as_data_not_truth(self):
        # An empty protocol blob is valid text; its eventual predicate is untouched.
        path=self.root/self.base.PROTOCOL;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('')
        self.commit('empty protocol fixture')
        head=self.git('rev-parse','HEAD').strip()
        report=dict(hits=0,misses=0,successful_reads=0,evictions=0,entries=0,cached_bytes=0,
                    peak_bytes=0,peak_entries=0,view_invalidations=0)
        view=self.helper._View(self.root,self.original_git)
        reader=self.helper._RawReads(self.root,self.original_git,view,report)
        for _ in range(2):self.assertEqual(reader.read(self.root,'show',head+':'+self.base.PROTOCOL),'')
        self.assertEqual(report['hits'],1)

    def test_missing_object_failure_is_never_cached_or_replaced(self):
        report=dict(hits=0,misses=0,successful_reads=0,evictions=0,entries=0,cached_bytes=0,
                    peak_bytes=0,peak_entries=0,view_invalidations=0)
        reader=self.helper._RawReads(self.root,self.original_git,
                    self.helper._View(self.root,self.original_git),report)
        for _ in range(2):
            with self.assertRaises(subprocess.CalledProcessError) as got:
                reader.read(self.root,'show','a'*40+':'+self.base.REG)
            self.assertEqual(got.exception.cmd,['git','show','a'*40+':'+self.base.REG])
        self.assertEqual(report['successful_reads'],0)
        self.assertEqual(report['hits'],0)

    def test_head_mutable_other_paths_and_git_predicates_always_delegate(self):
        report=dict(hits=0,misses=0,successful_reads=0,evictions=0,entries=0,cached_bytes=0,
                    peak_bytes=0,peak_entries=0,view_invalidations=0)
        reader=self.helper._RawReads(self.root,self.original_git,
                    self.helper._View(self.root,self.original_git),report)
        calls=[]; original_run=subprocess.run
        def observed(args,*a,**kw):calls.append(tuple(args));return original_run(args,*a,**kw)
        with patch.object(subprocess,'run',side_effect=observed):
            for _ in range(2):
                reader.read(self.root,'show','HEAD:'+self.base.REG)
                reader.read(self.root,'show',self.public+':forward_validation.py')
                reader.read(self.root,'merge-base','--is-ancestor',self.public,'HEAD')
        self.assertEqual(len(calls),6)
        self.assertEqual(report['hits'],0)
        self.assertEqual(report['misses'],0)

    def test_lru_entry_bound_and_clear_keep_only_immutable_text(self):
        heads=[]
        for i in range(4):
            (self.root/'unit_marker').write_text(str(i));self.commit('different immutable head')
            heads.append(self.git('rev-parse','HEAD').strip())
        report=dict(hits=0,misses=0,successful_reads=0,evictions=0,entries=0,cached_bytes=0,
                    peak_bytes=0,peak_entries=0,view_invalidations=0)
        reader=self.helper._RawReads(self.root,self.original_git,
                    self.helper._View(self.root,self.original_git),report)
        with patch.object(self.helper,'MAX_ENTRIES',2):
            for head in heads:reader.read(self.root,'show',head+':'+self.base.REG)
        self.assertEqual(len(reader.cache),2)
        self.assertEqual(report['evictions'],2)
        self.assertLessEqual(report['peak_entries'],2)
        self.assertTrue(all(type(k) is str and type(v) is bytes for k,v in reader.cache.items()))
        reader.clear();self.assertFalse(reader.cache);self.assertEqual(report['cached_bytes'],0)

    def test_mid_view_env_or_index_change_disables_existing_entry(self):
        for mode in ('env','index'):
            with self.subTest(mode=mode):
                report=dict(hits=0,misses=0,successful_reads=0,evictions=0,entries=0,cached_bytes=0,
                            peak_bytes=0,peak_entries=0,view_invalidations=0)
                reader=self.helper._RawReads(self.root,self.original_git,
                            self.helper._View(self.root,self.original_git),report)
                expected=reader.read(self.root,'show',self.public+':'+self.base.REG)
                if mode=='index':
                    path=self.root/'.git/index';old=path.read_bytes();path.write_bytes(old+b'x')
                context=patch.dict('os.environ',{'GIT_WITNESS_UNKNOWN':'1'}) if mode=='env' else contextlib.nullcontext()
                try:
                    with context:self.assertEqual(reader.read(self.root,'show',self.public+':'+self.base.REG),expected)
                finally:
                    if mode=='index':path.write_bytes(old)
                self.assertFalse(reader.enabled);self.assertEqual(report['hits'],0)

    def test_other_thread_reads_do_not_share_invocation_cache(self):
        import threading
        report=dict(hits=0,misses=0,successful_reads=0,evictions=0,entries=0,cached_bytes=0,
                    peak_bytes=0,peak_entries=0,view_invalidations=0)
        reader=self.helper._RawReads(self.root,self.original_git,
                    self.helper._View(self.root,self.original_git),report)
        expected=reader.read(self.root,'show',self.public+':'+self.base.REG);outputs=[]
        thread=threading.Thread(target=lambda:outputs.append(reader.read(self.root,'show',self.public+':'+self.base.REG)))
        thread.start();thread.join(5);self.assertFalse(thread.is_alive())
        self.assertEqual(outputs,[expected]);self.assertEqual(report['hits'],0)

    def test_scandir_io_failure_falls_back_without_changing_original_result(self):
        with patch.object(self.helper.os,'scandir',side_effect=PermissionError('original directory view unavailable')):
            with self.helper.installed(self.base,self.root) as report:
                self.assertEqual(self.call(),self.prior)
        self.assertEqual(report['native_calls'],0)
        self.assertEqual(report['fallback_calls'],1)
        self.assertIs(self.base.git,self.original_git)
        self.assertIs(self.base.history_receipts,self.original_history)

    def test_original_root_keyword_call_keeps_the_exact_public_signature(self):
        expected=self.call()
        with self.helper.installed(self.base,self.root) as report:
            with patch.object(self.base,'github_json',side_effect=AssertionError('HTTP forbidden')):
                actual=self.base.remote_witnesses(root=self.root,registry=self.registry,
                                                 prior=self.prior,runs=self.runs)
        self.assertEqual(actual,expected)
        self.assertEqual(report['native_calls'],1)
        self.assertEqual(report['hits'],2)

    def test_modified_defaults_or_keyword_defaults_keep_original_function_unwrapped(self):
        cases=((self.original_remote,'__defaults__',(True,)),
               (self.original_remote,'__defaults__',(0,)),
               (self.original_remote,'__kwdefaults__',{'persist_cache':False}),
               (self.original_git,'__defaults__',()),
               (self.original_git,'__kwdefaults__',{}))
        for function,attribute,changed in cases:
            with self.subTest(function=function.__name__,attribute=attribute,changed=changed):
                old=getattr(function,attribute)
                try:
                    setattr(function,attribute,changed)
                    with self.helper.installed(self.base,self.root) as report:
                        self.assertIs(self.base.remote_witnesses,self.original_remote)
                        self.assertIs(self.base.git,self.original_git)
                    self.assertFalse(report['installed'])
                finally:setattr(function,attribute,old)


    def test_scandir_internal_collection_is_bounded_before_the_514th_entry(self):
        seen=[];closed=[]
        class Entries:
            def __enter__(inner):return inner
            def __exit__(inner,*args):closed.append(True)
            def __iter__(inner):
                for i in range(10000):
                    if i>512:raise AssertionError('stdlib eagerly collected unbounded scandir')
                    seen.append(i)
                    yield types.SimpleNamespace(name=str(i),path=str(self.root/'.git/refs'/str(i)),
                                                is_dir=lambda **kwargs:False)
        with patch.object(self.helper.os,'scandir',return_value=Entries()):
            with self.helper.installed(self.base,self.root) as report:
                self.assertEqual(self.call(),self.prior)
        self.assertEqual(len(seen),513)
        self.assertTrue(closed)
        self.assertEqual(report['native_calls'],0)


if __name__=='__main__':unittest.main()

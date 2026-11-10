import argparse,ast,base64,copy,hashlib,importlib.util,json,os,subprocess,sys,tempfile,types,unittest
from unittest.mock import patch
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
HERE=Path(__file__).resolve().parent
C=HERE if (HERE/'phase2_runtime_engineering_v1.py').is_file() else HERE/'candidate'
P=next((a/'readiness-pr102'/'natural-after-merge' for a in [HERE.parent,HERE.parent.parent] if (a/'readiness-pr102'/'natural-after-merge').is_dir()),HERE.parent/'readiness-pr102'/'natural-after-merge')
sp=importlib.util.spec_from_file_location('r2_engineering_under_test',C/'phase2_runtime_engineering_v1.py')
eng=importlib.util.module_from_spec(sp);sp.loader.exec_module(eng)
encode=lambda v:(json.dumps(v,sort_keys=True,separators=(',',':'))+'\n').encode()
utc=lambda x:datetime.fromisoformat(x.replace('Z','+00:00')) if isinstance(x,str) else x
def fun(path,name,env):
    source=path.read_text() if isinstance(path,Path) else path
    node=next(x for x in ast.parse(source).body if isinstance(x,ast.FunctionDef) and x.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),(str(path) if isinstance(path,Path) else '<original registered runtime>'),'exec'),env)
    return env[name]
def http(code):return HTTPError('https://raw.githubusercontent.com/fixture',code,'fixture',None,None)
class Compatibility(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name)
        self.m=json.loads((C/eng.MANIFEST).read_text())
        self.m.update(authorization=eng.CANDIDATE,approved_at_utc=None,activation_utc=None)
        self.old={p:('fixture '+p+'\n').encode() for p in self.m['original_sha256_lf']}
        self.old[eng.RUNTIME]=base64.b64decode(self.m['original_runtime_base64'],validate=True)
        fpath=C/'phase2_forward_v2.py' if (C/'phase2_forward_v2.py').is_file() else P/'frozen-phase2_forward_v2.py'
        self.old['phase2_forward_v2.py']=fpath.read_bytes()
        for name in ('phase2_forward_audit_v2.yml','phase2_forward_live_v2.yml'):
            wpath=C/'.github/workflows'/name if (C/'.github/workflows'/name).is_file() else P/('frozen-'+name)
            self.old['.github/workflows/'+name]=wpath.read_bytes()
        self.reg={'protocol_id':self.m['protocol_id'],'model_identity':self.m['model_identity'],'sha256_lf':{p:eng.sha(b) for p,b in self.old.items()}}
        self.regraw=encode(self.reg)
        self.legacy={'event_id':'1_formal','issued_at_utc':'2026-10-19T12:00:00Z'}
        self.m.update(original_sha256_lf=self.reg['sha256_lf'],original_registration_sha256=eng.sha(self.regraw),legacy_event_git_blobs={'1_formal':eng.blob(encode(self.legacy))})
        original_pins={k:getattr(eng,k) for k in ('ORIGINAL_REGISTRATION_SHA256','ORIGINAL_MAP_SHA256','LEGACY_MAP_SHA256')}
        self.addCleanup(lambda:[setattr(eng,k,v) for k,v in original_pins.items()])
        eng.ORIGINAL_REGISTRATION_SHA256=eng.sha(self.regraw)
        eng.ORIGINAL_MAP_SHA256=eng.map_sha(self.reg['sha256_lf'])
        eng.LEGACY_MAP_SHA256=eng.map_sha(self.m['legacy_event_git_blobs'])
        self.actual={**self.old,eng.RUNTIME:(C/eng.RUNTIME).read_bytes()}
        for p,b in {**self.actual,eng.REGISTRATION:self.regraw,eng.HELPER:(C/eng.HELPER).read_bytes()}.items():
            q=self.root/p;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(b)
        self.write_manifest()
        env={'FIXED':set(self.old),'f':types.SimpleNamespace(old=types.SimpleNamespace(sha=eng.sha)),'frozen':types.SimpleNamespace(file_bytes=lambda root,p:(root/p).read_bytes()),'engineering':eng}
        src=self.old[eng.RUNTIME].decode('utf8') if os.environ.get('R2_COMPAT_BASELINE')=='1' else C/eng.RUNTIME
        self.verify=fun(src,'verify_files',env)
    def tearDown(self):self.t.cleanup()
    def write_manifest(self):
        (self.root/eng.MANIFEST).write_bytes(encode(self.m))
    def files(self):
        x=self.verify(self.root,self.reg)
        x[eng.REGISTRATION]=self.regraw
        return x
    def approve(self):
        self.m.update(authorization=eng.APPROVED,approved_at_utc='2026-11-10T01:00:00Z',activation_utc='2026-11-10T02:00:00Z')
        self.write_manifest()
    def test_new_runtime_admitted_without_replacing_original_registration(self):
        f=self.files()
        self.assertEqual(f[eng.RUNTIME],self.actual[eng.RUNTIME]);self.assertEqual(f[eng.REGISTRATION],self.regraw)
        self.assertEqual(eng.original_files(f),{**self.old,eng.REGISTRATION:self.regraw})
    def test_every_other_registered_file_is_still_strict(self):
        for p in self.old:
            if p==eng.RUNTIME:continue
            with self.subTest(path=p):
                original=(self.root/p).read_bytes();(self.root/p).write_bytes(original+b'x')
                with self.assertRaisesRegex(ValueError,'outside engineering'):self.files()
                (self.root/p).write_bytes(original)
    def test_original_registration_bytes_cannot_change(self):
        (self.root/eng.REGISTRATION).write_bytes(self.regraw+b' ')
        with self.assertRaisesRegex(ValueError,'registration changed'):self.files()
    def test_original_hash_map_cannot_change(self):
        self.m['original_sha256_lf']={};self.write_manifest()
        with self.assertRaisesRegex(ValueError,'registration changed'):self.files()
    def test_old_runtime_payload_cannot_change(self):
        self.m['original_runtime_base64']=base64.b64encode(b'wrong').decode();self.write_manifest()
        with self.assertRaisesRegex(ValueError,'runtime payload'):self.files()
    def test_candidate_actual_main_dispatch_cannot_post(self):
        calls=[]
        entry=fun(C/eng.RUNTIME,'main',{'argparse':argparse,'engineering':eng,'ROOT':self.root,'frozen':types.SimpleNamespace(file_bytes=lambda root,p:(root/p).read_bytes()),'dispatch':lambda:calls.append('POST')})
        with patch.object(sys,'argv',['runtime.py','dispatch']):
            with self.assertRaises(ValueError):entry()
        self.assertEqual(calls,[])
    def test_approved_actual_main_dispatch_keeps_original_call(self):
        self.approve();calls=[]
        entry=fun(C/eng.RUNTIME,'main',{'argparse':argparse,'engineering':eng,'ROOT':self.root,'frozen':types.SimpleNamespace(file_bytes=lambda root,p:(root/p).read_bytes()),'dispatch':lambda:calls.append('POST')})
        with patch.object(sys,'argv',['runtime.py','dispatch']):entry()
        self.assertEqual(calls,['POST'])
    def test_candidate_rejects_all_writer_modes_before_records_or_append(self):
        f=self.files()
        for mode in ('publish','canary'):
            with self.assertRaisesRegex(ValueError,'cannot write'):eng.require_execution_mode(f,mode)
        for mode in ('local','audit','audit_all'):eng.require_execution_mode(f,mode)
    def test_candidate_has_no_issuance_permission(self):
        f=self.files();self.assertFalse(eng.can_issue(f,utc('2026-11-11T00:00:00Z'),utc))
        with self.assertRaisesRegex(ValueError,'not active'):eng.issuance_source_files(f,b'raw','source',utc('2026-11-11T00:00:00Z'),utc)
    def test_candidate_cannot_claim_approval_time(self):
        self.m['approved_at_utc']='2026-11-10T00:00:00Z';self.write_manifest()
        with self.assertRaisesRegex(ValueError,'cannot claim'):self.files()
    def test_approval_cannot_follow_activation(self):
        self.approve();self.m['activation_utc']='2026-11-10T00:00:00Z';self.write_manifest()
        with self.assertRaisesRegex(ValueError,'predates'):self.files()
    def test_legacy_exact_event_routes_original_27_bytes(self):
        f=self.files();self.assertEqual(eng.event_files(self.legacy,f,utc,encode),{**self.old,eng.REGISTRATION:self.regraw})
        self.assertEqual(eng.source_files(self.legacy,f,b'raw','source',utc,encode),{'source':b'raw'})
    def test_legacy_mutation_fails(self):
        f=self.files()
        with self.assertRaisesRegex(ValueError,'legacy event bytes'):eng.event_files({**self.legacy,'issued_at_utc':'2026-10-19T13:00:00Z'},f,utc,encode)
    def test_unknown_new_event_cannot_enter_candidate(self):
        with self.assertRaisesRegex(ValueError,'unapproved'):eng.event_files({'event_id':'2_formal','issued_at_utc':'2026-11-11T00:00:00Z'},self.files(),utc,encode)
    def test_approved_future_binds_actual_current_runtime_manifest_and_helper(self):
        self.approve();f=self.files();e={'event_id':'2_formal','issued_at_utc':'2026-11-10T02:00:01Z'}
        self.assertIs(eng.event_files(e,f,utc,encode),f)
        proof=eng.source_files(e,f,b'raw','source',utc,encode)
        self.assertEqual(proof,{**f,'source':b'raw'})
        self.assertIn(eng.HELPER,proof);self.assertIn(eng.MANIFEST,proof)
    def test_future_source_has_approval_start_boundary_legacy_unchanged(self):
        self.approve();f=self.files();e={'event_id':'2_formal','issued_at_utc':'2026-11-10T02:00:01Z'}
        self.assertEqual(eng.source_after(e,f,encode),utc('2026-11-10T01:00:00Z'))
        self.assertIsNone(eng.source_after(self.legacy,f,encode))
        self.assertEqual(eng.issuance_source_after(f),utc('2026-11-10T01:00:00Z'))
    def test_pre_activation_new_event_fails(self):
        self.approve()
        with self.assertRaisesRegex(ValueError,'pre-activation'):eng.event_files({'event_id':'2_formal','issued_at_utc':'2026-11-10T01:59:59Z'},self.files(),utc,encode)
    def test_issuance_actual_clock_unchanged_boundary(self):
        self.approve();f=self.files()
        self.assertFalse(eng.can_issue(f,utc('2026-11-10T01:59:59Z'),utc))
        self.assertTrue(eng.can_issue(f,utc('2026-11-10T02:00:00Z'),utc))
    def test_safety_flags_cannot_be_enabled(self):
        for name in ('model_changed','ids_or_denominators_reset','deadlines_changed'):
            old=self.m[name];self.m[name]=True;self.write_manifest()
            with self.assertRaisesRegex(ValueError,'scope exceeded'):self.files()
            self.m[name]=old
    def test_helper_bytes_cannot_change(self):
        x=self.root/eng.HELPER;x.write_bytes(x.read_bytes()+b'x')
        with self.assertRaisesRegex(ValueError,'executable changed'):self.files()
class ActualGitAndWitness(unittest.TestCase):
    setUp=Compatibility.setUp
    tearDown=Compatibility.tearDown
    write_manifest=Compatibility.write_manifest
    files=Compatibility.files
    approve=Compatibility.approve
    def git(self,*a):
        return subprocess.check_output(['git','--no-replace-objects',*a],cwd=self.root,stderr=subprocess.DEVNULL)
    def init(self):
        self.git('init','-q');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
        for p,b in self.old.items():(self.root/p).write_bytes(b)
        self.git('add','.');self.git('commit','-qm','old original identities')
        oldhead=self.git('rev-parse','HEAD').decode().strip()
        (self.root/eng.RUNTIME).write_bytes(self.actual[eng.RUNTIME]);self.approve()
        self.git('add','.');self.git('commit','-qm','explicit approved engineering fixture')
        return oldhead,self.git('rev-parse','HEAD').decode().strip()
    def witness(self):
        return fun(C/'phase2_forward_v2.py' if (C/'phase2_forward_v2.py').is_file() else P/'frozen-phase2_forward_v2.py','witness',{'old':types.SimpleNamespace(REPO='lzwislam-collab/doge-data-collector'),'WORKFLOW':'.github/workflows/phase2_forward_audit_v2.yml','utc':utc})
    def row(self,head,began,finished):
        return {'id':1,'repository':{'full_name':'lzwislam-collab/doge-data-collector'},'path':'.github/workflows/phase2_forward_audit_v2.yml','head_branch':'main','event':'push','status':'completed','conclusion':'success','head_sha':head,'created_at':began,'audit_job':{'name':'audit','status':'completed','conclusion':'success','started_at':began,'completed_at':finished}}
    def test_real_old_registration_stays_qualified_new_proof_does_not_impersonate_it(self):
        old,new=self.init();f=self.files();w=self.witness();read=lambda h,p:self.git('show',h+':'+p)
        row=self.row(old,'2026-10-19T00:00:00Z','2026-10-19T00:01:00Z');before=utc('2026-10-20T00:00:00Z')
        self.assertIsNotNone(w(eng.original_files(f),[row],read,before))
        self.assertIsNone(w(f,[row],read,before))
    def test_real_new_source_accepts_only_actual_new_runtime_and_manifest(self):
        old,new=self.init();f=self.files();w=self.witness();read=lambda h,p:self.git('show',h+':'+p)
        row=self.row(new,'2026-11-10T02:00:00Z','2026-11-10T02:01:00Z')
        self.assertIsNotNone(w(f,[row],read,utc('2026-11-10T02:02:00Z')))
        self.assertIsNone(w(eng.original_files(f),[row],read,utc('2026-11-10T02:02:00Z')))
        self.assertIsNone(w(f,[row],read,utc('2026-11-10T02:01:00Z')))
    def test_original_witness_skips_real_missing_candidate_then_accepts_later_real_object(self):
        old,new=self.init();path='data/phase2_forward_v2/scores/1794240000_formal.json'
        x=self.root/path;x.parent.mkdir(parents=True,exist_ok=True);x.write_bytes(b'actual-score')
        self.git('add',path);self.git('commit','-qm','genuine later score fixture');present=self.git('rev-parse','HEAD').decode().strip()
        rows=[self.row(new,'2026-11-10T02:00:00Z','2026-11-10T02:01:00Z'),self.row(present,'2026-11-10T02:02:00Z','2026-11-10T02:03:00Z')]
        def raw_reader(h,p):
            try:return self.git('show',h+':'+p)
            except subprocess.CalledProcessError:raise http(404)
        w=self.witness();before=utc('2026-11-10T02:05:00Z')
        with self.assertRaises(HTTPError):w({path:b'actual-score'},rows,raw_reader,before)
        guarded=lambda h,p:eng.read_at(raw_reader,self.root,h,p)
        self.assertEqual(w({path:b'actual-score'},rows,guarded,before)['head_sha'],present)
        self.assertIsNone(w({path:b'actual-score'},rows[:1],guarded,before))
    def test_new_source_audit_started_before_approval_cannot_qualify(self):
        old,new=self.init();f=self.files();w=self.witness();reader=lambda h,p:self.git('show',h+':'+p)
        row=self.row(new,'2026-11-10T00:59:59Z','2026-11-10T02:01:00Z')
        self.assertIsNone(w(f,[row],reader,utc('2026-11-10T02:02:00Z'),eng.issuance_source_after(f)))
    def test_real_absent_object_404_becomes_typed_valueerror(self):
        old,new=self.init();exc=http(404)
        def absent(h,p):raise exc
        with self.assertRaises(eng.WitnessObjectAbsent) as cm:eng.read_at(absent,self.root,old,'data/phase2_forward_v2/scores/1794240000_formal.json')
        self.assertIs(cm.exception.__cause__,exc)
    def test_real_known_entry_404_is_not_absence(self):
        old,new=self.init();path='data/phase2_forward_v2/scores/1_formal.json'
        x=self.root/path;x.parent.mkdir(parents=True,exist_ok=True);x.write_bytes(b'{}')
        self.git('add',path);self.git('commit','-qm','known real object');head=self.git('rev-parse','HEAD').decode().strip();exc=http(404)
        def missing(h,p):raise exc
        with self.assertRaises(HTTPError) as cm:eng.read_at(missing,self.root,head,path)
        self.assertIs(cm.exception,exc)
    def test_unknown_head_and_fixed_path_never_downcast(self):
        old,new=self.init()
        for head,path in [('0'*40,'data/phase2_forward_v2/scores/1_formal.json'),(old,eng.MANIFEST),(old,'../scores/1_formal.json')]:
            exc=http(404)
            def missing(h,p):raise exc
            with self.assertRaises(HTTPError):eng.read_at(missing,self.root,head,path)
    def test_permission_and_transport_unchanged(self):
        old,new=self.init()
        for exc in [http(403),http(429),http(503),TimeoutError('transport')]:
            def missing(h,p):raise exc
            with self.assertRaises(type(exc)):eng.read_at(missing,self.root,old,'data/phase2_forward_v2/scores/1_formal.json')
    def test_cancellation_is_never_swallowed(self):
        old,new=self.init()
        for exc in (KeyboardInterrupt(),SystemExit(130)):
            def cancelled(h,p):raise exc
            with self.assertRaises(type(exc)):eng.read_at(cancelled,self.root,old,'data/phase2_forward_v2/scores/1_formal.json')
class ProductionPins(unittest.TestCase):
    def test_current_real_manifest_passes_shape(self):
        self.assertEqual(eng._parse((C/eng.MANIFEST).read_bytes())['legacy_anchor_commit'],'2c5068902abb320f9a45ecc0de1d49e66eaa0be9')
    def test_valid_shape_other_legacy_anchor_rejected(self):
        m=json.loads((C/eng.MANIFEST).read_bytes());m['legacy_anchor_commit']='1'*40
        with self.assertRaises(ValueError):eng._parse(encode(m))
    def test_valid_shape_added_legacy_identity_rejected(self):
        m=json.loads((C/eng.MANIFEST).read_bytes());m['legacy_event_git_blobs']['1794276000_formal']='a'*40
        with self.assertRaises(ValueError):eng._parse(encode(m))
if __name__=='__main__':unittest.main(verbosity=2)

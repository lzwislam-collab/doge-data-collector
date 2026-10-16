"""Synthetic fixtures only. Never request market data or publish to GitHub."""
import importlib.util,json,os,subprocess,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import direction_forward_v42 as frozen
import forward_validation as base
import forward_validation_v2 as v2
import quote_evidence_v2 as evidence
import quote_capture_retry as retry

MODULE=Path(__file__).with_name('quote_only_rescue.py')
rescue=None
if MODULE.exists():
    spec=importlib.util.spec_from_file_location('quote_only_rescue',MODULE)
    rescue=importlib.util.module_from_spec(spec);spec.loader.exec_module(rescue)

class PolicyTests(unittest.TestCase):
    def test_entry_is_available(self):self.assertTrue(callable(getattr(rescue,'eligible_ids',None)))
    def test_only_open_unscored_targets_and_reserve_five_minutes(self):
        now=datetime(2026,10,16,3,tzinfo=timezone.utc)
        reg={k:{'target_utc':(now+timedelta(minutes=m)).isoformat()} for k,m in [('open',-5),('edge',-20),('late',-21),('future',1),('missing',-5)]}
        self.assertEqual(rescue.eligible_ids(reg,{'missing':{'status':'unscorable_missing_target_quote'}},now),['edge','open'])
    def test_queued_stale_requested_ids_cannot_select_other_targets(self):
        now=datetime(2026,10,16,3,tzinfo=timezone.utc)
        reg={'old':{'target_utc':(now-timedelta(hours=1)).isoformat()},'new':{'target_utc':now.isoformat()}}
        self.assertEqual(rescue.eligible_ids(reg,{},now,['old']),[])
    def test_naive_clock_is_rejected(self):
        with self.assertRaises(ValueError):rescue.eligible_ids({}, {},datetime(2026,10,16))
    def test_shared_concurrency_does_not_replace_healthy_pending_collector(self):
        text=(MODULE.parent/'.github/workflows/quote_only_rescue.yml').read_text(encoding='utf-8')
        self.assertIn('group: okx-hourly-extreme-history',text);self.assertIn('queue: max',text);self.assertIn('cancel-in-progress: false',text)
        self.assertNotIn('schedule:',text);self.assertIn("github.ref == 'refs/heads/main'",text)

class RescueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'work';self.root.mkdir()
        self.remote=Path(self.tmp.name)/'remote';self.now=datetime.now(timezone.utc).replace(microsecond=0)-timedelta(minutes=1)
        self.target=self.now-timedelta(minutes=5);issued=self.target-timedelta(hours=1)
        self.git('init','-q','-b','main');self.git('config','core.autocrlf','false');self.git('config','user.name','QA');self.git('config','user.email','qa@example.test')
        for name in v2.REQUIRED_CODE:
            (self.root/name).write_bytes((Path(__file__).parent/name).read_bytes().replace(b'\r\n',b'\n'))
        for name in [base.PROTOCOL,v2.PROTOCOL]:
            out=self.root/name;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes((Path(__file__).parent/name).read_bytes())
        row=dict(zip(frozen.FCOLS,['p',frozen.MODEL,issued.isoformat(),issued.isoformat(),issued.isoformat(),'1','1',self.target.isoformat(),'.5','UP','UP','DOWN','synthetic']))
        self.write_rows(base.REG,{'p':row},frozen.FCOLS);self.write_rows(base.SCORES,{},frozen.SCOLS)
        bindings={};qid=evidence.save_quote(self.root,self.raw(issued,'1'),issued);evidence.bind(bindings,'p','ISSUE',qid);self.write_rows(evidence.BINDINGS,bindings,evidence.BCOLS)
        self.git('add','.');stamp=(issued+timedelta(minutes=1)).isoformat();env=dict(os.environ,GIT_AUTHOR_DATE=stamp,GIT_COMMITTER_DATE=stamp)
        subprocess.run(['git','commit','-qm','synthetic pre-target evidence'],cwd=self.root,env=env,check=True,capture_output=True)
        subprocess.run(['git','init','--bare','-q',str(self.remote)],check=True,capture_output=True)
        self.git('remote','add','origin',str(self.remote));self.git('push','-q','origin','main')
    def tearDown(self):self.tmp.cleanup()
    def git(self,*args):return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.DEVNULL).decode().strip()
    def write_rows(self,name,rows,cols):frozen.write(self.root/name,frozen.serial(rows,cols))
    def raw(self,stamp,price='1.1'):return json.dumps({'code':'0','data':[{'instId':'DOGE-USDT','last':price,'ts':str(int(stamp.timestamp()*1000))}]})
    def capture(self):
        now=self.now;raw=self.raw(now)
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return now
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return raw.encode()
        with patch.object(evidence,'datetime',Clock),patch.object(evidence.urllib.request,'urlopen',return_value=Response()):
            return retry.capture('watch',root=self.root)
    def run_rescue(self,**kw):return rescue.run(self.root,clock=lambda:self.now,capture_fn=self.capture,**kw)
    def test_real_capture_binding_score_and_local_bare_remote_readback(self):
        before=(self.root/base.REG).read_bytes();out=self.run_rescue()
        self.assertEqual(out['status'],'verified_window_quote_rescue');self.assertEqual(out['scored_ids'],['p']);self.assertEqual((self.root/base.REG).read_bytes(),before)
        self.assertEqual(out['publication']['status'],'verified_remote_readback');self.assertFalse(out['publication']['new_predictions'])
        public=self.git('--git-dir='+str(self.remote),'show','main:'+base.SCORES);self.assertIn('scored',public)
        binding=frozen.read_rows(self.root/evidence.BINDINGS,evidence.BCOLS)['p|TARGET'];self.assertEqual(out['quotes'][0]['quote_id'],binding['quote_id'])
        self.assertEqual(out['formal_prediction_issuance'],False)
    def add_target(self,fid,target):
        rows=frozen.read_rows(self.root/base.REG,frozen.FCOLS);issued=(target-timedelta(hours=1)).isoformat()
        rows[fid]=dict(rows['p'],forecast_id=fid,target_utc=target.isoformat(),issued_at_utc=issued,source_quote_utc=issued,source_hour_start_utc=issued)
        self.write_rows(base.REG,rows,frozen.FCOLS)
        bindings=frozen.read_rows(self.root/evidence.BINDINGS,evidence.BCOLS)
        qid=evidence.save_quote(self.root,self.raw(target-timedelta(hours=1),'1'),target-timedelta(hours=1));evidence.bind(bindings,fid,'ISSUE',qid)
        self.write_rows(evidence.BINDINGS,bindings,evidence.BCOLS)
        self.git('add','.');stamp=(self.target-timedelta(minutes=30)).isoformat()
        subprocess.run(['git','commit','-qm','synthetic second target'],cwd=self.root,env=dict(os.environ,GIT_AUTHOR_DATE=stamp,GIT_COMMITTER_DATE=stamp),check=True,capture_output=True)
        self.git('push','-q','origin','main')
    def test_requested_witness_allows_other_currently_open_targets(self):
        self.add_target('q',self.target)
        self.assertEqual(self.run_rescue(expected_ids=['p'])['scored_ids'],['p','q'])
    def test_unscored_late_target_skips_aggregate_watch_without_fetching(self):
        self.add_target('late',self.now-timedelta(minutes=26))
        out=rescue.run(self.root,expected_ids=['p'],clock=lambda:self.now,capture_fn=lambda:(_ for _ in ()).throw(AssertionError('unexpected capture')))
        self.assertEqual(out['status'],'skip_unsafe_aggregate_watch_batch')
    def test_target_opening_during_fetch_is_allowed_with_reserve(self):
        self.add_target('q',self.now+timedelta(seconds=1))
        def delayed():self.now+=timedelta(seconds=2);return self.capture()
        out=rescue.run(self.root,expected_ids=['p'],clock=lambda:self.now,capture_fn=delayed)
        self.assertEqual(out['scored_ids'],['p','q'])
    def test_final_verification_clock_cannot_use_stale_pre_hash_time(self):
        original=rescue.verify_capture;calls=[0]
        def slow(*args):
            result=original(*args);calls[0]+=1
            if calls[0]==2:self.now=self.target+timedelta(minutes=21)
            return result
        with patch.object(rescue,'verify_capture',side_effect=slow),self.assertRaisesRegex(ValueError,'reserve'):
            self.run_rescue(publish_fn=lambda root,validate:(_ for _ in ()).throw(AssertionError('unexpected publish')))
    def test_existing_missing_score_is_never_captured_or_replaced(self):
        row=dict(zip(frozen.SCOLS,['p','unscorable_missing_target_quote',(self.target+timedelta(minutes=26)).isoformat(),'','','','','','','missing']))
        self.write_rows(base.SCORES,{'p':row},frozen.SCOLS)
        with patch.object(rescue,'guard_protocol'),patch.object(rescue,'ensure_clean_checkout'),patch.object(rescue.publisher,'validate_state'),patch.object(rescue.retry,'capture',side_effect=AssertionError('unexpected capture')):
            self.assertEqual(self.run_rescue()['status'],'skip_no_open_unscored_target')
    def test_delayed_validation_rechecks_window_before_capture(self):
        clock=iter([self.now,self.target+timedelta(minutes=21)])
        with patch.object(rescue.publisher,'validate_state'):
            out=rescue.run(self.root,clock=lambda:next(clock),capture_fn=lambda:(_ for _ in ()).throw(AssertionError('unexpected capture')))
        self.assertEqual(out['status'],'skip_window_closed_during_validation')
    def test_capture_cannot_issue_new_prediction(self):
        def changed():
            rows=frozen.read_rows(self.root/base.REG,frozen.FCOLS);rows['other']=dict(rows['p'],forecast_id='other');self.write_rows(base.REG,rows,frozen.FCOLS)
            return {'status':'ok'}
        with self.assertRaisesRegex(ValueError,'registry'):
            rescue.run(self.root,clock=lambda:self.now,capture_fn=changed)
    def test_raw_snapshot_mutation_blocks_publication(self):
        def changed():
            result=self.capture();binding=frozen.read_rows(self.root/evidence.BINDINGS,evidence.BCOLS)['p|ISSUE'];path=evidence.snapshot_path(self.root,binding['quote_id']);path.write_bytes(path.read_bytes()+b' ');return result
        with self.assertRaisesRegex(ValueError,'snapshot'):
            rescue.run(self.root,clock=lambda:self.now,capture_fn=changed)
    def test_late_capture_is_not_published_even_if_frozen_25m_window_allows_it(self):
        def late():self.now=self.target+timedelta(minutes=21);return self.capture()
        with self.assertRaisesRegex(ValueError,'reserve|expired'):
            rescue.run(self.root,clock=lambda:self.now,capture_fn=late)
    def test_missing_remote_verification_cannot_be_claimed_success(self):
        with self.assertRaisesRegex(ValueError,'readback'):
            self.run_rescue(publish_fn=lambda root,validate:{'status':'push_succeeded'})
    def test_frozen_code_mutation_is_rejected_before_capture(self):
        (self.root/'spot_plus_quote_watch.py').write_text('tampered\n',encoding='utf-8')
        with self.assertRaises(ValueError):self.run_rescue()

if __name__=='__main__':unittest.main()

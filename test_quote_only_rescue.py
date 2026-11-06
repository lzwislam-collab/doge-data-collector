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

def forecast(target):
    issued=target-timedelta(hours=24);source=issued.replace(minute=0,second=0,microsecond=0)
    pid=f'{frozen.MODEL}_{int((source-timedelta(hours=1)).timestamp()*1000)}_24h'
    return pid,dict(zip(frozen.FCOLS,[pid,frozen.MODEL,issued.isoformat(),issued.isoformat(),source.isoformat(),'1','24',target.isoformat(),'.5','UP','UP','DOWN','synthetic']))

class PolicyTests(unittest.TestCase):
    def test_entry_is_available(self):self.assertTrue(callable(getattr(rescue,'eligible_ids',None)))
    def test_only_open_unscored_targets_and_reserve_five_minutes(self):
        now=datetime(2026,11,6,4,5,tzinfo=timezone.utc)
        for minutes,scored,eligible in [(-5,False,True),(-20,False,True),(-21,False,False),(1,False,False),(-5,True,False)]:
            pid,row=forecast(now+timedelta(minutes=minutes))
            self.assertEqual(rescue.eligible_ids({pid:row},{pid:{'status':'unscorable_missing_target_quote'}} if scored else {},now,[pid]),[pid] if eligible else [])
    def test_queued_stale_requested_ids_cannot_select_other_targets(self):
        now=datetime(2026,11,6,4,5,tzinfo=timezone.utc)
        old,oldrow=forecast(now-timedelta(hours=1));new,newrow=forecast(now)
        self.assertEqual(rescue.eligible_ids({old:oldrow,new:newrow},{},now,[old]),[])
    def test_naive_clock_is_rejected(self):
        with self.assertRaises(ValueError):rescue.eligible_ids({}, {},datetime(2026,10,16))
    def test_shared_concurrency_does_not_replace_healthy_pending_collector(self):
        text=(MODULE.parent/'.github/workflows/quote_only_rescue.yml').read_text(encoding='utf-8')
        self.assertIn('group: okx-hourly-extreme-history',text);self.assertIn('queue: max',text);self.assertIn('cancel-in-progress: false',text)
        self.assertNotIn('schedule:',text);self.assertIn("github.ref == 'refs/heads/main'",text)

class RescueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'work';self.root.mkdir()
        self.remote=Path(self.tmp.name)/'remote';self.now=datetime(2026,11,6,3,50,tzinfo=timezone.utc)
        self.target=self.now-timedelta(minutes=5);issued=self.target-timedelta(hours=24);self.pid,self.row=forecast(self.target)
        self.git('init','-q','-b','main');self.git('config','core.autocrlf','false');self.git('config','user.name','QA');self.git('config','user.email','qa@example.test')
        for name in v2.REQUIRED_CODE:
            (self.root/name).write_bytes((Path(__file__).parent/name).read_bytes().replace(b'\r\n',b'\n'))
        for name in [base.PROTOCOL,v2.PROTOCOL]:
            out=self.root/name;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes((Path(__file__).parent/name).read_bytes())
        row=self.row
        self.write_rows(base.REG,{self.pid:row},frozen.FCOLS);self.write_rows(base.SCORES,{},frozen.SCOLS)
        bindings={};qid=evidence.save_quote(self.root,self.raw(issued,'1'),issued);evidence.bind(bindings,self.pid,'ISSUE',qid);self.write_rows(evidence.BINDINGS,bindings,evidence.BCOLS)
        self.git('add','.');stamp=(issued+timedelta(minutes=1)).isoformat();env=dict(os.environ,GIT_AUTHOR_DATE=stamp,GIT_COMMITTER_DATE=stamp)
        subprocess.run(['git','commit','-qm','synthetic pre-target evidence'],cwd=self.root,env=env,check=True,capture_output=True)
        subprocess.run(['git','init','--bare','-q',str(self.remote)],check=True,capture_output=True)
        self.git('remote','add','origin',str(self.remote));self.git('push','-q','origin','main')
        self.witness_run={'id':12345,'head_sha':self.git('rev-parse','HEAD'),'created_at':stamp}
        witness={self.pid:dict(zip(base.WCOLS,[self.pid,'12345',stamp,self.witness_run['head_sha'],'https://github.com/lzwislam-collab/doge-data-collector/actions/runs/12345']))}
        self.write_rows(base.WITNESSES,witness,base.WCOLS);self.git('add','.');self.git('commit','-qm','synthetic immutable run witness');self.git('push','-q','origin','main')
        self.github_patch=patch.object(base,'github_json',side_effect=lambda suffix:dict(self.witness_run));self.github_patch.start()
        fixture=self
        class PublisherClock(datetime):
            @classmethod
            def now(cls,tz=None):return fixture.now
        self.publisher_clock=patch.object(rescue.publisher,'datetime',PublisherClock);self.publisher_clock.start()
    def tearDown(self):self.publisher_clock.stop();self.github_patch.stop();self.tmp.cleanup()
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
            return retry.capture('watch',root=self.root,collect=lambda root,mode:rescue.collect_scoped(root,[self.pid],clock=lambda:now))
    def run_rescue(self,**kw):
        kw.setdefault('expected_ids',[self.pid]);return rescue.run(self.root,clock=lambda:self.now,capture_fn=self.capture,**kw)
    def test_real_capture_binding_score_and_local_bare_remote_readback(self):
        before=(self.root/base.REG).read_bytes();out=self.run_rescue()
        self.assertEqual(out['status'],'verified_window_quote_rescue');self.assertEqual(out['scored_ids'],[self.pid]);self.assertEqual((self.root/base.REG).read_bytes(),before)
        self.assertEqual(out['publication']['status'],'verified_remote_readback');self.assertFalse(out['publication']['new_predictions'])
        public=self.git('--git-dir='+str(self.remote),'show','main:'+base.SCORES);self.assertIn('scored',public)
        binding=frozen.read_rows(self.root/evidence.BINDINGS,evidence.BCOLS)[self.pid+'|TARGET'];self.assertEqual(out['quotes'][0]['quote_id'],binding['quote_id'])
        self.assertEqual(out['formal_prediction_issuance'],False)
        persisted=rescue.take_state(self.root);head=self.git('rev-parse','HEAD')
        repeated=rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,
                            capture_fn=lambda:(_ for _ in ()).throw(AssertionError('duplicate capture')))
        self.assertEqual(repeated['status'],'skip_no_open_unscored_target')
        self.assertEqual(rescue.take_state(self.root),persisted);self.assertEqual(self.git('rev-parse','HEAD'),head)
        self.assertEqual(self.git('--git-dir='+str(self.remote),'rev-parse','main'),head)
    def add_target(self,target):
        pid,row=forecast(target);rows=frozen.read_rows(self.root/base.REG,frozen.FCOLS)
        self.assertNotIn(pid,rows);rows[pid]=row;self.write_rows(base.REG,rows,frozen.FCOLS)
        bindings=frozen.read_rows(self.root/evidence.BINDINGS,evidence.BCOLS)
        issued=frozen.utc(row['issued_at_utc']);qid=evidence.save_quote(self.root,self.raw(issued,'1'),issued);evidence.bind(bindings,pid,'ISSUE',qid)
        self.write_rows(evidence.BINDINGS,bindings,evidence.BCOLS);self.git('add','.');self.git('commit','-qm','synthetic second target');self.git('push','-q','origin','main')
        return pid
    def test_requested_ids_never_allow_other_currently_open_targets(self):
        self.add_target(self.target+timedelta(minutes=15));self.now+=timedelta(minutes=15)
        out=self.run_rescue(expected_ids=[self.pid]);self.assertEqual(out['status'],'skip_unsafe_aggregate_watch_batch')
    def test_unscored_late_target_skips_aggregate_watch_without_fetching(self):
        self.add_target(self.target+timedelta(minutes=15));self.now=self.target+timedelta(minutes=26)
        out=rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,capture_fn=lambda:(_ for _ in ()).throw(AssertionError('unexpected capture')))
        self.assertEqual(out['status'],'skip_no_open_unscored_target')
    def test_target_opening_during_fetch_is_rejected_without_persistence(self):
        self.add_target(self.target+timedelta(minutes=15));before=rescue.take_state(self.root)
        def delayed():self.now+=timedelta(minutes=11);return self.capture()
        with self.assertRaisesRegex(ValueError,'genuine quote capture failed'):
            rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,capture_fn=delayed)
        self.assertEqual(rescue.take_state(self.root),before)
    def test_final_verification_clock_cannot_use_stale_pre_hash_time(self):
        original=rescue.verify_capture;calls=[0]
        def slow(*args):
            result=original(*args);calls[0]+=1
            if calls[0]==2:self.now=self.target+timedelta(minutes=21)
            return result
        with patch.object(rescue,'verify_capture',side_effect=slow),self.assertRaisesRegex(ValueError,'reserve'):
            self.run_rescue(publish_fn=lambda root,validate:(_ for _ in ()).throw(AssertionError('unexpected publish')))
    def test_any_validation_failure_must_leave_no_new_quote_or_score(self):
        """User acceptance condition: intentionally exposes the remaining blocker."""
        before=rescue.take_state(self.root);head=self.git('rev-parse','HEAD')
        original=rescue.verify_capture;calls=[0];published=[]
        def slow(*args):
            result=original(*args);calls[0]+=1
            if calls[0]==2:self.now=self.target+timedelta(minutes=21)
            return result
        with patch.object(rescue,'verify_capture',side_effect=slow),self.assertRaisesRegex(ValueError,'reserve'):
            self.run_rescue(publish_fn=lambda *args,**kwargs:published.append(True))
        after=rescue.take_state(self.root)
        self.assertEqual(published,[])
        self.assertEqual(self.git('rev-parse','HEAD'),head)
        self.assertEqual(after,before,'Post-capture rejection retained a genuine raw quote and uncommitted TARGET score/binding; no publication occurred')
    def test_existing_missing_score_is_never_captured_or_replaced(self):
        row=dict(zip(frozen.SCOLS,[self.pid,'unscorable_missing_target_quote',(self.target+timedelta(minutes=26)).isoformat(),'','','','','','','missing']))
        self.write_rows(base.SCORES,{self.pid:row},frozen.SCOLS)
        with patch.object(rescue,'guard_protocol'),patch.object(rescue,'ensure_clean_checkout'),patch.object(rescue.publisher,'validate_state'),patch.object(rescue.retry,'capture',side_effect=AssertionError('unexpected capture')):
            self.assertEqual(self.run_rescue()['status'],'skip_no_open_unscored_target')
    def test_delayed_validation_rechecks_window_before_capture(self):
        clock=iter([self.now,self.target+timedelta(minutes=21)])
        with patch.object(rescue.publisher,'validate_state'):
            out=rescue.run(self.root,expected_ids=[self.pid],clock=lambda:next(clock),capture_fn=lambda:(_ for _ in ()).throw(AssertionError('unexpected capture')))
        self.assertEqual(out['status'],'skip_window_closed_during_validation')
    def test_capture_cannot_issue_new_prediction(self):
        def changed():
            rows=frozen.read_rows(self.root/base.REG,frozen.FCOLS);rows['other']=dict(rows[self.pid],forecast_id='other');self.write_rows(base.REG,rows,frozen.FCOLS)
            return {'status':'ok'}
        with self.assertRaisesRegex(ValueError,'registry'):
            rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,capture_fn=changed)
    def test_raw_snapshot_mutation_blocks_publication(self):
        def changed():
            result=self.capture();binding=frozen.read_rows(self.root/evidence.BINDINGS,evidence.BCOLS)[self.pid+'|ISSUE'];path=evidence.snapshot_path(self.root,binding['quote_id']);path.write_bytes(path.read_bytes()+b' ');return result
        with self.assertRaisesRegex(ValueError,'snapshot'):
            rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,capture_fn=changed)
    def test_late_capture_is_not_published_even_if_frozen_25m_window_allows_it(self):
        def late():self.now=self.target+timedelta(minutes=21);return self.capture()
        with self.assertRaisesRegex(ValueError,'reserve|expired'):
            rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,capture_fn=late)
    def test_missing_remote_verification_cannot_be_claimed_success(self):
        with self.assertRaisesRegex(ValueError,'readback'):
            self.run_rescue(publish_fn=lambda root,validate:{'status':'push_succeeded'})
    def test_frozen_code_mutation_is_rejected_before_capture(self):
        (self.root/'spot_plus_quote_watch.py').write_text('tampered\n',encoding='utf-8')
        with self.assertRaises(ValueError):self.run_rescue()

from test_quote_only_rescue_scope import ScopeTests

if __name__=='__main__':unittest.main()

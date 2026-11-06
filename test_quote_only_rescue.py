"""Synthetic fixtures only. Never request market data or publish to GitHub."""
import csv,hashlib,io,json,os,subprocess,sys,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import direction_forward_v42 as frozen
import forward_validation as base
import forward_validation_v2 as v2
import quote_evidence_v2 as evidence
import quote_capture_retry as retry
import quote_only_rescue as rescue

MODULE=Path(__file__).with_name('quote_only_rescue.py')
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
        self.environment=patch.dict(os.environ,{k:v for k,v in os.environ.items() if not k.startswith('GIT_')},clear=True);self.environment.start()
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'work';self.root.mkdir()
        self.remote=Path(self.tmp.name)/'remote';self.now=datetime(2026,11,6,3,50,tzinfo=timezone.utc)
        self.target=self.now-timedelta(minutes=5);issued=self.target-timedelta(hours=24);self.pid,self.row=forecast(self.target)
        self.git('init','-q','-b','main');self.git('config','core.autocrlf','false');self.git('config','core.longpaths','true');self.git('config','user.name','QA');self.git('config','user.email','qa@example.test')
        for name in v2.REQUIRED_CODE|{'publish_forward_evidence.py','quote_capture_retry.py','quote_only_rescue.py'}:
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
    def tearDown(self):self.publisher_clock.stop();self.github_patch.stop();self.tmp.cleanup();self.environment.stop()
    def git(self,*args):return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.DEVNULL).decode().strip()
    def write_rows(self,name,rows,cols):frozen.write(self.root/name,frozen.serial(rows,cols))
    def raw(self,stamp,price='1.1'):return json.dumps({'code':'0','data':[{'instId':'DOGE-USDT','last':price,'ts':str(int(stamp.timestamp()*1000))}]})
    def capture(self,root):
        now=self.now;raw=self.raw(now)
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return now
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return raw.encode()
        with patch.object(evidence,'datetime',Clock),patch.object(evidence.urllib.request,'urlopen',return_value=Response()):
            return retry.capture('watch',root=root,collect=lambda root,mode:rescue.collect_scoped(root,[self.pid],clock=lambda:now))
    def run_rescue(self,**kw):
        kw.setdefault('expected_ids',[self.pid]);kw.setdefault('capture_fn',self.capture)
        return rescue.run(self.root,clock=lambda:self.now,**kw)
    def test_real_capture_binding_score_and_local_bare_remote_readback(self):
        before=self.canonical_state();out=self.run_rescue()
        self.assertEqual(out['status'],'verified_window_quote_rescue');self.assertEqual(out['scored_ids'],[self.pid]);self.assertEqual(self.canonical_state(),before)
        self.assertEqual(out['publication']['status'],'verified_remote_readback');self.assertFalse(out['publication']['new_predictions'])
        public=self.git('--git-dir='+str(self.remote),'show','main:'+base.SCORES);self.assertIn('scored',public)
        binding=self.remote_rows(evidence.BINDINGS)[self.pid+'|TARGET'];self.assertEqual(out['quotes'][0]['quote_id'],binding['quote_id'])
        self.assertEqual(out['formal_prediction_issuance'],False)
        persisted=self.canonical_state();head=self.git('--git-dir='+str(self.remote),'rev-parse','main')
        repeated=rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,
                            capture_fn=lambda stage:(_ for _ in ()).throw(AssertionError('duplicate capture')))
        self.assertEqual(repeated['status'],'skip_no_open_unscored_target')
        self.assertEqual(self.canonical_state(),persisted)
        self.assertEqual(self.git('--git-dir='+str(self.remote),'rev-parse','main'),head)
        stage=Path(out['attempt']['path']);self.assertNotEqual(stage.resolve(),self.root.resolve())
        gd=self.stage_git(stage,'rev-parse','--absolute-git-dir');common=self.stage_git(stage,'rev-parse','--path-format=absolute','--git-common-dir')
        self.assertEqual(Path(gd).resolve(),Path(common).resolve());self.assertNotEqual(Path(gd).resolve(),(self.root/'.git').resolve())
        self.assertFalse((Path(gd)/'objects/info/alternates').exists())
    def canonical_state(self):
        paths=list(self.root.glob('data/**/*'))+[self.root/'.git/index',self.root/'.git/config']
        return {'files':{p.relative_to(self.root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()},
                'head':self.git('rev-parse','HEAD'),'refs':self.git('show-ref')}
    def remote_rows(self,path):
        body=self.git('--git-dir='+str(self.remote),'show','main:'+path)
        return {row['forecast_id']:row for row in csv.DictReader(io.StringIO(body))}
    def stage_git(self,stage,*args):return subprocess.check_output(['git',*args],cwd=stage,stderr=subprocess.DEVNULL).decode().strip()
    def advance_remote(self,mutate):
        other=Path(self.tmp.name)/'normal-writer'
        subprocess.run(['git','clone','-q','--branch','main',str(self.remote),str(other)],check=True,capture_output=True)
        self.stage_git(other,'config','core.autocrlf','false');self.stage_git(other,'config','core.longpaths','true')
        self.stage_git(other,'config','user.name','Synthetic normal writer');self.stage_git(other,'config','user.email','normal@example.test')
        mutate(other)
        self.stage_git(other,'add','.');self.stage_git(other,'commit','-qm','synthetic independent normal change');self.stage_git(other,'push','-q','origin','main')
        return self.stage_git(other,'rev-parse','HEAD')
    def assert_canonical_consumers_unchanged(self,before):
        self.assertEqual(self.canonical_state(),before)
        records=frozen.read_rows(self.root/base.SCORES,frozen.SCOLS)
        self.assertEqual(base.compare([self.row],records,self.target+timedelta(minutes=26))['scored'],0)
    def canonical_formal_consumer(self,root=None):
        # The fixture protocol files were committed in November, not at the
        # historical October freeze. Supply ONLY those already-assumed protocol
        # gates and the synthetic corroborated ISSUE witness; execute the actual
        # V2 CSV/raw/binding/score selection and metrics consumer unchanged.
        root=root or self.root;witness=frozen.read_rows(root/base.WITNESSES,base.WCOLS)
        with patch.object(base,'locked_file',return_value=datetime(2026,10,1,tzinfo=timezone.utc)),patch.object(v2,'protocol_witness',return_value={'synthetic_protocol_gate_only':True}),patch.object(base,'evaluate',return_value={'verified_remote_head_witnesses':witness}):
            return v2.evaluate(root,now=self.target+timedelta(minutes=26),runs=[],persist=False)
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
        out=rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,capture_fn=lambda stage:(_ for _ in ()).throw(AssertionError('unexpected capture')))
        self.assertEqual(out['status'],'skip_no_open_unscored_target')
    def test_target_opening_during_fetch_is_rejected_without_persistence(self):
        self.add_target(self.target+timedelta(minutes=15));before=rescue.take_state(self.root)
        def delayed(stage):self.now+=timedelta(minutes=11);return self.capture(stage)
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
        """Original +5m/+21m counterexample; audit bytes may remain outside canonical."""
        before=self.canonical_state();head=self.git('rev-parse','HEAD')
        self.assertEqual(self.canonical_formal_consumer()['horizons']['24h']['scored'],0)
        original=rescue.verify_capture;calls=[0];published=[]
        def slow(*args):
            result=original(*args);calls[0]+=1
            if calls[0]==2:self.now=self.target+timedelta(minutes=21)
            return result
        with patch.object(rescue,'verify_capture',side_effect=slow),self.assertRaisesRegex(ValueError,'reserve') as caught:
            self.run_rescue(publish_fn=lambda *args,**kwargs:published.append(True))
        self.assertEqual(published,[])
        self.assertEqual(self.git('rev-parse','HEAD'),head)
        self.assert_canonical_consumers_unchanged(before)
        self.assertEqual(self.canonical_formal_consumer()['horizons']['24h']['scored'],0)
        self.assertEqual(self.remote_rows(base.SCORES),{})
        audit=caught.exception.evidence;stage=Path(audit['attempt']['path'])
        pending=rescue.take_state(stage);self.assertIn(self.pid,pending['scores'])
        # Negative control: unchanged frozen consumer would count the same
        # pending data if explicitly directed to the non-production stage.
        self.assertEqual(self.canonical_formal_consumer(stage)['horizons']['24h']['scored'],1)
        binding=pending['bindings'][self.pid+'|TARGET'];raw=evidence.snapshot_path(stage,binding['quote_id'])
        self.assertTrue(raw.is_file());self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(),binding['quote_id'])
        self.assertTrue(audit['raw_evidence']);self.assertEqual(audit['remote_status'],'NOT_ATTEMPTED')
        # Retrying the canonical source after expiry does not consume isolated
        # pending scores, confer fresh eligibility, or write a new publication.
        repeated=self.run_rescue(capture_fn=lambda stage:(_ for _ in ()).throw(AssertionError('expired restart capture')))
        self.assertEqual(repeated['status'],'skip_no_open_unscored_target')
        self.assert_canonical_consumers_unchanged(before);self.assertEqual(self.remote_rows(base.SCORES),{})
    def test_existing_missing_score_is_never_captured_or_replaced(self):
        row=dict(zip(frozen.SCOLS,[self.pid,'unscorable_missing_target_quote',(self.target+timedelta(minutes=26)).isoformat(),'','','','','','','missing']))
        self.write_rows(base.SCORES,{self.pid:row},frozen.SCOLS)
        self.git('add',base.SCORES);self.git('commit','-qm','synthetic immutable missing disposition');self.git('push','-q','origin','main')
        self.assertEqual(self.run_rescue(capture_fn=lambda stage:(_ for _ in ()).throw(AssertionError('unexpected capture')))['status'],'skip_no_open_unscored_target')
    def test_delayed_validation_rechecks_window_before_capture(self):
        original=rescue.verify_formal_issues
        def delayed(*args):
            result=original(*args);self.now=self.target+timedelta(minutes=21);return result
        with patch.object(rescue,'verify_formal_issues',side_effect=delayed):
            out=rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,capture_fn=lambda stage:(_ for _ in ()).throw(AssertionError('unexpected capture')))
        self.assertEqual(out['status'],'skip_window_closed_during_validation')
    def test_capture_cannot_issue_new_prediction(self):
        def changed(stage):
            rows=frozen.read_rows(stage/base.REG,frozen.FCOLS);rows['other']=dict(rows[self.pid],forecast_id='other');frozen.write(stage/base.REG,frozen.serial(rows,frozen.FCOLS))
            return {'status':'ok'}
        with self.assertRaisesRegex(ValueError,'registry'):
            rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,capture_fn=changed)
    def test_raw_snapshot_mutation_blocks_publication(self):
        def changed(stage):
            result=self.capture(stage);binding=frozen.read_rows(stage/evidence.BINDINGS,evidence.BCOLS)[self.pid+'|ISSUE'];path=evidence.snapshot_path(stage,binding['quote_id']);path.write_bytes(path.read_bytes()+b' ');return result
        with self.assertRaisesRegex(ValueError,'snapshot'):
            rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,capture_fn=changed)
    def test_late_capture_is_not_published_even_if_frozen_25m_window_allows_it(self):
        def late(stage):self.now=self.target+timedelta(minutes=21);return self.capture(stage)
        with self.assertRaisesRegex(ValueError,'reserve|expired'):
            rescue.run(self.root,expected_ids=[self.pid],clock=lambda:self.now,capture_fn=late)
    def test_missing_remote_verification_cannot_be_claimed_success(self):
        with self.assertRaisesRegex(ValueError,'readback'):
            self.run_rescue(publish_fn=lambda root,validate:{'status':'push_succeeded'})
    def test_frozen_code_mutation_is_rejected_before_capture(self):
        (self.root/'spot_plus_quote_watch.py').write_text('tampered\n',encoding='utf-8')
        with self.assertRaises(ValueError):self.run_rescue()

    def test_real_publisher_rejected_push_retries_only_same_evidence(self):
        before=self.canonical_state();real=rescue.publisher.command;pushes=[];captures=[]
        def command(stage,*args):
            if args[0]=='push':
                pushes.append((self.stage_git(stage,'rev-parse','HEAD'),rescue.take_state(stage)))
                raise subprocess.CalledProcessError(1,['git',*args],stderr=b'synthetic remote rejects push')
            return real(stage,*args)
        def capture(stage):captures.append(str(stage));return self.capture(stage)
        with patch.object(rescue.publisher,'command',side_effect=command),patch.object(rescue.publisher.time,'sleep'),self.assertRaises(ValueError) as caught:
            self.run_rescue(capture_fn=capture)
        self.assertEqual(len(captures),1);self.assertEqual(len(pushes),3)
        self.assertTrue(all(state==pushes[0][1] for _,state in pushes))
        self.assertEqual(self.remote_rows(base.SCORES),{});self.assert_canonical_consumers_unchanged(before)
        self.assertEqual(caught.exception.evidence['remote_status'],'CONFIRMED_ABSENT')

    def test_real_publisher_accepted_push_then_client_failure_is_not_republished(self):
        before=self.canonical_state();real=rescue.publisher.command;pushes=[];captures=[]
        def command(stage,*args):
            if args[0]=='push':
                pushes.append(self.stage_git(stage,'rev-parse','HEAD'));result=real(stage,*args)
                raise subprocess.CalledProcessError(1,['git',*args],stderr=b'synthetic client lost accepted response')
            return real(stage,*args)
        def capture(stage):captures.append(str(stage));return self.capture(stage)
        with patch.object(rescue.publisher,'command',side_effect=command),patch.object(rescue.publisher.time,'sleep'),self.assertRaises(ValueError) as caught:
            self.run_rescue(capture_fn=capture)
        self.assertEqual(len(captures),1);self.assertEqual(len(pushes),1)
        rows=self.remote_rows(base.SCORES);self.assertEqual(set(rows),{self.pid});self.assertEqual(rows[self.pid]['status'],'scored')
        self.assertEqual(caught.exception.evidence['remote_status'],'CONFIRMED_PRESENT');self.assert_canonical_consumers_unchanged(before)

    def test_real_publisher_accepted_push_then_timeout_preserves_remote_fact(self):
        before=self.canonical_state();real=rescue.publisher.command;pushes=[]
        def command(stage,*args):
            if args[0]=='push':
                pushes.append(True);real(stage,*args);raise TimeoutError('synthetic accepted push response timeout')
            return real(stage,*args)
        with patch.object(rescue.publisher,'command',side_effect=command),self.assertRaises(ValueError) as caught:
            self.run_rescue()
        self.assertEqual(len(pushes),1);self.assertEqual(set(self.remote_rows(base.SCORES)),{self.pid})
        self.assertEqual(caught.exception.evidence['remote_status'],'CONFIRMED_PRESENT');self.assert_canonical_consumers_unchanged(before)

    def test_accepted_push_timeout_with_later_remote_head_uses_ancestry_and_business_identity(self):
        before=self.canonical_state();real=rescue.publisher.command;pushes=[];advanced=[]
        def normal(other):(other/'data/after-accepted-receipt.txt').write_text('normal later commit\n')
        def command(stage,*args):
            if args[0]=='push':
                pushes.append(self.stage_git(stage,'rev-parse','HEAD'));real(stage,*args)
                advanced.append(self.advance_remote(normal));raise TimeoutError('accepted response lost after normal remote advance')
            return real(stage,*args)
        with patch.object(rescue.publisher,'command',side_effect=command),self.assertRaises(ValueError) as caught:self.run_rescue()
        self.assertEqual(len(pushes),1);self.assertNotEqual(pushes[0],advanced[0])
        self.assertEqual(self.git('--git-dir='+str(self.remote),'rev-parse','main'),advanced[0])
        subprocess.run(['git','--git-dir='+str(self.remote),'merge-base','--is-ancestor',pushes[0],advanced[0]],check=True,capture_output=True)
        self.assertEqual(caught.exception.evidence['remote_status'],'CONFIRMED_PRESENT')
        self.assertEqual(self.remote_rows(base.SCORES)[self.pid]['status'],'scored');self.assert_canonical_consumers_unchanged(before)

    def test_real_publisher_fetch_failure_after_push_is_failure_not_absent(self):
        before=self.canonical_state();real=rescue.publisher.command;pushes=[]
        def command(stage,*args):
            if args[0]=='push':pushes.append(True)
            if pushes and args[0]=='fetch':raise subprocess.CalledProcessError(1,['git',*args],stderr=b'synthetic fetch unavailable')
            return real(stage,*args)
        with patch.object(rescue.publisher,'command',side_effect=command),self.assertRaises(ValueError) as caught:self.run_rescue()
        self.assertEqual(len(pushes),1);self.assertEqual(set(self.remote_rows(base.SCORES)),{self.pid})
        self.assertIn(caught.exception.evidence['remote_status'],('CONFIRMED_PRESENT','NOT_VERIFIED'));self.assert_canonical_consumers_unchanged(before)

    def test_real_publisher_content_readback_failure_after_push_is_not_success(self):
        before=self.canonical_state()
        with patch.object(rescue.publisher,'verify_readback',return_value=False),self.assertRaises(ValueError) as caught:self.run_rescue()
        self.assertIn('readback',str(caught.exception));self.assertEqual(set(self.remote_rows(base.SCORES)),{self.pid})
        self.assertIn(caught.exception.evidence['remote_status'],('CONFIRMED_PRESENT','NOT_VERIFIED'));self.assert_canonical_consumers_unchanged(before)

    def test_inclusive_plus20_capture_and_plus25_remote_readback_boundaries(self):
        self.now=self.target+timedelta(minutes=20);before=self.canonical_state();real=rescue.publisher.command;pushes=[]
        def command(stage,*args):
            if args[0]=='push':pushes.append(True)
            if pushes and args[0]=='fetch':self.now=self.target+timedelta(minutes=25)
            return real(stage,*args)
        with patch.object(rescue.publisher,'command',side_effect=command):out=self.run_rescue()
        self.assertEqual(out['status'],'verified_window_quote_rescue');self.assertTrue(out['remote_readback_within_window'])
        self.assertEqual(frozen.utc(out['quotes'][0]['observed_at_utc']),self.target+timedelta(minutes=20))
        self.assert_canonical_consumers_unchanged(before)

    def test_readback_one_microsecond_after_plus25_keeps_committed_fact_but_is_not_success(self):
        before=self.canonical_state();real=rescue.publisher.command;pushes=[]
        def command(stage,*args):
            if args[0]=='push':pushes.append(True)
            if pushes and args[0]=='fetch':self.now=self.target+timedelta(minutes=25,microseconds=1)
            return real(stage,*args)
        with patch.object(rescue.publisher,'command',side_effect=command):out=self.run_rescue()
        self.assertEqual(out['status'],'verified_remote_readback_after_window');self.assertFalse(out['remote_readback_within_window'])
        self.assertEqual(len(pushes),1);self.assertEqual(set(self.remote_rows(base.SCORES)),{self.pid})
        self.assert_canonical_consumers_unchanged(before)

    def test_push_outcome_unobservable_stays_not_verified_and_never_recaptures(self):
        before=self.canonical_state();real=rescue.publisher.command;pushes=[];captures=[];hidden=self.remote.with_name('temporarily-unavailable-local-remote')
        def command(stage,*args):
            if args[0]=='push':
                pushes.append(True);self.remote.rename(hidden);raise TimeoutError('synthetic transport timeout; remote inaccessible')
            return real(stage,*args)
        def capture(stage):captures.append(True);return self.capture(stage)
        try:
            with patch.object(rescue.publisher,'command',side_effect=command),self.assertRaises(ValueError) as caught:self.run_rescue(capture_fn=capture)
            self.assertEqual(caught.exception.evidence['remote_status'],'NOT_VERIFIED')
        finally:
            if hidden.exists():hidden.rename(self.remote)
        self.assertEqual(len(pushes),1);self.assertEqual(len(captures),1)
        self.assertEqual(self.remote_rows(base.SCORES),{});self.assert_canonical_consumers_unchanged(before)

    def test_dirty_and_git_environment_interference_never_mutates_business_inputs(self):
        before=self.canonical_state()
        for values in ({'GIT_DIR':str(self.root/'.git')},{'GIT_WORK_TREE':str(self.remote)},
                       {'GIT_INDEX_FILE':str(self.root/'synthetic-index')},
                       {'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'core.worktree','GIT_CONFIG_VALUE_0':str(self.remote)}):
            with self.subTest(values=list(values)),patch.dict(os.environ,values),self.assertRaises(ValueError):self.run_rescue()
            self.assert_canonical_consumers_unchanged(before)
        (self.root/'data/direction_v42/forward_scores.csv').write_bytes((self.root/base.SCORES).read_bytes()+b'\n')
        dirty=self.canonical_state()
        with self.assertRaises(ValueError):self.run_rescue()
        self.assertEqual(self.canonical_state(),dirty)

    def test_audit_hardlink_rejected_without_overwriting_its_other_name(self):
        target=Path(self.tmp.name)/'unique-audit-original.json';target.write_bytes(b'unique preserved audit source\n')
        os.link(target,self.root/'quote_rescue_result.json');before=self.canonical_state()
        with self.assertRaises(ValueError):self.run_rescue()
        self.assertEqual(target.read_bytes(),b'unique preserved audit source\n');self.assert_canonical_consumers_unchanged(before)

    def test_nonbare_origin_cannot_treat_canonical_checkout_as_its_remote(self):
        self.git('remote','set-url','origin',str(self.root));before=self.canonical_state()
        with self.assertRaises(ValueError):self.run_rescue()
        self.assert_canonical_consumers_unchanged(before);self.assertEqual(self.remote_rows(base.SCORES),{})

    def test_local_core_worktree_redirect_is_rejected_before_collection(self):
        other=Path(self.tmp.name)/'redirected-worktree';other.mkdir();self.git('config','core.worktree',str(other))
        before=self.canonical_state()
        with self.assertRaises(ValueError):self.run_rescue()
        self.assert_canonical_consumers_unchanged(before)

    def test_hardlinked_stage_score_cannot_write_back_into_canonical_ledger(self):
        before=self.canonical_state()
        def linked(stage):
            score=stage/base.SCORES;score.unlink();os.link(self.root/base.SCORES,score)
            return self.capture(stage)
        with self.assertRaises(ValueError):self.run_rescue(capture_fn=linked)
        self.assert_canonical_consumers_unchanged(before);self.assertEqual(self.remote_rows(base.SCORES),{})

    def test_failure_audit_never_dereferences_a_linked_business_file_outside_stage(self):
        sentinel='PRIVATE_CANONICAL_BINDING_SENTINEL_TEST_ONLY'
        target=self.root/'private-audit-canary.txt';target.write_text(sentinel);before=self.canonical_state()
        def linked(stage):
            binding=stage/evidence.BINDINGS;binding.unlink();os.link(target,binding)
            raise ValueError('synthetic failure after hostile business link')
        with self.assertRaises(ValueError) as caught:self.run_rescue(capture_fn=linked)
        self.assertNotIn(sentinel,json.dumps(caught.exception.evidence))
        self.assertEqual(target.read_text(),sentinel);self.assert_canonical_consumers_unchanged(before)
        self.assertNotIn(sentinel,(self.root/'quote_rescue_result.json').read_text())

    def test_failure_audit_never_dereferences_linked_git_config_or_writes_through_it(self):
        sentinel='PRIVATE_CANONICAL_CONFIG_SENTINEL_TEST_ONLY'
        config=self.root/'.git/config';config.write_text(config.read_text()+'\n# '+sentinel+'\n');before=self.canonical_state()
        def linked(stage):
            result=self.capture(stage)
            staged=stage/'.git/config';staged.unlink();os.link(config,staged)
            return result
        with self.assertRaises(ValueError) as caught:self.run_rescue(capture_fn=linked)
        self.assertNotIn(sentinel,json.dumps(caught.exception.evidence))
        self.assert_canonical_consumers_unchanged(before)
        self.assertNotIn(sentinel,(self.root/'quote_rescue_result.json').read_text())

    def test_failure_audit_never_dereferences_a_linked_raw_snapshot_outside_stage(self):
        sentinel='PRIVATE_CANONICAL_RAW_SENTINEL_TEST_ONLY'
        target=self.root/'private-raw-audit-canary.txt';target.write_text(sentinel);before=self.canonical_state()
        def linked(stage):
            path=evidence.snapshot_path(stage,'a'*64);path.parent.mkdir(parents=True,exist_ok=True);os.link(target,path)
            raise ValueError('synthetic failure after hostile raw link')
        with self.assertRaises(ValueError) as caught:self.run_rescue(capture_fn=linked)
        self.assertNotIn(sentinel,json.dumps(caught.exception.evidence))
        self.assertEqual(target.read_text(),sentinel);self.assert_canonical_consumers_unchanged(before)
        self.assertNotIn(sentinel,(self.root/'quote_rescue_result.json').read_text())

    def test_shared_stage_reflog_cannot_append_into_canonical_git_metadata(self):
        before=self.canonical_state();log=self.root/'.git/logs/HEAD';log_before=log.read_bytes()
        def linked(stage):
            result=self.capture(stage);staged=stage/'.git/logs/HEAD';staged.unlink();os.link(log,staged)
            return result
        with self.assertRaises(ValueError):self.run_rescue(capture_fn=linked)
        self.assertEqual(log.read_bytes(),log_before)
        self.assert_canonical_consumers_unchanged(before);self.assertEqual(self.remote_rows(base.SCORES),{})

    def test_temporary_root_redirect_into_source_git_is_rejected_before_any_write(self):
        before=self.canonical_state()
        metadata={p.relative_to(self.root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.root/'.git').rglob('*') if p.is_file()}
        with patch.object(rescue.tempfile,'gettempdir',return_value=str(self.root/'.git')),self.assertRaises(ValueError):self.run_rescue()
        self.assertEqual({p.relative_to(self.root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.root/'.git').rglob('*') if p.is_file()},metadata)
        self.assert_canonical_consumers_unchanged(before);self.assertFalse((self.root/'quote_rescue_result.json').exists())

    def test_actual_stage_directory_reparse_link_cannot_reach_canonical_snapshots(self):
        before=self.canonical_state();links=[]
        def linked(stage):
            link=stage/'data'/'canonical-snapshot-link';target=self.root/evidence.BASE/'snapshots'
            if os.name=='nt':
                env=dict(os.environ,QR_TEST_LINK=str(link),QR_TEST_TARGET=str(target))
                result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
                    'New-Item -ItemType Junction -Path $env:QR_TEST_LINK -Target $env:QR_TEST_TARGET -ErrorAction Stop | Out-Null'],
                    env=env,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            else:os.symlink(target,link,target_is_directory=True)
            self.assertEqual(link.resolve(),target.resolve());links.append(str(link))
            return self.capture(stage)
        with self.assertRaises(ValueError):self.run_rescue(capture_fn=linked)
        self.assertEqual(len(links),1);self.assert_canonical_consumers_unchanged(before)
        self.assertEqual(self.remote_rows(base.SCORES),{})

    def test_independent_git_index_refs_config_and_disk_footprint_are_recorded(self):
        before=self.canonical_state();out=self.run_rescue();attempt=out['attempt'];stage=Path(attempt['path'])
        self.assert_canonical_consumers_unchanged(before);self.assertTrue(attempt['git_identity']);self.assertTrue(attempt['code_identity'])
        self.assertTrue(attempt['timings']);self.assertGreater(attempt['disk_bytes'],0)
        # sparse-checkout can legitimately enable extensions.worktreeConfig in
        # a standalone clone. Test the actual redirect/common-directory boundary.
        redirect=subprocess.run(['git','config','--local','--get','core.worktree'],cwd=stage,capture_output=True)
        self.assertEqual(redirect.returncode,1);self.assertFalse((stage/'.git/commondir').exists())
        for source in (self.root/'.git/objects').rglob('*'):
            if source.is_file():
                dest=stage/'.git/objects'/source.relative_to(self.root/'.git/objects')
                if dest.exists():self.assertNotEqual(source.stat().st_ino,dest.stat().st_ino)

    def test_real_internal_rebase_preserves_normal_remote_advance_without_extra_publication(self):
        before=self.canonical_state();real=rescue.publisher.command;advanced=[];commands=[]
        def normal(other):
            path=other/'data/normal_receipt.txt';path.write_text('independent normal receipt\n')
        def command(stage,*args):
            commands.append(args)
            if args[0]=='pull' and not advanced:advanced.append(self.advance_remote(normal))
            return real(stage,*args)
        with patch.object(rescue.publisher,'command',side_effect=command):out=self.run_rescue()
        self.assertEqual(out['status'],'verified_window_quote_rescue');self.assertEqual(len([a for a in commands if a[0]=='push']),1)
        self.assertEqual(self.git('--git-dir='+str(self.remote),'show','main:data/normal_receipt.txt'),'independent normal receipt')
        subprocess.run(['git','--git-dir='+str(self.remote),'merge-base','--is-ancestor',advanced[0],'main'],check=True,capture_output=True)
        self.assertEqual(set(self.remote_rows(base.SCORES)),{self.pid});self.assert_canonical_consumers_unchanged(before)

    def test_remote_same_target_conflict_is_not_overwritten_by_internal_retries(self):
        before=self.canonical_state();real=rescue.publisher.command;advanced=[];normal_quote=[];commands=[]
        def normal(other):
            raw=self.raw(self.now,'1.2');qid=evidence.save_quote(other,raw,self.now)
            registry=frozen.read_rows(other/base.REG,frozen.FCOLS);prior=frozen.read_rows(other/base.SCORES,frozen.SCOLS)
            scores,_=frozen.score(registry,prior,1.2,self.now,self.now);frozen.write(other/base.SCORES,frozen.serial(scores,frozen.SCOLS))
            bindings=frozen.read_rows(other/evidence.BINDINGS,evidence.BCOLS);evidence.bind(bindings,self.pid,'TARGET',qid)
            frozen.write(other/evidence.BINDINGS,frozen.serial(bindings,evidence.BCOLS));normal_quote.append(qid)
        def command(stage,*args):
            commands.append(args)
            if args[0]=='pull' and not advanced:advanced.append(self.advance_remote(normal))
            return real(stage,*args)
        with patch.object(rescue.publisher,'command',side_effect=command),patch.object(rescue.publisher.time,'sleep'),self.assertRaises(ValueError):self.run_rescue()
        self.assertEqual(self.git('--git-dir='+str(self.remote),'rev-parse','main'),advanced[0])
        self.assertEqual(self.remote_rows(evidence.BINDINGS)[self.pid+'|TARGET']['quote_id'],normal_quote[0])
        self.assertEqual(self.remote_rows(base.SCORES)[self.pid]['target_price'],'1.2')
        self.assertLessEqual(len([a for a in commands if a[0]=='pull']),3);self.assertEqual(len([a for a in commands if a[0]=='push']),0)
        self.assert_canonical_consumers_unchanged(before)

    def test_rebase_code_identity_drift_stops_before_push(self):
        before=self.canonical_state();real=rescue.publisher.command;advanced=[];pushes=[]
        def normal(other):
            path=other/'publish_forward_evidence.py';path.write_bytes(path.read_bytes()+b'\n# synthetic runtime change\n')
        def command(stage,*args):
            if args[0]=='pull' and not advanced:advanced.append(self.advance_remote(normal))
            if args[0]=='push':pushes.append(True)
            return real(stage,*args)
        with patch.object(rescue.publisher,'command',side_effect=command),self.assertRaises(ValueError):self.run_rescue()
        self.assertEqual(pushes,[]);self.assertEqual(self.git('--git-dir='+str(self.remote),'rev-parse','main'),advanced[0])
        self.assertEqual(self.remote_rows(base.SCORES),{});self.assert_canonical_consumers_unchanged(before)

    def test_actual_process_termination_keeps_isolated_residue_outside_consumers(self):
        marker=Path(self.tmp.name)/'termination-marker.json'
        command=[sys.executable,'-B','-c','import test_quote_only_rescue as t; t.termination_probe('+repr(str(marker))+')']
        completed=subprocess.run(command,cwd=MODULE.parent,capture_output=True)
        self.assertEqual(completed.returncode,73,completed.stderr.decode(errors='replace'))
        info=json.loads(marker.read_text());canonical=Path(info['canonical']);stage=Path(info['stage'])
        self.assertNotEqual(canonical.resolve(),stage.resolve())
        records=frozen.read_rows(canonical/base.SCORES,frozen.SCOLS)
        self.assertEqual(records,{})
        self.assertEqual(self.stage_git(canonical,'rev-parse','HEAD'),info['head'])
        self.assertEqual(self.stage_git(canonical,'--git-dir='+info['remote'],'rev-parse','main'),info['head'])
        # The child was terminated, not gracefully unwound; evidence remains.
        qid=info['qid']
        self.assertTrue(evidence.snapshot_path(stage,qid).exists())
        audit=json.loads((canonical/'quote_rescue_result.json').read_text())
        self.assertEqual(audit['status'],'blocked');self.assertTrue(audit['raw_evidence'])
        self.assertIn(qid,json.dumps(audit['raw_evidence']))
        self.assertEqual(base.compare([info['row']],records,self.target+timedelta(minutes=26))['scored'],0)
        print(json.dumps({'synthetic_terminated_fixture':info,'preserved_raw_sha256':qid}))

    def test_unpushed_candidate_code_commit_is_never_in_data_publication_ancestry(self):
        path=self.root/'quote_only_rescue.py';path.write_bytes(path.read_bytes()+b'\n# synthetic unapproved candidate-only code\n')
        self.git('add','quote_only_rescue.py');self.git('commit','-qm','synthetic candidate code NEVER publish')
        candidate=self.git('rev-parse','HEAD');before=self.canonical_state();out=self.run_rescue()
        self.assertEqual(out['status'],'verified_window_quote_rescue');self.assert_canonical_consumers_unchanged(before)
        query=subprocess.run(['git','--git-dir='+str(self.remote),'merge-base','--is-ancestor',candidate,'main'],capture_output=True)
        self.assertNotEqual(query.returncode,0)
        source=self.git('--git-dir='+str(self.remote),'show','main:quote_only_rescue.py')
        self.assertNotIn('synthetic unapproved candidate-only code',source)

def termination_probe(marker):
    """Runs only in the explicit synthetic subprocess termination test."""
    fixture=RescueTests('test_actual_process_termination_keeps_isolated_residue_outside_consumers');fixture.setUp()
    real_save=evidence.save_quote
    def save_then_die(stage,raw,observed):
        qid=real_save(stage,raw,observed)
        Path(marker).write_text(json.dumps({'canonical':str(fixture.root),'stage':str(stage),'remote':str(fixture.remote),
             'pid':fixture.pid,'row':fixture.row,'qid':qid,'head':fixture.git('rev-parse','HEAD')}))
        os._exit(73)
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return fixture.raw(fixture.now).encode()
    # Exercise the runtime's actual default collector and pre-persistence audit
    # checkpoint; only exchange transport and abrupt process death are injected.
    with patch.object(evidence.urllib.request,'urlopen',return_value=Response()),patch.object(evidence,'save_quote',side_effect=save_then_die):
        fixture.run_rescue(capture_fn=None)

from test_quote_only_rescue_scope import ScopeTests

if __name__=='__main__':unittest.main()

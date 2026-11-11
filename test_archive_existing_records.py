"""Offline archive-role tests; no production calls or disposable Git trees."""
from contextlib import nullcontext
from datetime import datetime, timezone
import copy
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import archive_capture_bridge as archive
import runtime_deadline_service as runtime
import runtime_forward_bridge as bridge
import runtime_process
import r2_engineering_routing as routing
import test_r2_engineering_routing as routing_fixtures
import test_runtime_deadline_service as old

HERE = Path(__file__).resolve().parent
REPO = 'lzwislam-collab/doge-data-collector'
NOW = datetime(2026, 11, 11, 19, 26, 15, tzinfo=timezone.utc)


class ArchiveAdmissionTests(unittest.TestCase):
    def fixture(self):
        root = HERE/'retained-receipts'/uuid4().hex
        root.mkdir(parents=True)
        stop = root/'stop'
        env = {'GITHUB_ACTIONS':'true','GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':REPO,
            'DOGE_ARCHIVE_EXISTING_RECORDS':'1','DOGE_ARCHIVE_OWNED_PARENT_PID':str(os.getppid()),
            'DOGE_ARCHIVE_OWNED_STOP_FILE':str(stop.resolve())}
        return root, stop, env

    def test_only_explicit_owned_qualified_archive_may_enable_existing_records(self):
        check = getattr(runtime, '_archive_existing_records_allowed', None)
        self.assertTrue(callable(check), 'archive role admission is missing')
        root, stop, env = self.fixture()
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(check(True, stop, True, 600))
            for args in ((True,None,True,600),(True,stop,False,600),(True,stop,True,601)):
                with self.subTest(args=args),self.assertRaises(ValueError):check(*args)
        for change in ({'GITHUB_REF':'refs/pull/1/merge'}, {'GITHUB_REPOSITORY':'wrong/repo'},
                {'DOGE_ARCHIVE_EXISTING_RECORDS':'0'}, {'DOGE_ARCHIVE_OWNED_PARENT_PID':'999999'},
                {'DOGE_ARCHIVE_OWNED_STOP_FILE':str(root/'different-stop')}):
            with self.subTest(change=change),patch.dict(os.environ,{**env,**change},clear=True):
                with self.assertRaises(ValueError):check(True,stop,True,600)
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(check(False,None,False,2400))

    def test_existing_stop_does_not_wait_for_future_targets_or_add_a_cycle(self):
        root, stop, env = self.fixture(); stop.touch()
        adapter = SimpleNamespace(prospective_enabled=False,archive_existing_records_enabled=False,
            can_stop=lambda:True,last_qualified_book={})
        with patch.dict(os.environ,env,clear=True):
            try:
                result=runtime.run(root,adapter,600,clock=lambda:NOW,
                    sleep=lambda _:self.fail('new wait'),stop_file=stop,
                    require_qualified_stop=True,archive_existing_records=True)
            except TypeError:self.fail('explicit archive-only runtime scope is missing')
        self.assertEqual(result['cycles'],0)
        self.assertTrue(result['qualified_stop_satisfied'])
        self.assertFalse(result['prospective_bridge_enabled'])

    def test_invalid_scope_is_rejected_before_main_setup_or_writer_commands(self):
        calls=[]
        adapter=SimpleNamespace(python=lambda *a:calls.append(a),command=lambda *a:calls.append(a))
        args=SimpleNamespace(workflow_started_at=None,workflow_timeout_seconds=None,
            archive_reserve_seconds=None,duration_seconds=600,stop_file=None,
            require_qualified_stop=True,archive_existing_records=True)
        with patch.dict(os.environ,{},clear=True),self.assertRaises(ValueError):
            runtime._run_main(HERE,adapter,args)
        self.assertEqual(calls,[])


class LegacyExistingScopeTests(unittest.TestCase):
    def test_real_plan_is_called_scores_unchanged_and_binding_restores_on_all_exits(self):
        scope = getattr(bridge, 'original18_existing_scope', None)
        self.assertTrue(callable(scope), 'existing-records child plan scope is missing')
        value={'issue':[1,24],'score':['existing-id'],'matured_missing_issuance':{'1':2}}
        for error in (None, ValueError('original failure'),KeyboardInterrupt(),SystemExit(7)):
            with self.subTest(error=type(error).__name__),patch.object(bridge.original,'plan',return_value=value) as plan:
                try:
                    with scope():
                        selected=bridge.original.plan('policy','events','scores',NOW)
                        self.assertEqual(selected['issue'],[])
                        self.assertEqual(selected['score'],value['score'])
                        self.assertEqual(value['issue'],[1,24])
                        if error is not None:raise error
                except BaseException as caught:
                    self.assertIs(caught,error)
                self.assertIs(bridge.original.plan,plan)
                plan.assert_called_once_with('policy','events','scores',NOW)

    def test_owned_child_calls_complete_original_run_with_only_issuance_filtered(self):
        run = getattr(bridge,'run_original18_existing',None)
        self.assertTrue(callable(run), 'owned existing-records producer entry is missing')
        def original_run(publish=False):
            self.assertTrue(publish)
            self.assertEqual(bridge.original.plan(None,None,None,NOW)['issue'],[])
            return {'status':'original','new_events':[],'new_scores':['existing-id']}
        with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':REPO}), \
                patch.object(bridge.original,'plan',return_value={'issue':[1],'score':['existing-id']}) as plan, \
                patch.object(bridge.original,'run',side_effect=original_run) as producer:
            result=run()
            self.assertIs(bridge.original.plan,plan)
        self.assertEqual(result['new_events'],[])
        producer.assert_called_once_with(publish=True)

    def test_issuance_only_parent_admission_never_starts_existing_records_child(self):
        state={'policy':{},'events':[],'scores':{},'bindings':{}}
        with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':REPO}), \
                patch.object(bridge.original,'plan',return_value={'issue':[1],'score':[]}), \
                patch.object(bridge,'_journal',side_effect=AssertionError('no child/work expected')):
            try:
                result=bridge.capture_original18(HERE,lambda _:self.fail('new ISSUE producer'),
                    clock=lambda:NOW,state_reader=lambda _:state,existing_only=True)
            except TypeError:self.fail('existing-only parent admission is missing')
        self.assertEqual(result['status'],'SKIP_NO_CURRENT_OBLIGATION')

    def test_original_deadline_and_missing_existing_prediction_binding_are_preserved(self):
        import test_learned_shadow_v1 as fixtures
        fixture=fixtures.Shadow();policy=fixture.policy();event=fixture.event()
        state={'policy':policy,'events':[event],'scores':{},'bindings':{}}
        raw=json.dumps({'status':'original','new_events':[],'new_scores':[],
                        'errors':{},'request_witness':True})
        calls=[]
        with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':REPO}), \
                patch.object(bridge,'_journal',return_value={}):
            before=bridge.original.shadow.utc(event['issued_at_utc'])
            result=bridge.capture_original18(HERE,lambda timeout:calls.append(timeout) or raw,
                clock=lambda:before,state_reader=lambda _:state,existing_only=True)
            self.assertTrue(result['request_witness']);self.assertEqual(calls,[90])
            calls.clear()
            after=bridge.original.shadow.target_deadline(event,policy)
            result=bridge.capture_original18(HERE,lambda _:calls.append('late') or raw,
                clock=lambda:after,state_reader=lambda _:state,existing_only=True)
            self.assertEqual(result['status'],'SKIP_NO_CURRENT_OBLIGATION')
            self.assertEqual(calls,[])

    def test_child_restores_original_plan_when_original_run_guard_or_writer_raises(self):
        for error in (ValueError('full guard failed'),KeyboardInterrupt(),SystemExit(17)):
            with self.subTest(error=type(error).__name__), \
                    patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':REPO}), \
                    patch.object(bridge.original,'run',side_effect=error), \
                    patch.object(bridge.original,'plan') as plan:
                with self.assertRaises(type(error)) as caught:bridge.run_original18_existing()
                self.assertIs(caught.exception,error)
                self.assertIs(bridge.original.plan,plan)


class ExistingCycleTests(unittest.TestCase):
    def adapter(self, clock, fail_publish=False):
        class Adapter(old.Adapter):
            def state(self):
                return [{'forecast_id':'old','horizon_hours':1,
                    'source_hour_start_utc':clock.at.replace(minute=0,second=0,microsecond=0).isoformat(),
                    'target_utc':'2026-11-12T00:00:00+00:00'}],{}
            def action(self,name):
                self.calls.append(name)
                if name.startswith('custody'):raise AssertionError('new custody namespace')
                if name=='publish' and fail_publish:raise TimeoutError('unknown public push')
                if name=='original18_existing_capture':
                    return {'errors':{},'requires_publication':True,'request_witness':True,'new_events':[]}
                if name=='source_audit':return {'status':'ready'}
                if name=='book':return {'mode':'smoke','formal_eligibility':False}
                return {'status':'original'}
        return Adapter(clock)

    def test_capture_publication_and_ack_precede_sci_without_custody_or_full_bridge(self):
        clock=old.Clock();adapter=self.adapter(clock)
        state={'_prospective_enabled':False,'_archive_existing_records':True}
        result=runtime.cycle(adapter,state,clock)
        self.assertFalse(result['fatal'])
        for name in ('original18_existing_capture','original18_witness','r2_existing_dispatch'):
            self.assertIn(name,adapter.calls)
        self.assertLess(adapter.calls.index('original18_existing_capture'),adapter.calls.index('publish'))
        self.assertLess(adapter.calls.index('publish'),adapter.calls.index('original18_witness'))
        self.assertLess(adapter.calls.index('publish'),adapter.calls.index('source_audit'))
        for name in ('custody_prepare','custody_dispatch','original18_capture','r2_dispatch'):
            self.assertNotIn(name,adapter.calls)

    def test_unknown_publication_never_requests_scientific_witness_or_r2_post(self):
        clock=old.Clock();adapter=self.adapter(clock,True)
        result=runtime.cycle(adapter,{'_prospective_enabled':False,'_archive_existing_records':True},clock)
        self.assertTrue(result['fatal'])
        self.assertIn('original18_existing_capture',adapter.calls)
        self.assertNotIn('original18_witness',adapter.calls)
        self.assertNotIn('r2_existing_dispatch',adapter.calls)

    def test_r2_only_existing_work_is_not_held_behind_unrelated_source_audit(self):
        clock=old.Clock();adapter=self.adapter(clock)
        original_action=adapter.action
        def action(name):
            value=original_action(name)
            if name=='original18_existing_capture':
                return {'errors':{},'requires_publication':False,'request_witness':False,'new_events':[]}
            return value
        adapter.action=action
        result=runtime.cycle(adapter,{'_prospective_enabled':False,'_archive_existing_records':True},clock)
        self.assertFalse(result['fatal'])
        self.assertLess(adapter.calls.index('r2_existing_dispatch'),adapter.calls.index('source_audit'))
        self.assertEqual(adapter.calls.count('r2_existing_dispatch'),1)
        self.assertNotIn('original18_witness',adapter.calls)


class R2ExistingScopeTests(unittest.TestCase):
    def fixture(self):
        case=routing_fixtures.EngineeringRouting('test_existing_unique_canary_guard_is_retained')
        case.setUp();self.addCleanup(case.doCleanups)
        return case

    def call(self, case):
        try:return routing.route_r2(Path.cwd(),None,case.state,clock=lambda:case.remote.clock,
                                   client=case.remote,existing_only=True)
        except TypeError:self.fail('R2 existing-records route scope is missing')

    def test_new_source_and_issuance_choices_are_not_dispatched(self):
        for source in (False,True):
            with self.subTest(source_witness=source):
                case=self.fixture()
                if source:case.source_witness()
                self.assertEqual(self.call(case)['status'],'SKIP_NO_CURRENT_R2_EXISTING_RECORD_OBLIGATION')
                self.assertEqual(case.remote.posts,[])

    def test_existing_prediction_witness_still_routes_canary_without_new_issue(self):
        case=self.fixture();case.remote.add_event('formal',case.now.replace(minute=5))
        result=self.call(case)
        self.assertEqual(result['purpose'],'prediction_witness')
        self.assertEqual(case.remote.posts[0][1]['inputs']['mode'],'canary')

    def test_missing_prediction_proof_keeps_original_wait_reason_not_a_successful_skip(self):
        case=self.fixture();case.remote.add_event('formal',case.now.replace(minute=5))
        case.remote.clock=case.now.replace(hour=15)
        self.assertEqual(self.call(case)['status'],'WAIT_MISSING_ORIGINAL_PREDICTION_WITNESS')
        self.assertEqual(case.remote.posts,[])

    def test_existing_target_and_binding_keep_canary_and_old_scientific_guards(self):
        for due in (False,True):
            with self.subTest(due=due):
                case=self.fixture()
                path,event=case.remote.add_event('formal',case.now.replace(minute=5))
                case.remote.audit(3,'c'*40,case.now.replace(minute=6),case.now.replace(minute=8))
                case.remote.reads[('c'*40,routing.original.frozen.f.POLICY)]=b'fixed original bytes'
                case.remote.reads[('c'*40,path)]=case.remote.files[path]
                if due:case.remote.clock=case.now.replace(hour=15)
                result=self.call(case)
                self.assertEqual(result['purpose'],'target' if due else 'binding')
                self.assertEqual(case.remote.posts[-1][1]['inputs']['mode'],'canary')
        case=self.fixture()
        case.remote.files.pop(next(name for name in case.remote.files if name.endswith('_canary.json')))
        with self.assertRaises(ValueError):self.call(case)
        self.assertEqual(case.remote.posts,[])

    def test_unknown_existing_records_post_is_consumed_within_the_same_owner_state(self):
        case=self.fixture();case.remote.add_event('formal',case.now.replace(minute=5));case.remote.unknown=True
        with self.assertRaises(TimeoutError):self.call(case)
        self.assertEqual(self.call(case)['status'],'ALREADY_ATTEMPTED_THIS_R2_CONTEXT')
        self.assertEqual(len(case.remote.posts),1)


class WorkerScopeTests(unittest.TestCase):
    def call(self, enabled=True, qualified=True):
        captured=[]
        def launch(args,**kwargs):captured.append((args,kwargs));return SimpleNamespace(wait=lambda:0)
        env={'DOGE_ARCHIVE_QUALIFIED_DRAIN':'1' if qualified else '0',
             'DOGE_ARCHIVE_EXISTING_RECORDS':'1' if enabled else '0',
             'OPENBLAS_NUM_THREADS':'32','OMP_NUM_THREADS':'99'}
        with patch.dict(os.environ,env,clear=True),patch.object(archive,'trusted'), \
                patch.object(archive.subprocess,'Popen',side_effect=launch),patch.object(archive,'atomic_json'), \
                patch.object(runtime_process,'cancellation_signals',return_value=nullcontext()), \
                patch.object(runtime_process,'defer_cancellation',return_value=nullcontext()):
            archive.worker(HERE,HERE/'retained-control')
        return captured

    def test_worker_pins_threads_and_only_explicit_qualified_mode_adds_scope(self):
        for enabled in (False,True):
            args,options=self.call(enabled)[0]
            self.assertEqual(args[args.index('--duration-seconds')+1],'600')
            self.assertIn('--stop-file',args);self.assertIn('--require-qualified-stop',args)
            self.assertEqual('--archive-existing-records' in args,enabled)
            self.assertEqual(options['env']['OPENBLAS_NUM_THREADS'],'1')
            self.assertEqual(options['env']['OMP_NUM_THREADS'],'1')
            if enabled:self.assertEqual(options['env']['DOGE_ARCHIVE_OWNED_PARENT_PID'],str(os.getpid()))

    def test_nonqualified_worker_cannot_start_existing_records_scope(self):
        with self.assertRaises(ValueError):self.call(True,False)


class LiveArchiveActionTests(unittest.TestCase):
    def adapter(self):
        adapter=object.__new__(runtime.LiveAdapter)
        adapter.root=HERE
        adapter.prospective_enabled=False
        adapter.archive_existing_records_enabled=True
        adapter.prospective_state={}
        adapter.prospective_client=object()
        return adapter

    def test_existing_capture_uses_owned_ninety_second_child_and_scoped_entry(self):
        adapter=self.adapter()
        result={'status':'original','new_events':[],'new_scores':[],'errors':{}}
        def capture(root,producer,**options):
            self.assertIs(options['existing_only'],True)
            return json.loads(producer(90))
        with patch.object(bridge,'capture_original18',side_effect=capture), \
                patch.object(runtime_process,'command',return_value=json.dumps(result)) as command:
            self.assertEqual(adapter.action('original18_existing_capture'),result)
        self.assertEqual(command.call_args.kwargs['timeout'],90)
        self.assertEqual(command.call_args.kwargs['cwd'],HERE)
        self.assertIn('run_original18_existing',command.call_args.args[0][-1])
        self.assertEqual(command.call_args.kwargs['env']['OPENBLAS_NUM_THREADS'],'1')
        self.assertEqual(command.call_args.kwargs['env']['OMP_NUM_THREADS'],'1')

    def test_archive_role_does_not_admit_full_issue_or_custody_actions(self):
        adapter=self.adapter()
        for name in ('original18_capture','r2_dispatch','custody_prepare','custody_dispatch'):
            self.assertEqual(adapter.action(name)['status'],'DISABLED_OUTSIDE_FOREGROUND')
        with patch.object(bridge,'route_r2',return_value={'status':'existing'}) as route:
            adapter.action('r2_existing_dispatch')
        self.assertIs(route.call_args.kwargs['existing_only'],True)

    def test_new_cycle_keeps_existing_unknown_audit_intent_state(self):
        adapter=self.adapter();state={'original18_audit_attempted_head':'a'*40}
        adapter.prospective_state=state
        import runtime_custody_bridge as custody
        with patch.object(custody,'GitHubClient',return_value=object()):adapter.begin_prospective_cycle()
        self.assertIs(adapter.prospective_state,state)
        self.assertEqual(state,{'original18_audit_attempted_head':'a'*40})


class ArchivePublicationBoundaryTests(unittest.TestCase):
    """Real adapter+publisher+readback; only Git/owned child/provider are isolated.

    This checks publication wiring, not scientific acceptance of fixture bytes.
    Neither publisher.publish nor publisher.verify_readback nor adapter publish
    is mocked. No Git repository, shell command or network request is created.
    """
    def fixture(self, foreground=False, existing=True):
        import publish_forward_evidence as publisher
        import paired_forward_cohort as pair
        self.publisher=publisher
        root=HERE/'retained-publisher-receipts'/uuid4().hex
        root.mkdir(parents=True)
        def write(path,raw):
            target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        write(publisher.PATHS[0],b'forecast_id\n')
        (root/pair.BASE).mkdir(parents=True,exist_ok=True)
        approved={
            'data/learned_shadow_v1/scores/1794420000_1.json':b'{"fixture":"exact new target score"}\n',
            'data/learned_shadow_v1/witness_bindings/1794420000_1_target.json':b'{"fixture":"exact new binding"}\n'}
        initial={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
        for path,raw in approved.items():write(path,raw)
        test=self
        class GitBoundary:
            def __init__(self):
                self.head=dict(initial);self.remote=dict(initial);self.index=set()
                self.calls=[];self.pulls=self.pushes=0;self.sequence=1
                self.bad_ack=False;self.unknown_push=False;self.rebase_conflict=False
                self.change_on_pull=False
            def command(self, current_root, *args):
                test.assertEqual(Path(current_root),root)
                self.calls.append(args)
                if args[:4]==('diff','HEAD','--name-only','--'):return b''
                if args[:3]==('add','-f','--'):
                    for name in args[3:]:
                        target=root/name
                        files=[target] if target.is_file() else [p for p in target.rglob('*') if p.is_file()]
                        self.index.update(p.relative_to(root).as_posix() for p in files
                            if self.head.get(p.relative_to(root).as_posix())!=p.read_bytes())
                    return b''
                if args==('diff','--cached','--name-only'):
                    return ('\n'.join(sorted(self.index))+'\n' if self.index else '').encode()
                if args[:1]==('commit',):
                    self.head.update((p,(root/p).read_bytes()) for p in self.index)
                    self.index.clear();self.sequence+=1;return b''
                if args[:1]==('pull',):
                    self.pulls+=1
                    if self.rebase_conflict and self.pulls==1:
                        raise subprocess.CalledProcessError(1,['git','pull'],stderr=b'known rebase conflict')
                    if self.change_on_pull:
                        write(next(iter(approved)),b'{"fixture":"changed by rebase"}\n')
                    return b''
                if args[:1]==('push',):
                    self.pushes+=1
                    if self.unknown_push:raise TimeoutError('unknown remote push result')
                    self.remote=dict(self.head);return b''
                if args[:1]==('fetch',):return b''
                if args[:1]==('rev-parse',):return (format(self.sequence,'040x')+'\n').encode()
                if args[:1]==('show',):
                    head,path=args[1].split(':',1)
                    mapping=self.remote if head=='FETCH_HEAD' else self.head
                    if path not in mapping:raise subprocess.CalledProcessError(128,['git','show'])
                    if self.bad_ack and head=='FETCH_HEAD' and path in approved:return b'wrong remote legacy bytes'
                    return mapping[path]
                raise AssertionError('unexpected Git boundary '+repr(args))
        git=GitBoundary()
        adapter=object.__new__(runtime.LiveAdapter)
        adapter.root=root;adapter.prospective_enabled=foreground
        adapter.archive_existing_records_enabled=existing;adapter.prospective_state={}
        adapter.core_witness_pending=False;adapter.last_witness_head=None
        adapter.last_verified_publication=None;adapter.pending_source_binding=None
        adapter.original18_capture={'request_witness':True,'changed_paths':sorted(approved),
            'runtime_status':'READY_PUBLICATION_ONLY','requires_publication':True,'new_events':[],'errors':{}}
        def adapter_command(*args):
            self.assertEqual(args[0],'git');return git.command(root,*args[1:]).decode()
        adapter.command=adapter_command
        class Provider:
            def __init__(self):self.posts=[];self.unknown=False
            def api(self,path):return {'total_count':0,'workflow_runs':[]}
            def request(self,path,payload):
                test.assertIsNotNone(adapter.last_verified_publication)
                test.assertTrue(set(approved)<=set(adapter.last_verified_publication['verified_paths']))
                self.posts.append((path,payload))
                if self.unknown:raise TimeoutError('unknown SCI POST result')
                return 204
        provider=Provider();adapter.prospective_client=provider
        guards=[]
        def child_command(args, **kwargs):
            self.assertIn('validate_original18',args[-1])
            self.assertEqual(kwargs['cwd'],root)
            self.assertEqual(kwargs['timeout'],240)
            self.assertEqual(kwargs['env']['OPENBLAS_NUM_THREADS'],'1')
            guards.append({path:(root/path).read_bytes() for path in approved})
            return ('ORIGINAL18_FULL_GUARD_VERIFIED' if guards[-1]==approved else 'FULL_GUARD_REJECTED')
        source_binding=SimpleNamespace(base=SimpleNamespace(WITNESSES='data/stage5/forward_validation/remote_witnesses.csv'))
        for name in ('validate_p2','validate_publication_state','validate_paired_history'):
            self.enterContext(patch.object(runtime,name,return_value=None))
        self.enterContext(patch.object(pair,'guard',return_value=None))
        self.enterContext(patch.object(runtime,'_source_binding_backend',return_value=source_binding))
        self.enterContext(patch.object(publisher,'command',side_effect=git.command))
        self.enterContext(patch.object(runtime_process,'command',side_effect=child_command))
        self.enterContext(patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':REPO}))
        # The original publisher aborts/retries a known local rebase conflict.
        # Isolate even that command and sleep so this test never invokes Git.
        self.enterContext(patch.object(publisher.subprocess,'run',return_value=SimpleNamespace(returncode=0)))
        self.enterContext(patch.object(publisher.time,'sleep',return_value=None))
        return adapter,git,provider,guards,approved

    def cycle(self, adapter):
        real_action=adapter.action
        adapter.begin_prospective_cycle=lambda:None
        adapter.state=lambda:([{'forecast_id':'existing','horizon_hours':1,
            'source_hour_start_utc':NOW.replace(minute=0,second=0,microsecond=0).isoformat(),
            'target_utc':'2026-11-12T00:00:00+00:00'}],{})
        calls=[]
        def action(name):
            calls.append(name)
            if name in ('publish','original18_witness'):return real_action(name)
            if name=='original18_existing_capture':return adapter.original18_capture
            if name=='source_audit':return {'status':'ready'}
            if name=='book':return {'mode':'smoke','formal_eligibility':False}
            return {'status':'unchanged primary test boundary'}
        adapter.action=action
        result=runtime.cycle(adapter,{'_prospective_enabled':False,'_archive_existing_records':True},lambda:NOW)
        return result,calls

    def test_real_publish_stages_legacy_exact_bytes_and_guards_both_rebases(self):
        adapter,git,provider,guards,approved=self.fixture()
        publication=adapter.action('publish')
        self.assertFalse(adapter.prospective_enabled)
        self.assertTrue(set(approved)<=set(publication['verified_paths']))
        self.assertEqual({p:git.remote.get(p) for p in approved},approved)
        self.assertEqual(guards,[approved]*3)  # preflight, data rebase, receipt rebase
        self.assertEqual((git.pulls,git.pushes),(2,2))
        reads=[args[1] for args in git.calls if args[:1]==('show',) and args[1].startswith('FETCH_HEAD:')]
        for path in approved:self.assertEqual(reads.count('FETCH_HEAD:'+path),2)
        stages=[phase['stage'] for phase in publication['publication_profile']['phases']]
        for stage in ('preflight_original18_guard','data_post_rebase_original18_guard',
                      'receipt_post_rebase_original18_guard'):self.assertIn(stage,stages)
        self.assertEqual(adapter.action('original18_witness')['status'],'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')
        self.assertEqual(len(provider.posts),1)

    def test_default_archive_does_not_enable_namespace_or_guard_and_foreground_keeps_them(self):
        for foreground,existing in ((False,False),(True,False)):
            with self.subTest(foreground=foreground):
                adapter,git,provider,guards,approved=self.fixture(foreground,existing)
                publication=adapter.action('publish')
                self.assertEqual(bool(set(approved)&set(publication['verified_paths'])),foreground)
                self.assertEqual(len(guards),3 if foreground else 0)
                self.assertFalse(provider.posts)

    def test_wrong_legacy_remote_ack_stops_actual_cycle_before_scientific_request(self):
        adapter,git,provider,guards,approved=self.fixture();git.bad_ack=True
        result,calls=self.cycle(adapter)
        self.assertTrue(result['fatal'])
        self.assertTrue(any('supplementary evidence remote readback mismatch' in e['error'] for e in result['errors']))
        self.assertNotIn('original18_witness',calls)
        self.assertNotIn('r2_existing_dispatch',calls)
        self.assertIsNone(adapter.last_verified_publication)
        self.assertEqual(provider.posts,[])

    def test_original_preflight_guard_rejects_before_any_publication_or_sci(self):
        adapter,git,provider,guards,approved=self.fixture()
        path=next(iter(approved));(adapter.root/path).write_bytes(b'{"fixture":"bad before publication"}\n')
        result,calls=self.cycle(adapter)
        self.assertTrue(result['fatal'])
        self.assertEqual(len(guards),1)
        self.assertEqual((git.pulls,git.pushes),(0,0))
        self.assertNotIn('original18_witness',calls)
        self.assertEqual(provider.posts,[])

    def test_rebase_changed_legacy_bytes_must_fail_original_guard_before_push_or_sci(self):
        adapter,git,provider,guards,approved=self.fixture()
        git.rebase_conflict=True;git.change_on_pull=True
        result,calls=self.cycle(adapter)
        self.assertTrue(result['fatal'])
        self.assertEqual(git.pulls,2)
        self.assertEqual(len(guards),2)
        self.assertEqual(guards[0],approved)
        self.assertNotEqual(guards[1],approved)
        self.assertEqual(git.pushes,0)
        self.assertNotIn('original18_witness',calls)
        self.assertEqual(provider.posts,[])

    def test_unknown_public_push_is_not_retried_and_never_admits_sci(self):
        adapter,git,provider,guards,approved=self.fixture();git.unknown_push=True
        result,calls=self.cycle(adapter)
        self.assertTrue(result['fatal'])
        self.assertEqual(git.pushes,1)
        self.assertNotIn('original18_witness',calls)
        self.assertNotIn('r2_existing_dispatch',calls)
        self.assertEqual(provider.posts,[])
        self.assertEqual(adapter.prospective_state,{})

    def test_exact_publication_then_unknown_sci_post_keeps_original_consumed_marker(self):
        adapter,git,provider,guards,approved=self.fixture()
        publication=adapter.action('publish');provider.unknown=True
        with self.assertRaises(TimeoutError):adapter.action('original18_witness')
        state=dict(adapter.prospective_state)
        self.assertEqual(state,{'original18_audit_attempted_head':publication['head']})
        self.assertEqual(adapter.action('original18_witness')['status'],'UNKNOWN_PREVIOUS_AUDIT_DISPATCH_NOT_RETRIED')
        self.assertEqual(adapter.prospective_state,state)
        self.assertEqual(len(provider.posts),1)


if __name__=='__main__':unittest.main()

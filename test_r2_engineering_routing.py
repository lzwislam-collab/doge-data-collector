"""The real old router retains admission guards; provider I/O is synthetic."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import base64
import importlib
import os
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import copy
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import phase2_forward_runtime_v2 as frozen
import runtime_r2_bridge as original
from test_runtime_r2_bridge import Remote as OriginalRemote
import r2_engineering_runtime as engine
import r2_engineering_dispatch as dispatch
import r2_engineering_readiness as readiness
import forward_readiness as old_readiness

try:
    subject = importlib.import_module('r2_engineering_routing')
except ModuleNotFoundError:
    subject = None

OLD_SERVER = original._server_runs
MANIFEST = 'config/r2_engineering_transport_v1.json'


class Provider(OriginalRemote):
    def __init__(self, now):
        super().__init__(now)
        self.dispatched = []
        self.unknown = False
    def api(self, path):
        uri, query = urlsplit(path), parse_qs(urlsplit(path).query)
        if '/actions/workflows/' in uri.path and 'status' in query and self.active:
            workflow=uri.path.split('/')[3]
            if self.active['path'] != '.github/workflows/'+workflow:
                return {'total_count':0,'workflow_runs':[]}
        if uri.path == '/actions/workflows/'+dispatch.LIVE+'/runs' and 'status' not in query:
            return {'total_count':len(self.dispatched),'workflow_runs':list(self.dispatched)}
        if uri.path.startswith('/contents/') and query.get('ref') != [self.head]:
            head=query['ref'][0];relative=uri.path[len('/contents/'):]
            raw=self.reads.get((head,relative),b'older different bytes')
            return {**self.meta(relative,raw),'encoding':'base64','content':base64.b64encode(raw).decode()}
        return super().api(path)
    def request(self, path, payload=None):
        if payload is None:return self.api(path)
        self.posts.append((path,payload))
        if self.unknown:raise TimeoutError('POST outcome unknown')
        token=payload['inputs']['request_id']
        self.dispatched.insert(0,{'id':100+len(self.posts),'head_sha':self.head,'head_branch':'main',
          'path':'.github/workflows/'+dispatch.LIVE,'event':'workflow_dispatch','status':'queued',
          'display_title':'R2E1 publish '+token,'created_at':self.clock.isoformat(),
          'repository':{'full_name':frozen.f.old.REPO}})
        return 204


class EngineeringRouting(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(subject,'compatible engineering router is missing')
        self.now=datetime(2026,11,9,14,10,tzinfo=timezone.utc)
        self.remote=Provider(self.now);self.state={}
        self.manifest_raw=b'committed executing engineering identity'
        self.manifest_blob=subject._blob(self.manifest_raw)
        self.remote.files[MANIFEST]=self.manifest_raw
        original.transport._TREE_CACHE.clear();original.transport._POLICY_CACHE.clear()
        for target in (
            patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':frozen.f.old.REPO}),
            patch.object(engine,'context',return_value=nullcontext()),
            patch.object(engine,'runs_server',side_effect=lambda extra=():OLD_SERVER(self.remote.api,extra)),
            patch.object(original,'_remote_fixed_files',return_value={frozen.f.POLICY:b'fixed original bytes'}),
            patch.object(dispatch,'committed_manifest_blob',return_value=self.manifest_blob,create=True),
            patch.object(subject,'_local_git',return_value=None)):
            target.start();self.addCleanup(target.stop)
    def call(self):
        return subject.route_r2(Path.cwd(),None,self.state,clock=lambda:self.remote.clock,client=self.remote)
    def source_witness(self):
        self.remote.audit(2,'b'*40,self.now-timedelta(minutes=2),self.now-timedelta(minutes=1))
        self.remote.reads[('b'*40,frozen.SOURCE)]=self.remote.source
    def test_missing_source_bootstraps_only_new_publisher_with_real_head_binding(self):
        out=self.call()
        self.assertEqual(out['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(out['workflow'],dispatch.LIVE)
        self.assertEqual(self.remote.posts[0][0],'/actions/workflows/'+dispatch.LIVE+'/dispatches')
        self.assertEqual(self.remote.posts[0][1]['inputs']['mode'],'publish')
        self.assertEqual(self.remote.posts[0][1]['inputs']['not_after_utc'],'2026-11-09T14:45:00+00:00')
        self.assertFalse(out['witness_or_forecast_verified'])
    def test_existing_source_proof_still_routes_compatible_publisher(self):
        self.source_witness();out=self.call()
        self.assertEqual(out['purpose'],'issue');self.assertEqual(len(self.remote.posts),1)
    def test_dispatch_pins_committed_local_manifest_alongside_data(self):
        with patch.object(dispatch,'dispatch_bound',wraps=dispatch.dispatch_bound) as bound:
            result=self.call()
        self.assertEqual(result['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(bound.call_args.args[1].get(MANIFEST),self.manifest_blob)
    def test_same_data_with_different_remote_engineering_version_never_posts(self):
        self.remote.files[MANIFEST]=b'older engineering release with identical source data'
        result=self.call()
        self.assertEqual(result['status'],'DEFERRED_PUBLIC_BYTES_CHANGED')
        self.assertEqual(self.remote.posts,[])
    def test_closed_original_issue_window_has_no_post(self):
        self.remote.clock=self.now.replace(minute=45)
        self.assertEqual(self.call()['status'],'SKIP_NO_CURRENT_R2_OBLIGATION')
        self.assertEqual(self.remote.posts,[])
    def test_existing_unique_canary_guard_is_retained(self):
        self.remote.files.pop(next(p for p in self.remote.files if p.endswith('_canary.json')))
        with self.assertRaises(ValueError):self.call()
        self.assertEqual(self.remote.posts,[])
    def test_missing_pretarget_prediction_witness_never_creates_late_proof(self):
        self.remote.clock=self.now.replace(hour=15)
        self.remote.add_event('formal',self.now.replace(minute=5))
        self.assertEqual(self.call()['status'],'WAIT_MISSING_ORIGINAL_PREDICTION_WITNESS')
        self.assertEqual(self.remote.posts,[])
    def test_qualified_due_target_uses_its_original_deadline(self):
        self.remote.clock=self.now.replace(hour=15)
        path,event=self.remote.add_event('formal',self.now.replace(minute=5))
        self.remote.audit(3,'c'*40,self.now.replace(minute=6),self.now.replace(minute=8))
        self.remote.reads[('c'*40,frozen.f.POLICY)]=b'fixed original bytes'
        self.remote.reads[('c'*40,path)]=self.remote.files[path]
        out=self.call();self.assertEqual(out['purpose'],'target')
        self.assertEqual(self.remote.posts[0][1]['inputs']['not_after_utc'],'2026-11-09T15:30:00+00:00')
        self.assertEqual(self.remote.posts[0][1]['inputs']['mode'],'canary')
    def test_due_target_keeps_original_canary_mode_at_odd_and_crossed_even_slots(self):
        for at in (self.now.replace(hour=15,minute=50),self.now.replace(hour=16,minute=0)):
            with self.subTest(at=at):
                self.state.clear();self.remote.posts.clear();self.remote.dispatched.clear()
                self.remote.clock=at
                path,event=self.remote.add_event('formal',self.now.replace(minute=37))
                self.remote.audit(3,'c'*40,self.now.replace(minute=38),self.now.replace(minute=40))
                self.remote.reads[('c'*40,frozen.f.POLICY)]=b'fixed original bytes'
                self.remote.reads[('c'*40,path)]=self.remote.files[path]
                result=self.call()
                self.assertEqual(result['purpose'],'target')
                self.assertEqual(self.remote.posts[-1][1]['inputs']['mode'],'canary')
    def test_pre_target_binding_and_missing_prediction_keep_no_issue_mode(self):
        path,event=self.remote.add_event('formal',self.now.replace(minute=5))
        result=self.call()
        self.assertEqual(result['purpose'],'prediction_witness')
        self.assertEqual(self.remote.posts[-1][1]['inputs']['mode'],'canary')
        self.state.clear();self.remote.posts.clear();self.remote.dispatched.clear()
        self.remote.audit(3,'c'*40,self.now.replace(minute=6),self.now.replace(minute=8))
        self.remote.reads[('c'*40,frozen.f.POLICY)]=b'fixed original bytes'
        self.remote.reads[('c'*40,path)]=self.remote.files[path]
        result=self.call()
        self.assertEqual(result['purpose'],'binding')
        self.assertEqual(self.remote.posts[-1][1]['inputs']['mode'],'canary')
    def test_registered_purpose_workflow_mode_combinations_are_exact_and_unknown_fail_closed(self):
        select=getattr(subject,'_dispatch_mode',None)
        self.assertTrue(callable(select),'registered purpose/workflow/mode mapping missing')
        allowed=(('target',original.PUBLISHER,'canary','canary'),
            ('binding',original.PUBLISHER,'canary','canary'),
            ('issue',original.PUBLISHER,'publish','publish'),
            ('source_witness',original.AUDIT,None,'publish'),
            ('prediction_witness',original.AUDIT,None,'canary'))
        for purpose,workflow,mode,expected in allowed:
            self.assertEqual(select({'purpose':purpose,'workflow':workflow,'mode':mode}),expected)
        for invalid in ({}, {'purpose':'unknown','workflow':original.AUDIT,'mode':None},
                {'purpose':'target','workflow':original.PUBLISHER,'mode':'publish'},
                {'purpose':'binding','workflow':original.AUDIT,'mode':'canary'},
                {'purpose':'issue','workflow':original.PUBLISHER,'mode':''},
                {'purpose':'source_witness','workflow':original.AUDIT,'mode':''},
                {'purpose':'prediction_witness','workflow':original.AUDIT,'mode':'publish'},
                {'purpose':'target','workflow':'unknown.yml','mode':'canary'}):
            with self.subTest(choice=invalid),self.assertRaises(ValueError):select(invalid)
    def test_old_or_new_active_publisher_prevents_replacement(self):
        for workflow in (original.PUBLISHER,dispatch.LIVE):
            with self.subTest(workflow=workflow):
                self.state.clear();self.remote.active={'id':9,'head_branch':'main',
                  'path':'.github/workflows/'+workflow,'status':'queued'}
                self.assertEqual(self.call()['status'],'DEFERRED_ACTIVE_R2_WORKFLOW')
                self.assertEqual(self.remote.posts,[])
    def test_unknown_post_is_not_retried_in_same_caller_state(self):
        self.remote.unknown=True
        with self.assertRaises(TimeoutError):self.call()
        self.assertEqual(self.call()['status'],'ALREADY_ATTEMPTED_THIS_R2_CONTEXT')
        self.assertEqual(len(self.remote.posts),1)
    def test_readonly_manifest_failure_does_not_consume_new_context(self):
        with patch.object(dispatch,'committed_manifest_blob',side_effect=TimeoutError('read-only identity timeout')):
            with self.assertRaises(TimeoutError):self.call()
        self.assertEqual(self.remote.posts,[])
        self.assertEqual(self.state.get('r2_dispatch_attempts',{}),{})
        self.assertEqual(self.state.get('engineering_bound_dispatches',{}),{})
        self.assertEqual(self.state.get('engineering_routing_attempts',{}),{})
        self.assertEqual(self.call()['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(len(self.remote.posts),1)
    def test_bound_nonce_before_client_check_is_not_a_sent_post(self):
        ticks=[0.0]
        def token():
            ticks[0]=41.0
            return SimpleNamespace(hex='known-preflight-no-wire')
        with patch.object(subject.time,'monotonic',side_effect=lambda:ticks[0]), \
             patch.object(dispatch,'uuid4',side_effect=token):
            with self.assertRaisesRegex(TimeoutError,'engineering routing total budget exhausted'):self.call()
        self.assertEqual(self.remote.posts,[])
        self.assertEqual(self.state.get('engineering_bound_dispatches',{}),{})
        self.assertEqual(self.state.get('engineering_routing_attempts',{}),{})
        self.assertEqual(self.call()['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(len(self.remote.posts),1)
    def test_local_promotion_prepare_crossing_budget_never_commits_or_posts(self):
        ticks=[0.0]
        old={'old-intent':{'request_id':'protected-old','attempt':1}}
        class SlowComparison(dict):
            def __ne__(self,other):
                ticks[0]=41.0
                return super().__ne__(other)
        old['old-intent']=SlowComparison(old['old-intent'])
        self.state['engineering_bound_dispatches']=old
        before=copy.deepcopy(old)
        with patch.object(subject.time,'monotonic',side_effect=lambda:ticks[0]):
            with self.assertRaisesRegex(TimeoutError,'engineering routing total budget exhausted'):self.call()
        self.assertEqual(self.remote.posts,[])
        self.assertEqual(self.state.get('engineering_routing_attempts',{}),{})
        self.assertIs(self.state['engineering_bound_dispatches'],old)
        self.assertEqual(old,before)
    def test_existing_bound_conflict_is_validated_before_any_new_marker(self):
        class Conflict(dict):
            def __ne__(self,other):return True
        old={'old-intent':Conflict(request_id='protected-old',attempt=1)}
        self.state['engineering_bound_dispatches']=old
        before=copy.deepcopy(old)
        with self.assertRaisesRegex(ValueError,'existing engineering intent changed'):self.call()
        self.assertEqual(self.remote.posts,[])
        self.assertEqual(self.state.get('engineering_routing_attempts',{}),{})
        self.assertIs(self.state['engineering_bound_dispatches'],old)
        self.assertEqual(old,before)
    def test_readonly_manifest_deferral_can_be_rechecked_after_bytes_match(self):
        self.remote.files[MANIFEST]=b'old identity'
        self.assertEqual(self.call()['status'],'DEFERRED_PUBLIC_BYTES_CHANGED')
        self.assertEqual(self.remote.posts,[])
        self.remote.files[MANIFEST]=self.manifest_raw
        self.assertEqual(self.call()['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(len(self.remote.posts),1)
    def test_acknowledged_post_followed_by_budget_error_is_never_repeated(self):
        ticks=[0.0]
        real=self.remote.request
        def accepted(path,payload=None):
            result=real(path,payload)
            if payload is not None:ticks[0]=41.0
            return result
        with patch.object(subject.time,'monotonic',side_effect=lambda:ticks[0]), \
             patch.object(self.remote,'request',side_effect=accepted):
            with self.assertRaises(TimeoutError):self.call()
        self.assertEqual(len(self.remote.posts),1)
        self.assertEqual(self.call()['status'],'ALREADY_ATTEMPTED_THIS_R2_CONTEXT')
        self.assertEqual(len(self.remote.posts),1)
        self.assertTrue(self.state.get('engineering_routing_attempts'))
        self.assertTrue(self.state['engineering_bound_dispatches'])
    def test_old_attempt_and_bound_objects_are_preserved_and_other_state_propagates(self):
        attempts={'previous-unknown-context':{'head':'1'*40,'at_utc':'unknown'}}
        bound={'previous-unknown-intent':{'request_id':'old-marker','attempt':1}}
        saved=copy.deepcopy((attempts,bound))
        self.state.update(r2_dispatch_attempts=attempts,engineering_bound_dispatches=bound)
        with patch.object(dispatch,'committed_manifest_blob',side_effect=TimeoutError('read-only timeout')):
            with self.assertRaises(TimeoutError):self.call()
        self.assertIs(self.state['r2_dispatch_attempts'],attempts)
        self.assertIs(self.state['engineering_bound_dispatches'],bound)
        self.assertEqual((attempts,bound),saved)
        self.assertIn('r2_source_frame_context',self.state)
        self.assertTrue(self.state['engineering_routing_bytes'])
        self.assertEqual(self.call()['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(attempts,saved[0])
        self.assertEqual(bound['previous-unknown-intent'],saved[1]['previous-unknown-intent'])
    def test_preflight_failure_does_not_reopen_original_window(self):
        with patch.object(dispatch,'committed_manifest_blob',side_effect=TimeoutError('read-only timeout')):
            with self.assertRaises(TimeoutError):self.call()
        self.remote.clock=self.now.replace(minute=45)
        self.assertEqual(self.call()['status'],'SKIP_NO_CURRENT_R2_OBLIGATION')
        self.assertEqual(self.remote.posts,[])
    def test_existing_engineering_sent_attempt_is_not_overwritten_or_retried(self):
        self.remote.unknown=True
        with self.assertRaises(TimeoutError):self.call()
        self.assertTrue(self.state.get('engineering_routing_attempts'))
        sent=copy.deepcopy(self.state['engineering_routing_attempts'])
        self.assertEqual(self.call()['status'],'ALREADY_ATTEMPTED_THIS_R2_CONTEXT')
        self.assertEqual(self.state['engineering_routing_attempts'],sent)
        self.assertEqual(len(self.remote.posts),1)
    def test_phase_diagnostic_has_only_request_kind_and_started_flag(self):
        with patch.object(dispatch,'committed_manifest_blob',side_effect=TimeoutError('private-error-token')), \
             patch('sys.stderr',new_callable=io.StringIO) as stderr:
            with self.assertRaises(TimeoutError):self.call()
        rows=[json.loads(line) for line in stderr.getvalue().splitlines() if line.strip()]
        self.assertEqual(rows,[{'kind':'r2_engineering_route_phase','request_kind':'workflow_dispatch',
                                'provider_post_started':False}])
        self.assertNotIn('private-error-token',stderr.getvalue())
    def test_positively_stale_own_dispatch_keeps_original_single_retry(self):
        real=self.remote.request
        cancelled=[]
        def stale_first(path,payload=None):
            result=real(path,payload)
            if payload is not None and len(self.remote.posts)==1:
                self.remote.dispatched[0]['head_sha']='2'*40
            return result
        with patch.object(self.remote,'request',side_effect=stale_first), \
             patch.object(self.remote,'cancel',side_effect=lambda run_id:cancelled.append(run_id) or 202,create=True):
            result=self.call()
        self.assertEqual(result['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(len(self.remote.posts),2)
        self.assertEqual(len(cancelled),1)
        self.assertEqual(result['stale_run_ids'],cancelled)
        self.assertEqual(len(self.state['engineering_routing_attempts']),1)
        intent=next(iter(self.state['engineering_bound_dispatches'].values()))
        self.assertEqual(intent['attempt'],2)
        self.assertEqual(intent['request_id'],self.remote.posts[-1][1]['inputs']['request_id'])
        self.assertEqual(self.call()['status'],'ALREADY_ATTEMPTED_THIS_R2_CONTEXT')
        self.assertEqual(len(self.remote.posts),2)
    def test_scoped_bindings_restore_even_when_remote_read_fails(self):
        before=(original._choice,original._server_runs,original._server_bytes,frozen.frozen.api,engine.read_identity_at)
        with patch.object(self.remote,'api',side_effect=TimeoutError('provider unavailable')):
            with self.assertRaises(TimeoutError):self.call()
        self.assertEqual((original._choice,original._server_runs,original._server_bytes,
          frozen.frozen.api,engine.read_identity_at),before)
    def test_reader_rejects_metadata_bytes_mismatch(self):
        class Broken:
            end=float('inf')
            def api(self,path):return {'type':'file','path':frozen.SOURCE,'sha':'a'*40,
              'size':1,'encoding':'base64','content':base64.b64encode(b'x').decode()}
        with self.assertRaises(ValueError):subject._read_bytes(Path.cwd(),Broken(),{},'1'*40,frozen.SOURCE)
    def test_verified_byte_cache_evicts_old_content_before_exceeding_budget(self):
        class Small:
            end=float('inf')
            def api(self,path):
                raw=b'aa' if 'ref='+('1'*40) in path else b'bb'
                return {'type':'file','path':frozen.SOURCE,'sha':subject._blob(raw),
                  'size':2,'encoding':'base64','content':base64.b64encode(raw).decode()}
        state={}
        with patch.object(subject,'CACHE_BYTES',3,create=True):
            self.assertEqual(subject._read_bytes(Path.cwd(),Small(),state,'1'*40,frozen.SOURCE),b'aa')
            self.assertEqual(subject._read_bytes(Path.cwd(),Small(),state,'2'*40,frozen.SOURCE),b'bb')
        self.assertLessEqual(sum(map(len,state['engineering_routing_bytes'].values())),3)
    def test_readiness_redirects_only_old_r2_requests_and_restores_other_handlers(self):
        calls=[]
        def legacy(name):calls.append(name);return {'workflow':name,'status':'legacy'}
        with patch.object(old_readiness,'dispatch_once',side_effect=legacy), \
             patch.object(subject,'route_r2',return_value={'workflow':dispatch.LIVE,'status':'guarded'}):
            try:
                with readiness.dispatch_context():
                    self.assertEqual(old_readiness.dispatch_once(original.AUDIT)['workflow'],dispatch.LIVE)
                    self.assertEqual(old_readiness.dispatch_once('learned_shadow_live.yml')['status'],'legacy')
            except AttributeError:self.fail('readiness dispatch interception is missing')
        self.assertEqual(calls,['learned_shadow_live.yml'])
    def test_readiness_new_parent_requires_actual_successful_audit_steps(self):
        row={'id':8,'head_branch':'main','path':engine.ENGINEERING_AUDIT,'status':'completed',
          'conclusion':'success','event':'workflow_run','pull_requests':[],
          'repository':{'full_name':frozen.f.old.REPO},'head_repository':{'full_name':frozen.f.old.REPO}}
        job={'id':80,'run_id':8,'name':'audit','status':'completed','conclusion':'success',
          'steps':[{'name':name,'conclusion':'success'} for name in (
          'All tests and original frozen history',
          'Original frozen registration and real publication replay through engineering v1',
          'Preserve full engineering audit and failures')]}
        try:
            with readiness.dispatch_context():
                self.assertTrue(old_readiness.trusted_completion(row,[job]))
                job['steps'][0]['conclusion']='skipped'
                self.assertFalse(old_readiness.trusted_completion(row,[job]))
                self.assertFalse(old_readiness.trusted_completion(dict(row,pull_requests=[{}]),[job]))
        except AttributeError:self.fail('new audit parent admission is missing')


class FrozenCanaryProducerTests(unittest.TestCase):
    def test_real_frozen_run_scores_formal_with_existing_canary_and_never_enters_issue(self):
        import test_phase2_forward_v2 as frames
        import test_learned_shadow_v1 as quotes
        p=frozen.f.policy()
        model=frozen.f.make_model(json.loads((frozen.ROOT/frozen.f.PARENT).read_bytes()))
        old=json.loads((frozen.ROOT/frozen.f.old.PACKAGE).read_bytes())
        def event(cohort,at):
            frame=frames.Forward().frame(at)
            bm=old['models']['1']['full']
            features=frozen.f.old.causal_features(frozen.f.benchmark_frame(frame),at)
            probability=frozen.prior.predict(bm,[[features[j] for j in bm['columns']]])[0]
            result=frozen.f.issue(p,model,frame,quotes.Shadow().packet(at),at,probability,cohort)
            result.update(input_source_commit='1'*40,input_source_sha256='a'*64,
                          registration_run_id=1,source_run_id=2)
            return result
        canary=event('canary',datetime(2026,10,20,0,1,tzinfo=timezone.utc))
        formal=event('formal',datetime(2026,11,9,14,37,34,tzinfo=timezone.utc))
        events=[canary,formal]
        self.assertIs(canary['included_in_formal_statistics'],False)
        self.assertIs(formal['included_in_formal_statistics'],True)
        for at in (datetime(2026,11,9,15,50,tzinfo=timezone.utc),
                   datetime(2026,11,9,16,0,tzinfo=timezone.utc)):
            packet=quotes.Shadow().packet(at,101)
            expected=frozen.f.score(formal,packet,p)
            class Clock(datetime):
                @classmethod
                def now(cls,tz=None):return at if tz is not None else at.replace(tzinfo=None)
            for mode in ('canary','publish'):
                with self.subTest(at=at,mode=mode):
                    appended=[]
                    # Use the unchanged producer/control flow, event replay and score.
                    # Synthetic guard/provider I/O does not prove real server qualification.
                    with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main'}), \
                         patch.object(frozen,'datetime',Clock), \
                         patch.object(frozen,'guard',return_value=(p,model,old,{'registered':b'fixture'})), \
                         patch.object(frozen,'records',side_effect=lambda folder:copy.deepcopy(events) if folder=='events' else []), \
                         patch.object(frozen,'runs_server',return_value=[]), \
                         patch.object(frozen,'publication',return_value=([],{})), \
                         patch.object(frozen,'provenance'), \
                         patch.object(frozen.f,'witness',return_value={'run_id':1,'head_sha':'1'*40}), \
                         patch.object(frozen,'capture_target',return_value=packet) as target_capture, \
                         patch.object(frozen.frozen,'capture',side_effect=AssertionError('ISSUE capture entered')) as issue_capture, \
                         patch.object(frozen,'read_at',side_effect=ValueError('missing latest73 source bars')) as source_read, \
                         patch.object(frozen.subprocess,'check_output',return_value='1'*40), \
                         patch.object(frozen,'append',side_effect=lambda root,folder,key,value:appended.append((folder,key,copy.deepcopy(value)))):
                        result=frozen.run(mode)
                    self.assertEqual(result['new_scores'],[formal['event_id']])
                    self.assertEqual(result['new_events'],[])
                    target_capture.assert_called_once()
                    issue_capture.assert_not_called()
                    score=next(value for folder,key,value in appended if folder=='scores')
                    self.assertEqual(frozen.f.encode(score),frozen.f.encode(expected))
                    self.assertEqual(result['plan']['formal_issued'],1)
                    self.assertEqual(result['plan']['canary_issued'],1)
                    if mode=='canary':
                        source_read.assert_not_called()
                        self.assertNotIn('issuance',result['errors'])
                    else:
                        source_read.assert_called_once()
                        self.assertIn('missing latest73 source bars',result['errors']['issuance'])


if __name__=='__main__':unittest.main()

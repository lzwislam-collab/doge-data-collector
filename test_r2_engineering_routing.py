"""The real old router retains admission guards; provider I/O is synthetic."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import base64
import importlib
import os
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
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
        self.assertEqual(self.remote.posts[0][1]['inputs']['mode'],'publish')
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


if __name__=='__main__':unittest.main()

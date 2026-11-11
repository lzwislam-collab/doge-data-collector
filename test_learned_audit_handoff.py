"""No-network handoff tests; all parent/source clocks are synthetic."""
import copy
import importlib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import re
import hashlib
import uuid
from unittest.mock import patch
try:
    handoff = importlib.import_module('learned_audit_handoff')
except ModuleNotFoundError:
    handoff = None
REPO = 'lzwislam-collab/doge-data-collector'
HEAD = 'a' * 40
BLOB = 'b' * 40
NOW = datetime(2026, 11, 11, 10, 34, tzinfo=timezone.utc)
def parent(**changes):
    row = {'id': 7, 'run_attempt': 1, 'head_sha': HEAD, 'head_branch': 'main',
           'path': '.github/workflows/learned_shadow_v1.yml',
           'repository': {'full_name': REPO}, 'head_repository': {'full_name': REPO},
           'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
           'created_at': '2026-11-11T10:24:48Z', 'updated_at': '2026-11-11T10:33:49Z',
           'pull_requests': []}
    return dict(row, **changes)
class Provider:
    def __init__(self, rows=None):
        self.rows = list(rows or [parent()]); self.calls = []; self.posts = []
        self.main = HEAD; self.blobs = {HEAD: BLOB}; self.artifacts = [];self.queue=None
        self.marker={'id':77,'name':'learned-audit-handoff-request-7','expired':False,
          'workflow_run':{'id':7,'head_sha':HEAD,'head_branch':'main'}}
        self.audit_steps = [{'name':name,'conclusion':'success'} for name in (
          'Full regression and frozen production history',
          'Audit exact future bundle and probe raw public quote without predictions',
          'Preserve full audit and failures')]
    def api(self, path):
        self.calls.append(path)
        if path == '/actions/runs/7':
            return copy.deepcopy(self.rows.pop(0) if len(self.rows) > 1 else self.rows[0])
        if path == '/git/ref/heads/main': return {'object': {'type': 'commit', 'sha': self.main}}
        prefix = '/contents/data/hourly_research?ref='
        if path.startswith(prefix):
            return [{'name': 'ohlc.csv', 'path': 'data/hourly_research/ohlc.csv',
                     'type': 'file', 'sha': self.blobs[path[len(prefix):]], 'size': 11126790}]
        if path == '/actions/runs/7/artifacts?per_page=100':
            return {'total_count': len(self.artifacts), 'artifacts': self.artifacts}
        if path == '/actions/artifacts/77':return copy.deepcopy(self.marker)
        if path.startswith('/actions/workflows/doge_engineering_completion.yml/runs?'):
            return copy.deepcopy(self.queue or {'total_count':0,'workflow_runs':[]})
        if path == '/actions/runs/7/attempts/1/jobs?per_page=100':
            return {'total_count':1,'jobs':[{'id':9,'run_id':7,'name':'audit',
              'status':'completed','conclusion':'success','steps':self.audit_steps}]}
        raise AssertionError('unexpected provider read: ' + path)
    def request(self, path, payload):
        self.posts.append((path, payload)); return 204
class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(handoff, 'explicit learned audit handoff is missing')
        self.provider = Provider(); self.clock_value = NOW; self.sleeps = []
    def clock(self): return self.clock_value
    def sleep(self, seconds):
        self.sleeps.append(seconds); self.clock_value += timedelta(seconds=seconds)
    def verify(self, **kw):
        kw.setdefault('local_source_blob',BLOB)
        return handoff.verify_parent(7,1,HEAD,BLOB,client=self.provider,clock=self.clock,
              sleep=self.sleep,obligation=lambda at:['issue:1794391200_1'],**kw)
    def request(self, **kw):
        kw.setdefault('persist_intent',lambda row:None)
        prepared=handoff.prepare_handoff(7,1,HEAD,BLOB,client=self.provider,
          clock=self.clock,obligation=lambda at:['issue:1794391200_1'],kind=kw.get('kind','legacy'))
        if not prepared['prepared']:return prepared
        self.assertEqual(self.provider.posts,[])
        self.provider.artifacts.append(copy.deepcopy(self.provider.marker))
        return handoff.request_terminal(7,1,HEAD,BLOB,client=self.provider,clock=self.clock,
              obligation=lambda at:['issue:1794391200_1'],intent_artifact_id=77,
              expected_keys=prepared['obligation_keys'],**kw)
    def test_prepare_has_no_post_and_remote_marker_blocks_restart_before_post(self):
        try:
            result=handoff.prepare_handoff(7,1,HEAD,BLOB,client=self.provider,
              clock=self.clock,obligation=lambda at:['issue:1794391200_1'])
        except AttributeError:self.fail('prepare-before-upload phase is missing')
        self.assertTrue(result['prepared']);self.assertEqual(self.provider.posts,[])
        self.provider.artifacts.append(copy.deepcopy(self.provider.marker))
        again=handoff.prepare_handoff(7,1,HEAD,BLOB,client=self.provider,
          clock=self.clock,obligation=lambda at:['issue:1794391200_1'])
        self.assertFalse(again['prepared']);self.assertEqual(again['status'],'SKIP_PREVIOUS_HANDOFF_ATTEMPT')
    def test_wrong_or_expired_intent_artifact_cannot_authorize_post(self):
        for change in ({'expired':True},{'name':'wrong'}, {'workflow_run':{'id':8}}):
            self.provider.marker.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                handoff.request_terminal(7,1,HEAD,BLOB,client=self.provider,clock=self.clock,
                  obligation=lambda at:['issue:1794391200_1'],intent_artifact_id=77,
                  expected_keys=['issue:1794391200_1'],persist_intent=lambda row:None)
        self.assertEqual(self.provider.posts,[])
    def test_busy_terminal_is_not_replaced_by_another_pending_dispatch(self):
        self.provider.queue={'total_count':1,'workflow_runs':[{'id':9,'head_branch':'main',
          'path':'.github/workflows/doge_engineering_completion.yml','status':'pending'}]}
        self.assertEqual(self.request()['status'],'SKIP_EXISTING_TERMINAL_ACTIVE')
        self.assertEqual(self.provider.posts,[])
    def test_ambiguous_terminal_queue_fails_before_post(self):
        self.provider.queue={'total_count':1,'workflow_runs':[]}
        with self.assertRaises(ValueError):self.request()
        self.assertEqual(self.provider.posts,[])
    def test_requests_existing_terminal_with_exact_parent_and_source(self):
        self.provider.rows=[parent(status='in_progress',conclusion=None)]
        result=self.request()
        self.assertEqual(result['status'],'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')
        self.assertEqual(self.provider.posts,[('/actions/workflows/doge_engineering_completion.yml/dispatches',
          {'ref':'main','inputs':{'mode':'bridge','source_audit_run_id':'7','source_audit_attempt':'1',
           'source_audit_head':HEAD,'source_audit_blob':BLOB,'source_audit_kind':'legacy'}})])
        self.assertFalse(result['witness_or_forecast_verified'])
    def test_recorded_attempt_prevents_repeat_post_after_unknown_result(self):
        self.provider.artifacts=[{'name':'learned-audit-handoff-request-7','expired':False}]
        self.assertEqual(self.request()['status'],'SKIP_PREVIOUS_HANDOFF_ATTEMPT')
        self.assertEqual(self.provider.posts,[])
    def test_terminal_waits_for_exact_success_then_admits_readiness(self):
        self.provider.rows=[parent(status='in_progress',conclusion=None),parent()]
        result=self.verify()
        self.assertEqual(result['status'],'SOURCE_AUDIT_HANDOFF_ADMITTED')
        self.assertTrue(result['admitted']);self.assertEqual(self.sleeps,[1])
        self.assertEqual(result.get('readiness_mode'),'bridge')
        self.assertEqual(self.provider.posts,[])
    def test_failed_parent_never_admits(self):
        self.provider.rows=[parent(conclusion='failure')]
        with self.assertRaisesRegex(ValueError,'parent audit did not succeed'):self.verify()
    def test_failed_or_skipped_original_audit_step_is_not_admitted(self):
        for conclusion in ('failure','skipped'):
            self.provider.audit_steps[0]['conclusion']=conclusion
            with self.subTest(conclusion=conclusion),self.assertRaises(ValueError):self.verify()
    def test_wrong_parent_identity_never_admits(self):
        for change in ({'id':8},{'run_attempt':2},{'head_sha':'c'*40},{'head_branch':'feature'},
          {'event':'pull_request'},{'path':'.github/workflows/phase2_forward_audit_v2.yml'},
          {'repository':{'full_name':'elsewhere/repo'}},{'head_repository':{'full_name':'elsewhere/repo'}},
          {'pull_requests':[{'number':1}]}):
            with self.subTest(change=change):
                self.provider.rows=[parent(**change)]
                with self.assertRaises(ValueError):self.verify()
    def test_changed_main_source_skips(self):
        self.provider.main='c'*40;self.provider.blobs['c'*40]='d'*40
        result=self.verify()
        self.assertEqual(result['status'],'SKIP_SOURCE_CHANGED')
        self.assertEqual(result.get('readiness_mode'),'audit')
        self.assertEqual(self.provider.posts,[])
    def test_wrong_source_or_future_parent_clock_fails_closed(self):
        self.provider.blobs[HEAD]='d'*40
        with self.assertRaises(ValueError):self.verify()
        self.provider.blobs[HEAD]=BLOB;self.provider.rows=[parent(updated_at='2026-11-11T10:35:00Z')]
        with self.assertRaises(ValueError):self.verify()
    def test_parent_wait_is_bounded(self):
        self.provider.rows=[parent(status='in_progress',conclusion=None)]
        self.assertEqual(self.verify(wait_seconds=3)['status'],'SKIP_PARENT_NOT_COMPLETE')
        self.assertEqual(sum(self.sleeps),3);self.assertEqual(self.provider.posts,[])
    def test_closed_obligations_skip_request_and_verification(self):
        empty=lambda at:[]
        self.assertEqual(handoff.prepare_handoff(7,1,HEAD,BLOB,client=self.provider,clock=self.clock,
          obligation=empty)['status'],'SKIP_NO_CURRENT_OBLIGATION')
        self.assertEqual(handoff.verify_parent(7,1,HEAD,BLOB,client=self.provider,clock=self.clock,
          sleep=self.sleep,obligation=empty)['status'],'SKIP_NO_CURRENT_OBLIGATION')
        self.assertEqual(self.provider.posts,[])
    def test_ambiguous_provider_error_never_turns_into_success(self):
        def broken(path):raise TimeoutError('provider unavailable')
        self.provider.api=broken
        with self.assertRaises(TimeoutError):self.verify()
        self.assertEqual(self.provider.posts,[])
    def test_unknown_post_result_is_recorded_as_consumed_without_retry(self):
        def unknown(path,payload):
            self.provider.posts.append((path,payload));raise TimeoutError('unknown acceptance')
        self.provider.request=unknown;receipt={}
        with self.assertRaises(TimeoutError):self.request(receipt=receipt)
        self.assertTrue(receipt['request_attempted']);self.assertEqual(len(self.provider.posts),1)
    def test_durable_intent_is_saved_before_the_only_post(self):
        writes=[]
        def post(path,payload):
            self.assertEqual(len(writes),1)
            self.assertTrue(writes[0]['request_attempted'])
            self.assertEqual(writes[0]['parent_run_id'],7)
            self.provider.posts.append((path,payload));return 204
        self.provider.request=post
        try:self.request(persist_intent=lambda row:writes.append(copy.deepcopy(row)))
        except TypeError:self.fail('durable intent callback is not implemented')
        self.assertEqual(len(self.provider.posts),1)
    def test_local_intent_persistence_crossing_deadline_prevents_post(self):
        def persist(row):self.clock_value=NOW.replace(minute=45)
        prepared=handoff.prepare_handoff(7,1,HEAD,BLOB,client=self.provider,clock=self.clock,
          obligation=lambda at:['issue:1794391200_1'])
        self.provider.artifacts.append(copy.deepcopy(self.provider.marker))
        result=handoff.request_terminal(7,1,HEAD,BLOB,client=self.provider,clock=self.clock,
          intent_artifact_id=77,expected_keys=prepared['obligation_keys'],persist_intent=persist,
          obligation=lambda at:[] if at.minute>=45 else ['issue:1794391200_1'])
        self.assertEqual(result['status'],'SKIP_OBLIGATION_CHANGED')
        self.assertEqual(self.provider.posts,[])
    def test_obligation_closing_during_source_reads_never_admits(self):
        api=self.provider.api
        def advancing(path):
            value=api(path)
            if path.startswith('/contents/'):
                self.clock_value=NOW.replace(minute=45)
            return value
        self.provider.api=advancing
        status=handoff.verify_parent(7,1,HEAD,BLOB,client=self.provider,clock=self.clock,
          sleep=self.sleep,obligation=lambda at:[] if at.minute>=45 else ['issue:1794391200_1'])
        self.assertEqual(status['status'],'SKIP_NO_CURRENT_OBLIGATION')
    def test_original_plan_keeps_deadline_and_existing_event_exclusion(self):
        import learned_shadow_runtime as original
        policy=json.loads((original.ROOT/original.shadow.POLICY).read_bytes())
        self.assertEqual(handoff.obligation_keys(policy,[],{},NOW),['issue:1794391200_1'])
        self.assertEqual(handoff.obligation_keys(policy,[],{},NOW.replace(minute=45)),[])
        event={'event_id':'1794391200_1','horizon_hours':1,'slot_utc':'2026-11-11T10:00:00Z',
               'target_utc':'2026-11-11T11:34:00Z'}
        self.assertEqual(handoff.obligation_keys(policy,[event],{},NOW),[])
    def test_new_r2_parent_uses_its_own_successful_audit_steps(self):
        self.provider.rows=[parent(path='.github/workflows/r2_engineering_audit.yml',event='workflow_run')]
        self.provider.audit_steps=[{'name':name,'conclusion':'success'} for name in (
          'All tests and original frozen history',
          'Original frozen registration and real publication replay through engineering v1',
          'Preserve full engineering audit and failures')]
        try:result=self.verify(kind='r2')
        except TypeError:self.fail('new engineering audit kind is missing')
        self.assertTrue(result['admitted']);self.assertEqual(result['source_audit_kind'],'r2')
        result=self.request(kind='r2')
        self.assertEqual(result['status'],'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')
        self.assertEqual(self.provider.posts[0][1]['inputs']['source_audit_kind'],'r2')
    def test_claimed_kind_must_match_actual_parent_path(self):
        with self.assertRaises(ValueError):self.verify(kind='r2')
        with self.assertRaises(ValueError):self.verify(kind='unregistered')
    def test_old_terminal_checkout_source_cannot_enable_bridge(self):
        result=self.verify(local_source_blob='c'*40)
        self.assertEqual(result['status'],'SKIP_LOCAL_SOURCE_CHANGED')
        self.assertFalse(result['admitted']);self.assertEqual(result['readiness_mode'],'audit')
    def test_r2_obligations_keep_original_issuance_and_target_cutoffs(self):
        import phase2_forward_runtime_v2 as original
        policy=original.f.policy()
        try:keys=handoff.r2_obligation_keys(policy,[],{},NOW)
        except AttributeError:self.fail('R2 frozen obligation adapter is missing')
        self.assertEqual(keys,['issue:1794391200_formal'])
        self.assertEqual(handoff.r2_obligation_keys(policy,[],{},NOW.replace(minute=45)),[])
        event={'event_id':'1794384000_formal','cohort':'formal','slot_utc':'2026-11-11T08:00:00Z',
          'target_utc':'2026-11-11T10:10:00Z'}
        self.assertEqual(handoff.r2_obligation_keys(policy,[event],{},NOW),
          ['issue:1794391200_formal','target:1794384000_formal'])
        self.assertEqual(handoff.r2_obligation_keys(policy,[event],{},NOW.replace(minute=35)),
          ['issue:1794391200_formal'])
class CompletionTests(unittest.TestCase):
    def setUp(self): self.provider=Provider()
    def verify(self,event=None,**kwargs):
        self.assertTrue(callable(getattr(handoff,'verify_completion',None)),
                        'natural typed completion verifier is missing')
        return handoff.verify_completion(event or parent(),client=self.provider,
            clock=lambda:NOW,obligation=kwargs.pop('obligation',lambda at:['issue:1794391200_1']),
            local_source_blob=kwargs.pop('local_source_blob',BLOB),**kwargs)
    def test_natural_completion_uses_real_parent_and_has_no_post(self):
        result=self.verify()
        self.assertTrue(result['admitted']);self.assertEqual(result['readiness_mode'],'bridge')
        self.assertEqual((result['parent_run_id'],result['parent_attempt'],result['source_head']),
                         (7,1,HEAD))
        self.assertEqual(result['source_audit_kind'],'legacy')
        self.assertEqual(result['parent_wait_seconds'],0)
        self.assertEqual(self.provider.posts,[])
    def test_event_identity_kind_and_failed_whole_never_fall_back_to_bridge(self):
        for change in ({'id':8},{'run_attempt':2},{'head_sha':'c'*40},
          {'path':handoff.AUDITS['r2']},{'status':'in_progress'},{'conclusion':'failure'}):
            with self.subTest(change=change),self.assertRaises((ValueError,AssertionError)):
                self.verify(parent(**change))
        self.provider.rows=[parent(conclusion='failure')]
        with self.assertRaises(ValueError):self.verify()
        self.assertEqual(self.provider.posts,[])
    def test_source_current_local_and_expired_obligations_are_audit_only(self):
        self.provider.main='c'*40;self.provider.blobs['c'*40]='d'*40
        result=self.verify();self.assertFalse(result['admitted']);self.assertEqual(result['readiness_mode'],'audit')
        self.provider.main=HEAD
        result=self.verify(local_source_blob='d'*40)
        self.assertFalse(result['admitted']);self.assertEqual(result['readiness_mode'],'audit')
        result=self.verify(obligation=lambda at:[])
        self.assertFalse(result['admitted']);self.assertEqual(result['readiness_mode'],'audit')
        self.assertEqual(self.provider.posts,[])
    def test_r2_completion_requires_actual_r2_steps_and_kind(self):
        row=parent(path=handoff.AUDITS['r2'],event='workflow_run');self.provider.rows=[row]
        with self.assertRaises(ValueError):self.verify(row)
        self.provider.audit_steps=[{'name':name,'conclusion':'success'} for name in handoff.AUDIT_STEPS['r2']]
        result=self.verify(row);self.assertEqual(result['source_audit_kind'],'r2');self.assertTrue(result['admitted'])
        self.assertEqual(self.provider.posts,[])
    def test_completed_event_cannot_wait_for_a_replaced_attempt(self):
        self.provider.rows=[parent(status='in_progress',conclusion=None)]
        result=self.verify();self.assertFalse(result['admitted']);self.assertEqual(result['readiness_mode'],'audit')
        self.assertEqual(result['status'],'SKIP_PARENT_NOT_COMPLETE')
    def test_provider_boolean_attempt_is_not_a_real_attempt_identity(self):
        self.provider.rows=[parent(run_attempt=True)]
        with self.assertRaises(ValueError):self.verify()
    def test_cli_natural_event_ignores_dispatch_locators(self):
        import runtime_custody_bridge as transport
        root=Path(__file__).resolve().parent/'completion-test-receipts'/uuid.uuid4().hex
        (root/handoff.SOURCE).parent.mkdir(parents=True)
        raw=b'actual synthetic source bytes\n';(root/handoff.SOURCE).write_bytes(raw)
        blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        self.provider.blobs[HEAD]=blob
        event=root/'event.json';event.write_text(json.dumps({'workflow_run':parent()}),encoding='utf-8')
        output=root/'outputs'
        env={'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':REPO,'GITHUB_EVENT_NAME':'workflow_run',
          'GITHUB_EVENT_PATH':str(event),'GITHUB_OUTPUT':str(output),
          'SOURCE_AUDIT_RUN_ID':'999','SOURCE_AUDIT_ATTEMPT':'999','SOURCE_AUDIT_HEAD':'f'*40,
          'SOURCE_AUDIT_BLOB':'e'*40,'SOURCE_AUDIT_KIND':'r2'}
        with patch.dict(os.environ,env),patch.object(handoff,'ROOT',root), \
             patch.object(transport,'GitHubClient',return_value=self.provider), \
             patch.object(handoff,'current_obligation',return_value=['issue:1794391200_1']):
            handoff.main(['verify'])
        result=json.loads((root/'forward-readiness-output/learned-audit-handoff-verify.json').read_bytes())
        self.assertEqual(result['parent_run_id'],7);self.assertEqual(result['source_audit_kind'],'legacy')
        self.assertIn('readiness_mode=bridge',output.read_text());self.assertEqual(self.provider.posts,[])

class CrossFamilyCompletionTests(unittest.TestCase):
    def setUp(self):
        import learned_shadow_runtime as legacy
        import phase2_forward_runtime_v2 as r2
        self.policies = {'legacy': json.loads((legacy.ROOT/legacy.shadow.POLICY).read_bytes()),
                         'r2': r2.f.policy()}
        self.events = {'legacy': [{'event_id': '1794391200_1', 'horizon_hours': 1,
            'slot_utc': '2026-11-11T10:00:00Z', 'target_utc': '2026-11-11T11:34:00Z'}],
            'r2': [{'event_id': '1794391200_formal', 'cohort': 'formal',
                'slot_utc': '2026-11-11T10:00:00Z', 'target_utc': '2026-11-11T11:34:00Z'},
                {'event_id': '1794387600_formal', 'cohort': 'formal',
                'slot_utc': '2026-11-11T09:00:00Z', 'target_utc': '2026-11-11T10:10:00Z'}]}
        self.calls = []
        self.provider = Provider()

    def planned(self, at, kind='legacy'):
        self.calls.append((kind, at))
        planner = handoff.obligation_keys if kind == 'legacy' else handoff.r2_obligation_keys
        return planner(self.policies[kind], self.events[kind], {}, at)

    def use_parent(self, kind):
        row = parent(path=handoff.AUDITS[kind], event='workflow_run' if kind == 'r2' else 'push')
        self.provider.rows = [row]
        self.provider.audit_steps = [{'name': name, 'conclusion': 'success'}
                                     for name in handoff.AUDIT_STEPS[kind]]
        return row

    def verify(self, kind, at):
        with patch.object(handoff, 'current_obligation', side_effect=self.planned):
            return handoff.verify_completion(self.use_parent(kind), client=self.provider,
                clock=lambda: at, local_source_blob=BLOB)

    def test_legacy_completion_can_wake_only_original_r2_target(self):
        self.assertEqual(self.planned(NOW, 'legacy'), [])
        self.assertEqual(self.planned(NOW, 'r2'), ['target:1794387600_formal'])
        self.calls.clear()
        result = self.verify('legacy', NOW)
        self.assertTrue(result['admitted'])
        self.assertEqual(result['readiness_mode'], 'bridge')
        self.assertEqual(result['obligation_keys'], ['r2:target:1794387600_formal'])
        self.assertEqual(result['source_audit_kind'], 'legacy')
        self.assertIs(result['witness_or_forecast_verified'], False)
        self.assertEqual(self.calls, [('legacy', NOW), ('r2', NOW)]*2)
        self.assertEqual(self.provider.posts, [])

    def test_r2_completion_can_wake_only_original_legacy_target(self):
        at = NOW.replace(hour=11, minute=46)
        self.events['r2'] = []
        self.assertEqual(self.planned(at, 'r2'), [])
        self.assertEqual(self.planned(at, 'legacy'), ['target:1794391200_1'])
        self.calls.clear()
        result = self.verify('r2', at)
        self.assertTrue(result['admitted'])
        self.assertEqual(result['obligation_keys'], ['legacy:target:1794391200_1'])
        self.assertEqual(result['source_audit_kind'], 'r2')
        self.assertEqual(self.calls, [('legacy', at), ('r2', at)]*2)
        self.assertEqual(self.provider.posts, [])

    def test_both_empty_original_plans_remain_audit_only(self):
        at = NOW.replace(hour=11, minute=59)
        self.events['r2'] = []
        self.assertEqual(self.planned(at, 'legacy'), [])
        self.assertEqual(self.planned(at, 'r2'), [])
        result = self.verify('legacy', at)
        self.assertEqual(result['status'], 'SKIP_NO_CURRENT_OBLIGATION')
        self.assertFalse(result['admitted'])
        self.assertEqual(result['readiness_mode'], 'audit')

    def test_both_families_use_same_actual_utc_and_recheck_original_deadline(self):
        clocks = iter([NOW, NOW, NOW.replace(minute=35)])
        with patch.object(handoff, 'current_obligation', side_effect=self.planned):
            result = handoff.verify_completion(self.use_parent('legacy'), client=self.provider,
                clock=lambda: next(clocks), local_source_blob=BLOB)
        self.assertEqual(result['status'], 'SKIP_OBLIGATION_CHANGED')
        self.assertFalse(result['admitted'])
        self.assertEqual(result['readiness_mode'], 'audit')
        self.assertEqual(self.calls, [('legacy', NOW), ('r2', NOW),
            ('legacy', NOW.replace(minute=35)), ('r2', NOW.replace(minute=35))])

    def test_unknown_other_family_plan_cannot_admit_known_family_work(self):
        def unknown(at, kind='legacy'):
            if kind == 'r2': raise ValueError('unknown frozen planner state')
            return ['target:1794391200_1']
        with patch.object(handoff, 'current_obligation', side_effect=unknown):
            with self.assertRaisesRegex(ValueError, 'unknown frozen planner state'):
                handoff.verify_completion(self.use_parent('legacy'), client=self.provider,
                    clock=lambda: NOW, local_source_blob=BLOB)
        self.assertEqual(self.provider.posts, [])

    def test_explicit_prepare_post_and_verify_keep_original_parent_family(self):
        for kind, at in (('legacy', NOW), ('r2', NOW.replace(hour=11, minute=46))):
            with self.subTest(kind=kind):
                if kind == 'r2': self.events['r2'] = []
                self.use_parent(kind)
                self.calls.clear()
                with patch.object(handoff, 'current_obligation', side_effect=self.planned):
                    prepared = handoff.prepare_handoff(7, 1, HEAD, BLOB,
                        client=self.provider, clock=lambda: at, kind=kind)
                    posted = handoff.request_terminal(7, 1, HEAD, BLOB,
                        client=self.provider, clock=lambda: at, kind=kind,
                        intent_artifact_id=77, expected_keys=['unchanged-explicit-family-key'])
                    verified = handoff.verify_parent(7, 1, HEAD, BLOB, client=self.provider,
                        clock=lambda: at, kind=kind, local_source_blob=BLOB)
                for result in (prepared, posted, verified):
                    self.assertEqual(result['status'], 'SKIP_NO_CURRENT_OBLIGATION')
                    self.assertFalse(result['admitted'])
                self.assertTrue(self.calls)
                self.assertTrue(all(actual_kind == kind for actual_kind, _ in self.calls))
        self.assertEqual(self.provider.posts, [])


class CompletionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from source_recovery_test_dependencies import ensure_test_dependencies
        ensure_test_dependencies()
        import yaml
        cls.root=Path(__file__).resolve().parent
        cls.load=staticmethod(lambda name:yaml.load((cls.root/'.github/workflows'/name).read_text(encoding='utf-8'),Loader=yaml.BaseLoader))
    def expression(self,source,values):
        source=source.strip()
        if source.startswith('${{'):source=source[3:-2].strip()
        source=re.sub(r'\b(?:github|inputs|env|steps)\.[A-Za-z0-9_.]+',
                      lambda m:repr(values.get(m[0],'')),source)
        return eval(source.replace('&&',' and ').replace('||',' or '),
                    {'__builtins__':{},'startsWith':lambda a,b:a.startswith(b)},{})
    def terminal(self):return self.load('doge_engineering_completion.yml')['jobs']['terminal-receipt']
    def test_source_audits_contain_only_the_original_audit_job(self):
        for name in ('learned_shadow_v1.yml','r2_engineering_audit.yml'):
            with self.subTest(name=name):
                workflow=self.load(name);self.assertEqual(set(workflow['jobs']),{'audit'})
                steps=workflow['jobs']['audit']['steps']
                kind='r2' if name.startswith('r2_') else 'legacy'
                for required in handoff.AUDIT_STEPS[kind]:self.assertTrue(any(s.get('name')==required for s in steps))
                self.assertFalse(any('learned_audit_handoff.py' in s.get('run','') for s in steps))
        self.assertIn('Publish DOGE V2 R2 frozen forward evidence',
                      self.load('r2_engineering_audit.yml')['on']['workflow_run']['workflows'])
    def test_actual_yaml_routes_source_completions_and_preserves_other_completions(self):
        job=self.terminal();required=job.get('env',{}).get('SOURCE_AUDIT_REQUIRED')
        self.assertIsNotNone(required,'typed completion selector is missing')
        verify=next(s for s in job['steps'] if s.get('id')=='source_audit')
        readiness=next(s for s in job['steps'] if s.get('id')=='readiness')
        self.assertLess(job['steps'].index(verify),job['steps'].index(readiness))
        for kind,path in handoff.AUDITS.items():
            for actual in (path,path+'@refs/heads/main'):
                values={'github.event_name':'workflow_run','github.event.workflow_run.path':actual}
                self.assertTrue(self.expression(required,values))
        values={'github.event_name':'workflow_run','github.event.workflow_run.path':'.github/workflows/collect.yml',
                'github.ref':'refs/heads/main','env.SOURCE_AUDIT_REQUIRED':'false'}
        self.assertFalse(self.expression(required,values))
        self.assertTrue(self.expression(readiness['if'],values))
        self.assertEqual(self.expression(readiness['env']['READINESS_MODE'],values),'bridge')
    def test_typed_failure_skip_or_missing_output_cannot_fall_back_to_bridge(self):
        job=self.terminal();readiness=next(s for s in job['steps'] if s.get('id')=='readiness')
        base={'github.event_name':'workflow_run','github.ref':'refs/heads/main','env.SOURCE_AUDIT_REQUIRED':'true'}
        for outcome in ('failure','skipped','cancelled'):
            with self.subTest(outcome=outcome):
                self.assertFalse(self.expression(readiness['if'],dict(base,**{'steps.source_audit.outcome':outcome})))
        mode=readiness['env']['READINESS_MODE']
        self.assertEqual(self.expression(mode,dict(base,**{'steps.source_audit.outcome':'success'})),'audit')
        self.assertEqual(self.expression(mode,dict(base,**{'steps.source_audit.outputs.readiness_mode':'audit'})),'audit')
    def test_existing_dispatch_locator_and_completion_listeners_remain(self):
        workflow=self.load('doge_engineering_completion.yml');job=self.terminal()
        for name in ('source_audit_run_id','source_audit_attempt','source_audit_head','source_audit_blob','source_audit_kind'):
            self.assertIn(name,workflow['on']['workflow_dispatch']['inputs'])
        for name in ('learned_shadow_v1.yml','r2_engineering_audit.yml'):
            self.assertIn(self.load(name)['name'],workflow['on']['workflow_run']['workflows'])
        required=job.get('env',{}).get('SOURCE_AUDIT_REQUIRED')
        self.assertIsNotNone(required)
        self.assertTrue(self.expression(required,{'github.event_name':'workflow_dispatch','inputs.source_audit_run_id':'7'}))

if __name__=='__main__':unittest.main()

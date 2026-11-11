"""Bounded dispatch must bind real Actions heads, never HTTP204 alone."""
import copy
import learned_audit_handoff as handoff
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest
from urllib.error import HTTPError
from unittest.mock import patch
import json
import subprocess
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('dispatch_subject', HERE/'r2_engineering_dispatch.py') if (HERE/'r2_engineering_dispatch.py').exists() else None
subject = importlib.util.module_from_spec(spec) if spec else None
if spec: spec.loader.exec_module(subject)
NOW = datetime(2026,11,11,9,39,tzinfo=timezone.utc)
HEAD = 'a'*40
OLD = 'b'*40
BLOB = 'c'*40
WORKFLOW = 'r2_engineering_audit.yml'
PATH = 'data/phase2_forward_v2/scores/1794384000_formal.json'

class Client:
    def __init__(self, heads=None, *, visible=True, unknown=False, active=False, drift=False):
        self.heads = heads or [HEAD]
        self.visible, self.unknown, self.active, self.drift = visible, unknown, active, drift
        self.posts=[]; self.cancelled=[]; self.reads=[]; self.runs=[]
    def api(self,path):
        self.reads.append(path)
        if path=='/git/ref/heads/main':return {'object':{'type':'commit','sha':HEAD}}
        if path.startswith('/contents/'):
            head=path.split('ref=')[1]
            return {'type':'file','path':PATH,'sha':BLOB if head==HEAD and not self.drift else 'd'*40,'size':20}
        if '/runs?' in path:
            if 'status=' in path:
                return {'total_count':1 if self.active else 0,'workflow_runs':[self.row(99,HEAD,'busy')] if self.active else []}
            return {'total_count':len(self.runs),'workflow_runs':list(self.runs) if self.visible else []}
        raise AssertionError(path)
    def row(self,rid,head,token):
        return {'id':rid,'head_sha':head,'head_branch':'main','path':'.github/workflows/'+WORKFLOW,
                'event':'workflow_dispatch','status':'queued','display_title':'R2E1 audit '+token,
                'created_at':NOW.isoformat(),'repository':{'full_name':'lzwislam-collab/doge-data-collector'}}
    def post(self,path,payload):
        self.posts.append((path,payload))
        if self.unknown:raise TimeoutError('POST acknowledgement unknown')
        index=len(self.posts)-1
        self.runs.insert(0,self.row(100+index,self.heads[min(index,len(self.heads)-1)],payload['inputs']['request_id']))
        return 204
    def cancel(self,rid):self.cancelled.append(rid);return 202

class BoundDispatch(unittest.TestCase):
    def call(self,client,*,clock=None,state=None,deadline=None):
        if subject is None:self.fail('bounded real-head dispatch implementation is absent')
        return subject.dispatch_bound(WORKFLOW,{PATH:BLOB},deadline or NOW+timedelta(minutes=10),
            client=client,clock=clock or (lambda:NOW),sleep=lambda _:None,
            token_factory=iter(['first','retry']).__next__,state=state if state is not None else {})
    def test_timely_real_head_is_bound_but_not_claimed_successful(self):
        c=Client();out=self.call(c)
        self.assertEqual(out['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(out['head_sha'],HEAD)
        self.assertFalse(out['witness_verified'])
        self.assertEqual(len(c.posts),1)
    def test_proven_stale_head_is_cancelled_then_one_bounded_retry(self):
        c=Client([OLD,HEAD]);out=self.call(c)
        self.assertEqual(out['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(c.cancelled,[100]);self.assertEqual(len(c.posts),2)
        self.assertEqual(out['stale_run_ids'],[100])
    def test_second_stale_head_is_not_retried(self):
        c=Client([OLD,OLD]);out=self.call(c)
        self.assertEqual(out['status'],'STALE_DISPATCH_NOT_ACCEPTED')
        self.assertEqual(c.cancelled,[100,101]);self.assertEqual(len(c.posts),2)
    def test_invisible_result_is_unknown_and_never_retried(self):
        c=Client(visible=False);out=self.call(c)
        self.assertEqual(out['status'],'DISPATCH_RESULT_UNOBSERVED')
        self.assertEqual(len(c.posts),1);self.assertEqual(c.cancelled,[])
    def test_unknown_post_consumes_context_and_does_not_retry(self):
        c=Client(unknown=True);state={}
        with self.assertRaises(TimeoutError):self.call(c,state=state)
        self.assertTrue(state)
        out=self.call(c,state=state)
        self.assertEqual(out['status'],'PREVIOUS_INTENT_NOT_RETRIED')
        self.assertEqual(len(c.posts),1)
    def test_changed_public_source_prevents_post(self):
        c=Client(drift=True);out=self.call(c)
        self.assertEqual(out['status'],'DEFERRED_PUBLIC_BYTES_CHANGED');self.assertEqual(c.posts,[])
    def test_active_workflow_defers_without_post(self):
        c=Client(active=True);out=self.call(c)
        self.assertEqual(out['status'],'DEFERRED_ACTIVE_WORKFLOW');self.assertEqual(c.posts,[])
    def test_original_deadline_is_not_extended(self):
        c=Client();out=self.call(c,deadline=NOW)
        self.assertEqual(out['status'],'DEFERRED_WINDOW_CLOSED');self.assertEqual(c.posts,[])
    def test_window_closing_during_provider_reads_prevents_post(self):
        c=Client();times=iter([NOW,NOW+timedelta(minutes=11)])
        out=self.call(c,clock=lambda:next(times))
        self.assertEqual(out['status'],'DEFERRED_WINDOW_CLOSED');self.assertEqual(c.posts,[])
    def test_malformed_expected_identity_is_rejected(self):
        if subject is None:self.fail('bounded real-head dispatch implementation is absent')
        with self.assertRaises(ValueError):subject.dispatch_bound(WORKFLOW,{'../unsafe':'c'*40},NOW+timedelta(minutes=10),client=Client(),clock=lambda:NOW)
    def test_known_absent_object_is_stale_but_unknown404_propagates(self):
        if subject is None:self.fail('bounded real-head dispatch implementation is absent')
        class Missing(Client):
            def api(self,path):
                if path.startswith('/contents/') and ('ref='+OLD) in path:raise HTTPError(path,404,'Not Found',{},None)
                return super().api(path)
        c=Missing([OLD,HEAD]);out=subject.dispatch_bound(WORKFLOW,{PATH:BLOB},NOW+timedelta(minutes=10),
            client=c,clock=lambda:NOW,sleep=lambda _:None,token_factory=iter(['first','retry']).__next__,absence_proof=lambda h,p:h==OLD and p==PATH)
        self.assertEqual(out['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED');self.assertEqual(c.cancelled,[100])
        c=Missing([OLD,HEAD])
        with self.assertRaises(HTTPError):subject.dispatch_bound(WORKFLOW,{PATH:BLOB},NOW+timedelta(minutes=10),
            client=c,clock=lambda:NOW,sleep=lambda _:None,token_factory=lambda:'first',absence_proof=lambda *_:False)
        self.assertEqual(len(c.posts),1);self.assertEqual(c.cancelled,[])
    def test_consumed_intent_is_persisted_before_unknown_post(self):
        if subject is None:self.fail('bounded real-head dispatch implementation is absent')
        persisted=[]
        class Unknown(Client):
            def post(self,path,payload):
                if not persisted:raise AssertionError('POST preceded durable intent')
                return super().post(path,payload)
        c=Unknown(unknown=True)
        with self.assertRaises(TimeoutError):subject.dispatch_bound(WORKFLOW,{PATH:BLOB},NOW+timedelta(minutes=10),
            client=c,clock=lambda:NOW,persist_intent=lambda state:persisted.append(dict(state)))
        self.assertEqual(len(persisted),1);self.assertEqual(len(c.posts),1)
    def test_persistence_cannot_extend_original_window(self):
        at=[NOW];c=Client()
        out=subject.dispatch_bound(WORKFLOW,{PATH:BLOB},NOW+timedelta(minutes=10),
            client=c,clock=lambda:at[0],persist_intent=lambda _:at.__setitem__(0,NOW+timedelta(minutes=11)))
        self.assertEqual(out['status'],'DEFERRED_WINDOW_CLOSED');self.assertEqual(c.posts,[])
    def test_http_child_preserves_404_and_is_bounded(self):
        with patch.object(subject.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'{"http_error":404,"url":"https://api.github.com/not-found"}')) as run:
            with self.assertRaises(HTTPError):subject.Client().api('/contents/missing')
            self.assertLessEqual(run.call_args.kwargs['timeout'],40)
        with patch.object(subject.subprocess,'run',side_effect=subprocess.TimeoutExpired('http-only-child',1)):
            with self.assertRaises(TimeoutError):subject.Client().api('/actions/runs')
    def test_remote_intent_identity_and_expiration(self):
        self.assertTrue(callable(getattr(subject,'verify_remote_intent',None)),'remote pre-POST intent verifier is absent')
        intent={'schema_version':1,'parent_run_id':12,'parent_attempt':1,'artifact_name':'r2-engineering-audit-intent-12',
                'prepared_at_utc':NOW.isoformat(),'expected_blobs':{PATH:BLOB},'not_after_utc':(NOW+timedelta(minutes=10)).isoformat()}
        class Marker(Client):
            def __init__(self,expired=False,owner=12):super().__init__();self.expired=expired;self.owner=owner
            def api(self,path):
                if path=='/actions/artifacts/44':return {'id':44,'name':intent['artifact_name'],'expired':self.expired,
                    'size_in_bytes':222,'created_at':NOW.isoformat(),'workflow_run':{'id':self.owner,'head_branch':'main'}}
                return super().api(path)
        subject.verify_remote_intent(intent,44,client=Marker(),clock=lambda:NOW)
        for c in (Marker(expired=True),Marker(owner=99)):
            with self.assertRaises(ValueError):subject.verify_remote_intent(intent,44,client=c,clock=lambda:NOW)
    def test_original_publisher_in_shared_queue_prevents_new_dispatch(self):
        class SharedQueue(Client):
            def api(self,path):
                if '/phase2_forward_live_v2.yml/runs?' in path and 'status=queued' in path:
                    return {'total_count':1,'workflow_runs':[{'id':77,'head_branch':'main','path':'.github/workflows/phase2_forward_live_v2.yml','status':'queued'}]}
                return super().api(path)
        c=SharedQueue();out=subject.dispatch_bound(subject.LIVE,{PATH:BLOB},NOW+timedelta(minutes=10),
            client=c,clock=lambda:NOW,mode='canary')
        self.assertEqual(out['status'],'DEFERRED_ACTIVE_WORKFLOW');self.assertEqual(c.posts,[])
    def test_same_data_on_pre_release_head_cannot_bind_wrong_engineering_version(self):
        manifest='config/r2_engineering_transport_v1.json';version='f'*40
        class Versioned(Client):
            def api(self,path):
                if path.startswith('/contents/'+manifest+'?'):
                    if ('ref='+OLD) in path:raise HTTPError(path,404,'Not Found',{},None)
                    return {'type':'file','path':manifest,'sha':version,'size':700}
                if path.startswith('/contents/'):
                    return {'type':'file','path':PATH,'sha':BLOB,'size':20}
                return super().api(path)
        c=Versioned([OLD,HEAD]);out=subject.dispatch_bound(WORKFLOW,{PATH:BLOB,manifest:version},NOW+timedelta(minutes=10),
            client=c,clock=lambda:NOW,sleep=lambda _:None,token_factory=iter(['first','retry']).__next__,
            absence_proof=lambda h,p:h==OLD and p==manifest)
        self.assertEqual(out['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(c.cancelled,[100]);self.assertEqual(out['head_sha'],HEAD)
    def test_committed_manifest_is_pinned_to_local_execution_head(self):
        self.assertTrue(callable(getattr(subject,'committed_manifest_blob',None)),'committed version binding is absent')
        raw=(HERE/'config/r2_engineering_transport_v1.json').read_bytes().replace(b'\r\n',b'\n')
        def git(root,*args):
            if args==('rev-parse','HEAD'):return (HEAD+'\n').encode()
            self.assertEqual(args,('show',HEAD+':config/r2_engineering_transport_v1.json'));return raw
        with patch.object(subject,'_git',side_effect=git):
            self.assertEqual(subject.committed_manifest_blob(HERE),subject.hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest())
        with patch.object(subject,'_git',side_effect=[(HEAD+'\n').encode(),b'{}']):
            with self.assertRaises(ValueError):subject.committed_manifest_blob(HERE)
    def test_current_history_blob_above_four_megabytes_uses_original_wire_bound(self):
        # GitHub base64-encodes the historical CSV. This is still below the
        # existing24MiB transport cap, not an increase in the scientific bound.
        payload={'content':'A'*(6*1024*1024),'encoding':'base64'}
        wire=json.dumps({'value':payload}).encode()
        with patch.object(subject.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=wire)):
            self.assertEqual(subject.Client().api('/git/blobs/'+BLOB),payload)

TERMINAL = 'doge_engineering_completion.yml'

class QueueReadTests(unittest.TestCase):
    def reader(self, responses):
        calls=[]
        def read(path):
            calls.append(path)
            value=responses.pop(0) if responses else {'total_count':0,'workflow_runs':[]}
            if isinstance(value,Exception):raise value
            return copy.deepcopy(value)
        return read,calls

    def active(self, module, read):
        if module is handoff:return handoff._active_terminal(read)
        return subject._active(type('C',(),{'api':staticmethod(read)})(),subject.AUDIT)

    def row(self,module):
        return {'id':9,'head_branch':'main','path':'.github/workflows/'+
                (TERMINAL if module is handoff else subject.AUDIT),'status':'pending'}

    def test_count_without_rows_is_read_once_more_before_empty_is_accepted(self):
        for module in (handoff,subject):
            with self.subTest(module=module.__name__):
                read,calls=self.reader([{'total_count':1,'workflow_runs':[]},
                                        {'total_count':0,'workflow_runs':[]}])
                try:result=self.active(module,read)
                except ValueError as exc:self.fail('legal inconsistent queue was not rechecked: '+str(exc))
                self.assertIsNone(result)
                self.assertEqual(len(calls),6)
                self.assertEqual(calls[0],calls[1])

    def test_consistent_active_second_read_prevents_dispatch(self):
        for module in (handoff,subject):
            with self.subTest(module=module.__name__):
                read,calls=self.reader([{'total_count':1,'workflow_runs':[]},
                        {'total_count':1,'workflow_runs':[self.row(module)]}])
                try:result=self.active(module,read)
                except ValueError as exc:self.fail('legal inconsistent queue was not rechecked: '+str(exc))
                self.assertEqual(result if module is handoff else result['id'],9)
                self.assertEqual(len(calls),2)
                self.assertEqual(calls[0],calls[1])

    def test_persistent_inconsistency_still_fails_after_exactly_two_reads(self):
        for module in (handoff,subject):
            with self.subTest(module=module.__name__):
                read,calls=self.reader([{'total_count':1,'workflow_runs':[]},
                                        {'total_count':1,'workflow_runs':[]}])
                with self.assertRaisesRegex(ValueError,'queue.*count=1.*rows=0'):
                    self.active(module,read)
                self.assertEqual(len(calls),2)

    def test_invalid_schema_or_row_identity_never_receives_recovery_read(self):
        for module in (handoff,subject):
            for value in ({'total_count':True,'workflow_runs':[]},
                          {'total_count':-1,'workflow_runs':[]},
                          {'total_count':0,'workflow_runs':[dict(self.row(module),head_branch='wrong')]},
                          {'total_count':0,'workflow_runs':[dict(self.row(module),id=True)]},
                          {'total_count':0,'workflow_runs':[dict(self.row(module),status='garbage')]}):
                with self.subTest(module=module.__name__,value=value):
                    read,calls=self.reader([value])
                    with self.assertRaises(ValueError):self.active(module,read)
                    self.assertEqual(len(calls),1)

    def test_http_error_and_original_deadline_exhaustion_propagate_without_retry(self):
        for module in (handoff,subject):
            for error in (HTTPError('public-queue',403,'Forbidden',{},None),
                          TimeoutError('original40s exhausted')):
                with self.subTest(module=module.__name__,error=type(error).__name__):
                    read,calls=self.reader([error])
                    with self.assertRaises(type(error)):self.active(module,read)
                    self.assertEqual(len(calls),1)

    def test_second_read_error_never_becomes_empty(self):
        for module in (handoff,subject):
            with self.subTest(module=module.__name__):
                read,calls=self.reader([{'total_count':1,'workflow_runs':[]},TimeoutError('same budget')])
                try:self.active(module,read)
                except TimeoutError:pass
                except ValueError as exc:self.fail('second read was not reached: '+str(exc))
                else:self.fail('second read error became empty')
                self.assertEqual(len(calls),2)


if __name__=='__main__':unittest.main()

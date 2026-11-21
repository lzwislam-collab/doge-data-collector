"""Readonly audit census/admission; no Git, network, model or production calls."""
import base64
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import io
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from contextlib import redirect_stderr
from urllib.error import HTTPError
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
import r2_engineering_dispatch as subject

NOW=datetime(2026,11,11,22,20,tzinfo=timezone.utc)
HEAD,OLD,STALE='a'*40,'b'*40,'c'*40
SOURCE='data/hourly_research/ohlc.csv'
SAME,DIFFERENT='d'*40,'e'*40
WORKFLOW='.github/workflows/'+subject.AUDIT
WF_BYTES=(Path(__file__).resolve().parent/WORKFLOW).read_bytes()


def row(rid=99,head=OLD,**changes):
    return dict(id=rid,head_sha=head,run_attempt=1,head_branch='main',path=WORKFLOW,
        status='queued',event='push',conclusion=None,pull_requests=[],
        repository={'full_name':subject.REPO},head_repository={'full_name':subject.REPO},
        created_at=NOW.isoformat(),updated_at=NOW.isoformat(),**changes)


class Client:
    def __init__(self):
        self.queued=[row()];self.other={};self.fresh={};self.responses=[]
        self.reads=[];self.posts=[];self.cancelled=[];self.observed=[]
        self.source={HEAD:SAME,OLD:DIFFERENT,STALE:DIFFERENT}
        self.workflow=WF_BYTES;self.workflow_change={};self.source_change={}
        self.jobs={'total_count':0,'jobs':[]};self.unknown=False
        self.post_heads=[HEAD];self.run_reads=0;self.error=None
        self.end=40.;self.monotonic=0.;self.advance=None
    def check(self):
        if self.monotonic>=self.end:raise TimeoutError('same original40s total budget')
    def api(self,path):
        self.check();self.reads.append(path)
        if self.error is not None:raise self.error
        parts=urlsplit(path);query=parse_qs(parts.query)
        if parts.path=='/git/ref/heads/main':value={'object':{'type':'commit','sha':HEAD}}
        elif parts.path.startswith('/actions/workflows/'):
            if 'status' not in query:value={'workflow_runs':list(self.observed),'total_count':len(self.observed)}
            else:
                status=query['status'][0]
                if status=='queued' and self.responses:value=self.responses.pop(0)
                else:
                    rows=self.queued if status=='queued' else self.other.get(status,[])
                    value={'total_count':len(rows),'workflow_runs':copy.deepcopy(rows)}
        elif parts.path.startswith('/actions/runs/') and parts.path.endswith('/jobs'):
            value=copy.deepcopy(self.jobs)
        elif parts.path.startswith('/actions/runs/'):
            self.run_reads+=1;rid=int(parts.path.rsplit('/',1)[1])
            value=copy.deepcopy(self.fresh.get(rid,next(r for r in self.queued if r['id']==rid)))
        elif parts.path.startswith('/contents/'):
            name=parts.path[len('/contents/'):];head=query['ref'][0]
            if name==SOURCE:
                value={'type':'file','path':SOURCE,'sha':self.source.get(head,DIFFERENT),'size':11000000,
                       **self.source_change}
            elif name==WORKFLOW:
                value={'type':'file','path':WORKFLOW,'size':len(self.workflow),
                    'sha':hashlib.sha1(b'blob '+str(len(self.workflow)).encode()+b'\0'+self.workflow).hexdigest(),
                    'encoding':'base64','content':base64.b64encode(self.workflow).decode(),**self.workflow_change}
            else:value={'type':'file','path':name,'sha':'f'*40,'size':500}
        else:raise AssertionError(path)
        if self.advance:self.advance(path,self)
        self.check();return value
    def post(self,path,payload):
        self.check();self.posts.append((path,payload))
        if self.unknown:raise TimeoutError('unknown POST result')
        index=len(self.posts)-1;head=self.post_heads[min(index,len(self.post_heads)-1)]
        actual=row(1000+index,head)
        actual.update(event='workflow_dispatch',display_title='R2E1 audit '+payload['inputs']['request_id'])
        self.observed.insert(0,actual);return 204
    def cancel(self,rid):self.check();self.cancelled.append(rid);return 202


class AuditSourceAdmissionTests(unittest.TestCase):
    def call(self,client,*,state=None,expected=None,workflow=None,persist=None,mode=None):
        with redirect_stderr(io.StringIO()):
            return subject.dispatch_bound(workflow or subject.AUDIT,
                expected if expected is not None else {SOURCE:SAME,subject.ENGINEERING_MANIFEST:'f'*40},
                NOW+timedelta(minutes=10),client=client,clock=lambda:NOW,sleep=lambda _:None,
                token_factory=iter(['first','retry']).__next__,state={} if state is None else state,
                persist_intent=persist,mode=mode)

    def test_old_queued_push_no_jobs_known_workflow_different_source_admits_one_post(self):
        client=Client();state={};out=self.call(client,state=state)
        self.assertEqual(out['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(len(client.posts),1);self.assertEqual(client.cancelled,[])
        self.assertFalse(out['witness_verified']);self.assertTrue(state)
        queued=[p for p in client.reads if 'status=queued' in p]
        self.assertEqual(len(queued),1);self.assertIn('per_page=100',queued[0])
        self.assertGreaterEqual(client.run_reads,2)
        self.assertTrue(any('/contents/'+WORKFLOW in p for p in client.reads))
        self.assertTrue(any('/jobs?' in p and 'filter=all' in p for p in client.reads))

    def test_hidden_matching_source_behind_irrelevant_queue_row_still_defers(self):
        client=Client();client.queued.append(row(100,HEAD))
        out=self.call(client)
        self.assertEqual(out['status'],'DEFERRED_ACTIVE_WORKFLOW')
        self.assertEqual(out['active_run_id'],100)
        self.assertEqual(client.posts,[])

    def test_same_source_never_ignores_even_when_manifest_is_different(self):
        client=Client();client.source[OLD]=SAME
        out=self.call(client)
        self.assertEqual(out['status'],'DEFERRED_ACTIVE_WORKFLOW')
        self.assertEqual(client.posts,[])
        self.assertFalse(any('/contents/'+subject.ENGINEERING_MANIFEST+'?ref='+OLD in p for p in client.reads))

    def test_other_active_states_still_block_without_irrelevance_proof(self):
        for status in ('pending','requested','waiting','in_progress'):
            with self.subTest(status=status):
                client=Client();client.queued=[];active=row();active['status']=status;client.other[status]=[active]
                self.assertEqual(self.call(client)['status'],'DEFERRED_ACTIVE_WORKFLOW')
                self.assertFalse(client.posts)
                self.assertFalse(any('/contents/'+WORKFLOW in p for p in client.reads))

    def test_other_events_unknown_identity_and_completed_race_remain_blocking(self):
        changes=[{'event':'workflow_dispatch'},{'event':'workflow_run'},{'run_attempt':0},
            {'head_sha':'unknown'},{'pull_requests':[{'number':1}]},
            {'head_repository':{'full_name':'other/repo'}},{'repository':{'full_name':'other/repo'}},
            {'status':'completed'}]
        for delta in changes:
            with self.subTest(delta=delta):
                client=Client();client.queued[0].update(delta)
                self.assertEqual(self.call(client)['status'],'DEFERRED_ACTIVE_WORKFLOW')
                self.assertFalse(client.posts)

    def test_complete_census_rejects_truncation_count_mismatch_and_duplicate_ids(self):
        values=[{'total_count':101,'workflow_runs':[row(n) for n in range(1,101)]},
                {'total_count':2,'workflow_runs':[row()]},
                {'total_count':2,'workflow_runs':[row(),row()]},
                {'total_count':True,'workflow_runs':[]},
                {'total_count':1,'workflow_runs':'bad'}]
        for value in values:
            with self.subTest(value=str(value)[:100]):
                client=Client();client.responses=[value];state={};persist=[]
                with self.assertRaises(ValueError):self.call(client,state=state,persist=lambda s:persist.append(s))
                self.assertEqual(client.posts,[]);self.assertEqual(state,{});self.assertFalse(persist)
                self.assertEqual(sum('status=queued' in p for p in client.reads),1)

    def test_legal_queue_boolean_contradiction_gets_only_one_same_url_reread(self):
        client=Client();client.responses=[{'total_count':1,'workflow_runs':[]},
            {'total_count':1,'workflow_runs':[row()]}]
        self.assertEqual(self.call(client)['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        paths=[p for p in client.reads if 'status=queued' in p]
        self.assertEqual(len(paths),2);self.assertEqual(paths[0],paths[1])

    def test_persistent_boolean_contradiction_never_retries_a_third_time(self):
        client=Client();client.responses=[{'total_count':1,'workflow_runs':[]}]*3
        with self.assertRaises(ValueError):self.call(client)
        self.assertEqual(sum('status=queued' in p for p in client.reads),2)
        self.assertEqual(client.posts,[])

    def test_bad_queue_identity_cannot_authorize_boolean_reread(self):
        for changes in ({'id':True},{'head_branch':'feature'},{'path':None},{'status':'unknown'}):
            with self.subTest(changes=changes):
                client=Client();bad=row();bad.update(changes)
                client.responses=[{'total_count':0,'workflow_runs':[bad]}]
                with self.assertRaises(ValueError):self.call(client)
                self.assertEqual(sum('status=queued' in p for p in client.reads),1)
                self.assertFalse(client.posts)

    def test_fresh_run_head_attempt_or_state_change_fails_before_any_marker(self):
        for changes in ({'head_sha':HEAD},{'run_attempt':2},{'status':'in_progress'},
                        {'id':100},{'event':'workflow_dispatch'}):
            with self.subTest(changes=changes):
                client=Client();fresh=row();fresh.update(changes);client.fresh[99]=fresh;state={}
                with self.assertRaises(ValueError):self.call(client,state=state)
                self.assertFalse(client.posts);self.assertEqual(state,{})

    def test_run_change_during_proof_is_rechecked_and_fails(self):
        client=Client();state={}
        def change(path,client):
            if '/jobs?' in path:client.fresh[99]={**row(),'head_sha':HEAD}
        client.advance=change
        with self.assertRaises(ValueError):self.call(client,state=state)
        self.assertGreaterEqual(client.run_reads,2)
        self.assertFalse(client.posts);self.assertEqual(state,{})

    def test_first_proven_row_changed_during_later_proof_is_rechecked_before_admission(self):
        client=Client();client.queued.append(row(100,'9'*40));state={}
        def change(path,client):
            if '/actions/runs/100/jobs?' in path:client.fresh[99]={**row(),'head_sha':HEAD}
        client.advance=change
        with self.assertRaises(ValueError):self.call(client,state=state)
        self.assertFalse(client.posts);self.assertEqual(state,{})

    def test_malformed_or_nonutc_run_times_remain_unknown_and_blocking(self):
        for delta in ({'created_at':'not-time'},{'updated_at':'2026-11-11T22:20:00'},
                      {'updated_at':'2026-11-11T21:20:00+00:00'}):
            with self.subTest(delta=delta):
                client=Client();client.queued[0].update(delta)
                self.assertEqual(self.call(client)['status'],'DEFERRED_ACTIVE_WORKFLOW')
                self.assertFalse(client.posts)

    def test_unknown_workflow_bytes_are_blocking_not_approved_by_metadata_name(self):
        client=Client();client.workflow+=b'\n# unreviewed workflow\n'
        self.assertEqual(self.call(client)['status'],'DEFERRED_ACTIVE_WORKFLOW')
        self.assertFalse(client.posts)

    def test_workflow_metadata_encoding_blob_and_content_errors_fail_closed(self):
        values=[{'type':'dir'},{'path':'other'},{'size':True},{'size':20000},
                {'sha':'0'*40},{'encoding':'none'},{'content':'not base64!'},
                {'content':base64.b64encode(b'changed').decode()}]
        for delta in values:
            with self.subTest(delta=delta):
                client=Client();client.workflow_change=delta
                with self.assertRaises((ValueError,TypeError)):self.call(client)
                self.assertFalse(client.posts)

    def test_source_metadata_must_be_strict_before_proving_irrelevance(self):
        for delta in ({'type':'dir'},{'path':'other'},{'sha':'not-oid'},{'size':True},{'size':-1}):
            with self.subTest(delta=delta):
                client=Client()
                def only_old(path,cl):pass
                original=client.api
                def api(path):
                    value=original(path)
                    return {**value,**delta} if '/contents/'+SOURCE+'?ref='+OLD in path else value
                client.api=api
                with self.assertRaises(ValueError):self.call(client)
                self.assertFalse(client.posts)

    def test_any_real_job_blocks_and_malformed_zero_job_view_fails(self):
        client=Client();client.jobs={'total_count':1,'jobs':[{'id':5}]}
        self.assertEqual(self.call(client)['status'],'DEFERRED_ACTIVE_WORKFLOW')
        self.assertFalse(client.posts)
        for jobs in ({'total_count':True,'jobs':[]},{'total_count':0,'jobs':[{'id':5}]},
                     {'total_count':1,'jobs':[]},{'total_count':0,'jobs':'bad'}):
            with self.subTest(jobs=jobs):
                client=Client();client.jobs=jobs
                with self.assertRaises(ValueError):self.call(client)
                self.assertFalse(client.posts)

    def test_provider_failure_has_no_retry_or_intent(self):
        for failure in (HTTPError('https://example.invalid',403,'quota',{},None),TimeoutError('unknown'),RuntimeError('unknown')):
            client=Client();state={};original=client.api
            def api(path):
                if 'status=queued' in path:raise failure
                return original(path)
            client.api=api
            with self.assertRaises(type(failure)) as error:self.call(client,state=state)
            self.assertIs(error.exception,failure);self.assertEqual(state,{});self.assertFalse(client.posts)

    def test_shared_budget_expires_during_irrelevance_proof_without_marker_or_post(self):
        client=Client();state={};original_end=client.end;persist=[]
        def spend(path,client):
            if '/contents/'+WORKFLOW in path:client.monotonic=40.
        client.advance=spend
        with self.assertRaises(TimeoutError):self.call(client,state=state,persist=lambda s:persist.append(s))
        self.assertEqual(client.end,original_end);self.assertFalse(client.posts)
        self.assertEqual(state,{});self.assertFalse(persist)

    def test_actual_owned_http_client_retains_its_same40_end_for_all_proof_reads(self):
        fake=Client();now=[0.];state={};persist=[];paths=[]
        def child(arguments,**kwargs):
            path=arguments[5];paths.append(path)
            value=fake.api(path)
            self.assertLessEqual(kwargs['timeout'],40)
            if '/contents/'+WORKFLOW in path:now[0]=40.
            return SimpleNamespace(returncode=0,stdout=json.dumps({'value':value}).encode())
        with patch.object(subject.time,'monotonic',side_effect=lambda:now[0]), \
                patch.object(subject.subprocess,'run',side_effect=child):
            client=subject.Client();original_end=client.end
            with self.assertRaises(TimeoutError):self.call(client,state=state,persist=lambda s:persist.append(s))
        self.assertEqual(original_end,40);self.assertEqual(client.end,original_end)
        self.assertTrue(any('/contents/'+WORKFLOW in p for p in paths))
        self.assertFalse(any(p.endswith('/dispatches') for p in paths))
        self.assertEqual(state,{});self.assertFalse(persist)

    def test_unknown_post_is_consumed_even_after_ignoring_irrelevant_queued_audit(self):
        client=Client();client.unknown=True;state={}
        with self.assertRaises(TimeoutError):self.call(client,state=state)
        self.assertTrue(state)
        self.assertEqual(self.call(client,state=state)['status'],'PREVIOUS_INTENT_NOT_RETRIED')
        self.assertEqual(len(client.posts),1);self.assertFalse(client.cancelled)

    def test_positive_own_stale_head_still_cancelled_once_and_bounded_retry_preserved(self):
        client=Client();client.post_heads=[STALE,HEAD]
        out=self.call(client)
        self.assertEqual(out['status'],'DISPATCH_BOUND_COMPLETION_UNVERIFIED')
        self.assertEqual(client.cancelled,[1000]);self.assertEqual(len(client.posts),2)
        self.assertEqual(out['stale_run_ids'],[1000])

    def test_live_and_no_source_paths_retain_original_serial_guard(self):
        client=Client()
        self.assertEqual(self.call(client,expected={subject.ENGINEERING_MANIFEST:'f'*40})['status'],'DEFERRED_ACTIVE_WORKFLOW')
        self.assertFalse(client.posts)
        self.assertFalse(any('per_page=100' in p for p in client.reads))
        client=Client();client.queued[0]['path']='.github/workflows/'+subject.LIVE
        self.assertEqual(self.call(client,workflow=subject.LIVE,mode='canary')['status'],'DEFERRED_ACTIVE_WORKFLOW')
        self.assertFalse(client.posts)
        self.assertFalse(any('per_page=100' in p for p in client.reads))


if __name__=='__main__':unittest.main()

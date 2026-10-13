"""Closure regressions: actual transport boundaries, no model changes."""
import os, socket, subprocess, unittest, urllib.error
from pathlib import Path
from unittest.mock import patch
import forward_validation as api
import quote_capture_retry as retry
import test_publish_forward_evidence as publication_tests

class WorkflowClosureTests(unittest.TestCase):
    def test_independent_completion_audit_covers_timeout_without_new_schedule(self):
        path=Path(__file__).parent/'.github/workflows/doge_engineering_completion.yml'
        self.assertTrue(path.exists(),'no independent collector completion receipt')
        text=path.read_text(encoding='utf-8')
        self.assertIn('workflow_run:',text);self.assertIn('types: [completed]',text)
        self.assertNotIn('cron:',text);self.assertIn('contents: read',text)
        self.assertIn('engineering_run_receipt.py',text)

class CompletionReceiptTests(unittest.TestCase):
    def test_success_metadata_never_claims_content_or_engineering_acceptance(self):
        import engineering_run_receipt as receipts
        run={'id':7,'run_attempt':2,'conclusion':'success','repository':{'full_name':receipts.REPO}}
        def request(path):
            if '/artifacts' in path:return {'artifacts':[{'id':i,'name':name,'expired':False} for i,name in enumerate(('doge-forward-evidence-7-2','doge-plus-evidence-v1-0-7-2'))]}
            if '/jobs' in path:return {'jobs':[{'id':8,'conclusion':'success','steps':[{'name':'Persist data and diagnostics','conclusion':'success'}]}]}
            return run
        out=receipts.audit(run,request)
        self.assertEqual(out['gaps'],[])
        self.assertFalse(out['engineering_accepted']);self.assertFalse(out['artifact_content_readback_verified'])
        self.assertFalse(out['independent_retention_verified'])

    def test_changed_attempt_and_wrong_repository_fail_closed(self):
        import engineering_run_receipt as receipts
        event={'id':7,'run_attempt':1,'repository':{'full_name':receipts.REPO}}
        with self.assertRaisesRegex(ValueError,'attempt changed'):
            receipts.audit(event,lambda _:dict(event,run_attempt=2))
        with self.assertRaisesRegex(ValueError,'unexpected repository'):
            receipts.audit(dict(event,repository={'full_name':'other/repo'}),lambda _:event)

    def test_pagination_does_not_drop_late_artifacts(self):
        import engineering_run_receipt as receipts
        from urllib.parse import parse_qs,urlsplit
        def request(path):return {'artifacts':list(range(100)) if parse_qs(urlsplit(path).query)['page']==['1'] else [100]}
        self.assertEqual(len(receipts.pages(request,'/artifacts','artifacts')),101)

    def test_timeout_missing_artifacts_reports_loss_without_fake_recovery(self):
        import engineering_run_receipt as receipts
        run={'id':7,'run_attempt':2,'conclusion':'timed_out','head_sha':'a'*40,
             'event':'schedule','created_at':'2026-10-13T06:00:00Z','updated_at':'2026-10-13T06:08:00Z',
             'repository':{'full_name':'lzwislam-collab/doge-data-collector'}}
        def request(path):
            if '/artifacts' in path:return {'artifacts':[]}
            if '/jobs' in path:return {'jobs':[{'id':8,'conclusion':'timed_out','steps':[{'name':'Persist data and diagnostics','conclusion':'skipped'}]}]}
            return run
        out=receipts.audit(run,request)
        self.assertFalse(out['engineering_accepted']);self.assertFalse(out['unpersisted_evidence_recovered'])
        self.assertEqual(out['status'],'INCOMPLETE_EXECUTION')
        self.assertIn('forecast_artifact_missing',out['gaps'])
        self.assertIn('persist_not_verified',out['gaps'])

    def test_minimal_audit_has_authenticated_github_access(self):
        text=(Path(__file__).parent/'.github/workflows/okx_hourly_extreme_history.yml').read_text(encoding='utf-8')
        block=text.split('id: minimal_audit',1)[1].split('      - name:',1)[0]
        self.assertIn('GITHUB_TOKEN: ${{ github.token }}',block)
        self.assertEqual(text.count('cron:'),1)

    def test_github_client_transmits_token_without_putting_it_in_url(self):
        import io
        seen=[]
        def open_request(request,timeout):
            seen.append(request)
            return io.BytesIO(b'{"id": 123}')
        with patch.dict(os.environ,{'GITHUB_TOKEN':'test-only-token'}),patch.object(api.urllib.request,'urlopen',side_effect=open_request):
            self.assertEqual(api.github_json('/actions/runs/123'),{'id':123})
        self.assertEqual(seen[0].get_header('Authorization'),'Bearer test-only-token')
        self.assertNotIn('test-only-token',seen[0].full_url)

class PublicationFaultTests(publication_tests.PublishTests):
    def test_push_then_readback_failure_retry_still_requests_witness(self):
        import publication_recovery as recovery
        import publish_forward_evidence as publisher
        self.seed()
        with patch.object(publisher,'verify_readback',return_value=False):
            with self.assertRaises(ValueError):recovery.publish(self.root,publisher.publish)
        out=recovery.publish(self.root,publisher.publish)
        self.assertFalse(out['new_predictions'])
        workflow=(Path(__file__).parent/'.github/workflows/okx_hourly_extreme_history.yml').read_text(encoding='utf-8')
        block=workflow.split('id: public_witness',1)[1].split('      - name:',1)[0]
        self.assertIn("if: steps.early_publish.outcome == 'success'",block)

    def test_recovery_recognizes_remote_unpublished_forecast_after_local_commit(self):
        import publication_recovery as recovery
        import publish_forward_evidence as publisher
        self.seed();real=publisher.command
        def fail_push(root,*args):
            if args[0]=='push':raise subprocess.CalledProcessError(1,['git',*args])
            return real(root,*args)
        with patch.object(publisher,'command',side_effect=fail_push),patch.object(publisher.time,'sleep'):
            with self.assertRaises(subprocess.CalledProcessError):recovery.publish(self.root,publisher.publish)
        out=recovery.publish(self.root,publisher.publish)
        self.assertTrue(out['new_predictions']);self.assertEqual(out['new_ids'],['p'])
        self.assertFalse(recovery.publish(self.root,publisher.publish)['new_predictions'])

    def test_failed_push_preserves_local_evidence_then_recovers_once(self):
        path=self.seed();raw=path.read_bytes();real=__import__('publish_forward_evidence').command
        import publish_forward_evidence as publisher
        def fail_push(root,*args):
            if args[0]=='push':raise subprocess.CalledProcessError(1,['git',*args])
            return real(root,*args)
        with patch.object(publisher,'command',side_effect=fail_push),patch.object(publisher.time,'sleep'):
            with self.assertRaises(subprocess.CalledProcessError):publisher.publish(self.root)
        self.assertEqual(path.read_bytes(),raw)
        with self.assertRaises(subprocess.CalledProcessError):self.git(self.remote,'show','main:data/direction_v42/forward_registry.csv')
        recovered=publisher.publish(self.root)
        self.assertEqual(recovered['status'],'verified_remote_readback')
        self.assertEqual(self.git(self.remote,'show','main:data/direction_v42/forward_registry.csv'),'forecast_id\np')
        head=self.git(self.remote,'rev-parse','main');publisher.publish(self.root)
        self.assertEqual(self.git(self.remote,'rev-parse','main'),head)

    def test_successful_push_with_failed_remote_readback_is_not_success(self):
        import publish_forward_evidence as publisher
        self.seed()
        with patch.object(publisher,'verify_readback',return_value=False):
            with self.assertRaisesRegex(ValueError,'readback mismatch'):publisher.publish(self.root)

class TransportFaultTests(unittest.TestCase):
    def test_old_late_report_cannot_waive_current_network_failure(self):
        import direction_lateness_gate as gate
        report={'status':'stale_hour_no_new_registration','generated_utc':'2026-10-13T05:45:01+00:00',
                'feature_close_at_utc':'2026-10-13T05:00:00+00:00','feature_lag_min':45.02,'new_ids':[]}
        issue={'status':'failed','production_new':0,'pair_error':None,'observed_at_utc':'2026-10-13T06:17:01+00:00',
               'capture':{'status':'failed','attempts':1,'events':[{'started_at_utc':'2026-10-13T06:17:00+00:00','error':'URLError: offline'}]}}
        with self.assertRaises(ValueError):gate.verify(report,issue,'2026-10-13T06:16:59+00:00')
        issue['capture']['events'][0]={'started_at_utc':'2026-10-13T06:17:00+00:00','exit_code':1}
        with self.assertRaisesRegex(ValueError,'current attempt'):gate.verify(report,issue,'2026-10-13T06:16:59+00:00')

    def test_current_genuine_late_skip_is_safe_but_partial_or_pair_failure_is_not(self):
        import direction_lateness_gate as gate
        report={'status':'stale_hour_no_new_registration','generated_utc':'2026-10-13T06:46:00+00:00',
                'feature_close_at_utc':'2026-10-13T06:00:00+00:00','feature_lag_min':46.0,'new_ids':[]}
        issue={'status':'failed','production_new':0,'pair_error':None,'observed_at_utc':'2026-10-13T06:46:01+00:00',
               'capture':{'status':'failed','attempts':1,'events':[{'started_at_utc':'2026-10-13T06:45:59+00:00','exit_code':1}]}}
        start='2026-10-13T06:45:58+00:00'
        self.assertEqual(gate.verify(report,issue,start)['status'],'CURRENT_LATE_BUT_SAFE')
        with self.assertRaisesRegex(ValueError,'current attempt'):
            gate.verify(report,issue,'2026-10-13T07:00:00+00:00')
        for change in ({'production_new':1},{'pair_error':'failed challenger'}):
            with self.assertRaises(ValueError):gate.verify(report,dict(issue,**change),start)
        for change in ({'generated_utc':'2026-10-13T06:46:02+00:00'},{'feature_lag_min':99},{'feature_lag_min':float('nan')},{'new_ids':['injected']}):
            with self.assertRaises(ValueError):gate.verify(dict(report,**change),issue,start)

    def test_timeout_and_offline_are_bounded_and_do_not_create_evidence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);before=retry.state(root)
            for error in (socket.timeout('timeout'),urllib.error.URLError('offline')):
                def fail(*args):raise error
                out=retry.capture('watch',root,fail,lambda _:None)
                self.assertEqual(out['status'],'failed');self.assertEqual(out['attempts'],3)
                self.assertEqual(retry.state(root),before)

if __name__=='__main__':unittest.main()

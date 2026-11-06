"""Strict rescue scope; synthetic quotes and local files only."""
import hashlib,json,os,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch,Mock
import quote_only_rescue as rescue

NOW=datetime(2026,11,6,3,10,tzinfo=timezone.utc)
def row(h=24,issued=None):
    issued=issued or NOW-timedelta(hours=h,minutes=5)
    hour=issued.replace(minute=0,second=0,microsecond=0)
    fid=f'{rescue.frozen.MODEL}_{int((hour-timedelta(hours=1)).timestamp()*1000)}_{h}h'
    return dict(forecast_id=fid,model_id=rescue.frozen.MODEL,horizon_hours=str(h),
                issued_at_utc=issued.isoformat(),source_quote_utc=issued.isoformat(),
                source_hour_start_utc=hour.isoformat(),target_utc=(issued+timedelta(hours=h)).isoformat())

class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict(os.environ,{k:v for k,v in os.environ.items() if not k.startswith('GIT_')},clear=True);self.environment.start()
    def tearDown(self):self.environment.stop()
    def test_both_exact_current_github_origin_forms_reach_clone_without_network(self):
        class CloneReached(RuntimeError):pass
        origins=['https://github.com/lzwislam-collab/doge-data-collector',
                 'https://github.com/lzwislam-collab/doge-data-collector.git']
        for origin in origins:
            calls=[]
            def git(root,*args):
                if args==('remote','get-url','origin'):return origin.encode()
                if args[0]=='clone':calls.append(args);raise CloneReached('test-only clone interception; no network')
                raise AssertionError('unexpected Git operation before clone')
            with self.subTest(origin=origin),tempfile.TemporaryDirectory() as directory,patch.object(rescue,'local_git',side_effect=git):
                with self.assertRaises(CloneReached):rescue.create_attempt(Path(directory),Path(directory),{})
                self.assertEqual(len(calls),1);self.assertIn(origin,calls[0]);self.assertIn('--no-hardlinks',calls[0])
    def test_foreign_insecure_and_spoofed_github_origins_stop_before_clone(self):
        origins=['https://github.com/other/repository.git','https://example.test/lzwislam-collab/doge-data-collector',
                 'http://github.com/lzwislam-collab/doge-data-collector',
                 'https://user@example.test/lzwislam-collab/doge-data-collector',
                 'https://github.com/lzwislam-collab/doge-data-collector.evil']
        for origin in origins:
            calls=[]
            def git(root,*args):
                if args==('remote','get-url','origin'):return origin.encode()
                calls.append(args);raise AssertionError('foreign origin reached a Git write')
            with self.subTest(origin=origin),tempfile.TemporaryDirectory() as directory,patch.object(rescue,'local_git',side_effect=git):
                with self.assertRaises(ValueError):rescue.create_attempt(Path(directory),Path(directory),{})
                self.assertEqual(calls,[])
    def test_valid_24h_selected(self):
        r=row();self.assertEqual(rescue.eligible_ids({r['forecast_id']:r},{},NOW,[r['forecast_id']]),[r['forecast_id']])
    def test_requested_other_horizons_rejected_before_side_effect(self):
        for h in (1,168,720):
            r=row(h)
            with self.subTest(h=h),self.assertRaises(ValueError):
                rescue.eligible_ids({r['forecast_id']:r},{},NOW,[r['forecast_id']])
    def test_invalid_identity_period_and_clocks(self):
        changes=[{'forecast_id':'forged_24h'},{'model_id':'other'},{'horizon_hours':'1'},
                 {'target_utc':NOW.isoformat()},{'source_hour_start_utc':NOW.isoformat()},
                 {'source_quote_utc':(NOW+timedelta(seconds=1)).isoformat()}]
        changes.extend({'issued_at_utc':value} for value in ('2026-11-05T11:05:00+08:00','2026-11-05 03:05:00+00:00','2026-11-05T03:05:00.0000001Z'))
        for change in changes:
            r=dict(row(),**change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                rescue.eligible_ids({r['forecast_id']:r},{},NOW,[r['forecast_id']])
    def test_missing_empty_duplicate_unknown_request_rejected(self):
        r=row();fid=r['forecast_id']
        for ids in (None,[],[fid,fid],['unknown']):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                rescue.eligible_ids({fid:r},{},NOW,ids)
    def test_expired_and_scored_are_not_selected(self):
        r=row();fid=r['forecast_id']
        self.assertEqual(rescue.eligible_ids({fid:r},{},NOW+timedelta(minutes=16),[fid]),[])
        self.assertEqual(rescue.eligible_ids({fid:r},{fid:{}},NOW,[fid]),[])
    def test_aggregate_capture_cannot_select_unrequested_or_other_period(self):
        r=row();fid=r['forecast_id']
        for extra in (row(1),row(168),row(24,NOW-timedelta(hours=24,minutes=11))):
            registry={fid:r,extra['forecast_id']:extra}
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                rescue.capture_scope(registry,{},[fid],NOW,NOW)
    def test_capture_checks_actual_exchange_time_and_reserve(self):
        r=row();fid=r['forecast_id']
        for quoted,observed in ((NOW,NOW+timedelta(minutes=16)),(NOW+timedelta(seconds=1),NOW),
                                (NOW-timedelta(seconds=121),NOW)):
            with self.subTest(quoted=quoted),self.assertRaises(ValueError):
                rescue.capture_scope({fid:r},{},[fid],quoted,observed)
    def test_quote25m_and_age120s_boundaries_remain_distinct_from_rescue20m(self):
        r=row();fid=r['forecast_id'];target=rescue.frozen.utc(r['target_utc'])
        quoted=target+timedelta(minutes=25);observed=quoted+timedelta(seconds=120)
        raw=json.dumps({'code':'0','data':[{'instId':'DOGE-USDT','last':'1.1','ts':str(int(quoted.timestamp()*1000))}]})
        payload={'source_url':rescue.evidence.URL,'raw_response_utf8':raw,'observed_at_utc':observed.isoformat()}
        self.assertEqual(rescue.evidence.parse(payload)[1:],(quoted,observed))
        with self.assertRaises(ValueError):rescue.capture_scope({fid:r},{},[fid],quoted,observed)
        payload['observed_at_utc']=(observed+timedelta(microseconds=1)).isoformat()
        with self.assertRaises(ValueError):rescue.evidence.parse(payload)
        self.assertEqual(rescue.eligible_ids({fid:r},{},target+timedelta(minutes=20),[fid]),[fid])
        self.assertEqual(rescue.eligible_ids({fid:r},{},target+timedelta(minutes=20,microseconds=1),[fid]),[])
    def test_invalid_request_run_never_invokes_capture_publish_or_git_config(self):
        r=row(1);state={'registry':{r['forecast_id']:r},'scores':{},'bindings':{},'snapshots':{}}
        capture=Mock();publish=Mock()
        with patch.object(rescue,'ensure_clean_checkout'),patch.object(rescue,'guard_protocol'),patch.object(rescue,'take_state',return_value=state),patch.object(rescue.publisher,'command') as command:
            with self.assertRaises(ValueError):rescue.run(Path('.'),[r['forecast_id']],clock=lambda:NOW,capture_fn=capture,publish_fn=publish)
            capture.assert_not_called();publish.assert_not_called();command.assert_not_called()
    def test_formal_validation_covers_requested_target_that_opens_during_http(self):
        now=NOW.replace(minute=1)
        first=row(24,now-timedelta(hours=24,minutes=16));future=row(24,now-timedelta(hours=24)+timedelta(seconds=1))
        ids=[first['forecast_id'],future['forecast_id']]
        state={'registry':{r['forecast_id']:r for r in (first,future)},'scores':{},'bindings':{},'snapshots':{}}
        capture=Mock();publish=Mock()
        with patch.object(rescue,'ensure_clean_checkout'),patch.object(rescue,'guard_protocol'),patch.object(rescue,'take_state',return_value=state),patch.object(rescue.publisher,'validate_state'),patch.object(rescue,'verify_formal_issues',side_effect=ValueError('future target lacks witness')) as formal:
            # This is a pure pre-capture policy unit test of the actual attempt
            # entry. Full isolated-clone behavior is tested with local bare Git
            # fixtures in RescueTests; never clone the production origin here.
            with self.assertRaises(ValueError):rescue._run_in_attempt(Path('.'),ids,clock=lambda:now,capture_fn=capture,publish_fn=publish)
            self.assertEqual(formal.call_args.args[-1],set(ids));capture.assert_not_called();publish.assert_not_called()
    def test_http_result_rejected_before_any_quote_score_or_binding_write(self):
        r=row();fid=r['forecast_id'];extra=row(1,NOW-timedelta(hours=1)+timedelta(seconds=1))
        returned=NOW+timedelta(seconds=2)
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({'code':'0','data':[{'instId':'DOGE-USDT','last':'1','ts':str(int(returned.timestamp()*1000))}]}).encode()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(rescue.evidence.urllib.request,'urlopen',return_value=Response()),patch.object(rescue.frozen,'read_rows',side_effect=[{fid:r,extra['forecast_id']:extra},{},{}]),patch.object(rescue.evidence,'history_receipts'),patch.object(rescue.evidence,'save_quote') as save,patch.object(rescue.evidence.watch,'run') as watch:
                with self.assertRaises(ValueError):rescue.collect_scoped(root,[fid],clock=lambda:returned)
                save.assert_not_called();watch.assert_not_called();self.assertEqual(list(root.rglob('*')),[])

    def test_missing_formal_witness_rejected_without_network(self):
        r=row();fid=r['forecast_id']
        protocol={'effective_from_utc':'2026-10-12T02:00:00+00:00'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/rescue.v2.PROTOCOL;path.parent.mkdir(parents=True);path.write_text(json.dumps(protocol))
            with patch.object(rescue.frozen,'read_rows',return_value={}),patch.object(rescue.base,'history_receipts'),patch.object(rescue.base,'remote_witnesses') as remote:
                with self.assertRaisesRegex(ValueError,'witness missing'):rescue.verify_formal_issues(root,{fid:r},{},[fid])
                remote.assert_not_called()

    def test_invalid_formal_publication_blocked_before_raw_issue_verification(self):
        r=row();fid=r['forecast_id'];issued=rescue.frozen.utc(r['issued_at_utc'])
        protocol={'effective_from_utc':'2026-10-12T02:00:00+00:00'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/rescue.v2.PROTOCOL;path.parent.mkdir(parents=True);path.write_text(json.dumps(protocol))
            for stamp in (issued+timedelta(minutes=46),NOW):
                witness={fid:{'observed_public_utc':stamp.isoformat(),'head_sha':'a'*40}}
                with self.subTest(stamp=stamp),patch.object(rescue.frozen,'read_rows',return_value=witness),patch.object(rescue.base,'history_receipts'),patch.object(rescue.base,'remote_witnesses',return_value=witness),patch.object(rescue.evidence,'verify_public_issue') as verify:
                    with self.assertRaises(ValueError):rescue.verify_formal_issues(root,{fid:r},{},[fid])
                    verify.assert_not_called()

    def test_residual_target_binding_is_rejected_before_raw_write_even_same_hash(self):
        r=row();fid=r['forecast_id']
        raw=json.dumps({'code':'0','data':[{'instId':'DOGE-USDT','last':'1','ts':str(int(NOW.timestamp()*1000))}]})
        payload={'schema_version':2,'source_url':rescue.evidence.URL,'observed_at_utc':NOW.isoformat(),'raw_response_utf8':raw,
                 'transport':'HTTPS collector observation; not exchange-signed and not an executable fill'}
        qid=hashlib.sha256((json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()
        bindings={};rescue.evidence.bind(bindings,fid,'TARGET',qid)
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock();response.read.return_value=raw.encode()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(rescue.evidence.urllib.request,'urlopen',return_value=response),patch.object(rescue.frozen,'read_rows',side_effect=[{fid:r},{},bindings]),patch.object(rescue.frozen,'verify_append_only'),patch.object(rescue.evidence,'history_receipts'),patch.object(rescue.frozen,'score',return_value=({fid:{'status':'scored'}},1)),patch.object(rescue.evidence,'verify_bound'),patch.object(rescue.evidence,'save_quote') as save:
                with self.assertRaises(ValueError):rescue.collect_scoped(Path(directory),[fid],clock=lambda:NOW)
                save.assert_not_called()

if __name__=='__main__':unittest.main()

import copy, json, subprocess, tempfile, unittest, urllib.error
from contextlib import ExitStack
from unittest.mock import patch
from pathlib import Path
from datetime import datetime, timedelta, timezone
import quote_capture_retry as retry
import p0_operational_audit as audit

class RunPaginationTests(unittest.TestCase):
    def test_eight_days_of_ten_minute_polls_exceeds_1000_without_losing_runs(self):
        from urllib.parse import parse_qs,urlparse
        since=datetime(2026,10,1,tzinfo=timezone.utc);until=since+timedelta(days=8)
        def request(path):
            if '/runs?' not in path:return {'id':42}
            query=parse_qs(urlparse(path).query);start,end=query['created'][0].split('..')
            start=datetime.fromisoformat(start.replace('Z','+00:00'));end=datetime.fromisoformat(end.replace('Z','+00:00'))
            self.assertLessEqual((end-start).total_seconds(),86400)
            day=(start-since).days;page=int(query['page'][0]);count=range(100) if page==1 else range(100,144)
            return {'workflow_runs':[{'id':day*144+i,'created_at':start.isoformat()} for i in count]}
        rows=audit.fetch_operational_runs(since,request,until=until)
        self.assertEqual(len(rows),1152)
    def test_daily_target_failure_alerts_when_hourly_is_healthy(self):
        report={'rolling_7d':{'1h':{'slots':[{'status':'verified_issued'}],'target_missing':0},
                 '24h':{'slots':[{'status':'verified_issued'}],'target_missing':1,'target_missing_ids':['daily-1']}}}
        out=audit.alert_reasons(report)
        self.assertEqual(out,[{'horizon':'24h','kind':'missing_target_evidence','forecast_ids':['daily-1']}])
    def test_old_missing_slots_still_alert_when_latest_slot_is_healthy(self):
        t=datetime(2026,10,12,11,30,tzinfo=timezone.utc)
        rows=[{'status':'workflow_failed','slot_start_utc':'2026-10-12T02:00:00+00:00'},
              {'status':'workflow_failed','slot_start_utc':'2026-10-12T03:00:00+00:00'},
              {'status':'verified_issued','slot_start_utc':'2026-10-12T04:00:00+00:00'}]
        report={'audited_at_utc':t.isoformat(),'rolling_7d':{'1h':{
            'slots':rows,'expected':3,'issued':1,'target_missing':0}}}
        alerts=audit.alert_reasons(report)
        self.assertEqual(alerts,[{'horizon':'1h',
            'kind':'issuance_coverage_below_90pct','audit_day_utc':'2026-10-12',
            'required_pct':90}])

    def test_daily_coverage_alert_is_stable_across_retries(self):
        def report(n):
            rows=([{'status':'workflow_failed'}]*(n-1)+[{'status':'verified_issued'}])
            return {'audited_at_utc':f'2026-10-12T11:{n:02d}:00+00:00',
              'rolling_7d':{'1h':{'slots':rows,'expected':n,'issued':1}}}
        self.assertEqual(audit.alert_reasons(report(4)),audit.alert_reasons(report(5)))

    def test_full_coverage_or_no_closed_slots_do_not_alert(self):
        report={'audited_at_utc':'2026-10-12T11:30:00+00:00',
          'rolling_7d':{'1h':{'slots':[{'status':'verified_issued'}],
                           'expected':1,'issued':1},
                        '24h':{'slots':[],'expected':0,'issued':0}}}
        self.assertEqual(audit.alert_reasons(report),[])

    def test_alert_rejects_inconsistent_denominators(self):
        report={'rolling_7d':{'1h':{'slots':[{'status':'workflow_failed'}],
            'expected':1,'issued':2}}}
        with self.assertRaisesRegex(ValueError,'slot-count mismatch'):
            audit.alert_reasons(report)

    def test_operational_runs_include_second_page(self):
        calls=[]
        def request(path):
            calls.append(path)
            if '/runs?' not in path:return {'id':42}
            page=2 if 'page=2' in path else 1
            rows=[{'id':i,'created_at':'2026-10-12T02:00:00Z'} for i in range(100)] if page==1 else [{'id':101,'created_at':'2026-10-12T01:00:00Z'}]
            return {'workflow_runs':rows}
        rows=audit.fetch_operational_runs(datetime(2026,10,12,tzinfo=timezone.utc),request)
        self.assertEqual(len(rows),101);self.assertIn(101,{r['id'] for r in rows})
        self.assertTrue(any('page=2' in path for path in calls))

class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def test_transient_fetch_failure_recovers(self):
        calls=[]
        def collect(root,mode):
            calls.append(mode)
            if len(calls)<3: raise urllib.error.URLError('temporary')
            return 0
        out=retry.capture('watch',self.root,collect,lambda _:None)
        self.assertEqual(out['status'],'ok'); self.assertEqual(out['attempts'],3)
    def test_exhaustion_is_reported(self):
        def collect(root,mode): raise urllib.error.HTTPError('url',503,'unavailable',{},None)
        out=retry.capture('watch',self.root,collect,lambda _:None)
        self.assertEqual(out['status'],'failed'); self.assertEqual(out['attempts'],3)
    def test_integrity_errors_are_not_retried(self):
        def collect(root,mode): raise ValueError('immutable quote binding replaced')
        out=retry.capture('direction',self.root,collect,lambda _:None)
        self.assertEqual(out['status'],'failed'); self.assertEqual(out['attempts'],1)
    def test_partial_writes_stop_retries(self):
        path=self.root/'data/direction_v42/forward_registry.csv'
        def collect(root,mode):
            path.parent.mkdir(parents=True,exist_ok=True); path.write_text('partial')
            raise urllib.error.URLError('temporary after mutation')
        out=retry.capture('direction',self.root,collect,lambda _:None)
        self.assertEqual(out['status'],'failed'); self.assertEqual(out['attempts'],1)
    def test_late_return_is_not_treated_as_success(self):
        out=retry.capture('direction',self.root,lambda root,mode:1,lambda _:None)
        self.assertEqual(out['status'],'failed'); self.assertEqual(out['attempts'],1)

class SlotTests(unittest.TestCase):
    def setUp(self): self.t=datetime(2026,10,12,2,tzinfo=timezone.utc)
    def row(self,pid,minutes=13,h=1):
        t=self.t+timedelta(minutes=minutes)
        return {'forecast_id':pid,'issued_at_utc':t.isoformat(),'source_hour_start_utc':t.replace(minute=0,second=0).isoformat(),
                'horizon_hours':str(h),'target_utc':(t+timedelta(hours=h)).isoformat()}
    def test_missing_runs_keep_all_closed_slots(self):
        out=audit.reconcile([],{},set(),self.t,self.t+timedelta(hours=3),[])
        self.assertEqual((out['expected'],out['missing']),(3,3))
        self.assertEqual(out['slots'][0]['status'],'no_workflow_run')
    def test_late_issuance_is_missing_and_order_does_not_change_result(self):
        rows=[self.row('late',46),self.row('timely',73)]
        a=audit.reconcile(rows,{}, {'late','timely'},self.t,self.t+timedelta(hours=2),[])
        b=audit.reconcile(list(reversed(rows)),{}, {'late','timely'},self.t,self.t+timedelta(hours=2),[])
        self.assertEqual(a,b);self.assertEqual((a['issued'],a['missing']),(1,1))
        self.assertEqual(a['slots'][0]['status'],'late_issuance')
    def test_unpublished_is_not_counted_and_due_denominator_is_retained(self):
        row=self.row('p')
        out=audit.reconcile([row],{'p':{'status':'scored'}},set(),self.t,self.t+timedelta(hours=2),[])
        self.assertEqual(out['issued'],0);self.assertEqual(out['target_due'],1)
        self.assertEqual(out['target_scored'],0);self.assertEqual(out['target_coverage_pct'],0)
    def test_duplicate_slot_is_not_extra_success(self):
        out=audit.reconcile([self.row('p'),self.row('q',14)],{}, {'p','q'},self.t,self.t+timedelta(hours=1),[])
        self.assertEqual(out['issued'],0);self.assertEqual(out['slots'][0]['status'],'duplicate_issuance')
    def test_no_matured_target_is_null(self):
        out=audit.reconcile([self.row('p')],{}, {'p'},self.t,self.t+timedelta(minutes=50),[])
        self.assertEqual(out['target_due'],0);self.assertIsNone(out['target_coverage_pct'])
    def test_rolling_window_retains_missing_denominator(self):
        out=audit.reconcile([],{},set(),self.t,self.t+timedelta(days=8),[])
        self.assertEqual(out['expected'],168);self.assertEqual(out['missing'],168)
    def test_thirty_day_window_is_separate_and_keeps_all_missing_slots(self):
        out=audit.reconcile([],{},set(),self.t,self.t+timedelta(days=31),[],window_days=30)
        self.assertEqual(out['rolling_window_hours'],720)
        self.assertEqual(out['expected'],720)
        self.assertEqual(out['missing'],720)
        self.assertTrue(out['full_window_observed'])
        self.assertFalse(out['window_accepted'])
    def test_three_days_cannot_pass_thirty_day_maturity(self):
        out=audit.reconcile([],{},set(),self.t,self.t+timedelta(days=3),[],window_days=30)
        self.assertFalse(out['full_window_observed'])
        self.assertIsNone(out['target_coverage_pct'])
    def test_failed_run_distinguished_from_absent_run(self):
        run={'id':7,'created_at':self.t.isoformat(),'path':'.github/workflows/okx_hourly_extreme_history.yml','conclusion':'failure','event':'schedule'}
        out=audit.reconcile([],{},set(),self.t,self.t+timedelta(hours=1),[run])
        self.assertEqual(out['slots'][0]['status'],'workflow_failed')
        self.assertEqual(out['slots'][0]['run_ids'],[7])

class CapturedSourceRecoveryTests(unittest.TestCase):
    IDS=('DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1792987200000_1h',
         'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1793008800000_1h')
    REPO='lzwislam-collab/doge-data-collector'

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.seed()

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,text=True,encoding='utf-8').strip()

    def commit(self):
        self.git('add','.');self.git('-c','user.name=Offline QA','-c','user.email=qa@example.test',
                                    'commit','-qm','synthetic offline fixture')
        return self.git('rev-parse','HEAD')

    def write(self,path,body):
        path=self.root/path;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body,encoding='utf-8')

    def seed(self,missing_public_quote=False,missing_public_binding=False):
        self.git('init','-q');self.git('config','core.autocrlf','false');self.rows={};self.bindings={}
        clocks=(('2026-10-26T05:00:53.297688+00:00','2026-10-26T05:00:52.769000+00:00'),
                ('2026-10-26T11:01:20.888712+00:00','2026-10-26T11:01:20.269000+00:00'),
                ('2026-10-26T13:01:00+00:00','2026-10-26T13:01:00+00:00'))
        for fid,(issued,quoted) in zip(self.IDS+('UNSCOPED',),clocks):
            at=audit.frozen.utc(issued);stamp=audit.frozen.utc(quoted)
            row=dict(zip(audit.frozen.FCOLS,[fid,'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',issued,
                quoted,at.replace(minute=0,second=0,microsecond=0).isoformat(),'.11','1',
                (at+timedelta(hours=1)).isoformat(),'.5','UP','UP','DOWN','fixture']))
            raw=json.dumps({'code':'0','data':[{'instId':'DOGE-USDT','last':'.11',
                                             'ts':str(int(stamp.timestamp()*1000))}]})
            qid=audit.evidence.save_quote(self.root,raw,at)
            audit.evidence.bind(self.bindings,fid,'ISSUE',qid);self.rows[fid]=row
        self.write(audit.base.REG,audit.frozen.serial(self.rows,audit.frozen.FCOLS))
        public_bindings={k:v for k,v in self.bindings.items()
                         if not (missing_public_binding and k==self.IDS[0]+'|ISSUE')}
        self.write(audit.evidence.BINDINGS,audit.frozen.serial(public_bindings,audit.evidence.BCOLS))
        quote=audit.evidence.snapshot_path(self.root,self.bindings[self.IDS[0]+'|ISSUE']['quote_id'])
        quote_bytes=quote.read_bytes()
        if missing_public_quote:quote.unlink()
        head=self.commit()
        if missing_public_quote:quote.write_bytes(quote_bytes)
        self.write(audit.evidence.BINDINGS,audit.frozen.serial(self.bindings,audit.evidence.BCOLS))
        observed=('2026-10-26T05:01:43+00:00','2026-10-26T11:02:05+00:00')
        self.pins={fid:(rid,head,at) for fid,rid,at in zip(self.IDS,('40272071041','40311990494'),observed)}
        self.originals={fid:{'forecast_id':fid,'run_id':rid,'head_sha':head,
            'observed_public_utc':at,'run_url':f'https://github.com/{self.REPO}/actions/runs/{rid}'}
            for fid,(rid,head,at) in self.pins.items()}
        self.server_runs={rid:{'id':int(rid),'head_sha':head,'created_at':at.replace('+00:00','Z'),
            'html_url':f'https://github.com/{self.REPO}/actions/runs/{rid}',
            'repository':{'full_name':self.REPO},'head_repository':{'full_name':self.REPO}}
            for rid,head,at in self.pins.values()}
        self.proofs={fid:{'issued_at_utc':self.rows[fid]['issued_at_utc'],
            'source_quote_utc':self.rows[fid]['source_quote_utc'],
            'quote_id':self.bindings[fid+'|ISSUE']['quote_id'],'publication':copy.deepcopy(w)}
            for fid,w in self.originals.items()}
        late=dict(self.originals[self.IDS[1]],run_id='40318185646',
            observed_public_utc='2026-10-26T11:51:38+00:00',
            run_url=f'https://github.com/{self.REPO}/actions/runs/40318185646')
        self.write(audit.base.WITNESSES,audit.frozen.serial({self.IDS[1]:late},audit.base.WCOLS))
        other={'forecast_id':'UNSCOPED','run_id':'99','head_sha':head,
               'observed_public_utc':'2026-10-26T13:02:00+00:00',
               'run_url':f'https://github.com/{self.REPO}/actions/runs/99'}
        self.legacy={'verified_remote_head_witnesses':{self.IDS[1]:late,'UNSCOPED':other},
                     'commit_receipts_not_publication_times':{}}
        self.write(audit.v2.PROTOCOL,json.dumps({'protocol_id':'OFFLINE_FIXTURE',
            'effective_from_utc':'2026-10-26T00:00:00+00:00'}))
        self.write('data/stage5/forward_validation_v2/latest.json','{"horizons":{}}')
        self.write('data/hourly_research/ohlc.csv',
            'symbol,open_utc_ms\nDOGE-USDT,1793052000000\nBTC-USDT,1793052000000\nETH-USDT,1793052000000\n')
        self.write('p0_operational_audit.py',Path(audit.__file__).read_text(encoding='utf-8'))
        self.save_proof()

    def save_proof(self):
        self.write('data/p0_operations/latest.json',json.dumps({'forecast_proofs':self.proofs}))
        self.proof_commit=self.commit()

    def evaluate(self):
        def request(path):return copy.deepcopy(self.server_runs[path.rsplit('/',1)[-1]])
        # Isolate the supplied legacy projection; publication/quote/Git guards stay real.
        with ExitStack() as stack:
            for name in ('check_protocol','check_code'):
                stack.enter_context(patch.object(audit.v2,name))
            stack.enter_context(patch.object(audit.base,'locked_file'))
            stack.enter_context(patch.object(audit.v2,'protocol_witness',return_value=True))
            stack.enter_context(patch.object(audit.base,'evaluate',return_value=copy.deepcopy(self.legacy)))
            stack.enter_context(patch.object(audit.base,'github_json',side_effect=request))
            stack.enter_context(patch.object(audit,'_CAPTURED_SOURCE_WITNESSES',self.pins,create=True))
            stack.enter_context(patch.object(audit,'_CAPTURED_SOURCE_PROOF_COMMIT',self.proof_commit,create=True))
            return audit.evaluate(self.root,datetime(2026,10,27,tzinfo=timezone.utc),runs=[])

    def test_restores_only_the_two_original_publications_without_writing_history(self):
        before={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and '.git' not in p.relative_to(self.root).parts}
        out=self.evaluate()
        for fid in self.IDS:
            self.assertIn(fid,out['forecast_proofs'])
            self.assertEqual(out['forecast_proofs'][fid]['publication'],self.originals[fid])
        self.assertEqual(out['forecast_proofs']['UNSCOPED']['publication'],
                         self.legacy['verified_remote_head_witnesses']['UNSCOPED'])
        self.assertFalse(out['p0_7d_accepted']);self.assertFalse(out['p2_profit_accepted'])
        after={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*')
               if p.is_file() and '.git' not in p.relative_to(self.root).parts}
        self.assertEqual(after,before)

    def test_changed_server_identity_is_rejected(self):
        rid=self.pins[self.IDS[0]][0];original=copy.deepcopy(self.server_runs[rid])
        for field,value in [('id',1),('head_sha','f'*40),('created_at','2026-10-26T05:01:44Z'),
                            ('html_url','https://example.test/run'),
                            ('repository',{'full_name':'wrong/repository'}),
                            ('head_repository',{'full_name':'wrong/repository'})]:
            with self.subTest(field=field):
                self.server_runs[rid]=dict(original,**{field:value})
                out=self.evaluate();self.assertNotIn(self.IDS[0],out['forecast_proofs'])
                self.assertIn('original source run identity mismatch',out['rejected_evidence'][self.IDS[0]])

    def test_changed_historical_proof_is_rejected(self):
        self.proofs[self.IDS[0]]['publication']['head_sha']='f'*40;self.save_proof()
        out=self.evaluate();self.assertNotIn(self.IDS[0],out['forecast_proofs'])
        self.assertIn('original source proof identity mismatch',out['rejected_evidence'][self.IDS[0]])

    def test_changed_current_forecast_row_cannot_be_recovered(self):
        self.rows[self.IDS[0]]['majority_direction']='DOWN'
        self.write(audit.base.REG,audit.frozen.serial(self.rows,audit.frozen.FCOLS))
        out=self.evaluate();self.assertNotIn(self.IDS[0],out['forecast_proofs'])
        self.assertIn('invalid persisted forecast witness',out['rejected_evidence'][self.IDS[0]])

    def test_missing_original_public_quote_still_fails(self):
        self.root=self.root/'missing-quote';self.root.mkdir();self.seed(missing_public_quote=True)
        out=self.evaluate();self.assertNotIn(self.IDS[0],out['forecast_proofs'])
        self.assertIn('issue raw quote not present',out['rejected_evidence'][self.IDS[0]])

    def test_missing_original_public_binding_still_fails(self):
        self.root=self.root/'missing-binding';self.root.mkdir();self.seed(missing_public_binding=True)
        out=self.evaluate();self.assertNotIn(self.IDS[0],out['forecast_proofs'])
        self.assertIn('ISSUE binding absent',out['rejected_evidence'][self.IDS[0]])

    def test_unknown_forecast_does_not_gain_a_recovery_path(self):
        self.legacy['verified_remote_head_witnesses'].pop('UNSCOPED')
        out=self.evaluate();self.assertNotIn('UNSCOPED',out['forecast_proofs'])
        self.assertEqual(out['rejected_evidence']['UNSCOPED'],'no pre-target GitHub server run witness')

    def test_invalid_current_issue_quote_still_fails(self):
        path=audit.evidence.snapshot_path(self.root,self.bindings[self.IDS[0]+'|ISSUE']['quote_id'])
        path.write_bytes(path.read_bytes()+b' ')
        out=self.evaluate();self.assertNotIn(self.IDS[0],out['forecast_proofs'])
        self.assertIn('hash',out['rejected_evidence'][self.IDS[0]])

    def test_recovery_cannot_relax_the_45_minute_publication_deadline(self):
        fid=self.IDS[0];rid,head,_=self.pins[fid];late='2026-10-26T05:50:00+00:00'
        self.pins[fid]=(rid,head,late);self.proofs[fid]['publication']['observed_public_utc']=late
        self.server_runs[rid]['created_at']=late.replace('+00:00','Z');self.save_proof()
        out=self.evaluate();self.assertNotIn(fid,out['forecast_proofs'])
        self.assertEqual(out['rejected_evidence'][fid],'late public witness')

    def test_proof_commit_must_be_an_ancestor_of_current_checkout(self):
        self.proof_commit=self.git('-c','user.name=Offline QA','-c','user.email=qa@example.test',
            'commit-tree',self.git('rev-parse','HEAD^{tree}'),'-m','unrelated proof fixture')
        out=self.evaluate()
        for fid in self.IDS:self.assertNotIn(fid,out['forecast_proofs'])

if __name__=='__main__': unittest.main()

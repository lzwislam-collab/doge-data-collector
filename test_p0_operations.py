import tempfile, unittest, urllib.error
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

if __name__=='__main__': unittest.main()

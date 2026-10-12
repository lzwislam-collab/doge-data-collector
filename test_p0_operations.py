import tempfile, unittest, urllib.error
from pathlib import Path
from datetime import datetime, timedelta, timezone
import quote_capture_retry as retry
import p0_operational_audit as audit

class RunPaginationTests(unittest.TestCase):
    def test_daily_target_failure_alerts_when_hourly_is_healthy(self):
        report={'rolling_7d':{'1h':{'slots':[{'status':'verified_issued'}],'target_missing':0},
                 '24h':{'slots':[{'status':'verified_issued'}],'target_missing':1,'target_missing_ids':['daily-1']}}}
        out=audit.alert_reasons(report)
        self.assertEqual(out,[{'horizon':'24h','kind':'missing_target_evidence','forecast_ids':['daily-1']}])
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
    def test_failed_run_distinguished_from_absent_run(self):
        run={'id':7,'created_at':self.t.isoformat(),'path':'.github/workflows/okx_hourly_extreme_history.yml','conclusion':'failure','event':'schedule'}
        out=audit.reconcile([],{},set(),self.t,self.t+timedelta(hours=1),[run])
        self.assertEqual(out['slots'][0]['status'],'workflow_failed')
        self.assertEqual(out['slots'][0]['run_ids'],[7])

if __name__=='__main__': unittest.main()

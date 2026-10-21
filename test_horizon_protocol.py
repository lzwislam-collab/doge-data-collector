import copy
import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import horizon_protocol as hp

class HorizonTests(unittest.TestCase):
    def test_threshold_uses_only_past_and_freezes(self):
        rows=[{'anchor_ms':i*1000,'label_end_ms':i*1000+100,'raw_score':.7 if i%2 else .3,'y':i%2} for i in range(120)]
        a=hp.freeze_threshold(rows,200000)
        self.assertEqual(a['status'],'FROZEN')
        self.assertTrue(a['curve'])
        with self.assertRaises(ValueError):hp.freeze_threshold(rows,100000)
        self.assertEqual(hp.action(.5,a),'NO_TRADE')
        self.assertEqual(hp.action(.9,a),'UP')
        self.assertEqual(hp.action(.1,a),'DOWN')
    def test_no_eligible_threshold_means_abstain(self):
        a=hp.freeze_threshold([],1000)
        self.assertEqual(hp.action(.99,a),'NO_TRADE')
    def test_raw_is_not_probability_and_cross_horizon_rejected(self):
        m={'horizon_hours':24,'model_id':'x','calibration':{'status':'UNCALIBRATED'}}
        r=hp.probability_layer(.7,m,24)
        self.assertIsNone(r['probability_up'])
        self.assertEqual(r['label'],'raw score / uncalibrated')
        with self.assertRaises(ValueError):hp.probability_layer(.7,m,1)
    def test_daily_weekly_grids_and_no_backfill(self):
        start=datetime(2026,10,26,tzinfo=timezone.utc)
        p=hp.new_policy(start)
        self.assertEqual(hp.slot(p,168,start+timedelta(days=8)),start+timedelta(days=7))
        self.assertEqual(hp.slot(p,24,start+timedelta(hours=30)),start+timedelta(days=1))
        self.assertFalse(hp.issue_due(p,24,start+timedelta(hours=1),[]))
        self.assertTrue(hp.issue_due(p,24,start+timedelta(minutes=10),[]))
    def test_core_table_missing_pending_duplicate_and_baselines(self):
        t=datetime(2026,10,26,tzinfo=timezone.utc);p=hp.new_policy(t)
        e={'event_id':str(int(t.timestamp()))+'_1','slot_utc':t.isoformat(),'issued_at_utc':(t+timedelta(minutes=10)).isoformat(),'target_utc':(t+timedelta(hours=1,minutes=10)).isoformat(),'horizon_hours':1,'raw_score':.6,'direction':'UP','selective_direction':'NO_TRADE','probability_up':None,'btc_direction':'DOWN','frozen_direction':'UP'}
        r=hp.core_table(p,[e],[],{},t+timedelta(hours=2,minutes=30))['1']
        self.assertEqual(r['Matured'],1);self.assertEqual(r['Missing'],1)
        self.assertIsNone(r['Brier']);self.assertIsNone(r['Net return'])
        with self.assertRaises(ValueError):hp.core_table(p,[e,e],[],{},t+timedelta(hours=2,minutes=30))
        s={'event_id':e['event_id'],'y':1,'net_return':None}
        r=hp.core_table(p,[e],[s],{e['event_id']:True},t+timedelta(hours=2,minutes=30))['1']
        self.assertEqual(r['Always Up'],1);self.assertEqual(r['BTC baseline'],0)
        self.assertEqual(r['NO_TRADE rate'],1);self.assertIsNone(r['Selective Accuracy'])
    def test_pending_not_missing(self):
        t=datetime(2026,10,26,tzinfo=timezone.utc);r=hp.core_table(hp.new_policy(t),[],[],{},t)['24']
        self.assertEqual(r['Matured'],0);self.assertEqual(r['Missing'],0)
        self.assertIsNone(r['Coverage'])
    def test_hourly_keeps_actual_one_hour_horizon_and_final_deadline(self):
        t=datetime(2026,10,26,tzinfo=timezone.utc);p=hp.new_policy(t)
        self.assertEqual(hp.core_table(p,[],[],{},t+timedelta(hours=2))['1']['Matured'],0)
        self.assertEqual(hp.target_time(t,t+timedelta(minutes=40),1),t+timedelta(hours=1,minutes=40))
    def test_partial_witness_never_enters_formal_statistics(self):
        t=datetime(2026,10,26,tzinfo=timezone.utc);p=hp.new_policy(t)
        e={'event_id':str(int(t.timestamp()))+'_1','slot_utc':t.isoformat(),'issued_at_utc':t.isoformat(),'target_utc':(t+timedelta(hours=1)).isoformat(),'horizon_hours':1,'raw_score':.6,'direction':'UP','selective_direction':'UP','probability_up':None,'btc_direction':'UP','frozen_direction':'UP'}
        s={'event_id':e['event_id'],'y':1,'net_return':.01}
        r=hp.core_table(p,[e],[s],{e['event_id']:{'prediction':{'run_id':1}}},t+timedelta(hours=2))['1']
        self.assertEqual(r['Scored'],0)
    def test_snapshot_is_bounded_and_exact(self):
        import csv,io
        rows='symbol,open_utc_ms,close\n'+''.join('DOGE-USDT,'+str(i*3600000)+',1\n' for i in range(300))
        small=hp.source_tail(rows.encode())
        data=list(csv.DictReader(io.StringIO(small.decode())))
        self.assertEqual(len(data),169);self.assertEqual(data[-1]['open_utc_ms'],str(299*3600000))
    def test_unsafe_pinned_read_is_rejected(self):
        with self.assertRaises(ValueError):hp.read_at('a'*40,'../secret')
        with self.assertRaises(ValueError):hp.read_at('main',hp.PACKAGE)
    def test_registration_requires_exact_successful_main_job_before_start(self):
        import tempfile
        from pathlib import Path
        t=datetime(2026,10,26,tzinfo=timezone.utc);p={'policy':hp.new_policy(t),'fixed_files':{}}
        run={'id':1,'head_sha':'a'*40,'head_branch':'main','status':'completed','conclusion':'success','event':'workflow_dispatch','created_at':(t-timedelta(hours=1)).isoformat()}
        job={'name':'horizons','conclusion':'success','started_at':(t-timedelta(minutes=55)).isoformat(),'completed_at':(t-timedelta(minutes=50)).isoformat()}
        def api(path):return {'workflow_runs':[run]} if '/workflows/' in path else {'jobs':[job]}
        with tempfile.TemporaryDirectory() as temp,patch.object(hp,'ROOT',Path(temp)),patch.object(hp.bundle,'api',side_effect=api),patch.object(hp,'read_at',return_value=hp.encode(p)):
            proof,error,reg=hp.witnesses([],[],p,t)
            self.assertIsNone(error);self.assertEqual(reg['run_id'],1)
            job['completed_at']=(t+timedelta(minutes=1)).isoformat()
            self.assertIsNone(hp.witnesses([],[],p,t+timedelta(minutes=2))[2])
            job['completed_at']=(t-timedelta(minutes=50)).isoformat();run['conclusion']='failure'
            self.assertIsNone(hp.witnesses([],[],p,t)[2])
            run['conclusion']='success';p['fixed_files']={'horizon_protocol.py':'wrong-digest'}
            self.assertIsNone(hp.witnesses([],[],p,t)[2])
    def test_actual_model_horizon_binds_before_inference(self):
        m={'horizon_hours':168,'calibration':{'status':'UNCALIBRATED'}}
        self.assertEqual(hp.probability_layer(.2,m,168)['label'],'raw score / uncalibrated')
        with self.assertRaises(ValueError):hp.probability_layer(.2,m,24)
    def test_existing_r2_expiry_is_preserved(self):
        m={'horizon_hours':1,'base':{'kind':'r2','model':{}}}
        with patch.object(hp.r2,'check_age',side_effect=ValueError('expired')),patch.object(hp.r2,'frame_from_raw',return_value={}),patch.object(hp.r2,'features',return_value=[]),patch.object(hp.models,'predict',return_value=[.5]):
            with self.assertRaisesRegex(ValueError,'expired'):hp.predict(m,b'',datetime(2028,1,1,tzinfo=timezone.utc))
    def test_threshold_selection_is_invariant_to_validation_results(self):
        rows=[{'anchor_ms':i,'label_end_ms':i+1,'raw_score':.7,'y':1} for i in range(40)]
        frozen=hp.freeze_threshold(rows,100)
        before=hp.digest(frozen)
        hp.curve([{'raw_score':.9,'y':0}],[0.,.1])
        self.assertEqual(hp.digest(frozen),before)
    def test_stale_entry_and_changed_decision_fail_replay(self):
        t=datetime(2026,10,26,tzinfo=timezone.utc)
        m={'model_id':'m','horizon_hours':1};p={'identity':'p','models':{'1':m}}
        e={'horizon_hours':1,'issued_at_utc':t.isoformat(),'package_identity':'p','model_id':'m','real_orders_authorized':False,'source_raw':'csv','source_sha256':hp.sha(b'csv'),'features':[1.],'raw_score':.6,'selective_direction':'NO_TRADE','entry_packet':{}}
        with patch.object(hp,'predict',return_value=(.6,{},[1.])),patch.object(hp,'derived',return_value={'raw_score':.6,'selective_direction':'NO_TRADE'}),patch.object(hp.legacy,'quote',return_value={'captured':t-timedelta(minutes=1)}):
            with self.assertRaisesRegex(ValueError,'stale'):hp.validate_event(e,p)
            e['selective_direction']='UP'
            with self.assertRaisesRegex(ValueError,'replay'):hp.validate_event(e,p)

if __name__=='__main__':unittest.main()

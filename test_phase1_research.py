import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


class ResearchTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('phase1_research'), 'research engine must exist')
        import phase1_research
        return phase1_research

    def frame(self, n=400):
        t=np.arange(n)*3600000+1700002800000
        p=np.exp(np.arange(n)*.001+np.sin(np.arange(n)/7)*.03)
        return {'times':t.tolist(), 'DOGE-USDT':{'close':p.tolist(),'volume':(100+np.arange(n)%20).tolist(),
                'seen':(t+3600000).tolist()}, 'BTC-USDT':{'close':(p*2).tolist(),
                'volume':(200+np.arange(n)%25).tolist(),'seen':(t+3600000).tolist()}}

    def policy(self):
        return {'train_bars':100,'validation_bars':30,'test_bars':30,'step_bars':30,
                'embargo_bars':2,'max_folds':3,'iterations':15,'learning_rate':.1,'l2':.1,
                'horizons':[1,24],'availability_mode':'candle_close_research','seed':42}

    def test_engine_exists(self): self.module()

    def test_splits_purge_label_overlap_and_embargo(self):
        m=self.module()
        splits=m.splits(400,24,self.policy())
        self.assertEqual(len(splits),3)
        for s in splits:
            self.assertLess(s['train'][-1]+24+2,s['validation'][0])
            self.assertLess(s['validation'][-1]+24+2,s['test'][0])
            self.assertLess(s['test'][-1]+24,400)
        self.assertTrue(set(splits[0]['test']).isdisjoint(splits[1]['test']))

    def test_overlapping_test_windows_rejected(self):
        p=self.policy();p['step_bars']=10
        with self.assertRaises(ValueError): self.module().splits(400,1,p)

    def test_missing_fold_is_wait_not_fake_scores(self):
        self.assertEqual(self.module().splits(40,24,self.policy()),[])

    def test_train_scaler_never_uses_test_extremes(self):
        m=self.module();x=np.array([[1.,2.],[2.,3.],[3.,4.]])
        a=m.fit(x,np.array([0,1,1]),self.policy())
        before=copy.deepcopy(a)
        m.predict(a,np.array([[1e9,-1e9]]))
        self.assertEqual(a,before)
        self.assertEqual(a['mean'],[2.,3.])

    def test_machine_epsilon_does_not_change_saved_experiment(self):
        m=self.module()
        self.assertEqual(m.encode({'weight':.12345678901231}),m.encode({'weight':.1234567890123101}))
        self.assertEqual(m.predict({'mean':[0.],'scale':[1.],'weights':[0.],'intercept':0.},[[1.]]),[.5])
        model=m.fit(np.array([[.1234567890123],[.4234567890123],[.6234567890123]]),np.array([0,1,1]),self.policy())
        for key in ('mean','scale','weights'):
            self.assertTrue(all(v==round(v,12) for v in model[key]))

    def test_future_mutation_cannot_change_earlier_features(self):
        m=self.module();f=self.frame();a=m.features(f,1,'candle_close_research')
        f['DOGE-USDT']['close'][300:]=[10000]*100
        b=m.features(f,1,'candle_close_research')
        self.assertEqual(a[200],b[200])

    def test_receipt_mode_blocks_historical_download(self):
        m=self.module();f=self.frame();last=f['times'][-1]+7200000
        f['DOGE-USDT']['seen']=[last]*400
        rows=m.features(f,1,'receipt_time')
        self.assertTrue(all(r is None for r in rows))

    def test_unknown_availability_mode_rejected(self):
        with self.assertRaises(ValueError): self.module().features(self.frame(),1,'fake_pit')

    def test_late_target_receipt_cannot_enter_training(self):
        m=self.module();f=self.frame();p=self.policy();p['availability_mode']='receipt_time'
        p['embargo_bars']=32
        s=m.splits(400,24,p)[0]
        target=s['train'][-1]+24
        f['DOGE-USDT']['seen'][target]=f['times'][s['validation'][0]]+7200000
        hr=m.experiment(f,p)['horizons']['24']['folds'][0]
        self.assertEqual(hr['status'],'WAIT_AVAILABLE_TRAINING_DATA')
        self.assertEqual(hr['models'],{})

    def test_receipt_majority_excludes_unavailable_labels(self):
        m=self.module();f=self.frame();anchor=f['times'][200]+3600000
        f['DOGE-USDT']['seen'][50]=anchor+3600000
        available=m.majority_probability(f,200,24,'receipt_time')
        d=f['DOGE-USDT']['close'];known=[int(d[q+24]>d[q]) for q in range(177) if q!=50 and q+24!=50]
        self.assertAlmostEqual(available,(1+sum(known))/(2+len(known)))

    def test_full_drop_and_single_group_family_is_fixed(self):
        m=self.module();family=m.variants()
        self.assertEqual(len(family),9)
        self.assertIn('full',family)
        for group in m.GROUPS:
            self.assertNotIn(group,family['drop_'+group])
            self.assertEqual(family['only_'+group],[group])

    def test_all_candidates_same_test_events_and_frozen_direction(self):
        m=self.module();r=m.experiment(self.frame(),self.policy())
        self.assertFalse(r['promotion_authorized'])
        for h,hr in r['horizons'].items():
            self.assertTrue(hr['folds'])
            for f in hr['folds']:
                ids=[x['event_id'] for x in f['events']]
                self.assertEqual(len(ids),len(set(ids)))
                for x in f['events']:
                    self.assertEqual(set(x['candidate_p']),set(m.variants()))
                    self.assertIn(x['frozen_direction'],[0,1])
                    self.assertIsNone(x['frozen_p'])
                    self.assertIsNone(x['net_return'])

    def test_regime_and_paired_comparisons_retain_same_denominators(self):
        m=self.module();r=m.experiment(self.frame(),self.policy())
        for hr in r['horizons'].values():
            self.assertEqual(sum(x['full']['n'] for x in hr['regime_metrics'].values()),hr['nonoverlap_n'])
            for pair in hr['paired_comparisons'].values():
                self.assertEqual(pair['wins']+pair['losses']+pair['ties'],hr['nonoverlap_n'])

    def test_later_test_labels_do_not_change_fitted_model(self):
        m=self.module();f=self.frame();a=m.experiment(f,self.policy())
        at=a['horizons']['1']['folds'][0]['test_start_index']
        f['DOGE-USDT']['close'][at+1:]=[2]*len(f['DOGE-USDT']['close'][at+1:])
        b=m.experiment(f,self.policy())
        self.assertEqual(a['horizons']['1']['folds'][0]['models'],b['horizons']['1']['folds'][0]['models'])

    def test_registration_is_idempotent_but_never_overwrites(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);record={'experiment_id':'abc','status':'RESEARCH_ONLY','x':1}
            m.register(p,record);m.register(p,record)
            record['x']=2
            with self.assertRaises(ValueError):m.register(p,record)

    def test_output_cannot_target_protected_assets(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);record={'assets':{'old/data.csv':'dummy'}}
            with self.assertRaises(ValueError):m.check_output(root,root/'old',record)
            with self.assertRaises(ValueError):m.check_output(root,root,record)
            m.check_output(root,root/'research-output',record)

    def test_snapshot_export_is_exact_and_refuses_existing_files(self):
        self.assertIsNotNone(importlib.util.find_spec('phase1_research_snapshot'))
        import phase1_research_snapshot as snapshot
        import subprocess
        a=self.module()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'repo';root.mkdir()
            subprocess.run(['git','init',str(root)],check=True,capture_output=True)
            subprocess.run(['git','config','core.autocrlf','false'],cwd=root,check=True,capture_output=True)
            (root/'old.csv').write_bytes(b'a,b\n1,2\n')
            subprocess.run(['git','add','old.csv'],cwd=root,check=True,capture_output=True)
            subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','fixture'],cwd=root,check=True,capture_output=True)
            commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
            frozen={'source_commit':commit,'assets':{'old.csv':a.audit.sha(b'a,b\n1,2\n')}}
            # Git archive applies checkout conversion unless explicitly disabled.
            subprocess.run(['git','config','core.autocrlf','true'],cwd=root,check=True,capture_output=True)
            out=Path(d)/'snapshot';snapshot.export(root,out,frozen)
            self.assertEqual((out/'old.csv').read_bytes(),b'a,b\n1,2\n')
            with self.assertRaises(ValueError):snapshot.export(root,out,frozen)


class AuditTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('phase1_research_audit'), 'asset protection must exist')
        import phase1_research_audit
        return phase1_research_audit

    def test_frozen_asset_change_and_path_traversal_rejected(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'old.csv').write_bytes(b'old')
            manifest=m.manifest(root,['old.csv'],'abc');m.verify(root,manifest)
            (root/'old.csv').write_bytes(b'changed')
            with self.assertRaises(ValueError):m.verify(root,manifest)
            with self.assertRaises(ValueError):m.manifest(root,['../escape'],'abc')

    def market(self, rows):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bars.csv'
            p.write_text('symbol,open_utc_ms,open,high,low,close,volume_base,first_ingested_utc,source\n'+rows,encoding='utf-8')
            return m.market(p,3600000,datetime(2024,1,1,tzinfo=timezone.utc))

    def row(self,stamp=1700002800000,seen='2023-11-14T23:00:00+00:00',close='1'):
        return f'DOGE-USDT,{stamp},1,2,.5,{close},100,{seen},OKX history-candles 1H confirmed\n'

    def test_duplicate_bar_rejected(self):
        with self.assertRaises(ValueError):self.market(self.row()*2)

    def test_gap_rejected(self):
        with self.assertRaises(ValueError):self.market(self.row()+self.row(1700002800000+7200000))

    def test_nan_and_invalid_ohlc_rejected(self):
        for value in ('nan','3','0'):
            with self.assertRaises(ValueError):self.market(self.row(close=value))

    def test_ingestion_before_close_rejected(self):
        with self.assertRaises(ValueError):self.market(self.row(seen='2023-01-01T00:00:00+00:00'))

    def test_future_bar_rejected(self):
        with self.assertRaises(ValueError):self.market(self.row(1900000800000))

    def test_governance_requires_exact_36_unique_items(self):
        m=self.module();items=[{'id':i,'decision':'RESEARCH'} for i in range(1,37)]
        self.assertEqual(m.catalog(items)['RESEARCH'],36)
        items[-1]['id']=1
        with self.assertRaises(ValueError):m.catalog(items)


if __name__=='__main__':unittest.main()

import copy,importlib.util,json,tempfile,unittest
from pathlib import Path
import numpy as np


class Phase2ResearchTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('phase2_research'),'phase2 engine must exist')
        import phase2_research as m
        return m

    def frame(self,n=550):
        t=np.arange(n)*3600000+1700002800000
        d=np.exp(np.arange(n)*.0002+np.sin(np.arange(n)/7)*.03)
        b=np.exp(np.arange(n)*.0001+np.cos(np.arange(n)/13)*.02)
        return {'times':t.tolist(),'DOGE-USDT':{'close':d.tolist(),'volume':(100+np.arange(n)%20).tolist(),'seen':(t+3600000).tolist()},
                'BTC-USDT':{'close':b.tolist(),'volume':(200+np.arange(n)%25).tolist(),'seen':(t+3600000).tolist()}}

    def policy(self):
        return {'train_bars':100,'validation_bars':30,'test_bars':30,'step_bars':30,'embargo_bars':2,
                'max_folds':2,'iterations':8,'learning_rate':.1,'l2':.1,'horizons':[1,24],
                'availability_mode':'candle_close_research','seed':20261014,'numpy_version':'2.3.5',
                'max_fits':600,'hypothesis_settings':[{'model':'logistic','regularization':.03},
                    {'model':'logistic','regularization':.3},{'model':'shrinkage_lda','regularization':.3},
                    {'model':'shrinkage_lda','regularization':1.0}]}

    def output_fixture(self,out):
        m=self.module();identity={'source_sha256':m.sha(b'csv'),'test_fixture':True};eid=m.sha(m.encode(identity))
        model={'mean':[0.],'scale':[1.],'weights':[.1],'intercept':0.};raw=m.encode(model);mid=m.sha(raw)
        validation={'n':1,'brier':.2};fold={'fold':0,'models':{'toy':model},'validation':{'toy':validation},'selected_candidate':'toy'}
        research={'experiment_id':eid,'identity':identity,'actual_fits':1,'horizons':{'1':{'folds':[fold]}},'production_promotion_authorized':False}
        record={'experiment_id':eid,'model_path':'models/'+mid+'.json','model_sha256':mid,'horizon_hours':1,
                'fold':0,'hypothesis_id':'toy','validation':validation,'selected_in_fold':True}
        cid=m.sha(m.encode(record));record['candidate_id']=cid
        objects={'.phase2-owner':m.OWNER,'research.json':m.encode(research),
            'registry.json':m.encode({'experiment_id':eid,'production_promotion_authorized':False,'candidate_models':[record]}),
            'receipt.json':m.encode({'experiment_id':eid,'result_sha256':m.sha(m.encode(research))}),
            'source_audit.json':m.encode({'hourly':{'sha256':identity['source_sha256']}}),
            record['model_path']:raw,'candidates/'+cid+'.json':m.encode(record)}
        for name,data in objects.items():m.write_immutable(out/name,data)
        manifest={'experiment_id':eid,'objects':{name:m.sha(data) for name,data in objects.items()}}
        (out/'manifest.json').write_bytes(m.encode(manifest))
        return record,manifest

    def test_fixed_family_has_17_features_5_groups_and_44_hypotheses(self):
        m=self.module();self.assertEqual(len(m.library.NAMES),17);self.assertEqual(len(m.library.GROUPS),5)
        self.assertEqual(len(m.library.variants()),11);self.assertEqual(len(m.hypotheses(self.policy())),44)

    def test_future_prices_and_volumes_cannot_change_prior_features(self):
        m=self.module();f=self.frame();a=m.library.rows(f,1,'candle_close_research')
        f['DOGE-USDT']['close'][301:]=[10000]*249;f['DOGE-USDT']['volume'][301:]=[999999]*249
        b=m.library.rows(f,1,'candle_close_research');self.assertEqual(a[200],b[200])

    def test_feature_lags_have_known_numeric_values(self):
        m=self.module();f=self.frame();row=m.library.rows(f,1,'candle_close_research')[200]
        expected=np.log(f['DOGE-USDT']['close'][200]/f['DOGE-USDT']['close'][128])
        self.assertAlmostEqual(row['x'][4],expected,places=11)

    def test_timestamp_gap_and_duplicate_rejected(self):
        m=self.module()
        for value in (self.frame()['times'][198],self.frame()['times'][200]):
            f=self.frame();f['times'][199]=value
            with self.assertRaises(ValueError):m.library.rows(f,1,'candle_close_research')

    def test_receipt_mode_download_is_not_historical_pit(self):
        m=self.module();f=self.frame();f['DOGE-USDT']['seen']=[f['times'][-1]+7200000]*550
        self.assertTrue(all(r is None for r in m.library.rows(f,1,'receipt_time')))

    def test_negative_volume_and_nonfinite_prices_rejected(self):
        m=self.module()
        for key,value in [('volume',-1),('close',float('nan'))]:
            f=self.frame();f['DOGE-USDT'][key][200]=value
            with self.assertRaises(ValueError):m.library.rows(f,1,'candle_close_research')

    def test_both_models_fit_train_only_scaler_and_valid_probabilities(self):
        m=self.module();x=[[1.,2.],[2.,3.],[3.,4.]]
        for setting in self.policy()['hypothesis_settings']:
            model=m.fit(setting,x,[0,1,1],self.policy());before=copy.deepcopy(model)
            probs=m.predict(model,[[1e9,-1e9],[2.,3.]])
            self.assertEqual(model,before);self.assertEqual(model['mean'],[2.,3.])
            self.assertTrue(all(0<=p<=1 and np.isfinite(p) for p in probs))

    def test_single_class_training_is_supported(self):
        m=self.module()
        for setting in self.policy()['hypothesis_settings']:
            model=m.fit(setting,[[1.],[2.],[3.]],[1,1,1],self.policy())
            self.assertTrue(all(0<p<1 for p in m.predict(model,[[4.]])))

    def test_choice_uses_validation_brier_with_deterministic_tie_break(self):
        m=self.module();self.assertEqual(m.choose({'z':{'n':2,'brier':.1},'a':{'n':2,'brier':.1}}),'a')
        self.assertEqual(m.choose({'a':{'n':2,'brier':.2},'b':{'n':2,'brier':.1}}),'b')

    def test_budget_fails_before_any_partial_experiment(self):
        m=self.module();p=self.policy();p['max_fits']=1
        with self.assertRaises(ValueError):m.experiment(self.frame(),p)

    def test_invalid_model_and_duplicate_hypotheses_rejected(self):
        m=self.module()
        for settings in ([{'model':'exec','regularization':.1}],self.policy()['hypothesis_settings']*2):
            p=self.policy();p['hypothesis_settings']=settings
            with self.assertRaises(ValueError):m.hypotheses(p)

    def test_test_mutation_cannot_change_first_fold_training_or_choice(self):
        m=self.module();f=self.frame();a=m.experiment(f,self.policy());fold=a['horizons']['1']['folds'][0]
        i=fold['windows']['test']['start_index']+1;f['DOGE-USDT']['close'][i:]=[2]*len(f['times'][i:])
        b=m.experiment(f,self.policy())['horizons']['1']['folds'][0]
        self.assertEqual(fold['models'],b['models']);self.assertEqual(fold['selected_candidate'],b['selected_candidate'])
        self.assertEqual(fold['validation'],b['validation'])

    def test_all_variants_keep_same_events_and_denominators(self):
        m=self.module();r=m.experiment(self.frame(),self.policy())
        self.assertFalse(r['production_promotion_authorized']);self.assertFalse(r['validated_70pct'])
        self.assertFalse(r['historical_holdout_pristine']);self.assertFalse(r['cost_net_return_validated'])
        for hr in r['horizons'].values():
            self.assertEqual(len(hr['validation_ranking']),44)
            for f in hr['folds']:
                self.assertEqual(f['due_test_anchors'],len(f['events'])+f['missing_feature_anchors'])
                for e in f['events']:self.assertEqual(len(e['candidate_p']),44)
            for pair in hr['same_event_baselines'].values():self.assertEqual(pair['n'],hr['nonoverlap_n'])
            self.assertEqual(sum(v['selected']['n'] for v in hr['regime_metrics'].values()),hr['nonoverlap_n'])
            self.assertEqual(len(hr['descriptive_ablation_gain']),5)

    def test_nonoverlap_phase_never_uses_labels_or_overlapping_targets(self):
        m=self.module();r=m.experiment(self.frame(),self.policy())
        for hr in r['horizons'].values():
            events=hr['nonoverlap_events']
            self.assertTrue(all(b['anchor_ms']>a['label_end_ms'] for a,b in zip(events,events[1:])))

    def test_missing_data_reports_wait_no_fake_metrics(self):
        m=self.module();p=self.policy();p['availability_mode']='receipt_time';f=self.frame()
        f['DOGE-USDT']['seen']=[f['times'][-1]+7200000]*550
        r=m.experiment(f,p)
        self.assertEqual(r['actual_fits'],0)
        for hr in r['horizons'].values():
            self.assertEqual(hr['status'],'WAIT_AVAILABLE_DATA');self.assertEqual(hr['nonoverlap_n'],0)

    def test_immutable_model_registration_rejects_conflicting_bytes(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            path=m.write_immutable(Path(tmp)/'model.json',b'one');self.assertEqual(path.read_bytes(),b'one')
            m.write_immutable(path,b'one')
            with self.assertRaises(ValueError):m.write_immutable(path,b'two')

    def test_output_protection_rejects_foreign_directory_and_repo_assets(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'personal.txt').write_text('keep')
            with self.assertRaises(ValueError):m.check_output(out)
            self.assertEqual((out/'personal.txt').read_text(),'keep')
        with self.assertRaises(ValueError):m.check_output(m.ROOT/'data'/'phase2-output')

    def test_output_symlink_rejected(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);(base/'real').mkdir()
            try:(base/'linked').symlink_to(base/'real',target_is_directory=True)
            except OSError:self.skipTest('symlink privilege unavailable')
            with self.assertRaises(ValueError):m.check_output(base/'linked')

    def test_deterministic_same_input_experiment_bytes(self):
        m=self.module();a=m.encode(m.experiment(self.frame(),self.policy()));b=m.encode(m.experiment(self.frame(),self.policy()))
        self.assertEqual(a,b)

    def test_manifest_verification_detects_tampered_model(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);record,_=self.output_fixture(out)
            self.assertTrue(m.verify_output(out))
            (out/record['model_path']).write_bytes(b'two')
            with self.assertRaises(ValueError):m.verify_output(out)

    def test_git_metadata_cannot_be_an_output_directory(self):
        m=self.module()
        with self.assertRaises(ValueError):m.check_output(m.ROOT/'.git'/'phase2-new-output')

    def test_symlink_model_cannot_pass_manifest_verification(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);record,_=self.output_fixture(out);model=out/record['model_path']
            (out/'actual.json').write_bytes(model.read_bytes());model.unlink()
            try:model.symlink_to(out/'actual.json')
            except OSError:self.skipTest('symlink privilege unavailable')
            with self.assertRaises(ValueError):m.verify_output(out)

    def test_empty_manifest_cannot_certify_missing_research(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'manifest.json').write_bytes(m.encode({'experiment_id':'unrelated','objects':{}}))
            with self.assertRaises(ValueError):m.verify_output(out)

    def test_saved_numeric_contract_reproduces_validation_tie_choice(self):
        m=self.module()
        scores={'a':{'n':2,'brier':.1000000000004},'z':{'n':2,'brier':.1000000000003}}
        self.assertEqual(m.choose(scores),'a')

    def test_manifest_cannot_omit_registered_candidate(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);record,manifest=self.output_fixture(out)
            del manifest['objects']['candidates/'+record['candidate_id']+'.json']
            (out/'manifest.json').write_bytes(m.encode(manifest))
            with self.assertRaises(ValueError):m.verify_output(out)

    def test_manifest_cannot_substitute_unrelated_experiment_identity(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);_,manifest=self.output_fixture(out);manifest['experiment_id']='a'*64
            (out/'manifest.json').write_bytes(m.encode(manifest))
            with self.assertRaises(ValueError):m.verify_output(out)


if __name__=='__main__':unittest.main()

import copy
import tempfile
import json
import subprocess
import unittest
from pathlib import Path
from datetime import timedelta
import test_learned_shadow_v1 as fixtures


class Runtime(unittest.TestCase):
    def module(self):
        import learned_shadow_runtime as runtime
        return runtime

    def fixture(self):
        f = fixtures.Shadow(); e = f.event()
        return f, e

    def test_no_issuance_before_activation(self):
        f, e = self.fixture(); m = self.module()
        self.assertEqual(m.plan(f.policy(), [], {}, m.shadow.utc('2026-10-15T00:00:00Z'))['issue'], [])

    def test_once_per_horizon_and_no_late_backfill(self):
        f, e = self.fixture(); m = self.module(); at = m.shadow.utc(e['issued_at_utc'])
        self.assertEqual(m.plan(f.policy(), [], {}, at)['issue'], [1, 24])
        self.assertEqual(m.plan(f.policy(), [e], {}, at)['issue'], [24])
        self.assertEqual(m.plan(f.policy(), [], {}, at+timedelta(minutes=40))['issue'], [])

    def test_exact_target_deadline_and_no_reconstructed_target(self):
        f, e = self.fixture(); m = self.module(); target = m.shadow.utc(e['target_utc'])
        self.assertEqual(m.plan(f.policy(), [e], {}, target)['score'], [e['event_id']])
        self.assertEqual(m.plan(f.policy(), [e], {}, m.shadow.target_deadline(e, f.policy()))['score'], [])

    def test_first_score_wins(self):
        f, e = self.fixture(); m = self.module()
        self.assertEqual(m.plan(f.policy(), [e], {e['event_id']: {}}, m.shadow.utc(e['target_utc']))['score'], [])

    def test_journal_never_overwrites_or_accepts_symlink(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git','init',tmp],check=True,capture_output=True)
            subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                            'commit','--allow-empty','-m','fixture history'],cwd=root,check=True,capture_output=True)
            path = m.append_record(root, 'events', '123_1', {'a': 1})
            with self.assertRaises(FileExistsError): m.append_record(root, 'events', '123_1', {'a': 2})
            self.assertEqual(path.read_bytes(), m.shadow.encode({'a': 1}))
            with self.assertRaises(ValueError): m.append_record(root, 'events', '../x', {})

    def test_duplicate_journal_id_fails(self):
        f, e = self.fixture(); m = self.module()
        with self.assertRaises(ValueError): m.plan(f.policy(), [e, copy.deepcopy(e)], {}, m.shadow.utc(e['issued_at_utc']))

    def test_source_witness_must_precede_clock_and_exact_bytes(self):
        m = self.module(); f, e = self.fixture(); at=m.shadow.utc(e['issued_at_utc'])
        self.assertIsNone(m.source_witness(b'a', [], lambda *args: b'a', at))

    def test_retention_config_not_proof_and_promotion_stays_closed(self):
        m = self.module()
        self.assertFalse(m.status('READY')['external_immutable_retention_verified'])
        self.assertFalse(m.status('READY')['promotion_authorized'])

    def test_runtime_manifest_rejects_changed_code(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'x.py').write_bytes(b'a')
            with self.assertRaises(ValueError): m.verify_runtime_files(root, {'x.py': '0'*64})

    def test_missing_slots_retained_in_denominator(self):
        f, e=self.fixture(); m=self.module()
        result=m.plan(f.policy(), [], {}, m.shadow.utc('2026-10-16T03:00:00Z'))
        self.assertEqual(result['matured_missing_issuance']['1'], 1)

    def test_partial_failure_keeps_first_success_and_continues(self):
        m=self.module(); saved=[]
        def action(key):
            if key==2:raise ValueError('later source unavailable')
            saved.append(key)
        self.assertEqual(m.attempt_work([1,2,3], action), {2:'ValueError: later source unavailable'})
        self.assertEqual(saved,[1,3])

    def test_existing_unwitnessed_score_requests_retry_inside_deadline(self):
        m=self.module(); f,e=self.fixture();at=m.shadow.utc(e['target_utc'])
        self.assertTrue(m.publication_state(f.policy(), [e], {e['event_id']:{}}, [], lambda *a:b'', at)['request_witness'])
        self.assertFalse(m.publication_state(f.policy(), [e], {e['event_id']:{}}, [], lambda *a:b'', m.shadow.target_deadline(e,f.policy()))['request_witness'])

    def test_forecast_locator_survives_latest100_eviction(self):
        m=self.module();f,e=self.fixture()
        bindings={e['event_id']+'_prediction':{'run_id':999}}
        self.assertEqual(m.score_locator_ids([e['event_id']],bindings),[999])

    def test_runtime_workflow_exact_upstream_and_no_new_cron(self):
        m=self.module();source=(m.ROOT/'.github/workflows/learned_shadow_live.yml').read_text()
        self.assertIn('Backfill DOGE BTC ETH 1H extreme-event research history',source)
        self.assertNotIn('cron:',source)

    def test_prediction_and_target_bindings_require_actual_exact_server_clock(self):
        m=self.module();f,e=self.fixture();issued=m.shadow.utc(e['issued_at_utc']);target=m.shadow.utc(e['target_utc'])
        score=m.shadow.score(e,f.packet(target+timedelta(seconds=1),101),f.policy())
        files={m.BASE+'/events/'+e['event_id']+'.json':m.shadow.encode(e),
               m.BASE+'/scores/'+e['event_id']+'.json':m.shadow.encode(score)}
        def server(rid,start,end):
            return {'id':rid,'repository':{'full_name':m.shadow.REPO},'path':m.shadow.WORKFLOW,
                'status':'completed','conclusion':'success','event':'workflow_dispatch','head_sha':'a'*40,
                'created_at':start.isoformat(),'updated_at':end.isoformat()}
        runs=[server(2,issued,issued+timedelta(minutes=1)),server(3,target,target+timedelta(minutes=2))]
        good=m.publication_state(f.policy(),[e],{e['event_id']:score},runs,lambda head,path:files.get(path,b''),target+timedelta(minutes=3))
        self.assertEqual(good['bindings'][e['event_id']+'_prediction']['run_id'],2)
        self.assertEqual(good['bindings'][e['event_id']+'_target']['run_id'],3)
        bad=m.publication_state(f.policy(),[e],{e['event_id']:score},runs,lambda *a:b'forged',target+timedelta(minutes=3))
        self.assertEqual(bad['bindings'],{})
        self.assertTrue(bad['request_witness'])

    def test_runtime_manifest_matches_current_original_model_package(self):
        m=self.module();manifest=json.loads((m.ROOT/m.MANIFEST).read_bytes())
        m.verify_runtime_files(m.ROOT,manifest['sha256_lf'])
        self.assertEqual(manifest['model_package_sha256'],m.shadow.digest(json.loads((m.ROOT/m.shadow.PACKAGE).read_bytes())))

import importlib.util
import json
import tempfile
import subprocess
import unittest
from pathlib import Path

class Bundle(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('learned_shadow_bundle'))
        import learned_shadow_bundle
        return learned_shadow_bundle

    def test_output_cannot_overlap_repository_assets(self):
        m=self.module()
        with self.assertRaises(ValueError):m.check_output(m.ROOT)
        with self.assertRaises(ValueError):m.check_output(m.ROOT/'data')
        m.check_output(m.ROOT/'stage2-test-output')

    def test_foreign_existing_output_cannot_be_overwritten(self):
        m=self.module()
        with tempfile.TemporaryDirectory(dir=m.ROOT,prefix='stage2-foreign-') as d:
            p=Path(d);(p/'keep.txt').write_text('old',encoding='utf-8')
            with self.assertRaises(ValueError):m.check_output(p)
            self.assertEqual((p/'keep.txt').read_text(),'old')

    def test_retention_missing_never_becomes_production_review_approval(self):
        m=self.module();r=m.restrict_review({'status':'REVIEW_PROMOTION','production_allowed':True})
        self.assertEqual(r['status'],'WAIT_EXTERNAL_RETENTION_AND_INDEPENDENT_AUDIT')
        self.assertFalse(r['production_allowed'])

    def test_missing_bundle_cannot_be_declared_registered(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises((ValueError,FileNotFoundError)):m.guard(Path(d))

    def test_raw_payload_limit_prevents_unbounded_capture(self):
        m=self.module()
        self.assertEqual(m.decode_response(b'{"code":"0"}'),{'code':'0'})
        with self.assertRaises(ValueError):m.decode_response(b'x'*2000001)

    def test_build_preflights_noncanonical_policy_before_creating_files(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/'config/learned_shadow_v1.json';path.parent.mkdir()
            source=json.loads((m.ROOT/'config/learned_shadow_v1.json').read_bytes())
            path.write_text(json.dumps(source),encoding='utf-8')
            with self.assertRaises(ValueError):m.create_package(root,source_commit='a'*40)
            self.assertFalse((root/'research/learned_shadow_v1/model_package.json').exists())

    def test_training_contract_rejects_rehashed_wrong_weights(self):
        m=self.module();expected={'models':{'1':{'full':{'weights':[.1]}}},'majority_p':{'1':.4},'training_windows':{'1':{'n':4096}}}
        wrong=json.loads(json.dumps(expected));wrong['models']['1']['full']['weights']=[.2]
        with self.assertRaises(ValueError):m.compare_training(wrong,expected)

    def test_real_bundle_rehashed_weight_tampering_fails_training_replay(self):
        m=self.module();s=m.shadow
        if not (m.ROOT/s.REGISTRY).exists():self.skipTest('fixed bundle not built yet')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in s.REGISTERED_FILES:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(m.file_bytes(m.ROOT,name))
            package=json.loads((root/s.PACKAGE).read_bytes())
            raw=m.git(m.ROOT,'show',package['training_source_commit']+':'+m.SOURCE)
            src=root/m.SOURCE;src.parent.mkdir(parents=True,exist_ok=True);src.write_bytes(raw)
            subprocess.run(['git','init',str(root)],check=True,capture_output=True)
            subprocess.run(['git','config','core.autocrlf','false'],cwd=root,check=True,capture_output=True)
            subprocess.run(['git','add',m.SOURCE],cwd=root,check=True,capture_output=True)
            subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','source fixture'],cwd=root,check=True,capture_output=True)
            package['training_source_commit']=m.git(root,'rev-parse','HEAD').decode().strip()
            def refresh():
                (root/s.PACKAGE).write_bytes(s.encode(package))
                registry=json.loads((root/s.REGISTRY).read_bytes())
                registry['file_sha256_lf']={name:s.sha(m.file_bytes(root,name)) for name in s.REGISTERED_FILES-{s.REGISTRY}}
                (root/s.REGISTRY).write_bytes(s.encode(registry))
            refresh();m.guard(root)
            package['models']['1']['full']['weights'][0]+=.01;refresh()
            with self.assertRaisesRegex(ValueError,'training replay differs'):m.guard(root)

if __name__=='__main__':unittest.main()

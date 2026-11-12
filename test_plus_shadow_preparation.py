import copy
import importlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class Preparation(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module('plus_shadow_preparation')
        except ModuleNotFoundError:
            self.fail('missing read-only shadow identity preparation module')

    def test_existing_candidate_has_exact_bindings_without_new_authority(self):
        p = self.module().prepare(ROOT)
        self.assertEqual(p['candidate_reuse']['model_identity'], '0843d2e836f0d94cd46e66644460f1a543aef460e51ad884394a5880dadf3baf')
        self.assertFalse(p['new_candidate_created'])
        draft = p['bound_review_draft']
        self.assertEqual(len(draft['identity_bindings']), 6)
        self.assertTrue(all(len(x) == 64 for x in draft['identity_bindings'].values()))
        self.assertEqual(draft['identity_bindings']['training_data_sha256'], p['training_provenance']['source_sha256'])
        self.assertEqual(p['feature_schema']['columns'], list(range(5, 17)))
        self.assertEqual(len(p['feature_schema']['names']), 12)
        self.assertIsNone(draft['observation_start_utc'])
        self.assertIsNone(draft['public_registration_witness'])
        self.assertFalse(p['new_registration_performed'])
        self.assertFalse(p['runtime_activation_authorized'])
        self.assertFalse(p['production_promotion_authorized'])
        self.assertFalse(p['p0_pass_claimed'])
        self.assertFalse(p['preparation_validation']['eligible_for_prospective_registration'])

    def test_current_data_cannot_impersonate_original_training_snapshot(self):
        with self.assertRaisesRegex(ValueError, 'training source digest'):
            self.module().prepare(ROOT, reader=lambda root, commit, path: (root/path).read_bytes())

    def test_exact_training_commit_and_path_are_used(self):
        calls = []
        def reader(root, commit, path):
            calls.append((commit, path))
            return subprocess.check_output(['git', 'show', commit+':'+path], cwd=root)
        self.module().prepare(ROOT, reader=reader)
        self.assertEqual(calls, [('14e1b27d23190bb241d3b738619c352d58d76c54', 'data/hourly_research/ohlc.csv')])

    def test_unavailable_training_history_blocks_instead_of_using_latest(self):
        def missing(*args):
            raise ValueError('historical source unavailable')
        with self.assertRaisesRegex(ValueError, 'historical source unavailable'):
            self.module().prepare(ROOT, reader=missing)

    def test_changed_registered_feature_code_blocks_preparation(self):
        module = self.module()
        registration = json.loads((ROOT/'config/phase2_forward_registration_v2.json').read_bytes())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in list(registration['sha256_lf'])+['config/phase2_forward_registration_v2.json', 'p2_plus_review.py', 'plus_shadow_preparation.py']:
                target = root/name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT/name, target)
            with (root/'phase2_factor_library.py').open('ab') as stream:
                stream.write(b'\n# unexpected change\n')
            with self.assertRaisesRegex(ValueError, 'registered source changed'):
                module.prepare(root)

    def test_package_tampering_and_authority_flags_are_rejected(self):
        module = self.module()
        original = module.prepare(ROOT)
        for change in ('binding', 'authority'):
            bad = copy.deepcopy(original)
            if change == 'binding':
                bad['bound_review_draft']['identity_bindings']['model_sha256'] = 'f'*64
            else:
                bad['runtime_activation_authorized'] = True
            with self.assertRaisesRegex(ValueError, 'preparation differs'):
                module.verify(ROOT, bad)

    def test_repeated_preparation_is_deterministic(self):
        module = self.module()
        self.assertEqual(module.prepare(ROOT), module.prepare(ROOT))


if __name__ == '__main__':
    unittest.main()

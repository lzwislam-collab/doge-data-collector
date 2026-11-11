"""A collector repair must preserve the exact frozen feature import closure."""
import ast
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from source_recovery_test_dependencies import ensure_test_dependencies

ensure_test_dependencies()

import yaml

ROOT = Path(os.environ.get('DOGE_MACRO_COMPAT_ROOT', str(Path(__file__).resolve().parent)))
FROZEN = 'fae5407be6a19bcc3ed24c80e882739cf8807ef5d5c118e05781f562decade6b'
RUNTIME = '83dd1a25b11cb7af9e3dbfd1f6b7633b9b4b27fbfcb617f656a94828927c025e'


def load_module(name):
    path = ROOT/(name+'.py')
    spec = importlib.util.spec_from_file_location('compat_'+name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow(name):
    return yaml.load((ROOT/'.github/workflows'/name).read_text(encoding='utf-8'),
                     Loader=yaml.BaseLoader)


class MacroSourceCompatibilityTests(unittest.TestCase):
    def test_original_feature_guard_really_passes_on_repository_bytes(self):
        # Execute the actual module on its own physical root. No guard/hash mocks.
        code = ("import json,sys;from pathlib import Path;"
                "sys.path.insert(0,sys.argv[1]);import feature_ablation as a;"
                "p=a.guard();print(json.dumps({'protocol_id':p['protocol_id'],"
                "'files':a.code_files(),'root':str(a.ROOT.resolve())}))")
        completed = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT.resolve())],
            cwd=ROOT, check=False, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result['root'], str(ROOT.resolve()))
        self.assertEqual(result['protocol_id'], 'DOGE_FEATURE_ABLATION_V1_20261013_0200')
        self.assertEqual(len(result['files']), 24)
        self.assertIn('macro_collector.py', result['files'])
        self.assertNotIn('macro_collector_runtime_v1.py', result['files'])

    def test_registered_import_and_actual_runtime_keep_their_exact_bytes(self):
        for name, expected in [('macro_collector.py', FROZEN),
                               ('macro_collector_runtime_v1.py', RUNTIME)]:
            with self.subTest(name=name):
                path = ROOT/name
                self.assertTrue(path.is_file(), 'production runtime entry is missing')
                self.assertEqual(hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest(), expected)

    def test_collector_entry_and_frozen_parser_share_original_data_location(self):
        self.assertTrue((ROOT/'macro_collector_runtime_v1.py').is_file())
        frozen = load_module('macro_collector')
        runtime = load_module('macro_collector_runtime_v1')
        self.assertEqual(frozen.ROOT, ROOT/'data'/'macro')
        self.assertEqual(runtime.ROOT, frozen.ROOT)
        self.assertEqual(inspect.getsource(frozen.parse_csv), inspect.getsource(runtime.parse_csv))

    def test_existing_production_workflow_runs_repaired_collector(self):
        doc = workflow('macro.yml')
        job = doc['jobs']['collect']
        collector = next(step for step in job['steps'] if step.get('id') == 'collector')
        self.assertEqual(collector['run'], 'python macro_collector_runtime_v1.py')
        self.assertIn('macro_collector_runtime_v1.py', doc['on']['push']['paths'])
        self.assertEqual(doc['on']['schedule'], [{'cron':'27 3,15 * * *'}])
        self.assertEqual(set(doc['on']), {'workflow_dispatch','schedule','push'})
        self.assertEqual(job['timeout-minutes'], '12')
        self.assertEqual(doc['concurrency'], {'group':'doge-macro-collector','cancel-in-progress':'false'})
        self.assertIn('python source_publication.py --source macro', str(job))

    def test_existing_source_ci_covers_production_entry_and_compatibility_guard(self):
        doc = workflow('test_source_recovery.yml')
        self.assertEqual(set(doc['on']), {'pull_request','push'})
        for event in ('pull_request','push'):
            self.assertIn('macro_collector_runtime_v1.py', doc['on'][event]['paths'])
        commands = '\n'.join(step.get('run','') for step in doc['jobs']['verify']['steps'])
        self.assertIn('test_macro_source_compatibility', commands)
        self.assertIn('test_auxiliary_source_refresh', commands)
        checkout = next(step for step in doc['jobs']['verify']['steps']
                        if 'actions/checkout@' in step.get('uses',''))
        sparse = checkout['with']['sparse-checkout'].splitlines()
        self.assertIn('data/stage5', sparse)
        self.assertIn('system/versions', sparse)

    def test_existing_timestamp_assertions_target_actual_production_runtime(self):
        tree = ast.parse((ROOT/'test_auxiliary_source_refresh.py').read_text(encoding='utf-8'))
        aliases = [alias.name for node in tree.body if isinstance(node, ast.Import)
                   for alias in node.names if alias.asname == 'macro']
        self.assertEqual(aliases, ['macro_collector_runtime_v1'])


if __name__ == '__main__': unittest.main()

"""Integration boundaries for the offline patch-byte experiment."""
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_runtime_publication_profile as safety


class HistoryProbeIntegrationTests(unittest.TestCase):
    setUp = safety.ProfilerSafetyTests.setUp
    git = safety.ProfilerSafetyTests.git
    commit = safety.ProfilerSafetyTests.commit
    fixture = safety.ProfilerSafetyTests.fixture
    object_bytes = safety.ProfilerSafetyTests.object_bytes

    def probe_fixture(self, body):
        self.assertIn('history_batch_experiment',
                      inspect.signature(self.driver.run_profile).parameters)
        self.fixture()
        book = self.source / 'data/p2_paired_execution_v3_1/books.jsonl'
        book.parent.mkdir(parents=True, exist_ok=True)
        book.write_text('{}\n')
        (self.source / 'git_history_batch_probe.py').write_text(
            'def run_probe(root, path, timeout_seconds=360, allow_object_directory=None):\n' +
            '\n'.join('    ' + line for line in body.splitlines()) + '\n')
        return self.commit('offline probe integration fixture')

    def run_probe(self, fixed, timeout=10):
        output = self.base / 'history.json'
        code = self.driver.run_profile(self.source, fixed, output, timeout,
                                       history_batch_experiment=True)
        return code, json.loads(output.read_text())

    def test_original_validators_and_readback_still_run_before_byte_probe(self):
        fixed = self.probe_fixture(
            "return {'status':'completed','patch_bytes_equal':True,"
            "'ordered_oids_equal':True,'commit_count':1}")
        before = self.object_bytes()
        code, report = self.run_probe(fixed)
        self.assertEqual(code, 0, report)
        self.assertEqual([p['stage'] for p in report['phases']], [
            'preflight_validate_p2', 'preflight_validate_state', 'preflight_pair_guard',
            'preflight_pair_history_guard', 'core_exact_readback', 'history_patch_byte_probe'])
        self.assertTrue(all(p['status'] == 'ok' for p in report['phases']))
        experiment = report['history_batch_experiment']
        self.assertEqual(experiment['status'], 'completed')
        self.assertTrue(experiment['state_unchanged'])
        self.assertFalse(experiment['production_validator_replaced'])
        self.assertEqual(experiment['result']['commit_count'], 1)
        self.assertTrue(report['frozen_validation_completed'])
        self.assertTrue(report['tracked_worktree_clean'])
        self.assertEqual(self.object_bytes(), before)

    def test_failed_frozen_validator_prevents_the_byte_probe(self):
        fixed = self.probe_fixture("raise AssertionError('probe must not execute')")
        (self.source / 'runtime_deadline_service.py').write_text(
            "def validate_p2(root): raise ValueError('original validator')\n")
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['error']['type'], 'ValueError')
        self.assertFalse(report.get('frozen_validation_completed', False))
        self.assertNotEqual(report['history_batch_experiment']['status'], 'completed')
        self.assertEqual(len(report['phases']), 1)

    def test_measured_runtime_overlay_is_restored_before_the_fixed_history_probe(self):
        fixed = self.probe_fixture(
            "import subprocess\n"
            "assert (root/'runtime_deadline_service.py').read_text() == 'def validate_p2(root):\\n    return None\\n'\n"
            "assert not subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=root).strip()\n"
            "assert allow_object_directory is not None\n"
            "return {'status':'completed','patch_bytes_equal':True,"
            "'ordered_oids_equal':True,'commit_count':1}")
        current_runtime = self.source / 'runtime_deadline_service.py'
        current_runtime.write_text('def validate_p2(root): return None\n# current measured implementation\n')
        measured = current_runtime.read_bytes()
        code, report = self.run_probe(fixed)
        self.assertEqual(code, 0, report)
        self.assertNotEqual(report['runtime_code_sha256'], report['fixed_runtime_code_sha256'])
        self.assertEqual(current_runtime.read_bytes(), measured)
        self.assertTrue(report['tracked_worktree_clean'])

    def test_real_probe_accepts_only_the_explicit_shared_fixed_checkout(self):
        self.probe_fixture("raise AssertionError('replaced by real module')")
        real_probe = Path(__file__).with_name('git_history_batch_probe.py')
        (self.source / real_probe.name).write_bytes(real_probe.read_bytes())
        fixed = self.commit('actual native Git probe in source')
        (self.source / 'runtime_deadline_service.py').write_text(
            'def validate_p2(root): return None\n# measured source remains outside fixed history\n')
        before = self.object_bytes()
        code, report = self.run_probe(fixed)
        self.assertEqual(code, 0, report)
        result = report['history_batch_experiment']['result']
        self.assertEqual(result['head'], fixed)
        self.assertEqual(result['commit_count'], 1)
        self.assertEqual(result['verified_patch_count'], 1)
        self.assertTrue(result['patch_bytes_equal'])
        self.assertEqual(self.object_bytes(), before)

    def test_probe_cannot_report_success_after_changing_the_p2_input(self):
        fixed = self.probe_fixture(
            "(root/path).write_text('changed\\n')\n"
            "return {'status':'completed','patch_bytes_equal':True,"
            "'ordered_oids_equal':True,'commit_count':1}")
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['history_batch_experiment']['status'], 'error')
        self.assertFalse(report['history_batch_experiment'].get('state_unchanged', False))

    def test_incomplete_probe_result_cannot_be_green(self):
        fixed = self.probe_fixture("return {'status':'completed','commit_count':1}")
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['history_batch_experiment']['status'], 'error')

    def test_probe_timeout_is_inside_the_original_process_group_deadline(self):
        fixed = self.probe_fixture("__import__('time').sleep(30)")
        code, report = self.run_probe(fixed, timeout=2)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'timeout')
        self.assertEqual(report['history_batch_experiment']['status'], 'timeout')
        self.assertEqual(report['phases'][-1]['status'], 'timeout')
        self.assertTrue(report['frozen_validation_completed'])

    def test_original_validation_timeout_does_not_claim_the_probe_started(self):
        fixed = self.probe_fixture("raise AssertionError('probe must not execute')")
        (self.source / 'runtime_deadline_service.py').write_text(
            "def validate_p2(root): __import__('time').sleep(30)\n")
        code, report = self.run_probe(fixed, timeout=2)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'timeout')
        self.assertEqual(report['history_batch_experiment']['status'], 'not_started')
        self.assertEqual([p['stage'] for p in report['phases']], ['preflight_validate_p2'])
        self.assertFalse(report.get('frozen_validation_completed', False))

    def test_cli_rejects_combining_independent_experiments(self):
        result = subprocess.run([sys.executable, str(safety.SCRIPT), '--source-sha', 'a'*40,
            '--output', str(self.base/'out.json'), '--history-batch-experiment',
            '--parallel-validation-experiment'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not allowed with argument', result.stderr)


if __name__ == '__main__':
    unittest.main()

"""Real Git checkout proofs; fixtures never execute an audit script."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    import runtime_audit_checkout_profile as profile
except ImportError:
    profile = None

PREFIX = 'data/plus_evidence_v1_0/sources/'
INPUTS = ('data/direction_v42/forward_registry.csv',
          'data/direction_v42/forward_scores.csv', 'data/quote_evidence_v2/bindings.csv',
          'data/paired_forward_v1/pairs.jsonl', 'data/hourly_research/ohlc.csv')
SCRIPTS = ('forward_validation_v2.py', 'p0_operational_audit.py', 'paired_cohort_audit.py')


class AuditCheckoutProfileTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(profile, 'checkout equivalence probe is not implemented')
        self.temp = tempfile.TemporaryDirectory(prefix='audit-checkout-proof-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.git(self.source, 'init', '-q')
        self.git(self.source, 'config', 'user.name', 'Fixture')
        self.git(self.source, 'config', 'user.email', 'fixture@example.invalid')
        self.files = {path: ('input ' + str(i) + '\n').encode() for i, path in enumerate(INPUTS)}
        self.files.update({path: b'raise AssertionError("audit must not run")\n' for path in SCRIPTS})
        self.files.update({
            'runtime_audit_worker.py': b'# original pinned worker stays unchanged\n',
            'p2_paired_execution_v3_1.py':
                b"CODE_FILES = ('frozen.py',)\nPOLICY = 'policy.json'\nSYSTEM = 'registry.json'\n",
            'frozen.py': b'# frozen producer\n',
            'policy.json': b'{"policy": 1}\n',
            'registry.json': b'{"registered": true}\n',
            'system/current_p2_execution_shadow_v3_1.json': b'{"pointer": true}\n',
            'data/plus_evidence_v1_0/sources-extra/keep.bin': b'keep\x00\xff\r\n',
            'odd\tname\n.bin': b'unchanged\x00payload\r\n',
            'runner.sh': b'#!/bin/sh\nexit 0\n',
            PREFIX + 'blobs/raw.bin': b'0123456789' * 1000,
            PREFIX + 'manifests/space name.json': b'{"source":1}\n',
        })
        for path, data in self.files.items():
            target = self.source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (self.source / 'runner.sh').chmod(0o755)
        self.commit('initial')
        self.files[PREFIX + 'blobs/raw.bin'] += b'later\n'
        (self.source / (PREFIX + 'blobs/raw.bin')).write_bytes(self.files[PREFIX + 'blobs/raw.bin'])
        self.pinned = self.commit('archive changed')
        (self.source / 'after-pinned.txt').write_bytes(b'must not enter pinned checkout\n')
        self.live_head = self.commit('later source head')
        self.helper_path = Path(__file__).with_name('runtime_audit_worker.py')
        spec = importlib.util.spec_from_file_location('fixture_real_audit_worker', self.helper_path)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)

    @staticmethod
    def git(root, *args):
        return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE)

    def commit(self, message):
        self.git(self.source, 'add', '-A')
        self.git(self.source, 'commit', '-qm', message)
        return self.git(self.source, 'rev-parse', 'HEAD').decode().strip()

    def audited_fixture(self):
        # Test-only allowlist: the actual production helper still checks real Git blobs.
        return {path: self.git(self.source, 'rev-parse', self.pinned + ':' + path).decode().strip()
                for path in SCRIPTS}

    def prepare(self, *, sparse=True):
        full, candidate = self.base / 'full', self.base / 'candidate'
        with mock.patch.dict(self.helper.AUDITED_BLOBS, self.audited_fixture(), clear=True):
            self.helper.prepare_checkout(self.source, full, self.pinned, exclude_source_archive=False)
            detail = self.helper.prepare_checkout(self.source, candidate, self.pinned,
                                                  exclude_source_archive=sparse)
        return full, candidate, detail

    def test_retained_bytes_history_original_identities_and_excluded_git_objects_are_verified(self):
        # Break caught: exclusion outside the one subtree, skipped Git-byte comparison, or wrong HEAD.
        full, candidate, detail = self.prepare()
        self.assertTrue(detail['exclude_source_archive'])
        result = profile.compare_checkouts(self.source, self.pinned, full, candidate,
                                           effective_sparse=True)
        self.assertTrue(result['retained_bytes_equal'])
        self.assertTrue(result['full_reachable_history_equal'])
        self.assertTrue(result['excluded_git_blob_bytes_verified'])
        self.assertEqual(result['commit_count'], 2)
        self.assertEqual(result['excluded_files'], 2)
        self.assertEqual(result['excluded_bytes'], 10019)
        self.assertEqual(result['retained_files'], len(self.files) - 2)
        self.assertEqual(result['candidate_materialized']['files'], len(self.files) - 2)
        self.assertEqual(result['baseline_materialized']['files'], len(self.files))
        for path in INPUTS:
            self.assertEqual(result['audit_inputs'][path]['sha256'], hashlib.sha256(self.files[path]).hexdigest())
        self.assertFalse((candidate / PREFIX).exists())
        self.assertFalse((candidate / 'after-pinned.txt').exists())
        self.assertEqual(self.git(self.source, 'rev-parse', 'HEAD').decode().strip(), self.live_head)
        self.assertEqual(self.git(self.source, 'status', '--porcelain'), b'')

    def test_changed_retained_byte_is_rejected_even_when_file_size_is_equal(self):
        full, candidate, _ = self.prepare()
        self.git(candidate, 'update-index', '--assume-unchanged', INPUTS[0])
        (candidate / INPUTS[0]).write_bytes(b'input X\n')
        with self.assertRaises(profile.ProfileError):
            profile.compare_checkouts(self.source, self.pinned, full, candidate, effective_sparse=True)

    def test_missing_retained_file_and_materialized_excluded_file_are_rejected(self):
        full, candidate, _ = self.prepare()
        path = candidate / 'odd\tname\n.bin'
        path.unlink()
        with self.assertRaises(profile.ProfileError):
            profile.compare_checkouts(self.source, self.pinned, full, candidate, effective_sparse=True)
        path.write_bytes(self.files['odd\tname\n.bin'])
        (candidate / PREFIX).mkdir(parents=True)
        (candidate / PREFIX / 'unexpected').write_bytes(b'x')
        with self.assertRaises(profile.ProfileError):
            profile.compare_checkouts(self.source, self.pinned, full, candidate, effective_sparse=True)

    def test_different_pinned_head_cannot_pass_with_same_audit_inputs(self):
        full, candidate, _ = self.prepare()
        self.git(candidate, 'checkout', '-q', '--detach', self.live_head)
        with self.assertRaises(profile.ProfileError):
            profile.compare_checkouts(self.source, self.pinned, full, candidate, effective_sparse=True)

    def test_real_helper_fallback_is_reported_without_claiming_sparse_improvement(self):
        output = self.base / 'fallback.json'
        code = profile.run_profile(self.source, self.pinned, output, timeout_seconds=20,
                                   helper_file=self.helper_path)
        report = json.loads(output.read_text())
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'fallback')
        self.assertFalse(report['sparse_optimization_verified'])
        self.assertTrue(report['equivalence']['retained_bytes_equal'])
        self.assertEqual(report['trials'][1]['helper']['mode'], 'full_checkout_fallback')
        self.assertEqual(report['actual_source_head'], self.live_head)

    def test_cli_uses_current_helper_but_keeps_pinned_worker_and_prints_exact_report(self):
        # Only the test's allowlist is adapted to the small fixture, using the real helper.
        wrapper = ('import importlib.util\n'
                   + '_spec=importlib.util.spec_from_file_location("actual_helper", '
                   + repr(str(self.helper_path)) + ')\n'
                   + '_worker=importlib.util.module_from_spec(_spec)\n_spec.loader.exec_module(_worker)\n'
                   + '_worker.AUDITED_BLOBS=' + repr(self.audited_fixture()) + '\n'
                   + 'INPUTS=_worker.INPUTS\nSCRIPTS=_worker.SCRIPTS\n'
                   + 'prepare_checkout=_worker.prepare_checkout\n')
        measured = self.source / 'runtime_audit_worker.py'
        measured.write_text(wrapper)
        current = self.commit('current candidate helper, original target remains pinned')
        output = self.base / 'cli.json'
        result = subprocess.run([sys.executable, str(Path(profile.__file__).resolve()),
            '--source-root', str(self.source), '--ref', self.pinned,
            '--helper-file', str(measured), '--output', str(output), '--timeout-seconds', '20'],
            capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(output.read_text())
        self.assertEqual(json.loads(result.stdout), report)
        self.assertEqual(report['actual_source_head'], current)
        self.assertNotEqual(report['actual_source_head'], self.pinned)
        self.assertTrue(report['candidate_helper_matches_actual_source_commit'])
        self.assertEqual(report['candidate_helper_sha256'], hashlib.sha256(wrapper.encode()).hexdigest())
        self.assertEqual(report['pinned_original_worker_sha256'], hashlib.sha256(self.files['runtime_audit_worker.py']).hexdigest())
        self.assertTrue(report['sparse_optimization_verified'])
        self.assertTrue(report['source_state_unchanged'])
        self.assertTrue(report['temporary_checkouts_removed'])
        self.assertFalse(report['audit_scripts_executed'])

    def test_retained_symlink_fallback_cannot_be_relabelled_as_sparse_equivalence(self):
        (self.source / 'retained-source-alias').symlink_to(PREFIX + 'blobs', target_is_directory=True)
        self.pinned = self.commit('retained symlink reaches excluded archive')
        full, candidate, detail = self.prepare()
        self.assertEqual(detail['mode'], 'full_checkout_fallback')
        self.assertEqual(detail['fallback_reason'], 'retained_symlink')
        result = profile.compare_checkouts(self.source, self.pinned, full, candidate, effective_sparse=False)
        self.assertEqual(result['retained_symlinks'], 1)
        with self.assertRaises(profile.ProfileError):
            profile.compare_checkouts(self.source, self.pinned, full, candidate, effective_sparse=True)

    def test_full_history_source_is_required(self):
        shallow = self.base / 'shallow'
        subprocess.run(['git', 'clone', '-q', '--depth', '1', self.source.as_uri(), str(shallow)], check=True)
        output = self.base / 'shallow.json'
        code = profile.run_profile(shallow, self.live_head, output, timeout_seconds=20,
                                   helper_file=self.helper_path)
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(output.read_text())['status'], 'error')

    @unittest.skipUnless(os.name == 'posix', 'process-group hard deadline requires POSIX')
    def test_timeout_kills_descendants_keeps_completed_trial_and_removes_workspace(self):
        # Break caught: killing only the parent, dropping partial results, or retaining the big clone.
        pid_file = self.base / 'descendant.pid'
        helper = self.base / 'slow_helper.py'
        helper.write_text('import os, subprocess, time\n'
                          + 'INPUTS=' + repr(INPUTS) + '\nSCRIPTS=' + repr(SCRIPTS) + '\n'
                          + 'def prepare_checkout(source, checkout, head, *, exclude_source_archive=True):\n'
                          + '    if exclude_source_archive:\n'
                          + '        pid=os.fork()\n'
                          + '        if pid == 0:\n'
                          + '            time.sleep(60)\n'
                          + '            os._exit(0)\n'
                          + '        open(' + repr(str(pid_file)) + ', "w").write(str(pid))\n'
                          + '        time.sleep(60)\n'
                          + '    subprocess.run(["git","clone","--shared","--no-checkout",str(source),str(checkout)],check=True,capture_output=True)\n'
                          + '    subprocess.run(["git","checkout","--detach",head],cwd=checkout,check=True,capture_output=True)\n'
                          + '    return {"clone_seconds":0.0,"checkout_seconds":0.0,"exclude_source_archive":False,"mode":"full_checkout"}\n')
        output = self.base / 'timeout.json'
        temporary_parent = self.base / 'temporary'
        temporary_parent.mkdir()
        with mock.patch.dict(os.environ, {'TMPDIR': str(temporary_parent)}), mock.patch('tempfile.tempdir', str(temporary_parent)):
            code = profile.run_profile(self.source, self.pinned, output, timeout_seconds=2,
                                       helper_file=helper)
        report = json.loads(output.read_text())
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'timeout')
        self.assertEqual(report['trials'][0]['status'], 'completed')
        self.assertEqual(report['trials'][1]['status'], 'timeout')
        self.assertTrue(pid_file.exists(), 'test must reach the real descendant before timing out')
        pid = int(pid_file.read_text())
        proc = Path('/proc') / str(pid) / 'stat'
        self.assertTrue(not proc.exists() or proc.read_text().split()[2] == 'Z')
        self.assertEqual(list(temporary_parent.iterdir()), [])

    def test_deadline_above_existing_hard_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            profile.run_profile(self.source, self.pinned, self.base / 'too-long.json',
                                timeout_seconds=361, helper_file=self.helper_path)


if __name__ == '__main__':
    unittest.main()

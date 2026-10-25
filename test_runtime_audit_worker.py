import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_deadline_service as runtime
from test_runtime_deadline_service import Adapter, Clock, START


class AuditIsolationTests(unittest.TestCase):
    def test_pending_slow_audit_does_not_occupy_capture_tick(self):
        clock = Clock()
        adapter = Adapter(clock)
        adapter.registry = [{'forecast_id': 'issued', 'horizon_hours': '1',
                             'source_hour_start_utc': START.isoformat(),
                             'target_utc': START.replace(hour=11).isoformat()}]
        original = adapter.action
        def action(name):
            if name == 'source_audit':
                adapter.calls.append(name)
                return {'status': 'pending'}
            return original(name)
        adapter.action = action
        state = {}
        runtime.cycle(adapter, state, clock)
        self.assertNotIn('source_audited_at', state)
        self.assertIn('book', adapter.calls)
        self.assertEqual(clock(), START)

    def test_actions_have_individual_elapsed_times(self):
        clock = Clock()
        adapter = Adapter(clock)
        adapter.duration['source_audit'] = 137
        out = runtime.cycle(adapter, {}, clock)
        audit = next(x for x in out['actions'] if x['stage'] == 'source_audit')
        self.assertEqual(audit['seconds'], 137)

    def test_changed_inputs_discard_completed_snapshot_without_overwriting_reports(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for p in worker.INPUTS:
                (root/p).parent.mkdir(parents=True, exist_ok=True)
                (root/p).write_text('original')
            fingerprint = worker.fingerprint(root)
            result = {'status': 'ok', 'inputs': fingerprint,
                      'outputs': {p: '{"status":"ok"}' for p in worker.OUTPUTS}}
            for p in worker.OUTPUTS:
                (root/p).parent.mkdir(parents=True, exist_ok=True)
                (root/p).write_text('old report')
            (root/worker.INPUTS[0]).write_text('new issuance')
            self.assertEqual(worker.accept(root, result)['status'], 'discarded_input_changed')
            self.assertTrue(all((root/p).read_text() == 'old report' for p in worker.OUTPUTS))

    def test_failed_audit_cannot_install_partial_outputs(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with self.assertRaises(ValueError): worker.accept(root, {'status': 'failed'})
            self.assertFalse((root/worker.OUTPUTS[0]).exists())

    def test_pending_context_uses_raw_engineering_capture_without_formal_eligibility(self):
        with tempfile.TemporaryDirectory() as name, patch.dict('os.environ', {
                'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': runtime.REPO}):
            adapter = runtime.LiveAdapter(Path(name))
            with patch.object(adapter, 'python') as command:
                result = adapter.action('book')
            self.assertFalse(result['formal_eligibility'])
            command.assert_called_once_with('p2_paired_execution_v3_1.py', 'smoke')

    def test_complete_matching_reports_are_installed_only_by_accept(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for p in worker.INPUTS:
                (root/p).parent.mkdir(parents=True, exist_ok=True)
                (root/p).write_text('original')
            result = {'status': 'ok', 'input_head': 'fixture', 'inputs': worker.fingerprint(root),
                      'outputs': {p: '{"status":"ok"}' for p in worker.OUTPUTS}, 'timings': []}
            self.assertEqual(worker.accept(root, result)['status'], 'ready')
            self.assertTrue(all(json.loads((root/p).read_text())['status'] == 'ok' for p in worker.OUTPUTS))

    def test_audit_runs_against_pinned_clone_and_never_writes_live_ledger(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)/'repo'; root.mkdir()
            for p in worker.INPUTS:
                (root/p).parent.mkdir(parents=True, exist_ok=True)
                (root/p).write_text('frozen ledger')
            for script, output in zip(worker.SCRIPTS, [None, worker.OUTPUTS[0], worker.OUTPUTS[1]]):
                code = 'from pathlib import Path\n'
                if output:
                    code += 'p=Path('+repr(output)+');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(\'{"status":"ok"}\')\n'
                (root/script).write_text(code)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root).decode().strip()
            git('init', '-q'); git('add', '.')
            git('-c', 'user.name=test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'fixture')
            head = git('rev-parse', 'HEAD')
            result = worker.audit_snapshot(root, head)
            self.assertEqual(result['status'], 'ok')
            self.assertFalse((root/worker.OUTPUTS[0]).exists())
            self.assertEqual((root/worker.INPUTS[0]).read_text(), 'frozen ledger')
            self.assertEqual(set(result['outputs']), set(worker.OUTPUTS))


if __name__ == '__main__': unittest.main()

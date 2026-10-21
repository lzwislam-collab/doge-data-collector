"""Security boundaries for the independent observer; uses only stdlib."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent


class ObserverWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / '.github/workflows/e1_observer.yml').read_text(encoding='utf-8')
        self.observe = self.text.split('  observe:\n', 1)[1].split('  ci:\n', 1)[0]
        self.ci = self.text.split('  ci:\n', 1)[1]

    def test_natural_callbacks_have_no_timer_or_self_loop(self):
        self.assertNotRegex(self.text, r'(?m)^\s*(schedule|cron):')
        callbacks = self.text.split('  workflow_run:\n', 1)[1].split('  push:\n', 1)[0]
        for name in ('Backfill DOGE BTC ETH 1H extreme-event research history',
                     'Publish DOGE E1 forward evidence', 'Witness DOGE E1 forward evidence'):
            self.assertIn(name, callbacks)
        self.assertIn('types: [completed]', callbacks)
        self.assertNotIn('Observe DOGE E1 cloud exceptions', callbacks)
        self.assertNotIn("conclusion == 'success'", self.observe)

    def test_cache_code_and_actual_design_doc_trigger_push_and_pr_ci(self):
        triggers = self.text.split('  push:\n', 1)[1].split('permissions:\n', 1)[0]
        push, pull_request = triggers.split('  pull_request:\n', 1)
        for event in (push, pull_request):
            self.assertIn("'e1_observer_cache.py'", event)
            self.assertIn("'test_e1_observer*.py'", event)
            self.assertIn("'docs/e1-cloud-alert-design-20261021.md'", event)

    def test_notifications_require_main_same_repository_non_pr(self):
        for guard in ("github.ref == 'refs/heads/main'",
                      "github.event.workflow_run.head_branch == 'main'",
                      'github.event.workflow_run.head_repository.full_name == github.repository',
                      "github.event.workflow_run.event != 'pull_request'",
                      "github.event.workflow_run.event != 'pull_request_target'"):
            self.assertIn(guard, self.observe)
        self.assertIn("github.event_name == 'workflow_dispatch'", self.observe)
        self.assertIn("github.event_name == 'workflow_run'", self.observe)
        self.assertIn("github.event_name == 'push' || github.event_name == 'pull_request'", self.ci)
        self.assertNotIn('e1_observer_notify.cjs', self.ci.split('node --test')[0])

    def test_least_privilege_and_trusted_checkout(self):
        self.assertNotRegex(self.text, r'(?m)^\s*(contents|actions): write')
        for value in ('contents: read', 'actions: read', 'issues: write',
                      'ref: main', 'fetch-depth: 0', 'persist-credentials: false',
                      'group: e1-observer-notification', 'cancel-in-progress: false', 'queue: max'):
            self.assertIn(value, self.observe)
        self.assertNotIn('download-artifact', self.text)
        self.assertNotIn('issues: write', self.ci)

    def test_probe_cannot_call_detector_or_e1_write_commands(self):
        self.assertIn('options: [inspect, probe]', self.text)
        self.assertIn("inputs.mode != 'probe'", self.observe)
        self.assertNotRegex(self.text, r'python e1_forward\.py (tick|score|confirm-registration|freeze)')
        self.assertNotIn('gh workflow run', self.text)
        self.assertNotIn('git push', self.text)
        self.assertIn('probeId:', self.observe)

    def test_evidence_cutoff_precedes_checkout_and_is_passed_only_as_env(self):
        snapshot = self.observe.index('id: snapshot')
        checkout = self.observe.index('name: Read current trusted main only')
        self.assertLess(snapshot, checkout)
        cutoff_step = self.observe[snapshot:checkout]
        self.assertIn('date -u', cutoff_step)
        self.assertIn('asof_utc=', cutoff_step)
        self.assertIn('GITHUB_OUTPUT', cutoff_step)
        scan = self.observe.split('id: scan', 1)[1].split('      - name:', 1)[0]
        self.assertIn('E1_OBSERVER_CUTOFF_UTC: ${{ steps.snapshot.outputs.asof_utc }}', scan)
        self.assertIn("inputs.mode != 'probe'", scan)
        self.assertEqual(self.observe.count('${{ steps.snapshot.outputs.asof_utc }}'), 1)

    def test_untrusted_context_is_not_in_executable_blocks(self):
        for block in re.findall(r'(?m)^        (?:run|script): \|\n((?:          .*\n|\n)+)', self.text):
            self.assertNotIn('${{', block)

    def test_failure_preservation_and_separate_ci(self):
        for item in ('continue-on-error: true', 'notification.json', 'finally', 'if: always()',
                     'retention-days: 90', 'e1-observer-output/', 'python e1_observer.py',
                     "E1_OBSERVER_ISSUE_NUMBER: '70'", 'if (error.receipt) receipt = error.receipt;'):
            self.assertIn(item, self.observe)
        for command in ('python -m unittest discover -q',
                        'python forward_validation_v2.py --integrity-only',
                        'python e1_forward.py check',
                        'node --test .github/scripts/test_e1_observer_notify.cjs'):
            self.assertIn(command, self.ci)
        self.assertIn("paths:", self.text)
        self.assertNotIn('p0_alert.cjs', self.text)


if __name__ == '__main__':
    unittest.main()

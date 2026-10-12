import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parent
class SupplementalWorkflowTests(unittest.TestCase):
    def test_hourly_capture_does_not_delay_publication_and_has_own_failure_status(self):
        t=(ROOT/'.github/workflows/okx_hourly_extreme_history.yml').read_text(encoding='utf-8')
        self.assertIn('python supplemental_evidence_audit.py --capture-execution',t)
        self.assertLess(t.index('id: early_publish'),t.index('id: supplemental_evidence'))
        self.assertLess(t.index('id: operations'),t.index('id: supplemental_evidence'))
        self.assertLess(t.index('id: supplemental_evidence'),t.index('id: persist'))
        self.assertIn("steps.supplemental_evidence.outcome != 'success'",t)
        self.assertEqual(t.count("- cron:"),1)
        for path in ('data/plus_evidence_v1_0/','doge-plus-evidence-v1-0-'):
            self.assertIn(path,t)
    def test_daily_snapshot_uses_existing_schedule_without_repeated_live_execution_capture(self):
        t=(ROOT/'.github/workflows/prediction_gate.yml').read_text(encoding='utf-8')
        self.assertIn('python supplemental_evidence_audit.py',t)
        self.assertNotIn('python supplemental_evidence_audit.py --capture-execution',t)
        self.assertEqual(t.count("- cron:"),1)
        self.assertIn("steps.supplemental_evidence.outcome != 'success'",t)
        self.assertIn('python audited_forward_refresh.py',t)
    def test_existing_ci_includes_new_evidence_regressions(self):
        t=(ROOT/'.github/workflows/test_three_risk_gates.yml').read_text(encoding='utf-8')
        for module in ('test_source_evidence_snapshot','test_evidence_retention','test_forward_execution_evidence','test_supplemental_workflow'):
            self.assertIn(module,t)
    def test_daily_attempt_receipts_survive_failed_push_in_separate_artifact(self):
        t=(ROOT/'.github/workflows/prediction_gate.yml').read_text(encoding='utf-8')
        self.assertIn('name: Preserve V1.0 evidence even if repository persistence fails',t)
        a=t.index('name: Preserve V1.0 evidence even if repository persistence fails')
        self.assertIn('if: always()',t[a:a+150])
        self.assertIn('doge-plus-evidence-v1-0-',t[a:])
        self.assertIn('data/plus_evidence_v1_0/',t[a:])
if __name__=='__main__':
    unittest.main()

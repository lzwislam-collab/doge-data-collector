import unittest
from pathlib import Path
try:
    from feature_publication_gate import requires_publication
except ImportError:
    requires_publication=None

class GateTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(requires_publication,'new-context publication gate missing')
    def test_same_hour_zero_new_contexts_has_no_new_publication_to_verify(self):
        self.assertFalse(requires_publication({'status':'ok','new_contexts':0}))
        self.assertFalse(requires_publication({'status':'WAIT_ACTIVATION','new_contexts':0}))
    def test_real_new_contexts_still_require_strict_publication(self):
        self.assertTrue(requires_publication({'status':'ok','new_contexts':2}))
    def test_missing_failed_malformed_and_contradictory_receipts_fail_closed(self):
        for row in ({},{'status':'BLOCKED','new_contexts':0},{'status':'ok','new_contexts':True},
            {'status':'ok','new_contexts':-1},{'status':'ok','new_contexts':'0'},{'status':'WAIT_ACTIVATION','new_contexts':1}):
            with self.assertRaises(ValueError):requires_publication(row)
    def test_workflow_uses_actual_frozen_bind_receipt_and_positive_gate(self):
        text=(Path(__file__).parent/'.github/workflows/okx_hourly_extreme_history.yml').read_text(encoding='utf-8')
        self.assertIn('python feature_ablation.py bind > "$RUNNER_TEMP/doge-feature-bind.json"',text)
        self.assertIn('python feature_publication_gate.py < "$RUNNER_TEMP/doge-feature-bind.json" >> "$GITHUB_OUTPUT"',text)
        self.assertIn("steps.feature_bind.outputs.needs_publication == 'true'",text)
        self.assertIn('run: python feature_ablation.py publication',text)
        self.assertEqual(text.count('cron:'),1)
if __name__=='__main__':unittest.main()

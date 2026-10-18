import unittest
from pathlib import Path

class WorkflowTests(unittest.TestCase):
    def test_independent_prepare_bind_and_readback_order(self):
        text=(Path(__file__).parent/'.github/workflows/okx_hourly_extreme_history.yml').read_text(encoding='utf-8')
        self.assertLess(text.index('python minimal_market_forward.py prepare'),text.index('python paired_forward_cohort.py issue'))
        self.assertLess(text.index('python paired_forward_cohort.py issue'),text.index('python minimal_market_forward.py bind'))
        # Locate the command in either a single-line or environment-prefixed block run.
        publication=text.index('python publication_recovery.py')
        self.assertLess(text.index('python minimal_market_forward.py bind'),publication)
        self.assertLess(publication,text.index('python minimal_market_forward.py publication'))
        self.assertEqual(text.count('cron:'),1)
        self.assertIn('git add -f data/minimal_market_forward_v1',text)
        self.assertIn("steps.minimal_audit.outcome == 'failure'",text)

if __name__=='__main__':unittest.main()

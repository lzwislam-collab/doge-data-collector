import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
try:
    import supplemental_evidence_audit as audit
except ModuleNotFoundError:
    audit=None

class SupplementalAuditTests(unittest.TestCase):
    def test_missing_sources_or_storage_never_authorize_claims(self):
        self.assertIsNotNone(audit,'supplemental integration must exist')
        with tempfile.TemporaryDirectory() as tmp:
            out=audit.run(Path(tmp),capture_execution=False)
            self.assertEqual(out['verdict'],'REVISE')
            self.assertFalse(out['real_orders_authorized'])
            self.assertEqual(out['retention_status'],'NOT_CONFIGURED')
            self.assertFalse(out['independent_retention_verified'])
            self.assertTrue((Path(tmp)/'reports/DOGE_PLUS_EVIDENCE_V1_0.md').exists())
    def test_execution_failure_is_visible_and_snapshot_still_saved(self):
        self.assertIsNotNone(audit,'supplemental integration must exist')
        with tempfile.TemporaryDirectory() as tmp:
            with patch('supplemental_evidence_audit.capture_execution_quote',side_effect=ValueError('publication audit stale')):
                out=audit.run(Path(tmp),capture_execution=True)
            self.assertEqual(out['status'],'blocked')
            self.assertIn('publication audit stale',out['execution_error'])
            self.assertEqual(out['verdict'],'REVISE')
            self.assertTrue((Path(tmp)/out['manifest_path']).exists())
if __name__=='__main__':
    unittest.main()

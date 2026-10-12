import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
try:
    import plus_evidence_version as version
except ModuleNotFoundError:
    version=None

class VersionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(version,'independent version registry must exist')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for name in version.CODE_FILES:
            (self.root/name).write_text('source\n',encoding='utf-8')
    def audit(self,passed=True):
        return {'engineering_pass':passed,'tests_passed':1,'tests_failed':0,
                'audited_at_utc':datetime.now(timezone.utc).isoformat(),
                'code_sha256_lf':version.code_hashes(self.root),
                'overall':'REVISE','real_orders_authorized':False}
    def test_rejected_audit_cannot_be_system_current(self):
        with self.assertRaisesRegex(ValueError,'audit'):
            version.register(self.root,self.audit(False))
        self.assertFalse((self.root/'system/current_plus_evidence.json').exists())
    def test_code_change_after_audit_blocks_registration(self):
        audit=self.audit()
        (self.root/version.CODE_FILES[0]).write_text('changed\n',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'code'):
            version.register(self.root,audit)
    def test_registration_is_separate_and_idempotent_without_trade_promotion(self):
        audit=self.audit()
        first=version.register(self.root,audit)
        second=version.register(self.root,audit)
        self.assertEqual(first,second)
        self.assertEqual(first['data_namespace'],'data/plus_evidence_v1_0')
        self.assertFalse(first['new_prediction_model'])
        self.assertFalse(first['real_orders_authorized'])
        self.assertEqual(first['overall'],'REVISE')
        current=json.loads((self.root/'system/current_plus_evidence.json').read_text(encoding='utf-8'))
        self.assertEqual(current['version_id'],version.VERSION_ID)
    def test_new_namespace_does_not_overwrite_legacy_data(self):
        import supplemental_evidence_audit as a
        legacy=self.root/'data/supplemental_evidence/latest.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('legacy-marker',encoding='utf-8')
        out=a.run(self.root)
        self.assertEqual(legacy.read_text(encoding='utf-8'),'legacy-marker')
        self.assertEqual(out['version_id'],version.VERSION_ID)
        self.assertTrue((self.root/'data/plus_evidence_v1_0/latest.json').exists())
if __name__=='__main__':
    unittest.main()

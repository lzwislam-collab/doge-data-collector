import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import feature_ablation as a
import feature_registration as r
class RegistrationTests(unittest.TestCase):
    def fixture(self,root):
        p=root/a.POLICY;p.parent.mkdir(parents=True);p.write_bytes(b'{"protocol_id":"future","effective_from_utc":"2099-01-01T00:00:00+00:00","fixed_logit_feature_weight":0.15,"real_orders_authorized":false}\n')
        audit={'independent_review_pass':True,'tests_failed':0,'tests_passed':1,'code_sha256_lf':{},'protocol_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        return audit
    def test_registered_bytes_and_pointer_guard(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(a,'code_files',return_value=[]):
            root=Path(temp);audit=self.fixture(root);r.register(root,audit);a.guard(root)
            pointer=root/'system/current_feature_research.json';v=json.loads(pointer.read_bytes());v['registry_sha256']='changed';pointer.write_text(json.dumps(v))
            with self.assertRaisesRegex(ValueError,'pointer'):a.guard(root)
    def test_unreviewed_or_crlf_registration_rejected(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(a,'code_files',return_value=[]):
            root=Path(temp);audit=self.fixture(root);audit['independent_review_pass']=False
            with self.assertRaisesRegex(ValueError,'audit'):r.register(root,audit)
            p=root/a.POLICY;p.write_bytes(p.read_bytes().replace(b'\n',b'\r\n'))
            with self.assertRaisesRegex(ValueError,'LF'):r.register(root,audit)

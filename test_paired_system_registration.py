import hashlib
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
import paired_forward_cohort as c
import paired_system_registration as r


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        p=json.loads((c.ROOT/c.POLICY).read_text());p['effective_from_utc']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        path=self.root/c.POLICY;path.parent.mkdir(parents=True);path.write_bytes((json.dumps(p)+'\n').encode())
        for code in c.CODE_FILES:(self.root/code).write_bytes((c.ROOT/code).read_bytes())
        self.audit={'engineering_pass':True,'independent_review_pass':True,'tests_passed':20,'tests_failed':0,
            'code_sha256_lf':c.code_hashes(self.root),'protocol_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'real_orders_authorized':False,'audited_at_utc':datetime.now(timezone.utc).isoformat()}

    def tearDown(self):self.temp.cleanup()

    def test_exact_reviewed_bytes_registered_separately(self):
        record=r.register(self.root,self.audit)
        self.assertFalse(record['real_orders_authorized']);self.assertEqual(c.guard(self.root)['status'],'frozen_future_paired_shadow')

    def test_changed_code_or_missing_review_blocks_registration(self):
        a=dict(self.audit,independent_review_pass=False)
        with self.assertRaises(ValueError):r.register(self.root,a)
        (self.root/c.CODE_FILES[0]).write_text('changed')
        with self.assertRaises(ValueError):r.register(self.root,self.audit)

    def test_runtime_mutation_of_model_or_pointer_is_blocked(self):
        r.register(self.root,self.audit)
        ptr=self.root/'system/current_paired_shadow.json';ptr.write_text('{}')
        with self.assertRaisesRegex(ValueError,'pointer'):c.guard(self.root)

    def test_late_registration_cannot_claim_prior_activation(self):
        path=self.root/c.POLICY;p=json.loads(path.read_text());p['effective_from_utc']=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat();path.write_text(json.dumps(p))
        self.audit['protocol_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'precede'):r.register(self.root,self.audit)


if __name__=='__main__':unittest.main()

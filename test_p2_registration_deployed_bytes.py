import hashlib,json,unittest
import test_p2_execution_records_v3_1 as fixtures
import p2_execution_registration_v3_1 as registration

class DeploymentBytesTests(unittest.TestCase):
    def test_windows_newline_protocol_must_be_rejected_before_registration(self):
        fixture=fixtures.RecordsTests();fixture.setUp()
        try:
            audit=fixture.make_registration();path=fixture.root/registration.p.POLICY
            raw=path.read_bytes().replace(b'\n',b'\r\n');path.write_bytes(raw)
            audit['protocol_sha256']=hashlib.sha256(raw).hexdigest()
            with self.assertRaisesRegex(ValueError,'LF'):
                registration.register(fixture.root,audit)
        finally:fixture.tearDown()

if __name__=='__main__':unittest.main()

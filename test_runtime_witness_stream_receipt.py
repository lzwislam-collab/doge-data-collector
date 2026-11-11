"""Witness acceleration must prove fresh real reads and complete cleanup."""
import contextlib,io,json,unittest
import runtime_audit_pipeline_profile as profile
import runtime_audit_worker as worker

class WitnessStreamReceiptTests(unittest.TestCase):
    def setUp(self):
        self.base=dict(kind='runtime_publication_state_validation',mode='native_batch',status='ok',validation_status='ok',native_batch_calls=1)
        self.row=dict(kind='runtime_witness_git_stream',implementation='witness_fresh_git_blob_stream_v1',mode='fresh_sequential_blobs',status='ok',installed=True,requests=4,verified_reads=4,processes=1,fallback_calls=0,reaped=True,reader_joined=True,disabled=False,module_sha256='c'*64)
    def check(self,*rows,stage='forward_validation_v2.py'):
        return profile.validate_native_diagnostics('\n'.join(map(json.dumps,rows)),stage,expected_witness_sha256='c'*64)
    def test_exact_one_authenticated_fresh_witness_receipt_required(self):
        self.assertIn(self.row,self.check(self.base,self.row))
        for rows in ((self.base,),(self.base,self.row,self.row)):
            with self.assertRaises(profile.ProfileError):self.check(*rows)
    def test_drift_fallback_unused_or_incomplete_cleanup_cannot_pass(self):
        for change in ({'module_sha256':'d'*64},{'requests':0},{'requests':True},{'verified_reads':3},
                       {'processes':2},{'installed':False},{'disabled':True},{'fallback_calls':1},
                       {'reaped':False},{'reader_joined':False},{'status':'fallback'},{'secret':'PRIVATE_TOKEN'}):
            with self.subTest(change=change),self.assertRaises(profile.ProfileError):self.check(self.base,dict(self.row,**change))
    def test_worker_forwards_only_bounded_authenticated_matching_stage_receipt(self):
        def forwarded(row,stage='forward_validation_v2.py',expected='c'*64):
            out=io.StringIO()
            with contextlib.redirect_stdout(out):worker.forward_transport_diagnostics(json.dumps(row),stage,expected_witness_sha256=expected)
            return out.getvalue()
        for stage in ('forward_validation_v2.py','p0_operational_audit.py'):
            self.assertEqual(json.loads(forwarded(self.row,stage))['source_kind'],'runtime_witness_git_stream')
        for change in ({'module_sha256':'d'*64},{'requests':True},{'verified_reads':3},{'reaped':False},{'secret':'PRIVATE_TOKEN'}):
            self.assertEqual(forwarded(dict(self.row,**change)),'')
        self.assertEqual(forwarded(self.row,'paired_cohort_audit.py'),'')
        self.assertEqual(forwarded(self.row,expected=None),'')
    def test_duplicate_member_is_not_accepted_as_clean_receipt(self):
        body=json.dumps(self.base)+'\n'+json.dumps(self.row)[:-1]+',"fallback_calls":0}'
        with self.assertRaises(profile.ProfileError):
            profile.validate_native_diagnostics(body,'forward_validation_v2.py',expected_witness_sha256='c'*64)

if __name__=='__main__':unittest.main()

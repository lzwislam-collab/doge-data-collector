"""Actual fresh Git usage is required; forged, fallback or drifting receipts fail."""
import contextlib,importlib.util,io,json,sys,types,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
profile=load('stream_receipt_profile',HERE/'runtime_audit_pipeline_profile.py')
if 'runtime_process' not in sys.modules:sys.modules['runtime_process']=load('runtime_process',HERE/'runtime_process.py')
worker=load('stream_receipt_worker',HERE/'runtime_audit_worker.py')
class PairedStreamReceiptTests(unittest.TestCase):
    def setUp(self):
        base={'kind':'runtime_publication_state_validation','mode':'native_batch','status':'ok','validation_status':'ok','native_batch_calls':1}
        pair=dict(base,kind='runtime_paired_history_validation')
        canonical={'kind':'runtime_canonical_json_transport','implementation':'paired_canonical_utf8_lru_v1','mode':'canonical_bytes_lru','status':'ok','module_sha256':'a'*64,'calls':2,'hits':1,'misses':1,'fallback_calls':0,'sha256_calls':2,'encoded_calls':1,'evictions':0,'entries':1,'cached_bytes':10}
        self.base=(base,pair,canonical)
        self.row={'kind':'runtime_paired_git_stream','implementation':'paired_fresh_git_blob_stream_v1','mode':'fresh_sequential_blobs','status':'ok','installed':True,'requests':3,'verified_reads':3,'processes':1,'fallback_calls':0,'reaped':True,'reader_joined':True,'disabled':False,'module_sha256':'b'*64}
    def check(self,*rows):
        return profile.validate_native_diagnostics('\n'.join(json.dumps(row) for row in rows),'paired_cohort_audit.py',expected_launcher_sha256='a'*64,expected_stream_sha256='b'*64)
    def test_real_profile_requires_exact_one_authenticated_active_complete_stream_receipt(self):
        self.assertIn(self.row,self.check(*self.base,self.row))
        for rows in (self.base,self.base+(self.row,self.row)):
            with self.assertRaises(profile.ProfileError):self.check(*rows)
    def test_fallback_drift_no_usage_bad_hash_counts_or_cleanup_never_pass(self):
        changes=({'module_sha256':'c'*64},{'requests':0},{'requests':True},{'verified_reads':2},
                 {'processes':2},{'installed':False},{'disabled':True},{'fallback_calls':1},
                 {'reaped':False},{'reader_joined':False},{'status':'error'},{'secret':'PRIVATE_TOKEN'})
        for change in changes:
            with self.subTest(change=change),self.assertRaises(profile.ProfileError):self.check(*self.base,dict(self.row,**change))
    def test_duplicate_json_members_cannot_forge_clean_receipt(self):
        text='\n'.join(json.dumps(row) for row in self.base)+'\n'+json.dumps(self.row)[:-1]+',"fallback_calls":0}'
        with self.assertRaises(profile.ProfileError):profile.validate_native_diagnostics(text,'paired_cohort_audit.py',expected_launcher_sha256='a'*64,expected_stream_sha256='b'*64)
    def test_worker_forwards_only_exact_real_helper_receipt_for_paired(self):
        def forwarded(row,stage='paired_cohort_audit.py',expected='b'*64):
            out=io.StringIO()
            with contextlib.redirect_stdout(out):worker.forward_transport_diagnostics(json.dumps(row),stage,expected_stream_sha256=expected)
            return out.getvalue()
        self.assertEqual(json.loads(forwarded(self.row))['source_kind'],'runtime_paired_git_stream')
        for row in (dict(self.row,secret='PRIVATE_TOKEN'),dict(self.row,requests=True),dict(self.row,module_sha256='c'*64),dict(self.row,disabled=True),dict(self.row,reaped=False),dict(self.row,verified_reads=2)):
            self.assertEqual(forwarded(row),'')
        self.assertEqual(forwarded(self.row,'p0_operational_audit.py'),'')
        self.assertEqual(forwarded(self.row,expected=None),'')

if __name__=='__main__':unittest.main(verbosity=2)

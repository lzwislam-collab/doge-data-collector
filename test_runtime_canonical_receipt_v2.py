import json,unittest
import runtime_audit_pipeline_profile as profile

class HybridReceiptTests(unittest.TestCase):
    def rows(self):
        base=dict(kind='runtime_publication_state_validation',mode='native_batch',status='ok',validation_status='ok',native_batch_calls=1)
        row=dict(kind='runtime_canonical_json_transport',implementation='paired_canonical_utf8_lru_v2_fit1h_direct',
                 mode='canonical_bytes_lru',status='ok',module_sha256='a'*64,calls=5,hits=1,misses=2,direct_calls=2,
                 fallback_calls=0,sha256_calls=5,encoded_calls=2,evictions=0,entries=2,cached_bytes=100)
        return base,dict(base,kind='runtime_paired_history_validation'),row
    def check(self,rows):
        return profile.validate_native_diagnostics('\n'.join(map(json.dumps,rows)),
                  'paired_cohort_audit.py',expected_launcher_sha256='a'*64)
    def test_direct_hashes_are_counted_separately_without_fake_cache_hits(self):
        self.assertEqual(len(self.check(self.rows())),3)
    def test_fresh_sha_and_complete_accounting_still_required(self):
        base,pair,row=self.rows()
        for change in ({'direct_calls':True},{'direct_calls':-1},{'direct_calls':1},{'sha256_calls':3},
                       {'fallback_calls':1},{'hits':0},{'encoded_calls':0},
                       {'misses':0,'direct_calls':4,'encoded_calls':0},
                       {'entries':128},{'evictions':999999999},{'evictions':2},{'cached_bytes':0},
                       {'implementation':'paired_canonical_utf8_lru_v1'}):
            with self.subTest(change=change),self.assertRaises(profile.ProfileError):
                self.check((base,pair,dict(row,**change)))
if __name__=='__main__':unittest.main()

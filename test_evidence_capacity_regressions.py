"""Exact historical bytes survive bounded archive transport and source reuse."""
import hashlib,io,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import evidence_readiness_v2 as readiness
import oss_inventory_retention as inventory

class SourceReuse(unittest.TestCase):
    def test_sorted_appends_restore_exact_original_not_current_history(self):
        original=b'symbol,time\r\nBTC,1\r\nBTC,2\r\nDOGE,1\r\nETH,1\r\n'
        current=b'symbol,time\r\nBTC,1\r\nBTC,2\r\nBTC,3\r\nDOGE,1\r\nDOGE,2\r\nETH,1\r\nETH,2\r\n'
        reference=readiness.source_reference(current,original)
        self.assertEqual(readiness.restore_source(current,reference),original)
        self.assertEqual(reference['sha256'],hashlib.sha256(original).hexdigest())
        self.assertEqual(reference['byte_ranges'],[[0,27],[34,8],[50,7]])

    def test_changed_historical_byte_requires_storing_original(self):
        self.assertIsNone(readiness.source_reference(b'h\nBTC,2\n',b'h\nBTC,1\n'))

    def test_existing_prefix_descriptor_remains_compatible(self):
        ref=readiness.source_reference(b'h\nx\ny\n',b'h\nx\n')
        self.assertEqual(ref['prefix_bytes'],4)
        self.assertEqual(readiness.restore_source(b'h\nx\ny\n',ref),b'h\nx\n')

    def test_duplicate_lines_use_later_occurrence_without_changing_bytes(self):
        ref=readiness.source_reference(b'h\nx\nz\nx\ny\n',b'h\nx\nx\ny\n')
        self.assertEqual(readiness.restore_source(b'h\nx\nz\nx\ny\n',ref),b'h\nx\nx\ny\n')

    def test_corrupt_range_or_digest_is_rejected(self):
        good={'source_path':'data/hourly_research/ohlc.csv','byte_ranges':[[0,2]],'sha256':hashlib.sha256(b'h\n').hexdigest()}
        for ref in [dict(good,byte_ranges=[[0,99]]),dict(good,byte_ranges=[[-1,2]]),
                    dict(good,byte_ranges=[[0,2],[1,1]]),dict(good,byte_ranges=[[False,2]]),
                    dict(good,prefix_bytes=True),dict(good,sha256='0'*64)]:
            with self.subTest(ref=ref),self.assertRaises(ValueError):readiness.restore_source(b'h\nx\n',ref)

class Transport(unittest.TestCase):
    class Response(io.BytesIO):
        def read(self,n=-1):
            if n<0 or n>1024*1024:raise AssertionError('artifact transport must stream bounded reads')
            return super().read(n)

    def transfer(self,raw,expected=None,sha=None):
        artifact={'id':7,'size_in_bytes':len(raw) if expected is None else expected,'digest':'sha256:'+(sha or hashlib.sha256(raw).hexdigest())}
        response=self.Response(raw)
        class Opener:
            def open(self,*args,**kwargs):return response
        with tempfile.TemporaryDirectory() as d,patch.dict(inventory.os.environ,{'GITHUB_TOKEN':'test-token'}),patch.object(inventory.urllib.request,'build_opener',return_value=Opener()):
            path=Path(d)/'artifact.zip'
            result=inventory.download_artifact(artifact,path)
            self.assertEqual(result,path)
            self.assertEqual(path.read_bytes(),raw)

    def test_outer_container_can_exceed_object_cap_without_raising_object_cap(self):
        # Small equivalent of a 508 MiB container holding individually bounded objects.
        with patch.object(inventory,'LIMIT',64):self.transfer(b'archive-container'*100)
        self.assertEqual(inventory.LIMIT,256*1024*1024)

    def test_actual_published_inventory_reaches_stream_without_relaxing_object_cap(self):
        artifact={'id':12581914557,'size_in_bytes':1410368543,
                  'digest':'sha256:d6974f86bfffe7365e6d43f90e6b9cfea194c380acf493100c3959084c48a1d2'}
        with tempfile.TemporaryDirectory() as d,patch.object(inventory,'github_response',side_effect=OSError('stream probe')) as response:
            target=Path(d)/'inventory.zip'
            with self.assertRaisesRegex(OSError,'stream probe'):inventory.download_artifact(artifact,target)
            response.assert_called_once()
            self.assertFalse(target.exists())
        self.assertEqual(inventory.LIMIT,256*1024*1024)

    def test_transport_rejects_size_mismatch_and_hash_mismatch(self):
        for expected,sha in [(4,None),(3,'0'*64)]:
            with self.subTest(expected=expected),self.assertRaises(ValueError):self.transfer(b'abc',expected,sha)

    def test_transport_rejects_container_above_existing_extraction_budget(self):
        with patch.object(inventory,'ARTIFACT_LIMIT',2),self.assertRaises(ValueError):self.transfer(b'abc')

    def test_hash_rejection_removes_only_new_partial_file(self):
        class Opener:
            def open(self,*args,**kwargs):return Transport.Response(b'abc')
        artifact={'id':7,'size_in_bytes':3,'digest':'sha256:'+'0'*64}
        with tempfile.TemporaryDirectory() as d,patch.dict(inventory.os.environ,{'GITHUB_TOKEN':'test'}),patch.object(inventory.urllib.request,'build_opener',return_value=Opener()):
            target=Path(d)/'archive.zip'
            with self.assertRaises(ValueError):inventory.download_artifact(artifact,target)
            self.assertFalse(target.exists())
            target.write_bytes(b'existing')
            with self.assertRaises(FileExistsError):inventory.download_artifact(artifact,target)
            self.assertEqual(target.read_bytes(),b'existing')

if __name__=='__main__':unittest.main()

"""Offline negative acceptance tests for the 3987056 PR75 review."""
import io,json,os,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import oss_inventory_retention as m

class InnerCodecBudgets(unittest.TestCase):
 def test_unsupported_inner_compression_rejected_before_frozen_parse(self):
  for codec in (zipfile.ZIP_BZIP2,zipfile.ZIP_LZMA):
   with self.subTest(codec=codec),tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'part.zip'
    with zipfile.ZipFile(path,'w',compression=codec) as archive:
     archive.writestr('bundle_manifest.json',b'{"objects":{}}');archive.writestr('data/a',b'x')
    with self.assertRaisesRegex(ValueError,'compression'):m.validate_staged_packages(tmp)

 def test_store_and_deflate_remain_supported(self):
  for codec in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
   with self.subTest(codec=codec),tempfile.TemporaryDirectory() as tmp:
    with zipfile.ZipFile(Path(tmp)/'part.zip','w',compression=codec) as archive:
     archive.writestr('bundle_manifest.json',b'{"objects":{}}');archive.writestr('data/a',b'x')
    m.validate_staged_packages(tmp)
 def test_codec_rejection_precedes_frozen_verifier_and_single_semantics(self):
  from test_pr75_offline_completion import single_fixture
  import pit_retention_preparation as frozen
  for codec in (zipfile.ZIP_BZIP2,zipfile.ZIP_LZMA):
   with self.subTest(codec=codec),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);outer,artifact,run,prior=single_fixture(root)
    with zipfile.ZipFile(outer) as z:raw=z.read('bundles/'+prior['bundle_sha256']+'.zip')
    stream=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as old,zipfile.ZipFile(stream,'w',compression=codec) as new:
     for name in old.namelist():new.writestr(name,old.read(name))
    raw=stream.getvalue();sha=m.digest(raw);prior.update(bundle_sha256=sha,bundle_path='data/pit_retention_preparation_v1/bundles/'+sha+'.zip',byte_count=len(raw))
    base=root/'data/pit_retention_preparation_v1';(base/'latest.json').write_text(json.dumps(prior))
    with zipfile.ZipFile(outer,'w') as z:z.writestr('latest.json',json.dumps(prior));z.writestr('bundles/'+sha+'.zip',raw)
    digest=m.digest(outer.read_bytes());artifact.update(size_in_bytes=outer.stat().st_size,digest='sha256:'+digest)
    (base/'artifact_publication.json').write_text(json.dumps({'bundle_sha256':sha,'artifact_id':1,'run_id':2,'artifact_archive_sha256':digest}))
    with patch.object(m,'github',side_effect=lambda p:artifact if '/artifacts/' in p else run),patch.object(m,'github_response',side_effect=lambda p:outer.open('rb')),patch.object(frozen,'verify_inventory_bundle') as verifier,patch.object(m,'verify_single_inventory') as semantic:
     with self.assertRaisesRegex(ValueError,'compression'):m.stage(root)
     verifier.assert_not_called();semantic.assert_not_called()
    self.assertFalse((root/'oss-inventory-stage').exists());self.assertEqual(list(root.glob('oss-artifact-*')),[])
 def test_encrypted_member_flag_rejected_during_preflight(self):
  import struct
  with tempfile.TemporaryDirectory() as tmp:
   stream=io.BytesIO()
   with zipfile.ZipFile(stream,'w') as z:z.writestr('bundle_manifest.json',b'{}')
   raw=bytearray(stream.getvalue());directory=raw.index(b'PK\x01\x02')
   struct.pack_into('<H',raw,6,struct.unpack_from('<H',raw,6)[0]|1)
   struct.pack_into('<H',raw,directory+8,struct.unpack_from('<H',raw,directory+8)[0]|1)
   (Path(tmp)/'part.zip').write_bytes(raw)
   with self.assertRaisesRegex(ValueError,'encryption'):m.validate_staged_packages(tmp)

 def test_both_supported_codecs_pass_full_single_package_semantics(self):
  from test_pr75_offline_completion import run_single
  for mode in (None,'store'):
   with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);ready,_=run_single(root,mode);self.assertTrue(ready)
    self.assertEqual(m.prepare_upload_inventory(root)['prior']['objects'],1)

class EmptyEvidenceFailureMatrix(unittest.TestCase):
 def test_remote_non404_errors_do_not_use_local_empty_blob_proof(self):
  from urllib.error import HTTPError
  import plus_shadow as report
  for status in (401,403,429,500):
   with self.subTest(status=status):
    reader=report.ReportEvidenceReader(Path('.'),lambda h,p:(_ for _ in ()).throw(HTTPError('https://example.invalid',status,'',{},None)))
    with patch.object(report.subprocess,'run') as git:
     with self.assertRaises(report.ReportEvidenceReadError) as error:reader('a'*40,'empty.json')
     self.assertEqual(error.exception.diagnostic['classification'],'REMOTE_EVIDENCE_UNAVAILABLE');git.assert_not_called()
 def test_failed_empty_blob_type_probe_remains_unverified_and_uncached(self):
  from types import SimpleNamespace as NS
  import plus_shadow as report
  reader=report.ReportEvidenceReader(Path('.'),lambda h,p:b'')
  replies=[NS(returncode=0),NS(returncode=0,stdout=b'100644 blob id\tempty.json\0'),NS(returncode=0,stdout=b'0'),NS(returncode=1,stdout=b'')]
  with patch.object(report.subprocess,'run',side_effect=replies):
   with self.assertRaises(report.ReportEvidenceReadError):reader('a'*40,'empty.json')
  self.assertFalse(reader.cache)

class OptimizedAcceptance(unittest.TestCase):
 def child(self,code):
  result=subprocess.run([sys.executable,'-B','-O','-c',code],cwd=Path(__file__).parent,capture_output=True,text=True,encoding='utf-8')
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_resource_boundary_rejects_missing_expansion_under_optimization(self):
  self.child("""
from unittest.mock import patch
import offline_inventory_boundary as m
with patch.object(m.inventory,'extract_artifact',return_value=None):
 try:m.stress(False)
 except ValueError:raise SystemExit(0)
raise SystemExit('incorrectly accepted missing expansion')
""")
 def test_inventory_stress_rejects_false_ready_under_optimization(self):
  self.child("""
import json
from unittest.mock import patch
import offline_inventory_stress as m
def false_stage(root):
 output=root/'oss-retention-output';output.mkdir()
 (output/'source.json').write_text(json.dumps({'inventory_objects':0,'inventory_capacity':{}}))
 return False
with patch.object(m.inventory,'stage',side_effect=false_stage):
 try:m.stress(2,1)
 except ValueError:raise SystemExit(0)
raise SystemExit('incorrectly accepted false ready')
""")

class AdditionalAcceptanceFailures(unittest.TestCase):
 child=OptimizedAcceptance.child
 def test_resource_rejects_equal_total_wrong_file_sizes(self):
  self.child("""
from unittest.mock import patch
import offline_inventory_boundary as m
def wrong(raw,destination):
 for name,size in [('container-000.zip',0),('container-001.zip',2*1024**2),('metadata-000.json',1024**2),('metadata-001.json',1024**2)]:
  with (destination/name).open('wb') as f:f.truncate(size)
with patch.object(m.inventory,'extract_artifact',side_effect=wrong):
 try:m.stress(False)
 except ValueError:raise SystemExit(0)
raise SystemExit('incorrectly accepted redistributed sizes')
""")
 def test_resource_rejects_equal_total_wrong_file_names(self):
  self.child("""
from unittest.mock import patch
import offline_inventory_boundary as m
def wrong(raw,destination):
 for i in range(4):
  with (destination/('wrong'+str(i))).open('wb') as f:f.truncate(1024**2)
with patch.object(m.inventory,'extract_artifact',side_effect=wrong):
 try:m.stress(False)
 except ValueError:raise SystemExit(0)
raise SystemExit('incorrectly accepted wrong names')
""")
 def test_resource_rejects_wrong_fixture_transport_descriptor(self):
  self.child("""
from unittest.mock import patch
import offline_inventory_boundary as m
original=m.sparse_zip
def wrong(*a,**k):
 result=original(*a,**k);result['size']-=1;return result
with patch.object(m,'sparse_zip',side_effect=wrong):
 try:m.stress(False)
 except ValueError:raise SystemExit(0)
raise SystemExit('incorrectly accepted wrong fixture size')
""")
 def test_inventory_rejects_wrong_capacity_under_optimization(self):
  self.child("""
import json
from unittest.mock import patch
import offline_inventory_stress as m
def wrong(root):
 output=root/'oss-retention-output';output.mkdir()
 (output/'source.json').write_text(json.dumps({'inventory_objects':2,'inventory_capacity':{}}))
 return True
with patch.object(m.inventory,'stage',side_effect=wrong):
 try:m.stress(2,1)
 except ValueError:raise SystemExit(0)
raise SystemExit('incorrectly accepted wrong capacity')
""")
 def test_inventory_rejects_private_staging_residue_under_optimization(self):
  self.child("""
import json
from unittest.mock import patch
import offline_inventory_stress as m
def wrong(root):
 output=root/'oss-retention-output';output.mkdir();(root/'oss-artifact-leftover').mkdir()
 capacity=m.inventory.artifact_capacity(root.parent/'fixture/artifact.zip')
 (output/'source.json').write_text(json.dumps({'inventory_objects':2,'inventory_capacity':capacity}))
 return True
with patch.object(m.inventory,'stage',side_effect=wrong):
 try:m.stress(2,1)
 except ValueError:raise SystemExit(0)
raise SystemExit('incorrectly accepted private residue')
""")
 def test_resource_cleanup_failure_never_returns_success(self):
  self.child("""
from unittest.mock import patch
import offline_inventory_boundary as m
original=m.tempfile.TemporaryDirectory;owned=[]
class Held:
 def __init__(self,*a,**k):self.real=original(*a,**k);owned.append(self.real)
 def __enter__(self):return self.real.__enter__()
 def __exit__(self,*a):return False
try:
 with patch.object(m.tempfile,'TemporaryDirectory',Held):
  try:m.stress(False)
  except ValueError:raise SystemExit(0)
 raise SystemExit('incorrectly accepted cleanup failure')
finally:
 for temporary in owned:temporary.cleanup()
""")

if __name__=='__main__':unittest.main()

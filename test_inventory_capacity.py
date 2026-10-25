"""Offline capacity and private-staging regressions; no provider calls."""
import io,json,tempfile,unittest,zipfile,hashlib,os
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace as NS
import oss_inventory_retention as m

def directory_link(target,link):
 if os.name=='nt':
  import _winapi
  _winapi.CreateJunction(str(target),str(link))
 else:link.symlink_to(target,target_is_directory=True)

class CapacityTests(unittest.TestCase):
 def test_supported_expanded_max_includes_all_metadata(self):
  entries=[NS(filename=str(i)+'.zip',file_size=m.STORED_ZIP_LIMIT,is_dir=lambda:False) for i in range(64)]
  entries += [NS(filename=str(i)+'.json',file_size=m.INVENTORY_METADATA_LIMIT,is_dir=lambda:False) for i in range(448)]
  plan=m.inventory_capacity(entries,21*1024**3)
  self.assertEqual(plan['expanded_bytes'],20*1024**3)
  self.assertEqual(plan['peak_additional_disk_bytes'],41*1024**3+m.DISK_RESERVE)
  self.assertGreaterEqual(m.ARTIFACT_LIMIT,21*1024**3)
  self.assertGreaterEqual(m.ARTIFACT_EXPANDED_LIMIT,20*1024**3)
 def test_65th_zip_is_rejected_even_when_tiny(self):
  entries=[NS(filename=str(i)+'.zip',file_size=1,is_dir=lambda:False) for i in range(65)]
  with self.assertRaisesRegex(ValueError,'64'):m.inventory_capacity(entries,65)
 def test_budget_rejects_exact_plus_one(self):
  with self.assertRaises(ValueError):m.inventory_capacity([],m.ARTIFACT_LIMIT+1)
  entries=[NS(filename='a.json',file_size=m.INVENTORY_METADATA_LIMIT+1,is_dir=lambda:False)]
  with self.assertRaises(ValueError):m.inventory_capacity(entries,1)
 def test_destination_ancestor_link_cannot_write_outside(self):
  with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as outside:
   root=Path(tmp);link=root/'linked';directory_link(Path(outside),link)
   stream=io.BytesIO()
   with zipfile.ZipFile(stream,'w') as z:z.writestr('a.json',b'{}')
   with self.assertRaises(ValueError):m.extract_artifact(stream.getvalue(),link/'nested')
   self.assertEqual(list(Path(outside).iterdir()),[])
 def fixture(self,root):
  base=root/'data/pit_retention_preparation_v1';base.mkdir(parents=True)
  prior={'bundle_sha256':'a'*64,'objects':1,'prepared_at_utc':'2026-10-24T00:00:00Z'}
  (base/'latest.json').write_text(json.dumps(prior));(base/'artifact_publication.json').write_text(json.dumps({'bundle_sha256':'a'*64,'artifact_id':1,'run_id':2,'artifact_archive_sha256':'b'*64}))
  artifact={'id':1,'size_in_bytes':1,'digest':'sha256:'+'b'*64,'expired':False,'workflow_run':{'id':2}}
  run={'id':2,'head_branch':'main','event':'push','repository':{'full_name':'lzwislam-collab/doge-data-collector'},'path':'.github/workflows/okx_hourly_extreme_history.yml','head_sha':'c'*40,'html_url':'https://example.invalid/2'}
  def get(path):return artifact if '/artifacts/' in path else run
  def download(artifact,path):
   with zipfile.ZipFile(path,'w') as z:z.writestr('latest.json',json.dumps(prior))
   return path
  return get,download
 def test_post_extract_validation_failure_leaves_no_staging(self):
  import pit_retention_preparation as pit
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);get,download=self.fixture(root)
   with patch.object(m,'github',side_effect=get),patch.object(m,'download_artifact',side_effect=download),patch.object(m,'validate_staged_packages',side_effect=ValueError('invalid inner package')):
    with self.assertRaises(ValueError):m.stage(root)
   self.assertFalse((root/'oss-inventory-stage').exists())
   self.assertEqual(list(root.glob('oss-artifact-*')),[])
 def test_stage_root_link_is_rejected_before_download(self):
  with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as outside:
   root=Path(tmp);get,download=self.fixture(root);directory_link(Path(outside),root/'oss-inventory-stage')
   with patch.object(m,'github',side_effect=get),patch.object(m,'download_artifact') as down:
    with self.assertRaises(ValueError):m.stage(root)
    down.assert_not_called()
   self.assertEqual(list(Path(outside).iterdir()),[])

class BoundaryEvidenceTests(unittest.TestCase):
 def test_disk_equality_passes_and_one_byte_short_rejects(self):
  with tempfile.TemporaryDirectory() as tmp:
   with patch.object(m.shutil,'disk_usage',return_value=NS(free=100+m.DISK_RESERVE)):m.disk_budget(tmp,100)
   with patch.object(m.shutil,'disk_usage',return_value=NS(free=99+m.DISK_RESERVE)),self.assertRaises(ValueError):m.disk_budget(tmp,100)
 def test_frozen_producer_accepts_64_source_cap_shards_and_rejects_65(self):
  import pit_retention_preparation as pit
  with tempfile.TemporaryDirectory() as tmp,patch.object(pit,'LIMIT_BYTES',4):
   root=Path(tmp);(root/'data').mkdir();objects={}
   for i in range(64):
    path=root/'data'/str(i);path.write_bytes(b'abcd');objects['data/'+str(i)]=pit.digest(b'abcd')
   _,receipt=pit.prepare_inventory_bundle(root,objects)
   self.assertEqual(receipt['part_count'],64);pit.verify_inventory_bundle(root,receipt)
   (root/'data/extra').write_bytes(b'abcd');objects['data/extra']=pit.digest(b'abcd')
   with self.assertRaisesRegex(ValueError,'shard count'):pit.prepare_inventory_bundle(root,objects)
 def test_zero_byte_objects_consume_manifest_but_not_source_cap(self):
  import pit_retention_preparation as pit
  with tempfile.TemporaryDirectory() as tmp,patch.object(pit,'LIMIT_BYTES',1):
   root=Path(tmp);(root/'data').mkdir();objects={}
   for i in range(128):
    path=root/'data'/str(i);path.write_bytes(b'');objects['data/'+str(i)]=pit.digest(b'')
   _,receipt=pit.prepare_inventory_bundle(root,objects)
   self.assertEqual(receipt['objects'],128)
   self.assertGreater(receipt['byte_count'],1)
 def test_windows_never_reports_linux_harness_success(self):
  if os.name!='nt':self.skipTest('Windows-only platform refusal')
  import subprocess,sys
  result=subprocess.run([sys.executable,'offline_linux_inventory_checks.py','--output','unused-linux-proof.json'],capture_output=True,text=True)
  self.assertNotEqual(result.returncode,0)
  self.assertIn('Linux required',result.stderr)
  self.assertFalse(Path('unused-linux-proof.json').exists())

class OutputAndReparseTests(unittest.TestCase):
 def test_windows_junction_rejected_without_path_is_junction(self):
  if os.name!='nt':self.skipTest('Windows reparse attribute fallback')
  with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as outside:
   root=Path(tmp);directory_link(Path(outside),root/'link')
   with patch.object(Path,'is_junction',return_value=False,create=True):
    with self.assertRaises(ValueError):m.reject_link_ancestors(root/'link'/'nested')
 def test_source_report_link_is_checked_before_publish(self):
  fixture=CapacityTests()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);get,download=fixture.fixture(root)
   original=Path.is_symlink
   def linked(path):return path.name=='source.json' or original(path)
   with patch.object(Path,'is_symlink',linked),patch.object(m,'github',side_effect=get),patch.object(m,'download_artifact',side_effect=download),patch.object(m,'validate_staged_packages'),patch('pit_retention_preparation.verify_inventory_bundle'),patch.object(m.subprocess,'check_output',return_value='d'*40):
    with self.assertRaises(ValueError):m.stage(root)
   self.assertFalse((root/'oss-inventory-stage').exists())

if __name__=='__main__':unittest.main()

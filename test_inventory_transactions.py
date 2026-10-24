"""Synthetic-only transaction and independent-upload validation checks."""
import io,json,shutil,tempfile,unittest,zipfile,os,sys
from pathlib import Path
from unittest.mock import patch,Mock
from types import SimpleNamespace as NS
import oss_inventory_retention as m
import pit_retention_preparation as frozen

def fixture(root,json_part=False):
 base=root/'data/pit_retention_preparation_v1';base.mkdir(parents=True)
 files={};parts=[];objects={}
 for i in range(2):
  name='data/source'+str(i);payload=b'x';sha=m.digest(payload);objects[name]=sha
  raw=io.BytesIO()
  with zipfile.ZipFile(raw,'w') as z:
   z.writestr('bundle_manifest.json',frozen.canonical({'objects':{name:sha}}));z.writestr(name,payload)
  raw=raw.getvalue();sha=m.digest(raw);extension='.json' if json_part and i==0 else '.zip'
  relative='data/pit_retention_preparation_v1/bundles/'+sha+extension
  files[relative.split('data/pit_retention_preparation_v1/')[1]]=raw
  parts.append({'bundle_path':relative,'bundle_sha256':sha,'byte_count':len(raw),'objects':1})
 index=frozen.canonical({'bundle_kind':'SHARDED_INVENTORY_INDEX','per_part_uncompressed_cap':frozen.LIMIT_BYTES,'parts':parts,'objects':objects})
 sha=m.digest(index);relative='bundles/'+sha+'.index.json';files[relative]=index
 prior={'bundle_sha256':sha,'bundle_path':'data/pit_retention_preparation_v1/'+relative,'bundle_kind':'SHARDED_INVENTORY_INDEX','objects':2,'prepared_at_utc':'2026-10-24T00:00:00Z'}
 files['latest.json']=frozen.canonical(prior)
 (base/'latest.json').write_bytes(files['latest.json'])
 artifact=root/'fixture.zip'
 with zipfile.ZipFile(artifact,'w') as z:
  for name,raw in files.items():z.writestr(name,raw)
 digest=m.digest(artifact.read_bytes());(base/'artifact_publication.json').write_text(json.dumps({'bundle_sha256':sha,'artifact_id':1,'run_id':2,'artifact_archive_sha256':digest}))
 metadata={'id':1,'size_in_bytes':artifact.stat().st_size,'digest':'sha256:'+digest,'expired':False,'workflow_run':{'id':2}}
 run={'id':2,'head_branch':'main','event':'push','repository':{'full_name':'lzwislam-collab/doge-data-collector'},'path':'.github/workflows/okx_hourly_extreme_history.yml','head_sha':'c'*40,'html_url':'https://example.invalid/fixture'}
 def get(path):return metadata if '/artifacts/' in path else run
 return artifact,get,prior

def stage(root,json_part=False):
 artifact,get,prior=fixture(root,json_part)
 with patch.object(m,'github',side_effect=get),patch.object(m,'github_response',side_effect=lambda p:artifact.open('rb')),patch.object(m.subprocess,'check_output',return_value='d'*40):
  return m.stage(root),prior

class Transactions(unittest.TestCase):
 def test_git_metadata_failure_leaves_no_published_stage(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);artifact,get,_=fixture(root)
   with patch.object(m,'github',side_effect=get),patch.object(m,'github_response',side_effect=lambda p:artifact.open('rb')),patch.object(m.subprocess,'check_output',side_effect=RuntimeError('git unavailable')):
    with self.assertRaises(RuntimeError):m.stage(root)
   self.assertFalse((root/'oss-inventory-stage').exists())
   self.assertFalse((root/'oss-retention-output/source.json').exists())
 def test_report_write_failure_leaves_no_published_stage(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);artifact,get,_=fixture(root);original=Path.write_text
   def write(path,*args,**kwargs):
    if path.name in ('source.json','publication-source.json'):raise OSError('report write failed')
    return original(path,*args,**kwargs)
   with patch.object(m,'github',side_effect=get),patch.object(m,'github_response',side_effect=lambda p:artifact.open('rb')),patch.object(m.subprocess,'check_output',return_value='d'*40),patch.object(Path,'write_text',write):
    with self.assertRaises(OSError):m.stage(root)
   self.assertFalse((root/'oss-inventory-stage').exists())
 def test_mistyped_referenced_part_rejected_before_frozen_zip_parse(self):
  with tempfile.TemporaryDirectory() as tmp:
   with self.assertRaises(ValueError):stage(Path(tmp),json_part=True)
 def test_upload_checks_corrupted_local_inventory_before_identity(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);_,prior=stage(root)
   (root/'oss-inventory-stage'/prior['bundle_path']).write_bytes(b'{}')
   identity=Mock(side_effect=AssertionError('provider called before local validation'))
   with patch.object(m,'identity_client',identity),patch.dict(os.environ,{'ALIBABA_CLOUD_SECURITY_TOKEN':'fixture','ALIBABA_CLOUD_ACCESS_KEY_ID':'fixture','ALIBABA_CLOUD_ACCESS_KEY_SECRET':'fixture'}):
    with self.assertRaises(ValueError):m.upload_inventory(root)
   identity.assert_not_called()
 def test_download_keyboard_interrupt_cleans_created_file(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'archive.zip';response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.side_effect=KeyboardInterrupt()
   with patch.object(m,'github_response',return_value=response):
    with self.assertRaises(KeyboardInterrupt):m.download_artifact({'id':1,'size_in_bytes':1,'digest':'a'*64},path)
   self.assertFalse(path.exists())
 def test_extract_keyboard_interrupt_cleans_created_files(self):
  raw=io.BytesIO()
  with zipfile.ZipFile(raw,'w') as z:z.writestr('nested/file.json',b'{}')
  with tempfile.TemporaryDirectory() as tmp,patch.object(zipfile.ZipExtFile,'read',side_effect=KeyboardInterrupt()):
   with self.assertRaises(KeyboardInterrupt):m.extract_artifact(raw.getvalue(),tmp)
   self.assertEqual(list(Path(tmp).iterdir()),[])

class PublicationAndBudgetTests(unittest.TestCase):
 def test_report_commit_failure_rolls_back_and_preserves_old_report(self):
  for failure in (OSError('commit failed'),KeyboardInterrupt()):
   with self.subTest(kind=type(failure).__name__),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);artifact,get,_=fixture(root);output=root/'oss-retention-output';output.mkdir();(output/'source.json').write_bytes(b'old report')
    original=Path.replace
    def replace(path,target):
     if path.name=='publication-source.json':raise failure
     return original(path,target)
    with patch.object(m,'github',side_effect=get),patch.object(m,'github_response',side_effect=lambda p:artifact.open('rb')),patch.object(m.subprocess,'check_output',return_value='d'*40),patch.object(Path,'replace',replace):
     with self.assertRaises(type(failure)):m.stage(root)
    self.assertFalse((root/'oss-inventory-stage').exists())
    self.assertEqual((output/'source.json').read_bytes(),b'old report')
    self.assertEqual(list(root.glob('oss-artifact-*')),[])
 def test_retry_after_metadata_failure_succeeds(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);artifact,get,_=fixture(root)
   with patch.object(m,'github',side_effect=get),patch.object(m,'github_response',side_effect=lambda p:artifact.open('rb')),patch.object(m.subprocess,'check_output',side_effect=[RuntimeError('temporary error'),'d'*40]):
    with self.assertRaises(RuntimeError):m.stage(root)
    self.assertTrue(m.stage(root))
   self.assertEqual(m.prepare_upload_inventory(root)['prior']['objects'],2)
 def test_stale_source_receipt_rejected_before_sdk(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);stage(root);path=root/'oss-retention-output/source.json';source=json.loads(path.read_bytes());source['bundle_sha256']='f'*40;path.write_text(json.dumps(source))
   with patch.object(m,'identity_client') as identity:
    with self.assertRaisesRegex(ValueError,'receipt mismatch'):m.upload_inventory(root)
    identity.assert_not_called()
 def test_added_oversized_metadata_rejected_before_sdk(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);stage(root);path=root/'oss-inventory-stage/data/pit_retention_preparation_v1/extra.json'
   with path.open('wb') as f:f.truncate(m.INVENTORY_METADATA_LIMIT+1)
   with patch.object(m,'identity_client') as identity:
    with self.assertRaises(ValueError):m.upload_inventory(root)
    identity.assert_not_called()
 def test_deep_archive_path_rejected_before_creating_directories(self):
  raw=io.BytesIO()
  with zipfile.ZipFile(raw,'w') as z:z.writestr('/'.join(['x']*(m.ARTIFACT_PATH_DEPTH_LIMIT+1))+'.json',b'{}')
  with tempfile.TemporaryDirectory() as tmp:
   with self.assertRaises(ValueError):m.extract_artifact(raw.getvalue(),tmp)
   self.assertEqual(list(Path(tmp).iterdir()),[])

class AtomicReceiptTests(unittest.TestCase):
 def test_failed_replace_preserves_old_receipt_and_cleans_temp(self):
  for failure in (OSError('disk failure'),KeyboardInterrupt()):
   with self.subTest(kind=type(failure).__name__),tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'receipt.json';path.write_bytes(b'old valid receipt')
    with patch.object(Path,'replace',side_effect=failure):
     with self.assertRaises(type(failure)):m.write_json_atomic(path,{'status':'new'})
    self.assertEqual(path.read_bytes(),b'old valid receipt')
    self.assertEqual(list(Path(tmp).iterdir()),[path])
 def test_success_replaces_complete_receipt(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'receipt.json';path.write_bytes(b'old')
   m.write_json_atomic(path,{'status':'complete'})
   self.assertEqual(json.loads(path.read_bytes()),{'status':'complete'})
   self.assertEqual(list(Path(tmp).iterdir()),[path])
 def test_upload_success_publishes_three_complete_receipts_offline(self):
  import sys,types
  class Request:
   def __getattr__(self,name):return lambda *args:None
  provider=types.SimpleNamespace(StsAuth=lambda *a:None,Bucket=lambda *a,**k:object())
  request=types.SimpleNamespace(CommonRequest=Request)
  env={'ALIBABA_CLOUD_SECURITY_TOKEN':'fixture','ALIBABA_CLOUD_ACCESS_KEY_ID':'fixture','ALIBABA_CLOUD_ACCESS_KEY_SECRET':'fixture','GITHUB_REPOSITORY':'fixture/repo','GITHUB_RUN_ID':'1'}
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);_,prior=stage(root)
   client=Mock();client.do_action_with_exception.return_value=json.dumps({'AccountId':m.OWNER,'Arn':'doge-evidence-github-main'})
   with patch.dict(sys.modules,{'oss2':provider,'aliyunsdkcore.request':request}),patch.dict(os.environ,env),patch.object(m,'identity_client',return_value=client),patch.object(m,'check_bucket',return_value={'fixture':True}),patch.object(m,'upload_one',side_effect=lambda b,raw,ext,lock:{'sha256':m.digest(raw)}):
    report=m.upload_inventory(root)
   for path in (root/'audit/oss_retention'/(prior['bundle_sha256']+'.json'),root/'audit/oss_retention/latest.json',root/'oss-retention-output/receipt.json'):
    self.assertEqual(json.loads(path.read_bytes()),report)
   self.assertEqual(report['object_count'],3)
 def test_previous_receipt_link_rejected_before_skip(self):
  from test_inventory_capacity import directory_link
  with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as outside:
   root=Path(tmp);_,_,prior=fixture(root);(root/'audit').mkdir()
   directory_link(Path(outside),root/'audit/oss_retention')
   (Path(outside)/(prior['bundle_sha256']+'.json')).write_text(json.dumps({'status':'LOCKED_DAILY_INVENTORY_VERIFIED','bundle_sha256':prior['bundle_sha256']}))
   with self.assertRaises(ValueError):m.stage(root)
   self.assertFalse((root/'oss-retention-output/status.json').exists())
 def test_failure_report_refuses_unsafe_output(self):
  with tempfile.TemporaryDirectory() as tmp:
   with patch.object(m,'reject_link_ancestors',side_effect=ValueError('unsafe output')):
    self.assertFalse(m.record_upload_failure(Path(tmp),ValueError('rejected')))
   self.assertEqual(list(Path(tmp).iterdir()),[])

if __name__=='__main__':unittest.main()

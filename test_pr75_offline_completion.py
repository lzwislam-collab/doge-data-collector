"""Additional synthetic offline acceptance checks for PR #75; no production data."""
import io,json,os,socket,tempfile,unittest,zipfile,hashlib,struct
from pathlib import Path
from unittest.mock import patch,Mock
from types import SimpleNamespace as NS
import oss_inventory_retention as m
import pit_retention_preparation as frozen
from test_inventory_transactions import fixture,stage
from test_oss_inventory_retention import Bucket,NOW


def single_fixture(root,mode=None):
 (root/'data').mkdir();source=root/'data/source';source.write_bytes(b'proof')
 package,prior=frozen.prepare_inventory_bundle(root,{'data/source':m.digest(b'proof')},now=NOW)
 raw=package.read_bytes()
 if mode in ('payload','manifest','missing','extra','crc','unsafe'):
  stream=io.BytesIO()
  with zipfile.ZipFile(io.BytesIO(raw)) as old,zipfile.ZipFile(stream,'w') as new:
   for name in old.namelist():
    if mode=='missing' and name=='data/source':continue
    payload=old.read(name)
    if mode=='payload' and name=='data/source':payload=b'wrong'
    if mode=='manifest' and name=='bundle_manifest.json':payload=b'not-json'
    if mode=='unsafe':
     if name=='bundle_manifest.json':payload=frozen.canonical({'objects':{'../bad':m.digest(b'proof')}})
     else:name='../bad'
    new.writestr(name,payload)
   if mode=='extra':new.writestr('data/extra',b'additional')
  raw=stream.getvalue()
  if mode=='crc':raw=raw.replace(b'proof',b'WRONG',1)
 if mode=='objects':prior['objects']=99
 sha=m.digest(raw);prior.update(bundle_sha256=sha,bundle_path='data/pit_retention_preparation_v1/bundles/'+sha+'.zip',byte_count=len(raw))
 base=root/'data/pit_retention_preparation_v1';(base/'latest.json').write_text(json.dumps(prior))
 outer=root/'fixture.zip'
 with zipfile.ZipFile(outer,'w') as z:
  z.writestr('latest.json',json.dumps(prior));z.writestr('bundles/'+sha+'.zip',raw)
 digest=m.digest(outer.read_bytes());(base/'artifact_publication.json').write_text(json.dumps({'bundle_sha256':sha,'artifact_id':1,'run_id':2,'artifact_archive_sha256':digest}))
 artifact={'id':1,'size_in_bytes':outer.stat().st_size,'digest':'sha256:'+digest,'expired':False,'workflow_run':{'id':2}}
 run={'id':2,'head_branch':'main','event':'push','repository':{'full_name':'lzwislam-collab/doge-data-collector'},'path':'.github/workflows/okx_hourly_extreme_history.yml','head_sha':'c'*40,'html_url':'https://example.invalid/fixture'}
 return outer,artifact,run,prior

def run_single(root,mode=None):
 outer,artifact,run,prior=single_fixture(root,mode)
 with patch.object(m,'github',side_effect=lambda p:artifact if '/artifacts/' in p else run),patch.object(m,'github_response',side_effect=lambda p:outer.open('rb')),patch.object(m.subprocess,'check_output',return_value='d'*40):
  return m.stage(root),prior

class SinglePackageIntegrity(unittest.TestCase):
 def test_unchanged_producer_single_package_accepted(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);ready,prior=run_single(root);self.assertTrue(ready)
   checked=m.prepare_upload_inventory(root);self.assertEqual(checked['parts'],[]);self.assertEqual(checked['prior']['bundle_sha256'],prior['bundle_sha256'])
 def test_single_package_payload_hash_mismatch_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   with self.assertRaises(ValueError):run_single(root,'payload')
   self.assertFalse((root/'oss-inventory-stage').exists())
 def test_single_package_json_coverage_crc_and_paths(self):
  for mode in ('manifest','missing','extra','crc','unsafe'):
   with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    with self.assertRaises((ValueError,zipfile.BadZipFile)):run_single(root,mode)
    self.assertFalse((root/'oss-inventory-stage').exists());self.assertEqual(list(root.glob('oss-artifact-*')),[])
 def test_single_package_semantics_rechecked_before_sdk(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);run_single(root)
   latest=root/'oss-inventory-stage/data/pit_retention_preparation_v1/latest.json';prior=json.loads(latest.read_bytes());prior['objects']=99;latest.write_text(json.dumps(prior))
   source=root/'oss-retention-output/source.json';record=json.loads(source.read_bytes());record['inventory_objects']=99;record['inventory_capacity']=m.staged_capacity(root/'oss-inventory-stage',record['inventory_capacity']['archive_bytes']);source.write_text(json.dumps(record))
   with patch.object(m,'identity_client') as identity:
    with self.assertRaisesRegex(ValueError,'coverage/count'):m.upload_inventory(root)
    identity.assert_not_called()
 def test_single_package_object_count_mismatch_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   with self.assertRaises(ValueError):run_single(root,'objects')
   self.assertFalse((root/'oss-inventory-stage').exists())

class OfflineFailureMatrix(unittest.TestCase):
 def test_atomic_fsync_failure_preserves_old_receipt(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'receipt.json';path.write_bytes(b'previous')
   with patch.object(m.os,'fsync',side_effect=OSError('disk failure')):
    with self.assertRaises(OSError):m.write_json_atomic(path,{'new':True})
   self.assertEqual(path.read_bytes(),b'previous');self.assertEqual(list(Path(tmp).iterdir()),[path])
 def test_download_existing_destination_never_overwritten(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(m,'github_response') as request:
   path=Path(tmp)/'archive';path.write_bytes(b'owned by another operation')
   with self.assertRaises(FileExistsError):m.download_artifact({'id':1,'size_in_bytes':1,'digest':'a'*64},path)
   request.assert_not_called();self.assertEqual(path.read_bytes(),b'owned by another operation')
 def test_download_network_open_failure_removes_created_file(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(m,'github_response',side_effect=OSError('offline failure')):
   path=Path(tmp)/'archive'
   with self.assertRaises(OSError):m.download_artifact({'id':1,'size_in_bytes':1,'digest':'a'*64},path)
   self.assertFalse(path.exists())
 def test_readback_error_closes_response_and_never_returns_verified(self):
  bucket=Bucket();body=Mock();body.read.side_effect=OSError('readback failed')
  bucket.get_object=lambda *a,**k:body
  with self.assertRaises(OSError):m.upload_one(bucket,b'proof','.zip',m.check_bucket(bucket),now=NOW)
  body.close.assert_called_once()
 def test_permission_failure_never_creates_object(self):
  bucket=Bucket();bucket.head_object=Mock(side_effect=PermissionError('denied'))
  with self.assertRaises(PermissionError):m.upload_one(bucket,b'proof','.zip',m.check_bucket(bucket),now=NOW)
  self.assertEqual(bucket.writes,0)
 def test_readback_rejects_bad_headers(self):
  for changes in ({'x-oss-version-id':'null'},{'x-oss-server-side-encryption':'none'},{'date':'not-a-date'}):
   with self.subTest(changes=changes):
    bucket=Bucket();original=bucket.get_object
    def get(*a,**k):
     body=original(*a,**k);body.headers.update(changes);return body
    bucket.get_object=get
    with self.assertRaises((ValueError,TypeError)):m.upload_one(bucket,b'proof','.zip',m.check_bucket(bucket),now=NOW)
 def test_failure_report_sanitizes_exception_text(self):
  with tempfile.TemporaryDirectory() as tmp:
   self.assertTrue(m.record_upload_failure(Path(tmp),ValueError('credential-secret-must-not-appear')))
   raw=(Path(tmp)/'oss-retention-output/failure.json').read_bytes()
   self.assertNotIn(b'credential-secret',raw);self.assertFalse(json.loads(raw)['locked_bytes_verified'])
 def test_stage_skip_never_claims_fresh_readback(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);_,_,prior=fixture(root);folder=root/'audit/oss_retention';folder.mkdir(parents=True)
   (folder/(prior['bundle_sha256']+'.json')).write_text(json.dumps({'status':'LOCKED_DAILY_INVENTORY_VERIFIED','bundle_sha256':prior['bundle_sha256']}))
   with patch.object(m,'github') as api:self.assertFalse(m.stage(root));api.assert_not_called()
   result=json.loads((root/'oss-retention-output/status.json').read_bytes());self.assertFalse(result['fresh_object_readback_performed'])
 def test_stage_force_does_not_skip_verified_inventory(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);outer,get,prior=fixture(root);folder=root/'audit/oss_retention';folder.mkdir(parents=True)
   receipt=folder/(prior['bundle_sha256']+'.json');receipt.write_bytes(b'previous receipt')
   with patch.object(m,'github',side_effect=get),patch.object(m,'github_response',side_effect=lambda p:outer.open('rb')),patch.object(m.subprocess,'check_output',return_value='d'*40):self.assertTrue(m.stage(root,True))
   self.assertEqual(receipt.read_bytes(),b'previous receipt')
 def test_postdownload_disk_failure_leaves_no_stage_or_temporary_files(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);outer,get,_=fixture(root)
   with patch.object(m,'github',side_effect=get),patch.object(m,'github_response',side_effect=lambda p:outer.open('rb')),patch.object(m,'disk_budget',side_effect=[None,ValueError('insufficient disk')]):
    with self.assertRaises(ValueError):m.stage(root)
   self.assertFalse((root/'oss-inventory-stage').exists());self.assertEqual(list(root.glob('oss-artifact-*')),[])
 def test_poststage_payload_change_rejected_before_identity(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);stage(root);base=root/'oss-inventory-stage/data/pit_retention_preparation_v1';index=json.loads(next((base/'bundles').glob('*.index.json')).read_bytes())
   (root/'oss-inventory-stage'/index['parts'][0]['bundle_path']).write_bytes(b'broken')
   with patch.object(m,'identity_client') as identity:
    with self.assertRaises(ValueError):m.upload_inventory(root)
    identity.assert_not_called()

class BoundaryFixtureTests(unittest.TestCase):
 def test_full_budget_prefix_accounts_for_zip64_offset_growth(self):
  import offline_inventory_boundary as boundary
  members=[('container-%03d.zip'%i,{'size':m.STORED_ZIP_LIMIT}) for i in range(64)]+[('metadata-%03d.json'%i,{'size':m.INVENTORY_METADATA_LIMIT}) for i in range(448)]
  prefix=boundary.prefix_for_size(members,m.ARTIFACT_LIMIT)
  self.assertEqual(boundary.fixture_size(members,prefix),m.ARTIFACT_LIMIT)
 def test_prefixed_sparse_zip_downloads_and_extracts(self):
  import offline_inventory_stress as stress
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);path=root/'prefixed.zip';member=stress.byte_member(b'payload')
   descriptor=stress.sparse_zip(path,[('a.json',member)],prefix_bytes=1024)
   self.assertEqual(path.read_bytes()[:1024],b'\0'*1024)
   self.assertEqual(descriptor['sha'],m.digest(path.read_bytes()))
   destination=root/'expanded';destination.mkdir();m.extract_artifact(path,destination)
   self.assertEqual((destination/'a.json').read_bytes(),b'payload')

class ReportOfflineBoundaries(unittest.TestCase):
 def test_actual_git_empty_tree_and_empty_blob_are_distinguished(self):
  import shutil,subprocess
  import plus_shadow as report
  git=shutil.which('git')
  if not git:self.skipTest('Git executable required for real object-type fixture')
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   def command(*args,input=None):
    return subprocess.check_output([git,'-c','safe.directory=*',*args],cwd=root,input=input).strip()
   command('init','-q');blob=command('hash-object','-w','--stdin',input=b'');tree=command('mktree',input=b'')
   top=command('mktree',input=b'040000 tree '+tree+b'\tdirectory\n100644 blob '+blob+b'\tempty.json\n')
   commit=command('-c','user.name=Offline Test','-c','user.email=offline@example.invalid','commit-tree',top.decode(),'-m','synthetic offline fixture').decode()
   reader=report.ReportEvidenceReader(root,lambda h,p:b'')
   with self.assertRaises(report.ReportEvidenceReadError):reader(commit,'directory')
   self.assertEqual(reader(commit,'empty.json'),b'')
 def test_zero_length_tree_is_not_an_empty_evidence_blob(self):
  import plus_shadow as report
  reader=report.ReportEvidenceReader(Path('.'),lambda h,p:b'')
  results=[NS(returncode=0),NS(returncode=0,stdout=b'040000 tree object\tdirectory\0'),NS(returncode=0,stdout=b'0'),NS(returncode=0,stdout=b'tree')]
  with patch.object(report.subprocess,'run',side_effect=results):
   with self.assertRaises(report.ReportEvidenceReadError):reader('a'*40,'directory')
 def test_verified_zero_length_blob_remains_accepted(self):
  import plus_shadow as report
  reader=report.ReportEvidenceReader(Path('.'),lambda h,p:b'')
  results=[NS(returncode=0),NS(returncode=0,stdout=b'100644 blob object\tempty.json\0'),NS(returncode=0,stdout=b'0'),NS(returncode=0,stdout=b'blob')]
  with patch.object(report.subprocess,'run',side_effect=results):self.assertEqual(reader('a'*40,'empty.json'),b'')
 def test_run_limit_exactly_1000_is_complete(self):
  import plus_shadow as report
  calls=[]
  def api(path):
   page=int(path.split('page=')[2].split('&')[0]);calls.append(page)
   return {'total_count':1000,'workflow_runs':[{'id':i,'head_sha':format(i,'040x'),'created_at':'2026-10-21T00:00:00Z','run_attempt':1} for i in range((page-1)*100+1,page*100+1)]}
  self.assertEqual(len(report.report_witness_runs(api,'learned_shadow_v1.yml','2026-10-21','2026-10-21')),1000)
  self.assertEqual(len(calls),20)
 def test_attempt_boundary_100_and_plus_one(self):
  import plus_shadow as report
  run={'id':1,'run_attempt':100,'head_sha':'a'*40,'created_at':'2026-10-21T00:00:00Z'}
  calls=[]
  def api(path):
   number=int(path.rsplit('/',1)[1]);calls.append(number);return dict(run,run_attempt=number)
  self.assertEqual(len(report.report_attempts(api,run)),100);self.assertEqual(len(calls),99)
  with self.assertRaises(ValueError):report.report_attempts(api,dict(run,run_attempt=101))
 def test_report_cache_evicts_and_does_not_cache_unknown_failures(self):
  import plus_shadow as report
  read=Mock(return_value=b'x');reader=report.ReportEvidenceReader(Path('.'),read)
  for i in range(513):reader('a'*40,'data/'+str(i))
  self.assertEqual(len(reader.cache),512);reader('a'*40,'data/0');self.assertEqual(read.call_count,514)
  failed=Mock(side_effect=OSError('temporary'));reader=report.ReportEvidenceReader(Path('.'),failed)
  for _ in range(2):
   with self.assertRaises(report.ReportEvidenceReadError):reader('a'*40,'data/a')
  self.assertEqual(failed.call_count,2);self.assertFalse(reader.cache)

if __name__=='__main__':unittest.main()

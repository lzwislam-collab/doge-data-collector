"""Offline malformed-source and failure-boundary checks for PR75 8b27c4b."""
import io,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch,Mock
import oss_inventory_retention as m
from test_inventory_transactions import fixture

class SourceIdentifiers(unittest.TestCase):
 def test_marker_identifier_types_reject_before_api(self):
  for field,value in [('artifact_id',1.9),('run_id',2.9),('artifact_id',True),('run_id','2.0'),('artifact_id',0),('run_id',-2)]:
   with self.subTest(field=field,value=value),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);fixture(root);path=root/'data/pit_retention_preparation_v1/artifact_publication.json';marker=json.loads(path.read_bytes());marker[field]=value;path.write_text(json.dumps(marker))
    with patch.object(m,'github',side_effect=AssertionError('API before marker validation')) as api:
     with self.assertRaises(ValueError):m.stage(root)
     api.assert_not_called()
 def test_embedded_run_id_type_rejected(self):
  for value in (2.0,True,'2'):
   with self.subTest(value=value),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);_,get,_=fixture(root)
    def api(path):
     row=dict(get(path))
     if '/artifacts/' in path:row['workflow_run']={'id':value}
     return row
    with patch.object(m,'github',side_effect=api),patch.object(m,'download_artifact',side_effect=AssertionError('download before source checks')) as download:
     with self.assertRaises(ValueError):m.stage(root)
     download.assert_not_called()
 def test_supplied_artifact_head_mismatch_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);_,get,_=fixture(root)
   def api(path):
    row=dict(get(path))
    if '/artifacts/' in path:row['workflow_run']={'id':2,'head_sha':'a'*40}
    return row
   with patch.object(m,'github',side_effect=api),patch.object(m,'download_artifact',side_effect=AssertionError('download before source checks')):
    with self.assertRaises(ValueError):m.stage(root)
 def test_pull_request_target_source_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);_,get,_=fixture(root)
   def api(path):
    row=dict(get(path))
    if '/runs/' in path:row['event']='pull_request_target'
    return row
   with patch.object(m,'github',side_effect=api),patch.object(m,'download_artifact',side_effect=AssertionError('download before source checks')):
    with self.assertRaises(ValueError):m.stage(root)

class SourceCompatibility(unittest.TestCase):
 def test_integer_and_decimal_string_markers_with_matching_heads_pass(self):
  for ids in ((1,2),('1','2'),('001','002')):
   with self.subTest(ids=ids),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);outer,get,_=fixture(root);path=root/'data/pit_retention_preparation_v1/artifact_publication.json';marker=json.loads(path.read_bytes());marker.update(artifact_id=ids[0],run_id=ids[1]);path.write_text(json.dumps(marker))
    def api(address):
     row=dict(get(address))
     if '/artifacts/' in address:row['workflow_run']={'id':2,'head_sha':'c'*40,'head_branch':'main'}
     return row
    with patch.object(m,'github',side_effect=api),patch.object(m,'github_response',side_effect=lambda p:outer.open('rb')),patch.object(m.subprocess,'check_output',return_value='d'*40):self.assertTrue(m.stage(root))
    self.assertEqual(m.prepare_upload_inventory(root)['prior']['objects'],2)
 def test_malformed_run_commit_is_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);_,get,_=fixture(root)
   def api(address):
    row=dict(get(address))
    if '/runs/' in address:row['head_sha']='not-a-commit'
    return row
   with patch.object(m,'github',side_effect=api),patch.object(m,'download_artifact') as download:
    with self.assertRaises(ValueError):m.stage(root)
    download.assert_not_called()
 def test_supplied_artifact_branch_mismatch_is_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);_,get,_=fixture(root)
   def api(address):
    row=dict(get(address))
    if '/artifacts/' in address:row['workflow_run']={'id':2,'head_branch':'other'}
    return row
   with patch.object(m,'github',side_effect=api),patch.object(m,'download_artifact') as download:
    with self.assertRaises(ValueError):m.stage(root)
    download.assert_not_called()

class ExistingFailureGuarantees(unittest.TestCase):
 def test_download_delete_denial_preserves_read_failure_context(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'download.zip';failure=OSError('response failed')
   with patch.object(m,'github_response',side_effect=failure),patch.object(Path,'unlink',side_effect=PermissionError('delete denied')):
    with self.assertRaises(PermissionError) as error:m.download_artifact({'id':1,'size_in_bytes':1,'digest':'a'*64},path)
   self.assertIs(error.exception.__context__,failure);self.assertTrue(path.exists())
 def test_receipt_double_failure_preserves_old_bytes_and_original_context(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'receipt.json';path.write_bytes(b'old receipt');failure=OSError('sync failed')
   with patch.object(m.os,'fsync',side_effect=failure),patch.object(Path,'unlink',side_effect=PermissionError('delete denied')):
    with self.assertRaises(PermissionError) as error:m.write_json_atomic(path,{'status':'new'})
   self.assertEqual(path.read_bytes(),b'old receipt');self.assertIs(error.exception.__context__,failure)
 def test_exclusive_report_postpublish_cleanup_error_keeps_durable_valid_result(self):
  import offline_inventory_stress as tool
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'result.json';original=Path.unlink
   def unlink(target,*a,**k):
    if target.name.startswith('.offline-evidence-'):raise PermissionError('private cleanup denied')
    return original(target,*a,**k)
   with patch.object(Path,'unlink',unlink):
    with self.assertRaises(PermissionError):tool.write_evidence_json(path,{'accepted':True})
   self.assertEqual(json.loads(path.read_bytes()),{'accepted':True})
   self.assertEqual(len(list(Path(tmp).glob('.offline-evidence-*'))),1)
 def test_readback_close_failure_does_not_hide_original_exception_chain(self):
  from test_oss_inventory_retention import Bucket,NOW
  body=Mock();failure=OSError('read failed');body.read.side_effect=failure;body.close.side_effect=OSError('close failed');bucket=Bucket();bucket.get_object=lambda *a,**k:body
  with self.assertRaises(OSError) as error:m.upload_one(bucket,b'proof','.zip',m.check_bucket(bucket),now=NOW)
  self.assertIs(error.exception.__context__,failure);body.close.assert_called_once()

if __name__=='__main__':unittest.main()

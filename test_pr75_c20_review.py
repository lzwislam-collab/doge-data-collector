"""Offline evidence/compatibility regressions; never assert real Linux execution."""
import json, tempfile, threading, unittest, zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import oss_inventory_retention as inventory
import offline_inventory_stress as evidence
import offline_linux_inventory_checks as linux_checks
import pit_retention_preparation as frozen
from test_inventory_transactions import fixture

class LinuxEvidenceSafety(unittest.TestCase):
 def test_existing_evidence_is_rejected_before_linux_tests(self):
  with tempfile.TemporaryDirectory() as folder:
   output=Path(folder)/'prior.json';output.write_bytes(b'prior evidence')
   with patch.object(linux_checks.sys,'argv',['checks','--output',str(output)]),patch.object(linux_checks.sys,'platform','linux'),patch.object(linux_checks.unittest.TextTestRunner,'run',side_effect=AssertionError('tests ran before evidence preflight')):
    with self.assertRaises(FileExistsError):linux_checks.main()
   self.assertEqual(output.read_bytes(),b'prior evidence')
 def test_non_linux_cannot_publish_evidence(self):
  with tempfile.TemporaryDirectory() as folder:
   output=Path(folder)/'new.json'
   with patch.object(linux_checks.sys,'argv',['checks','--output',str(output)]),patch.object(linux_checks.sys,'platform','win32'):
    with self.assertRaises(SystemExit):linux_checks.main()
   self.assertEqual(list(Path(folder).iterdir()),[])

 def test_synthetic_failed_linux_result_cannot_overwrite_racing_evidence(self):
  from types import SimpleNamespace
  result=SimpleNamespace(testsRun=0,skipped=[],failures=[('synthetic','not a Linux run')],errors=[],wasSuccessful=lambda:False)
  with tempfile.TemporaryDirectory() as folder:
   output=Path(folder)/'result.json'
   def collision(source,target):Path(target).write_bytes(b'foreign evidence');raise FileExistsError('race')
   with patch.object(linux_checks.sys,'argv',['checks','--output',str(output)]),patch.object(linux_checks.sys,'platform','linux'),patch.object(linux_checks.unittest.TextTestRunner,'run',return_value=result),patch.object(evidence.os,'link',side_effect=collision):
    with self.assertRaises(FileExistsError):linux_checks.main()
   self.assertEqual(output.read_bytes(),b'foreign evidence');self.assertEqual(list(Path(folder).iterdir()),[output])
 def test_synthetic_failed_linux_result_sync_and_cleanup_failure_has_no_final_report(self):
  from types import SimpleNamespace
  result=SimpleNamespace(testsRun=0,skipped=[],failures=[('synthetic','not a Linux run')],errors=[],wasSuccessful=lambda:False)
  with tempfile.TemporaryDirectory() as folder:
   output=Path(folder)/'result.json';failure=OSError('sync failed');cleanup=PermissionError('delete denied')
   with patch.object(linux_checks.sys,'argv',['checks','--output',str(output)]),patch.object(linux_checks.sys,'platform','linux'),patch.object(linux_checks.unittest.TextTestRunner,'run',return_value=result),patch.object(evidence.os,'fsync',side_effect=failure),patch.object(Path,'unlink',side_effect=cleanup):
    with self.assertRaises(BaseExceptionGroup) as caught:linux_checks.main()
   self.assertFalse(output.exists());self.assertEqual(caught.exception.exceptions,(failure,cleanup));self.assertEqual(len(list(Path(folder).glob('.offline-evidence-*'))),1)

class ProducerCompatibility(unittest.TestCase):
 def test_frozen_producer_markers_preserve_large_ids_through_stage(self):
  for event,aid,rid,prefix in [('schedule',1,2,''),('workflow_dispatch',9007199254740993,9007199254740995,'sha256:')]:
   with self.subTest(event=event),tempfile.TemporaryDirectory() as folder:
    root=Path(folder);outer,get,prior=fixture(root);base=root/'data/pit_retention_preparation_v1'
    with zipfile.ZipFile(outer) as archive:archive.extractall(base)
    prior.update(status='NOT_CONFIGURED',generated_new_bundle=True,run_id=str(rid),implementation_sha256=frozen.implementation_hash())
    (base/'latest.json').write_text(json.dumps(prior))
    digest=inventory.digest(outer.read_bytes())
    marker=frozen.record_artifact(root,str(aid),prefix+digest,str(rid),now='2026-10-24T01:00:00Z')
    self.assertEqual(marker['artifact_id'],str(aid));self.assertEqual(marker['run_id'],str(rid))
    def api(path):
     row=dict(get(path))
     if '/artifacts/' in path:row.update(id=aid,workflow_run={'id':rid})
     else:row.update(id=rid,event=event)
     return row
    with patch.object(inventory,'github',side_effect=api),patch.object(inventory,'github_response',side_effect=lambda p:outer.open('rb')),patch.object(inventory.subprocess,'check_output',return_value='d'*40):
     self.assertTrue(inventory.stage(root))
    checked=inventory.prepare_upload_inventory(root)
    self.assertEqual(checked['source']['artifact_id'],aid);self.assertEqual(checked['source']['source_run_id'],rid)
 def test_verified_skip_requires_no_unused_source_locator_api(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);_,_,prior=fixture(root);base=root/'data/pit_retention_preparation_v1';marker=json.loads((base/'artifact_publication.json').read_bytes());marker.update(artifact_id=None,run_id=None);(base/'artifact_publication.json').write_text(json.dumps(marker))
   previous=root/'audit/oss_retention'/ (prior['bundle_sha256']+'.json');previous.parent.mkdir(parents=True);previous.write_text(json.dumps({'status':'LOCKED_DAILY_INVENTORY_VERIFIED','bundle_sha256':prior['bundle_sha256']}))
   with patch.object(inventory,'github',side_effect=AssertionError('unused source queried')):self.assertFalse(inventory.stage(root))
   status=json.loads((root/'oss-retention-output/status.json').read_bytes());self.assertFalse(status['fresh_object_readback_performed']);self.assertFalse((root/'oss-inventory-stage').exists())

class ExclusivePublication(unittest.TestCase):
 def test_two_real_link_publishers_leave_one_complete_winner(self):
  with tempfile.TemporaryDirectory() as folder:
   output=Path(folder)/'result.json';barrier=threading.Barrier(2);link=evidence.os.link
   def simultaneous_link(source,target):barrier.wait(timeout=10);return link(source,target)
   def publish(value):
    try:evidence.write_evidence_json(output,{'writer':value,'payload':'x'*65536});return 'published'
    except FileExistsError:return 'lost'
   with patch.object(evidence.os,'link',side_effect=simultaneous_link),ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(publish,[1,2]))
   self.assertEqual(sorted(results),['lost','published']);report=json.loads(output.read_bytes());self.assertIn(report['writer'],[1,2]);self.assertEqual(report['payload'],'x'*65536);self.assertEqual(list(Path(folder).iterdir()),[output])
 def test_link_and_cleanup_failure_preserve_foreign_evidence_and_both_errors(self):
  with tempfile.TemporaryDirectory() as folder:
   output=Path(folder)/'result.json';failure=FileExistsError('race');cleanup=PermissionError('delete denied')
   def collision(source,target):Path(target).write_bytes(b'foreign evidence');raise failure
   with patch.object(evidence.os,'link',side_effect=collision),patch.object(Path,'unlink',side_effect=cleanup):
    with self.assertRaises(BaseExceptionGroup) as caught:evidence.write_evidence_json(output,{'verified':True})
   self.assertEqual(caught.exception.exceptions,(failure,cleanup));self.assertEqual(output.read_bytes(),b'foreign evidence');self.assertEqual(len(list(Path(folder).glob('.offline-evidence-*'))),1)
 def test_receipt_replace_and_cleanup_failure_preserve_old_bytes_and_chain(self):
  with tempfile.TemporaryDirectory() as folder:
   output=Path(folder)/'receipt.json';output.write_bytes(b'old');failure=OSError('replace failed')
   with patch.object(Path,'replace',side_effect=failure),patch.object(Path,'unlink',side_effect=PermissionError('delete denied')):
    with self.assertRaises(PermissionError) as caught:inventory.write_json_atomic(output,{'status':'new'})
   self.assertIs(caught.exception.__context__,failure);self.assertEqual(output.read_bytes(),b'old')

if __name__=='__main__':unittest.main()

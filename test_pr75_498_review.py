"""Windows offline control-flow tests; no actual Linux acceptance claims."""
import json,tempfile,threading,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import offline_inventory_stress as evidence
import offline_linux_inventory_checks as linux_checks
import oss_inventory_retention as inventory
from test_inventory_transactions import fixture

class LinuxFailureReporting(unittest.TestCase):
 def test_synthetic_failed_skipped_or_incomplete_results_are_never_verified(self):
  cases=[(4,[('synthetic','failure')],[],[],False),(4,[],[('synthetic','error')],[],False),(4,[],[],[('synthetic','skip')],True),(3,[],[],[],True)]
  for count,failures,errors,skips,successful in cases:
   with self.subTest(count=count,failures=len(failures),errors=len(errors),skips=len(skips)),tempfile.TemporaryDirectory() as folder:
    output=Path(folder)/'result.json';result=SimpleNamespace(testsRun=count,failures=failures,errors=errors,skipped=skips,wasSuccessful=lambda:successful)
    with patch.object(linux_checks.sys,'argv',['checks','--output',str(output)]),patch.object(linux_checks.sys,'platform','linux'),patch.object(linux_checks.unittest.TextTestRunner,'run',return_value=result):
     with self.assertRaisesRegex(SystemExit,'incomplete'):linux_checks.main()
    report=json.loads(output.read_bytes());self.assertIs(report['linux_verified'],False);self.assertEqual(report['tests_run'],count);self.assertEqual(report['failures'],len(failures));self.assertEqual(report['errors'],len(errors));self.assertEqual(report['skipped'],len(skips));self.assertEqual(list(Path(folder).iterdir()),[output])
 def test_runner_cancellation_publishes_no_final_or_private_report(self):
  with tempfile.TemporaryDirectory() as folder:
   output=Path(folder)/'result.json'
   with patch.object(linux_checks.sys,'argv',['checks','--output',str(output)]),patch.object(linux_checks.sys,'platform','linux'),patch.object(linux_checks.unittest.TextTestRunner,'run',side_effect=KeyboardInterrupt('cancelled')):
    with self.assertRaises(KeyboardInterrupt):linux_checks.main()
   self.assertEqual(list(Path(folder).iterdir()),[])

class ConcurrentCleanup(unittest.TestCase):
 def test_failed_writer_cleanup_denial_cannot_remove_successful_writer_evidence(self):
  with tempfile.TemporaryDirectory() as folder:
   output=Path(folder)/'result.json';barrier=threading.Barrier(2);state=threading.local();sync=evidence.os.fsync;unlink=Path.unlink;failed_temp=[]
   failure=OSError('writer sync failed');cleanup=PermissionError('private delete denied')
   def fsync(fd):
    sync(fd);barrier.wait(timeout=10)
    if state.failed:raise failure
   def remove(path,*args,**kwargs):
    if getattr(state,'failed',False) and path.name.startswith('.offline-evidence-'):failed_temp.append(path);raise cleanup
    return unlink(path,*args,**kwargs)
   def publish(failed):
    state.failed=failed
    try:evidence.write_evidence_json(output,{'writer':'failed' if failed else 'complete','payload':'x'*65536});return None
    except BaseException as exc:return exc
   with patch.object(evidence.os,'fsync',side_effect=fsync),patch.object(Path,'unlink',remove),ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(publish,[True,False]))
   self.assertIsInstance(results[0],BaseExceptionGroup);self.assertEqual(results[0].exceptions,(failure,cleanup));self.assertIsNone(results[1]);self.assertEqual(json.loads(output.read_bytes()),{'writer':'complete','payload':'x'*65536});self.assertEqual(len(failed_temp),1);self.assertTrue(failed_temp[0].exists());self.assertEqual(set(Path(folder).iterdir()),{output,failed_temp[0]})
 def test_retry_after_published_report_cleanup_failure_preserves_first_report(self):
  with tempfile.TemporaryDirectory() as folder:
   output=Path(folder)/'result.json';unlink=Path.unlink
   def remove(path,*args,**kwargs):
    if path.name.startswith('.offline-evidence-'):raise PermissionError('private delete denied')
    return unlink(path,*args,**kwargs)
   with patch.object(Path,'unlink',remove):
    with self.assertRaises(PermissionError):evidence.write_evidence_json(output,{'first':True})
   before=output.read_bytes()
   with self.assertRaises(FileExistsError):evidence.write_evidence_json(output,{'replacement':True})
   self.assertEqual(output.read_bytes(),before);self.assertEqual(json.loads(before),{'first':True})

class SourceForceBoundary(unittest.TestCase):
 def test_force_revalidates_source_instead_of_reusing_old_receipt(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);_,get,prior=fixture(root);receipt=root/'audit/oss_retention'/(prior['bundle_sha256']+'.json');receipt.parent.mkdir(parents=True);old=json.dumps({'status':'LOCKED_DAILY_INVENTORY_VERIFIED','bundle_sha256':prior['bundle_sha256']}).encode();receipt.write_bytes(old)
   def api(path):
    value=dict(get(path))
    if '/artifacts/' in path:value['workflow_run']={'id':999}
    return value
   with patch.object(inventory,'github',side_effect=api),patch.object(inventory,'download_artifact',side_effect=AssertionError('download before source binding')):
    with self.assertRaisesRegex(ValueError,'provenance'):inventory.stage(root,force=True)
   self.assertEqual(receipt.read_bytes(),old);self.assertFalse((root/'oss-inventory-stage').exists());self.assertFalse((root/'oss-retention-output/source.json').exists())

if __name__=='__main__':unittest.main()

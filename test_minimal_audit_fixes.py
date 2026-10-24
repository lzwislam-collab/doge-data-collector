import unittest, tempfile, io, hashlib
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
import plus_shadow as report
import oss_inventory_retention as inventory

def run_record(i,day='2026-10-21'):
 return {'id':i,'created_at':day+'T00:00:00Z','head_sha':format(i,'040x'),'run_attempt':1}

class MinimalFixTests(unittest.TestCase):
 def test_witness_paginates_and_keeps_locator(self):
  calls=[]
  def api(path):
   calls.append(path)
   if '/runs/999' in path:return {'id':999}
   page=2 if 'page=2&' in path else 1
   return {'total_count':101,'workflow_runs':[run_record(i) for i in (range(1,101) if page==1 else [101])]}
  rows=report.report_witness_runs(api,'learned_shadow_v1.yml', '2026-10-21','2026-10-21',[999])
  self.assertEqual({r['id'] for r in rows},set(range(1,102))|{999})
  self.assertTrue(any('page=2&' in p for p in calls))
 def test_truncated_search_is_not_missing_witness(self):
  with self.assertRaisesRegex(ValueError,'incomplete'):
   report.report_witness_runs(lambda p:{'total_count':1001,'workflow_runs':[]},'learned_shadow_v1.yml','2026-10-21','2026-10-21',[])
 def test_missing_git_does_not_mask_404_classification(self):
  reader=report.ReportEvidenceReader(Path('.'),lambda h,p:(_ for _ in ()).throw(HTTPError('https://example.invalid',404,'',{},None)))
  with patch.object(report.subprocess,'run',side_effect=FileNotFoundError()),self.assertRaises(report.ReportEvidenceReadError) as error:
   reader('a'*40,'missing.json')
  self.assertEqual(error.exception.diagnostic['classification'],'PINNED_TREE_UNAVAILABLE')
 def test_real_inventory_size_passes_metadata_gate_but_not_byte_verification(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(inventory,'github_response',return_value=io.BytesIO(b'')) as get:
   with self.assertRaisesRegex(ValueError,'transport size or digest mismatch'):
    inventory.download_artifact({'id':1,'size_in_bytes':1288488954,'digest':'sha256:'+hashlib.sha256(b'').hexdigest()},Path(tmp)/'archive.zip')
   get.assert_called_once()
   self.assertFalse((Path(tmp)/'archive.zip').exists())
 def test_capacity_remains_bounded_and_inner_cap_unchanged(self):
  self.assertEqual(inventory.LIMIT,256*1024*1024)
  self.assertEqual(inventory.ARTIFACT_LIMIT,21*1024*1024*1024)
  with tempfile.TemporaryDirectory() as tmp,patch.object(inventory,'github_response') as get:
   with self.assertRaises(ValueError):inventory.download_artifact({'id':1,'size_in_bytes':inventory.ARTIFACT_LIMIT+1,'digest':'0'*64},Path(tmp)/'archive.zip')
   get.assert_not_called()

class BoundaryTests(unittest.TestCase):
 def test_changed_or_short_run_pages_block(self):
  for response in ({'total_count':2,'workflow_runs':[{'id':1}]},{'total_count':True,'workflow_runs':[]}):
   with self.subTest(response=response),self.assertRaisesRegex(ValueError,'incomplete'):
    report.report_witness_runs(lambda p:response,'learned_shadow_v1.yml','2026-10-21','2026-10-21')
 def test_daily_windows_deduplicate_server_records(self):
  calls=[]
  def api(path):
   calls.append(path);return {'total_count':1,'workflow_runs':[run_record(1,path.split('created=')[1])]}
  self.assertEqual(report.report_witness_runs(api,'learned_shadow_v1.yml','2026-10-20','2026-10-21'),[run_record(1)])
  self.assertEqual(len(calls),4)
  self.assertTrue(all('/actions/workflows/' in path for path in calls))
 def test_locator_id_mismatch_blocks(self):
  def api(path):return {'id':2} if '/runs/1' in path else {'total_count':0,'workflow_runs':[]}
  with self.assertRaisesRegex(ValueError,'locator mismatch'):
   report.report_witness_runs(api,'learned_shadow_v1.yml','2026-10-21','2026-10-21',[1])
 def test_job_pagination_retains_audit_beyond_first_page(self):
  run={'id':7,'status':'completed','conclusion':'success','repository':{'full_name':report.f.old.REPO},'path':report.f.WORKFLOW,'head_branch':'main','event':'workflow_dispatch'}
  def api(path):
   jobs=[{'id':i,'name':'other','run_id':7,'run_attempt':1} for i in range(1,101)] if '&page=1' in path else [{'id':101,'name':'audit','run_id':7,'run_attempt':1}]
   return {'total_count':101,'jobs':jobs}
  self.assertEqual(report.report_audit_jobs(api,[run]),[{**run,'audit_job':{'id':101,'name':'audit','run_id':7,'run_attempt':1}}])
 def test_incomplete_job_pages_block_and_failed_run_never_qualifies(self):
  with self.assertRaisesRegex(ValueError,'incomplete'):
   report.report_audit_jobs(lambda p:{'total_count':2,'jobs':[]},[{'id':1,'status':'completed','conclusion':'success','repository':{'full_name':report.f.old.REPO},'path':report.f.WORKFLOW,'head_branch':'main','event':'workflow_dispatch'}])
  def no_api(path):raise AssertionError('failed run queried')
  self.assertEqual(report.report_audit_jobs(no_api,[{'id':1,'status':'completed','conclusion':'failure'}]),[])
 def test_download_verifies_bytes_and_cleans_mismatch(self):
  for raw,declared,digest,ok in [(b'abc',3,hashlib.sha256(b'abc').hexdigest(),True),(b'abcd',3,'0'*64,False),(b'abc',3,'0'*64,False)]:
   with self.subTest(raw=raw,digest=digest),tempfile.TemporaryDirectory() as tmp,patch.object(inventory,'github_response',return_value=io.BytesIO(raw)):
    path=Path(tmp)/'archive.zip'
    if ok:
     self.assertEqual(inventory.download_artifact({'id':1,'size_in_bytes':declared,'digest':digest},path).read_bytes(),raw)
    else:
     with self.assertRaises(ValueError):inventory.download_artifact({'id':1,'size_in_bytes':declared,'digest':digest},path)
     self.assertFalse(path.exists())
 def test_extraction_budget_is_enforced_before_writes(self):
  import zipfile
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as archive:archive.writestr('part.zip',b'12345')
  with tempfile.TemporaryDirectory() as tmp,patch.object(inventory,'ARTIFACT_EXPANDED_LIMIT',4):
   with self.assertRaisesRegex(ValueError,'exceeds cap'):inventory.extract_artifact(stream.getvalue(),tmp)
   self.assertEqual(list(Path(tmp).iterdir()),[])
  with tempfile.TemporaryDirectory() as tmp,patch.object(inventory,'ARTIFACT_EXPANDED_LIMIT',5):
   inventory.extract_artifact(stream.getvalue(),tmp)
   self.assertEqual((Path(tmp)/'part.zip').read_bytes(),b'12345')

class DiscoveredWitnessTests(unittest.TestCase):
 def test_second_page_exact_witness_qualifies_but_late_clock_does_not(self):
  from datetime import datetime,timezone,timedelta
  at=datetime(2026,10,21,15,45,tzinfo=timezone.utc)
  def api(path):
   if '/jobs?' in path:return {'total_count':1,'jobs':[{'id':1,'run_id':int(path.split('/runs/')[1].split('/')[0]),'run_attempt':1,'name':'audit','status':'completed','conclusion':'success','started_at':at.isoformat(),'completed_at':(at+timedelta(minutes=1)).isoformat()}]}
   ids=range(1,101) if '&page=1&' in path else [101]
   return {'total_count':101,'workflow_runs':[{'id':i,'status':'completed','conclusion':'success','repository':{'full_name':report.f.old.REPO},'path':report.f.WORKFLOW,'head_branch':'main','event':'workflow_dispatch','created_at':at.isoformat(),'head_sha':format(i,'040x')} for i in ids]}
  runs=report.report_audit_jobs(api,report.report_witness_runs(api,'phase2_forward_audit_v2.yml','2026-10-21','2026-10-21'))
  read=lambda head,path:b'exact' if head==format(101,'040x') else b'mismatch'
  files={'event.json':b'exact'}
  self.assertEqual(report.f.witness(files,runs,read,at+timedelta(minutes=2),at)['run_id'],101)
  self.assertIsNone(report.f.witness(files,runs,read,at+timedelta(minutes=1),at))

class RequestBudgetTests(unittest.TestCase):
 def test_untrusted_candidates_make_zero_job_requests(self):
  run={'id':1,'status':'completed','conclusion':'success','repository':{'full_name':report.f.old.REPO},'path':report.f.WORKFLOW,'head_branch':'main','event':'workflow_dispatch'}
  candidates=[{**run,'head_branch':'feature'},{**run,'event':'pull_request'},{**run,'repository':{'full_name':'foreign/repo'}},{**run,'path':'other.yml'},{**run,'event':'workflow_run','pull_requests':[{}]}]
  def api(path):raise AssertionError('untrusted job requested')
  self.assertEqual(report.report_audit_jobs(api,candidates),[])
 def test_pinned_reads_cached_only_within_report(self):
  calls=[]
  def read(head,path):calls.append((head,path));return b'exact bytes'
  reader=report.ReportEvidenceReader(Path('.'),read)
  for _ in range(100):self.assertEqual(reader('a'*40,'proof.json'),b'exact bytes')
  self.assertEqual(len(calls),1)
  report.ReportEvidenceReader(Path('.'),read)('a'*40,'proof.json')
  self.assertEqual(len(calls),2)
 def test_run_exact_1000_boundary_and_count_changes(self):
  def api(path):
   from urllib.parse import urlparse,parse_qs
   page=int(parse_qs(urlparse(path).query)['page'][0]);return {'total_count':1000,'workflow_runs':[run_record(i) for i in range((page-1)*100+1,page*100+1)]}
  self.assertEqual(len(report.report_witness_runs(api,'learned_shadow_v1.yml','2026-10-21','2026-10-21')),1000)
  def changing(path):return {'total_count':101 if '&page=1&' in path else 102,'workflow_runs':[run_record(i) for i in range(1,101)]}
  with self.assertRaisesRegex(ValueError,'changing'):
   report.report_witness_runs(changing,'learned_shadow_v1.yml','2026-10-21','2026-10-21')

if __name__=='__main__':unittest.main()

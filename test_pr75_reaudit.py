import unittest,tempfile,io,zipfile,subprocess
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from urllib.error import HTTPError
import plus_shadow as report
import oss_inventory_retention as inventory

class ReauditTests(unittest.TestCase):
 def test_confirmed_absence_cannot_match_expected_empty_bytes(self):
  reader=report.ReportEvidenceReader(Path('.'),lambda h,p:(_ for _ in ()).throw(HTTPError('https://invalid',404,'',{},None)))
  with patch.object(report.subprocess,'run',side_effect=[SimpleNamespace(returncode=0),SimpleNamespace(returncode=0,stdout=b'')]):
   with self.assertRaises(ValueError):reader('a'*40,'empty.json')
 def test_silent_empty_fixed_read_is_not_valid_evidence(self):
  reader=report.ReportEvidenceReader(Path('.'),report.legacy.read_at)
  with patch.object(report.legacy.bundle,'git',side_effect=subprocess.CalledProcessError(1,['git'])),patch.object(report.subprocess,'run',return_value=SimpleNamespace(returncode=1)):
   with self.assertRaises(report.ReportEvidenceReadError):reader('a'*40,report.legacy.MANIFEST)
 def test_equal_count_membership_drift_is_blocked(self):
  calls=0
  def api(path):
   nonlocal calls
   calls+=1
   return {'total_count':1,'workflow_runs':[{'id':calls,'created_at':'2026-10-21T00:00:00Z','head_sha':'a'*40,'run_attempt':1}]}
  with self.assertRaisesRegex(ValueError,'unstable'):
   report.report_witness_runs(api,'learned_shadow_v1.yml','2026-10-21','2026-10-21')
 def test_early_success_survives_later_failed_attempt(self):
  latest={'id':1,'run_attempt':2,'status':'completed','conclusion':'failure','head_sha':'a'*40,'created_at':'2026-10-21T00:00:00Z','repository':{'full_name':report.f.old.REPO},'path':report.f.WORKFLOW,'head_branch':'main','event':'workflow_dispatch'}
  early={**latest,'run_attempt':1,'conclusion':'success'}
  def api(path):
   if '/attempts/1/jobs?' in path:return {'total_count':1,'jobs':[{'id':11,'name':'audit','run_id':1,'run_attempt':1}]}
   if path.endswith('/attempts/1'):return early
   raise AssertionError(path)
  rows=report.report_audit_jobs(api,[latest])
  self.assertEqual(len(rows),1)
  self.assertEqual(rows[0]['run_attempt'],1)
 def test_zip_entry_cap_checked_before_zipfile_allocations(self):
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as archive:
   archive.writestr('a',b'');archive.writestr('b',b'')
  with tempfile.TemporaryDirectory() as tmp,patch.object(inventory,'ARTIFACT_ENTRY_LIMIT',1,create=True):
   with self.assertRaisesRegex(ValueError,'entry count'):inventory.extract_artifact(stream.getvalue(),tmp)
   self.assertEqual(list(Path(tmp).iterdir()),[])
 def test_all_paths_validated_before_first_write(self):
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as archive:
   archive.writestr('ok',b'yes');archive.writestr('../bad',b'no')
  with tempfile.TemporaryDirectory() as tmp:
   with self.assertRaises(ValueError):inventory.extract_artifact(stream.getvalue(),tmp)
   self.assertEqual(list(Path(tmp).iterdir()),[])
 def test_disk_budget_checked_before_network(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(inventory.shutil,'disk_usage',return_value=SimpleNamespace(free=0)),patch.object(inventory,'github_response') as get:
   with self.assertRaisesRegex(ValueError,'disk'):
    inventory.download_artifact({'id':1,'size_in_bytes':1,'digest':'a'*64},Path(tmp)/'a.zip')
   get.assert_not_called()

class HistoryScopeTests(unittest.TestCase):
 def test_old_created_run_can_be_rerun_during_new_event_window(self):
  from datetime import datetime,timezone
  calls=[]
  def api(path):
   calls.append(path)
   return {'total_count':2,'workflow_runs':[{'id':1 if 'page=2' in path else 2,'created_at':'2026-09-01T00:00:00Z' if 'page=2' in path else '2026-10-21T00:00:00Z'}]}
  start,end=report.report_witness_window([{'issued_at_utc':'2026-10-21T00:00:00Z','target_utc':'2026-10-21T01:00:00Z'}],{'target_window_seconds':1500},datetime(2026,10,21,2,tzinfo=timezone.utc),'r2',api)
  self.assertEqual(start,'2026-09-01')
  self.assertEqual(end,'2026-10-21')

class MoreReauditTests(unittest.TestCase):
 def test_job_cannot_borrow_other_run_or_attempt(self):
  run={'id':1,'run_attempt':1,'status':'completed','conclusion':'success','repository':{'full_name':report.f.old.REPO},'path':report.f.WORKFLOW,'head_branch':'main','event':'workflow_dispatch'}
  for bad in ({'run_id':2,'run_attempt':1},{'run_id':1,'run_attempt':2}):
   job={'id':11,'name':'audit','status':'completed','conclusion':'success','started_at':'2026-10-21T00:00:00Z','completed_at':'2026-10-21T00:01:00Z',**bad}
   with self.subTest(bad=bad),self.assertRaisesRegex(ValueError,'job'):
    report.report_audit_jobs(lambda path:{'total_count':1,'jobs':[job]},[run])
 def test_duplicate_job_ids_rejected_even_when_unique_count_matches_total(self):
  run={'id':1,'status':'completed','conclusion':'success','repository':{'full_name':report.f.old.REPO},'path':report.f.WORKFLOW,'head_branch':'main','event':'workflow_dispatch'}
  job={'id':11,'run_id':1,'run_attempt':1,'name':'audit'}
  with self.assertRaisesRegex(ValueError,'job'):
   report.report_audit_jobs(lambda path:{'total_count':1,'jobs':[job,job]},[run])
 def test_incompressible_source_cap_leaves_room_for_zip_container(self):
  import pit_retention_preparation as producer
  import test_oss_inventory_retention as fixture
  import os
  with tempfile.TemporaryDirectory() as tmp,patch.object(producer,'LIMIT_BYTES',128):
   path=Path(tmp)/'source.py';path.write_bytes(os.urandom(128))
   _,receipt=producer.prepare_bundle(tmp,{'source.py':producer.digest(path.read_bytes())},fixture.NOW)
   payload=(Path(tmp)/receipt['bundle_path']).read_bytes()
   self.assertGreater(len(payload),128)
   with patch.object(inventory,'LIMIT',128),patch.object(inventory,'STORED_ZIP_LIMIT',2048):
    b=fixture.Bucket();proof=inventory.upload_one(b,payload,'.zip',inventory.check_bucket(b),now=fixture.NOW)
    self.assertEqual(proof['byte_count'],len(payload))
 def test_zip64_directory_budget_and_crc_failure_cleanup(self):
  import struct
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as archive:archive.writestr('a',b'payload')
  raw=stream.getvalue();end=raw.rfind(b'PK\x05\x06')
  fields=list(struct.unpack('<4s4H2LH',raw[end:end+22]));offset=fields[6];cd_size=fields[5]
  record=struct.pack('<4sQ2H2L4Q',b'PK\x06\x06',44,45,45,0,0,1,1,cd_size,offset)
  locator=struct.pack('<4sLQL',b'PK\x06\x07',0,end,1)
  fields[3]=fields[4]=65535;fields[5]=fields[6]=0xffffffff
  zip64=raw[:end]+record+locator+struct.pack('<4s4H2LH',*fields)
  with tempfile.TemporaryDirectory() as tmp:
   inventory.extract_artifact(zip64,tmp)
   self.assertEqual((Path(tmp)/'a').read_bytes(),b'payload')
  bad=raw.replace(b'payload',b'PAYLOAD',1)
  with tempfile.TemporaryDirectory() as tmp:
   with self.assertRaises(zipfile.BadZipFile):inventory.extract_artifact(bad,tmp)
   self.assertEqual(list(Path(tmp).iterdir()),[])

class NestedBudgetTests(unittest.TestCase):
 def test_nested_zip_metadata_is_bounded_before_frozen_verifier(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'part.zip'
   with zipfile.ZipFile(path,'w') as archive:
    archive.writestr('bundle_manifest.json',b'{}');archive.writestr('data/a',b'')
   with patch.object(inventory,'INVENTORY_MEMBER_LIMIT',1,create=True):
    with self.assertRaisesRegex(ValueError,'entry count'):inventory.validate_staged_packages(tmp)
 def test_nested_source_and_json_caps_preserve_frozen_raw_limit(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'part.zip'
   with zipfile.ZipFile(path,'w') as archive:
    archive.writestr('bundle_manifest.json',b'{}');archive.writestr('data/a',b'12345')
   with patch.object(inventory,'LIMIT',4):
    with self.assertRaisesRegex(ValueError,'source bytes'):inventory.validate_staged_packages(tmp)
   inventory.validate_staged_packages(tmp)

if __name__=='__main__':unittest.main()

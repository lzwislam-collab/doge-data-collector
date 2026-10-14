"""End-to-end ZIP loader tests; all remote inputs are explicit test fixtures."""
import copy,hashlib,io,json,unittest,zipfile
from unittest.mock import patch
from datetime import datetime,timezone
import phase3_batches as b
import phase3_runtime as runtime
import phase2_forward_v2 as f

class RemoteCertificate(unittest.TestCase):
 def bundle(self):
  self.run={'id':1,'repository':{'full_name':f.old.REPO},'path':runtime.WORKFLOW,'head_branch':'main','head_sha':'a'*40,'event':'workflow_dispatch','status':'completed','conclusion':'success','created_at':'2026-10-14T12:00:00Z'}
  self.job={'name':'independent','status':'completed','conclusion':'success','started_at':'2026-10-14T12:01:00Z','completed_at':'2026-10-14T12:03:00Z'}
  self.cert={'kind':'DOGE_PHASE3_REGISTERED_AUDIT_BATCH_V1','source_commit':self.run['head_sha'],'generated_at_utc':'2026-10-14T12:02:00Z','batch_events_cap':20,'event_sha256':{},'score_sha256':{},'assessment_registration_identity':'b'*64,'qualified':[],'rejected':{}}
  self.files={'frozen.py':b'frozen'};self.now=datetime(2026,10,14,13,tzinfo=timezone.utc)
 def archive(self,duplicate=False):
  buf=io.BytesIO()
  with zipfile.ZipFile(buf,'w') as z:
   z.writestr('out/batch_certificate.json',json.dumps(self.cert))
   if duplicate:z.writestr('another/batch_certificate.json',json.dumps(self.cert))
  raw=buf.getvalue();self.art={'id':2,'name':'phase3-independent-1','expired':False,'digest':'sha256:'+hashlib.sha256(raw).hexdigest()};return raw
 def load(self,raw,code=b'frozen'):
  def api(path):
   if path.endswith('/jobs'):return {'jobs':[self.job]}
   if path.endswith('/artifacts'):return {'artifacts':[self.art]}
   return self.run
  class Opener:
   def open(self,*args,**kwargs):return io.BytesIO(raw)
  with patch.object(b.forward.frozen,'api',side_effect=api),patch.object(b,'build_opener',return_value=Opener()),patch.object(runtime,'read_at',return_value=code):
   return b.load([1],self.files,self.now)
 def test_real_zip_path_is_parsed_then_checked(self):
  self.bundle();raw=self.archive();self.assertEqual(self.load(raw),[self.cert])
 def test_digest_tamper_rejected(self):
  self.bundle();raw=self.archive();self.art['digest']='sha256:'+'0'*64
  with self.assertRaises(ValueError):self.load(raw)
 def test_frozen_source_mismatch_rejected(self):
  self.bundle();raw=self.archive()
  with self.assertRaises(ValueError):self.load(raw,b'changed')
 def test_duplicate_certificate_entries_rejected(self):
  self.bundle();raw=self.archive(True)
  with self.assertRaises(ValueError):self.load(raw)
 def test_expired_artifact_and_non_main_server_run_rejected(self):
  for key,value in (('expired',True),('head_branch','feature'),('event','pull_request')):
   with self.subTest(key=key):
    self.bundle();raw=self.archive()
    if key=='expired':self.art[key]=value
    else:self.run[key]=value
    with self.assertRaises(ValueError):self.load(raw)
 def test_payload_clock_after_job_finish_rejected(self):
  self.bundle();self.cert['generated_at_utc']='2026-10-14T12:04:00Z';raw=self.archive()
  with self.assertRaises(ValueError):self.load(raw)

if __name__=='__main__':unittest.main()

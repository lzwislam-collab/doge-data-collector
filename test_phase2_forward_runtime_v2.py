import copy,json,tempfile,unittest,subprocess
from unittest.mock import patch
from pathlib import Path
from datetime import datetime,timedelta,timezone
import phase2_forward_v2 as f
import phase2_forward_runtime_v2 as r
import test_phase2_forward_v2 as fixtures
import test_learned_shadow_v1 as quotes

class Runtime(unittest.TestCase):
 def test_rejected_complete_http_responses_retained_before_validation(self):
  class Response:
   def __init__(self,url):self.url=url
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def geturl(self):return self.url
   def read(self,n):return b'{"code":"0","data":[]}'
  with patch.object(r,'urlopen',side_effect=lambda req,timeout:Response(req.full_url)):
   packet=r.capture_target()
  self.assertEqual(set(packet['raw_base64']),set(f.old.URLS))
  with self.assertRaises(ValueError):f.old.quote(packet)
 def test_score_requires_exact_retained_attempt_packet(self):
  with self.assertRaises(ValueError):r.verify_attempt_binding({'event_id':'1_canary','target_packet':{},'attempt_id':'1_canary_attempt_1'}, [])
 def repo(self,root):
  def git(*args):subprocess.run(['git','-c','safe.directory=*',*args],cwd=root,check=True,capture_output=True)
  git('init');git('config','user.email','fixture@example.invalid');git('config','user.name','fixture');git('commit','--allow-empty','-m','initial fixture')
  return git
 def test_registration_uses_exact_fixed_file_set(self):
  with self.assertRaises(ValueError):r.verify_files(Path('.'),{'sha256_lf':{}})
 def test_append_cannot_overwrite_or_escape(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.repo(root);row={'event_id':'1_canary'}
   r.append(root,'events','1_canary',row)
   with self.assertRaises(FileExistsError):r.append(root,'events','1_canary',row)
   with self.assertRaises(ValueError):r.append(root,'events','../x',row)
 def test_canary_never_enters_formal_denominator(self):
  at=datetime(2026,10,20,2,50,tzinfo=timezone.utc)
  e={'event_id':'1_canary','cohort':'canary','target_utc':at.isoformat(),'included_in_formal_statistics':False}
  p=r.plan(f.policy(),[e],[],at)
  self.assertEqual(p['formal_scheduled'],2)
  self.assertEqual(p['formal_issued'],0)
  self.assertEqual(p['matured_missing_issuance'],2)
  self.assertEqual(p['canary_issued'],1)
 def test_old_events_are_replayed_and_changed_predictions_rejected(self):
  at=datetime(2026,10,20,0,5,tzinfo=timezone.utc)
  m=f.make_model(json.loads((f.ROOT/f.PARENT).read_bytes()));frame=fixtures.Forward().frame(at)
  bm=json.loads((f.ROOT/f.old.PACKAGE).read_bytes())['models']['1']['full'];bx=f.old.causal_features(f.benchmark_frame(frame),at)
  bp=r.prior.predict(bm,[[bx[j] for j in bm['columns']]])[0]
  e=f.issue(f.policy(),m,frame,quotes.Shadow().packet(at),at,bp,'formal')
  r.verify_event(e,f.policy(),m)
  e['candidate_p']['selected']=.999
  with self.assertRaises(ValueError):r.verify_event(e,f.policy(),m)
 def test_deleted_published_record_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   git=self.repo(root)
   r.append(root,'events','1_canary',{'event_id':'1_canary'})
   git('add','.');git('commit','-m','fixture')
   (root/f.BASE/'events/1_canary.json').unlink()
   with self.assertRaises(ValueError):r.guard_history(root)
 def test_missing_per_event_source_or_registration_cannot_qualify(self):
  with self.assertRaises(ValueError):r.provenance({'issued_at_utc':'2026-10-14T10:03:00Z'}, {}, [])
 def test_registration_after_issue_cannot_be_reused_as_preregistration(self):
  e={'issued_at_utc':'2026-10-14T10:03:00Z','registration_run_id':1,'source_run_id':2,
   'input_source_commit':'a'*40,'input_source_sha256':'a'*64,'frame':{}}
  run={'id':1,'head_sha':'a'*40,'head_branch':'main','repository':{'full_name':f.old.REPO},'path':f.WORKFLOW,
   'event':'workflow_dispatch','status':'completed','conclusion':'success','created_at':'2026-10-14T10:00:00Z',
   'audit_job':{'name':'audit','status':'completed','conclusion':'success','started_at':'2026-10-14T10:00:01Z','completed_at':'2026-10-14T10:04:00Z'}}
  with self.assertRaises(ValueError):r.provenance(e,{},[run])

if __name__=='__main__':unittest.main()

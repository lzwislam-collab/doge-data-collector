"""Actual Git snapshots; later appends must not enter an older review."""
import copy,hashlib,io,json,subprocess,tempfile,unittest,zipfile
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
import prediction_release_control as release
import test_phase3_independent as fixtures

class ReviewSnapshot(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.git('init');self.git('-c','user.name=test','-c','user.email=test@example.invalid','commit','--allow-empty','-m','empty')
  self.empty=self.git('rev-parse','HEAD').strip()
  self.a,self.p,self.events,self.scores,self.q,self.end=fixtures.Independent().cohort()
 def git(self,*args):
  return subprocess.check_output(['git','-c','safe.directory=*',*args],cwd=self.root,text=True,stderr=subprocess.DEVNULL)
 def write(self,folder,row):
  path=self.root/release.f.BASE/folder/(row['event_id']+'.json');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(release.encode(row));return path
 def commit(self):
  self.git('add','.');self.git('-c','user.name=test','-c','user.email=test@example.invalid','commit','-m','append');return self.git('rev-parse','HEAD').strip()
 def records(self,head,folder):
  with patch.object(release,'ROOT',self.root):return release.review_records(head,folder)
 def test_later_events_and_scores_do_not_enter_zero_event_old_review(self):
  self.write('events',self.events[0]);self.write('scores',self.scores[0]);current=self.commit()
  old=self.a.evaluate(self.p,self.records(self.empty,'events'),self.records(self.empty,'scores'),[],release.f.utc('2026-10-15T00:00:00Z'),True,True)
  self.assertEqual(old['scheduled_events'],0);self.assertEqual(old['qualified_events'],0);self.assertFalse(old['statistical_gate_passed'])
  self.assertEqual(len(self.records(current,'events')),1)
 def test_old_snapshot_keeps_original_event_and_excludes_later_backdated_append(self):
  self.write('events',self.events[0]);old=self.commit()
  self.write('events',self.events[1]);self.commit()
  self.assertEqual([e['event_id'] for e in self.records(old,'events')],[self.events[0]['event_id']])
 def test_missing_or_mutated_original_bytes_fail_closed(self):
  path=self.write('events',self.events[0]);head=self.commit();original=path.read_bytes()
  for raw in (None,original+b' '):
   if raw is None:path.unlink()
   else:path.write_bytes(raw)
   with self.assertRaises(ValueError):self.records(head,'events')
 def test_future_event_present_in_original_snapshot_is_not_filtered(self):
  self.write('events',self.events[0]);head=self.commit()
  with self.assertRaisesRegex(ValueError,'future/overlapping'):
   self.a.evaluate(self.p,self.records(head,'events'),[],[],release.f.utc('2026-10-15T00:00:00Z'),True,True)
 def test_real_invalid_horizon_still_fails(self):
  bad=copy.deepcopy(self.events[0]);bad['target_utc']=self.events[1]['target_utc'];self.write('events',bad);head=self.commit()
  with self.assertRaisesRegex(ValueError,'future/overlapping'):
   self.a.evaluate(self.p,self.records(head,'events'),[],[],self.end,True,True)
 def test_overlap_guard_remains_active_for_individually_valid_forecasts(self):
  # The actual 120-minute grid and 45+60-minute window cannot overlap.
  # A synthetic hourly grid exercises the distinct pairwise overlap guard;
  # no registered file or production timing is changed.
  fp=copy.deepcopy(release.f.policy());fp['slot_seconds']=3600
  start=release.f.utc(self.events[0]['slot_utc']);rows=copy.deepcopy(self.events[:2])
  for row,slot,delay in ((rows[0],start,40),(rows[1],start+timedelta(hours=1),5)):
   at=slot+timedelta(minutes=delay);row.update(event_id=str(int(slot.timestamp()))+'_formal',slot_utc=slot.isoformat(),issued_at_utc=at.isoformat(),target_utc=(at+timedelta(hours=1)).isoformat());self.write('events',row)
  head=self.commit()
  with patch.object(release.f,'policy',return_value=fp),self.assertRaisesRegex(ValueError,'overlapping forecast observations'):
   self.a.evaluate(self.p,self.records(head,'events'),[],[],self.end,True,True)
 def test_missing_issuance_and_missing_score_remain_in_denominator(self):
  self.write('events',self.events[0]);head=self.commit()
  now=release.f.utc(self.events[0]['slot_utc'])+timedelta(hours=4,minutes=10)
  result=self.a.evaluate(self.p,self.records(head,'events'),self.records(head,'scores'),[],now,True,True)
  self.assertEqual(result['scheduled_events'],2);self.assertEqual(result['qualified_events'],0)
  self.assertEqual({r['reason'] for r in result['missing']},{'MISSING_ISSUANCE','MISSING_SCORE'})
  self.assertFalse(result['statistical_gate_passed'])
 def test_unknown_commit_and_unsafe_scope_fail_closed(self):
  for head,folder in (('f'*40,'events'),('HEAD','events'),(self.empty,'../scores')):
   with self.assertRaises(ValueError):self.records(head,folder)
 def test_zip_review_source_replays_server_head_instead_of_current_records(self):
  self.write('events',self.events[0]);self.write('scores',self.scores[0]);head=self.commit()
  self.verify_source(self.empty,'2026-10-15T00:02:00+00:00',[],[],[],0,0)
  self.verify_source(head,'2026-10-20T04:10:00+00:00',self.events[:1],self.scores[:1],self.q[:1],2,1)
 def verify_source(self,head,at,events,scores,qualified,scheduled,valid):
  assessment=self.a.evaluate(self.p,events,scores,qualified,release.f.utc(at),True,True)
  assessment.update(at_utc=at,independent_registration={'run_id':2},forward_runtime={'qualified':qualified,'network_audit_covers_all_events':True})
  archive=io.BytesIO()
  with zipfile.ZipFile(archive,'w') as z:z.writestr('out/assessment.json',release.encode(assessment))
  raw=archive.getvalue();sha=hashlib.sha256(raw).hexdigest()
  now=release.f.utc(at)
  run={'repository':{'full_name':release.f.old.REPO},'path':release.assessment_runtime.WORKFLOW,'head_branch':'main','head_sha':head,'status':'completed','conclusion':'success','event':'workflow_dispatch','run_attempt':1,'created_at':(now-timedelta(minutes=2)).isoformat()}
  job={'name':'independent','status':'completed','conclusion':'success','started_at':(now-timedelta(minutes=1)).isoformat(),'completed_at':(now+timedelta(minutes=1)).isoformat()}
  def api(path):
   if path.endswith('/jobs'):return {'jobs':[job]}
   if path.endswith('/artifacts'):return {'artifacts':[{'name':'phase3-independent-1','id':3,'expired':False,'digest':'sha256:'+sha}]}
   return run
  with patch.object(release,'ROOT',self.root),patch.object(release.assessment_runtime,'guard',return_value=(self.p,{},{})),patch.object(release.forward,'run'),patch.object(release.forward.frozen,'api',side_effect=api),patch.object(release,'_download',return_value=raw),patch.object(release.assessment_runtime,'registration_witness',return_value={'run_id':2}):
   result,_,actual_sha,_=release.review_source(1)
  self.assertEqual(result['scheduled_events'],scheduled);self.assertEqual(result['qualified_events'],valid);self.assertEqual(actual_sha,sha);self.assertFalse(result['statistical_gate_passed'])


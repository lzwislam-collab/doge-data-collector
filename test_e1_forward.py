import copy,importlib,json,subprocess,tempfile,unittest
from contextlib import ExitStack
from pathlib import Path
from datetime import datetime,timedelta,timezone
from urllib.error import HTTPError
from unittest.mock import patch
import phase2_forward_v2 as parent
import test_phase2_forward_v2 as frames
import test_learned_shadow_v1 as packets

class E1Core(unittest.TestCase):
 def engine(self):return importlib.import_module('e1_forward')
 def protocol(self):
  p=parent.policy();p.update(protocol_id='DOGE_E1_FORWARD_20261021',effective_from_utc='2026-10-21T00:00:00Z',observation_end_utc='2027-01-19T00:00:00Z',fixed_review_utc=['2027-01-19T00:25:00Z','2027-02-11T08:00:00Z']);return p
 def test_isolated_policy_reuses_formula_without_mutating_parent(self):
  e=self.engine();original=parent.policy();p=self.protocol();core=e.engine_core(p)
  at=parent.utc('2026-10-21T00:05:00Z');model=json.loads((parent.ROOT/parent.MODEL).read_bytes());frame=frames.Forward().frame(at);packet=packets.Shadow().packet(at)
  old=parent.issue(original,model,frame,packet,at,.5,'formal');new=core.issue(p,model,frame,packet,at,.5,'formal')
  self.assertEqual(new['candidate_p'],old['candidate_p']);self.assertEqual(parent.policy(),original);self.assertNotEqual(new['protocol_id'],old['protocol_id'])
  self.assertEqual(core.score(new,packets.Shadow().packet(at+timedelta(hours=1),101),p)['y'],1)
 def test_new_effective_time_and_expired_target_are_enforced(self):
  e=self.engine();p=self.protocol();core=e.engine_core(p);at=parent.utc(p['effective_from_utc']);model=json.loads((parent.ROOT/parent.MODEL).read_bytes())
  with self.assertRaises(ValueError):core.issue(p,model,frames.Forward().frame(at-timedelta(seconds=1)),packets.Shadow().packet(at),at-timedelta(seconds=1),.5,'formal')
  event=core.issue(p,model,frames.Forward().frame(at),packets.Shadow().packet(at),at,.5,'formal')
  for delay in (-1,1500):
   with self.assertRaises(ValueError):core.score(event,packets.Shadow().packet(at+timedelta(hours=1,seconds=delay)),p)
 def test_unknown_or_changed_policy_has_no_unreviewed_parameters(self):
  e=self.engine();bad=self.protocol();bad['horizon_seconds']=60
  with self.assertRaises(ValueError):e.validate_policy(bad)
 def test_real_git_absence_is_mismatch_but_unknown_commit_is_not(self):
  e=self.engine()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);subprocess.run(['git','init',str(root)],check=True,capture_output=True);path='data/e1_forward_v1/scores/1_formal.json';p=root/path;p.parent.mkdir(parents=True);p.write_bytes(b'original\n')
   def git(*args):return subprocess.check_output(['git','-c','safe.directory=*',*args],cwd=root,stderr=subprocess.DEVNULL)
   git('add','.');git('-c','user.name=test','-c','user.email=test@example.invalid','commit','-m','snapshot');head=git('rev-parse','HEAD').decode().strip()
   self.assertEqual(e.read_pinned(root,head,path),b'original\n');self.assertEqual(e.read_pinned(root,head,'data/e1_forward_v1/scores/2_formal.json'),b'')
   with self.assertRaises(ValueError):e.read_pinned(root,head,'data/e1_forward_v1/scores/2_formal.json',required=True)
   for exc in [HTTPError('https://example.invalid',404,'missing',{},None),PermissionError('denied'),TimeoutError('offline')]:
    with self.assertRaises(type(exc)):e.read_pinned(root,'f'*40,path,remote=lambda h,p:(_ for _ in ()).throw(exc))
 def fixture(self):
  e=self.engine();now=parent.utc('2026-10-21T01:00:00Z');run={'id':1,'path':e.WITNESS,'repository':{'full_name':parent.old.REPO},'head_branch':'main','head_sha':'a'*40,'status':'completed','conclusion':'success','event':'workflow_dispatch','created_at':'2026-10-21T00:06:00Z','audit_job':{'name':'witness','status':'completed','conclusion':'success','started_at':'2026-10-21T00:06:01Z','completed_at':'2026-10-21T00:07:00Z'}};return e,now,run
 def test_absent_candidate_does_not_poison_later_exact_witness(self):
  e,now,run=self.fixture();second=copy.deepcopy(run);second.update(id=2,head_sha='b'*40)
  result=e.verified_witness({'event':b'actual'},[run,second],lambda h,p:b'' if h=='a'*40 else b'actual',{},now,parent.utc('2026-10-21T00:05:00Z'))
  self.assertEqual(result['run_id'],2)
 def test_auth_and_transport_failures_do_not_become_successful_witness(self):
  e,now,run=self.fixture()
  for exc in [PermissionError('denied'),TimeoutError('offline')]:
   with self.assertRaises(type(exc)):e.verified_witness({'event':b'actual'},[run],lambda h,p:(_ for _ in ()).throw(exc),{},now)
 def test_witness_requires_actual_main_success_exact_bytes_and_deadline(self):
  e,now,run=self.fixture()
  for name,value in [('head_branch','feature'),('status','in_progress'),('conclusion','failure'),('event','pull_request')]:
   bad=copy.deepcopy(run);bad[name]=value;self.assertIsNone(e.verified_witness({'event':b'actual'},[bad],lambda h,p:b'actual',{},now))
  self.assertIsNone(e.verified_witness({'event':b'actual'},[run],lambda h,p:b'changed',{},now))
  self.assertIsNone(e.verified_witness({'event':b'actual'},[run],lambda h,p:b'actual',{},parent.utc('2026-10-21T00:07:00Z')))
 def test_registration_must_precede_start_and_never_backdate(self):
  e,now,run=self.fixture();p=self.protocol()
  with self.assertRaises(ValueError):e.check_registration(run,p,{},lambda h,p:b'actual',now)
  run['created_at']='2026-10-20T23:40:00Z';run['audit_job'].update(started_at='2026-10-20T23:40:01Z',completed_at='2026-10-20T23:41:00Z')
  self.assertEqual(e.check_registration(run,p,{},lambda h,p:b'actual',now)['run_id'],1)
 def test_append_never_overwrites_original_or_writes_r2_namespace(self):
  e=self.engine()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);row={'event_id':'1_formal','value':1};e.append_record(root,'events','1_formal',row);p=root/e.BASE/'events/1_formal.json';before=p.read_bytes()
   with self.assertRaises((FileExistsError,ValueError)):e.append_record(root,'events','1_formal',{'event_id':'1_formal','value':2})
   self.assertEqual(p.read_bytes(),before);self.assertFalse((root/parent.BASE).exists())
   for folder in ['../phase2_forward_v2/events','unknown']:
    with self.assertRaises(ValueError):e.append_record(root,folder,'2_formal',row)
 def test_activation_needs_real_prestart_publication_of_locator_and_archive(self):
  e,now,run=self.fixture();p=self.protocol();proof={'run_id':9,'artifact_sha256':'a'*64};registration={}
  with tempfile.TemporaryDirectory() as tmp,patch.object(e,'ROOT',Path(tmp)),patch.object(e.evidence_append_guard,'guard'),patch.object(e,'registration_proof',return_value=proof),patch.object(e,'now_utc',return_value=now):
   root=Path(tmp);(root/e.BASE).mkdir(parents=True);(root/e.LOCATOR).write_bytes(e.encode(proof));(root/e.ARCHIVE).write_bytes(b'original archive')
   with patch.object(e,'candidates',return_value=[]):self.assertIsNone(e.registered(p,registration,{}))
   with patch.object(e,'candidates',return_value=[run]),patch.object(e,'read_pinned',side_effect=lambda root,h,path:e.encode(proof) if path==e.LOCATOR else b'original archive'):
    self.assertIsNone(e.registered(p,registration,{}))
    run['created_at']='2026-10-20T23:40:00Z';run['audit_job'].update(started_at='2026-10-20T23:40:01Z',completed_at='2026-10-20T23:41:00Z')
    self.assertEqual(e.registered(p,registration,{})['activation_publication']['run_id'],1)
 def test_explicit_old_proofs_survive_many_recent_successes(self):
  e=self.engine();recent=[{'id':i,'head_branch':'main','status':'completed','conclusion':'success','created_at':'2026-10-21T01:00:00Z'} for i in range(2,42)];old={'id':1,'head_branch':'main','status':'completed','conclusion':'success','created_at':'2026-10-20T23:00:00Z'}
  with patch.object(e,'api',side_effect=lambda path:{'workflow_runs':recent} if '/workflows/' in path else old),patch.object(e,'run_with_job',side_effect=lambda row:row):
   self.assertIn(1,[r['id'] for r in e.candidates([1])])
 def test_observation_end_is_not_an_extra_issuance_slot(self):
  e=self.engine();p=self.protocol();now=parent.utc(p['observation_end_utc'])+timedelta(hours=3)
  with patch.object(e,'check_state',return_value=(p,{}, {},[],[])),patch.object(e,'now_utc',return_value=now):
   panel=e.panel(online=False)
  self.assertEqual(panel['counts']['matured_fixed_denominator'],1080);self.assertEqual(panel['gaps']['missing_issuance'],1080)
 def test_prestart_callback_persists_activation_pin_without_issuing(self):
  e=self.engine();p=self.protocol();proof={'run_id':1,'activation_publication':{'run_id':2,'head_sha':'a'*40}};at=parent.utc(p['effective_from_utc'])-timedelta(minutes=1)
  with tempfile.TemporaryDirectory() as tmp,patch.object(e,'ROOT',Path(tmp)),patch.object(e,'check_state',return_value=(p,{}, {},[],[])),patch.object(e,'registered',return_value=proof),patch.object(e,'now_utc',return_value=at),patch.dict('os.environ',{'GITHUB_REF':'refs/heads/main','GITHUB_WORKFLOW':'Publish DOGE E1 forward evidence'}),patch.object(e.original.frozen,'capture',side_effect=AssertionError('early capture')):
   result=e.tick();binding=json.loads((Path(tmp)/e.BASE/'bindings/0_formal_prediction.json').read_bytes())
  self.assertEqual(binding['run_id'],2);self.assertEqual(result['new_events'],[]);self.assertEqual(result['status'],'WAIT_ACTIVATION')
 def test_archived_registration_retains_exact_source_binding_after_expiry(self):
  e,now,run=self.fixture();run['created_at']='2026-10-20T23:30:00Z';run['audit_job'].update(started_at='2026-10-20T23:30:01Z',completed_at='2026-10-20T23:31:00Z');reg={'registration_identity':'a'*64};p=self.protocol()
  import io,zipfile,hashlib
  def archive(head):
   stream=io.BytesIO()
   with zipfile.ZipFile(stream,'w') as z:z.writestr('witness.json',json.dumps({'kind':'register','registration_identity':'a'*64,'source_commit':head}))
   return stream.getvalue()
  with tempfile.TemporaryDirectory() as tmp,patch.object(e,'ROOT',Path(tmp)),patch.object(e,'run_with_job',return_value=run),patch.object(e,'now_utc',return_value=now),patch.object(e,'read_pinned',return_value=b'x'),patch.object(e,'api',side_effect=lambda path:{'artifacts':[]} if path.endswith('/artifacts') else run):
   root=Path(tmp);(root/e.BASE).mkdir(parents=True);raw=archive('a'*40);(root/e.ARCHIVE).write_bytes(raw);(root/e.LOCATOR).write_bytes(e.encode({'artifact_sha256':hashlib.sha256(raw).hexdigest()}))
   with self.assertRaises(ValueError):e.registration_proof(1,p,reg,{},require_live=True)
   self.assertEqual(e.registration_proof(1,p,reg,{},require_live=False)['run_id'],1)
   raw=archive('b'*40);(root/e.ARCHIVE).write_bytes(raw);(root/e.LOCATOR).write_bytes(e.encode({'artifact_sha256':hashlib.sha256(raw).hexdigest()}))
   with self.assertRaisesRegex(ValueError,'source SHA'):e.registration_proof(1,p,reg,{},require_live=False)
 def operation(self,at,events=(),scores=()):
  e=self.engine();stack=ExitStack();self.addCleanup(stack.close);tmp=stack.enter_context(tempfile.TemporaryDirectory());root=Path(tmp);p=self.protocol();core=e.engine_core(p)
  for name in (parent.MODEL,parent.old.PACKAGE):
   target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((parent.ROOT/name).read_bytes())
  _,_,run=self.fixture();run['created_at']='2026-10-21T00:02:00Z';run['audit_job'].update(started_at='2026-10-21T00:02:01Z',completed_at='2026-10-21T00:03:00Z');proof={'run_id':9,'activation_publication':{'run_id':8,'head_sha':'b'*40}}
  stack.enter_context(patch.object(e,'ROOT',root));stack.enter_context(patch.object(e,'check_state',return_value=(p,{}, {},list(events),list(scores))));stack.enter_context(patch.object(e,'registered',return_value=proof));stack.enter_context(patch.object(e,'now_utc',return_value=at));stack.enter_context(patch.object(e,'candidates',return_value=[run]));stack.enter_context(patch.dict('os.environ',{'GITHUB_REF':'refs/heads/main','GITHUB_WORKFLOW':'Publish DOGE E1 forward evidence'}))
  return e,root,p,core,run,stack
 def test_tick_issues_real_current_slot_to_separate_namespace(self):
  at=parent.utc('2026-10-21T00:05:00Z');e,root,p,core,run,stack=self.operation(at);frame=frames.Forward().frame(at)
  stack.enter_context(patch.object(e,'read_pinned',return_value=b'source csv'));stack.enter_context(patch.object(e.parent,'frame_from_raw',return_value=frame));stack.enter_context(patch.object(e.original.frozen,'capture',return_value=packets.Shadow().packet(at)))
  result=e.tick();event=json.loads(next((root/e.BASE/'events').glob('*.json')).read_bytes())
  self.assertEqual(result['status'],'PROSPECTIVE_ISSUANCE_CAPTURED');self.assertEqual(event['protocol_id'],p['protocol_id']);self.assertEqual(event['issued_at_utc'],at.isoformat());self.assertEqual(event['source_run_id'],1);self.assertFalse((root/parent.BASE).exists());self.assertTrue(result['request_witness'])
 def event(self):
  e=self.engine();p=self.protocol();at=parent.utc('2026-10-21T00:05:00Z');model=json.loads((parent.ROOT/parent.MODEL).read_bytes());event=e.engine_core(p).issue(p,model,frames.Forward().frame(at),packets.Shadow().packet(at),at,.5,'formal');event.update(input_source_commit='a'*40,input_source_sha256='b'*64,registration_run_id=9,source_run_id=1);return event
 def test_only_genuine_due_score_is_appended_and_expired_capture_never_runs(self):
  event=self.event();target=parent.utc(event['target_utc']);e,root,p,core,run,stack=self.operation(target+timedelta(seconds=10),[event]);run['created_at']='2026-10-21T00:06:00Z';run['audit_job'].update(started_at='2026-10-21T00:06:01Z',completed_at='2026-10-21T00:07:00Z');stack.enter_context(patch.object(e,'read_pinned',return_value=e.encode(event)));stack.enter_context(patch.object(e.original,'capture_target',return_value=packets.Shadow().packet(target+timedelta(seconds=5),101)))
  result=e.tick(event['event_id']);self.assertEqual(result['new_scores'],[event['event_id']]);self.assertEqual(len(list((root/e.BASE/'attempts').glob('*.json'))),1);self.assertFalse((root/parent.BASE).exists())
  e2,root2,p2,core2,run2,stack2=self.operation(target+timedelta(seconds=1500),[event]);stack2.enter_context(patch.object(e2,'read_pinned',return_value=e2.encode(event)));stack2.enter_context(patch.object(e2.original,'capture_target',side_effect=AssertionError('expired capture')))
  self.assertEqual(e2.tick(event['event_id'])['status'],'NO_OPEN_UNSCORED_TARGET');self.assertFalse((root2/e.BASE/'scores').exists())
 def test_missing_prediction_proof_prevents_capture_and_does_not_forge_score(self):
  event=self.event();target=parent.utc(event['target_utc']);e,root,p,core,run,stack=self.operation(target+timedelta(seconds=1),[event]);stack.enter_context(patch.object(e,'read_pinned',return_value=b''));stack.enter_context(patch.object(e.original,'capture_target',side_effect=AssertionError('unwitnessed prediction')))
  result=e.tick(event['event_id']);self.assertEqual(result['new_scores'],[]);self.assertIn(event['event_id'],result['errors']);self.assertFalse((root/e.BASE/'attempts').exists())

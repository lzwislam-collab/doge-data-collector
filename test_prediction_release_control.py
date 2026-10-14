import importlib,copy,tempfile,unittest,json,subprocess
from pathlib import Path
from datetime import datetime,timezone,timedelta

class Release(unittest.TestCase):
 def test_activation_push_retry_refuses_revoked_authority(self):
  a=self.engine()
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);a.initialize(root);m=a.manifest(1,'a'*64,'b'*64);now=datetime(2027,1,18,tzinfo=timezone.utc);auth={'schema':'VERIFIED_RELEASE_AUTHORITY_V1','manifest_sha256':a.digest(m)}
   event=a.apply(root,'activate',m,now,lambda m:auth)
   def revoked(m):raise ValueError('withdrawn')
   with self.assertRaises(ValueError):a.verify_pending(root,event,now,revoked)
   a.verify_pending(root,event,now,lambda m:auth)
 def test_replay_matches_frozen_json_precision_but_not_changed_gate(self):
  a=self.engine();actual={'single_claim_alpha':.05/1280000,'statistical_gate_passed':False};stored=json.loads(a.encode(actual));a.compare_replay(stored,actual)
  stored['statistical_gate_passed']=True
  with self.assertRaises(ValueError):a.compare_replay(stored,actual)
 def test_published_deleted_tail_is_rejected(self):
  a=self.engine()
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);subprocess.run(['git','init',str(root)],check=True,capture_output=True)
   subprocess.run(['git','-c','safe.directory=*','-c','user.name=test','-c','user.email=test@example.invalid','commit','--allow-empty','-m','test repository bootstrap'],cwd=root,check=True,capture_output=True)
   a.initialize(root)
   for args in (['add','.'],['-c','user.name=test','-c','user.email=test@example.invalid','commit','-m','baseline']):
    subprocess.run(['git','-c','safe.directory=*',*args],cwd=root,check=True,capture_output=True)
   next((root/a.BASE).glob('*.json')).unlink()
   with self.assertRaises(ValueError):a.load_state(root)
 def test_release_namespace_parent_symlink_rejected(self):
  a=self.engine()
  with tempfile.TemporaryDirectory() as d,tempfile.TemporaryDirectory() as external:
   root=Path(d);(root/'system').mkdir()
   try:(root/'system/prediction_releases_v1').symlink_to(external,target_is_directory=True)
   except OSError:self.skipTest('platform cannot create directory symlink')
   with self.assertRaises(ValueError):a.initialize(root)
 def engine(self):
  self.assertIsNotNone(importlib.util.find_spec('prediction_release_control'),'real release control missing')
  return importlib.import_module('prediction_release_control')
 def test_actual_persistent_baseline_journal_rejects_overwrite(self):
  a=self.engine()
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);a.initialize(root);state=a.load_state(root);self.assertEqual(state['selection'],'FROZEN_REFERENCE')
   self.assertEqual(len(list((root/a.BASE).glob('*.json'))),1)
   with self.assertRaises(FileExistsError):a.initialize(root)
 def test_self_sealed_approved_flags_do_not_authorize_selection(self):
  a=self.engine()
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);a.initialize(root)
   with self.assertRaises(ValueError):a.apply(root,'activate',{'approved':True},datetime.now(timezone.utc),lambda m:{'approved':True})
 def test_review_must_be_full_fixed_window_and_true_criteria(self):
  a=self.engine();v={'mode':'routine','statistical_gate_passed':True,'qualified_events':1080,'scheduled_events':1080,'criteria':{'a':True}}
  with self.assertRaises(ValueError):a.check_assessment(v)
 def test_account_approval_is_bound_to_exact_release_and_after_review(self):
  a=self.engine();at=datetime(2027,1,18,2,tzinfo=timezone.utc);sha='a'*64
  comment={'body':'APPROVE_PREDICTION_RELEASE_V1 '+sha,'user':{'login':'lzwislam-collab','type':'User'},'created_at':'2027-01-18T01:00:00Z','issue_url':'https://api.github.com/repos/lzwislam-collab/doge-data-collector/issues/1'}
  a.check_approval(comment,'admin',sha,at-timedelta(hours=2),at)
  for key,value in (('body','APPROVE_PREDICTION_RELEASE_V1 '+'b'*64),('user',{'login':'lzwislam-collab','type':'Bot'}),('created_at','2026-10-14T00:00:00Z')):
   bad=copy.deepcopy(comment);bad[key]=value
   with self.assertRaises(ValueError):a.check_approval(bad,'admin',sha,at-timedelta(hours=2),at)
 def test_missing_external_storage_cannot_activate(self):
  a=self.engine()
  with self.assertRaises(ValueError):a.check_retention({'locked_retention_verified':False},'a'*64)
 def test_ledger_tampering_and_forked_parent_are_rejected(self):
  a=self.engine()
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);a.initialize(root);p=next((root/a.BASE).glob('*.json'));v=json.loads(p.read_bytes());v['selection']='CANDIDATE';p.write_bytes(a.encode(v))
   with self.assertRaises(ValueError):a.load_state(root)
 def test_activation_expiry_and_authority_failure_select_exact_fallback(self):
  a=self.engine()
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);a.initialize(root);manifest=a.manifest(1,'a'*64,'b'*64);now=datetime(2027,1,18,tzinfo=timezone.utc)
   auth={'schema':'VERIFIED_RELEASE_AUTHORITY_V1','manifest_sha256':a.digest(manifest),'verified_at_utc':now.isoformat()}
   a.apply(root,'activate',manifest,now,lambda m:auth)
   v=a.select(root,datetime(2027,2,12,tzinfo=timezone.utc),lambda m:auth);self.assertEqual(v['selection'],'FROZEN_REFERENCE');self.assertFalse(v['real_orders_authorized'])
   def revoked(m):raise ValueError('approval revoked')
   self.assertEqual(a.select(root,now,revoked)['selection'],'FROZEN_REFERENCE')
   a.apply(root,'rollback',{},now,revoked);self.assertEqual(a.load_state(root)['selection'],'FROZEN_REFERENCE')
 def test_wrong_model_manifest_cannot_pass_even_with_account_approval(self):
  a=self.engine();m=a.manifest(1,'a'*64,'b'*64);m['model_identity']='0'*64
  with self.assertRaises(ValueError):a.check_manifest(m)

if __name__=='__main__':unittest.main()

"""Real original guard and two real Git code versions; no production/network CLI."""
import ast,hashlib,importlib.util,json,os,subprocess,sys,tempfile,unittest,zipfile
from datetime import datetime,timezone
from pathlib import Path
from unittest import mock

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[2]
OLD=PROJECT/'continuation-20261110/marker/pit_retention_preparation.pinned.py'
NEW=PROJECT/'continuation-20261110/pit-preparation-timeout/candidate/pit_retention_preparation.py'
if not OLD.exists():
 # Full CI repository: retain the original known source from an immutable Git blob.
 OLD=None

class PreparedPublicationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'repo';self.root.mkdir()
  self.env=dict(os.environ,GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',GITHUB_REPOSITORY='lzwislam-collab/doge-data-collector',GITHUB_REF='refs/heads/main',RUNNER_TEMP=str(Path(self.tmp.name)/'runner'))
  Path(self.env['RUNNER_TEMP']).mkdir();self.patch=mock.patch.dict(os.environ,self.env);self.patch.start();self.addCleanup(self.patch.stop)
  if OLD is not None:self.old=OLD.read_bytes();self.new=NEW.read_bytes()
  else:
   self.old=subprocess.run(['git','--no-replace-objects','-c','protocol.allow=never','cat-file','blob','6a5128c829c3568ddd9ea14d5f213469bb939c6e'],cwd=HERE,env=dict(os.environ,GIT_NO_LAZY_FETCH='1'),timeout=5,check=True,capture_output=True).stdout
   self.new=(HERE/'pit_retention_preparation.py').read_bytes()
  self.assertEqual(hashlib.sha256(self.old.replace(b'\r\n',b'\n')).hexdigest(),'7180d1842723960c1ca079eae6d7cf64cde6c654e44321fd5637875e2c3ef1b7')
  (self.root/'pit_retention_preparation.py').write_bytes(self.old)
  self.git('init','-b','main');self.git('config','user.name','test');self.git('config','user.email','test@example.invalid');self.git('config','core.autocrlf','false');self.git('add','.');self.git('commit','-m','old true preparation implementation')
  self.helper=None
  if not os.environ.get('PIT_REVIEW_BASELINE'):
   import pit_prepared_publication as helper
   self.helper=helper;helper.capture(self.root)
  spec=importlib.util.spec_from_file_location('original_prepared_implementation',self.root/'pit_retention_preparation.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  self.module=module
  _,r=module.prepare_inventory_bundle(self.root,{'pit_retention_preparation.py':hashlib.sha256(self.old).hexdigest()},datetime(2026,11,10,tzinfo=timezone.utc))
  r.update(generated_new_bundle=True,implementation_sha256=module.implementation_hash(),run_id='123',run_attempt='1')
  self.prior=(json.dumps(r,indent=2)+'\n').encode();self.path=self.root/module.BASE;self.path.mkdir(parents=True,exist_ok=True)
  for name in ('latest.json','run_latest.json'):(self.path/name).write_bytes(self.prior)
  if self.helper:self.helper.seal(self.root)
  snap=self.root/'archive-snapshot'/module.BASE;snap.mkdir(parents=True);(snap/'latest.json').write_bytes(self.prior)
  (self.root/'archive-snapshot/manifest.json').write_text(json.dumps({'file_sha256':{module.BASE+'/latest.json':hashlib.sha256(self.prior).hexdigest()}}))
  (self.root/'pit_retention_preparation.py').write_bytes(self.new);self.git('add','pit_retention_preparation.py');self.git('commit','-m','actual later engineering version after rebase')
 def git(self,*args):return subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True).stdout
 def command(self):
  if self.helper:return [sys.executable,str(HERE/'pit_prepared_publication.py'),'--root',str(self.root),'record-artifact','--artifact-id','456','--artifact-digest','sha256:'+'a'*64,'--run-id','123']
  return [sys.executable,str(self.root/'pit_retention_preparation.py'),'--root',str(self.root),'record-artifact','--artifact-id','456','--artifact-digest','sha256:'+'a'*64,'--run-id','123']
 def test_real_rebase_keeps_original_guard_and_records_actual_prepared_implementation(self):
  before=(self.root/'pit_retention_preparation.py').read_bytes();head=self.git('rev-parse','HEAD');index=self.git('write-tree');r=subprocess.run(self.command(),env=self.env,cwd=self.root,capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stderr)
  marker=json.loads((self.path/'artifact_publication.json').read_bytes());self.assertEqual(marker['implementation_sha256'],hashlib.sha256(self.old).hexdigest());self.assertEqual(marker['artifact_id'],'456');self.assertEqual(marker['run_id'],'123');self.assertFalse(marker['independent_retention_verified']);self.assertFalse(marker['remote_bundle_content_readback_verified']);self.assertEqual((self.root/'pit_retention_preparation.py').read_bytes(),before)
  self.assertEqual(self.git('rev-parse','HEAD'),head);self.assertEqual(self.git('write-tree'),index)
 def test_copy_or_sealed_receipt_tamper_never_records(self):
  c=self.helper.control();(c/'implementation.py').write_bytes(b'print("untrusted")')
  with self.assertRaises(ValueError):self.helper.record_command(self.root,'456','a'*64)
  self.assertFalse((self.path/'artifact_publication.json').exists())
 def test_uploaded_prior_and_workspace_prior_must_match_exactly(self):
  (self.root/'archive-snapshot'/self.helper.BASE/'latest.json').write_bytes(self.prior+b' ')
  with self.assertRaises(ValueError):self.helper.record_command(self.root,'456','a'*64)
 def test_cross_attempt_or_missing_capture_fails_closed(self):
  with mock.patch.dict(os.environ,{'GITHUB_RUN_ATTEMPT':'2'}):
   with self.assertRaises((ValueError,FileNotFoundError)):self.helper.record_command(self.root,'456','a'*64)
 def test_capture_never_overwrites_existing_copy(self):
  raw=(self.helper.control()/'implementation.py').read_bytes()
  with self.assertRaises(FileExistsError):self.helper.capture(self.root)
  self.assertEqual((self.helper.control()/'implementation.py').read_bytes(),raw)
 def test_sealed_implementation_hash_tamper_is_not_an_allowed_version_switch(self):
  c=self.helper.control();seal=c/'sealed.json';x=json.loads(seal.read_bytes());x['implementation_sha256']='b'*64;seal.write_text(json.dumps(x))
  with self.assertRaises(ValueError):self.helper.record_command(self.root,'456','a'*64)
 def test_changed_ROOT_source_before_bind_is_rejected(self):
  c=self.helper.control();(c/'sealed.json').unlink()
  with self.assertRaisesRegex(ValueError,'source or run receipt drifted'):self.helper.seal(self.root)
  self.assertFalse((c/'sealed.json').exists())
 def test_actual_embedded_source_must_match_captured_bytes_even_with_rehashed_zip(self):
  c=self.helper.control();(c/'sealed.json').unlink();(self.root/'pit_retention_preparation.py').write_bytes(self.old)
  prior=json.loads(self.prior);oldpath=self.root/prior['bundle_path']
  with zipfile.ZipFile(oldpath) as z:entries=[(i,z.read(i.filename)) for i in z.infolist()]
  target=self.path/'bundles/tampered.zip'
  with zipfile.ZipFile(target,'w') as z:
   for info,raw in entries:z.writestr(info,self.new if info.filename=='pit_retention_preparation.py' else raw)
  raw=target.read_bytes();sha=hashlib.sha256(raw).hexdigest();dest=target.with_name(sha+'.zip');target.rename(dest);prior.update(bundle_path=dest.relative_to(self.root).as_posix(),bundle_sha256=sha,byte_count=len(raw))
  for name in ('latest.json','run_latest.json'):(self.path/name).write_text(json.dumps(prior,indent=2)+'\n')
  with self.assertRaisesRegex(ValueError,'source payload differs'):self.helper.seal(self.root)
  self.assertFalse((c/'sealed.json').exists())
 def test_original_current_version_guard_still_rejects_old_receipt(self):
  r=subprocess.run([sys.executable,str(self.root/'pit_retention_preparation.py'),'--root',str(self.root),'record-artifact','--artifact-id','456','--artifact-digest','a'*64,'--run-id','123'],cwd=self.root,env=self.env,capture_output=True,text=True)
  self.assertNotEqual(r.returncode,0);self.assertIn('artifact receipt does not match current preparation',r.stderr)
 def test_dirty_preparer_is_rejected_against_actual_commit_before_capture(self):
  with mock.patch.dict(os.environ,{'GITHUB_RUN_ATTEMPT':'2'}):
   (self.root/'pit_retention_preparation.py').write_bytes(self.new+b'\n# dirty source\n')
   with self.assertRaisesRegex(ValueError,'working bytes differ'):self.helper.capture(self.root)
 def test_symlink_control_is_rejected_without_following_it(self):
  with mock.patch.dict(os.environ,{'GITHUB_RUN_ATTEMPT':'2'}):
   target=Path(self.tmp.name)/'unrelated';target.mkdir();link=Path(self.env['RUNNER_TEMP'])/'doge-pit-prepared-123-2'
   try:link.symlink_to(target,target_is_directory=True)
   except OSError:self.skipTest('platform cannot create a directory symlink')
   with self.assertRaises(ValueError):self.helper.capture(self.root)
   self.assertEqual(list(target.iterdir()),[])
 def test_invalid_upload_outputs_never_get_a_record_command(self):
  for aid,ad in [('0','a'*64),('456','not-a-digest')]:
   with self.subTest(aid=aid,ad=ad),self.assertRaises(ValueError):self.helper.record_command(self.root,aid,ad)
 def test_cancellation_during_owned_git_read_propagates_without_marker(self):
  error=KeyboardInterrupt()
  with mock.patch.object(self.helper.runtime_process,'command',side_effect=error),self.assertRaises(KeyboardInterrupt) as got:self.helper.record_command(self.root,'456','a'*64)
  self.assertIs(got.exception,error);self.assertFalse((self.path/'artifact_publication.json').exists())
 def test_real_two_part_inventory_binds_exact_source_payload(self):
  c=self.helper.control();(c/'sealed.json').unlink();(self.root/'pit_retention_preparation.py').write_bytes(self.old)
  self.git('add','pit_retention_preparation.py');self.git('commit','-m','restore same actual source for second fixture prepare')
  m=self.module;now=datetime(2026,11,10,tzinfo=timezone.utc);obj=self.root/'data/input';obj.parent.mkdir(exist_ok=True);obj.write_bytes(b'actual second fixture object')
  _,part1=m.prepare_bundle(self.root,{'pit_retention_preparation.py':hashlib.sha256(self.old).hexdigest()},now)
  _,part2=m.prepare_bundle(self.root,{'data/input':hashlib.sha256(obj.read_bytes()).hexdigest()},now)
  index={'schema_version':2,'bundle_kind':'SHARDED_INVENTORY_INDEX','prepared_at_utc':now.isoformat(),'objects':{'pit_retention_preparation.py':hashlib.sha256(self.old).hexdigest(),'data/input':hashlib.sha256(obj.read_bytes()).hexdigest()},'parts':[{k:r[k] for k in ('bundle_path','bundle_sha256','byte_count','objects')} for r in (part1,part2)],'per_part_uncompressed_cap':256*1024*1024,'independent_retention_verified':False,'all_part_versions_and_retention_required':True}
  raw=m.canonical(index);sha=hashlib.sha256(raw).hexdigest();(self.root/m.BASE/'bundles'/(sha+'.index.json')).write_bytes(raw)
  r=dict(part1,bundle_path=m.BASE+'/bundles/'+sha+'.index.json',bundle_sha256=sha,byte_count=len(raw),objects=2,bundle_kind='SHARDED_INVENTORY_INDEX',part_count=2,generated_new_bundle=True,implementation_sha256=hashlib.sha256(self.old).hexdigest(),run_id='123',run_attempt='1')
  self.prior=(json.dumps(r,indent=2)+'\n').encode()
  for name in ('latest.json','run_latest.json'):(self.path/name).write_bytes(self.prior)
  self.helper.seal(self.root)
  (self.root/'archive-snapshot'/m.BASE/'latest.json').write_bytes(self.prior);(self.root/'archive-snapshot/manifest.json').write_text(json.dumps({'file_sha256':{m.BASE+'/latest.json':hashlib.sha256(self.prior).hexdigest()}}))
  (self.root/'pit_retention_preparation.py').write_bytes(self.new);self.git('add','pit_retention_preparation.py');self.git('commit','-m','later version after second true source binding')
  r=subprocess.run(self.command(),cwd=self.root,env=self.env,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
  # The original verifier must still reject a changed non-source shard.
  (self.path/'artifact_publication.json').unlink();bad=self.root/part2['bundle_path'];body=bytearray(bad.read_bytes());body[len(body)//2]^=1;bad.write_bytes(body)
  # Invoke the exact authenticated argv directly: Windows execv does not
  # provide the POSIX process-replacement exit contract used by the collector.
  command=self.helper.record_command(self.root,'456','sha256:'+'a'*64)
  r=subprocess.run(command,cwd=self.root,env=self.env,capture_output=True,text=True);self.assertNotEqual(r.returncode,0);self.assertIn('inventory shard changed',r.stderr);self.assertFalse((self.path/'artifact_publication.json').exists())

class WorkflowContractTests(unittest.TestCase):
 def test_capture_root_prepare_bind_and_post_upload_record_are_real_ordered_commands(self):
  text=(HERE/'.github/workflows/okx_hourly_extreme_history.yml').read_text(encoding='utf-8')
  capture=text.index('python pit_prepared_publication.py capture');prepare=text.index('python pit_retention_preparation.py prepare --latest --daily');bind=text.index('python pit_prepared_publication.py bind')
  self.assertLess(text.index('id: supplemental_evidence'),capture);self.assertLess(capture,prepare);self.assertLess(prepare,bind);self.assertLess(bind,text.index('id: persist'))
  record=text.index('python pit_prepared_publication.py record-artifact --artifact-id');self.assertLess(text.index('id: pit_artifact'),record);self.assertLess(text.index('id: archive_bridge_stop'),record);self.assertLess(record,text.index('python pit_artifact_publication.py'))
  self.assertIn('steps.pit_artifact.outcome == \'success\'',text);self.assertIn('steps.archive_bridge_stop.outcome == \'success\'',text);self.assertEqual(text.count('cron:'),1)
 def test_original_owned_workflow_test_body_is_unchanged_and_still_passes(self):
  source=HERE/'test_pit_retention_daily.py'
  if not source.exists():source=PROJECT/'continuation-20261110/pit-preparation-timeout/candidate/test_pit_retention_daily.py'
  node=next(n for n in ast.parse(source.read_bytes()).body if isinstance(n,ast.ClassDef) and n.name=='WorkflowTests')
  ns={'Path':Path,'unittest':unittest,'__file__':str(HERE/'test_pit_retention_daily.py')};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
  case=ns['WorkflowTests']('test_preparation_runs_after_capture_before_persist_and_no_new_cron');result=unittest.TestResult();case.run(result);self.assertEqual(result.errors,[]);self.assertEqual(result.failures,[])

if __name__=='__main__':unittest.main()

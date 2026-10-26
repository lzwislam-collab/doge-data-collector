"""Offline real-Git regression tests; remotes are temporary local directories."""
import json, shutil, subprocess, tempfile, unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
import pit_retention_preparation as prep
try:
    import pit_receipt_publish as publisher
except ImportError:
    publisher = None

def git(root, *args, check=True):
    return subprocess.run(['git', '-C', str(root), *args], check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)

class ReceiptPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.remote = self.base/'remote.git'
        subprocess.run(['git','init','--bare',str(self.remote)],check=True,capture_output=True)
        self.root = self.base/'source'; git(self.base,'clone',str(self.remote),str(self.root))
        git(self.root,'checkout','-b','main'); git(self.root,'config','user.name','Offline Test')
        git(self.root,'config','user.email','offline@example.invalid')
        shutil.copyfile(prep.__file__,self.root/'pit_retention_preparation.py')
        (self.root/'source.py').write_bytes(b'original fixture\n')
        _,receipt=prep.prepare_bundle(self.root,{'source.py':prep.digest((self.root/'source.py').read_bytes())},datetime(2026,1,1,tzinfo=timezone.utc))
        receipt.update(status='NOT_CONFIGURED',generated_new_bundle=True,run_id='42',implementation_sha256=prep.implementation_hash())
        self.latest=self.root/prep.BASE/'latest.json'; self.latest.write_text(json.dumps(receipt))
        self.marker=self.root/prep.BASE/'artifact_publication.json'; self.marker.write_text('{"old":true}\n')
        (self.root/'unrelated.txt').write_bytes(b'old\n')
        git(self.root,'add','.'); git(self.root,'commit','-m','fixture'); git(self.root,'push','origin','main')
        self.before=git(self.root,'rev-parse','HEAD').stdout
        prep.record_artifact(self.root,'7','a'*64,'42')

    def publish(self, **kwargs):
        self.assertIsNotNone(publisher,'isolated publisher is not implemented')
        return publisher.publish(self.root,'42',**kwargs)

    def remote_marker(self):
        return json.loads(git(self.remote,'show','main:'+prep.BASE+'/artifact_publication.json').stdout)

    def test_dirty_source_publishes_only_marker_without_touching_source(self):
        (self.root/'unrelated.txt').write_bytes(b'dirty\n')
        (self.root/'untracked.txt').write_bytes(b'keep\n')
        git(self.root,'add','unrelated.txt')
        before=git(self.root,'status','--porcelain=v1','-z').stdout
        result=self.publish()
        self.assertEqual(self.remote_marker()['artifact_id'],'7')
        self.assertEqual(git(self.root,'status','--porcelain=v1','-z').stdout,before)
        self.assertEqual(git(self.root,'rev-parse','HEAD').stdout,self.before)
        self.assertEqual(git(self.remote,'diff-tree','--no-commit-id','--name-only','-r','main').stdout.decode().strip(),prep.BASE+'/artifact_publication.json')
        self.assertEqual(result['status'],'PUBLISHED')

    def test_legacy_pull_reproduces_unstaged_changes_failure(self):
        (self.root/'unrelated.txt').write_bytes(b'dirty\n')
        r=git(self.root,'pull','--rebase','origin','main',check=False)
        self.assertNotEqual(r.returncode,0); self.assertIn(b'unstaged changes',r.stderr)

    def test_marker_digest_mismatch_rejected(self):
        d=json.loads(self.marker.read_bytes());d['bundle_sha256']='b'*64;self.marker.write_text(json.dumps(d))
        with self.assertRaises(ValueError): self.publish()
        self.assertEqual(self.remote_marker(),{'old':True})

    def test_wrong_run_rejected(self):
        self.assertIsNotNone(publisher,'isolated publisher is not implemented')
        with self.assertRaises(ValueError): publisher.publish(self.root,'43')

    def test_corrupt_local_bundle_rejected(self):
        d=json.loads(self.latest.read_bytes()); (self.root/d['bundle_path']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError): self.publish()

    def test_invalid_artifact_output_and_false_retention_claim_rejected(self):
        original=self.marker.read_bytes()
        for key,value in [('artifact_id','0'),('artifact_archive_sha256','bad'),('independent_retention_verified',True)]:
            with self.subTest(key=key):
                d=json.loads(original);d[key]=value;self.marker.write_text(json.dumps(d))
                with self.assertRaises(ValueError):self.publish()

    def test_private_checkout_does_not_materialize_inventory_or_unrelated_files(self):
        self.assertIsNotNone(publisher,'isolated publisher is not implemented')
        original=publisher.git;seen=[]
        def inspect(root,*args,**kwargs):
            if args[:1]==('push',):
                work=Path(root);seen.append(True)
                self.assertFalse((work/'unrelated.txt').exists())
                self.assertFalse((work/'source.py').exists())
                self.assertFalse((work/json.loads(self.latest.read_bytes())['bundle_path']).exists())
            return original(root,*args,**kwargs)
        with patch.object(publisher,'git',side_effect=inspect):self.publish()
        self.assertTrue(seen)

    def peer(self):
        other=self.base/'peer';git(self.base,'clone','--branch','main',str(self.remote),str(other))
        git(other,'config','user.name','Peer');git(other,'config','user.email','peer@example.invalid');return other

    def test_remote_latest_mismatch_rejected(self):
        other=self.peer();p=other/prep.BASE/'latest.json';d=json.loads(p.read_bytes());d['bundle_sha256']='b'*64;p.write_text(json.dumps(d))
        git(other,'add','.');git(other,'commit','-m','advance inventory');git(other,'push','origin','main')
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(self.remote_marker(),{'old':True})

    def test_remote_implementation_change_rejected(self):
        other=self.peer();p=other/'pit_retention_preparation.py';p.write_bytes(p.read_bytes()+b'\n# changed\n')
        git(other,'add','.');git(other,'commit','-m','change producer');git(other,'push','origin','main')
        with self.assertRaises(ValueError):self.publish()

    def test_idempotent_exact_marker(self):
        self.publish();self.assertEqual(self.publish()['status'],'ALREADY_PUBLISHED')

    def test_different_artifact_for_same_bundle_cannot_overwrite(self):
        self.publish();d=json.loads(self.marker.read_bytes());d['artifact_id']='8';self.marker.write_text(json.dumps(d))
        with self.assertRaises(ValueError):self.publish()
        self.assertEqual(self.remote_marker()['artifact_id'],'7')

    def test_non_fast_forward_retries_on_fresh_tree(self):
        self.assertIsNotNone(publisher,'isolated publisher is not implemented')
        other=self.peer();original=publisher.git;once=[]
        def racing(root,*args,**kwargs):
            if args[:1]==('push',) and not once:
                once.append(True);(other/'peer.txt').write_bytes(b'peer\n');git(other,'add','.');git(other,'commit','-m','peer');git(other,'push','origin','main')
            return original(root,*args,**kwargs)
        with patch.object(publisher,'git',side_effect=racing):self.publish()
        self.assertEqual(self.remote_marker()['artifact_id'],'7');self.assertEqual(git(self.remote,'show','main:peer.txt').stdout,b'peer\n')

    def test_failed_push_cleans_worktree_preserves_original_error(self):
        self.assertIsNotNone(publisher,'isolated publisher is not implemented')
        original=publisher.git
        def rejected(root,*args,**kwargs):
            if args[:1]==('push',):raise subprocess.CalledProcessError(1,['git','push'],stderr=b'offline denial')
            return original(root,*args,**kwargs)
        with patch.object(publisher,'git',side_effect=rejected),self.assertRaises(subprocess.CalledProcessError):self.publish(attempts=2)
        self.assertEqual(len(git(self.root,'worktree','list','--porcelain').stdout.decode().split('worktree '))-1,1)
        self.assertEqual(self.remote_marker(),{'old':True})

class WorkflowContractTests(unittest.TestCase):
    def test_existing_step_uses_validated_record_then_isolated_publisher(self):
        c=(Path(__file__).parent/'.github/workflows/okx_hourly_extreme_history.yml').read_text()
        block=c.split('      - name: Record successful daily package upload only after artifact success',1)[1].split('      - name:',1)[0]
        self.assertIn("steps.pit_artifact.outcome == 'success'",block)
        self.assertLess(block.index('pit_retention_preparation.py record-artifact'),block.index('pit_receipt_publish.py --run-id'))
        self.assertNotIn('git pull',block);self.assertNotIn('git add',block)
        self.assertIn("steps.pit_publication.outcome == 'failure'",c)

if __name__=='__main__':unittest.main()

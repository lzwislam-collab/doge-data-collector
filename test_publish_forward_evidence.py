import subprocess,tempfile,unittest
from pathlib import Path
import publish_forward_evidence as publisher

class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.remote=self.base/'remote';self.root=self.base/'work';self.root.mkdir()
        self.git(self.base,'init','--bare','-q',str(self.remote))
        self.git(self.root,'init','-q','-b','main')
        self.git(self.root,'config','core.autocrlf','false')
        self.git(self.root,'config','user.name','QA');self.git(self.root,'config','user.email','qa@example.test')
        (self.root/'initial.txt').write_bytes(b'initial\n')
        self.git(self.root,'add','.');self.git(self.root,'commit','-qm','initial')
        self.git(self.root,'remote','add','origin',str(self.remote));self.git(self.root,'push','-q','origin','main')
    def tearDown(self):self.tmp.cleanup()
    def git(self,root,*args):return subprocess.check_output(['git',*args],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
    def seed(self):
        p=self.root/'data/direction_v42/forward_registry.csv';p.parent.mkdir(parents=True);p.write_bytes(b'forecast_id\np\n')
        return p
    def test_new_prediction_is_published_and_remote_bytes_match(self):
        self.seed();out=publisher.publish(self.root)
        self.assertEqual(out['status'],'verified_remote_readback');self.assertTrue(out['new_predictions'])
        body=self.git(self.remote,'show','main:data/direction_v42/forward_registry.csv')
        self.assertEqual(body,'forecast_id\np')
    def test_repeat_does_not_create_another_commit(self):
        self.seed();publisher.publish(self.root);head=self.git(self.root,'rev-parse','HEAD')
        out=publisher.publish(self.root)
        self.assertEqual(out['status'],'verified_remote_readback');self.assertFalse(out['new_predictions'])
        self.assertEqual(self.git(self.root,'rev-parse','HEAD'),head)
    def test_readback_rejects_wrong_contents(self):
        self.seed();publisher.publish(self.root)
        self.assertFalse(publisher.verify_readback(self.root,'origin/main',{'data/direction_v42/forward_registry.csv':b'forged\n'}))

if __name__=='__main__':unittest.main()

#!/usr/bin/env python3
"""Synthetic, isolated git-history regression tests; never changes production registry."""
import csv,io,subprocess,tempfile,unittest
from pathlib import Path
from prospective_forecast import COLS,verify_registry_integrity

class RegistryIntegrityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.file=self.root/'data/prospective/registry.csv';self.file.parent.mkdir(parents=True)
  self.git('init','-q');self.git('config','user.name','QA Test')
  self.git('config','user.email','qa@example.test')
  self.a=self.row('A','0.48');self.b=self.row('B','0.47')
  self.commit([self.a])
 def tearDown(self):self.tmp.cleanup()
 def git(self,*args):
  return subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True,text=True).stdout
 def row(self,id,p):
  return dict(zip(COLS,[id,'2026-10-11T00:00:00+00:00','2026-10-10','0.08','1','2026-10-11',p,'v1','100','digest']))
 def commit(self,rows):
  buf=io.StringIO();wr=csv.DictWriter(buf,fieldnames=COLS);wr.writeheader();wr.writerows(rows)
  self.file.write_text(buf.getvalue());self.git('add','.');self.git('commit','-qm','audit fixture')
 def test_legal_append(self):
  self.commit([self.a,self.b]);verify_registry_integrity({'A':self.a,'B':self.b},self.root)
 def test_worktree_mutation(self):
  changed=dict(self.a,p_up='0.99')
  with self.assertRaisesRegex(ValueError,'removed or modified'):
   verify_registry_integrity({'A':changed},self.root)
 def test_committed_mutation_hidden_by_later_commit(self):
  self.commit([dict(self.a,p_up='0.99')])
  self.commit([self.a,self.b])
  with self.assertRaisesRegex(ValueError,'committed forecast history modified'):
   verify_registry_integrity({'A':self.a,'B':self.b},self.root)
 def test_committed_deletion(self):
  self.commit([])
  with self.assertRaisesRegex(ValueError,'committed forecast history modified'):
   verify_registry_integrity({},self.root)

if __name__=='__main__':unittest.main(verbosity=2)

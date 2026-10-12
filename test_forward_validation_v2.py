import importlib,tempfile,unittest,json,subprocess,os
from pathlib import Path
from datetime import datetime,timezone,timedelta

class V2Tests(unittest.TestCase):
 def setUp(self):
  self.v=importlib.import_module('forward_validation_v2');self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.now=datetime(2026,10,12,2,tzinfo=timezone.utc)
 def tearDown(self):self.tmp.cleanup()
 def test_missing_issuance_is_not_hidden(self):
  r=self.v.issuance([],self.now,self.now+timedelta(hours=3),1)
  self.assertEqual(r['expected'],3);self.assertEqual(r['missing'],3);self.assertEqual(r['coverage_pct'],0)
 def test_open_day_not_counted_missing(self):
  r=self.v.issuance([],self.now,self.now+timedelta(hours=23),24)
  self.assertEqual(r['expected'],0);self.assertIsNone(r['coverage_pct'])
 def test_changed_implementation_blocks(self):
  subprocess.run(['git','init','-q'],cwd=self.root,check=True)
  p=self.root/'engine.py';p.write_text('a=1\n')
  sha=subprocess.check_output(['git','hash-object','engine.py'],cwd=self.root,text=True).strip()
  protocol={'locked_code_git_blobs':{'engine.py':sha}}
  self.v.check_code(self.root,protocol)
  p.write_text('a=2\n')
  with self.assertRaisesRegex(ValueError,'implementation'):
   self.v.check_code(self.root,protocol)
 def test_full_v2_report_uses_raw_bound_future_cohort(self):
  import hashlib
  from unittest.mock import patch
  from test_direction_forward_v42 import ForwardDirectionTests
  import quote_evidence_v2 as q
  import direction_forward_v42 as f
  fixture=ForwardDirectionTests();fixture.setUp()
  try:
   fixture.end=self.now;fixture.start=fixture.end-timedelta(hours=1600);fixture.seed_hours(1600)
   pins={}
   for name in self.v.REQUIRED_CODE:
    data=(self.v.ROOT/name).read_bytes().replace(b'\r\n',b'\n')
    (fixture.root/name).write_bytes(data)
    pins[name]=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
   p={'status':'frozen_protocol','protocol_id':'TEST_V2','effective_from_utc':self.now.isoformat(),
     'model_id':f.MODEL,'primary_horizons_hours':[1,24],'locked_code_git_blobs':pins,
     'minimum_quote_and_issuance_coverage_fraction':.9,'minimum_nonoverlap_valid_scores':100,
     'fee_fraction_each_side':.001,'slippage_fraction_each_side':.0005}
   path=fixture.root/self.v.PROTOCOL;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(p))
   subprocess.run(['git','add','.'],cwd=fixture.root,check=True)
   # All fixture clocks are synthetic; never depend on the wall clock of CI.
   protocol_env=dict(os.environ,GIT_AUTHOR_DATE=(self.now-timedelta(hours=2)).isoformat(),GIT_COMMITTER_DATE=(self.now-timedelta(hours=2)).isoformat())
   subprocess.run(['git','-c','user.name=QA','-c','user.email=qa@example.test','commit','-qm','protocol'],cwd=fixture.root,check=True,env=protocol_env)
   head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=fixture.root,text=True).strip()
   clock=self.now+timedelta(minutes=13)
   class Clock(datetime):
    @classmethod
    def now(cls,tz=None):return clock
   class Response:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps({'code':'0','data':[{'instId':'DOGE-USDT','last':price,'ts':str(int(clock.timestamp()*1000))}]}).encode()
   price='.11'
   with patch.object(q,'datetime',Clock),patch.object(q.urllib.request,'urlopen',return_value=Response()):
    q.collect(fixture.root,'direction')
    # Publish ISSUE evidence before fetching any target outcome.
    subprocess.run(['git','add','.'],cwd=fixture.root,check=True)
    issue_env=dict(os.environ,GIT_AUTHOR_DATE=(clock+timedelta(minutes=1)).isoformat(),GIT_COMMITTER_DATE=(clock+timedelta(minutes=1)).isoformat())
    subprocess.run(['git','-c','user.name=QA','-c','user.email=qa@example.test','commit','-qm','issue evidence'],cwd=fixture.root,check=True,env=issue_env)
    public_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=fixture.root,text=True).strip()
    clock+=timedelta(hours=1,minutes=1);price='.12';q.collect(fixture.root,'watch')
   reg=f.read_rows(fixture.root/self.v.base.REG,f.FCOLS)
   public={fid:{'observed_public_utc':(f.utc(r['issued_at_utc'])+timedelta(minutes=20)).isoformat(),'head_sha':public_head} for fid,r in reg.items()}
   runs=[{'id':1,'head_sha':head,'created_at':(self.now-timedelta(hours=2)).isoformat()}]
   with patch.object(self.v.base,'evaluate',return_value={'verified_remote_head_witnesses':public}):
    out=self.v.evaluate(fixture.root,self.now+timedelta(hours=1,minutes=39),runs,persist=False)
   self.assertEqual(out['horizons']['1h']['scored'],1)
   self.assertEqual(out['horizons']['1h']['quote_coverage_pct'],100)
   self.assertEqual(out['raw_quote_snapshots'],2)
   self.assertFalse(out['accuracy_70pct_validated'])
  finally:fixture.tearDown()

if __name__=='__main__':unittest.main()

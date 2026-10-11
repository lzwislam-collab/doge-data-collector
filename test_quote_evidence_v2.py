import importlib,json,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from datetime import datetime,timezone,timedelta
from direction_forward_v42 import FCOLS,SCOLS

class EvidenceTests(unittest.TestCase):
 def setUp(self):
  self.q=importlib.import_module('quote_evidence_v2');self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.now=datetime(2026,10,12,2,tzinfo=timezone.utc)
 def tearDown(self):self.tmp.cleanup()
 def raw(self,price='1',stamp=None):
  stamp=stamp or self.now
  return json.dumps({'code':'0','data':[{'instId':'DOGE-USDT','ts':str(int(stamp.timestamp()*1000)),'last':price,'bidPx':price,'askPx':price}]})
 def row(self):
  return dict(zip(FCOLS,['p','DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',self.now.isoformat(),self.now.isoformat(),self.now.isoformat(),'1','1',(self.now+timedelta(hours=1)).isoformat(),'.5','UP','UP','DOWN','x']))
 def test_snapshot_hash_detects_changed_raw_price(self):
  qid=self.q.save_quote(self.root,self.raw(),self.now)
  path=self.q.snapshot_path(self.root,qid)
  path.write_text(path.read_text().replace('last\\": \\"1','last\\": \\"2'),encoding='utf-8')
  # Change bytes regardless of JSON formatting.
  path.write_bytes(path.read_bytes()+b' ')
  with self.assertRaisesRegex(ValueError,'hash'):
   self.q.load_quote(self.root,qid)
 def test_exchange_symbol_and_clock_are_checked(self):
  with self.assertRaises(ValueError):self.q.save_quote(self.root,self.raw().replace('DOGE-USDT','BTC-USDT'),self.now)
  with self.assertRaises(ValueError):self.q.save_quote(self.root,self.raw(stamp=self.now-timedelta(minutes=3)),self.now)
 def test_forged_price_cannot_pass_bound_quote_check(self):
  r=self.row();qid=self.q.save_quote(self.root,self.raw(),self.now)
  later=self.now+timedelta(hours=1)
  target=self.q.save_quote(self.root,self.raw('1.1',later),later)
  bindings={}
  self.q.bind(bindings,'p','ISSUE',qid);self.q.bind(bindings,'p','TARGET',target)
  s=dict(zip(SCOLS,['p','scored',later.isoformat(),later.isoformat(),'12345','UP','1','1','0','']))
  with self.assertRaisesRegex(ValueError,'price'):
   self.q.verify_bound(self.root,r,s,bindings)
  s['target_price']='1.1';self.q.verify_bound(self.root,r,s,bindings)
 def test_missing_evidence_cannot_be_reconstructed_from_score(self):
  with self.assertRaisesRegex(ValueError,'binding'):
   self.q.verify_bound(self.root,self.row(),None,{})
 def test_existing_binding_never_replaced(self):
  rows={};self.q.bind(rows,'p','ISSUE','a')
  with self.assertRaises(ValueError):self.q.bind(rows,'p','ISSUE','b')
 def test_actual_collectors_preserve_scored_evidence_when_features_are_stale(self):
  from test_direction_forward_v42 import ForwardDirectionTests
  fixture=ForwardDirectionTests();fixture.setUp()
  try:
   clock=fixture.end+timedelta(minutes=13)
   class Clock(datetime):
    @classmethod
    def now(cls,tz=None):return clock
   class Response:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return self.raw
   response=Response();response.raw=self.raw('.11',clock).encode()
   with patch.object(self.q,'datetime',Clock),patch.object(self.q.urllib.request,'urlopen',return_value=response):
    self.assertEqual(self.q.collect(fixture.root,'direction'),0)
    clock+=timedelta(hours=1,minutes=1);response.raw=self.raw('.12',clock).encode()
    self.assertEqual(self.q.collect(fixture.root,'direction'),1)
   import direction_forward_v42 as f
   reg=f.read_rows(fixture.root/'data/direction_v42/forward_registry.csv',FCOLS)
   scores=f.read_rows(fixture.root/'data/direction_v42/forward_scores.csv',SCOLS)
   bindings=f.read_rows(fixture.root/self.q.BINDINGS,self.q.BCOLS)
   fid=next(k for k,v in scores.items() if v['status']=='scored')
   self.q.verify_bound(fixture.root,reg[fid],scores[fid],bindings)
   self.assertEqual(len(bindings),3)
   clock+=timedelta(minutes=1);response.raw=self.raw('.13',clock).encode()
   with patch.object(self.q,'datetime',Clock),patch.object(self.q.urllib.request,'urlopen',return_value=response):
    self.assertEqual(self.q.collect(fixture.root,'watch'),0)
   self.assertEqual(len(f.read_rows(fixture.root/self.q.BINDINGS,self.q.BCOLS)),3)
  finally:fixture.tearDown()

if __name__=='__main__':unittest.main()

"""Collector quality gates must preserve existing bytes on rejected merges."""
import contextlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import doge_history as h

class DailyQuality(unittest.TestCase):
 def collect(self,days):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp);existing=[]
   for symbol in h.SYMBOLS:
    for day in days:existing.append(h.validrow(symbol,[str(day*h.DAY),'1','2','.5','1','5','0','0','1'],4*h.DAY))
   path=base/'daily.csv';path.write_text(h.dump(existing),encoding='utf-8');original=path.read_bytes()
   def fetch(symbol,oldest=None):return [[str(3*h.DAY),'1','2','.5','1','5','0','0','1']] if oldest is None else []
   with patch.object(h,'BASE',base),patch.object(h,'request',side_effect=fetch),patch.object(h.time,'time',return_value=4*h.DAY/1000),contextlib.redirect_stdout(io.StringIO()):code=h.main()
   return code,json.loads((base/'latest.json').read_bytes()),original,path.read_bytes(),(base/'backtest_latest.json').exists()
 def test_gapped_merge_fails_without_overwriting_history(self):
  code,result,before,after,backtest=self.collect([0,2])
  self.assertEqual(code,1);self.assertEqual(result['status'],'error');self.assertEqual(before,after);self.assertFalse(backtest)
  self.assertTrue(all(v['missing_days_between_endpoints']==1 for v in result['quality']['symbols'].values()))
 def test_contiguous_merge_is_accepted(self):
  code,result,before,after,backtest=self.collect([0,1,2])
  self.assertEqual(code,0);self.assertEqual(result['status'],'ok');self.assertNotEqual(before,after);self.assertTrue(backtest)

if __name__=='__main__':unittest.main()

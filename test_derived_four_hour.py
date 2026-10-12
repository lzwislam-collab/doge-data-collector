import unittest
from datetime import datetime,timezone
import derived_four_hour as d

class FourHourTests(unittest.TestCase):
    def rows(self):
        return [{'symbol':'DOGE-USDT','open_utc_ms':str(i*3600000),'open':str(10+i),'high':str(12+i),'low':str(9+i),'close':str(11+i),'volume_base':'0.1','first_ingested_utc':'2026-10-13T00:00:00+00:00','source':'OKX history-candles 1H confirmed'} for i in range(4)]
    def test_exact_aligned_ohlc_volume_and_first_available(self):
        bars,missing=d.aggregate(self.rows(),datetime(2026,10,13,1,tzinfo=timezone.utc))
        self.assertEqual(len(bars),1);self.assertEqual(missing,[])
        self.assertEqual([bars[0][k] for k in ['open','high','low','close','volume_base']],['10','15','9','14','0.4'])
        self.assertEqual(bars[0]['usable_from_utc'],'2026-10-13T01:00:00+00:00')
    def test_missing_child_never_synthesized(self):
        bars,missing=d.aggregate(self.rows()[:3],datetime(2026,10,13,tzinfo=timezone.utc))
        self.assertEqual(bars,[]);self.assertEqual(missing[0]['missing_open_utc_ms'],[10800000])
    def test_entire_missing_bucket_detected(self):
        rows=self.rows()+[dict(r,open_utc_ms=str(int(r['open_utc_ms'])+28800000)) for r in self.rows()]
        bars,missing=d.aggregate(rows,datetime(2026,10,13,tzinfo=timezone.utc))
        self.assertEqual(len(bars),2);self.assertEqual(len(missing[0]['missing_open_utc_ms']),4)
    def test_duplicate_or_misaligned_source_blocked(self):
        with self.assertRaises(ValueError):d.aggregate(self.rows()+self.rows()[:1],datetime(2026,10,13,tzinfo=timezone.utc))
        rows=self.rows();rows[0]['open_utc_ms']='1'
        with self.assertRaises(ValueError):d.aggregate(rows,datetime(2026,10,13,tzinfo=timezone.utc))

if __name__=='__main__':unittest.main()

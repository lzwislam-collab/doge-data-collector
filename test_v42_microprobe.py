#!/usr/bin/env python3
"""Synthetic OKX micro-sample time-availability tests."""
import unittest
from v42_microprobe import parse,assess
class MicroProbeTests(unittest.TestCase):
 def candle(self,t,confirm='1',low='0.99',volume='100'):
  return [str(t),'1','1.01',low,'1.0',volume,'100','100',confirm]
 def test_only_confirmed_full_bar(self):
  x=parse('DOGE-USDT','1m',self.candle(600000),1200000,'2026-10-11T00:00:00+00:00')
  self.assertIsNotNone(x)
  self.assertIsNone(parse('DOGE-USDT','1m',self.candle(600000,'0'),1200000,'2026-10-11T00:00:00Z'))
 def test_not_yet_closed_excluded(self):
  self.assertIsNone(parse('DOGE-USDT','5m',self.candle(900000),1000000,'2026-10-11T00:00:00Z'))
 def test_invalid_low_and_timestamp(self):
  with self.assertRaises(ValueError):
   parse('DOGE-USDT','1m',self.candle(600000,low='1.4'),1200000,'2026-10-11T00:00:00Z')
  self.assertIsNone(parse('DOGE-USDT','1m',self.candle(605000),1200000,'2026-10-11T00:00:00Z'))
 def test_detect_gap(self):
  rows=[('DOGE-USDT','1m',str(i*60000)) for i in range(1,53)]
  with self.assertRaises(ValueError):
   assess(rows[:8]+rows[9:],'1m',53*60000)
 def test_duplicate(self):
  rows=[('DOGE-USDT','1m',str(i*60000)) for i in range(1,53)]
  with self.assertRaises(ValueError):
   assess(rows+[rows[-1]],'1m',53*60000)
if __name__=='__main__':unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Deterministic positive and negative validation for OKX spot quote snapshots."""
import tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from spot_plus_live_quote import parse,run

NOW=datetime(2026,10,11,13,20,tzinfo=timezone.utc)
def raw(now=NOW):
 ts=str(int((now-timedelta(seconds=4)).timestamp()*1000))
 d={}
 for sym,v in (('DOGE-USDT',.08531),('BTC-USDT',83055.2),('ETH-USDT',2502.5)):
  d[sym]={'instId':sym,'last':str(v),'bidPx':str(v*.99995),
       'askPx':str(v*1.00005),'ts':ts}
 return d

class TestLiveQuote(unittest.TestCase):
 def test_valid_and_independent_of_forecast(self):
  with tempfile.TemporaryDirectory() as folder:
   report=run(Path(folder),NOW,raw())
   self.assertEqual(report['status'],'ok')
   self.assertTrue(report['snapshot_only_not_continuous_market_stream'])
   self.assertFalse(report['change_to_frozen_v42_or_dca'])
   self.assertEqual(report['instruments']['DOGE-USDT']['last_usdt'],.08531)
   self.assertTrue((Path(folder)/'data/stage5/spot_plus/live_quote_latest.json').exists())

 def test_stale_quote_fails_closed(self):
  d=raw(NOW-timedelta(minutes=5))
  with self.assertRaisesRegex(ValueError,'stale'):parse(d,NOW)

 def test_future_quote_fails_closed(self):
  d=raw(NOW+timedelta(seconds=60))
  with self.assertRaisesRegex(ValueError,'future'):parse(d,NOW)

 def test_doge_symbol_mismatch_fails_closed(self):
  d=raw();d['DOGE-USDT']['instId']='DOGE-USDT-SWAP'
  with self.assertRaisesRegex(ValueError,'mismatched'):parse(d,NOW)

 def test_crossed_book_fails_closed(self):
  d=raw();d['DOGE-USDT']['bidPx']='0.09'
  with self.assertRaisesRegex(ValueError,'invalid spot ticker'):parse(d,NOW)

 def test_missing_eth_fails_closed(self):
  d=raw();del d['ETH-USDT']
  with self.assertRaisesRegex(ValueError,'missing'):parse(d,NOW)

 def test_invalid_negative_price_fails_closed(self):
  d=raw();d['DOGE-USDT']['last']='-0.085'
  with self.assertRaisesRegex(ValueError,'invalid spot ticker'):parse(d,NOW)

 def test_quote_over_100bps_spread_fails_closed(self):
  d=raw();d['DOGE-USDT']['bidPx']='0.080'
  with self.assertRaisesRegex(ValueError,'100bps'):parse(d,NOW)

if __name__=='__main__':unittest.main(verbosity=2)

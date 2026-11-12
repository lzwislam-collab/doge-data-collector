import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


class CompleteMarketAuditTests(unittest.TestCase):
    def market(self, symbols):
        import plus_p1_audit
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bars.csv'
            with path.open('w', newline='', encoding='utf-8') as stream:
                writer = csv.writer(stream)
                writer.writerow(['symbol','open_utc_ms','open','high','low','close','volume_base','first_ingested_utc','source'])
                for symbol in symbols:
                    writer.writerow([symbol,1700002800000,1,2,.5,1,100,'2023-11-15T01:00:00+00:00','OKX history-candles 1H confirmed'])
            return plus_p1_audit.market(path,3600000,datetime(2024,1,1,tzinfo=timezone.utc))

    def test_missing_asset_rejected_instead_of_silent_shared_subset(self):
        with self.assertRaisesRegex(ValueError, 'required symbols'):
            self.market(['DOGE-USDT'])

    def test_complete_asset_set_passes(self):
        _, report = self.market(['DOGE-USDT','BTC-USDT','ETH-USDT'])
        self.assertEqual(set(report['symbols']), {'DOGE-USDT','BTC-USDT','ETH-USDT'})


if __name__ == '__main__':
    unittest.main()

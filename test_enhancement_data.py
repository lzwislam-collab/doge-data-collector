import csv,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
import test_feed_quality as fixtures
import feed_freshness_audit as quality

class ClockTests(unittest.TestCase):
    def test_delayed_notification_is_not_invalid_event_timestamp(self):
        f=fixtures.QualityAuditTests();f.setUp()
        try:
            p=f.b/'data/liquidations/events.csv'
            with p.open(newline='') as stream:rows=list(csv.DictReader(stream));cols=list(rows[0])
            rows[0]['event_ts_ms']=str(int((f.t-timedelta(minutes=46)).timestamp()*1000))
            with p.open('w',newline='') as stream:
                w=csv.DictWriter(stream,fieldnames=cols);w.writeheader();w.writerows(rows)
            checks,failed=quality.check_stage2_csvs(f.b,f.t)
            r=checks['liquidations_okx']
            self.assertEqual(r['invalid_timestamps'],0)
            self.assertEqual(r['delayed_notifications'],1)
            self.assertEqual(r['late_events'][0]['usable_from_utc'],rows[0]['received_at_utc'])
            self.assertNotIn('liquidations_okx',failed)
        finally:f.tearDown()

if __name__=='__main__':unittest.main()

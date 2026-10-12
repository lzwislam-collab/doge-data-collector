import unittest,json
from datetime import datetime,timezone
import enhancement_sources as s
class SourcesTests(unittest.TestCase):
    def setUp(self):self.t=datetime(2026,10,13,tzinfo=timezone.utc)
    def test_news_availability_not_backdated_and_tone_version_fixed(self):
        raw=b'<rss><channel><item><guid>x</guid><title>Fraud lawsuit</title><link>https://example.com/x</link><pubDate>Mon, 12 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>'
        row=s.parse('FED_NEWS',raw,self.t)[0]
        self.assertEqual(row['usable_from_utc'],self.t.isoformat())
        self.assertLess(row['value'],0)
        self.assertEqual(row['score_version'],'FIXED_HEADLINE_TONE_V1')
    def test_fear_greed_is_btc_proxy_and_not_historically_available(self):
        raw=json.dumps({'data':[{'value':'25','timestamp':'1791763200','value_classification':'Extreme Fear'}]}).encode()
        row=s.parse('BTC_FEAR_GREED_PROXY',raw,self.t)[0]
        self.assertEqual(row['value'],25)
        self.assertIn('Bitcoin',row['scope'])
        self.assertEqual(row['usable_from_utc'],self.t.isoformat())
    def test_invalid_sentiment_value_rejected(self):
        raw=json.dumps({'data':[{'value':'101','timestamp':'1791763200'}]}).encode()
        with self.assertRaises(ValueError):s.parse('BTC_FEAR_GREED_PROXY',raw,self.t)
    def test_dollar_proxy_never_labelled_dxy(self):
        raw=('observation_date,DTWEXBGS\n'+''.join('2026-09-%02d,120\n'%i for i in range(1,15))).encode()
        rows=s.parse('USD_BROAD_FRED_PROXY',raw,self.t)
        self.assertEqual(rows[0]['source_id'],'USD_BROAD_FRED_PROXY')
        self.assertFalse(rows[0]['is_dxy'])
if __name__=='__main__':unittest.main()

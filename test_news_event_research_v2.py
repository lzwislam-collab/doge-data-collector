"""Synthetic fixtures only: tests verify safety/invariants, never predictive edge."""
import copy
import unittest
from datetime import datetime,timezone,timedelta
from news_event_research_v2 import build_report,classify,timestamp,canonical_url
T=datetime(2026,10,16,5,0,tzinfo=timezone.utc)

def event(title='Dogecoin hack leads to withdrawal halt',source='CRYPTO_NEWS',seen='2026-10-16T04:00:00Z',published='2026-10-16T03:50:00Z',url='https://example.com/story'):
    return {'source_id':source,'observation_key':'event-1','title':title,'url':url,
        'published_at_utc':published,'first_seen_utc':seen,'usable_from_utc':seen,'raw_sha256':'sha-example'}

class EventResearchTests(unittest.TestCase):
    def test_negative_doge_security_classified_without_direction(self):
        c=classify('Dogecoin exchange hacked, withdrawals halted','CRYPTO_NEWS')
        self.assertEqual(c['primary_type'],'MARKET_INFRASTRUCTURE')
        self.assertEqual(c['wording'],'NEGATIVE_WORDING')
        self.assertEqual(c['market_direction'],'UNKNOWN')
    def test_positive_and_mixed_are_not_trades(self):
        self.assertEqual(classify('DOGE payment approved','CRYPTO_NEWS')['wording'],'POSITIVE_WORDING')
        self.assertEqual(classify('ETF approval despite hack','CRYPTO_NEWS')['wording'],'MIXED')
        self.assertFalse(classify('Bitcoin rally','CRYPTO_NEWS')['critical_watch_only'])
    def test_unseen_future_cannot_enter_asof(self):
        x=event(seen='2026-10-16T05:01:00Z')
        self.assertEqual(build_report([x],T)['deduplicated_event_count'],0)
    def test_published_at_not_used_as_first_seen(self):
        x=event(seen='2026-10-16T04:40:00Z',published='2026-10-15T11:00:00Z')
        r=build_report([x],T)
        self.assertEqual(r['events'][0]['first_usable_utc'],'2026-10-16T04:40:00+00:00')
    def test_clock_conflict_rejected(self):
        x=event();x['usable_from_utc']='2026-10-16T03:00:00Z'
        self.assertEqual(build_report([x],T)['rejected_rows']['clock_conflict'],1)
    def test_invalid_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):timestamp('2026-10-16T04:00:00')
    def test_url_tracking_dedup_across_provider_keys(self):
        a=event(url='https://example.com/story?utm_source=abc')
        b=event(url='https://example.com/story',source='FED_NEWS');b['observation_key']='event-2'
        r=build_report([a,b],T)
        self.assertEqual(r['distinct_provider_records'],2)
        self.assertEqual(r['deduplicated_event_count'],1)
    def test_revisions_keep_only_visible_current(self):
        a=event(title='Initial headline'); b=copy.deepcopy(a)
        b['title']='Dogecoin hack';b['usable_from_utc']='2026-10-16T04:30:00Z';b['first_seen_utc']='2026-10-16T04:30:00Z'
        r=build_report([a,b],T);self.assertEqual(r['events'][0]['wording'],'NEGATIVE_WORDING')
    def test_revisions_after_asof_do_not_retroactively_rewrite(self):
        a=event(title='Initial headline'); b=copy.deepcopy(a)
        b['title']='Dogecoin hack';b['usable_from_utc']='2026-10-16T05:30:00Z';b['first_seen_utc']='2026-10-16T05:30:00Z'
        r=build_report([a,b],T);self.assertEqual(r['events'][0]['wording'],'UNSPECIFIED')
    def test_missing_source_and_staleness_not_filled_with_zero(self):
        old=event(seen='2026-10-14T00:00:00Z',published='2026-10-14T00:00:00Z')
        r=build_report([old],T)
        self.assertEqual(len(r['missing_or_stale_sources']),3)
        self.assertFalse(r['coverage_sufficient_for_directional_claim'])
    def test_non_news_historical_rows_excluded(self):
        a=event(source='BTC_FEAR_GREED_PROXY')
        self.assertEqual(build_report([a],T)['input_news_rows'],0)
    def test_immutable_original_input(self):
        x=event();b=copy.deepcopy(x)
        build_report([x],T)
        self.assertEqual(x,b)
    def test_deterministic(self):
        a=event();b=event(title='FED cuts interest rate',source='FED_NEWS',url='https://fed.gov/news',published='2026-10-16T04:30:00Z')
        b['observation_key']='event-2'
        self.assertEqual(build_report([a,b],T),build_report([b,a],T))
    def test_canonical_url(self):
        self.assertEqual(canonical_url('https://EXAMPLE.com/a/?utm_campaign=zz&x=1'),'https://example.com/a?x=1')

if __name__=='__main__': unittest.main()

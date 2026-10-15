"""Report claims fail closed independently of descriptive chapters."""
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch

class Evidence(unittest.TestCase):
 def test_real_report_keeps_eleven_chapters_and_inserts_evidence_panels(self):
  import report_v41 as report
  import contextlib,io,tempfile,csv
  from pathlib import Path
  rendered=[]
  class Clock(datetime):
   @classmethod
   def now(cls,tz=None):return cls(2026,10,15,1,tzinfo=timezone.utc)
  forecast={'status':'ok','anchor_date_utc':'2026-10-14','price_intervals':{}}
  back={'source_cutoff_utc':'2026-10-14','horizons':{}}
  for h in (1,7,30):
   forecast['price_intervals'][str(h)+'d']={'target_utc':(datetime(2026,10,14)+timedelta(days=h)).date().isoformat(),'p10_p50_p90_usdt':{'p10':1,'p50':2,'p90':3},'historical_up_frequency_pct':50,'research_large_down_threshold_pct':-10,'historical_large_down_frequency_pct':5}
   metric={'n':3,'rolling':{'coverage_pct':80,'mean_interval_score_log_pct':1},'expanding':{'mean_interval_score_log_pct':1}}
   back['horizons'][str(h)+'d']={'all_anchors_overlapping':metric,'nonoverlap_fixed_start_phase':{'n':1}}
  inputs={'data/forecast_v41/latest.json':forecast,'data/forecast_v41/backtest_latest.json':back,
   'data/forecast_v41/scores.json':{'status':'ok','source_cutoff_utc':'2026-10-14','registered':3,'counts':{'scored':0,'pending':3,'quarantined':0}},
   'data/history/latest.json':{'status':'ok','quality':{'duplicate_keys':0,'symbols':{'DOGE-USDT':{'last_utc':'2026-10-14','first_utc':'2026-09-14','days':31}}}}}
  ticks=[int((datetime(2026,9,14,tzinfo=timezone.utc)+timedelta(days=i)).timestamp()*1000) for i in range(31)]
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'data/history').mkdir(parents=True);(root/'data/forecast_v41').mkdir()
   with (root/'data/history/daily.csv').open('w',newline='') as out:
    writer=csv.DictWriter(out,fieldnames=['symbol','open_utc_ms','close']);writer.writeheader()
    for symbol in ('DOGE-USDT','BTC-USDT','ETH-USDT'):
     for t in ticks:writer.writerow({'symbol':symbol,'open_utc_ms':t,'close':2})
   (root/'data/forecast_v41/registry.csv').write_text('anchor_date_utc,registered_utc,horizon_days\n2026-10-14,2026-10-15T00:05:00Z,1\n',encoding='utf-8')
   with patch.object(report,'ROOT',root),patch.object(report,'read',side_effect=lambda key:inputs.get(key,{})),patch.object(report,'load_prices',return_value=(ticks,[2]*31)),patch.object(report,'datetime',Clock),patch.object(report,'atomic',side_effect=lambda p,t:rendered.append(t)),patch('report_evidence.current_plus',return_value={'status':'UNAVAILABLE'}),contextlib.redirect_stdout(io.StringIO()):
    report.main()
  text=rendered[0]
  self.assertEqual(text.count('## F'),11)
  self.assertIn('DEGRADED_NO_VALIDATED_EDGE',text)
  self.assertIn('PLUS 当前证据不可用',text)
  self.assertIn('2026-10-16T00:00:00+00:00',text)
 def module(self):
  import report_evidence
  return report_evidence
 def test_current_coverage_limited_health_is_not_a_failed_audit(self):
  m=self.module();now=datetime(2026,10,15,1,tzinfo=timezone.utc)
  r=m.evidence_state({'status':'ok','sources':{'daily_last_closed_start_utc':'2026-10-14T00:00:00Z'}},
   {'status':'ok_with_coverage_limits','audited_at_utc':now.isoformat(),'critical_failures':[]},'2026-10-14',now)
  self.assertEqual(r['overall'],'RESEARCH_ONLY_NO_VALIDATED_EDGE');self.assertFalse(r['prediction_edge_validated'])
 def test_stale_quality_pass_and_wrong_direction_never_become_overall_pass(self):
  m=self.module();now=datetime(2026,10,15,1,tzinfo=timezone.utc)
  result=m.evidence_state({'status':'ok','sources':{'daily_last_closed_start_utc':'2026-10-13T00:00:00Z'}},
   {'status':'ok','audited_at_utc':(now-timedelta(hours=4)).isoformat()},'2026-10-14',now)
  self.assertFalse(result['direction_available']);self.assertEqual(result['quality_current_status'],'STALE_OR_INVALID')
  self.assertEqual(result['overall'],'DEGRADED_NO_VALIDATED_EDGE')
 def test_quality_future_missing_timezone_or_blocked_never_pass(self):
  m=self.module();now=datetime(2026,10,15,1,tzinfo=timezone.utc)
  d={'status':'ok','sources':{'daily_last_closed_start_utc':'2026-10-14T00:00:00Z'}}
  for stamp in ('2026-10-15T01:01:00Z','2026-10-15T01:00:00',None):
   r=m.evidence_state(d,{'status':'ok','audited_at_utc':stamp},'2026-10-14',now)
   self.assertEqual(r['quality_current_status'],'STALE_OR_INVALID')
  r=m.evidence_state(d,{'status':'blocked','audited_at_utc':now.isoformat()},'2026-10-14',now)
  self.assertEqual(r['quality_current_status'],'blocked');self.assertFalse(r['prediction_edge_validated'])
 def test_daily_target_is_day_close_not_midnight_start(self):
  m=self.module()
  self.assertEqual(m.daily_target_close('2026-10-15'),'2026-10-16T00:00:00+00:00')
 def test_plus_failure_has_no_old_snapshot_fallback(self):
  m=self.module()
  with patch('plus_shadow.snapshot',side_effect=ValueError('evidence invalid')):
   r=m.current_plus(False)
  self.assertEqual(r['status'],'UNAVAILABLE');self.assertEqual(r['forecasts'],[])
 def test_unverified_and_canary_rows_cannot_render_probabilities(self):
  m=self.module()
  for track,verified in (('canary',True),('r2',False)):
   r={'status':'SHADOW_REPORT_READY','forecasts':[{'track':track,'horizon_hours':1,'prediction_verified':verified,'probability_up':.987654,'direction':'UP','status':'PENDING_TARGET'}]}
   text=m.plus_sections(r)['F01']
   self.assertNotIn('98.77%',text);self.assertNotIn('|上涨|',text)
 def test_forecast_before_target_displays_but_unverified_score_does_not(self):
  m=self.module();r={'status':'SHADOW_REPORT_READY','forecasts':[{'track':'r2','horizon_hours':1,'prediction_verified':True,'score_verified':False,'probability_up':.6,'direction':'UP','status':'PENDING_TARGET','latest_same_event':{'accuracy':1,'net_return':.9}}]}
  panels=m.plus_sections(r)
  self.assertIn('60.00%',panels['F01']);self.assertNotIn('90.00%',panels['F10']);self.assertIn('未知',panels['F10'])

if __name__=='__main__':unittest.main()

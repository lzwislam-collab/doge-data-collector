import copy,sys,types,unittest
from unittest.mock import patch
import plus_shadow as report

class E1ReportTests(unittest.TestCase):
 def panel(self,**changes):
  value={'protocol_id':'E1_TEST','status':'WAIT_REGISTRATION','registered':False,'registration_run_id':None,'effective_from_utc':'2026-10-22T00:00:00Z','counts':{'events':0,'scores':0,'qualified':0},'latest':None,'gaps':{},'validated_70pct':False,'statistical_gate_passed':False,'real_orders_authorized':False}
  value.update(changes);return value
 def snapshot(self,panel=None,error=None):
  def external(online=True):
   if error:raise error
   return copy.deepcopy(panel)
  with patch.dict(sys.modules,{'e1_forward':types.SimpleNamespace(panel=external)}):return report.snapshot(online=False)
 def test_snapshot_adds_independent_panel_without_changing_r2_statistics(self):
  value=self.snapshot(self.panel())
  self.assertEqual(value['engineering_forward']['status'],'WAIT_REGISTRATION')
  self.assertEqual(len(value['forecasts']),3)
  self.assertEqual(value['runtime_gaps']['r2'],report.r2.plan(report.f.policy(),report.r2.records('events'),report.r2.records('scores'),report.f.utc(value['generated_at_utc'])))
  self.assertFalse(value['validated_70pct']);self.assertFalse(value['promotion_authorized']);self.assertFalse(value['real_orders_authorized'])
  self.assertTrue(value['scorecard']['frozen_protocols_unchanged'])
 def test_e1_failure_preserves_original_report_and_does_not_claim_verification(self):
  value=self.snapshot(error=TimeoutError('private remote detail'))
  self.assertEqual(value['status'],'SHADOW_REPORT_READY')
  self.assertEqual(len(value['forecasts']),3)
  e1=value['engineering_forward'];self.assertEqual(e1['status'],'E1_UNAVAILABLE')
  self.assertFalse(e1['real_orders_authorized']);self.assertFalse(e1['statistical_gate_passed'])
  self.assertNotIn('private remote detail',str(e1))
 def render_panel(self,panel):
  html=report.render({'status':'SHADOW_REPORT_READY','engineering_forward':panel})
  return html.split('<section id="e1-engineering">',1)[-1].split('</section>',1)[0]
 def test_unverified_forecast_is_hidden_and_engineering_is_separate(self):
  latest={'event_id':'secret-event','issued_at_utc':'secret-time','target_utc':'secret-target','direction':'UP','probability_up':.99,'prediction_verified':False}
  html=self.render_panel(self.panel(latest=latest))
  self.assertIn('E1 工程前瞻续采（独立登记，尚未绩效验收）',html)
  self.assertNotIn('secret-time',html);self.assertNotIn('secret-event',html)
  self.assertIn('70%尚未证明',html);self.assertIn('交易授权：关闭',html)
 def test_verified_forecast_escapes_values_and_never_promotes_engineering(self):
  latest={'event_id':'<event>','issued_at_utc':'<issue>','target_utc':'<target>','direction':'UP','probability_up':.7,'prediction_verified':True,'score_verified':False,'score_present':True}
  html=self.render_panel(self.panel(registered=True,status='ACTIVE',latest=latest))
  self.assertIn('&lt;event&gt;',html);self.assertIn('&lt;issue&gt;',html);self.assertIn('&lt;target&gt;',html)
  self.assertIn('上涨',html);self.assertIn('评分尚未核实',html)
  self.assertNotIn('<event>',html);self.assertNotIn('70.0%',html)

if __name__=='__main__':unittest.main()

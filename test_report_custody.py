import unittest
from pathlib import Path
class ReportCustodyTests(unittest.TestCase):
 def test_no_false_unconfigured_storage_claim(self):
  self.assertNotIn('未配置独立锁定存储',Path('report_evidence.py').read_text(encoding='utf-8'))
 def test_report_separates_daily_and_forecast_custody(self):
  self.assertIn('per_forecast_custody',Path('plus_shadow.py').read_text(encoding='utf-8'))
 def test_user_report_exposes_missing_event_receipt(self):
  import report_evidence
  r={'status':'SHADOW_REPORT_READY','per_forecast_custody':{'per_forecast_status':'BLOCKED_NO_PER_FORECAST_RECEIPT','events':[{'track':'original18','event_id':'1792108800_1','status':'BLOCKED_NO_PER_FORECAST_RECEIPT'}]}}
  f11=report_evidence.plus_sections(r)['F11']
  self.assertIn('BLOCKED_NO_PER_FORECAST_RECEIPT',f11)
  self.assertIn('1792108800_1',f11)
 def test_frozen_registered_bytes_untouched(self):
  import forecast_worm
  forecast_worm.verify_frozen(Path('.'))
if __name__=='__main__':unittest.main()

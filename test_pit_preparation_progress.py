"""Progress logging must never hide an original preparation failure."""
import contextlib,io,json,unittest
from unittest import mock
import pit_retention_preparation as prep
class PreparationProgressTests(unittest.TestCase):
 def stage(self):
  self.assertTrue(hasattr(prep,'_preparation_stage'),'bounded actual-stage preparation progress is absent')
  return prep._preparation_stage('compress_exact_part')
 def test_success_preserves_stdout_and_emits_only_fixed_stage_metadata(self):
  out=io.StringIO();err=io.StringIO()
  with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
   with self.stage():print('original stdout')
  self.assertEqual(out.getvalue(),'original stdout\n');rows=[json.loads(l) for l in err.getvalue().splitlines()];self.assertEqual(len(rows),2)
  self.assertEqual([r['event'] for r in rows],['begin','end']);self.assertEqual(rows[-1]['status'],'ok');self.assertEqual(rows[-1]['phase'],'compress_exact_part')
  self.assertTrue(all(set(r)<={'kind','phase','event','at_utc','status','seconds','error_type'} for r in rows));self.assertGreaterEqual(rows[-1]['seconds'],0)
 def test_error_cancellation_and_systemexit_are_the_same_original_object(self):
  for error in (ValueError('PRIVATE_PAYLOAD'),KeyboardInterrupt(),SystemExit(7)):
   err=io.StringIO()
   with contextlib.redirect_stderr(err),self.assertRaises(type(error)) as got:
    with self.stage():raise error
   self.assertIs(got.exception,error);r=json.loads(err.getvalue().splitlines()[-1]);self.assertEqual(r['status'],'error');self.assertEqual(r['error_type'],type(error).__name__);self.assertNotIn('PRIVATE_PAYLOAD',err.getvalue())
 def test_logging_failure_never_changes_original_result_or_exception(self):
  self.assertTrue(hasattr(prep,'_preparation_stage'),'bounded actual-stage preparation progress is absent')
  error=ValueError('original')
  with mock.patch('builtins.print',side_effect=OSError('logging failed')):
   with prep._preparation_stage('verify_inventory_history'):value=123
   self.assertEqual(value,123)
   with self.assertRaises(ValueError) as got:
    with prep._preparation_stage('verify_inventory_history'):raise error
   self.assertIs(got.exception,error)

 def test_timer_failure_never_prevents_original_work(self):
  self.assertTrue(hasattr(prep,'_preparation_stage'),'bounded actual-stage preparation progress is absent')
  with mock.patch.object(prep.time,'monotonic',side_effect=OSError('clock unavailable')),contextlib.redirect_stderr(io.StringIO()):
   with prep._preparation_stage('verify_inventory_history'):value=456
  self.assertEqual(value,456)

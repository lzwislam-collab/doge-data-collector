"""A queue read can be retried, but incomplete evidence must never dispatch."""
import ast,contextlib,io,json,sys,types,unittest
from unittest import mock
from pathlib import Path
from urllib.parse import parse_qs,urlparse,urlencode
SOURCE=Path(__file__).with_name('runtime_r2_bridge.py')
def subject():
 tree=ast.parse(SOURCE.read_bytes());wanted={'_active','_active_once','_QueueSnapshotChanged'};nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in wanted]
 ns={'urlencode':urlencode,'json':json,'sys':sys,'STATES':('pending','queued','requested','waiting','in_progress')};exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),ns);return ns
class QueueConsistencyTests(unittest.TestCase):
 def setUp(self):self.ns=subject();self.calls=[];self.workflow='learned_shadow_v1.yml';self.row={'id':123,'head_branch':'main','path':'.github/workflows/'+self.workflow,'status':'in_progress'}
 def invoke(self,replies):
  def api(url):self.calls.append(parse_qs(urlparse(url).query)['status'][0]);return replies.pop(0)
  with contextlib.redirect_stderr(io.StringIO()):return self.ns['_active'](api,self.workflow)
 def empty(self):return {'total_count':0,'workflow_runs':[]}
 def test_disagreement_restarts_a_fresh_full_five_state_view(self):
  replies=[self.empty(),{'total_count':1,'workflow_runs':[]}]+[self.empty() for _ in range(4)]+[{'total_count':1,'workflow_runs':[self.row]}]
  self.assertEqual(self.invoke(replies),self.row);self.assertEqual(self.calls,['pending','queued','pending','queued','requested','waiting','in_progress']);self.assertFalse(replies)
 def test_zero_count_with_verified_row_also_requires_fresh_view(self):
  replies=[{'total_count':0,'workflow_runs':[self.row]}]+[self.empty() for _ in range(5)]
  self.assertIsNone(self.invoke(replies));self.assertEqual(len(self.calls),6)
 def test_two_disagreements_fail_closed_after_at_most_two_views(self):
  with self.assertRaisesRegex(ValueError,'incomplete workflow queue response'):self.invoke([{'total_count':1,'workflow_runs':[]},{'total_count':1,'workflow_runs':[]}])
  self.assertEqual(self.calls,['pending','pending'])
 def test_malformed_schema_is_never_retried(self):
  for bad in ({'total_count':True,'workflow_runs':[]},{'total_count':-1,'workflow_runs':[]},{'total_count':1,'workflow_runs':'bad'},{'total_count':2,'workflow_runs':[self.row,self.row]}):
   self.calls=[]
   with self.subTest(bad=bad),self.assertRaises(ValueError):self.invoke([bad])
   self.assertEqual(self.calls,['pending'])
 def test_invalid_present_identity_is_never_hidden_by_a_count_disagreement(self):
  for changed in ({'id':True},{'head_branch':'other'},{'path':'.github/workflows/other.yml'},{'status':'unknown'}):
   self.calls=[]
   with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,'invalid active workflow identity'):self.invoke([{'total_count':0,'workflow_runs':[dict(self.row,**changed)]}])
   self.assertEqual(self.calls,['pending'])
 def test_normal_all_empty_view_uses_exactly_original_five_reads(self):
  self.assertIsNone(self.invoke([self.empty() for _ in range(5)]));self.assertEqual(self.calls,list(self.ns['STATES']))
 def test_transport_error_identity_is_unchanged_without_retry(self):
  error=TimeoutError('original40secondclientbudget')
  def api(url):self.calls.append(url);raise error
  with self.assertRaises(TimeoutError) as got:self.ns['_active'](api,self.workflow)
  self.assertIs(got.exception,error);self.assertEqual(len(self.calls),1)

 def test_recheck_receipt_is_bounded_and_logging_failure_cannot_skip_the_gate(self):
  replies=[{'total_count':1,'workflow_runs':[]}]+[self.empty() for _ in range(5)];out=io.StringIO()
  def api(url):self.calls.append(url);return replies.pop(0)
  with contextlib.redirect_stderr(out):self.assertIsNone(self.ns['_active'](api,self.workflow))
  row=json.loads(out.getvalue());self.assertEqual(row,{'kind':'runtime_workflow_queue_consistency','workflow':self.workflow,'reason':'count_page_disagreement','next_attempt':2});self.assertEqual(len(self.calls),6)

 def test_actual_logging_failure_still_rechecks_every_state_before_active_result(self):
  replies=[{'total_count':1,'workflow_runs':[]}]+[self.empty() for _ in range(4)]+[{'total_count':1,'workflow_runs':[self.row]}]
  with mock.patch('builtins.print',side_effect=OSError('stderr unavailable')):
   self.assertEqual(self.invoke(replies),self.row)
  self.assertEqual(self.calls,['pending',*self.ns['STATES']]);self.assertFalse(replies)

 def test_actual_logging_failure_cannot_hide_second_disagreement(self):
  replies=[{'total_count':1,'workflow_runs':[]},{'total_count':0,'workflow_runs':[self.row]}]
  with mock.patch('builtins.print',side_effect=OSError('stderr unavailable')):
   with self.assertRaisesRegex(ValueError,'incomplete workflow queue response'):self.invoke(replies)
  self.assertEqual(self.calls,['pending','pending']);self.assertFalse(replies)

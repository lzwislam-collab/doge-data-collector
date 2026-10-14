import importlib,unittest,copy
from datetime import datetime,timezone
from unittest.mock import patch
import phase2_forward_v2 as f

class Batches(unittest.TestCase):
 def engine(self):
  self.assertIsNotNone(importlib.util.find_spec('phase3_batches'),'bounded independent batch audit missing')
  return importlib.import_module('phase3_batches')
 def test_1080_events_have_bounded_per_batch_run_requests(self):
  a=self.engine();events=[{'event_id':str(i)+'_formal','registration_run_id':4*i+1,'source_run_id':4*i+2} for i in range(1080)]
  def bindings(e):return [4*i+3,4*i+4] if (i:=int(e['event_id'].split('_')[0]))>=0 else []
  self.assertEqual(len(a.partition(events)),54)
  for group in a.partition(events):self.assertLessEqual(a.rest_call_upper_bound(group,bindings),161)
 def test_incomplete_duplicate_or_wrong_model_certificates_cannot_merge(self):
  a=self.engine();events=[{'event_id':'1_formal'}];scores=[];ident='a'*64
  cert={'assessment_registration_identity':ident,'event_sha256':{'1_formal':f.digest(events[0])},'score_sha256':{},'qualified':[],'rejected':{'1_formal':'missing'}}
  for certs in ([],[{**cert,'assessment_registration_identity':'wrong'}],[cert,cert]):
   with self.assertRaises(ValueError):a.merge_verified(events,scores,certs,ident)
  merged=a.merge_verified(events,scores,[cert],ident);self.assertTrue(merged['network_audit_covers_all_events']);self.assertEqual(merged['qualified'],[])
 def test_changed_raw_events_or_scores_invalidate_previous_batch(self):
  a=self.engine();events=[{'event_id':'1_formal'}];scores=[{'event_id':'1_formal','y':1}];ident='a'*64
  c={'assessment_registration_identity':ident,'event_sha256':{'1_formal':f.digest(events[0])},'score_sha256':{'1_formal':'wrong'},'qualified':[],'rejected':{}}
  with self.assertRaises(ValueError):a.merge_verified(events,scores,[c],ident)
 def test_unverified_certificate_dict_is_never_remote_proof(self):
  a=self.engine()
  with self.assertRaises(ValueError):a.verify_remote_certificate({'run':{'conclusion':'success'}}, {}, datetime.now(timezone.utc))
 def test_batch_run_ids_are_bounded_positive_unique(self):
  a=self.engine()
  for text in ('1,1','-1','abc',','.join(str(i+1) for i in range(65))):
   with self.assertRaises(ValueError):a.parse_ids(text)
  self.assertEqual(a.parse_ids('1,2'),[1,2])
 def test_actual_metadata_fetch_for_maximum_batch_stays_under_budget(self):
  a=self.engine();calls=[]
  def api(path):
   calls.append(path)
   return {'jobs':[{'name':'audit'}]} if path.endswith('/jobs') else {'id':int(path.rsplit('/',1)[1])}
  with patch.object(a.forward.frozen,'api',side_effect=api):rows=a.fetch_runs(set(range(1,81)))
  self.assertEqual(len(rows),80);self.assertEqual(len(calls),160)
  with self.assertRaises(ValueError):a.fetch_runs(set(range(1,82)))

if __name__=='__main__':unittest.main()

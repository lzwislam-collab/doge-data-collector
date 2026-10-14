import copy,json,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
import phase2_forward_v2 as f
import phase2_factor_library as library
import test_learned_shadow_v1 as fixtures

class Forward(unittest.TestCase):
 def frame(self,at):
  last=int(at.timestamp()*1000)//f.H*f.H-f.H
  times=[last-(72-i)*f.H for i in range(73)]
  return {'times':times,**{s:{'close':[100+i*.1 for i in range(73)],'volume':[100+i%3 for i in range(73)],'seen':[t+f.H for t in times]} for s in ('DOGE-USDT','BTC-USDT')}}
 def test_age_can_cover_90_day_observation_without_altering_old_record(self):
  old=json.loads((f.ROOT/f.PARENT).read_bytes());model=f.make_model(old)
  self.assertEqual(old['max_training_data_age_days'],14)
  self.assertEqual(model['max_training_data_age_days'],120)
  p=f.policy();f.check_age(model,f.utc(p['observation_end_utc']))
  self.assertNotEqual(model['trial_model_identity'],old['recent_identity'])
  with self.assertRaises(ValueError):f.check_age(model,f.utc(p['model_expires_utc'])+timedelta(milliseconds=1))
 def test_live_features_match_training_formula_without_future_label(self):
  at=datetime(2026,10,20,0,5,tzinfo=timezone.utc);frame=self.frame(at)
  x=f.features(frame,at)
  extended=copy.deepcopy(frame);extended['times'].append(frame['times'][-1]+f.H)
  for s in ('DOGE-USDT','BTC-USDT'):
   for key in ('close','volume','seen'):extended[s][key].append(frame[s][key][-1])
  expected=library.rows(extended,1,'candle_close_research')[72]['x']
  self.assertEqual(x,[round(expected[j],9) for j in range(5,17)])
  frame['DOGE-USDT']['seen'][-1]=int(at.timestamp()*1000)+1
  with self.assertRaises(ValueError):f.features(frame,at)
 def test_closed_hour_gap_and_future_price_rejected(self):
  at=datetime(2026,10,20,0,5,tzinfo=timezone.utc)
  for modify in (lambda q:q['times'].__setitem__(1,q['times'][0]),lambda q:q['BTC-USDT']['close'].__setitem__(1,float('nan'))):
   frame=self.frame(at);modify(frame)
   with self.assertRaises(ValueError):f.features(frame,at)
 def test_formal_cannot_issue_early_or_late(self):
  old=json.loads((f.ROOT/f.PARENT).read_bytes());m=f.make_model(old);p=f.policy()
  for at in (datetime(2026,10,19,tzinfo=timezone.utc),datetime(2026,10,20,0,46,tzinfo=timezone.utc)):
   with self.assertRaises(ValueError):f.issue(p,m,self.frame(at),fixtures.Shadow().packet(at),at,.5,'formal')
 def test_canary_is_not_formal_and_targets_real_future(self):
  at=datetime(2026,10,14,12,5,tzinfo=timezone.utc);m=f.make_model(json.loads((f.ROOT/f.PARENT).read_bytes()))
  e=f.issue(f.policy(),m,self.frame(at),fixtures.Shadow().packet(at),at,.5,'canary')
  self.assertFalse(e['included_in_formal_statistics']);self.assertEqual(f.utc(e['target_utc'])-at,timedelta(minutes=10))
  with self.assertRaises(ValueError):f.score(e,fixtures.Shadow().packet(at+timedelta(minutes=9)),f.policy())
  score=f.score(e,fixtures.Shadow().packet(at+timedelta(minutes=10),101),f.policy())
  self.assertEqual(score['y'],1);self.assertFalse(score['promotion_authorized'])
 def test_changed_policy_rejected(self):
  p=f.policy();p['minimum_calendar_days']=8
  with self.assertRaises(ValueError):f.check_policy(p)
 def test_missing_target_depth_leaves_cost_unknown(self):
  at=datetime(2026,10,14,12,5,tzinfo=timezone.utc);m=f.make_model(json.loads((f.ROOT/f.PARENT).read_bytes()));fixture=fixtures.Shadow()
  e=f.issue(f.policy(),m,self.frame(at),fixture.packet(at),at,.5,'canary')
  packet=fixture.packet(at+timedelta(minutes=10),101);packet['raw']['book']=None;fixture.sync(packet)
  result=f.score(e,packet,f.policy())
  self.assertIsNone(result['economic_returns']['selected']);self.assertIsNotNone(result['cost_error'])
 def test_post_target_cached_ticker_before_http_request_is_valid(self):
  at=datetime(2026,10,14,12,5,tzinfo=timezone.utc);m=f.make_model(json.loads((f.ROOT/f.PARENT).read_bytes()));fixture=fixtures.Shadow()
  e=f.issue(f.policy(),m,self.frame(at),fixture.packet(at),at,.5,'canary')
  quote_at=at+timedelta(minutes=10,seconds=5);packet=fixture.packet(quote_at,101)
  packet['requested_at_utc']=(quote_at+timedelta(milliseconds=100)).isoformat()
  packet['captured_at_utc']=(quote_at+timedelta(milliseconds=500)).isoformat()
  result=f.score(e,packet,f.policy());self.assertEqual(result['y'],1)
 def test_cached_ticker_before_target_still_rejected(self):
  at=datetime(2026,10,14,12,5,tzinfo=timezone.utc);m=f.make_model(json.loads((f.ROOT/f.PARENT).read_bytes()));fixture=fixtures.Shadow()
  e=f.issue(f.policy(),m,self.frame(at),fixture.packet(at),at,.5,'canary')
  packet=fixture.packet(at+timedelta(minutes=9,seconds=59),101)
  packet['requested_at_utc']=(at+timedelta(minutes=10,seconds=1)).isoformat();packet['captured_at_utc']=packet['requested_at_utc']
  with self.assertRaises(ValueError):f.score(e,packet,f.policy())
 def test_book_before_target_cannot_validate_cost(self):
  at=datetime(2026,10,14,12,5,tzinfo=timezone.utc);m=f.make_model(json.loads((f.ROOT/f.PARENT).read_bytes()));fixture=fixtures.Shadow()
  e=f.issue(f.policy(),m,self.frame(at),fixture.packet(at),at,.5,'canary')
  packet=fixture.packet(at+timedelta(minutes=10),101)
  packet['raw']['book']['data'][0]['ts']=str(int((at+timedelta(minutes=9,seconds=59)).timestamp()*1000));fixture.sync(packet)
  result=f.score(e,packet,f.policy());self.assertIsNone(result['economic_returns']['selected'])

 def test_server_witness_requires_actual_main_successful_job_clock(self):
  run={'id':1,'head_sha':'a'*40,'head_branch':'main','repository':{'full_name':f.old.REPO},
   'path':f.WORKFLOW,'event':'workflow_dispatch','status':'completed','conclusion':'success',
   'created_at':'2026-10-14T10:00:00Z','audit_job':{'name':'audit','status':'completed','conclusion':'success',
   'started_at':'2026-10-14T10:00:01Z','completed_at':'2026-10-14T10:02:00Z'}}
  at=f.utc('2026-10-14T10:03:00Z');read=lambda h,p:b'actual'
  self.assertIsNotNone(f.witness({'file':b'actual'},[run],read,at))
  for key,value in (('head_branch','other'),('conclusion','failure')):
   bad=copy.deepcopy(run);bad[key]=value;self.assertIsNone(f.witness({'file':b'actual'},[bad],read,at))
  self.assertIsNone(f.witness({'file':b'forged'},[run],read,at))
  self.assertIsNone(f.witness({'file':b'actual'},[run],read,f.utc('2026-10-14T10:01:00Z')))

if __name__=='__main__':unittest.main()

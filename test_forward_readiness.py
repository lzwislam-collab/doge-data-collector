import importlib,copy,unittest,csv,io,json,os,tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
import phase2_forward_v2 as f

class Readiness(unittest.TestCase):
 def test_grace_never_hides_an_older_hour_missing(self):
  a=self.engine();now=datetime(2026,10,15,0,5,tzinfo=timezone.utc)
  v=a.health([],[],now,now.replace(minute=0)-timedelta(hours=2),[])
  self.assertIn('LATEST_CLOSED_BAR_MISSING',v['errors'])
  v=a.health([],[],now,now.replace(minute=0)-timedelta(hours=1),[])
  self.assertNotIn('LATEST_CLOSED_BAR_MISSING',v['errors'])
 def test_history_receipt_before_close_and_nonfinite_volume_rejected(self):
  a=self.engine()
  raw=(a.ROOT/a.r.SOURCE).read_bytes();rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
  now=max(f.utc(row['first_ingested_utc']) for row in rows)+timedelta(hours=1)
  a.source_state(raw,now)
  for key,value in (('first_ingested_utc',datetime.fromtimestamp(int(rows[0]['open_utc_ms'])/1000,timezone.utc).isoformat()),('volume_base','nan')):
   bad=copy.deepcopy(rows);bad[0][key]=value;buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(bad)
   with self.assertRaises(ValueError):a.source_state(buf.getvalue().encode(),now)
 def engine(self):
  self.assertIsNotNone(importlib.util.find_spec('forward_readiness'),'operational monitor missing')
  return importlib.import_module('forward_readiness')
 def test_before_activation_zero_formal_is_not_missing(self):
  a=self.engine();now=datetime(2026,10,14,12,tzinfo=timezone.utc)
  v=a.health([],[],now,now,[]);self.assertEqual(v['missing_issuance'],0);self.assertFalse(v['errors'])
 def test_matured_missing_issuance_and_score_are_errors(self):
  a=self.engine();start=f.utc(f.policy()['effective_from_utc']);now=start+timedelta(minutes=90)
  v=a.health([],[],now,now,[]);self.assertEqual(v['missing_issuance'],1);self.assertIn('MISSING_ISSUANCE',v['errors'])
  e={'event_id':str(int(start.timestamp()))+'_formal','cohort':'formal','slot_utc':start.isoformat(),'target_utc':(start+timedelta(hours=1)).isoformat()}
  v=a.health([e],[],now,now,[]);self.assertEqual(v['missing_targets'],1);self.assertIn('MISSING_TARGET',v['errors'])
 def test_due_score_near_deadline_is_urgent_not_backfilled(self):
  a=self.engine();start=f.utc(f.policy()['effective_from_utc']);e={'event_id':'1_formal','cohort':'formal','slot_utc':start.isoformat(),'target_utc':(start+timedelta(hours=1)).isoformat()}
  v=a.health([e],[],start+timedelta(minutes=80),start+timedelta(hours=1),[])
  self.assertIn('TARGET_DEADLINE_NEAR',v['errors']);self.assertEqual(v['due_targets'],['1_formal'])
 def test_source_last_closed_hour_not_merely_internal_gap(self):
  a=self.engine();now=datetime(2026,10,14,12,30,tzinfo=timezone.utc)
  v=a.health([],[],now,now-timedelta(hours=1,minutes=30),[])
  self.assertIn('LATEST_CLOSED_BAR_MISSING',v['errors'])
 def event(self):
  return {'id':1,'run_attempt':1,'repository':{'full_name':f.old.REPO},'head_repository':{'full_name':f.old.REPO},
   'head_branch':'main','head_sha':'a'*40,'event':'schedule','status':'completed','conclusion':'failure','path':'.github/workflows/okx_hourly_extreme_history.yml'}
 def test_auxiliary_failure_can_trigger_reverification_when_core_saved(self):
  a=self.engine();run=self.event();jobs=[{'steps':[{'name':'Fail closed before writes on full-history or frozen protocol mutation','conclusion':'success'},{'name':'Persist data and diagnostics','conclusion':'success'}]}]
  self.assertTrue(a.trusted_completion(run,jobs));jobs[0]['steps'][1]['conclusion']='failure';self.assertFalse(a.trusted_completion(run,jobs))
 def test_untrusted_or_changed_attempt_completion_rejected(self):
  a=self.engine();event=self.event()
  for field,value in (('head_branch','feature'),('event','pull_request'),('head_repository',{'full_name':'other/repo'})):
   bad=copy.deepcopy(event);bad[field]=value;self.assertFalse(a.trusted_completion(bad,[]))
  with self.assertRaises(ValueError):a.match_event(event,{**event,'run_attempt':2})
 def test_existing_collector_completion_is_only_trusted_after_success(self):
  a=self.engine();run=self.event();run['path']='.github/workflows/collect.yml';run['conclusion']='success'
  self.assertTrue(a.trusted_completion(run,[]))
  run['conclusion']='failure';self.assertFalse(a.trusted_completion(run,[]))
  run['conclusion']='success';run['head_branch']='feature';self.assertFalse(a.trusted_completion(run,[]))

 def test_no_cycle_when_no_current_issue_or_target_needed(self):
  a=self.engine();now=datetime(2026,10,14,tzinfo=timezone.utc)
  self.assertEqual(a.actions([],[],now,False,False),[])
  start=f.utc(f.policy()['effective_from_utc']);e={'event_id':str(int(start.timestamp()))+'_formal','cohort':'formal','slot_utc':start.isoformat(),'target_utc':(start+timedelta(hours=1)).isoformat()}
  self.assertEqual(a.actions([e],[],start+timedelta(minutes=10),True,True),[])

class CurrentReadiness(unittest.TestCase):
 def setUp(self):
  self.a=importlib.import_module('forward_readiness');self.p=f.policy();self.start=f.utc(self.p['effective_from_utc'])
 def event(self,hours,target_hours=None,cohort='formal'):
  slot=self.start+timedelta(hours=hours)
  return {'event_id':str(int(slot.timestamp()))+'_'+cohort,'cohort':cohort,'slot_utc':slot.isoformat(),
   'target_utc':(self.start+timedelta(hours=target_hours if target_hours is not None else hours+1)).isoformat()}
 def view(self,events,scores,hours,qualified=()):
  now=self.start+timedelta(hours=hours)
  return self.a.health(events,scores,now,now.replace(minute=0,second=0,microsecond=0),qualified)
 def test_healthy_latest_window_does_not_erase_or_fail_for_old_issuance_gap(self):
  e=self.event(2);v=self.view([e],[{'event_id':e['event_id']}],3.5,[e])
  self.assertEqual(v['missing_issuance_ids'],['1792454400_formal'])
  self.assertIn('MISSING_ISSUANCE',v['errors'])
  self.assertEqual(v.get('runtime_errors',v['errors']),[])
 def test_latest_closed_issue_is_required_even_when_new_window_opens(self):
  old=self.event(0)
  for hours in (2.75,4):
   with self.subTest(hours=hours):
    v=self.view([old],[{'event_id':old['event_id']}],hours,[old])
    self.assertIn('MISSING_ISSUANCE',v.get('runtime_errors',[]))
    self.assertEqual(v['runtime_missing_issuance_ids'],['1792461600_formal'])
 def test_older_missing_target_stays_historical_after_latest_target_is_verified(self):
  old,e=self.event(0),self.event(2);v=self.view([old,e],[{'event_id':e['event_id']}],3.5,[e])
  self.assertEqual(v['missing_target_ids'],[old['event_id']])
  self.assertIn('MISSING_TARGET',v['errors']);self.assertEqual(v.get('runtime_errors',v['errors']),[])
 def test_young_event_cannot_hide_the_latest_matured_missing_target(self):
  old,young=self.event(0),self.event(2);v=self.view([old,young],[],2.1)
  self.assertEqual(v.get('runtime_missing_target_ids'),[old['event_id']])
  self.assertIn('MISSING_TARGET',v['runtime_errors'])
  self.assertEqual({e['event_id'] for e in self.a.publication_events([old,young],self.start+timedelta(hours=2.1),self.p)},
   {old['event_id'],young['event_id']})
 def test_matured_score_needs_online_public_evidence_and_canary_cannot_replace_it(self):
  old,young=self.event(0),self.event(2,cohort='canary')
  v=self.view([old,young],[{'event_id':old['event_id']},{'event_id':young['event_id']}],2.1,[young])
  self.assertIn('PUBLIC_DEADLINE_EVIDENCE_MISSING',v.get('runtime_errors',[]))
  self.assertEqual(v['runtime_missing_public_witness_ids'],[old['event_id']])
 def test_target_deadline_exact_boundary_fails_and_near_deadline_remains_urgent(self):
  e=self.event(0)
  near=self.view([e],[],80/60)
  self.assertIn('TARGET_DEADLINE_NEAR',near.get('runtime_errors',[]))
  due=self.view([e],[],85/60)
  self.assertIn('MISSING_TARGET',due.get('runtime_errors',[]))
 def test_before_activation_and_missing_bar_keep_original_boundaries(self):
  now=self.start-timedelta(hours=1)
  v=self.a.health([],[],now,now,[]);self.assertEqual(v.get('runtime_errors'),[])
  v=self.a.health([],[],now,now-timedelta(hours=2),[])
  self.assertIn('LATEST_CLOSED_BAR_MISSING',v['runtime_errors'])
 def legacy(self):
  return {'effective_from_utc':self.start.isoformat(),'horizons_hours':[1,24],
   'slot_steps_hours':{'1':2,'24':25},'max_issue_delay_minutes':45,'target_window_minutes':25}
 def legacy_event(self,hours,horizon):
  e=self.event(hours,hours+horizon);e['event_id']=str(int((self.start+timedelta(hours=hours)).timestamp()))+'_'+str(horizon)
  e['horizon_hours']=horizon;return e
 def test_original18_latest_issuance_uses_45_minutes_not_statistical_maturity(self):
  v=self.a.legacy_runtime_health(self.legacy(),[],[],self.start+timedelta(minutes=45))
  self.assertEqual(set(v['missing_issuance_ids']),{'1792454400_1','1792454400_24'})
 def test_original18_each_horizon_retains_its_latest_missing_target(self):
  e1,e24=self.legacy_event(26,1),self.legacy_event(0,24)
  v=self.a.legacy_runtime_health(self.legacy(),[e1,e24],[{'event_id':e1['event_id']}],self.start+timedelta(hours=27.5))
  self.assertEqual(v['missing_target_ids'],[e24['event_id']]);self.assertIn('ORIGINAL18_MISSING_TARGET',v['errors'])
 def test_original18_healthy_latest_targets_do_not_retry_older_missing_targets(self):
  old1,old24=self.legacy_event(0,1),self.legacy_event(0,24)
  e1,e24=self.legacy_event(48,1),self.legacy_event(25,24)
  v=self.a.legacy_runtime_health(self.legacy(),[old1,old24,e24,e1],
   [{'event_id':e1['event_id']},{'event_id':e24['event_id']}],self.start+timedelta(hours=49.5))
  self.assertEqual(v['errors'],[]);self.assertEqual(v['missing_target_ids'],[])
 def test_runtime_source_gap_fails_but_original_hour_grace_is_preserved(self):
  source={'latest73_ready':False,'frame_errors':['missing latest73 source bars'],
   'latest_closed_utc':self.start.isoformat(),'asset_rows':{'DOGE-USDT':73,'BTC-USDT':73,'ETH-USDT':1}}
  self.assertEqual(self.a.source_runtime_errors(source,self.start+timedelta(hours=1,minutes=5)),[])
  self.assertIn('SOURCE_FRAME_NOT_READY',self.a.source_runtime_errors(source,self.start+timedelta(hours=1,minutes=15)))
  source['frame_errors']=['empty volume basis']
  self.assertIn('SOURCE_FRAME_NOT_READY',self.a.source_runtime_errors(source,self.start+timedelta(hours=1,minutes=5)))
 def test_cli_preserves_historical_output_but_exports_separate_current_gate(self):
  result={'status':'OPERATIONS_ERRORS_PRESERVED','errors':['MISSING_TARGET'],'dispatches':[],
   'runtime_errors':[],'runtime_status':'CURRENT_WINDOW_CLEAR_WITH_HISTORICAL_GAPS'}
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/'outputs'
   with patch.object(self.a,'ROOT',Path(tmp)),patch.object(self.a,'run',return_value=result),patch('sys.argv',['forward_readiness.py','audit']),patch.dict(os.environ,{'GITHUB_OUTPUT':str(out)}):
    self.a.main()
   self.assertIn('has_errors=true\n',out.read_text());self.assertIn('has_runtime_errors=false\n',out.read_text())
   self.assertEqual(json.loads((Path(tmp)/'forward-readiness-output/readiness.json').read_text())['errors'],['MISSING_TARGET'])
 def test_cli_missing_runtime_contract_never_defaults_to_success(self):
  result={'status':'OPERATIONS_ERRORS_PRESERVED','errors':['MISSING_TARGET'],'dispatches':[]}
  with tempfile.TemporaryDirectory() as tmp:
   with patch.object(self.a,'ROOT',Path(tmp)),patch.object(self.a,'run',return_value=result),patch('sys.argv',['forward_readiness.py','audit']),patch.dict(os.environ,{'GITHUB_OUTPUT':str(Path(tmp)/'outputs')}):
    with self.assertRaises(KeyError):self.a.main()
   self.assertIn('KeyError',json.loads((Path(tmp)/'forward-readiness-output/failure.json').read_text())['error'])

class CollectorQueueProtection(unittest.TestCase):
 def setUp(self):
  self.a=importlib.import_module('forward_readiness')
  self.collector='okx_hourly_extreme_history.yml'
  self.research='learned_shadow_live.yml'
 def api(self,blocking=None,own_pending=False):
  from urllib.parse import parse_qs,urlsplit
  def request(path):
   query=parse_qs(urlsplit(path).query)
   if path.startswith('/actions/workflows/'+self.research+'/'):
    rows=[{'id':42,'head_branch':'main','status':'pending','path':'.github/workflows/'+self.research}] if own_pending and query.get('status')==['pending'] else []
    return {'total_count':len(rows),'workflow_runs':rows}
   if '/'+self.collector+'/' not in path:return {'total_count':0,'workflow_runs':[]}
   # An old pending collector is deliberately absent from the recent-history page.
   if query.get('branch')!=['main'] or not query.get('status'):
    return {'total_count':11,'workflow_runs':[{'head_branch':'main','status':'completed'}]*10}
   rows=[blocking] if blocking and query['status']==[blocking['status']] else []
   return {'total_count':len(rows),'workflow_runs':rows}
  return request
 def post(self):
  from unittest.mock import MagicMock
  response=MagicMock();response.__enter__.return_value.status=204
  return response
 def collector_row(self,status):
  return {'id':41,'head_branch':'main','status':status,'path':'.github/workflows/'+self.collector}
 def test_each_waiting_collector_state_blocks_research_post_even_after_ten_newer_runs(self):
  for status in ('pending','queued','requested','waiting'):
   with self.subTest(status=status),patch.object(self.a.r.frozen,'api',side_effect=self.api(self.collector_row(status))),patch('urllib.request.urlopen',return_value=self.post()) as post,patch.dict(os.environ,{'GITHUB_TOKEN':'unit-test-token'}):
    result=self.a.dispatch_once(self.research)
    self.assertEqual(result['status'],'DEFERRED_NORMAL_COLLECTOR_PENDING')
    self.assertEqual(result['normal_collector_run_id'],41);post.assert_not_called()
 def test_running_collector_alone_does_not_block_existing_research_dispatch(self):
  with patch.object(self.a.r.frozen,'api',side_effect=self.api(self.collector_row('in_progress'))),patch('urllib.request.urlopen',return_value=self.post()) as post,patch.dict(os.environ,{'GITHUB_TOKEN':'unit-test-token'}):
   result=self.a.dispatch_once(self.research)
  self.assertEqual(result,{'workflow':self.research,'status':'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED'})
  post.assert_called_once();request=post.call_args.args[0]
  self.assertEqual(request.get_method(),'POST');self.assertEqual(json.loads(request.data),{'ref':'main'})
  self.assertTrue(request.full_url.endswith('/actions/workflows/'+self.research+'/dispatches'))
 def test_empty_queue_uses_four_filtered_reads_and_original_post(self):
  with patch.object(self.a.r.frozen,'api',side_effect=self.api()) as get,patch('urllib.request.urlopen',return_value=self.post()) as post,patch.dict(os.environ,{'GITHUB_TOKEN':'unit-test-token'}):
   self.a.dispatch_once(self.research)
  from urllib.parse import parse_qs,urlsplit
  queries=[parse_qs(urlsplit(c.args[0]).query) for c in get.call_args_list if '/'+self.collector+'/' in c.args[0]]
  self.assertEqual({q['status'][0] for q in queries},{'pending','queued','requested','waiting'})
  self.assertTrue(all(q['branch']==['main'] and q['per_page']==['1'] for q in queries));post.assert_called_once()
 def test_queue_lookup_failure_never_posts(self):
  for failed_status in ('pending','queued','requested','waiting'):
   normal=self.api()
   def request(path):
    if 'status='+failed_status+'&' in path:raise OSError('queue read unavailable')
    return normal(path)
   with self.subTest(status=failed_status),patch.object(self.a.r.frozen,'api',side_effect=request),patch('urllib.request.urlopen',return_value=self.post()) as post,patch.dict(os.environ,{'GITHUB_TOKEN':'unit-test-token'}):
    with self.assertRaises(OSError):self.a.dispatch_once(self.research)
    post.assert_not_called()
 def test_unknown_queue_response_never_defaults_to_empty(self):
  for response in ({},{'workflow_runs':None},{'workflow_runs':{}},{'workflow_runs':[None]},
    {'total_count':1,'workflow_runs':[]},
    {'total_count':1,'workflow_runs':[{'id':41,'head_branch':'feature','status':'pending','path':'.github/workflows/'+self.collector}]}):
   def request(path):
    return response if '/'+self.collector+'/' in path else {'total_count':0,'workflow_runs':[]}
   with self.subTest(response=response),patch.object(self.a.r.frozen,'api',side_effect=request),patch('urllib.request.urlopen',return_value=self.post()) as post,patch.dict(os.environ,{'GITHUB_TOKEN':'unit-test-token'}):
    with self.assertRaises((ValueError,TypeError,KeyError)):self.a.dispatch_once(self.research)
    post.assert_not_called()
 def test_existing_research_pending_returns_without_collector_reads_or_post(self):
  with patch.object(self.a.r.frozen,'api',side_effect=self.api(own_pending=True)) as get,patch('urllib.request.urlopen') as post:
   self.assertEqual(self.a.dispatch_once(self.research),{'workflow':self.research,'status':'ALREADY_PENDING'})
  self.assertEqual(get.call_count,1);post.assert_not_called()
 def test_other_approved_dispatch_keeps_its_existing_payload_and_checks(self):
  with patch.object(self.a.r.frozen,'api',return_value={'total_count':0,'workflow_runs':[]}) as get,patch('urllib.request.urlopen',return_value=self.post()) as post,patch.dict(os.environ,{'GITHUB_TOKEN':'unit-test-token'}):
   result=self.a.dispatch_once('phase2_forward_live_v2.yml')
  self.assertEqual(result['status'],'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED');self.assertEqual(get.call_count,5)
  self.assertEqual(json.loads(post.call_args.args[0].data),{'ref':'main','inputs':{'mode':'publish'}})


class OriginalTargetBridge(unittest.TestCase):
 """Exercise the real bridge, CSV eligibility and Request payload; only remote I/O and unrelated model audits are doubled."""
 def setUp(self):
  from contextlib import ExitStack
  import quote_only_rescue as rescue
  self.a=importlib.import_module('forward_readiness');self.rescue=rescue
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.target=datetime(2026,11,9,3,22,30,940428,tzinfo=timezone.utc)
  self.now=datetime(2026,11,9,3,24,57,901549,tzinfo=timezone.utc)
  self.fid='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1794186000000_1h'
  self.parent={'id':42555437195,'run_attempt':1,'repository':{'full_name':f.old.REPO},
   'head_repository':{'full_name':f.old.REPO},'head_branch':'main','head_sha':'a'*40,
   'event':'workflow_run','status':'completed','conclusion':'success','pull_requests':[],
   'path':'.github/workflows/phase2_forward_audit_v2.yml',
   'created_at':'2026-11-09T03:16:01Z','updated_at':'2026-11-09T03:22:33Z'}
  self.jobs=[{'id':128959965819,'run_id':42555437195,'status':'completed','conclusion':'success',
   'started_at':'2026-11-09T03:16:03Z','completed_at':'2026-11-09T03:22:32Z',
   'steps':[{'name':'Fail closed before writes on full-history or frozen protocol mutation','conclusion':'success'},
    {'name':'Persist data and diagnostics','conclusion':'success'}]}]
  self.active=[];self.attempts=[];self.posts=[];self.gets=[];self.override=None;self.on_get=None
  self.remote_override=None;self.main_sha='c'*40
  self.set_state([(self.fid,self.target)])
  source=self.root/self.a.r.SOURCE;source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(b'upstream source fixture')
  policy=self.root/f.old.POLICY;policy.parent.mkdir(parents=True,exist_ok=True)
  policy.write_text(json.dumps({'effective_from_utc':'2026-10-20T00:00:00+00:00','horizons_hours':[1,24],
   'slot_steps_hours':{'1':2,'24':25},'max_issue_delay_minutes':45,'target_window_minutes':25}))
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  import learned_shadow_runtime as legacy
  for obj,name,value in (
   (self.a,'ROOT',self.root),(self.a.r,'run',lambda mode:None),
   (self.a.r,'guard',lambda:(f.policy(),None,None,{})),(self.a.r,'records',lambda kind:[]),
   (self.a.r,'runs_server',lambda extra:[]),(self.a.r,'publication',lambda *args:([],[])),
   (self.a,'source_state',lambda *args:{'rows':81891,'asset_rows':{'DOGE-USDT':27297,'BTC-USDT':27297,'ETH-USDT':27297},
    'latest_closed_utc':'2026-11-09T03:00:00+00:00','latest73_ready':True,'frame_errors':[]}),
   (self.a.f,'witness',lambda *args:None),(legacy,'read_records',lambda *args:[]),
   (legacy,'plan',lambda *args:{'issue':[],'score':[],'matured_missing_issuance':{}}),
   (self.a,'actions',lambda *args:[]),(self.a.terminal,'audit',lambda *args:{})):
   self.stack.enter_context(patch.object(obj,name,value))
  owner=self
  class Clock(datetime):
   @classmethod
   def now(cls,tz=None):return owner.now
  self.stack.enter_context(patch.object(self.a,'datetime',Clock))
  self.stack.enter_context(patch.object(self.a.r.frozen,'api',side_effect=self.api))
  self.stack.enter_context(patch('urllib.request.urlopen',side_effect=self.post))
  self.stack.enter_context(patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_TOKEN':'unit-test-token'}))
 def set_state(self,entries,scored=()):
  registry=self.root/self.rescue.base.REG;registry.parent.mkdir(parents=True,exist_ok=True)
  with registry.open('w',newline='') as stream:
   writer=csv.DictWriter(stream,fieldnames=self.rescue.frozen.FCOLS);writer.writeheader()
   for fid,target in entries:
    row={k:'' for k in self.rescue.frozen.FCOLS}
    row.update(forecast_id=fid,issued_at_utc=(target-timedelta(hours=1)).isoformat(),
     source_quote_utc=(target-timedelta(hours=1,seconds=1)).isoformat(),target_utc=target.isoformat(),
     horizon_hours='1',reference_price='0.07')
    writer.writerow(row)
  scores=self.root/self.rescue.base.SCORES;scores.parent.mkdir(parents=True,exist_ok=True)
  with scores.open('w',newline='') as stream:
   writer=csv.DictWriter(stream,fieldnames=self.rescue.frozen.SCOLS);writer.writeheader()
   for fid in scored:
    row={k:'' for k in self.rescue.frozen.SCOLS};row.update(forecast_id=fid,status='scored');writer.writerow(row)
  self.remote_files={self.rescue.base.REG:registry.read_bytes(),self.rescue.base.SCORES:scores.read_bytes()}
  import hashlib
  self.main_sha=hashlib.sha1(b''.join(self.remote_files.values())).hexdigest()
 def runrow(self,workflow,status='completed',created=None,conclusion='failure'):
  return {'id':42555000000,'run_attempt':1,'path':'.github/workflows/'+workflow,
   'head_branch':'main','event':'workflow_dispatch','status':status,'conclusion':conclusion,
   'created_at':(created or self.target+timedelta(seconds=30)).isoformat()}
 def api(self,path):
  from urllib.parse import parse_qs,urlsplit
  if path=='/actions/runs/'+str(self.parent['id']):return copy.deepcopy(self.parent)
  if path.startswith('/actions/runs/'+str(self.parent['id'])+'/attempts/'):
   return {'jobs':copy.deepcopy(self.jobs)}
  self.gets.append(path)
  if self.on_get:self.on_get(path)
  if path.startswith(('/git/ref/','/contents/')):
   if self.remote_override:return self.remote_override(path)
   if path=='/git/ref/heads/main':return {'object':{'type':'commit','sha':self.main_sha}}
   import base64,hashlib
   parsed=urlsplit(path);query=parse_qs(parsed.query);name=parsed.path[len('/contents/'):]
   self.assertEqual(query,{'ref':[self.main_sha]});raw=self.remote_files[name]
   return {'type':'file','path':name,'encoding':'base64','size':len(raw),
    'sha':hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),
    'content':base64.b64encode(raw).decode()}
  if self.override is not None:return self.override(path)
  parsed=urlsplit(path);query=parse_qs(parsed.query)
  workflow=parsed.path.split('/')[3]
  if query.get('branch')!=['main'] or query.get('per_page')!=['1']:
   return {'total_count':11,'workflow_runs':[self.runrow(workflow)]*10}
  if 'status' in query:
   rows=[row for row in self.active if row['path']=='.github/workflows/'+workflow and row['status']==query['status'][0]]
  else:
   if query.get('event')!=['workflow_dispatch'] or len(query.get('created',[]))!=1:
    raise AssertionError('attempt lookup must be server-filtered by dispatch and creation time: '+path)
   bound=query['created'][0];self.assertTrue(bound.startswith('>='))
   since=f.utc(bound[2:])
   rows=[row for row in self.attempts if row['path']=='.github/workflows/'+workflow and f.utc(row['created_at'])>=since]
  return {'total_count':len(rows),'workflow_runs':copy.deepcopy(rows[:1])}
 def post(self,request,**kwargs):
  from unittest.mock import MagicMock
  self.posts.append(request);response=MagicMock();response.__enter__.return_value.status=204;return response
 def invoke(self,mode='bridge',event=True):
  path=self.root/'event.json';path.write_text(json.dumps({'workflow_run':copy.deepcopy(self.parent)} if event else {}))
  with patch.dict(os.environ,{'GITHUB_EVENT_PATH':str(path)}):return self.a.run(mode)
 def assert_rescue(self):
  self.assertEqual(len(self.posts),1,'eligible original TARGET was not routed through the existing bridge')
  request=self.posts[0]
  self.assertEqual(request.get_method(),'POST')
  self.assertTrue(request.full_url.endswith('/actions/workflows/quote_only_rescue.yml/dispatches'))
  payload=json.loads(request.data)
  self.assertEqual(payload,{'ref':'main','inputs':{'expected_ids_json':json.dumps([self.fid],separators=(',',':'))}})
 def test_observed_clock_completions_route_open_original_target_with_actual_payload(self):
  for stamp in ('2026-11-09T03:24:57.901549+00:00','2026-11-09T03:30:15.078246+00:00'):
   with self.subTest(stamp=stamp):
    self.posts.clear();self.now=f.utc(stamp);result=self.invoke();self.assert_rescue()
    self.assertTrue(result['parent_trusted']);self.assertIn('MISSING_ISSUANCE',result['errors'])
 def test_future_empty_and_scored_targets_do_not_post(self):
  for entries,scored in ([],()),([(self.fid,self.now+timedelta(microseconds=1))],()),([(self.fid,self.target)],(self.fid,)):
   with self.subTest(entries=entries,scored=scored):
    self.posts.clear();self.set_state(entries,scored);self.invoke();self.assertEqual(self.posts,[])
 def test_twenty_minute_boundary_preserves_existing_five_minute_reserve(self):
  self.now=self.target+timedelta(minutes=20);self.invoke();self.assert_rescue()
  self.posts.clear();self.now+=timedelta(microseconds=1);self.invoke();self.assertEqual(self.posts,[])
 def test_unsafe_aggregate_batch_never_invokes_frozen_watch(self):
  self.set_state([(self.fid,self.target),('older-target',self.now-timedelta(minutes=21))])
  self.invoke();self.assertEqual(self.posts,[])
 def test_more_than_one_hundred_current_ids_fails_closed(self):
  self.set_state([(str(i),self.target) for i in range(101)])
  with self.assertRaises(ValueError):self.invoke()
  self.assertEqual(self.posts,[])
 def test_all_active_normal_and_rescue_states_block_even_outside_recent_ten(self):
  for workflow in ('okx_hourly_extreme_history.yml','quote_only_rescue.yml'):
   for status in ('pending','queued','requested','waiting','in_progress'):
    with self.subTest(workflow=workflow,status=status):
     self.posts.clear();self.gets.clear();self.active=[self.runrow(workflow,status)]
     self.invoke();self.assertEqual(self.posts,[])
     from urllib.parse import parse_qs,urlsplit
     queries=[parse_qs(urlsplit(path).query) for path in self.gets if 'status=' in path]
     self.assertTrue(queries);self.assertTrue(all(q.get('branch')==['main'] and q.get('per_page')==['1'] for q in queries))
 def test_prior_failed_or_cancelled_attempt_in_target_window_is_not_retried(self):
  self.jobs[0]['completed_at']='2026-11-09T03:24:00Z'
  for conclusion in ('failure','cancelled','success'):
   with self.subTest(conclusion=conclusion):
    self.posts.clear();self.attempts=[self.runrow('quote_only_rescue.yml',created=self.target+timedelta(seconds=30),conclusion=conclusion)]
    self.invoke();self.assertEqual(self.posts,[])
 def test_same_parent_is_not_reused_for_a_later_target(self):
  self.attempts=[self.runrow('quote_only_rescue.yml',created=f.utc('2026-11-09T03:23:00Z'))]
  self.set_state([(self.fid,f.utc('2026-11-09T03:24:00Z'))]);self.invoke();self.assertEqual(self.posts,[])
 def test_attempt_before_parent_and_target_does_not_silence_new_target(self):
  self.attempts=[self.runrow('quote_only_rescue.yml',created=f.utc('2026-11-09T03:20:00Z'))]
  self.invoke();self.assert_rescue()
 def test_final_clock_expiry_during_server_checks_prevents_post(self):
  self.on_get=lambda path:setattr(self,'now',self.target+timedelta(minutes=20,microseconds=1)) if path.startswith('/actions/workflows/') else None
  self.invoke();self.assertEqual(self.posts,[])
 def test_score_added_during_server_checks_prevents_post(self):
  candidate_head=self.main_sha
  self.on_get=lambda path:self.set_state([(self.fid,self.target)],(self.fid,)) if path.startswith('/actions/workflows/') else None
  result=self.invoke();self.assertEqual(self.posts,[])
  rescue=result['dispatches'][0]
  self.assertEqual(rescue['candidate_head_sha'],candidate_head)
  self.assertEqual(rescue['dispatch_head_sha'],self.main_sha)
  self.assertNotEqual(rescue['candidate_head_sha'],rescue['dispatch_head_sha'])
  self.assertEqual(rescue['status'],'SKIP_WINDOW_CLOSED_OR_SCORED')
 def test_parent_source_rescue_failure_and_manual_modes_never_route_rescue(self):
  for path,conclusion,mode,event in (
   (self.a.SOURCE_WORKFLOW,'success','bridge',True),
   ('.github/workflows/quote_only_rescue.yml','success','bridge',True),
   ('.github/workflows/phase2_forward_audit_v2.yml','failure','bridge',True),
   ('.github/workflows/phase2_forward_audit_v2.yml','success','audit',True),
   ('.github/workflows/phase2_forward_audit_v2.yml','success','bridge',False)):
   with self.subTest(path=path,conclusion=conclusion,mode=mode,event=event):
    self.parent['path']=path;self.parent['conclusion']=conclusion;self.posts.clear()
    self.invoke(mode,event);self.assertEqual(self.posts,[])
 def test_unknown_queue_api_failure_and_malformed_identity_do_not_post(self):
  invalid=({}, {'total_count':1,'workflow_runs':[]}, {'total_count':0,'workflow_runs':[{}]},
   {'total_count':'0','workflow_runs':[]},
   {'total_count':1,'workflow_runs':[self.runrow('other.yml','pending')]})
  for response in invalid:
   with self.subTest(response=response):
    self.posts.clear();self.override=lambda path:response
    with self.assertRaises((ValueError,KeyError,TypeError)):self.invoke()
    self.assertEqual(self.posts,[])
  def unavailable(path):raise OSError('provider unavailable')
  self.override=unavailable
  with self.assertRaises(OSError):self.invoke()
  self.assertEqual(self.posts,[])
 def test_missing_parent_job_completion_cannot_claim_parent_deduplication(self):
  self.jobs[0].pop('completed_at')
  with self.assertRaises((ValueError,KeyError)):self.invoke()
  self.assertEqual(self.posts,[])
 def test_uses_current_main_files_instead_of_event_checkout(self):
  (self.root/self.rescue.base.REG).write_text('stale event checkout has no current rows')
  self.invoke();self.assert_rescue()
  self.assertGreaterEqual(self.gets.count('/git/ref/heads/main'),2)
 def test_invalid_main_identity_content_or_registry_fails_closed(self):
  for response in ({'object':{'type':'commit','sha':'invalid'}},{'object':{'type':'tree','sha':'c'*40}},{}):
   with self.subTest(response=response):
    self.remote_override=lambda path:response
    with self.assertRaises((ValueError,KeyError,TypeError)):self.invoke()
    self.assertEqual(self.posts,[])
  self.remote_override=None
  self.remote_files[self.rescue.base.REG]=b'forecast_id,target_utc\nwrong,bad\n'
  with self.assertRaises(ValueError):self.invoke()
  self.assertEqual(self.posts,[])
 def test_blob_mismatch_and_duplicate_forecast_ids_do_not_post(self):
  name=self.rescue.base.REG
  content=self.api('/contents/'+name+'?ref='+self.main_sha)
  for changes in ({'sha':'f'*40},{'size':content['size']+1},{'content':'not base64'}):
   with self.subTest(changes=changes):
    self.remote_override=lambda path:({'object':{'type':'commit','sha':self.main_sha}} if path.startswith('/git/ref/') else {**content,**changes})
    with self.assertRaises(ValueError):self.invoke()
    self.assertEqual(self.posts,[])
  self.remote_override=None
  self.remote_files[name]+=self.remote_files[name].splitlines(keepends=True)[1]
  with self.assertRaises(ValueError):self.invoke()
  self.assertEqual(self.posts,[])
 def test_attempt_response_must_match_dispatch_time_filter(self):
  row=self.runrow('quote_only_rescue.yml')
  for changes in ({'event':'schedule'},{'created_at':'2026-11-09T02:00:00Z'},
   {'created_at':'2026-11-10T02:00:00Z'},{'status':'unknown'},{'id':True}):
   with self.subTest(changes=changes):
    self.override=lambda path:({'total_count':0,'workflow_runs':[]} if 'status=' in path
     else {'total_count':1,'workflow_runs':[{**row,**changes}]})
    with self.assertRaises(ValueError):self.invoke()
    self.assertEqual(self.posts,[])
 def test_normal_dispatch_requested_in_this_bridge_takes_priority(self):
  with patch.object(self.a,'actions',return_value=['okx_hourly_extreme_history.yml']):result=self.invoke()
  self.assertEqual(len(self.posts),1)
  self.assertTrue(self.posts[0].full_url.endswith('/okx_hourly_extreme_history.yml/dispatches'))
  self.assertEqual(result['dispatches'][-1]['status'],'DEFERRED_NORMAL_COLLECTOR_REQUESTED')
  self.assertNotIn('/git/ref/heads/main',self.gets)
 def test_fork_pr_or_changed_parent_attempt_never_posts(self):
  for field,value in (('head_branch','feature'),('event','pull_request'),('head_repository',{'full_name':'untrusted/fork'})):
   original=copy.deepcopy(self.parent)
   with self.subTest(field=field):
    self.parent[field]=value;self.invoke();self.assertEqual(self.posts,[])
   self.parent=original
  real_match=self.a.match_event
  def different_attempt(event,run):return real_match(event,{**run,'run_attempt':run['run_attempt']+1})
  with patch.object(self.a,'match_event',side_effect=different_attempt):
   with self.assertRaises(ValueError):self.invoke()
  self.assertEqual(self.posts,[])


from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

class DispatchQueue(unittest.TestCase):
    names = ('phase2_forward_live_v2.yml', 'phase2_forward_audit_v2.yml',
             'okx_hourly_extreme_history.yml', 'learned_shadow_live.yml',
             'learned_shadow_v1.yml')
    states = ('pending', 'queued', 'requested', 'waiting', 'in_progress')

    def setUp(self):
        self.a = importlib.import_module('forward_readiness')
        self.response = MagicMock()
        self.response.__enter__.return_value.status = 204

    def row(self, name, state, rid=41):
        return {'id': rid, 'head_branch': 'main', 'status': state,
                'path': '.github/workflows/' + name}

    def api(self, name, blocking=None):
        def request(path):
            q = parse_qs(urlsplit(path).query)
            if '/actions/workflows/' + name + '/runs?' not in path:
                return {'total_count': 0, 'workflow_runs': []}
            if not q.get('status'):
                # Ten newer completed runs hide an older active run in production.
                return {'total_count': 11, 'workflow_runs': [
                    self.row(name, 'completed', 100 + i) for i in range(10)]}
            self.assertEqual(q.get('branch'), ['main'])
            self.assertEqual(q.get('per_page'), ['1'])
            rows = [blocking] if blocking and q['status'] == [blocking['status']] else []
            return {'total_count': len(rows), 'workflow_runs': rows}
        return request

    def test_all_approved_workflows_defer_old_active_runs_after_recent_page_churn(self):
        for name in self.names:
            for state in self.states:
                with self.subTest(name=name, state=state), \
                        patch.object(self.a.r.frozen, 'api', side_effect=self.api(name, self.row(name, state))), \
                        patch('urllib.request.urlopen', return_value=self.response) as post, \
                        patch.dict(os.environ, {'GITHUB_TOKEN': 'unit-test-token'}):
                    result = self.a.dispatch_once(name)
                    self.assertEqual(result['status'], 'ALREADY_PENDING')
                    post.assert_not_called()

    def test_filtered_api_failure_never_posts(self):
        name = 'phase2_forward_live_v2.yml'
        normal = self.api(name)
        for state in self.states:
            def request(path):
                if parse_qs(urlsplit(path).query).get('status') == [state]:
                    raise OSError('queue unavailable')
                return normal(path)
            with self.subTest(state=state), patch.object(self.a.r.frozen, 'api', side_effect=request), \
                    patch('urllib.request.urlopen', return_value=self.response) as post, \
                    patch.dict(os.environ, {'GITHUB_TOKEN': 'unit-test-token'}):
                with self.assertRaises(OSError):
                    self.a.dispatch_once(name)
                post.assert_not_called()

    def test_filtered_response_must_have_complete_trusted_identity(self):
        name = 'phase2_forward_live_v2.yml'
        invalid = ({}, {'total_count': 1, 'workflow_runs': []},
                   {'total_count': 0, 'workflow_runs': [self.row(name, 'pending')]},
                   {'total_count': True, 'workflow_runs': []},
                   {'total_count': 1, 'workflow_runs': [self.row('unapproved.yml', 'pending')]},
                   {'total_count': 1, 'workflow_runs': [{**self.row(name, 'pending'), 'head_branch': 'feature'}]})
        for response in invalid:
            def request(path):
                return response if parse_qs(urlsplit(path).query).get('status') else self.api(name)(path)
            with self.subTest(response=response), patch.object(self.a.r.frozen, 'api', side_effect=request), \
                    patch('urllib.request.urlopen', return_value=self.response) as post, \
                    patch.dict(os.environ, {'GITHUB_TOKEN': 'unit-test-token'}):
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    self.a.dispatch_once(name)
                post.assert_not_called()

    def test_empty_queue_preserves_existing_exact_payload_after_all_five_checks(self):
        name = 'phase2_forward_live_v2.yml'
        with patch.object(self.a.r.frozen, 'api', side_effect=self.api(name)) as get, \
                patch('urllib.request.urlopen', return_value=self.response) as post, \
                patch.dict(os.environ, {'GITHUB_TOKEN': 'unit-test-token'}):
            result = self.a.dispatch_once(name)
        self.assertEqual(result['status'], 'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED')
        self.assertEqual({parse_qs(urlsplit(c.args[0]).query).get('status', [None])[0]
                          for c in get.call_args_list}, set(self.states))
        post.assert_called_once()
        self.assertEqual(json.loads(post.call_args.args[0].data),
                         {'ref': 'main', 'inputs': {'mode': 'publish'}})



if __name__=='__main__':unittest.main()

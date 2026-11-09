import base64,hashlib,json,os,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
import runtime_custody_bridge as b
NOW=datetime(2026,11,9,12,10,tzinfo=timezone.utc)
REPO='lzwislam-collab/doge-data-collector'
def enc(x):return json.dumps(x,sort_keys=True,indent=2).encode()+b'\n'
def sha(x):return hashlib.sha256(x).hexdigest()
def blob(x):return hashlib.sha1(b'blob '+str(len(x)).encode()+b'\0'+x).hexdigest()
class Remote:
 def __init__(self):
  self.head='1'*40;self.files={};self.calls=[];self.active=None;self.bad_queue=None;self.posts=[];self.clock=NOW
  p={'protocol_id':'DOGE_LEARNED_SHADOW_V1_20261016','horizons_hours':[1,24],'max_issue_delay_minutes':45,'slot_steps_hours':{'1':2,'24':25}}
  q={'protocol_id':'DOGE_V2_FROZEN120_20261020_R2','horizon_seconds':3600,'issue_window_seconds':2700,'slot_seconds':7200}
  self.files['config/learned_shadow_v1.json']=enc(p);self.files['config/phase2_forward_v2.json']=enc(q)
  self.files['config/phase2_forward_registration_v2.json']=enc({'sha256_lf':{k:sha(v) for k,v in self.files.items()}})
 def add(self,track='original18',hours=1,slot=None):
  slot=slot or NOW.replace(minute=0);issued=slot+timedelta(minutes=9)
  eid=str(int(slot.timestamp()))+'_'+(str(hours) if track=='original18' else 'formal')
  e={'event_id':eid,'slot_utc':slot.isoformat(),'issued_at_utc':issued.isoformat(),'target_utc':(issued+timedelta(hours=hours)).isoformat(),'protocol_id':'DOGE_LEARNED_SHADOW_V1_20261016' if track=='original18' else 'DOGE_V2_FROZEN120_20261020_R2'}
  if track=='original18':e['horizon_hours']=hours
  else:e.update(cohort='formal',horizon_seconds=3600,included_in_formal_statistics=True)
  path=('data/learned_shadow_v1/events/' if track=='original18' else 'data/phase2_forward_v2/events/')+eid+'.json'
  self.files[path]=enc(e);return path,e
 def meta(self,path,raw):return {'type':'file','path':path,'name':path.rsplit('/',1)[-1],'sha':blob(raw),'size':len(raw)}
 def make_trees(self):
  self.trees={}
  dirs={'data/learned_shadow_v1/events','data/phase2_forward_v2/events'}
  for p in list(self.files)+list(dirs):
   parts=p.split('/')
   for i in range(1,len(parts)):dirs.add('/'.join(parts[:i]))
  def build(prefix):
   entries=[];children=set()
   pre=prefix+'/' if prefix else ''
   for p in list(self.files)+list(dirs):
    if p.startswith(pre) and p!=prefix:children.add(p[len(pre):].split('/')[0])
   for name in sorted(children):
    p=pre+name
    if p in dirs:entries.append({'path':name,'type':'tree','mode':'040000','sha':build(p)})
    else:entries.append({'path':name,'type':'blob','mode':'100644','sha':blob(self.files[p])})
   body=b''.join(e['mode'].lstrip('0').encode()+b' '+e['path'].encode()+b'\0'+bytes.fromhex(e['sha']) for e in sorted(entries,key=lambda e:e['path'].encode()+(b'/' if e['type']=='tree' else b'')))
   oid=hashlib.sha1(b'tree '+str(len(body)).encode()+b'\0'+body).hexdigest();self.trees[oid]={'sha':oid,'truncated':False,'tree':entries};return oid
  return build('')
 def api(self,path):
  self.calls.append(path);u=urlsplit(path);q=parse_qs(u.query)
  if u.path=='/git/ref/heads/main':return {'object':{'type':'commit','sha':self.head}}
  if u.path.startswith('/git/commits/'):
   return {'sha':self.head,'tree':{'sha':self.make_trees()}}
  if u.path.startswith('/git/trees/'):
   return self.trees[u.path.rsplit('/',1)[-1]]
  if '/actions/workflows/' in u.path:
   if self.bad_queue is not None:return self.bad_queue
   rows=[self.active] if self.active and q.get('status')==[self.active['status']] else []
   return {'total_count':len(rows),'workflow_runs':rows}
  if u.path.startswith('/contents/'):
   p=u.path[len('/contents/'):];self.assert_ref(q)
   if p in self.files:
    r=self.files[p];return {**self.meta(p,r),'encoding':'base64','content':base64.b64encode(r).decode()}
   if p in ('data/learned_shadow_v1/events','data/phase2_forward_v2/events'):
    return [self.meta(k,v) for k,v in sorted(self.files.items()) if k.startswith(p+'/') and '/' not in k[len(p)+1:]]
   raise HTTPError(path,404,'missing',{},None)
  raise AssertionError(path)
 def assert_ref(self,q):
  if q.get('ref')!=[self.head]:raise AssertionError('unfixed source')
 def publish(self,root):
  for p in root.glob('data/runtime_deadline_v1/custody_dispatch_attempts/*/*.json'):
   self.files[p.relative_to(root).as_posix()]=p.read_bytes()
  self.head='2'*40
 def post(self,payload,timeout):
  self.posts.append(payload)
  if not any(p.startswith('data/runtime_deadline_v1/custody_dispatch_attempts/') for p in self.files):raise AssertionError('POST before remote intent')
  return 204
class Bridge(unittest.TestCase):
 def setUp(self):
  b._TREE_CACHE.clear();b._POLICY_CACHE.clear()
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name);self.r=Remote()
  self.env=patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':REPO,'GITHUB_EVENT_NAME':'push','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'});self.env.start();self.addCleanup(self.env.stop)
 def call(self,publish=None):
  return b.tick(self.root,publish or (lambda:self.r.publish(self.root)),api=self.r.api,post=self.r.post,clock=lambda:self.r.clock)
 def receipt(self,path,e,track='original18'):
  raw=self.r.files[path];d=sha(raw);issued=datetime.fromisoformat(e['issued_at_utc'])
  return {'status':'PRE_TARGET_WORM_VERIFIED','track':track,'event_id':e['event_id'],'issued_at_utc':e['issued_at_utc'],'target_utc':e['target_utc'],'sha256':d,'byte_count':len(raw),'bucket':b.custody.inventory.BUCKET,'endpoint':b.custody.inventory.ENDPOINT,'key':b.custody.PREFIX+track+'/'+e['event_id']+'/'+d+'.json','version_id':'v1','readback_request_id':'get1','server_last_modified_utc':issued.isoformat(),'server_response_date_utc':(issued+timedelta(seconds=1)).isoformat(),'verified_at_utc':(issued+timedelta(seconds=2)).isoformat(),'calculated_retain_until_utc':'2028-11-09T12:00:00Z','historical_backfill':False}
 def test_publish_exact_intent_before_post_and_no_success_claim(self):
  self.r.add();r=self.call();self.assertEqual(r['status'],'DISPATCH_ACCEPTED_CUSTODY_NOT_VERIFIED');self.assertEqual(len(self.r.posts),1)
  self.assertEqual(self.r.posts[0],{'ref':'main','inputs':{'engineering_probe':False,'extend_retention_days':'0','force_readback':False}})
  self.assertFalse(r['custody_verified']);self.assertEqual(r['intent_readback_sha'],self.r.head)
 def test_restart_does_not_resend_same_public_intent(self):
  self.r.add();self.call()
  with tempfile.TemporaryDirectory() as t:r=b.tick(Path(t),lambda:None,api=self.r.api,post=self.r.post,clock=lambda:NOW)
  self.assertEqual(len(self.r.posts),1);self.assertEqual(r['status'],'ALREADY_ATTEMPTED_CUSTODY_NOT_VERIFIED')
 def test_local_uncertain_intent_is_not_overwritten_or_posted(self):
  self.r.add()
  with self.assertRaises((ValueError,HTTPError)):self.call(lambda:None)
  before=list(self.root.rglob('*.json'))[0].read_bytes();r=self.call();self.assertEqual(r['status'],'LOCAL_INTENT_UNCONFIRMED_NOT_RETRIED');self.assertFalse(self.r.posts);self.assertEqual(before,list(self.root.rglob('*.json'))[0].read_bytes())
 def test_no_old_event_raw_reads_or_backfill(self):
  old,_=self.r.add(slot=NOW.replace(hour=8,minute=0));r=self.call()
  self.assertEqual(r['status'],'NO_ELIGIBLE_PRE_TARGET_EVENTS');self.assertFalse(self.r.posts);self.assertFalse(any(x.startswith('/contents/'+old+'?') for x in self.r.calls))
 def test_expired_history_does_not_block_new_eligible_track(self):
  self.r.add(slot=NOW.replace(hour=8,minute=0));self.r.add(track='r2');r=self.call();self.assertEqual(len(self.r.posts),1);self.assertEqual(len(r['requested_events']),1)
 def test_valid_recorded_receipt_is_noop_not_fresh_live_readback(self):
  path,e=self.r.add();self.r.files['audit/forecast_worm/original18/'+e['event_id']+'.json']=enc(self.receipt(path,e));r=self.call();self.assertFalse(self.r.posts);self.assertFalse(r['custody_verified']);self.assertEqual(r['status'],'NO_MISSING_CURRENT_RECEIPTS')
 def test_corrupt_recorded_receipt_does_not_post(self):
  path,e=self.r.add();r=self.receipt(path,e);r['sha256']='0'*64;self.r.files['audit/forecast_worm/original18/'+e['event_id']+'.json']=enc(r)
  with self.assertRaises(ValueError):self.call()
  self.assertFalse(self.r.posts)
 def test_all_active_states_defer_without_creating_intents(self):
  self.r.add()
  for state in ('pending','queued','requested','waiting','in_progress'):
   with self.subTest(state=state):
    self.r.active={'id':19,'path':'.github/workflows/oss_inventory_retention.yml','head_branch':'main','status':state}
    self.assertEqual(self.call()['status'],'DEFERRED_ACTIVE_CUSTODY');self.assertFalse(list(self.root.rglob('*.json')));self.assertFalse(self.r.posts)
 def test_incomplete_queue_response_fails_closed(self):
  self.r.add();self.r.bad_queue={'total_count':1,'workflow_runs':[]}
  with self.assertRaises(ValueError):self.call()
  self.assertFalse(self.r.posts)
 def test_remote_intent_tamper_prevents_post(self):
  self.r.add()
  def publish():
   self.r.publish(self.root)
   for k in list(self.r.files):
    if '/custody_dispatch_attempts/' in k:self.r.files[k]+=b' '
  with self.assertRaises(ValueError):self.call(publish)
  self.assertFalse(self.r.posts)
 def test_target_expiring_during_publication_is_not_dispatched(self):
  self.r.add()
  def publish():self.r.publish(self.root);self.r.clock=NOW+timedelta(hours=2)
  r=self.call(publish);self.assertEqual(r['status'],'SKIP_TARGET_EXPIRED_AFTER_INTENT');self.assertFalse(self.r.posts)
 def test_event_mutation_during_intent_publication_is_rejected(self):
  path,e=self.r.add()
  def publish():
   self.r.publish(self.root);e['horizon_hours']=24;self.r.files[path]=enc(e)
  with self.assertRaises(ValueError):self.call(publish)
  self.assertFalse(self.r.posts)
 def test_new_receipt_during_publication_avoids_duplicate_dispatch(self):
  path,e=self.r.add()
  def publish():self.r.publish(self.root);self.r.files['audit/forecast_worm/original18/'+e['event_id']+'.json']=enc(self.receipt(path,e))
  self.assertEqual(self.call(publish)['status'],'NO_MISSING_CURRENT_RECEIPTS_AFTER_INTENT');self.assertFalse(self.r.posts)
 def test_unknown_post_outcome_consumes_intent_without_auto_retry(self):
  self.r.add()
  def post(payload,timeout):self.r.posts.append(payload);raise TimeoutError('unknown accepted outcome')
  with self.assertRaises(TimeoutError):b.tick(self.root,lambda:self.r.publish(self.root),api=self.r.api,post=post,clock=lambda:NOW)
  self.call();self.assertEqual(len(self.r.posts),1)
 def test_permission_and_frozen_policy_contract_fail_closed(self):
  self.r.add()
  with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'pull_request'}):
   with self.assertRaises(ValueError):self.call()
  self.r.files['config/learned_shadow_v1.json']+=b' '
  with self.assertRaises(ValueError):self.call()
  self.assertFalse(self.r.posts)
 def test_canary_never_dispatched(self):
  path,e=self.r.add(track='r2');e['cohort']='canary';e['event_id']=e['event_id'].replace('formal','canary');self.r.files.pop(path);self.r.files[path.replace('formal','canary')]=enc(e)
  self.assertEqual(self.call()['status'],'NO_ELIGIBLE_PRE_TARGET_EVENTS');self.assertFalse(self.r.posts)
 def test_three_current_tracks_share_one_dispatch_after_all_intents_readback(self):
  self.r.add();self.r.add(hours=24);self.r.add(track='r2');r=self.call()
  self.assertEqual(len(self.r.posts),1);self.assertEqual(len(r['requested_events']),3)
  self.assertEqual(len([k for k in self.r.files if '/custody_dispatch_attempts/' in k]),3)
 def test_complete_nonrecursive_tree_handles_more_than_one_thousand_old_events(self):
  for n in range(1001):self.r.add(slot=NOW.replace(hour=0,minute=0)-timedelta(hours=2*n))
  self.r.add();r=self.call();self.assertEqual(len(self.r.posts),1)
  self.assertEqual(len([x for x in self.r.calls if '/contents/data/learned_shadow_v1/events/' in x]),2)
 def test_truncated_tree_and_transport_failure_prevent_post(self):
  self.r.add();original=self.r.api
  def api(path):
   result=original(path)
   if path.startswith('/git/trees/'):result={**result,'truncated':True}
   return result
  with self.assertRaises(ValueError):b.tick(self.root,lambda:self.r.publish(self.root),api=api,post=self.r.post,clock=lambda:NOW)
  self.assertFalse(self.r.posts)
  with self.assertRaises(OSError):b.tick(self.root,lambda:None,api=lambda path:(_ for _ in ()).throw(OSError('offline')),post=self.r.post,clock=lambda:NOW)
  self.assertFalse(self.r.posts)
 def test_second_active_check_defers_and_intent_is_not_retried_automatically(self):
  self.r.add()
  def publish():
   self.r.publish(self.root);self.r.active={'id':21,'path':'.github/workflows/oss_inventory_retention.yml','head_branch':'main','status':'in_progress'}
  self.assertEqual(self.call(publish)['status'],'DEFERRED_ACTIVE_CUSTODY_AFTER_INTENT');self.assertFalse(self.r.posts)
  self.r.active=None;self.assertEqual(self.call()['status'],'ALREADY_ATTEMPTED_CUSTODY_NOT_VERIFIED');self.assertFalse(self.r.posts)
 def test_existing_local_intent_binding_tamper_is_fail_closed(self):
  self.r.add()
  with self.assertRaises(HTTPError):self.call(lambda:None)
  path=next(self.root.rglob('*.json'));value=json.loads(path.read_bytes());value['event_id']='wrong';path.write_bytes(enc(value))
  with self.assertRaises(ValueError):self.call()
  self.assertFalse(self.r.posts)
 def test_prepare_and_after_publish_can_share_existing_foreground_publication(self):
  self.r.add();prepared=b.prepare(self.root,api=self.r.api,clock=lambda:NOW)
  self.assertTrue(prepared['candidates']);self.assertFalse(self.r.posts)
  self.r.publish(self.root)
  result=b.after_publish(self.root,prepared,api=self.r.api,post=self.r.post,clock=lambda:NOW)
  self.assertEqual(result['status'],'DISPATCH_ACCEPTED_CUSTODY_NOT_VERIFIED');self.assertEqual(len(self.r.posts),1)
 def test_full_original_job_and_startup_reserve_required(self):
  self.r.add();self.r.clock=NOW.replace(minute=59,second=1)
  self.assertEqual(self.call()['status'],'SKIP_INSUFFICIENT_CUSTODY_BUDGET');self.assertFalse(list(self.root.rglob('*.json')));self.assertFalse(self.r.posts)
 def test_budget_rechecked_after_caller_publication(self):
  self.r.add()
  def publish():self.r.publish(self.root);self.r.clock=NOW.replace(minute=59,second=1)
  self.assertEqual(self.call(publish)['status'],'SKIP_INSUFFICIENT_CUSTODY_BUDGET_AFTER_INTENT');self.assertFalse(self.r.posts)
 def test_transport_budget_and_invalid_status_prevent_requests_or_false_acceptance(self):
  client=b._Client();client.spent=40
  with patch.object(b,'urlopen') as request:
   with self.assertRaises(TimeoutError):client.api('/git/ref/heads/main')
  request.assert_not_called()
  import io
  class Response(io.BytesIO):
   status=200
   def __enter__(self):return self
   def __exit__(self,*args):self.close()
  client=b._Client()
  with patch.dict(os.environ,{'GITHUB_TOKEN':'unit-token'}),patch.object(b,'urlopen',return_value=Response(b'{"ok":true}')) as request:
   self.assertEqual(client.api('/git/ref/heads/main'),{'ok':True});self.assertLessEqual(request.call_args.kwargs['timeout'],6)
  response=Response(b'');response.status=201
  with patch.dict(os.environ,{'GITHUB_TOKEN':'unit-token'}),patch.object(b,'urlopen',return_value=response):
   with self.assertRaises(ValueError):client.post({'ref':'main'},6)
 def test_policy_mutation_during_caller_publication_prevents_post(self):
  self.r.add()
  def publish():self.r.publish(self.root);self.r.files['config/phase2_forward_v2.json']+=b' '
  with self.assertRaises(ValueError):self.call(publish)
  self.assertFalse(self.r.posts)
 def test_tree_body_hash_mismatch_prevents_cache_or_post(self):
  self.r.add();original=self.r.api
  def api(path):
   result=original(path)
   if path.startswith('/git/trees/') and result['tree']:
    result={**result,'tree':[{**e,'sha':'0'*40} for e in result['tree']]}
   return result
  with self.assertRaises(ValueError):b.tick(self.root,lambda:None,api=api,post=self.r.post,clock=lambda:NOW)
  self.assertFalse(self.r.posts)
 def test_unchanged_config_and_event_trees_reuse_only_cryptographic_caches(self):
  self.call();self.r.calls=[];self.r.files['data/runtime_deadline_v1/latest.json']=enc({'run':2});self.r.head='3'*40
  self.call();self.assertFalse(any(x.startswith('/contents/config/') for x in self.r.calls));self.assertFalse(self.r.posts)
 def test_eof_crossing_transport_budget_is_rejected(self):
  clock=[0.0]
  class Response:
   status=200
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read1(self,n):
    if clock[0]==0.0:clock[0]=0.9;return b'{"ok":true}'
    clock[0]=1.1;return b''
  client=b.GitHubClient();client.spent=39
  with patch.dict(os.environ,{'GITHUB_TOKEN':'unit-token'}),patch.object(b.time,'monotonic',side_effect=lambda:clock[0]),patch.object(b,'urlopen',return_value=Response()):
   with self.assertRaises(TimeoutError):client.api('/git/ref/heads/main')
 def test_late_http204_and_late_response_close_are_not_accepted(self):
  for phase in ('enter','close'):
   clock=[0.0]
   class Response:
    status=204
    def __enter__(self):
     if phase=='enter':clock[0]=1.1
     return self
    def __exit__(self,*args):clock[0]=1.1
   client=b.GitHubClient();client.spent=39
   with self.subTest(phase=phase),patch.dict(os.environ,{'GITHUB_TOKEN':'unit-token'}),patch.object(b.time,'monotonic',side_effect=lambda:clock[0]),patch.object(b,'urlopen',return_value=Response()):
    with self.assertRaises(TimeoutError):client.post({'ref':'main'},6)
 def test_old_contents_policy_cannot_be_cached_under_new_verified_config_tree(self):
  self.r.add();original=self.r.api;old=self.r.files['config/phase2_forward_v2.json']
  def publish():self.r.publish(self.root);self.r.files['config/phase2_forward_v2.json']+=b' '
  def api(path):
   result=original(path)
   if self.r.head=='2'*40 and path.startswith('/contents/config/phase2_forward_v2.json?'):
    return {**self.r.meta('config/phase2_forward_v2.json',old),'encoding':'base64','content':base64.b64encode(old).decode()}
   return result
  with self.assertRaises(ValueError):b.tick(self.root,publish,api=api,post=self.r.post,clock=lambda:NOW)
  self.assertFalse(self.r.posts)
 def test_prewriter_http503_and_timeout_are_explicit_readonly_observation_failures(self):
  self.r.add();original=self.r.api
  for error in (HTTPError('https://api.github.com/',503,'unavailable',{},None),TimeoutError('unit timeout'),ConnectionError('unit connection'),URLError('unit URL unavailable')):
   def api(path):
    if path.startswith('/contents/config/'):raise error
    return original(path)
   with self.subTest(error=type(error).__name__):
    try:b.prepare(self.root,api=api,clock=lambda:NOW)
    except Exception as caught:
     self.assertEqual(type(caught).__name__,'ObservationUnavailable');self.assertIs(caught.__cause__,error)
     self.assertNotIn('unit timeout',str(caught));self.assertFalse(list(self.root.rglob('*.json')));self.assertFalse(self.r.posts)
    else:self.fail('observation error was silently suppressed')
 def test_intent_write_error_is_not_reclassified_as_readonly(self):
  self.r.add()
  for error in (PermissionError('unit write denied'),TimeoutError('unit filesystem write timeout')):
   with self.subTest(error=type(error).__name__),patch.object(Path,'open',side_effect=error):
    try:b.prepare(self.root,api=self.r.api,clock=lambda:NOW)
    except Exception as caught:self.assertIs(caught,error)
    else:self.fail('intent write error suppressed')
  self.assertFalse(self.r.posts)
 def test_oversize_local_intent_is_bounded_integrity_failure(self):
  self.r.add();b.prepare(self.root,api=self.r.api,clock=lambda:NOW)
  local=next(self.root.rglob('*.json'));raw=b'x'*(65536+2000);local.write_bytes(raw)
  opened=Path.open;reads=[]
  class BoundedReader:
   def __init__(self,stream):self.stream=stream
   def __enter__(self):return self
   def __exit__(self,*args):self.stream.close()
   def read(self,size=-1):
    reads.append(size)
    if not 0<=size<=65537:raise AssertionError('unbounded local intent read')
    return self.stream.read(size)
  def checked_open(path,*args,**kwargs):
   stream=opened(path,*args,**kwargs)
   return BoundedReader(stream) if path==local and (args==('rb',) or kwargs.get('mode')=='rb') else stream
  with patch.object(Path,'open',new=checked_open):
   with self.assertRaisesRegex(ValueError,'local intent size'):b.prepare(self.root,api=self.r.api,clock=lambda:NOW)
  self.assertEqual(reads,[65537]);self.assertEqual(local.read_bytes(),raw);self.assertFalse(self.r.posts)
if __name__=='__main__':unittest.main()

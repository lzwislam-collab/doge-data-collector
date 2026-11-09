import re
import csv, copy, importlib.util, json, os, shutil, subprocess, tempfile, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
MODULE=Path(__file__).with_name('clock_recovery_request.py')
if MODULE.exists():
 spec=importlib.util.spec_from_file_location('receiver',MODULE);receiver=importlib.util.module_from_spec(spec);spec.loader.exec_module(receiver)
else: receiver=None
GIT=shutil.which('git') or r'C:/Users/lzwis/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd/git.exe'
TASK='doge';THREAD='01a17272-26ad-7803-92f6-dd5796248d34'
TEST_COLUMNS={"daily":["forecast_id","registered_utc","anchor_date_utc","anchor_close","target_date_utc","horizon_days","model_id","training_n","p10","p50","p90","historical_up_frequency","baseline_p10","baseline_p50","baseline_p90","baseline_up_frequency","input_digest"],"hourly":["forecast_id","model_id","issued_at_utc","source_quote_utc","source_hour_start_utc","reference_price","horizon_hours","target_utc","majority_p_up","majority_direction","doge_reversal_direction","btc_momentum_direction","feature_sha256"]}
TEST_SCORE_COLUMNS=["forecast_id","status","observed_at_utc","target_quote_utc","target_price","realized_up","majority_correct","doge_reversal_correct","btc_momentum_correct","reason"]
def dt(s):return datetime.fromisoformat(s.replace('Z','+00:00'))
def stamp(t):return t.isoformat().replace('+00:00','Z')
class ReceiverTests(unittest.TestCase):
 def setUp(self):
  self.assertIsNotNone(receiver,'Independent clock intent has no receiving guard')
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.git('init','-q');self.git('config','user.name','fixture');self.git('config','user.email','fixture@example.invalid')
 def git(self,*args,at=None):
  e=os.environ.copy()
  if at:e.update(GIT_AUTHOR_DATE=stamp(at),GIT_COMMITTER_DATE=stamp(at))
  return subprocess.run([GIT,*args],cwd=self.root,env=e,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=15).stdout.strip()
 def write(self,path,text):
  f=self.root/path;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(text,encoding='utf-8')
 def commit(self,msg,at):
  self.git('add','-A');self.git('commit','-q','-m',msg,at=at);return self.git('rev-parse','HEAD')
 def make(self,kind='daily',complete=False):
  self.kind=kind;self.now=dt('2026-11-10T00:57:00Z' if kind=='daily' else '2026-11-10T12:10:00Z')
  self.period=self.now.replace(hour=0,minute=0,second=0,microsecond=0) if kind=='daily' else self.now.replace(minute=0,second=0,microsecond=0)
  self.key=self.period.strftime('%Y-%m-%d' if kind=='daily' else '%Y-%m-%dT%H')
  self.path='data/p0_operations/clock_requests/'+kind+'/'+self.key+'.json'
  if kind=='daily':
   anchor=(self.period-timedelta(days=1)).date().isoformat();ids=['doge_empirical_730d_v4_1_'+anchor+'_'+str(h)+'d' for h in (1,7,30)]
   reg='data/forecast_v41/registry.csv'
  else:
   ts=int((self.period-timedelta(hours=1)).timestamp()*1000);ids=['DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_'+str(ts)+'_'+str(h)+'h' for h in (1,24)]
   reg='data/direction_v42/forward_registry.csv'
  self.expected=ids;self.registry=reg
  self.write(reg,','.join(TEST_COLUMNS[kind])+'\n'+(''.join(x+','*(len(TEST_COLUMNS[kind])-1)+'\n' for x in ids) if complete else ''))
  self.before=self.commit('fixture baseline',self.now-timedelta(minutes=2))
  self.request={'schema_version':1,'kind':kind,'source_task_id':TASK,'source_thread_id':THREAD,'observed_task_run_id':'01a17800-1111-2222-3333-123456789abc','requested_at_utc':stamp(self.now-timedelta(seconds=10)),'expires_at_utc':stamp(self.now+timedelta(minutes=10)),'missing_ids':ids}
  if kind=='daily':self.request.update(day_utc=self.key,anchor_date_utc=(self.period-timedelta(days=1)).date().isoformat())
  else:self.request.update(slot_start_utc=stamp(self.period),reason='ISSUANCE')
  return self.request
 def publish(self,extra=None):
  self.write(self.path,json.dumps(self.request))
  if extra:self.write(extra,'unexpected simultaneous change')
  self.after=self.commit('data: clock recovery request '+self.kind+' '+self.key,self.now-timedelta(seconds=5))
  return {'repository':{'full_name':'lzwislam-collab/doge-data-collector'},'ref':'refs/heads/main','before':self.before,'after':self.after,'forced':False,'deleted':False,'sender':{'login':'lzwislam-collab'},'head_commit':{'id':self.after,'message':'data: clock recovery request '+self.kind+' '+self.key}}
 def admit(self,event,kind=None,now=None):return receiver.admit(self.root,kind or self.kind,'push',event,now or self.now,event_sha=event['after'])
 def rejected(self,event,now=None):
  with self.assertRaises(receiver.Rejected):self.admit(event,now=now)
 def test_fresh_daily_request_runs_existing_writer(self):
  self.make();result=self.admit(self.publish());self.assertTrue(result['run']);self.assertEqual(result['expected_ids'],self.expected)
 def test_fresh_hourly_request_uses_prior_bar_open(self):
  self.make('hourly');result=self.admit(self.publish());self.assertTrue(result['run']);self.assertEqual(result['expected_ids'],self.expected)
 def test_complete_registry_is_noop(self):
  for kind in ('daily','hourly'):
   with self.subTest(kind=kind):
    self.make(kind,True);result=self.admit(self.publish());self.assertFalse(result['run']);self.assertEqual(result['decision'],'ALREADY_REGISTERED')
 def test_wrong_task_thread_schema_and_forecast_payload_rejected(self):
  self.make()
  for key,val in [('source_task_id','other'),('source_thread_id','other'),('schema_version',2),('forecast_probability',0.9)]:
   with self.subTest(key=key):
    self.request[key]=val;self.rejected(self.publish());self.request.pop(key)
    self.request.update(source_task_id=TASK,source_thread_id=THREAD,schema_version=1)
 def test_wrong_day_anchor_and_ids_rejected(self):
  self.make()
  for key,val in [('day_utc','2026-11-09'),('anchor_date_utc','2026-11-08'),('missing_ids',['forged_id']),('missing_ids',[self.expected[0],self.expected[0]])]:
   with self.subTest(key=key):
    old=self.request[key];self.request[key]=val;self.rejected(self.publish());self.request[key]=old
 def test_future_naive_and_overlong_ttl_rejected(self):
  self.make()
  for key,val in [('requested_at_utc',stamp(self.now+timedelta(seconds=1))),('requested_at_utc','2026-11-10T00:56:50'),('expires_at_utc',stamp(self.now+timedelta(hours=1)))]:
   with self.subTest(key=key):
    old=self.request[key];self.request[key]=val;self.rejected(self.publish());self.request[key]=old
 def test_expired_request_rejected_before_writer(self):
  self.make();event=self.publish();self.rejected(event,now=self.now+timedelta(minutes=10))
 def test_daily_warm_window_and_20minute_reserve(self):
  self.make();self.rejected(self.publish(),now=self.period+timedelta(hours=1,minutes=40))
 def test_hourly_10minute_reserve(self):
  self.make('hourly');self.rejected(self.publish(),now=self.period+timedelta(minutes=35))
 def test_multiple_changed_files_rejected(self):
  self.make();self.rejected(self.publish(extra='README-unexpected.md'))
 def test_modified_intent_is_not_new(self):
  self.make();event=self.publish();self.before=event['after'];self.request['observed_task_run_id']='different';self.rejected(self.publish())
 def test_deleted_then_recreated_intent_is_replay(self):
  self.make();self.publish();(self.root/self.path).unlink();self.before=self.commit('fixture deletion',self.now-timedelta(seconds=4));self.rejected(self.publish())
 def test_wrong_repo_branch_forced_push_or_sha_rejected(self):
  self.make();event=self.publish()
  variants=[{'repository':{'full_name':'attacker/repo'}},{'ref':'refs/heads/other'},{'forced':True},{'head_commit':{'id':'a'*40,'message':event['head_commit']['message']}}]
  for update in variants:
   with self.subTest(update=update):bad=copy.deepcopy(event);bad.update(update);self.rejected(bad)
 def test_duplicate_registry_is_failure(self):
  self.make();self.write(self.registry,','.join(TEST_COLUMNS[self.kind])+'\n'+('old'+','*(len(TEST_COLUMNS[self.kind])-1)+'\n')*2);self.before=self.commit('fixture corrupt registry',self.now-timedelta(seconds=20));self.rejected(self.publish())
 def test_normal_code_push_schedule_dispatch_preserved(self):
  self.make();self.write('ordinary.py','print(1)');after=self.commit('ordinary change',self.now)
  event={'before':self.before,'after':after}
  self.assertTrue(receiver.admit(self.root,'daily','push',event,self.now,event_sha=after)['run'])
  for name in ('schedule','workflow_dispatch'):
   self.assertTrue(receiver.admit(self.root,'daily',name,{},self.now,event_sha=after)['run'])
 def test_clock_intent_wrong_receiver_kind_is_rejected(self):
  self.make();event=self.publish()
  with self.assertRaises(receiver.Rejected):self.admit(event,kind='hourly')
 def test_cli_has_no_production_clock_override(self):
  result=subprocess.run([os.sys.executable,str(MODULE),'--kind','daily','--now','2026-11-10T00:57:00Z'],capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,2)

 def test_actual_commit_after_producer_window_rejected(self):
  self.make();self.now=self.period+timedelta(hours=1,minutes=31)
  self.request['requested_at_utc']=stamp(self.period+timedelta(hours=1,minutes=29))
  self.request['expires_at_utc']=stamp(self.period+timedelta(hours=1,minutes=39))
  with self.assertRaisesRegex(receiver.Rejected,'CLOCK_WINDOW_CLOSED'):self.admit(self.publish())
 def test_duplicate_json_member_rejected(self):
  self.make();event=self.publish();raw=json.dumps(self.request).replace('"kind": "daily"','"kind": "daily", "kind": "daily"')
  self.write(self.path,raw);after=self.commit(event['head_commit']['message'],self.now-timedelta(seconds=4));event['after']=after;event['head_commit']['id']=after
  self.rejected(event)
 def test_actions_token_is_not_an_independent_clock(self):
  self.make();event=self.publish();event['sender']['login']='github-actions[bot]'
  with self.assertRaisesRegex(receiver.Rejected,'ACTIONS_TOKEN_NOT_INDEPENDENT'):self.admit(event)

 def test_unknown_event_is_rejected(self):
  self.make()
  with self.assertRaisesRegex(receiver.Rejected,'EVENT_KIND'):receiver.admit(self.root,'daily','unknown',{},self.now)

 def test_missing_frozen_registry_columns_cannot_claim_noop(self):
  self.make(complete=True);self.write(self.registry,'forecast_id\n'+''.join(x+'\n' for x in self.expected));self.before=self.commit('fixture missing columns',self.now-timedelta(seconds=20))
  with self.assertRaisesRegex(receiver.Rejected,'REGISTRY_SCHEMA'):self.admit(self.publish())

 def test_missing_malformed_or_unapproved_sender_rejected(self):
  self.make();event=self.publish()
  for sender in (None,{}, {'login':''}, {'login':None}, {'login':'unexpected-app[bot]'}):
   with self.subTest(sender=sender):
    bad=copy.deepcopy(event)
    if sender is None:bad.pop('sender')
    else:bad['sender']=sender
    with self.assertRaises(receiver.Rejected):self.admit(bad)
 def cli_slow_read(self,late):
  from unittest.mock import patch
  event=self.publish();ep=self.root/'event.json';ep.write_text(json.dumps(event),encoding='utf-8');op=self.root/'output'
  readings=iter((self.now,late))
  class SlowDatetime(datetime):
   @classmethod
   def now(cls,tz=None):return next(readings)
  env={'GITHUB_EVENT_NAME':'push','GITHUB_EVENT_PATH':str(ep),'GITHUB_SHA':event['after'],'GITHUB_OUTPUT':str(op)}
  with patch.dict(os.environ,env),patch.object(receiver,'datetime',SlowDatetime),patch.object(receiver,'__file__',str(self.root/'clock_recovery_request.py')):
   code=receiver.main(['--kind',self.kind])
  self.assertEqual(code,1);self.assertIn('run=false',op.read_text())
 def test_cli_rechecks_real_clock_after_slow_git_crosses_expiry(self):
  self.make();self.cli_slow_read(dt(self.request['expires_at_utc']))
 def test_cli_rechecks_real_clock_after_slow_git_crosses_receiver_window(self):
  self.make();self.now=self.period+timedelta(hours=1,minutes=30)
  self.request['requested_at_utc']=stamp(self.now-timedelta(seconds=20));self.request['expires_at_utc']=stamp(self.now+timedelta(minutes=15))
  self.cli_slow_read(self.period+timedelta(hours=1,minutes=40))

 def continuity(self,minutes=-6,hours=1,scored=False):
  self.make('hourly',complete=True);target=self.now+timedelta(minutes=minutes);issued=target-timedelta(hours=hours);source=issued.replace(minute=0,second=0,microsecond=0);bar=source-timedelta(hours=1)
  fid='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_'+str(int(bar.timestamp()*1000))+'_'+str(hours)+'h'
  rows=list(csv.DictReader((self.root/self.registry).read_text().splitlines()));rows=[r for r in rows if r['forecast_id']!=fid]
  row={k:'' for k in TEST_COLUMNS['hourly']};row.update(forecast_id=fid,model_id='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1',issued_at_utc=stamp(issued),source_quote_utc=stamp(issued-timedelta(seconds=1)),source_hour_start_utc=stamp(source),reference_price='0.1',horizon_hours=str(hours),target_utc=stamp(target));rows.append(row)
  text=','.join(TEST_COLUMNS['hourly'])+'\n'+''.join(','.join(r[k] for k in TEST_COLUMNS['hourly'])+'\n' for r in rows);self.write(self.registry,text)
  score='data/direction_v42/forward_scores.csv';self.write(score,','.join(TEST_SCORE_COLUMNS)+'\n'+((fid+',scored'+','*(len(TEST_SCORE_COLUMNS)-2)+'\n') if scored else ''))
  self.before=self.commit('fixture existing unscored target',self.now-timedelta(seconds=20));self.request.update(reason='TARGET_CONTINUITY',missing_ids=[fid]);return fid,target
 def test_continuity_admits_existing_open_target_when_current_issuance_complete(self):
  self.continuity()
  try:r=self.admit(self.publish())
  except receiver.Rejected as e:self.fail('Existing unscored target has no continuity admission: '+str(e))
  self.assertTrue(r['run']);self.assertEqual(r['reason'],'TARGET_CONTINUITY')
 def test_continuity_admits_real_future_target_within_sixty_minutes(self):
  self.continuity(minutes=55);self.assertTrue(self.admit(self.publish())['run'])
 def test_continuity_recorded_score_is_noop(self):
  self.continuity(scored=True);self.assertFalse(self.admit(self.publish())['run'])
 def test_continuity_lost_safe_target_window_is_noop(self):
  self.continuity(minutes=-26);self.assertFalse(self.admit(self.publish())['run'])
 def test_continuity_far_24hour_or_unregistered_id_is_rejected(self):
  self.continuity(minutes=23*60,hours=24);self.rejected(self.publish())
  self.request['missing_ids']=['DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1_1h'];self.rejected(self.publish())
 def test_continuity_score_schema_must_be_exact_frozen_columns(self):
  fid,target=self.continuity();self.write('data/direction_v42/forward_scores.csv','forecast_id\n'+fid+'\n');self.before=self.commit('fixture corrupt scores',self.now-timedelta(seconds=19));self.rejected(self.publish())
 def test_continuity_final_clock_recheck_keeps_ten_minute_target_reserve(self):
  fid,target=self.continuity();self.cli_slow_read(target+timedelta(minutes=15))

 def actual_16z_request(self,offset=9,commit_at=None,update=None):
  # Exact natural request cd9c2c381a9d03df7fd832529557189cd1823973;
  # registry row is copied from that pinned commit, never generated in production.
  self.kind='hourly';self.now=dt('2026-11-09T16:24:19.380Z');self.period=dt('2026-11-09T16:00:00Z');self.key='2026-11-09T16'
  self.path='data/p0_operations/clock_requests/hourly/'+self.key+'.json';self.registry='data/direction_v42/forward_registry.csv'
  raw='{"schema_version":1,"kind":"hourly","source_task_id":"doge","source_thread_id":"01a17272-26ad-7803-92f6-dd5796248d34","observed_task_run_id":"01a1c11b-1949-7c53-aeec-8b846ab11043","requested_at_utc":"2026-11-09T16:21:27.954Z","expires_at_utc":"2026-11-09T16:35:00Z","missing_ids":["DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1794236400000_1h"],"slot_start_utc":"2026-11-09T16:00:00Z","reason":"TARGET_CONTINUITY"}\n'
  self.request=json.loads(raw)
  row='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_1794236400000_1h,DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1,2026-11-09T16:05:56.844770+00:00,2026-11-09T16:05:56.570000+00:00,2026-11-09T16:00:00+00:00,0.07056,1,2026-11-09T17:05:56.844770+00:00,0.5031672220,UP,DOWN,UP,c6ba779b91b79b83fae3faa7a361003d8c17c43bd60c4783129d8293e458da71\n'
  self.write(self.registry,','.join(TEST_COLUMNS['hourly'])+'\n'+row)
  self.write('data/direction_v42/forward_scores.csv',','.join(TEST_SCORE_COLUMNS)+'\n')
  self.before=self.commit('fixture pinned 16Z registry',dt('2026-11-09T16:19:19Z'))
  if update:self.request.update(update);raw=json.dumps(self.request,separators=(',',':'))+'\n'
  request_file=self.root/self.path;request_file.parent.mkdir(parents=True,exist_ok=True);request_file.write_bytes(raw.encode('utf-8'))
  instant=commit_at or dt('2026-11-09T16:21:36Z')
  self.after=self.commit('data: clock recovery request hourly '+self.key,instant.astimezone(timezone(timedelta(hours=offset))))
  return {'repository':{'full_name':'lzwislam-collab/doge-data-collector'},'ref':'refs/heads/main','before':self.before,'after':self.after,'forced':False,'deleted':False,'sender':{'login':'lzwislam-collab'},'head_commit':{'id':self.after,'message':'data: clock recovery request hourly '+self.key}}
 def test_actual_412byte_z_request_with_raw_plus09_committer_is_admitted(self):
  event=self.actual_16z_request();raw=self.git('cat-file','-p',self.after)
  self.assertIn('1794241296 +0900',raw);self.assertEqual(self.git('show','-s','--format=%cI',self.after),'2026-11-10T01:21:36+09:00')
  self.assertEqual(len((self.root/self.path).read_bytes()),412)
  result=self.admit(event);self.assertTrue(result['run']);self.assertEqual(result['pending_target_ids'],self.request['missing_ids'])
  self.assertEqual(result['admission_deadline_utc'],'2026-11-09T16:35:00+00:00')
 def test_plus08_committer_same_instant_is_admitted(self):
  event=self.actual_16z_request(offset=8);self.assertTrue(self.git('show','-s','--format=%cI',self.after).endswith('+08:00'));self.assertTrue(self.admit(event)['run'])
 def test_minus08_committer_same_instant_is_admitted(self):
  event=self.actual_16z_request(offset=-8);self.assertTrue(self.git('show','-s','--format=%cI',self.after).endswith('-08:00'));self.assertTrue(self.admit(event)['run'])
 def test_offset_committer_future_instant_is_still_rejected(self):
  event=self.actual_16z_request(commit_at=dt('2026-11-09T16:24:20Z'))
  with self.assertRaisesRegex(receiver.Rejected,'CLOCK_INTENT_EXPIRED_OR_FUTURE'):self.admit(event)
 def test_offset_committer_before_request_is_still_rejected(self):
  event=self.actual_16z_request(commit_at=dt('2026-11-09T16:21:27Z'))
  with self.assertRaisesRegex(receiver.Rejected,'CLOCK_INTENT_EXPIRED_OR_FUTURE'):self.admit(event)
 def test_offset_committer_before_period_is_still_rejected(self):
  event=self.actual_16z_request(commit_at=dt('2026-11-09T15:59:59Z'))
  with self.assertRaisesRegex(receiver.Rejected,'CLOCK_WINDOW_CLOSED'):self.admit(event)
 def test_offset_committer_at_producer_end_is_still_rejected(self):
  event=self.actual_16z_request(commit_at=dt('2026-11-09T16:35:00Z'))
  with self.assertRaisesRegex(receiver.Rejected,'CLOCK_WINDOW_CLOSED'):self.admit(event,now=dt('2026-11-09T16:34:00Z'))
 def test_offset_committer_does_not_allow_expired_or_overlong_request(self):
  event=self.actual_16z_request(update={'expires_at_utc':'2026-11-09T16:24:19.380Z'})
  with self.assertRaisesRegex(receiver.Rejected,'CLOCK_INTENT_EXPIRED_OR_FUTURE'):self.admit(event)
 def test_offset_committer_does_not_allow_overlong_ttl(self):
  event=self.actual_16z_request(update={'expires_at_utc':'2026-11-09T16:42:00Z'})
  with self.assertRaisesRegex(receiver.Rejected,'CLOCK_INTENT_EXPIRED_OR_FUTURE'):self.admit(event)
 def test_offset_committer_does_not_allow_non_utc_request(self):
  event=self.actual_16z_request(update={'requested_at_utc':'2026-11-10T01:21:27.954+09:00'})
  with self.assertRaisesRegex(receiver.Rejected,'CLOCK_UTC_FORMAT'):self.admit(event)
 def test_invalid_internal_commit_epoch_fails_closed(self):
  from unittest.mock import patch
  event=self.actual_16z_request();real_git=receiver.git
  for invalid in ('not-an-epoch','999999999999999999999999999999999999999999'):
   with self.subTest(epoch=invalid):
    def fake_git(root,*args):return invalid if args==('show','-s','--format=%ct',self.after) else real_git(root,*args)
    with patch.object(receiver,'git',fake_git),self.assertRaisesRegex(receiver.Rejected,'CLOCK_COMMIT_TIME'):self.admit(event)

class ExternalUtcFormatTests(unittest.TestCase):
 def test_utc_z_and_zero_offset_equivalent_with_valid_fractions(self):
  for fraction in ('','.1','.954','.123456'):
   with self.subTest(fraction=fraction):self.assertEqual(receiver.utc('2026-11-09T16:21:27'+fraction+'Z'),receiver.utc('2026-11-09T16:21:27'+fraction+'+00:00'))
 def test_non_utc_naive_bad_fraction_and_suffix_still_rejected(self):
  for value in ('2026-11-10T01:21:27.954+09:00','2026-11-09T08:21:27-08:00','2026-11-09T16:21:27','2026-11-09T16:21:27.Z','2026-11-09T16:21:27.1234567Z','2026-11-09T16:21:27,954Z','2026-11-09T16:21:27Z+00:00','2026-11-09T16:21:27+00:00Z','2026-11-09T16:21:27z','2026-11-09T16:21:27Zjunk','2026-02-30T16:21:27Z'):
   with self.subTest(value=value),self.assertRaisesRegex(receiver.Rejected,'CLOCK_UTC_FORMAT'):receiver.utc(value)

class WorkflowAdmissionTests(unittest.TestCase):
 def blocks(self,text):
  lines=text.splitlines();i=next(i for i,l in enumerate(lines) if l.strip()=='steps:');blocks=[]
  for l in lines[i+1:]:
   if l.startswith('      - '):blocks.append([l])
   elif blocks:blocks[-1].append(l)
  return blocks
 def test_every_step_after_admission_requires_true_gate(self):
  for filename in ('doge_history.yml','okx_hourly_extreme_history.yml'):
   with self.subTest(filename=filename):
    text=(Path(__file__).parent/'.github/workflows'/filename).read_text(encoding='utf-8');past=False;count=0
    for b in self.blocks(text):
     if any('id: clock_request' in l for l in b):past=True;continue
     if not past:continue
     expr=next((l.strip()[4:] for l in b if l.startswith('        if: ')),None)
     self.assertIsNotNone(expr,'unguarded step: '+b[0]);expr=expr.strip()
     if expr.startswith(chr(36)+'{{'):expr=expr[3:-2].strip()
     gate="steps.clock_request.outputs.run == 'true'"
     self.assertTrue(expr==gate or expr.endswith(' && '+gate),'gate must dominate entire expression: '+b[0])
     prefix=expr[:-len(' && '+gate)] if expr!=gate else ''
     depth=0
     for z,c in enumerate(prefix):
      depth+=1 if c=='(' else -1 if c==')' else 0
      self.assertFalse(depth==0 and prefix[z:z+2]=='||','unguarded alternative: '+b[0])
     count+=1
    self.assertTrue(past);self.assertGreater(count,5)

 def test_prospective_bridge_is_enabled_only_on_foreground_step(self):
  text=(Path(__file__).parent/'.github/workflows/okx_hourly_extreme_history.yml').read_text(encoding='utf-8')
  flag="DOGE_PROSPECTIVE_RUNTIME_BRIDGE:"
  self.assertEqual(text.count(flag),1)
  flagged=[b for b in self.blocks(text) if any(flag in line for line in b)]
  self.assertEqual(len(flagged),1);self.assertTrue(any('id: deadline_service' in line for line in flagged[0]))
  self.assertTrue(any('        env:'==line for line in flagged[0]))
  switch=next(line for line in flagged[0] if flag in line)
  self.assertIn("steps.prospective_deps.outcome == 'success'",switch)
  self.assertIn("'1' || '0'",switch)


 def test_numpy_install_is_after_fast_publish_and_failure_visible_after_artifacts(self):
  text=(Path(__file__).parent/'.github/workflows/okx_hourly_extreme_history.yml').read_text(encoding='utf-8');blocks=self.blocks(text)
  idx=lambda marker:next((i for i,b in enumerate(blocks) if any(marker in line for line in b)),-1)
  early=idx('id: early_publish');install=idx('id: prospective_deps');capture=idx('id: deadline_service')
  self.assertGreater(install,early);self.assertLess(install,capture)
  body='\n'.join(blocks[install]);self.assertIn('python -m pip install --disable-pip-version-check numpy==2.3.5',body);self.assertIn('python -B runtime_history_graph.py',body);self.assertLess(body.index('python -m pip install'),body.index('python -B runtime_history_graph.py'));self.assertIn('timeout-minutes: 2',body);self.assertIn('continue-on-error: true',body);self.assertIn("steps.early_publish.outcome == 'success'",body)
  failure=idx('name: Fail on prospective dependency installation after preserving core evidence')
  self.assertGreater(failure,max(i for i,b in enumerate(blocks) if any('uses: actions/upload-artifact@' in line for line in b)))
  tail='\n'.join(blocks[failure]);self.assertIn("steps.prospective_deps.outcome != 'success'",tail);self.assertIn('always()',tail);self.assertIn('run: exit 1',tail)

 def test_workflow_plain_scalars_do_not_hide_mapping_colons(self):
  for filename in ('doge_history.yml','okx_hourly_extreme_history.yml','runtime_deadline_tests.yml'):
   text=(Path(__file__).parent/'.github/workflows'/filename).read_text(encoding='utf-8')
   for number,line in enumerate(text.splitlines(),1):
    match=re.match(r'^\s*(?:- )?[A-Za-z_][A-Za-z0-9_-]*: (.*)$',line)
    if match:
     value=match.group(1)
     if value and value[0] not in (chr(34),chr(39),'|','>'):
      self.assertNotIn(': ',value,filename+':'+str(number)+' ambiguous unquoted YAML scalar')

if __name__=='__main__':unittest.main()

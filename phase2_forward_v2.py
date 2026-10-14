"""Separate frozen 120-day prospective procedure. Pure inference; no orders."""
import csv,io,json,math
from datetime import datetime,timedelta,timezone
from pathlib import Path
import numpy as np
import phase1_research as prior
import phase1_research_audit as audit
import phase2_models_v2 as models
import learned_shadow_v1 as old

ROOT=Path(__file__).resolve().parent;H=3600000
PARENT='research/phase2_recent_v1/model.json'
MODEL='research/phase2_forward_v2/model.json'
POLICY='config/phase2_forward_v2.json'
REGISTRATION='config/phase2_forward_registration_v2.json'
BASE='data/phase2_forward_v2'
WORKFLOW='.github/workflows/phase2_forward_audit_v2.yml'
encode=prior.encode;digest=old.digest;utc=old.utc

def policy():
 return {'schema':'DOGE_PHASE2_FROZEN_FORWARD_V2','protocol_id':'DOGE_V2_FROZEN120_20261020_R2',
  'effective_from_utc':'2026-10-20T00:00:00Z','observation_end_utc':'2027-01-18T00:00:00Z',
  'model_expires_utc':'2027-02-11T08:00:00Z','fixed_review_utc':['2027-01-18T00:25:00Z','2027-02-11T08:00:00Z'],
  'minimum_calendar_days':90,'minimum_nonoverlap_events':100,'family_size_upper_bound':10000,
  'familywise_alpha':.05,'block_days':3,'feature_decimal_places':9,
  'accuracy_margin':.01,'brier_margin':.002,'regime_min_events':20,'regime_max_brier_harm':.01,
  'max_training_data_age_days':120,'horizon_seconds':3600,'slot_seconds':7200,
  'target_ticker_need_not_follow_http_request':True,'max_quote_age_seconds':120,
  'issue_window_seconds':2700,'target_window_seconds':1500,'canary_horizon_seconds':600,
  'canary_max_events':1,'canary_included_in_formal_statistics':False,
  'fee_per_side':.001,'slippage_per_side':.0005,'stress_multiplier':2,'notional_usdt':100,
  'shadow_issuance_authorized':True,'automatic_retraining':False,'automatic_promotion':False,'real_orders_authorized':False,
  'promotion_requires':['complete independently replayed raw prospective cohort including missing events',
   'dependency-robust fixed-look family-adjusted lower bound >=0.70',
   'same-event frozen model/constant/majority direction and probability excess',
   'cross-regime coverage and no material harm','same-event raw depth nominal and stress net benefit',
   'independent evidence review and explicit new production version with rollback']}

def check_policy(p):
 if encode(p)!=encode(policy()):raise ValueError('fixed forward procedure changed; new protocol required')

def make_model(parent):
 if parent['recent_identity']!='64b45852d44b906d4dc29f5128def882329ff093ebb6e231203b6660951113a0' or digest({k:v for k,v in parent.items() if k!='recent_identity'})!=parent['recent_identity']:raise ValueError('accepted parent identity required')
 if parent['model_sha256']!=digest(parent['model']) or parent['feature_decimal_places']!=9:raise ValueError('parent weights changed')
 value={k:v for k,v in parent.items() if k not in ('recent_identity','max_training_data_age_days','schema','status')}
 value.update(schema='DOGE_PHASE2_FROZEN120_MODEL_V2',parent_recent_identity=parent['recent_identity'],
  max_training_data_age_days=120,auto_issuance_authorized=True,status='SEPARATE_FROZEN_FORWARD_MODEL_NOT_PROMOTED')
 value['trial_model_identity']=digest(value)
 return value

def check_age(model,at):
 at=int(at.timestamp()*1000)
 if model['max_training_data_age_days']!=120 or not model['trained_at_ms']<at<=model['latest_label_end_ms']+120*24*H:
  raise ValueError('outside frozen model lifetime')

def features(frame,at):
 t=frame['times'];last=int(at.timestamp()*1000)//H*H-H
 if t!=[last-(72-i)*H for i in range(73)]:raise ValueError('latest exact 73 closed hours required')
 for s in ('DOGE-USDT','BTC-USDT'):
  r=frame[s]
  if any(len(r[k])!=73 for k in ('close','volume','seen')):raise ValueError('unaligned frame')
  for i in range(73):
   c,v,seen=r['close'][i],r['volume'][i],r['seen'][i]
   if isinstance(c,bool) or isinstance(v,bool) or not math.isfinite(c) or c<=0 or not math.isfinite(v) or v<0 or type(seen) is not int or not t[i]+H<=seen<=int(at.timestamp()*1000):raise ValueError('unknown source receipt')
 d=np.log(frame['DOGE-USDT']['close']);b=np.log(frame['BTC-USDT']['close']);v=np.asarray(frame['DOGE-USDT']['volume'])
 means=[float(v[-lag-1:-1].mean()) for lag in (6,24,72)]
 if min(means)<=0:raise ValueError('empty volume basis')
 x=[float(np.std(np.diff(d[-13:]))),float(np.std(np.diff(d[-25:]))),float(np.max(d[-25:])-np.min(d[-25:]))]
 x.extend(float(v[-1]/q-1) for q in means)
 x.extend(float(b[-1]-b[-1-k]) for k in (1,4,24,72))
 x.extend(float((d[-1]-d[-1-k])-(b[-1]-b[-1-k])) for k in (1,24))
 # Training library rounds to12 before this version's9-decimal quantization.
 return [round(round(q,12),9) for q in x]

def frame_from_raw(raw,at):
 series={s:{} for s in ('DOGE-USDT','BTC-USDT')};keys=set();last=int(at.timestamp()*1000)//H*H-H
 wanted=[last-(72-i)*H for i in range(73)]
 for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
  s=r['symbol'];stamp=int(r['open_utc_ms']);key=(s,stamp)
  if key in keys:raise ValueError('duplicate raw source bar')
  keys.add(key)
  if s not in series or stamp not in wanted:continue
  if r['source']!='OKX history-candles 1H confirmed':raise ValueError('source origin changed')
  series[s][stamp]=(float(r['close']),float(r['volume_base']),int(utc(r['first_ingested_utc']).timestamp()*1000))
 if any(set(s)!=set(wanted) for s in series.values()):raise ValueError('missing latest73 source bars')
 frame={'times':wanted,**{s:{'close':[series[s][t][0] for t in wanted],'volume':[series[s][t][1] for t in wanted],
   'seen':[series[s][t][2] for t in wanted]} for s in series}}
 features(frame,at);return frame

def benchmark_frame(frame):
 return {'times':frame['times'][-25:],**{s:{k:v[-25:] for k,v in frame[s].items()} for s in ('DOGE-USDT','BTC-USDT')}}

def issue(p,model,frame,packet,at,frozen_p,cohort):
 check_policy(p);check_age(model,at)
 if cohort not in ('formal','canary'):raise ValueError('unknown cohort')
 start=utc(p['effective_from_utc'])
 if cohort=='formal':
  if not start<=at<utc(p['observation_end_utc']):raise ValueError('outside formal observation period')
  slot=start+timedelta(seconds=int((at-start).total_seconds()//p['slot_seconds'])*p['slot_seconds'])
  if not at<slot+timedelta(seconds=p['issue_window_seconds']):raise ValueError('missed issue window')
 else:slot=at
 q=old.quote(packet)
 if not q['captured']<=at<=q['captured']+timedelta(seconds=30):raise ValueError('unknown/stale entry quote')
 if isinstance(frozen_p,bool) or not math.isfinite(frozen_p) or not 0<=frozen_p<=1:raise ValueError('invalid frozen comparator')
 x=features(frame,at);prob=models.predict(model['model'],[x])[0]
 horizon=p['horizon_seconds'] if cohort=='formal' else p['canary_horizon_seconds']
 d=frame['DOGE-USDT']['close'];majority=json.loads((ROOT/old.PACKAGE).read_bytes())['majority_p']['1']
 return prior.canonical({'event_id':str(int(slot.timestamp()))+'_'+cohort,'cohort':cohort,
  'protocol_id':p['protocol_id'],'model_identity':model['trial_model_identity'],'policy_sha256':digest(p),
  'slot_utc':slot.isoformat(),'issued_at_utc':at.isoformat(),'target_utc':(at+timedelta(seconds=horizon)).isoformat(),
  'horizon_seconds':horizon,'frame':frame,'features':x,'entry_packet':packet,
  'candidate_p':{'selected':prob,'frozen_full':frozen_p,'constant_05':.5,'frozen_majority':majority},
  'frozen_rule_direction':int(d[-1]<=d[-2]),'regime':('UP' if d[-1]>d[-25] else 'DOWN')+('_HIGH_VOL' if x[1]>=model['training_volatility_median'] else '_LOW_VOL'),
  'included_in_formal_statistics':cohort=='formal','baseline_kind':'frozen_model_same_quote_replay_not_original_production_issuance',
  'public_eligibility_verified':False,'promotion_authorized':False,'real_orders_authorized':False})

def deadline(event,p):return utc(event['target_utc'])+timedelta(seconds=p['target_window_seconds'])

def score(event,packet,p):
 check_policy(p)
 if packet.get('capture_errors'):raise ValueError('raw HTTP capture incomplete or invalid')
 entry=old.quote(event['entry_packet']);exit=old.quote(packet);target=utc(event['target_utc'])
 if not (target<=utc(packet['requested_at_utc'])<=exit['captured']<deadline(event,p)
   and target<=exit['at']<=exit['captured']):raise ValueError('target quote outside actual prospective window')
 costs={};error=None
 try:
  if exit['book'] is None or not target<=datetime.fromtimestamp(exit['book']['ts_ms']/1000,timezone.utc)<=exit['captured']:raise ValueError('target raw depth unavailable or too early')
  nominal=old.paper_net(entry,exit,p,1);stress=old.paper_net(entry,exit,p,p['stress_multiplier'])
  costs={k:{'net_return':nominal if prob>=.5 else 0.,'stress_net_return':stress if prob>=.5 else 0.} for k,prob in event['candidate_p'].items()}
  costs.update(hold={'net_return':nominal,'stress_net_return':stress},cash={'net_return':0.,'stress_net_return':0.})
  costs['frozen_rule']={'net_return':nominal if event['frozen_rule_direction'] else 0.,'stress_net_return':stress if event['frozen_rule_direction'] else 0.}
 except ValueError as exc:error=str(exc);costs={k:None for k in (*event['candidate_p'],'hold','cash','frozen_rule')}
 return prior.canonical({'event_id':event['event_id'],'event_sha256':digest(event),'observed_at_utc':exit['captured'].isoformat(),
  'attempt_id':event['event_id']+'_attempt_'+str(int(utc(packet['requested_at_utc']).timestamp()*1000)),
  'target_packet':packet,'y':int(exit['price']>entry['price']),'economic_returns':costs,'cost_error':error,
  'included_in_formal_statistics':event['included_in_formal_statistics'],'actual_fills_verified':False,
  'promotion_authorized':False,'real_orders_authorized':False})

def witness(files,runs,read_at,before,after=None):
 for run in runs:
  try:
   if run['repository']['full_name']!=old.REPO or run['path'].split('@')[0]!=WORKFLOW or run['head_branch']!='main' or run['event'] not in ('push','workflow_dispatch','workflow_run') or run['status']!='completed' or run['conclusion']!='success':continue
   if run['event']=='workflow_run' and run.get('pull_requests'):continue
   head=run['head_sha'];job=run['audit_job'];started=utc(job['started_at']);finished=utc(job['completed_at'])
   if job['name']!='audit' or job['status']!='completed' or job['conclusion']!='success' or not utc(run['created_at'])<=started<=finished<before or after is not None and started<after:continue
   if all(read_at(head,path)==raw for path,raw in files.items()):return {'run_id':run['id'],'head_sha':head,'job_completed_at_utc':finished.isoformat()}
  except (KeyError,ValueError,TypeError):continue
 return None

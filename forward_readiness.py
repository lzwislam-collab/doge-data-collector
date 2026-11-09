"""Actual-clock operational monitor and bounded bridge; no forecast rewriting."""
import argparse,csv,io,json,math,os
from datetime import datetime,timedelta,timezone
from pathlib import Path
import phase2_forward_v2 as f
import phase2_forward_runtime_v2 as r
import engineering_run_receipt as terminal

ROOT=Path(__file__).resolve().parent
SOURCE_WORKFLOW='.github/workflows/okx_hourly_extreme_history.yml'
CLOCK_WORKFLOWS={'.github/workflows/bybit_liquidations.yml','.github/workflows/binance_liquidations.yml',
 '.github/workflows/phase2_forward_audit_v2.yml','.github/workflows/learned_shadow_v1.yml',
 '.github/workflows/collect.yml'}

def match_event(event,run):
 if any(event.get(k)!=run.get(k) for k in ('id','run_attempt','head_sha','path')):raise ValueError('completion identity/attempt changed')

def trusted_completion(run,jobs):
 if run.get('repository',{}).get('full_name')!=f.old.REPO or run.get('head_repository',{}).get('full_name')!=f.old.REPO or run.get('head_branch')!='main' or run.get('event') not in ('schedule','push','workflow_dispatch','workflow_run') or run.get('status')!='completed':return False
 if run.get('event')=='workflow_run' and run.get('pull_requests'):return False
 path=run.get('path','').split('@')[0]
 if path==SOURCE_WORKFLOW:
  steps=[s for j in jobs for s in j.get('steps',[])]
  return all(any(s.get('name')==name and s.get('conclusion')=='success' for s in steps) for name in ('Fail closed before writes on full-history or frozen protocol mutation','Persist data and diagnostics'))
 return path in CLOCK_WORKFLOWS and run.get('conclusion')=='success'

def source_state(raw,now):
 rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))));seen=set();ends={};counts={};errors=[]
 for row in rows:
  symbol=row['symbol'];stamp=int(row['open_utc_ms']);key=(symbol,stamp)
  if key in seen:raise ValueError('duplicate source timestamp')
  seen.add(key)
  if symbol not in ('DOGE-USDT','BTC-USDT','ETH-USDT') or row['source']!='OKX history-candles 1H confirmed':raise ValueError('unexpected source')
  receipt=f.utc(row['first_ingested_utc'])
  if stamp%f.H or stamp+f.H>int(now.timestamp()*1000) or receipt>now or receipt.timestamp()*1000<stamp+f.H:raise ValueError('future/unclosed/unknown source receipt')
  if any(not math.isfinite(float(row[k])) or float(row[k])<=0 for k in ('open','high','low','close')) or not math.isfinite(float(row['volume_base'])) or float(row['volume_base'])<0 or float(row['low'])>min(float(row['open']),float(row['close'])) or float(row['high'])<max(float(row['open']),float(row['close'])):raise ValueError('invalid source price/volume')
  counts[symbol]=counts.get(symbol,0)+1;ends[symbol]=max(ends.get(symbol,0),stamp+f.H)
 if set(ends)!= {'DOGE-USDT','BTC-USDT','ETH-USDT'}:raise ValueError('missing asset source')
 for symbol in ends:
  stamps=sorted(t for s,t in seen if s==symbol)
  if any(b-a!=f.H for a,b in zip(stamps,stamps[1:])):raise ValueError('source internal hourly gap')
 latest=datetime.fromtimestamp(min(ends.values())/1000,timezone.utc)
 ready=False
 try:f.frame_from_raw(raw,now);ready=True
 except ValueError as exc:errors.append(str(exc))
 return {'rows':len(rows),'asset_rows':counts,'latest_closed_utc':latest.isoformat(),'latest73_ready':ready,'frame_errors':errors}

def latest_matured(events,now,deadline):
 candidates=[e for e in events if deadline(e)<=now]
 return max(candidates,key=lambda e:(deadline(e),f.utc(e['slot_utc']),e['event_id']),default=None)

def publication_events(events,now,p):
 """A younger event cannot hide the most recently closed formal target."""
 matured=latest_matured([e for e in events if e['cohort']=='formal'],now,lambda e:f.deadline(e,p))
 selected=events[-1:]+([matured] if matured is not None else [])
 return list({e['event_id']:e for e in selected}.values())

def source_runtime_errors(source,now):
 if source['latest73_ready']:return []
 expected=now.replace(minute=0,second=0,microsecond=0)
 # Preserve only the existing one-hour tail grace, never an internal/frame gap.
 grace=(now-expected<timedelta(minutes=15)
  and f.utc(source['latest_closed_utc'])==expected-timedelta(hours=1)
  and source['frame_errors']==['missing latest73 source bars']
  and all(source['asset_rows'][s]>=73 for s in ('DOGE-USDT','BTC-USDT')))
 return [] if grace else ['SOURCE_FRAME_NOT_READY']

def legacy_runtime_health(p,events,scores,now):
 """Latest obligations per horizon; cumulative frozen plan stays untouched."""
 import learned_shadow_runtime as legacy
 start=f.utc(p['effective_from_utc']);emap={e['event_id']:e for e in events};scored={s['event_id'] for s in scores}
 missing_issue=[];missing_target=[];checked=[]
 for h in p['horizons_hours']:
  step=timedelta(hours=p['slot_steps_hours'][str(h)]);delay=timedelta(minutes=p['max_issue_delay_minutes'])
  if now>=start+delay:
   slot=start+((now-start-delay)//step)*step;key=str(int(slot.timestamp()))+'_'+str(h)
   if key not in emap:missing_issue.append(key)
  matured=latest_matured([e for e in events if e['horizon_hours']==h],now,lambda e:legacy.shadow.target_deadline(e,p))
  if matured is not None:
   checked.append(matured['event_id'])
   if matured['event_id'] not in scored:missing_target.append(matured['event_id'])
 errors=[]
 if missing_issue:errors.append('ORIGINAL18_MISSING_ISSUANCE')
 if missing_target:errors.append('ORIGINAL18_MISSING_TARGET')
 return {'missing_issuance_ids':missing_issue,'missing_target_ids':missing_target,'checked_target_ids':checked,'errors':errors}

def health(events,scores,now,last_closed,qualified):
 p=f.policy();start=f.utc(p['effective_from_utc']);scored={s['event_id'] for s in scores};emap={e['event_id']:e for e in events};errors=[];due=[];near=[];missing_targets=[]
 slots=[];slot=start
 while slot<min(now,f.utc(p['observation_end_utc'])):
  if slot+timedelta(seconds=p['issue_window_seconds'])<=now:slots.append(str(int(slot.timestamp()))+'_formal')
  slot+=timedelta(seconds=p['slot_seconds'])
 missing=[key for key in slots if key not in emap]
 for e in events:
  if e['event_id'] in scored:continue
  target=f.utc(e['target_utc']);deadline=target+timedelta(seconds=p['target_window_seconds'])
  if now>=deadline:missing_targets.append(e['event_id'])
  elif target<=now:
   due.append(e['event_id'])
   if deadline-now<=timedelta(minutes=10):near.append(e['event_id'])
 if missing:errors.append('MISSING_ISSUANCE')
 if missing_targets:errors.append('MISSING_TARGET')
 if near:errors.append('TARGET_DEADLINE_NEAR')
 expected=now.replace(minute=0,second=0,microsecond=0)
 if last_closed<expected and (now-expected>=timedelta(minutes=15) or last_closed<expected-timedelta(hours=1)):errors.append('LATEST_CLOSED_BAR_MISSING')
 qids={q['event_id'] for q in qualified};missing_witness=[e['event_id'] for e in events if e['event_id'] in scored and f.deadline(e,p)<=now and e['event_id'] not in qids]
 if missing_witness:errors.append('PUBLIC_DEADLINE_EVIDENCE_MISSING')
 # A separate current-window gate never resets or repairs historical gaps.
 runtime_issue=[slots[-1]] if slots and slots[-1] not in emap else []
 matured=latest_matured([e for e in events if e['cohort']=='formal'],now,lambda e:f.deadline(e,p))
 runtime_target=[];runtime_witness=[]
 if matured is not None:
  key=matured['event_id']
  if key not in scored:runtime_target.append(key)
  elif key not in qids:runtime_witness.append(key)
 runtime_errors=[]
 if runtime_issue:runtime_errors.append('MISSING_ISSUANCE')
 if runtime_target:runtime_errors.append('MISSING_TARGET')
 if runtime_witness:runtime_errors.append('PUBLIC_DEADLINE_EVIDENCE_MISSING')
 runtime_errors.extend(e for e in errors if e in ('TARGET_DEADLINE_NEAR','LATEST_CLOSED_BAR_MISSING'))
 return {'at_utc':now.isoformat(),'before_r2_activation':now<start,'missing_issuance':len(missing),'missing_issuance_ids':missing,
  'missing_targets':len(missing_targets),'missing_target_ids':missing_targets,'due_targets':due,'urgent_targets':near,
  'missing_public_witness':missing_witness,'last_closed_utc':last_closed.isoformat(),'errors':errors,
  'runtime_errors':runtime_errors,'runtime_missing_issuance_ids':runtime_issue,'runtime_missing_target_ids':runtime_target,
  'runtime_missing_public_witness_ids':runtime_witness,'runtime_checked_event_ids':[] if matured is None else [matured['event_id']],
  'historical_backfill_authorized':False,'real_orders_authorized':False,'promotion_authorized':False}

def actions(events,scores,now,source_ready,source_witnessed):
 p=f.policy();start=f.utc(p['effective_from_utc']);end=f.utc(p['observation_end_utc']);emap={e['event_id']:e for e in events};scored={s['event_id'] for s in scores};result=[]
 due=any(e['event_id'] not in scored and f.utc(e['target_utc'])<=now<f.deadline(e,p) for e in events)
 issue=False
 if start<=now<end:
  slot=start+timedelta(seconds=int((now-start).total_seconds()//p['slot_seconds'])*p['slot_seconds'])
  issue=str(int(slot.timestamp()))+'_formal' not in emap and now<slot+timedelta(seconds=p['issue_window_seconds'])
 if due:result.append('phase2_forward_live_v2.yml')
 if issue and source_ready:
  if source_witnessed:
   if 'phase2_forward_live_v2.yml' not in result:result.append('phase2_forward_live_v2.yml')
  else:result.append('phase2_forward_audit_v2.yml')
 return result

def pending_normal_collector():
 # Filter on the server: a waiting collector can be older than ten recent runs.
 # This is a pre-dispatch guard, not an atomic lock or a workflow_run override.
 for status in ('pending','queued','requested','waiting'):
  result=r.frozen.api('/actions/workflows/okx_hourly_extreme_history.yml/runs?branch=main&status='+status+'&per_page=1')
  runs=result['workflow_runs'];count=result['total_count']
  if not isinstance(runs,list) or type(count) is not int or count<0 or len(runs)>1 or bool(count)!=bool(runs):raise ValueError('normal collector queue response incomplete')
  if runs:
   run=runs[0]
   if not isinstance(run,dict) or run.get('head_branch')!='main' or run.get('path','').split('@')[0]!=SOURCE_WORKFLOW or type(run.get('id')) is not int or run['id']<=0 or run.get('status') not in ('pending','queued','requested','waiting','in_progress','completed'):raise ValueError('normal collector queue identity invalid')
   # Conservatively defer if the filtered run changed state during the read.
   return {'normal_collector_run_id':run['id'],'normal_collector_status':run['status']}
 return None

def dispatch_once(name):
 if name not in ('phase2_forward_live_v2.yml','phase2_forward_audit_v2.yml','okx_hourly_extreme_history.yml','learned_shadow_live.yml','learned_shadow_v1.yml'):raise ValueError('unapproved dispatcher target')
 runs=r.frozen.api('/actions/workflows/'+name+'/runs?per_page=10')['workflow_runs']
 if any(v['head_branch']=='main' and v['status'] in ('queued','in_progress','waiting','pending','requested') for v in runs):return {'workflow':name,'status':'ALREADY_PENDING'}
 if name=='learned_shadow_live.yml':
  pending=pending_normal_collector()
  if pending is not None:return {'workflow':name,'status':'DEFERRED_NORMAL_COLLECTOR_PENDING',**pending}
 from urllib.request import Request,urlopen
 payload={'ref':'main'}
 if name=='phase2_forward_live_v2.yml':payload['inputs']={'mode':'publish'}
 req=Request('https://api.github.com/repos/'+f.old.REPO+'/actions/workflows/'+name+'/dispatches',data=json.dumps(payload).encode(),method='POST',headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','User-Agent':'DOGE-Forward-Readiness'})
 with urlopen(req,timeout=20) as response:
  if response.status!=204:raise ValueError('publisher/audit dispatch not accepted')
 return {'workflow':name,'status':'DISPATCH_ACCEPTED_COMPLETION_UNVERIFIED'}

def run(mode):
 r.run('local');p,model,old,files=r.guard();now=datetime.now(timezone.utc);events=r.records('events');scores=r.records('scores');inspected=publication_events(events,now,p)
 extra=[e[k] for e in inspected for k in ('registration_run_id','source_run_id')]
 for e in inspected:
  for kind in ('prediction','target'):
   path=ROOT/f.BASE/'bindings'/(e['event_id']+'_'+kind+'.json')
   if path.exists():extra.append(json.loads(path.read_bytes())['run_id'])
 runs=r.runs_server(extra);qualified,rejected=r.publication(p,inspected,[s for s in scores if s['event_id'] in {e['event_id'] for e in inspected}],files,runs)
 raw=(ROOT/r.SOURCE).read_bytes();source=source_state(raw,now);h=health(events,scores,now,f.utc(source['latest_closed_utc']),qualified)
 # Preserve the historical summary's original latest-event scope. The separate
 # runtime gate additionally checks the latest matured formal event online.
 h['missing_public_witness']=[k for k in h['missing_public_witness'] if k in {e['event_id'] for e in events[-1:]}]
 if not h['missing_public_witness'] and 'PUBLIC_DEADLINE_EVIDENCE_MISSING' in h['errors']:h['errors'].remove('PUBLIC_DEADLINE_EVIDENCE_MISSING')
 result={**h,'source':source,'online_publication_events':len(inspected),'online_publication_covers_all':len(inspected)==len(events),
  'publication_rejected':rejected,'dispatches':[],'parent_terminal_receipt':None,'parent_trusted':None,'original18':{}}
 result['runtime_checked_event_ids']=[e['event_id'] for e in inspected]
 result['runtime_errors'].extend(source_runtime_errors(source,now))
 import learned_shadow_runtime as legacy
 lp=json.loads((ROOT/f.old.POLICY).read_bytes());le=legacy.read_records(ROOT,'events');ls=legacy.read_records(ROOT,'scores');legacyplan=legacy.plan(lp,le,{s['event_id']:s for s in ls},now)
 result['original18']=legacyplan
 legacy_missing=[e['event_id'] for e in le if e['event_id'] not in {s['event_id'] for s in ls} and legacy.shadow.target_deadline(e,lp)<=now]
 result['original18']['missing_target_ids']=legacy_missing
 if legacy_missing:result['errors'].append('ORIGINAL18_MISSING_TARGET')
 if any(legacyplan['matured_missing_issuance'].values()):result['errors'].append('ORIGINAL18_MISSING_ISSUANCE')
 result['original18_runtime']=legacy_runtime_health(lp,le,ls,now)
 result['runtime_errors'].extend(result['original18_runtime']['errors'])
 desired=actions(events,scores,now,source['latest73_ready'],f.witness({r.SOURCE:raw},runs,r.read_at,now) is not None)
 if legacyplan['issue'] or legacyplan['score']:desired.append('learned_shadow_live.yml')
 if not source['latest73_ready'] and 'LATEST_CLOSED_BAR_MISSING' in h['errors']:desired.append('okx_hourly_extreme_history.yml')
 if mode=='bridge':
  if os.environ.get('GITHUB_REF')!='refs/heads/main':raise ValueError('bridge dispatch only from main')
  event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes()).get('workflow_run')
  if event:
   runrow=r.frozen.api('/actions/runs/'+str(event['id']));match_event(event,runrow)
   jobs=terminal.pages(r.frozen.api,'/actions/runs/'+str(event['id'])+'/attempts/'+str(event['run_attempt'])+'/jobs','jobs')
   result['parent_trusted']=trusted_completion(runrow,jobs)
   if runrow['path'].split('@')[0]==SOURCE_WORKFLOW:result['parent_terminal_receipt']=terminal.audit(event,r.frozen.api)
   if runrow['path'].split('@')[0]==SOURCE_WORKFLOW:desired=[name for name in desired if name!='okx_hourly_extreme_history.yml']
   if not result['parent_trusted']:desired=[]
  for name in dict.fromkeys(desired):result['dispatches'].append(dispatch_once(name))
 result['status']='OPERATIONS_ERRORS_PRESERVED' if result['errors'] else 'WAIT_ACTIVATION' if now<f.utc(p['effective_from_utc']) else 'OPERATIONS_CURRENT_WINDOW_CLEAR'
 result['runtime_status']=('CURRENT_OPERATIONAL_ERRORS' if result['runtime_errors'] else 'WAIT_ACTIVATION' if now<f.utc(p['effective_from_utc'])
  else 'CURRENT_WINDOW_CLEAR_WITH_HISTORICAL_GAPS' if result['errors'] else 'CURRENT_WINDOW_CLEAR')
 return result

def main():
 parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['audit','bridge']);a=parser.parse_args();out=ROOT/'forward-readiness-output';out.mkdir(exist_ok=True)
 try:
  result=run(a.mode)
  # Missing/invalid current-state output is an error, never implicit success.
  if not isinstance(result['runtime_errors'],list) or any(not isinstance(e,str) for e in result['runtime_errors']):raise ValueError('invalid runtime error contract')
  if not isinstance(result['runtime_status'],str) or not result['runtime_status']:raise ValueError('missing runtime status')
 except Exception as exc:
  (out/'failure.json').write_bytes(f.encode({'error':type(exc).__name__+': '+str(exc)}));raise
 (out/'readiness.json').write_bytes(f.encode(result));print(json.dumps({k:result[k] for k in ('status','errors','runtime_status','runtime_errors','dispatches')},sort_keys=True))
 if os.environ.get('GITHUB_OUTPUT'):
  with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as stream:
   stream.write('has_errors='+str(bool(result['errors'])).lower()+'\n')
   stream.write('has_runtime_errors='+str(bool(result['runtime_errors'])).lower()+'\n')

if __name__=='__main__':main()

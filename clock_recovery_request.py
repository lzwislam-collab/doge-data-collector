#!/usr/bin/env python3
"""Admit bounded immutable clock requests before existing collectors; no clock override."""
import argparse,csv,io,json,math,os,re,subprocess
from datetime import datetime,timedelta,timezone
from pathlib import Path
REPO='lzwislam-collab/doge-data-collector'
TASK='doge'
THREAD='01a17272-26ad-7803-92f6-dd5796248d34'
PREFIX='data/p0_operations/clock_requests/'
REGISTRY_COLUMNS={"daily":["forecast_id","registered_utc","anchor_date_utc","anchor_close","target_date_utc","horizon_days","model_id","training_n","p10","p50","p90","historical_up_frequency","baseline_p10","baseline_p50","baseline_p90","baseline_up_frequency","input_digest"],"hourly":["forecast_id","model_id","issued_at_utc","source_quote_utc","source_hour_start_utc","reference_price","horizon_hours","target_utc","majority_p_up","majority_direction","doge_reversal_direction","btc_momentum_direction","feature_sha256"]}
SCORE_COLUMNS=["forecast_id","status","observed_at_utc","target_quote_utc","target_price","realized_up","majority_correct","doge_reversal_correct","btc_momentum_correct","reason"]
class Rejected(ValueError):pass
def reject(code):raise Rejected('CLOCK_'+code)
def git(root,*args):
 try:return subprocess.run(['git',*args],cwd=root,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',timeout=15).stdout
 except (OSError,subprocess.SubprocessError):reject('GIT_READ_FAILED')
def utc(value):
 if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)',value):reject('UTC_FORMAT')
 try:return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
 except ValueError:reject('UTC_FORMAT')
def strict_object(pairs):
 obj={}
 for key,value in pairs:
  if key in obj:reject('DUPLICATE_JSON_KEY')
  obj[key]=value
 return obj
def expected(kind,period):
 if kind=='daily':
  anchor=(period-timedelta(days=1)).date().isoformat()
  return ['doge_empirical_730d_v4_1_'+anchor+'_'+str(h)+'d' for h in (1,7,30)]
 last=int((period-timedelta(hours=1)).timestamp()*1000)
 return ['DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_'+str(last)+'_'+str(h)+'h' for h in (1,24)]
def rows_at(root,head,path,columns):
 text=git(root,'show',head+':'+path);reader=csv.DictReader(io.StringIO(text))
 if reader.fieldnames!=columns:reject('REGISTRY_SCHEMA')
 rows={}
 for row in reader:
  fid=row.get('forecast_id')
  if None in row or any(v is None for v in row.values()) or not fid or fid in rows:reject('REGISTRY_SCHEMA')
  rows[fid]=row
 return rows
def registered(root,head,kind):
 path='data/forecast_v41/registry.csv' if kind=='daily' else 'data/direction_v42/forward_registry.csv'
 return rows_at(root,head,path,REGISTRY_COLUMNS[kind])
def continuity_targets(root,head,records,missing,now):
 scores=rows_at(root,head,'data/direction_v42/forward_scores.csv',SCORE_COLUMNS);pending=[];deadlines=[]
 for fid in missing:
  if fid not in records:reject('TARGET_ID_NOT_REGISTERED')
  row=records[fid];match=re.fullmatch(r'DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1_([0-9]+)_(1|24)h',fid)
  if not match or row['model_id']!='DOGE_DIRECTION_ISSUED_QUOTE_SHADOW_V42_1' or row['horizon_hours']!=match[2]:reject('TARGET_ROW_BINDING')
  try:
   bar=int(match[1]);price=float(row['reference_price']);source=datetime.fromtimestamp(bar/1000,timezone.utc)+timedelta(hours=1)
  except (ValueError,OverflowError,OSError):reject('TARGET_ROW_BINDING')
  issued=utc(row['issued_at_utc']);target=utc(row['target_utc']);quote=utc(row['source_quote_utc'])
  if bar%3600000 or not math.isfinite(price) or price<=0 or utc(row['source_hour_start_utc'])!=source or not source<=issued<=source+timedelta(minutes=45) or not quote<=issued<=now or target!=issued+timedelta(hours=int(match[2])):reject('TARGET_ROW_BINDING')
  if target>now+timedelta(minutes=60):reject('TARGET_TOO_FAR')
  if now>=target+timedelta(minutes=15) or fid in scores:continue
  pending.append(fid);deadlines.append(target+timedelta(minutes=15))
 return pending,deadlines
def admit(root,kind,event_name,event,now,event_sha=None):
 if kind not in ('daily','hourly'):reject('KIND')
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):reject('SERVER_CLOCK')
 if event_name in ('schedule','workflow_dispatch'):return {'run':True,'decision':'LEGACY_EVENT','kind':kind}
 if event_name!='push':reject('EVENT_KIND')
 before,after=event.get('before'),event.get('after')
 if not all(isinstance(v,str) and re.fullmatch('[0-9a-f]{40}',v) and v!='0'*40 for v in (before,after)):reject('EVENT_SHA')
 if event_sha!=after:reject('EVENT_SHA')
 raw=git(root,'diff','--name-status','--no-renames','-z',before,after).split('\0')
 if raw[-1]=='':raw.pop()
 if len(raw)%2:reject('CHANGE_SCHEMA')
 changes=list(zip(raw[0::2],raw[1::2]));intents=[item for item in changes if item[1].startswith(PREFIX)]
 if not intents:return {'run':True,'decision':'LEGACY_EVENT','kind':kind}
 if len(changes)!=1 or len(intents)!=1 or intents[0][0]!='A':reject('SINGLE_NEW_INTENT_REQUIRED')
 request_path=intents[0][1]
 pattern=PREFIX+kind+'/'+(r'(\d{4}-\d{2}-\d{2})' if kind=='daily' else r'(\d{4}-\d{2}-\d{2}T\d{2})')+r'\.json'
 match=re.fullmatch(pattern,request_path)
 if not match:reject('PATH_KIND')
 key=match.group(1)
 try:period=datetime.strptime(key,'%Y-%m-%d' if kind=='daily' else '%Y-%m-%dT%H').replace(tzinfo=timezone.utc)
 except ValueError:reject('PERIOD_FORMAT')
 if event.get('repository',{}).get('full_name')!=REPO or event.get('ref')!='refs/heads/main' or event.get('forced') or event.get('deleted'):reject('EVENT_IDENTITY')
 head_event=event.get('head_commit') or {}
 if head_event.get('id')!=after or head_event.get('message')!='data: clock recovery request '+kind+' '+key:reject('EVENT_IDENTITY')
 sender=event.get('sender')
 if isinstance(sender,dict) and sender.get('login')=='github-actions[bot]':reject('ACTIONS_TOKEN_NOT_INDEPENDENT')
 if not isinstance(sender,dict) or sender.get('login')!='lzwislam-collab':reject('SOURCE_SENDER')
 git(root,'merge-base','--is-ancestor',before,after)
 head=git(root,'rev-parse','HEAD').strip();git(root,'merge-base','--is-ancestor',after,head)
 if git(root,'log','-1','--format=%H',before,'--',request_path).strip():reject('INTENT_ALREADY_USED')
 blob=git(root,'show',after+':'+request_path)
 if len(blob.encode('utf-8'))>4096:reject('INTENT_SIZE')
 try:request=json.loads(blob,object_pairs_hook=strict_object)
 except (ValueError,TypeError):reject('INTENT_JSON')
 common={'schema_version','kind','source_task_id','source_thread_id','observed_task_run_id','requested_at_utc','expires_at_utc','missing_ids'}
 keys=common|({'day_utc','anchor_date_utc'} if kind=='daily' else {'slot_start_utc','reason'})
 if not isinstance(request,dict) or set(request)!=keys:reject('INTENT_SCHEMA')
 if type(request['schema_version']) is not int or request['schema_version']!=1 or request['kind']!=kind or request['source_task_id']!=TASK or request['source_thread_id']!=THREAD:reject('SOURCE_IDENTITY')
 if not isinstance(request['observed_task_run_id'],str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',request['observed_task_run_id']):reject('SOURCE_RUN')
 if period.date()!=now.date():reject('PERIOD_NOT_CURRENT')
 if kind=='daily':
  if request['day_utc']!=key or request['anchor_date_utc']!=(period-timedelta(days=1)).date().isoformat():reject('ANCHOR')
  producer_end=period+timedelta(minutes=90);receiver_end=period+timedelta(minutes=100)
 else:
  if utc(request['slot_start_utc'])!=period or now.replace(minute=0,second=0,microsecond=0)!=period:reject('SLOT')
  producer_end=receiver_end=period+timedelta(minutes=35)
 requested=utc(request['requested_at_utc']);expires=utc(request['expires_at_utc'])
 commit_time=utc(git(root,'show','-s','--format=%cI',after).strip())
 if not period<=requested<producer_end or not period<=commit_time<producer_end or now>=receiver_end:reject('WINDOW_CLOSED')
 if not requested<=commit_time<=now or not timedelta(0)<expires-requested<=timedelta(minutes=20) or now>=expires:reject('INTENT_EXPIRED_OR_FUTURE')
 wanted=expected(kind,period);missing=request['missing_ids'];reason='ISSUANCE' if kind=='daily' else request['reason']
 if reason not in ('ISSUANCE','TARGET_CONTINUITY') or kind=='daily' and reason!='ISSUANCE':reject('REASON')
 if not isinstance(missing,list) or not missing or len(missing)>16 or any(not isinstance(fid,str) for fid in missing) or len(missing)!=len(set(missing)):reject('MISSING_IDS')
 records=registered(root,head,kind);deadline=min(expires,receiver_end);pending=[]
 if reason=='ISSUANCE':
  if not set(missing).issubset(wanted):reject('MISSING_IDS')
  run=not set(wanted).issubset(records);decision='FRESH_CLOCK_REQUEST' if run else 'ALREADY_REGISTERED'
 else:
  pending,limits=continuity_targets(root,head,records,missing,now);run=bool(pending)
  decision='FRESH_CLOCK_REQUEST' if run else 'TARGETS_ALREADY_RECORDED_OR_OUTSIDE_SAFE_WINDOW'
  if limits:deadline=min(deadline,min(limits))
 return {'run':run,'decision':decision,'reason':reason,'kind':kind,'period':key,'expected_ids':wanted,'pending_target_ids':pending,'request_path':request_path,'request_sha':after,'registry_head':head,'source_run_id':request['observed_task_run_id'],'admission_deadline_utc':deadline.isoformat()}

def main(argv=None):
 parser=argparse.ArgumentParser();parser.add_argument('--kind',choices=('daily','hourly'),required=True);args=parser.parse_args(argv)
 try:
  name=os.environ.get('GITHUB_EVENT_NAME','');event={}
  if name=='push':event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'),object_pairs_hook=strict_object)
  result=admit(Path(__file__).resolve().parent,args.kind,name,event,datetime.now(timezone.utc),event_sha=os.environ.get('GITHUB_SHA'))
  if result['run'] and 'admission_deadline_utc' in result and datetime.now(timezone.utc)>=utc(result['admission_deadline_utc']):reject('ADMISSION_EXPIRED_AFTER_READS')
  code=0
 except (Rejected,OSError,ValueError,KeyError) as error:
  reason=str(error) if isinstance(error,Rejected) else 'CLOCK_EVENT_UNREADABLE'
  result={'run':False,'decision':reason,'kind':args.kind};code=1
 output=os.environ.get('GITHUB_OUTPUT')
 if output:
  with open(output,'a',encoding='utf-8') as stream:stream.write('run='+str(result['run']).lower()+'\ndecision='+result['decision']+'\n')
 print(json.dumps(result,sort_keys=True));return code
if __name__=='__main__':raise SystemExit(main())

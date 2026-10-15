"""Per-event pinned OSS custody. No historical forecast or frozen code writes."""
import base64,hashlib,json,os,re,sys
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import oss_inventory_retention as inventory
FLOOR=datetime(2028,10,16,tzinfo=timezone.utc)
ROOT=Path(__file__).resolve().parent
PREFIX='doge/evidence/per-forecast/v1/'
def utc(s):
 d=datetime.fromisoformat(s.replace('Z','+00:00'))
 if d.tzinfo is None:raise ValueError('timezone required')
 return d.astimezone(timezone.utc)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def retain(bucket,raw,track,lock,now=None):
 test_clock=now;now=now or datetime.now(timezone.utc);event=json.loads(raw)
 if track not in ('original18','r2','canary') or len(raw)>16*1024*1024:raise ValueError('isolated track/payload required')
 keyid=event['event_id'];issued=utc(event['issued_at_utc']);target=utc(event['target_utc'])
 pattern=r'[0-9]+_(1|24)' if track=='original18' else r'[0-9]+_'+('formal' if track=='r2' else 'canary')
 if not re.fullmatch(pattern,keyid) or not issued<=now<target:raise ValueError('no history backfill/future issuance')
 if track=='original18':
  if event.get('horizon_hours') not in (1,24) or target-issued!=timedelta(hours=event['horizon_hours']):raise ValueError('original horizon mismatch')
 else:
  if event.get('cohort')!=('formal' if track=='r2' else 'canary') or target-issued!=timedelta(seconds=event['horizon_seconds']):raise ValueError('cohort/horizon mismatch')
  if track=='r2' and event.get('included_in_formal_statistics') is not True:raise ValueError('formal membership required')
 if lock.get('state')!='Locked' or not lock.get('worm_id') or lock['retention_days']<730 or now+timedelta(days=lock['retention_days'])<FLOOR:raise ValueError('fixed horizon not met; extend Locked policy without editing objects')
 digest=sha(raw);key=PREFIX+track+'/'+keyid+'/'+digest+'.json'
 recovered=False
 try:
  put=bucket.put_object(key,raw,headers={'x-oss-forbid-overwrite':'true','x-oss-server-side-encryption':'AES256','Content-Type':'application/json','Content-MD5':base64.b64encode(hashlib.md5(raw).digest()).decode()})
 except Exception as exc:
  if getattr(exc,'status',None)!=409:raise
  put=bucket.head_object(key);recovered=True
 version=put.headers.get('x-oss-version-id')
 if not version or version=='null' or not put.request_id:raise ValueError('upload server receipt/version missing')
 result=bucket.get_object(key,params={'versionId':version});data=bytearray()
 try:
  while True:
   chunk=result.read(1024*1024)
   if not chunk:break
   data.extend(chunk)
   if len(data)>16*1024*1024:raise ValueError('oversize readback')
 finally:result.close()
 server=parsedate_to_datetime(result.headers['date']).astimezone(timezone.utc)
 modified=parsedate_to_datetime(result.headers['last-modified']).astimezone(timezone.utc)
 finished=test_clock or datetime.now(timezone.utc)
 until=modified+timedelta(days=lock['retention_days'])
 if finished>=target or not (issued<=modified<=server<target) or sha(data)!=digest or bytes(data)!=raw or result.headers.get('x-oss-version-id')!=version or not result.request_id or result.headers.get('x-oss-server-side-encryption')!='AES256' or until<FLOOR:raise ValueError('pre-target full pinned readback or retention failed')
 return {'schema':'DOGE_PER_FORECAST_WORM_V1','status':'PRE_TARGET_WORM_VERIFIED','track':track,'event_id':keyid,'issued_at_utc':event['issued_at_utc'],'target_utc':event['target_utc'],'sha256':digest,'byte_count':len(raw),'bucket':inventory.BUCKET,'endpoint':inventory.ENDPOINT,'key':key,'version_id':version,'upload_request_id':None if recovered else put.request_id,'recovery_head_request_id':put.request_id if recovered else None,'readback_request_id':result.request_id,'server_last_modified_utc':modified.isoformat(),'server_response_date_utc':server.isoformat(),'calculated_retain_until_utc':until.isoformat(),'bucket_worm':lock,'verified_at_utc':finished.isoformat(),'historical_backfill':False,'formal_statistics_eligible':track!='canary','independent_custodian_verified':False,'run_id':os.environ.get('GITHUB_RUN_ID'),'source_commit':os.environ.get('SOURCE_COMMIT')}
def connection():
 import oss2
 token=os.environ['ALIBABA_CLOUD_SECURITY_TOKEN']
 if not token:raise ValueError('short-lived token required')
 return oss2.Bucket(oss2.StsAuth(os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],token),inventory.ENDPOINT,inventory.BUCKET,connect_timeout=30)
def verify_frozen(root):
 for config in ('config/learned_shadow_runtime_v1.json','config/phase2_forward_registration_v2.json'):
  manifest=json.loads((Path(root)/config).read_bytes())
  for name,expected in manifest['sha256_lf'].items():
   if sha((Path(root)/name).read_bytes())!=expected:raise ValueError('frozen file changed: '+name)
def run():
 verify_frozen(ROOT)
 bucket=connection();lock=inventory.check_bucket(bucket);now=datetime.now(timezone.utc)
 out=ROOT/'forecast-worm-output';out.mkdir(exist_ok=True)
 report={'status':'WAIT_NO_ELIGIBLE_PRE_TARGET_EVENTS','at_utc':now.isoformat(),'bucket_worm':lock,'fixed_horizon_utc':FLOOR.isoformat(),'fixed_horizon_for_new_objects_satisfied':now+timedelta(days=lock['retention_days'])>=FLOOR,'receipts':[],'expired_unproven':[],'errors':[]}
 extension=os.environ.get('EXTEND_RETENTION_DAYS','0')
 if extension not in ('0','732'):raise ValueError('only explicit monotonic 732-day extension allowed')
 if extension=='732':
  report['extension']={'before':lock,'requested_days':732,'status':'NO_CHANGE_ALREADY_SUFFICIENT'}
  if lock['retention_days']<732:
   try:
    response=bucket.extend_bucket_worm(lock['worm_id'],732)
    after=inventory.check_bucket(bucket)
    if after['worm_id']!=lock['worm_id'] or after['retention_days']!=732:raise ValueError('extension readback mismatch')
    report['extension'].update(status='EXTENDED_AND_READBACK',request_id=response.request_id,after=after)
    lock=after;report['bucket_worm']=lock
   except Exception as exc:
    report['extension'].update(status='BLOCKED_EXTENSION',type=type(exc).__name__,provider_code=str(getattr(exc,'code','')),request_id=str(getattr(exc,'request_id','')))
    report['errors'].append(report['extension'])
  report['fixed_horizon_for_new_objects_satisfied']=now+timedelta(days=lock['retention_days'])>=FLOOR
 if os.environ.get('ENGINEERING_PROBE','false')=='true':
  issued=datetime.now(timezone.utc)
  probe={'schema':'ENGINEERING_CANARY_NOT_MODEL_FORECAST','event_id':str(int(issued.timestamp()))+'_canary','cohort':'canary','included_in_formal_statistics':False,'issued_at_utc':issued.isoformat(),'target_utc':(issued+timedelta(minutes=10)).isoformat(),'horizon_seconds':600,'real_orders_authorized':False}
  try:
   receipt=retain(bucket,json.dumps(probe,sort_keys=True).encode(),'canary',lock)
   dest=ROOT/'audit/forecast_worm/canary'/(probe['event_id']+'.json');dest.parent.mkdir(parents=True,exist_ok=True)
   with dest.open('x',encoding='utf-8') as stream:json.dump(receipt,stream,indent=2)
   report['receipts'].append(receipt)
  except Exception as exc:report['errors'].append({'track':'canary','type':type(exc).__name__,'provider_code':str(getattr(exc,'code','')),'request_id':str(getattr(exc,'request_id',''))})
 for base,track in [('data/learned_shadow_v1/events','original18'),('data/phase2_forward_v2/events','r2')]:
  for path in sorted((ROOT/base).glob('*.json')):
   raw=path.read_bytes();e=json.loads(raw);actual='canary' if e.get('cohort')=='canary' else track
   dest=ROOT/'audit/forecast_worm'/actual/(path.stem+'.json')
   if dest.exists():
    saved=json.loads(dest.read_bytes())
    if saved['sha256']!=sha(raw) or saved['track']!=actual:raise ValueError('existing receipt binding changed')
    continue
   if utc(e['target_utc'])<=now:
    report['expired_unproven'].append(actual+'/'+e['event_id']);continue
   try:
    receipt=retain(bucket,raw,actual,lock)
    if inventory.check_bucket(bucket)!=lock:raise ValueError('WORM policy changed during readback')
    dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('x',encoding='utf-8') as stream:json.dump(receipt,stream,indent=2)
    report['receipts'].append(receipt)
   except Exception as exc:report['errors'].append({'track':actual,'event_id':e['event_id'],'type':type(exc).__name__,'provider_code':str(getattr(exc,'code','')),'request_id':str(getattr(exc,'request_id',''))})
 if report['errors']:report['status']='BLOCKED_PRE_TARGET_WORM'
 elif report['receipts']:report['status']='PRE_TARGET_WORM_READBACK_PASS'
 (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps(report))
 return bool(report['errors'])
def report_panel(root,rows):
 daily=Path(root)/'audit/oss_retention/latest.json'
 result={'daily_inventory_status':'NOT_READ','per_forecast_status':'WAIT_NO_FORMAL_EVENTS','external_immutable_retention_verified':False,'live_oss_readback_this_report':False,'events':[],'fixed_horizon_utc':FLOOR.isoformat()}
 if daily.exists():
  d=json.loads(daily.read_bytes());result['daily_inventory_status']=d.get('status','UNKNOWN');result['daily_inventory_run_url']=d.get('run_url');result['daily_inventory_verified_at_utc']=d.get('verified_at_utc');result['daily_inventory_scope']='Recorded daily inventory only; not per-forecast proof'
 for row in rows:
  if not row.get('event_id'):continue
  track=row['track'];path=Path(root)/'audit/forecast_worm'/track/(row['event_id']+'.json')
  item={'track':track,'event_id':row['event_id'],'status':'BLOCKED_NO_PER_FORECAST_RECEIPT'}
  if path.exists():
   r=json.loads(path.read_bytes())
   item.update(status='RECORDED_RECEIPT_REQUIRES_LIVE_RECHECK',receipt=r)
  result['events'].append(item)
 if result['events']:result['per_forecast_status']='BLOCKED_LIVE_RECEIPT_RECHECK_REQUIRED'
 return result
if __name__=='__main__':sys.exit(run())


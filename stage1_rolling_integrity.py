#!/usr/bin/env python3
"""Read-only Stage 1 integrity check: whale scanner -> historical PoR UTXO audit.

Never mistake an old audit's internal "0 pending" for 0 pending against the
LATEST whale source. Run/provenance proof is separate and must consult real
GitHub Actions identities, not only fields written to a JSON snapshot.
"""
import csv
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
WHALE='data/whales_blocks/events.csv'
UTXO='data/stage1/rolling_por_utxo_events.csv'
WHALE_REPORT='data/whales_blocks/latest.json'
UTXO_REPORT='data/stage1/rolling_por_utxo_latest.json'
HEX64=re.compile(r'^[0-9a-f]{64}$')
REQUIRED_WHALE={'txid','block_height','amount_doge','observed_at_utc','verification'}
REQUIRED_UTXO={'txid','block_height','audited_at_utc','classification','balance_reconciled','issues'}
AMOUNT_FIELDS=('por_input_amount_doge','por_output_amount_doge',
               'por_address_set_delta_doge','fee_doge')

def utc(value):
 dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
 if dt.tzinfo is None or dt.utcoffset()!=timedelta(0):
  raise ValueError('timestamps must specify UTC')
 return dt.astimezone(timezone.utc)

def rows(root,path,required,now):
 p=root/path
 with p.open(newline='',encoding='utf-8-sig') as fh:
  data=csv.DictReader(fh)
  if not required.issubset(data.fieldnames or []):
   raise ValueError('CSV schema missing expected fields: '+path)
  entries={}
  for line in data:
   txid=line['txid'].lower()
   if not HEX64.fullmatch(txid) or txid in entries:
    raise ValueError('duplicate or invalid txid: '+path)
   if int(line['block_height'])<=0:
    raise ValueError('invalid block height: '+path)
   key='audited_at_utc' if path==UTXO else 'observed_at_utc'
   if utc(line[key])>now+timedelta(minutes=5):
    raise ValueError('future event time: '+path)
   if path==UTXO:
    if line['balance_reconciled'] not in ('true','false'):
     raise ValueError('invalid UTXO reconciliation state')
    if line['balance_reconciled']=='false' and not line['issues']:
     raise ValueError('unreconciled UTXO without issue')
   elif 'confirmed-depth>=6' not in line['verification']:
    raise ValueError('unconfirmed candidate in whale event CSV')
   entries[txid]=line
 return entries

def evaluate(root=ROOT,now=None):
 if now is None:now=datetime.now(timezone.utc)
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):
  raise ValueError('audit clock must be UTC')
 source=rows(root,WHALE,REQUIRED_WHALE,now)
 audited=rows(root,UTXO,REQUIRED_UTXO,now)
 whale=json.loads((root/WHALE_REPORT).read_text(encoding='utf-8'))
 report=json.loads((root/UTXO_REPORT).read_text(encoding='utf-8'))
 whale_end=utc(whale['ended_at_utc'])
 audit_end=utc(report['generated_at_utc'])
 if whale_end>now+timedelta(minutes=5) or audit_end>now+timedelta(minutes=5):
  raise ValueError('future source or audit snapshot')
 if whale.get('status')!='ok' or report.get('status') not in ('ok_sample_complete','partial_backlog'):
  raise ValueError('upstream or downstream status not healthy')
 if whale.get('cumulative_unique_whale_transactions')!=len(source):
  raise ValueError('whale source snapshot cumulative count differs from CSV')
 if len(audited)!=int(report.get('audited_unique_events',-1)):
  raise ValueError('UTXO report count does not match audited CSV')
 if any(txid not in source for txid in audited):
  raise ValueError('orphan audited UTXO transaction; source history changed')
 if any(source[tid]['block_height']!=entry['block_height'] for tid,entry in audited.items()):
  raise ValueError('confirmed height differs between source and audited CSV')
 if report.get('verified_exchange_netflow') is not False or report.get('current_exchange_address_ownership_verified') is not False:
  raise ValueError('unverified PoR candidate data claimed to be verified exchange flow')
 asof_rows=int(report.get('source_whale_events',-1))
 asof_pending=int(report.get('events_pending',-1))
 if asof_rows<0 or asof_rows>len(source) or asof_pending!=asof_rows-len(audited):
  raise ValueError('UTXO report as-of source count does not reconcile')
 if report['status']=='ok_sample_complete' and asof_pending:
  raise ValueError('audit claims completeness despite pending transactions')
 for entry in audited.values():
  if utc(entry['audited_at_utc'])>audit_end+timedelta(minutes=5):
   raise ValueError('UTXO row observed after its report timestamp')
 full_count=sum(e['balance_reconciled']=='true' for e in audited.values())
 if full_count!=int(report.get('full_utxo_reconciled_events',-1)):
  raise ValueError('UTXO reconciliation count differs from report')
 waiting=sorted(set(source)-set(audited))
 chain_meta={
  'trigger_event':report.get('trigger_event'),
  'audit_workflow_run_id':report.get('audit_workflow_run_id'),
  'upstream_whale_run_id':report.get('upstream_whale_run_id'),
  'upstream_whale_run_event':report.get('upstream_whale_run_event'),
  'upstream_whale_run_conclusion':report.get('upstream_whale_run_conclusion'),
  'upstream_whale_run_created_at_utc':report.get('upstream_whale_run_created_at_utc')}
 eligible=(chain_meta['trigger_event']=='workflow_run' and
   chain_meta['upstream_whale_run_event']=='schedule' and
   chain_meta['upstream_whale_run_conclusion']=='success' and
   bool(chain_meta['audit_workflow_run_id']) and bool(chain_meta['upstream_whale_run_id']))
 if eligible:
  upstream_created=utc(chain_meta['upstream_whale_run_created_at_utc'])
  if not(upstream_created<=audit_end and upstream_created.hour==3):
   raise ValueError('upstream run did not originate in promised 03 UTC window')
 result={
  'state':'consistent_with_current_backlog' if waiting else 'consistent_current',
  'local_data_integrity':'ok',
  'asof_utxo_snapshot_complete':asof_pending==0 and full_count==asof_rows,
  'audit_snapshot_utc':audit_end.isoformat(),
  'current_whale_snapshot_utc':whale_end.isoformat(),
  'source_rows_current':len(source),
  'utxo_rows_current':len(audited),
  'current_unprocessed_whale_events':len(waiting),
  'current_backlog_txid_sample':waiting[:5],
  'unreconciled_utxo_events':len(audited)-full_count,
  'asof_source_rows_in_report':asof_rows,
  'asof_pending_in_report':asof_pending,
  'eligible_chain_metadata_present':eligible,
  'provenance_metadata':chain_meta,
  'verified_exchange_netflow':False,
  'current_chain_automation_verified':False,
  'limitations':['Source can grow after a successful once-daily UTXO audit; never compare counts from different cutoffs without checking IDs',
    'File metadata alone cannot establish a real scheduled workflow_run chain',
    'Historical PoR address matches are NOT present-day OKX netflow']}
 return result


def monitor_quality(root=ROOT,now=None):
 """Collect diagnostics even when strict validation fails. No writes or API calls.

 Daily 03 UTC audit window: 30 hours allows the next window plus delay.
 Missing audit rows are backlog, distinct from absent files/required fields.
 """
 now=now or datetime.now(timezone.utc)
 if now.tzinfo is None or now.utcoffset()!=timedelta(0):
  raise ValueError('quality clock must be UTC')
 q={'schema_version':1,'generated_at_utc':now.isoformat(),'alerts':[],
    'missing_files':[],'missing_required_fields':0,'invalid_amount_events':0,
    'coverage_pct':None,'reconciled_coverage_pct':None,
    'verified_exchange_netflow':False,'results_not_for_trading_signals':True}
 def alert(code):
  if code not in q['alerts']:q['alerts'].append(code)
 data={}
 for path,required in ((WHALE,REQUIRED_WHALE),(UTXO,REQUIRED_UTXO|set(AMOUNT_FIELDS)
                      |{'input_addresses_matched','output_addresses_matched'})):
  try:
   with (root/path).open(newline='',encoding='utf-8-sig') as f:
    reader=csv.DictReader(f)
    headers=set(reader.fieldnames or [])
    entries=list(reader)
   q['missing_required_fields']+=len(required-headers)
   # Issues can be blank for reconciled rows; amounts must be blank for false rows.
   for row in entries:
    for key in required & headers:
     if key=='issues' or (key in AMOUNT_FIELDS and row.get('balance_reconciled')=='false'):
      continue
     if row.get(key) in (None,''):q['missing_required_fields']+=1
   data[path]=entries
  except FileNotFoundError:
   q['missing_files'].append(path);data[path]=[];alert('MISSING_FILES')
  except (OSError,ValueError,csv.Error):
   data[path]=[];alert('UNREADABLE_CSV')
 if q['missing_required_fields']:alert('MISSING_REQUIRED_FIELDS')
 def index(path):
  counts=Counter(str(r.get('txid') or '').lower() for r in data[path])
  entries={str(r.get('txid') or '').lower():r for r in data[path]
           if HEX64.fullmatch(str(r.get('txid') or '').lower())}
  return entries,sum(n-1 for n in counts.values() if n>1)
 source,source_dups=index(WHALE);audited,audit_dups=index(UTXO)
 covered=set(source)&set(audited)
 full={tid for tid in covered if audited[tid].get('balance_reconciled')=='true'}
 pending=set(source)-full
 q.update(source_unique_events=len(source),audited_unique_events=len(audited),
   covered_unique_events=len(covered),reconciled_events=len(full),
   backlog_events=len(set(source)-set(audited)),pending_reconciliation_events=len(pending),
   unreconciled_events=sum(r.get('balance_reconciled')!='true' for r in audited.values()),
   economic_ambiguity_events=sum(r.get('classification')=='BOTH_SIDES_MIXED_OR_SELF_RETURN'
                               for r in audited.values()),
   duplicate_source_rows=source_dups,duplicate_audit_rows=audit_dups,
   orphan_audit_events=len(set(audited)-set(source)))
 if not q['missing_files'] and 'UNREADABLE_CSV' not in q['alerts']:
  q['coverage_pct']=round(100*len(covered)/len(source),2) if source else None
  q['reconciled_coverage_pct']=round(100*len(full)/len(source),2) if source else None
 ages=[]
 for tid in pending:
  try:ages.append((now-utc(source[tid]['observed_at_utc'])).total_seconds()/3600)
  except (ValueError,KeyError,TypeError):alert('INVALID_EVENT_TIME')
 q['oldest_pending_age_hours']=round(max(ages),2) if ages else None
 if ages and max(ages)>30:alert('BACKLOG_OVER_30H')
 if len(pending)>240:alert('BACKLOG_OVER_BATCH_CAPACITY')
 if q['unreconciled_events']:alert('UNRECONCILED_UTXO')
 for row in source.values():
  try:
   amount=Decimal(row['amount_doge'])
   if (not amount.is_finite() or amount<0 or amount>Decimal('1000000000000')
       or amount.as_tuple().exponent < -8):
    raise ValueError('invalid source amount')
  except (ValueError,KeyError,TypeError,InvalidOperation):q['invalid_amount_events']+=1
 for row in audited.values():
  try:
   if row.get('balance_reconciled')=='true':
    values=[Decimal(row[k]) for k in AMOUNT_FIELDS]
    if any(not x.is_finite() or x.as_tuple().exponent < -8 for x in values):
     raise ValueError('invalid precision / non-finite amount')
    pin,pout,delta,fee=values
    if pin<0 or pout<0 or fee<0 or pout-pin!=delta or row.get('issues'):
     raise ValueError('invalid reconciled amount or issue')
   elif any(row.get(k) for k in AMOUNT_FIELDS):
    raise ValueError('unreconciled row exposes verified amounts')
  except (ValueError,KeyError,TypeError,InvalidOperation):q['invalid_amount_events']+=1
 if q['invalid_amount_events']:alert('INVALID_AMOUNTS')
 for path,key,limit,code in ((WHALE_REPORT,'ended_at_utc',2,'STALE_WHALE_OVER_2H'),
                           (UTXO_REPORT,'generated_at_utc',30,'STALE_UTXO_OVER_30H')):
  try:
   snap=json.loads((root/path).read_text(encoding='utf-8'))
   age=(now-utc(snap[key])).total_seconds()/3600
   q['whale_age_hours' if path==WHALE_REPORT else 'utxo_age_hours']=round(age,2)
   if age>limit:alert(code)
   if age < -5/60:alert('FUTURE_SNAPSHOT')
  except FileNotFoundError:q['missing_files'].append(path);alert('MISSING_FILES')
  except (ValueError,KeyError,TypeError,OSError):alert('INVALID_SNAPSHOT')
 try:evaluate(root,now)
 except (ValueError,KeyError,TypeError,OSError,csv.Error) as exc:
  alert('INVALID_LOCAL_DATA');q['integrity_error']=str(exc)[:280]
 q['alert_required']=bool(q['alerts'])
 q['state']='alert' if q['alerts'] else ('expected_window_backlog' if pending else 'healthy')
 q['policy']={'max_pending_age_hours':30,'max_utxo_age_hours':30,
              'max_whale_age_hours':2,'backlog_batch_capacity':240,
              'economic_ambiguity_is_informational':True}
 return q

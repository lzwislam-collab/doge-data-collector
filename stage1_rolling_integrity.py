#!/usr/bin/env python3
"""Read-only Stage 1 integrity check: whale scanner -> historical PoR UTXO audit.

Never mistake an old audit's internal "0 pending" for 0 pending against the
LATEST whale source. Run/provenance proof is separate and must consult real
GitHub Actions identities, not only fields written to a JSON snapshot.
"""
import csv
import json
import re
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
  'asof_utxo_snapshot_complete':asof_pending==0,
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

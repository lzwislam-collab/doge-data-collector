#!/usr/bin/env python3
"""Offline tests; no API requests and no scheduled or manually triggered UTXO runs."""
import csv
import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from stage1_rolling_integrity import evaluate

A='a'*64
B='b'*64
C='c'*64
NOW=datetime(2026,10,11,10,0,tzinfo=timezone.utc)
WH=['txid','block_height','amount_doge','observed_at_utc','verification']
UT=['txid','block_height','audited_at_utc','classification','balance_reconciled','issues']


def save(root,rel,headers,rows):
 path=root/rel
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='') as fh:
  writer=csv.DictWriter(fh,fieldnames=headers)
  writer.writeheader()
  writer.writerows(rows)


class StageOneRollingAuditTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.root=Path(self.tmp.name)
  self.source=[
   {'txid':A,'block_height':'6410001','amount_doge':'1000000',
    'observed_at_utc':'2026-10-11T07:12:00+00:00','verification':'confirmed-depth>=6'},
   {'txid':B,'block_height':'6410002','amount_doge':'2000000',
    'observed_at_utc':'2026-10-11T09:10:00+00:00','verification':'confirmed-depth>=6'}]
  self.audited=[
   {'txid':A,'block_height':'6410001','audited_at_utc':'2026-10-11T07:30:00+00:00',
    'classification':'NO_POR_MATCH','balance_reconciled':'true','issues':''}]
  self.whale={'status':'ok','ended_at_utc':'2026-10-11T09:24:03+00:00',
    'cumulative_unique_whale_transactions':2}
  self.report={'status':'ok_sample_complete','generated_at_utc':'2026-10-11T07:37:00+00:00',
    'source_whale_events':1,'audited_unique_events':1,'events_pending':0,
    'full_utxo_reconciled_events':1,'verified_exchange_netflow':False,
    'current_exchange_address_ownership_verified':False,'trigger_event':'push'}
  self.write()

 def tearDown(self):
  self.tmp.cleanup()

 def write(self):
  save(self.root,'data/whales_blocks/events.csv',WH,self.source)
  save(self.root,'data/stage1/rolling_por_utxo_events.csv',UT,self.audited)
  (self.root/'data/whales_blocks/latest.json').write_text(json.dumps(self.whale))
  (self.root/'data/stage1/rolling_por_utxo_latest.json').write_text(json.dumps(self.report))

 def test_old_asof_zero_is_not_current_zero(self):
  result=evaluate(self.root,NOW)
  self.assertEqual(result['local_data_integrity'],'ok')
  self.assertEqual(result['asof_pending_in_report'],0)
  self.assertEqual(result['current_unprocessed_whale_events'],1)
  self.assertEqual(result['state'],'consistent_with_current_backlog')
  self.assertFalse(result['current_chain_automation_verified'])
  self.assertFalse(result['eligible_chain_metadata_present'])

 def test_orphan_audit_fails(self):
  self.audited[0]['txid']=C
  self.write()
  with self.assertRaisesRegex(ValueError,'orphan'):
   evaluate(self.root,NOW)

 def test_duplicate_source_fails(self):
  self.source.append(dict(self.source[0]))
  self.whale['cumulative_unique_whale_transactions']=3
  self.write()
  with self.assertRaisesRegex(ValueError,'duplicate'):
   evaluate(self.root,NOW)

 def test_false_netflow_verification_fails(self):
  self.report['verified_exchange_netflow']=True
  self.write()
  with self.assertRaisesRegex(ValueError,'unverified PoR'):
   evaluate(self.root,NOW)

 def test_mismatched_snapshot_count_fails(self):
  self.report['source_whale_events']=2
  self.write()
  with self.assertRaisesRegex(ValueError,'as-of source count'):
   evaluate(self.root,NOW)

 def test_all_audited_but_only_push_not_chain(self):
  self.audited.append({'txid':B,'block_height':'6410002',
   'audited_at_utc':'2026-10-11T09:30:00+00:00',
   'classification':'NO_POR_MATCH','balance_reconciled':'true','issues':''})
  self.report.update({'generated_at_utc':'2026-10-11T09:35:00+00:00',
                      'source_whale_events':2,'audited_unique_events':2,
                      'full_utxo_reconciled_events':2})
  self.write()
  a=evaluate(self.root,NOW)
  self.assertEqual(a['current_unprocessed_whale_events'],0)
  self.assertFalse(a['eligible_chain_metadata_present'])

 def test_eligible_metadata_does_not_alone_prove_github_chain(self):
  self.report.update({'generated_at_utc':'2026-10-11T07:37:00+00:00',
   'trigger_event':'workflow_run','audit_workflow_run_id':'100',
   'upstream_whale_run_id':'99','upstream_whale_run_event':'schedule',
   'upstream_whale_run_conclusion':'success',
   'upstream_whale_run_created_at_utc':'2026-10-11T03:07:00Z'})
  self.write()
  result=evaluate(self.root,NOW)
  self.assertTrue(result['eligible_chain_metadata_present'])
  self.assertFalse(result['current_chain_automation_verified'])

 def test_non_03_upstream_metadata_is_rejected(self):
  self.report.update({'trigger_event':'workflow_run','audit_workflow_run_id':'100',
   'upstream_whale_run_id':'99','upstream_whale_run_event':'schedule',
   'upstream_whale_run_conclusion':'success',
   'upstream_whale_run_created_at_utc':'2026-10-11T04:07:00Z'})
  self.write()
  with self.assertRaisesRegex(ValueError,'03 UTC'):
   evaluate(self.root,NOW)

if __name__=='__main__':
 unittest.main(verbosity=2)

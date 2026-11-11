"""The six existing schedules recover data only, with a callback audit."""
from pathlib import Path
import unittest
import yaml

ROOT=Path(__file__).resolve().parent
SOURCES={
 'onchain.yml':('23 1 * * *','onchain'),
 'liquidations.yml':('4,19,34,49 * * * *','liquidations_okx'),
 'liquidity.yml':('13 * * * *','liquidity'),
 'etf_flows.yml':('43 5,13,21 * * 1-6','etf'),
 'macro.yml':('27 3,15 * * *','macro'),
 'whale_blocks.yml':('7,22,37,52 * * * *','whale_confirmed_blocks'),
}

def load(name):return yaml.load((ROOT/'.github/workflows'/name).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


class SourceWorkflowTests(unittest.TestCase):
 def test_original_six_clocks_and_serial_writers(self):
  for file,(cron,source) in SOURCES.items():
   with self.subTest(file=file):
    doc=load(file)
    self.assertEqual(doc['on']['schedule'],[{'cron':cron}])
    self.assertEqual(doc['concurrency']['cancel-in-progress'],'false')
    self.assertNotIn('DOGE_BLOCK_START_HEIGHT',str(doc))
 def test_collector_publication_is_scoped_and_reads_remote(self):
  for file,(_,source) in SOURCES.items():
   with self.subTest(file=file):
    job=load(file)['jobs']['sample' if source=='liquidations_okx' else 'collect']
    scripts='\n'.join(step.get('run','') for step in job['steps'])
    self.assertIn('python source_publication.py --source '+source,scripts)
    for forbidden in ('workflow_dispatch','quote_evidence_v2','forward_validation','p1_adaptive','python collector.py','git add -A','git add .'):
     self.assertNotIn(forbidden,scripts)
    checkout=next(step for step in job['steps'] if 'actions/checkout@' in step.get('uses',''))
    self.assertEqual(checkout['with']['ref'],'main')
    self.assertIn('sparse-checkout',checkout['with'])
 def test_liquidation_clock_cannot_awaken_frozen_quote_job(self):
  doc=load('liquidations.yml')
  self.assertEqual(doc['jobs']['quote']['if'],"github.event_name != 'schedule'")
  self.assertNotIn('.github/workflows/liquidations.yml',doc['on']['push']['paths'])
  self.assertEqual(set(doc['on']['push']['paths']),{'quote_evidence_v2.py','data/stage5/forward_validation_protocol_v2.json'})
 def test_quality_refreshes_after_exact_source_workflows_without_new_clock(self):
  doc=load('feed_freshness_audit.yml')
  callback=doc['on']['workflow_run']
  self.assertEqual(set(callback['workflows']),{load(file)['name'] for file in SOURCES})
  self.assertEqual(callback['types'],['completed'])
  self.assertNotIn('schedule',doc['on'])
  job=doc['jobs']['audit']
  self.assertIn("head_branch == 'main'",job['if'])
  self.assertIn('head_repository.full_name == github.repository',job['if'])
  self.assertIn('python source_publication.py --source quality',str(job))
 def test_whale_uses_existing_legal_budget_without_changing_global_rate(self):
  job=load('whale_blocks.yml')['jobs']['collect']
  self.assertEqual(job['timeout-minutes'],'15')
  self.assertEqual(job['env']['DOGE_BLOCK_RUN_SECONDS'],'780')
  self.assertEqual(job['env']['DOGE_BLOCK_MAX'],'100')


if __name__=='__main__':unittest.main()

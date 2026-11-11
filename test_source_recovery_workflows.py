"""The six existing schedules recover data only, with a callback audit."""
from pathlib import Path
import ast
import re
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


def guard_allows(job, event_name, **upstream):
 # Evaluate only the small literal/comparison boolean language used by these
 # GitHub job guards. Unexpected identifiers or operators fail the contract.
 context={'github.event_name':event_name,'github.repository':'owner/repo',
  'github.event.workflow_run.conclusion':upstream.get('conclusion'),
  'github.event.workflow_run.event':upstream.get('event'),
  'github.event.workflow_run.head_branch':upstream.get('head_branch'),
  'github.event.workflow_run.head_repository.full_name':upstream.get('repository')}
 expression=job.get('if','True')
 def literal(match):
  name=match.group()
  if name not in context:raise AssertionError('unexpected guard context: '+name)
  return repr(context[name])
 expression=re.sub(r'github(?:\.[A-Za-z_][A-Za-z_0-9]*)+',literal,expression)
 expression=expression.replace('&&',' and ').replace('||',' or ')
 tree=ast.parse(expression,mode='eval')
 allowed=(ast.Expression,ast.BoolOp,ast.Compare,ast.Constant,ast.And,ast.Or,ast.Eq,ast.NotEq)
 if any(not isinstance(node,allowed) for node in ast.walk(tree)):
  raise AssertionError('unexpected guard syntax')
 return bool(eval(compile(tree,'<workflow guard>','eval'),{'__builtins__':{}},{}))


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
  for event in ('workflow_dispatch','push','schedule','workflow_run','pull_request','repository_dispatch'):
   with self.subTest(event=event):
    self.assertEqual(guard_allows(doc['jobs']['quote'],event),event in ('workflow_dispatch','push'))
  self.assertNotIn('.github/workflows/liquidations.yml',doc['on']['push']['paths'])
  self.assertEqual(set(doc['on']['push']['paths']),{'quote_evidence_v2.py','data/stage5/forward_validation_protocol_v2.json'})
 def test_release_data_window_listens_only_to_existing_source_contract_ci(self):
  doc=load('liquidations.yml')
  self.assertEqual(doc['on'].get('workflow_run'),{
   'workflows':[load('test_source_recovery.yml')['name']],'types':['completed']})
  self.assertEqual(doc['on']['schedule'],[{'cron':'4,19,34,49 * * * *'}])
 def test_release_data_window_requires_successful_main_repository_push(self):
  job=load('liquidations.yml')['jobs']['sample']
  valid={'conclusion':'success','event':'push','head_branch':'main','repository':'owner/repo'}
  self.assertTrue(guard_allows(job,'workflow_run',**valid))
  invalid=[
   ('conclusion','failure'),('conclusion','cancelled'),('conclusion','skipped'),('conclusion',None),
   ('event','pull_request'),('event','schedule'),('event','workflow_dispatch'),('event',None),
   ('head_branch','feature/elsewhere'),('head_branch',None),
   ('repository','fork/repo'),('repository',None)]
  for field,value in invalid:
   context={**valid,field:value}
   with self.subTest(field=field,value=value):
    self.assertFalse(guard_allows(job,'workflow_run',**context))
  for event in ('workflow_dispatch','push','schedule'):
   with self.subTest(existing_event=event):
    self.assertTrue(guard_allows(job,event))
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
 def test_repository_discovery_installs_source_test_parsers(self):
  job=load('test_forward_validation.yml')['jobs']['test']
  scripts='\n'.join(step.get('run','') for step in job['steps'])
  for package in ('beautifulsoup4==4.12.3','PyYAML==6.0.2'):
   self.assertIn(package,scripts)


if __name__=='__main__':unittest.main()

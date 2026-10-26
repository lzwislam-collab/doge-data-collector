"""Offline runtime/workflow contract: no network, deployment, or source rewriting."""
import importlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent
WORKFLOW=ROOT/'.github/workflows/okx_hourly_extreme_history.yml'

def blocks():
    return re.split(r'(?=^      - (?:name:|uses:))',WORKFLOW.read_text(),flags=re.M)[1:]

def step(identifier):
    found=[b for b in blocks() if re.search(r'^        id: '+re.escape(identifier)+r'$',b,re.M)]
    if len(found)!=1: raise AssertionError('missing/duplicate step '+identifier)
    return found[0]

def enabled(block, values, successful=True):
    match=re.search(r'^        if: (.+)$',block,re.M)
    expression=match.group(1) if match else 'success()'
    has_status=bool(re.search(r'\b(always|success|failure|cancelled)\(\)',expression))
    expression=re.sub(r'\bsteps\.[A-Za-z_0-9.]+',lambda m:repr(values.get(m.group(),'')),expression)
    expression=expression.replace('always()','True').replace('success()',str(successful)).replace('failure()',str(not successful)).replace('cancelled()','False')
    expression=expression.replace('&&',' and ').replace('||',' or ')
    return bool(eval(expression,{'__builtins__':{}},{})) and (has_status or successful)

class WorkflowIntegrationTests(unittest.TestCase):
    def safe(self):
        return {'steps.deadline_service.outputs.fatal':'false',
                'steps.open_target_guard.outputs.tail_fatal':'false',
                'steps.persist.outcome':'success','steps.archive_bridge_start.outcome':'success',
                'steps.archive_bridge_stop.outcome':'success','steps.pit_artifact.outcome':'success',
                'steps.pit_preparation.outputs.generated':'true'}

    def test_foreground_invokes_budget_before_slow_collection(self):
        service=step('deadline_service')
        self.assertIn('--archive-reserve-seconds 1800',service)
        text=WORKFLOW.read_text()
        self.assertLess(text.index(service),text.index(step('collect')))
        for name in ('runtime_deadline_service','runtime_audit_worker','archive_capture_bridge','hourly_publication_recovery'):
            self.assertIsNotNone(importlib.util.find_spec(name),name+' is missing')

    def test_missing_or_unsafe_foreground_output_blocks_later_writers(self):
        for unsafe in ('','true'):
            values=self.safe();values['steps.deadline_service.outputs.fatal']=unsafe
            for sid in ('open_target_guard','operations','supplemental_evidence','pit_preparation','persist','archive_bridge_start','pit_publication'):
                with self.subTest(unsafe=unsafe,step=sid):self.assertFalse(enabled(step(sid),values))

    def test_missing_or_unsafe_tail_output_blocks_archive_and_publication(self):
        for unsafe in ('','true'):
            values=self.safe();values['steps.open_target_guard.outputs.tail_fatal']=unsafe
            for sid in ('operations','supplemental_evidence','pit_preparation','persist','archive_bridge_start','pit_publication'):
                with self.subTest(unsafe=unsafe,step=sid):self.assertFalse(enabled(step(sid),values))

    def test_snapshot_upload_and_drain_order_protect_live_writer(self):
        text=WORKFLOW.read_text()
        self.assertLess(text.index(step('persist')),text.index(step('archive_bridge_start')))
        self.assertLess(text.index(step('archive_bridge_start')),text.index(step('artifact')))
        self.assertLess(text.index(step('pit_artifact')),text.index(step('archive_bridge_stop')))
        self.assertLess(text.index(step('archive_bridge_stop')),text.index(step('pit_publication')))
        for b in blocks():
            if 'uses: actions/upload-artifact@v4' in b and any(name in b for name in ('doge-forward-evidence-','doge-plus-evidence-','doge-pit-preparation-')):
                paths=b.split('          path:',1)[1]
                self.assertTrue(all(line.strip().startswith('archive-snapshot/') for line in paths.splitlines() if line.strip() and line.strip()!='|'),paths)
        self.assertIn("DOGE_ARCHIVE_QUALIFIED_DRAIN: '1'",text)

    def test_marker_requires_drain_and_upload_but_keeps_pr_receipt_publisher(self):
        publication=step('pit_publication')
        self.assertIn('python pit_receipt_publish.py --run-id',publication)
        self.assertNotIn('pit_artifact_publication.py',publication)
        self.assertTrue(enabled(publication,self.safe()))
        for key in ('steps.archive_bridge_stop.outcome','steps.pit_artifact.outcome'):
            for value in ('failure','skipped',''):
                state=self.safe();state[key]=value
                self.assertFalse(enabled(publication,state),str(state))
        state=self.safe();state['steps.archive_bridge_start.outcome']=''
        self.assertFalse(enabled(step('archive_bridge_stop'),state))

    def test_foreground_and_guard_failure_gates_run_when_the_output_is_missing(self):
        failures=[b for b in blocks() if 'run: exit 1' in b]
        for field in ('steps.deadline_service.outputs.fatal','steps.open_target_guard.outputs.tail_fatal'):
            self.assertTrue(any(enabled(b,{}) and field in b and "!= 'false'" in b for b in failures),field)

class RuntimeIntegrationTests(unittest.TestCase):
    def runtime(self):
        self.assertIsNotNone(importlib.util.find_spec('runtime_deadline_service'),'service not integrated')
        return importlib.import_module('runtime_deadline_service')

    def test_unqualified_shutdown_cannot_publish_or_clear_fatal(self):
        runtime=self.runtime()
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'outputs';adapter=Mock()
            result={'fatal':False,'status':'DEGRADED','qualified_stop_required':True,'qualified_stop_satisfied':False}
            with patch.dict(os.environ,{'GITHUB_OUTPUT':str(output)}):out=runtime.finish(Path(directory),adapter,result)
            adapter.action.assert_not_called()
            self.assertTrue(out['fatal'])
            self.assertEqual(output.read_text().splitlines()[-1],'fatal=true')

    def test_required_qualification_survives_run_result_for_finalization(self):
        runtime=self.runtime()
        from test_runtime_deadline_service import Adapter,Clock
        clock=Clock();adapter=Adapter(clock);adapter.can_stop=lambda:False
        with tempfile.TemporaryDirectory() as directory:
            stop=Path(directory)/'stop';stop.touch()
            result=runtime.run(Path(directory),adapter,1,clock,clock.sleep,stop_file=stop,require_qualified_stop=True)
            self.assertIs(result.get('qualified_stop_required'),True)
            self.assertFalse(result['qualified_stop_satisfied'])

    def test_cli_budget_controls_real_loop_and_persists_the_plan(self):
        runtime=self.runtime()
        from test_runtime_deadline_service import Adapter,Clock
        clock=Clock();adapter=Adapter(clock);adapter.auditor=type('Auditor',(),{'close':lambda self:None})()
        adapter.python=lambda *args:None;adapter.command=lambda *args:None
        plan={'duration_seconds':1,'formal_eligibility_claimed':False,'archive_reserve_seconds':1800}
        actual=runtime.run
        def loop(root,service,duration,**kwargs):
            self.assertEqual(duration,1)
            return actual(root,service,duration,clock,clock.sleep,**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runtime,'__file__',str(Path(directory)/'runtime_deadline_service.py')),patch.object(runtime,'LiveAdapter',return_value=adapter),patch.object(runtime,'run',side_effect=loop),patch('archive_window_budget.from_root',return_value=plan),patch.object(sys,'argv',['runtime','--duration-seconds','2400','--archive-reserve-seconds','1800']):
                self.assertEqual(runtime.main(),0)
            result=json.loads((Path(directory)/runtime.BASE/'latest.json').read_text())
            self.assertEqual(result['cycles'],1)
            self.assertEqual(result['archive_window_budget'],plan)

if __name__=='__main__':unittest.main()

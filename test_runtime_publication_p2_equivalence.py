"""Real isolated-checkout tests; fixture timings are not performance evidence."""
import hashlib
import ast
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_runtime_publication_profile as safety


class P2EquivalenceTests(unittest.TestCase):
    setUp = safety.ProfilerSafetyTests.setUp
    git = safety.ProfilerSafetyTests.git
    commit = safety.ProfilerSafetyTests.commit
    fixture = safety.ProfilerSafetyTests.fixture
    object_bytes = safety.ProfilerSafetyTests.object_bytes

    def require_feature(self):
        self.assertIn('p2_equivalence_experiment',
                      inspect.signature(self.driver.run_profile).parameters,
                      'full P2 equivalence experiment is missing')

    def setup_p2(self, helper_extra='', runtime_extra='', reference_extra=''):
        self.require_feature()
        self.fixture()
        files = {
            'p2_paired_execution_v3_1.py':
                "import json\n"
                "def count(root, name):\n"
                "    p=root/name; n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n)); return n\n"
                "def guard(root):\n"
                "    count(root,'.guard-count')\n"
                "def history_guard(root):\n"
                "    " + (reference_extra or 'pass') + "\n"
                "    return [json.loads(line) for line in (root/'data/p2_paired_execution_v3_1/books.jsonl').read_text().splitlines()]\n"
                "def event_books(root, events):\n"
                "    for row in events: yield row\n"
                "    count(root,'.books-count')\n",
            'data/p2_paired_execution_v3_1/books.jsonl': '{"id":"first"}\n{"id":"second"}\n',
            'data/direction_v42/forward_scores.csv': 'forecast_id\n',
            'data/quote_evidence_v2/bindings.csv': 'forecast_id\n',
            'data/paired_forward_v1/pairs.jsonl': '{}\n',
        }
        for name, body in files.items():
            path=self.source/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body)
        publisher=self.source/'publish_forward_evidence.py'
        publisher.write_text(publisher.read_text()+
            "\n_original_readback=verify_readback\n"
            "def verify_readback(root, head, expected):\n"
            "    p=root/'.readbacks'; n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n))\n"
            "    assert int((root/'.guard-count').read_text())==n, 'P2 guard omitted'\n"
            "    assert int((root/'.books-count').read_text())==n, 'P2 event books omitted'\n"
            "    return _original_readback(root,head,expected)\n")
        fixed=self.commit('fixed frozen P2 inputs without runtime helper')
        helper=(
            "import json\n"
            "def history_guard(root, engine=None, diagnostics=None):\n"
            "    assert (root/'.git/objects/info/alternates').is_file(), 'real shared checkout required'\n"
            "    p=root/'.helper-calls'; n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n))\n"
            "    if diagnostics is not None: diagnostics.update(implementation='runtime_p2_history_native_batch_v1',mode='native_batch',status='ok',attempted_commit_count=1,ordered_oids_sha256='fixture',native_batch_calls=1,empty_fallback_show_calls=0)\n"
            "    events=engine.history_guard(root)\n"
            "    " + (helper_extra or 'pass') + "\n"
            "    return events\n")
        (self.source/'runtime_p2_history.py').write_text(helper)
        (self.source/'runtime_deadline_service.py').write_text(
            "import json,hashlib\n"
            "def validate_p2(root):\n"
            "    import p2_paired_execution_v3_1 as engine\n"
            "    import runtime_p2_history as helper\n"
            "    engine.guard(root)\n"
            "    diagnostics={}\n"
            "    events=helper.history_guard(root,engine=engine,diagnostics=diagnostics)\n"
            "    for _ in engine.event_books(root,events): pass\n"
            "    " + (runtime_extra or 'pass') + "\n"
            "    print(json.dumps(dict(diagnostics,kind='runtime_p2_history_validation',module_sha256=hashlib.sha256((root/'runtime_p2_history.py').read_bytes()).hexdigest(),validation_status='ok')))\n")
        # Importing any frozen module from the current source is a concrete bug.
        (self.source/'p2_paired_execution_v3_1.py').write_text("raise AssertionError('current source frozen engine imported')\n")
        return fixed

    def run_case(self, fixed, timeout=15):
        output=self.base/'p2.json'
        code=self.driver.run_profile(self.source,fixed,output,timeout,p2_equivalence_experiment=True)
        return code,json.loads(output.read_text())

    def test_three_full_trials_use_fixed_engine_and_restore_dirty_helper_overlay(self):
        fixed=self.setup_p2()
        source_bytes={p.name:p.read_bytes() for p in self.source.glob('*.py')}
        objects=self.object_bytes()
        code,report=self.run_case(fixed)
        self.assertEqual(code,0,report)
        exp=report['p2_equivalence_experiment']; trials=exp['trials']
        self.assertEqual([t['name'] for t in trials],['reference_before','candidate','reference_after'])
        self.assertEqual([t['status'] for t in trials],['completed']*3)
        for trial in trials:
            self.assertEqual([p['stage'] for p in trial['phases']],['preflight_validate_p2','preflight_validate_state','preflight_pair_guard','preflight_pair_history_guard','core_exact_readback'])
            self.assertTrue(all(p['status']=='ok' for p in trial['phases']))
            self.assertEqual(trial['events']['count'],2)
            self.assertEqual(trial['head_after'],fixed)
        self.assertEqual(len({t['events']['sha256'] for t in trials}),1)
        candidate=trials[1]
        self.assertEqual(candidate['candidate_diagnostics']['mode'],'native_batch')
        self.assertEqual([p['stage'] for p in candidate['equivalence_phases']],['candidate_history_equivalence'])
        self.assertTrue(exp['events_equal']); self.assertTrue(exp['source_unchanged'])
        self.assertTrue(report['overlays_restored'])
        self.assertIsNone(report['implementation_overlays']['runtime_p2_history.py']['fixed_sha256'])
        self.assertTrue(report['tracked_worktree_clean'])
        self.assertEqual({p.name:p.read_bytes() for p in self.source.glob('*.py')},source_bytes)
        self.assertEqual(self.object_bytes(),objects)

    def test_candidate_event_reordering_cannot_report_equivalence(self):
        fixed=self.setup_p2(helper_extra='events=list(reversed(events))')
        code,report=self.run_case(fixed)
        self.assertNotEqual(code,0)
        exp=report['p2_equivalence_experiment']
        self.assertEqual([t['status'] for t in exp['trials']],['completed','error'])
        self.assertFalse(exp.get('events_equal',False))
        self.assertTrue(report['overlays_restored'])

    def test_candidate_failure_keeps_primary_error_when_state_check_also_fails(self):
        fixed=self.setup_p2(runtime_extra="(root/'data/p2_paired_execution_v3_1/books.jsonl').write_text('changed\\n'); raise KeyError('actual candidate failure')")
        code,report=self.run_case(fixed)
        self.assertNotEqual(code,0)
        self.assertEqual(report['error']['type'],'KeyError')
        trial=report['p2_equivalence_experiment']['trials'][-1]
        self.assertEqual(trial['error']['type'],'KeyError')
        self.assertIn('state_or_timing_error',trial)
        self.assertTrue(report['overlays_restored'])

    def test_extra_equivalence_pass_is_outside_main_validation_wall(self):
        fixed=self.setup_p2(helper_extra="__import__('time').sleep(.2) if n==2 else None")
        code,report=self.run_case(fixed)
        self.assertEqual(code,0,report)
        trial=report['p2_equivalence_experiment']['trials'][1]
        self.assertGreaterEqual(trial['equivalence_phases'][0]['seconds'],.2)
        self.assertGreaterEqual(trial['wall_seconds']-trial['validation_and_readback_wall_seconds'],.2)

    def test_timeout_marks_extra_equivalence_without_invalidating_completed_main_phases(self):
        fixed=self.setup_p2(helper_extra="__import__('time').sleep(30) if n==2 else None")
        code,report=self.run_case(fixed,timeout=2)
        self.assertNotEqual(code,0); self.assertEqual(report['status'],'timeout')
        trials=report['p2_equivalence_experiment']['trials']
        self.assertEqual([t['status'] for t in trials],['completed','timeout'])
        self.assertTrue(all(p['status']=='ok' for p in trials[1]['phases']))
        self.assertEqual(trials[1]['equivalence_phases'][0]['status'],'timeout')

    def test_missing_current_helper_fails_before_any_equivalence_trial(self):
        fixed=self.setup_p2(); (self.source/'runtime_p2_history.py').unlink()
        code,report=self.run_case(fixed)
        self.assertNotEqual(code,0)
        self.assertEqual(report['p2_equivalence_experiment']['trials'],[])
        self.assertFalse(report.get('frozen_validation_completed',False))

    def test_history_probe_restores_all_overlay_files_before_clean_check(self):
        fixed=self.setup_p2()
        (self.source/'git_history_batch_probe.py').write_text(
            "def run_probe(root,path,timeout_seconds=360,allow_object_directory=None):\n"
            "    assert not (root/'runtime_p2_history.py').exists(), 'untracked helper overlay leaked'\n"
            "    assert (root/'runtime_deadline_service.py').read_text()=='def validate_p2(root):\\n    return None\\n'\n"
            "    return {'status':'completed','patch_bytes_equal':True,'ordered_oids_equal':True,'commit_count':1}\n")
        output=self.base/'history.json'
        code=self.driver.run_profile(self.source,fixed,output,15,history_batch_experiment=True)
        report=json.loads(output.read_text())
        self.assertEqual(code,0,report)
        self.assertTrue(report['overlays_restored'])
        self.assertTrue(report['tracked_worktree_clean'])

    def test_source_helper_mutation_is_detected_even_though_helper_is_untracked(self):
        fixed=self.setup_p2(helper_extra="(__import__('pathlib').Path("+repr(str(self.source/'runtime_p2_history.py'))+").write_text('changed\\n')) if n==2 else None")
        code,report=self.run_case(fixed)
        self.assertNotEqual(code,0)
        self.assertFalse(report['p2_equivalence_experiment'].get('source_unchanged',False))
        self.assertIn('state_or_timing_error',report['p2_equivalence_experiment']['trials'][-1])

    def test_api_and_cli_reject_mixed_experiments(self):
        self.require_feature()
        for other in ('index_experiment','parallel_validation_experiment','history_batch_experiment'):
            with self.subTest(other=other):
                with self.assertRaises(ValueError):
                    self.driver.initial_report('a'*40,p2_equivalence_experiment=True,**{other:True})
        run=subprocess.run([sys.executable,str(safety.SCRIPT),'--source-sha','a'*40,'--output',str(self.base/'x.json'),'--p2-equivalence-experiment','--history-batch-experiment'],capture_output=True,text=True)
        self.assertNotEqual(run.returncode,0)
        self.assertIn('not allowed with argument',run.stderr)

    def test_actual_candidate_helper_and_wrapper_work_in_shared_fixed_checkout(self):
        # Real candidate code and exact original history_guard; synthetic guard/
        # raw-book fixtures keep this an integration test, not production proof.
        self.require_feature()
        import runtime_p2_history as actual_helper
        support=Path(actual_helper.__file__).parent
        original=(support/'p2_paired_execution_v3_1.py').read_text()
        node=next(n for n in ast.parse(original).body if isinstance(n,ast.FunctionDef) and n.name=='history_guard')
        history_function=ast.get_source_segment(original,node)
        self.fixture()
        engine=(
            "import json,re,hashlib,subprocess,types\n"
            "EVENTS='data/p2_paired_execution_v3_1/books.jsonl'\n"
            "def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()\n"
            "def read_events(root): return [json.loads(line) for line in (root/EVENTS).read_text().splitlines()]\n"
            "def command(root,*args): return subprocess.check_output(['git',*args],cwd=root)\n"
            "source=types.SimpleNamespace(publication=types.SimpleNamespace(command=command))\n"
            "def guard(root): return None\n"
            "def event_books(root,events):\n"
            "    for row in events: yield row\n" + history_function+'\n')
        (self.source/'p2_paired_execution_v3_1.py').write_text(engine)
        rows=[]
        for number in range(2):
            row={'previous_sha256':rows[-1]['record_sha256'] if rows else None,'value':number}
            row['record_sha256']=hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            rows.append(row)
        book=self.source/'data/p2_paired_execution_v3_1/books.jsonl'
        book.parent.mkdir(parents=True,exist_ok=True)
        book.write_text(''.join(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n' for row in rows))
        fixed=self.commit('actual candidate integration fixed history')
        (self.source/'runtime_p2_history.py').write_bytes(Path(actual_helper.__file__).read_bytes())
        (self.source/'runtime_deadline_service.py').write_bytes((support/'runtime_deadline_service.py').read_bytes())
        (self.source/'p2_paired_execution_v3_1.py').write_text("raise AssertionError('must use fixed engine')\n")
        objects=self.object_bytes()
        code,report=self.run_case(fixed)
        self.assertEqual(code,0,report)
        trials=report['p2_equivalence_experiment']['trials']
        self.assertEqual([t['events']['count'] for t in trials],[2,2,2])
        self.assertEqual(trials[1]['candidate_diagnostics']['mode'],'native_batch')
        self.assertEqual(trials[1]['candidate_diagnostics']['attempted_commit_count'],1)
        self.assertEqual(trials[1]['equivalence_diagnostics']['mode'],'native_batch')
        self.assertEqual(self.object_bytes(),objects)
        self.assertTrue(report['overlays_restored'])


if __name__=='__main__': unittest.main()

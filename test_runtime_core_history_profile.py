"""Small offline Git fixtures test probe safety, never production performance."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name('runtime_core_history_profile.py')


class CoreHistoryProfileTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'Core history profiling entry point is not implemented')
        spec = importlib.util.spec_from_file_location('core_profile_test_driver', SCRIPT)
        self.driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.driver)
        temporary = tempfile.TemporaryDirectory(prefix='core-profile-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.output = self.base / 'profile.json'

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME='Offline Profile Fixture',
                   GIT_AUTHOR_EMAIL='profile@example.invalid',
                   GIT_COMMITTER_NAME='Offline Profile Fixture',
                   GIT_COMMITTER_EMAIL='profile@example.invalid')
        return subprocess.run(['git', *args], cwd=self.source, env=env,
                              check=True, capture_output=True).stdout.decode().strip()

    def put(self, relative, body):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding='utf-8')

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'offline probe fixture')
        return self.git('rev-parse', 'HEAD')

    def fixture(self, p2_extra='', state_extra='', pair_extra='', receipt_extra='',
                state_mode='native_batch', state_sha=None):
        trace = """import json
EXPECTED=['p2','state_reference','guard','pair_reference','readback',
          'p2','state_candidate','guard','pair_candidate','readback',
          'p2','state_reference','guard','pair_reference','readback',
          'pair_reference','pair_candidate']
def record(root,name):
    path=root/'.fixture-phase-order'
    calls=json.loads(path.read_text()) if path.exists() else []
    assert len(calls)<len(EXPECTED) and EXPECTED[len(calls)]==name, ('wrong execution order',calls,name)
    calls.append(name);path.write_text(json.dumps(calls));return len(calls)
"""
        emit = """def emit_diagnostics(diagnostics,validation_succeeded):
    print(json.dumps(dict(diagnostics,kind=KIND,module_sha256=MODULE_SHA or hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),validation_status='ok' if validation_succeeded else 'error')))
"""
        common = "import hashlib,json\nfrom pathlib import Path\nimport fixture_trace as trace\n"
        files = {
            'fixture_trace.py': trace,
            'direction_forward_v42.py': """import csv
FCOLS=SCOLS=['forecast_id','value']
def read_rows(path,columns):
    with path.open(newline='',encoding='utf-8') as stream:
        reader=csv.DictReader(stream);assert reader.fieldnames==columns
        return {r['forecast_id']:r for r in reader}
""",
            'forward_validation.py': """import csv,io,subprocess
REG='data/direction_v42/forward_registry.csv'
SCORES='data/direction_v42/forward_scores.csv'
def history_receipts(root,relative,columns,current):
    revisions=subprocess.check_output(['git','log','--reverse','--format=%H','--',relative],cwd=root).decode().splitlines()
    receipts={};prior={}
    for revision in revisions:
        raw=subprocess.check_output(['git','show',revision+':'+relative],cwd=root).decode()
        rows={r['forecast_id']:r for r in csv.DictReader(io.StringIO(raw))}
        if any(rows.get(k)!=v for k,v in prior.items()):raise ValueError('historical row changed')
        for key in rows.keys()-prior.keys():receipts[key]={'commit_sha':revision,'fixture_time':'fixed'}
        prior=rows
    if any(current.get(k)!=v for k,v in prior.items()):raise ValueError('current row changed')
    return receipts
""",
            'quote_evidence_v2.py': "BASE='data/quote_evidence_v2'\nBINDINGS=BASE+'/bindings.csv'\nBCOLS=['forecast_id','value']\n",
            'publish_forward_evidence.py': """import subprocess
import fixture_trace as trace
import direction_forward_v42 as frozen
import forward_validation as base
import quote_evidence_v2 as evidence
PATHS=(base.REG,base.SCORES,'data/direction_v42/forward_latest.json',evidence.BINDINGS)
def validate_state(root):trace.record(root,'state_reference')
def verify_readback(root,head,expected):
    trace.record(root,'readback')
    return all(subprocess.check_output(['git','show',head+':'+p],cwd=root)==b for p,b in expected.items())
""",
            'paired_forward_cohort.py': """import json
import fixture_trace as trace
REG='data/paired_forward_v1/pairs.jsonl'
def guard(root):trace.record(root,'guard');return {}
def history_guard(root):
    trace.record(root,'pair_reference')
    return [json.loads(line) for line in (root/REG).read_text().splitlines()]
""",
            'runtime_p2_history.py': common + "KIND='runtime_p2_history_validation'\nMODULE_SHA=None\n" + emit,
            'runtime_publication_state.py': common + f"KIND='runtime_publication_state_validation'\nMODULE_SHA={state_sha!r}\n" + """import forward_validation as base
def history_receipts(root,relative,columns,current,diagnostics=None):
    result=base.history_receipts(root,relative,columns,current)
    if diagnostics is not None:diagnostics.update(mode='native_batch',status='ok')
""" + ('    ' + receipt_extra + '\n' if receipt_extra else '') + "    return result\n" + """def validate_state(root,diagnostics=None):
    trace.record(root,'state_candidate')
""" + ('    ' + state_extra + '\n' if state_extra else '') + f"    if diagnostics is not None:diagnostics.update(mode={state_mode!r},status='ok')\n" + emit,
            'runtime_paired_history.py': common + "KIND='runtime_paired_history_validation'\nMODULE_SHA=None\n" + """def history_guard(root,cohort=None,diagnostics=None):
    trace.record(root,'pair_candidate')
    result=[json.loads(line) for line in (root/'data/paired_forward_v1/pairs.jsonl').read_text().splitlines()]
""" + ('    ' + pair_extra + '\n' if pair_extra else '') + "    if diagnostics is not None:diagnostics.update(mode='native_batch',status='ok')\n    return result\n" + emit,
            'runtime_deadline_service.py': """import fixture_trace as trace
def validate_p2(root):
    import runtime_p2_history as helper
    count=trace.record(root,'p2')
""" + ('    ' + p2_extra + '\n' if p2_extra else '') + """    helper.emit_diagnostics({'mode':'native_batch','status':'ok'},True)
def validate_publication_state(root):
    import runtime_publication_state as helper
    diagnostics={};succeeded=False
    try:helper.validate_state(root,diagnostics);succeeded=True
    finally:helper.emit_diagnostics(diagnostics,succeeded)
def validate_paired_history(root):
    import runtime_paired_history as helper
    diagnostics={};succeeded=False
    try:
        result=helper.history_guard(root,diagnostics=diagnostics);succeeded=True;return result
    finally:helper.emit_diagnostics(diagnostics,succeeded)
""",
            'data/direction_v42/forward_registry.csv': 'forecast_id,value\nf0,original\n',
            'data/direction_v42/forward_scores.csv': 'forecast_id,value\ns0,original\n',
            'data/direction_v42/forward_latest.json': '{}\n',
            'data/quote_evidence_v2/bindings.csv': 'forecast_id,value\nb0,original\n',
            'data/quote_evidence_v2/snapshots/ab/abc.json': '{"fixture":"raw"}\n',
            'data/paired_forward_v1/pairs.jsonl': '{"id":"p0","value":1}\n',
            'data/p2_paired_execution_v3_1/books.jsonl': '{"book":"b0"}\n',
            'data/hourly_research/ohlc.csv': 'symbol,open\nDOGE,1\n',
            'data/p0_operations/latest.json': '{"fixture":"report"}\n',
            'reports/reference.md': 'fixed report\n',
        }
        for path, body in files.items():
            self.put(path, body)
        self.commit()
        for path, prefix in (('data/direction_v42/forward_registry.csv', 'f'),
                             ('data/direction_v42/forward_scores.csv', 's'),
                             ('data/quote_evidence_v2/bindings.csv', 'b')):
            self.put(path, f'forecast_id,value\n{prefix}0,original\n{prefix}1,added\n')
        self.put('data/paired_forward_v1/pairs.jsonl', '{"id":"p0","value":1}\n{"id":"p1","value":2}\n')
        self.put('data/p2_paired_execution_v3_1/books.jsonl', '{"book":"b0"}\n{"book":"b1"}\n')
        return self.commit()

    def run_probe(self, fixed, timeout=15):
        code = self.driver.run_profile(self.source, fixed, self.output, timeout)
        return code, json.loads(self.output.read_text())

    def test_complete_three_trials_and_deep_equivalence_preserve_source(self):
        fixed = self.fixture()
        before = self.driver.source_snapshot(self.source)
        code, report = self.run_probe(fixed)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual([t['name'] for t in report['trials']], ['reference_before', 'candidate', 'reference_after'])
        stages = ['preflight_validate_p2', 'preflight_validate_state', 'preflight_pair_guard',
                  'preflight_pair_history_guard', 'core_exact_readback']
        for trial in report['trials']:
            self.assertEqual(trial['status'], 'completed')
            self.assertEqual([p['stage'] for p in trial['phases']], stages)
            self.assertTrue(all(p['status'] == 'ok' for p in trial['phases']))
            self.assertEqual(trial['paired_records']['count'], 2)
            self.assertEqual(trial['history_before'], trial['history_after'])
        self.assertEqual(len(report['trials'][1]['candidate_stdout']['diagnostics']), 3)
        self.assertEqual(report['equivalence']['status'], 'completed')
        self.assertEqual(len(report['equivalence']['receipts']), 3)
        for receipt in report['equivalence']['receipts'].values():
            self.assertEqual(receipt['count'], 2)
            self.assertEqual(receipt['reference_sha256'], receipt['candidate_sha256'])
        self.assertEqual(report['equivalence']['paired_records']['count'], 2)
        self.assertTrue(report['overlays_restored'])
        self.assertTrue(report['source_unchanged'])
        self.assertEqual(self.driver.source_snapshot(self.source), before)
        self.assertFalse(report['os_cache_controlled'])
        self.assertFalse(report['production_publication_verified'])
        self.assertEqual(report['source_object_inventory_scope'], 'names_sizes_mtime_ns_not_pack_content_hashes')

    def test_equal_receipt_counts_cannot_hide_changed_first_commit_values(self):
        fixed = self.fixture(receipt_extra="result[next(iter(result))]['commit_sha']='0'*40")
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['equivalence']['status'], 'error')
        self.assertIn('first-commit receipts', report['error']['reason'])

    def test_equal_pair_counts_cannot_hide_reordered_complete_records(self):
        fixed = self.fixture(pair_extra='result.reverse()')
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['trials'][1]['status'], 'error')
        self.assertIn('paired records', report['error']['reason'])

    def test_original_exception_and_cause_win_over_changed_input_diagnostic(self):
        fixed = self.fixture(state_extra="(root/'data/direction_v42/forward_registry.csv').write_text('changed');raise ValueError('primary validator') from KeyError('primary cause')")
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['error']['type'], 'ValueError')
        self.assertEqual(report['error']['cause_type'], 'KeyError')
        self.assertEqual(report['trials'][1]['phases'][-1]['status'], 'error')
        self.assertEqual(report['trials'][1]['state_or_timing_error']['type'], 'ProfileError')
        self.assertTrue(report['source_unchanged'])

    def test_changed_report_in_isolated_checkout_is_not_completed(self):
        fixed = self.fixture(state_extra="(root/'reports/reference.md').write_text('changed report')")
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['trials'][1]['status'], 'error')
        self.assertEqual((self.source/'reports/reference.md').read_text(), 'fixed report\n')

    def test_missing_current_helper_cannot_silently_use_fixed_version(self):
        fixed = self.fixture()
        (self.source/'runtime_publication_state.py').unlink()
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertIn('implementation is missing', report['error']['reason'])
        self.assertEqual(report['trials'], [])

    def test_fallback_diagnostics_cannot_be_claimed_as_native_candidate(self):
        fixed = self.fixture(state_mode='legacy_full_fallback')
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertIn('native', report['error']['reason'])
        trial = report['trials'][1]
        self.assertEqual(trial['status'], 'error')
        diagnostics = trial['candidate_stdout']['diagnostics']
        self.assertEqual([item['kind'] for item in diagnostics],
                         list(self.driver.DIAGNOSTICS))
        self.assertEqual(diagnostics[1]['mode'], 'legacy_full_fallback')
        self.assertTrue(report['source_unchanged'])
        self.assertTrue(report['overlays_restored'])

    def test_wrong_module_hash_in_candidate_diagnostics_is_rejected(self):
        fixed = self.fixture(state_sha='0'*64)
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertIn('implementation', report['error']['reason'])
        self.assertEqual(report['trials'][1]['candidate_stdout']['diagnostics'][1]['module_sha256'],
                         '0' * 64)

    def test_candidate_stdout_is_bounded_before_a_large_print_completes(self):
        fixed = self.fixture(state_extra="print('x'*70000)")
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertIn('bounded', report['error']['reason'])

    def test_timeout_preserves_completed_first_trial_and_marks_running_phase(self):
        fixed = self.fixture(p2_extra="__import__('time').sleep(20) if count==6 else None")
        code, report = self.run_probe(fixed, timeout=1)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'timeout')
        self.assertEqual([t['status'] for t in report['trials']], ['completed', 'timeout'])
        self.assertEqual(report['trials'][1]['phases'][0]['status'], 'timeout')
        self.assertTrue(report['workspace_cleaned'])
        self.assertTrue(report['source_unchanged'])

    def test_python_source_write_is_denied_without_changing_original_bytes(self):
        target = self.source/'runtime_deadline_service.py'
        fixed = self.fixture(p2_extra=f"__import__('pathlib').Path({str(target)!r}).write_text('forbidden')")
        original = target.read_bytes()
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['error']['type'], 'PermissionError')
        self.assertEqual(target.read_bytes(), original)

    def test_git_source_config_write_is_denied_without_changing_config(self):
        fixed = self.fixture(p2_extra=f"__import__('subprocess').run(['git','-C',{str(self.source)!r},'config','fixture.invalid','value'],check=True)")
        original = (self.source/'.git/config').read_bytes()
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['error']['type'], 'PermissionError')
        self.assertEqual((self.source/'.git/config').read_bytes(), original)

    def test_git_explicit_source_config_path_is_denied_from_isolated_checkout(self):
        config = self.source/'.git/config'
        fixed = self.fixture(p2_extra=f"__import__('subprocess').run(['git','config','--file',{str(config)!r},'fixture.invalid','value'],check=True,cwd=root)")
        original = config.read_bytes()
        code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['error']['type'], 'PermissionError')
        self.assertEqual(config.read_bytes(), original)

    def test_equivalence_final_report_error_preserves_primary_exception_and_cause(self):
        original = ValueError('primary receipt validation')
        cause = KeyError('original receipt cause')
        def fail(*args):
            raise original from cause
        publisher = types.SimpleNamespace(
            base=types.SimpleNamespace(REG='registry.csv', SCORES='scores.csv', history_receipts=fail),
            frozen=types.SimpleNamespace(FCOLS=['forecast_id'], SCOLS=['forecast_id'], read_rows=lambda *args: {}),
            evidence=types.SimpleNamespace(BINDINGS='bindings.csv', BCOLS=['forecast_id']))
        report = {'phases': [], 'equivalence': {'status': 'not_started', 'phases': [], 'receipts': {}}}
        with mock.patch.object(self.driver.support, 'write_report', side_effect=[None, None, OSError('final report failed')]):
            with self.assertRaises(ValueError) as caught:
                self.driver._equivalence(self.source, report, self.output, publisher, None, None, None)
        self.assertIs(caught.exception, original)
        self.assertIs(caught.exception.__cause__, cause)
        self.assertEqual(report['equivalence']['status'], 'error')

    def test_source_before_and_after_evidence_identifies_objects_and_exact_protected_inputs(self):
        fixed = self.fixture()
        code, report = self.run_probe(fixed)
        self.assertEqual(code, 0, report)
        self.assertIn('source_before', report)
        self.assertIn('source_after', report)
        self.assertEqual(report['source_before'], report['source_after'])
        self.assertEqual(len(report['source_before']['audit_input_sha256']), 5)
        self.assertEqual(len(report['source_before']['history_sequences']), 5)
        self.assertGreater(report['source_before']['objects_metadata']['files'], 0)

    def test_source_code_change_after_worker_exit_cannot_be_reported_completed(self):
        fixed = self.fixture()
        real = self.driver.support.run_bounded
        def change_after(*args):
            result = real(*args)
            path = self.source/'runtime_deadline_service.py'
            path.write_bytes(path.read_bytes()+b'\n# changed after worker exit\n')
            return result
        with mock.patch.object(self.driver.support, 'run_bounded', side_effect=change_after):
            code, report = self.run_probe(fixed)
        self.assertNotEqual(code, 0)
        self.assertEqual(report['status'], 'error')
        self.assertFalse(report['source_unchanged'])

    def test_output_cannot_overlap_source(self):
        fixed = self.fixture()
        with self.assertRaises(ValueError):
            self.driver.run_profile(self.source, fixed, self.source/'report.json', 15)
        self.assertFalse((self.source/'report.json').exists())

    def test_current_runtime_overlay_is_measured_and_restored(self):
        fixed = self.fixture()
        runtime = self.source/'runtime_deadline_service.py'
        runtime.write_bytes(runtime.read_bytes()+b'\n# current runtime overlay identity\n')
        measured = hashlib.sha256(runtime.read_bytes()).hexdigest()
        code, report = self.run_probe(fixed)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['implementation_overlays']['runtime_deadline_service.py']['measured_sha256'], measured)
        self.assertNotEqual(report['implementation_overlays']['runtime_deadline_service.py']['fixed_sha256'], measured)
        self.assertTrue(report['overlays_restored'])
        self.assertEqual(hashlib.sha256(runtime.read_bytes()).hexdigest(), measured)


if __name__ == '__main__':
    unittest.main()

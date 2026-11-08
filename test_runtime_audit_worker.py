import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_deadline_service as runtime
from test_runtime_deadline_service import Adapter, Clock, START


class AuditIsolationTests(unittest.TestCase):
    def test_pending_slow_audit_does_not_occupy_capture_tick(self):
        clock = Clock()
        adapter = Adapter(clock)
        adapter.registry = [{'forecast_id': 'issued', 'horizon_hours': '1',
                             'source_hour_start_utc': START.isoformat(),
                             'target_utc': START.replace(hour=11).isoformat()}]
        original = adapter.action
        def action(name):
            if name == 'source_audit':
                adapter.calls.append(name)
                return {'status': 'pending'}
            return original(name)
        adapter.action = action
        state = {}
        runtime.cycle(adapter, state, clock)
        self.assertNotIn('source_audited_at', state)
        self.assertIn('book', adapter.calls)
        self.assertEqual(clock(), START)

    def test_actions_have_individual_elapsed_times(self):
        clock = Clock()
        adapter = Adapter(clock)
        adapter.duration['source_audit'] = 137
        out = runtime.cycle(adapter, {}, clock)
        audit = next(x for x in out['actions'] if x['stage'] == 'source_audit')
        self.assertEqual(audit['seconds'], 137)

    def test_changed_inputs_discard_completed_snapshot_without_overwriting_reports(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for p in worker.INPUTS:
                (root/p).parent.mkdir(parents=True, exist_ok=True)
                (root/p).write_text('original')
            fingerprint = worker.fingerprint(root)
            result = {'status': 'ok', 'inputs': fingerprint,
                      'outputs': {p: '{"status":"ok"}' for p in worker.OUTPUTS}}
            for p in worker.OUTPUTS:
                (root/p).parent.mkdir(parents=True, exist_ok=True)
                (root/p).write_text('old report')
            (root/worker.INPUTS[0]).write_text('new issuance')
            self.assertEqual(worker.accept(root, result)['status'], 'discarded_input_changed')
            self.assertTrue(all((root/p).read_text() == 'old report' for p in worker.OUTPUTS))

    def test_failed_audit_cannot_install_partial_outputs(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with self.assertRaises(ValueError): worker.accept(root, {'status': 'failed'})
            self.assertFalse((root/worker.OUTPUTS[0]).exists())

    def test_pending_context_uses_raw_engineering_capture_without_formal_eligibility(self):
        with tempfile.TemporaryDirectory() as name, patch.dict('os.environ', {
                'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': runtime.REPO}):
            adapter = runtime.LiveAdapter(Path(name))
            with patch.object(adapter, 'python') as command:
                result = adapter.action('book')
            self.assertFalse(result['formal_eligibility'])
            command.assert_called_once_with('p2_paired_execution_v3_1.py', 'smoke')

    def test_complete_matching_reports_are_installed_only_by_accept(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for p in worker.INPUTS:
                (root/p).parent.mkdir(parents=True, exist_ok=True)
                (root/p).write_text('original')
            result = {'status': 'ok', 'input_head': 'fixture', 'inputs': worker.fingerprint(root),
                      'outputs': {p: '{"status":"ok"}' for p in worker.OUTPUTS}, 'timings': []}
            self.assertEqual(worker.accept(root, result)['status'], 'ready')
            self.assertTrue(all(json.loads((root/p).read_text())['status'] == 'ok' for p in worker.OUTPUTS))

    def test_audit_runs_against_pinned_clone_and_never_writes_live_ledger(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)/'repo'; root.mkdir()
            for p in worker.INPUTS:
                (root/p).parent.mkdir(parents=True, exist_ok=True)
                (root/p).write_text('frozen ledger')
            for script, output in zip(worker.SCRIPTS, [None, worker.OUTPUTS[0], worker.OUTPUTS[1]]):
                code = 'from pathlib import Path\n'
                if output:
                    code += 'p=Path('+repr(output)+');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(\'{"status":"ok"}\')\n'
                (root/script).write_text(code)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root).decode().strip()
            git('init', '-q'); git('add', '.')
            git('-c', 'user.name=test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'fixture')
            head = git('rev-parse', 'HEAD')
            result = worker.audit_snapshot(root, head)
            self.assertEqual(result['status'], 'ok')
            self.assertFalse((root/worker.OUTPUTS[0]).exists())
            self.assertEqual((root/worker.INPUTS[0]).read_text(), 'frozen ledger')
            self.assertEqual(set(result['outputs']), set(worker.OUTPUTS))


class SparseCheckoutAndProgressTests(unittest.TestCase):
    EXCLUDED = 'data/plus_evidence_v1_0/sources/archive.bin'

    def git(self, root, *args):
        return subprocess.check_output(['git', *args], cwd=root).decode().strip()

    def make_repository(self, directory, *, noisy=False, slow=False):
        import runtime_audit_worker as worker
        root = Path(directory)/'repo'; root.mkdir()
        retained = {p: ('ledger:'+p+'\r\n').encode() for p in worker.INPUTS}
        retained.update({
            'data/stage5/pre_registration.json': b'{"locked_source_file":"fixture.py"}\n',
            'data/plus_evidence_v1_0/keep.json': b'keep parent bytes\x00\xff',
            'data/plus_evidence_v1_0/sources_neighbor/keep.bin': b'keep sibling bytes',
            'data/other/nested.txt': b'keep unrelated bytes\r\n',
            'system/frozen.json': b'unchanged protocol\n',
            '.gitattributes': b'* -text\n',
        })
        for path, data in retained.items():
            target = root/path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        excluded = root/self.EXCLUDED; excluded.parent.mkdir(parents=True)
        excluded.write_bytes(b'archive-original\x00\xff')
        for i, script in enumerate(worker.SCRIPTS):
            output = None if i == 0 else worker.OUTPUTS[i-1]
            code = "import hashlib,json,subprocess,time\nfrom pathlib import Path\n"
            code += "order=Path('fixture-order.json')\n"
            code += "seen=json.loads(order.read_text()) if order.exists() else []\n"
            code += "seen.append("+repr(script)+");order.write_text(json.dumps(seen))\n"
            if noisy and i == 0:
                code += "import sys\nprint('REPORT_SECRET_'+('x'*1048576));print('STDERR_SECRET_'+('y'*1048576),file=sys.stderr)\n"
            if slow and i == 0: code += "time.sleep(30)\n"
            if output:
                code += "tree=subprocess.check_output(['git','ls-tree','-r','-z','HEAD']).split(b'\\0')\n"
                code += "retained_ok=True\nfor entry in tree:\n if not entry:continue\n meta,name=entry.split(b'\\t',1);name=name.decode();mode,kind,oid=meta.split()\n if name.startswith('data/plus_evidence_v1_0/sources/'):continue\n body=Path(name).read_bytes();actual=hashlib.sha1(b'blob '+str(len(body)).encode()+b'\\0'+body).hexdigest().encode();retained_ok &= actual==oid\n"
                code += "report={'status':'ok','secret':'REPORT_SECRET_NEVER_LOG','order':seen,'excluded_missing':not Path("+repr(self.EXCLUDED)+").exists(),'retained_ok':retained_ok,'excluded_blob':subprocess.check_output(['git','show','HEAD:"+self.EXCLUDED+"']).hex(),'history_count':int(subprocess.check_output(['git','rev-list','--all','--count']))}\n"
                code += "target=Path("+repr(output)+");target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report))\n"
            (root/script).write_text(code)
        self.git(root, 'init', '-q'); self.git(root, 'add', '.')
        self.git(root, '-c', 'user.name=test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'first')
        excluded.write_bytes(b'archive-current\x00\xff')
        self.git(root, 'add', self.EXCLUDED)
        self.git(root, '-c', 'user.name=test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'second')
        head = self.git(root, 'rev-parse', 'HEAD')
        paths = list(worker.SCRIPTS)+['data/stage5/pre_registration.json']
        reviewed = {path:self.git(root, 'rev-parse', head+':'+path) for path in paths}
        return root, head, retained, reviewed

    def snapshot(self, root, head, reviewed, **kwargs):
        import runtime_audit_worker as worker
        output = io.StringIO()
        # The production allowlist is pinned to real audited modules; only the
        # test substitutes the identities of this actual Git fixture.
        with patch.object(worker, 'AUDITED_BLOBS', reviewed, create=True), contextlib.redirect_stdout(output):
            result = worker.audit_snapshot(root, head, **kwargs)
        return result, output.getvalue()

    def test_sparse_snapshot_omits_only_archive_and_keeps_history_inputs_and_order(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as directory:
            root, head, retained, reviewed = self.make_repository(directory)
            before_config = (root/'.git/config').read_bytes()
            result, log = self.snapshot(root, head, reviewed)
            report = json.loads(result['outputs'][worker.OUTPUTS[1]])
            self.assertTrue(report['excluded_missing'])
            self.assertTrue(report['retained_ok'])
            self.assertEqual(report['order'], ['forward_validation_v2.py','p0_operational_audit.py','paired_cohort_audit.py'])
            self.assertEqual(bytes.fromhex(report['excluded_blob']), b'archive-current\x00\xff')
            self.assertEqual(report['history_count'], 2)
            self.assertEqual(result['inputs'], {p:hashlib.sha256(retained[p]).hexdigest() for p in worker.INPUTS})
            self.assertTrue(all((root/p).read_bytes() == b for p,b in retained.items()))
            self.assertEqual((root/'.git/config').read_bytes(), before_config)
            self.assertFalse((root/'fixture-order.json').exists())
            self.assertTrue(all(not (root/p).exists() for p in worker.OUTPUTS))

    def test_explicit_full_baseline_materializes_archive_without_changing_validators(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as directory:
            root, head, _, reviewed = self.make_repository(directory)
            self.assertTrue(callable(getattr(worker, 'prepare_checkout', None)))
            result, _ = self.snapshot(root, head, reviewed, exclude_source_archive=False)
            report = json.loads(result['outputs'][worker.OUTPUTS[1]])
            self.assertFalse(report['excluded_missing']); self.assertTrue(report['retained_ok'])
            self.assertEqual(report['order'], list(worker.SCRIPTS))

    def test_unreviewed_module_or_lock_config_falls_back_to_full_checkout(self):
        import runtime_audit_worker as worker
        for changed in ('forward_validation_v2.py','data/stage5/pre_registration.json'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root, head, _, reviewed = self.make_repository(directory)
                target=root/changed;target.write_text(target.read_text()+'\n')
                self.git(root,'add',changed);self.git(root,'-c','user.name=test','-c','user.email=test@example.com','commit','-qm','new dependency')
                result, log = self.snapshot(root,self.git(root,'rev-parse','HEAD'),reviewed)
                self.assertFalse(json.loads(result['outputs'][worker.OUTPUTS[1]])['excluded_missing'])
                self.assertIn('full_checkout_fallback',log)

    def test_identity_selection_uses_pinned_tree_not_dirty_live_files_or_default_head(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as directory:
            root, head, _, reviewed = self.make_repository(directory)
            target=root/worker.SCRIPTS[0];target.write_text('raise RuntimeError("new HEAD must not execute")\n')
            self.git(root,'add',worker.SCRIPTS[0]);self.git(root,'-c','user.name=test','-c','user.email=test@example.com','commit','-qm','later HEAD')
            target.write_text('dirty source must not execute\n')
            result,_=self.snapshot(root,head,reviewed)
            report=json.loads(result['outputs'][worker.OUTPUTS[1]])
            self.assertTrue(report['excluded_missing']);self.assertEqual(report['history_count'],3)
            self.assertEqual(target.read_text(),'dirty source must not execute\n')

    def test_retained_symlink_forces_full_checkout_so_original_validator_sees_target(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as directory:
            root, head, _, reviewed = self.make_repository(directory)
            (root/'data/plus_evidence_v1_0/sources/manifest.json').write_text('{}')
            link=root/'data/quote_evidence_v2/snapshots/zz';link.parent.mkdir(parents=True,exist_ok=True)
            link.symlink_to('../../plus_evidence_v1_0/sources',target_is_directory=True)
            self.git(root,'add',str(link.relative_to(root)),'data/plus_evidence_v1_0/sources/manifest.json');self.git(root,'-c','user.name=test','-c','user.email=test@example.com','commit','-qm','retained link')
            checkout=Path(directory)/'checkout'
            self.assertTrue(callable(getattr(worker,'prepare_checkout',None)))
            with patch.object(worker,'AUDITED_BLOBS',reviewed,create=True),contextlib.redirect_stdout(io.StringIO()):
                info=worker.prepare_checkout(root,checkout,self.git(root,'rev-parse','HEAD'))
            self.assertEqual(info['mode'],'full_checkout_fallback')
            self.assertEqual(info['fallback_reason'],'retained_symlink')
            self.assertTrue((checkout/'data/quote_evidence_v2/snapshots/zz/archive.bin').exists())
            from quote_evidence_v2 import load_quote
            raw_paths=list((checkout/'data/quote_evidence_v2/snapshots').glob('*/*.json'))
            self.assertEqual(len(raw_paths),1)
            with self.assertRaisesRegex(ValueError,'invalid quote ID'):
                load_quote(checkout,raw_paths[0].stem)

    def test_sparse_configuration_overrides_global_cone_and_sparse_index_before_checkout(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as directory:
            root, head, _, reviewed=self.make_repository(directory)
            config=Path(directory)/'global-gitconfig';config.write_text('[core]\n sparseCheckout = true\n sparseCheckoutCone = true\n[index]\n sparse = true\n')
            checkout=Path(directory)/'checkout'
            self.assertTrue(callable(getattr(worker,'prepare_checkout',None)))
            with patch.object(worker,'AUDITED_BLOBS',reviewed,create=True),patch.dict(os.environ,{'GIT_CONFIG_GLOBAL':str(config)}),contextlib.redirect_stdout(io.StringIO()):
                info=worker.prepare_checkout(root,checkout,head)
            self.assertEqual(info['mode'],'sparse_source_archive_excluded')
            self.assertEqual(self.git(checkout,'config','--local','core.sparseCheckout'),'true')
            self.assertEqual(self.git(checkout,'config','--local','core.sparseCheckoutCone'),'false')
            self.assertEqual(self.git(checkout,'config','--local','index.sparse'),'false')
            self.assertFalse((checkout/self.EXCLUDED).exists())

    def test_phase_logs_do_not_disclose_reports_or_command_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root,head,_,reviewed=self.make_repository(directory,noisy=True)
            result,log=self.snapshot(root,head,reviewed)
            rows=[json.loads(line) for line in log.splitlines()]
            self.assertTrue(rows)
            self.assertNotIn('REPORT_SECRET',log);self.assertNotIn('STDERR_SECRET',log)
            self.assertTrue(all(len(line)<4096 for line in log.splitlines()))
            self.assertTrue(all(row['kind']=='runtime_source_audit_progress' for row in rows))
            self.assertEqual([r['phase'] for r in rows if r['event']=='begin' and r['phase'].endswith('.py')],['forward_validation_v2.py','p0_operational_audit.py','paired_cohort_audit.py'])

    def test_inherited_worker_logs_complete_with_large_output_without_touching_live_ledger(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as directory:
            root,head,retained,reviewed=self.make_repository(directory,noisy=True)
            logpath=Path(directory)/'inherited.log'
            driver="import json,sys,time\nfrom runtime_audit_worker import Auditor\na=Auditor(sys.argv[1])\ntry:\n deadline=time.monotonic()+12\n while True:\n  result=a.poll()\n  if result['status']=='ready':break\n  if time.monotonic()>deadline:raise RuntimeError('worker did not complete')\n  time.sleep(.02)\n print(json.dumps({'driver_result':result['status']}),flush=True)\nfinally:a.close()\n"
            with logpath.open('w') as log:
                done=subprocess.run([sys.executable,'-c',driver,str(root)],cwd=Path(worker.__file__).parent,stdout=log,stderr=log,timeout=18)
            self.assertEqual(done.returncode,0,logpath.read_text()[-2000:])
            text=logpath.read_text();events=[json.loads(line) for line in text.splitlines()]
            self.assertTrue(any(e.get('phase')=='result' and e.get('event')=='end' and e.get('result_sha256') for e in events))
            self.assertTrue(any(e.get('phase')=='parent_close' and e.get('worker_state')=='exited' for e in events))
            self.assertNotIn('REPORT_SECRET',text);self.assertNotIn('STDERR_SECRET',text)
            self.assertTrue(all((root/p).read_bytes()==data for p,data in retained.items()))

    def test_parent_close_records_completed_unconsumed_result_before_cleanup(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as directory:
            root,head,_,_=self.make_repository(directory)
            logpath=Path(directory)/'shutdown.log'
            driver="import sys,time\nfrom runtime_audit_worker import Auditor\na=Auditor(sys.argv[1]);a.poll()\ntry:\n deadline=time.monotonic()+10\n while a.process.poll() is None:\n  if time.monotonic()>deadline:raise RuntimeError('worker stuck')\n  time.sleep(.02)\nfinally:a.close()\n"
            with logpath.open('w') as log:
                done=subprocess.run([sys.executable,'-c',driver,str(root)],cwd=Path(worker.__file__).parent,stdout=log,stderr=log,timeout=15)
            self.assertEqual(done.returncode,0,logpath.read_text()[-1500:])
            events=[json.loads(line) for line in logpath.read_text().splitlines()]
            end=next(e for e in events if e.get('phase')=='parent_close' and e['event']=='end')
            self.assertEqual(end['reason'],'shutdown');self.assertEqual(end['worker_state'],'exited')
            self.assertTrue(end.get('result_json_valid'));self.assertEqual(end.get('result_status'),'ok')
            self.assertEqual(end['worker_returncode'],0)
            self.assertTrue(end['result_sha256'])
            self.assertTrue(all(not (root/p).exists() for p in worker.OUTPUTS))

    def test_parent_shutdown_records_running_worker_termination_without_raw_output(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as directory:
            root,head,_,_=self.make_repository(directory,slow=True)
            logpath=Path(directory)/'shutdown.log'
            driver="import json,sys,time\nfrom pathlib import Path\nfrom runtime_audit_worker import Auditor\na=Auditor(sys.argv[1]);a.poll()\ntry:\n deadline=time.monotonic()+10\n while True:\n  rows=[json.loads(line) for line in Path(sys.argv[2]).read_text().splitlines()]\n  if any(r.get('phase')=='forward_validation_v2.py' and r.get('event')=='begin' for r in rows):break\n  if time.monotonic()>deadline:raise RuntimeError('worker never entered script')\n  time.sleep(.02)\nfinally:a.close()\n"
            with logpath.open('w') as log:
                done=subprocess.run([sys.executable,'-c',driver,str(root),str(logpath)],cwd=Path(worker.__file__).parent,stdout=log,stderr=log,timeout=15)
            self.assertEqual(done.returncode,0,logpath.read_text()[-1500:])
            events=[json.loads(line) for line in logpath.read_text().splitlines()]
            end=next(e for e in events if e.get('phase')=='parent_close' and e['event']=='end')
            self.assertEqual(end['reason'],'shutdown');self.assertEqual(end['worker_state'],'running')
            self.assertTrue(end.get('termination_requested'));self.assertFalse(end['result_present'])
            self.assertLess(end['worker_returncode'],0)
            self.assertTrue(all(not (root/p).exists() for p in worker.OUTPUTS))

    def test_non_utf8_retained_filename_preserves_original_checkout_behavior(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as directory:
            root,head,_,reviewed=self.make_repository(directory)
            name=os.fsdecode(b'data/other/\xff.bin')
            (root/name).write_bytes(b'non-UTF8 retained file')
            self.git(root,'add','data/other');self.git(root,'-c','user.name=test','-c','user.email=test@example.com','commit','-qm','raw filename')
            head=self.git(root,'rev-parse','HEAD')
            with patch.object(worker,'AUDITED_BLOBS',reviewed,create=True),contextlib.redirect_stdout(io.StringIO()):
                full=Path(directory)/'full';worker.prepare_checkout(root,full,head,exclude_source_archive=False)
                try:info=worker.prepare_checkout(root,Path(directory)/'candidate',head)
                except UnicodeError:self.fail('valid non-UTF8 tracked name must not make the candidate audit fail')
            self.assertEqual((full/name).read_bytes(),b'non-UTF8 retained file')
            self.assertEqual((Path(directory)/'candidate'/name).read_bytes(),b'non-UTF8 retained file')
            self.assertEqual(info['mode'],'sparse_source_archive_excluded')

    def test_diagnostic_event_whitelist_rejects_arbitrary_payloads(self):
        import runtime_audit_worker as worker
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            worker.emit_progress('clone','REPORT_SECRET_NEVER_LOG',outputs='REPORT_SECRET_NEVER_LOG')
            worker.emit_progress('clone','begin',outputs='REPORT_SECRET_NEVER_LOG',reason='REPORT_SECRET_NEVER_LOG')
        self.assertNotIn('REPORT_SECRET',output.getvalue())
        rows=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['event'],'begin')

    def test_real_script_failure_keeps_original_acceptance_failure_and_bounded_diagnostics(self):
        import runtime_audit_worker as worker
        with tempfile.TemporaryDirectory() as directory:
            root,head,_,_=self.make_repository(directory)
            (root/worker.SCRIPTS[0]).write_text('raise RuntimeError("REPORT_SECRET_NEVER_LOG")\n')
            self.git(root,'add',worker.SCRIPTS[0]);self.git(root,'-c','user.name=test','-c','user.email=test@example.com','commit','-qm','failing audit')
            result_path=Path(directory)/'result.json';logpath=Path(directory)/'failed.log'
            with logpath.open('w') as log:
                done=subprocess.run([sys.executable,worker.__file__,str(root),self.git(root,'rev-parse','HEAD'),str(result_path)],stdout=log,stderr=log,timeout=10)
            self.assertEqual(done.returncode,0)
            result=json.loads(result_path.read_text());self.assertEqual(result['status'],'failed')
            with self.assertRaises(ValueError):worker.accept(root,result)
            self.assertTrue(all(not (root/p).exists() for p in worker.OUTPUTS))
            text=logpath.read_text();self.assertNotIn('REPORT_SECRET',text)
            rows=[json.loads(line) for line in text.splitlines()]
            self.assertTrue(any(r['phase']==worker.SCRIPTS[0] and r['event']=='end' and r.get('status')=='error' for r in rows))
            self.assertFalse(any(r['phase'] in worker.SCRIPTS[1:] for r in rows))
            self.assertTrue(any(r['phase']=='result' and r['event']=='end' and r.get('result_status')=='failed' for r in rows))


if __name__ == '__main__': unittest.main()

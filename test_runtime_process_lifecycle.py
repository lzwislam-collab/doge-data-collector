"""Real, bounded local process fixtures. No market/network/publisher is used."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


HERE = Path(__file__).resolve().parent


@unittest.skipUnless(os.name == 'posix', 'production process-group contract is POSIX')
class ProcessLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='runtime-lifecycle-test-')
        self.root = Path(self.temporary.name)
        self.drivers = []

    def tearDown(self):
        # An outer safety net also applies during RED and failed assertions.
        groups = {p.pid for p in self.drivers}
        for path in self.root.glob('*-pids.json'):
            for pid in json.loads(path.read_text()).values():
                try: groups.add(os.getpgid(pid))
                except ProcessLookupError: pass
        for group in groups:
            if group == os.getpgrp():
                continue
            try: os.killpg(group, signal.SIGKILL)
            except ProcessLookupError: pass
        for process in self.drivers:
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: pass
        self.temporary.cleanup()

    def fixture(self, name='command', *, parent_exits=False, seconds=5):
        child = self.root / (name+'-child.py')
        child.write_text(textwrap.dedent(f'''\
            import os, signal, time
            from pathlib import Path
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            root = Path({str(self.root)!r})
            (root/{(name+'-child-ready')!r}).touch()
            until = time.monotonic()+{seconds!r}
            while time.monotonic()<until:
                with (root/{(name+'-heartbeat')!r}).open('a') as stream:
                    stream.write('tick\\n');stream.flush()
                time.sleep(.02)
            '''))
        parent = self.root / (name+'-parent.py')
        parent.write_text(textwrap.dedent(f'''\
            import json, os, signal, subprocess, sys, time
            from pathlib import Path
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            root = Path({str(self.root)!r})
            child = subprocess.Popen([sys.executable, {str(child)!r}])
            until = time.monotonic()+2
            while not (root/{(name+'-child-ready')!r}).exists():
                if time.monotonic()>until: raise RuntimeError('fixture child did not start')
                time.sleep(.01)
            (root/{(name+'-pids.json')!r}).write_text(json.dumps({{'parent':os.getpid(),'child':child.pid}}))
            print('partial stdout',flush=True)
            print('Authorization: Bearer PROCESS_TEST_SECRET',file=sys.stderr,flush=True)
            (root/{(name+'-ready')!r}).touch()
            if not {parent_exits!r}: time.sleep({seconds!r})
            '''))
        return parent

    def start_driver(self, source):
        path = self.root / ('driver-'+str(len(self.drivers))+'.py')
        path.write_text(textwrap.dedent(source))
        logpath = self.root / ('driver-'+str(len(self.drivers))+'.log')
        env = dict(os.environ, PYTHONPATH=str(HERE)+os.pathsep+os.environ.get('PYTHONPATH', ''))
        with logpath.open('w') as log:
            process = subprocess.Popen([sys.executable, str(path)], cwd=HERE, env=env,
                                       stdout=log, stderr=log, start_new_session=True)
        self.drivers.append(process)
        return process, logpath

    def complete(self, process, logpath, timeout=12):
        try: process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.fail('outer fixture timeout: '+logpath.read_text()[-3000:])
        return logpath.read_text()

    def wait_ready(self, name, process, logpath):
        deadline = time.monotonic()+5
        while not (self.root/(name+'-ready')).exists():
            if process.poll() is not None or time.monotonic()>deadline:
                self.fail('fixture did not become ready: '+logpath.read_text()[-3000:])
            time.sleep(.01)

    def assert_no_writer(self, name):
        path = self.root/(name+'-heartbeat')
        before = path.read_bytes()
        time.sleep(.15)
        self.assertEqual(path.read_bytes(), before, 'a descendant still writes after cleanup')
        pids = json.loads((self.root/(name+'-pids.json')).read_text())
        for pid in pids.values():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            # Orphans may briefly remain zombies awaiting the OS reaper.
            stat = Path('/proc')/str(pid)/'stat'
            if stat.exists():
                self.assertEqual(stat.read_text().rsplit(')', 1)[1].split()[0], 'Z',
                                 'a live descendant survived cleanup')
            else:
                self.fail('process still exists without an observable terminated state')

    def command_driver(self, fixture):
        return f'''
            import json, subprocess, sys, time
            from pathlib import Path
            import runtime_deadline_service as runtime
            root=Path({str(self.root)!r})
            runtime.COMMAND_TIMEOUT_SECONDS=.15
            adapter=object.__new__(runtime.LiveAdapter);adapter.root=root
            started=time.monotonic()
            try:
                output=adapter.command(sys.executable,{str(fixture)!r})
                result={{'type':None,'output':output}}
            except BaseException as exc:
                result={{'type':type(exc).__name__,'details':runtime.exception_details(exc)}}
            result['seconds']=time.monotonic()-started
            (root/'result.json').write_text(json.dumps(result))
            '''

    def test_timeout_stops_term_resistant_writer_and_keeps_safe_partial_diagnostics(self):
        fixture = self.fixture()
        process, log = self.start_driver(self.command_driver(fixture))
        self.complete(process, log)
        result = json.loads((self.root/'result.json').read_text())
        self.assertEqual(result['type'], 'TimeoutExpired', 'synchronous command has no timeout')
        self.assertLess(result['seconds'], 4)
        self.assertEqual(result['details']['stdout'], 'partial stdout\n')
        self.assertIn('[REDACTED]', result['details']['stderr'])
        self.assertNotIn('PROCESS_TEST_SECRET', json.dumps(result['details']))
        self.assertEqual(result['details']['timeout_seconds'], .15)
        self.assertTrue(result['details']['process_cleanup']['kill_requested'])
        self.assert_no_writer('command')

    def test_parent_exit_cannot_hide_descendant_holding_captured_pipes(self):
        fixture = self.fixture(parent_exits=True)
        process, log = self.start_driver(self.command_driver(fixture))
        self.complete(process, log)
        result = json.loads((self.root/'result.json').read_text())
        self.assertEqual(result['type'], 'TimeoutExpired', 'an exited parent left pipes open')
        self.assertLess(result['seconds'], 4)
        self.assert_no_writer('command')

    def test_nonzero_command_retains_exit_code_and_both_output_streams(self):
        source = self.root/'failure.py'
        source.write_text("import sys\nprint('partial stdout')\nprint('failure detail',file=sys.stderr)\nraise SystemExit(7)\n")
        process, log = self.start_driver(self.command_driver(source))
        self.complete(process, log)
        result = json.loads((self.root/'result.json').read_text())
        self.assertEqual(result['type'], 'CalledProcessError')
        self.assertEqual(result['details']['returncode'], 7)
        self.assertEqual(result['details']['stdout'], 'partial stdout\n')
        self.assertEqual(result['details']['stderr'], 'failure detail\n')

    def audit_driver(self, fixture, *, wait_for_parent):
        return f'''
            import contextlib, json, subprocess, sys, tempfile, time
            from pathlib import Path
            import runtime_audit_worker as worker
            root=Path({str(self.root)!r})
            auditor=worker.Auditor(root)
            auditor.directory=tempfile.TemporaryDirectory(prefix='audit-close-fixture-')
            result_path=Path(auditor.directory.name)/'result.json'
            result_path.write_text('{{"status":"failed","error":"PRIVATE_RESULT"}}')
            auditor.process=subprocess.Popen([sys.executable,{str(fixture)!r}],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            process=auditor.process
            deadline=time.monotonic()+3
            while not (root/'audit-ready').exists():
                if time.monotonic()>deadline:raise RuntimeError('fixture not ready')
                time.sleep(.01)
            if {wait_for_parent!r}:process.wait(timeout=2)
            started=time.monotonic()
            with (root/'cleanup.log').open('w') as log,contextlib.redirect_stdout(log):auditor.close()
            (root/'result.json').write_text(json.dumps({{'seconds':time.monotonic()-started,
                'returncode':process.returncode,'directory_removed':not result_path.parent.exists()}}))
            '''

    def check_audit_cleanup(self, *, parent_exits):
        fixture = self.fixture('audit', parent_exits=parent_exits)
        process, log = self.start_driver(self.audit_driver(fixture, wait_for_parent=parent_exits))
        self.complete(process, log)
        result = json.loads((self.root/'result.json').read_text())
        self.assertLess(result['seconds'], 4, 'auditor did not escalate termination')
        self.assertTrue(result['directory_removed'])
        self.assert_no_writer('audit')
        rows = [json.loads(line) for line in (self.root/'cleanup.log').read_text().splitlines()]
        final = next(row for row in rows if row['event']=='before_cleanup')
        self.assertTrue(final.get('kill_requested'), 'termination escalation was not recorded')
        self.assertEqual(final['result_status'], 'failed')
        self.assertEqual(final['result_sha256'], hashlib.sha256(
            b'{"status":"failed","error":"PRIVATE_RESULT"}').hexdigest())
        self.assertNotIn('PRIVATE_RESULT', (self.root/'cleanup.log').read_text())

    def test_auditor_close_kills_term_resistant_group_before_removing_diagnostics(self):
        self.check_audit_cleanup(parent_exits=False)

    def test_auditor_close_cleans_descendant_after_worker_already_exited(self):
        self.check_audit_cleanup(parent_exits=True)

    def main_driver(self, command, audit, *, cleanup_failure=False, command_timeout=240, spawn_cancel=None):
        return f'''
            import json, os, signal, subprocess, sys, tempfile, time
            from pathlib import Path
            import runtime_deadline_service as runtime
            import runtime_audit_worker as worker
            root=Path({str(self.root)!r})
            runtime.COMMAND_TIMEOUT_SECONDS={command_timeout!r}
            class Adapter(runtime.LiveAdapter):
                def __init__(self, root):
                    self.root=root;self.auditor=worker.Auditor(root)
                    if {cleanup_failure!r}:
                        def failed_close():raise OSError('cleanup fixture failure')
                        self.auditor.close=failed_close
                    elif {spawn_cancel!r}!='audit':
                        self.auditor.directory=tempfile.TemporaryDirectory()
                        self.auditor.process=subprocess.Popen([sys.executable,{str(audit)!r}],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
                def python(self,*args,env=None):return ''
                def command(self,*args,env=None):
                    if args[:2]==('git','config'):return ''
                    return super().command(*args,env=env)
                def action(self,name):
                    if name=='publish':(root/'published').write_text('unsafe publication')
                    return {{'status':'ok'}}
            def run(root,adapter,*args,**kwargs):
                if {cleanup_failure!r}:
                    raise subprocess.TimeoutExpired(('fixture',),240,output=b'original partial output')
                if {spawn_cancel!r}=='audit':
                    for relative in worker.INPUTS:
                        path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
                        path.write_text('offline fixture input')
                    worker.subprocess.check_output=lambda *args,**kwargs:b'a'*40
                    adapter.auditor.poll()
                else:
                    adapter.command(sys.executable,{str(command)!r})
                return {{'status':'CAPTURE_LOOP_COMPLETED','fatal':False}}
            if {spawn_cancel!r}:
                real_popen=subprocess.Popen
                def interrupted_start(args,*positional,**kwargs):
                    is_audit=len(args)>1 and args[1]==worker.__file__
                    if is_audit:
                        process=real_popen([sys.executable,{str(audit)!r}],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
                    else:
                        process=real_popen(args,*positional,**kwargs)
                    selected=(is_audit and {spawn_cancel!r}=='audit') or (len(args)>1
                        and args[1]=={str(command)!r} and {spawn_cancel!r}=='command')
                    if selected:
                        name='audit' if is_audit else 'command'
                        deadline=time.monotonic()+3
                        while not (root/(name+'-ready')).exists():
                            if time.monotonic()>deadline:raise RuntimeError('spawn fixture not ready')
                            time.sleep(.01)
                        # A real process exists, but the caller has not yet saved its handle.
                        os.kill(os.getpid(),signal.SIGTERM)
                    return process
                subprocess.Popen=interrupted_start
            runtime.LiveAdapter=Adapter;runtime.run=run
            runtime.__file__=str(root/'runtime_deadline_service.py')
            os.environ['GITHUB_OUTPUT']=str(root/'github-output')
            sys.argv=['runtime_deadline_service.py','--duration-seconds','1']
            code=runtime.main()
            (root/'main-return.json').write_text(json.dumps({{'code':code}}))
            raise SystemExit(code)
            '''

    def check_cancellation(self, first_signal, *, repeat=False, during_timeout_cleanup=False):
        command = self.fixture(seconds=10)
        audit = self.fixture('audit', seconds=10)
        process, log = self.start_driver(self.main_driver(command, audit,
            command_timeout=.15 if during_timeout_cleanup else 240))
        self.wait_ready('command', process, log)
        self.wait_ready('audit', process, log)
        if during_timeout_cleanup: time.sleep(.35)
        os.kill(process.pid, first_signal)
        if repeat:
            deadline=time.monotonic()+5
            count=0
            while process.poll() is None and time.monotonic()<deadline:
                time.sleep(.05)
                try:os.kill(process.pid, signal.SIGINT if count%2 else signal.SIGTERM)
                except ProcessLookupError:break
                count+=1
        self.complete(process, log)
        receipt = self.root/'data/runtime_deadline_v1/latest.json'
        self.assertTrue(receipt.exists(), 'cancellation lost the fatal local receipt')
        value = json.loads(receipt.read_text())
        self.assertTrue(value['fatal'])
        self.assertEqual(value['status'], 'DEGRADED')
        self.assertEqual(value.get('fatal_reason'), 'RUNTIME_CANCELLED')
        self.assertEqual(value.get('stdout'), 'partial stdout\n')
        self.assertIn('[REDACTED]', value.get('stderr', ''))
        self.assertNotIn('PROCESS_TEST_SECRET', json.dumps(value))
        if during_timeout_cleanup:
            self.assertTrue(value['original_failure']['error'].startswith('TimeoutExpired:'))
            self.assertEqual(value['original_failure']['stdout'], 'partial stdout\n')
            self.assertNotIn('PROCESS_TEST_SECRET', json.dumps(value))
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse((self.root/'published').exists(), 'cancelled service still published')
        self.assertEqual((self.root/'github-output').read_text().splitlines()[-1], 'fatal=true')
        self.assert_no_writer('command')
        self.assert_no_writer('audit')

    def test_sigterm_cleans_writers_and_saves_fatal_receipt_without_publication(self):
        self.check_cancellation(signal.SIGTERM)

    def test_keyboard_interrupt_cleans_writers_and_saves_fatal_receipt_without_publication(self):
        self.check_cancellation(signal.SIGINT)

    def test_repeated_cancellation_cannot_interrupt_group_cleanup_or_fatal_receipt(self):
        self.check_cancellation(signal.SIGTERM, repeat=True)

    def test_first_sigterm_during_timeout_cleanup_is_deferred_without_losing_original_error(self):
        self.check_cancellation(signal.SIGTERM, during_timeout_cleanup=True)

    def test_first_sigint_during_timeout_cleanup_is_deferred_without_losing_original_error(self):
        self.check_cancellation(signal.SIGINT, during_timeout_cleanup=True)

    def check_spawn_cancellation(self, stage):
        command=self.fixture(seconds=10)
        audit=self.fixture('audit', seconds=10)
        process,log=self.start_driver(self.main_driver(command,audit,spawn_cancel=stage))
        self.complete(process,log)
        receipt=json.loads((self.root/'data/runtime_deadline_v1/latest.json').read_text())
        self.assertEqual(receipt.get('fatal_reason'), 'RUNTIME_CANCELLED')
        self.assertFalse((self.root/'published').exists())
        self.assert_no_writer('audit')
        if stage=='command': self.assert_no_writer('command')

    def test_cancellation_during_command_handle_acquisition_cleans_the_real_group(self):
        self.check_spawn_cancellation('command')

    def test_cancellation_during_audit_handle_acquisition_cleans_the_real_group(self):
        self.check_spawn_cancellation('audit')

    def test_cleanup_failure_keeps_original_failure_and_still_saves_fatal_receipt(self):
        process, log = self.start_driver(self.main_driver('', '', cleanup_failure=True))
        self.complete(process, log)
        receipt=self.root/'data/runtime_deadline_v1/latest.json'
        self.assertTrue(receipt.exists(), 'cleanup failure hid the original failure receipt')
        value=json.loads(receipt.read_text())
        self.assertTrue(value['fatal'])
        self.assertTrue(value['error'].startswith('TimeoutExpired:'))
        self.assertEqual(value['stdout'], 'original partial output')
        self.assertTrue(value['cleanup_error']['error'].startswith('OSError:'))
        self.assertFalse((self.root/'published').exists())


class InterruptedPublicationTests(unittest.TestCase):
    def check_due_failure(self, error):
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace
        import runtime_deadline_service as runtime
        from archive_window_budget import WorkflowBudgetExhausted
        actions=[]
        def action(name):
            actions.append(name)
            if name=='watch': raise error
            return {'status':'ok'}
        adapter=SimpleNamespace(action=action, state=lambda:([
            {'forecast_id':'due-fixture','target_utc':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}],{}))
        with tempfile.TemporaryDirectory() as directory:
            result=runtime.finish_due_on_budget_exhaustion(Path(directory), adapter, WorkflowBudgetExhausted({}))
            self.assertTrue(result['fatal'])
            self.assertEqual(result['due_only_receipt']['errors'][0]['stage'], 'watch')
            self.assertEqual(actions,['watch'], 'interrupted writer was followed by publication')

    def test_due_only_timeout_preserves_receipt_without_publishing(self):
        self.check_due_failure(subprocess.TimeoutExpired(('fixture',),240,output='partial writer output'))

    def test_due_only_cleanup_failure_preserves_receipt_without_publishing(self):
        error=RuntimeError('fixture cleanup failure')
        error.process_cleanup={'cleanup_error_type':'PermissionError'}
        self.check_due_failure(error)


if __name__ == '__main__':
    unittest.main()

"""Archive timeout owns its Linux worker; no market/network/publisher is used."""
import importlib.util
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import archive_capture_bridge as bridge

HERE = Path(__file__).resolve().parent
LINUX = sys.platform == 'linux' and hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal')


class TimeoutGateTests(unittest.TestCase):
    def test_timeout_retains_failure_and_cleanup_evidence_without_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = root/'control'; control.mkdir()
            (control/'started.json').write_text(json.dumps({
                'pid': 12345, 'qualified_stop_required': True,
                'process_identity': {'pid': 12345}}))
            boundary = types.SimpleNamespace(cancel=lambda identity: {
                'cleanup_verified': False, 'reason': 'identity_not_verified'})
            with patch.dict(sys.modules, {'archive_process': boundary}), \
                    patch.object(bridge, 'trusted'), \
                    patch.object(bridge.time, 'monotonic', side_effect=[0, 421]):
                with self.assertRaisesRegex(TimeoutError, 'has not drained'):
                    bridge.stop(root, root/'snapshot', control)
            self.assertTrue((control/'shutdown.json').exists(),
                            'timeout lost the bounded cleanup outcome')
            self.assertFalse(json.loads((control/'shutdown.json').read_text())['cleanup_verified'])
            self.assertFalse(list(root.glob('data/runtime_deadline_v1/archive_bridge_*.json')))
            self.assertFalse((control/'completed.json').exists())


    def test_cancellation_after_cleanup_keeps_original_timeout_diagnostics(self):
        from contextlib import contextmanager
        import runtime_process
        @contextmanager
        def interrupted_after_cleanup():
            yield
            raise runtime_process.RuntimeCancelled(signal.SIGTERM)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);control=root/'control';control.mkdir()
            (control/'started.json').write_text(json.dumps({'pid':12345,'qualified_stop_required':True}))
            boundary=types.SimpleNamespace(cancel=lambda identity:{
                'cleanup_verified':False,'reason':'identity_not_verified'})
            with patch.dict(sys.modules,{'archive_process':boundary}), \
                    patch.object(bridge,'trusted'), \
                    patch.object(bridge.time,'monotonic',side_effect=[0,421]), \
                    patch.object(runtime_process,'defer_cancellation',interrupted_after_cleanup):
                with self.assertRaisesRegex(TimeoutError,'has not drained') as caught:
                    bridge.stop(root,root/'snapshot',control)
            self.assertFalse(caught.exception.process_cleanup['cleanup_verified'])
            self.assertTrue((control/'shutdown.json').exists())

    def test_cleanup_receipt_failure_cannot_replace_original_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);control=root/'control';control.mkdir()
            (control/'started.json').write_text(json.dumps({'pid':12345,'qualified_stop_required':True}))
            boundary=types.SimpleNamespace(cancel=lambda identity:{
                'cleanup_verified':False,'reason':'identity_not_verified'})
            with patch.dict(sys.modules,{'archive_process':boundary}), \
                    patch.object(bridge,'trusted'), \
                    patch.object(bridge.time,'monotonic',side_effect=[0,421]), \
                    patch.object(bridge,'atomic_json',side_effect=OSError('offline receipt disk failure')):
                with self.assertRaisesRegex(TimeoutError,'has not drained') as caught:
                    bridge.stop(root,root/'snapshot',control)
            self.assertEqual(caught.exception.process_cleanup['receipt_error_type'],'OSError')


@unittest.skipUnless(LINUX, 'archive descendant ownership requires Linux procfs and pidfds')
class LinuxArchiveOwnershipTests(unittest.TestCase):
    cooperative_elapsed = []

    @classmethod
    def tearDownClass(cls):
        if cls.cooperative_elapsed:
            print(json.dumps({"kind":"archive_cleanup_fixture_timings",
                "scope":"offline_real_runtime_cooperative_cancellation",
                "observations":len(cls.cooperative_elapsed),
                "minimum_seconds":min(cls.cooperative_elapsed),
                "maximum_seconds":max(cls.cooperative_elapsed)}),flush=True)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='archive-ownership-')
        self.root = Path(self.temporary.name)
        self.processes = []

    def tearDown(self):
        # Covers RED and failed assertions; never touch a group containing this test.
        for path in self.root.glob('*-pids.json'):
            for pid in json.loads(path.read_text()).values():
                try:
                    pgid = os.getpgid(pid)
                    if pgid != os.getpgrp(): os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError: pass
        for process in self.processes:
            if process.poll() is None:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            process.wait(timeout=3)
        self.temporary.cleanup()

    def trusted(self):
        return patch.dict(os.environ, {'GITHUB_ACTIONS': 'true',
            'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': bridge.REPO
            if hasattr(bridge, 'REPO') else 'lzwislam-collab/doge-data-collector'})

    def resistant(self, name):
        path = self.root/(name+'.py')
        path.write_text(
            'import json, os, signal, subprocess, sys, time\nfrom pathlib import Path\n'
            'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
            f'root=Path({str(self.root)!r})\n'
            f'child=subprocess.Popen([sys.executable,"-c","import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"])\n'
            f'(root/{(name+"-pids.json")!r}).write_text(json.dumps({{"parent":os.getpid(),"child":child.pid}}))\n'
            'while True:\n'
            f'    with (root/{(name+"-heartbeat")!r}).open("a") as stream: stream.write("tick\\n");stream.flush()\n'
            '    time.sleep(.02)\n')
        return path

    def ready(self, names, process):
        deadline = time.monotonic()+8
        while not all((self.root/(name+'-heartbeat')).exists() for name in names):
            if process.poll() is not None or time.monotonic() >= deadline:
                self.fail('offline archive fixture did not become ready')
            time.sleep(.02)

    def assert_stopped(self, names):
        before = {name: (self.root/(name+'-heartbeat')).read_bytes() for name in names}
        time.sleep(.12)
        self.assertEqual(before, {name: (self.root/(name+'-heartbeat')).read_bytes() for name in names},
                         'an owned writer continued after archive timeout returned')
        for name in names:
            for pid in json.loads((self.root/(name+'-pids.json')).read_text()).values():
                path = Path('/proc')/str(pid)/'stat'
                if path.exists():
                    state = path.read_text().rsplit(')',1)[1].split()[0]
                    self.assertEqual(state, 'Z', 'live detached descendant survived archive cleanup')

    def timeout(self, control):
        real_clock = time.monotonic
        ticks = iter([0, 421])
        def clock():
            return next(ticks, real_clock())
        with self.trusted(), patch.object(bridge.time, 'monotonic', side_effect=clock):
            with self.assertRaisesRegex(TimeoutError, 'has not drained'):
                bridge.stop(self.root, self.root/'snapshot', control)

    def test_timeout_cancels_real_service_and_detached_resistant_command_and_auditor(self):
        command, auditor = self.resistant('command'), self.resistant('audit')
        for name in ('archive_capture_bridge.py','archive_process.py','runtime_process.py','runtime_audit_worker.py'):
            shutil.copyfile(HERE/name, self.root/name)
        actual = HERE/'runtime_deadline_service.py'
        # I/O producers are fixtures; main cancellation, command cleanup, auditor.close,
        # final unsafe receipt, and bridge worker control flow are the real code.
        (self.root/'runtime_deadline_service.py').write_text(f'''
import importlib.util, json, os, subprocess, sys, tempfile
from pathlib import Path
import runtime_audit_worker as worker
spec=importlib.util.spec_from_file_location("real_runtime",{str(actual)!r})
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
class Adapter(runtime.LiveAdapter):
    def __init__(self,root):
        self.root=root;self.auditor=worker.Auditor(root)
        self.auditor.directory=tempfile.TemporaryDirectory()
        self.auditor.process=subprocess.Popen([sys.executable,{str(auditor)!r}],start_new_session=True)
    def python(self,*args,env=None):return ""
    def command(self,*args,env=None):
        if args[:2]==("git","config"):return ""
        return super().command(*args,env=env)
    def action(self,name):
        if name=="publish":(self.root/"unexpected-publication").touch()
        return {{"status":"ok"}}
def run(root,adapter,*args,**kwargs):
    adapter.command(sys.executable,{str(command)!r})
    return {{"fatal":False,"status":"CAPTURE_LOOP_COMPLETED"}}
runtime.LiveAdapter=Adapter;runtime.run=run
runtime.__file__=__file__
raise SystemExit(runtime.main())
''')
        control=self.root/'control'; control.mkdir()
        env=dict(os.environ, GITHUB_ACTIONS='true', GITHUB_REF='refs/heads/main',
                 GITHUB_REPOSITORY='lzwislam-collab/doge-data-collector',
                 GITHUB_OUTPUT=str(control/'runtime.outputs'))
        process=subprocess.Popen([sys.executable,str(self.root/'archive_capture_bridge.py'),
            'worker','--control',str(control)],cwd=self.root,env=env,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        self.processes.append(process)
        self.ready(['command','audit'],process)
        import archive_process
        (control/'started.json').write_text(json.dumps({'pid':process.pid,
            'qualified_stop_required':True,'started_at_utc':datetime.now(timezone.utc).isoformat(),
            'process_identity':archive_process.identity(process.pid)}))
        started=time.monotonic()
        self.timeout(control)
        self.assertLess(time.monotonic()-started, 26)
        self.assert_stopped(['command','audit'])
        receipt=json.loads((self.root/'data/runtime_deadline_v1/latest.json').read_text())
        self.assertTrue(receipt['fatal'])
        self.assertEqual((control/'runtime.outputs').read_text().splitlines()[-1], 'fatal=true')
        self.assertEqual(receipt['fatal_reason'],'RUNTIME_CANCELLED')
        self.assertFalse((self.root/'unexpected-publication').exists())
        self.assertNotEqual(json.loads((control/'completed.json').read_text())['exit_code'],0)
        shutdown=json.loads((control/'shutdown.json').read_text())
        self.cooperative_elapsed.append(shutdown['elapsed_seconds'])
        self.assertTrue(shutdown['cleanup_verified'],shutdown)

    def test_cancelled_wrapper_cannot_complete_while_its_runtime_is_still_alive(self):
        import archive_process
        command,auditor=self.resistant('command'),self.resistant('audit')
        (self.root/'runtime_deadline_service.py').write_text(
            'import signal,subprocess,sys,time\n'
            'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
            f'subprocess.Popen([sys.executable,{str(command)!r}],start_new_session=True)\n'
            f'subprocess.Popen([sys.executable,{str(auditor)!r}],start_new_session=True)\n'
            'while True: time.sleep(.02)\n')
        control=self.root/'control';control.mkdir()
        driver=self.root/'wrapper-driver.py'
        # Accelerate only a hypothetical old 15s wait. The real Popen/wait/TERM
        # and stubborn runtime remain; an unbounded owning wait is left alone.
        driver.write_text(f'''
import subprocess,sys
from pathlib import Path
sys.path.insert(0,{str(HERE)!r})
import archive_capture_bridge as bridge
real_popen=subprocess.Popen
def popen(*args,**kwargs):
    process=real_popen(*args,**kwargs)
    real_wait=process.wait
    def wait(timeout=None):
        return real_wait(timeout=.05 if timeout==15 else timeout)
    process.wait=wait
    return process
bridge.subprocess.Popen=popen
bridge.worker(Path({str(self.root)!r}),Path({str(control)!r}))
''')
        env=dict(os.environ,GITHUB_ACTIONS='true',GITHUB_REF='refs/heads/main',
                 GITHUB_REPOSITORY='lzwislam-collab/doge-data-collector')
        process=subprocess.Popen([sys.executable,str(driver)],env=env,start_new_session=True)
        self.processes.append(process)
        self.ready(['command','audit'],process)
        owner=archive_process.identity(process.pid)
        (control/'started.json').write_text(json.dumps({'pid':process.pid,
            'qualified_stop_required':True,'process_identity':owner}))
        os.kill(process.pid,signal.SIGTERM)
        time.sleep(.25)
        self.assertIsNone(process.poll(),'wrapper abandoned its live runtime')
        self.assertFalse((control/'completed.json').exists(),
                         'wrapper recorded completion before reaping its runtime')
        with patch.object(archive_process,'COOPERATIVE_SECONDS',.15):
            self.timeout(control)
        self.assert_stopped(['command','audit'])
        shutdown=json.loads((control/'shutdown.json').read_text())
        self.assertFalse(shutdown['cleanup_verified'])
        self.assertTrue(shutdown['registered_processes_exit_verified'])

    def test_hard_stop_cleans_registered_detached_groups_and_preserves_unrelated_process(self):
        import archive_process
        command,auditor=self.resistant('command'),self.resistant('audit')
        worker=self.root/'stubborn-worker.py'
        worker.write_text('import signal,subprocess,sys,time\n'
            'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
            f'subprocess.Popen([sys.executable,{str(command)!r}],start_new_session=True)\n'
            f'subprocess.Popen([sys.executable,{str(auditor)!r}],start_new_session=True)\n'
            'while True: time.sleep(.02)\n')
        process=subprocess.Popen([sys.executable,str(worker)],start_new_session=True)
        unrelated=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
        self.processes += [process,unrelated]
        self.ready(['command','audit'],process)
        owner=archive_process.identity(process.pid)
        with patch.object(archive_process,'COOPERATIVE_SECONDS',.15):
            result=archive_process.cancel(owner)
        self.assertFalse(result['cleanup_verified'],result)
        self.assertTrue(result['registered_processes_exit_verified'],result)
        self.assertTrue(result['kill_requested'])
        self.assert_stopped(['command','audit'])
        self.assertIsNone(unrelated.poll(),'archive cleanup signalled an unrelated process')

    def test_proc_discovery_budget_exhaustion_cannot_hang_or_signal_unproved_owner(self):
        import itertools
        import archive_process
        process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
        self.processes.append(process)
        owner=archive_process.identity(process.pid)
        started=time.monotonic()
        # Model a procfs listing that keeps growing. The real pidfd/owner
        # validation and the cleanup deadline run; only directory enumeration is replaced.
        with patch.object(archive_process,'FREEZE_SECONDS',.05), \
                patch.object(archive_process.Path,'iterdir',
                             return_value=itertools.repeat(Path('/proc/not-a-process'))):
            result=archive_process.cancel(owner)
        self.assertLess(time.monotonic()-started,1)
        self.assertFalse(result['cleanup_verified'])
        self.assertEqual(result.get('error_type'),'TimeoutError')
        self.assertFalse(result['termination_requested'])
        self.assertIsNone(process.poll())

    def test_pid_reuse_after_discovery_never_acquires_or_signals_unrelated_process(self):
        import archive_process
        process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
        unrelated=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
        self.processes += [process,unrelated]
        owner=archive_process.identity(process.pid)
        real_process=archive_process._process
        real_open=os.pidfd_open
        replacement=real_process(unrelated.pid)
        # The procfs enumeration saw the former child; by acquisition this PID
        # names the real unrelated process. No actual PID reuse is forced.
        former=dict(replacement,ppid=process.pid,start_ticks=replacement['start_ticks']+'0')
        observations=[former]
        opened=[]
        def observed(pid):
            if pid==unrelated.pid and observations: return observations.pop()
            return real_process(pid)
        def acquire(pid,*args,**kwargs):
            opened.append(pid)
            return real_open(pid,*args,**kwargs)
        with patch.object(archive_process,'_process',side_effect=observed), \
                patch.object(archive_process.os,'pidfd_open',side_effect=acquire), \
                patch.object(archive_process.Path,'iterdir',
                    side_effect=lambda: iter([Path('/proc')/str(process.pid),
                                              Path('/proc')/str(unrelated.pid)])), \
                patch.object(archive_process,'COOPERATIVE_SECONDS',.05):
            result=archive_process.cancel(owner)
        self.assertIsNone(unrelated.poll(),'PID reuse after discovery signalled an unrelated process')
        self.assertNotIn(unrelated.pid,opened,'a replacement PID was acquired without matching discovery identity')
        self.assertTrue(result['registered_processes_exit_verified'],result)

    def test_missing_or_mismatched_start_identity_never_signals_a_live_process(self):
        import archive_process
        process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
        self.processes.append(process)
        owner=archive_process.identity(process.pid)
        for invalid in (None, {'pid':process.pid},
                        dict(owner,start_ticks=owner['start_ticks']+'0'),
                        dict(owner,pgid=os.getpgrp())):
            result=archive_process.cancel(invalid)
            self.assertFalse(result['cleanup_verified'])
            self.assertIsNone(process.poll(),'unverified identity signalled a live process')


if __name__ == '__main__': unittest.main()

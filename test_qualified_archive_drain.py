import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch, Mock

import runtime_deadline_service as runtime
import runtime_audit_worker as audit
from test_runtime_deadline_service import Adapter, Clock, START


class QualifiedDrainTests(unittest.TestCase):
    def test_stop_waits_for_a_formal_book_after_pending_audit(self):
        clock=Clock(); adapter=Adapter(clock); polls=[]
        original=adapter.action
        adapter.can_stop=lambda:len(polls)>=3
        def action(name):
            if name=='source_audit':
                adapter.calls.append(name);polls.append(name)
                return {'status':'ready' if len(polls)>=3 else 'pending'}
            return original(name)
        adapter.action=action
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);stop=root/'stop'
            def sleep(seconds): stop.touch();clock.sleep(seconds)
            out=runtime.run(root,adapter,300,clock,sleep,stop_file=stop,require_qualified_stop=True)
            self.assertEqual(out['cycles'],3)
            self.assertTrue(out['qualified_stop_satisfied'])
            self.assertEqual(out['status'],'CAPTURE_LOOP_COMPLETED')

    def test_unqualified_stop_at_runtime_limit_is_not_success(self):
        clock=Clock();adapter=Adapter(clock);adapter.can_stop=lambda:False
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);stop=root/'stop';stop.touch()
            out=runtime.run(root,adapter,121,clock,clock.sleep,stop_file=stop,require_qualified_stop=True)
            self.assertEqual(out['status'],'DEGRADED')
            self.assertFalse(out['qualified_stop_satisfied'])

    def test_source_verified_flag_alone_is_insufficient_for_stop(self):
        row={'quote_utc':START.isoformat(),'captured_at_utc':(START+timedelta(seconds=1)).isoformat(),
             'source_items':[{'source_verified':True,'paired_audit_verified':False,
                 'source_error':None,'target_utc':(START+timedelta(hours=1)).isoformat(),'observed_public_utc':(START-timedelta(minutes=1)).isoformat()}]}
        self.assertFalse(runtime.qualified_capture(row, {'requested_at_utc': START.isoformat()}))
        row['source_items'][0]['paired_audit_verified']=True
        self.assertTrue(runtime.qualified_capture(row, {'requested_at_utc': START.isoformat()}))
        row['source_items'][0]['observed_public_utc']=START.isoformat()
        self.assertFalse(runtime.qualified_capture(row, {'requested_at_utc': START.isoformat()}))
        row['source_items'][0]['observed_public_utc']=(START-timedelta(minutes=1)).isoformat()
        self.assertFalse(runtime.qualified_capture(row, {'requested_at_utc': (START-timedelta(minutes=2)).isoformat()}))
        row['source_items'][0]['target_utc']=START.isoformat()
        self.assertFalse(runtime.qualified_capture(row, {'requested_at_utc': START.isoformat()}))
        row['source_items'][0]['observed_public_utc']=(START+timedelta(minutes=1)).isoformat()
        self.assertFalse(runtime.qualified_capture(row, {'requested_at_utc': START.isoformat()}))

    def test_new_input_terminates_obsolete_read_only_auditor_before_restarting(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            for p in audit.INPUTS:
                (root/p).parent.mkdir(parents=True,exist_ok=True);(root/p).write_text('original')
            worker=audit.Auditor(root)
            worker.inputs=audit.fingerprint(root)
            worker.directory=tempfile.TemporaryDirectory()
            worker.process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],start_new_session=True)
            old=worker.process;worker.started=time.monotonic()
            (root/audit.INPUTS[0]).write_text('new genuine issue')
            fake=Mock();fake.poll.return_value=0;fake.returncode=0
            try:
                with patch.object(audit.subprocess,'check_output',return_value=b'a'*40),patch.object(audit.subprocess,'Popen',return_value=fake):
                    result=worker.poll()
                self.assertIsNotNone(old.poll(),'obsolete worker still running')
                self.assertEqual(result['status'],'pending')
            finally:
                if old.poll() is None:old.terminate();old.wait()
                worker.close()


if __name__=='__main__':unittest.main()

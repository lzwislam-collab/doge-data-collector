import csv
import json
import tempfile
import os
import shutil
import subprocess
import sys
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import runtime_deadline_service as runtime
import p0_open_target_guard as guard
from test_runtime_deadline_service import Adapter, Clock, START


class ArchiveBridgeTests(unittest.TestCase):
    def test_graceful_stop_finishes_current_cycle_before_returning(self):
        clock = Clock(); adapter = Adapter(clock)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); stop = root/'stop'
            def sleep(seconds):
                stop.touch(); clock.sleep(seconds)
            out = runtime.run(root, adapter, 300, clock, sleep, stop_file=stop)
            self.assertEqual(out['cycles'], 1)
            self.assertEqual(adapter.calls[-1], 'witness')
            self.assertEqual(out['status'], 'CAPTURE_LOOP_COMPLETED')

    def test_archive_copy_is_independent_of_later_live_writes(self):
        import archive_capture_bridge as bridge
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = root/'data/ledger.jsonl'
            source.parent.mkdir(); source.write_bytes(b'old ledger\n')
            snapshot = root/'snapshot'
            bridge.freeze(root, snapshot)
            source.write_bytes(b'old ledger\nnew genuine observation\n')
            self.assertEqual((snapshot/'data/ledger.jsonl').read_bytes(), b'old ledger\n')
            bridge.verify(snapshot)

    def test_archive_tamper_is_detected(self):
        import archive_capture_bridge as bridge
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); (root/'data').mkdir()
            (root/'data/a').write_bytes(b'original')
            snapshot = root/'snapshot'; bridge.freeze(root, snapshot)
            (snapshot/'data/a').write_bytes(b'changed')
            with self.assertRaises(ValueError): bridge.verify(snapshot)

    def test_snapshot_cannot_overwrite_old_evidence(self):
        import archive_capture_bridge as bridge
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); snapshot = root/'snapshot'; snapshot.mkdir()
            (snapshot/'old').write_text('keep')
            with self.assertRaises(FileExistsError): bridge.freeze(root, snapshot)
            self.assertEqual((snapshot/'old').read_text(), 'keep')

    def fixture(self, root, target_minutes=2, issued=True):
        p = root/'data/runtime_deadline_v1/latest.json'; p.parent.mkdir(parents=True)
        p.write_text(json.dumps({'status':'CAPTURE_LOOP_COMPLETED', 'fatal':False,
                                'ended_at_utc':START.isoformat()}))
        p = root/'data/direction_v42/forward_registry.csv'; p.parent.mkdir(parents=True)
        row = {'forecast_id':'genuine', 'model_id':'frozen', 'horizon_hours':'1',
               'source_hour_start_utc':(START if issued else START-timedelta(hours=1)).isoformat(),
               'target_utc':(START+timedelta(minutes=target_minutes)).isoformat()}
        with p.open('w', newline='') as f:
            w = csv.DictWriter(f,fieldnames=list(row)); w.writeheader(); w.writerow(row)
        (p.parent/'forward_scores.csv').write_text('forecast_id,status\n')

    def trusted(self):
        return patch.dict('os.environ', {'GITHUB_ACTIONS':'true', 'GITHUB_REF':'refs/heads/main',
                           'GITHUB_REPOSITORY':runtime.REPO,'DOGE_TAIL_CAPTURE_BRIDGE':'1'})

    def test_imminent_target_requests_bounded_bridge_after_finished_writer(self):
        with tempfile.TemporaryDirectory() as name, self.trusted():
            root = Path(name); self.fixture(root)
            self.assertTrue(guard.needs_tail_bridge(root, START))

    def test_no_bridge_for_distant_target_and_already_issued_hour(self):
        with tempfile.TemporaryDirectory() as name, self.trusted():
            root = Path(name); self.fixture(root, target_minutes=30)
            self.assertFalse(guard.needs_tail_bridge(root, START))

    def test_unissued_current_hour_requests_bridge_without_retrospective_issue(self):
        with tempfile.TemporaryDirectory() as name, self.trusted():
            root = Path(name); self.fixture(root, target_minutes=-40, issued=False)
            self.assertTrue(guard.needs_tail_bridge(root, START))

    def test_non_main_never_launches_tail_bridge(self):
        with tempfile.TemporaryDirectory() as name, patch.dict('os.environ', {'GITHUB_REF':'refs/pull/78/merge'}):
            self.assertFalse(guard.needs_tail_bridge(Path(name), START))

    def test_old_compiled_workflow_cannot_launch_an_ungated_tail_writer(self):
        with tempfile.TemporaryDirectory() as name, self.trusted(), patch.dict('os.environ', {'DOGE_TAIL_CAPTURE_BRIDGE':'0'}):
            root=Path(name);self.fixture(root)
            self.assertFalse(guard.needs_tail_bridge(root, START))

    def test_completion_signal_is_invisible_until_json_is_fully_written(self):
        import archive_capture_bridge as bridge
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'completed.json'
            original=json.dump
            def dump(value, stream, **kwargs):
                self.assertFalse(path.exists())
                original(value,stream,**kwargs)
            with patch.object(json,'dump',side_effect=dump):
                bridge.atomic_json(path,{'exit_code':0})
            self.assertEqual(json.loads(path.read_text()),{'exit_code':0})

    def test_tail_child_does_not_write_parent_safety_output(self):
        with tempfile.TemporaryDirectory() as name, self.trusted():
            root=Path(name);self.fixture(root)
            parent=root/'parent.outputs';parent.write_text('tail_fatal=true\n')
            def child(args, cwd, env):
                self.assertNotEqual(env['GITHUB_OUTPUT'],str(parent))
                Path(env['GITHUB_OUTPUT']).write_text('fatal=true\nfatal=false\n')
                return type('Result',(),{'returncode':0})()
            with patch.dict('os.environ', {'GITHUB_OUTPUT':str(parent)}), patch.object(guard.subprocess,'run',side_effect=child):
                guard.tail_bridge(root,START)
            self.assertEqual(parent.read_text(),'tail_fatal=true\n')

    def test_real_background_process_drains_before_final_publication(self):
        import archive_capture_bridge as bridge
        with tempfile.TemporaryDirectory() as name, self.trusted():
            root=Path(name);(root/'data').mkdir()
            (root/'data/ledger.jsonl').write_text('old evidence\n')
            script=root/'archive_capture_bridge.py'
            shutil.copyfile(bridge.__file__,script)
            stub='''import argparse, os, time
from pathlib import Path
class LiveAdapter:
    def __init__(self, root): self.root=root
    def action(self, name):
        assert (self.root/'control/completed.json').exists()
        (self.root/'published-after-drain').write_text(name)
if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--duration-seconds');p.add_argument('--stop-file');a=p.parse_args()
    Path('data/runtime_deadline_v1').mkdir(parents=True,exist_ok=True)
    limit=time.monotonic()+10
    while not Path(a.stop_file).exists() and time.monotonic()<limit:
        with Path('data/ledger.jsonl').open('a') as f:f.write('genuine-fixture-tick\\n')
        time.sleep(.05)
    Path(os.environ['GITHUB_OUTPUT']).write_text('fatal=true\\nfatal=false\\n')
'''
            (root/'runtime_deadline_service.py').write_text(stub)
            args=[sys.executable,str(script)]
            subprocess.run(args+['start','--control',str(root/'control')],cwd=root,check=True,capture_output=True)
            deadline=time.monotonic()+5
            while (root/'data/ledger.jsonl').read_text() == 'old evidence\n':
                if time.monotonic()>deadline:self.fail('background writer did not advance')
                time.sleep(.05)
            self.assertEqual((root/'archive-snapshot/data/ledger.jsonl').read_text(),'old evidence\n')
            result=subprocess.run(args+['stop','--control',str(root/'control')],cwd=root,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((root/'published-after-drain').read_text(),'publish')
            self.assertEqual(json.loads((root/'control/completed.json').read_text())['exit_code'],0)


if __name__ == '__main__': unittest.main()

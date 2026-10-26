"""Slow audits in an isolated, pinned local clone; only two reports return.

The worker cannot publish, issue, score, or touch the live ledger. Its temporary
audit caches are discarded. The live serial writer installs complete matching
reports, and frozen P2 checks still decide eligibility at capture time.
"""
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

INPUTS = ('data/direction_v42/forward_registry.csv',
          'data/direction_v42/forward_scores.csv', 'data/quote_evidence_v2/bindings.csv',
          'data/paired_forward_v1/pairs.jsonl', 'data/hourly_research/ohlc.csv')
OUTPUTS = ('data/p0_operations/latest.json', 'data/paired_forward_v1/audit_latest.json')
SCRIPTS = ('forward_validation_v2.py', 'p0_operational_audit.py', 'paired_cohort_audit.py')


def fingerprint(root):
    return {p: hashlib.sha256((Path(root)/p).read_bytes()).hexdigest() for p in INPUTS}


def audit_snapshot(root, head, temporary_parent=None):
    def command(*args, cwd):
        return subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                              text=True, encoding='utf-8', timeout=240).stdout
    with tempfile.TemporaryDirectory(prefix='doge-source-audit-', dir=temporary_parent) as name:
        snapshot = Path(name)/'snapshot'
        command('git', 'clone', '--shared', '--no-checkout', str(root), str(snapshot), cwd=root)
        command('git', '-c', 'core.autocrlf=false', 'checkout', '--detach', head, cwd=snapshot)
        inputs = fingerprint(snapshot)
        timings = []
        for script in SCRIPTS:
            start = time.monotonic()
            command(sys.executable, script, cwd=snapshot)
            timings.append({'stage': script, 'seconds': time.monotonic()-start})
        outputs = {p: (snapshot/p).read_text(encoding='utf-8') for p in OUTPUTS}
        return {'status': 'ok', 'input_head': head, 'inputs': inputs,
                'outputs': outputs, 'timings': timings}


def accept(root, result):
    if result.get('status') != 'ok': raise ValueError('isolated audit failed: '+str(result.get('error'))[:500])
    if result['inputs'] != fingerprint(root): return {'status': 'discarded_input_changed'}
    if set(result['outputs']) != set(OUTPUTS): raise ValueError('unexpected isolated audit outputs')
    for p in OUTPUTS:
        if json.loads(result['outputs'][p]).get('status') != 'ok': raise ValueError('blocked isolated report')
    # Called by the only live writer, never by the background process. If a disk
    # error interrupts the pair installation, the caller fails closed before book.
    for p in OUTPUTS:
        path = Path(root)/p
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result['outputs'][p], encoding='utf-8')
    return {'status': 'ready', 'input_head': result['input_head'], 'timings': result['timings']}


class Auditor:
    def __init__(self, root):
        self.root = Path(root)
        self.process = None
        self.directory = None
        self.inputs = None

    def poll(self):
        # An issuer/target watcher can change inputs while a read-only audit is
        # running. Never spend the next ticks waiting for an obsolete snapshot.
        if self.process is not None and self.inputs != fingerprint(self.root):
            self.close()
        if self.process is not None:
            if self.process.poll() is None:
                if time.monotonic()-self.started > 600:
                    self.close()
                    raise TimeoutError('isolated source audit exceeded 600 seconds')
                return {'status': 'pending'}
            try:
                if self.process.returncode: raise ValueError('isolated audit worker exited unsuccessfully')
                result = json.loads((Path(self.directory.name)/'result.json').read_text(encoding='utf-8'))
                accepted = accept(self.root, result)
            finally:
                self.close()
            if accepted['status'] == 'ready': return accepted
        # Snapshot only a committed source version. No live .git/index, ledger,
        # or output directory is shared with the worker.
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.root).decode().strip()
        self.directory = tempfile.TemporaryDirectory(prefix='doge-audit-result-')
        self.started = time.monotonic()
        self.inputs = fingerprint(self.root)
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
            str(self.root), head, str(Path(self.directory.name)/'result.json')],
            cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        return {'status': 'pending', 'input_head': head}

    def close(self):
        if self.process is not None and self.process.poll() is None:
            if os.name == 'posix': os.killpg(self.process.pid, signal.SIGTERM)
            else: self.process.terminate()
            self.process.wait(timeout=10)
        self.process = None
        if self.directory is not None: self.directory.cleanup()
        self.directory = None


if __name__ == '__main__':
    try: result = audit_snapshot(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]).parent)
    except Exception as exc: result = {'status': 'failed', 'error': type(exc).__name__+': '+str(exc)[:500]}
    Path(sys.argv[3]).write_text(json.dumps(result), encoding='utf-8')

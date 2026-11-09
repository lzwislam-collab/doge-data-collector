"""Linux ownership cleanup for the archive bridge only; no publication or success gate."""
import os
from pathlib import Path
import select
import signal
import sys
import time

COOPERATIVE_SECONDS = 20
FREEZE_SECONDS = 2
KILL_WAIT_SECONDS = 2


def _process(pid):
    try:
        fields = (Path('/proc')/str(pid)/'stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'ppid': int(fields[1]), 'pgid': int(fields[2]),
                'session': int(fields[3]), 'start_ticks': fields[19], 'state': fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def identity(pid):
    if (sys.platform != 'linux' or type(pid) is not int or pid <= 1
            or not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal')):
        return None
    row = _process(pid)
    if row is None: return None
    result = {key: row[key] for key in ('pid', 'pgid', 'session', 'start_ticks')}
    result['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return result


def cancel(expected):
    """Cancel one recorded archive worker and verify all acquired descendants exit.

    Freeze the current subtree before cancellation so even detached command/audit
    sessions are acquired. Held pidfds prevent signalling a reused PID. The worker
    forwards TERM to its runtime; that runtime retains its existing graceful cleanup.
    A bounded final freeze precedes forced termination of remaining owned handles.
    Missing ownership evidence is always UNVERIFIED, never permission to signal.
    """
    started = time.monotonic()
    result = {'schema_version': 1, 'status': 'UNVERIFIED', 'cleanup_verified': False,
              'termination_requested': False, 'kill_requested': False,
              'registered_processes': 0, 'registered_processes_exit_verified': False,
              'scope': 'acquired_archive_process_handles', 'cooperative_seconds': COOPERATIVE_SECONDS,
              'maximum_cleanup_seconds': COOPERATIVE_SECONDS+2*FREEZE_SECONDS+KILL_WAIT_SECONDS}
    owned = {}
    paused = set()
    def alive(pid):
        poller = select.poll()
        poller.register(owned[pid], select.POLLIN)
        return not poller.poll(0)
    def send(pid, signum):
        try: signal.pidfd_send_signal(owned[pid], signum)
        except ProcessLookupError: pass
    def acquire(expected_row):
        pid = expected_row["pid"]
        if pid in owned: return False
        keys = ("pid", "ppid", "pgid", "session", "start_ticks")
        before = _process(pid)
        if before is None or any(before[k] != expected_row[k] for k in keys): return False
        try: fd = os.pidfd_open(pid)
        except ProcessLookupError: return False
        after = _process(pid)
        if after is None or any(expected_row[k] != after[k] for k in keys):
            os.close(fd)
            return False
        owned[pid] = fd
        return True
    def discover(deadline):
        added = False
        # Repeated discovery handles multiple generations in either procfs order.
        while True:
            if time.monotonic() >= deadline: raise TimeoutError("archive descendant discovery exceeded cleanup bound")
            found = False
            parents = {pid for pid in owned if alive(pid)}
            for path in Path('/proc').iterdir():
                if time.monotonic() >= deadline: raise TimeoutError('archive descendant discovery exceeded cleanup bound')
                if not path.name.isdigit(): continue
                row = _process(int(path.name))
                if row is not None and row['ppid'] in parents and row['pid'] not in owned:
                    found = acquire(row) or found
            added = added or found
            if not found: return added
    def freeze(require_root=False):
        deadline = time.monotonic()+FREEZE_SECONDS
        while True:
            discover(deadline)
            for pid in list(owned):
                if alive(pid):
                    send(pid, signal.SIGSTOP)
                    paused.add(pid)
            discover(deadline)
            states = {pid: _process(pid) for pid in owned if alive(pid)}
            if require_root and (expected['pid'] not in states):
                raise RuntimeError('archive owner exited before subtree ownership was established')
            if all(row is not None and row['state'] in ('T', 't') for row in states.values()):
                if not discover(deadline): return
            if time.monotonic() >= deadline:
                raise TimeoutError('archive owned subtree did not freeze within cleanup bound')
            time.sleep(.01)
    def resume():
        for pid in list(paused):
            send(pid, signal.SIGCONT)
            paused.discard(pid)
    try:
        if (type(expected) is not dict or type(expected.get('pid')) is not int
                or expected['pid'] <= 1 or expected.get('pgid') != expected['pid']
                or expected.get('session') != expected['pid']
                or expected['pid'] == os.getpid() or identity(expected['pid']) != expected):
            result['reason'] = 'archive_process_identity_not_verified'
            return result
        root_row = _process(expected['pid'])
        if root_row is None or not acquire(root_row) or identity(expected['pid']) != expected:
            result['reason'] = 'archive_process_identity_changed'
            return result
        freeze(require_root=True)
        send(expected['pid'], signal.SIGTERM)
        result['termination_requested'] = True
        resume()
        deadline = time.monotonic()+COOPERATIVE_SECONDS
        while any(alive(pid) for pid in owned):
            if time.monotonic() >= deadline: break
            try: discover(deadline)
            except TimeoutError: break
            time.sleep(.02)
        if any(alive(pid) for pid in owned):
            freeze()
            result['kill_requested'] = True
            for pid in reversed(list(owned)):
                if alive(pid): send(pid, signal.SIGKILL)
            resume()
            deadline = time.monotonic()+KILL_WAIT_SECONDS
            while any(alive(pid) for pid in owned) and time.monotonic() < deadline:
                time.sleep(.01)
        result['registered_processes_exit_verified'] = not any(alive(pid) for pid in owned)
        # Acquired handles do not prove absence of an unobserved orphan.
        # The caller must also confirm the real collector's cancellation receipt.
        result['reason'] = ('collector_cancellation_receipt_required'
            if result['registered_processes_exit_verified'] and not result['kill_requested']
            else 'hard_stop_unobserved_descendants_not_verified' if result['registered_processes_exit_verified']
            else 'archive_owned_process_still_live')
    except Exception as exc:
        result.update(status='UNVERIFIED', cleanup_verified=False, error_type=type(exc).__name__)
    finally:
        try: resume()
        finally:
            result['registered_processes'] = len(owned)
            result['elapsed_seconds'] = time.monotonic()-started
            for fd in owned.values(): os.close(fd)
    return result

"""Bounded ownership of synchronous subprocesses on the Linux collector.

The limit applies to one command, plus bounded termination and pipe draining.
It does not turn the capture cycle or an in-process publisher into a hard deadline.
"""
from contextlib import contextmanager
import math
import os
import signal
import subprocess
import threading
import time


TERM_GRACE_SECONDS = 2
KILL_WAIT_SECONDS = 2


class RuntimeCancelled(BaseException):
    def __init__(self, signum):
        self.signum = signum
        super().__init__('runtime cancellation requested')


@contextmanager
def cancellation_signals():
    """Latch the first cancellation so repeated runner signals cannot abort cleanup."""
    state = {'cancelled': False, 'signum': None}
    previous = {}
    def cancel(signum, frame):
        if state['cancelled']: return
        state.update(cancelled=True, signum=signum)
        if signum == signal.SIGINT: raise KeyboardInterrupt
        raise RuntimeCancelled(signum)
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, cancel)
    try:
        yield state
    finally:
        for signum, handler in previous.items(): signal.signal(signum, handler)


@contextmanager
def defer_cancellation():
    """Latch a first signal immediately, but raise it only after the protected work.

Calling the previous handler preserves the outer cancellation state. Repeated
signals are ignored by that latched handler, rather than losing a first signal
which happens to arrive during cleanup.
    """
    previous = {}
    pending = []
    def defer(signum, frame):
        handler = previous[signum]
        if handler == signal.SIG_IGN: return
        try:
            if callable(handler): handler(signum, frame)
            elif signum == signal.SIGINT: raise KeyboardInterrupt
            else: raise RuntimeCancelled(signum)
        except (KeyboardInterrupt, RuntimeCancelled) as exc:
            if not pending: pending.append(exc)
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, defer)
    try:
        yield
    finally:
        for signum, handler in previous.items(): signal.signal(signum, handler)
    if pending: raise pending[0]


def terminate_group(process):
    """Reap the leader and kill surviving descendants even after leader exit.

POSIX callers must create the process with start_new_session=True. The non-POSIX
fallback can terminate only the direct process; production runs on Ubuntu.
    """
    result = {'scope': 'process_group' if os.name == 'posix' else 'process',
              'termination_requested': False, 'kill_requested': False,
              'leader_reaped': False}
    def alive():
        process.poll()
        if os.name != 'posix': return process.returncode is None
        try: os.killpg(process.pid, 0)
        except ProcessLookupError: return False
        return True
    def send(force=False):
        try:
            if os.name == 'posix': os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            elif force: process.kill()
            else: process.terminate()
        except ProcessLookupError: return False
        return True
    failure = None
    try:
        if alive():
            result['termination_requested'] = send()
            deadline = time.monotonic()+TERM_GRACE_SECONDS
            while alive() and time.monotonic() < deadline:
                time.sleep(min(.02, max(0, deadline-time.monotonic())))
    except BaseException as exc:
        failure = exc
    # A second cancellation cannot interrupt escalation or leader reaping.
    try:
        with defer_cancellation():
            if alive(): result['kill_requested'] = send(force=True)
            process.wait(timeout=KILL_WAIT_SECONDS)
            result['leader_reaped'] = True
    except BaseException as exc:
        if isinstance(exc, Exception): result['cleanup_error_type'] = type(exc).__name__
        if failure is None: failure = exc
    if failure is not None:
        failure.process_cleanup = result
        raise failure
    return result


def command(args, *, cwd, env=None, timeout=240):
    """Return captured stdout; preserve typed failures and bounded cleanup evidence."""
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('finite positive subprocess timeout required')
    process = None
    error = None
    cleanup = {}
    stdout = stderr = None
    try:
        try:
            # Establish ownership before a cancellation can escape Popen/assignment.
            with defer_cancellation():
                process = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding='utf-8', start_new_session=True)
            stdout, stderr = process.communicate(timeout=timeout)
            if process.returncode:
                error = subprocess.CalledProcessError(process.returncode, args, output=stdout, stderr=stderr)
        except BaseException as exc:
            error = exc
    finally:
        if process is not None:
            try:
                with defer_cancellation():
                    try:
                        cleanup = terminate_group(process)
                    except BaseException as cleanup_error:
                        cleanup = getattr(cleanup_error, 'process_cleanup', {})
                        cleanup['cleanup_error_type'] = type(cleanup_error).__name__
                        if error is None: error = cleanup_error
                    try:
                        stdout, stderr = process.communicate(timeout=KILL_WAIT_SECONDS)
                        cleanup['pipes_drained'] = True
                    except BaseException as drain_error:
                        stdout = getattr(drain_error, 'output', getattr(error, 'output', None))
                        stderr = getattr(drain_error, 'stderr', getattr(error, 'stderr', None))
                        cleanup.update(pipes_drained=False, drain_error_type=type(drain_error).__name__)
                        if error is None: error = drain_error
                    finally:
                        for stream in (process.stdout, process.stderr):
                            try:
                                if stream is not None: stream.close()
                            except Exception as close_error:
                                cleanup['pipe_close_error_type'] = type(close_error).__name__
                                if error is None: error = close_error
            except (KeyboardInterrupt, RuntimeCancelled) as cancellation:
                if error is not None and error is not cancellation:
                    cancellation.original_failure = error
                error = cancellation
    if error is not None:
        error.process_cleanup = cleanup
        original = getattr(error, 'original_failure', error)
        if isinstance(original, subprocess.TimeoutExpired):
            original.output, original.stderr = stdout, stderr
        if isinstance(error, (KeyboardInterrupt, RuntimeCancelled)):
            error.stdout, error.stderr = stdout, stderr
        if original is not error: raise error from original
        raise error
    return stdout

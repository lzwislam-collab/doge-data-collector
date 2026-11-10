"""Bounded fresh P0 inventory GETs; all per-day logic is the original function.

The successful inventory path has the same request multiset. Only first-page
GETs may look ahead, at most three days. Later pages wait for their original
day order, so even an early pagination error has at most three extra GETs.
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, timedelta
from functools import wraps
import hashlib
from pathlib import Path
import threading
import types

P0_BLOB = 'c615bdc665d24481569f545922d3d7aa04594d60'
WORKFLOW = '/actions/workflows/okx_hourly_extreme_history.yml'
WINDOW = 4


def eligible(original):
    """Unknown code or arguments use the complete original implementation."""
    if not isinstance(original, types.FunctionType) or original.__name__ != 'fetch_operational_runs':
        return False
    try:
        path = Path(original.__code__.co_filename)
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return False
        raw = raw.replace(b'\r\n', b'\n')
        actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if actual != P0_BLOB:
            return False
        # Whole-module compilation preserves the compiler's real global-name
        # context. Extracting just this AST changes bytecode for datetime.now.
        # Compile only: do not execute imports, main, or any scientific function.
        module_code = compile(raw, str(path), 'exec', dont_inherit=True)
        expected = [value for value in module_code.co_consts
                    if isinstance(value, types.CodeType)
                    and value.co_name == 'fetch_operational_runs']
        return len(expected) == 1 and original.__code__ == expected[0]
    except (OSError, ValueError, TypeError, StopIteration, SyntaxError):
        return False


def _routine_interval(since, until):
    try:
        # The actual P0 caller passes a fixed aware UTC `now`. Keep unusual or
        # open-ended callers on the original path, including its own clock.
        return (isinstance(since, date) and isinstance(until, date)
                and since.utcoffset() == timedelta(0) and until.utcoffset() == timedelta(0)
                and timedelta(0) < until - since <= timedelta(days=31))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


class _Abandoned(Exception):
    """Internal later partition; never replaces the first ordered real error."""


def fetch_operational_runs(original, since, request=None, until=None, _report=None):
    if request is not None or not eligible(original) or not _routine_interval(since, until):
        if _report is not None:
            _report['fallback_calls'] += 1
        return original(since, request=request, until=until)

    if _report is not None:
        _report['native_calls'] += 1
        _report['real_gets'] += 1
    fresh = original.__globals__['base'].github_json
    workflow = fresh(WORKFLOW)
    workflow['id']  # Preserve the original immediate missing/malformed ID error.
    partitions = []
    start = since
    while start < until:
        end = min(start + timedelta(days=1), until)
        partitions.append((start, end))
        start = end
    if _report is not None:
        _report['days'] += len(partitions)

    lock = threading.Condition()
    failed_index = None
    stopping = False
    current_index = 0

    def failed(index):
        nonlocal failed_index
        with lock:
            failed_index = index if failed_index is None else min(failed_index, index)
            lock.notify_all()

    def day(index):
        requests_started = 0

        def real_or_metadata(suffix):
            nonlocal requests_started
            if suffix == WORKFLOW:
                # One freshly fetched workflow reply, reused only in this call.
                return workflow
            with lock:
                while True:
                    if stopping or failed_index is not None and index > failed_index:
                        raise _Abandoned()
                    if requests_started == 0 or index <= current_index:
                        requests_started += 1
                        if _report is not None:
                            _report['real_gets'] += 1
                        break
                    lock.wait()
            try:
                return fresh(suffix)
            except BaseException:
                failed(index)
                raise

        try:
            return original(partitions[index][0], request=real_or_metadata,
                            until=partitions[index][1])
        except _Abandoned:
            raise
        except BaseException:
            failed(index)
            raise

    executor = ThreadPoolExecutor(max_workers=WINDOW, thread_name_prefix='p0-inventory')
    pending = deque()
    next_index = 0
    rows = {}
    try:
        while next_index < min(WINDOW, len(partitions)):
            pending.append((next_index, executor.submit(day, next_index)))
            next_index += 1
        while pending:
            index, future = pending.popleft()
            for run in future.result():
                rows[run['id']] = run
            with lock:
                current_index = index + 1
                lock.notify_all()
                can_submit = failed_index is None and not stopping
            if can_submit and next_index < len(partitions):
                pending.append((next_index, executor.submit(day, next_index)))
                next_index += 1
        return list(rows.values())
    finally:
        with lock:
            stopping = True
            lock.notify_all()
        for _, future in pending:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)


@contextmanager
def installed(main_globals):
    """Use the real original main's globals; restore on every exit, never print."""
    original = main_globals.get('fetch_operational_runs')
    report = {'installed': False, 'implementation': 'p0_fresh_inventory_window4_v1',
              'window': WINDOW, 'native_calls': 0, 'fallback_calls': 0,
              'real_gets': 0, 'days': 0}
    if not eligible(original):
        yield report
        return

    @wraps(original)
    def wrapper(since, request=None, until=None):
        return fetch_operational_runs(original, since, request=request, until=until,
                                      _report=report)

    main_globals['fetch_operational_runs'] = wrapper
    report['installed'] = True
    try:
        yield report
    finally:
        main_globals['fetch_operational_runs'] = original

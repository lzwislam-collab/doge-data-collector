"""Original pinned P0 is the oracle; these fixtures make no network requests."""
import ast
import contextlib
import importlib.util
import io
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import patch
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
PIN = HERE.parent / 'pinned' / 'p0_operational_audit.py'
if not PIN.is_file():
    PIN = HERE / 'p0_operational_audit.py'
HELPER = HERE / 'runtime_p0_inventory_transport.py'
UTC = timezone.utc
START = datetime(2026, 10, 12, 2, tzinfo=UTC)
WORKFLOW = '/actions/workflows/okx_hourly_extreme_history.yml'


def original(request):
    node = next(n for n in ast.parse(PIN.read_text(encoding='utf8')).body
                if isinstance(n, ast.FunctionDef) and n.name == 'fetch_operational_runs')
    env = {'base': types.SimpleNamespace(github_json=request), 'datetime': datetime,
           'timezone': timezone, 'timedelta': timedelta}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(PIN), 'exec'), env)
    return env['fetch_operational_runs']


class Requests:
    def __init__(self, result=None):
        self.calls = []
        self.lock = threading.Lock()
        self.result = result

    def __call__(self, suffix):
        with self.lock:
            self.calls.append(suffix)
        if suffix == WORKFLOW:
            return {'id': 380949273, 'fresh_workflow_marker': 'same invocation only'}
        query = parse_qs(urlparse(suffix).query)
        start = datetime.fromisoformat(query['created'][0].split('..')[0].replace('Z', '+00:00'))
        index = (start - START).days
        page = int(query['page'][0])
        if self.result is not None:
            return self.result(index, page)
        # The duplicate at the day boundary must retain original insertion order
        # and take the value from the later original partition.
        return {'workflow_runs': [{'id': index, 'value': 'first'},
                                 {'id': index + 1, 'value': 'boundary-' + str(index)}]}


class InventoryTransport(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HELPER.is_file(), 'owned bounded inventory transport is not implemented')
        spec = importlib.util.spec_from_file_location('p0_inventory_under_test', HELPER)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)

    def test_success_exact_original_results_url_multiset_and_midnight_duplicates(self):
        serial = Requests()
        expected = original(serial)(START, until=START + timedelta(days=9, hours=3))
        concurrent = Requests()
        actual = self.helper.fetch_operational_runs(
            original(concurrent), START, until=START + timedelta(days=9, hours=3))
        self.assertEqual(actual, expected)
        self.assertEqual(Counter(concurrent.calls), Counter(serial.calls))
        self.assertEqual(concurrent.calls.count(WORKFLOW), 1)

    def test_future_partitions_cannot_paginate_before_original_prefix_is_consumed(self):
        initial = set()
        initial_lock = threading.Lock()
        initial_three = threading.Event()
        release_first = threading.Event()
        speculative_page_two = threading.Event()

        def result(index, page):
            if index == 0:
                if not release_first.wait(2):
                    raise TimeoutError('fixture first partition not released')
                return {'workflow_runs': []}
            if page == 1:
                with initial_lock:
                    initial.add(index)
                    if len(initial) == 3:
                        initial_three.set()
                return {'workflow_runs': [{'id': index * 1000 + j} for j in range(100)]}
            speculative_page_two.set()
            return {'workflow_runs': []}

        request = Requests(result)
        errors = []

        def run():
            try:
                self.helper.fetch_operational_runs(original(request), START,
                                                    until=START + timedelta(days=4))
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(initial_three.wait(1), 'the existing four-day window must be concurrent')
            self.assertFalse(speculative_page_two.wait(.08),
                             'future days must not issue page2 before the earlier day completes')
        finally:
            release_first.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_original_conditional_second_page_and_response_order_are_preserved(self):
        def result(index, page):
            if index == 1 and page == 1:
                return {'workflow_runs': [{'id': 1000 + i, 'page': 1} for i in range(100)]}
            return {'workflow_runs': [{'id': index, 'page': page}]}
        a, b = Requests(result), Requests(result)
        expected = original(a)(START, until=START + timedelta(days=6))
        actual = self.helper.fetch_operational_runs(original(b), START,
                                                    until=START + timedelta(days=6))
        self.assertEqual(actual, expected)
        self.assertEqual(Counter(a.calls), Counter(b.calls))
        pages = [parse_qs(urlparse(x).query).get('page') for x in b.calls if x != WORKFLOW]
        self.assertEqual(pages.count(['2']), 1)

    def test_rolling_window_does_not_submit_fifth_day_while_first_is_unfinished(self):
        started = set()
        mutex = threading.Lock()
        first_four = threading.Event()
        release = threading.Event()
        errors = []
        def result(index, page):
            with mutex:
                started.add(index)
                if len(started) == 4:
                    first_four.set()
            if index == 0 and not release.wait(2):
                raise TimeoutError('fixture not released')
            return {'workflow_runs': []}
        request = Requests(result)
        def run():
            try:
                self.helper.fetch_operational_runs(original(request), START,
                                                    until=START + timedelta(days=29))
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(first_four.wait(1))
            self.assertEqual(started, {0, 1, 2, 3})
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(started, set(range(29)))

    def test_custom_request_uses_the_complete_original_without_default_GET(self):
        def forbidden(suffix):
            self.fail('default GET must not run for an explicit custom request')
        a, b = Requests(), Requests()
        expected = original(forbidden)(START, request=a, until=START + timedelta(days=3))
        actual = self.helper.fetch_operational_runs(original(forbidden), START, request=b,
                                                    until=START + timedelta(days=3))
        self.assertEqual(actual, expected)
        self.assertEqual(b.calls, a.calls)

    def test_unknown_function_identity_is_complete_original_fallback(self):
        marker = object()
        calls = []
        def changed(since, request=None, until=None):
            calls.append((since, request, until))
            return marker
        self.assertIs(self.helper.fetch_operational_runs(changed, START,
                                                        until=START + timedelta(days=2)), marker)
        self.assertEqual(calls, [(START, None, START + timedelta(days=2))])

    def test_same_source_file_but_changed_function_code_is_not_eligible(self):
        f = original(Requests())
        f.__code__ = (lambda since, request=None, until=None: ['legacy']).__code__
        self.assertFalse(self.helper.eligible(f))
        self.assertEqual(self.helper.fetch_operational_runs(f, START,
                                                          until=START + timedelta(days=2)), ['legacy'])

    def test_missing_workflow_id_raises_original_error_before_daily_queries(self):
        calls = []
        def bad(suffix):
            calls.append(suffix)
            return {}
        with self.assertRaises(KeyError) as cm:
            self.helper.fetch_operational_runs(original(bad), START,
                                               until=START + timedelta(days=3))
        self.assertEqual(cm.exception.args, ('id',))
        self.assertEqual(calls, [WORKFLOW])

    def test_http_permission_and_rate_errors_preserve_original_object(self):
        for code in (403, 429):
            with self.subTest(code=code):
                exc = HTTPError('https://api.github.com/fixture', code, 'original-http', {}, None)
                def result(index, page):
                    if index == 2:
                        raise exc
                    return {'workflow_runs': []}
                request = Requests(result)
                with self.assertRaises(HTTPError) as cm:
                    self.helper.fetch_operational_runs(original(request), START,
                                                       until=START + timedelta(days=29))
                self.assertIs(cm.exception, exc)
                indices = [(datetime.fromisoformat(parse_qs(urlparse(x).query)['created'][0]
                            .split('..')[0].replace('Z', '+00:00')) - START).days
                           for x in request.calls if x != WORKFLOW]
                self.assertLessEqual(len([i for i in indices if i > 2]), 3)

    def test_out_of_order_http_failure_does_not_replace_earlier_original_failure(self):
        later_failed = threading.Event()
        first = HTTPError('https://api.github.com/fixture', 403, 'first-day', {}, None)
        second = HTTPError('https://api.github.com/fixture', 429, 'second-day', {}, None)
        def result(index, page):
            if index == 0:
                if not later_failed.wait(1):
                    raise TimeoutError('fixture concurrent day did not run')
                raise first
            if index == 1:
                later_failed.set()
                raise second
            return {'workflow_runs': []}
        request = Requests(result)
        with self.assertRaises(HTTPError) as cm:
            self.helper.fetch_operational_runs(original(request), START,
                                               until=START + timedelta(days=8))
        self.assertIs(cm.exception, first)
        self.assertLessEqual(len(request.calls), 5)

    def test_original_missing_response_field_parse_error_is_not_filtered(self):
        def bad(index, page):
            return {}
        a, b = Requests(bad), Requests(bad)
        with self.assertRaises(KeyError) as old:
            original(a)(START, until=START + timedelta(days=9))
        with self.assertRaises(KeyError) as new:
            self.helper.fetch_operational_runs(original(b), START,
                                               until=START + timedelta(days=9))
        self.assertEqual(new.exception.args, old.exception.args)
        self.assertLessEqual(len(b.calls), 5)

    def test_original_1000_cap_keeps_error_and_only_three_future_first_pages(self):
        def full(index, page):
            return {'workflow_runs': [{'id': index * 10000 + page * 100 + j}
                                     for j in range(100)]}
        a, b = Requests(full), Requests(full)
        with self.assertRaises(ValueError) as old:
            original(a)(START, until=START + timedelta(days=29))
        with self.assertRaises(ValueError) as new:
            self.helper.fetch_operational_runs(original(b), START,
                                               until=START + timedelta(days=29))
        self.assertEqual(str(new.exception), str(old.exception))
        extra = []
        for suffix in b.calls:
            if suffix == WORKFLOW:
                continue
            query = parse_qs(urlparse(suffix).query)
            if not query['created'][0].startswith(START.isoformat().replace('+00:00', 'Z') + '..'):
                extra.append(query)
        self.assertLessEqual(len(extra), 3)
        self.assertTrue(all(x['page'] == ['1'] for x in extra))

    def test_keyboard_and_system_exit_are_never_swallowed(self):
        for exc in (KeyboardInterrupt(), SystemExit(130)):
            with self.subTest(kind=type(exc).__name__):
                def cancelled(index, page):
                    raise exc
                request = Requests(cancelled)
                with self.assertRaises(type(exc)) as cm:
                    self.helper.fetch_operational_runs(original(request), START,
                                                       until=START + timedelta(days=8))
                self.assertIs(cm.exception, exc)
                self.assertLessEqual(len(request.calls), 5)

    def test_open_ended_reverse_and_out_of_scope_ranges_keep_original_path(self):
        for since, until in [(START, None), (START, START),
                             (START, START - timedelta(days=1)),
                             (START, START + timedelta(days=32))]:
            with self.subTest(until=until):
                a, b = Requests(), Requests()
                f, g = original(a), original(b)
                class FixedClock:
                    @staticmethod
                    def now(tz):
                        return START + timedelta(days=3)
                f.__globals__['datetime'] = g.__globals__['datetime'] = FixedClock
                expected = f(since, until=until)
                actual = self.helper.fetch_operational_runs(g, since, until=until)
                self.assertEqual(actual, expected)
                self.assertEqual(b.calls, a.calls)

    def test_actual_function_global_context_installs_and_restores(self):
        self.assertTrue(callable(getattr(self.helper, 'installed', None)),
                        'owned original-main context is not implemented')
        request = Requests()
        f = original(request)
        namespace = f.__globals__
        with self.helper.installed(namespace) as report:
            self.assertTrue(report['installed'])
            self.assertIsNot(namespace['fetch_operational_runs'], f)
            actual = namespace['fetch_operational_runs'](START, until=START + timedelta(days=3))
            self.assertEqual(len(actual), 4)
            self.assertEqual(report['native_calls'], 1)
            self.assertEqual(report['fallback_calls'], 0)
            self.assertEqual(report['real_gets'], 4)
            self.assertEqual(report['days'], 3)
        self.assertIs(namespace['fetch_operational_runs'], f)

    def test_context_restores_after_exception_and_system_exit_without_printing(self):
        self.assertTrue(callable(getattr(self.helper, 'installed', None)),
                        'owned original-main context is not implemented')
        for exc in (ValueError('fixture body failure'), SystemExit(130), KeyboardInterrupt()):
            f = original(Requests())
            namespace = f.__globals__
            with self.assertRaises(type(exc)) as cm:
                with self.helper.installed(namespace):
                    raise exc
            self.assertIs(cm.exception, exc)
            self.assertIs(namespace['fetch_operational_runs'], f)

    def test_unknown_main_global_context_leaves_original_callable_untouched(self):
        self.assertTrue(callable(getattr(self.helper, 'installed', None)),
                        'owned original-main context is not implemented')
        sentinel = object()
        def other(since, request=None, until=None):
            return sentinel
        namespace = {'fetch_operational_runs': other}
        with self.helper.installed(namespace) as report:
            self.assertFalse(report['installed'])
            self.assertIs(namespace['fetch_operational_runs'], other)
        self.assertIs(namespace['fetch_operational_runs'], other)

    def test_context_fallback_is_explicit_and_never_prints_response_or_exception(self):
        secret = 'FIXTURE_SECRET_NEVER_LOG_THIS'
        f = original(Requests(lambda index, page: {'workflow_runs': [{'id': index, 'secret': secret}]}))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.helper.installed(f.__globals__) as report:
                custom = Requests()
                f.__globals__['fetch_operational_runs'](START, request=custom,
                                                       until=START + timedelta(days=2))
                self.assertEqual(report['fallback_calls'], 1)
                self.assertEqual(report['native_calls'], 0)
        self.assertNotIn(secret, repr(report))
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), '')

    def test_utc_non_utc_and_naive_inputs_use_original_without_native_reinterpretation(self):
        for since, until in [(START.replace(tzinfo=None), (START + timedelta(days=1)).replace(tzinfo=None)),
                             (START.astimezone(timezone(timedelta(hours=8))),
                              (START + timedelta(days=1)).astimezone(timezone(timedelta(hours=8))))]:
            class AnyIntervalRequests:
                def __init__(self): self.calls = []
                def __call__(self, suffix):
                    self.calls.append(suffix)
                    return {'id': 380949273} if suffix == WORKFLOW else {'workflow_runs': [{'id': 1}]}
            a, b = AnyIntervalRequests(), AnyIntervalRequests()
            expected = original(a)(since, until=until)
            f = original(b)
            with self.helper.installed(f.__globals__) as report:
                actual = f.__globals__['fetch_operational_runs'](since, until=until)
                self.assertEqual(report['native_calls'], 0)
                self.assertEqual(report['fallback_calls'], 1)
            self.assertEqual(actual, expected)
            self.assertEqual(b.calls, a.calls)


if __name__ == '__main__':
    unittest.main()

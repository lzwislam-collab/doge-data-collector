"""Local-only differential rescue tests; synthetic Git fixtures stay protected.

Market HTTPS alone is replaced by a deterministic response. The original
collector, frozen scoring/guards, Git history, commit/rebase/push and remote
readback execute against real local repositories. No production request.
"""
from contextlib import contextmanager, nullcontext, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
NAMES = ('direction_baseline_v42', 'direction_forward_v42', 'stage5_70pct_audit',
         'stage5_forward_quality', 'forward_validation', 'forward_validation_v2',
         'spot_plus_quote_watch', 'quote_evidence_v2', 'quote_capture_retry',
         'publish_forward_evidence', 'quote_only_rescue', 'runtime_publication_state',
         'quote_rescue_runtime')
PROTOCOLS = ('data/stage5/forward_validation_protocol.json',
             'data/stage5/forward_validation_protocol_v2.json')


@contextmanager
def graph(root):
    saved = {name: sys.modules.get(name) for name in NAMES}
    prior_path = list(sys.path)
    try:
        for name in NAMES:
            sys.modules.pop(name, None)
        sys.path.insert(0, str(root))
        loaded = {name: importlib.import_module(name) for name in NAMES[:-1]}
        if (root / 'quote_rescue_runtime.py').is_file():
            loaded['quote_rescue_runtime'] = importlib.import_module('quote_rescue_runtime')
        yield loaded
    finally:
        sys.path[:] = prior_path
        for name, module in saved.items():
            sys.modules.pop(name, None)
            if module is not None:
                sys.modules[name] = module


class RuntimeRescueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='quote-rescue-native-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name)
        self.root = self.area / 'work'
        self.root.mkdir()
        self.remote = self.area / 'remote.git'
        for name in NAMES:
            source = HERE / (name + '.py')
            if source.is_file():
                (self.root / source.name).write_bytes(source.read_bytes())
        for relative in PROTOCOLS:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((HERE / relative).read_bytes())
        self.context = graph(self.root)
        modules = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.rescue = modules['quote_only_rescue']
        self.base = modules['forward_validation']
        self.publisher = modules['publish_forward_evidence']
        self.evidence = modules['quote_evidence_v2']
        self.frozen = modules['direction_forward_v42']
        self.native = modules['runtime_publication_state']
        self.runtime = modules.get('quote_rescue_runtime')
        self.now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=1)
        self.target = self.now - timedelta(minutes=5)
        issued = self.target - timedelta(hours=1)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'core.longpaths', 'true')
        self.git('config', 'user.name', 'Protected Rescue QA')
        self.git('config', 'user.email', 'qa@example.invalid')
        (self.root / '.gitignore').write_text('quote_rescue_result.json\n', encoding='utf-8')
        row = dict(zip(self.frozen.FCOLS, ['p', self.frozen.MODEL, issued.isoformat(),
            issued.isoformat(), issued.isoformat(), '1', '1', self.target.isoformat(),
            '.5', 'UP', 'UP', 'DOWN', 'synthetic']))
        self.rows(self.base.REG, {'p': row}, self.frozen.FCOLS)
        self.rows(self.base.SCORES, {}, self.frozen.SCOLS)
        bindings = {}
        qid = self.evidence.save_quote(self.root, self.raw(issued, '1'), issued)
        self.evidence.bind(bindings, 'p', 'ISSUE', qid)
        self.rows(self.evidence.BINDINGS, bindings, self.evidence.BCOLS)
        self.commit('synthetic pre-target state', issued + timedelta(seconds=30))
        subprocess.run(['git', 'init', '--bare', '-q', str(self.remote)], check=True, capture_output=True)
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-q', 'origin', 'main')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True).stdout

    def rows(self, relative, rows, columns):
        self.frozen.write(self.root / relative, self.frozen.serial(rows, columns))

    def commit(self, message, stamp=None):
        self.git('add', '-A')
        env = dict(os.environ)
        if stamp is not None:
            env.update(GIT_AUTHOR_DATE=stamp.isoformat(), GIT_COMMITTER_DATE=stamp.isoformat())
        subprocess.run(['git', 'commit', '--allow-empty', '-qm', message], cwd=self.root,
                       env=env, check=True, capture_output=True)

    def raw(self, stamp, price='1.1'):
        return json.dumps({'code': '0', 'data': [{'instId': 'DOGE-USDT', 'last': price,
                           'ts': str(int(stamp.timestamp() * 1000))}]})

    def installed(self):
        return self.runtime.installed(self.root) if self.runtime else nullcontext({})

    @contextmanager
    def market(self, failures=0):
        now = self.now
        raw = self.raw(now).encode('utf-8')
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return raw
        responses = [TimeoutError('synthetic transport timeout')] * failures + [Response()]
        with patch.object(self.evidence, 'datetime', Clock), \
             patch.object(self.rescue, 'datetime', Clock), \
             patch.object(self.publisher, 'datetime', Clock), \
             patch.object(self.evidence.urllib.request, 'urlopen', side_effect=responses), \
             patch.object(self.rescue.retry.time, 'sleep'):
            yield

    @contextmanager
    def commands(self, corrupt_frames=False):
        actual = subprocess.run
        seen = []
        def read(args, **kwargs):
            seen.append(list(args))
            result = actual(args, **kwargs)
            if corrupt_frames and args[:3] == ['git', 'cat-file', '--batch']:
                return subprocess.CompletedProcess(args, result.returncode, b'bad frame\n', result.stderr)
            return result
        with patch.object(subprocess, 'run', side_effect=read):
            yield seen

    def require_runtime(self):
        self.assertIsNotNone(self.runtime, 'rescue runtime adapter is not implemented')
        return self.runtime

    def test_real_capture_and_bare_remote_readback_use_native_fresh_histories(self):
        registry = (self.root / self.base.REG).read_bytes()
        with self.commands() as seen, self.market(), self.installed() as report:
            result = self.rescue.run(self.root, ['p'], clock=lambda: self.now)
        self.assertEqual(result['status'], 'verified_window_quote_rescue')
        self.assertEqual(result['scored_ids'], ['p'])
        self.assertFalse(result['publication']['new_predictions'])
        self.assertEqual((self.root / self.base.REG).read_bytes(), registry)
        public = self.git('--git-dir=' + str(self.remote), 'show', 'main:' + self.base.SCORES)
        self.assertIn(b'scored', public)
        # RED against the real old entry: capture/publication succeeds, but no
        # native frames are read. No fake verifier can satisfy this assertion.
        self.assertGreater(sum(args[1:3] == ['cat-file', '--batch'] for args in seen), 0,
                           'original rescue still launches only legacy Git history readers')
        self.assertEqual(report['native_validation_calls'], 3)
        self.assertEqual(report['native_history_calls'], 1)

    def test_all_three_histories_and_early_bound_alias_equal_original_and_fresh(self):
        runtime = self.require_runtime()
        rows = self.frozen.read_rows(self.root / self.evidence.BINDINGS, self.evidence.BCOLS)
        want = self.evidence.history_receipts(self.root, self.evidence.BINDINGS, self.evidence.BCOLS, rows)
        original_base = self.base.history_receipts
        with self.commands() as seen, runtime.installed(self.root) as report:
            self.assertIs(self.base.history_receipts, original_base)
            self.assertIsNone(self.publisher.validate_state(self.root))
            got = self.evidence.history_receipts(self.root, self.evidence.BINDINGS, self.evidence.BCOLS, rows)
            self.assertEqual(got, want)
            first = sum(args[1:3] == ['cat-file', '--batch'] for args in seen)
            self.publisher.validate_state(self.root)
            self.evidence.history_receipts(self.root, self.evidence.BINDINGS, self.evidence.BCOLS, rows)
            self.assertEqual(sum(args[1:3] == ['cat-file', '--batch'] for args in seen), 2 * first)
        self.assertEqual(report['native_validation_calls'], 2)
        self.assertEqual(report['native_history_calls'], 2)
        self.assertTrue(all(h['mode'] == 'native_batch' for call in report['calls']
                            for h in call.get('diagnostics', {}).get('histories', [])))

    def test_unknown_backend_module_function_alias_and_call_path_use_full_legacy(self):
        runtime = self.require_runtime()
        cases = [(self.native, 'validate_state', lambda *a, **k: None),
                 (self.native, '_snapshot', lambda *a, **k: ({}, True)),
                 (self.rescue, 'guard_protocol', lambda *a: None),
                 (self.base, 'REG', 'data/direction_v42/forward_registry.csv.invalid'),
                 (self.evidence, 'history_receipts', lambda *a: {})]
        # REG mutation is tested separately as a genuine original error: do not
        # mistake a changed business global for a successful empty validation.
        for module, name, changed in cases[:3] + cases[4:]:
            with self.subTest(name=name), patch.object(module, name, changed), \
                 self.commands() as seen, runtime.installed(self.root) as report:
                self.publisher.validate_state(self.root)
            self.assertFalse(any(args[1:3] == ['cat-file', '--batch'] for args in seen))
            self.assertEqual(report['native_validation_calls'], 0)
        other = self.area / 'other'
        subprocess.run(['git', '-c', 'core.longpaths=true', '-c', 'core.autocrlf=false',
                        'clone', '-q', str(self.root), str(other)], check=True, capture_output=True)
        with self.commands() as seen, runtime.installed(self.root):
            self.publisher.validate_state(other)
        self.assertFalse(any(args[1:3] == ['cat-file', '--batch'] for args in seen))

    def test_raw_source_or_protocol_change_disables_optimization_without_refreezing(self):
        runtime = self.require_runtime()
        for relative in ('quote_only_rescue.py', PROTOCOLS[0]):
            path = self.root / relative
            before = path.read_bytes()
            try:
                path.write_bytes(before + b'\n')
                with self.commands() as seen, runtime.installed(self.root) as report:
                    self.publisher.validate_state(self.root)
                self.assertEqual(report['native_validation_calls'], 0)
                self.assertFalse(any(args[1:3] == ['cat-file', '--batch'] for args in seen))
            finally:
                path.write_bytes(before)

    def test_normal_HEAD_advance_still_uses_native(self):
        runtime = self.require_runtime()
        self.commit('ordinary later HEAD')
        with runtime.installed(self.root) as report:
            self.publisher.validate_state(self.root)
        self.assertEqual(report['native_validation_calls'], 1)

    def test_replaced_adapter_backend_module_cannot_supply_a_fake_success(self):
        runtime = self.require_runtime()
        fake = types.ModuleType('runtime_publication_state')
        fake.__dict__.update(self.native.__dict__)
        fake.validate_state = lambda *args, **kwargs: None
        with patch.object(runtime, 'native', fake), self.commands() as seen, \
             runtime.installed(self.root) as report:
            self.publisher.validate_state(self.root)
        self.assertEqual(report['native_validation_calls'], 0)
        self.assertTrue(any(args[1] == 'show' for args in seen), 'complete original must execute')

    def test_function_code_with_foreign_filename_uses_full_original(self):
        runtime = self.require_runtime()
        original = self.native.validate_state
        changed = types.FunctionType(original.__code__.replace(co_filename='other.py'),
                                     original.__globals__, original.__name__, original.__defaults__)
        with patch.object(self.native, 'validate_state', changed), self.commands() as seen, \
             runtime.installed(self.root) as report:
            self.publisher.validate_state(self.root)
        self.assertEqual(report['native_validation_calls'], 0)
        self.assertTrue(any(args[1] == 'show' for args in seen))

    def test_initial_missing_or_untrusted_backend_never_executes_and_preserves_original_cli(self):
        self.require_runtime()
        def cli(module):
            source = 'import sys;sys.path.insert(0,' + repr(str(self.root)) + ');import ' + module + ' as m;raise SystemExit(m.main())'
            return subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', source,
                                   '--expected-ids-json', '{}'], cwd=self.root, capture_output=True)
        want = cli('quote_only_rescue')
        self.assertEqual(want.returncode, 1)
        want_result = (self.root / 'quote_rescue_result.json').read_bytes()
        backend = self.root / 'runtime_publication_state.py'
        original = backend.read_bytes()
        samples = [None, b'def syntax error\n',
                   b"from pathlib import Path\nPath('untrusted_backend_executed').write_text('bad')\n"]
        try:
            for raw in samples:
                with self.subTest(raw=raw):
                    (self.root / 'quote_rescue_result.json').unlink(missing_ok=True)
                    if raw is None: backend.unlink()
                    else: backend.write_bytes(raw)
                    got = cli('quote_rescue_runtime')
                    self.assertEqual(got.returncode, want.returncode)
                    self.assertEqual(got.stdout, want.stdout)
                    self.assertTrue((self.root / 'quote_rescue_result.json').is_file())
                    self.assertEqual((self.root / 'quote_rescue_result.json').read_bytes(), want_result)
                    self.assertFalse((self.root / 'untrusted_backend_executed').exists())
        finally:
            backend.write_bytes(original)

    def test_frame_fallback_is_reported_as_fallback_not_native_success(self):
        runtime = self.require_runtime()
        with self.commands(corrupt_frames=True), runtime.installed(self.root) as report:
            self.publisher.validate_state(self.root)
        self.assertEqual(report['native_validation_calls'], 0)
        self.assertEqual(report['native_validation_attempts'], 1)
        self.assertEqual(report['fallback_validation_calls'], 1)

    def test_readonly_probe_cli_completes_original_and_native_without_capture(self):
        self.require_runtime()
        probe = self.root / 'test_quote_rescue_runtime.py'
        probe.write_bytes(Path(__file__).read_bytes())
        self.commit('include local readonly probe')
        before = (self.root / self.base.REG).read_bytes(), (self.root / self.base.SCORES).read_bytes()
        process = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', str(probe),
                                  '--readonly-probe'], cwd=self.root, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', 'replace'))
        result = json.loads(process.stdout)
        self.assertEqual(result['status'], 'verified_readonly_validation_probe')
        self.assertEqual(result['binding_receipt_count'], 1)
        self.assertTrue(result['unchanged'])
        self.assertEqual(result['native_fallback_count'], 0)
        self.assertEqual(before, ((self.root / self.base.REG).read_bytes(),
                                  (self.root / self.base.SCORES).read_bytes()))
        self.assertFalse((self.root / 'quote_rescue_result.json').exists())

    def test_backend_frame_failure_replays_full_original_each_history_without_recursion(self):
        runtime = self.require_runtime()
        rows = self.frozen.read_rows(self.root / self.evidence.BINDINGS, self.evidence.BCOLS)
        want = self.base.history_receipts(self.root, self.evidence.BINDINGS, self.evidence.BCOLS, rows)
        with self.commands(corrupt_frames=True), runtime.installed(self.root) as report:
            self.assertIsNone(self.publisher.validate_state(self.root))
            self.assertEqual(self.evidence.history_receipts(self.root, self.evidence.BINDINGS,
                                                          self.evidence.BCOLS, rows), want)
        reports = report['calls']
        self.assertEqual([h['mode'] for h in reports[0]['diagnostics']['histories']],
                         ['legacy_full_fallback'] * 3)
        self.assertEqual(reports[1]['diagnostics']['mode'], 'legacy_full_fallback')

    def test_original_business_error_propagates_once_and_restores_hooks(self):
        runtime = self.require_runtime()
        rows = self.frozen.read_rows(self.root / self.base.REG, self.frozen.FCOLS)
        rows['p']['reference_price'] = '2'
        self.rows(self.base.REG, rows, self.frozen.FCOLS)
        self.commit('deliberate historical mutation')
        with self.assertRaises(ValueError) as legacy:
            self.publisher.validate_state(self.root)
        original = self.publisher.validate_state
        alias = self.evidence.history_receipts
        with self.commands() as seen, self.assertRaises(type(legacy.exception)) as current:
            with runtime.installed(self.root):
                self.publisher.validate_state(self.root)
        self.assertEqual(str(current.exception), str(legacy.exception))
        self.assertFalse(any(args[1] == 'show' for args in seen), 'business errors must not replay legacy')
        self.assertIs(self.publisher.validate_state, original)
        self.assertIs(self.evidence.history_receipts, alias)

    def test_base_exception_and_partial_install_restore_both_original_attributes(self):
        runtime = self.require_runtime()
        original, alias = self.publisher.validate_state, self.evidence.history_receipts
        with self.assertRaises(KeyboardInterrupt):
            with runtime.installed(self.root):
                raise KeyboardInterrupt('synthetic cancellation')
        self.assertIs(self.publisher.validate_state, original)
        self.assertIs(self.evidence.history_receipts, alias)
        class RefusingModule(types.ModuleType):
            def __setattr__(module, name, value):
                if name == 'history_receipts' and value is not alias:
                    raise KeyboardInterrupt('synthetic half installation')
                return super().__setattr__(name, value)
        previous = self.evidence.__class__
        self.evidence.__class__ = RefusingModule
        try:
            with self.assertRaises(KeyboardInterrupt):
                with runtime.installed(self.root): pass
        finally:
            self.evidence.__class__ = previous
        self.assertIs(self.publisher.validate_state, original)
        self.assertIs(self.evidence.history_receipts, alias)

    def test_changed_early_alias_is_preserved_and_never_rebound(self):
        runtime = self.require_runtime()
        original = self.evidence.history_receipts
        events = []
        def changed(*args):
            events.append(args[1])
            return original(*args)
        with patch.object(self.evidence, 'history_receipts', changed):
            with runtime.installed(self.root):
                self.assertIs(self.evidence.history_receipts, changed)
                rows = self.frozen.read_rows(self.root / self.evidence.BINDINGS, self.evidence.BCOLS)
                self.evidence.history_receipts(self.root, self.evidence.BINDINGS, self.evidence.BCOLS, rows)
            self.assertIs(self.evidence.history_receipts, changed)
        self.assertEqual(events, [self.evidence.BINDINGS])

    def test_transient_retry_preserves_real_capture_and_fresh_alias_check(self):
        runtime = self.require_runtime()
        with self.market(failures=1), runtime.installed(self.root) as report:
            result = self.rescue.run(self.root, ['p'], clock=lambda: self.now)
        self.assertEqual(result['status'], 'verified_window_quote_rescue')
        self.assertEqual(result['capture']['attempts'], 2)
        self.assertEqual(report['native_history_calls'], 1)

    def test_expired_and_unsafe_targets_never_capture_with_runtime(self):
        runtime = self.require_runtime()
        for now, status in ((self.target + timedelta(minutes=21), 'skip_no_open_unscored_target'),
                            (self.now, 'skip_unsafe_aggregate_watch_batch')):
            if now == self.now:
                reg = self.frozen.read_rows(self.root / self.base.REG, self.frozen.FCOLS)
                reg['late'] = dict(reg['p'], forecast_id='late',
                                  target_utc=(self.now - timedelta(minutes=26)).isoformat(),
                                  issued_at_utc=(self.now - timedelta(hours=1, minutes=26)).isoformat(),
                                  source_quote_utc=(self.now - timedelta(hours=1, minutes=26)).isoformat())
                self.rows(self.base.REG, reg, self.frozen.FCOLS)
                bindings = self.frozen.read_rows(self.root / self.evidence.BINDINGS, self.evidence.BCOLS)
                issued = self.frozen.utc(reg['late']['issued_at_utc'])
                qid = self.evidence.save_quote(self.root, self.raw(issued, '1'), issued)
                self.evidence.bind(bindings, 'late', 'ISSUE', qid)
                self.rows(self.evidence.BINDINGS, bindings, self.evidence.BCOLS)
                self.commit('second synthetic late target')
            with patch.object(self.evidence.urllib.request, 'urlopen', side_effect=AssertionError('no HTTP')), \
                 runtime.installed(self.root):
                result = self.rescue.run(self.root, ['p'], clock=lambda: now)
            self.assertEqual(result['status'], status)

    def test_cli_delegates_original_success_and_blocked_results_and_restores(self):
        runtime = self.require_runtime()
        old_cwd = Path.cwd()
        original, alias = self.publisher.validate_state, self.evidence.history_receipts
        try:
            os.chdir(self.root)
            for argument, want in (('{}', 1), ('["p"]', 0)):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(sys, 'argv', ['quote_only_rescue.py', '--expected-ids-json', argument]), \
                     self.market(), redirect_stdout(stdout), redirect_stderr(stderr):
                    code = runtime.main()
                result = json.loads((self.root / 'quote_rescue_result.json').read_text())
                self.assertEqual(code, want)
                self.assertEqual(json.loads(stdout.getvalue().splitlines()[-1]), result)
                self.assertEqual(result['status'], 'blocked' if want else 'verified_window_quote_rescue')
                self.assertNotIn('quote_rescue_runtime', stdout.getvalue())
                self.assertIs(self.publisher.validate_state, original)
                self.assertIs(self.evidence.history_receipts, alias)
            with patch.object(sys, 'argv', ['original-prog']), redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit) as missing:
                runtime.main()
            self.assertEqual(missing.exception.code, 2)
        finally:
            os.chdir(old_cwd)

    def test_failed_stderr_diagnostics_cannot_change_validation_result(self):
        runtime = self.require_runtime()
        class Broken:
            def write(self, value): raise OSError('synthetic closed stderr')
            def flush(self): raise OSError('synthetic closed stderr')
        with patch.object(sys, 'stderr', Broken()), runtime.installed(self.root):
            self.assertIsNone(self.publisher.validate_state(self.root))


def readonly_probe():
    """Explicit CI-only read comparison. Never invoke rescue/main/collector."""
    import hashlib
    import shlex
    import time
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(HERE))
    runtime = importlib.import_module('quote_rescue_runtime')
    publisher, evidence, base = runtime.publisher, runtime.evidence, runtime.base
    os.environ['GIT_OPTIONAL_LOCKS'] = '0'
    os.environ['GIT_NO_LAZY_FETCH'] = '1'
    os.environ['GIT_TERMINAL_PROMPT'] = '0'
    frames = {'batch': 0, 'batch_check': 0}

    def audit(event, args):
        if event.startswith('socket.') or event in ('urllib.Request', 'http.client.connect'):
            raise RuntimeError('readonly probe prohibits network operations')
        if event == 'subprocess.Popen':
            argv = shlex.split(args[1], posix=False) if isinstance(args[1], str) else args[1]
            if (Path(argv[0].strip('"')).name.lower() not in ('git', 'git.exe')
                    or argv[1] not in ('hash-object', 'rev-parse', 'log', 'show', 'cat-file',
                                        'for-each-ref', 'ls-files') or '-w' in argv):
                raise RuntimeError('readonly probe prohibits mutating/non-Git subprocess')
            if argv[1] == 'cat-file':
                option = argv[2].strip('"')
                if option == '--batch': frames['batch'] += 1
                elif option.startswith('--batch-check='): frames['batch_check'] += 1
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                raise RuntimeError('readonly probe prohibits filesystem writes')
        if event in ('os.remove', 'os.rename', 'os.replace', 'os.mkdir', 'os.rmdir',
                     'os.truncate', 'os.link', 'os.symlink'):
            raise RuntimeError('readonly probe prohibits filesystem mutation')

    sys.addaudithook(audit)
    def git(*args):
        return subprocess.run(['git', *args], cwd=HERE, check=True, capture_output=True).stdout
    def snapshot():
        paths = set(runtime.PINS) | {'quote_rescue_runtime.py', 'test_quote_rescue_runtime.py',
                                     base.REG, base.SCORES, evidence.BINDINGS}
        paths.update(p.relative_to(HERE).as_posix()
                     for p in (HERE / evidence.BASE / 'snapshots').glob('*/*.json'))
        files = {path: hashlib.sha256((HERE / path).read_bytes()).hexdigest()
                 for path in sorted(paths)}
        private = Path(git('rev-parse', '--git-dir').decode('utf-8').strip())
        if not private.is_absolute(): private = HERE / private
        common = Path(git('rev-parse', '--git-common-dir').decode('utf-8').strip())
        if not common.is_absolute(): common = HERE / common
        git_files = {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in (private / 'index', private / 'HEAD', private / 'config.worktree',
                                  common / 'config', common / 'packed-refs') if path.exists()}
        return {'files': files, 'git_files': git_files,
                'head': git('rev-parse', 'HEAD').decode('ascii').strip(),
                'refs': git('for-each-ref', '--format=%(refname) %(objectname)').decode('utf-8')}
    before = snapshot()
    bindings = publisher.frozen.read_rows(HERE / evidence.BINDINGS, evidence.BCOLS)
    started = time.perf_counter()
    old_result = publisher.validate_state(HERE)
    old_receipts = evidence.history_receipts(HERE, evidence.BINDINGS, evidence.BCOLS, bindings)
    original_seconds = time.perf_counter() - started
    started = time.perf_counter()
    with runtime.installed(HERE) as report:
        new_result = publisher.validate_state(HERE)
        new_receipts = evidence.history_receipts(HERE, evidence.BINDINGS, evidence.BCOLS, bindings)
    native_seconds = time.perf_counter() - started
    if old_result != new_result or old_receipts != new_receipts:
        raise AssertionError('original/native full returns differ')
    if (report['native_validation_calls'] != 1 or report['native_history_calls'] != 1
            or report['legacy_validation_calls'] or report['legacy_history_calls']):
        raise AssertionError('reviewed native route was not used')
    for call in report['calls']:
        diagnostics = call['diagnostics']
        if diagnostics.get('mode') != 'native_batch' or diagnostics.get('status') != 'ok':
            raise AssertionError('native check fell back or failed')
        if call['kind'] == 'validation':
            if len(diagnostics.get('histories', [])) != 3:
                raise AssertionError('three complete histories required')
            if any(h.get('mode') != 'native_batch' or h.get('status') != 'ok'
                   for h in diagnostics['histories']):
                raise AssertionError('a complete history fell back or failed')
    expected_frames = sum(call['diagnostics']['native_batch_calls'] for call in report['calls'])
    if not expected_frames or frames != {'batch': expected_frames, 'batch_check': expected_frames}:
        raise AssertionError('native diagnostics do not match actual Git frame processes')
    after = snapshot()
    if before != after:
        raise AssertionError('source/input/HEAD/refs/index changed')
    receipts_json = json.dumps(new_receipts, sort_keys=True, separators=(',', ':')).encode('utf-8')
    print(json.dumps({'status': 'verified_readonly_validation_probe',
        'head': before['head'], 'original_seconds': original_seconds,
        'native_seconds': native_seconds, 'binding_receipt_count': len(new_receipts),
        'binding_receipts_sha256': hashlib.sha256(receipts_json).hexdigest(),
        'actual_native_git_frame_processes': frames,
        'native_fallback_count': 0, 'unchanged': True, 'diagnostics': report,
        'scope': 'validation segment only; not full rescue or 180s/coverage acceptance'}, sort_keys=True))


if __name__ == '__main__':
    if sys.argv[1:] == ['--readonly-probe']:
        readonly_probe()
    else:
        unittest.main()

import io
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import runtime_audit_pipeline_profile as profile


URL = 'https://api.github.com/repos/lzwislam-collab/doge-data-collector/actions/runs/1'


class Response(io.BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json', 'Authorization': 'must-not-be-recorded'}
    def geturl(self): return URL


class QuotaDiagnosticTests(unittest.TestCase):
    def test_quota_headers_survive_success_and_http_failure_without_sensitive_headers(self):
        wanted = {'Content-Type': 'application/json', 'Content-Length': '2',
            'Date': 'Wed, 11 Nov 2026 17:00:00 GMT', 'ETag': 'original-etag',
            'X-RateLimit-Limit': '1000', 'x-ratelimit-remaining': '0',
            'X-RATELIMIT-RESET': '1794417000', 'X-RateLimit-Resource': 'core',
            'X-RateLimit-Used': '1000', 'Retry-After': '17'}
        private = {name: 'private-marker' for name in ('Authorization', 'Proxy-Authorization',
            'Cookie', 'Set-Cookie', 'X-Api-Key', 'X-GitHub-Token', 'X-Unknown-Header')}
        for status in (200, 403):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as name:
                response = Response(b'{}')
                response.headers = dict(wanted, **private)
                replacement = {'return_value': response} if status == 200 else {'side_effect':
                    urllib.error.HTTPError(URL, status, 'Forbidden', response.headers, io.BytesIO(b'{}'))}
                path = Path(name)/'quota-reply.json'
                with patch.object(profile.urllib.request, 'urlopen', **replacement):
                    with profile.transport('record', path):
                        if status == 200:
                            profile.urllib.request.urlopen(URL).close()
                        else:
                            with self.assertRaises(urllib.error.HTTPError):
                                profile.urllib.request.urlopen(URL)
                fixture = json.loads(path.read_text())
                self.assertEqual(fixture['entries'][0]['headers'], wanted)
                self.assertNotIn('private-marker', path.read_text())

    def quota_program(self):
        import textwrap
        workflow = Path(__file__).resolve().parent/'.github/workflows/runtime_deadline_tests.yml'
        text = workflow.read_text(encoding='utf-8')
        opening = "python -B - <<'PY_QUOTA'\n"
        self.assertIn(opening, text, 'whole CLI step must include one diagnostic probe')
        program, separator, tail = text.split(opening, 1)[1].partition('          PY_QUOTA\n')
        self.assertTrue(separator)
        original = 'python -B runtime_audit_pipeline_profile.py --source-root "$GITHUB_WORKSPACE" --ref "$GITHUB_SHA" --output "$RUNNER_TEMP/source-audit-native-profile"'
        self.assertIn(original, tail)
        self.assertNotIn('sleep', program)
        return textwrap.dedent(program)

    def run_probe(self, reply=None, error=None):
        program = self.quota_program()
        replacement = {'side_effect': error} if error is not None else {'return_value': reply}
        with patch.dict(profile.os.environ, {'GITHUB_TOKEN': 'private-token-marker'}, clear=True), \
                patch.object(profile.urllib.request, 'urlopen', **replacement) as request, \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            exec(compile(program, '<actual-workflow-quota-probe>', 'exec'), {})
        request.assert_called_once()
        call = request.call_args
        self.assertEqual(call.args[0].full_url, 'https://api.github.com/rate_limit')
        self.assertEqual(call.args[0].get_method(), 'GET')
        self.assertEqual(call.args[0].get_header('Authorization'), 'Bearer private-token-marker')
        self.assertEqual(call.kwargs, {'timeout': 6})
        self.assertNotIn('private-token-marker', output.getvalue())
        return json.loads(output.getvalue())

    def test_actual_workflow_probe_reads_same_token_core_quota_once(self):
        response = Response(json.dumps({'resources': {'core': {
            'limit': 1000, 'remaining': 7, 'used': 993, 'reset': 1794417000}}}).encode())
        response.headers = {'Date': 'Wed, 11 Nov 2026 17:00:00 GMT'}
        result = self.run_probe(reply=response)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['core'], {'limit': 1000, 'remaining': 7, 'used': 993, 'reset': 1794417000})
        self.assertEqual(result['server_date'], response.headers['Date'])
        from datetime import datetime, timezone
        self.assertLess(abs((datetime.now(timezone.utc)-datetime.fromisoformat(result['actual_utc'])).total_seconds()), 10)

    def test_probe_errors_are_diagnostics_without_retry_or_interrupting_cli(self):
        for error in (TimeoutError('private-token-marker'),
                urllib.error.HTTPError('https://api.github.com/rate_limit', 403,
                    'private-token-marker', {'Date': 'Wed, 11 Nov 2026 17:00:00 GMT'}, None)):
            with self.subTest(error=type(error).__name__):
                result = self.run_probe(error=error)
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['error_type'], type(error).__name__)
        for body in (b'x'*16385, b'{"resources":{"core":{"limit":true}}}'):
            with self.subTest(size=len(body)):
                response = Response(body)
                self.assertEqual(self.run_probe(reply=response)['status'], 'error')



class ProfilerOptInTests(unittest.TestCase):
    def test_formal_comparison_has_no_profiler_by_default(self):
        with patch.dict(profile.os.environ, {}, clear=True):
            self.assertFalse(profile.paired_profile_enabled(True, 'paired_cohort_audit.py'))

    def test_explicit_flag_profiles_only_native_paired_cli(self):
        with patch.dict(profile.os.environ, {'DOGE_AUDIT_PAIRED_CPU_PROFILE': '1'}):
            self.assertTrue(profile.paired_profile_enabled(True, 'paired_cohort_audit.py'))
            self.assertFalse(profile.paired_profile_enabled(False, 'paired_cohort_audit.py'))
            self.assertFalse(profile.paired_profile_enabled(True, 'forward_validation_v2.py'))
        with patch.dict(profile.os.environ, {'DOGE_AUDIT_PAIRED_CPU_PROFILE': '0'}):
            self.assertFalse(profile.paired_profile_enabled(True, 'paired_cohort_audit.py'))


class PairedProfilerTests(unittest.TestCase):
    def test_disabled_diagnostic_has_no_output_file(self):
        with tempfile.TemporaryDirectory() as name:
            fixture = Path(name) / 'captured-get' / 'paired.json'
            fixture.parent.mkdir()
            with profile.paired_cpu_profile(False, fixture):
                self.assertEqual(sum(range(10)), 45)
            self.assertFalse((Path(name) / 'native-paired.cprofile').exists())

    def test_enabled_diagnostic_writes_readable_real_profile(self):
        import pstats
        with tempfile.TemporaryDirectory() as name:
            fixture = Path(name) / 'captured-get' / 'paired.json'
            fixture.parent.mkdir()
            with profile.paired_cpu_profile(True, fixture):
                self.assertEqual(sum(range(10)), 45)
            stats = pstats.Stats(str(Path(name) / 'native-paired.cprofile'))
            self.assertGreater(stats.total_calls, 0)

    def test_profile_is_saved_and_original_exception_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            fixture = Path(name) / 'captured-get' / 'paired.json'
            fixture.parent.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'original failure'):
                with profile.paired_cpu_profile(True, fixture):
                    raise RuntimeError('original failure')
            self.assertTrue((Path(name) / 'native-paired.cprofile').is_file())


class CanonicalEvidenceTests(unittest.TestCase):
    def rows(self):
        base = dict(kind='runtime_publication_state_validation', mode='native_batch',
                    status='ok', validation_status='ok', native_batch_calls=1)
        pair = dict(base, kind='runtime_paired_history_validation')
        cache = dict(kind='runtime_canonical_json_transport',
                     implementation='paired_canonical_utf8_lru_v2_fit1h_direct', mode='canonical_bytes_lru',
                     status='ok', module_sha256='a'*64, calls=2, hits=1, misses=1, direct_calls=0,
                     fallback_calls=0, sha256_calls=2, encoded_calls=1,
                     evictions=0, entries=1, cached_bytes=10)
        return base, pair, cache

    def test_complete_paired_performance_needs_real_cache_evidence(self):
        base, pair, cache = self.rows()
        with self.assertRaises(profile.ProfileError):
            profile.validate_native_diagnostics(
                '\n'.join(map(json.dumps, (base, pair))), 'paired_cohort_audit.py')

    def test_cache_hits_hash_counts_identity_and_shape_are_checked(self):
        base, pair, cache = self.rows()
        for bad in (dict(cache, hits=0), dict(cache, fallback_calls=1), dict(cache, sha256_calls=1),
                    dict(cache, module_sha256='b'*64), dict(cache, calls=True),
                    dict(cache, secret='must-not-be-accepted')):
            with self.subTest(bad=bad), self.assertRaises(profile.ProfileError):
                profile.validate_native_diagnostics(
                    '\n'.join(map(json.dumps, (base, pair, bad))),
                    'paired_cohort_audit.py', expected_launcher_sha256='a'*64)

    def test_valid_evidence_proves_transport_and_every_successful_sha(self):
        rows = self.rows()
        result = profile.validate_native_diagnostics(
            '\n'.join(map(json.dumps, rows)), 'paired_cohort_audit.py',
            expected_launcher_sha256='a'*64)
        self.assertEqual(len(result), 3)

    def test_cache_evidence_is_rejected_outside_paired_cli(self):
        base, pair, cache = self.rows()
        with self.assertRaises(profile.ProfileError):
            profile.validate_native_diagnostics(
                '\n'.join(map(json.dumps, (base, cache))), 'p0_operational_audit.py')


class ProfileTests(unittest.TestCase):
    def test_record_replay_preserves_bytes_and_measured_network_latency(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'get.json'
            with patch.object(profile.urllib.request, 'urlopen', return_value=Response(b'{"actual":true}')):
                with profile.transport('record', path):
                    with profile.urllib.request.urlopen(URL) as response:
                        self.assertEqual(response.read(), b'{"actual":true}')
            fixture = json.loads(path.read_text())
            self.assertNotIn('Authorization', json.dumps(fixture))
            self.assertGreaterEqual(fixture['entries'][0]['elapsed_seconds'], 0)
            with patch.object(profile.time, 'sleep') as sleep:
                with profile.transport('replay', path):
                    with profile.urllib.request.urlopen(URL) as response:
                        self.assertEqual(response.read(), b'{"actual":true}')
                sleep.assert_called_once_with(fixture['entries'][0]['elapsed_seconds'])

    def test_unrecorded_request_fails_closed_without_live_network(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'get.json'
            path.write_text('{"entries":[]}')
            with patch.object(profile.urllib.request, 'urlopen') as network:
                with self.assertRaises(profile.ProfileError):
                    with profile.transport('replay', path):
                        profile.urllib.request.urlopen(URL)
                network.assert_not_called()

    def test_unconsumed_reply_fails_equivalence(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'get.json'
            with patch.object(profile.urllib.request, 'urlopen', return_value=Response(b'{}')):
                with profile.transport('record', path):
                    profile.urllib.request.urlopen(URL).close()
            with self.assertRaisesRegex(profile.ProfileError, 'unconsumed'):
                with profile.transport('replay', path): pass

    def test_fixture_body_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'get.json'
            with patch.object(profile.urllib.request, 'urlopen', return_value=Response(b'{}')):
                with profile.transport('record', path):
                    profile.urllib.request.urlopen(URL).close()
            fixture = json.loads(path.read_text())
            fixture['entries'][0]['body_base64'] = 'Zm9yZ2Vk'
            path.write_text(json.dumps(fixture))
            with self.assertRaisesRegex(profile.ProfileError, 'digest'):
                with profile.transport('replay', path):
                    profile.urllib.request.urlopen(URL)

    def test_http_error_status_and_body_are_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'get.json'
            error = urllib.error.HTTPError(URL, 404, 'Not Found', {}, io.BytesIO(b'actual missing'))
            with patch.object(profile.urllib.request, 'urlopen', side_effect=error):
                with profile.transport('record', path):
                    with self.assertRaises(urllib.error.HTTPError) as found:
                        profile.urllib.request.urlopen(URL)
                    self.assertEqual(found.exception.read(), b'actual missing')
            with patch.object(profile.time, 'sleep'):
                with profile.transport('replay', path):
                    with self.assertRaises(urllib.error.HTTPError) as found:
                        profile.urllib.request.urlopen(URL)
                    self.assertEqual(found.exception.code, 404)
                    self.assertEqual(found.exception.read(), b'actual missing')

    def test_clock_is_true_capture_value_and_restored_on_systemexit(self):
        import datetime
        original = datetime.datetime
        at = original(2026, 11, 9, 22, 45, tzinfo=datetime.timezone.utc)
        with self.assertRaises(SystemExit):
            with profile.frozen_clock(at):
                self.assertEqual(datetime.datetime.now(datetime.timezone.utc), at)
                raise SystemExit(9)
        self.assertIs(datetime.datetime, original)

    def test_non_public_repo_network_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            with patch.object(profile.urllib.request, 'urlopen') as network:
                with self.assertRaises(profile.ProfileError):
                    with profile.transport('record', Path(name) / 'get.json'):
                        profile.urllib.request.urlopen('https://example.com/not-a-repository')
                network.assert_not_called()

    def test_json_only_object_key_order_is_canonicalized(self):
        self.assertEqual(profile.comparison_digest(b'{"a":1,"b":[2,3]}', 'result.json'),
                         profile.comparison_digest(b'{"b":[2,3],"a":1}', 'result.json'))
        self.assertNotEqual(profile.comparison_digest(b'{"a":1,"b":[2,3]}', 'result.json'),
                            profile.comparison_digest(b'{"a":1,"b":[3,2]}', 'result.json'))
        self.assertNotEqual(profile.comparison_digest(b'{"a":1}', 'result.json'),
                            profile.comparison_digest(b'{"a":1.0}', 'result.json'))
        self.assertNotEqual(profile.comparison_digest(b'{"a":1}', 'result.json'),
                            profile.comparison_digest(b'{"a":{"$number":"1"}}', 'result.json'))

    def test_non_json_output_remains_exact_bytes(self):
        self.assertNotEqual(profile.comparison_digest(b'a,b\n', 'rows.csv'),
                            profile.comparison_digest(b'a,b\r\n', 'rows.csv'))

    def test_missing_output_is_explicit_cli_error(self):
        with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as found:
            profile.main([])
        self.assertEqual(found.exception.code, 2)

    def test_native_performance_cannot_be_claimed_for_fallback(self):
        good = {'kind': 'runtime_publication_state_validation', 'mode': 'native_batch',
                'status': 'ok', 'validation_status': 'ok', 'native_batch_calls': 1}
        self.assertEqual(len(profile.validate_native_diagnostics(json.dumps(good), 'p0_operational_audit.py')), 1)
        for row in ({}, dict(good, mode='legacy_full_fallback'),
                    {'kind': 'runtime_audit_cli_fallback', 'reason': 'identity'},
                    dict(good, validation_status='error'), dict(good, native_batch_calls=0)):
            with self.subTest(row=row), self.assertRaises(profile.ProfileError):
                profile.validate_native_diagnostics(json.dumps(row), 'p0_operational_audit.py')
        with self.assertRaises(profile.ProfileError):
            profile.validate_native_diagnostics(json.dumps(good), 'paired_cohort_audit.py')

    def test_whole_harness_with_real_git_and_stub_clis_not_production_models(self):
        # Exercise subprocess/clock/order/output/cleanup plumbing on real Git.
        # This fixture does not prove the real three business CLIs equivalent.
        with tempfile.TemporaryDirectory() as name:
            top = Path(name); source = top / 'source'; source.mkdir()
            for relative in profile.INPUTS:
                path = source / relative; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('actual fixture input\n')
            for script, targets, needs in (
                ('forward_validation_v2.py', profile.REPORTS[:2], None),
                ('p0_operational_audit.py', profile.REPORTS[2:3], profile.REPORTS[1]),
                ('paired_cohort_audit.py', profile.REPORTS[3:], profile.REPORTS[2])):
                code = "from pathlib import Path\nimport datetime,json,os\n"
                if needs: code += "assert json.loads(Path(" + repr(needs) + ").read_text())['status']=='ok'\n"
                code += "report={'status':'ok','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'seed':os.environ['PYTHONHASHSEED']}\n"
                code += "for relative in " + repr(targets) + ":\n p=Path(relative);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(report,sort_keys=True))\n"
                code += "print(json.dumps(report,sort_keys=True))\n"
                (source / script).write_text(code)
            launcher = "import runpy,json,sys,hashlib\nfrom pathlib import Path\n"
            launcher += "def run_cli(root,script):\n"
            launcher += " kinds=['runtime_publication_state_validation']+(['runtime_paired_history_validation'] if script=='paired_cohort_audit.py' else [])\n"
            launcher += " for kind in kinds: print(json.dumps({'kind':kind,'mode':'native_batch','status':'ok','validation_status':'ok','native_batch_calls':1}),file=sys.stderr)\n"
            launcher += " if script=='paired_cohort_audit.py': print(json.dumps({'kind':'runtime_canonical_json_transport','implementation':'paired_canonical_utf8_lru_v2_fit1h_direct','mode':'canonical_bytes_lru','status':'ok','module_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'calls':2,'hits':1,'misses':1,'direct_calls':0,'fallback_calls':0,'sha256_calls':2,'encoded_calls':1,'evictions':0,'entries':1,'cached_bytes':10}),file=sys.stderr)\n"
            launcher += " if script=='p0_operational_audit.py': print(json.dumps({'kind':'runtime_p0_inventory_transport','implementation':'p0_fresh_inventory_window4_v1','mode':'fresh_bounded_inventory','status':'ok','installed':True,'window':4,'native_calls':1,'fallback_calls':0,'real_gets':2,'days':1,'module_sha256':hashlib.sha256((root/'runtime_p0_inventory_transport.py').read_bytes()).hexdigest()}),file=sys.stderr)\n"
            launcher += " runpy.run_path(str(root/script),run_name='__main__')\n"
            (source / 'runtime_audit_cli.py').write_text(launcher)
            (source / 'runtime_audit_worker.py').write_text('# fixture helper\n')
            (source / 'runtime_p0_inventory_transport.py').write_text('# fixture P0 helper identity only\n')
            subprocess.run(['git', 'init', '-q'], cwd=source, check=True)
            subprocess.run(['git', 'add', '.'], cwd=source, check=True)
            subprocess.run(['git', '-c', 'user.name=test', '-c', 'user.email=test@example.com',
                            'commit', '-qm', 'actual local fixture'], cwd=source, check=True)
            head = profile.git(source, 'rev-parse', 'HEAD').decode().strip()
            worker = types.ModuleType('runtime_audit_worker')
            def prepare(root, target, ref):
                profile.git(root, 'clone', '--shared', '--no-checkout', '-q', str(root), str(target))
                profile.git(target, 'checkout', '-q', '--detach', ref)
                return {'mode': 'real_local_git_fixture'}
            worker.prepare_checkout = prepare
            helper = types.ModuleType('runtime_audit_checkout_profile')
            helper.require_full_history = lambda root: self.assertEqual(
                profile.git(root, 'rev-parse', '--is-shallow-repository').strip(), b'false')
            helper.source_state = lambda root, path: {
                'head': profile.git(root, 'rev-parse', 'HEAD').decode().strip(),
                'status': profile.digest(profile.git(root, 'status', '--porcelain', '--untracked-files=no'))}
            old_path = list(sys.path)
            try:
                with patch.dict(sys.modules, {'runtime_audit_worker': worker,
                                             'runtime_audit_checkout_profile': helper}):
                    self.assertEqual(profile.worker(source, head, top / 'output', 60, 50), 0)
            finally: sys.path[:] = old_path
            report = json.loads((top / 'output/summary.json').read_text())
            self.assertEqual(report['status'], 'PASS_WHOLE_CLI_EQUIVALENCE')
            self.assertTrue(report['output_semantics_exactly_equal'])
            self.assertTrue(report['output_bytes_exactly_equal'])
            self.assertTrue(report['source_state_unchanged'])
            self.assertGreater(report['native_whole_source_seconds'], 0)
            self.assertFalse(report['diagnostic_components']['components_enabled'])
            self.assertEqual(report['diagnostic_components']['statistics'],'NOT_MEASURED_DISABLED')
            component=json.loads((top/'output/native-paired-components.json').read_text())
            self.assertFalse(component['enabled'])
            self.assertEqual(component['status'],'disabled')


class ComponentTimingTests(unittest.TestCase):
    def launcher(self):
        path = Path(__file__).parent / 'runtime_audit_cli.py'
        if not path.exists():
            path = path.parent / 'reference' / path.name
        spec = importlib.util.spec_from_file_location('real_timing_launcher', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def timer(self, module, **kwargs):
        self.assertTrue(hasattr(profile, 'paired_component_timing'), 'paired component timing missing')
        return profile.paired_component_timing(module, **kwargs)

    def targets(self, module):
        return [module._CanonicalBytes.digest, module.marshal.dumps, module.marshal.loads, module._json_closed,
                module.json.dumps, module.hashlib.sha256, module._hooks]

    def test_real_digest_values_and_all_sha_calls_preserved_with_component_counts(self):
        module = self.launcher()
        original = lambda v: module.hashlib.sha256(module.json.dumps(
            v, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        value = {'x': [1, 2.5, '汉字']}
        expected = original(value)
        before = self.targets(module)
        cache = module._CanonicalBytes(original)
        with self.timer(module, enabled=True) as stats:
            for _ in range(3):
                self.assertEqual(cache.digest(value), expected)
            # Unrelated operations outside digest are deliberately not counted.
            module.json.dumps({'outside': True})
        self.assertEqual(self.targets(module), before)
        self.assertEqual(stats['components']['digest']['calls'], 3)
        self.assertEqual(stats['components']['marshal']['calls'], 4)
        self.assertEqual(stats['components']['marshal_loads']['calls'], 0)
        self.assertEqual(stats['components']['eligibility']['calls'], 1)
        self.assertEqual(stats['components']['json_encode']['calls'], 1)
        self.assertEqual(stats['components']['sha256']['calls'], 3)
        self.assertTrue(stats['business_completed'])
        self.assertTrue(all(v['seconds'] >= 0 for v in stats['components'].values()))

    def test_original_exception_object_and_global_restoration(self):
        module = self.launcher()
        before = self.targets(module)
        error = RuntimeError('PRIVATE_PAYLOAD_MUST_NOT_APPEAR')
        with self.assertRaises(RuntimeError) as caught:
            with self.timer(module, enabled=True) as stats:
                raise error
        self.assertIs(caught.exception, error)
        self.assertEqual(self.targets(module), before)
        self.assertFalse(stats['business_completed'])
        self.assertNotIn('PRIVATE_PAYLOAD', json.dumps(stats))

    def test_systemexit_and_keyboardinterrupt_preserved_and_restored(self):
        module = self.launcher()
        for error in (SystemExit(7), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                before = self.targets(module)
                with self.assertRaises(type(error)) as caught:
                    with self.timer(module, enabled=True):
                        raise error
                self.assertIs(caught.exception, error)
                self.assertEqual(self.targets(module), before)

    def test_disabled_has_no_patches_and_no_component_counts(self):
        module = self.launcher()
        before = self.targets(module)
        with self.timer(module, enabled=False) as stats:
            self.assertEqual(self.targets(module), before)
        self.assertEqual(stats['status'], 'disabled')
        self.assertEqual(stats['components']['digest']['calls'], 0)

    def test_unavailable_module_is_noop_not_business_failure(self):
        module = types.ModuleType('unknown')
        with self.timer(module, enabled=True) as stats:
            self.assertFalse(hasattr(module, '_CanonicalBytes'))
        self.assertEqual(stats['status'], 'unavailable')

    def test_timing_clock_errors_never_change_real_digest_result(self):
        module = self.launcher()
        original = lambda v: 'original fallback result'
        value = {'x': [1]}
        expected = module._CanonicalBytes(original).digest(value)
        before = self.targets(module)
        clock = lambda: (_ for _ in ()).throw(RuntimeError('bad timer'))
        with self.timer(module, enabled=True, clock=clock) as stats:
            self.assertEqual(module._CanonicalBytes(original).digest(value), expected)
        self.assertEqual(self.targets(module), before)
        self.assertGreater(stats['timer_errors'], 0)

    def test_utc_is_timed_for_whole_paired_context_and_restored(self):
        from contextlib import contextmanager
        module = self.launcher()
        cohort = types.ModuleType('paired_forward_cohort')
        calls = []
        def utc(value):
            calls.append(value)
            return value
        cohort.utc = utc
        @contextmanager
        def hooks(root, script):
            yield
        module._hooks = hooks
        with patch.dict(sys.modules, {'paired_forward_cohort': cohort}):
            with self.timer(module, enabled=True) as stats:
                with module._hooks(Path('.'), 'paired_cohort_audit.py'):
                    for number in range(4):
                        self.assertEqual(cohort.utc(number), number)
                self.assertIs(cohort.utc, utc)
            self.assertIs(cohort.utc, utc)
        self.assertEqual(calls, list(range(4)))
        self.assertEqual(stats['components']['cohort_utc']['calls'], 4)

    def test_metric_write_error_does_not_replace_original_business_result(self):
        self.assertTrue(hasattr(profile, 'write_component_metrics'), 'component writer missing')
        with patch.object(Path, 'write_text', side_effect=PermissionError('private error')):
            self.assertFalse(profile.write_component_metrics(Path('unused'), {'status': 'ok'}))

    def test_standard_encoder_reference_timed_together_and_restored_on_success(self):
        module = self.launcher()
        original = module.json.dumps
        module._STANDARD_JSON_DUMPS = original
        with self.timer(module, enabled=True) as stats:
            self.assertIs(module._STANDARD_JSON_DUMPS, module.json.dumps)
            self.assertIsNot(module.json.dumps, original)
            self.assertEqual(module.json.dumps({'x': 1}), original({'x': 1}))
        self.assertIs(module._STANDARD_JSON_DUMPS, original)
        self.assertIs(module.json.dumps, original)
        self.assertTrue(stats['instrumented_standard_encoder'])

    def test_marshal_loads_counted_only_inside_digest_and_restored(self):
        module = self.launcher()
        closed = module._json_closed
        def checked(value):
            module.marshal.loads(module.marshal.dumps(value, 2))
            return closed(value)
        module._json_closed = checked
        before = self.targets(module)
        expected = module._CanonicalBytes(lambda v: 'fallback').digest({'x': 1})
        with self.timer(module, enabled=True) as stats:
            module.marshal.loads(module.marshal.dumps({'outside': True}, 2))
            actual = module._CanonicalBytes(lambda v: 'fallback').digest({'x': 1})
        self.assertEqual(actual, expected)
        self.assertEqual(stats['components']['marshal_loads']['calls'], 1)
        self.assertEqual(self.targets(module), before)

    def test_standard_encoder_reference_restored_on_exception(self):
        module = self.launcher()
        original = module.json.dumps
        module._STANDARD_JSON_DUMPS = original
        error = RuntimeError('original error')
        with self.assertRaises(RuntimeError) as got:
            with self.timer(module, enabled=True):
                self.assertIs(module._STANDARD_JSON_DUMPS, module.json.dumps)
                raise error
        self.assertIs(got.exception, error)
        self.assertIs(module._STANDARD_JSON_DUMPS, original)
        self.assertIs(module.json.dumps, original)

    def test_standard_encoder_reference_restored_on_cancel(self):
        module = self.launcher()
        original = module.json.dumps
        module._STANDARD_JSON_DUMPS = original
        for error in (SystemExit(9), KeyboardInterrupt()):
            with self.assertRaises(type(error)) as got:
                with self.timer(module, enabled=True):
                    self.assertIs(module._STANDARD_JSON_DUMPS, module.json.dumps)
                    raise error
            self.assertIs(got.exception, error)
            self.assertIs(module._STANDARD_JSON_DUMPS, original)
            self.assertIs(module.json.dumps, original)

    def test_external_encoder_hook_is_never_blessed_and_is_exactly_restored(self):
        module = self.launcher()
        original = module.json.dumps
        module._STANDARD_JSON_DUMPS = original
        seen = []
        def external(*args, **kwargs):
            seen.append(1)
            return original(*args, **kwargs)
        with patch.object(module.json, 'dumps', external):
            with self.timer(module, enabled=True) as stats:
                self.assertIs(module._STANDARD_JSON_DUMPS, original)
                self.assertIsNot(module._STANDARD_JSON_DUMPS, module.json.dumps)
                self.assertEqual(module.json.dumps({'x': 1}), original({'x': 1}))
            self.assertIs(module.json.dumps, external)
            self.assertFalse(stats['instrumented_standard_encoder'])
        self.assertIs(module._STANDARD_JSON_DUMPS, original)
        self.assertIs(module.json.dumps, original)
        self.assertEqual(seen, [1])




class ComponentProfileOptInTests(unittest.TestCase):
    def run_child(self, setting, expected_enabled, error=None):
        import contextlib
        module=ComponentTimingTests.launcher(self)
        before=ComponentTimingTests.targets(self,module)
        stats=[]
        original_path=list(sys.path)
        def original_main(root,script):
            current=ComponentTimingTests.targets(self,module)
            self.assertEqual([a is b for a,b in zip(before,current)], [not expected_enabled]*len(before))
            self.assertEqual(script,'paired_cohort_audit.py')
            original_digest=lambda value: module.hashlib.sha256(module.json.dumps(
                value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            value=module._CanonicalBytes(original_digest).digest({'real_original_digest':[1,2,3]})
            self.assertEqual(len(value),64)
            if error is not None:raise error
            print('unchanged original stdout')
            return 17
        env={} if setting is None else {'DOGE_AUDIT_PAIRED_COMPONENT_PROFILE':setting}
        try:
            with tempfile.TemporaryDirectory() as name, patch.dict(profile.os.environ,env,clear=True), patch.dict(sys.modules,{'runtime_audit_cli':module,'numpy':types.ModuleType('numpy')}), patch.object(module,'run_cli',side_effect=original_main,create=True), patch.object(profile,'transport',return_value=contextlib.nullcontext()), patch.object(profile,'write_component_metrics',side_effect=lambda path,value:stats.append(value)), patch('sys.stdout',new_callable=io.StringIO) as stdout:
                result=profile.child(Path(name),'paired_cohort_audit.py','replay',Path(name)/'captured-get/paired.json','2026-11-10T00:00:00+00:00',True)
                self.assertEqual(result,17)
                self.assertEqual(stdout.getvalue(),'unchanged original stdout\n')
        finally:
            sys.path[:]=original_path
            after=ComponentTimingTests.targets(self,module)
            self.assertTrue(all(a is b for a,b in zip(before,after)))
        return stats[0]

    def test_formal_native_child_default_and_nonexact_flags_install_no_component_wrappers(self):
        for setting in (None,'0','true','01','1 ',''):
            with self.subTest(setting=setting):
                stats=self.run_child(setting,False)
                self.assertFalse(stats['enabled'])
                self.assertEqual(stats['status'],'disabled')
                self.assertTrue(stats['business_completed'])
                self.assertTrue(all(row['calls']==0 for row in stats['components'].values()))

    def test_exact_optin_keeps_existing_real_component_diagnostics(self):
        stats=self.run_child('1',True)
        self.assertTrue(stats['enabled'])
        self.assertEqual(stats['status'],'ok')
        self.assertEqual(stats['components']['digest']['calls'],1)
        self.assertEqual(stats['components']['sha256']['calls'],1)
        self.assertTrue(stats['business_completed'])

    def test_optin_child_exception_and_cancel_keep_original_error_and_restore(self):
        for error in (ValueError('original failure'),KeyboardInterrupt(),SystemExit(7)):
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)) as got:
                self.run_child('1',True,error)
            self.assertIs(got.exception,error)

    def test_component_flag_only_enables_exact_native_paired_cli(self):
        self.assertTrue(hasattr(profile,'paired_component_profile_enabled'))
        with patch.dict(profile.os.environ,{'DOGE_AUDIT_PAIRED_COMPONENT_PROFILE':'1'}):
            self.assertTrue(profile.paired_component_profile_enabled(True,'paired_cohort_audit.py'))
            self.assertFalse(profile.paired_component_profile_enabled(False,'paired_cohort_audit.py'))
            for script in ('forward_validation_v2.py','p0_operational_audit.py','unknown'):
                self.assertFalse(profile.paired_component_profile_enabled(True,script))


class NativeFirstTrialOrderTests(unittest.TestCase):
    """Safe orchestration only: no Git, temporary directories or real child CLIs."""
    def run_worker(self, native_first=False, native_seconds=3., original_seconds=3., fail_label=None):
        import copy
        import inspect
        from contextlib import nullcontext
        from unittest.mock import Mock
        if native_first:
            self.assertIn('native_first', inspect.signature(profile.worker).parameters,
                          'worker lacks explicit native-first opt-in')
        root=Path(__file__).resolve().parent
        source=root/'mock-source-not-created'
        output=root/'mock-output-not-created'
        workspace=root/'mock-workspace-not-created'
        head='a'*40
        clock=types.SimpleNamespace(value=0.)
        calls=[];reports=[]
        class Workspace:
            def __enter__(self):return str(workspace)
            def __exit__(self,*args):return False
        audit=types.SimpleNamespace(
            CLI_HELPERS=('runtime_paired_git_stream.py',),
            prepare_checkout=Mock(return_value={'mode':'sparse_source_archive_excluded'}),
            verify_cli_sources=Mock(return_value=True),
            paired_stream_source=lambda *args:nullcontext())
        checkout=types.SimpleNamespace(require_full_history=Mock())
        code={name:'b'*64 for name in ('runtime_audit_cli.py','runtime_p0_inventory_transport.py',
                                      'runtime_paired_git_stream.py','runtime_witness_git_stream.py')}
        inputs={name:'c'*64 for name in profile.INPUTS}
        outputs={f'output-{i}.json':{'sha256':'d'*64,'comparison_sha256':'d'*64,'bytes':2}
                 for i in range(9)}
        def command(arguments,**kwargs):
            script=arguments[arguments.index('--script')+1]
            label='native' if '--optimized' in arguments else 'original'
            mode=(arguments[arguments.index('--metadata-mode')+1]
                  if '--metadata-mode' in arguments else ('replay' if label=='native' else 'record'))
            calls.append({'label':label,'script':script,'mode':mode,'kwargs':kwargs,
                          'explicit_metadata_mode':'--metadata-mode' in arguments,
                          'clock':arguments[arguments.index('--clock')+1]})
            clock.value+=(native_seconds if label=='native' else original_seconds)/3
            failed=label==fail_label
            return subprocess.CompletedProcess(arguments,7 if failed else 0,
                                               stdout='complete '+script+'\n',stderr='diagnostics')
        def git(root,*args,**kwargs):
            self.assertEqual(args,('rev-parse','HEAD'))
            return head.encode()
        def report(path,value):reports.append(copy.deepcopy(value))
        error=None
        with patch.dict(sys.modules,{'runtime_audit_worker':audit,'runtime_audit_checkout_profile':checkout}),\
             patch.object(profile,'source_control_state',return_value={'head':head,'refs':'fixed'}),\
             patch.object(profile,'fingerprint',return_value=inputs),\
             patch.object(profile,'code_fingerprint',return_value=code),\
             patch.object(profile,'output_manifest',return_value=outputs),\
             patch.object(profile,'git',side_effect=git),\
             patch.object(profile,'write_json',side_effect=report),\
             patch.object(profile,'validate_native_diagnostics',return_value=[{'mode':'native_batch'}]) as validation,\
             patch.object(profile.tempfile,'TemporaryDirectory',return_value=Workspace()),\
             patch.object(profile.subprocess,'run',side_effect=command),\
             patch.object(profile.Path,'write_text',return_value=0),\
             patch.object(profile.shutil,'rmtree') as cleanup,\
             patch.object(profile.time,'perf_counter',side_effect=lambda:clock.value):
            try:
                if native_first:profile.worker(source,head,output,600,140,native_first=True)
                else:profile.worker(source,head,output,600,140)
            except profile.ProfileError as caught:error=caught
        self.assertTrue(reports)
        self.assertLessEqual(validation.call_count,3)
        self.assertLessEqual(cleanup.call_count,2)
        return calls,reports[-1],error

    def test_default_keeps_original_record_then_native_replay(self):
        calls,report,error=self.run_worker()
        self.assertIsNone(error)
        self.assertEqual([(r['label'],r['mode']) for r in calls],
                         [('original','record')]*3+[('native','replay')]*3)
        self.assertEqual([t['kind'] for t in report['trials']],['original','native'])
        self.assertEqual(report['native_whole_source_seconds'],3.)
        self.assertEqual(len({r['clock'] for r in calls}),1)
        self.assertEqual(report['max_native_whole_source_seconds'],140)
        self.assertFalse(any(r['explicit_metadata_mode'] for r in calls))
        self.assertNotIn('execution_order',report)
        self.assertNotIn('metadata_recorded_by',report)
        self.assertEqual(report['metadata'],'actual original GET replies; candidate replays equal measured latency')

    def test_native_first_records_first_and_keeps_report_label_indices(self):
        calls,report,error=self.run_worker(native_first=True)
        self.assertIsNone(error)
        self.assertEqual([(r['label'],r['mode']) for r in calls],
                         [('native','record')]*3+[('original','replay')]*3)
        self.assertEqual([t['kind'] for t in report['trials']],['original','native'])
        self.assertEqual(report['execution_order'],['native','original'])
        self.assertEqual(report['metadata_recorded_by'],'native')
        self.assertEqual(report['trials'][1]['metadata_mode'],'record')
        self.assertEqual(report['trials'][0]['metadata_mode'],'replay')
        self.assertEqual(report['status'],'PASS_WHOLE_CLI_EQUIVALENCE')
        self.assertTrue(report['five_inputs_unchanged'])
        self.assertTrue(report['source_state_unchanged'])
        self.assertTrue(report['output_semantics_exactly_equal'])
        self.assertEqual(len(report['trials'][1]['outputs']),9)
        self.assertEqual(len({r['clock'] for r in calls}),1)
        self.assertTrue(all(r['kwargs']['timeout']<=240 for r in calls))
        self.assertTrue(all(r['explicit_metadata_mode'] for r in calls))

    def test_native_budget_is_checked_by_label_after_execution_order_reversal(self):
        _,report,error=self.run_worker(native_first=True,native_seconds=141.,original_seconds=3.)
        self.assertIsInstance(error,profile.ProfileError)
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(report['native_whole_source_seconds'],141.)
        self.assertEqual(report['original_whole_source_seconds'],3.)

    def test_slow_original_is_not_mistaken_for_native_budget(self):
        _,report,error=self.run_worker(native_first=True,native_seconds=138.,original_seconds=201.)
        self.assertIsNone(error)
        self.assertEqual(report['native_whole_source_seconds'],138.)
        self.assertEqual(report['original_whole_source_seconds'],201.)

    def test_failed_original_replay_cannot_turn_native_local_success_into_pass(self):
        calls,report,error=self.run_worker(native_first=True,fail_label='original')
        self.assertIsInstance(error,profile.ProfileError)
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual([t['kind'] for t in report['trials']],['original','native'])
        self.assertEqual(len(report['trials'][1]['phases']),3)
        self.assertNotIn('output_semantics_exactly_equal',report)

    def test_partial_native_failure_keeps_original_not_started_slot(self):
        calls,report,error=self.run_worker(native_first=True,fail_label='native')
        self.assertIsInstance(error,profile.ProfileError)
        self.assertEqual(len(calls),1)
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(report['trials'][0],{'kind':'original','status':'not_started'})
        self.assertEqual(report['trials'][1]['kind'],'native')
        self.assertNotIn('measured_speedup',report)

    def test_default_child_metadata_path_is_unchanged(self):
        base=['--child','--script',profile.SCRIPTS[0],'--fixture','not-created.json',
              '--clock','2026-11-11T20:00:00+00:00']
        with patch.object(profile,'child',return_value=0) as child:
            profile.main(base)
            self.assertEqual(child.call_args.args[2],'record')
            self.assertIs(child.call_args.args[-1],False)
            profile.main(base+['--optimized'])
            self.assertEqual(child.call_args.args[2],'replay')
            self.assertIs(child.call_args.args[-1],True)

    def test_explicit_child_metadata_mode_is_independent_of_optimized(self):
        base=['--child','--script',profile.SCRIPTS[0],'--fixture','not-created.json',
              '--clock','2026-11-11T20:00:00+00:00']
        with patch.object(profile,'child',return_value=0) as child,patch('sys.stderr',new_callable=io.StringIO):
            try:profile.main(base+['--optimized','--metadata-mode','record'])
            except SystemExit:self.fail('native child cannot explicitly record first')
            self.assertEqual(child.call_args.args[2],'record')
            self.assertIs(child.call_args.args[-1],True)

    def test_public_optin_is_forwarded_to_owned_worker_without_running_a_process(self):
        from contextlib import nullcontext
        from unittest.mock import Mock
        class LocalPath:
            def __init__(self,value):self.value=str(value)
            def resolve(self):return self
            def is_relative_to(self,other):return False
            def __truediv__(self,name):return type(self)(self.value+'/'+name)
            def read_text(self,**kwargs):return '{}'
            def __str__(self):return self.value
            @property
            def parent(self):return type(self)('profile-root')
        command=Mock(return_value='')
        runtime=types.SimpleNamespace(command=command,cancellation_signals=lambda:nullcontext())
        base=['--source-root','source','--ref','a'*40,'--output','output']
        with patch.dict(sys.modules,{'runtime_process':runtime}),patch.object(profile,'Path',LocalPath),\
             patch.object(profile.os,'name','posix'),patch.object(profile.sys,'path',list(sys.path)),\
             patch('sys.stdout',new_callable=io.StringIO):
            profile.main(base)
            self.assertNotIn('--native-first',command.call_args.args[0])
            profile.main(base+['--native-first'])
            arguments=command.call_args.args[0]
            self.assertIn('--native-first',arguments)
            self.assertEqual(float(arguments[arguments.index('--max-native-seconds')+1]),140.)
            self.assertEqual(command.call_args.kwargs['timeout'],600.)

    def test_worker_entry_receives_optin_and_legacy_call_remains_unchanged(self):
        class LocalPath:
            def __init__(self,value):self.value=str(value)
            def resolve(self):return self
            def is_relative_to(self,other):return False
            def __str__(self):return self.value
            @property
            def parent(self):return type(self)('profile-root')
        base=['--worker','--source-root','source','--ref','a'*40,'--output','output']
        with patch.object(profile,'Path',LocalPath),patch.object(profile.os,'name','posix'),\
             patch.object(profile,'worker',return_value=0) as worker:
            profile.main(base)
            self.assertEqual(worker.call_args.kwargs,{})
            profile.main(base+['--native-first'])
            self.assertEqual(worker.call_args.kwargs,{'native_first':True})


class NativeFirstTransportSafetyTests(unittest.TestCase):
    """Real transport context on in-memory fixtures; no files or live HTTP."""
    def fixture(self):
        body=b'{}'
        return {'entries':[{'url':URL,'response_url':URL,'status':200,'headers':{},
            'body_base64':'e30=','sha256':profile.digest(body),'elapsed_seconds':0.}],
            'request_credentials_recorded':False}

    def test_record_first_does_not_require_a_preexisting_fixture(self):
        path=Path('native-first-fixture-not-created.json')
        with patch.object(profile.Path,'stat',side_effect=AssertionError('record read fixture')),\
             patch.object(profile.Path,'read_text',side_effect=AssertionError('record read fixture')),\
             patch.object(profile,'write_json') as write,\
             patch.object(profile.urllib.request,'urlopen',return_value=Response(b'{}')):
            with profile.transport('record',path):
                self.assertEqual(profile.urllib.request.urlopen(URL).read(),b'{}')
        self.assertEqual(len(write.call_args.args[1]['entries']),1)
        self.assertFalse(write.call_args.args[1]['request_credentials_recorded'])

    def test_missing_replay_fixture_fails_without_live_network(self):
        with patch.object(profile.Path,'stat',side_effect=FileNotFoundError('missing fixture')),\
             patch.object(profile.urllib.request,'urlopen') as network:
            with self.assertRaises(FileNotFoundError):
                with profile.transport('replay',Path('not-created.json')):pass
        network.assert_not_called()

    def test_extra_replay_get_fails_without_live_fallback(self):
        with patch.object(profile.Path,'stat',return_value=types.SimpleNamespace(st_size=500)),\
             patch.object(profile.Path,'read_text',return_value=json.dumps(self.fixture())),\
             patch.object(profile.time,'sleep') as sleep,\
             patch.object(profile.urllib.request,'urlopen') as network:
            with self.assertRaisesRegex(profile.ProfileError,'unrecorded'):
                with profile.transport('replay',Path('not-created.json')):
                    self.assertEqual(profile.urllib.request.urlopen(URL).read(),b'{}')
                    profile.urllib.request.urlopen(URL)
        network.assert_not_called()
        sleep.assert_called_once_with(0.)

    def test_unconsumed_replay_reply_fails(self):
        with patch.object(profile.Path,'stat',return_value=types.SimpleNamespace(st_size=500)),\
             patch.object(profile.Path,'read_text',return_value=json.dumps(self.fixture())),\
             patch.object(profile.urllib.request,'urlopen') as network:
            with self.assertRaisesRegex(profile.ProfileError,'unconsumed'):
                with profile.transport('replay',Path('not-created.json')):pass
        network.assert_not_called()


class NativeFirstWorkflowContractTests(unittest.TestCase):
    BRANCH='fix/p0-native-first-profile-20261111'
    def test_exact_new_branch_uses_existing_three_profile_selectors_and_explicit_flag(self):
        text=(Path(__file__).resolve().parent/'.github/workflows/runtime_deadline_tests.yml').read_text()
        selectors=[line for line in text.splitlines()
                   if line.lstrip().startswith(('if:','NATIVE_TRANSPORT_PR:')) and self.BRANCH in line]
        self.assertEqual(len(selectors),3,'experimental branch lacks main profile/artifact/test closure')
        self.assertTrue(all('pull_request' in line and "== '"+self.BRANCH+"'" in line for line in selectors))
        self.assertIn('NATIVE_FIRST_PROFILE:',text)
        self.assertIn('--native-first',text)
        self.assertIn('timeout-minutes: 11',text)
        self.assertIn('timeout-minutes: 15',text)

    def test_science_adds_only_existing_pr_path_closure(self):
        text=(Path(__file__).resolve().parent/'.github/workflows/test_forward_validation.yml').read_text()
        for name in ('runtime_audit_pipeline_profile.py','test_runtime_audit_pipeline_profile.py',
                     '.github/workflows/runtime_deadline_tests.yml'):
            self.assertIn("'"+name+"'",text)
        self.assertNotIn('\n  push:',text)
        self.assertNotIn('\n  schedule:',text)
        self.assertIn('\n  workflow_dispatch:',text)



if __name__ == '__main__':
    unittest.main()

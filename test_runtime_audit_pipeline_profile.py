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
                     implementation='paired_canonical_utf8_lru_v1', mode='canonical_bytes_lru',
                     status='ok', module_sha256='a'*64, calls=2, hits=1, misses=1,
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
            launcher += " if script=='paired_cohort_audit.py': print(json.dumps({'kind':'runtime_canonical_json_transport','implementation':'paired_canonical_utf8_lru_v1','mode':'canonical_bytes_lru','status':'ok','module_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'calls':2,'hits':1,'misses':1,'fallback_calls':0,'sha256_calls':2,'encoded_calls':1,'evictions':0,'entries':1,'cached_bytes':10}),file=sys.stderr)\n"
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


if __name__ == '__main__':
    unittest.main()

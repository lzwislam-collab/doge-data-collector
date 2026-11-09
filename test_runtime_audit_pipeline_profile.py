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
            launcher = "import runpy,json,sys\n"
            launcher += "def run_cli(root,script):\n"
            launcher += " kinds=['runtime_publication_state_validation']+(['runtime_paired_history_validation'] if script=='paired_cohort_audit.py' else [])\n"
            launcher += " for kind in kinds: print(json.dumps({'kind':kind,'mode':'native_batch','status':'ok','validation_status':'ok','native_batch_calls':1}),file=sys.stderr)\n"
            launcher += " runpy.run_path(str(root/script),run_name='__main__')\n"
            (source / 'runtime_audit_cli.py').write_text(launcher)
            (source / 'runtime_audit_worker.py').write_text('# fixture helper\n')
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


if __name__ == '__main__':
    unittest.main()
